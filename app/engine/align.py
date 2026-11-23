# -*- coding: utf-8 -*-
"""align.py —— 拿成品图当基准，量「现在画的和它差多少」。

为什么要一个数字
----------------
光靠眼睛看叠图，「差半毫米」和「差两毫米」是分不清的；把参考图挪一挪，
看着都"差不多"。这里把两张图都按「墨迹 / 空白」二值化，再算平均绝对差，
得到一个 0~1 的分数 —— 0 表示完全重合。挪动参考图时盯着这个数往下走，
比肉眼靠谱得多。

为什么不用 numpy：双平台 runtime 里不保证都有；PIL 自带的
ImageChops.difference + ImageStat 已经够快（520px 宽的一张图约 1.5ms）。
"""
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

# 低于这个灰度算「墨」。200 是留了余量的：印刷品扫进来边缘会发灰，
# 卡在 128 会把抗锯齿边缘全丢掉，两张图明明一样也扣分。
INK_TH = 200


def binarize(im, th=INK_TH):
    """墨迹=255、空白=0 的单通道图。"""
    return im.convert("L").point(lambda v: 255 if v < th else 0)


def blank(im, holes):
    """把指定矩形涂成空白（二值图里 0 就是没墨）。"""
    if not holes:
        return im
    im = im.copy()
    d = ImageDraw.Draw(im)
    for r in holes:
        d.rectangle([r[0], r[1], r[2], r[3]], fill=0)
    return im


def valid_area(size, holes):
    """没被挖掉的像素数。归一化的分母。"""
    if not holes:
        return size[0] * size[1]
    m = Image.new("L", size, 255)
    d = ImageDraw.Draw(m)
    for r in holes:
        d.rectangle([r[0], r[1], r[2], r[3]], fill=0)
    return ImageStat.Stat(m).sum[0] / 255.0


def score(a, b, th=INK_TH, holes=None):
    """两张同尺寸图的差异，0（完全重合）~ 1（毫无重叠）。

    holes 是「不参与比对」的像素矩形（变量区：条码、二维码、批号、日期…）。
    这些地方换个规格本来就该不一样，不挖掉的话分数永远有个下限，
    用户会以为版面没调好。

    归一化用的是「有效面积」而不是全图：挖掉一半再按全图平均，
    分数会自动腰斩，挖得越多越「好看」，那就成了自欺欺人的指标。
    """
    if a.size != b.size:
        b = b.resize(a.size, Image.LANCZOS)
    da, db = binarize(a, th), binarize(b, th)
    if holes:
        da, db = blank(da, holes), blank(db, holes)
    d = ImageChops.difference(da, db)
    n = valid_area(a.size, holes)
    if n <= 0:
        return 1.0
    return ImageStat.Stat(d).sum[0] / n / 255.0


