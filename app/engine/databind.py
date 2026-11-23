# -*- coding: utf-8 -*-
"""databind.py —— 数据表记录 ↔ 模板占位符 的翻译层，外加「变量区」判定。

为什么需要这一层
----------------
Excel 的列名是图纸注释号（`NOTE 03 球囊外径`、`NOTE 12-2atm`），模板里的
占位符是可读的语义名（`【球囊直径】`）。中间少一道映射，导入表也填不上值，
最后只能靠人手工把列名改成中文，那这张表就不再是「物料主数据」了。

为什么还要判「变量区」
----------------------
参考图只是**某一个规格**的成品。条码、二维码、批号、日期、9 个压力值，
换个规格本来就该不一样 —— 拿参考图去和它们叠，红色永远消不掉。
所以比对前必须把这些区域挖掉，否则差异分有个下限降不下来，
用户会以为版面没对齐，其实那些地方本来就该不一样。

判定规则只有一条：**凡是会被数据替换的东西，都是变量区。**
  · L4 文字块（层里标了就是）
  · 条码 / 二维码图形（内容来自数据）
  · 四宫格上半（商品名/规格/条码/批号/日期），下半的注册信息是固定的，不算
"""
import re

import placeholders as PH

# 变量区外扩（毫米）：字块画出来的墨可能比盒子大一点点，留一圈余量，
# 免得边上残留一点红边把差异分卡在高位。
PAD_TEXT = 0.8
PAD_CODE = 1.2

# `1.9F（0.63mm）` 这种「数值 + 括号补充」的列，要能拆成两块用。
# 模板上「近端杆径」确实分两行印：上行 1.9F、下行 (0.63mm)。
_SPLIT = re.compile(r"^(.+?)\s*[（(]\s*(.+?)\s*[）)]\s*$")


def _seg(v, mode: str) -> str:
    """按 #1 / #2 / #3 取一段：括号外 / 带括号 / 括号内不带括号。#u 去掉尾部单位。"""
    s = str(v or "").strip()
    if mode == "u":
        # 数据表里常写「1.0mm」「5mm」这种带单位的。模板上单位是单独一个
        # 字块（那个 mm 还要单独定位），所以这里只留数字。
        m2 = re.match(r"^([-+]?\d+(?:\.\d+)?)", s)
        return m2.group(1) if m2 else s
    m = _SPLIT.match(s)
    if not m:
        return s
    if mode == "1":
        return m.group(1).strip()
    if mode == "2":
        return "(" + m.group(2).strip() + ")"
    if mode == "3":
        return m.group(2).strip()
    return s


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().upper()


def mapping(tpl: dict) -> dict:
    """模板里的「占位符名 → 数据表列名」。值是候选列表，用 | 分隔。"""
    m = (tpl or {}).get("数据映射")
    return dict(m) if isinstance(m, dict) else {}


# 匹配置信度。**只有 exact / prefix 允许直接填值**，
# fuzzy 一律标成「疑似」，由界面标黄让人确认 —— 见 bind()。
CONF_EXACT = "exact"      # 整串相同
CONF_PREFIX = "prefix"    # 前缀相同且边界干净
CONF_FUZZY = "fuzzy"      # 只在某个位置包含，边界不可靠


def match_col(keys, spec: str, with_conf: bool = True):
    """在记录里找 spec 对应的那一列。

    Returns:
        with_conf=True  → (列名, 置信度) 或 (None, None)
        with_conf=False → 列名 或 None（老调用方式，仍兼容）

    三级匹配，从严到松：
      1. **整串相同**            → exact
      2. **前缀相同 + 数字边界**  → prefix
      3. **任意位置包含 + 数字边界** → fuzzy

    数字边界是什么意思：匹配段落**后一个字符不能是数字**。
      · keys=['NOTE 14'] 查 'NOTE 1'  → 前缀后是 '4'（数字）→ 不算命中
      · keys=['NOTE 30'] 查 'NOTE 3'  → 前缀后是 '0'（数字）→ 不算命中
      · keys=['NOTE 1']  查 'NOTE 1'  → 整串相同 → exact
      · keys=['NOTE 1A'] 查 'NOTE 1'  → 后是 'A'（非数字）→ prefix

    第 3 级以前**没有**数字边界，这是个真 bug：
    keys=['X NOTE 14'] 查 'NOTE 1' 会命中 —— 因为 'NOTE 1' 确实是
    'X NOTE 14' 的子串，而后面紧跟着 '4'。图纸上 NOTE 1 和 NOTE 14
    是两个完全不同的字段，这么认会把两档压力值接到一起，
    印出来是「看着没错、其实是别的规格」的数，比空着危险得多。

    fuzzy 单独给一档而不是不给：真有人把列名写成 `压力1(ATM)` 而映射里写
    `压力1`，直接判不命中会让人以为是数据表缺列。但**这种命中不可信**，
    所以只标「疑似」不填值。
    """
    if not spec:
        return (None, None) if with_conf else None
    sp = _norm(spec)

    # ① 整串相同
    for k in keys:
        if _norm(k) == sp:
            return (k, CONF_EXACT) if with_conf else k

    # ② 前缀相同 + 边界（后一个字符不是数字）
    for k in keys:
        nk = _norm(k)
        if nk.startswith(sp):
            rest = nk[len(sp):]
            if not rest or not rest[0].isdigit():
                return (k, CONF_PREFIX) if with_conf else k

    # ③ 任意位置包含 + 同样的数字边界
    #    以前这里只判 `sp in nk`，'NOTE 1' 会吃掉 'NOTE 14'。
    for k in keys:
        nk = _norm(k)
        pos = nk.find(sp)
        if pos < 0:
            continue
        end = pos + len(sp)
        nxt = nk[end] if end < len(nk) else ""
        if nxt and nxt.isdigit():
            continue                      # 后面紧跟数字 → 不是同一个字段
        return (k, CONF_FUZZY) if with_conf else k

    return (None, None) if with_conf else None


