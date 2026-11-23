# -*- coding: utf-8 -*-
"""界面统一样式 —— 照项目6 基线 `02-界面视觉.md`（浅色系 UI 风格指南 v1.0）。

为什么需要这个文件
------------------
`tk.Button` 在 macOS 上由系统主题（aqua）画成原生 NSButton，**传进去的
`bg=` / `relief=` 会被 Tk 直接忽略**；同一份参数在 Windows 上却生效。
于是同一套代码在两平台长成两个软件 —— 这就是「UI 天差地别」的真正原因。

解法：按钮改用 `tk.Label` 自绘。Label 在所有平台都认 bg/fg，外观由这里决定，
两边逐像素一致。色板、字号阶梯照规范抄，界面上不再出现裸色值。

用法
----
    import ui_theme as T
    T.apply(root)                       # 建完 root 立刻调一次
    T.Btn(parent, text="保存", command=f, kind="primary")

`Btn` 的构造参数与 `tk.Button` 兼容（text / command / bg / fg / padx / pady /
font / state / width / anchor / wraplength / cursor），旧代码改个类名即可。
"""
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ---------------------------------------------------------------- 色板（规范 §2）
BG        = "#f5f6f8"   # 窗口底 / 工具栏
CARD      = "#ffffff"   # 卡片、按钮底、输入框
DZ_BG     = "#f7f9fc"   # 块中块（输入区常态底）
DZ_ON     = "#eaf1ff"   # 悬停 / 选中浅蓝底
CANVAS    = "#eef0f3"   # 画布底（比窗口底稍深，把内容衬出来）
STATUS    = "#eceef1"   # 底部状态栏（收口）

BORDER    = "#d9dee5"   # 全局唯一描边色，1px
TEXT      = "#2a2f36"   # 正文（不用纯黑）
TEXT2     = "#7a828c"   # 次要说明
TEXT3     = "#9aa1a9"   # 弱提示、脚注
DISABLED  = "#c3c8ce"   # 禁用文字

PRIMARY   = "#2f6fed"   # 主色，面积 ≤5%
PRIMARY_D = "#2257c4"   # 主色深（hover / pressed）
AMBER     = "#b26a00"   # 提示琥珀
RED       = "#c0392b"   # 止损红（只放字，不铺底）
RED_ON    = "#fdf1ef"   # 止损红 hover 底
RED_DN    = "#f7e0dc"   # 止损红 pressed 底

HOVER     = "#f0f2f5"   # 次按钮 hover
PRESSED   = "#e6e9ee"   # 次按钮 pressed

# 分段控件的选中态（项目1/2/3 同一套值）：淡蓝底 + 钢蓝字，不用实心主色，
# 因为一屏里已经有「保存」那种实心按钮，再多一块饱和色会互相抢。
TINT_HEAD = "#eaf1ff"
TINT_HEAD_FG = "#2257c4"
SEP = "#e3e7ec"         # 分区细线（比 BORDER 更淡，用来分「块」而不是分「控件」）
GUIDE = "#7599d6"       # 拖动时的对齐参考线（淡蓝点线，不能抢内容）
GRID = "#cfdcef"        # 画布上的 5mm 网格（比参考线淡，只做对位参照）

# ------------------------------------------------------- 字号阶梯（规范 §4，只用这六档）
SZ_ICON   = 15          # 拖放区大图标
SZ_DLG    = 13          # 弹窗标题
SZ_APP    = 12          # 应用标题
SZ_BODY   = 11          # 正文 / 按钮
SZ_SMALL  = 10          # 说明、工具条、勾选框、状态栏
SZ_FOOT   = 9           # 图例、脚注、版本号

# 界面字体：挑到哪个用哪个。Windows 雅黑、macOS 苹方，都是各自平台的原生感。
_UI_CHAIN = ("PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei",
             "Hiragino Sans GB", "Heiti SC", "STHeiti",
             "Noto Sans CJK SC", "Helvetica Neue")

# 量字宽用的字体：**必须**是导出时真正画字的那个族（见 engine/render.py 的
# FONT_CHAIN）。界面显示字体和它不一样没关系 —— 但拿它量，选中框才不会偏。
_MEASURE_CHAIN = ("Microsoft YaHei UI", "Microsoft YaHei",
                  "Hiragino Sans GB", "Heiti SC", "STHeiti",
                  "Noto Sans CJK SC", "Helvetica Neue")

UI_FONT = _UI_CHAIN[0]
MEASURE_FONT = _MEASURE_CHAIN[0]


def _pick(chain):
    try:
        have = set(tkfont.families())
    except Exception:              # 还没有根窗口
        return chain[0]
    for fam in chain:
        if fam in have:
            return fam
    return chain[0]


