# -*- coding: utf-8 -*-
"""verify.py —— L5 验证层：叠影 vs 我们放的元素，逐像素比对。

用户 2026-11-21 定的规则（严格照做）
-------------------------------
    「叠影部分用红色，我们添加的元素用绿色。如果重叠，理论上基本上只会显示
      绿色；如果有偏移，红色会露出来（上层绿色，下层红色）。
      同时为了防止耍赖，来个按钮一键上下层替换，让叠影在上方为绿、我们的元素
      为红，再核对一遍。两次结果都趋近于绿色占大部分、红色极少才合格。」

颜色 = **图层身份**（不是"对/不对"）
-----------------------------
    默认 (ref_below=True)          互换 (ref_below=False)
    ─────────────────────          ─────────────────────
    上层 = 我们的元素 → 绿          上层 = 叠影     → 绿
    下层 = 叠影       → 红          下层 = 我们的   → 红

    重合处只看得见上层 → 绿；有偏移时下层露出来 → 红边。

★★ 为什么必须是纯 PIL（2026-11-21 血的教训）
---------------------------------------
第一版用了 numpy —— 但**软件自带的 runtime/bin/python3 里没有 numpy**，
一进验证模式就 ModuleNotFoundError，异常被界面吞掉，
用户看到的就是「L5 好像没有用」。
**这个功能要跑在用户的运行时里，只能用标准库 + Pillow。**

三个必须避开的坑
-------------
① 不能比彩色块：实测叠影墨迹 904360px / 渲染 414306px（2.2 倍），
   差的全在蓝色 产品线A 字标、蓝色横幅上；规则不画彩色，比了永远全红。
   → 只比「R,G,B 都 < 130」的纯黑内容。
② 不能逐像素比边缘：渲染矢量锐利(1px) vs 叠影扫描模糊(2~3px)，
   每个字被红边包一圈，看着"全红"其实位置对（实测差 0.08~0.26mm）。
   → 两边各膨胀 3px（≈0.25mm）再比。
③ 不能用 22% 叠影验收：太淡，肉眼看不出 0.5mm 的偏。
   → L5 直接二值分色。
"""
from PIL import Image, ImageChops, ImageFilter

GREEN = (13, 158, 51)      # 上层（默认 = 我们的元素）
RED = (235, 26, 38)        # 下层（默认 = 叠影）


def _black_mask(im, thr=130):
    """纯黑内容的掩膜（L 模式，255=黑）。

    取 R/G/B 的**最大值**通道来判暗：
      · 纯黑 (0,0,0)   → max=0    → 算黑 ✅
      · 蓝色 (0,0,255) → max=255  → 不算黑 ✅（彩色块被排除）
      · 粉色 (255,192,203) → max=255 → 不算黑 ✅
    只用 max 通道就够了，而且一次 point 搞定，不用三通道分别比。
    """
    r, g, b = im.convert("RGB").split()
    mx = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return mx.point(lambda v: 255 if v < thr else 0)


def _grow(mask, rad):
    """掩膜膨胀（吸收扫描模糊导致的边缘差）。"""
    return mask.filter(ImageFilter.MaxFilter(rad * 2 + 1))


