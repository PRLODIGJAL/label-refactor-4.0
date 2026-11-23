# -*- coding: utf-8 -*-
"""fitwidth.py —— 「⌖ 按叠影定宽」的求解器（P1-5）。

用户口径
-------
> 由叠影墨迹框取 H、W，先定字号，再先词距后字距摊平差值；
> 字距超出字高 ±10% 时先换相邻字重，仍超限则标红。
> L4 存 h 和 tracking，不存宽度；在"叠影数据"那组上求解，所有规格共用。
> 验收：定宽后墨迹宽误差 ≤ 0.2mm，字形纵横比不变（xs=1）。

求解顺序（**这个顺序不能换**）
--------------------------
    ① 由叠影量到 H、W（墨迹框）
    ② H 定字号 → 量出"自然宽" W0（tracking=0, word_gap=0）
    ③ 差值 D = W - W0
    ④ **先摊词距**：D / 空格数，封顶 ±0.35 个字高（词距可以比字距大，
       因为词与词之间本来就有空隙，加宽不显眼）
    ⑤ **再摊字距**：剩下的 D / (字数-1)
    ⑥ 字距超出字高 ±10% → 试**相邻字重**（细↔常规↔中↔粗）重算 ②~⑤
    ⑦ 还是超限 → 标红（`over_limit=True`），给出最接近的方案供人判断

为什么要"先词距后字距"
--------------------
词距加了不改变字与字的黏连感；字距一加，整行看起来就"散了"。
所以能用词距解决的，绝不先动字距。

为什么字距限 ±10% 字高
--------------------
实测下来，字距超过字高 10% 就开始像"排字没排紧"，
负的超过 10% 会粘连。到这个程度该换字重 —— 换字重改变的是笔画粗细，
比硬拉字距自然得多。

不存宽度
------
L4 元素的内容会随规格变（2.75mm → 3.50mm），宽度跟着变，
存宽度等于把某一规格的宽度锁死。只存 `h`（字号依据）+ `tracking`
（相对量），任何规格都能算出正确的宽度。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "app"), _HERE, os.path.join(_ROOT, "app", "ui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import labelkit as LK  # noqa: E402

# 字距上限：字高的 ±10%（用户定的）
TRACK_LIMIT = 0.10
# 词距上限：字高的 ±35%（词距宽松些 —— 词间本来就有空）
WORDGAP_LIMIT = 0.35

# 字重邻居表：从当前字面能换到哪些相邻字重
_WEIGHT_NEIGHBORS = {
    "siyuanl": ("siyuan",),
    "siyuan": ("siyuanl", "siyuanm"),
    "siyuanm": ("siyuan", "siyuanb"),
    "siyuanb": ("siyuanm",),
    "hei": ("heib",),
    "heib": ("hei",),
    "song": ("songb",),
    "songb": ("song",),
}


def neighbors(kind):
    """相邻字重（先粗后细的优先级：往字距更容易收敛的方向试）。"""
    return _WEIGHT_NEIGHBORS.get(kind, ())


def _measure(s, h, kind, tracking=0.0, word_gap=0.0, dpi=None):
    """按目标字高 h 定字号，返回 (墨迹宽mm, 墨迹高mm, 实际字号mm)。

    ★ 和 `Canvas.text_tracked` 走完全同一条路（同一个度量），
      否则"求解出来的"和"真画出来的"会差 —— 这正是 P1-5 里踩过的坑。
    """
    # 度量**固定**在 labelkit.MEASURE_DPI 下，不跟调用方 dpi 走
    dpi = dpi or LK.MEASURE_DPI
    cv = LK.Canvas(20, 20, dpi)          # 只为借用它的 measure
    w0, h0, _bb = cv.measure(s, 10.0, kind)
    if h0 <= 0:
        return 0.0, 0.0, 0.0
    size = 10.0 * h / h0

    # ★ 必须返回**真实墨迹宽**，不是 advance 行宽（P1-5 验收）
    #   验收条件是"墨迹宽误差 ≤0.2mm"，量的对象是屏幕上那块墨。
    #   用 advance 行宽去求解会系统性偏大 0.4~0.6mm（末字右侧 bearing），
    #   于是"解出来宽 25.0，画出来 24.6" —— 正是这一版踩的坑。
    #   所以这里直接开一块够大的画布**真画一遍**再量（text_tracked 的
    #   返回值本身就是墨迹宽，见 Canvas.text_tracked 的注释）。
    # ★ 画布必须开**足够宽**（P1-5 踩过）
    #   text_tracked 是"真画一遍再量墨迹"，画布窄了字被裁，
    #   量出来的宽就偏小 —— 实测 48mm 的字被裁成 43.86mm，误差 4.4mm。
    #   guess 用 advance 行宽，但**负字距时 advance 比墨迹宽**，
    #   而正字距时又要加很多，所以两边都要留足余量：
    #     画布宽 = 行宽 + |字距|×(字数+4) + 30mm
    try:
        guess, _ = LK.text_tracking_w(s, size, kind, dpi, tracking, word_gap)
    except Exception:
        guess = len(s) * h
    guess = max(guess, len(s) * h * 0.8)      # 兜底：按字高估
    cw = max(60.0, guess + abs(tracking) * (len(s) + 4)
             + abs(word_gap) * (len(s) + 4) + 30.0)
    c2 = LK.Canvas(cw, max(20.0, h * 4 + 6), dpi)
    ink_w, ink_h = c2.text_tracked(0.5, 0.5, h, s, kind=kind,
                                   tracking=tracking, word_gap=word_gap)
    return ink_w, ink_h, size


def solve(s, target_w, target_h, kind="hei", dpi=300):
    """给一句话求解 (tracking, word_gap, kind)，让它恰好宽 target_w。

    Args:
        s: 文本
        target_w: 目标**墨迹**宽度（毫米），来自叠影
        target_h: 目标**墨迹**高度（毫米），来自叠影，用来定字号
        kind: 起始字面

    Returns:
        dict:
          ok           是否在限内收敛
          kind         最终字面
          size         字号（mm）
          tracking     字距（mm）
          word_gap     词距（mm）
          ink_w        求解后墨迹宽（mm）
          want_w       目标宽
          err          误差（mm）
          over_limit   字距/词距超限（调用方标红）
          tried        试过的字面链（便于排查）
          reason       没收敛的原因
    """
    result = {
        "ok": False, "kind": kind, "size": target_h, "tracking": 0.0,
        "word_gap": 0.0, "ink_w": 0.0, "want_w": target_w, "err": None,
        "over_limit": False, "tried": [], "reason": "",
    }
    if not s:
        result["reason"] = "空文本"
        return result
    if target_w <= 0 or target_h <= 0:
        result["reason"] = "目标宽/高必须为正（W=%.3f H=%.3f）"
        return result

    n_spaces = sum(1 for ch in s if ch == " ")
    n_gaps = max(1, len(s) - 1)
    limit = TRACK_LIMIT * target_h
    wlimit = WORDGAP_LIMIT * target_h

    chain = [kind] + [k for k in neighbors(kind)]
    best = None
    for k in chain:
        result["tried"].append(k)
        # ② 自然宽（无字距）
        ink0, ink_h, size = _measure(s, target_h, k, 0.0, 0.0, dpi)
        if ink0 <= 0:
            continue
        D = target_w - ink0

        # ④ 先摊词距（**用到限值就停**，剩下的交给字距）
        #   截断是必须的：不截的话，一句话里只有 2 个空格、
        #   却要摊 4mm 差，词距会变成 -2mm（词黏在一起，丑陋且不可读）。
        #   词距用满了还差多少，全部推给字距 —— 那才是它该干的活。
        wg = 0.0
        if n_spaces > 0 and abs(D) > 1e-6:
            wg = max(-wlimit, min(wlimit, D / n_spaces))
        used = wg * n_spaces
        rest = D - used

        # ⑤ 再摊字距 —— **该用多少用多少，不截断**
        #   截断过一版是错的：把字距按在 ±10% 上，剩下的宽度差没人补，
        #   误差反而变成 1.19mm（本该 0.005mm）。
        #   限值的用途**只有两个**：判断是否要换字重、是否要标红，
        #   不是用来削求解结果。宽度对不上的行比"字距略超 10%"难看得多。
        trk = (rest / n_gaps) if abs(rest) > 1e-6 else 0.0
        leftover = rest - trk * n_gaps      # 理论上恒为 0，留着做数值兜底
        # 容差 1e-6：词距正好被截到限值时，浮点会让它比限值大 1e-16
        # 而被判成"超限"，白试一档字重。
        over = abs(trk) > limit + 1e-6 or abs(wg) > wlimit + 1e-6

        # 量一次真实结果（摊完之后）
        ink, _h, size2 = _measure(s, target_h, k, trk, wg, dpi)
        err = abs(ink - target_w)
        cand = dict(kind=k, size=round(size2, 4),
                    tracking=round(trk, 4), word_gap=round(wg, 4),
                    ink_w=round(ink, 4), err=round(err, 4), over=over)
        if best is None or (not cand["over"], cand["err"]) < (not best["over"],
                                                             best["err"]):
            best = cand
        if not over and err <= 0.2:
            result.update({
                "ok": True, "kind": k, "size": cand["size"],
                "tracking": cand["tracking"], "word_gap": cand["word_gap"],
                "ink_w": cand["ink_w"], "err": cand["err"],
                "over_limit": False, "reason": "",
            })
            return result

    if best is None:
        result["reason"] = "一个字面都量不出来（字体文件缺失？）"
        return result

    # 没收敛：返回最接近的方案，并标 red
    result.update({
        "ok": False, "kind": best["kind"], "size": best["size"],
        "tracking": best["tracking"], "word_gap": best["word_gap"],
        "ink_w": best["ink_w"], "err": best["err"],
        "over_limit": True,
        "reason": ("字距 %.3fmm 超出字高 ∓%.0f%%（±%.3fmm），"
                   "换遍相邻字重仍收不住；最接近的方案误差 %.3fmm"
                   % (best["tracking"], TRACK_LIMIT * 100, limit, best["err"])),
    })
    return result


def limit_of(h):
    """字距的可用范围（毫米）：±10% 字高。"""
    return TRACK_LIMIT * float(h or 0)


def wordgap_limit_of(h):
    return WORDGAP_LIMIT * float(h or 0)
