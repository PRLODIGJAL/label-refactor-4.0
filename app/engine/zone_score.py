# -*- coding: utf-8 -*-
"""zone_score.py —— 分区域对齐验收（P2）。

为什么不能只看整体分
------------------
`verify.compare` 给一个总对齐率。80% 听起来不错，但可能是
"左边全对、右边全错" —— 那种标签印出来是废的，总分照样 80。

用户口径（P2）
-------------
> 整体验收用 verify.compare：**分区域达标才允许停止**，不要只看整体分。

做法
----
把画布切成 N×M 个区域，逐区算对齐率。**每一个区**都要过线才算达标。

为什么不用固定网格
----------------
固定切成 4×4 的话，标签正中间那条分割线可能刚好切在某行字中间，
把一个好区劈成两个差的。所以先把**版面结构**（行投影得到的文字行）
当作天然分界，再在每行里按列分。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "app"), _HERE,
           os.path.join(_ROOT, "app", "ui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 一个区域达到这个对齐率算"过"
ZONE_PASS = 0.60
# 区域里墨太少（几乎空白）就不参与打分 —— 空白区永远 100%，拉高总分会骗人
ZONE_MIN_INK = 0.002


def zones_by_rows(gray_ref, w_mm, rows=8, cols=4, min_ink=ZONE_MIN_INK):
    """按固定行列切区（简单可靠，先给出稳定的分区域口径）。

    Args:
        gray_ref: 参考图（L 模式）
        w_mm: 画布宽（毫米，只用于报错时说明）
        rows / cols: 切几行几列

    Returns:
        [{"idx", "r", "c", "box"(px), "x_mm","y_mm","w_mm","h_mm"}]
    """
    W, H = gray_ref.size
    out = []
    for r in range(rows):
        for c in range(cols):
            x0 = int(round(W * c / float(cols)))
            x1 = int(round(W * (c + 1) / float(cols)))
            y0 = int(round(H * r / float(rows)))
            y1 = int(round(H * (r + 1) / float(rows)))
            if x1 <= x0 or y1 <= y0:
                continue
            out.append({
                "idx": r * cols + c, "r": r, "c": c,
                "box": (x0, y0, x1, y1),
                "x_mm": x0 / float(W) * w_mm,
                "y_mm": y0 / float(H) * w_mm,
                "w_mm": (x1 - x0) / float(W) * w_mm,
                "h_mm": (y1 - y0) / float(H) * w_mm,
            })
    return out


def score_zones(render_img, ref_img, w_mm, rows=8, cols=4,
                black_thr=130, skip_boxes=None, min_ink=ZONE_MIN_INK):
    """逐区算对齐率（不依赖 numpy，纯 PIL + 计数）。

    对齐的定义和 `verify.compare` 一致：
        该区里"渲染有墨且参考也有墨"的像素 占 "两图中至少一个有墨"的像素。
    只是把统计范围限制在每个小框里。

    Args:
        render_img / ref_img: 同尺寸 RGB 图
        w_mm: 画布宽（mm），用来把 skip_boxes 的毫米换算成像素
        rows / cols: 切几行几列
        skip_boxes: 不参与的区域（mm，[(x0,y0,x1,y1), ...]）
        min_ink: 区域墨太少就不参与

    Returns:
        {
          "zones": [{idx,r,c,align,ink_ratio,skip}],
          "worst": {...},
          "passed": bool,
          "pass_line": ZONE_PASS,
          "n_ok"/"n_bad"/"n_skip",
        }
    """
    from PIL import ImageChops, ImageStat

    if render_img.size != ref_img.size:
        ref_img = ref_img.resize(render_img.size, getattr(
            __import__("PIL.Image", fromlist=["Image"]), "LANCZOS"))

    def ink(im):
        g = im.convert("L")
        return g.point(lambda v: 255 if v < black_thr else 0)

    A = ink(render_img)
    B = ink(ref_img)
    W, H = A.size
    kx = W / float(w_mm) if w_mm else 1.0
    sky = H / float(w_mm) if w_mm else 1.0      # 高也按同一比例（cmm 宽高比一致）

    skips_px = []
    for bx in (skip_boxes or []):
        try:
            x0, y0, x1, y1 = [float(v) for v in bx]
        except (TypeError, ValueError):
            continue
        skips_px.append((int(x0 * kx), int(y0 * sky),
                         int(x1 * kx), int(y1 * sky)))

    zones = zones_by_rows(ref_img.convert("L"), w_mm, rows, cols)
    out = []
    for z in zones:
        bx0, by0, bx1, by1 = z["box"]
        # 跳过框：整区落在某个 skip 里就不算
        if any(bx0 >= s[0] and by0 >= s[1] and bx1 <= s[2] and by1 <= s[3]
               for s in skips_px):
            out.append({**{kk: z[kk] for kk in ("idx", "r", "c")},
                        "align": None, "ink_ratio": 0.0, "skip": True})
            continue
        a = A.crop(z["box"])
        b = B.crop(z["box"])
        sa = ImageStat.Stat(a).sum[0] / 255.0
        sb = ImageStat.Stat(b).sum[0] / 255.0
        both = ImageChops.multiply(a, b)
        sboth = ImageStat.Stat(both).sum[0] / 255.0
        union = sa + sb - sboth
        area = a.width * a.height
        ink_ratio = union / float(area) if area else 0.0
        if union <= 0:
            align = 1.0                 # 两图都空 → 一致
        else:
            align = sboth / union
        if ink_ratio < min_ink:
            out.append({**{kk: z[kk] for kk in ("idx", "r", "c")},
                        "align": None, "ink_ratio": round(ink_ratio, 5),
                        "skip": True, "reason": "墨太少"})
        else:
            out.append({**{kk: z[kk] for kk in ("idx", "r", "c")},
                        "align": round(align, 4),
                        "ink_ratio": round(ink_ratio, 5), "skip": False})

    scored = [z for z in out if not z["skip"]]
    bad = [z for z in scored if z["align"] < ZONE_PASS]
    worst = min(scored, key=lambda z: z["align"]) if scored else None
    return {
        "zones": out,
        "worst": worst,
        "passed": len(bad) == 0,
        "pass_line": ZONE_PASS,
        "n_ok": len(scored) - len(bad),
        "n_bad": len(bad),
        "n_skip": len(out) - len(scored),
        "bad_zones": [(z["r"], z["c"], z["align"]) for z in
                      sorted(bad, key=lambda z: z["align"])],
    }


def format_report(res, rows=8, cols=4):
    """把分区结果画成一张 ASCII 热力图（命令行/日志里一眼看得出哪块差）。"""
    grid = {}
    for z in res["zones"]:
        grid[(z["r"], z["c"])] = z
    lines = []
    lines.append("分区对齐率（%d 行 × %d 列，过线 %.0f%%）"
                 % (rows, cols, res["pass_line"] * 100))
    marks = []
    for r in range(rows):
        row = []
        for c in range(cols):
            z = grid.get((r, c))
            if z is None:
                row.append("  --  ")
            elif z["skip"]:
                row.append("  ..  ")
            elif z["align"] >= res["pass_line"]:
                row.append(" %3.0f%% " % (z["align"] * 100))
            else:
                row.append("[%3.0f%%]" % (z["align"] * 100))
        marks.append(" ".join(row))
    lines.extend(marks)
    lines.append("  [xx%%] = 不达标   .. = 墨太少跳过   -- = 不参与")
    lines.append("达标 %d / 不达标 %d / 跳过 %d"
                 % (res["n_ok"], res["n_bad"], res["n_skip"]))
    if res["worst"]:
        w = res["worst"]
        lines.append("最差区：第 %d 行第 %d 列 %.1f%%"
                     % (w["r"] + 1, w["c"] + 1, w["align"] * 100))
    lines.append("总判定：" + ("✅ 分区全部达标" if res["passed"]
                               else "❌ 还有 %d 个分区不达标" % res["n_bad"]))
    return "\n".join(lines)
