"""拖动时的「软吸附」：把元素往叠影里墨迹最吻合的地方轻轻拽一把。

为什么是「软」的
---------------
现场说的是「我把它拖到那附近，它就更愿意落在那个位置，但我想拖开还是能拖开」。
所以这里不做硬吸附（不锁死、不弹开），只加一个**随距离衰减的力**：

    w = (1 - d/R)^1.6      d = 拖到的位置离最佳位置多远，R = 引力半径
    最终位置 = 鼠标位置 + (最佳位置 - 鼠标位置) × w × 强度

鼠标真拉远（d → R）时 w → 0，元素就老老实实跟着鼠标走；键盘微调根本不经过
这里，也不会被吸住。多选不吸：几块各自的最佳点会互相打架，越拖越乱。

怎么算「最吻合」
-------------
1. 把这块元素单独渲染出来（`render.render_one`），取让它「有墨」的二值遮罩；
2. 从叠影上取一块同样大小的窗口，在 ±R 范围里滑动遮罩；
3. 每个位置算「遮罩里的墨有多少正好压在叠影的墨上」；
4. 取命中率最高的那个位置。

用命中率而不是逐像素差：叠影是印刷稿，墨会扩散、字形也不同，逐像素差会把
「字形差异」当成「位置不对」，永远给出满屏的偏移。命中率只问一句
「这块字的笔画有没有落在墨上」，对字形差异更宽容。
"""
from PIL import Image, ImageChops, ImageStat


# ★ 2026-11-20 用户指出的关键改进：**只认黑色像素**
# DWG 叠影上有大量绿色箭头、蓝色字标、红色标注。用「灰度 < 阈值」判定时，
# 绿色 (0,224,0) 灰度约 150 —— 会被当成墨迹，于是：
#   · 互相关把绿箭头当成"模板的一部分"，把字吸到箭头上
#   · 墨迹包围盒被箭头撑大，量出来的位置尺寸全偏
# 正确判定：R/G/B **三个通道都低** 才是真黑。
BLACK_THR = 90

# 判"彩色"的通道差阈值（P2）。某通道比最暗的高过它 → 认为是彩色标注。
# 60 是老实现里用的值，行为保持一致（绿 (0,224,0) 的 G-R = 224 → 彩色）。
COLOR_SPREAD = 60


def black_ink(im, thr=BLACK_THR):
    """只保留黑色像素的灰度图（彩色 → 白）。黑=0，其余=255。

    ★ P2：改用 ImageChops/pint 做**通道运算**，不再逐像素循环。
      旧实现是 `for y: for x: r,g,b = px[x,y]` —— 一张 7830×8442 的叠影
      要跑 6600 万次 Python 循环，单次 20 秒以上。摆位要反复调用，
      根本跑不动。

    "三通道都低才是黑"这个判定可以用通道运算表达：
        darkest = 三个通道逐像素取**最小**（ImageChops.darker）
        darkest < thr  → 暗到够黑
    而"彩色"的判据是某通道明显更亮。两张图：
        darkest 图（取最小）代表"这个像素最暗的那个通道"
    对纯黑来说三通道一样，darkest 就是它自己；
    对绿色 (0,224,0) 来说 darkest = 0（G 被 R/B 拉下来了）——
    所以光靠 darkest 分不出绿和黑，**还要排除彩色**：
        spread = 最亮通道 - 最暗通道（ImageChops.difference）
        spread > 阈值 → 彩色，白掉
    两步都是 C 层通道运算，6600 万像素也就几百毫秒。

    Args:
        im: PIL 图（任何模式）
        thr: 暗于它才算黑（0~255）

    Returns:
        L 模式灰度图：黑处是原灰度（便于算墨量），其余 255
    """
    rgb = im.convert("RGB")
    r, g, b = rgb.split()
    # 最暗通道（逐像素取 min）
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)
    # 最亮通道（逐像素取 max）
    lightest = ImageChops.lighter(ImageChops.lighter(r, g), b)
    # 彩度 = 最亮 - 最暗
    spread = ImageChops.difference(lightest, darkest)
    # 够黑 且 不够彩 → 是黑
    is_black = ImageChops.multiply(
        darkest.point(lambda v: 255 if v < thr else 0),
        spread.point(lambda v: 255 if v <= COLOR_SPREAD else 0))
    # 黑处保留原灰度（算墨量用），其余 255
    return Image.composite(darkest, Image.new("L", rgb.size, 255), is_black)


