# -*- coding: utf-8 -*-
"""render.py —— 模板 → 图。

一条数据流：模板(dict) + 字段(dict) → PIL.Image。
界面预览和导出走同一次绘制，所以「看到的」和「导出的」永远一致。

模板结构（labeldesigner/2）
--------------------------
四层按「值从哪来」分（定义见 layers.py），数组只是存放位置：

  画布   {宽, 高}                              单位毫米
  底图   L1   空白 PDF 转的底版（dataURL / 文件名 / 绝对路径）
  图形   L2   rect / line / image / vimage / hourglass / vtext / comp（表格线）
             也放 L4 的 barcode / qrcode / compvals —— 它们画出来是图，
             内容却来自数据表，所以元素自己的 layer 写 L4，不是 L2
  组     重复组（四宫格）：一套元素按多个位置印 4 次。组里的元素各自带
         layer，同时含 L2 骨架、L3 固定字、L4 数据 —— 组本身不占某一层
  文字   L3   text（text_h 按字高 / fit 强制塞盒）
              list（一条记录一行，行距固定）/ table（网格表）
         L4   带【占位符】的 text / list
              以及 2.0 遗留的 lines + bars

坐标一律毫米、原点左上角，和图纸完全一致。
"""
import base64
import io
import os

from PIL import Image

import barcode as BC
import labelkit as LK
import layers
import frame as _FRAMEMOD        # canvas_rect：画布范围的唯一来源（P1-6）
from placeholders import fill_placeholders

# 当前渲染的模板名（render() 入口登记）—— 找素材时先看模板自己的
# 素材/参考/数据源文件夹，再退回共享图库/旧池子。见 store.resolve_ref。
_TPL_NAME = None

# ── 「只打黑色」 ────────────────────────────────────────────────────
# 现场那台标签打印机只有黑墨：彩色填充印不出来（纸上一片白），彩色字印出来
# 只是一层灰。所以渲染时统一按「黑稿」出 —— 彩色填充整块不画，彩色字转黑。
# 黑白输出**恒定开启**（2026-11-20）：现场只有黑墨不是选项，是生产约束。
# 模板里的「黑白输出」字段已废弃，这里不再读它 —— 旧模板带一个 false
# 进来也不会把彩色印出去（那正是用户反复说的"彩色部分不放图案"）。
MONO_DEFAULT = True
_MONO = MONO_DEFAULT
# 灰到什么程度不算「彩色」：纯黑白灰的 chroma 是 0，
# 深蓝 #0045A5 的 chroma 到 165，隔得很远，阈值给 28 足够稳。
_COLOR_CHROMA = 28


