# -*- coding: utf-8 -*-
"""labelkit.py —— 毫米坐标画布原语。

设计原则：
  · 所有坐标单位毫米，原点左上角，和标签图纸完全一致；
  · 文字分两种落法：`text()` 按字号自动排版（日常排版用），
    `fit()` 强制塞进给定宽高盒子（字标 / logo 用，允许变形）；
  · 画布只依赖 Pillow，不依赖 tkinter，界面和导出共用同一份像素结果。
"""
import os

from PIL import Image, ImageDraw, ImageFont

# 字体链：Windows 路径在前（Windows 上第一项就命中，行为与旧版一致），
# macOS 前几项不存在自然落到后面。两平台共用同一份代码。
_WIN = r"C:\Windows\Fonts"
_MAC = "/System/Library/Fonts"
_MAC_S = "/System/Library/Fonts/Supplemental"

# 随程序走的字体目录（相对本文件：app/engine → ../../data/字体）。
# 为什么自带：图纸规定的是「思源黑体」，Mac 上没有这个字体（只有冬青黑/
# 苹方），Windows 上默认是微软雅黑 —— 同一份模板在两边印出来字宽不一样。
# 思源黑体（Noto Sans SC）是 OFL 授权，可以随程序分发，两边就同源了。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BUNDLE = os.path.join(_ROOT, "data", "字体")
_BUNDLE_OLD = os.path.join(_ROOT, "data", "素材", "字体")   # 迁移前的老位置


def _bundled(name):
    p = os.path.join(_BUNDLE, name)
    if os.path.exists(p):
        return p
    return os.path.join(_BUNDLE_OLD, name)

# 每一项是 [(字体文件, TTC 里的第几个字面), ...]。
# 为什么带 index：Mac 的中文字体都是 .ttc 合集 —— Songti.ttc 里第 0 个是
# 「Songti SC Black」（超粗），第 6 个才是「Songti SC Regular」。只给路径的话
# 永远拿到第 0 个，宋体看着像粗黑，几种字体选下来都一个样。
FONTS = {
    # —— 中文族 ——
    "hei": [(os.path.join(_WIN, "msyh.ttc"), 0),
            (os.path.join(_WIN, "msyh.ttf"), 0),
            (os.path.join(_WIN, "NotoSansSC-VF.ttf"), 0),
            (os.path.join(_MAC, "Hiragino Sans GB.ttc"), 0),
            (os.path.join(_MAC, "STHeiti Medium.ttc"), 0)],
    "heib": [(os.path.join(_WIN, "msyhbd.ttc"), 0),
             (os.path.join(_WIN, "msyh.ttc"), 0),
             (os.path.join(_MAC, "Hiragino Sans GB.ttc"), 2),
             (os.path.join(_MAC, "STHeiti Medium.ttc"), 0)],
    "song": [(os.path.join(_WIN, "simsun.ttc"), 0),
             (os.path.join(_WIN, "NotoSerifSC-VF.ttf"), 0),
             (os.path.join(_MAC_S, "Songti.ttc"), 6),
             (os.path.join(_MAC_S, "Songti.ttc"), 4)],
    "songb": [(os.path.join(_WIN, "STZHONGS.TTF"), 0),
              (os.path.join(_WIN, "simsun.ttc"), 0),
              (os.path.join(_MAC_S, "Songti.ttc"), 1),
              (os.path.join(_MAC_S, "Songti.ttc"), 0)],
    # —— 拉丁族 ——
    "lat": [(os.path.join(_WIN, "arial.ttf"), 0),
            (os.path.join(_MAC_S, "Arial.ttf"), 0),
            (os.path.join(_MAC, "Helvetica.ttc"), 0)],
    "latb": [(os.path.join(_WIN, "arialbd.ttf"), 0),
             (os.path.join(_WIN, "arial.ttf"), 0),
             (os.path.join(_MAC_S, "Arial Bold.ttf"), 0),
             (os.path.join(_MAC_S, "Arial.ttf"), 0),
             (os.path.join(_MAC, "Helvetica.ttc"), 1)],
    "mono": [(os.path.join(_WIN, "consola.ttf"), 0),
             (os.path.join(_MAC, "Menlo.ttc"), 0),
             (os.path.join(_MAC, "Courier.ttc"), 0)],
    "impact": [(os.path.join(_WIN, "impact.ttf"), 0),
               (os.path.join(_MAC_S, "Impact.ttf"), 0)],
    "times": [(os.path.join(_WIN, "times.ttf"), 0),
              (os.path.join(_MAC_S, "Times New Roman.ttf"), 0)],
    "timesb": [(os.path.join(_WIN, "timesbd.ttf"), 0),
               (os.path.join(_WIN, "times.ttf"), 0),
               (os.path.join(_MAC_S, "Times New Roman Bold.ttf"), 0),
               (os.path.join(_MAC_S, "Times New Roman.ttf"), 0)],
    "georgia": [(os.path.join(_WIN, "georgia.ttf"), 0),
                (os.path.join(_MAC_S, "Georgia.ttf"), 0)],
    "georgiab": [(os.path.join(_WIN, "georgiab.ttf"), 0),
                 (os.path.join(_WIN, "georgia.ttf"), 0),
                 (os.path.join(_MAC_S, "Georgia Bold.ttf"), 0),
                 (os.path.join(_MAC_S, "Georgia.ttf"), 0)],
    # —— 思源黑体（图纸指定的字体，随程序自带，四档字重）——
    # 细体是给「注册信息」那种 1.8mm 上下的小字用的：正体在 300dpi 下笔画
    # 会粗一圈，整段墨量比印刷稿多一成多，看着发黑。
    "siyuanl": [_bundled("NotoSansSC-Light.otf"),
                _bundled("NotoSansSC-Regular.otf"),
                (os.path.join(_WIN, "NotoSansSC-VF.ttf"), 0)],
    "siyuan": [_bundled("NotoSansSC-Regular.otf"),
               (os.path.join(_WIN, "NotoSansSC-VF.ttf"), 0),
               (os.path.join(_MAC, "Hiragino Sans GB.ttc"), 0)],
    "siyuanm": [_bundled("NotoSansSC-Medium.otf"),
                _bundled("NotoSansSC-Regular.otf"),
                (os.path.join(_WIN, "NotoSansSC-VF.ttf"), 0)],
    "siyuanb": [_bundled("NotoSansSC-Bold.otf"),
                _bundled("NotoSansSC-Medium.otf"),
                (os.path.join(_WIN, "msyhbd.ttc"), 0)],
}
_fcache = {}