def ink_mask(im, thr=128, black_only=False):
    """图 → 墨迹遮罩（墨 = 255，纸 = 0）。

    ★ P2：同样改成通道运算，不再逐像素循环（理由见 black_ink）。

    Args:
        im: PIL 图。
        thr: 灰度阈值。
        black_only: True 时**只认黑色**（剔除绿色箭头等彩色标注）。
                    对位叠影时应该传 True。
        其余情况按灰度判墨，但**排除明显高饱和的彩色**（绿箭头之类）。
    """
    if black_only:
        return black_ink(im, BLACK_THR).point(lambda v: 255 if v < thr else 0)
    rgb = im.convert("RGB")
    gray = rgb.convert("L")
    r, g, b = rgb.split()
    lightest = ImageChops.lighter(ImageChops.lighter(r, g), b)
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)
    spread = ImageChops.difference(lightest, darkest)
    # 墨 = 灰度够暗 且 不够彩
    ink = ImageChops.multiply(
        gray.point(lambda v: 255 if v < thr else 0),
        spread.point(lambda v: 255 if v <= COLOR_SPREAD else 0))
    return ink


def ink_amount(mask):
    """遮罩里有多少墨（像素×255）。算一次存着，搜索时每帧复用。"""
    return ImageStat.Stat(mask).sum[0]


def overlap(mask, ref, dx, dy, denom=None):
    """把 mask 贴在 ref 的 (dx, dy)，返回**双向**墨迹吻合度（F1，0~1）。

    ★ 2026-11-20 修正（这是个真 bug，导致吸附老是吸错地方）
    ---------------------------------------------------------
    原实现只除以 **mask 自己的墨量** —— 也就是"我的墨有多少压住了别人的墨"。
    这是单向的，后果很严重：

        模板是一个 10×10 的小字块，窗口里是一大片墨（比如邻行文字连成一片）。
        只要把这块小墨放到大片墨的任何一个位置，分子=分母=小墨量 → 得 1.0 满分。
        换一个完全不同形状的模板，压上去**照样 1.0**。

    实测（dev 里跑过）：三种完全不同的形状压在同一片墨上，全是 1.000。
    于是吸附会把小字吸到大块墨迹（表格线、色块、图标的黑块）上去 ——
    用户看到的"有些字怎么都吸不准、吸到别人身上"就是这个原因。

    现在改成 F1：既要求"我的墨被覆盖了多少"（precision），也要求
    "那片墨被我用到了多少"（recall），两边都得高才算吻合：

        precision = 交集 / mask 墨量      （小墨压大墨 → 1.0，但 recall 很低）
        recall    = 交集 / ref 窗口墨量   （大墨包小墨 → 低）
        F1        = 2PR / (P + R)

    大墨块对小字的 F1 会掉到 0.2 以下，自然被淘汰。
    """
    if dx < 0 or dy < 0:
        return 0.0
    if dx + mask.width > ref.width or dy + mask.height > ref.height:
        return 0.0
    sub = ref.crop((dx, dy, dx + mask.width, dy + mask.height))
    both = ImageChops.multiply(mask, sub)
    inter = ImageStat.Stat(both).sum[0]
    if inter <= 0:
        return 0.0
    m = denom if denom is not None else ImageStat.Stat(mask).sum[0]
    r = ImageStat.Stat(sub).sum[0]        # 窗口这一块的墨量（recall 的分母）
    if m <= 0 or r <= 0:
        return 0.0
    p_rec = inter / m
    p_recall = inter / r
    if p_rec + p_recall <= 0:
        return 0.0
    return 2.0 * p_rec * p_recall / (p_rec + p_recall)


def find_offset(mask, ref, radius_px, coarse=4, denom=None):
    """在 ±radius_px 内找 mask 与 ref 最重合的偏移（相对「正中」位置）。

    两级搜索：先按 coarse 像素粗扫一遍，再在最佳点附近 1px 精调。
    纯细扫 61×61 个位置要 180ms，拖起来明显发涩；这样两级下来 20ms 左右。

    返回 (dx, dy, score)：dx/dy 单位是像素，正数 = 该往右下挪。
    """
    cx = (ref.width - mask.width) // 2
    cy = (ref.height - mask.height) // 2
    if cx < 0 or cy < 0:
        return 0, 0, 0.0
    best = (-1.0, 0, 0)
    d0 = denom if denom is not None else ink_amount(mask)

    def probe(ox, oy):
        nonlocal best
        s = overlap(mask, ref, cx + ox, cy + oy, d0)
        if s > best[0]:
            best = (s, ox, oy)

    r = int(max(0, radius_px))
    for oy in range(-r, r + 1, coarse):
        for ox in range(-r, r + 1, coarse):
            probe(ox, oy)
    _, bx, by = best
    for oy in range(by - coarse, by + coarse + 1):
        for ox in range(bx - coarse, bx + coarse + 1):
            probe(ox, oy)
    return best[1], best[2], best[0]