def ref_fields(tpl: dict) -> dict:
    """叠影图上印的那个规格的完整字段 —— **对位专用**。

    为什么必须有这个东西
    ------------------
    模板里的叠影是研发给的某一版设计稿，上面印的是**某一个具体规格**的数
    （2.75mm×20mm、801-2720、2408051234…）。可 L4 元素的内容会随规格切换
    变 —— 拿当前规格去渲染遮罩，遮罩的形状/宽度就跟着变，
    「按叠影对位」算出来的坐标自然也变。同一个元素换个规格对出来两个位置，
    这工具就没法用了：先对 A 规格、再换 B 规格、坐标全偏，得重对。

    正确做法：对位时**一律用叠影自己那份数据**渲染遮罩。
    重绘出来的字和叠影上的字是同一串，遮罩才和底下的灰影形状一致，
    对出来的坐标才是「这块内容在设计稿上的真实位置」，与当前规格无关。

    取值顺序（从严到松）：
      1. `tpl["叠影数据"]` —— 建模板时一次定下，**不随规格切换变**
      2. `tpl["示例数据"]` —— 老模板没有叠影数据时的兜底
         （示例数据本来就是照着那份设计稿填的）
      3. `{}` —— 都没有就返回空，调用方要能接受「全是【括号】」
    """
    for key in ("叠影数据", "示例数据"):
        d = (tpl or {}).get(key)
        if isinstance(d, dict) and d:
            return dict(d)
    return {}


def ref_fields_hash(tpl: dict) -> str:
    """叠影数据的指纹，给缓存键用。改了叠影数据 → 缓存自动失效。"""
    import hashlib
    import json as _json
    d = ref_fields(tpl)
    raw = _json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def bind(tpl: dict, rec: dict) -> dict:
    """一条数据记录 → 一份占位符字典（【球囊直径】这种键）。

    先铺模板自带的示例数据，再让真值逐项盖上去。这样缺几列的时候，
    画面上是「示例值 + 真值混排」，不会整块变成【括号】。
    哪些还没填上，由 holes() 单独报，不靠肉眼找。
    """
    out = dict((tpl or {}).get("示例数据") or {})
    if not rec:
        return out
    keys = list(rec.keys())
    src = {}                       # 字段名 → 这个值是从数据表来的吗
    suspects = {}                  # 字段名 → {"列":..., "模式":...}：fuzzy 命中，待人工确认

    # 1) 直通：记录里的列名直接当字段名用（标签框架.xlsx 是这种，列名即语义）。
    #    这里不要求「示例数据里先有这个名字」—— 没有示例数据的模板（或新建
    #    的空模板）本来就是靠列名直接填的，加了那个限制反而一个字段都进不来。
    for k in keys:
        v = rec.get(k)
        if v not in (None, ""):
            out[k] = v
            src[k] = True

    # 2) 显式映射：占位符名 ← NOTE 列。
    #    分两轮，先取实列再算派生 —— 「规格串 = 【球囊直径】×【球囊长度】」
    #    这种派生依赖前面已经取到的值，一轮遍历顺序不对就算出空串。
    mp = mapping(tpl)
    for expr_pass in (False, True):
        for name, spec in mp.items():
            spec = str(spec or "")
            derived = spec.startswith("=")
            if derived != expr_pass:
                continue
            if derived:
                # `=【生产日期+3y】` 这种：有效期不是表里的一列，是算出来的
                v = PH.fill_placeholders(spec[1:], out)
                if v and "【" not in v:
                    out[name] = v
                    src[name] = True
                continue
            seg = ""
            m = re.search(r"#([123u])$", spec)
            if m:
                seg, spec = m.group(1), spec[:m.start()]
            for cand in spec.split("|"):
                col, conf = match_col(keys, cand.strip())
                if col is None or rec.get(col) in (None, ""):
                    continue
                if conf == CONF_FUZZY:
                    # 只在某处"包含"就认，边界不可靠（'NOTE 1' 可能其实是
                    # 'X NOTE 14' 的一部分）。**不填值**，只记成「疑似」，
                    # 界面 L4 面板标黄，让人自己决定连哪一列。
                    # 不填值 + 记下来 > 猜着填。猜错会印出别的规格的数。
                    suspects[name] = {"列": col, "模式": cand.strip()}
                    continue
                out[name] = _seg(rec[col], seg) if seg else rec[col]
                src[name] = True
                break

    # 映射里申明了、但数据源里取不到值的字段。这类不算「错」——
    # 批号/日期本来就来自装配工单，规格表里没有；但必须让界面标出来，
    # 否则会把示例数据当成本规格的批号印出去，那是最危险的一种错。
    miss = [n for n in mp
            if not n.startswith("=") and n not in src]
    out["__来源__"] = src
    out["__缺列__"] = miss
    # fuzzy 命中：界面 L4 面板标黄。**不允许静默填值** —— 猜错会把
    # 别的规格的数印上去，那是这类工具最危险的错。
    out["__疑似__"] = suspects

    # 4) 顺应性卡这种「表格整体是一次绘制」的，要把 9 行拼成一张表
    _comp_rows(tpl, out)
    return out