def _parse_hex(c):
    """'#RRGGBB' / '#RGB' → (r, g, b)；看不懂就返回 None。"""
    s = str(c or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return None
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return None


def is_color(c) -> bool:
    """这个颜色是不是「有彩色」（黑墨印不出来）。黑 / 白 / 灰都不算。"""
    rgb = _parse_hex(c)
    if rgb is None:
        return False
    r, g, b = rgb
    if max(rgb) - min(rgb) <= _COLOR_CHROMA:
        return False                    # 纯灰阶
    if max(rgb) <= 90:
        return False                    # 深到看着就是黑
    return True


def ink(color, default="#111111"):
    """落墨色：只打黑色时，彩色字一律转黑（不然印出来是一层灰）。"""
    if not color:
        return default
    return "#111111" if (_MONO and is_color(color)) else color


def fill_of(color):
    """填充色：只打黑色时，彩色填充返回 None（这一块就不上墨了）。"""
    if not color:
        return None
    return None if (_MONO and is_color(color)) else color

# ── 缓存 ────────────────────────────────────────────────────────────
# 一次渲染里同一张素材会被贴好几次（四宫格 4 次），拖动时每秒要重画十几帧。
# 不缓存的话，每帧都在做「b64 解码 → 开文件 → LANCZOS 缩放」，
# 实测占掉一次渲染 40% 的时间 —— 这才是拖动卡的真正原因。
_SRC = {}        # src 字符串 hash → 原图
_SCALED = {}     # (素材 hash, 目标像素宽, 高) → 缩放后的图
_KEEP = {}       # 和 _SCALED 配套：留住原图引用，id() 才不会复用
_BITS = {}       # (条码内容, 码制) → 位串
_LAST_CANVAS = None   # 最近一次渲染的画布（导出 PDF 时取文字落点）


def fam_of(tpl: dict, el: dict = None):
    """这个元素该用哪个字体族 —— **L3 和 L4 各自独立**（P1-3）。

    为什么要分开：L3 是固定文字（REF/LOT/mm/注意事项），L4 是数据
    （批号/日期/规格数字）。这两类的字形要求本来不同 ——
    固定文字常要宋体显得正式，数据数字常要等宽或思源粗便于对位。
    以前共用一个 `tpl["字体"]`，改数据字体会把固定文字一起带偏。

    取值：
      L3 元素（或没给 el）→ tpl["字体_L3"]
      L4 元素             → tpl["字体_L4"]
      其它层（L1/L2/L5）  → 按 L3 算（图形层没有字体概念，但有标签文字）

    兜底顺序：层字段 → 旧 `tpl["字体"]` → labelkit.DEFAULT_FONT。
    第三级是为了老模板 / 手工构造的 dict（测试里就有）不至于炸。
    """
    import labelkit as _LK
    key = None
    if isinstance(el, dict):
        key = _LK.FONT_KEYS.get(str(el.get("layer") or "").upper())
    if not key:
        key = _LK.FONT_KEYS["L3"]
    return (tpl.get(key) or tpl.get(_LK.LEGACY_FONT_KEY) or _LK.DEFAULT_FONT)


def _src_of(g: dict):
    """一个 image/图形元素该用哪张图 —— **lib_id 优先**（P0-3）。

    顺序：
      1. `lib_id`（图库序号）→ 从 paths.IMG_LIB 取原图。
         图库更新了立刻生效，模板里也不用存副本。
      2. 回退到 `src`（老模板没有 lib_id 时的兼容路径）。
      3. 都没有 → None（画成空白，不报错）。

    为什么不是直接读 src：
      以前 src 是 `素材/lib_079.jpg` —— 等于把图库原图**拷了一份**进模板。
      20 个模板就存 20 份，图库改了它们还是老的。
      现在 lib_id 是权威，src 只当缓存/回退。
    """
    lid = g.get("lib_id")
    if lid is not None and lid != "":
        try:
            import lib as _lib
            fp = _lib.path_of(lid)
            if fp:
                return _load_src(fp)          # 走绝对路径分支 + 缓存
        except Exception:
            pass
    return _load_src(g.get("src"))


def _load_src(src):
    """src 可以是 dataURL、绝对路径、或文件引用（裸名/素材x/参考x/数据源x）。

    文件引用走 store.resolve_ref：模板自己的文件夹优先，图库兜底。
    """
    if not src:
        return None
    if src.startswith("data:"):
        key = src
    else:
        import store
        p = store.resolve_ref(_TPL_NAME, src)
        if not p:
            return None
        key = p              # 按真实文件路径缓存，同名不同模板不串图
    im = _SRC.get(key)
    if im is not None:
        return im
    try:
        if src.startswith("data:"):
            raw = base64.b64decode(src.split(",", 1)[1])
            im = _open_keep_alpha(Image.open(io.BytesIO(raw)))
        else:
            im = _open_keep_alpha(Image.open(key))
    except Exception:
        return None
    if im is not None:
        if len(_SRC) > 80:
            _SRC.clear()
        _SRC[key] = im
    return im


def _open_keep_alpha(im):
    """打开素材：有透明通道就留着，没有才摊平成 RGB。

    以前这里一律 `.convert("RGB")`，图的透明区会变成一块实心色 ——
    图库原图（法规符号）都是透明底 + 纯黑墨，摊平之后整块变成黑方块，
    底下的线条也一并被盖掉。透明通道是「这里不该上墨」的信息，必须留。
    """
    if im.mode in ("RGBA", "LA", "PA"):
        return im.convert("RGBA")
    if im.mode == "P" and ("transparency" in im.info or "transparency" in im.__dict__):
        return im.convert("RGBA")
    return im.convert("RGB")


def _alpha_of(im):
    """取素材的透明通道（没有就返回 None）。"""
    if im is not None and im.mode in ("RGBA", "LA"):
        return im.getchannel("A")
    return None


def _flat_rgb(im):
    """把透明底合到白底上，返回 RGB。用于「必须先摊平才能算灰」的场合。"""
    if im is None or im.mode not in ("RGBA", "LA"):
        return im
    a = im.getchannel("A")
    bg = Image.new("RGB", im.size, "white")
    bg.paste(im.convert("RGB"), (0, 0), a)
    return bg


def _scaled(im, wp, hp):
    """按目标像素尺寸缩放素材，结果缓存。

    key 用 id(im)：_SRC 里一直持有原图引用，id 不会被回收复用，
    比拿整张图做 hash 便宜得多。
    """
    if im is None:
        return None
    key = (id(im), wp, hp)
    v = _SCALED.get(key)
    if v is None:
        # 大幅缩小时用 BOX（面积平均）：LANCZOS 在 2 倍以上的缩小里会在
        # 细笔画拐角处振铃，四宫格那几个 3mm 高的字标缩到 2mm 时，
        # 「某医疗器械公司」底下就会多出几条等距横纹 —— 那是算法画出来的，不是字。
        ratio = im.size[0] / float(max(1, wp))
        v = im.resize((max(1, wp), max(1, hp)),
                      Image.BOX if ratio >= 2.0 else Image.LANCZOS)
        if len(_SCALED) > 240:
            _SCALED.clear()
        _SCALED[key] = v
        _KEEP[key] = im              # 留住原图，id 不会被复用
    return v


_GRAY = {}       # id(原图) → 去色后的图（只打黑色时用）


def _mono_img(im):
    """位图去色：彩色素材（蓝色字标、红色警示图标）转成灰阶。

    为什么非转不可：现场只有黑墨，彩色 png 直接贴上去，打印机会按自己的
    灰度公式折一遍 —— 深蓝可能折成一片黑糊，浅蓝干脆没了。提前用
    标准亮度公式转灰，屏幕上看到的就是纸上那团墨，颜色也不再进 PDF。

    透明通道在这里**不保留**：先合到白底再算灰度，透明度交给 `_paste`
    当遮罩用。留着 alpha 一起贴，`paste` 不会自动吃它（画布是 RGB），
    结果那圈透明区按 RGB 原值（近黑的 #111）贴上去，整块变成黑方块。
    """
    if im is None or not _MONO:
        return im
    if im.mode in ("L", "1"):
        return im
    key = id(im)
    v = _GRAY.get(key)
    if v is None:
        v = _flat_rgb(im).convert("L")
        if len(_GRAY) > 240:
            _GRAY.clear()
        _GRAY[key] = v
        _KEEP[("gray", key)] = im
    return v


def _paste(c: LK.Canvas, im, x, y, w, h):
    if im is None:
        return
    am = _alpha_of(im)                 # 去色会摊平，遮罩得先留出来
    im = _mono_img(im)
    if w and h:
        tw, th = round(w * c.k), round(h * c.k)
    elif w:
        tw, th = round(w * c.k), round(w * im.size[1] / float(im.size[0]) * c.k)
    else:
        tw = th = None
    if tw:
        im = _scaled(im, tw, th)
        if am is not None:
            am = _scaled(am, tw, th)   # 和图案同步缩放，边缘才对得上
    c.im.paste(im, (int(round(c.px(x))), int(round(c.py(y)))), am)


# ══════════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════════
def render(tpl: dict, fields: dict = None, dpi: int = 300) -> Image.Image:
    """按模板画一张图。

    Args:
        tpl: 模板 dict（store 的结构）
        fields: 占位符取值；空的话退回模板自带的「示例数据」
        dpi: 输出分辨率

    注意 fields 必须是**翻译过**的（`databind.bind(tpl, 行)` 或模板自带的
    示例数据），不是数据表的原始行。原始行的列名是 `NOTE 03`，模板里的
    占位符叫【球囊直径】，直接丢进来会画出一张全是【括号】的图 ——
    这曾经是「屏幕上好好的、一导出完全不一样」的原因。这里不替调用方
    兜底：`顺应性` 这种整表字段是调用方手搓的，替它重算反而会把手改的值
    抹掉，所以翻译留在导出那几处显式做（见 designer_app 的 `_rec_for_export`）。
    """
    fields = _prepare(tpl, fields)
    # ★ 画布范围只从 canvas_rect 取（P1-6 用户要求，不允许这里自己算）
    r = _FRAMEMOD.canvas_rect(tpl)
    # ox/oy = m：把"黑框外沿"（模型原点）挪到画布内 (m, m) 处，
    # 于是白边那块负坐标区域自动落在画布最外圈，元素一个都不用动。
    cv = LK.Canvas(r["w"], r["h"], dpi, ox=r["margin"], oy=r["margin"])
    _draw_all(cv, tpl, fields)

    global _LAST_CANVAS
    _LAST_CANVAS = cv          # 导出 PDF 时要拿它的 texts（文字落点）
    return cv.im


def render_one(tpl: dict, kind: str, idx: int, fields: dict = None,
               dpi: int = 200, pad: float = 1.2):
    """只画**一个**元素，返回 (图, 相对元素的毫米偏移, 每毫米像素)。

    拖动时「软吸附」要用它：得先知道这块元素自己长什么样，才能拿它去叠影上
    比对「放到哪儿最吻合」。整张 render() 也能用，但拖动每帧都要算，只画一块
    快得多（一两毫秒）。

    `pad` 是四周留的余量（毫米）—— 文字块声明的 h 常常只有字号那么大，
    实际笔画会探出去一点，留 1.2mm 才不会把边缘切掉。
    """
    src = tpl["文字"] if kind == "t" else tpl["图形"]
    b = src[idx]
    # 按元素所在层取字体（L3 / L4 各自独立，见 fam_of）
    fam = fam_of(tpl, b)
    bx = float(b.get("x") or 0.0)
    by = float(b.get("y") or 0.0)
    if b.get("type") == "table":
        bw = sum(float(v) for v in (b.get("cols") or [10.0]))
        bh = sum(float(v) for v in (b.get("rows") or [4.0]))
    else:
        bw = float(b.get("w") or 8.0)
        bh = float(b.get("h") or 4.0)
        if bw <= 0.1:                      # 宽度没写：按字号给个估值
            bw = bh * 4.0
    wx, wy = bx - pad, by - pad
    ww, wh = max(0.6, bw + 2 * pad), max(0.6, bh + 2 * pad)
    dpi = max(60, int(dpi))
    cv = LK.Canvas(ww, wh, dpi, bg="#ffffff", ox=-wx, oy=-wy)
    try:
        if kind == "t":
            _draw_block(cv, b, fields or {}, fam)
        else:
            _draw_graphic(cv, b, fields or {}, fam)
    except Exception:
        return None, -pad, -pad, dpi / 25.4
    # 偏移是**相对元素左上角**的（一般就是 -pad）：遮罩缓存之后元素会被拖到
    # 别处，只有相对量才一直成立。给绝对坐标的话拖动一挪，取窗口就取到空纸上了。
    return cv.im, wx - bx, wy - by, dpi / 25.4


def render_workspace(tpl: dict, fields: dict = None, dpi: int = 300,
                     pad_mm=12.0, bg: str = "#eef0f3",
                     page_bg: str = "#ffffff"):
    """画出「纸面 + 纸外一圈」的预览图（只在屏幕上用，导出永远走 render）。

    为什么要这一层：标签上的字和表格常常要先挪到纸外面去改（按住不动、
    不压住别的元素），改完再挪回来。以前的预览图严格等于纸面大小，
    一挪出边界就被裁掉，等于「拖出去就没了」。这里把纸面四周各留
    pad_mm 的空白一起画进来，纸外的内容照样看得见。

    Args:
        pad_mm: 纸外留白（毫米）；给一个数 = 四边一样，给四元组 = 左/上/右/下
        bg: 纸外的底色（跟画布底色一致，纸边才看得出来）
        page_bg: 纸面底色
    """
    fields = _prepare(tpl, fields)
    _r = _FRAMEMOD.canvas_rect(tpl)      # 画布范围只从 canvas_rect 取
    w = _r["w"]
    h = _r["h"]
    # pad_mm 可以是单个数字（四边一样），也可以是 (左, 上, 右, 下)。
    # 界面上要四边不一样：一块表格被推到纸右边 60mm，右边就该留 60mm，
    # 左边只留一点点 —— 四边平均留，纸会被无谓地缩小一半。
    if isinstance(pad_mm, (tuple, list)):
        pl, pt, pr, pb = (max(0.0, float(v)) for v in pad_mm)
    else:
        pl = pt = pr = pb = max(0.0, float(pad_mm))
    cv = LK.Canvas(w + pl + pr, h + pt + pb, dpi, bg=bg, ox=pl, oy=pt)
    if pl or pt or pr or pb:
        # 纸面本身是白的（全 0 时整张就是纸，不用画）
        cv.fill_rect(0, 0, w, h, page_bg)
    _draw_all(cv, tpl, fields)

    global _LAST_CANVAS
    _LAST_CANVAS = cv
    return cv.im


def _prepare(tpl, fields):
    """两处入口共用的开头：定数据、定当前模板名、定彩色/黑白口径。"""
    fields = fields or tpl.get("示例数据") or {}
    global _TPL_NAME, _MONO
    _TPL_NAME = (tpl or {}).get("模板名") or None
    _MONO = True        # 恒定黑白，见上方 MONO_DEFAULT 的注释
    return fields


def _draw_all(cv, tpl, fields):
    """把模板整个画到一张已经建好的画布上（纸面偏移由画布自己管）。"""
    # 底图铺满的是**黑框范围**，不是整张画布（外面那圈白边不该有底图）
    _r = _FRAMEMOD.canvas_rect(tpl)
    w_mm = _r["frame"]["w"]
    h_mm = _r["frame"]["h"]
    if tpl.get("底图"):
        _paste(cv, _load_src(tpl["底图"]), 0, 0, w_mm, h_mm)

    # 字体不再取一个全局 fam —— L3 / L4 各自独立（P1-3）。
    # 每个元素画之前用 fam_of(tpl, el) 现取。

    # 叠放顺序按层走：底图（L1）→ L2 → L3 → L4；同一层里图形先、文字后。
    # 以前是「所有图形 → 所有组 → 所有文字」三趟循环，看着也对 —— 因为模板里
    # 图形都归 L2、文字都归 L3/L4。但只要手动把一块挪了层（白字提到 L3 压住
    # 色块、或把某个字退回 L2），叠放关系就得跟着层走，否则「换层」只换了面板
    # 上的位置，画面上谁压谁一点没变。
    order = {k: i for i, k in enumerate(layers.ORDER)}
    draws = []
    for i, g in enumerate(tpl.get("图形") or []):
        # ★ 黑色大色块固定在 L2 最底层（2026-11-23 P0-3 用户口径）。
        # 它是「例外」：不是从图库取的 image，而是 rect 色块（压在图库件下面
        # 当底）。用 `lib_bottom` 显式标记，而不是靠"它在列表第 0 个"——
        # 那个位置一旦被人拖动/新增元素挤掉，色块就会盖住上面的白字。
        if g.get("lib_bottom"):
            draws.append((-1, 0, i, "g", g))
        else:
            draws.append((order.get(layers.of(g, "g"), 1), 0, i, "g", g))
    for i, grp in enumerate(tpl.get("组") or []):     # 重复组，组本身不占层
        draws.append((order["L2"], 1, i, "gr", grp))
    for i, b in enumerate(tpl.get("文字") or []):
        draws.append((order.get(layers.of(b, "t"), 2), 2, i, "t", b))
    draws.sort(key=lambda d: (d[0], d[1], d[2]))
    for _o, _k, _i, kind, obj in draws:
        # 每个元素按自己那一层取字体（P1-3：L3 / L4 独立）
        _fam = fam_of(tpl, obj)
        if kind == "g":
            _draw_graphic(cv, obj, fields, _fam)
        elif kind == "gr":
            _draw_group(cv, obj, fields, _fam)
        else:
            _draw_block(cv, obj, fields, _fam)


# ══════════════════════════════════════════════════════════════════
#  文字块
# ══════════════════════════════════════════════════════════════════
def _draw_block(cv: LK.Canvas, b: dict, fields: dict, fam: str):
    t = b.get("type")
    if t == "text":
        _text_box(cv, b, fields, fam)
    elif t == "list":
        _list_box(cv, b, fields, fam)
    elif t == "table":
        _table(cv, b, fields, fam)
    elif t == "vtext":
        _vtext(cv, float(b.get("x", 0)), float(b.get("y", 0)),
               float(b.get("w", 0)), float(b.get("h", 0)),
               fill_placeholders(str(b.get("t", "")), fields),
               ink(b.get("color"), "#111111"), fam)
    else:
        _legacy_block(cv, b, fields, fam)


def _kind_of(b: dict, text: str, fam: str) -> str:
    """这一块该用哪个字面。

    分三种情况：
      · 块上写了具体族（hei / song / impact …）→ 就按它，模板里的特例优先；
      · 块上写的 "lat"/"latb" → 它的意思是「这里要拉丁/要粗」，不是「锁死 Arial」。
        以前字面锁死，选宋体后满屏规格数字还是 Arial，看着像字体没换。
      · 什么都没写 → 中文跟模板族，数字/字母跟族的拉丁角色。
    """
    k = b.get("kind")
    if k in ("lat", "latb"):
        return LK.latin_role(fam, k == "latb")
    if k:
        return k
    if LK.has_cjk(text):
        return LK.cjk_kind(fam)
    return LK.latin_role(fam, bool(b.get("bold")))


def _ink_w(s: str, h: float, fam: str, b: dict = None) -> float:
    """一段字按字高 h 排出来，真正落墨有多宽（毫米）。

    和 `_line_w` 的区别：那个用的是「前进宽度」（含左右侧承），拿来算横向
    缩放会偏胖一点点；这里取的是墨迹宽度，和 fit 贴图的口径一致。
    """
    if not s:
        return 0.0
    bb = text_bbox({"x": 0, "y": 0, "h": h, "t": s,
                    "kind": (b or {}).get("kind")}, {}, fam)
    return bb[2]


def _text_box(cv: LK.Canvas, b: dict, fields: dict, fam: str):
    """一个毫米盒子 + 一段文字。

    · 只给 h        → text_h：按字高定字号，字宽自然（日常排版）
    · 给 w、h、fit  → fit：强制塞进盒子，允许拉伸（字标 / 长串）
    · 给 h + xs     → 按自然字宽横向压/放 xs 倍（拟合印刷稿的窄体字）
    · 只给 size     → text：按磅值直接排
    """
    text = fill_placeholders(str(b.get("t", "")), fields)
    if not text:
        return
    x, y = float(b.get("x", 0)), float(b.get("y", 0))
    w, h = float(b.get("w", 0) or 0), float(b.get("h", 0) or 0)
    kind = _kind_of(b, text, fam)
    color = ink(b.get("color"))
    align = b.get("align") or "left"
    xs = float(b.get("xs") or 1.0)
    # ★ 字距 / 词距（P1-5）：有 tracking 或 word_gap 就走逐字绘制，
    #   字形不变形。优先于 xs —— xs 是横向拉伸（会压扁字），
    #   tracking 才是正确的做法。
    trk, wg = LK.tracking_of(b), LK.wordgap_of(b)
    if b.get("fit") and w and h:
        cv.fit(x, y, w, h, text, kind=kind, color=color, align=align)
    elif h and (trk or wg):
        cv.text_tracked(x, y, h, text, kind=kind, color=color, align=align,
                        valign=b.get("valign") or "top",
                        tracking=trk, word_gap=wg)
    elif h and xs != 1.0:
        # 兼容旧模板：xs 还没有迁移成 tracking 时仍走拉伸。
        # 新模板不该再有 xs（P1-5 迁移会转成 tracking）。
        tw = _ink_w(text, h, fam, dict(b, kind=kind)) * xs
        cv.fit(x, y, tw, h, text, kind=kind, color=color, align=align)
    elif h:
        cv.text_h(x, y, h, text, kind=kind, color=color, align=align,
                  valign=b.get("valign") or "top", maxw=w or None)
    else:
        cv.text(x, y, text, size=float(b.get("size", 3)), kind=kind,
                color=color, anchor="la")

    # 带框文字（REF / LOT 这种）：框画在字的净墨迹外面，跟着字一起走。
    # 以前框是一个独立的 rect，拖字框不动、拖框字不动，两个元素迟早会散开。
    if b.get("box"):
        bx0, by0, bw, bh = _box_geom(b, fields, fam)
        cv.rect(bx0, by0, bx0 + bw, by0 + bh,
                w=float(b.get("box_lw", 0.2)),
                color=b.get("box_color") or color)


def _text_ink_bbox(b: dict, fields: dict, fam: str = "hei"):
    """一个文字块**真正落墨**的毫米包围盒 (x, y, w, h)。

    为什么要有这个函数：界面的选中框以前是自己估的（Tk 字体量宽 + 直接把
    b["x"] 当左边），和导出的字形对不上 —— 居中的块框会整体偏半个字宽，
    小字块框会胖一圈。这里改用绘制时的同一套度量（labelkit 的字体 +
    PIL textbbox），框和字从此是同一条边线。

    返回的 y 是墨迹上沿、h 是墨迹高度，和 labelkit.text_h 落字的语义一致。
    """
    x = float(b.get("x") or 0)
    y = float(b.get("y") or 0)
    w = float(b.get("w") or 0)
    h = float(b.get("h") or 0)
    typ = b.get("type")

    if typ == "table":
        return (x, y, float(sum(b.get("cols") or [0])) or 1.0,
                float(sum(b.get("rows") or [0])) or 1.0)

    if typ == "vtext":
        # 竖排：盒子就是版面，不用量字
        return (x, y, w or 1.0, h or 1.0)

    if typ == "list":
        val = (fields or {}).get(str(b.get("字段") or ""))
        items = list(val) if isinstance(val, (list, tuple)) else str(val or "").split("\n")
        items = [s for s in items if str(s)][:int(b.get("max", 200))]
        step = float(b.get("step", 3.0))
        bh = h or 2.0
        bw = w
        if not bw:
            for s in items:
                bw = max(bw, _line_w(str(s), bh, fam))
        return (x, y, bw or 1.0,
                (len(items) - 1) * step + bh if items else bh)

    text = fill_placeholders(str(b.get("t") or ""), fields)
    kind = _kind_of(b, text, fam)
    align = b.get("align") or "left"

    # fit：强制塞进盒子，盒子就是框
    if b.get("fit") and w and h:
        return ({"left": x, "center": x - w / 2.0, "right": x - w}.get(align, x),
                y, w, h)

    # ★ 字距 / 词距（P1-5）：盒宽 = 含字距的真实墨迹宽。
    #   和绘制走**同一个函数**（text_tracked），所以选中框和实际墨迹一致。
    #   拖动读数、右栏宽高也都读这里，全项目只有一个度量。
    # 统一走 text_tracked —— **不管有没有字距**（P1-5 用户口径：
    # "text_h / text_metrics / 选中框 / 拖动读数全部用同一个含字距的度量函数"）。
    # 之前只在有字距时才走，导致 tracking=0 和 tracking=0.0001 两种情况
    # 量出的框高差 0.14mm（一个量墨迹 bbox、一个直接返回 h）——
    # 看着是小事，但屏幕上的选中框会因为"有没有设过字距"而跳一下。
    _trk, _wg = LK.tracking_of(b), LK.wordgap_of(b)
    # 旧模板还有 xs 时，**xs 优先**（它表示"要这个宽"）——
    # 迁移会用这条分支量出宽，再换算成 tracking（见 store.normalize）。
    # 等迁移跑完，模板里就没有 xs 了，一律走下面的统一分支。
    _xs_raw = float(b.get("xs") or 1.0)
    if h and text and abs(_xs_raw - 1.0) > 1e-9:
        _no = {k: v for k, v in b.items() if k != "xs"}
        _w0 = _text_ink_bbox(_no, fields, fam)[2]
        _tw = _w0 * _xs_raw
        return ({"left": x, "center": x - _tw / 2.0,
                 "right": x - _tw}.get(align, x), y, _tw, h)
    if h and text:
        dpi = 300
        # 画布必须开得够大 —— text_tracked 是"真画一遍再量"，画布小了
        # 超出部分直接被裁，量出来的宽比实际小（第一版写 20×20，
        # 25mm 的字框出来只有 19.8mm）。
        # ★ 这里**不能调 _ink_w / text_bbox** —— 那会绕回来无限递归
        #   （text_bbox → 本分支 → _ink_w → text_bbox → ...）。
        #   直接用字体度量估：字高 h × 字符数 × 1.6 足够宽。
        cw = max(40.0, len(text) * h * 1.6 + 12.0)
        tmp = LK.Canvas(cw, max(20.0, h * 4 + 6), dpi)
        tw, _th = tmp.text_tracked(0, 0, h, text, kind=kind,
                                   tracking=_trk, word_gap=_wg,
                                   maxw=w or None)
        return ({"left": x, "center": x - tw / 2.0,
                 "right": x - tw}.get(align, x), y, tw, h)

    # 横向缩放：盒子宽度 = 自然墨迹宽 × xs（旧模板兼容，新模板走 tracking）
    xs = float(b.get("xs") or 1.0)
    if h and xs != 1.0 and text:
        tw = _ink_w(text, h, fam, dict(b, kind=kind)) * xs
        return ({"left": x, "center": x - tw / 2.0, "right": x - tw}.get(align, x),
                y, tw, h)

    # 按字高定字号（text_h）：高度由 h 定，宽度要量；超宽时字会等比缩小
    if h:
        if not text:
            return (x, y, w or 1.0, h)
        dpi = 300
        k = dpi / 25.4
        w0, h0, bb0, _k = LK.text_metrics(text, 10.0, kind, dpi)
        if h0 <= 0:
            return (x, y, w or 1.0, h)
        size = 10.0 * h / h0
        ww = w0 * size / 10.0
        if w and ww > w:                      # text_h 里的 maxw 缩字
            size *= float(w) / ww
            ww = float(w)
        hh = h0 * size / 10.0
        if align == "center":
            px = x - ww / 2.0
        elif align == "right":
            px = x - ww
        else:
            px = x
        # 上面拿到的是「字身框」（按前进宽度算的）。绘制真正落墨的是字形位图，
        # 它比字身框窄一点点（左右各有侧承），直接用字身框会框住一圈空白。
        # 这里按绘制时的同一个落点（px、py）去取位图的实际墨迹范围，框才贴字。
        bb1 = bb0[1] * size / 10.0
        py = y - bb1 / k
        g = LK.glyph(kind, size * dpi / 25.4, text)
        if g:
            mask, off = g
            gb = mask.getbbox()
            if gb:
                ax, ay = int(round(px * k)), int(round(py * k))
                return ((ax + off[0] + gb[0]) / k, (ay + off[1] + gb[1]) / k,
                        (gb[2] - gb[0]) / k, (gb[3] - gb[1]) / k)
        return (px, y, ww, hh)

    # 按磅值直接排（anchor=la）：y 是行框上沿，墨迹还要往下让一点
    if not text:
        return (x, y, w or 1.0, max(1.0, float(b.get("size", 3))))
    _w, _h, bb0, k = LK.text_metrics(text, float(b.get("size", 3)), kind)
    return (x, y, _w, _h)


def _box_geom(b: dict, fields: dict, fam: str = "hei"):
    """带框文字那个框的几何 (x, y, w, h)。

    框以**字的墨迹**为基准，两种给法：
      · `box_pad`      四边外扩一圈（默认 0.3mm）—— 跟着字变
      · `box_w`/`box_h` 框的尺寸写死，字在框里居中 —— REF / LOT 这种，
        框的长宽是设计定的，不随字变，但两者永远是一个整体
    """
    bx, by, bw, bh = _text_ink_bbox(b, fields, fam)
    pad = float(b.get("box_pad", 0.3))
    w = float(b.get("box_w") or 0) or (bw + 2 * pad)
    h = float(b.get("box_h") or 0) or (bh + 2 * pad)
    return (bx + bw / 2.0 - w / 2.0, by + bh / 2.0 - h / 2.0, w, h)


def text_bbox(b: dict, fields: dict, fam: str = "hei", with_box: bool = True):
    """文字块的毫米包围盒。带 `box` 的元素把边框一起算进来。

    `with_box=False` 给绘制用（框要按字的净尺寸再外扩，不能自己套自己）。
    """
    if with_box and b.get("box"):
        return _box_geom(b, fields, fam)
    return _text_ink_bbox(b, fields, fam)


def _line_w(text: str, h_mm: float, fam: str) -> float:
    """一行字按字高 h 排出来有多宽（给 list 块估框用）。"""
    if not text:
        return 0.0
    kind = LK.cjk_kind(fam) if LK.has_cjk(text) else LK.latin_role(fam)
    w0, h0, _bb, _k = LK.text_metrics(text, 10.0, kind)
    return w0 * (h_mm / h0) if h0 > 0 else 0.0


def _list_box(cv: LK.Canvas, b: dict, fields: dict, fam: str):
    """一条记录一行，行距固定。用于注册信息这种行数不固定的段落。

    每行怎么定宽，三种写法：
      · 都不给      → 按字高排，宽度自然（换行是数据里断好的）
      · `xs: 0.9`   → 整体按自然宽压 0.9 倍（拟合印刷稿的窄体字）
      · `宽: [...]`  → 逐行给目标宽（和印刷稿逐行对齐，最准；行数要一致）
      · `fit: true` + `w` → 每行都拉满同一个盒子（旧行为，四宫格里别用）
    """
    val = fields.get(str(b.get("字段") or ""))
    if val in (None, ""):
        return
    items = list(val) if isinstance(val, (list, tuple)) else str(val).split("\n")
    x, y = float(b.get("x", 0)), float(b.get("y", 0))
    w, h = float(b.get("w", 0) or 0), float(b.get("h", 0) or 0)
    step = float(b.get("step", 3.0))
    color = ink(b.get("color"))
    xs = float(b.get("xs") or 1.0)
    ws = b.get("宽") or []
    kind = b.get("kind") or LK.cjk_kind(fam)     # 小字可以单独指定细体
    for i, it in enumerate(items[:int(b.get("max", 200))]):
        s = str(it)
        if not s:
            continue
        yy = y + i * step
        tw = ws[i] if i < len(ws) else 0
        if tw and h:
            cv.fit(x, yy, float(tw), h, s, kind=kind, color=color)
        elif b.get("fit") and w and h:
            cv.fit(x, yy, w, h, s, kind=kind, color=color)
        elif h and xs != 1.0:
            cv.fit(x, yy, _ink_w(s, h, fam, {"kind": kind}) * xs, h, s,
                   kind=kind, color=color)
        else:
            cv.text_h(x, yy, h or 2.0, s, kind=kind, color=color, maxw=w or None)


def _cjk_bold(fam: str) -> str:
    """这个字体族对应的「粗」字面（表格表头用）。没有就退回常规。"""
    k = LK.cjk_kind(fam)
    b = k + "b"
    return b if b in LK.CJK_KINDS else k


def _table(cv: LK.Canvas, b: dict, fields: dict, fam: str):
    """表格：逐列宽 / 逐行高，格子里的字按行高定字号、超宽自动缩。

    早期版本用 `fit()` 把每个格子撑满 —— 那会把中文横向拉扁，一列表头
    「压力球囊直径」看着像换了个字体。现在改成「按行高定字高、放不下就
    等比缩小」，字宽保持自然。
    """
    cols = b.get("cols") or [10, 10, 10, 10]
    rows = b.get("rows") or [4]
    cells = b.get("cells") or []
    gx, gy = float(b.get("x", 0)), float(b.get("y", 0))
    lw = float(b.get("lw", 0.25))
    color = ink(b.get("stroke"), "#000000")
    base_kind = LK.cjk_kind(b.get("kind") or fam)
    head_kind = _cjk_bold(b.get("kind") or fam)
    align = b.get("align") or "center"
    cell_sz = float(b.get("cell_sz") or 0)
    pad = float(b.get("pad", 1.0))
    # 合并单元格（2026-11-20）：b["merges"] 里每条 [r0, c0, r1, c1]
    # 表示这几格画成一整块 —— 框线只画外圈，文字放在整块正中。
    merges = [m for m in (b.get("merges") or [])
              if isinstance(m, (list, tuple)) and len(m) == 4]
    covered = set()
    for r0, c0, r1, c1 in merges:
        for rr in range(r0, r1 + 1):
            for cc in range(c0, c1 + 1):
                covered.add((rr, cc))

    def _span(ri, ci):
        """这一格所在的合并块 (r0, c0, r1, c1)，没有就返回 None。"""
        for m in merges:
            if m[0] <= ri <= m[2] and m[1] <= ci <= m[3]:
                return m
        return None

    def _xy_wh(r0, c0, r1, c1):
        """合并块的物理位置与尺寸（mm）。"""
        x = gx + sum(cols[:c0])
        y = gy + sum(rows[:r0])
        w = sum(cols[c0:c1 + 1])
        h = sum(rows[r0:r1 + 1])
        return x, y, w, h

    # 先只画"非合并"的格子框；合并块最后单独画外圈
    cy = gy
    for ri, rh in enumerate(rows):
        cx = gx
        for ci, cw in enumerate(cols):
            if (ri, ci) not in covered:
                cv.rect(cx, cy, cx + cw, cy + rh, w=lw, color=color)
            sp = _span(ri, ci)
            if sp is not None and (ri, ci) != (sp[0], sp[1]):
                cx += cw                  # 合并块里的非左上格：不画字
                continue
            txt = ""
            if ri < len(cells) and ci < len(cells[ri]):
                txt = cells[ri][ci] or ""
            txt = fill_placeholders(txt, fields)
            if txt:
                head = bool(b.get("head_bold")) and ri == 0
                kind = head_kind if head else base_kind
                if sp is not None:            # 合并块：用整块的高宽定字号
                    _, _, bw, bh = _xy_wh(*sp)
                    cx2 = gx + sum(cols[:sp[1]])
                    cy2 = gy + sum(rows[:sp[0]])
                else:
                    bw, bh = cw, rh
                    cx2, cy2 = cx, cy
                sz = cell_sz or (bh * 0.62)
                maxw = max(0.6, bw - pad * 2)
                tw = _ink_w(txt, sz, fam, {"kind": kind})
                if tw > maxw:                     # 放不下就等比缩，别越界
                    sz *= maxw / tw
                vx = cx2 + pad if align == "left" else (
                    cx2 + bw - pad if align == "right" else cx2 + bw / 2)
                cv.text_h(vx, cy2 + (bh - sz) / 2, sz, txt, kind=kind,
                          color="#111111", align=align, valign="top",
                          maxw=None)
            cx += cw
        cy += rh

    # 合并块的外圈最后画，压在普通格子线上（避免被盖住）
    for r0, c0, r1, c1 in merges:
        x, y, w, h = _xy_wh(r0, c0, r1, c1)
        cv.rect(x, y, x + w, y + h, w=lw, color=color)


def _legacy_block(cv: LK.Canvas, b: dict, fields: dict, fam: str):
    """2.0 留下的结构：一个块里塞 bars（条码）+ lines（散点文字）。"""
    for bar in b.get("bars") or []:
        seq = fill_placeholders(str(bar.get("seq", "")), fields)
        if seq:
            _draw_barcode(cv, float(bar.get("x", 0)), float(bar.get("y", 0)),
                          float(bar.get("w", 0)) or None, float(bar.get("h", 10)),
                          seq, bar.get("mode", "C"), None, 1.8)
    for ln in b.get("lines") or []:
        text = fill_placeholders(ln.get("t", ""), fields)
        if not text:
            continue
        kind = fam if LK.has_cjk(text) else ("latb" if ln.get("b") else "lat")
        cv.text(float(ln.get("x", 0)), float(ln.get("y", 0)), text,
                size=float(ln.get("sz", 3)), kind=kind,
                color=ink(ln.get("c"), "#111111"), anchor="la")


# ══════════════════════════════════════════════════════════════════
#  图形
# ══════════════════════════════════════════════════════════════════
def _draw_graphic(cv: LK.Canvas, g: dict, fields: dict, fam: str):
    t = g.get("type") or ("rect" if g.get("fill") else "image")
    x, y = float(g.get("x", 0)), float(g.get("y", 0))
    w, h = float(g.get("w", 0)), float(g.get("h", 0))

    if t == "rect":
        _rect(cv, g, x, y, w, h)

    elif t == "line":
        cv.line(x, y, float(g.get("x2", x)), float(g.get("y2", y)),
                w=float(g.get("lw", 0.25)),
                color=ink(g.get("stroke"), "#000000"))

    elif t == "barcode":
        seq = fill_placeholders(str(g.get("seq", "")), fields)
        readable = fill_placeholders(str(g["readable"]), fields) if g.get("readable") else None
        _draw_barcode(cv, x, y, w, h, seq, g.get("mode", "C"), readable,
                      float(g.get("readable_size", 1.8)), float(g.get("rdy", 0.30)),
                      mod=g.get("mod"))

    elif t == "qrcode":
        from qrcode_min import draw_qr as _qr
        seq = fill_placeholders(str(g.get("seq", "")), fields)
        _qr(cv, x, y, w, seq or "0", color=ink(g.get("color"), "#000000"))

    elif t == "hourglass":
        _hourglass(cv, x, y, w, h, ink(g.get("color"), "#000000"))

    elif t == "vtext":
        _vtext(cv, x, y, w, h, fill_placeholders(str(g.get("t", "")), fields),
               ink(g.get("color"), "#000000"), g.get("kind") or fam)

    elif t == "vimage":
        _vimage(cv, g)

    elif t == "comp":
        _comp(cv, g)
    elif t == "compvals":
        _comp_values(cv, g, fields)

    else:                                   # image
        _paste(cv, _src_of(g), x, y, w, h)


def _rect(cv: LK.Canvas, g: dict, x, y, w, h):
    lw = float(g.get("lw", 0.25))
    raw_fill = g.get("fill")
    stroke, fill = (ink(g["stroke"], "#000000") if g.get("stroke") else None), \
        fill_of(raw_fill)
    if raw_fill is not None and not fill:
        # 彩色填充在「只打黑色」下被拿掉了（或模板里置空 = 明确不填充）：
        # 这一块不上墨，只剩框（有框的话）。
        # 不能退化成「画成黑块」—— 一整条蓝横幅变成黑条会把上面的字压掉。
        if stroke:
            cv.rect(x, y, x + w, y + h, w=lw, color=stroke)
        return
    if g.get("r"):
        if fill:
            cv.rrect_fill(x, y, x + w, y + h, r=float(g["r"]), color=fill)
        if stroke or not fill:
            cv.rrect(x, y, x + w, y + h, r=float(g["r"]), w=lw, color=stroke or "#000000")
    elif fill and not stroke:
        cv.fill_rect(x, y, x + w, y + h, fill)
    else:
        # 只给 stroke，或者两个都没给 —— 一律当成描边，避免误画成黑块
        cv.rect(x, y, x + w, y + h, w=lw, color=stroke or "#000000")


def _comp(cv: LK.Canvas, g: dict):
    """顺应性卡的**表格线**（L2，固定版面）。

    线来自 PDF 矢量图：11 横 2 竖，外加表头下方那条中线。数值另由
    `_comp_values` 画 —— 线和数分属两层（版面 / 数据），所以这里不碰
    fields，换规格时这条线一个像素都不动。
    """
    x0, x1 = float(g["x0"]), float(g["x1"])
    xm = float(g["xm"])
    ys = [float(v) for v in g["ys"]]
    lw = float(g.get("lw", 0.25))
    for y in ys:
        cv.line(x0, y, x1, y, w=lw)
    cv.line(x0, ys[0], x0, ys[-1], w=lw)
    cv.line(x1, ys[0], x1, ys[-1], w=lw)
    cv.line(xm, ys[0], xm, ys[1], w=lw)


def _comp_values(cv: LK.Canvas, g: dict, fields: dict):
    """顺应性卡里的九行数值（L4，来自数据表 NOTE 12-* 列）。

    左列「2 /200」和一列中文后缀（命名压力 / 额定爆破压力）是全规格共用的
    固定标注，右列直径查表得来。左右字号不同：左列 1.95、右列 1.45，
    照成品图实测 —— 统一字号右列会把格子撑满。
    """
    x0, x1 = float(g["x0"]), float(g["x1"])
    xm = float(g["xm"])
    ys = [float(v) for v in g["ys"]]

    rows = fields.get(g.get("字段") or "顺应性") or g.get("rows") or []
    dy = float(g.get("dy", 1.33))
    th = float(g.get("th", 1.95))               # 左列：压力串
    th_r = float(g.get("th_r", 1.45))           # 右列：直径值
    lcx = x0 + float(g.get("lcx", 9.10))        # 左列居中位置
    dcx = float(g.get("dcx") or (xm + x1) / 2.0 + 1.60)
    for i, row in enumerate(rows[:len(ys) - 2]):
        p, d, note = (list(row) + ["", "", ""])[:3]
        yy = ys[i + 1] + dy
        ps = fill_placeholders(str(p), fields)
        ns = fill_placeholders(str(note), fields)
        if ps and ns:
            # 带中文后缀的行（命名压力 / 额定爆破压力），整串要一起居中：
            # 单把数字居中、后缀钉死在固定 x 上，遇到「14 / 1400」这种长串
            # 就会压到后缀上。先量出两段宽度，再合成一条整体居中。
            th_n = float(g.get("th_note", 1.15))     # 后缀比数字再小一档
            w1 = _text_w(cv, ps, th, "lat")
            w2 = _text_w(cv, ns, th_n, "hei")
            gap = float(g.get("gap", 0.8))
            # 「14 /1400 额定爆破压力」是最长的一行，硬排会顶出左边线。
            # 按可用宽度等比缩一次字号，保证整串始终落在格子内。
            avail = (xm - x0) - 3.0
            total = w1 + gap + w2
            if total > avail > 0:
                r = avail / total
                th, th_n = th * r, th_n * r
                w1 = _text_w(cv, ps, th, "lat")
                w2 = _text_w(cv, ns, th_n, "hei")
                total = w1 + gap + w2
            start = lcx - total / 2.0
            cv.text_h(start, yy, th, ps, color="#000000")
            cv.text_h(start + w1 + gap, yy + 0.28, th_n, ns, color="#111111")
        elif ps:
            cv.text_h(lcx, yy, th, ps, color="#000000", align="center")
        if d:
            cv.text_h(dcx, yy, th_r, fill_placeholders(str(d), fields),
                      color="#000000", align="center")


def _text_w(cv: LK.Canvas, s: str, h_mm: float, kind: str = "lat") -> float:
    """这段字按目标字高排出来有多宽（毫米）。用来做「两段拼一行」的排布。"""
    w10, h10, _bb = cv.measure(s, 10.0, kind)
    if h10 <= 0:
        return 0.0
    return w10 * h_mm / h10


def _draw_barcode(cv: LK.Canvas, x, y, w, h, seq, mode="C", readable=None,
                  rsize=1.8, rdy=0.30, mod=None):
    seq = str(seq or "").strip()
    if not seq:
        return
    key = (seq, mode)
    bits = _BITS.get(key)
    if bits is None:
        try:
            bits = BC.bits(seq, mode)
        except Exception:
            bits = []
        if len(_BITS) > 200:
            _BITS.clear()
        _BITS[key] = bits
    if not bits:
        return
    # 有「模块宽」就以它为准（条码的固有尺寸），否则按给定总宽反算。
    # 以前这里只按总宽反算，界面上那个「模块宽」数字改了也不生效。
    step = float(mod) if mod else (float(w) / len(bits) if w else 0.3)
    bx = float(x)
    y0, y1 = cv.py(y), cv.py(y + h)
    # 按「整像素边界」落笔：一条 1 模块宽的条在 300dpi 下只有 1.9px，
    # 如果直接把浮点坐标丢给 PIL，它会把 100.0→100.0 和 101.86→101.0 各取整
    # 一次，条宽在 2px/3px 之间跳，整条码就胖了 20% 以上（印出来糊成一片）。
    # 这里统一用 round(起点)~round(终点) 当边界，宽度自然是 1 或 2px，总数守恒。
    px0, px1 = [], []
    acc = bx
    for _bit in bits:
        px0.append(int(round(cv.px(acc))))
        acc += step
        px1.append(int(round(cv.px(acc))))
    for i, bit in enumerate(bits):
        if bit and px1[i] > px0[i]:
            cv.draw.rectangle([px0[i], y0, px1[i] - 1, y1], fill="#000000")
    if readable:
        bw = step * len(bits)          # 人可读那行要压着条码的实际宽度居中
        cv.fit(x, y + h + rdy, bw, rsize, readable, kind="latb", color="#111111")


def _hourglass(cv: LK.Canvas, x, y, w, h, color):
    k = cv.k
    bw = w * 0.14
    cv.draw.rectangle([cv.px(x - bw), cv.py(y), cv.px(x + w + bw), cv.py(y + h * 0.16)],
                      fill=color)
    cv.draw.rectangle([cv.px(x - bw), cv.py(y + h * 0.84),
                       cv.px(x + w + bw), cv.py(y + h)], fill=color)
    top, bot = y + h * 0.16, y + h * 0.84
    mid = (top + bot) / 2.0
    cv.draw.polygon([(cv.px(x), cv.py(top)), (cv.px(x + w), cv.py(top)),
                     (cv.px(x + w / 2), cv.py(mid))], fill=color)
    cv.draw.polygon([(cv.px(x + w / 2), cv.py(mid)), (cv.px(x + w), cv.py(bot)),
                     (cv.px(x), cv.py(bot))], fill=color)


def _vtext(cv: LK.Canvas, x, y, w, h, text, color, fam):
    """竖排：整段旋转 90°，再按盒子拉伸。"""
    if not text:
        return
    from PIL import ImageDraw
    tmp = Image.new("L", (1400, 300), 0)
    ImageDraw.Draw(tmp).text((14, 14), text, font=LK.font(fam, 12, cv.dpi), fill=255)
    bb = tmp.getbbox()
    if not bb:
        return
    tmp = tmp.crop(bb).rotate(90, expand=True)
    if w and h:
        tmp = tmp.resize((max(1, int(w * cv.k)), max(1, int(h * cv.k))), Image.LANCZOS)
    cv.paste_mask(tmp, x, y, color)


def _vimage(cv: LK.Canvas, g: dict):
    """位图竖排：取暗部当遮罩，转 90°，用单色贴上去（右侧竖排 BrandX 用）。"""
    src = _src_of(g)
    if src is None:
        return
    x, y = float(g.get("x", 0)), float(g.get("y", 0))
    w, h = float(g.get("w", 0)), float(g.get("h", 0))
    # 透明底素材要先摊白：不摊的话透明区的 RGB 是近黑，会被当成「整块有墨」
    mask = _flat_rgb(src).convert("L").point(lambda v: 255 if v < 170 else 0)
    bb = mask.getbbox()
    if bb:
        mask = mask.crop(bb)
    mask = mask.rotate(-90, expand=True)
    if w and h:
        mask = mask.resize((max(1, int(round(w * cv.k))), max(1, int(round(h * cv.k)))),
                           Image.LANCZOS)
    cv.paste_mask(mask, x, y, ink(g.get("color"), "#000000"))


# ══════════════════════════════════════════════════════════════════
#  重复组
# ══════════════════════════════════════════════════════════════════
def _draw_group(cv: LK.Canvas, grp: dict, fields: dict, fam: str):
    """同一套元素按「位置」重复。四宫格 = 一张小标签印 4 次。

    元素里的坐标有两种写法：
      dx   —— 相对格子左边
      dxr  —— 相对格子右边（右对齐的东西用它，不用自己算宽度）
    `need` 用来标记「只在某些格子里出现」的元素，取值比位置上的 tag 列表。
    """
    elems = grp.get("元素") or []
    # 整组平移：界面上把四宫格当一个对象拖，改的就是这两个数，
    # 不用把每个位置、每个元素的坐标逐个改一遍。
    gdx, gdy = float(grp.get("dx", 0) or 0), float(grp.get("dy", 0) or 0)
    for pos in grp.get("位置") or []:
        ox = float(pos.get("x", 0)) + gdx
        oy = float(pos.get("y", 0)) + gdy
        cw = float(pos.get("w", 0))
        tags = pos.get("tag") or []
        if isinstance(tags, str):
            tags = [tags]
        for e in elems:
            need = e.get("need")
            if need and need not in tags:
                continue
            g = dict(e)
            if "dxr" in g:
                g["x"] = ox + cw - float(g["dxr"])
                g.setdefault("align", "right")
            else:
                g["x"] = ox + float(g.get("dx", 0))
            g["y"] = oy + float(g.get("dy", 0))
            if g.get("type") in (None, "text"):
                g["type"] = "text"
                _text_box(cv, g, fields, fam)
            elif g["type"] == "list":
                _list_box(cv, g, fields, fam)
            else:
                _draw_graphic(cv, g, fields, fam)


# ══════════════════════════════════════════════════════════════════
#  输出
# ══════════════════════════════════════════════════════════════════
def save_png(tpl, path, fields=None, dpi=300):
    """单页 PNG —— 1-bit（和 PDF 同口径，见 save_pdf 的注释）。

    以前是 RGB：PNG 无损压缩，一张 300dpi 要 1~2 MB。
    1-bit 后同尺寸只剩几百 KB，且和印刷效果一致（只有黑白）。
    """
    render(tpl, fields, dpi).convert("L").point(
        lambda v: 255 if v >= 128 else 0, mode="1").save(path, "PNG", optimize=True)
    return path


# ── PDF 里的文字层 ────────────────────────────────────────────────
# 只把位图塞进 PDF 的话，导出的文件是「图片 PDF」：放大就糊，选中不了，
# 也提取不出一个字。这里在图片之上再盖一层**不可见文字**（render_mode=3），
# 画面一个像素都不变，但任何阅读器都能选中、搜索、复制出真实文字。
# 这是扫描件做 OCR 之后的标准形态（searchable PDF）。
_MU = 72 / 25.4                     # 毫米 → PDF 点


def _ink_text_layer(page, texts, w_mm, h_mm, margin=0.0):
    """在页面上铺一层不可见文字。texts = [(文字, x_mm, y_mm, 字高_mm), ...]

    ★ `texts` 里的坐标是**模型坐标**（原点 = 黑框外沿），而 PDF 页面坐标的
      原点在页面左上角 = 黑框外沿再往左上 m 毫米处（P1-6 用户要求）。
      所以落笔前要**整体加 margin** —— 不加的话，PDF 里的文字层会比画面
      偏出 m 毫米，搜索/选中时对不上看得见的字（验收项 4 就是查这个）。

    字体用 PDF 自带的 CJK 字体（china-ss），不嵌入任何字体文件：
      · 我们的文字是**不可见**的（render_mode=3），显示成什么样都无所谓，
        要的只是「阅读器能选中、搜索、复制出来」；
      · 嵌入一份中文字体要十几兆（ttc 子集化在 macOS 上还会失败），
        而内置 CJK 字体是 0 字节 —— 整份 PDF 只多 1 KB。
    """
    if not texts:
        return 0
    n = 0
    for item in texts:
        # 兼容两种长度：4 项（老记录）和 5 项（带精确基线，P1-6）
        if len(item) >= 5:
            s, x, y, sz, baseline = item[0], item[1], item[2], item[3], item[4]
        else:
            s, x, y, sz = item[0], item[1], item[2], item[3]
            baseline = sz * 0.82        # 老记录退回估算
        s = str(s or "")
        if not s.strip():
            continue
        # y 是渲染原点（已含"墨迹上沿对齐"），再加基线偏移才是基线
        base = (y + margin + baseline) * _MU
        px = (x + margin) * _MU
        if base > h_mm * _MU or px > w_mm * _MU:
            continue
        try:
            page.insert_text((max(0.0, px), max(0.0, base)), s,
                             fontsize=max(1.0, sz * _MU), fontname="china-ss",
                             render_mode=3, color=(0, 0, 0))
            n += 1
        except Exception:
            continue
    return n


def save_pdf(tpl, path, fields=None, dpi=300, with_text=True, vector=False):
    """导出单页 PDF。

    vector=False（默认）导出**位图 PDF**：画面按 dpi 光栅化成一张图，再叠一层
    不可见文字（可搜索/可选中）。放大到超高倍数会糊，但对标签打印来说 300~600
    dpi 已经足够清晰。

    vector=True 走实验性的真矢量渲染（见 render_vec.py），文字是矢量对象、
    任意缩放清晰；但个别拉伸字标与位图版存在轻微对位偏差，暂不作为默认。

    with_text 只在 vector=False 时生效；矢量 PDF 里文字天然就是文字。
    """
    if vector:
        import render_vec
        return render_vec.save_pdf(tpl, path, fields, dpi)

    import pymupdf
    # ★ 1-bit 位图（2026-11-23 P1-1 实测后定稿）
    #
    # 用户要求的就是「1-bit 位图」。同页 600dpi 三种方案的实测：
    #   现状 RGB       2083 KB   像素差 0.00%
    #   灰度 8-bit     1126 KB   像素差 0.00%
    #   **1-bit 阈值    197 KB   像素差 0.00%**  ← 采用
    #   1-bit 抖动      279 KB   （放大有网纹，不用）
    #
    # 为什么像素差能是 0：标签是黑稿，本来只有黑白两色，
    # 灰度的中间值全是抗锯齿产生的。转 1-bit 只是把那条过渡带
    # 一刀切在 128，在 600dpi（0.042mm/px）下肉眼看不出，
    # 而体积再降 82%。
    #
    # 阈值用点操作而不是 `convert("1")`：后者默认**抖动**，
    # 会把大片浅色区打成网点，放大看是密密麻麻的点阵。
    #
    # 为什么不走矢量：实测 render_vec 与 600dpi 位图差 27.10%，
    # 条码画成乱码（`REF 1) +0+) LOT`）、BrandX 字标重叠 —— 见
    # dev/measure_pdf.py 的输出与 /tmp/pdf_measure/zoom800_*.png。
    img = render(tpl, fields, dpi).convert("L").point(
        lambda v: 255 if v >= 128 else 0, mode="1")
    texts = list(getattr(_LAST_CANVAS, "texts", []) or [])
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    # ★ PDF 页面 = 画布 = 黑框 + 2m（验收项 1：误差 ≤0.01mm）
    _r = _FRAMEMOD.canvas_rect(tpl)
    w_mm, h_mm = _r["w"], _r["h"]
    doc = pymupdf.open()
    page = doc.new_page(width=w_mm * _MU, height=h_mm * _MU)
    page.insert_image(page.rect, stream=buf.getvalue())
    if with_text:
        # 文字层坐标同样走 canvas_rect 的 margin（验收项 4）
        _ink_text_layer(page, texts, w_mm, h_mm, margin=_r["margin"])
    # deflate 必须开：不压缩的话一张 300dpi 的图能把 PDF 撑到 15 MB
    doc.save(path, garbage=4, deflate=True)
    doc.close()
    return path


def _safe_name(v, fallback):
    t = str(v or "").strip() or fallback
    for ch in '\\/:*?"<>|':
        t = t.replace(ch, "-")
    return t[:60]


def save_batch_pdf(tpl, path, records, dpi=300, with_text=True, vector=False,
                   progress=None, cancel=None):
    """一条规格一页，合成一个多页 PDF。返回页数。

    progress: `progress(done, total)`，每页画完调一次 —— 界面靠它推进度条。
    cancel:   `cancel()` 返回 True 就中止；已写的半个文件由调用方删。
    """
    if vector:
        import render_vec
        return render_vec.save_batch_pdf(tpl, path, records, dpi)

    import pymupdf
    doc = pymupdf.open()
    # 画布范围只从 canvas_rect 取（多页 PDF 每页同尺寸）
    _r = _FRAMEMOD.canvas_rect(tpl)
    w_mm, h_mm = _r["w"], _r["h"]
    total = len(records)
    for _i, rec in enumerate(records, 1):
        if cancel is not None and cancel():
            doc.close()
            raise RuntimeError("已取消")
        buf = io.BytesIO()
        # 同 save_pdf：1-bit 阈值（黑稿只有黑白，中间值是抗锯齿）
        render(tpl, rec, dpi).convert("L").point(
            lambda v: 255 if v >= 128 else 0, mode="1").save(
                buf, "PNG", optimize=True)
        texts = list(getattr(_LAST_CANVAS, "texts", []) or [])
        page = doc.new_page(width=w_mm * _MU, height=h_mm * _MU)
        page.insert_image(page.rect, stream=buf.getvalue())
        if with_text:
            _ink_text_layer(page, texts, w_mm, h_mm, margin=_r["margin"])
        if progress is not None:
            try:
                progress(_i, total)
            except Exception:
                pass
    doc.save(path, garbage=4, deflate=True)
    doc.close()
    return len(records)


def save_batch_png(tpl, outdir, records, dpi=300, key_field="", prefix="",
                   progress=None, cancel=None):
    """一条规格一张 PNG，文件名用规格键。返回文件路径列表。

    progress / cancel 同 save_batch_pdf。
    """
    os.makedirs(outdir, exist_ok=True)
    used, out = set(), []
    total = len(records)
    for i, rec in enumerate(records, 1):
        if cancel is not None and cancel():
            raise RuntimeError("已取消")
        key = _safe_name(rec.get(key_field), "规格%02d" % i)
        name = "%s%s" % (prefix, key)
        n = 2
        while name in used:
            name = "%s%s(%d)" % (prefix, key, n)
            n += 1
        used.add(name)
        fp = os.path.join(outdir, name + ".png")
        # PNG 也走 1-bit：和 PDF 同口径
        render(tpl, rec, dpi).convert("L").point(
            lambda v: 255 if v >= 128 else 0, mode="1").save(fp, "PNG", optimize=True)
        out.append(fp)
        if progress is not None:
            try:
                progress(i, total)
            except Exception:
                pass
    return out