def find_offset_top(mask, ref, radius_px, coarse=4, denom=None, sep_px=6):
    """同 find_offset，但**额外给出次优分差**（P1-4：CLI 的 place 要用）。

    为什么要分差：光看"最优分 0.72"没法判断这个位置可不可信 ——
    如果隔壁 0.71 还有一个几乎一样好的位置，说明这块内容在叠影上是
    **有歧义**的（例如一排等距的格子、重复的图标），挪过去大概率错。
    分差小 = 该标红让人看一眼。

    做法：记下所有得分，按得分降序，挑**距离最优点超过 sep_px** 的
    最高分作为次优 —— 紧挨着最优点的那些格子分数天然接近，
    拿它们当次优会让分差永远是 0，没有意义。

    Args:
        sep_px: 次优候选与最优点至少要隔多远（像素）。6px @500ppm ≈ 0.3mm

    Returns:
        (dx, dy, best_score, second_score, margin)
        margin = best_score - second_score，越大越可信。
        找不到任何候选时返回 (0, 0, 0.0, 0.0, 0.0)。
    """
    cx = (ref.width - mask.width) // 2
    cy = (ref.height - mask.height) // 2
    if cx < 0 or cy < 0:
        return 0, 0, 0.0, 0.0, 0.0
    d0 = denom if denom is not None else ink_amount(mask)
    r = int(max(0, radius_px))
    scored = []
    for oy in range(-r, r + 1, coarse):
        for ox in range(-r, r + 1, coarse):
            scored.append((overlap(mask, ref, cx + ox, cy + oy, d0), ox, oy))
    if not scored:
        return 0, 0, 0.0, 0.0, 0.0
    scored.sort(reverse=True)
    b_score, bx, by = scored[0]
    # 精调最优附近
    for oy in range(by - coarse, by + coarse + 1):
        for ox in range(bx - coarse, bx + coarse + 1):
            scored.append((overlap(mask, ref, cx + ox, cy + oy, d0), ox, oy))
    scored.sort(reverse=True)
    b_score, bx, by = scored[0]
    # 次优：离最优点够远的最高分
    second = 0.0
    for sc, ox, oy in scored:
        if abs(ox - bx) > sep_px or abs(oy - by) > sep_px:
            second = sc
            break
    return bx, by, b_score, second, b_score - second


def nudge(mask, ref, radius_mm, ppm, denom=None,
          strength=0.95, power=1.25, min_score=0.30, min_gain=0.08):
    """算这一帧该给多少引力。

    Args:
        mask: 元素遮罩（PIL，墨=255）
        ref: 叠影上以**元素当前位置**为中心的窗口（PIL，墨=255），
             尺寸 = mask + 2×radius，所以窗口正中 = 「元素就在当前位置」
        radius_mm: 引力半径
        ppm: 遮罩/窗口的每毫米像素

    返回 (dx_mm, dy_mm, score)：引力造成的位移增量。
    """
    if mask is None or ref is None or ppm <= 0:
        return 0.0, 0.0, 0.0
    off_x, off_y, score = find_offset(mask, ref, int(round(radius_mm * ppm)),
                                      denom=denom)
    if score < min_score:
        return 0.0, 0.0, score
    # 现在已经压得不错了就别再拽：只挪一点点、又让元素在鼠标底下微微打滑，
    # 手感比不吸还糟。判断标准是「最佳位置比现在好多少」。
    cx = (ref.width - mask.width) // 2
    cy = (ref.height - mask.height) // 2
    cur = overlap(mask, ref, cx, cy, denom)
    if score - cur < min_gain:
        return 0.0, 0.0, score
    # 最佳位置相对「正中」偏了多少 → 元素左上角该挪多少
    tx = off_x / ppm
    ty = off_y / ppm
    d = (tx * tx + ty * ty) ** 0.5
    if d <= 1e-6 or d >= radius_mm:
        return 0.0, 0.0, score
    w = (1.0 - d / radius_mm) ** power * strength
    return tx * w, ty * w, score