def _comp_rows(tpl: dict, out: dict):
    """把「顺应性2..18」这些单值拼成 comp 要的行列表。

    卡片是一次画 9 行的，不是 9 个独立文字块 —— 压力串和中文后缀是全规格
    共用的固定标注，只有直径值查表得来，所以只换第 2 列。
    换规格时这里不跟着变，卡片就会印着上一档的数字，比不填还糟。

    骨架（压力串 + 中文后缀）从两个地方取，按新版到旧版：
      · 元素自己的 `rows`（旧 comp 结构，一对象画线 + 数）
      · `out[字段]`（3.0 的 compvals：示例数据里带着那张 9 行的表）

    **这里以前只认 `rows`，而 compvals 没有 `rows`** —— 于是九档直径永远停在
    示例数据那一列（1.65~2.05），换规格也不变。数据表里明明有 NOTE 12-*
    九个真值（1.0mm 那档是 0.9~1.25），印出来却是 1.75mm 的数。
    这是「印着看着没错、其实是别的规格」的那种错，比空着危险得多。
    """
    for g in (tpl or {}).get("图形") or []:
        # compvals 是 3.0 的结构（线归 L2、数归 L4）；comp 是旧结构兜底。
        if g.get("type") not in ("comp", "compvals"):
            continue
        key = g.get("字段") or "顺应性"
        base = g.get("rows") or out.get(key) or []
        rows = []
        for row in base:
            row = list(row) + ["", "", ""]
            p, d, note = row[0], row[1], row[2]
            m = re.match(r"\s*(\d+)", str(p))
            if m:
                v = out.get("顺应性%s" % m.group(1))
                if v not in (None, ""):
                    d = v
            rows.append([p, d, note])
        if rows:
            out[key] = rows


_PH = re.compile(r"【(.+?)】")


def holes(tpl: dict, fields: dict) -> list:
    """哪些占位符到最后一刻还没被填上（数据体检的头号问题）。

    注意要连占位符自己都还在 fields 里的情况一起算：示例数据把
    `【商品名】` 原样当值存过，那种也算没填。
    """
    out = []
    seen = set()

    def scan(text):
        for m in _PH.finditer(str(text or "")):
            key = m.group(1).strip()
            # 剥掉格式后缀：【GTIN:gtin】的有效字段名是 GTIN，不是 "GTIN:gtin"。
            # 不剥的话，带格式的占位符永远查不到值，体检会误报「没填上」。
            if ":" in key:
                key = key.rsplit(":", 1)[0].strip()
            if key in seen:
                continue
            v = (fields or {}).get(key)
            if v in (None, "") or str(v).startswith("【"):
                seen.add(key)
                out.append(key)

    def block(b):
        if b.get("t"):
            scan(b["t"])
        for ln in b.get("lines") or []:
            scan(ln.get("t"))

    for b in (tpl or {}).get("文字") or []:
        block(b)
    for grp in (tpl or {}).get("组") or []:
        for e in grp.get("元素") or []:
            block(e)
    for g in (tpl or {}).get("图形") or []:
        for k in ("seq", "readable"):
            if g.get(k):
                scan(g[k])
    return out