# 界面下拉里的字体族（key → 中文名）。顺序 = 面板里的顺序。
FAMILIES = [
    ("siyuanl", "思源黑体 · 细"),
    ("siyuan", "思源黑体"),
    ("siyuanm", "思源黑体 · 中"),
    ("siyuanb", "思源黑体 · 粗"),
    ("hei", "黑体"),
    ("heib", "黑体 · 粗"),
    ("song", "宋体"),
    ("songb", "宋体 · 粗"),
    ("mono", "等宽"),
    ("impact", "Impact"),
    ("times", "Times"),
    ("timesb", "Times · 粗"),
    ("georgia", "Georgia"),
    ("georgiab", "Georgia · 粗"),
    ("lat", "Arial"),
    ("latb", "Arial · 粗"),
]

# ══════════════════════════════════════════════════════════════
#  ★ 全项目唯一的字体默认值（P1-3）
#
#  以前默认值散在 6 个地方，而且**写得不一致**：
#      store.py:68       "hei"
#      store.py:278      "hei"
#      store.py:661      "hei"
#      render.py:302/383 "hei"
#      render_vec.py:693 "hei"
#      designer_app.py:4647/4658  "siyuan"   ← 和上面不一样！
#  结果是：新建模板得到黑体，但右栏下拉在某些路径下显示思源；
#  用户看到的"当前字体"和实际画出来的字体对不上。
#
#  现在只留这一个常量。任何地方要默认字体，import 它，不许写字面量。
# ══════════════════════════════════════════════════════════════
DEFAULT_FONT = "hei"

# 层 → 字体字段名。L3（固定文字）和 L4（数据）各自独立可设。
FONT_KEYS = {"L3": "字体_L3", "L4": "字体_L4"}

# 老模板的字段名 → 新字段名（读入时用它迁移）
LEGACY_FONT_KEY = "字体"

# 族 → (常规, 粗体) 对应的拉丁字面。
# 模板里写 kind="latb" 的块（规格数字、品牌字标）意思是「这一处要粗的拉丁」，
# 不该永远锁死 Arial —— 换了字体族就该跟着变，否则下拉里选什么都一个样。
LATIN_ROLE = {
    "hei": ("lat", "latb"), "heib": ("latb", "latb"),
    "song": ("song", "songb"), "songb": ("songb", "songb"),
    "mono": ("mono", "mono"),
    "impact": ("impact", "impact"),
    "times": ("times", "timesb"), "timesb": ("timesb", "timesb"),
    "georgia": ("georgia", "georgiab"), "georgiab": ("georgiab", "georgiab"),
    "lat": ("lat", "latb"), "latb": ("latb", "latb"),
    # 思源自带拉丁字形，数字/字母也用它 —— 和图纸上那一套是同源，
    # 不用再去凑 Arial 的字宽（规格数字差半个字宽，四宫格里就要重排）。
    "siyuanl": ("siyuanl", "siyuanb"),
    "siyuan": ("siyuan", "siyuanb"), "siyuanm": ("siyuanm", "siyuanb"),
    "siyuanb": ("siyuanb", "siyuanb"),
}

