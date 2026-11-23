# -*- coding: utf-8 -*-
"""layers.py —— 四层到底是什么，只在这里定义一次。

四层不是「画图的先后顺序」，是**东西从哪来**。一张标签由两份文件拼起来：

  来源一：设计文件（印厂那张空白 PDF + 原设计文件里的图和固定文字）
    L1 底图   整页底版。刀模线、四宫格红框、竖排字标（已转曲，是图不是字）。
              它是一张图，不能逐字改，导出时连同其它层一起印。
    L2 图形   原设计文件里的矢量件：外框、横幅底色、细线、图标、
              球囊/导管示意图、顺应性卡的表格线。
              它们是「固定版面」，可单独选中、挪动、改色。
    L3 文字   原设计文件里的固定文本：REF / LOT / mm / Ø / 表头 /
              「注意：严禁超过额定爆破压力」/ 注册信息。换规格一个字都不变。

  来源二：另一份表格 Excel（一规格一行）
    L4 数据   商品名、球囊直径/长度、目录号、批号、日期、9 档压力值、
              条码、二维码。换规格整片都要变。

所以判断一个元素属于哪层，只看**它的值从哪来**，不看它长什么样：
条码画出来是图，但内容是数据 → L4；顺应性卡的表格线是固定版面 → L2，
九行数值来自 Excel → L4；四宫格里的 REF/LOT 是固定文本 → L3，
【目录代码】【批号】是数据 → L4。

模板里每个元素都自带 `layer` 字段（构建时由 `assign()` 写进去）。
运行期不再靠「文字里有没有【】」去猜 —— 猜出来的层和真实来源对不上，
正是以前四层看着乱的原因。
"""
import re

# 顺序就是界面上的顺序，也是「本层及以下」的叠放顺序。
ORDER = ("L1", "L2", "L3", "L4", "L5")

DEFS = {
    "L1": {
        "name": "底图",
        "src": "空白 PDF",
        "what": "整页底版：刀模、红框、竖排字标（已转曲）",
        "note": "图案在、文字空。是一张图，不能逐字改。",
    },
    "L2": {
        "name": "图形",
        "src": "原设计文件的矢量图",
        "what": "外框、底色、细线、图标、示意图、表格线",
        "note": "固定版面，可单独选中改色改位置。",
    },
    "L3": {
        "name": "文字",
        "src": "原设计文件的固定文字",
        "what": "REF / LOT / mm / Ø / 表头 / 注意事项 / 注册信息",
        "note": "换规格一个字都不变。",
    },
    "L4": {
        "name": "数据",
        "src": "另一份表格 Excel（一规格一行）",
        "what": "规格、目录号、批号、日期、压力值、条码、二维码",
        "note": "换规格整片都变，不能拿参考图去比。",
    },
    "L5": {
        "name": "验证",
        "src": "渲染结果 × 叠影，逐像素比对",
        "what": "绿=对齐 / 红=没对齐 / 白=空白",
        "note": "只用来验收对位，不导出。整张全绿才算做完。",
    },
}

_PH = re.compile(r"【.+?】")


def name(layer: str) -> str:
    return (DEFS.get(layer) or {}).get("name") or layer


def source(layer: str) -> str:
    return (DEFS.get(layer) or {}).get("src") or ""


def has_placeholder(item: dict) -> bool:
    """这个元素的内容里有没有【占位符】—— 也就是「值来自数据表」。"""
    for k in ("t", "seq", "readable", "字段"):
        if _PH.search(str(item.get(k) or "")):
            return True
    for row in item.get("rows") or []:
        for cell in row if isinstance(row, (list, tuple)) else []:
            if _PH.search(str(cell)):
                return True
    for ln in item.get("lines") or []:
        if _PH.search(str(ln.get("t") or "")):
            return True
    return False


def infer(item: dict, kind: str = "t") -> str:
    """没有 `layer` 字段时（旧模板）按来源推一个。

    注意这只是兼容旧文件的兜底。新模板一律走 `assign()` 写死，
    运行期读 `layer`，不再每次推断。

    「组里的元素」不能一律当图形 —— 四宫格里既有 REF/LOT 这种固定字（L3），
    也有【批号】这种数据（L4），还有蓝框、图标（L2）。所以这里先看元素
    自己是文字还是图形，再看文字里有没有占位符。
    """
    t = item.get("type")
    if kind == "gr":
        return "L2"
    if t in ("compvals", "barcode", "qrcode"):
        return "L4"
    if t == "comp":
        # 旧结构里 comp 一对象画「线 + 数」；线是固定版面，算 L2。
        return "L2"
    is_text = t in ("text", "list", "vtext") or (
        t is None and "src" not in item and ("t" in item or "字段" in item))
    if is_text:
        return "L4" if has_placeholder(item) else "L3"
    return "L2"


def of(item: dict, kind: str = "t") -> str:
    """这个元素属于哪一层。**先读元素自己写的 `layer`**，没有才推断。"""
    lay = (item or {}).get("layer")
    if lay in DEFS:
        return lay
    return infer(item, kind)