def resolve_fonts():
    """定稿字体。必须在根窗口建好之后调。"""
    global UI_FONT, MEASURE_FONT
    UI_FONT = _pick(_UI_CHAIN)
    MEASURE_FONT = _pick(_MEASURE_CHAIN)
    return UI_FONT, MEASURE_FONT


def font(size=SZ_BODY, bold=False):
    """界面字体元组。"""
    return (UI_FONT, size, "bold") if bold else (UI_FONT, size)


# ------------------------------------------------------------------ 颜色小工具
def mix(c1, c2, t):
    """把 c1 往 c2 混 t（0~1），返回 #rrggbb。用来算 hover/pressed。"""
    def rgb(c):
        c = c.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    a, b = rgb(c1), rgb(c2)
    return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _hover_of(bg):
    if bg == PRIMARY:
        return PRIMARY_D
    if bg in (CARD, "#ffffff", BG, DZ_BG):
        return HOVER
    return mix(bg, "#000000", 0.05)


def _press_of(bg):
    if bg == PRIMARY:
        return mix(PRIMARY_D, "#000000", 0.12)
    if bg in (CARD, "#ffffff", BG, DZ_BG):
        return PRESSED
    return mix(bg, "#000000", 0.10)


# ------------------------------------------------------------------ 自绘按钮
class Btn(tk.Label):
    """扁平按钮。参数兼容 tk.Button 的子集。

    kind: "normal"（白底细框）| "primary"（实心主色白字）| "stop"（白底红字）
    """

    def __init__(self, master=None, command=None, kind=None, **kw):
        # tk.Button 专有、Label 不认的参数，静默丢掉
        for k in ("relief", "overrelief", "bd", "activebackground",
                  "activeforeground", "disabledforeground", "takefocus",
                  "indicatoron", "default", "offvalue", "onvalue",
                  "highlightthickness"):
            kw.pop(k, None)

        bg = kw.pop("bg", None)
        fg = kw.pop("fg", None)
        if kind == "primary":
            bg = bg or PRIMARY
            fg = fg or "#ffffff"
            kw.setdefault("font", font(SZ_BODY, True))
        elif kind == "stop":
            bg = bg or CARD
            fg = fg or RED
            kw.setdefault("font", font(SZ_SMALL))
        else:
            bg = bg or DZ_BG
            fg = fg or TEXT
            kw.setdefault("font", font(SZ_SMALL))

        # 白底次按钮有 1px 描边，放在白面板上也不会消失，不必再换灰底。
        # （以前换成 DZ_BG，结果工具条上每个按钮都像贴了一块脏印子。）

        state = kw.pop("state", "normal")

        kw.setdefault("padx", 10)
        kw.setdefault("pady", 5)
        kw.setdefault("anchor", "center")
        kw.setdefault("bd", 0)
        kw.setdefault("highlightthickness", 1)
        kw.setdefault("highlightbackground", BORDER)
        kw.setdefault("highlightcolor", BORDER)
        kw.setdefault("cursor", "arrow")     # 不用小手：这个软件的按钮没有「链接」语义
        kw.setdefault("bg", bg)
        kw.setdefault("fg", fg)

        super().__init__(master, **kw)

        self._base_bg = bg
        self._base_fg = fg
        self._enabled = (state != "disabled")
        self._cmd = command

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_down)
        self.bind("<ButtonRelease-1>", self._on_up)
        if not self._enabled:
            self._paint_disabled()

    # -- 状态 ----------------------------------------------------------
    def _paint_disabled(self):
        tk.Label.config(self, fg=DISABLED, cursor="arrow", bg=self._base_bg)

    def _paint_normal(self):
        tk.Label.config(self, fg=self._base_fg, cursor="arrow", bg=self._base_bg)

    def set_enabled(self, on):
        self._enabled = bool(on)
        self._paint_normal() if on else self._paint_disabled()

    # -- 事件 ----------------------------------------------------------
    def _on_enter(self, _e=None):
        if self._enabled:
            tk.Label.config(self, bg=_hover_of(self._base_bg))

    def _on_leave(self, _e=None):
        if self._enabled:
            tk.Label.config(self, bg=self._base_bg)

    def _on_down(self, _e=None):
        if self._enabled:
            tk.Label.config(self, bg=_press_of(self._base_bg))

    def _on_up(self, _e=None):
        if not self._enabled:
            return
        tk.Label.config(self, bg=_hover_of(self._base_bg))
        if self._cmd:
            self._cmd()

    # -- 与 tk.Button 的 config 兼容 ------------------------------------
    def config(self, cnf=None, **kw):
        if cnf:
            kw = dict(cnf, **kw)
        if "state" in kw:
            self.set_enabled(kw.pop("state") != "disabled")
        if "bg" in kw:
            self._base_bg = kw["bg"]
            kw["bg"] = self._base_bg
        if "fg" in kw:
            self._base_fg = kw["fg"]
            if self._enabled:
                kw["fg"] = self._base_fg
        if "command" in kw:
            self._cmd = kw.pop("command")
        return tk.Label.config(self, **kw)

    configure = config

    def cget(self, key):
        if key == "state":
            return "normal" if self._enabled else "disabled"
        if key == "command":
            return self._cmd
        return tk.Label.cget(self, key)

    __getitem__ = cget