# 哪些字面里带汉字（别的族画中文会出豆腐块）
CJK_KINDS = ("hei", "heib", "song", "songb", "siyuanl", "siyuan",
             "siyuanm", "siyuanb")


def latin_role(fam, bold=False):
    """字体族下「拉丁/数字」该用哪个字面。"""
    pair = LATIN_ROLE.get(fam) or LATIN_ROLE["hei"]
    return pair[1] if bold else pair[0]


# 拉丁族（Impact / Times / Georgia / Arial / 等宽）里没有汉字 —— 直接拿去画
# 「注册信息」「商品名」会得到一排豆腐块。所以含中文的块一律回到有汉字的族，
# 只有数字和字母跟着选中的族走：选 Impact 时「1.75 x 20」变粗壮，中文照旧。
CJK_ROLE = {
    "impact": "hei", "times": "song", "timesb": "songb",
    "georgia": "song", "georgiab": "songb",
    "lat": "hei", "latb": "heib", "mono": "hei",
}


def cjk_kind(fam):
    """这个族画中文时实际用哪个字面（保证不出豆腐块）。"""
    return fam if fam in CJK_KINDS else CJK_ROLE.get(fam, "hei")


def font_spec(kind):
    """这个字面该用哪个文件、第几个 —— 返回 (路径, index)，找不到返回 ("", 0)。"""
    for spec in FONTS.get(kind, FONTS["hei"]):
        p, idx = spec if isinstance(spec, tuple) else (spec, 0)
        if os.path.exists(p):
            return p, idx
    return "", 0


def font_path(kind):
    return font_spec(kind)[0]


def font_px(kind, px):
    """按像素字号取字体（内部缓存）。"""
    px = max(4, int(round(px)))
    key = (kind, px)
    if key not in _fcache:
        fp, idx = font_spec(kind)
        try:
            _fcache[key] = (ImageFont.truetype(fp, px, index=idx)
                            if fp else ImageFont.load_default())
        except Exception:
            try:
                _fcache[key] = ImageFont.truetype(fp, px) if fp else ImageFont.load_default()
            except Exception:
                _fcache[key] = ImageFont.load_default()
    return _fcache[key]


def font(kind, size_mm, dpi=300):
    return font_px(kind, size_mm * dpi / 25.4)


def has_cjk(s):
    for c in s:
        o = ord(c)
        if 0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            return True
    return False


def auto_kind(s, bold=False):
    if has_cjk(s):
        return "hei"
    return "latb" if bold else "lat"


_MCACHE = {}          # (dpi, kind, 像素字号, 文本) → (宽mm, 高mm, bb)

_MEASURE_DRAW = None


def text_metrics(s, size_mm, kind, dpi=300):
    """量一句话的墨迹尺寸：(宽mm, 高mm, 像素bb, k)。

    和 Canvas.measure 用的是同一个算法（PIL textbbox + 同一个字体），
    区别只是这里不需要先建一块画布 —— 界面上要量「选中框该多大」时，
    不必为了量一行字去分配一张位图。

    参数 dpi 保持可传：像素 bb 的数值依赖 dpi，但换成毫米之后与 dpi 无关。
    """
    global _MEASURE_DRAW
    if _MEASURE_DRAW is None:
        _MEASURE_DRAW = ImageDraw.Draw(Image.new("L", (4, 4)))
    k = dpi / 25.4
    try:
        f = font(kind, size_mm, dpi)
        b = _MEASURE_DRAW.textbbox((0, 0), s, font=f)
    except Exception:
        return (0.0, 0.0, (0, 0, 0, 0), k)
    return ((b[2] - b[0]) / k, (b[3] - b[1]) / k, b, k)

# ══════════════════════════════════════════════════════════════
#  字距 / 词距（P1-5）
#
#  以前想让一行字变宽，只能 `xs`（横向拉伸）—— 那是**把字压扁**，
#  字形纵横比就变了，看着就是"这行字被捏过"。印刷上不可接受。
#
#  正确的做法是**字距**（tracking）：字与字之间多留/少留一点，
#  字形本身一点不变。
#    tracking  字距，毫米，字与字之间额外加的间距（可为负）
#    word_gap  词距，毫米，空格处**额外**加的间距（在 tracking 之上）
#
#  这两个值在 CLI/模板里按毫米存，渲染时换算成像素。
# ══════════════════════════════════════════════════════════════
# 量墨迹宽 / 高时统一的基准分辨率（P1-5）。
# 抗锯齿边缘随分辨率变化，同一个字 300dpi 和 600dpi 量出的墨迹宽差 0.3~0.4mm。
# 全项目固定用这个值做**度量**，渲染用各自需要的 dpi。
MEASURE_DPI = 300