def assign(tpl: dict) -> dict:
    """给模板里每个元素补上 `layer`（模板构建时调用一次）。

    写死之后，界面、导出、变量区、体检全都读同一个字段，
    不会出现「面板说它在 L4、变量区却没挖掉」这种自相矛盾。
    """
    for g in tpl.get("图形") or []:
        g["layer"] = of(g, "g")
    for b in tpl.get("文字") or []:
        b["layer"] = of(b, "t")
    for grp in tpl.get("组") or []:
        for e in grp.get("元素") or []:
            e["layer"] = of(e, "ge")
    return tpl


def entries(tpl: dict) -> list:
    """模板里所有可归属的元素，一条一个 dict。

    四宫格（组）里的元素也算单独的条目 —— 它们分属 L2/L3/L4，
    整组算一层是不对的。`group` 标出它属于哪个重复组。
    """
    out = []
    for i, g in enumerate((tpl or {}).get("图形") or []):
        out.append({"layer": of(g, "g"), "kind": "g", "idx": i,
                    "item": g, "where": "图形", "group": None})
    for i, b in enumerate((tpl or {}).get("文字") or []):
        out.append({"layer": of(b, "t"), "kind": "t", "idx": i,
                    "item": b, "where": "文字", "group": None})
    for gi, grp in enumerate((tpl or {}).get("组") or []):
        for ei, e in enumerate(grp.get("元素") or []):
            out.append({"layer": of(e, "ge"), "kind": "ge", "idx": ei,
                        "item": e, "where": "组", "group": gi,
                        "group_name": grp.get("名称") or "重复组"})
    return out


def counts(tpl: dict) -> dict:
    """每层各有多少个元素（四宫格里的元素按 1 个算，不按 4 格乘）。"""
    c = {k: 0 for k in ORDER}
    c["L1"] = 1 if (tpl or {}).get("底图") else 0
    for e in entries(tpl):
        c[e["layer"]] = c.get(e["layer"], 0) + 1
    return c


def audit(tpl: dict) -> list:
    """分层体检：找出「标的层」和「实际来源」对不上的元素。

    返回 [{"level": "error"|"warn", "layer": ..., "msg": ...}]。
    error = 一定会印错或改规格改不动；warn = 看着像标错，需要人确认。
    """
    out = []
    if not tpl:
        return [{"level": "error", "layer": "L1", "msg": "没有模板"}]
    # L1 底图已不再要求：标签纸上的图案在实体纸张上已经印好，
    # 模板只需 L2/L3/L4（图形/固定文字/数据）。底图可选，有就贴、没有就白底。

    for e in entries(tpl):
        it, lay, kind = e["item"], e["layer"], e["kind"]
        label = _label(e)
        if kind == "t":
            if lay == "L3" and has_placeholder(it):
                out.append({"level": "error", "layer": lay,
                            "msg": "「%s」标成固定文字，但内容带【占位符】" % label})
            elif lay == "L4" and not has_placeholder(it) and not it.get("字段"):
                out.append({"level": "warn", "layer": lay,
                            "msg": "「%s」标成数据，但内容里没有占位符" % label})
        elif kind == "g":
            t = it.get("type")
            dataish = t in ("barcode", "qrcode", "compvals") or has_placeholder(it)
            # 只要「内容是数据」却不在 L4 就算标错 —— 以前只查 `lay == "L2"`，
            # 于是一条二维码被标成 L3 能一路活着：画得出来、体检也不吭声。
            # `层手动` 是有人在界面上刻意改的（右键 → 归属层级），不当错误报。
            if dataish and lay != "L4" and not it.get("层手动"):
                out.append({"level": "error", "layer": lay,
                            "msg": "「%s」是数据印出来的，不该放在 %s 层"
                                   % (label, lay)})
            if lay == "L4" and not dataish:
                out.append({"level": "warn", "layer": lay,
                            "msg": "「%s」标成数据，但内容和数据表无关" % label})
            if t == "comp" and it.get("rows"):
                out.append({"level": "error", "layer": lay,
                            "msg": "顺应性卡把数值也画在图形对象里，应拆成 comptext"})
        elif kind == "ge":
            if lay == "L4" and not (has_placeholder(it) or it.get("type") in ("barcode", "qrcode")):
                out.append({"level": "warn", "layer": lay,
                            "msg": "四宫格里的「%s」标成数据，但内容固定" % label})
    return out


def label(item: dict, kind: str = "t") -> str:
    """元素在列表里显示的名字（短、能认出来）。"""
    return _label({"item": item, "kind": kind})


def _label(e) -> str:
    it = e["item"]
    t = it.get("type")
    if t in ("text", "list", None):
        s = it.get("t") or it.get("字段") or "文字"
    elif t == "barcode":
        s = "条码 " + str(it.get("seq") or "")
    elif t == "qrcode":
        s = "二维码"
    elif t == "comp":
        s = "顺应性卡表格线"
    elif t == "compvals":
        s = "顺应性卡数值"
    else:
        s = it.get("name") or t or "图形"
    return str(s)[:20]


def summary(tpl: dict) -> str:
    """状态栏用的一句话：四层各几个、体检几处不对。"""
    c = counts(tpl)
    bad = [x for x in audit(tpl) if x["level"] == "error"]
    s = " · ".join("%s %s %d" % (k, name(k), c.get(k, 0)) for k in ORDER)
    if bad:
        return s + " · 分层有 %d 处不一致" % len(bad)
    return s + " · 分层一致"