def score_in(a, b, box, th=INK_TH, blur=1.6):
    """只在一个像素窗口里算差异分（带降噪）。

    整张图算分对「挪半毫米」几乎没反应：一个 20mm 宽的字块偏 2mm，整图分
    只动 0.0002，数字根本不跟着走。只看这一块灵敏 20 倍以上。

    blur 是必须的：两张图的字形本来就不完全一样（字库/印刷不同），逐像素比
    会被字形差异淹没，同一块挪 1mm 和挪 2mm 的分能来回跳。两边一起糊一层之后，
    毛刺被压平，剩下的就是「有没有错位」—— 实测偏移 5→0mm 时分数严格递减。

    窗口要跟着元素走：固定在原位的话，元素挪出窗口就不再被计入，
    分数反而会「越偏越低」，完全反了。
    """
    if a.size != b.size:
        b = b.resize(a.size, Image.LANCZOS)
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(a.width, x1), min(a.height, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return 1.0
    box = (x0, y0, x1, y1)
    da = binarize(a, th).crop(box)
    db = binarize(b, th).crop(box)
    if blur:
        da = da.filter(ImageFilter.GaussianBlur(blur))
        db = db.filter(ImageFilter.GaussianBlur(blur))
    return ImageStat.Stat(ImageChops.difference(da, db)).mean[0] / 255.0


def diff_cells(cur, ref, mm=2.2, thresh=58, th=INK_TH, holes=None, mm_w=None,
               tint=(255, 246, 214)):
    """「重叠相减」的块级版本：按 mm 网格统计墨量，只标出墨量差得多的格。

    为什么不用逐像素
    ----------------
    模板是矢量渲染、参考图是 200dpi 位图，同一个字的两张画法天生不同：
    笔画粗细差半像素、拐角差一格。逐像素减就是满屏红蓝噪点，真正想看的
    「这一块整体偏了 2mm」被彻底淹没，看着像整张标签都是错的。

    按 2mm 网格把墨量平均掉之后，字形细节没了，剩下的是「这格该有墨却没有 /
    不该有墨却有了」—— 位置对不对，一眼就能看出来。

    Args:
        cur: 当前渲染图（RGB）
        ref: 参考图（会缩放到同尺寸）
        mm: 网格边长（毫米）。1.5 太碎、2.5 太糊，2.2 左右最好认。
        thresh: 一格里墨量差多少（0~255）才算「不同」。低于这个当噪声。
        holes: 不参与比对的像素矩形（变量区），涂成浅黄。
    """
    if cur.size != ref.size:
        ref = ref.resize(cur.size, Image.LANCZOS)
    W = float(mm_w or cur.width)          # 没给画布宽就按像素算，退化但不出错
    k = cur.width / W
    fp = max(1, int(round(float(mm) * k)))
    gw, gh = max(1, cur.width // fp), max(1, cur.height // fp)

    # BOX 重采样 = 每格取平均，正好是「这格有多少墨」
    A = binarize(cur, th).resize((gw, gh), Image.BOX)
    B = binarize(ref, th).resize((gw, gh), Image.BOX)
    red = ImageChops.subtract(B, A).point(lambda v: 255 if v >= thresh else 0)
    blue = ImageChops.subtract(A, B).point(lambda v: 255 if v >= thresh else 0)
    out = Image.new("RGB", (gw, gh), "white")
    out.paste((232, 41, 28), (0, 0), red)      # 参考有、这里没有
    out.paste((27, 111, 217), (0, 0), blue)    # 这里有、参考没有

    out = out.resize(cur.size, Image.NEAREST)
    if holes:
        d = ImageDraw.Draw(out)
        for r in holes:
            d.rectangle([r[0], r[1], r[2], r[3]], fill=tint)
    return out


def _scaled(im, width):
    r = float(width) / im.width
    return im.resize((max(1, int(width)), max(1, int(round(im.height * r)))),
                     Image.LANCZOS)


def overlap(ref, dx_mm, dy_mm, mm_w, size, scale=1.0, dpi_ratio=1.0):
    """把参考图按 (dx, dy, scale) 摆到 size 的画布上，白底、越界裁掉。"""
    W, H = size
    k = W / float(mm_w)                     # px / mm（屏幕像素）
    w = max(1, int(round(W * scale)))
    h = max(1, int(round(H * scale)))
    r = ref.resize((w, h), Image.LANCZOS) if (w, h) != ref.size else ref
    canvas = Image.new("RGB", (W, H), "white")
    canvas.paste(r, (int(round(dx_mm * k)), int(round(dy_mm * k))))
    return canvas


def best_offset(base, ref, mm_w, max_mm=5.0, coarse=1.0, fine=0.25,
                probe_w=520):
    """在 ±max_mm 内找让差异最小的 (dx, dy)。

    两步走：先 1mm 粗扫定位，再在赢家附近 0.25mm 细修 —— 一遍 0.1mm
    要扫 100×100 次，慢到没法用；两步加起来只要 170 次左右。

    返回 (dx_mm, dy_mm, score)。
    """
    b = _scaled(base, probe_w)
    r = _scaled(ref, probe_w)
    W, H = b.size

    def at(dx, dy):
        canvas = Image.new("RGB", (W, H), "white")
        k = W / float(mm_w)
        canvas.paste(r, (int(round(dx * k)), int(round(dy * k))))
        return score(b, canvas)

    def sweep(cx, cy, span, step):
        best = (at(cx, cy), cx, cy)
        n = int(round(span / step))
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                dx, dy = round(cx + i * step, 3), round(cy + j * step, 3)
                if abs(dx) > max_mm or abs(dy) > max_mm:
                    continue
                s = at(dx, dy)
                if s < best[0] - 1e-6:
                    best = (s, dx, dy)
        return best

    s0, dx0, dy0 = sweep(0.0, 0.0, max_mm, coarse)
    s1, dx1, dy1 = sweep(dx0, dy0, coarse * 1.5, fine)
    return (dx1, dy1, s1) if s1 <= s0 else (dx0, dy0, s0)