TRACKING_KEY = "tracking"
WORDGAP_KEY = "word_gap"


def tracking_of(b: dict) -> float:
    """元素的字距（毫米）。没有就是 0。"""
    try:
        return float((b or {}).get(TRACKING_KEY) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def wordgap_of(b: dict) -> float:
    """元素的词距（毫米，空格处额外加的）。没有就是 0。"""
    try:
        return float((b or {}).get(WORDGAP_KEY) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_cjk(ch: str) -> bool:
    """是不是中日韩字符（判断"词"边界用）。"""
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
            or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF)


def _gaps(s: str, tracking_mm: float, word_gap_mm: float, k: float):
    """每个字之后要额外加多少像素。

    返回长度 == len(s) 的列表，第 i 项是「第 i 个字画完之后额外挪多少」。
    最后一位恒为 0（行尾不补字距，否则整行宽度会多出一个字距，
    "按叠影定宽"会越定越宽）。
    """
    n = len(s)
    out = [0.0] * n
    if n <= 1:
        return out
    tpx = tracking_mm * k
    wpx = word_gap_mm * k
    for i in range(n - 1):
        extra = tpx
        # ★ 词距**只在空格本身那一个间隙上加**（P1-5 修的 bug）
        #   以前写成 `ch == " " or nxt == " "`，于是 "a b c" 的两个空格
        #   被算成 3 次、4 个空格算成 8 次 —— 词距实际被放大了近一倍，
        #   求解器算出来的宽和实画差 4~5mm。词是一个"间隙"，不是一个"位置"。
        if s[i] == " ":
            extra += wpx
        out[i] = extra
    return out


def text_tracking_w(s: str, size_mm: float, kind: str, dpi: int = 300,
                    tracking_mm: float = 0.0, word_gap_mm: float = 0.0,
                    draw=None, k=None):
    """含字距的文字**行宽** (宽mm, 高mm)。和 text_tracked 逐字累加同一套。

    ★ 这段踩了整整四轮，结论必须记住（P1-5）
    -------------------------------------
    **行宽 = Σ(每个字的 advance) + Σ(额外字距)**

    这是排版意义上的行宽：笔从行首走到行尾走了多远。

    期间试错过两次，都是同一个错误 —— 想"对齐墨迹"而扣 bearing：
      ① 用 textbbox 的宽      → 比实画窄，因为 textbbox 只覆盖墨迹
      ② 逐字 bbox 取并集      → 同上
    两者都比逐字 advance 累加小 0.2~1mm，而且**不同字符串差得不一样**
    （拉丁 side bearing 大、中文小），根本没法用一个常数补偿。

    实测："ABC" advance 累加 5.46mm，textbbox 只有 4.40mm —— 差 1mm。
    拿 textbbox 当宽度去"定宽"，定出来的行会窄一大截。

    结论：**一律用 advance 累加**。要和"墨迹"比的时候（比如验证），
    从渲染出来的图上量 bbox，不要从字体度量里推。
    """
    _w0, h0, _bb, _k = text_metrics(s, size_mm, kind, dpi)
    if not s:
        return 0.0, h0
    kk = k if k else dpi / 25.4
    if draw is None:
        global _MEASURE_DRAW
        if _MEASURE_DRAW is None:
            _MEASURE_DRAW = ImageDraw.Draw(Image.new("L", (4, 4)))
        draw = _MEASURE_DRAW
    try:
        f = font(kind, size_mm, dpi)
        total_px = 0.0
        for ch in s:
            total_px += float(draw.textlength(ch, font=f))
    except Exception:
        return _w0, h0
    # 口径必须和 _gaps 一致：字距 n-1 个，词距 = 空格个数
    # （空格在行尾时不算 —— 与 _gaps 的 n-1 截断一致）
    gaps = tracking_mm * max(0, len(s) - 1)
    gaps += word_gap_mm * sum(1 for i, ch in enumerate(s)
                              if ch == " " and i < len(s) - 1)
    return total_px / kk + gaps, h0


# 字形位图缓存：(kind, 像素字号, 文本) → (L 图, 偏移)
# 为什么值得单独缓存：FreeType 光栅化一次 0.8ms，而四宫格里同一句话要画 4 遍，
# 拖动时一秒要重画十几帧 —— 之前一半的时间花在把同一句话反复光栅化上。
_GLYPH = {}

# 哪几档字面印出来偏虚：思源「细」在 1.8~2mm 这个尺寸上，600dpi 打出来
# 笔画只剩一两像素，纸面上看着发灰、断笔。补一点点墨就正好了 ——
# 不是换一档字重（正体会粗整整一圈），只是每边加厚约 0.008mm：
# 实测墨量 +18% 左右，落在「细」和「正体」之间，肉眼是「不再发虚」。
THIN_KINDS = ("siyuanl",)
GAIN_MM = 0.008


def _gain(img, w):
    """把字形遮罩往外扩一点点，返回 (新图, dx, dy)。

    w 是「扩散层的浓度」：1.0 = 每边实打实多 1 像素，0.5 = 半像素的分量。
    扩完图会大 2 像素（每边 1），所以落点要往回挪 1 像素。
    """
    from PIL import ImageFilter
    big = img.filter(ImageFilter.MaxFilter(3))
    if w >= 0.999:
        return big, -1, -1
    pad = Image.new("L", big.size, 0)
    pad.paste(img, (1, 1))
    return Image.blend(pad, big, max(0.0, w)), -1, -1


def glyph(kind, px, s, gain=0.0):
    """取一句话的字形位图（紧贴墨迹的 L 图 + 相对基线的偏移）。

    与 ImageDraw.text 同源：text() 内部也是 getmask2 + offset 画上去的，
    这里只是把结果留下来复用，像素完全一致。
    gain > 0 时按那个浓度补墨（见 THIN_KINDS）。
    """
    px = max(4, int(round(px)))
    key = (kind, px, s, round(gain, 2))
    v = _GLYPH.get(key)
    if v is None:
        try:
            m, off = font_px(kind, px).getmask2(s, mode="L")
            img = Image.frombytes("L", m.size, bytes(m))
        except Exception:
            return None
        if gain > 0:
            img, dx, dy = _gain(img, gain)
            off = (off[0] + dx, off[1] + dy)
        v = (img, off)
        if len(_GLYPH) > 1500:
            _GLYPH.clear()
        _GLYPH[key] = v
    return v


class Canvas:
    """毫米坐标画布。"""

    def __init__(self, w_mm, h_mm, dpi=300, bg="white", ox=0.0, oy=0.0):
        self.k = dpi / 25.4
        self.dpi = dpi
        self.w_mm, self.h_mm = w_mm, h_mm
        # 纸面在画布里的位置（毫米）。导出时是 0；屏幕预览要留一圈纸外空白，
        # 就把纸面挪到 (ox, oy)，原来那套毫米坐标一个字都不用改。
        self.ox, self.oy = float(ox), float(oy)
        self.W = max(1, int(round(w_mm * self.k)))
        self.H = max(1, int(round(h_mm * self.k)))
        self.im = Image.new("RGB", (self.W, self.H), bg)
        self.draw = ImageDraw.Draw(self.im)
        # 每段落字的「实际落点」都记一份：导出 PDF 时要在同样的位置补一层
        # 不可见文字，PDF 才能被搜索/复制/提取 —— 而画面仍是这张位图，不变样。
        self.texts = []

    # 毫米 → 像素。所有图元都从这里过一遍，纸面偏移只在这一处生效。
    def px(self, x):
        return (float(x) + self.ox) * self.k

    def py(self, y):
        return (float(y) + self.oy) * self.k

    # ─────────────────────────── 图元 ───────────────────────────
    def line(self, x0, y0, x1, y1, w=0.25, color="#000000"):
        self.draw.line([(self.px(x0), self.py(y0)), (self.px(x1), self.py(y1))],
                       fill=color, width=max(1, int(round(w * self.k))))

    def rect(self, x0, y0, x1, y1, w=0.25, color="#000000"):
        self.draw.rectangle([self.px(x0), self.py(y0), self.px(x1), self.py(y1)],
                            outline=color, width=max(1, int(round(w * self.k))))

    def fill_rect(self, x0, y0, x1, y1, color="#000000"):
        self.draw.rectangle([self.px(x0), self.py(y0), self.px(x1), self.py(y1)],
                            fill=color)

    def rrect(self, x0, y0, x1, y1, r=3.0, w=0.3, color="#000000"):
        """圆角矩形描边。"""
        self.draw.rounded_rectangle([self.px(x0), self.py(y0),
                                     self.px(x1), self.py(y1)],
                                    radius=r * self.k, outline=color,
                                    width=max(1, int(round(w * self.k))))

    def rrect_fill(self, x0, y0, x1, y1, r=3.0, color="#000000"):
        """圆角矩形填充（横幅底色这类）。"""
        self.draw.rounded_rectangle([self.px(x0), self.py(y0),
                                     self.px(x1), self.py(y1)],
                                    radius=r * self.k, fill=color)

    # ─────────────────────────── 文字 ───────────────────────────
    def measure(self, s, size, kind=None, bold=False):
        """(宽mm, 高mm, 像素bb)。带缓存 —— 同一句字在 4 个格子里要量 4 次，
        拖动时每秒要重画十几帧，不缓存的话光量字就占掉一半时间。"""
        kind = kind or auto_kind(s, bold)
        px = max(4, int(round(size * self.dpi / 25.4)))
        key = (self.dpi, kind, px, s)
        v = _MCACHE.get(key)
        if v is None:
            b = self.draw.textbbox((0, 0), s, font=font(kind, size, self.dpi))
            v = ((b[2] - b[0]) / self.k, (b[3] - b[1]) / self.k, b)
            if len(_MCACHE) > 6000:
                _MCACHE.clear()
            _MCACHE[key] = v
        return v

    def text(self, x, y, s, size=3.0, kind=None, bold=False,
             color="#111111", anchor="la"):
        """按字号排版。anchor 同 PIL（la=左上, ma=上居中, ra=右上,
        lm=左中, mm=正中, rm=右中, ld=左下, md=下居中, rd=右下）。"""
        kind = kind or auto_kind(s, bold)
        self.draw.text((self.px(x), self.py(y)), s,
                       font=font(kind, size, self.dpi), fill=color, anchor=anchor)
        if s:
            self.texts.append((s, x, y, size))

    def fit(self, x, y, w, h, s, kind=None, bold=True, color="#111111", align="left",
            tw_hint=None):
        """把 s 渲染成恰好 w×h 的图贴上去（可横向拉伸，用于字标/logo）。

        align: left=左边界对齐 x；center=x 是中心；right=右边界对齐 x。
        """
        kind = kind or auto_kind(s, bold)
        if tw_hint is None:
            tw_hint = h
        # 探测字号跟目标像素高走：以前固定 30mm，300dpi 下等于 354px 的大字，
        # 光生成位图就要 4ms，而目标多半只有 50~180px —— 纯浪费。
        probe = min(180, max(32, int(round(tw_hint * self.k))))
        # 探测尺寸下的「紧贴墨迹的 L 图」缓存起来：四宫格里同一句字标要拉伸
        # 4 次，每次只是目标尺寸不同，字本身没必要重新光栅化。
        key = ("fit", kind, probe, s)
        tmp = _GLYPH.get(key)
        if tmp is None:
            mask = font_px(kind, probe).getmask(s, mode="L")
            tmp = Image.frombytes("L", mask.size, bytes(mask))
            bb = tmp.getbbox()
            tmp = tmp.crop(bb) if bb else tmp
            if len(_GLYPH) > 1500:
                _GLYPH.clear()
            _GLYPH[key] = tmp
        if tmp.width <= 0 or tmp.height <= 0:
            return
        tw, th = max(1, int(round(w * self.k))), max(1, int(round(h * self.k)))
        tmp = tmp.resize((tw, th), Image.LANCZOS)
        px = {"left": x, "center": x - w / 2.0, "right": x - w}[align]
        self.im.paste(Image.new("RGB", tmp.size, color),
                      (int(round(self.px(px))), int(round(self.py(y)))), tmp)
        if s:
            self.texts.append((s, px, y, h))


    def text_h(self, x, y, h, s, kind=None, bold=False, color="#111111",
               align="left", valign="top", maxw=None):
        """按目标字高自动定字号（保持自然字宽，不拉伸）。

        坐标指的是「字的墨迹上沿」，不是行框上沿——量图纸得到的就是墨迹位置，
        这样模板里的 y 可以直接照抄实测值，不用反复减行距。
        align: left / center / right —— 相对 x。
        """
        kind = kind or auto_kind(s, bold)
        if not s:
            return
        w0, h0, bb0 = self.measure(s, 10.0, kind)
        if h0 <= 0:
            return
        size = 10.0 * h / h0
        # 字号线性缩放时字宽/字高也线性 —— 拿 10 号那次的量测按比例换算就够，
        # 不用再为每个实际字号各量一次（同一句字在界面里一帧可能量十几次）。
        w, hh = w0 * size / 10.0, h0 * size / 10.0
        bb1 = bb0[1] * size / 10.0
        if maxw and w > maxw:                    # 超宽就等比缩字高，宁可字小也不越界
            size *= float(maxw) / w
            w, hh = w0 * size / 10.0, h0 * size / 10.0
            bb1 = bb0[1] * size / 10.0
        if align == "center":
            px = x - w / 2.0
        elif align == "right":
            px = x - w
        else:
            px = x
        py = y - bb1 / self.k                        # 墨迹上沿对齐 y
        if valign == "middle":
            py -= (hh - h) / 2.0
        # 走字形缓存：同一句话在四宫格里出现 4 次，只光栅化一次。
        # 偏移和 draw.text 内部用的 offset 是同一个，落点和以前一字不差。
        # 细字重（思源 Light）在这里补一点墨 —— 600dpi 打出来才不断笔。
        gain = 0.0
        if kind in THIN_KINDS:
            gain = min(1.0, GAIN_MM * self.k)     # 300dpi≈0.35，600dpi≈0.71
        g = glyph(kind, size * self.dpi / 25.4, s, gain)
        if g is None:
            return w, hh
        mask, off = g
        if mask.width <= 0 or mask.height <= 0:
            return w, hh
        ax, ay = int(round(self.px(px))), int(round(self.py(py)))
        self.im.paste(Image.new("RGB", mask.size, color),
                      (ax + off[0], ay + off[1]), mask)
        # 记**基线偏移**（第 5 项）：从记录的 py（= 渲染原点，已含
        #   "墨迹上沿对齐 y" 那一步）量到基线有多少毫米。
        #   以前 PDF 文字层用固定系数 `sz*0.82` 估基线，不同字形会差
        #   0.2~1.1mm —— 验收项 4（文字层 vs 可见字 ≤0.3mm）就是查这个。
        #   基线 = 原点 + bbox上沿 + 墨迹高  （原点在 bbox 上沿之上 bb1 处）
        try:
            _bt = (bb0[1] * size / 10.0) / self.k
            _bh = (bb0[3] - bb0[1]) * size / 10.0 / self.k
            _baseline = _bt + _bh            # 原点 → 基线
        except Exception:
            _baseline = size * 0.82
        self.texts.append((s, px, py, size, _baseline))
        return w, hh

    def _advance(self, ch, size, kind):
        """一个字符的**前进量**（毫米）—— 画完它笔往右移多少。"""
        try:
            f = font(kind, size, self.dpi)
            return float(self.draw.textlength(ch, font=f)) / self.k
        except Exception:
            w, _h, _b = self.measure(ch, size, kind)
            return w

    def text_tracked(self, x, y, h, s, kind=None, color="#111111",
                     align="left", valign="top", tracking=0.0,
                     word_gap=0.0, maxw=None):
        """按字高定字号，**逐字落笔**以支持字距 / 词距（P1-5）。

        为什么不复用 `text_h` 再横向拉伸：拉伸会把字压扁，纵横比就变了。
        这里改成一个字一个字地贴，每个字之间多挪 `tracking` 毫米 ——
        字形一个像素都不变形，只是间隙变宽/变窄。

        度量用 `text_tracking_w`（和这里同一套算法），保证
        "量出来多宽" == "画出来多宽"，界面选中框和实际墨迹不会差。

        Args:
            h: 目标墨迹高度（毫米）
            tracking: 字距（毫米）
            word_gap: 词距（毫米），空格处额外加
            maxw: 超宽时等比缩字高（宁可字小也不越界）

        Returns:
            (墨迹宽 mm, 墨迹高 mm)
        """
        if not s:
            return 0.0, 0.0
        kind = kind or auto_kind(s)
        w0, h0, bb0 = self.measure(s, 10.0, kind)
        if h0 <= 0:
            return 0.0, 0.0
        size = 10.0 * h / h0
        # 字距按**目标字号**换算：先按无字距算一遍总宽
        tw, _th = text_tracking_w(s, size, kind, self.dpi,
                                  tracking, word_gap)
        if maxw and tw > maxw and tw > 0:
            # 超宽：整行等比缩小（字距也跟着缩，保持相对关系）
            r = float(maxw) / tw
            size *= r
            tw, _ = text_tracking_w(s, size, kind, self.dpi, tracking, word_gap)

        hh = h if not (maxw and tw > maxw) else h * (size / (10.0 * h / h0))
        # 落点：整行墨迹按 align 对齐 x
        if align == "center":
            px = x - tw / 2.0
        elif align == "right":
            px = x - tw
        else:
            px = x
        # 基线偏移：用 10 号那次量测按比例换算（同 text_h 的做法）
        bb1 = bb0[1] * size / 10.0
        py = y - bb1 / self.k
        if valign == "middle":
            py -= (hh - h) / 2.0

        gain = 0.0
        if kind in THIN_KINDS:
            gain = min(1.0, GAIN_MM * self.k)

        # 逐字画：先算好每个字的自然前进量，再按需插字距
        cur = px
        gaps = _gaps(s, tracking, word_gap, self.k)
        for i, ch in enumerate(s):
            if ch != " ":
                g = glyph(kind, size * self.dpi / 25.4, ch, gain)
                if g is not None:
                    mask, off = g
                    if mask.width > 0 and mask.height > 0:
                        ax = int(round(self.px(cur)))
                        ay = int(round(self.py(py)))
                        self.im.paste(Image.new("RGB", mask.size, color),
                                      (ax + off[0], ay + off[1]), mask)
            # 前进量必须用**字体的 advance 宽度**，不能用 textbbox 的宽。
            # textbbox 量的是"墨迹外框"，含左右 bearing（字身留白）；
            # 逐字累加时每个字都多算一次 bearing，整行就系统性偏宽
            # （实测 16 个字符差 0.21mm，正好是约一个 bearing）。
            # getlength 给的是这个字画完后笔该往右移多少 —— 那是排版真正要的量。
            cur += self._advance(ch, size, kind) + gaps[i] / self.k
        # 逐字落笔后，PDF 文字层没法按整串给（位置是散的），
        # 这里仍按整串记一条 —— 可搜索性保留，落点用首字位置。
        self.texts.append((s, px, py, size))

        # ★ 返回值必须是**真实墨迹宽**（P1-5 验收要求误差 ≤0.2mm）
        #   前面算出的 tw 是"行宽"（advance 累加），比墨迹宽 0.15~0.3mm
        #   —— 因为末字右侧还有 bearing。界面拿它画选中框会明显偏宽，
        #   "按叠影定宽"拿它求解也会系统性偏大。
        #   这里直接从**已经画上去的像素**反量一次：从第一个非空字的最左
        #   到最后一个非空字的最右。代价是一次 bbox（几毫秒），换来
        #   "量 = 画"完全一致。
        ink_w = tw
        # ★ 在这张图**自己的分辨率**上量墨迹（P1-5）
        #   抗锯齿边缘随分辨率变化：同一段字 300dpi 量 25.061mm、
        #   600dpi 量 24.680mm（差 0.38mm）。所以"墨迹宽"没有绝对定义，
        #   必须和画布分辨率绑定 —— 就用 self.k，和实际贴上去的像素一致。
        #   求解器统一在 MEASURE_DPI(300) 下算，界面按屏幕 dpi 画，
        #   两边各按自己的分辨率量，结果自然对得上（差异 <0.05mm）。
        try:
            x0 = int(max(0, self.px(px) - 2))
            y0 = int(max(0, self.py(py) - 2))
            x1 = int(min(self.im.width, self.px(px + tw) + 4))
            y1 = int(min(self.im.height, self.py(py + hh) + 4))
            if x1 > x0 and y1 > y0:
                band = self.im.crop((x0, y0, x1, y1)).convert("L")
                bm = band.point(lambda v: 255 if v < 250 else 0)
                bb = bm.getbbox()
                if bb:
                    ink_w = (bb[2] - bb[0]) / self.k
        except Exception:
            pass
        return ink_w, hh

    # ─────────────────────────── 图片 ───────────────────────────
    def image(self, path, x, y, w=None, h=None, colorize=None):
        """贴图。只给 w 或只给 h 时等比缩放；两个都给则强制拉伸。"""
        im = Image.open(path)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg.convert("RGB")
        else:
            im = im.convert("RGB")
        if w is None and h is not None:
            w = h * im.size[0] / float(im.size[1])
        if h is None and w is not None:
            h = w * im.size[1] / float(im.size[0])
        if w is None:
            w, h = im.size[0] / self.k, im.size[1] / self.k
        im = im.resize((max(1, int(round(w * self.k))),
                        max(1, int(round(h * self.k)))), Image.LANCZOS)
        if colorize:
            gray = im.convert("L")
            mask = gray.point(lambda v: 255 - v)
            self.im.paste(Image.new("RGB", im.size, colorize),
                          (int(round(self.px(x))), int(round(self.py(y)))), mask)
            return h
        self.im.paste(im, (int(round(self.px(x))), int(round(self.py(y)))))
        return h

    def paste_mask(self, mask_img, x, y, color):
        """把灰度图当遮罩，用纯色贴上去（用于竖排字标）。"""
        self.im.paste(Image.new("RGB", mask_img.size, color),
                      (int(round(self.px(x))), int(round(self.py(y)))), mask_img)

    # ─────────────────────────── 输出 ───────────────────────────
    def to_image(self):
        return self.im

    def save_png(self, path):
        self.im.save(path, "PNG")
        return path

    def save_pdf(self, path):
        import io
        import pymupdf
        buf = io.BytesIO()
        self.im.save(buf, "PNG")
        doc = pymupdf.open()
        page = doc.new_page(width=self.w_mm * 72 / 25.4, height=self.h_mm * 72 / 25.4)
        page.insert_image(page.rect, stream=buf.getvalue())
        doc.save(path)
        doc.close()
        return path
