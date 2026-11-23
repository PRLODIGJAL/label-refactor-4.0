# -*- coding: utf-8 -*-
"""ctl.py —— 不依赖 Tk 的命令行接口，全部输出 JSON（P1-4）。

**为什么要这个**
-------------
所有能力（渲染 / 对位 / 验证 / 数据绑定）本来都在 engine 里，但只能
通过界面点。别的系统、脚本、AI 想用就得开窗口、截图、模拟点击 ——
既不稳也没法自动化。

这里把 engine 的能力包成 7 个命令，**直接用 `模板.json`，不碰 Tk**：

    list                          列模板 / 列元素
    get                           读一个元素或模板字段
    set                           改一个元素的字段
    place --snap                  按叠影算出每个元素该挪多少
    score                         对齐率（L5，只看文字+矢量件）
    link --auto                   自动给 L4 占位符找数据列
    export                        导出 PDF / PNG

**统一约定**
-----------
· 输出永远是 JSON（`{"ok": true, "data": ...}` 或 `{"ok": false, "error": "..."}）
· 退出码：0 成功 / 1 用法错误 / 2 业务失败
· 改文件前自动备份到 `模板/.backup/<时间戳>.json`
· 不 import tkinter，不用 numpy（运行时里没有）

用法：
    runtime/bin/python3 -B app/engine/ctl.py list
    runtime/bin/python3 -B app/engine/ctl.py get --tpl "名字" --kind t --idx 3
    runtime/bin/python3 -B app/engine/ctl.py place --snap --tpl "名字"
    runtime/bin/python3 -B app/engine/ctl.py score --tpl "名字"
    runtime/bin/python3 -B app/engine/ctl.py link --auto --tpl "名字" --xlsx "表.xlsx"
    runtime/bin/python3 -B app/engine/ctl.py export --tpl "名字" --out /tmp/a.pdf
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

# ── 路径：让本文件既能被 import，也能直接跑 ──────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "app"), _HERE, os.path.join(_ROOT, "app", "ui"),
           os.path.join(_ROOT, "app", "shell")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import paths            # noqa: E402
import store            # noqa: E402
import layers           # noqa: E402
import databind         # noqa: E402
import render           # noqa: E402

# 以下按需 import（避免没用到时拖慢 / 失败）
#   snap      拖动对位算法（纯 PIL）
#   verify    L5 验证（纯 PIL）
#   data_src  Excel 读取（openpyxl）


# ══════════════════════════════════════════════════════════════
#  输出
# ══════════════════════════════════════════════════════════════
class Fail(Exception):
    """业务失败（参数没问题，但做的事做不成）。"""


def out_ok(data, **extra):
    d = {"ok": True, "data": data}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False, indent=1, default=str))
    return 0


def out_err(msg, code=2, **extra):
    d = {"ok": False, "error": str(msg)}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False, indent=1, default=str))
    return code


# ══════════════════════════════════════════════════════════════
#  模板定位 / 备份
# ══════════════════════════════════════════════════════════════
def _resolve_tpl(name=None):
    """找到模板名。没给就要求只有一个模板（否则报错让人指定）。"""
    # list_templates() 返回 4 元组 (名字, 路径, 修改时间, 版本)
    names = [t[0] for t in store.list_templates()]
    if not names:
        raise Fail("一个模板都没有")
    if name:
        if name not in names:
            raise Fail("找不到模板「%s」。现有：%s" % (name, ", ".join(names)))
        return name
    if len(names) == 1:
        return names[0]
    raise Fail("有 %d 个模板，请用 --tpl 指定：%s" % (len(names), ", ".join(names)))


def backup(name):
    """改文件前备份。**没有备份就不要改** —— 这类 CLI 最容易一次写坏。"""
    src = store.tpl_path(name)
    if not os.path.isfile(src):
        return None
    d = os.path.join(store.tpl_dir(name), ".backup")
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, time.strftime("%Y%m%d_%H%M%S") + ".json")
    try:
        shutil.copy2(src, dst)
        return dst
    except OSError:
        return None


def _load(name):
    return store.load(name)


def _save(tpl, name):
    store.save(tpl)
    return store.tpl_path(name)


def _elem(tpl, kind, idx):
    """按 kind+idx 取元素。kind: t=文字 g=图形 gr=组"""
    key = {"t": "文字", "g": "图形", "gr": "组"}.get(kind)
    if not key:
        raise Fail("--kind 只能是 t / g / gr，收到 %r" % (kind,))
    arr = tpl.get(key) or []
    try:
        i = int(idx)
    except (TypeError, ValueError):
        raise Fail("--idx 要是整数，收到 %r" % (idx,))
    if i < 0 or i >= len(arr):
        raise Fail("%s 只有 %d 个，取不到第 %d 个" % (key, len(arr), i))
    return key, i, arr[i]


# ══════════════════════════════════════════════════════════════
#  list
# ══════════════════════════════════════════════════════════════
def cmd_list(a):
    if not a.tpl:
        # 列模板
        rows = []
        for _row in store.list_templates():
            nm = _row[0]
            try:
                t = store.load(nm)
                rows.append({
                    "模板": nm,
                    "画布": t.get("画布"),
                    "图形": len(t.get("图形") or []),
                    "文字": len(t.get("文字") or []),
                    "有参考图": bool(t.get("参考图")),
                })
            except Exception as e:                      # noqa: BLE001
                rows.append({"模板": nm, "错误": str(e)})
        return out_ok(rows, count=len(rows))

    name = _resolve_tpl(a.tpl)
    t = _load(name)
    rows = []
    for kind, key in (("t", "文字"), ("g", "图形"), ("gr", "组")):
        for i, el in enumerate(t.get(key) or []):
            rows.append({
                "kind": kind, "idx": i,
                "name": el.get("name") or el.get("名") or "",
                "layer": layers.of(el, kind),
                "type": el.get("type") or "",
                "text": str(el.get("t") or "")[:60],
            })
    if a.layer:
        rows = [r for r in rows if r["layer"] == a.layer.upper()]
    return out_ok(rows, tpl=name, count=len(rows))


# ══════════════════════════════════════════════════════════════
#  get
# ══════════════════════════════════════════════════════════════
def cmd_get(a):
    name = _resolve_tpl(a.tpl)
    t = _load(name)
    if a.field:
        # 读模板顶层字段
        return out_ok({"tpl": name, "field": a.field, "value": t.get(a.field)},
                      tpl=name)
    if a.kind is None or a.idx is None:
        # 整份模板元信息
        return out_ok({
            "模板名": t.get("模板名"),
            "画布": t.get("画布"),
            "字体_L3": t.get("字体_L3"),
            "字体_L4": t.get("字体_L4"),
            "参考图": t.get("参考图"),
            "图形": len(t.get("图形") or []),
            "文字": len(t.get("文字") or []),
            "不参与对齐": t.get("不参与对齐"),
        }, tpl=name)
    _k, _i, el = _elem(t, a.kind, a.idx)
    return out_ok(el, tpl=name, kind=a.kind, idx=_i)


# ══════════════════════════════════════════════════════════════
#  set
# ══════════════════════════════════════════════════════════════
_SET_NUM = ("x", "y", "w", "h", "th", "mod")


def cmd_set(a):
    name = _resolve_tpl(a.tpl)
    t = _load(name)

    if not a.pairs:
        raise Fail("要改什么？用 k=v 给出，例如 x=1.5 h=3.0")

    edits = {}
    for p in a.pairs:
        if "=" not in p:
            raise Fail("参数要写成 k=v，收到 %r" % (p,))
        k, v = p.split("=", 1)
        k, v = k.strip(), v.strip()
        if k in _SET_NUM:
            try:
                edits[k] = round(float(v), 3)
            except ValueError:
                raise Fail("%s 要数字，收到 %r" % (k, v))
        elif v in ("true", "false"):
            edits[k] = (v == "true")
        else:
            edits[k] = v

    if a.kind is None or a.idx is None:
        # 改模板顶层字段
        bk = backup(name)
        for k, v in edits.items():
            t[k] = v
        _save(t, name)
        return out_ok({"changed": edits, "backup": bk}, tpl=name)

    _k, _i, el = _elem(t, a.kind, a.idx)
    bk = backup(name)
    _before = {k: el.get(k) for k in edits}
    for k, v in edits.items():
        el[k] = v
    _save(t, name)

    # ★ 人工纠正自动进技能库（P2）
    #   `set` 是"人手动改了一个值"的通用入口，正好是学习记录该抓的地方。
    #   按改的字段判断是三类里的哪一类（见 learn.KINDS）：
    #     改了 x/y       → 对位纠正
    #     改了 src/lib_id → 选图纠正
    #     改了 数据映射   → 链接纠正
    #   **自动成功的不记** —— 只记人动了手的，否则记录会淹成流水账。
    _learned = _learn_from_edit(name, t, a, _k, _i, el, _before, edits)
    return out_ok({"changed": edits, "now": el, "backup": bk,
                   "learned": _learned},
                  tpl=name, kind=a.kind, idx=_i)


_PLACE_KEYS = {"x", "y"}
_PICK_KEYS = {"src", "lib_id", "lib_ratio"}


def _learn_from_edit(tpl_name, tpl, args, key, idx, el, before, edits):
    """看这次 `set` 改了什么，按类别记一条学习记录。返回记了什么（没记返回 None）。

    为什么要在 `set` 里记而不是给每个命令都塞参数：人纠正的方式有无数种
    （改 x、改 src、改映射……），但落到文件上都是"某个字段从 A 变成 B"。
    在这里判类型，比在十几个命令里各写一遍可靠。
    """
    try:
        import learn as LN
    except Exception:
        return None
    keys = set(edits.keys())
    kind = None
    if keys & _PLACE_KEYS:
        kind = "place"
    elif keys & _PICK_KEYS:
        kind = "pick"
    elif "数据映射" in keys:
        kind = "link"
    if not kind:
        return None
    # 三个字段都得有内容 —— 调用方可以用 --symptom/--cause/--rule 提供；
    # 没提供就从改动本身生成一句"症状"，原因和规则必须人给（那是判断，不是事实）
    sym = (getattr(args, "symptom", None) or "").strip()
    cause = (getattr(args, "cause", None) or "").strip()
    rule = (getattr(args, "rule", None) or "").strip()
    if not sym:
        who = str(el.get("name") or "") or ("%s:%d" % (key, idx))
        delta = ", ".join("%s %s→%s" % (k, before.get(k), edits.get(k))
                          for k in sorted(keys))
        sym = "人工改了「%s」：%s" % (who, delta)
    if not (cause and rule):
        # 原因/规则是人给的判断，不许由程序编 —— 编出来的是噪音，
        # 下次读的人会当真。只把症状写进一个"待补充"清单里。
        try:
            import learn as _LN2
            _LN2._ensure_dir()
            _p = os.path.join(_LN2.SKILL_DIR, "学习记录_待补充.jsonl")
            import json as _j
            with open(_p, "a", encoding="utf-8") as _f:
                _f.write(_j.dumps({"时间": __import__("time").strftime(
                    "%Y-%m-%d %H:%M:%S"), "类别": kind,
                    "模板": tpl_name, "症状": sym}, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return {"pending": True, "kind": kind, "symptom": sym,
                "hint": "原因/规则要人给：加 --cause \"...\" --rule \"...\""}
    rec = LN.record(kind, sym, cause, rule, tpl=tpl_name,
                    detail={"改动": {k: [before.get(k), edits.get(k)]
                                    for k in sorted(keys)}})
    return rec and {"kind": kind, "症状": sym}


# ══════════════════════════════════════════════════════════════
#  place --snap
# ══════════════════════════════════════════════════════════════
def _ref_opener(tpl, name):
    """返回一个能按毫米窗口取叠影的函数；没有参考图就返回 None。

    ★ 为什么不在开头把整张叠影缩到 ppm（第一版就是这么干的，结果超时）：
      叠影是 7830×8442 = **6600 万像素**，转 L 再 LANCZOS 缩放一次就要
      十几秒；`place` 要遍历几十个元素，光缩放就把时间吃光了。
      正确做法是**每个元素只处理它自己那块窗口**：
      先在原图上按英寸比例裁一小块（几万像素），再缩到目标 ppm。
      一次裁剪+缩放只要几十毫秒。
    """
    src = tpl.get("参考图")
    if not src:
        return None
    from PIL import Image
    fp = store.resolve_ref(name, src)
    if not fp or not os.path.isfile(fp):
        return None
    try:
        im = Image.open(fp).convert("L")
    except Exception:
        return None
    im.load()
    w_mm = float((tpl.get("画布") or {}).get("宽") or 0)
    if w_mm <= 0:
        return None
    k = im.width / w_mm                     # 原图 px per mm

    def window(x_mm, y_mm, w_mm_box, h_mm_box, ppm):
        """取一块毫米窗口，缩放到 ppm，越界补白。"""
        box = (int(round(x_mm * k)), int(round(y_mm * k)),
               int(round((x_mm + w_mm_box) * k)),
               int(round((y_mm + h_mm_box) * k)))
        tw = max(4, int(round(w_mm_box * ppm)))
        th = max(4, int(round(h_mm_box * ppm)))
        dst = Image.new("L", (tw, th), 255)
        sx0, sy0 = max(0, box[0]), max(0, box[1])
        sx1, sy1 = min(im.width, box[2]), min(im.height, box[3])
        if sx1 > sx0 and sy1 > sy0:
            piece = im.crop((sx0, sy0, sx1, sy1))
            pw = max(1, int(round((sx1 - sx0) / k * ppm)))
            ph = max(1, int(round((sy1 - sy0) / k * ppm)))
            piece = piece.resize((pw, ph), Image.LANCZOS)
            ox = int(round((sx0 / k - x_mm) * ppm))
            oy = int(round((sy0 / k - y_mm) * ppm))
            dst.paste(piece, (ox, oy))
        return dst

    return window


def _elem_mask(tpl, kind, idx, ppm):
    """这个元素自己的墨迹遮罩（**用叠影数据渲染**，见 P0-2）。"""
    import snap as SNAP
    dpi = int(round(ppm * 25.4))
    fields = databind.ref_fields(tpl)     # ← 不用当前规格，避免坐标随规格漂
    im, mx, my, ppm2 = render.render_one(tpl, kind, idx, fields, dpi=dpi)
    if im is None:
        return None
    m = SNAP.ink_mask(im)
    if m.getbbox() is None:               # 全白 = 没画出来
        return None
    return m, mx, my, ppm2, SNAP.ink_amount(m)


def _elem_pos(el):
    """元素的 (x, y) mm。顺应性卡这类用 x0/ys 的也兜住。"""
    if el.get("x") is None and "x0" in el:
        ys = el.get("ys") or [0]
        return float(el["x0"]), float(ys[0])
    return float(el.get("x") or 0), float(el.get("y") or 0)


def cmd_place(a):
    """按叠影算每个元素该挪多少，带分差。"""
    import snap as SNAP
    from PIL import Image

    name = _resolve_tpl(a.tpl)
    t = _load(name)
    ppm = float(a.ppm)
    R = float(a.radius)

    window = _ref_opener(t, name)
    if window is None:
        raise Fail("这个模板没有可用的参考图（叠影），没法对位")

    targets = []
    for kind, key in (("t", "文字"), ("g", "图形")):
        for i, el in enumerate(t.get(key) or []):
            if a.layer and layers.of(el, kind) != a.layer.upper():
                continue
            if kind == "g" and el.get("type") in ("barcode", "qrcode"):
                continue          # 条码/二维码是数据码，不参与对位（见报告）
            targets.append((kind, i, el))
    if a.only:
        targets = [x for x in targets if "%s:%d" % (x[0], x[1]) in a.only]

    rows = []
    thr = float(a.margin_thr)
    for kind, i, el in targets:
        try:
            got = _elem_mask(t, kind, i, ppm)
        except Exception as e:                        # noqa: BLE001
            rows.append({"kind": kind, "idx": i, "name": el.get("name") or "",
                         "error": str(e)})
            continue
        if got is None:
            rows.append({"kind": kind, "idx": i, "name": el.get("name") or "",
                         "skipped": "没有可比的墨迹"})
            continue
        mask, mx, my, mppm, denom = got
        ex, ey = _elem_pos(el)
        mw, mh = mask.width / mppm, mask.height / mppm
        # 只取这一块周围的窗口（见 _ref_opener 的注释：整图缩放会超时）
        wx, wy = ex + mx - R, ey + my - R
        ww, wh = mw + 2 * R, mh + 2 * R
        dst = window(wx, wy, ww, wh, ppm)
        ref_mask = SNAP.ink_mask(dst)

        # ★★ 搜索前先把遮罩降采样（这是真正的性能关键，2026-11-23 实测）
        #
        # 第一版直接拿原始遮罩搜：遮罩 7306×904、窗口 8906×2504，
        # 每次 overlap 要 crop 一张 660 万像素的图 —— **单次约 1 秒**。
        # 粗扫 256 次就是 4 分钟，一个元素都算不完。
        #
        # 对位的物理需求只有 ~0.1mm：印刷套准公差就是 0.1~0.2mm，
        # 算到 0.05mm 跟 0.1mm 没有意义差别。所以先把两边按 `work_ppm`
        # 重新采样（这里取 80 ppm ≈ 0.31mm/px），搜索量降 ~6 倍、
        # 单次 overlap 降到几万像素（毫秒级），再按比例换回毫米。
        # 最后在**原分辨率**上对最优点做一次小范围 ±2px 精调。
        work_ppm = float(min(ppm, 80))
        scale = work_ppm / float(ppm)
        if abs(scale - 1.0) > 1e-6:
            m_small = mask.resize(
                (max(2, int(round(mask.width * scale))),
                 max(2, int(round(mask.height * scale)))), Image.LANCZOS)
            r_small = ref_mask.resize(
                (max(2, int(round(ref_mask.width * scale))),
                 max(2, int(round(ref_mask.height * scale)))), Image.LANCZOS)
            d_small = denom * (m_small.width * m_small.height) / float(
                max(1, mask.width * mask.height))
        else:
            m_small, r_small, d_small = mask, ref_mask, denom

        rad_small = int(round(R * work_ppm))
        coarse_small = max(2, int(round(work_ppm / 4.0)))   # ≈0.25mm 一档
        sx, sy, best, second, margin = SNAP.find_offset_top(
            m_small, r_small, rad_small, coarse=coarse_small,
            denom=d_small, sep_px=max(4, int(round(work_ppm * 0.3))))

        # 换回原分辨率，再在 ±2px 内精调（原分辨率上只探 25 个点，很快）
        bx_px = int(round(sx / scale))
        by_px = int(round(sy / scale))
        ox, oy, best, second, margin = bx_px, by_px, best, second, margin
        cx0 = (ref_mask.width - mask.width) // 2
        cy0 = (ref_mask.height - mask.height) // 2
        d0 = denom
        fine_best = (best, bx_px, by_px)
        for _dy in range(-2, 3):
            for _dx in range(-2, 3):
                _s = SNAP.overlap(mask, ref_mask, cx0 + bx_px + _dx,
                                  cy0 + by_px + _dy, d0)
                if _s > fine_best[0]:
                    fine_best = (_s, bx_px + _dx, by_px + _dy)
        best, ox, oy = fine_best[0], fine_best[1], fine_best[2]
        # 次优分差：**直接用降采样粗扫那一步给的 second**
        # （find_offset_top 里已经按 sep_px = 0.3mm 排除了紧邻最优点
        #  的候选，尺度是对的）。
        # 之前我在原分辨率上另扫一个 ±9px 的小邻域取次优 —— 那只有
        # 0.05mm，几乎贴着最优点，second 必然接近 best，margin 恒接近 0，
        # 于是所有元素都被标成 risky，那个红标就失去意义了。
        # best 用精调后的高分，second 用粗扫尺度给的，两者可比。
        margin = best - second
        if margin < 0:
            margin = 0.0
        rows.append({
            "kind": kind, "idx": i,
            "name": el.get("name") or "",
            "layer": layers.of(el, kind),
            "dx": round(ox / ppm, 3),
            "dy": round(oy / ppm, 3),
            "score": round(best, 4),
            "second": round(second, 4),
            "margin": round(margin, 4),
            "risky": bool(margin < thr),
        })

    risky = [r for r in rows if r.get("risky")]
    return out_ok(rows, tpl=name, count=len(rows),
                  risky_count=len(risky), margin_thr=thr,
                  note="risky=true 表示次优分差 < %.2f，位置有歧义，需人看一眼"
                       % thr)


# ══════════════════════════════════════════════════════════════
#  score（L5 对齐率）
# ══════════════════════════════════════════════════════════════
def cmd_score(a):
    """按「只看文字 + 矢量件」的口径算对齐率（条码位置排除）。"""
    import verify as VER
    from PIL import Image

    name = _resolve_tpl(a.tpl)
    t = _load(name)
    if not t.get("参考图"):
        raise Fail("这个模板没有参考图（叠影），算不了对齐率")

    dpi = int(a.dpi)
    # 画布尺寸只从 canvas_rect 取
    import frame as _FRM
    _rc = _FRM.canvas_rect(t)
    W, H = _rc["w"], _rc["h"]
    size = (int(W / 25.4 * dpi), int(H / 25.4 * dpi))

    fields = databind.bind(t, None)
    r = render.render(t, fields, dpi).convert("RGB").resize(size, Image.LANCZOS)
    p = store.resolve_ref(name, t["参考图"])
    # 参考图必须按「图框」换算（P1-6）——和界面、L5 用同一条路
    import frame as FR
    g = FR.ref_page_for(t, Image.open(p).convert("RGB"), size)

    skip = []
    for bx in (t.get("不参与对齐") or []):
        try:
            skip.append((float(bx["x0"]), float(bx["y0"]),
                         float(bx["x1"]), float(bx["y1"])))
        except (KeyError, TypeError, ValueError):
            continue

    # 画布范围由 canvas_rect 统一给（P1-6）
    _img, st = VER.compare(r, g, skip_boxes=skip, canvas_w_mm=W, tpl=t)
    raw_img, raw_st = VER.compare(r, g, canvas_w_mm=W, tpl=t)
    return out_ok({
        "align": round(st["align"], 4),
        "align_pct": round(st["align"] * 100, 2),
        "green_px": st["green_px"],
        "red_px": st["red_px"],
        "skipped_boxes": st.get("skipped", 0),
        "align_without_skip": round(raw_st["align"], 4),
        "dpi": dpi,
    }, tpl=name,
        note="align 只看文字+矢量件（条码位置已排除）；"
             "align_without_skip 含条码位置，仅作参考")


# ══════════════════════════════════════════════════════════════
#  link --auto
# ══════════════════════════════════════════════════════════════
def _placeholders(tpl):
    """L4 里出现的所有占位符名（去重，保持出现顺序）。"""
    import re
    seen, order = set(), []
    for b in tpl.get("文字") or []:
        if layers.of(b, "t") != "L4":
            continue
        for m in re.finditer(r"【([^】]+)】", str(b.get("t") or "")):
            nm = m.group(1).split(":")[0].split("+")[0].strip()
            if nm and nm not in seen:
                seen.add(nm)
                order.append(nm)
    for g in tpl.get("图形") or []:
        if g.get("type") == "barcode":
            for m in re.finditer(r"【([^】]+)】", str(g.get("seq") or "")):
                nm = m.group(1).split(":")[0].split("+")[0].strip()
                if nm and nm not in seen:
                    seen.add(nm)
                    order.append(nm)
    return order


def cmd_link(a):
    """对 L4 每个占位符给候选列 + 置信度；exact 自动落，其余进待人工清单。"""
    name = _resolve_tpl(a.tpl)
    t = _load(name)

    # 数据表：命令行给的，或者模板自己接的
    xl = a.xlsx
    sheet = a.sheet
    if not xl:
        ds = t.get("数据源") or {}
        xl = ds.get("path")
        sheet = sheet or ds.get("sheet")
        if xl and not os.path.isabs(xl):
            xl = os.path.join(store.tpl_dir(name), xl)
    if not xl or not os.path.isfile(xl):
        raise Fail("找不到数据表：%r" % (xl,))

    import data_src
    head, _recs, _sh = data_src.pick_records(xl, sheet or None)
    head = list(head or [])
    if not head:
        raise Fail("数据表没有表头")

    spec_and_line = []
    mp = databind.mapping(t)
    names = _placeholders(t)
    # 已经映射过的也列出来（--force 时重算）
    for nm in names:
        spec = str(mp.get(nm) or "").strip()
        spec_and_line.append((nm, spec))

    auto, manual, derived = {}, [], []
    for nm, spec in spec_and_line:
        # 派生字段（`=【生产日期+3y-1d】`）不需要数据列 —— 它是算出来的。
        # 以前把它也扔进"待人工选择"，等于让人去表里找一列根本不存在的东西。
        if spec.startswith("="):
            derived.append({"占位符": nm, "表达式": spec,
                            "说明": "派生字段，由其它字段算出，不需要数据列"})
            continue
        cands = []
        for part in (spec.split("|") if spec else []):
            part = part.strip()
            if not part or not part.startswith("=") and part == "":
                continue
            seg = ""
            import re as _re
            m = _re.search(r"#([123u])$", part)
            if m:
                seg, part = m.group(1), part[:m.start()]
            col, conf = databind.match_col(head, part)
            if col:
                cands.append({"列": col, "置信度": conf, "模式": part,
                              "段": seg})
        # 没写映射的占位符：拿占位符名本身去猜
        if not cands:
            col, conf = databind.match_col(head, nm)
            if col:
                cands.append({"列": col, "置信度": conf, "模式": nm, "段": ""})
            else:
                # 再宽一点：列出所有包含它的列（不判边界，仅作候选）
                loose = [h for h in head if nm and nm in str(h)]
                for h in loose[:3]:
                    cands.append({"列": h, "置信度": "fuzzy", "模式": nm,
                                  "段": ""})

        cands.sort(key=lambda c: {"exact": 0, "prefix": 1,
                                  "fuzzy": 2}.get(c["置信度"], 9))
        exact = [c for c in cands if c["置信度"] == "exact"]
        if exact:
            auto[nm] = exact[0]["列"]
        else:
            manual.append({
                "占位符": nm,
                "现有映射": spec or None,
                "候选": cands[:5],
                "原因": ("没有 exact 命中" if cands
                         else "数据表里找不到任何相近的列"),
            })

    changed = False
    bk = None
    if a.apply and auto:
        mp2 = dict(mp)
        for nm, col in auto.items():
            seg = ""
            import re as _re
            old = str(mp.get(nm) or "")
            m = _re.search(r"#([123u])$", old)
            if m:
                seg = "#" + m.group(1)
            mp2[nm] = col + seg
        if mp2 != mp:
            bk = backup(name)
            t["数据映射"] = mp2
            _save(t, name)
            changed = True

    return out_ok({
        "表头": head,
        "自动落": auto,
        "待人工选择": manual,
        "派生字段（不需要列）": derived,
        "已写入": changed,
    }, tpl=name,
        note="只把 exact 写进去；prefix/fuzzy 一律留给人工确认"
             "（--apply 才会真写，默认只报告）")


# ══════════════════════════════════════════════════════════════
#  frame（L1 黑框）
# ══════════════════════════════════════════════════════════════
def cmd_frame(a):
    """检查参考图有没有黑框；有就报告框在哪、白边该多少。

    --apply 时改画布尺寸并把所有元素坐标平移到「原点=框外沿」。
    """
    import frame as FR

    name = _resolve_tpl(a.tpl)
    t = _load(name)
    ref = t.get("参考图")
    if not ref:
        raise Fail("这个模板没有参考图（叠影）")
    path = store.resolve_ref(name, ref)
    if not path or not os.path.isfile(path):
        raise Fail("参考图文件找不到：%s" % ref)

    has, det = FR.has_frame(path)
    if not has:
        return out_ok({"有黑框": False, "参考图": ref,
                       "说明": "这张图没有贯穿整幅的长直线，不是黑框图",
                       "详情": det}, tpl=name)

    r = FR.apply_to_tpl(t, name, margin_mm=a.margin, dry_run=not a.apply)
    if not r.get("ok"):
        raise Fail(r.get("reason") or "算不出画布")

    extra = {"有黑框": True, "参考图": ref}
    if a.apply:
        bk = backup(name)
        _save(t, name)
        extra["backup"] = bk
    return out_ok({**r, **extra}, tpl=name)


# ══════════════════════════════════════════════════════════════
#  learn（技能库 / 学习记录）
# ══════════════════════════════════════════════════════════════
def cmd_learn(a):
    """技能库的学习记录：记一条 / 列出来 / 看统计。"""
    import learn as LN

    if a.list or a.stats:
        recs = LN.dedup()
        if a.stats:
            return out_ok(LN.stats(), file=LN.MD,
                          note="md 由 jsonl 自动生成，别手改")
        return out_ok(recs, count=len(recs), file=LN.MD)

    if not a.kind:
        raise Fail("要记一条就给它 --kind place|pick|link（或者用 --list 看）")
    if not (a.symptom and a.cause and a.rule):
        raise Fail("症状 / 原因 / 规则 三个都要给 —— "
                   "只写症状那是流水账，下次读的人不知道该怎么做")
    rec = LN.record(a.kind, a.symptom, a.cause, a.rule, tpl=a.tpl)
    if not rec:
        raise Fail("写不进去（技能目录不可写？）")
    return out_ok(rec, file=LN.MD, stats=LN.stats())


# ══════════════════════════════════════════════════════════════
#  export
# ══════════════════════════════════════════════════════════════
def cmd_export(a):
    name = _resolve_tpl(a.tpl)
    t = _load(name)
    ext = os.path.splitext(a.out)[1].lower()
    if ext not in (".pdf", ".png"):
        raise Fail("--out 要以 .pdf 或 .png 结尾")

    # 可选：指定某个规格
    fields = None
    if a.spec:
        ds = t.get("数据源") or {}
        xl = ds.get("path")
        if xl and not os.path.isabs(xl):
            xl = os.path.join(store.tpl_dir(name), xl)
        import data_src
        head, recs, _sh = data_src.pick_records(xl, ds.get("sheet") or None)
        hit = None
        for r in recs:
            for v in r.values():
                if str(v).strip() == str(a.spec).strip():
                    hit = r
                    break
            if hit:
                break
        if hit is None:
            raise Fail("数据表里找不到规格 %r" % (a.spec,))
        fields = databind.bind(t, hit)
    else:
        fields = databind.bind(t, None)

    dpi = int(a.dpi)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    if ext == ".pdf":
        render.save_pdf(t, a.out, fields, dpi=dpi)
    else:
        render.save_png(t, a.out, fields, dpi=dpi)
    return out_ok({"file": a.out,
                   "bytes": os.path.getsize(a.out),
                   "dpi": dpi,
                   "spec": a.spec or "（模板示例数据）"}, tpl=name)


# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════
def build_parser():
    p = argparse.ArgumentParser(
        prog="ctl.py",
        description="标签重构工具 · 命令行接口（不依赖 Tk，全部输出 JSON）")
    sub = p.add_subparsers(dest="cmd")

    def common(sp, need_tpl=True):
        sp.add_argument("--tpl", help="模板名（只有一个时可省略）")
        return sp

    s = common(sub.add_parser("list", help="列模板 / 列元素"))
    s.add_argument("--layer", help="只列这一层（L1~L4）")
    s.set_defaults(fn=cmd_list)

    s = common(sub.add_parser("get", help="读一个元素或模板字段"))
    s.add_argument("--kind", choices=["t", "g", "gr"])
    s.add_argument("--idx", type=int)
    s.add_argument("--field", help="读模板顶层字段，例如 字体_L4")
    s.set_defaults(fn=cmd_get)

    s = common(sub.add_parser("set", help="改一个元素的字段"))
    s.add_argument("--kind", choices=["t", "g", "gr"])
    s.add_argument("--idx", type=int)
    s.add_argument("pairs", nargs="*", help="k=v，例如 x=1.5 h=3.0")
    # 人工纠正的"原因 / 规则"（P2）：给了才记进技能库。
    # 程序不编原因 —— 编出来的是噪音，下次读的人会当真。
    s.add_argument("--symptom", help="症状（不给就按改动自动生成一句）")
    s.add_argument("--cause", help="原因（人给的判断，必填才记录）")
    s.add_argument("--rule", help="规则（下次怎么办，必填才记录）")
    s.set_defaults(fn=cmd_set)

    s = common(sub.add_parser("place", help="按叠影算每个元素该挪多少"))
    s.add_argument("--snap", action="store_true",
                   help="（必须给，表示按叠影对位）")
    s.add_argument("--layer", help="只算这一层")
    s.add_argument("--only", nargs="*", help="只算这些，形如 t:3 g:7")
    # 500ppm = 0.05mm/px，对标签对位**远超需要**（印刷套准公差就 0.1~0.2mm），
    # 却把搜索量放大 6 倍。200ppm = 0.127mm/px，够用且快得多。
    s.add_argument("--ppm", type=float, default=200,
                   help="对位分辨率（px/mm），默认 200（≈0.13mm/px）")
    s.add_argument("--radius", type=float, default=4.0,
                   help="搜索半径 mm，默认 4")
    s.add_argument("--margin-thr", type=float, default=0.04,
                   help="次优分差低于它标 risky，默认 0.04")
    s.set_defaults(fn=cmd_place)

    s = common(sub.add_parser("score", help="L5 对齐率"))
    s.add_argument("--dpi", type=int, default=300)
    s.set_defaults(fn=cmd_score)

    s = common(sub.add_parser("link", help="自动给 L4 占位符找数据列"))
    s.add_argument("--auto", action="store_true", help="（必须给）")
    s.add_argument("--xlsx", help="数据表路径；不给就用模板自己接的那个")
    s.add_argument("--sheet", help="工作表名")
    s.add_argument("--apply", action="store_true",
                   help="把 exact 命中真的写进模板（默认只报告）")
    s.set_defaults(fn=cmd_link)

    s = common(sub.add_parser("frame", help="L1 黑框检测 / 应用白边画布"))
    s.add_argument("--apply", action="store_true",
                   help="真的改画布和坐标（默认只报告）")
    s.add_argument("--margin", type=float, default=3.0,
                   help="白边宽（毫米，四边相等），默认 3")
    s.set_defaults(fn=cmd_frame)

    s = sub.add_parser("learn", help="技能库：学习记录（人工纠正的坑）")
    s.add_argument("--kind", choices=["place", "pick", "link"],
                   help="要记的类别：对位 / 选图 / 链接")
    s.add_argument("--symptom", help="症状：看到什么现象")
    s.add_argument("--cause", help="原因：为什么会这样")
    s.add_argument("--rule", help="规则：下次该怎么做")
    s.add_argument("--tpl", help="模板名（可选）")
    s.add_argument("--list", action="store_true", help="列出已记录的")
    s.add_argument("--stats", action="store_true", help="按类别统计")
    s.set_defaults(fn=cmd_learn)

    s = common(sub.add_parser("export", help="导出 PDF / PNG"))
    s.add_argument("--out", required=True, help="输出路径（.pdf 或 .png）")
    s.add_argument("--dpi", type=int, default=600)
    s.add_argument("--spec", help="指定规格（数据表里某个值）；不给用示例数据")
    s.set_defaults(fn=cmd_export)
    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "cmd", None):
        p.print_help()
        return 1
    try:
        return a.fn(a)
    except Fail as e:
        return out_err(e, code=2, cmd=a.cmd)
    except Exception as e:                              # noqa: BLE001
        import traceback
        return out_err(e, code=2, cmd=a.cmd,
                       trace=traceback.format_exc().splitlines()[-6:])


if __name__ == "__main__":
    sys.exit(main())