def _text_w(b) -> float:
    """没写盒宽时估一个：中文按 1 字宽、数字/字母按 0.55 字宽。"""
    s = str(b.get("t") or "")
    n = sum(1.0 if ord(ch) > 0x2E80 else 0.55 for ch in s)
    return n * float(b.get("h") or 3.0) + 1.0


def _pad(bb, pad):
    """外扩一圈。入参出参都是 (x, y, w, h)，和画布、模板一致。"""
    return (bb[0] - pad, bb[1] - pad, bb[2] + pad * 2, bb[3] + pad * 2)


def _merge(boxes, gap=0.35):
    """只把真正贴在一起的小框并起来。

    合并距离放大到 3mm 会把「规格大字 / 导管标注 / 四行信息」连成一整片，
    挖完之后画布一半都成了盲区，比对就没意义了。这里只并「几乎相接」的，
    保持洞小、洞准。
    """
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if (a[0] <= b[0] + b[2] + gap and b[0] <= a[0] + a[2] + gap and
                        a[1] <= b[1] + b[3] + gap and b[1] <= a[1] + a[3] + gap):
                    x0 = min(a[0], b[0])
                    y0 = min(a[1], b[1])
                    x1 = max(a[0] + a[2], b[0] + b[2])
                    y1 = max(a[1] + a[3], b[1] + b[3])
                    boxes[i] = (x0, y0, x1 - x0, y1 - y0)
                    boxes.pop(j)
                    changed = True
                    break
            if changed:
                break
    return boxes


def variable_boxes(tpl: dict, bbox_fn=None) -> list:
    """哪些矩形是「每规格都会变」的区域，比对时要挖掉。

    Args:
        tpl: 模板 dict。
        bbox_fn: 可选，`(kind, idx) -> (x, y, w, h)`。界面传自己的测量函数，
            量得比估算准；导出/测试不传就用估算。

    Returns:
        [(x, y, w, h)]，单位毫米，已合并、已外扩。
    """
    out = []
    for i, b in enumerate((tpl or {}).get("文字") or []):
        if b.get("layer") != "L4":
            continue
        bb = None
        if bbox_fn is not None:
            try:
                bb = bbox_fn("t", i)
            except Exception:
                bb = None
        if not bb:
            w = b.get("w")
            bb = (float(b.get("x") or 0), float(b.get("y") or 0),
                  float(w) if w else _text_w(b), float(b.get("h") or 3))
        out.append(_pad(bb, PAD_TEXT))

    for g in (tpl or {}).get("图形") or []:
        if g.get("type") in ("barcode", "qrcode"):
            out.append(_pad((float(g.get("x") or 0), float(g.get("y") or 0),
                             float(g.get("w") or 20), float(g.get("h") or 9)),
                            PAD_CODE))
        elif g.get("type") in ("comp", "compvals"):
            # 顺应性卡：右列 9 个直径值全是查表得来的，换个规格整列都变。
            # 左列的压力档位是固定的，不用挖。线（comp）不算变量区。
            ys = [float(v) for v in (g.get("ys") or [])]
            if len(ys) >= 2:
                xm, x1 = float(g.get("xm") or 0), float(g.get("x1") or 0)
                out.append(_pad((xm, ys[1], x1 - xm, ys[-1] - ys[1]), PAD_TEXT))

    # 四宫格：上半两块整体是变量（商品名/规格/条码/批号/日期），
    # 下半带 reg 标记的是注册信息，全规格共用，不排除。
    for grp in (tpl or {}).get("组") or []:
        dx = float(grp.get("dx") or 0)
        dy = float(grp.get("dy") or 0)
        for pos in grp.get("位置") or []:
            if pos.get("tag"):
                continue
            out.append(_pad((float(pos["x"]) + dx, float(pos["y"]) + dy,
                             float(pos["w"]), float(pos["h"])), PAD_CODE))

    return _merge(out)


def coverage(boxes, w_mm, h_mm) -> float:
    """这些变量区占画布面积的比例（0~1），给界面显示「实际比对了多少」。

    按 1mm 网格去重再数：直接累加矩形面积，重叠的部分会算两遍，
    报出来的百分比比实际大，看着像「一半没比」。
    """
    if not boxes or w_mm <= 0 or h_mm <= 0:
        return 0.0
    gw, gh = max(1, int(w_mm)), max(1, int(h_mm))
    grid = bytearray(gw * gh)
    for x, y, w, h in boxes:
        for gy in range(max(0, int(y)), min(gh, int(y + h) + 1)):
            row = gy * gw
            for gx in range(max(0, int(x)), min(gw, int(x + w) + 1)):
                grid[row + gx] = 1
    return sum(grid) / float(gw * gh)
