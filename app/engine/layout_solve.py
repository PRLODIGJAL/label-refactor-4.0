# -*- coding: utf-8 -*-
"""layout_solve.py —— 按叠影自动摆位（P2）。

老办法的问题
-----------
以前是"每个元素各自去叠影上找最像的一块"（`snap.find_offset`）。
每个元素独立找，就会出这些事：

  · **两块元素抢同一个墨块** —— 谁先算谁赢，另一个被挤到旁边空白处
  · **四宫格里同一句话** —— 8 个格子长一样，第 1 格的字可能被吸到第 3 格
  · **整齐的东西不齐了** —— 每块各自差 0.1mm，方向还不一样，
    本来对齐的一排变得参差

用户口径（P2）
-------------
> 元素定位改为：先对叠影做**行投影 + 连通块分割**，得到墨块清单，
> 再对元素与墨块做**一对一指派**，最后用现有**墨压墨 F1 精修**。
> **对称位置（四宫格）按行/列取中位数后统一对齐。**

三段式
-----
    ① 分割：行投影切出"文字行"，行内找连通块 → 墨块清单
    ② 指派：元素 ↔ 墨块 一对一指派（不抢、不重复）
    ③ 精修：在指派到的墨块附近用 F1 找精确偏移
    ④ 对称：同组内按行/列取中位数，统一对齐

为什么"对称取中位数"
------------------
四宫格里同一行的两格，理论上 dy 完全相同。实际算出来是 0.08 / 0.11 / 0.06 / 0.09
—— 差 0.05mm 看不出来，但**排出来就是参差**。取中位数（不是平均，避免
一个离群值把整排拽偏）统一用，四格就严丝合缝。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "app"), _HERE,
           os.path.join(_ROOT, "app", "ui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 行投影：这一行有多少比例的像素是墨，才算"有字"。
# ★ 0.004 太小（P2 实测）—— 叠影是 JPEG，整页都有一层压缩噪点，
#   实测背景行投影 0.0041。卡在这个值上等于"每行都有字"，
#   整页被并成一行。取 0.015（约背景的 3.5 倍）才能干净分开。
ROW_INK_MIN = 0.015
# 行间空隙小于它就并成同一行（mm）
ROW_GAP_MM = 0.35
# 连通块：横向间隙小于它就当同一块（mm）
COL_GAP_MM = 0.45
# 指派时允许的最大距离（mm）；超过就不配（宁可留空也不硬凑）
ASSIGN_MAX_MM = 14.0


def _proj_rows(mask, k):
    """行投影：每行墨占比。返回 list[(y, ratio)]，k = px/mm。"""
    from PIL import ImageStat
    w, h = mask.size
    rows = []
    # 按 2px 采样列，够判断"这一行有没有字"
    for y in range(h):
        band = mask.crop((0, y, w, y + 1))
        s = ImageStat.Stat(band).sum[0]
        rows.append(s / (255.0 * max(1, w)))
    return rows


def split_blocks(mask, k, row_min=ROW_INK_MIN, row_gap_mm=ROW_GAP_MM,
                 col_gap_mm=COL_GAP_MM):
    """把墨迹遮罩切成"墨块清单"（P2 第①段）。

    两步：
      ① **行投影**切出文字行 —— 每行墨占比 > row_min 的连续区间，
         间隔小于 row_gap_mm 的合并（同一行字的上下沿会抖一点）
      ② 行内**连通块** —— 按列投影找横向空隙，切开

    为什么先按行切：叠影上字是横向排的，一行里的字彼此独立。
    直接对整个图做连通域，会把上下相邻的两行粘成一块（行距小的时候）。

    Args:
        mask: L 模式墨迹遮罩（墨=255）
        k: px/mm
        row_min: 行墨占比阈值
        row_gap_mm: 行内间隙合并阈值
        col_gap_mm: 块间横向间隙阈值

    Returns:
        [{"x0","y0","x1","y1","w","h","cx","cy","ink"}]  # 全部毫米 + 原地像素框
        坐标是**遮罩的像素坐标**(px)，另外给 mm 值方便比较
    """
    from PIL import ImageStat
    w, h = mask.size
    rows = _proj_rows(mask, k)
    gap_px = max(1, int(round(row_gap_mm * k)))

    # ── ① 行区间 ─────────────────────────────────────────
    spans = []
    in_row = False
    for y, ratio in enumerate(rows):
        if ratio > row_min:
            if in_row:
                spans[-1][1] = y
            else:
                spans.append([y, y])
                in_row = True
        else:
            in_row = False
    # 合并上下沿抖动
    merged = []
    for s0, s1 in spans:
        if merged and s0 - merged[-1][1] <= gap_px:
            merged[-1][1] = s1
        else:
            merged.append([s0, s1])

    # ── ② 行内按列切块 ────────────────────────────────────
    cgap = max(1, int(round(col_gap_mm * k)))
    blocks = []
    for y0, y1 in merged:
        band = mask.crop((0, y0, w, y1 + 1))
        # 列投影
        cols = []
        for x in range(w):
            s = ImageStat.Stat(band.crop((x, 0, x + 1, band.height))).sum[0]
            cols.append(s / (255.0 * max(1, band.height)))
        # 连续有墨的列区间
        spans2 = []
        in_c = False
        for x, ratio in enumerate(cols):
            if ratio > row_min:
                if in_c:
                    spans2[-1][1] = x
                else:
                    spans2.append([x, x])
                    in_c = True
            else:
                in_c = False
        # 横向合并
        m2 = []
        for c0, c1 in spans2:
            if m2 and c0 - m2[-1][1] <= cgap:
                m2[-1][1] = c1
            else:
                m2.append([c0, c1])
        for c0, c1 in m2:
            bw, bh = c1 - c0 + 1, y1 - y0 + 1
            if bw < 2 or bh < 2:
                continue
            sub = mask.crop((c0, y0, c1 + 1, y1 + 1))
            ink = ImageStat.Stat(sub).sum[0] / 255.0
            if ink < 8:                       # 太小的点（噪点/标点）不算块
                continue
            blocks.append({
                "x0": c0, "y0": y0, "x1": c1, "y1": y1,
                "w": bw, "h": bh,
                "cx": (c0 + c1) / 2.0,
                "cy": (y0 + y1) / 2.0,
                "ink": ink,
                # 毫米版（便于跟元素坐标比较）
                "x0_mm": c0 / k, "y0_mm": y0 / k,
                "w_mm": bw / k, "h_mm": bh / k,
                "cx_mm": (c0 + c1) / 2.0 / k,
                "cy_mm": (y0 + y1) / 2.0 / k,
            })
    return blocks


def assign(elems, blocks, k, max_mm=ASSIGN_MAX_MM):
    """元素 ↔ 墨块 **一对一指派**（P2 第②段）。

    每个元素最多配一块，每块最多被一个元素用。做法：
      按"元素与墨块的尺寸差 + 中心距"打分，从最优的一对开始贪心，
      定了就从池子里拿掉（这就是一对一的保证）。

    为什么不按"每个元素找最近的"：四宫格里 8 个格子尺寸位置都一样，
    各自找最近会**一起扑向同一块**，其余 7 个去抢别的格子。

    打分用一个简单的代价：
        cost = 中心距(mm) + 2 × |尺寸比 - 1| × 元素尺寸
    尺寸差权重大一点 —— 一行小字和一大块色块中心可能挨得很近，
    但尺寸差十倍，不该配。

    Returns:
        (pairs, orphans)
        pairs:   [(elem_index, block_index, cost)]
        orphans: 没配上墨块的元素下标
    """
    import math
    cand = []
    for ei, e in enumerate(elems):
        ex, ey = e.get("cx", 0.0), e.get("cy", 0.0)
        ew, eh = max(0.1, e.get("w", 1.0)), max(0.1, e.get("h", 1.0))
        for bi, b in enumerate(blocks):
            bx, by = b["cx_mm"], b["cy_mm"]
            d = math.hypot(bx - ex, by - ey)
            if d > max_mm:
                continue
            bw, bh = max(0.1, b["w_mm"]), max(0.1, b["h_mm"])
            # 尺寸比：宽/高各自相对误差，取平均
            sd = (abs(bw / ew - 1.0) + abs(bh / eh - 1.0)) / 2.0
            cost = d + 2.0 * sd * max(ew, eh)
            cand.append((cost, ei, bi))
    cand.sort(key=lambda t: t[0])
    used_e, used_b, pairs = set(), set(), []
    for cost, ei, bi in cand:
        if ei in used_e or bi in used_b:
            continue
        used_e.add(ei)
        used_b.add(bi)
        pairs.append((ei, bi, round(cost, 3)))
    orphans = [i for i in range(len(elems)) if i not in used_e]
    return pairs, orphans


def refine(mask_elem, ref_win, radius_px, denom=None, sep_px=6):
    """在指派到的位置附近用 **F1** 精修（P2 第③段，复用 snap.find_offset）。

    Returns:
        (dx_mm, dy_mm, score, second, margin) 或 None
    """
    import snap as SNAP
    if mask_elem is None or ref_win is None:
        return None
    from PIL import Image
    em = SNAP.ink_mask(mask_elem) if mask_elem.mode == "RGB" else mask_elem
    rm = SNAP.ink_mask(ref_win) if ref_win.mode == "RGB" else ref_win
    if em.getbbox() is None or rm.getbbox() is None:
        return None
    ox, oy, best, second, margin = SNAP.find_offset_top(
        em, rm, radius_px, coarse=max(2, radius_px // 8),
        denom=SNAP.ink_amount(em), sep_px=sep_px)
    return ox, oy, best, second, margin


def median_of(vals):
    """中位数（不用 statistics 模块也行，但这里就用它，标准库）。"""
    v = sorted(float(x) for x in vals if x is not None)
    if not v:
        return 0.0
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def symmetrize(items, key_x="row", key_y="col", tol_mm=1.2):
    """**对称位置统一对齐**（P2 第④段）。

    四宫格这类东西，一行两格理论上 dy 一样、一列两格 dx 一样。
    各自算出来的偏移会差 0.03~0.08mm —— 单看没事，排一起就参差。

    做法：
      ① 按"元素原始坐标"分组：y 相近的归一行，x 相近的归一列
      ② 行内 dy 取**中位数**（不是平均：一个离群值会把整排拽偏）
      ③ 列内 dx 取中位数
      ④ 两个都取到就用（保证行对齐也保证列对齐）

    Args:
        items: [{"id", "x", "y", "dx", "dy"}]  ← x/y 是元素的**原始**坐标(mm)
        tol_mm: 多近算同一行/列

    Returns:
        {id: (dx, dy)}  调整后的偏移
    """
    if not items:
        return {}
    # 分组：按 y 排序后聚簇
    def cluster(vals, tol):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        groups, cur = [], []
        for i in order:
            if cur and vals[i] - vals[cur[-1]] > tol:
                groups.append(cur)
                cur = []
            cur.append(i)
        if cur:
            groups.append(cur)
        return groups

    ys = [it.get("y", 0.0) for it in items]
    xs = [it.get("x", 0.0) for it in items]
    row_groups = cluster(ys, tol_mm)
    col_groups = cluster(xs, tol_mm)

    out = {it["id"]: [it.get("dx", 0.0), it.get("dy", 0.0)] for it in items}
    # 行内统一 dy
    for g in row_groups:
        if len(g) < 2:
            continue
        mdy = median_of([items[i].get("dy", 0.0) for i in g])
        for i in g:
            out[items[i]["id"]][1] = mdy
    # 列内统一 dx
    for g in col_groups:
        if len(g) < 2:
            continue
        mdx = median_of([items[i].get("dx", 0.0) for i in g])
        for i in g:
            out[items[i]["id"]][0] = mdx
    return {k: (round(v[0], 4), round(v[1], 4)) for k, v in out.items()}