# --------------------------------------------------------------- 分段控件
class Seg(tk.Frame):
    """一排互斥的小按钮，外面包一圈 1px 框 —— 项目 1/2/3 里「并排/滑动/叠加」
    「导出PDF/首页替换/合并打印」用的就是这种，选中的那格是淡蓝底。

    为什么不用 ttk.Radiobutton(indicatoron=False)：aqua 主题下它会被画成
    系统按钮，Windows 上又是凸起的经典样式，两端对不上。
    """

    def __init__(self, master=None, bg=CARD, pad=0):
        super().__init__(master, bg=bg, bd=0,
                         highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=BORDER)
        self._bg = bg
        self._first = True
        self._pad = pad

    def add(self, text, command, **kw):
        if not self._first:
            tk.Frame(self, bg=BORDER, width=1, bd=0,
                     highlightthickness=0).pack(side="left", fill="y")
        self._first = False
        b = Btn(self, text=text, command=command, bg=self._bg, fg=TEXT2,
                padx=kw.pop("padx", 12), pady=kw.pop("pady", 4),
                font=kw.pop("font", font(SZ_SMALL)), **kw)
        b.pack(side="left")
        return b


def on(b):
    b.config(bg=TINT_HEAD, fg=TINT_HEAD_FG, font=font(SZ_SMALL, True))


def off(b):
    b.config(bg=CARD, fg=TEXT2, font=font(SZ_SMALL))


# ------------------------------------------------------------- 全局主题应用
def apply(root):
    """建完 root 立刻调：统一底色、字体、ttk 主题。"""
    resolve_fonts()
    root.configure(bg=BG)

    # ttk 控件（下拉框）统一用 clam —— 它是纯 Tk 绘制，两平台长得一样。
    # 不设的话 Mac 用 aqua、Windows 用 vista，下拉箭头和边框完全不同。
    try:
        st = ttk.Style(root)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=TEXT,
                     fieldbackground=CARD, bordercolor=BORDER,
                     lightcolor=CARD, darkcolor=BORDER,
                     troughcolor=BG, focuscolor=PRIMARY,
                     font=font(SZ_SMALL))
        st.configure("TCombobox", padding=3,
                     fieldbackground=CARD, background=CARD,
                     foreground=TEXT, arrowcolor=TEXT2,
                     bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD)
        st.map("TCombobox",
               fieldbackground=[("readonly", CARD)],
               foreground=[("readonly", TEXT)],
               bordercolor=[("focus", PRIMARY)],
               lightcolor=[("focus", PRIMARY)],
               darkcolor=[("focus", PRIMARY)])
        st.configure("Vertical.TScrollbar", background=CARD, troughcolor=BG,
                     bordercolor=BORDER, arrowcolor=TEXT2,
                     lightcolor=CARD, darkcolor=CARD, gripcount=0)
        st.map("Vertical.TScrollbar", background=[("active", HOVER)])
        st.configure("Horizontal.TScrollbar", background=CARD, troughcolor=BG,
                     bordercolor=BORDER, arrowcolor=TEXT2,
                     lightcolor=CARD, darkcolor=CARD, gripcount=0)
        st.map("Horizontal.TScrollbar", background=[("active", HOVER)])
    except Exception:
        pass

    # 兜底：没显式指定字体的控件（Entry / Text / Listbox / Menu）也走阶梯
    try:
        for cls, sz in (("Entry", SZ_SMALL), ("Text", SZ_SMALL),
                        ("Listbox", SZ_SMALL), ("Menu", SZ_SMALL),
                        ("Checkbutton", SZ_SMALL), ("Radiobutton", SZ_SMALL)):
            root.option_add("*%s.Font" % cls, font(sz))
    except Exception:
        pass


def sep(parent, orient="h"):
    """1px 分隔线。规范：不用 ttk.Separator（粗细不可控）。"""
    if orient == "h":
        return tk.Frame(parent, bg=BORDER, height=1, bd=0, highlightthickness=0)
    return tk.Frame(parent, bg=BORDER, width=1, bd=0, highlightthickness=0)