def compare(render_img, ref_img, black_thr=130, grow_px=3, ref_below=True,
            skip_boxes=None, canvas_w_mm=0.0, tpl=None):
    """生成 L5 验证图。

    Args:
        render_img: 我们渲出来的图（RGB）
        ref_img: 叠影 / 设计稿（RGB），自动缩放到同尺寸
        black_thr: 「算黑」的阈值
        grow_px: 边缘容差（像素）。3px @300dpi ≈ 0.25mm
        skip_boxes: 不用来算对齐率的矩形区（mm，[(x0,y0,x1,y1), ...]）。
        canvas_w_mm: 画布宽度（mm），把 skip_boxes 的毫米换算成像素要用。
        tpl: 给了就直接用 `frame.canvas_rect(tpl)` 取画布范围（**推荐**）。
             P1-6 用户要求：比对裁剪范围也必须走 canvas_rect，不许自己算。
             给了 tpl 时 canvas_w_mm 会被覆盖成 canvas_rect 的宽。
                    **条码位置必须传进来** —— 见下方说明。
        ref_below: True  = 叠影在下(红)、我们的在上(绿)   ← 默认
                   False = 叠影在上(绿)、我们的在下(红)   ← 互换复核

    Returns:
        (PIL.Image, dict)
            Image: 绿=上层 / 红=下层 / 白=空白
            dict:  {"align": 0~1, "green_px": int, "red_px": int,
                    "ref_below": bool}
    """
    if ref_img.size != render_img.size:
        ref_img = ref_img.convert("RGB").resize(render_img.size, Image.LANCZOS)
    r = render_img.convert("RGB")

    r_on = _black_mask(r, black_thr)             # 255 = 有黑
    g_on = _black_mask(ref_img, black_thr)

    r_big = _grow(r_on, grow_px)
    g_big = _grow(g_on, grow_px)

    # 重合：我这边的黑，落在对方"膨胀后"的范围里
    both = ImageChops.lighter(ImageChops.multiply(r_on, g_big),
                              ImageChops.multiply(g_on, r_big))
    ink = ImageChops.lighter(r_on, g_on)
    only = ImageChops.subtract(ink, both)        # 只在一边（= 错位）

    # 上色：先铺下层红，再铺上层绿（重合处被绿盖住）
    out = Image.new("RGB", r.size, (255, 255, 255))
    out.paste(Image.new("RGB", r.size, RED), (0, 0), only)
    if ref_below:
        out.paste(Image.new("RGB", r.size, GREEN), (0, 0), r_on)   # 上层=我们的
    else:
        out.paste(Image.new("RGB", r.size, GREEN), (0, 0), g_on)   # 上层=叠影
    # 重合区明确压成绿（上层边缘外的重合部分不留红）
    out.paste(Image.new("RGB", r.size, GREEN), (0, 0), both)

    def _cnt(mask):
        return mask.histogram()[255]

    # ★ 排除区（2026-11-23 用户定稿）
    # 对齐的评判对象只有两样：**文字 + 矢量图库件**。
    # 条码不参与 —— 它是数据生成的码，不是图库矢量件；P0-3 定过
    # 「L2 只能取自图库」，模板里本来就不该有它。叠影上有它，把它算进
    # 分母只会平白拉低对齐率，还会把"文字有没有对上"这个真问题盖住。
    # 彩色块同理（规则里不画彩色）。
    #
    # 图上**照样画出来**（让人看见这里为什么红的），只是不进分母。
    # ★ 画布宽度只从 canvas_rect 取（P1-6）
    if tpl is not None:
        try:
            import frame as _FR
            canvas_w_mm = _FR.canvas_rect(tpl)["w"]
        except Exception:
            pass
    if skip_boxes and canvas_w_mm > 0:
        from PIL import ImageDraw
        _m = Image.new("L", r.size, 255)          # 255 = 参与统计
        _d = ImageDraw.Draw(_m)
        _kk = float(r.size[0]) / float(canvas_w_mm)
        for bx in skip_boxes:
            _d.rectangle([int(bx[0] * _kk), int(bx[1] * _kk),
                          int(bx[2] * _kk), int(bx[3] * _kk)], fill=0)
        _keep = _m.point(lambda v: 255 if v > 127 else 0)
        ink = ImageChops.multiply(ink, _keep)
        both = ImageChops.multiply(both, _keep)

    total = max(1, _cnt(ink))
    stats = {
        "align": float(_cnt(both)) / total,
        "green_px": _cnt(both),
        "red_px": max(0, _cnt(ink) - _cnt(both)),
        "ref_below": bool(ref_below),
        "skipped": len(skip_boxes or []),
    }
    return out, stats


def judge(stats_a, stats_b, min_align=0.80):
    """两次（默认 + 互换）结果都达标才算合格。"""
    a, b = stats_a.get("align", 0), stats_b.get("align", 0)
    ok = a >= min_align and b >= min_align
    msg = ("默认方向对齐 %.1f%%，互换方向对齐 %.1f%%（要求两次都 ≥ %.0f%%）"
           % (a * 100, b * 100, min_align * 100))
    if ok:
        return True, "✅ 合格：" + msg
    worse = "默认" if a < b else "互换"
    return False, "❌ 不合格：%s，%s方向更差" % (msg, worse)
