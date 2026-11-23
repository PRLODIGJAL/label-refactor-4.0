# -*- coding: utf-8 -*-
"""frame.py —— L1 黑框检测与白边（P1-6）。

问题背景
-------
叠影/设计稿外面有一圈**刀模框线**（细蓝线或黑线）。画布如果按"整张图"
算，元素坐标就会被拉宽 ~1%。
实测 DWG 0000003 产品线B：
    整图 1357×1472 px → 画布 181.61×196.00 mm
    黑框在 (8,7)-(1350,1464)，框内 1342×1457 = 整图的 98.89%×98.98%
    元素坐标按框内做的，却被画在整图画布上 → 横向拉宽 1.01 倍
    表现：渲染墨迹 bbox 左 1.77%/右 97.86%，叠影 0.23%/100.00%
          整体窄 1.5%，看起来"整版都不齐"

用户口径（P1-6）
-------------
> 先检查 参考/叠影_DWG-*.jpg 是否带黑框。没有则改用 vec_asset.vec_clip
> 从图框外沿开始取矢量层，再与位图合成。
> 黑框外沿用**行/列投影的长直线**判定，不用任意墨点。
> 白画布 = 黑框外沿向外各 m 毫米（m 可设置，默认 3，四边相等），
> 坐标原点仍为黑框外沿左上角，白边走"纸外空白"。
> 验收：四边白边宽度相差 ≤ 1px。

**为什么不用"任意墨点"**：设计稿里有大量黑色文字。拿墨点反推边界，
第一行文字会冒充上边框；而且文字稀疏的行列投影很低，完全测不出来。
只有**长直线**（一整行/一整列几乎全是暗像素）才是框线。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "app"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 判"暗"的阈值（0~255）。框线常常是细蓝线，JPEG 压缩后偏淡，
# 130 能把 (60,90,160) 这种蓝线也算进来，同时排除浅灰底纹。
DARK_THR = 130

# 判"这是一整行/整列的线"的投影占比。
# 0.75 而不是 0.95：框线是 1~2px 细线，JPEG 会把其中某些像素抬亮，
# 实测最高投影 93%（永远到不了 95%）。但文字行的投影通常 <60%，
# 0.75 能干净地区分"线"和"字"。
LINE_RATIO = 0.75

# 画布边距（白边）默认值。用户 P1-6 原文：
#   「模板新增 "画布边距_mm"（默认取软件设置里的全局默认，初始 4，范围 0~20）」
DEFAULT_MARGIN_MM = 4.0
MARGIN_MIN, MARGIN_MAX = 0.0, 20.0


def margin_of(tpl):
    """这个模板的画布边距（毫米）。**全项目唯一取值入口。**

    优先 tpl["画布边距_mm"]，其次软件设置里的全局默认，最后 DEFAULT_MARGIN_MM。
    范围钳到 0~20（用户定），越界的值不报错、直接钳 —— 这类数值没必要
    让用户被一个提示框打断，钳住并继续画更合适。
    """
    v = (tpl or {}).get("画布边距_mm")
    if v is None:
        v = global_default_margin()
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = DEFAULT_MARGIN_MM
    if v != v:                                    # NaN
        v = DEFAULT_MARGIN_MM
    return max(MARGIN_MIN, min(MARGIN_MAX, v))


def global_default_margin():
    """软件设置里的全局默认画布边距。读不到就返回 DEFAULT_MARGIN_MM。"""
    try:
        import json
        import paths
        with open(paths.SETTINGS, encoding="utf-8") as f:
            d = json.load(f)
        v = d.get("画布边距_mm")
        if v is None:
            return DEFAULT_MARGIN_MM
        return max(MARGIN_MIN, min(MARGIN_MAX, float(v)))
    except Exception:
        return DEFAULT_MARGIN_MM


def canvas_rect(tpl):
    """★ **画布范围唯一来源**（P1-6 用户要求）。

    > 画布预览、save_png、save_pdf、save_batch_pdf、PDF 文字层坐标、
    > verify 的比对裁剪范围，全部通过同一个函数 canvas_rect(tpl) 取范围，
    > 不允许各自再算。

    以前 40 处各自读 `tpl["画布"]["宽"]` 再自己加/减边距，改一处置漏一处，
    表现就是"预览和导出差几毫米""PDF 页面比画布小一圈"。

    坐标约定（用户定稿）：
      · **原点 = 黑框外沿左上角**
      · 白边各 m 毫米 → **白边区域是负坐标**（-m ~ 0，以及 W ~ W+m）
      · 改 m **不移动任何元素** —— 元素坐标是相对黑框的，m 只影响画布多大

    Returns:
        dict:
          x0, y0, x1, y1    画布范围（毫米，含负值）。画布 = [x0,x1)×[y0,y1)
          w, h              画布宽高（毫米）
          margin            边距 m
          frame             {"w","h"} 黑框宽高（毫米）；没有图框记录时等于 w-2m
          has_frame         有没有图框记录
          origin            原点说明（调试用）
    """
    m = margin_of(tpl)
    fr = (tpl or {}).get("图框") or None
    cv = (tpl or {}).get("画布") or {}
    try:
        cw = float(cv.get("宽") or 0)
        ch = float(cv.get("高") or 0)
    except (TypeError, ValueError):
        cw = ch = 0.0

    if fr and fr.get("框mm"):
        # 有图框记录：框内尺寸是权威值，画布 = 框内 + 2m
        bw = float(fr["框mm"][0])
        bh = float(fr["框mm"][1])
    else:
        # 没有图框记录：画布就是框（下面把 m 归零）
        bw = cw
        bh = ch

    # ★ 没有图框记录时：**现有画布就是"框内"**（P1-6 修正 2）
    #   走过两版弯路，记下来：
    #     第一版「框内 = 画布 - 2m」→ 把画布往里缩了，元素全错位，
    #       对齐率 80.44% → 25.57%。
    #     第二版「没有框就 margin=0」→ 对齐率回来了，但**UI 调边距没反应**，
    #       用户拖数值框什么都不变，等于这个功能不存在。
    #   正解：**画布尺寸 = 框内尺寸**，白边加在它外面。
    #   这样 m=0 时画布不变（等价于没有白边，与升级前一致），
    #   调大 m 画布就变大、元素不动 —— 正是用户要的行为。
    #   有图框记录时，框内取记录里的「框mm」（更准）。
    # ★★ 最终定案（P1-6，走了三版弯路）★★
    #
    #   **没有图框记录 → m 不生效**（画布 = 框内 = tpl["画布"]，白边 0）。
    #
    #   为什么必须这样：088 升级前画布 185.5 且没有白边，参考图
    #   叠影_L1.png 就是按 185.5 铺满的。如果"没有框也加 m"，
    #   画布变 193.5、参考图还是按 185.5 铺 → 整体错位 4mm，
    #   对齐率 80.44% → 36.71%（实测）。
    #   白边的前提是"知道框在哪"；不知道框就不该凭空加白边。
    #
    #   UI 那边调边距时会**先自动建立图框记录**（见 apply_canvas_margin），
    #   所以用户不会遇到"调了没反应"。
    has_fr = bool(fr and fr.get("框mm"))
    if not has_fr:
        bw, bh = cw, ch          # 没有框：画布就是框内
        m = 0.0                  # 白边不生效
    w = bw + 2 * m
    h = bh + 2 * m
    # ★ 绝不在这里改 tpl（P1-6 修正 3）
    #   上一版在这里"顺手同步 tpl['画布']"，想着让两个来源不分叉 ——
    #   结果是个**累积放大**的灾难：
    #     没有图框时"框内 = 画布"，调一次 canvas_rect 就把画布写成 画布+2m，
    #     再调一次又 +2m …… 输入框每敲一个字符就调一次，画布一路涨到 265mm。
    #   查询函数不该有副作用。要同步就由**调用方显式写**
    #   （apply_canvas_margin / apply_to_tpl 里那两个地方）。
    return {
        "x0": -m, "y0": -m, "x1": -m + w, "y1": -m + h,
        "w": w, "h": h,
        "margin": m,
        "frame": {"w": bw, "h": bh},
        "has_frame": bool(fr and fr.get("框mm")),
        "origin": "黑框外沿左上角",
    }


def _proj(im):
    """行/列投影：每行、每列的暗像素占比。"""
    w, h = im.size
    px = im.load()
    # 每 1px 都统计太慢（1357×1472 = 200 万次）。按 2px 采样，
    # 对"找一条贯穿的线"完全够 —— 线是连续的，采样不影响它的占比。
    step = 2
    rows = []
    for y in range(h):
        n = 0
        c = 0
        for x in range(0, w, step):
            c += 1
            if px[x, y] < DARK_THR:
                n += 1
        rows.append(n / float(c) if c else 0.0)
    cols = []
    for x in range(w):
        n = 0
        c = 0
        for y in range(0, h, step):
            c += 1
            if px[x, y] < DARK_THR:
                n += 1
        cols.append(n / float(c) if c else 0.0)
    return rows, cols


def _cluster(idxs, gap=3):
    """把连续（允许 gap 的断裂）的序号聚成组，返回 [(起, 止), ...]。"""
    out = []
    for i in idxs:
        if out and i - out[-1][1] <= gap:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def find_frame(im, ratio=None):
    """找黑框外沿。返回 dict，找不到返回 None。

    Args:
        im: PIL 图（任意模式，内部转 L）
        ratio: 投影占比阈值，默认 LINE_RATIO

    Returns:
        {
          "box": (left, top, right, bottom),      # 外沿像素坐标（含）
          "w_px"/"h_px": 框宽/高,
          "image": (w, h),
          "lines": {"rows": [...], "cols": [...]}, # 检出的线位置
          "margin_px": {"left","top","right","bottom"},  # 各边到图边距离
        }
        找不到（没有贯穿长线）返回 None
    """
    ratio = LINE_RATIO if ratio is None else ratio
    g = im.convert("L")
    w, h = g.size
    rows, cols = _proj(g)

    r_lines = [i for i, v in enumerate(rows) if v >= ratio]
    c_lines = [i for i, v in enumerate(cols) if v >= ratio]
    if not r_lines or not c_lines:
        return None

    # 外沿 = 最外侧的那条线。取**簇的最外端**（一簇 3px 宽的线取最外一像素）
    rc = _cluster(r_lines)
    cc = _cluster(c_lines)
    top = rc[0][0]
    bottom = rc[-1][1]
    left = cc[0][0]
    right = cc[-1][1]

    # 合理性：框必须围住要够大的区域（至少占图 50%），否则不是"图框"
    if (right - left) < w * 0.5 or (bottom - top) < h * 0.5:
        return None

    return {
        "box": (left, top, right, bottom),
        "w_px": right - left + 1,
        "h_px": bottom - top + 1,
        "image": (w, h),
        "lines": {"rows": r_lines, "cols": c_lines},
        "margin_px": {
            "left": left, "top": top,
            "right": w - 1 - right, "bottom": h - 1 - bottom,
        },
    }


def has_frame(path):
    """这个文件有没有黑框。返回 (有无, 详情dict)。"""
    from PIL import Image
    try:
        im = Image.open(path)
    except Exception as e:                              # noqa: BLE001
        return False, {"error": str(e)}
    f = find_frame(im)
    return (f is not None), (f or {})


def canvas_of(path, margin_mm=DEFAULT_MARGIN_MM):
    """给定的参考图 → 画布几何（含黑框白边）。

    **坐标原点 = 黑框外沿左上角**（用户口径）。
    白画布 = 黑框外沿向外各 margin_mm 毫米，四边相等。

    Returns:
        {
          "ok": bool,
          "frame": 框信息,
          "canvas_mm": {"宽","高"},        # = 框内 + 两侧 margin
          "origin_px": (left, top),        # 原点在**原图**里的像素坐标
          "px_per_mm": (kx, ky),
          "white_mm": {"left","top","right","bottom"},   # 四边白边
          "reason": 失败原因
        }
    """
    from PIL import Image
    try:
        im = Image.open(path)
    except Exception as e:                              # noqa: BLE001
        return {"ok": False, "reason": "读不了图：%s" % e}
    f = find_frame(im)
    if f is None:
        return {"ok": False,
                "reason": "这张图没有贯穿整幅的长直线（不是黑框图）",
                "image": im.size}
    w, h = im.size
    left, top, right, bottom = f["box"]
    # 图框本身多少毫米：没法从图里知道绝对尺寸，只能按**原图画布**
    # （外部传进来的 mm）换算。所以这里返回**像素几何**，
    # 毫米换算交给调用方（它知道画布该多大）。
    return {
        "ok": True,
        "frame": f,
        "img_px": (w, h),
        "box_px": (left, top, right, bottom),
        "box_w_px": f["w_px"],
        "box_h_px": f["h_px"],
        "origin_px": (left, top),
        "white_px": {"left": left, "top": top,
                     "right": w - 1 - right, "bottom": h - 1 - bottom},
    }


def canvas_mm_for(path, canvas_w_mm, canvas_h_mm, margin_mm=DEFAULT_MARGIN_MM):
    """按「黑框 + 白边」重算画布尺寸（毫米）。

    Args:
        path: 参考图
        canvas_w_mm / canvas_h_mm: **当前**模板画布尺寸（= 整图尺寸）
        margin_mm: 白边宽（四边相等）

    Returns:
        dict:
          ok           能否算
          box_mm       黑框的毫米尺寸
          canvas_mm    新画布 = 黑框 + 2×margin
          shift_mm     元素坐标要往左上平移多少（原点移到框外沿）
          scale        X/Y 缩放系数（老坐标 → 新坐标）
          reason
    """
    info = canvas_of(path, margin_mm)
    if not info.get("ok"):
        return {"ok": False, "reason": info.get("reason")}
    w, h = info["img_px"]
    left, top, right, bottom = info["box_px"]
    # 原画布对应整张图 → 每毫米多少像素
    kx = w / float(canvas_w_mm)
    ky = h / float(canvas_h_mm)
    box_w_mm = info["box_w_px"] / kx
    box_h_mm = info["box_h_px"] / ky
    return {
        "ok": True,
        "img_px": (w, h),
        "box_px": (left, top, right, bottom),
        "px_per_mm": (kx, ky),
        "box_mm": {"宽": round(box_w_mm, 4), "高": round(box_h_mm, 4)},
        "canvas_mm": {"宽": round(box_w_mm + 2 * margin_mm, 4),
                      "高": round(box_h_mm + 2 * margin_mm, 4)},
        # 老坐标原点在图左上角；新原点在框外沿 → 往左上平移
        "shift_mm": {"dx": round(-left / kx, 4), "dy": round(-top / ky, 4)},
        "white_mm": {"left": round(left / kx, 4), "top": round(top / ky, 4),
                     "right": round((w - 1 - right) / kx, 4),
                     "bottom": round((h - 1 - bottom) / ky, 4)},
        "margin_mm": margin_mm,
    }


def apply_to_tpl(tpl, tpl_name, margin_mm=DEFAULT_MARGIN_MM, dry_run=True):
    """把「黑框 + 白边」应用到模板：改画布尺寸 + 平移所有元素。

    用户口径（1996 明确确认）：
      · **白边算在画布里** —— 新画布 = 框内尺寸 + 2×margin
      · 坐标原点 = 黑框外沿左上角（不是整图左上角）
      · 所以所有元素坐标要**平移**：新坐标 = 旧坐标 - 框外沿在老画布上的位置
        + margin（因为白边占了画布最外面那圈）

    注意这里**不动参考图文件**：参考图还是那张整图，只是模板记下
    「框在哪、白边多少」，以后取参考窗口时按记下的框来换算。
    参考图不重裁 —— 重裁会丢信息，而且改完发现不对就回不去了。

    Args:
        tpl: 模板 dict（就地改）
        tpl_name: 模板名（找参考图用）
        margin_mm: 白边宽
        dry_run: True 只算不改（返回报告）

    Returns:
        dict 报告：新画布 / 平移量 / 搬了几个元素
    """
    import store
    ref = tpl.get("参考图")
    if not ref:
        return {"ok": False, "reason": "没有参考图（叠影）"}
    path = store.resolve_ref(tpl_name, ref)
    if not path or not os.path.isfile(path):
        return {"ok": False, "reason": "参考图文件找不到：%s" % ref}

    cw = float((tpl.get("画布") or {}).get("宽") or 0)
    ch = float((tpl.get("画布") or {}).get("高") or 0)
    if cw <= 0 or ch <= 0:
        return {"ok": False, "reason": "画布尺寸无效"}

    r = canvas_mm_for(path, cw, ch, margin_mm)
    if not r.get("ok"):
        return {"ok": False, "reason": r.get("reason")}

    # 老画布(整图) 每毫米像素
    w_px, h_px = r["img_px"]
    left, top, right, bottom = r["box_px"]
    kx, ky = w_px / cw, h_px / ch
    # 框外沿在**老画布坐标**里的位置（毫米）
    fx, fy = left / kx, top / ky
    # 新坐标 = 老坐标 - 框外沿 + margin
    dx = margin_mm - fx
    dy = margin_mm - fy

    box_w_px = right - left + 1
    box_h_px = bottom - top + 1
    new_cw = box_w_px / kx + 2 * margin_mm
    new_ch = box_h_px / ky + 2 * margin_mm

    moved = 0
    if not dry_run:
        def _shift(el):
            nonlocal moved
            if el.get("x") is None:
                return
            el["x"] = round(float(el["x"]) + dx, 3)
            el["y"] = round(float(el.get("y") or 0) + dy, 3)
            moved += 1

        for b in tpl.get("文字") or []:
            _shift(b)
        for g in tpl.get("图形") or []:
            # 顺应性卡这类用 x0/x1/ys 描述的，四个方向都要移
            if g.get("x0") is not None:
                g["x0"] = round(float(g["x0"]) + dx, 3)
                g["x1"] = round(float(g.get("x1") or 0) + dx, 3)
                ys = g.get("ys") or []
                g["ys"] = [round(float(v) + dy, 3) for v in ys]
                moved += 1
            else:
                _shift(g)
        # 组内的元素表（位置/尺寸）也一起移
        for grp in tpl.get("组") or []:
            for pos in grp.get("位置") or []:
                if pos.get("x") is not None:
                    pos["x"] = round(float(pos["x"]) + dx, 3)
                    pos["y"] = round(float(pos.get("y") or 0) + dy, 3)
        # ★ 画布尺寸 = 框内 + 2m，但**下次改 m 时不能再动它** ——
        #   框内尺寸记在「图框.框mm」里，canvas_rect 由它 + m 现算。
        tpl["画布边距_mm"] = round(margin_mm, 3)
        tpl["图框"] = {
            "框像素": [left, top, right, bottom],
            "框mm": [round(box_w_px / kx, 3), round(box_h_px / ky, 3)],
            "原画布": [round(cw, 3), round(ch, 3)],
            "原点": "黑框外沿左上角",
        }
        # ★ tpl["画布"] 存**框内尺寸**（不是画布总尺寸）
        #   总尺寸 = 框内 + 2m，一律用 canvas_rect 现算。
        #   把总尺寸写进"画布"会让下次 apply 再 +2m，累积放大。
        tpl["画布"] = {"宽": round(box_w_px / kx, 3),
                       "高": round(box_h_px / ky, 3)}

    return {
        "ok": True,
        "old_canvas": {"宽": round(cw, 3), "高": round(ch, 3)},
        "new_canvas": {"宽": round(new_cw, 3), "高": round(new_ch, 3)},
        "box_px": [left, top, right, bottom],
        "box_mm": {"宽": round(box_w_px / kx, 3),
                   "高": round(box_h_px / ky, 3)},
        "shift_mm": {"dx": round(dx, 3), "dy": round(dy, 3)},
        "margin_mm": margin_mm,
        "moved": moved if not dry_run else None,
        "dry_run": dry_run,
    }


def ref_page_for(tpl, ref_img, size):
    """把参考图按「图框」换算成**和画布同尺寸**的一张图（P1-6 共用）。

    三处都要用同一条换算：界面预览（_ref_image）、L5 验证（score）、
    CLI 的 score 命令。别各写各的 —— 三处不一致的话，屏幕上"对齐了"、
    CLI 报"没对齐"，人会以为软件坏了。

    **只依赖 canvas_rect**（P1-6 用户要求）：
      画布 = 框内 + 2m，框内那块贴参考图的「框像素」区域，
      周围 m 毫米留白。所以参考图永远贴得和画布严丝合缝。

    Args:
        tpl: 模板
        ref_img: 参考图原图（PIL RGB）
        size: 目标像素尺寸 (w, h) —— 通常是画布按 dpi 换算出来的像素尺寸

    Returns:
        和 size 同尺寸的对齐图；没有图框记录时按整图铺满（老行为）
    """
    from PIL import Image
    fr = (tpl or {}).get("图框") or {}
    r = canvas_rect(tpl)
    bx = fr.get("框像素")
    if bx and r["margin"] >= 0:
        try:
            bx0, by0, bx1, by1 = [int(v) for v in bx]
            kx = size[0] / float(r["w"])          # 画布 px/mm
            ky = size[1] / float(r["h"])
            m = r["margin"]
            # ① 裁出框内那块
            box = ref_img.crop((bx0, by0, bx1 + 1, by1 + 1))
            # ② 缩放到「框内」应有的像素尺寸
            bw = max(2, int(round(r["frame"]["w"] * kx)))
            bh = max(2, int(round(r["frame"]["h"] * ky)))
            box = box.resize((bw, bh), Image.LANCZOS)
            # ③ 贴到 (m, m)，四周留白
            #    用 int(round()) 保证四边等宽（验收 2：四边白边相差 ≤1px）
            ox = int(round(m * kx))
            oy = int(round(m * ky))
            out = Image.new("RGB", size, "white")
            out.paste(box, (ox, oy))
            return out
        except Exception:
            pass
    return ref_img.resize(size, Image.LANCZOS)
