# -*- coding: utf-8 -*-
"""designer_app.py —— 标签设计器主窗口（tkinter 原生，不依赖浏览器）。

四层模型（判据是「值从哪来」，不是「画在第几遍」）：
    L1 底图  印厂那张空白 PDF —— 图案都在、文字留空，整页压在最下面
    L2 图形  原设计文件里的矢量件：外框、底色、细线、图标、表格线
    L3 文字  原设计文件里的固定文字：REF / LOT / mm / 表头 / 注册信息
    L4 数据  另一份表格 Excel：规格、目录号、批号、日期、压力值、条码、二维码
    定义只有一份，在 engine/layers.py；元素自己带 layer 字段，不再靠猜。

交互约定：
    · 点层栏切层 —— 只有当前层能被选中和拖动，别的层看得到但动不了
    · 右侧三档控制显示：全部 / 本层及以下 / 只看本层
    · 拖元素改位置，松手即存盘（不用点保存）
"""
import base64
import copy
import io
import json
import glob as glob_mod
import os
import re
import sys
import tempfile
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

import json

import paths

paths.install()
paths.ensure_dirs()

import align             # noqa: E402
import barcode           # noqa: E402
import data_src          # noqa: E402
import databind          # noqa: E402
import framework         # noqa: E402
import labelkit as LK    # noqa: E402
import layers            # noqa: E402
import frame as FRAME_MOD  # noqa: E402  画布范围唯一来源（P1-6）
import placeholders as PH  # noqa: E402
import render            # noqa: E402
import refshot           # noqa: E402
import store             # noqa: E402
import table_editor      # noqa: E402  表格的独立编辑窗口（像 Excel）
import ui_theme as T     # noqa: E402  统一样式（色板 / 字号 / 自绘按钮）
import snap              # noqa: E402  拖动时的软吸附（叠影对位）
import verify as verifymod   # noqa: E402  L5 验证层：绿=对齐 / 红=没对齐
import worker            # noqa: E402  后台线程 + 进度条 + 取消（P1-1）
import verify as verifymod   # noqa: E402  L5 验证层：绿=对齐 / 红=没对齐
import worker            # noqa: E402  后台线程 + 进度条 + 取消（P1-1）

LAYERS = [(k, layers.name(k)) for k in layers.ORDER]
VIEWS = [("only", "只看本层"), ("upTo", "本层及以下"), ("all", "全部")]
# 和成品图的比对方式：正常 / 叠影
#   叠影 —— 成品图半透明垫在下面，看「结构是不是在一起」
# 以前还有一档「相减」（红蓝分色看偏到哪），实际用起来满屏红蓝反而看不出
# 手边这一块偏没偏，已经删掉；同一排的位置让给 5mm 网格开关。
# 和成品图的比对方式：正常 / 叠影 / 验证（L5）
#   叠影 —— 成品图半透明垫在下面，看「结构是不是在一起」（22% 太淡，只看大概）
#   验证 —— L5 验证层：绿=对齐 / 红=没对齐，逐像素比对，用来验收对位
# 顶栏只放「正常 / 叠影」两档。
# 「验证」(L5) 和它的「互换」不下顶栏 —— 它们是**验收工具**，
# 不是日常看图的姿势，放顶栏会天天误点。挪到左侧图层栏 L5 档下面。
CMPS = [("off", "正常"), ("ghost", "叠影")]
UI_FONT = T.UI_FONT

# 纸外留白：纸 (w,h) 四周留出来的「空桌」，按纸短边的比例算，夹在上下限之间。
# 作用就一个 —— 元素拖出纸面时还看得见、还能抓回来（见 redraw）。
# 比例从 0.06 提到 0.18：以前只够放下一行字，把整块表格推出去就只剩半截，
# 想再挪回来得凭感觉瞎摸。18% 能装下「一个元素整块 + 一点余量」，
# 纸面还占着屏幕八成宽度，不至于为了留白把纸挤小。
#
# 2026-11-20 用户要求「画布只保留白色区域那么大」→ 改成 0：
# 标签四周那一大圈灰底让人分不清"纸到哪为止"。元素坐标不受影响
# （留白从来只影响显示，不影响 mm 坐标）；真有元素拖到纸外，⌘0 适配还能找到。
PAD_RATIO = 0.0
PAD_MM_MIN = 0.0
PAD_MM_MAX = 0.55      # 动态留白的上限：按「纸短边 × 这个数」算，不再写死毫米

# 两个平台的鼠标习惯不一样，这里只分一次，后面全按它走：
#   Mac     滚轮 = 双指平移，缩放靠按钮
#   Windows 滚轮 = 没选中时缩放（选中后不动），平移靠右键拖
IS_MAC = sys.platform == "darwin"

# ⌘ / Ctrl 的 state 位：Control=0x4，Mod1(Command/Alt)=0x8。
# 两个都认 —— 哪一位在哪个平台生效不用记，多认一位不会错。
MOD_MASK = 0x0004 | 0x0008
# Alt（Windows）/ Option（Mac）：Tk 报的位不一样 ——
# X11/Windows 把 Alt 报成 Mod1(0x8)，macOS 把 Option 报成 Mod2(0x10)，
# 而 macOS 的 ⌘ 才是 Mod1。所以这一位必须按平台取，不能混。
ALT_MASK = 0x0010 if sys.platform == "darwin" else 0x0008


_WHEEL_ACC = 0.0        # 精度触摸板的小数滚轮量攒着，攒够一格再滚


def _wheel_units(ev):
    """滚轮事件 → 滚动行数（跨平台）。

    Windows 的 ev.delta 是 ±120/格，macOS 是 ±1/格。
    直接 int(delta/120) 在 Mac 上恒等于 0 —— 滚轮滚不动。
    Windows 精度触摸板还会来 ±30/±60 这种小数格，用累加器攒够一格再动。
    """
    global _WHEEL_ACC
    d = getattr(ev, "delta", 0) or 0
    if abs(d) >= 40:
        # Windows 鼠标 ±120/格，精度触摸板 ±30~±120/帧 —— 都折算成格
        _WHEEL_ACC += d / 120.0
        n = int(_WHEEL_ACC)
        _WHEEL_ACC -= n
        return n
    _WHEEL_ACC = 0.0
    return int(d) if d else 0        # macOS（已是 ±1/格）

# 界面显示字体和「量字宽用的字体」是两件事，别再混成一个：
#   · 界面显示 —— 走 T.UI_FONT（Mac 苹方 / Windows 雅黑），要的是原生观感
#   · 量字宽   —— 走 T.MEASURE_FONT，**必须**是导出时真正画字的那个族
#                （engine/render.py 的 FONT_CHAIN["hei"]），否则屏幕上量出来的
#                宽度和画出来的字对不上，选中框会偏。
# 苹方虽然最"原生"，但它装在系统 Assets 里、拿不到字体文件，PIL 画不出来，
# 所以它只能做界面字体，不能做度量字体。


def pick_ui_font() -> str:
    """界面字体定稿（实现在 ui_theme，那里还顺带定了度量字体）。"""
    global UI_FONT
    UI_FONT = T.resolve_fonts()[0]
    return UI_FONT


def mm2px(v, scale):
    return v * scale


class DesignerApp:
    def __init__(self, root: tk.Tk):
        # 此刻 root 已存在，才能枚举本机字体族 —— 界面字体在这里定稿，
        # 后面所有 widget 建的时候读的都是这个全局值。
        global UI_FONT
        UI_FONT = pick_ui_font()
        T.apply(root)              # 统一底色 / ttk 主题 / 字号阶梯
        self.root = root
        self.root.title("标签重构工具 3.0")
        self.root.geometry("1320x880")
        self.root.minsize(1080, 700)

        self.tpl = None            # 当前模板 dict
        self.tpl_name = ""
        self.layer = "L1"
        self.view_mode = "all"
        self.scale = 3.0           # 1mm = ? px（屏幕，已含 zoom）
        self.zoom = 1.0            # 视图缩放倍率（中键/滚轮控制）
        self.pan_x = 0.0           # 视图平移（像素）
        self.pan_y = 0.0
        self.mid_drag = None
        self._mid_as_click = False  # ⌃+点击在 macOS 上被报成 Button-2，记一笔转给左键逻辑
        self._last_zoom = 0.0      # 滚轮缩放节流（触控板双指会连发事件）
        self._space_down = False   # 空格按住 = 平移画布
        self.sel = None            # (kind, index) kind: "g" 图形 / "t" 文字块
        self.sel_more = []         # 多选时除主选之外的元素（Shift / ⌘ 加选）
        self.drag = None
        self.history = []          # 撤销栈（模板快照）
        self.future = []           # 重做栈
        self._grid_cur = {}        # 表格「当前格」：增删行列按它定位（id(块) → (行, 列)）
        self._table_win = None     # 表格编辑窗口（同一时刻只开一个）
        # 历史版本「只读查看」：点底下的版本标签就像点 Excel 的 sheet，
        # 看的是那一版，不动手上这一版。真正在编辑的那份寄存在 _live_tpl 里。
        self._live_tpl = None      # 只读查看时，当前编辑版寄存到这
        self._peek_fn = None       # 正在看的历史版本存档文件名（None = 在看当前版）
        self.readonly = False      # True = 只读查看模式（禁编辑、禁存盘）
        # 软吸附：拖元素到叠影附近时，轻轻往「墨迹最吻合」的位置拽一把
        self.snap_on = True        # 顶部工具条那个「吸附」开关
        self._snap_cache = {}      # (kind, idx, 模板id) → 元素遮罩（拖动时反复用）
        self._snap_ts = 0.0        # 上一次算吸附的时间（60ms 节流）
        self._snap_last = (0.0, 0.0, 0.0)   # 上一次的 (dx, dy, score)，状态栏用
        self.data = {"head": [], "recs": [], "cur": 0, "sheet": ""}
        self._imgs = {}            # PhotoImage 缓存，避免被 GC
        self._img_cache = {}       # (dataURL 哈希, 宽, 高) → PhotoImage，拖动时命中就不重算
        self._wheel_ts = 0.0       # 滚轮连续触发时，历史只记一次
        self._font_cache = {}      # (像素字号, 粗体) → tkfont.Font，量文字宽度用
        self._thumbs = {}          # 模板名 → 目录页缩略图（PhotoImage，别被 GC）
        self._thumb_key = {}       # 模板名 → (改于, 版本)，变了才重画封面
        self.show_grid = False     # 画布上是否叠 5mm 网格
        self.show_ref = True       # 是否叠参考图（只影响屏幕，不导出）
        self.cmp_mode = "ghost"    # 比对方式：off 正常 / ghost 叠影 / verify 验证(L5)
        self.verify_ref_below = True   # L5：叠影在下(红)、我们的在上(绿)。
                                       # 点「互换」翻成 False 再核一遍。
        self.dragging = False      # 正在拖（拖动时画参考线、浮动数值）
        self.drag_dx = 0.0         # 本次拖动累计位移（毫米），给浮动标签用
        self.drag_dy = 0.0
        self.drag_tag = ""         # 浮动标签显示的名字
        self._hud_tick = 0         # 拖动时降低差异分的重算频率
        self._ref_cache = {}       # 参考图解码缓存
        self._ref_raw = {}         # 参考图原图（按文件 + 修改时间）
        self._ref_aligned = {}     # 已按画布尺寸对齐好的参考图（拖动时每帧都要）

        self._build_ui()
        self.root.bind_all("<Control-z>", self._on_ctrl_z)
        self.root.bind_all("<Control-y>", self._on_ctrl_y)
        self.root.bind_all("<Command-z>", self._on_ctrl_z)   # Mac ⌘Z
        self.root.bind_all("<Command-y>", self._on_ctrl_y)   # Mac ⌘Y
        self.root.bind_all("<Delete>", self._on_delete_key)
        # ⌘V / Ctrl+V：把剪贴板里的图片贴成 L2 图形（= 「加图」，见 paste_image）
        self.root.bind_all("<Command-v>", self._on_paste)
        self.root.bind_all("<Control-v>", self._on_paste)
        # 方向键微调：0.2mm，按住 Shift 走 1mm。鼠标挪不准的最后一两毫米靠它。
        for _k in ("<Left>", "<Right>", "<Up>", "<Down>"):
            self.root.bind_all(_k, self._on_arrow)
        self.root.bind_all("<Escape>", self._on_escape)
        self.refresh_tpl_list()
        # 启动先停目录页：延迟 60ms 是为了等窗口映射完，不然 pack 顺序会乱。
        # ⚠️ 这个任务必须能取消 —— 否则它会在「已经打开某个模板之后」突然触发，
        #    把界面硬拽回目录页（脚本/AI 启动后立刻 open_tpl 就会撞上，肉眼几乎察觉不到）。
        self._first_open_job = self.root.after(60, self._first_open)

    # ────────────────────────── UI 骨架 ──────────────────────────
    def _build_ui(self):
        """窗口骨架：一条顶栏 + 三栏主体 + 版本栏 + 状态栏。

        排布照项目 1/2/3 的统一语言：
          顶栏一行管两件事 ——「我在编辑什么」（模板名 / 版本 / 撤销保存）
          和「怎么看」（查看范围 / 比对 / 网格）；四个图层不再占顶栏，
          改成画布左侧一条竖导轨（L1-L4），顶栏省下的高度全给画布。
          主体三栏靠 1px 细线分隔，不用边框、不用阴影。
        """
        self.root.configure(bg=T.BG)

        # ── 第一行：身份条 ──────────────────────────────────────
        top = tk.Frame(self.root, bg=T.CARD, height=50)
        top.pack(side="top", fill="x")
        top.pack_propagate(False)
        self.topbar = top

        tk.Label(top, text="标签重构工具", bg=T.CARD, fg=T.TEXT,
                 font=(UI_FONT, T.SZ_APP, "bold")).pack(side="left", padx=(18, 4))
        tk.Label(top, text="3.0", bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, T.SZ_FOOT)).pack(side="left", padx=(0, 12))
        tk.Frame(top, bg=T.BORDER, width=1, height=18).pack(side="left", pady=16)

        # 目录放在最左。原来的第二行工具条整条并到这一行 —— 少一行，
        # 画布就高一截（这一条比任何「放大按钮」都实在）。
        self.btn_cat = T.Btn(top, text="☰ 目录", command=self.show_catalog,
                             bg=T.CARD, fg=T.TEXT2, padx=11, pady=4)
        self.btn_cat.pack(side="left", padx=(12, 6), pady=12)
        tk.Frame(top, bg=T.BORDER, width=1, height=20).pack(side="left", pady=15)

        # 保存 / 撤销 / 重做（软件直觉：工具操作靠左）
        self.btn_save = T.Btn(top, text="保存", command=self.save_now,
                              bg=T.CARD, fg=T.TEXT, padx=16, pady=4)
        self.btn_save.pack(side="left", padx=(12, 4), pady=12)
        self.btn_undo = T.Btn(top, text="撤销", command=self.undo,
                              bg=T.CARD, fg=T.TEXT2, padx=11, pady=4)
        self.btn_undo.pack(side="left", padx=2, pady=12)
        self.btn_redo = T.Btn(top, text="重做", command=self.redo,
                              bg=T.CARD, fg=T.TEXT2, padx=11, pady=4)
        self.btn_redo.pack(side="left", padx=2, pady=12)

        # 命名（模板名 + 版本）放右上角（软件直觉：文档标识靠右）
        self.lbl_rev = tk.Label(top, text="", bg=T.CARD, fg=T.TEXT3,
                                font=(UI_FONT, T.SZ_SMALL))
        self.lbl_rev.pack(side="right", padx=(2, 18))
        self.lbl_rev.bind("<Double-Button-1>", self._edit_rev)   # 双击改版本号
        self.lbl_tpl = tk.Label(top, text="（未打开模板）", bg=T.CARD, fg=T.TEXT2,
                                font=(UI_FONT, T.SZ_BODY))
        self.lbl_tpl.pack(side="right", padx=(8, 2))

        # 查看范围 / 比对 / 网格：都在这行右半边。目录页整组藏起来
        # （编辑页才有「看哪层、和成品图怎么比」这件事）。
        self.layer_bar = tk.Frame(top, bg=T.CARD)
        self.layer_bar.pack(side="right", pady=9, padx=(0, 6))
        self.toolbar = self.layer_bar

        self.view_var = tk.StringVar(value="all")
        self.view_btns = {}
        vseg = T.Seg(self.layer_bar, bg=T.CARD)
        vseg.pack(side="right", pady=2, padx=(0, 8))
        for key, name in VIEWS:
            b = vseg.add(name, lambda k=key: self.set_view(k), padx=9)
            self.view_btns[key] = b
        self._refresh_view_btns()

        # 比对方式：正常 / 叠影 + 5mm 网格开关（都在「怎么看着核对」这一排）。
        # 图层决定「画什么」，比对决定「和成品图怎么对照着看」，网格是纯显示辅助。
        self.cmp_btns = {}
        cseg = T.Seg(self.layer_bar, bg=T.CARD)
        cseg.pack(side="right", pady=2, padx=(0, 6))
        for key, name in CMPS:
            b = cseg.add(name, lambda k=key: self.set_cmp(k), padx=9)
            self.cmp_btns[key] = b
        self.btn_grid = cseg.add("5mm 网格", self._toggle_grid_btn, padx=9)
        # 吸附：拖着元素经过叠影上的墨迹附近时，轻轻往那儿拽一把。
        # 默认开 —— 只有开着叠影才有意义，所以跟叠影是同一排。
        self.btn_snap = cseg.add("吸附", self._toggle_snap_btn, padx=9)
        self._refresh_cmp_btns()

        # ── 主体 ────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=T.BG)
        body.pack(side="top", fill="both", expand=True)
        self.body = body
        self.cat_sel = ""            # 目录页里选中的模板名

        # 一张白卡片包住整个编辑区（项目 1/2/3 都是这个结构：灰底上浮一张纸）。
        # 三栏之间的分界靠 1px 线，不靠各自的边框 —— 边框会在交界处变成 2px。
        self.shell = tk.Frame(body, bg=T.CARD, highlightthickness=1,
                              highlightbackground=T.BORDER)
        self.shell.pack(fill="both", expand=True, padx=12, pady=12)

        # 右侧属性面板
        self.right = tk.Frame(self.shell, bg=T.CARD, width=304)
        self.right.pack_propagate(False)   # 不锁宽度的话，内容一变整个面板会被撑开
        # 分隔线用 place 固定在左沿：这样 show/hide 这个面板时线跟着走，不用单独管
        tk.Frame(self.right, bg=T.BORDER, width=1).place(x=0, y=0, relheight=1)

        head = tk.Frame(self.right, bg=T.CARD, height=34)
        head.pack(fill="x", padx=(14, 10))
        head.pack_propagate(False)
        self.pnl_title = tk.Label(head, text="", bg=T.CARD, fg=T.TEXT,
                                  font=(UI_FONT, T.SZ_SMALL, "bold"), anchor="w")
        self.pnl_title.pack(side="left", pady=8)
        # 「四层」按钮：随时能把四层各是什么、各有几个、有没有标错摊开看。
        self.btn_layers = T.Btn(head, text="四层", command=self.show_layers,
                                relief="flat", bg=T.CARD, fg=T.TEXT3,
                                padx=8, pady=2, font=(UI_FONT, 8),
                                )
        self.btn_layers.pack(side="right", pady=4)
        tk.Frame(self.right, bg=T.SEP, height=1).pack(fill="x")

        # 面板内容可滚动：L4 面板很长，之前超出窗口就被截掉，按钮点不到
        _wrap = tk.Frame(self.right, bg=T.CARD)
        _wrap.pack(fill="both", expand=True)
        self.pnl_canvas = tk.Canvas(_wrap, bg=T.CARD, bd=0, highlightthickness=0)
        _vsb = ttk.Scrollbar(_wrap, orient="vertical", command=self.pnl_canvas.yview)
        self.pnl_canvas.configure(yscrollcommand=_vsb.set)
        _vsb.pack(side="right", fill="y")
        self.pnl_canvas.pack(side="left", fill="both", expand=True)

        self.pnl = tk.Frame(self.pnl_canvas, bg=T.CARD, padx=14, pady=6)
        self._pnl_win = self.pnl_canvas.create_window((0, 0), window=self.pnl, anchor="nw")
        self.pnl.bind("<Configure>", lambda e: self.pnl_canvas.configure(
            scrollregion=self.pnl_canvas.bbox("all")))
        self.pnl_canvas.bind("<Configure>", lambda e: self.pnl_canvas.itemconfigure(
            self._pnl_win, width=e.width))

        # 主区域
        self.mid = tk.Frame(self.shell, bg=T.BG)

        # ── 编辑页：一块画布，占满 ──
        # 原来左边有一列「规格」，只在 L4 出现 —— 一切到数据层画布就横着缩一截，
        # 元素跟着重排，看着像闪了一下。现在规格放进右栏（L4 面板里），
        # 画布宽度从头到尾不变。
        self.editor_page = tk.Frame(self.mid, bg=T.BG)

        # ── 画布左侧的图层导轨 ──────────────────────────────────
        # 四个层原来横在顶栏，占掉一整行高度。改成竖导轨之后，顶栏少一行，
        # 画布高度全归内容；导轨本身很窄（76px），纸是竖版的，宽度本来也够。
        self.layer_rail = tk.Frame(self.editor_page, bg=T.CARD, width=76)
        self.layer_rail.pack(side="left", fill="y")
        self.layer_rail.pack_propagate(False)
        tk.Frame(self.layer_rail, bg=T.BORDER, width=1).place(relx=1.0, x=-1, y=0,
                                                             relheight=1)
        tk.Label(self.layer_rail, text="图层", bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, T.SZ_FOOT)).pack(fill="x", pady=(10, 6))
        self.layer_btns = {}
        for key, name in LAYERS:
            b = T.Btn(self.layer_rail, text="%s\n%s" % (key, name),
                      command=lambda k=key: self.set_layer(k),
                      bg=T.CARD, fg=T.TEXT2, pady=7, padx=2,
                      font=(UI_FONT, T.SZ_SMALL))
            b.pack(fill="x", padx=8, pady=2)
            self.layer_btns[key] = b

        # ── L5 验证层的两个操作（挂在 L5 档下面，不占顶栏）──────────
        # 为什么挪到这里：验证/互换是**验收工具**，做完对位才点一次。
        # 放顶栏会和「正常/叠影」混在一起，天天误点，而且顶栏已经挤满了。
        self.verify_box = tk.Frame(self.layer_rail, bg=T.CARD)
        self.verify_box.pack(fill="x", padx=8, pady=(6, 2))
        tk.Frame(self.verify_box, bg=T.BORDER, height=1).pack(fill="x", pady=(0, 6))
        self.btn_verify = T.Btn(self.verify_box, text="验证（绿对/红偏）",
                                command=lambda: self.set_cmp("verify"),
                                bg=T.CARD, fg=T.TEXT2, pady=5, padx=2,
                                font=(UI_FONT, T.SZ_SMALL))
        self.btn_verify.pack(fill="x", pady=2)
        self.btn_vflip = T.Btn(self.verify_box, text="互换上下层复核",
                               command=self._toggle_verify_flip,
                               bg=T.CARD, fg=T.TEXT2, pady=5, padx=2,
                               font=(UI_FONT, T.SZ_SMALL))
        self.btn_vflip.pack(fill="x", pady=2)
        self.lbl_verify = tk.Label(self.verify_box, text="", bg=T.CARD, fg=T.TEXT3,
                                   font=(UI_FONT, max(9, T.SZ_SMALL - 1)),
                                   wraplength=88, justify="left")
        self.lbl_verify.pack(fill="x", pady=(4, 2))

        self.canvas = tk.Canvas(self.editor_page, bg=T.CANVAS, bd=0, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Button-1>", self.on_click)
        # 多选键只留 Ctrl / ⌘ 一个。为什么不靠 ev.state 位判断：
        # macOS 上 Command 落在哪一位随 Tk 版本变，而 Tk 的绑定是「按事件
        # 描述的精确度挑」—— 写死 <Control-Button-1> / <Command-Button-1>
        # 最稳，不受位掩码影响。
        self.canvas.bind("<Control-Button-1>", lambda e: self.on_click(e, mod=True))
        self.canvas.bind("<Command-Button-1>", lambda e: self.on_click(e, mod=True))
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", self.on_dblclick)
        # 滚轮分两种玩法（两平台不一样，别硬凑成一个）：
        #   Mac     —— 双指上下 = 平移画布；缩放只走右下角 −/＋ 按钮
        #              （触控板双指缩放手感不可靠，宁可给个看得见的按钮）
        #   Windows —— 没选中东西时滚轮 = 缩放；选中了就什么都不做
        #              （元素大小只靠拖边框和右栏数字，见 on_wheel）
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Button-4>", self.on_wheel)
        self.canvas.bind("<Button-5>", self.on_wheel)
        # 右键：拖动=平移，原地松开=弹菜单（两平台统一，见 on_right_down）
        self.canvas.bind("<Button-3>", self.on_right_down)
        self.canvas.bind("<B3-Motion>", self.on_right_drag)
        self.canvas.bind("<ButtonRelease-3>", self.on_right_up)
        # 中键只剩一个用途：macOS 把 ⌃+左键报成 Button-2，接过来转成多选。
        # 中键拖动不再平移（平移统一右键）。
        self.canvas.bind("<Button-2>", self.on_mid_down)
        self.canvas.bind("<B2-Motion>", self.on_mid_drag)
        self.canvas.bind("<ButtonRelease-2>", self.on_mid_up)
        # 空格按住 + 左键拖 = 平移（Figma/Illustrator 同款）
        self.canvas.bind("<KeyPress-space>", self._space_press)
        self.canvas.bind("<KeyRelease-space>", self._space_release)
        self.root.bind_all("<KeyPress-space>", self._space_press, add="+")
        self.root.bind_all("<KeyRelease-space>", self._space_release, add="+")

        # 浮动缩放小按钮（Mac 触控板/鼠标滚轮缩放不稳定，给个看得见的入口）
        zoom_ctl = tk.Frame(self.canvas, bg=T.CARD, highlightthickness=1,
                            highlightbackground=T.BORDER)
        self._zoom_bar = zoom_ctl
        T.Btn(zoom_ctl, text="−", command=lambda: self._zoom_step(1 / 1.25),
              bg=T.CARD, fg=T.TEXT2, padx=8, pady=2,
              font=(UI_FONT, 11, "bold")).pack(side="left", padx=(2, 0), pady=2)
        self._zoom_lbl = tk.Label(zoom_ctl, text="100%", bg=T.CARD, fg=T.TEXT3,
                                  font=(UI_FONT, 8), width=5)
        self._zoom_lbl.pack(side="left", padx=2)
        T.Btn(zoom_ctl, text="＋", command=lambda: self._zoom_step(1.25),
              bg=T.CARD, fg=T.TEXT2, padx=8, pady=2,
              font=(UI_FONT, 11, "bold")).pack(side="left", padx=(0, 2), pady=2)
        T.Btn(zoom_ctl, text="⟲", command=self.reset_view,
              bg=T.CARD, fg=T.TEXT3, padx=6, pady=2,
              font=(UI_FONT, 9)).pack(side="left", padx=(0, 2), pady=2)
        # 放在画布右下角，用 place 锚定（pack 会被画布内容挤走）
        zoom_ctl.place(relx=1.0, rely=1.0, x=-12, y=-12, anchor="se")

        # ── 目录页 ──
        self.catalog_page = tk.Frame(self.mid, bg=T.BG)
        head2 = tk.Frame(self.catalog_page, bg=T.BG)
        head2.pack(fill="x", padx=24, pady=(20, 6))
        tk.Label(head2, text="模板", bg=T.BG, fg=T.TEXT,
                 font=(UI_FONT, T.SZ_DLG, "bold")).pack(side="left")
        self.lbl_cat_n = tk.Label(head2, text="", bg=T.BG, fg=T.TEXT3,
                                  font=(UI_FONT, T.SZ_FOOT))
        self.lbl_cat_n.pack(side="left", padx=(10, 0), pady=(6, 0))
        # 右上角那组按钮挪走（2026-11-20 用户要求）：目录页最右侧单独拎出一
        # 竖栏，新建/导入/浏览图库都放那儿 —— 顶部只留标题和数量，视线干净。
        # 竖栏固定在最右，卡片区自动让位（pack side="right" 先占位）。

        # ── 右侧操作栏 ──
        self.cat_rail = tk.Frame(self.catalog_page, bg=T.CARD, width=176)
        self.cat_rail.pack(side="right", fill="y", padx=(0, 14), pady=(4, 12))
        self.cat_rail.pack_propagate(False)     # 固定宽度，不跟着内容缩
        _rail_pad = tk.Frame(self.cat_rail, bg=T.CARD)
        _rail_pad.pack(fill="both", expand=True, padx=12, pady=14)

        tk.Label(_rail_pad, text="模板", bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, T.SZ_FOOT), anchor="w").pack(fill="x")
        T.Btn(_rail_pad, text="＋ 新建模板", command=self.new_tpl, kind="primary",
              pady=7).pack(fill="x", pady=(6, 4))
        T.Btn(_rail_pad, text="⇩ 导入模板", command=self.import_tpl, bg=T.DZ_BG,
              fg=T.TEXT, pady=7).pack(fill="x", pady=(0, 14))

        tk.Frame(_rail_pad, bg=T.SEP, height=1).pack(fill="x", pady=(0, 12))
        tk.Label(_rail_pad, text="资源", bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, T.SZ_FOOT), anchor="w").pack(fill="x")
        T.Btn(_rail_pad, text="▦ 浏览标签图库", command=self.browse_library,
              bg=T.DZ_BG, fg=T.TEXT, pady=7).pack(fill="x", pady=(6, 4))
        T.Btn(_rail_pad, text="✦ 建模板技能", command=self.open_skill,
              bg=T.DZ_BG, fg=T.TEXT, pady=7).pack(fill="x", pady=(0, 6))
        tk.Label(_rail_pad,
                 text="图库是预设好的法规图标，\n建模板只能从这里取图；\n"
                      "技能是给 AI 看的搭建手册。",
                 bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, 8),
                 anchor="w", justify="left", wraplength=150
                 ).pack(fill="x", pady=(2, 0))

        wrap = tk.Frame(self.catalog_page, bg=T.BG)
        wrap.pack(side="left", fill="both", expand=True, padx=18, pady=(4, 12))
        self.card_canvas = tk.Canvas(wrap, bg=T.BG, bd=0, highlightthickness=0)
        self.cat_vsb = ttk.Scrollbar(wrap, orient="vertical",
                                     command=self.card_canvas.yview)
        self.card_canvas.configure(yscrollcommand=self.cat_vsb.set)
        self.card_canvas.pack(side="left", fill="both", expand=True)
        self.card_wrap = tk.Frame(self.card_canvas, bg=T.BG)
        self.card_canvas.create_window((0, 0), window=self.card_wrap, anchor="nw")
        self.card_wrap.bind("<Configure>", lambda e: self.card_canvas.configure(
            scrollregion=self.card_canvas.bbox("all")))
        self.card_canvas.bind("<Configure>", lambda e: self._relayout_cards())
        for _seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.card_canvas.bind(_seq, lambda e: self.card_canvas.yview_scroll(
                -_wheel_units(e), "units"))

        # 默认：编辑页
        self.mid.pack(side="left", fill="both", expand=True)
        self.right.pack(side="right", fill="y")

        # 底部：版本栏在上、状态栏在最底
        self.status = tk.Label(self.root, text="", bg=T.STATUS, fg=T.TEXT2,
                               anchor="w", font=(UI_FONT, T.SZ_SMALL), padx=14)
        self.status.pack(side="bottom", fill="x")

        self.version_bar = tk.Frame(self.root, bg=T.CARD, height=40)
        self.version_bar.pack(side="bottom", fill="x")
        self.version_bar.pack_propagate(False)
        tk.Frame(self.version_bar, bg=T.BORDER, height=1).place(relx=0, rely=1.0, y=-1,
                                                              relwidth=1)

    def _ro_block(self):
        """只读查看历史版本时，所有「改」的入口都先撞这儿。"""
        live = (self._live_tpl or {}).get("版本") or ""
        self.set_status(
            "只读查看 %s —— 这里改不了。点第一个标签回 %s 继续编辑"
            % (store.norm_rev((self.tpl or {}).get("版本") or ""),
               store.norm_rev(live) or "当前版"))
        return True

    def save_now(self):
        """手动保存（其实每步操作都会自动存，这个按钮是给人吃定心丸的）。"""
        if not self.tpl:
            return
        self.save()
        self.set_status("已保存 %s · %s" % (self.tpl_name,
                                           self.tpl.get("版本") or "rev01"))

    def _on_ctrl_z(self, ev):
        """Ctrl+Z：焦点在输入框里就交给输入框，否则撤销画布操作。"""
        if isinstance(ev.widget, (tk.Entry, tk.Text)):
            return None
        self.undo()
        return "break"

    def _on_ctrl_y(self, ev):
        if isinstance(ev.widget, (tk.Entry, tk.Text)):
            return None
        self.redo()
        return "break"

    # ────────────────────────── 撤销 ──────────────────────────
    def snapshot(self) -> str:
        """当前模板的文本快照（用于撤销）。"""
        return json.dumps(self.tpl, ensure_ascii=False, sort_keys=True)

    def _drop_snap_cache(self, why=""):
        """倒掉吸附/对位的遮罩缓存。

        ★ 2026-11-23：元素被编辑、增删、撤销、重做时**必须**调这个。
        缓存键是 (内容哈希, 叠影数据哈希, dpi)，正常情况改了内容哈希自然变，
        但有两种情况会漏：
          · 撤销/重做把 self.tpl 整个换掉 → 老缓存还挂在那儿，键却"撞得上"
            （内容被改回原样时哈希也回到原样，是最容易串的时刻）
          · 叠影数据改了但哈希计算撞车（理论上有，代价低没必要赌）
        所以这两种路径上**主动清**，不靠哈希兜底。
        """
        try:
            self._snap_cache.clear()
        except Exception:
            pass

    def push_history(self) -> None:
        """改模板之前调一次。相同快照不重复入栈，最多留 50 步。

        ★ 顺带倒遮罩缓存（2026-11-23）。
        这里是**所有改模板操作**的必经之路（增删元素、改内容、拖位置、
        改字号、改映射…），比在十几个调用点各写一次可靠得多 —— 漏一处
        就会留下一个"键撞得上、内容却已经变了"的陈旧遮罩。
        缓存键虽然带内容哈希，但 idx 移位后旧条目还占着内存，一并清掉更干净。
        """
        if not self.tpl or self.readonly:
            return
        self._drop_snap_cache("push_history")
        snap = self.snapshot()
        if self.history and self.history[-1] == snap:
            return
        self.history.append(snap)
        if len(self.history) > 50:
            self.history.pop(0)
        self.future.clear()        # 有新动作就没法重做了

    def undo(self) -> None:
        """回到上一步。"""
        if not self.tpl:
            return
        if self.readonly:
            return self._ro_block()
        if not self.history:
            self.set_status("没有可撤销的操作了")
            return
        self.future.append(self.snapshot())   # 当前状态进重做栈
        prev = self.history.pop()
        self.tpl = json.loads(prev)
        self.sel, self.sel_more = None, []
        self.drag = None
        self._drop_snap_cache("undo")     # 模板整体换掉了，老遮罩全部作废
        self.redraw()
        self.panel()
        self.save()
        self.set_status("已撤销（还剩 %d 步，可重做 %d 步）"
                        % (len(self.history), len(self.future)))

    def redo(self) -> None:
        """回退掉的那一步再走回来。"""
        if not self.tpl:
            return
        if self.readonly:
            return self._ro_block()
        if not self.future:
            self.set_status("没有可重做的操作了")
            return
        nxt = self.future.pop()
        self.history.append(self.snapshot())
        self.tpl = json.loads(nxt)
        self.sel, self.sel_more = None, []
        self.drag = None
        self._drop_snap_cache("redo")
        self.redraw()
        self.panel()
        self.save()
        self.set_status("已重做（还可重做 %d 步）" % len(self.future))

    # ────────────────────────── 规格列表（右栏，只在 L4）──────────────────────────
    def _spec_block(self):
        """L4 面板里的「规格」一段：这一版能出的所有规格，点一条换预览。

        以前这是左边独立的一列，只在数据层出现 —— 一起就切，画布跟着缩一截，
        元素位置全变，看着像闪了一下。放进右栏之后画布宽度是固定的。
        """
        recs = self.data["recs"]
        self._h("规格%s" % (("（%d）" % len(recs)) if recs else "（示例）"))
        if not recs:
            tk.Label(self.pnl, text="还没导入数据表" if not (self.tpl or {}).get("示例数据")
                     else "现在印的是示例值",
                     bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, T.SZ_FOOT),
                     anchor="w").pack(fill="x", pady=(2, 6))
            return
        head = self._spec_key_field()
        lb = tk.Listbox(self.pnl, height=min(9, len(recs)), bd=0,
                        highlightthickness=1, highlightbackground=T.BORDER,
                        font=(UI_FONT, 9), activestyle="none", selectmode="browse")
        lb.pack(fill="x", pady=(2, 6))
        for r in recs:
            lb.insert("end", " " + str(r.get(head, "")))
        if 0 <= self.data["cur"] < len(recs):
            lb.selection_set(self.data["cur"])
            lb.see(self.data["cur"])
        lb.bind("<<ListboxSelect>>", lambda e, w=lb: self._pick_spec(w))

    def _pick_spec(self, lb):
        cur = lb.curselection()
        if not cur or cur[0] == self.data["cur"]:
            return                      # 同一个规格不重复处理（防重建时递归）
        self.data["cur"] = cur[0]
        self.redraw()
        self.set_status("预览规格：%s" % lb.get(cur[0]).strip())

    # ────────────────────────── 底部版本栏（Excel 式标签页） ──────────────────────────
    def paint_version_bar(self):
        """底部一排版本标签，像 Excel 的 sheet：当前版 + 各历史版并排铺开。

        以前历史版本是一排小按钮，点下去等于「把那一版的内容载进当前」——
        想看一眼旧版长什么样，只能先把手上这版覆盖掉，看完再撤回。现在
        点历史标签是**只读查看**：画布换成那一版，手上这版原封寄存着，
        点回第一个标签就回来。右键标签可以删版本、也可以真载入。
        """
        for w in self.version_bar.winfo_children():
            w.destroy()
        if not self.tpl:
            return
        # 第一个标签永远是「手上正在编辑的那一版」的号。
        # 只读查看时 self.tpl 已经换成存档那份了，得从寄存处取 —— 否则
        # 底下会冒出两个 Rev01（当前版被替换成存档号），回去的按钮也跟着写错。
        base = (self._live_tpl or self.tpl) if self.readonly else self.tpl
        cur = store.norm_rev(base.get("版本") or "")

        tk.Label(self.version_bar, text="版本", bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, 8)).pack(side="left", padx=(12, 5))

        # Excel 里第一个 sheet 永远在最左边 —— 当前编辑版就坐这个位置
        self._rev_tab(cur, cur=True, on=(not self.readonly))

        olds = sorted(store.list_versions(self.tpl_name),
                      key=store.rev_sort_key, reverse=True)
        for fn in olds[:12]:
            ver = store.norm_rev(fn.split("_")[0])
            # 存档里可能有和当前版**同一个号**的快照（同一版号下存过两回夹）。
            # 直接并列会出现两个 Rev03，看着像程序出错 —— 后面那个标上「·旧存」。
            lab = ver if ver != cur else "%s·旧存" % ver
            self._rev_tab(lab, fn=fn, on=(self._peek_fn == fn))
        if len(olds) > 12:
            tk.Label(self.version_bar, text="…另有 %d 版" % (len(olds) - 12),
                     bg=T.CARD, fg=T.TEXT3,
                     font=(UI_FONT, 8)).pack(side="left", padx=(4, 0))

        T.Btn(self.version_bar, text="＋ 新版本", command=self.bump_version,
              relief="flat", bg=T.DZ_ON, fg=T.PRIMARY, padx=10, pady=3,
              font=(UI_FONT, 9)).pack(side="left", padx=(12, 0))

        # 只读查看时，最该看见的是「怎么回去」—— 直接给个按钮，别让人找
        if self.readonly:
            T.Btn(self.version_bar, text="← 回到 %s 继续编辑" % cur,
                  relief="flat", kind="primary", padx=10, pady=3,
                  font=(UI_FONT, 9), command=self.back_to_live
                  ).pack(side="left", padx=(10, 0))
            tk.Label(self.version_bar, text="· 只读", bg=T.CARD, fg=T.PRIMARY,
                     font=(UI_FONT, 8, "bold")).pack(side="left", padx=(6, 0))

        # 导出放在版本栏右侧：它和「这一版」是一件事，也顺手接替了
        # 原来左栏底部那两个按钮 —— 左栏整列都撤了，画布才不会被挤。
        has = bool(self.data["recs"])
        b2 = T.Btn(self.version_bar, text="全部规格 PNG + PDF",
                   command=self.export_all, relief="flat",
                   kind="primary" if has else None,
                   bg=None if has else T.DZ_BG,
                   fg=None if has else T.TEXT3, padx=12, pady=3,
                   font=(UI_FONT, 9))
        b2.pack(side="right", padx=(6, 14))
        if not has:
            b2.config(state="disabled")
        T.Btn(self.version_bar, text="本规格 PNG + PDF", command=self.export_one,
              relief="flat", bg=T.CARD, fg=T.TEXT2, padx=11, pady=3,
              font=(UI_FONT, 9)).pack(side="right", padx=2)

    def _rev_tab(self, text, cur=False, fn=None, on=False):
        """一个版本标签。`on` = 现在正看着这一版（当前版或正在看的历史版）。"""
        bg = T.PRIMARY if (cur and on) else (T.TINT_HEAD if on else T.CARD)
        fg = T.CARD if (cur and on) else (T.TINT_HEAD_FG if on else T.TEXT2)
        edge = T.PRIMARY if on else T.BORDER
        lab = tk.Label(self.version_bar, text=text, bg=bg, fg=fg,
                       font=(UI_FONT, 9, "bold" if on else "normal"),
                       padx=11, pady=3, highlightthickness=1,
                       highlightbackground=edge, cursor="hand2")
        lab.pack(side="left", padx=(0, 3), pady=5)
        lab.rev_file = fn            # 测试和菜单都靠它认出这是哪一版
        if cur:
            lab.bind("<Double-Button-1>", self._edit_rev)      # 双击改版本号
            if self.readonly:                                  # 点一下就回编辑
                lab.bind("<Button-1>", lambda e: self.back_to_live())
        else:
            lab.bind("<Button-1>", lambda e, f=fn: self.peek_version(f))
            for ev in ("<Button-2>", "<Button-3>"):
                lab.bind(ev, lambda e, f=fn: self._rev_menu(e, f))
            lab.bind("<Enter>", lambda e, w=lab, c=bg, o=on:
                     w.config(bg=c if o else T.DZ_BG))
            lab.bind("<Leave>", lambda e, w=lab, c=bg: w.config(bg=c))
        return lab

    def _rev_menu(self, ev, fn):
        """右键版本标签：查看 / 载入当前 / 删除。"""
        ver = store.norm_rev(fn.split("_")[0])
        # 同名快照可能不止一个（同一版号存过两回夹），菜单里带上存盘时间才分得清
        when = fn.rsplit("_", 2)
        tag = ver
        if len(when) == 3 and len(when[1]) == 8 and len(when[2]) >= 4:
            tag = "%s（%s-%s %s:%s）" % (ver, when[1][4:6], when[1][6:8],
                                       when[2][:2], when[2][2:4])
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="只看一眼 %s（不改当前）" % tag,
                      command=lambda: self.peek_version(fn))
        m.add_separator()
        m.add_command(label="把 %s 载入当前（覆盖，可撤销）" % tag,
                      command=lambda: self.load_archived(fn))
        m.add_separator()
        m.add_command(label="删除 %s 这个版本…" % tag,
                      command=lambda: self.delete_version(fn))
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def peek_version(self, fn):
        """只读查看一个历史版本 —— 不动手上这一版。

        这是「点开 Excel 底下另一个 sheet」：看得到、查得了，但改的是纸上
        那版的话不该被存回去，所以整段时间 `readonly=True`，`save()` 直接
        返回，交互入口全部拦下。
        """
        if not self.tpl:
            return
        fp = os.path.join(store.archive_dir(self.tpl_name), fn)
        if not os.path.exists(fp):
            messagebox.showwarning("找不到", "这个版本的存档不在了：\n%s" % fp)
            self.paint_version_bar()
            return
        if self._peek_fn == fn:
            return
        try:
            with open(fp, encoding="utf-8") as f:
                old = json.load(f)
        except Exception as e:
            messagebox.showerror("读不了存档", str(e))
            return
        if not self.readonly:              # 第一次进查看：把当前版寄存起来
            self._live_tpl = self.tpl
        old["模板名"] = self.tpl_name      # 名字跟当前走，避免错位
        self.tpl = old
        self._peek_fn = fn
        self.readonly = True
        self.sel, self.sel_more = None, []
        self.drag = None
        self.redraw()
        self.panel()
        self.paint_version_bar()
        self._update_title()
        self.set_status("只读查看 %s —— 手上这版没动；点第一个标签回去继续编辑"
                        % store.norm_rev(fn.split("_")[0]))

    def back_to_live(self):
        """从只读查看回到当前编辑版。"""
        if not self.readonly:
            return
        self._peek_fn, self.readonly = None, False
        if self._live_tpl is not None:
            self.tpl = self._live_tpl
            self._live_tpl = None
        self.sel, self.sel_more = None, []
        self.drag = None
        self.redraw()
        self.panel()
        self.paint_version_bar()
        self._update_title()
        self.set_status("回到 %s 继续编辑"
                        % store.norm_rev((self.tpl or {}).get("版本") or ""))

    def delete_version(self, fn):
        """删掉一个历史版本的存档。删之前问一声，删完页签立即消失。"""
        ver = store.norm_rev(fn.split("_")[0])
        if not messagebox.askyesno(
                "删除版本", "把 %s 的存档删掉？\n\n删了就找不回来了。" % ver,
                parent=self.root):
            return
        fp = os.path.join(store.archive_dir(self.tpl_name), fn)
        try:
            if os.path.exists(fp):
                os.remove(fp)
        except Exception as e:
            messagebox.showerror("删不掉", str(e))
            return
        if self._peek_fn == fn:            # 正在看的就是它 → 先回当前版
            self.back_to_live()
        self.paint_version_bar()
        self.set_status("已删除 %s" % ver)

    def _edit_rev(self, _ev=None):
        """双击版本号改它。前缀统一 Rev（写别的会自动归一）。"""
        if not self.tpl or self.readonly:
            return
        cur = store.norm_rev(self.tpl.get("版本") or "")
        got = simpledialog.askstring("改版本号", "版本号（前缀统一 Rev）：",
                                     initialvalue=cur, parent=self.root)
        if got is None or not got.strip():
            return
        new = store.norm_rev(got)
        self.tpl["版本"] = new
        self.save()
        self._update_title()
        self.paint_version_bar()
        self.set_status("版本号 → %s" % new)

    def load_archived(self, fn):
        """把历史版本的快照**载入当前**（会覆盖，可撤销）。

        和 `peek_version` 的区别：这个是「拿它当底稿接着改」，那个只是看一眼。
        覆盖前先退回编辑态，否则会把存档盖到存档上。
        """
        if not self.tpl:
            return
        if self.readonly:
            self.back_to_live()
        fp = os.path.join(store.archive_dir(self.tpl_name), fn)
        if not os.path.exists(fp):
            messagebox.showwarning("找不到", "这个版本的存档不在了：\n%s" % fp)
            return
        ver = fn.split("_")[0]
        if not messagebox.askyesno("载入历史版本",
                                   "把「%s」的内容载进来？\n当前编辑的内容会被替换（可以 Ctrl+Z 撤回）"
                                   % ver):
            return
        try:
            with open(fp, encoding="utf-8") as f:
                old = json.load(f)
        except Exception as e:
            messagebox.showerror("读不了存档", str(e))
            return
        self.push_history()
        old["模板名"] = self.tpl_name      # 名字跟当前走，避免错位
        self.tpl = old
        self.sel, self.sel_more = None, []
        self.save()
        self.redraw()
        self.panel()
        self.paint_version_bar()
        self._update_title()
        self.set_status("已载入 %s 的内容" % ver)

    # ────────────────────────── 目录页 / 编辑页 ──────────────────────────
    def show_catalog(self):
        """切到首页目录。"""
        # 从编辑页回目录时，只读查看状态一并清掉：目录页没有「版本」这回事，
        # 留着 readonly=True 会让下次 open_tpl 前的快捷键/拖拽全部被拦。
        self._peek_fn, self._live_tpl, self.readonly = None, None, False
        self.editor_page.pack_forget()
        self.right.pack_forget()
        self.mid.pack_forget()
        self.mid.pack(side="left", fill="both", expand=True)
        self.toolbar.pack_forget()            # 目录页不显示工具条
        self.version_bar.pack_forget()        # 也不显示版本栏
        # 撤销 / 重做 / 保存是「编辑某个模板」的操作。停在目录页时按它们
        # 只会在一个没打开的模板上打转，所以整组收起来。
        for b in (self.btn_save, self.btn_redo, self.btn_undo):
            b.pack_forget()
        self.catalog_page.pack(fill="both", expand=True)
        self.build_cards()
        self.set_status("模板目录：%d 个模板" % len(store.list_templates()))

    def _show_edit_btns(self):
        """编辑页把保存 / 撤销 / 重做放回来（左上角，从左到右）。"""
        if self.btn_save.winfo_manager():
            return
        self.btn_save.pack(side="left", padx=(12, 4), pady=12)
        self.btn_undo.pack(side="left", padx=2, pady=12)
        self.btn_redo.pack(side="left", padx=2, pady=12)

    def show_editor(self):
        """切到编辑页。"""
        self._cancel_first_open()
        self.catalog_page.pack_forget()
        self.mid.pack_forget()
        self.right.pack_forget()
        self.mid.pack(side="left", fill="both", expand=True)
        self.right.pack(side="right", fill="y")
        self.toolbar.pack(side="right", pady=9, padx=(0, 6))
        self.editor_page.pack(fill="both", expand=True)
        self.version_bar.pack(side="bottom", fill="x")
        self._show_edit_btns()
        self.redraw()
        self.panel()
        self.paint_version_bar()

    def _relayout_cards(self):
        if self.catalog_page.winfo_ismapped():
            self.build_cards()

    def build_cards(self):
        """按窗口宽度铺卡片：每张一个标签缩略图 + 名字 + 版本。"""
        for w in self.card_wrap.winfo_children():
            w.destroy()
        tpls = store.list_templates()
        self.lbl_cat_n.config(text=("%d 个" % len(tpls)) if tpls else "")
        if not tpls:
            tk.Label(self.card_wrap, text="还没有模板",
                     bg=T.BG, fg=T.TEXT3, font=(UI_FONT, 11),
                     anchor="w").grid(row=0, column=0, padx=8, pady=24, sticky="w")
            self.card_canvas.configure(scrollregion=self.card_canvas.bbox("all"))
            return
        avail = max(190, self.card_canvas.winfo_width() - 8)
        cols = max(1, avail // 200)
        for c in range(cols):
            self.card_wrap.grid_columnconfigure(c, weight=0)
        for i, t in enumerate(tpls):
            ver = t[3] if len(t) > 3 else "rev01"
            self._make_card(i, t[0], t[2], cols, ver)
        self.root.after(50, self._sync_cat_scroll)

    def _sync_cat_scroll(self):
        """内容不够一屏就不显示滚动条 —— 空着一条竖杠看着像没加载完。"""
        try:
            need = (self.card_wrap.winfo_reqheight()
                    > self.card_canvas.winfo_height() + 4)
        except Exception:
            return
        shown = bool(self.cat_vsb.winfo_manager())
        if need and not shown:
            self.cat_vsb.pack(side="right", fill="y")
        elif not need and shown:
            self.cat_vsb.pack_forget()

    def _thumb(self, name, when="", ver=""):
        """模板封面：整张标签按 40dpi 渲一遍，缩到 148 宽。按「改于+版本」缓存。"""
        key = (when, ver)
        if name in self._thumbs and self._thumb_key.get(name) == key:
            return self._thumbs[name]
        ph = None
        try:
            from PIL import Image, ImageTk
            im = render.render(store.load(name), None, 72)
            tw = 148
            th = max(1, int(round(im.height * tw / float(im.width))))
            im = im.resize((tw, th), Image.LANCZOS)
            ph = ImageTk.PhotoImage(im)
        except Exception:
            ph = None
        self._thumbs[name] = ph
        self._thumb_key[name] = key
        return ph

    def _make_card(self, i, name, when, cols, ver="rev01"):
        sel = (name == self.cat_sel)
        card = tk.Frame(self.card_wrap, bg=T.CARD, highlightthickness=1,
                        highlightbackground=T.PRIMARY if sel else T.BORDER)
        card.grid(row=i // cols, column=i % cols, padx=6, pady=6, sticky="n")

        # 封面：浅灰底把白标签衬出来；标签是竖的，所以给它固定高度
        box = tk.Frame(card, bg=T.CANVAS, height=168, width=168)
        box.pack(fill="x")
        box.pack_propagate(False)
        ph = self._thumb(name, when, ver)
        if ph is not None:
            tk.Label(box, image=ph, bg=T.CANVAS, bd=0).pack(expand=True)
        else:
            tk.Label(box, text="没有预览", bg=T.CANVAS, fg=T.DISABLED,
                     font=(UI_FONT, 8)).pack(expand=True)

        inner = tk.Frame(card, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=10, pady=(7, 8))
        tk.Label(inner, text=name, bg=T.CARD, fg=T.TEXT,
                 font=(UI_FONT, 10, "bold"), anchor="w", wraplength=148,
                 justify="left").pack(fill="x")
        meta = "%s%s" % (ver, ("　" + when) if when else "")
        tk.Label(inner, text=meta.strip(), bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, 8), anchor="w").pack(fill="x", pady=(1, 6))

        bar = tk.Frame(inner, bg=T.CARD)
        bar.pack(fill="x")
        for txt, cmd, fg in (("打开", lambda n=name: self.open_tpl(n), T.PRIMARY),
                             ("导出", lambda n=name: self.export_named(n), T.TEXT2),
                             ("改名", lambda n=name: self.rename_named(n), T.TEXT2),
                             ("删除", lambda n=name: self.del_named(n), T.TEXT3)):
            T.Btn(bar, text=txt, command=cmd, relief="flat", bg=T.CARD,
                  fg=fg, padx=5, pady=2,
                  font=(UI_FONT, 8)).pack(side="left", padx=(0, 2))
        # 单击选中，双击打开（卡片本身和它的子控件都要绑）
        for w in [card, box, inner] + list(box.winfo_children()) \
                + list(inner.winfo_children()) + list(bar.winfo_children()):
            if isinstance(w, T.Btn):
                continue
            w.bind("<Button-1>", lambda e, n=name: self.sel_card(n))
            w.bind("<Double-Button-1>", lambda e, n=name: self.open_tpl(n))

    def sel_card(self, name):
        self.cat_sel = name
        self.build_cards()

    # ────────────────────────── 模板目录 ──────────────────────────
    def refresh_tpl_list(self, keep=None):
        """模板目录变了就重画卡片（只在目录页可见时画，省无谓重绘）。"""
        self._tpls = store.list_templates()
        if keep is not None:
            self.cat_sel = keep
        if self.catalog_page.winfo_ismapped():
            self.build_cards()
        self._refresh_layer_btns()

    def _selected_name(self):
        return self.cat_sel or None

    def on_pick_tpl(self, _evt=None):
        self._refresh_layer_btns()

    def _first_open(self):
        """启动就停在目录页 —— 先挑模板，再进编辑。"""
        self._first_open_job = None
        self.show_catalog()

    def _cancel_first_open(self):
        """还没触发就取消「启动停目录页」那个定时任务（进编辑页时调用）。"""
        job = getattr(self, "_first_open_job", None)
        if not job:
            return
        self._first_open_job = None
        try:
            self.root.after_cancel(job)
        except Exception:
            pass

    def open_selected(self):
        self.open_tpl(self.cat_sel)

    def open_tpl(self, name):
        if not name:
            return
        try:
            self.tpl = store.load(name)
            # 旧模板可能没写 layer：打开时按来源补一遍（只在内存里，
            # 下次保存才落盘）。不补的话面板列不出东西、变量区也挖不准。
            layers.assign(self.tpl)
            self.tpl_name = name
            self.cat_sel = name
            # 只读查看状态三件套必须随模板一起重置。曾经漏了这一步：
            # 在 087 只读看着 Rev1 → 切到 088，readonly 还是 True、版本栏
            # 还拿 087 寄存的 _live_tpl 画号 —— 088 显示成 087 的版本，
            # 甚至「回到 Rev03」会把 087 的内容当 088 编辑/存盘。
            self._peek_fn, self._live_tpl, self.readonly = None, None, False
            self._update_title()
            self.sel, self.sel_more = None, []
            self.drag = None
            self.history = []
            self.future = []          # 重做栈同样随模板清空：留着上一个模板
                                      # 的重做步会把它“重做”进当前模板里
            self._grid_cur = {}       # 表格当前格、5mm 网格开关、吸附节流
            self.show_grid = False    # 这些都是「这一个模板」的界面状态
            self._snap_ts = 0.0
            self._snap_last = (0.0, 0.0, 0.0)
            self.zoom, self.pan_x, self.pan_y = 1.0, 0.0, 0.0
            self._sync_zoom_lbl()
            self.layer = "L1"
            self.show_ref = bool(self.tpl.get("参考图显示", True))
            self.cmp_mode = self.tpl.get("比对方式") or ("ghost" if self.show_ref else "off")
            self.snap_on = bool(self.tpl.get("吸附", True))
            self._drop_snap_cache("setting")
            self._refresh_cmp_btns()
            self.dragging = False
            self.data = {"head": [], "recs": [], "cur": 0, "sheet": ""}
            _tip = self._restore_data_source()
            self._refresh_layer_btns()
            self.show_editor()
            # 数据表接回/丢失的提示优先于「已打开」
            self.set_status(_tip or ("已打开「%s」" % name))
        except Exception as e:
            messagebox.showerror("打不开", str(e))

    def new_tpl(self):
        name = simpledialog.askstring("新建模板", "模板名：", initialvalue="新模板 1",
                                      parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in {t[0] for t in store.list_templates()}:
            messagebox.showwarning("重名", "已经有同名模板了")
            return
        tpl = store.blank_template(name)
        store.save(tpl)
        self.open_tpl(name)
        self.set_status("已新建空白模板「%s」：先在 L1 换底图" % name)

    def del_named(self, name):
        if not name:
            return
        if not messagebox.askyesno("删除模板", "删除「%s」？里面的布局和文字都会没。" % name):
            return
        store.delete(name)
        if name == self.tpl_name:
            self.tpl = None
            self.tpl_name = ""
            self.lbl_tpl.config(text="（未打开模板）")
            self.canvas.delete("all")
            self.cat_sel = ""
            self.show_catalog()
        else:
            if self.cat_sel == name:
                self.cat_sel = ""
            self.refresh_tpl_list()
        self.set_status("已删除「%s」" % name)

    def del_tpl(self):
        self.del_named(self.cat_sel or self.tpl_name)

    def rename_named(self, name):
        if not name:
            return
        new = simpledialog.askstring("改名", "新名字：", initialvalue=name, parent=self.root)
        if not new or not new.strip():
            return
        new = new.strip()
        store.rename(name, new)
        if name == self.tpl_name:
            self.tpl_name = new
            self.lbl_tpl.config(text=new)
        self.refresh_tpl_list(keep=new)

    def rename_tpl(self):
        self.rename_named(self.cat_sel or self.tpl_name)

    # ── 目录页右侧竖栏：图库 / 技能 ──────────────────────────────
    def browse_library(self):
        """浏览内置标签图库（data/标签图库/）。

        为什么要内置一份：建模板时 L2 只能从图库取图（用户定死的口径），
        图库跟着软件走就不依赖"某个人桌面上那个文件夹还在不在"。
        窗口里给出序号和文件名 —— 序号就是 dev/lib_l2.py 里 LIB_MAP 用的号。
        """
        import glob
        # 图库目录只从 paths.IMG_LIB 取，不许再写字面量（P0-3：统一一处）
        d = paths.IMG_LIB
        files = sorted(glob.glob(os.path.join(d, "序号*")))
        if not files:
            messagebox.showinfo("图库是空的",
                                "%s/ 里没有素材。\n"
                                "把「标签框架_矢量图库」整个拷进去即可。" % d)
            return

        win = tk.Toplevel(self.root)
        win.title("标签图库 · %d 个素材" % len(files))
        win.configure(bg=T.BG)
        win.geometry("860x640")

        top = tk.Frame(win, bg=T.BG)
        top.pack(fill="x", padx=16, pady=(12, 6))
        tk.Label(top, text="标签图库", bg=T.BG, fg=T.TEXT,
                 font=(UI_FONT, T.SZ_DLG, "bold")).pack(side="left")
        tk.Label(top, text="序号即 LIB_MAP 里的号；建模板只能从这里取图",
                 bg=T.BG, fg=T.TEXT3, font=(UI_FONT, T.SZ_FOOT)
                 ).pack(side="left", padx=(10, 0), pady=(4, 0))
        T.Btn(top, text="在文件管理器里打开",
              command=lambda: paths.open_path(d),   # 统一入口，见 paths.open_path
              bg=T.DZ_BG, fg=T.TEXT, padx=10, pady=3,
              font=(UI_FONT, 9)).pack(side="right")

        body = tk.Frame(win, bg=T.BG)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        cv = tk.Canvas(body, bg=T.BG, bd=0, highlightthickness=0)
        vsb = ttk.Scrollbar(body, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=vsb.set)
        cv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        inner = tk.Frame(cv, bg=T.BG)
        cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))

        self._lib_thumbs = []      # 别让 PhotoImage 被 GC
        COLS, CW, CH = 6, 130, 150
        for i, fp in enumerate(files):
            cell = tk.Frame(inner, bg=T.CARD)
            cell.grid(row=i // COLS, column=i % COLS, padx=4, pady=4)
            try:
                from PIL import Image as _Im, ImageTk as _Itk
                im = _Im.open(fp).convert("RGBA")
                bg = _Im.new("RGBA", im.size, "white")
                im = _Im.alpha_composite(bg, im).convert("RGB")
                im.thumbnail((CW - 16, CH - 46))
                ph = _Itk.PhotoImage(im)
                self._lib_thumbs.append(ph)
                tk.Label(cell, image=ph, bg=T.CARD).pack(padx=4, pady=(6, 2))
            except Exception:
                tk.Label(cell, text="(读不到)", bg=T.CARD, fg=T.TEXT3,
                         width=12, height=4).pack(padx=4, pady=(6, 2))
            num = os.path.basename(fp).split("_")[0].replace("序号", "")
            try:
                num = str(int(num))
            except Exception:
                pass
            tk.Label(cell, text="#" + num, bg=T.CARD, fg=T.PRIMARY,
                     font=(UI_FONT, 9, "bold")).pack()
            tk.Label(cell, text=os.path.basename(fp)[:18], bg=T.CARD,
                     fg=T.TEXT3, font=(UI_FONT, 7)).pack(pady=(0, 6))
        for s_ in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            cv.bind(s_, lambda e: cv.yview_scroll(
                -1 if (getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0) else 1,
                "units"))

    def open_skill(self):
        """打开技能库（data/技能/）。

        ★ 以前这里固定取第一份，只打开一篇（P2 用户指出的问题）——
        技能库有六七份文档，光打开第一份等于其余都看不见，
        而"每次学到的内容放进技能库"就成了空话（学了也没人看得到）。

        现在：弹一个**选择列表**，把文档全列出来，并显示每份多大、
        什么时候改的。**学习记录排第一**（那是新学的，最该先看）。
        """
        d = os.path.join(paths.DATA, "技能")
        os.makedirs(d, exist_ok=True)
        try:
            import learn as LN
            LN.rebuild_md()          # 顺手把 md 刷新成 jsonl 的最新版
        except Exception:
            LN = None
        docs = sorted(glob_mod.glob(os.path.join(d, "*.md")))
        if not docs:
            messagebox.showinfo("还没有技能文件",
                                "把搭建手册放进 data/技能/ 即可。")
            return
        # 学习记录排最前（它是"新学的"，其余是"一开始就写好的"）
        docs.sort(key=lambda p: (0 if "学习记录" in os.path.basename(p) else 1,
                                 os.path.basename(p)))

        win = tk.Toplevel(self.root)
        win.title("技能库 · %d 份文档" % len(docs))
        win.transient(self.root)
        win.geometry("+%d+%d" % (self.root.winfo_rootx() + 120,
                                 self.root.winfo_rooty() + 100))
        tk.Label(win, text="选一份打开（学习记录在最上面）",
                 font=(UI_FONT, 10), anchor="w").pack(fill="x", padx=12, pady=(10, 4))
        n_learn = 0
        if LN is not None:
            try:
                n_learn = LN.stats().get("总数", 0)
            except Exception:
                n_learn = 0
        body = tk.Frame(win)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        for fp in docs:
            base = os.path.basename(fp)
            try:
                sz = os.path.getsize(fp)
                mt = time.strftime("%m-%d %H:%M",
                                   time.localtime(os.path.getmtime(fp)))
            except OSError:
                sz, mt = 0, ""
            sub = "%s  ·  %.1f KB" % (mt, sz / 1024.0)
            if "学习记录" in base and n_learn:
                sub += "  ·  %d 条纠正" % n_learn
            row = tk.Frame(body)
            row.pack(fill="x", pady=1)
            T.Btn(row, text=base, relief="flat", bg=T.DZ_BG, anchor="w",
                  padx=8, pady=4,
                  command=lambda p2=fp: self._open_one_skill(p2, win)
                  ).pack(side="left", fill="x", expand=True)
            tk.Label(row, text=sub, fg=T.TEXT3, font=(UI_FONT, 8),
                     width=22, anchor="w").pack(side="left", padx=(6, 0))
        foot = tk.Frame(win)
        foot.pack(fill="x", padx=12, pady=(0, 10))
        T.Btn(foot, text="打开整个文件夹", relief="flat", bg=T.DZ_BG, pady=4,
              command=lambda: paths.open_path(d)).pack(side="left")
        T.Btn(foot, text="关闭", relief="flat", bg=T.DZ_BG, pady=4,
              command=win.destroy).pack(side="right")

    def _open_one_skill(self, fp, win=None):
        """打开技能库里的一份文档。"""
        if paths.open_path(fp):
            self.set_status("已打开技能：%s" % os.path.basename(fp))
            if win is not None:
                try:
                    win.destroy()
                except tk.TclError:
                    pass
        else:
            messagebox.showinfo("打不开",
                                "%s\n\n请手动打开。" % fp)

    def import_tpl(self):
        d = filedialog.askdirectory(title="选一个模板文件夹（整个文件夹会收进软件）")
        if not d:
            return
        try:
            name = store.import_folder(d)
            self.refresh_tpl_list(keep=name)
            self.set_status("已把文件夹收进软件「%s」（模板/%s/），双击卡片打开"
                            % (name, store._safe(name)))
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    def export_named(self, name):
        if not name:
            return
        d = filedialog.askdirectory(title="选一个文件夹放导出包")
        if not d:
            return
        try:
            folder = store.export_folder(name, d)
            self.set_status("已导出到 %s" % folder)
            messagebox.showinfo("导出完成",
                                "自包含文件夹已创建：\n%s\n\n"
                                "拷到空电脑上用「导入模板」选这个文件夹即可。" % folder)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def export_tpl(self):
        self.export_named(self.tpl_name or self.cat_sel)

    # ────────────────────────── 层与显示 ──────────────────────────
    def _refresh_layer_btns(self):
        """层按钮：有选中就标选中元素所在的层，跨层多选都标蓝。

        「本层及以下 / 全部」下 Shift 加选、框选可以跨层 —— 这时顶部层级
        按钮是**展示**（选中的东西在哪几层），不是筛选。没选中才显示当前层。
        """
        try:
            sel_layers = {self._key_layer(k) for k in self._sel_list()} if self.tpl else set()
        except Exception:
            sel_layers = set()
        for key, _ in LAYERS:
            b = self.layer_btns[key]
            on = (key in sel_layers) if sel_layers else (key == self.layer)
            (T.on if on else T.off)(b)

    def set_layer(self, key):
        self.layer = key
        self.sel, self.sel_more = None, []
        self._refresh_layer_btns()
        self.redraw()
        self.panel()

    def set_view(self, key):
        self.view_var.set(key)
        self._refresh_view_btns()
        self.on_view_change()

    def set_cmp(self, key):
        """切换比对方式（正常 / 叠影）。"""
        self.cmp_mode = key
        self.show_ref = key != "off"
        if self.tpl is not None:
            self.tpl["参考图显示"] = self.show_ref
            self.tpl["比对方式"] = key
        self._refresh_cmp_btns()
        self.redraw()
        self.panel()

    def _toggle_verify_flip(self):
        """L5 验证层：一键上下层互换。

        默认「叠影在下(红) / 我们的在上(绿)」；互换后「叠影在上(绿) / 我们的在下(红)」。
        两次都趋近全绿才说明真对齐 —— 只测一次，整体偏糊的那边可能蒙混过关。
        """
        self.verify_ref_below = not getattr(self, "verify_ref_below", True)
        if self.cmp_mode != "verify":
            self.set_cmp("verify")
            return
        self.redraw()
        who = "叠影在上(绿) / 我们的在下(红)" if not self.verify_ref_below \
            else "叠影在下(红) / 我们的在上(绿)"
        self._refresh_cmp_btns()
        self.set_status("L5 验证：%s" % who)

    def _refresh_cmp_btns(self):
        cur = getattr(self, "cmp_mode", "ghost")
        for k, b in self.cmp_btns.items():
            (T.on if k == cur else T.off)(b)
        if getattr(self, "btn_grid", None) is not None:
            (T.on if self.show_grid else T.off)(self.btn_grid)
        if getattr(self, "btn_snap", None) is not None:
            (T.on if self.snap_on else T.off)(self.btn_snap)
        # L5 的两个按钮在左侧图层栏（不在顶栏）：按当前模式高亮
        if getattr(self, "btn_verify", None) is not None:
            (T.on if cur == "verify" else T.off)(self.btn_verify)
        if getattr(self, "btn_vflip", None) is not None:
            on = (cur == "verify") and not getattr(self, "verify_ref_below", True)
            (T.on if on else T.off)(self.btn_vflip)

    def _toggle_snap_btn(self):
        """开/关吸附。关了之后拖动就是纯手放，一点力都不加。"""
        self.snap_on = not self.snap_on
        if self.tpl is not None:
            self.tpl["吸附"] = self.snap_on
            self.save()
        self._drop_snap_cache("snap_off")   # 关的时候顺手把遮罩缓存倒掉
        self._refresh_cmp_btns()
        if not self.snap_on:
            self.set_status("吸附已关：拖动完全不施加引力")
        elif not (self.tpl or {}).get("参考图"):
            self.set_status("吸附已开 —— 但这模板还没设叠影，吸不了任何东西")
        else:
            self.set_status("吸附已开：拖到叠影墨迹 3mm 内会轻轻往那儿靠")

    def _refresh_view_btns(self):
        cur = self.view_var.get()
        for k, b in self.view_btns.items():
            (T.on if k == cur else T.off)(b)

    def on_view_change(self):
        self.view_mode = self.view_var.get()
        self.redraw()

    @staticmethod
    def _group_name(grp) -> str:
        """组的显示名。模板里这个字段叫「名」，以前只读「名称」，
        结果界面上永远显示默认的「四宫格」，看着像个来路不明的功能。"""
        return (grp or {}).get("名称") or (grp or {}).get("名") or "四格区"

    def _visible(self, layer):
        """这一层现在该不该画。"""
        if self.view_mode == "all":
            return True
        idx = [k for k, _ in LAYERS].index(self.layer)
        cur = [k for k, _ in LAYERS].index(layer)
        return cur <= idx if self.view_mode == "upTo" else cur == idx

    @staticmethod
    def _glayer(g) -> str:
        """图形属于哪一层 —— 直接读元素自己的 `layer`（engine/layers.py）。

        以前这里按类型猜（条码 → L4、顺应性卡 → L4），猜的结果和模板里
        实际存的东西可能对不上：面板说它在数据层，变量区却按图形算。
        现在层是构建模板时写死的字段，界面只读不猜。
        """
        return layers.of(g, "g")

    def _glayer_idx(self, want=None):
        """图形索引列表（want=None 表示全部）。面板列表和命中测试共用一份顺序。"""
        out = []
        for i, g in enumerate(self.tpl.get("图形") or []):
            if want is None or self._glayer(g) == want:
                out.append(i)
        return out

    # ────────────────────────── 画布 ──────────────────────────
    def _canvas_size(self):
        w = max(120, self.canvas.winfo_width() - 40)
        h = max(120, self.canvas.winfo_height() - 40)
        return w, h

    def _union_mm(self):
        """纸面 + 所有元素的联合范围 (x0, y0, x1, y1)，单位毫米，可能为负。

        用元素自己的声明框估（x/y/w/h），不去精确量字 —— 每帧都要算，
        量字太慢；这里只是决定「留白留多宽」，差个半毫米无所谓。
        """
        w_mm = float(self.tpl["画布"]["宽"]) if self.tpl else 200.0
        h_mm = float(self.tpl["画布"]["高"]) if self.tpl else 300.0
        x0, y0, x1, y1 = 0.0, 0.0, w_mm, h_mm
        if not self.tpl:
            return x0, y0, x1, y1

        def add(x, y, w, h):
            return (min(x0, x), min(y0, y), max(x1, x + w), max(y1, y + h))

        try:
            for g in self.tpl.get("图形") or []:
                x0, y0, x1, y1 = add(float(g.get("x") or 0), float(g.get("y") or 0),
                                     float(g.get("w") or 0), float(g.get("h") or 0))
            for b in self.tpl.get("文字") or []:
                if b.get("type") == "table":
                    bw = sum(float(c) for c in (b.get("cols") or [0]))
                    bh = sum(float(r) for r in (b.get("rows") or [0]))
                else:
                    bw = float(b.get("w") or 0) or 12.0
                    bh = float(b.get("h") or 0) or 4.0
                x0, y0, x1, y1 = add(float(b.get("x") or 0), float(b.get("y") or 0),
                                     bw, bh)
                for ln in b.get("lines") or []:
                    x0, y0, x1, y1 = add(float(ln.get("x") or 0),
                                         float(ln.get("y") or 0),
                                         bw, float(ln.get("sz") or 3) * 1.4)
            for gi, grp in enumerate(self.tpl.get("组") or []):
                bb = self._bbox_of("gr", gi)
                if bb:
                    x0, y0, x1, y1 = add(*bb)
        except (TypeError, ValueError):
            pass
        return x0, y0, x1, y1

    def redraw(self):
        c = self.canvas
        c.delete("all")
        self._refresh_layer_btns()   # 选中变化时层按钮跟着亮（跨层多选都标蓝）
        if not self.tpl:
            c.create_text(20, 20, anchor="nw", fill=T.TEXT3, font=(UI_FONT, 11),
                          text="左边选一个模板，或点「＋ 新建」从白纸开始")
            return
        w_mm = float(self.tpl["画布"]["宽"])
        h_mm = float(self.tpl["画布"]["高"])
        aw, ah = self._canvas_size()
        # 纸外留白：纸面四周留出的一圈空桌（毫米）。要够放下被临时挪出去的字，
        # 又不能大到把纸挤小 —— 取纸短边的 12%，夹在 8~30mm 之间。
        # 关键：算缩放时把这圈留白一起算进「要装下的尺寸」，纸和留白才都完整可见。
        short = min(w_mm, h_mm)
        base_pad = max(PAD_MM_MIN, PAD_RATIO * short)
        # 纸外留白**四边分开算**：哪边有东西被推出去，哪边就长。
        # 以前四边一样宽，于是「把表格推到右边」和「推到左边」留出来的
        # 空一样多 —— 纸被无谓地缩小一半，推出去的那块还是可能出屏。
        ux0, uy0, ux1, uy1 = self._union_mm()
        gap = 8.0                                   # 元素外面还留一圈呼吸
        cap = short * PAD_MM_MAX
        if PAD_RATIO <= 0:
            # 留白关掉了：四边都不留，纸面铺满（见顶部 PAD_RATIO 注释）
            pl = pt = pr = pb = 0.0
        else:
            pl = min(cap, max(base_pad, -ux0 + gap))
            pt = min(cap, max(base_pad, -uy0 + gap))
            pr = min(cap, max(base_pad, ux1 - w_mm + gap))
            pb = min(cap, max(base_pad, uy1 - h_mm + gap))
        # 量化到 4mm 档，拖动时画面才不会一格一格地跳
        pl, pt, pr, pb = (round(v / 4.0) * 4.0 for v in (pl, pt, pr, pb))
        base = min(aw / (w_mm + pl + pr), ah / (h_mm + pt + pb))
        base = max(0.2, base)
        self.scale = base * self.zoom
        W = w_mm * self.scale
        H = h_mm * self.scale
        # 整块（纸 + 留白）居中，纸在里面的位置由 pl/pt 决定
        TW = W + (pl + pr) * self.scale
        TH = H + (pt + pb) * self.scale
        ox = (c.winfo_width() - TW) / 2 + pl * self.scale + self.pan_x
        oy = (c.winfo_height() - TH) / 2 + pt * self.scale + self.pan_y
        self.ox, self.oy = ox, oy

        pad_px = (pl * self.scale, pt * self.scale, pr * self.scale, pb * self.scale)
        self._pad_mm, self._pad_px = (pl, pt, pr, pb), pad_px

        # 画布内容：交给引擎画一遍，和导出用的是同一份像素。
        # 以前这里有一套平行的 Tk 绘图代码，和 engine/render.py 各画各的，
        # 改一处忘一处就会出现「预览好好的、导出不一样」，所以整段删掉了。
        ph = self._preview_photo(W, H, (pl, pt, pr, pb), pad_px)
        if ph is not None:
            c.create_image(ox - pad_px[0], oy - pad_px[1], image=ph, anchor="nw",
                           tags="preview")
        # 纸边线要压在预览图上：留白和纸面都是白的，没有这条线分不清纸在哪。
        c.create_rectangle(ox, oy, ox + W, oy + H, outline=T.BORDER,
                           width=1, tags="pageedge")

        # 5mm 网格（对齐辅助，不影响导出）。
        # 必须在预览图之后画 —— 以前画在 create_image 前面，被那张不透明的
        # 位图整片盖住，开关点了什么也看不见。
        if self.show_grid:
            step = 5 * self.scale
            gx = ox
            while gx <= ox + W + 0.5:
                c.create_line(gx, oy, gx, oy + H, fill=T.GRID, tags="grid")
                gx += step
            gy = oy
            while gy <= oy + H + 0.5:
                c.create_line(ox, gy, ox + W, gy, fill=T.GRID, tags="grid")
                gy += step

        # 选中框
        if self.sel:
            self._draw_sel()

        # 拖动中：参考线 + 浮动读数（必须在选中框之后画，压在最上层）
        if self.dragging and self.drag:
            self._draw_drag_aids()

    def _to_photo(self, im):
        from PIL import ImageTk
        ph = ImageTk.PhotoImage(im)
        # 拖动时每秒建几十张，留太多会一直涨内存；留最近几张就够
        # （画布上正在显示的那张由 canvas 自己持引用，不会被这里释放）。
        self._imgs[id(ph)] = ph
        if len(self._imgs) > 6:
            for k in list(self._imgs)[:-6]:
                self._imgs.pop(k, None)
        return ph

    def _photo_for(self, data_url: str, w: int, h: int):
        """按 (图片, 宽, 高) 缓存 PhotoImage。

        拖动时尺寸不变，命中缓存就不用再 b64 解码 + 缩放 —— 这是之前卡的主因。
        """
        w, h = max(1, int(w)), max(1, int(h))
        key = (hash(data_url), w, h)
        ph = self._img_cache.get(key)
        if ph is None:
            im = render.b64_to_image(data_url).resize((w, h), render.Image.LANCZOS)
            ph = self._to_photo(im)
            if len(self._img_cache) > 80:
                self._img_cache.clear()
            self._img_cache[key] = ph
        return ph

    def _display_tpl(self):
        """按当前图层可见性，给出一份「只用来画」的模板副本。

        不改 self.tpl —— 图层开关只影响看，不影响存。
        """
        tpl = dict(self.tpl)
        if not self._visible("L1"):
            tpl["底图"] = None
        # 图形也按层过滤：条码 / 二维码 / 顺应性卡算数据层，只看 L4 时它们该在，
        # 只看 L2 时它们不该在 —— 不然「数据层」和「图形层」的边界是假的。
        tpl["图形"] = [g for g in self.tpl.get("图形") or []
                       if self._visible(self._glayer(g))]
        # 四宫格里一个组同时含 L2 骨架 / L3 固定字 / L4 数据，不能整组
        # 按 L2 开关 —— 那样「只看数据层」会把四格里的批号、条码一起藏掉。
        # 这里按元素自己的层过滤，组只保留还看得见的那几个元素。
        groups = []
        for grp in self.tpl.get("组") or []:
            elems = [e for e in grp.get("元素") or []
                     if self._visible(layers.of(e, "ge"))]
            if elems:
                gg = dict(grp)
                gg["元素"] = elems
                groups.append(gg)
        tpl["组"] = groups
        texts = []
        for b in self.tpl.get("文字") or []:
            if self._visible(b.get("layer", "L3")):
                texts.append(b)
        tpl["文字"] = texts
        return tpl

    def _preview_photo(self, W, H, pad_mm=0.0, pad_px=0):
        """把当前模板画成一张位图（缓存）。尺寸或内容没变就直接复用。

        画的是「纸面 + 四周留白」：纸上和纸外一起出图，拖到纸外的字才不会
        凭空消失。缓存签名必须带上留白尺寸 —— 否则缩放后边距变了、
        画面还拿旧的那张，纸边会一跳一跳。
        """
        W, H = max(1, int(W)), max(1, int(H))
        # pad 可能是单个数字，也可能是 (左, 上, 右, 下) —— 统一成四元组
        if isinstance(pad_px, (tuple, list)):
            plp, ptp, prp, pbp = (max(0, int(round(v))) for v in pad_px)
        else:
            plp = ptp = prp = pbp = max(0, int(round(pad_px)))
        if not isinstance(pad_mm, (tuple, list)):
            pad_mm = (float(pad_mm),) * 4
        TW, TH = W + plp + prp, H + ptp + pbp
        focus = self._focus_px()
        try:
            # 签名必须把「当前这条数据」也算进去：只签模板的话，切规格
            # （L4 里换一行）画面不会重绘，看着像数据没生效。
            # ★ verify_ref_below 也要进签名：否则点「互换」画面不重绘
            #   （缓存命中就返回旧图，看着像按钮没反应）
            sig = (TW, TH, (plp, ptp, prp, pbp), self.cmp_mode,
                   getattr(self, "verify_ref_below", True), focus,
                   self.data.get("cur", 0),
                   len(self.data.get("recs") or []),
                   json.dumps(self._fields(), ensure_ascii=False,
                              sort_keys=True, default=str),
                   json.dumps(self._display_tpl(), ensure_ascii=False,
                              sort_keys=True, default=str))
        except Exception:
            sig = None
        if sig is not None and sig == getattr(self, "_prev_sig", None):
            return getattr(self, "_prev_ph", None)
        try:
            w_mm = float(self.tpl["画布"]["宽"])
            full_mm = w_mm + float(pad_mm[0]) + float(pad_mm[2])
            dpi = max(24.0, TW / full_mm * 25.4)
            im = render.render_workspace(self._display_tpl(), self._fields(), dpi,
                                         pad_mm=pad_mm, bg=T.CANVAS)
            if im.size != (TW, TH):
                im = im.resize((TW, TH), render.Image.LANCZOS)
        except Exception:
            return None
        # 纸面那一块（不含留白）—— 叠参考图、算差异分都按纸面坐标来
        page = im.crop((plp, ptp, plp + W, ptp + H))
        self._base_im = page      # 叠参考图之前的纯矢量渲染，算差异分要用
        if self.cmp_mode != "off" and self.tpl.get("参考图"):
            page = self._compare(page)
            im = im.copy()
            im.paste(page, (plp, ptp))
        elif self.dragging:
            im = self._tint(im)
        ph = self._to_photo(im)
        self._prev_sig, self._prev_ph = sig, ph
        self._prev_im = im          # 拖动时从这里裁「被抬起的那一块」
        return ph

    def _tk_font(self, px: int, bold: bool):
        key = (px, bold)
        f = self._font_cache.get(key)
        if f is None:
            # size 传负数才是「像素」；传正数 Tk 当磅值，会放大 1.33 倍（96dpi/72）
            # 字体用 MEASURE_FONT：量出来的宽度必须等于导出时画出来的宽度
            f = tkfont.Font(family=T.MEASURE_FONT, size=-px,
                            weight="bold" if bold else "normal")
            self._font_cache[key] = f
        return f

    REF_PX = 100      # 参考字号：固定用 100px 量，再按比例换算

    def _text_width_mm(self, text: str, sz: float, bold: bool = False) -> float:
        """一行文字的毫米宽度 —— 用 Tk 字体实测。

        为什么固定 100px 量：小字号（几 px）下字体宽度和字号不成线性
        （有 hinting 和最小宽度），直接用屏幕字号量会算大 30% 以上。
        100px 下线性很好，量完按 sz/100 换算即可。

        Args:
            text: 文字（已替换过占位符）
            sz: 字号（毫米）
            bold: 是否粗体
        Returns:
            宽度（毫米）
        """
        if not text:
            return 0.0
        try:
            return self._tk_font(self.REF_PX, bold).measure(text) * sz / self.REF_PX
        except Exception:
            return sum(sz * (1.0 if ord(c) > 0x2E80 else 0.55) for c in text)

    def _focus_px(self):
        """拖动时只对这一块（当前选中元素 + 余量）做差异聚焦，其余保持原样。

        返回的是**画布图内部**的像素坐标（不含 ox/oy）——
        预览图就是从画布左上角开始画的，加了偏移就圈错地方。
        """
        if not self.dragging:
            return None
        bb = self._drag_bbox()
        if not bb:
            return None
        x, y, w, h = bb
        pad = 12.0                       # 留一圈余量，看得出「往哪边挪」
        W = float(self.tpl["画布"]["宽"]) * self.scale
        H = float(self.tpl["画布"]["高"]) * self.scale
        return (max(0, int(round((x - pad) * self.scale))),
                max(0, int(round((y - pad) * self.scale))),
                min(int(round(W)), int(round((x + w + pad) * self.scale))),
                min(int(round(H)), int(round((y + h + pad) * self.scale))))

    def _tint(self, im):
        """拖动时给选中块铺一层很淡的蓝底 —— 让人一眼锁定「正在动的是这一块」。

        Tk 的 canvas 没有透明色，所以染色放在 PIL 这边做，
        和导出用的那张图是同一条链路，不会串到导出里。
        """
        box = self._focus_px()
        if not box:
            return im
        # _focus_px 给的是纸面坐标，预览图左上角在纸外留白之外 —— 补上偏移
        pk = getattr(self, "_pad_px", 0) or 0
        if isinstance(pk, (tuple, list)):
            plp, ptp = int(round(pk[0])), int(round(pk[1]))
        else:
            plp = ptp = int(round(pk))
        x0, y0, x1, y1 = box[0] + plp, box[1] + ptp, box[2] + plp, box[3] + ptp
        x1, y1 = min(x1, im.width), min(y1, im.height)
        x0, y0 = max(0, x0), max(0, y0)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return im
        reg = im.crop((x0, y0, x1, y1))
        reg = render.Image.blend(reg, render.Image.new("RGB", reg.size, "#4a86ff"), 0.20)
        im = im.copy()
        im.paste(reg, (x0, y0))
        return im

    def _ref_image(self, size):
        """参考图按当前画布尺寸对齐好的一张图（带缓存）。

        拖动时每帧都要这张图，而它跟画布尺寸、对齐偏移都不变 ——
        每帧重算一次 resize/overlap 要多花十几毫秒，正好是「拖起来一顿」的来源。
        """
        src = (self.tpl or {}).get("参考图") or ""
        if not src:
            return None
        fp = store.resolve_ref(self.tpl_name, src) if not os.path.isabs(src) else src
        if not fp or not os.path.exists(fp):
            return None
        mtime = os.path.getmtime(fp)
        dx = float(self.tpl.get("参考图X") or 0.0)
        dy = float(self.tpl.get("参考图Y") or 0.0)
        out_key = (fp, mtime, round(dx, 3), round(dy, 3), size)
        hit = self._ref_aligned.get(out_key)
        if hit is not None:
            return hit

        key = (fp, mtime)
        raw = self._ref_raw.get(key)
        if raw is None:
            try:
                raw = render.Image.open(fp).convert("RGB")
            except Exception:
                return None
            if len(self._ref_raw) > 3:
                self._ref_raw.clear()
            self._ref_raw[key] = raw
        # ★ 有「图框」记录时，参考图要按**黑框**取，再贴到新画布上（P1-6）
        #   用户口径：原点 = 黑框外沿左上角；白边向外各 margin 毫米。
        #   参考图还是那张整图（不重裁），但取用时要：
        #     ① 把整图里「框外沿 → 框右下」那部分裁出来
        #     ② 缩放到「框内尺寸」
        #     ③ 贴到新画布上 (margin, margin) 处
        #   这样画布最外面一圈 margin 是白的（纸外空白），
        #   元素坐标也刚好落在框内 —— 两边坐标系一致。
        fr = self.tpl.get("图框")
        if fr and fr.get("框像素"):
            try:
                bx0, by0, bx1, by1 = [int(v) for v in fr["框像素"]]
                m = float(fr.get("白边mm") or 0.0)
                k = size[0] / float(self.tpl["画布"]["宽"])   # 新画布 px/mm
                box = raw.crop((bx0, by0, bx1 + 1, by1 + 1))
                bw = max(2, int(round((float(self.tpl["画布"]["宽"]) - 2 * m) * k)))
                bh = max(2, int(round((float(self.tpl["画布"]["高"]) - 2 * m) * k)))
                box = box.resize((bw, bh), render.Image.LANCZOS)
                out = render.Image.new("RGB", size, "white")
                out.paste(box, (int(round(m * k)), int(round(m * k))))
                if len(self._ref_aligned) > 6:
                    self._ref_aligned.clear()
                self._ref_aligned[out_key] = out
                return out
            except Exception:
                pass            # 图框记录坏了就退回老路，不要让预览崩
        if dx or dy:
            out = align.overlap(raw, dx, dy, float(self.tpl["画布"]["宽"]), size)
        else:
            out = raw.resize(size, render.Image.LANCZOS)
        if len(self._ref_aligned) > 6:
            self._ref_aligned.clear()
        self._ref_aligned[out_key] = out
        return out

    def _compare(self, im):
        """把当前画面和成品图摆在一起看：叠影（半透明）或相减（分色）。"""
        ref = self._ref_image(im.size)
        if ref is None:
            return im
        if self.cmp_mode == "diff":
            holes = self._var_holes(im.size)
            # 块级相减：按 2mm 网格比墨量，不是逐像素比。
            # 逐像素会被「字形本身不同」淹没 —— 这张标签的字标是位图、
            # 参考图又是 200dpi 扫描，满屏红蓝反而看不出哪里真的偏了。
            # 变量区由 diff_cells 自己涂成浅黄。
            d = align.diff_cells(im, ref, holes=holes,
                                 mm_w=float(self.tpl["画布"]["宽"]))
            # 拖动时只看手边这一块：整张图的固有偏差（字形、条码分段）会
            # 铺满红蓝，把「我刚挪的这一下变好还是变坏」淹掉。
            # 窗口外保持正常画面 —— 既不被红蓝吵到，也还能看见周围的参照物。
            box = self._focus_px()
            if box:
                x0, y0 = max(0, box[0]), max(0, box[1])
                x1, y1 = min(d.width, box[2]), min(d.height, box[3])
                if x1 - x0 > 2 and y1 - y0 > 2:
                    out = im.copy()
                    out.paste(d.crop((x0, y0, x1, y1)), (x0, y0))
                    return out
            return d
        if self.cmp_mode == "verify":
            # L5 验证层：绿=对齐 / 红=没对齐（只比黑色内容，彩色块不算）
            # ★ 排除「不参与对齐」的区（2026-11-23 用户定稿）
            #   对齐的评判对象只有两样：**文字 + 矢量图库件**。
            #   条码不参与 —— 它是数据生成的码，不是矢量件；P0-3 也定过
            #   「L2 只能取自图库」，模板里本来就不该有它。把它算进分母
            #   只会平白拉低对齐率，还盖住「文字到底有没有对上」这个真问题。
            #   位置由模板的 "不参与对齐" 给出（建模板时一次定下）。
            _skip = []
            for _bx in (self.tpl.get("不参与对齐") or []):
                try:
                    _skip.append((float(_bx["x0"]), float(_bx["y0"]),
                                  float(_bx["x1"]), float(_bx["y1"])))
                except (KeyError, TypeError, ValueError):
                    continue
            out, st = verifymod.compare(
                im, ref,
                ref_below=getattr(self, "verify_ref_below", True),
                skip_boxes=_skip,
                canvas_w_mm=float(self.tpl["画布"]["宽"]),
                tpl=self.tpl)      # 画布范围由 canvas_rect 统一给（P1-6）
            self._verify_stats = st
            # 状态栏直接报对齐率，肉眼之外给个数字
            try:
                _where = "叠影在下" if st["ref_below"] else "叠影在上"
                self.set_status("L5 验证 · %s · 对齐 %.1f%%" % (_where, st["align"] * 100))
                if getattr(self, "lbl_verify", None) is not None:
                    self.lbl_verify.config(
                        text="%s\n对齐 %.1f%%\n红 %d px"
                             % (_where, st["align"] * 100, st["red_px"]))
            except Exception:
                pass
            return out
        a = float(self.tpl.get("参考图不透明度") or 0.22)
        return render.Image.blend(im.convert("RGB"), ref, max(0.0, min(1.0, a)))

    # ── 变量区：每规格都会变的那些格子 ──────────────────────────────
    def _var_boxes(self):
        """当前模板里「每规格都不一样」的区域（毫米），带缓存。

        条码、二维码、批号、有效期、9 档压力值、四宫格上半……
        参考图只有某一个规格，拿它去比这些地方永远是红的，所以要挖掉。
        """
        if not self.tpl:
            return []
        try:
            sig = hash(json.dumps(self.tpl, ensure_ascii=False, sort_keys=True))
        except Exception:
            sig = None
        if sig is not None and sig == getattr(self, "_vb_sig", None):
            return getattr(self, "_vb", [])
        try:
            vb = databind.variable_boxes(
                self.tpl, lambda kind, idx: self._bbox_of(kind, idx))
        except Exception:
            vb = []
        self._vb_sig, self._vb = sig, vb
        return vb

    def _var_holes(self, size):
        """变量区换成像素矩形，给 align.score / diff_cells 挖洞用。"""
        vb = self._var_boxes()
        if not vb or not self.tpl:
            return []
        k = size[0] / float(self.tpl["画布"]["宽"])
        return [(int(x * k), int(y * k),
                 int((x + w) * k) + 1, int((y + h) * k) + 1)
                for x, y, w, h in vb]

    def _var_ratio(self):
        """变量区占画布的比例，面板上显示「实际比对了多少」。"""
        if not self.tpl:
            return 0.0
        # 分母是**框内面积**（canvas_rect 给），白边不该算进"变量区占比"
        _r = FRAME_MOD.canvas_rect(self.tpl)
        return databind.coverage(self._var_boxes(),
                                 _r["frame"]["w"], _r["frame"]["h"])

    def _bbox_of(self, kind, idx):
        if not self.tpl:
            return None
        if kind == "gr":
            # 组现在是可选的（四格区那种「一套元素画 4 次」的结构已经去掉），
            # 越界要返回 None 而不是抛异常 —— 撤销/重做后残留的旧选中会走到这里。
            groups = self.tpl.get("组") or []
            if not (0 <= idx < len(groups)):
                return None
            grp = groups[idx]
            ps = grp.get("位置") or []
            if not ps:
                return None
            # 位置只给 x/y/w（没给 h），组内元素用 dx/dy 相对偏移。
            # 必须把元素的 dx/dy/w/h 也算进来，否则 bbox 高度为 0，
            # 点不中任何组内元素 —— 这就是「客户方选不中」的根因。
            els = grp.get("元素") or []
            ex0 = min((float(e.get("dx") or 0) for e in els), default=0)
            ey0 = min((float(e.get("dy") or 0) for e in els), default=0)
            ex1 = max((float(e.get("dx") or 0) + float(e.get("w") or 0)
                       for e in els), default=0)
            ey1 = max((float(e.get("dy") or 0) + float(e.get("h") or 0)
                       for e in els), default=0)
            x0 = min(float(p.get("x", 0)) for p in ps) + ex0 + float(grp.get("dx", 0) or 0)
            y0 = min(float(p.get("y", 0)) for p in ps) + ey0 + float(grp.get("dy", 0) or 0)
            x1 = max(float(p.get("x", 0)) + float(p.get("w", 0)) for p in ps) + ex1 + float(grp.get("dx", 0) or 0)
            y1 = max(float(p.get("y", 0)) + float(p.get("h", 0)) for p in ps) + ey1 + float(grp.get("dy", 0) or 0)
            return (x0, y0, x1 - x0, y1 - y0)
        if kind == "g":
            g = self.tpl["图形"][idx]
            if g.get("type") in ("comp", "compvals"):
                # 顺应性卡没有 x/y/w/h：坐标是「左右边界 + 11 条横线的 y」。
                # 不单独算这里，选中它只会得到一个 1×1mm 的框贴在左上角。
                xs0 = float(g.get("x0") or 0)
                xs1 = float(g.get("x1") or 0)
                ys = [float(v) for v in (g.get("ys") or [])]
                if ys:
                    return (xs0, ys[0], max(0.5, xs1 - xs0),
                            max(0.5, ys[-1] - ys[0] + float(g.get("th") or 1.95)))
            if g.get("type") == "barcode":
                # 条码宽度要么由「模块宽」定、要么由给定总宽定，二选一。
                # 以前这里直接拿 0.3 当模块宽算，选中框比实际条码窄 5mm，
                # 拖着拖着就对不上手。
                # 用「填过数据」的串来算：模板里存的是【条码数据】这种占位符，
                # 直接编码会失败，退回默认值，宽度和模块宽就都不对了。
                seq = PH.fill_placeholders(str(g.get("seq", "")), self._fields() or {})
                w, _mod = barcode.geom(seq, g.get("mode", "C"),
                                       g.get("mod"), g.get("w"))
                return (float(g.get("x", 0)), float(g.get("y", 0)),
                        w, float(g.get("h", 9)))
            return (float(g.get("x", 0)), float(g.get("y", 0)),
                    float(g.get("w", 1)), float(g.get("h", 1)))
        b = self.tpl["文字"][idx]
        if b.get("type") == "table":
            w = sum(b.get("cols") or [0])
            h = sum(b.get("rows") or [0])
            return (float(b.get("x", 0)), float(b.get("y", 0)), w, h)

        # 新结构（3.0 的声明式块）：坐标就写在自己身上，没有 lines。
        # 框的大小交给 render.text_bbox —— 它和绘制用的是同一套字体度量，
        # 所以「框」和「字的墨迹」是同一条边线：
        #   · 居中对齐的块，框不再整体右偏半个字宽
        #   · 按字高排的块，量出来就是真实墨迹宽（以前用 Tk 估，偏 5% 上下）
        #   · 超宽被缩字的块，框跟着缩，不会框住一片空气
        if b.get("lines") is None and b.get("x") is not None:
            try:
                # 按元素所在层取字体（P1-3：L3 / L4 独立）
                return render.text_bbox(b, self._fields() or {},
                                        render.fam_of(self.tpl, b))
            except Exception:
                bw = b.get("w") or 8.0
                return (float(b["x"]), float(b["y"]),
                        float(bw), float(b.get("h") or 3.0))

        # 条码块：宽度按模块数算
        if b.get("bars") and not b.get("lines"):
            bar = b["bars"][0]
            try:
                w = barcode.width_mm(str(bar.get("seq", "")), bar.get("mod", 0.3),
                                     bar.get("mode", "C"))
            except Exception:
                w = 20.0
            return (float(bar.get("x", 0)), float(bar.get("y", 0)),
                    w or 20.0, float(bar.get("h", 10)))

        # 文字宽度用 tkfont.measure 实测：中英文混排、标点、粗体都算得准。
        # 不能用 canvas.bbox() —— 窗口没映射（最小化/隐藏）时它给负坐标和错宽度，
        # 算出来的框能有几百毫米宽，命中区互相重叠。
        lines = b.get("lines") or []
        if not lines:
            return None
        fields = self._fields()
        xs = [l["x"] for l in lines]
        ys = [l["y"] for l in lines]
        minx, miny = min(xs), min(ys)
        maxw = 0.0
        for l in lines:
            t = l.get("t", "")
            if fields:
                t = PH.fill_placeholders(t, fields)
            maxw = max(maxw, self._text_width_mm(t, l.get("sz", 3), bool(l.get("b"))))
        maxh = max((l["y"] - miny) + l.get("sz", 3) * 1.25 for l in lines)
        return (minx, miny, maxw, maxh)

    # 8 个手柄的方向键：nw=n 西北、n=北、ne=东北 …（和 CSS 的写法一致）
    DIRS = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
    # 每个方向改哪两个方向的分量（x=宽、y=高）。拉边只改一个，拉角两个都改。
    DIR_AXES = {
        "nw": (-1, -1), "n": (0, -1), "ne": (1, -1), "e": (1, 0),
        "se": (1, 1), "s": (0, 1), "sw": (-1, 1), "w": (-1, 0),
    }
    # 光标：让手一眼看出「这条边能拉」。macOS 的 Tk 认 sb_*_double_arrow 这一套，
    # top_side / left_side 这些 X11 专名在 mac 上会被忽略，所以只用通用名。
    DIR_CURSOR = {"nw": "top_left_corner", "n": "sb_v_double_arrow",
                  "ne": "top_right_corner", "e": "sb_h_double_arrow",
                  "se": "bottom_right_corner", "s": "sb_v_double_arrow",
                  "sw": "bottom_left_corner", "w": "sb_h_double_arrow"}

    def _handles_px(self) -> dict:
        """选中框上 8 个手柄的像素坐标：4 个角 + 4 条边的中点。

        以前只有右下角一个 —— 于是「只想拉宽一点」这件事做不到，
        一拉就等比放大，字也跟着长高。Word / Figma 里都是拉边只改一个
        方向、拉角才等比，这里对齐那个习惯。
        """
        if not self.sel:
            return {}
        bb = self._bbox_of(*self.sel)
        if not bb:
            return {}
        x, y, w, h = bb
        X0, Y0 = self.ox + x * self.scale, self.oy + y * self.scale
        X1, Y1 = X0 + w * self.scale, Y0 + h * self.scale
        XM, YM = (X0 + X1) / 2.0, (Y0 + Y1) / 2.0

        # 图形（image / rect）**只给 4 个角**（2026-11-20 用户要求「删干净」）。
        # 之前虽然把 image 的缩放改成等比了，但 4 条边的手柄还画着 ——
        # 用户抓到边手柄拖动，行为是"按等比缩放"，可视觉上那是个"拉边"手柄，
        # 造成"我明明只拉宽，怎么整个变大了"的困惑，也容易误触。
        # 现在图形一律只有 4 个角手柄，从 UI 上就没法"只拉一条边"。
        # 文字（L3/L4）保留 8 个手柄 —— 它是唯一允许横向拉伸的东西。
        if self.sel and self.sel[0] == "g":
            g = self.tpl["图形"][self.sel[1]]
            if g.get("type") in ("image", "rect"):
                return {"nw": (X0, Y0), "ne": (X1, Y0),
                        "se": (X1, Y1), "sw": (X0, Y1)}

        return {"nw": (X0, Y0), "n": (XM, Y0), "ne": (X1, Y0),
                "e": (X1, YM), "se": (X1, Y1), "s": (XM, Y1),
                "sw": (X0, Y1), "w": (X0, YM)}

    def _handle_px(self):
        """兼容老调用：右下角那个手柄。"""
        return self._handles_px().get("se")

    def _hit_handle(self, px, py, bb=None):
        """按在哪个手柄上 → 返回方向名（'se' / 'e' / 'n' …），没按到返回 None。"""
        hs = self._handles_px()
        if not hs:
            return None
        # 10px 的手柄配 7px 容差：手柄本身小，容差再小就得像素级对准，
        # 试两次抓不住就会以为「这个手柄没用」。
        best, bd = None, 8.0
        for d, (hx, hy) in hs.items():
            dd = max(abs(px - hx), abs(py - hy))
            if dd <= bd:
                best, bd = d, dd
        if not best:
            return None
        bb = bb or self._bbox_of(*self.sel)
        if not bb:
            return None
        # 小块在屏幕上只有几像素（2mm 高的字在 60% 缩放下不到 2px），
        # 这时四角互相重叠、也和块中心重合。按在上面的本意多半是想拖整块，
        # 所以块太小时不认手柄 —— 真要缩放就滚轮或右侧改字号。
        sw = bb[2] * self.scale
        sh = bb[3] * self.scale
        if sw < 14 and sh < 14:
            return None
        # 只在一个方向窄（比如一条很细的横线）：两侧手柄会叠在一起，不认。
        if sw < 10 and best in ("n", "s"):
            return None
        if sh < 10 and best in ("e", "w"):
            return None
        return best

    # ── 表格：列线 / 行线（像 Word 一样拖）──────────────────────────
    def _table_drag_target(self):
        """当前单选的是不是表格 → 返回它的索引；否则 None。"""
        if not self.tpl or not self.sel or len(self._sel_list()) != 1:
            return None
        if self.sel[0] != "t":
            return None
        b = self.tpl["文字"][self.sel[1]]
        return self.sel[1] if b.get("type") == "table" else None

    def _table_lines_px(self, idx):
        """表格内部列线 / 行线的屏幕坐标。"""
        b = self.tpl["文字"][idx]
        x0 = self.ox + float(b.get("x") or 0) * self.scale
        y0 = self.oy + float(b.get("y") or 0) * self.scale
        cols = [float(c) for c in (b.get("cols") or [])]
        rows = [float(r) for r in (b.get("rows") or [])]
        xs, ys, acc = [], [], 0.0
        for c in cols[:-1]:
            acc += c
            xs.append(x0 + acc * self.scale)
        acc = 0.0
        for r in rows[:-1]:
            acc += r
            ys.append(y0 + acc * self.scale)
        return xs, ys, (x0, y0, x0 + sum(cols) * self.scale,
                        y0 + sum(rows) * self.scale)

    def _hit_table_line(self, px, py):
        """按在表格的列线 / 行线上 → ("col"|"row", 内部第几条线)。"""
        idx = self._table_drag_target()
        if idx is None:
            return None
        xs, ys, (x0, y0, x1, y1) = self._table_lines_px(idx)
        tol = 4.0
        best, bestd = None, 1e9
        for i, x in enumerate(xs):
            if abs(px - x) <= tol and y0 - 6 <= py <= y1 + 6:
                if abs(px - x) < bestd:
                    best, bestd = ("col", i + 1), abs(px - x)
        for i, y in enumerate(ys):
            if abs(py - y) <= tol and x0 - 6 <= px <= x1 + 6:
                if abs(py - y) < bestd:
                    best, bestd = ("row", i + 1), abs(py - y)
        return best

    def _drag_table_line(self, ev):
        """拖列线改左右两列宽，拖行线改上下两行高（总尺寸不变）。"""
        b = self.tpl["文字"][self.drag["idx"]]
        if not self.drag.get("moved"):
            if abs(ev.x - self.drag["sx"]) + abs(ev.y - self.drag["sy"]) <= 2:
                return
            self.drag["moved"] = True
            self.push_history()          # 一次拖动只记一步撤销
        i = self.drag["i"]
        lo = 2.0
        if self.drag["mode"] == "tcol":
            cols = list(self.drag["cols0"])
            d = (ev.x - self.drag["sx"]) / self.scale
            total = cols[i - 1] + cols[i]
            left = min(max(lo, cols[i - 1] + d), total - lo)
            cols[i - 1], cols[i] = round(left, 2), round(total - left, 2)
            b["cols"] = cols
            self.set_status("列宽 %.1f / %.1f mm" % (cols[i - 1], cols[i]))
        else:
            rows = list(self.drag["rows0"])
            d = (ev.y - self.drag["sy"]) / self.scale
            total = rows[i - 1] + rows[i]
            top = min(max(1.2, rows[i - 1] + d), total - 1.2)
            rows[i - 1], rows[i] = round(top, 2), round(total - top, 2)
            b["rows"] = rows
            self.set_status("行高 %.1f / %.1f mm" % (rows[i - 1], rows[i]))
        self.redraw()

    def _resize_dirs(self, ev) -> tuple:
        """手柄拖了多远 → (kx, ky)，各自相对按下时那一维的倍数。

        分母用「按下时这一维在屏幕上有多少像素」再夹一个下限，
        免得 1mm 高的细线拖 5px 就翻好几倍。
        """
        dx = ev.x - self.drag["sx"]
        dy = ev.y - self.drag["sy"]
        w0 = max(1.0, float(self.drag.get("w0") or 1.0))
        h0 = max(1.0, float(self.drag.get("h0") or 1.0))
        d = self.drag.get("dir") or "se"
        ax, ay = self.DIR_AXES.get(d, (1, 1))
        # 屏幕上至少按 40px 算，太小的块拖起来才不至于一碰就爆
        span_w = max(40.0, w0 * self.scale)
        span_h = max(40.0, h0 * self.scale)
        kx = 1.0 + ax * (dx / span_w) if ax else 1.0
        ky = 1.0 + ay * (dy / span_h) if ay else 1.0
        clamp = lambda k: max(0.12, min(8.0, k))     # noqa: E731
        return clamp(kx), clamp(ky)

    def _elem_cfg(self, kind, idx):
        """拿到某个元素的原始 dict（缩放手柄要留一份按下时的快照）。"""
        if kind == "gr":
            return (self.tpl.get("组") or [])[idx]
        if kind == "g":
            return (self.tpl.get("图形") or [])[idx]
        return (self.tpl.get("文字") or [])[idx]

    # ── 顺应性卡：表格线（L2）和数值（L4）是成对的两个对象 ──────────
    COMP_TYPES = ("comp", "compvals")

    def _comp_twin(self, idx):
        """另一半个顺应性卡：线找数、数找线。没有配对就返回 None。"""
        g = self.tpl["图形"][idx]
        key = g.get("配对") or "顺应性卡"
        for j, h in enumerate(self.tpl.get("图形") or []):
            if j == idx or h.get("type") not in self.COMP_TYPES:
                continue
            if (h.get("配对") or "顺应性卡") == key:
                return j
        return None

    @staticmethod
    def _comp_geo(g):
        return {"x0": float(g.get("x0") or 0), "x1": float(g.get("x1") or 0),
                "xm": float(g.get("xm") or 0),
                "ys": [float(v) for v in (g.get("ys") or [])]}

    @staticmethod
    def _comp_set_geo(g, geo, dx=0.0, dy=0.0):
        g["x0"] = round(geo["x0"] + dx, 2)
        g["x1"] = round(geo["x1"] + dx, 2)
        g["xm"] = round(geo["xm"] + dx, 2)
        g["ys"] = [round(v + dy, 2) for v in geo["ys"]]

    def _comp_font_target(self, idx):
        """字号挂在哪一半上 —— 永远是数值那一半（线没有字）。"""
        g = self.tpl["图形"][idx]
        if g.get("type") == "compvals":
            return g
        j = self._comp_twin(idx)
        return self.tpl["图形"][j] if j is not None else g

    def _apply_resize(self, kx, ky):
        """按 (kx, ky) 改选中的这一块，返回 (改到了没, 状态栏文案)。

        kx/ky 是「相对按下时」的倍数，没拉的那个方向是 1.0。

        图形（矩形 / 图片）：宽高分别乘 —— 拉左右边只改宽、高度一毫米不动，
        就是 Word 里拉图片侧边框那种「压扁」效果。
        文字：垂直拉改字高，水平拉改 `xs`（横向压/放，字高不变）——
        这样「字要宽一点但别长高」终于能做到，不用去改字号再改回来。
        """
        kind, idx = self.drag["kind"], self.drag["idx"]
        d = self.drag.get("dir") or "se"
        ax, ay = self.DIR_AXES.get(d, (1, 1))
        c0 = self.drag.get("cfg0") or {}
        if abs(kx - 1.0) < 2e-3 and abs(ky - 1.0) < 2e-3:
            return False, ""
        pct = lambda k: int(round(k * 100))      # noqa: E731

        if kind == "gr":
            # 四宫格：格子位置是刀模定的（底图上那四条红线），跟着缩放就跟
            # 模切线错开了。所以只缩放「格子里的东西」，格子本身不动。
            grp = self.tpl["组"][idx]
            for e, e0 in zip(grp.get("元素") or [], c0.get("元素") or []):
                self._scale_decl(e, e0, kx, ky, d)
            return True, ("四宫格内容 宽 %d%% · 高 %d%%（格子位置由刀模定，不动）"
                          % (pct(kx), pct(ky)))

        if kind == "t":
            b = self.tpl["文字"][idx]
            if b.get("lines"):
                # 老式多行块：只有一个字号，等比（拖边也只认一个方向）
                ratio = kx if ax else ky
                base = float((c0.get("lines") or [{}])[0].get("sz", 3) or 3)
                self._resize_block(idx, round(max(0.5, base * ratio), 2))
                return True, "字号 %.2f mm" % b["lines"][0]["sz"]
            ok = self._scale_decl(b, c0, kx, ky, d)
            if not ok:
                return False, "这个块不能这样拉"
            txt = "字高 %.2f mm" % float(b.get("h") or 0)
            if ax and not ay:
                txt = "横向 %d%%（字高不动）" % pct(kx)
            elif ax and ay:
                txt = "字高 %.2f mm · 横向 %d%%" % (float(b.get("h") or 0), pct(kx))
            return True, txt

        g = self.tpl["图形"][idx]
        t = g.get("type")
        if t in self.COMP_TYPES:
            # 顺应性卡：表格线是固定版面（L2），字号挂在数值那一半（L4）。
            # 拖线框的手柄也改字 —— 卡片是一张，别让人去 L4 再改一次。
            ratio = kx if ax else ky
            if ax and ay:
                ratio = (kx + ky) / 2.0
            tgt = self._comp_font_target(idx)
            for k, mini in (("th", 0.6), ("th_r", 0.6), ("th_note", 0.5),
                            ("dy", 0.4), ("gap", 0.2)):
                if c0.get(k):
                    tgt[k] = round(max(mini, float(c0[k]) * ratio), 2)
            return True, "顺应性卡字号 %d%%（表格线不动）" % pct(ratio)
        if t == "line":
            return False, "这条细线的长短请直接改端点坐标"
        # ── 图形一律等比缩放（2026-11-20 用户要求）──────────────────
        # 用户原话：「图形这边只允许等比例放大缩小，不允许横向拉伸纵向拉伸，
        # 横纵拉伸只能出现在 L3 和 L4 里。这样子你就不会瞎去调图案了。」
        # 为什么：法规图标和品牌字标拉伸后字形就变了（圆变成椭圆、笔画
        # 粗细不一），印出来是错的图，而且肉眼一眼看得出歪。
        # 适用范围：所有图形（image 位图 + rect 色块/管线图），
        # 它们都是"一个图案"，压扁就失真。文字不受限（L3/L4 可以横向拉伸）。
        if t in ("image", "rect"):
            if ax and ay:
                ratio = min(kx, ky) if abs(kx - ky) > 0.02 else kx
            else:
                ratio = kx if ax else ky
            w0 = float(c0.get("w") or g.get("w") or 1)
            h0 = float(c0.get("h") or g.get("h") or 1)
            # 锚点：拖右边（含 ne/se/e）左上角不动；拖左边（含 nw/sw/w）
            # 右下角不动。同理上/下。这样拖哪边就像哪边固定。
            x0, y0 = float(c0.get("x") or 0), float(c0.get("y") or 0)
            nw, nh = max(0.3, w0 * ratio), max(0.3, h0 * ratio)
            if "w" in d:                            # nw / sw / w → 右边不动
                g["x"] = round(x0 + (w0 - nw), 2)
            if "n" in d:                            # nw / ne / n → 下边不动
                g["y"] = round(y0 + (h0 - nh), 2)
            g["w"], g["h"] = round(nw, 2), round(nh, 2)
            return True, "等比 %d%%（%.1f×%.1f mm，不允许单向拉伸）" % (
                pct(ratio), nw, nh)
        ok = self._scale_decl(g, c0, kx, ky, d)
        if not ok:
            return False, "这个图形不能这样拉"
        if ax and not ay:
            return True, "宽 %.2f mm（高不动）" % float(g.get("w") or 0)
        if ay and not ax:
            return True, "高 %.2f mm（宽不动）" % float(g.get("h") or 0)
        return True, "宽 %.2f × 高 %.2f mm" % (float(g.get("w") or 0),
                                              float(g.get("h") or 0))

    def _scale_decl(self, it, it0, kx, ky, d) -> bool:
        """把一个声明式元素按 kx/ky 就地缩放。返回有没有改动。

        文字和图形分两套口径：
          · 文字  → 垂直改 `h`（字高）、水平改 `xs`（横向倍数，字高不变）
          · 图形  → 宽高直接乘
        拉左边 / 上边时把锚点也一起挪，让对边钉在原位（跟 Word 一样）。
        """
        if not isinstance(it, dict) or not isinstance(it0, dict):
            return False
        t = it.get("type")
        ax, ay = self.DIR_AXES.get(d, (1, 1))
        did = False

        if t in (None, "text", "list"):
            h0 = float(it0.get("h") or 0)
            if ky != 1.0 and h0:
                it["h"] = round(max(0.6, h0 * ky), 2)
                if ay < 0 and it0.get("y") is not None:
                    it["y"] = round(float(it0["y"]) - (float(it["h"]) - h0), 2)
                did = True
            if t == "list" and ky != 1.0 and it0.get("step"):
                it["step"] = round(max(0.8, float(it0["step"]) * ky), 2)
            if kx != 1.0:
                if it0.get("fit") and it0.get("w"):
                    it["w"] = round(max(0.6, float(it0["w"]) * kx), 2)
                else:
                    xs0 = float(it0.get("xs") or 1.0)
                    it["xs"] = round(max(0.15, min(6.0, xs0 * kx)), 3)
                did = True
            return did

        if t == "table":
            if ay != 1.0:
                it["rows"] = [max(0.6, round(float(r) * ky, 2))
                              for r in (it0.get("rows") or [4.0])]
            if ax != 1.0:
                it["cols"] = [max(0.6, round(float(c) * kx, 2))
                              for c in (it0.get("cols") or [20])]
            return True

        if t == "vtext":
            # 竖排：盒子宽是「字的横向占位」，高是「整串长度」
            if it0.get("h"):
                it["h"] = round(max(0.6, float(it0["h"]) * (kx if ax else ky)), 2)
            if it0.get("w"):
                it["w"] = round(max(0.6, float(it0["w"]) * (ky if ay else kx)), 2)
            return True

        if t == "barcode":
            if ax:
                if it0.get("mod"):
                    it["mod"] = max(0.10, round(float(it0["mod"]) * kx, 3))
                elif it0.get("w"):
                    it["w"] = max(5.0, round(float(it0["w"]) * kx, 2))
            if ay:
                it["h"] = max(1.0, round(float(it0.get("h") or 9) * ky, 2))
            return True

        # 矩形 / 图片 / 其它有 w、h 的图形
        if it0.get("w") is not None:
            it["w"] = round(max(0.3, float(it0["w"]) * kx), 2)
            if ax < 0:
                it["x"] = round(float(it0.get("x") or 0)
                                + float(it0["w"]) * (1 - kx), 2)
        if it0.get("h") is not None:
            it["h"] = round(max(0.3, float(it0["h"]) * ky), 2)
            if ay < 0:
                it["y"] = round(float(it0.get("y") or 0)
                                + float(it0["h"]) * (1 - ky), 2)
        return True

    # ── 多选：Shift/⌘ 点第二个元素 ──────────────────────────────
    def _sel_list(self):
        """当前选中的全部元素，主选在前。"""
        if not self.sel:
            return []
        out = [tuple(self.sel)]
        for k in getattr(self, "sel_more", []) or []:
            k = tuple(k)
            if k not in out:
                out.append(k)
        return out

    def _sel_toggle(self, key):
        """把一个元素加进/移出多选。"""
        key = tuple(key)
        more = [tuple(k) for k in (self.sel_more or [])]
        if key == tuple(self.sel):
            # 取消主选：把最后一个副选提上来当主选
            self.sel = more.pop() if more else None
            self.sel_more = more
        elif key in more:
            self.sel_more = [k for k in more if k != key]
        elif not self.sel:
            self.sel = key
        else:
            self.sel_more = more + [key]

    def _snap_pos(self, key):
        """记下元素当前的位置（绝对量）。拖动/多选移动都靠它，避免逐帧累加漂移。"""
        kind, idx = key
        if kind == "gr":
            g = self.tpl["组"][idx]
            return ("gr", (float(g.get("dx") or 0), float(g.get("dy") or 0)))
        if kind == "g":
            g = self.tpl["图形"][idx]
            if g.get("type") in self.COMP_TYPES:
                return ("comp", self._comp_geo(g))
            return ("xy", (float(g.get("x") or 0), float(g.get("y") or 0)))
        b = self.tpl["文字"][idx]
        if b.get("lines"):
            return ("lines", [(l["x"], l["y"]) for l in b["lines"]])
        return ("xy", (float(b.get("x") or 0), float(b.get("y") or 0)))

    def _set_pos(self, key, snap, dx, dy):
        """按「快照 + 位移」定位。文字块的 x 可能是中线，所以只能动锚点不能动框边。"""
        kind, idx = key
        mode, data = snap
        if mode == "gr":
            g = self.tpl["组"][idx]
            g["dx"] = round(data[0] + dx, 2)
            g["dy"] = round(data[1] + dy, 2)
        elif mode == "comp":
            g = self.tpl["图形"][idx]
            self._comp_set_geo(g, data, dx, dy)
            j = self._comp_twin(idx)
            if j is not None:
                self._comp_set_geo(self.tpl["图形"][j], data, dx, dy)
        elif mode == "lines":
            b = self.tpl["文字"][idx]
            for (x0, y0), ln in zip(data, b["lines"]):
                ln["x"] = round(x0 + dx, 2)
                ln["y"] = round(y0 + dy, 2)
        else:
            obj = self.tpl["图形"][idx] if kind == "g" else self.tpl["文字"][idx]
            obj["x"] = round(data[0] + dx, 2)
            obj["y"] = round(data[1] + dy, 2)

    # ─────────────── 软吸附：拖到叠影附近就轻轻吸过去 ───────────────
    SNAP_R_MM = 3.0        # 引力半径：离最佳位置 3mm 以外就完全不受影响
    SNAP_PPM = 6.0         # 比对用的分辨率（1px≈0.17mm，够对位；10ppm 时单次要 70ms）

    def _elem_of(self, kind, idx):
        """按 (kind, idx) 取出元素字典本身。

        kind: "t" 文字 / "g" 图形 / "gr" 组。取不到返回 None，
        调用方（只用来算哈希）要能接受 None。
        """
        try:
            if kind == "t":
                return (self.tpl.get("文字") or [])[idx]
            if kind == "g":
                return (self.tpl.get("图形") or [])[idx]
            if kind == "gr":
                return (self.tpl.get("组") or [])[idx]
        except (IndexError, TypeError):
            return None
        return None

    def _elem_hash(self, kind, idx):
        """这个元素当前内容的指纹。

        缓存键不能再带 idx —— idx 会随增删元素而变，同一个 idx 可能指向
        完全不同的元素，缓存就串了（删一个再 undo，拿到的可能是旧遮罩）。
        改用「内容哈希」，内容是啥就缓存啥，元素挪位置也不失效（遮罩与
        位置无关，这正是它能缓存的原因）。
        """
        try:
            el = self._elem_of(kind, idx)
        except Exception:
            return ""
        import hashlib
        import json as _json
        raw = _json.dumps(el or {}, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

    def _snap_mask_of(self, kind, idx):
        """这一块元素自己长什么样（墨迹遮罩，带缓存）。

        遮罩与位置无关，拖动全程只渲染一次 —— 每帧重渲染文字是「拖起来一顿」
        的主要来源。

        ★ L4 用「叠影数据」渲染，不用当前规格（2026-11-23 用户要求）
        -------------------------------------------------------
        叠影上印的是某一版设计稿的**某一个规格**的数。若拿当前规格渲染遮罩，
        遮罩的宽度/形状随规格变，「按叠影对位」得到的坐标也跟着变 ——
        同一个元素换个规格对出来两个位置，这对位功能就没意义了。

        L4 一律用 `databind.ref_fields()`（= tpl["叠影数据"]，建模板时定死、
        不随规格切换）渲染。这样重绘的字和叠影上的字是同一串，遮罩形状一致。
        L2/L3 是固定版面，内容本来就不随规格变，用哪份数据都一样，
        但为了口径统一也走同一份 —— 少一处分支就少一处错。

        缓存键 = (元素内容哈希, 叠影数据哈希, dpi)，**不带 idx**。
        """
        dpi = int(round(self.SNAP_PPM * 25.4))
        # 对位一律用叠影数据；叠影数据改了 → 哈希变 → 自动失效
        fields = databind.ref_fields(self.tpl)
        key = (kind, self._elem_hash(kind, idx),
               databind.ref_fields_hash(self.tpl), dpi)
        if key in self._snap_cache:
            return self._snap_cache[key]
        try:
            im, mx, my, ppm = render.render_one(self.tpl, kind, idx,
                                                fields, dpi=dpi)
        except Exception:
            im = None
        out = None
        if im is not None:
            m = snap.ink_mask(im)
            if m.getbbox() is not None:        # 全白 = 这元素没画出来，别吸
                out = (m, mx, my, ppm, snap.ink_amount(m))
        if len(self._snap_cache) > 24:
            self._snap_cache.clear()      # 单纯控体量，不是"内容变了"
        self._snap_cache[key] = out
        return out

    def _ref_window(self, wx, wy, ww, wh):
        """从叠影上取一块毫米窗口（含纸外部分，越界补白）。"""
        try:
            w_mm = float(self.tpl["画布"]["宽"])
            h_mm = float(self.tpl["画布"]["高"])
        except Exception:
            return None
        k = self.SNAP_PPM
        size = (max(8, int(round(w_mm * k))), max(8, int(round(h_mm * k))))
        page = self._ref_image(size)
        if page is None:
            return None
        pad = int(round(4 * k))                 # 纸外 4mm 的缓冲
        # 元素可以拖到纸外面，crop 越界会补黑 —— 先铺一张白底再取
        bg = render.Image.new("RGB", (page.width + 2 * pad,
                                      page.height + 2 * pad), "white")
        bg.paste(page, (pad, pad))
        page = bg
        box = (int(round(wx * k)) + pad, int(round(wy * k)) + pad,
               int(round((wx + ww) * k)) + pad, int(round((wy + wh) * k)) + pad)
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            return None
        return page.crop(box)

    def _snap_probe(self, key, dx=0.0, dy=0.0):
        """算出「这块元素离叠影的最佳位置还差多少」（完整量，不衰减）。

        返回 (dx_mm, dy_mm, score)；算不了（没遮罩 / 没叠影）返回 None。
        """
        got = self._snap_mask_of(*key)
        if got is None:
            return None
        pos = self._snap_pos(key)
        if pos[0] != "xy":
            return None
        mask, mx, my, ppm, denom = got
        ex, ey = pos[1]
        R = self.SNAP_R_MM
        mw, mh = mask.width / ppm, mask.height / ppm
        ref = self._ref_window(ex + dx + mx - R, ey + dy + my - R,
                               mw + 2 * R, mh + 2 * R)
        if ref is None:
            return None
        ox, oy, sc = snap.find_offset(mask, snap.ink_mask(ref),
                                      int(round(R * ppm)), denom=denom)
        return ox / ppm, oy / ppm, sc

    def _snap_nudge(self, dx, dy):
        """把拖动位移往「叠影里最吻合的位置」拽一把。返回修正后的 (dx, dy)。

        引力随距离衰减（见 snap.nudge）：拖到 1mm 内几乎完全贴上去，2mm 外
        只剩一点点，3mm 以外完全不管；鼠标真拉远就自然脱离。这里不动元素
        本身，每帧都从鼠标位置重算，所以不存在「吸住出不来」。
        """
        if not (self.snap_on and self.show_ref):
            return dx, dy
        if not (self.tpl or {}).get("参考图"):
            return dx, dy
        if len(self.drag.get("items") or []) > 1:
            return dx, dy                      # 多选不吸：各块最佳点会打架
        key = (self.drag.get("kind"), self.drag.get("idx"))
        if key[0] not in ("t", "g") or key[1] is None:
            return dx, dy
        now = time.time()
        if now - self._snap_ts < 0.06:         # 60ms 一次，够跟手；单次约 20ms
            return dx + self._snap_last[0], dy + self._snap_last[1]
        self._snap_ts = now
        got = self._snap_probe(key, dx, dy)
        if got is None:
            self._snap_last = (0.0, 0.0, 0.0)
            return dx, dy
        tx, ty, sc = got
        R = self.SNAP_R_MM
        d = (tx * tx + ty * ty) ** 0.5
        if sc < 0.30 or d >= R or d <= 1e-6:
            self._snap_last = (0.0, 0.0, sc)
            return dx, dy
        w = (1.0 - d / R) ** 1.25 * 0.95
        rx, ry = tx * w, ty * w
        self._snap_last = (rx, ry, sc)
        return dx + rx, dy + ry

    def align_to_ref(self, idx, kind="t"):
        """「按叠影对位」：把这一块一次性吸到最近的墨迹上（可撤销）。

        用户提的场景：L4 那些数据文字在设计稿上是**另一个规格的数**，字不对、
        宽度也不同，自动拟合很容易串到邻列去。所以不批量猜，给一个显式按钮：
        选中哪块就对哪块，一次一块，对完能撤回。内容以后从 Excel 换，位置不动。
        """
        if not (self.tpl or {}).get("参考图"):
            self.set_status("这个模板还没设叠影，没法对位")
            return
        key = (kind, idx)
        self._snap_ts = 0
        got = self._snap_probe(key)
        if got is None:
            self.set_status("这块没有可比的墨迹（反白字或空块），对不了位")
            return
        tx, ty, sc = got
        d = (tx * tx + ty * ty) ** 0.5
        if sc < 0.30:
            self.set_status("附近找不到对得上的墨迹（匹配度 %.0f%%）" % (sc * 100))
            return
        if d > self.SNAP_R_MM:
            self.set_status("最近的墨迹在 %.1fmm 外，超出对位范围（%.0fmm）"
                            % (d, self.SNAP_R_MM))
            return
        if d < 0.02:
            self.set_status("已经对齐了，不用动")
            return
        self.push_history()
        self._offset_elem(key, tx, ty)
        self.save()
        self.redraw()
        self.panel()
        self.set_status("已按叠影对位：%+.2f, %+.2f mm（匹配 %.0f%%，可撤销）"
                        % (tx, ty, sc * 100))

    def fit_width_to_ref(self, idx):
        """⌖ 按叠影定宽：由叠影量 H、W，解出 h + tracking（P1-5）。

        用户口径：
          · 由叠影墨迹框取 H、W，**先定字号**（H 决定），再**先词距后字距**
            摊平宽度差
          · 字距超出字高 ±10% 时先换相邻字重，仍超限则**标红**
          · L4 只存 `h` 和 `tracking`，**不存宽度**（宽度随规格变）
          · 在「叠影数据」那组上求解，所有规格共用

        为什么在叠影数据上求解：求解依据是**叠影上那串字有多宽**，
        跟当前规格无关。用当前规格求解，换个规格就得重算一遍，
        而且会解出不同的字距 —— 那就不是"定死一套字号"了。

        做法：
          ① 取这一块的叠影窗口（复用 _snap_mask_of，它已经用叠影数据渲染）
          ② 在窗口里找最佳对位 → 得到 dx,dy 与吻合度
          ③ 在**最佳对位处**量叠影的墨迹框 → W、H
          ④ fitwidth.solve(文本, W, H, kind) → tracking / word_gap / 新字面
          ⑤ 写回 h + tracking（+ word_gap + kind），**删掉 xs**
        """
        if not (self.tpl or {}).get("参考图"):
            self.set_status("这个模板还没设叠影，定不了宽")
            return
        b = self.tpl["文字"][idx]
        text = PH.fill_placeholders(str(b.get("t") or ""),
                                    databind.ref_fields(self.tpl))
        if not text:
            self.set_status("这块是空文本，定不了宽")
            return

        # ★ 精确定宽**不能复用 _snap_mask_of**（P1-5 踩过）
        #   那个是拖动吸附用的：SNAP_PPM = 6px/mm（≈152dpi），
        #   一个 50mm×4mm 的元素量出来窗口有 56mm×10mm —— 上下几行字
        #   全被框进去，量到的"字高"是 7.33mm（真实 1.70mm），
        #   求出来的 h 就变成 5.833，误差 17mm。
        #   定宽是**一次性、要求准**的操作，可以慢一点：
        #   用 300dpi 单独渲染这一块，再在叠影上取同一位置的高精度窗口。
        ppm = 300 / 25.4                       # ≈11.8 px/mm
        dpi = 300
        fields_ref = databind.ref_fields(self.tpl)
        im, mx, my, ppm = render.render_one(
            self.tpl, "t", idx, fields_ref, dpi=dpi)
        if im is None:
            self.set_status("这块渲染不出来，定不了宽")
            return
        pos = self._snap_pos(("t", idx))
        if pos[0] != "xy":
            self.set_status("这块不支持按字高排，定不了宽")
            return
        ex, ey = pos[1]
        # 元素自己的墨迹遮罩（高精度）
        m = snap.ink_mask(im)
        bb_m = m.getbbox()
        if not bb_m:
            self.set_status("这块没有墨迹（反白字或空块），定不了宽")
            return
        mw = (bb_m[2] - bb_m[0]) / ppm
        mh = (bb_m[3] - bb_m[1]) / ppm
        # 元素墨迹在纸上的位置
        ex_ink = ex + mx + bb_m[0] / ppm
        ey_ink = ey + my + bb_m[1] / ppm
        R = 1.5                                 # 只往外放 1.5mm，别框进邻行
        ref = self._ref_window(ex_ink - R, ey_ink - R, mw + 2 * R, mh + 2 * R)
        if ref is None:
            self.set_status("取不到叠影窗口，定不了宽")
            return
        # 在窗口里找最佳对位（让两边的墨迹对齐再量）
        d0 = snap.ink_amount(m)
        ox, oy, sc = snap.find_offset(m, snap.ink_mask(ref),
                                      int(round(R * ppm)), denom=d0)
        # 对齐后再量叠影的墨迹框
        from PIL import Image as _Im
        ref2 = self._ref_window(ex_ink - R + ox / ppm, ey_ink - R + oy / ppm,
                               mw + 2 * R, mh + 2 * R)
        if ref2 is None:
            self.set_status("取不到叠影窗口，定不了宽")
            return
        # ★ 只取**与元素墨迹垂直重叠的那条带**（P1-5 踩过）
        #   窗口里上下常常还有别的墨（比如条码人可读行紧贴着条码黑条）。
        #   整窗取 bbox 会把邻行的墨一起框进来：一行 1.69mm 高的字，
        #   量出 H=4.50mm —— 求出来的 h 就变成 4.50，整块字被放大 2.6 倍。
        #   做法：把窗口按"元素墨迹的中心线"上下各留 0.6×字高，其余切掉。
        #   字高从**元素自己的遮罩**来（那个是准的，4.15/1.69 都对）。
        kref = self.SNAP_PPM
        hband = max(0.6, mh * 0.6)          # 上下各留 0.6 个字高
        band_top = (mh - hband) / 2.0       # 相对窗口顶的偏移（毫米）
        if band_top < 0:
            band_top = 0.0
        y0px = int(round(band_top * kref))
        y1px = int(round((band_top + hband) * kref))
        y1px = min(y1px, ref2.height)
        if y1px - y0px < 2:
            self.set_status("叠影这块太窄，量不出字高")
            return
        band_img = ref2.crop((0, y0px, ref2.width, y1px))
        bb = band_img.convert("L").point(
            lambda v: 255 if v < 200 else 0).getbbox()
        if not bb:
            # 常见于 REF / LOT 这类"字在框里"的元素：框线占了垂直带，
            # 或者叠影上这个位置本来就是空白（设计稿那版没印东西）。
            self.set_status("叠影这条带上没有墨迹（可能是空位或框内空白），定不了宽")
            return
        W = (bb[2] - bb[0]) / kref
        # 高度用元素自己的（准），叠影的高度只用来**校正**（万一叠影字更大）
        H_ref = (bb[3] - bb[1]) / kref
        H = mh if abs(mh - H_ref) < 0.4 else max(mh, H_ref)
        if H <= 0 or W <= 0:
            self.set_status("叠影这块量不出字高/字宽，定不了宽")
            return
        import fitwidth as FW
        kind0 = b.get("kind") or LK.cjk_kind(
            self._font_of_layer(layers.of(b, "t")))
        r = FW.solve(text, W, H, kind=kind0)

        self.push_history()
        b["h"] = round(H, 3)
        b["tracking"] = r["tracking"]
        if abs(r["word_gap"]) > 1e-6:
            b["word_gap"] = r["word_gap"]
        else:
            b.pop("word_gap", None)
        if r["kind"] != kind0:
            b["kind"] = r["kind"]
        # 定宽之后**不许再有 xs**（那是压扁字，正是这次要废掉的东西）
        b.pop("xs", None)
        self.save()
        self.redraw()
        self.panel()

        if r["ok"]:
            self.set_status(
                "定宽 %.2fmm：h=%.2f 字距 %+.3fmm（误差 %.3fmm）"
                % (W, H, r["tracking"], r["err"]))
        else:
            # 标红：字距超限且换字重也收不住
            self.set_status(
                "⚠ 字距超限：%+.3fmm > 字高 ±10%%（限 ±%.3fmm），"
                "已取最接近方案（误差 %.3fmm），请人工确认"
                % (r["tracking"], FW.limit_of(H), r["err"]))
            messagebox.showwarning(
                "字距超限（已标红）",
                "目标宽 %.2fmm 用字距收不住：\n\n"
                "  解得字距 %+.3fmm\n"
                "  限值 ±%.3fmm（字高 %.2fmm 的 10%%）\n\n"
                "已换遍相邻字重仍收不住，取最接近的方案写回（误差 %.3fmm）。\n"
                "这行字号或文字本身可能要人工调整。"
                % (W, r["tracking"], FW.limit_of(H), H, r["err"]))

    def _snap_settle(self):
        """松手时补最后一脚：已经贴得很近的，直接落到位。

        拖动过程中的引力是衰减的，松手时总会差个零点几毫米 —— 那点差距
        肉眼看得出来（尤其和底下的叠影对着看）。这里只在「已经很近」时
        才补，差得远（>1.2mm）就不动，免得松手时突然跳一下。
        """
        if not (self.snap_on and self.show_ref):
            return
        if not (self.tpl or {}).get("参考图") or not self.drag:
            return
        if len(self.drag.get("items") or []) > 1:
            return
        key = (self.drag.get("kind"), self.drag.get("idx"))
        if key[0] not in ("t", "g") or key[1] is None:
            return
        self._snap_ts = 0
        got = self._snap_probe(key)
        if got is None:
            return
        tx, ty, sc = got
        d = (tx * tx + ty * ty) ** 0.5
        if sc < 0.45 or d <= 0.02 or d > 1.2:
            return
        self._offset_elem(key, tx, ty)
        self.set_status("吸附到位（%+.2f, %+.2f mm）" % (tx, ty))

    def _offset_elem(self, key, dx, dy):
        """把一个元素整体平移（对齐、微调都走这里）。"""
        kind, idx = key
        if kind == "gr":
            g = self.tpl["组"][idx]
            g["dx"] = round(float(g.get("dx") or 0) + dx, 2)
            g["dy"] = round(float(g.get("dy") or 0) + dy, 2)
        elif kind == "g":
            g = self.tpl["图形"][idx]
            if g.get("type") in self.COMP_TYPES:
                self._comp_set_geo(g, self._comp_geo(g), dx, dy)
                j = self._comp_twin(idx)
                if j is not None:
                    self._comp_set_geo(self.tpl["图形"][j],
                                       self._comp_geo(self.tpl["图形"][j]), dx, dy)
            else:
                g["x"] = round(float(g.get("x") or 0) + dx, 2)
                g["y"] = round(float(g.get("y") or 0) + dy, 2)
        else:
            b = self.tpl["文字"][idx]
            if b.get("lines"):
                for l in b["lines"]:
                    l["x"] = round(l["x"] + dx, 2)
                    l["y"] = round(l["y"] + dy, 2)
            else:
                b["x"] = round(float(b.get("x") or 0) + dx, 2)
                b["y"] = round(float(b.get("y") or 0) + dy, 2)

    def _apply_offsets(self, pairs):
        """成批平移。顺应性卡的线和数是一对，只算一次，别挪两遍。"""
        done = set()
        for key, dx, dy in pairs:
            key = tuple(key)
            if key in done:
                continue
            kind, idx = key
            if kind == "g" and self.tpl["图形"][idx].get("type") in self.COMP_TYPES:
                j = self._comp_twin(idx)
                if j is not None:
                    done.add(("g", j))
            self._offset_elem(key, dx, dy)
            done.add(key)

    ALIGNS = (("left", "左"), ("hcenter", "水平中"), ("right", "右"),
              ("top", "上"), ("vcenter", "垂直中"), ("bottom", "下"))

    def _align_selected(self, how):
        """多选对齐：以选中范围的边界为准，不是以画布为准。"""
        items = [k for k in self._sel_list() if self._bbox_of(*k)]
        if len(items) < 2:
            return
        boxes = {k: self._bbox_of(*k) for k in items}
        gx0 = min(b[0] for b in boxes.values())
        gy0 = min(b[1] for b in boxes.values())
        gx1 = max(b[0] + b[2] for b in boxes.values())
        gy1 = max(b[1] + b[3] for b in boxes.values())
        pairs = []
        for k, b in boxes.items():
            dx = dy = 0.0
            if how == "left":
                dx = gx0 - b[0]
            elif how == "right":
                dx = gx1 - (b[0] + b[2])
            elif how == "hcenter":
                dx = (gx0 + gx1) / 2.0 - (b[0] + b[2] / 2.0)
            elif how == "top":
                dy = gy0 - b[1]
            elif how == "bottom":
                dy = gy1 - (b[1] + b[3])
            elif how == "vcenter":
                dy = (gy0 + gy1) / 2.0 - (b[1] + b[3] / 2.0)
            pairs.append((k, dx, dy))
        self.push_history()
        self._apply_offsets(pairs)
        self.save()
        self.redraw()
        self.panel()
        self.set_status("已对齐（%d 项）" % len(items))

    def _distribute(self, axis):
        """等间距分布：最外两个不动，中间的按空隙均分。"""
        items = [k for k in self._sel_list() if self._bbox_of(*k)]
        if len(items) < 3:
            return
        boxes = {k: self._bbox_of(*k) for k in items}
        if axis == "h":
            items.sort(key=lambda k: boxes[k][0])
            start = boxes[items[0]][0]
            end = boxes[items[-1]][0] + boxes[items[-1]][2]
            used = sum(boxes[k][2] for k in items)
            gap = (end - start - used) / float(len(items) - 1)
            cursor = start
            pairs = []
            for k in items:
                pairs.append((k, cursor - boxes[k][0], 0.0))
                cursor += boxes[k][2] + gap
        else:
            items.sort(key=lambda k: boxes[k][1])
            start = boxes[items[0]][1]
            end = boxes[items[-1]][1] + boxes[items[-1]][3]
            used = sum(boxes[k][3] for k in items)
            gap = (end - start - used) / float(len(items) - 1)
            cursor = start
            pairs = []
            for k in items:
                pairs.append((k, 0.0, cursor - boxes[k][1]))
                cursor += boxes[k][3] + gap
        self.push_history()
        self._apply_offsets(pairs)
        self.save()
        self.redraw()
        self.panel()
        self.set_status("已等距分布（%d 项）" % len(items))

    # ── 键盘微调 ────────────────────────────────────────────────
    def _on_arrow(self, ev):
        w = ev.widget
        if isinstance(w, (tk.Entry, tk.Text, tk.Listbox, tk.Scale,
                          ttk.Combobox, ttk.Scale, ttk.Entry)):
            return None                      # 输入框里让光标自己走
        if not self.sel or not self.tpl:
            return None
        step = 1.0 if (getattr(ev, "state", 0) & 0x0001) else 0.2
        dx = {"Left": -step, "Right": step}.get(ev.keysym, 0.0)
        dy = {"Up": -step, "Down": step}.get(ev.keysym, 0.0)
        self._nudge(dx, dy)
        return "break"

    def _nudge(self, dx, dy):
        items = self._sel_list()
        if not items:
            return
        now = time.time()
        # 连按只记一步撤销，否则按十下方向键就要点十次「撤销」
        if now - getattr(self, "_nudge_ts", 0.0) > 0.7:
            self.push_history()
        self._nudge_ts = now
        self._apply_offsets([(k, dx, dy) for k in items])
        self.save()
        self.redraw()
        label = self._elem_label(*items[0]) if len(items) == 1 else "%d 项" % len(items)
        self.set_status("%s  %+.2f, %+.2f mm" % (label, dx, dy))
        # 面板里的 X/Y 要跟着变，但不必每按一下重建一遍控件
        job = getattr(self, "_panel_job", None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._panel_job = self.root.after(350, self._flush_panel)

    def _flush_panel(self):
        self._panel_job = None
        self.panel()

    def _on_escape(self, ev):
        if isinstance(ev.widget, (tk.Entry, tk.Text, ttk.Combobox)):
            return None
        if self.sel_more:
            self.sel_more = []
            self.redraw()
            self.panel()
            return "break"

    def _z_step(self, key, delta):
        """上移 / 下移一层（只管叠放顺序，不动层归属）。

        **界面上已经没有入口了**（右键里那两项撤掉），留着是给脚本和测试用。
        为什么撤：版面顺序本来就该由层决定 —— 图形先画、文字后画，
        白字压在色块上是因为它归 L3/L4，不是因为它在同一层里排得靠后。
        真需要压住谁，右键「归属层级」换一层就是了，比调叠放直观得多。
        """
        kind, idx = key
        if kind not in ("g", "t"):
            return
        arr = self.tpl["图形"] if kind == "g" else self.tpl["文字"]
        grp = [idx]
        if kind == "g" and arr[idx].get("type") in self.COMP_TYPES:
            j = self._comp_twin(idx)
            if j is not None:
                grp.append(j)
        grp.sort()
        if delta > 0 and grp[-1] >= len(arr) - 1:
            self.set_status("已经在最上层了")
            return
        if delta < 0 and grp[0] <= 0:
            self.set_status("已经在最下层了")
            return

        self.push_history()
        objs = [arr[i] for i in grp]
        rest = [x for i, x in enumerate(arr) if i not in grp]
        if delta > 0:
            # 跨过紧挨着的下一个元素，插到它后面
            pos = min(grp[-1] + 2 - len(grp), len(rest))
        else:
            pos = max(grp[0] - 1, 0)
        arr[:] = rest[:pos] + objs + rest[pos:]
        self.sel = (kind, arr.index(objs[0]))
        self.sel_more = []
        self.save()
        self.redraw()
        self.panel()
        self.set_status("上移一层" if delta > 0 else "下移一层")

    def _move_layer_to(self, key, new):
        """把这一块直接改到指定层（右键「归属层级」→ 四选一）。

        为什么不给「上一层 / 下一层」：挪一次要按好几下，每按一下还看不见
        落点，改完人还站在原地。层一共就四个，摊开来点更省事，也看得见
        它到底去哪了。

        什么时候用：分层是按规则判的，规则会看走眼（比如把某个固定字
        判成数据），也可能有人手动放错。落进 `layer` 字段，存模板，
        下次打开仍然是这一层。
        """
        if self.readonly:
            return self._ro_block()
        kind, idx = key
        if kind not in ("g", "t"):
            self.set_status("整组没有单独的层，要改请选中组里的单个元素")
            return
        if new == "L1":
            # L1 是整页底图（印厂那张空白 PDF），一张图，不装元素
            self.set_status("L1 是底图，只放整页图；元素放在 L2 / L3 / L4")
            return
        arr = self.tpl["图形"] if kind == "g" else self.tpl["文字"]
        cur = layers.of(arr[idx], "g" if kind == "g" else "t")
        if new == cur:
            self.set_status("它已经在 %s %s 了" % (new, layers.name(new)))
            return
        self.push_history()
        arr[idx]["layer"] = new
        # 手动定的层要钉住：内容是会变的（改字、补日期），每次保存都按内容
        # 重猜一遍，就会把手动纠正的结果改回去，右键换层等于白按。
        # 只有「和内容本来的判断不一致」才算手动钉 —— 一致的不用记。
        if new == layers.infer(arr[idx], "g" if kind == "g" else "t"):
            arr[idx].pop("层手动", None)
        else:
            arr[idx]["层手动"] = True
        # 顺应性卡「线 + 数」是一对：只挪一半，面板会显示成「一对拆开了」
        if kind == "g" and arr[idx].get("type") in self.COMP_TYPES:
            t = self._comp_twin(idx)
            if t is not None:
                arr[t]["layer"] = new
        self.save()
        # 改完就站到那一层上：否则「改到 L3」之后人还在 L4，只看见它消失，
        # 看不见它去了哪 —— 这正是「换层之后选不中」的一半原因。
        # 这里直接改 self.layer 不调 set_layer：set_layer 会顺手清掉选中，
        # 那刚挪完的东西又没了。
        if not self._visible(new):
            self.layer = new
            self._refresh_layer_btns()
        self.sel, self.sel_more = (kind, idx), []
        self.redraw()
        self.panel()
        self.set_status("已改到 %s %s" % (new, layers.name(new)))

    def _move_layer(self, key, delta):
        """按「上移 / 下移一层」改归属。

        菜单里已经不这么给了（改成直接选层），留着是给脚本和测试用的，
        也方便「一次挪一格」这种连续操作。
        """
        kind, idx = key
        if kind not in ("g", "t"):
            self.set_status("整组没有单独的层，要改请选中组里的单个元素")
            return
        arr = self.tpl["图形"] if kind == "g" else self.tpl["文字"]
        cur = layers.of(arr[idx], "g" if kind == "g" else "t")
        i = layers.ORDER.index(cur)
        j = i + delta
        if not (0 <= j < len(layers.ORDER)):
            self.set_status("已经在最%s层了" % ("上" if delta > 0 else "下"))
            return
        self._move_layer_to(key, layers.ORDER[j])

    def _draw_sel(self):
        items = self._sel_list()
        if not items:
            return
        for key in items:
            bb = self._bbox_of(*key)
            if not bb:
                continue
            x, y, w, h = bb
            pad = 1.5
            x0 = self.ox + x * self.scale - pad
            y0 = self.oy + y * self.scale - pad
            x1 = self.ox + (x + w) * self.scale + pad
            y1 = self.oy + (y + h) * self.scale + pad
            main = (key == tuple(self.sel))
            if main:
                # 静止和拖动都是虚线：拖动中还有原位置虚框、参考线、读数三层辅助，
                # 再压一个实线框反而糊在内容上，看不清到底贴没贴边。
                self.canvas.create_rectangle(x0, y0, x1, y1,
                                             outline=T.PRIMARY, dash=(4, 3), width=1,
                                             tags="selbox")
            else:
                # 多选时的其余元素：细一档、浅一档，主次分得开
                self.canvas.create_rectangle(x0, y0, x1, y1,
                                             outline=T.GUIDE, dash=(3, 3), width=1,
                                             tags="selbox")
        # 8 个缩放手柄：4 角 + 4 边中点，只在单选时给（多选缩放没有意义）
        hs = self._handles_px() if len(items) == 1 else {}
        if hs and not self.dragging:
            # 角画实心方块（等比），边画小一点的方块（单方向）——
            # 一眼就能分出「拉这里是等比、拉那里只改一边」
            for d, (hx, hy) in hs.items():
                corner = len(d) == 2
                r = 5 if corner else 4
                self.canvas.create_rectangle(
                    hx - r, hy - r, hx + r, hy + r,
                    fill=T.PRIMARY if corner else T.CARD,
                    outline=T.PRIMARY, width=1,
                    tags=("selbox", "handle"))

        # 表格：选中时把内部的列线 / 行线亮出来，端点给个小方点 ——
        # 「能拖」这件事得看得见，否则用户只知道能拖整张表。
        tix = self._table_drag_target()
        if tix is not None and not self.dragging:
            xs, ys, (tx0, ty0, tx1, ty1) = self._table_lines_px(tix)
            for x in xs:
                self.canvas.create_line(x, ty0, x, ty1, fill=T.PRIMARY,
                                        dash=(2, 3), width=1, tags="selbox")
                self.canvas.create_rectangle(x - 3, ty0 - 3, x + 3, ty0 + 3,
                                             fill=T.CARD, outline=T.PRIMARY,
                                             width=1, tags=("selbox", "tblh"))
            for y in ys:
                self.canvas.create_line(tx0, y, tx1, y, fill=T.PRIMARY,
                                        dash=(2, 3), width=1, tags="selbox")
                self.canvas.create_rectangle(tx0 - 3, y - 3, tx0 + 3, y + 3,
                                             fill=T.CARD, outline=T.PRIMARY,
                                             width=1, tags=("selbox", "tblh"))

    def _drag_bbox(self):
        """正在拖的那个元素的毫米包围盒（不含拖动增量 —— 增量已经写进数据了）。"""
        if not (self.drag and self.drag.get("kind") is not None):
            return None
        return self._bbox_of(self.drag["kind"], self.drag["idx"])

    def _draw_drag_aids(self):
        """拖动时压在画布上的三件辅助：参考线、浮动读数、对齐尺。

        为什么必须加：整张标签是一张位图，拖动时「元素真的挪了」，但周围的
        东西一动不动，眼睛缺少参照，很容易看不出到底挪了多少。四条参考线
        把这块的上下左右延伸到画布边缘，读数给出精确位移，眼睛就有锚了。
        """
        bb = self._drag_bbox()
        if not bb:
            return
        c = self.canvas
        x, y, w, h = bb
        x0, y0 = self.ox + x * self.scale, self.oy + y * self.scale
        x1, y1 = x0 + w * self.scale, y0 + h * self.scale
        cw = float(self.tpl["画布"]["宽"]) * self.scale
        ch = float(self.tpl["画布"]["高"]) * self.scale

        # 「从哪来」：原位置留一个虚线空框。有了它，位移多少、往哪个方向，
        # 不用看数字也能一眼认出来 —— 这就是之前「拖起来没法看」缺的那一环。
        if self.drag.get("x0") is not None:
            gx0 = self.ox + self.drag["x0"] * self.scale
            gy0 = self.oy + self.drag["y0"] * self.scale
            gx1 = gx0 + (self.drag.get("w0") or w) * self.scale
            gy1 = gy0 + (self.drag.get("h0") or h) * self.scale
            if abs(gx0 - x0) > 1 or abs(gy0 - y0) > 1:
                c.create_rectangle(gx0, gy0, gx1, gy1, outline="#b9c3d1",
                                   dash=(2, 3), width=1, tags="aid")

        # 参考线：把这块的上下左右拉到画布边缘。颜色比正文淡但不至于看不见 ——
        # 它的作用是「给眼睛一个坐标系」，不该抢内容。
        line = T.GUIDE
        for coords in ((self.ox, y0, x0, y0), (x1, y0, self.ox + cw, y0),
                       (x0, self.oy, x0, y0), (x0, y1, x0, self.oy + ch)):
            c.create_line(*coords, fill=line, dash=(4, 4), width=1, tags="aid")

        # 相减模式下给「正在看差异」的那一块圈个框：不然一屏红蓝，
        # 分不清哪块是刚挪的、哪块是老问题。
        if self.cmp_mode == "diff":
            box = self._focus_px()
            if box:
                c.create_rectangle(self.ox + box[0], self.oy + box[1],
                                   self.ox + box[2], self.oy + box[3],
                                   outline=line, dash=(3, 3), width=1, tags="aid")

        txt = "%s   %+.2f, %+.2f mm" % (self.drag_tag or "位移",
                                        self.drag_dx, self.drag_dy)
        d = self._drag_score()
        if getattr(self, "_hud_var", False):
            txt += "   · 变量区，不比参考图"
        fg = "#1f2b3d"
        if d is not None:
            # 趋势箭头就是「往哪挪更对」的直接回答：↓ 越来越对，↑ 走反了
            arrow = {(-1): " ↓", 1: " ↑"}.get(getattr(self, "_hud_trend", 0), "")
            txt += "   · %.1f%%%s" % (d * 100.0, arrow)
            fg = (T.TEXT if d < 0.16 else (T.AMBER if d < 0.26 else T.RED))
        # 浮动读数：白底小标签，不用深色块 —— 深底压在上面像一块膏药，
        # 本来就挡住了内容，还让整片变黑。白底 + 细边框只挡掉一层薄薄的字。
        tid = c.create_text(-4000, -4000, anchor="w", text=txt, fill=fg,
                            font=(UI_FONT, T.SZ_SMALL, "bold"), tags="aid")
        tb = c.bbox(tid)
        tw = (tb[2] - tb[0]) + 18
        th = (tb[3] - tb[1]) + 8
        # 放的位置有讲究：优先搁在块右边（同一行往往还有空白），
        # 右边放不下换左边，都放不下才压到上/下方 —— 密排标签上，
        # 压到上一行会把「上一行写了什么」也一并糊掉，比压在旁边干扰大得多。
        if x1 + 12 + tw <= self.ox + cw - 4:
            tx, ty = x1 + 12, (y0 + y1) / 2.0
        elif x0 - 12 - tw >= self.ox + 4:
            tx, ty = x0 - 12 - tw, (y0 + y1) / 2.0
        else:
            tx = min(max(self.ox + 4, x0), self.ox + cw - tw - 4)
            ty = y0 - th / 2 - 12
            if ty - th / 2 < self.oy + 6:
                ty = y1 + th / 2 + 12
        c.coords(tid, tx + 9, ty)
        box = c.create_rectangle(tx, ty - th / 2, tx + tw, ty + th / 2,
                                 fill="#ffffff", outline=T.BORDER, tags="aid")
        c.tag_raise(tid, box)

    def _sel_is_variable(self) -> bool:
        """当前拖的这块是不是「每规格都不一样」的数据块。"""
        if not self.drag or not self.tpl:
            return False
        kind, idx = self.drag.get("kind"), self.drag.get("idx")
        try:
            if kind == "t":
                return self.tpl["文字"][idx].get("layer") == "L4"
            if kind == "g":
                return self._glayer(self.tpl["图形"][idx]) == "L4"
            return kind == "gr"          # 四宫格整块都是数据
        except Exception:
            return False

    def _drag_score(self):
        """拖动中这一块和成品图的局部差异分。

        整张图算分对「挪半毫米」几乎没反应（实测一个 20mm 字块偏 5mm，整图
        只动 0.0002），所以只算这块附近；窗口跟着元素走，元素挪到哪量到哪。
        加了降噪之后分数才是单调的：偏移 5→0mm 严格递减。

        为了不掉帧，每 3 帧才重算一次 —— 数字跳得比画面慢一点，比画面卡住强。
        """
        if not self.tpl.get("参考图"):
            return None
        # 拖着的是数据块（批号 / 日期 / 条码 / 压力值）时，这一块本来就该和
        # 参考图不同 —— 报个 60% 的差异分只会让人以为放偏了，越调越错。
        # 这里直接不给分，读数条改成「变量区」。
        self._hud_var = self._sel_is_variable()
        if self._hud_var:
            return None
        self._hud_tick += 1
        if self._hud_tick % 3 and getattr(self, "_hud_val", None) is not None:
            return self._hud_val
        bb = self._drag_bbox()
        if not bb:
            return None
        try:
            w_mm = float(self.tpl["画布"]["宽"])
            im = render.render(self.tpl, self._fields(), 150)
            ref = self._ref_image(im.size)
            if ref is None:
                return None
            k = im.width / w_mm
            pad = 3.0                 # 窗口贴着块，别把周围表格算进来（会稀释信号）
            box = ((bb[0] - pad) * k, (bb[1] - pad) * k,
                   (bb[0] + bb[2] + pad) * k, (bb[1] + bb[3] + pad) * k)
            v = align.score_in(im, ref, box)
            prev = getattr(self, "_hud_val", None)
            if prev is not None:
                if v < prev - 0.002:
                    self._hud_trend = -1        # 在变好
                elif v > prev + 0.002:
                    self._hud_trend = 1         # 在变差
            self._hud_val = v
            return v
        except Exception:
            return None

    def _key_layer(self, key):
        """(kind, idx) → 该元素的层归属。"""
        kind, idx = key
        if kind == "t":
            return layers.of(self.tpl["文字"][idx], "t")
        if kind == "g":
            return self._glayer(self.tpl["图形"][idx])
        return "L2"                       # "gr" 组

    # ────────────────────────── 鼠标 ──────────────────────────
    def _cands(self, layer=None):
        """能点中、也能框选的东西。

        判据只有一条：**元素自己的 `layer` 说的是这一层**，不看它是文字还是图形。
        以前这里是按类型分着写的（L2 只找图形、L3 只找文字、只有 L4 才认条码），
        于是「手动把一块挪到别的层」会把它变成看得见点不着的鬼：画得出来，
        点不上，框选也框不到 —— 换层等于把它弄丢了。

        顺序照叠放的反序（文字最上、图形最下），命中时取第一个中的。

        「只看本层」时只返回本层元素；「本层及以下」和「全部」时返回
        **所有可见层**的元素（点击时 on_click 会自动跳到所属层）。
        """
        if layer is not None:
            lays = [layer]
        elif self.view_mode == "only":
            lays = [self.layer]
        else:
            # all / upTo：收集所有可见层
            idx = [k for k, _ in LAYERS].index(self.layer)
            if self.view_mode == "all":
                lays = [k for k, _ in LAYERS]
            else:  # upTo
                lays = [k for k, _ in LAYERS[:idx + 1]]
        out = []
        texts = self.tpl.get("文字") or []
        for i in range(len(texts) - 1, -1, -1):
            if layers.of(texts[i], "t") in lays:
                out.append(("t", i))
        if "L2" in lays:                # 组是整块图形，只挂在图形层
            for i in range(len(self.tpl.get("组") or []) - 1, -1, -1):
                out.append(("gr", i))
        for lay in lays:
            out += [("g", i) for i in reversed(self._glayer_idx(lay))]
        return out

    def _hit(self, px, py):
        """按当前层找命中的元素（只有当前层能被选中）。"""
        if not self.tpl:
            return None
        mx = (px - self.ox) / self.scale
        my = (py - self.oy) / self.scale
        for key in self._cands():
            bb = self._bbox_of(*key)
            if not bb:
                continue
            # 文字给 1mm 余量：一个「Ø」或「.」的墨迹只有零点几毫米，
            # 按精确墨迹做命中区，细字几乎点不中。图形本来就是实的，不给。
            pad = 1.0 if key[0] == "t" else 0.0
            if (bb[0] - pad <= mx <= bb[0] + bb[2] + pad
                    and bb[1] - pad <= my <= bb[1] + bb[3] + pad):
                return key
        return None

    def on_click(self, ev, mod=None):
        """按下鼠标。mod=True 表示按着 Ctrl / ⌘（多选 + 拖动复制）。

        mod 由绑定的 <Control-Button-1> / <Command-Button-1> 直接给，
        不靠 ev.state 的位 —— macOS 上 Command 停在哪一位跟 Tk 版本有关。
        """
        if not self.tpl:
            return
        if self.readonly:
            return self._ro_block()
        self.canvas.focus_set()    # Windows 上画布不拿焦点，Delete/方向键会失灵
        self.drag = None          # 每次按下先清，避免上一次没收到 release 时残留
        # 空格按住 + 左键拖 = 平移画布（放大之后挪视野；Figma/Illustrator 同款）
        if self._space_down:
            self.drag = {"mode": "pan", "sx": ev.x, "sy": ev.y,
                         "px": self.pan_x, "py": self.pan_y}
            self.canvas.config(cursor="fleur")
            return
        if mod is None:
            # 退路：万一是脚本 / 别的平台直接调进来，看状态位（Ctrl + Mod1）
            mod = self._mod_down(ev)
        hit = self._hit(ev.x, ev.y)

        # 「本层及以下 / 全部」视图下点到别的层的元素 → 自动跳到那层再选中。
        # 「只看本层」时 _hit 只找本层元素，不会走到这里。
        if hit:
            elayer = self._key_layer(hit)
            if elayer != self.layer:
                self.layer = elayer
                self._refresh_layer_btns()

        if hit and mod:
            # Ctrl / ⌘ + 点：松手时是「加选 / 减选」，拖起来是「复制一份」。
            # 按下这一下还不能定案 —— 先把它算进选区（眼睛要有反馈），
            # 真动了 3px 才复制（见 on_drag 的 copy 分支），没动就成了减选。
            key = tuple(hit)
            was = key in [tuple(k) for k in self._sel_list()]
            if not was:
                if not self.sel:
                    self.sel, self.sel_more = key, []
                else:
                    self.sel_more = ([tuple(k) for k in (self.sel_more or [])]
                                     + [key])
            self.redraw()
            self.panel()
            self.drag = {"mode": "copy", "sx": ev.x, "sy": ev.y,
                         "kind": hit[0], "idx": hit[1], "moved": False,
                         "toggle": key if was else None}
            self.set_status("已选 %d 项 · 按住拖动 = 复制一份"
                            % len(self._sel_list()))
            return

        # 表格：按住 Alt 再按列线 / 行线 = 调列宽 / 行高。
        # 为什么默认关掉：普通点击下这两条线离格子太近，想选中一行、
        # 想拖表格，动不动就抓成「改列宽」，一歪整张表就废了。
        # 现在改内容一律进「编辑表格」窗口，画布上这套留作应急（Alt+拖）。
        tl = self._hit_table_line(ev.x, ev.y) if self._alt_down(ev) else None
        if tl:
            axis, i = tl
            b = self.tpl["文字"][self.sel[1]]
            self.drag = {"mode": "tcol" if axis == "col" else "trow",
                         "idx": self.sel[1], "i": i,
                         "sx": ev.x, "sy": ev.y,
                         "cols0": list(b.get("cols") or []),
                         "rows0": list(b.get("rows") or []),
                         "moved": False}
            self.canvas.config(cursor=("sb_h_double_arrow" if axis == "col"
                                       else "sb_v_double_arrow"))
            return

        # 单选状态下按在手柄上 = 缩放（8 个方向：4 角等比、4 边单方向）
        _dir = (self._hit_handle(ev.x, ev.y)
                if len(self._sel_list()) == 1 and self.sel else None)
        if _dir:
            bb = self._bbox_of(*self.sel)
            self.drag = {"mode": "resize", "dir": _dir,
                         "kind": self.sel[0], "idx": self.sel[1],
                         "sx": ev.x, "sy": ev.y,
                         "x0": bb[0], "y0": bb[1], "w0": bb[2], "h0": bb[3],
                         "cfg0": copy.deepcopy(self._elem_cfg(*self.sel)),
                         "moved": False}
            self.canvas.config(cursor=self.DIR_CURSOR.get(_dir, "sizing"))
            return

        cur = self._sel_list()
        if hit and tuple(hit) in cur and len(cur) > 1:
            # 点在已选中的那一块上：保持整组，一起拖
            self.sel = tuple(hit)
        elif not hit and mod:
            pass                      # ⌘/Ctrl 框选：保留已选，框到的加进来
        else:
            self.sel = tuple(hit) if hit else None
            self.sel_more = []

        if not hit:
            # 空白处按下并拖动 = 框选（BarTender 里最顺手的多选方式）
            self.drag = {"mode": "band", "sx": ev.x, "sy": ev.y,
                         "ex": ev.x, "ey": ev.y, "moved": False,
                         "add": bool(mod)}
            self.redraw()
            self.panel()
            return

        kind, idx = hit
        bb = self._bbox_of(kind, idx)
        self.drag = {"kind": kind, "idx": idx, "sx": ev.x, "sy": ev.y,
                     "lx": ev.x, "ly": ev.y,
                     "x0": bb[0], "y0": bb[1],
                     "w0": bb[2], "h0": bb[3], "moved": False,
                     "items": [(k, self._snap_pos(k)) for k in self._sel_list()]}
        self.redraw()
        self.panel()

    def on_hover(self, ev):
        """悬停到手柄上就把光标换成那个方向的缩放箭头。

        没有这一步，8 个手柄看起来只是 8 个点，谁也不知道拉哪条边只改宽。
        """
        if self.drag:
            return
        cur = ""
        tl = self._hit_table_line(ev.x, ev.y) if self._alt_down(ev) else None
        if tl:
            # 表格的列线 / 行线：光标直接告诉人「这条能拖」
            cur = ("sb_h_double_arrow" if tl[0] == "col" else "sb_v_double_arrow")
        elif len(self._sel_list()) == 1 and self.sel:
            d = self._hit_handle(ev.x, ev.y)
            if d:
                cur = self.DIR_CURSOR.get(d, "sizing")
        if self.canvas.cget("cursor") != cur:
            try:
                self.canvas.config(cursor=cur)
            except tk.TclError:
                pass

    def on_drag(self, ev):
        if not self.drag or not self.tpl:
            return
        if self.readonly and self.drag.get("mode") != "pan":
            return
        if self.drag.get("mode") == "pan":
            self.pan_x = self.drag["px"] + (ev.x - self.drag["sx"])
            self.pan_y = self.drag["py"] + (ev.y - self.drag["sy"])
            self.redraw()
            return
        if self.drag.get("mode") in ("tcol", "trow"):
            self._drag_table_line(ev)
            return
        # 拖手柄：改大小。拉角等比，拉左右边只改宽、拉上下边只改高
        # （图形改宽高、文字垂直改字高 / 水平改横向缩放、条码改模块宽）
        if self.drag.get("mode") == "resize":
            if not self.drag.get("moved"):
                self.drag["moved"] = True
                self.push_history()
            kx, ky = self._resize_dirs(ev)
            ok, msg = self._apply_resize(kx, ky)
            self.redraw()
            if msg:
                self.set_status(msg)
            return

        if self.drag.get("mode") == "band":
            self.drag["moved"] = True
            self.drag["ex"], self.drag["ey"] = ev.x, ev.y
            self.redraw()
            # 框选框要**真透明**（看得见后面的字）：Tk 的 canvas 没有 alpha，
            # 浅蓝实底看着像半透明其实把内容盖住了。stipple 是真镂空 ——
            # 只有 1/4 的像素点落色，其余透出底下的字，两个平台一样。
            self.canvas.create_rectangle(
                self.drag["sx"], self.drag["sy"], ev.x, ev.y,
                outline=T.PRIMARY, dash=(3, 3), width=1,
                fill=T.PRIMARY, stipple="gray25", tags="band")
            return

        if self.drag.get("mode") == "copy":
            # Ctrl/⌘ + 拖动 = 复制一份再拖（Illustrator/Figma 同款手感）。
            # 一开始按下时还不确定是「点一下加选」还是「拖出去复制」，
            # 所以要等真的动了 3px 以上才复制 —— 否则每次加选都会多出一个副本。
            if abs(ev.x - self.drag["sx"]) + abs(ev.y - self.drag["sy"]) <= 3:
                return
            if not self._mod_down(ev):
                # 修饰键途中松开了：这次不算复制，改回普通移动
                keys = self._sel_list()
                k0, i0 = self.drag["kind"], self.drag["idx"]
                bb = self._bbox_of(k0, i0) or (0.0, 0.0, 1.0, 1.0)
                self.drag = {"kind": k0, "idx": i0,
                             "sx": self.drag["sx"], "sy": self.drag["sy"],
                             "x0": bb[0], "y0": bb[1], "w0": bb[2], "h0": bb[3],
                             "moved": False,
                             "items": [(k, self._snap_pos(k)) for k in keys]}
                self.set_status("没按 ⌘/Ctrl —— 只移动，不复制")
            else:
                self.push_history()             # 复制之前记历史，撤销回到「没复制」
                keys = self._dup_selection(off=0.0)
                if not keys:
                    self.drag = None
                    return
                self.sel, self.sel_more = keys[0], keys[1:]
                bb = self._bbox_of(*self.sel) or (0.0, 0.0, 1.0, 1.0)
                self.drag = {"kind": self.sel[0], "idx": self.sel[1],
                             "sx": self.drag["sx"], "sy": self.drag["sy"],
                             "x0": bb[0], "y0": bb[1], "w0": bb[2], "h0": bb[3],
                             "moved": True,
                             "items": [(k, self._snap_pos(k))
                                       for k in self._sel_list()]}
                self.dragging = True
                self._hud_tick, self._hud_val, self._hud_trend = 0, None, 0
                n = len(self._sel_list())
                self.drag_tag = ("%d 项副本" % n) if n > 1 else \
                    (self._elem_label(self.sel[0], self.sel[1]) + " 副本")
                self.canvas.config(cursor="fleur")
                self.set_status("已复制一份，拖到位松手")
                # 不 return：接着走下面的普通拖动，把这一份挪到鼠标下面

        dx = (ev.x - self.drag["sx"]) / self.scale
        dy = (ev.y - self.drag["sy"]) / self.scale
        if not self.drag.get("moved"):
            if abs(dx) + abs(dy) <= 0.15:
                return                     # 手抖不当事
            self.drag["moved"] = True
            self.push_history()            # 真正开始移动时才记历史
            self.dragging = True
            self._hud_tick = 0
            self._hud_val = None
            self._hud_trend = 0
            k0, i0 = self.drag.get("kind"), self.drag.get("idx")
            self.drag_tag = self._elem_label(k0, i0)
            if len(self.drag.get("items") or []) > 1:
                self.drag_tag = "%d 项" % len(self.drag["items"])
            self.canvas.config(cursor="fleur")

        # 软吸附：位置先过一道「往叠影墨迹上靠」的引力，再落到元素上
        if self.snap_on:
            dx, dy = self._snap_nudge(dx, dy)

        # 多选时整组跟着走：每一块都按自己按下时的位置 + 同一个位移重新定位
        for key, snap in (self.drag.get("items")
                          or [((self.drag["kind"], self.drag["idx"]),
                               self._snap_pos((self.drag["kind"], self.drag["idx"])))]):
            self._set_pos(key, snap, dx, dy)

        self.drag_dx, self.drag_dy = dx, dy
        # 真的按新坐标重画，而不是拖一块「截图」走。
        # 以前是把选中区域 crop 下来跟着鼠标平移，那块膏药盖住半张标签、
        # 其他元素纹丝不动 —— 眼睛根本没有参照，看着完全无法判断位置。
        # 现在渲染一次只要几毫秒（字形和素材都缓存了），每帧重画完全跟得上，
        # 移动的就是元素本身。参考线和浮动读数在 redraw 里一起画。
        self.redraw()
        self.set_status("%s  %+.2f, %+.2f mm" % (self.drag_tag, dx, dy))

    def _elem_label(self, kind, idx):
        """拖动标签上显示的名字。"""
        try:
            if kind == "gr":
                grp = self.tpl["组"][idx]
                return grp.get("name") or "重复组"
            if kind == "g":
                g = self.tpl["图形"][idx]
                return g.get("name") or g.get("type") or "图形"
            b = self.tpl["文字"][idx]
            t = str(b.get("t") or b.get("字段") or "").replace("\n", " ")
            return (b.get("name") or t[:14] or "文字")
        except Exception:
            return "元素"

    def on_release(self, _ev):
        self.canvas.config(cursor="")
        band = self.drag if (self.drag and self.drag.get("mode") == "band") else None
        if band and band.get("moved"):
            self._select_in_band(band["sx"], band["sy"],
                                 band.get("ex", band["sx"]), band.get("ey", band["sy"]),
                                 add=bool(band.get("add")))
            self.dragging = False
            self.drag = None
            return
        # Ctrl/⌘ 按了一下没拖：这时才算「减选」（拖了的话已经复制完了）
        tog = (self.drag.get("toggle")
               if self.drag and self.drag.get("mode") == "copy"
               and not self.drag.get("moved") else None)
        if tog:
            self._sel_toggle(tog)
            self.drag = None
            self.redraw()
            self.panel()
            n = len(self._sel_list())
            self.set_status(("已取消该项（还剩 %d 项）" % n) if n else "已取消选择")
            return
        was = bool(self.drag and self.drag.get("moved"))
        if was:
            self._snap_settle()            # 已经很贴近叠影的，松手时落到位
        self.dragging = False
        self.drag = None
        if was:
            self.save()
            self.redraw()
            self.panel()

    def _band_cands(self):
        """当前层能被框选的元素。和 _hit 共用同一份口径，否则框得到却拖不动。"""
        return self._cands()

    def _select_in_band(self, x0, y0, x1, y1, add=False):
        mx0 = (min(x0, x1) - self.ox) / self.scale
        mx1 = (max(x0, x1) - self.ox) / self.scale
        my0 = (min(y0, y1) - self.oy) / self.scale
        my1 = (max(y0, y1) - self.oy) / self.scale
        got = []
        for key in self._band_cands():
            bb = self._bbox_of(*key)
            if not bb:
                continue
            if bb[0] <= mx1 and bb[0] + bb[2] >= mx0 \
                    and bb[1] <= my1 and bb[1] + bb[3] >= my0:
                got.append(key)
        if add:
            # ⌘/Ctrl + 框选：框到的是「再加一批」，不是「换一批」
            keep = [tuple(k) for k in self._sel_list()]
            got = keep + [k for k in got if tuple(k) not in keep]
        self.sel = got[0] if got else None
        self.sel_more = got[1:]
        self.redraw()
        self.panel()
        self.set_status(("框选 %d 项" % len(got)) if got else "空选")

    def _alt_down(self, ev) -> bool:
        """这次事件里 Alt / Option 是不是按着的（表格拖线用）。"""
        return bool(getattr(ev, "state", 0) & ALT_MASK)

    def _mod_down(self, ev) -> bool:
        """这次事件里 ⌘ / Ctrl 是不是按着的。

        只认一个多选键，所以两位都算「按着」—— 用户不用记到底该按哪个。
        """
        return bool(getattr(ev, "state", 0) & MOD_MASK)

    def on_wheel(self, ev):
        """滚轮 / 双指。**两端至少共享一条缩放路径：Ctrl(⌘)+滚轮**。

        P1-2 之前的平台差异（用户要求补平）：

            ┌────────────┬──────────────────────┬──────────────────────┐
            │            │  Mac                 │  Windows             │
            ├────────────┼──────────────────────┼──────────────────────┤
            │ 竖向平移    │ 双指上下滑 ✅         │ 右键拖 ✅             │
            │ 横向平移    │ ❌ 没有（本次补）     │ 右键拖 ✅             │
            │ 缩放        │ 只有 −/＋ 按钮        │ 滚轮（未选中时）      │
            │ Ctrl+滚轮   │ ❌ 没有（本次补）     │ ❌ 没有（本次补）     │
            └────────────┴──────────────────────┴──────────────────────┘

        补完后的统一规则（两端一致，这是重点）：

            Ctrl（Mac 上 ⌘ 也算）+ 滚轮  →  **缩放**（锚定鼠标点）
            Shift + 滚轮                →  **横向平移**
            普通滚轮 / 双指              →  平台各按习惯：
                                            Mac  上下平移，左右横扫也平移
                                            Win  未选中时缩放，选中时不动

        为什么 Ctrl+滚轮要两端都做：
        这是用户从浏览器/编辑器带来的肌肉记忆，Mac 和 Windows 上都是这个手势。
        有了它，"怎么缩放"这件事就不用分平台解释了 —— 一条路走到底。
        """
        if not self.tpl:
            return
        d = getattr(ev, "delta", 0) or 0
        num = getattr(ev, "num", 0)
        st = getattr(ev, "state", 0) or 0

        # ── 方向与步长 ──────────────────────────────────────────
        # MouseWheel：delta 有正负；X11：Button-4/5
        if d:
            step = 1 if d > 0 else -1
        elif num in (4, 5):
            step = 1 if num == 4 else -1
        else:
            step = 0
        if not step:
            return

        # ── ① Ctrl / ⌘ + 滚轮 = 缩放（两端统一）──────────────
        # Control=0x4；Mac 的 Command 落 Mod1=0x8。两位都认 ——
        # 多认一位不会错，总比在某些 Tk 版本上少认一位强。
        if st & (0x0004 | 0x0008):
            now = time.monotonic()
            if now - self._last_zoom < 0.04:
                return
            self._last_zoom = now
            self._zoom_at(1.12 if step > 0 else 1 / 1.12,
                          getattr(ev, "x", None), getattr(ev, "y", None))
            self.set_status("视图 %d%%　·　Ctrl(⌘)+滚轮缩放"
                            % round(self.zoom * 100))
            return

        # ── ② Shift + 滚轮 = 横向平移（两端统一）──────────────
        if st & 0x0001:
            self.pan_x += step * 24
            self.redraw()
            self.set_status("视图 %d%%　·　Shift+滚轮横向平移"
                            % round(self.zoom * 100))
            return

        # ── ③ Mac：双指/滚轮 = 平移（含横向）──────────────────
        if IS_MAC:
            # macOS 触控板的两指滑动会同时给 delta 和（有时）x/y 位移。
            # 垂直优先；纯横向滑动（横向分量大）就走 pan_x。
            hx = abs(getattr(ev, "x", 0) or 0)
            hy = abs(getattr(ev, "y", 0) or 0)
            if hx > hy:
                self.pan_x += step * 24
            else:
                self.pan_y += step * 24
            # 双指滑多少就挪多少：预览图本身没变，redraw 只是重贴那张缓存，
            # 不会每帧重渲染，所以这里不节流也不会卡。
            self.redraw()
            self.set_status("视图 %d%%　·　双指平移 / 右键拖动，Ctrl(⌘)+滚轮缩放"
                            % round(self.zoom * 100))
            return

        # ── ④ Windows：普通滚轮 = 缩放（原来就有的习惯）────────
        if self._sel_list():
            return                        # 选了东西：滚轮不缩放、也不改大小
        now = time.monotonic()
        if now - self._last_zoom < 0.04:
            return
        self._last_zoom = now
        self._zoom_at(1.12 if step > 0 else 1 / 1.12,
                      getattr(ev, "x", None), getattr(ev, "y", None))
        self.set_status("视图 %d%%　·　右键拖动平移，Ctrl+滚轮缩放"
                        % round(self.zoom * 100))

    def _zoom_step(self, factor):
        """缩放按钮：以画布中心为锚点缩放（1.25 = 放大 25%）。"""
        if not self.tpl:
            return
        self._zoom_at(factor)
        self.set_status("视图 %d%%" % round(self.zoom * 100))

    def _zoom_at(self, factor, px=None, py=None):
        """缩放视图；给了 (px, py) 就以那个屏幕点为锚点（滚到哪放哪）。"""
        if not self.tpl:
            return
        new = max(0.25, min(6.0, self.zoom * factor))
        if abs(new - self.zoom) < 1e-12:
            return
        if not hasattr(self, "ox"):
            self.redraw()
        c = self.canvas
        if px is None or py is None:
            px, py = c.winfo_width() / 2.0, c.winfo_height() / 2.0
        # 锚点下的模型坐标：缩放前后都钉在同一个屏幕点，画面才不会飘走
        mx = (px - self.ox) / self.scale
        my = (py - self.oy) / self.scale
        self.zoom = new
        self.redraw()
        self.pan_x += px - (self.ox + mx * self.scale)
        self.pan_y += py - (self.oy + my * self.scale)
        self.redraw()
        self._sync_zoom_lbl()

    def _sync_zoom_lbl(self):
        """把缩放百分比同步到浮动按钮上的小标签。"""
        try:
            self._zoom_lbl.config(text="%d%%" % round(self.zoom * 100))
        except Exception:
            pass

    # ────────────────────────── 中键平移 ──────────────────────────
    def on_mid_down(self, ev):
        """中键只剩一个用途：接住 macOS 的 ⌃+点击。

        macOS 会把 ⌃+左键报成 Button-2（系统的右键模拟），所以带 Control
        的 Button-2 一律当「Ctrl+点」交给 on_click —— 否则 Mac 上按 Ctrl
        多选会变成拖画布，用户会以为多选键坏了。
        中键本身不再平移（两平台的平移都是右键拖，见 on_right_down）。
        """
        if getattr(ev, "state", 0) & 0x0004:
            self._mid_as_click = True       # 后续的 Motion/Release 也走左键那一套
            self.on_click(ev, mod=True)
            return
        self._mid_as_click = False
        self.mid_drag = None

    def on_mid_drag(self, ev):
        if getattr(self, "_mid_as_click", False):
            self.on_drag(ev)                # ⌃+拖动 = Ctrl+拖动（复制）
            return

    def on_mid_up(self, ev):
        if getattr(self, "_mid_as_click", False):
            self._mid_as_click = False
            self.on_release(ev)
            return
        self.mid_drag = None

    def _space_press(self, ev=None):
        """空格按下 = 进入平移模式（正在输入框里打字时不抢空格）。"""
        if self.readonly:
            return self._ro_block()
        w = self.root.focus_get()
        if isinstance(w, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
            return
        self._space_down = True
        if self.tpl:
            try:
                self.canvas.config(cursor="fleur")
            except tk.TclError:
                pass

    def _space_release(self, ev=None):
        self._space_down = False
        if not self.drag or self.drag.get("mode") != "pan":
            try:
                self.canvas.config(cursor="")
            except tk.TclError:
                pass

    def on_right_down(self, ev):
        """右键按下：拖了就是平移，没拖（原地松开）就是弹菜单。"""
        if not self.tpl:
            return
        self.canvas.focus_set()
        self.drag = {"mode": "rpan", "sx": ev.x, "sy": ev.y,
                     "px": self.pan_x, "py": self.pan_y, "moved": False}

    def on_right_drag(self, ev):
        if not self.drag or self.drag.get("mode") != "rpan":
            return
        if not self.drag.get("moved"):
            if abs(ev.x - self.drag["sx"]) + abs(ev.y - self.drag["sy"]) > 3:
                self.drag["moved"] = True
                self.canvas.config(cursor="fleur")
            else:
                return
        self.pan_x = self.drag["px"] + (ev.x - self.drag["sx"])
        self.pan_y = self.drag["py"] + (ev.y - self.drag["sy"])
        self.redraw()

    def on_right_up(self, ev):
        moved = bool(self.drag and self.drag.get("mode") == "rpan"
                     and self.drag.get("moved"))
        self.drag = None
        try:
            self.canvas.config(cursor="")
        except tk.TclError:
            pass
        if moved:
            return                       # 拖过 = 平移完了，不弹菜单
        self.on_right_click(ev)          # 原地松开 = 按原逻辑选中 + 弹菜单

    def on_right_click(self, ev):
        """右键：先选中，再把菜单弹出来。菜单内容在 `_make_menu()`。"""
        if not self.tpl:
            return
        hit = self._hit(ev.x, ev.y)
        cur = self._sel_list()
        if hit and tuple(hit) in cur and len(cur) > 1:
            pass                          # 点的是已选中的一块 → 保持多选
        elif hit:
            self.sel, self.sel_more = tuple(hit), []
        if not self.sel:
            return
        self.redraw()
        self.panel()

        m = self._make_menu()
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def _make_menu(self):
        """右键菜单：复制 / 叠放 / 换层 / 删除。多选时换成对齐那一套。

        单抽出来是为了能测：菜单项写了什么、点了会怎样，不用真弹出来
        （tk_popup 会 grab 住事件循环，脚本里一弹就卡）。
        """
        items = self._sel_list()
        m = tk.Menu(self.root, tearoff=0)
        if len(items) > 1:
            am = tk.Menu(m, tearoff=0)
            for how, name in self.ALIGNS:
                am.add_command(label=name + "对齐",
                               command=lambda h=how: self._align_selected(h))
            am.add_separator()
            am.add_command(label="横向等距",
                           command=lambda: self._distribute("h"))
            am.add_command(label="纵向等距",
                           command=lambda: self._distribute("v"))
            m.add_cascade(label="对齐（%d 项）" % len(items), menu=am)
            m.add_separator()
            m.add_command(label="复制这 %d 项" % len(items), command=self.copy_selected)
            m.add_command(label="删除这 %d 项" % len(items), command=self.del_selected)
            m.add_command(label="取消多选", command=self._clear_multi)
        else:
            kind, idx = self.sel
            if kind == "g":
                name = self.tpl["图形"][idx].get("name", "图形")
            elif kind == "gr":
                name = self._group_name(self.tpl["组"][idx])
            else:
                name = self.tpl["文字"][idx].get("name", "文字块")
            m.add_command(label="复制「%s」" % name, command=self.copy_selected)
            if kind == "g" and self.tpl["图形"][idx].get("type") in self.COMP_TYPES:
                # 顺应性卡：右键第一项就是专门编辑空间（用户明确要求）
                m.add_command(label="✎ 编辑顺应性卡…",
                              command=lambda i=idx: self.open_comp_editor(i))
                m.add_separator()
            if kind == "t" and self._is_table(idx):
                # 表格最常干的事就是改格子 —— 放第一项，右键就能进
                m.add_command(label="✎ 编辑表格…",
                              command=lambda i=idx: self.open_table_editor(i))
            m.add_separator()
            if kind in ("g", "t"):
                # 归属层级摊开来选：L1~L4 一共四个，直接点目标层。
                # 以前这里是「上一层 / 下一层」两个命令，从 L2 挪到 L4 要连按
                # 两次、每次都不知道落在哪；旁边还有一组「上移/下移一层（叠放）」
                # 只管压盖顺序 —— 两套「上下」摆在一起，谁也说不清差在哪。
                # 版面顺序本来就由层决定（图形先画、文字后画），需要压住谁就换层，
                # 所以叠放那两项撤掉了。
                lay = layers.of(self.tpl["图形" if kind == "g" else "文字"][idx],
                                "g" if kind == "g" else "t")
                sub = tk.Menu(m, tearoff=0)
                # L1 不在选项里：L1 是整整一页底图（一张图，不能逐字改），
                # 不是放元素的地方。元素只在 L2/L3/L4 之间挪。
                for code in [k for k in layers.ORDER if k != "L1"]:
                    sub.add_command(
                        label="%s %s" % (code, layers.name(code))
                        + ("　← 现在" if code == lay else ""),
                        command=lambda c=code: self._move_layer_to(self.sel, c))
                m.add_cascade(label="归属层级（现在 %s %s）"
                              % (lay, layers.name(lay)), menu=sub)
                # 链接到数据列：右键就能改，不用先回面板找那个下拉。
                # 只有在导入了数据表、且选中的是文字块时才出现。
                if kind == "t" and self.data.get("recs"):
                    cur_col = None
                    used = PH.used_keys(str(self.tpl["文字"][idx].get("t") or ""))
                    if used:
                        cur_col = used[0]
                    sub2 = tk.Menu(m, tearoff=0)
                    sub2.add_command(
                        label="（不链接）" + ("　← 现在" if not cur_col else ""),
                        command=lambda i=idx: self._bind_text(i, "（不链接）"))
                    sub2.add_separator()
                    for col in self.data.get("head") or []:
                        sub2.add_command(
                            label=str(col) + ("　← 现在" if str(col) == cur_col else ""),
                            command=lambda c=col, i=idx: self._bind_text(i, str(c)))
                    m.add_cascade(label="链接到数据列", menu=sub2)
                m.add_separator()
            m.add_command(label="删除「%s」" % name, command=self.del_selected)
        return m

    def _clear_multi(self):
        self.sel_more = []
        self.redraw()
        self.panel()

    def _dup_one(self, kind, idx, off=4.0):
        """复制一个元素（不保存、不重绘），返回新元素的 key。

        off：副本相对原件错开多少毫米。Ctrl/⌘+拖动 走 off=0（副本原地生成，
        再跟着鼠标走），菜单里的「复制」走默认的 4mm 错开。
        """
        off = float(off)
        if kind == "g":
            src = self.tpl["图形"][idx]
            if src.get("type") in self.COMP_TYPES:
                # 顺应性卡是「线 + 数」一对：只复制一半，副本就只剩个空表
                twin = self._comp_twin(idx)
                pair_key = "顺应性卡#%d" % len(self.tpl["图形"])
                base = len(self.tpl["图形"])
                for j in ([idx] + ([twin] if twin is not None else [])):
                    gg = json.loads(json.dumps(self.tpl["图形"][j]))
                    self._comp_set_geo(gg, self._comp_geo(gg), off, off)
                    gg["配对"] = pair_key
                    gg["name"] = (gg.get("name") or "顺应性卡") + " 副本"
                    self.tpl["图形"].append(gg)
                return ("g", base)
            g = json.loads(json.dumps(src))
            if "x" in g:
                g["x"] = round(g["x"] + off, 2)
                g["y"] = round(g["y"] + off, 2)
            g["name"] = (g.get("name") or "图形") + " 副本"
            self.tpl["图形"].append(g)
            return ("g", len(self.tpl["图形"]) - 1)

        b = json.loads(json.dumps(self.tpl["文字"][idx]))
        if b.get("lines"):
            for ln in b["lines"]:
                ln["x"] = round(ln["x"] + off, 2)
                ln["y"] = round(ln["y"] + off, 2)
        elif "x" in b:
            b["x"] = round(float(b.get("x", 0)) + off, 2)
            b["y"] = round(float(b.get("y", 0)) + off, 2)
        b["k"] = "%s_copy%d" % (b.get("k") or "t", len(self.tpl["文字"]))
        b["name"] = (b.get("name") or "文字块") + " 副本"
        self.tpl["文字"].append(b)
        return ("t", len(self.tpl["文字"]) - 1)

    def _dup_selection(self, off=4.0):
        """把当前选中的元素各复制一份（不存盘、不重绘），返回新 keys。"""
        keys = [k for k in self._sel_list() if k[0] in ("g", "t")]
        if not keys or not self.tpl:
            return []
        new, handled = [], set()
        for k in keys:
            if k in handled:
                continue
            got = self._dup_one(*k, off=off)
            if not got:
                continue
            new.append(got)
            handled.add(k)
            if k[0] == "g" and self.tpl["图形"][k[1]].get("type") in self.COMP_TYPES:
                j = self._comp_twin(k[1])
                if j is not None:
                    handled.add(("g", j))
        return new

    def copy_selected(self):
        """原地复制，往右下错开 4mm；多选就整组复制。"""
        if not self.tpl:
            return
        self.push_history()
        new = self._dup_selection(off=4.0)
        if new:
            self.sel, self.sel_more = new[0], new[1:]
        self.save()
        self.redraw()
        self.panel()
        self.set_status("已复制 %d 个（错开 4mm）" % len(new))

    def del_selected(self):
        """删除选中的元素（图形 / 文字块，支持多选）。"""
        keys = [k for k in self._sel_list() if k[0] in ("g", "t")]
        if not keys or not self.tpl:
            return
        self.push_history()
        gidx = {i for k, i in keys if k == "g"}
        # 顺应性卡的线删了、数还留着，就成了没有格子的浮字：成对删
        for i in list(gidx):
            if self.tpl["图形"][i].get("type") in self.COMP_TYPES:
                j = self._comp_twin(i)
                if j is not None:
                    gidx.add(j)
        for i in sorted(gidx, reverse=True):
            del self.tpl["图形"][i]
        for i in sorted({i for k, i in keys if k == "t"}, reverse=True):
            del self.tpl["文字"][i]
        self.sel, self.sel_more = None, []
        self.save()
        self.redraw()
        self.panel()
        self.set_status("已删除 %d 个" % len(keys))

    def _on_delete_key(self, ev):
        """Delete 键也能删（在输入框里时让输入框自己删字）。"""
        if isinstance(ev.widget, (tk.Entry, tk.Text)):
            return None
        if self.readonly:
            return self._ro_block()
        if self.sel:
            self.del_selected()
            return "break"

    def on_dblclick(self, ev):
        """双击元素 = 进右栏直接改；双击表格 = 开表格编辑窗口；空白 = 视图复位。"""
        if self.readonly:
            return self._ro_block()
        hit = self._hit(ev.x, ev.y)
        if hit is None:
            self.reset_view()
            self.set_status("视图已复位到 100%")
            return
        self.sel, self.sel_more = tuple(hit), []
        self.redraw()
        self.panel()
        if hit[0] == "t" and self._is_table(hit[1]):
            self.open_table_editor(hit[1])
            return
        self.root.after(60, self._focus_panel_text)

    def _focus_panel_text(self):
        """把焦点放进右栏第一个文字框（双击元素后直接能打字）。"""
        def find(w):
            for c in w.winfo_children():
                if isinstance(c, tk.Text):
                    return c
                got = find(c)
                if got is not None:
                    return got
            return None
        t = find(self.pnl)
        if t is not None:
            t.focus_set()
            t.mark_set("insert", "end-1c")

    def reset_view(self):
        """回到 100% 且居中（双击空白处 / 换模板时用）。"""
        self.zoom, self.pan_x, self.pan_y = 1.0, 0.0, 0.0
        self.redraw()
        self._sync_zoom_lbl()

    # ────────────────────────── 数据 ──────────────────────────
    def _restore_data_source(self) -> str:
        """模板里记过数据表路径就自动重读一次，返回给状态栏用的一句话。

        文件被移动/删掉就只提示，不报错 —— 用户重新导一遍即可。
        """
        if not self.tpl:
            return ""
        ds = self.tpl.get("数据源") or {}
        path = ds.get("path") or ""
        if not path:
            return ""
        # 存在模板文件夹里的用相对路径（跨平台、整个文件夹搬走也不断），
        # 从别处导入的仍是绝对路径。
        if not os.path.isabs(path):
            p2 = store.resolve_ref(self.tpl_name, path)
            path = p2 or os.path.join(paths.DATA, path)
        if not os.path.exists(path):
            return "上次的数据表不在了：%s（请重新导入）" % path
        try:
            head, recs, sheet = data_src.pick_records(path, ds.get("sheet") or None)
            cur = int(ds.get("cur", 0) or 0)
            cur = max(0, min(cur, len(recs) - 1))
            self.data = {"head": head, "recs": recs, "cur": cur,
                         "sheet": sheet, "path": path}
            return "已自动接回数据表：%s（%d 行）" % (os.path.basename(path), len(recs))
        except Exception as e:
            return "数据表读不了（%s），请重新导入" % e

    def _fields(self):
        """当前这一条记录，已经按模板的「数据映射」翻译好。

        以前这里直接返回原始行，列名是 `NOTE 03 球囊外径`，而模板占位符
        叫【球囊直径】—— 对不上，导入表也只填了个空。现在统一走 databind，
        一层翻译把 NOTE 列、带单位的值、`1.9F（0.63mm）`这种合串都拆好。
        """
        if not self.data["recs"]:
            return (self.tpl or {}).get("示例数据") or {}
        try:
            return databind.bind(self.tpl, self.data["recs"][self.data["cur"]])
        except Exception:
            return self.data["recs"][self.data["cur"]]

    def _bound_recs(self):
        """全部记录翻译好的一份，批量导出用。

        直接拿原始行去批量出图，出来的每张都是示例值 —— 字段没翻译过，
        看起来「批量成功」，其实印了一叠同样的小标签。
        """
        if not self.tpl:
            return []
        out = []
        for r in self.data["recs"]:
            try:
                out.append(databind.bind(self.tpl, r))
            except Exception:
                out.append(r)
        return out

    def _using_demo(self):
        """现在画面上填的是不是示例数据（不是真数据）。"""
        return not self.data.get("recs") and bool((self.tpl or {}).get("示例数据"))

    def import_data(self):
        if self.readonly:
            return self._ro_block()
        fp = filedialog.askopenfilename(
            title="导入数据表",
            filetypes=[("表格", "*.xlsx *.xlsm *.csv *.tsv *.json"), ("全部", "*.*")])
        if not fp:
            return
        try:
            # 标签框架.xlsx（产品参数信息）优先：列名本身就是语义，直接当绑定键
            if framework.is_framework(fp):
                head, recs, sheet = framework.load(fp)
                self.set_status("识别为标签框架：%s，%d 个成品" % (sheet, len(recs)))
            else:
                head, recs, sheet = data_src.pick_records(fp)
            # 数据表收进模板自己的 数据源/ 文件夹 —— 模板文件夹自包含，
            # 发给同事不用另外带 Excel。
            p = fp
            if self.tpl_name:
                try:
                    ref = store.put_asset(self.tpl_name, fp, "数据源")
                    p = os.path.join(store.tpl_dir(self.tpl_name), ref)
                except Exception:
                    pass
            self.data = {"head": head, "recs": recs, "cur": 0, "sheet": sheet,
                         "path": p}
            self.redraw()
            self.panel()
            self.set_status("已导入 %s：%s · %d 行 × %d 列" %
                            (os.path.basename(fp), sheet, len(recs), len(head)))
        except Exception as e:
            messagebox.showerror("读不了这个表", str(e))

    # ────────────────────────── 右侧面板 ──────────────────────────
    def _bind_panel_wheel(self, w):
        """面板里所有控件都能用滚轮滚（子控件不绑的话，鼠标停在按钮上就滚不动）。

        Mac 的双指、Windows 的中键滚轮都是 MouseWheel；Button-4/5 是 X11 的
        老式滚轮事件，顺手绑上，Linux 上也能滚。
        """
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            w.bind(seq, self._panel_wheel, add="+")
        for c in w.winfo_children():
            self._bind_panel_wheel(c)

    def _panel_wheel(self, ev):
        if not self.pnl_canvas.winfo_ismapped():
            return
        self.pnl_canvas.yview_scroll(-_wheel_units(ev), "units")
        return "break"

    def panel(self):
        for w in self.pnl.winfo_children():
            w.destroy()
        if not self.tpl:
            self.pnl_title.config(text="")
            return
        name = dict(LAYERS)[self.layer]
        _n = layers.counts(self.tpl).get(self.layer, 0)
        self.pnl_title.config(text="%s · %s（%d）" % (self.layer, name, _n))
        if len(self._sel_list()) > 1:
            self._multi_block()
            self.pnl_canvas.yview_moveto(0)
            self._bind_panel_wheel(self.pnl)
            return
        getattr(self, "_panel_" + self.layer.lower())()
        if self.layer in ("L3", "L4"):
            self._group_block(self.layer)
        self.pnl_canvas.yview_moveto(0)      # 换层/换选中时回到顶部
        self._bind_panel_wheel(self.pnl)

    def _multi_block(self):
        """多选时右栏只放一件事：怎么对齐。"""
        items = self._sel_list()
        self._h("已选 %d 项" % len(items))
        grid = tk.Frame(self.pnl, bg=T.CARD)
        grid.pack(fill="x", pady=(2, 4))
        for i, (how, name) in enumerate(self.ALIGNS):
            T.Btn(grid, text=name, relief="flat", bg=T.DZ_BG, padx=6, pady=3,
                  font=(UI_FONT, 8),
                  command=lambda h=how: self._align_selected(h)
                  ).grid(row=i // 3, column=i % 3, sticky="ew", padx=1, pady=1)
        for c in range(3):
            grid.columnconfigure(c, weight=1)
        row = tk.Frame(self.pnl, bg=T.CARD)
        row.pack(fill="x", pady=2)
        for txt, axis in (("横向等距", "h"), ("纵向等距", "v")):
            T.Btn(row, text=txt, relief="flat", bg=T.DZ_BG, padx=6, pady=3,
                  font=(UI_FONT, 8),
                  command=lambda a=axis: self._distribute(a)
                  ).pack(side="left", fill="x", expand=True, padx=1)
        T.Btn(self.pnl, text="复制这 %d 项" % len(items),
              command=self.copy_selected, relief="flat", bg=T.DZ_BG,
              pady=4).pack(fill="x", pady=(8, 2))
        T.Btn(self.pnl, text="删除这 %d 项" % len(items),
              command=self.del_selected, relief="flat", bg=T.DZ_BG, fg=T.RED,
              pady=4).pack(fill="x", pady=2)

    def _group_block(self, layer):
        """组里属于这一层的元素 —— 它们藏在组的壳里，不列出来就看不见。"""
        rows = []
        for gi, grp in enumerate(self.tpl.get("组") or []):
            for e in grp.get("元素") or []:
                if layers.of(e, "ge") == layer:
                    rows.append((gi, grp, e))
        if not rows:
            return
        gname = self._group_name(rows[0][1])
        self._h("%s · %d 项" % (gname, len(rows)))
        lb = tk.Listbox(self.pnl, height=min(8, len(rows)), bd=0,
                        font=(UI_FONT, 8), highlightthickness=1,
                        highlightbackground=T.BORDER)
        lb.pack(fill="x", pady=(2, 4))
        for _gi, _grp, e in rows:
            if e.get("dxr") is not None:
                pos = "右 %.1f / 上 %.1f" % (float(e["dxr"]), float(e.get("dy") or 0))
            else:
                pos = "左 %.1f / 上 %.1f" % (float(e.get("dx") or 0),
                                            float(e.get("dy") or 0))
            lb.insert("end", " %s   %s" % (layers.label(e, "ge"), pos))
        T.Btn(self.pnl, text="▦ 选中整组", relief="flat",
              bg=T.DZ_BG, pady=4,
              command=lambda i=rows[0][0]: self._select_group(i)).pack(fill="x", pady=(0, 4))

    def show_layers(self):
        """四层说明 + 分层体检。

        四层最容易混的不是名字，是「这条线算图还是算字」「条码算不算数据」。
        这个小窗把每层的来源、装什么、现在有几个摆在一起，最后一行报
        「标的层和实际来源对不上」的元素 —— 不用去翻模板 json。
        """
        if not self.tpl:
            return
        win = tk.Toplevel(self.root)
        win.title("四层 · 各是什么、从哪来")
        win.configure(bg=T.BG)
        win.geometry("560x520")
        win.transient(self.root)

        head = tk.Frame(win, bg=T.BG)
        head.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(head, text="四层按「值从哪来」分，不按画在第几遍分",
                 bg=T.BG, fg=T.TEXT, font=(UI_FONT, T.SZ_DLG, "bold")).pack(anchor="w")

        box = tk.Frame(win, bg=T.CARD, highlightthickness=1,
                       highlightbackground=T.BORDER)
        box.pack(fill="both", expand=True, padx=18, pady=8)
        c = layers.counts(self.tpl)
        for k in layers.ORDER:
            d = layers.DEFS[k]
            row = tk.Frame(box, bg=T.CARD)
            row.pack(fill="x", padx=14, pady=(10, 0))
            tk.Label(row, text=k, bg=T.CARD, fg=T.PRIMARY,
                     font=(UI_FONT, 10, "bold"), width=3, anchor="w").pack(side="left")
            tk.Label(row, text=d["name"], bg=T.CARD, fg=T.TEXT,
                     font=(UI_FONT, 10, "bold"), width=4, anchor="w").pack(side="left")
            tk.Label(row, text="%d 个" % c.get(k, 0), bg=T.CARD, fg=T.TEXT3,
                     font=(UI_FONT, 9), width=5, anchor="w").pack(side="left")
            tk.Label(row, text="← " + d["src"], bg=T.CARD, fg=T.TEXT2,
                     font=(UI_FONT, 9), anchor="w").pack(side="left")
            tk.Label(box, text="　　" + d["what"], bg=T.CARD, fg=T.TEXT3,
                     font=(UI_FONT, T.SZ_FOOT), anchor="w",
                     justify="left", wraplength=490).pack(fill="x", padx=14)
            if k != layers.ORDER[-1]:
                tk.Frame(box, bg=T.SEP, height=1).pack(fill="x", padx=14, pady=(8, 0))

        bad = layers.audit(self.tpl)
        foot = tk.Frame(win, bg=T.BG)
        foot.pack(fill="x", padx=18, pady=(0, 14))
        if not bad:
            tk.Label(foot, text="分层体检：✓ 每一层的元素都和它的来源对得上",
                     bg=T.BG, fg=T.TEXT, font=(UI_FONT, 9, "bold")).pack(anchor="w")
        else:
            tk.Label(foot, text="分层体检：%d 处要处理" % len(bad), bg=T.BG,
                     fg=T.RED, font=(UI_FONT, 9, "bold")).pack(anchor="w")
            for it in bad[:6]:
                tk.Label(foot, text="· [%s] %s" % (it["layer"], it["msg"]),
                         bg=T.BG, fg=T.TEXT2, font=(UI_FONT, T.SZ_FOOT),
                         anchor="w", justify="left", wraplength=510).pack(fill="x")
        T.Btn(foot, text="知道了", command=win.destroy, bg=T.CARD,
              fg=T.TEXT2, padx=14, pady=4).pack(anchor="e", pady=(8, 0))

    # 每层一个固定色标 —— 四个层在视觉上必须一眼能分开（用户反复提的
    # 「操作逻辑不够明了、分界不明显」）。色只用在色条和层名上，不碰内容。
    LAYER_META = {
        "L1": ("底图 · 叠影 · 画布", "先定纸和基准", "#8E8E93"),
        "L2": ("固定图形", "选元素 → 调位置 → ⌖对位", "#2E7D32"),
        "L3": ("固定文字", "改字 → 调字号位置 → ⌖对位", "#1565C0"),
        "L4": ("数据文字", "连数据列 → 调字号位置 → ⌖对位", "#C62828"),
    }

    def _layer_hint(self, layer):
        """每层面板顶部的层头（2026-11-20 用户要求「分界明显一些」）。

        结构统一成三块，四层一模一样：
          ① 左侧竖色条 + 层号（色条让四层一眼分开）
          ② 层名 —— 这一层装什么
          ③ 操作顺序 —— 这一层的常规动作，箭头串起来
        三层都是同一个版式，切层时视觉位置不变，不用重新找控件。
        """
        meta = self.LAYER_META.get(layer)
        if not meta:
            return
        name, order, color = meta
        box = tk.Frame(self.pnl, bg=T.CARD)
        box.pack(fill="x", pady=(0, 8))
        # ① 竖色条（4px 宽，整行高）
        tk.Frame(box, bg=color, width=4).pack(side="left", fill="y")
        inner = tk.Frame(box, bg=T.CARD)
        inner.pack(side="left", fill="x", expand=True, padx=(9, 8), pady=6)
        # ② 层号 + 层名
        head = tk.Frame(inner, bg=T.CARD)
        head.pack(fill="x")
        tk.Label(head, text=layer, bg=T.CARD, fg=color,
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        tk.Label(head, text=name, bg=T.CARD, fg=T.TEXT,
                 font=(UI_FONT, 9)).pack(side="left", padx=(7, 0))
        # ③ 操作顺序
        tk.Label(inner, text=order, bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, 8), anchor="w", justify="left",
                 wraplength=228).pack(fill="x", pady=(1, 0))
        # 分隔线：层头与下面内容分开
        tk.Frame(self.pnl, bg=T.SEP, height=1).pack(fill="x", pady=(0, 8))

    def _h(self, text):
        tk.Label(self.pnl, text=text, bg=T.CARD, fg=T.TEXT2,
                 font=(UI_FONT, 9), anchor="w").pack(fill="x", pady=(10, 2))

    def _row(self, label, value, cmd=None, text="改"):
        f = tk.Frame(self.pnl, bg=T.CARD)
        f.pack(fill="x", pady=1)
        tk.Label(f, text=label, bg=T.CARD, fg=T.TEXT2, width=10, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left")
        tk.Label(f, text=str(value), bg=T.CARD, fg=T.TEXT, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left", fill="x", expand=True)
        if cmd:
            T.Btn(f, text=text, command=cmd, relief="flat", bg=T.CARD,
                      padx=6).pack(side="right")

    def _num_row(self, label, value, apply_fn, unit="mm", width=8):
        """一行「标签 + 可编辑数值 + 单位」。回车或失焦生效，值没变就不记历史。"""
        f = tk.Frame(self.pnl, bg=T.CARD)
        f.pack(fill="x", pady=1)
        # 标签不锁字符宽：中英混排（「参考图 X」）按固定字符宽算会算窄，
        # 把右边的输入框挤到下一行去，整栏看着就散了。
        tk.Label(f, text=label, bg=T.CARD, fg=T.TEXT2, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 6))
        var = tk.StringVar(value=("%.2f" % float(value)).rstrip("0").rstrip("."))
        e = tk.Entry(f, textvariable=var, width=width, font=(UI_FONT, 9),
                     justify="right", relief="flat", bg=T.DZ_BG,
                     highlightthickness=1, highlightbackground=T.BORDER)
        e.pack(side="left", ipady=2)
        tk.Label(f, text=unit, bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, 8)).pack(side="left", padx=(4, 0))

        def commit(_ev=None):
            try:
                v = float(var.get())
            except ValueError:
                return
            if abs(v - float(value)) < 1e-9:
                return                      # 没改，不记历史
            self.push_history()
            apply_fn(v)
            self.save()
            self.redraw()
            self.panel()

        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)
        return e

    def _panel_l1(self):
        self._layer_hint("L1")
        has = bool(self.tpl.get("底图"))
        self._row("底图", "已导入" if has else "无")
        T.Btn(self.pnl, text="换底图（PDF / 图片）", command=self.change_base,
              bg=T.DZ_BG, fg=T.TEXT, padx=10, pady=5).pack(fill="x", pady=(4, 2))

        # 叠影 = 半透明垫在下面那张，只给屏幕核对用，不进导出。
        # 来源可以是 GRA 成品图，也可以是研发给的图纸（新模板通常只有图纸）。
        self._h("叠影")
        ref = self.tpl.get("参考图")
        self._row("文件", os.path.basename(str(ref))[:20] if ref else "无")
        if ref:
            d = self._ref_diff()
            if d is not None:
                self._row("差异", "%.1f%%" % (d * 100.0))
        T.Btn(self.pnl, text="换叠影（图纸 / 图片）", command=self.change_ref,
              bg=T.DZ_BG, fg=T.TEXT, padx=10, pady=5).pack(fill="x", pady=(4, 2))
        if ref:
            T.Btn(self.pnl, text="自动对齐", command=self.align_ref,
                  bg=T.DZ_BG, fg=T.TEXT, padx=10, pady=4).pack(fill="x", pady=(2, 2))

        self._h("画布")
        f = tk.Frame(self.pnl, bg=T.CARD)
        f.pack(fill="x")
        self.v_w = tk.StringVar(value=str(self.tpl["画布"]["宽"]))
        self.v_h = tk.StringVar(value=str(self.tpl["画布"]["高"]))
        for lab, var in (("宽", self.v_w), ("高", self.v_h)):
            row = tk.Frame(f, bg=T.CARD)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=lab, bg=T.CARD, width=2, anchor="w",
                     font=(UI_FONT, 9)).pack(side="left")
            e = tk.Entry(row, textvariable=var, width=8, font=(UI_FONT, 9))
            e.pack(side="left")
            e.bind("<Return>", lambda _e: self.apply_canvas_size())
            tk.Label(row, text="mm", bg=T.CARD, fg=T.TEXT3,
                     font=(UI_FONT, 8)).pack(side="left", padx=4)
        T.Btn(f, text="应用尺寸", command=self.apply_canvas_size, relief="flat",
              bg=T.DZ_BG, pady=3).pack(fill="x", pady=4)

        # ★ 画布边距（P1-6）：白画布 = 黑框外沿向外各 m 毫米，四边严格相等。
        #   原点仍是黑框外沿左上角 → **改 m 不移动任何元素**，只改画布大小。
        #   改动就立刻重画预览（不按回车、不点按钮），因为这是"看一眼就调"的东西。
        row_m = tk.Frame(self.pnl, bg=T.CARD)
        row_m.pack(fill="x", pady=(2, 2))
        tk.Label(row_m, text="画布边距", bg=T.CARD, width=6, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left")
        self.v_margin = tk.StringVar(
            value=("%g" % FRAME_MOD.margin_of(self.tpl)))
        e_m = tk.Entry(row_m, textvariable=self.v_margin, width=8,
                       font=(UI_FONT, 9))
        e_m._is_margin = True        # 测试靠这个找它
        e_m.pack(side="left")
        tk.Label(row_m, text="mm", bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, 8)).pack(side="left", padx=4)
        tk.Label(row_m, text="0~20", bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, 8)).pack(side="left")
        e_m.bind("<Return>", lambda _e: self.apply_canvas_margin())
        e_m.bind("<FocusOut>", lambda _e: self.apply_canvas_margin())
        # 实时刷新：每敲一个字符就重画（数字很小，重画一次 ~20ms，够快）
        self.v_margin.trace_add(
            "write", lambda *_: self.apply_canvas_margin(live=True))
        _r = FRAME_MOD.canvas_rect(self.tpl)
        tk.Label(self.pnl,
                 text="画布 %.2f × %.2f mm（框内 %.2f × %.2f + 2×%g）"
                      % (_r["w"], _r["h"], _r["frame"]["w"],
                         _r["frame"]["h"], _r["margin"]),
                 bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, T.SZ_FOOT),
                 anchor="w", wraplength=238, justify="left").pack(
                     fill="x", pady=(0, 4))

        # 「只打黑色」开关已删除（2026-11-20 用户要求）。
        # 理由：现场只有黑墨不是"可选项"，彩色部分建模板时就不放图案。
        # 留个开关只会让人以为"关了就能彩印" —— 实际印出来还是灰的。
        # 现在黑白输出**恒定开启**，见 _mono_forced()。

    @staticmethod
    def _mono_forced() -> bool:
        """黑白输出恒定开启（2026-11-20）。

        现场那台机器只有黑墨 —— 这不是一个"选项"，是生产约束。所以模板里
        不再有「黑白输出」开关字段；渲染时一律按黑白出，彩色填充不画、
        彩色字转黑。模板里若残留旧字段，这里也不再读它（防止旧模板带进来
        一个"关"的状态把彩色印出去）。
        """
        return True

    def _toggle_grid_btn(self):
        """顶栏那颗「5mm 网格」：点一下开、再点关。"""
        self.show_grid = not self.show_grid
        self._refresh_cmp_btns()
        self.redraw()
        self.set_status("5mm 网格 %s" % ("已打开" if self.show_grid else "已关闭"))

    def _toggle_grid(self, on):
        self.show_grid = bool(on)
        self._refresh_cmp_btns()
        self.redraw()

    def _ref_diff(self):
        """当前画面与参考图的差异分（0~1，越小越重合）。

        口径：两张图都按「墨迹/空白」二值化后求平均绝对差。测出来的是
        「有多少墨没对上」，不是像素级 RMS —— 抗锯齿和字形差异会被放大一点，
        但拿它判断「挪了 2mm 是变好还是变坏」足够灵敏。
        """
        src = (self.tpl or {}).get("参考图")
        if not src:
            return None
        # 素材可能放在模板自己的 参考/ 目录，也可能在 data/ 下 —— resolve_ref
        # 是唯一的权威解析（project 里三级回退都在它里面）。
        fp = store.resolve_ref(self.tpl_name, src)
        if not fp or not os.path.exists(fp):
            return None
        dx = float(self.tpl.get("参考图X") or 0.0)
        dy = float(self.tpl.get("参考图Y") or 0.0)
        # 不能用屏幕上那张预览图来算：窗口刚建时画布只有一百多像素，
        # 条码细线糊成一片，同一份模板能算出 36%；等布局完成又变成 17%。
        # 固定 150dpi 自己渲一遍，什么时候问都是同一个数。
        try:
            key = (hash(json.dumps(self.tpl, ensure_ascii=False, sort_keys=True)),
                   dx, dy, self.data.get("cur", 0),
                   len(self.data.get("recs") or []))
        except Exception:
            key = None
        if key is not None and key == getattr(self, "_diff_key", None):
            return getattr(self, "_diff_val", None)
        try:
            base = render.render(self.tpl, self._fields(), 150)
            ref = render.Image.open(fp).convert("RGB")
        except Exception:
            return None
        r = align.overlap(ref, dx, dy, float(self.tpl["画布"]["宽"]), base.size)
        # 挖掉变量区再算分：条码、二维码、批号、日期、9 档压力值这些
        # 每规格都不一样，留着它们，分数就永远有个降不下去的底。
        v = align.score(base, r, holes=self._var_holes(base.size))
        self._diff_key, self._diff_val = key, v
        return v

    def align_ref(self):
        """自动找让差异最小的参考图偏移（±5mm 内两段搜索）。"""
        im = getattr(self, "_base_im", None)
        src = (self.tpl or {}).get("参考图")
        if im is None or not src:
            return
        fp = src if os.path.isabs(src) else os.path.join(paths.DATA, src)
        if not os.path.exists(fp):
            return
        ref = render.Image.open(fp).convert("RGB")
        self.set_status("正在找参考图的最佳位置…")
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            dx, dy, sc = align.best_offset(im, ref, float(self.tpl["画布"]["宽"]))
        finally:
            self.root.config(cursor="")
        self.push_history()
        self.tpl["参考图X"], self.tpl["参考图Y"] = round(dx, 2), round(dy, 2)
        self.save()
        self.redraw()
        self.panel()
        self.set_status("参考图已对到 (%.2f, %.2f) mm，差异 %.1f%%"
                        % (dx, dy, sc * 100))

    def _toggle_ref(self, on):
        """开关比对 —— 等价于在「正常 / 叠影」之间切。"""
        self.set_cmp("ghost" if on else "off")

    def _panel_l2(self):
        self._layer_hint("L2")
        idxs = self._glayer_idx("L2")
        n = len(idxs)
        # 选中的是表格 / 归到 L2 的文字 → 把编辑器整块提到最上面。
        # 四个「加东西」的按钮 + 缺素材提示要先让位：它们加起来 300px，
        # 编辑器排在后面的话，表格的格子网格就被顶到可视区外面去了，
        # 打开面板看见的是一排按钮，还得自己往下滚才知道能编辑。
        sel_l2_text = False
        if (self.sel and self.sel[0] == "t"
                and self.sel[1] < len(self.tpl.get("文字") or [])):
            sel_l2_text = layers.of(self.tpl["文字"][self.sel[1]], "t") == "L2"
        if sel_l2_text:
            idx = self.sel[1]
            self._edit_text_block(idx)
            if self.tpl["文字"][idx].get("type") == "table":
                T.Btn(self.pnl, text="删除这个表格", command=self.del_selected,
                      relief="flat", bg=T.DZ_BG, fg=T.RED, pady=4,
                      ).pack(fill="x", pady=(6, 0))
            self._h("图形层 · 加东西")

        # 四颗「能加的东西」用同一个颜色 —— 颜色在这里只表示「这一类是新增」，
        # 不表示主次。以前「加图形」是主色、其余是灰底，看着像只有一个能按。
        # 「加色块」已经去掉：现场打印机只吃黑色，彩色填充本来就不上墨，
        # 加了也是一块白 —— 留着只会让人以为「填了色但没打出来」。
        # ★ 2026-11-23 修：
        #   · 「（⌘V 粘一张也行）」删掉 —— P0-3 之后 ⌘V 不再加图，只会弹提示，
        #     这么写等于教用户去点一个必然失败的操作，然后反复弹窗。
        #   · 「＋ 加条码」暂时撤掉 —— 用户明确「条码先不要添加，等后续指示」。
        #     条码不是图库件，怎么来还没定，先别给入口免得更乱。
        for txt, cmd in (("＋ 加图形（从图库选）", self.add_graphic),
                         ("＋ 加表格", self.add_table),
                         ("＋ 加二维码", self.add_qrcode)):
            T.Btn(self.pnl, text=txt, command=cmd, relief="flat",
                  bg=T.DZ_BG, pady=4).pack(fill="x", pady=2)
        # 条码 / 二维码归数据层（内容来自表），但「加」的动作在图形这边 ——
        # 它们画出来是图形，找的时候会先想到这里。
        miss = [x for x in (self.tpl.get("缺素材") or [])]
        if miss:
            tk.Label(self.pnl, text="缺素材：%s" % "、".join(miss),
                     bg=T.CARD, fg=T.AMBER, font=(UI_FONT, T.SZ_FOOT),
                     anchor="w", wraplength=250, justify="left").pack(fill="x", pady=(4, 0))

        if sel_l2_text:
            self._h("图形层其它元素")

        # 表格虽然画的是线，但属于「固定版面」，所以挂在图形层。
        # 文字块也可能被人手动挪进图形层（白字压在色块上就得往上提一层），
        # 所以这里列的是「所有归 L2 的文字」，不只是表格。
        tbls = [i for i, b in enumerate(self.tpl.get("文字") or [])
                if layers.of(b, "t") == "L2"]
        if n or tbls:
            lb = tk.Listbox(self.pnl, height=min(9, n + len(tbls)), bd=0,
                            font=(UI_FONT, 9),
                            highlightthickness=1, highlightbackground=T.BORDER)
            lb.pack(fill="x", pady=6)
            items = [("g", i) for i in idxs] + [("t", i) for i in tbls]
            for k, (kind, i) in enumerate(items):
                if kind == "g":
                    g = self.tpl["图形"][i]
                    bb = self._bbox_of("g", i) or (0, 0, 0, 0)
                    lb.insert("end", " %d. %s  %.0f×%.0f"
                              % (k + 1, g.get("name") or g.get("type") or "图形",
                                 bb[2], bb[3]))
                else:
                    b = self.tpl["文字"][i]
                    what = "表格" if b.get("type") == "table" else "文字"
                    lb.insert("end", " %d. %s · %s" % (k + 1, what,
                                                       b.get("name") or ""))
            if self.sel and tuple(self.sel) in items:
                lb.selection_set(items.index(tuple(self.sel)))
            lb.bind("<<ListboxSelect>>", lambda e: self._pick_items(lb, items))

        for gi, grp in enumerate(self.tpl.get("组") or []):
            nm = self._group_name(grp)
            # 名字后面直接写里面有什么 —— 「四格区」三个字本身说明不了它是干嘛的
            T.Btn(self.pnl, text="▦ %s · 品牌/条码/日期" % nm,
                  relief="flat", bg=T.DZ_BG, pady=4,
                  command=lambda i=gi: self._select_group(i)).pack(fill="x", pady=(2, 2))
        if self.sel and self.sel[0] == "g" and self.sel[1] in idxs:
            self._edit_graphic(self.sel[1])
        elif self.sel and self.sel[0] == "gr":
            grp = self.tpl["组"][self.sel[1]]
            bb = self._bbox_of("gr", self.sel[1]) or (0, 0, 0, 0)
            self._h("%s · %.1f × %.1f mm" % (self._group_name(grp), bb[2], bb[3]))
            self._row("内含", "品牌 / 条码 / REF·LOT / 日期")
            self._num_row("右移", float(grp.get("dx", 0) or 0),
                          lambda v: grp.__setitem__("dx", round(v, 2)))
            self._num_row("下移", float(grp.get("dy", 0) or 0),
                          lambda v: grp.__setitem__("dy", round(v, 2)))

    def _comp_frame_editor(self, idx):
        """L2 里选中「顺应性卡 · 表格线」：只管格子，数值在 L4。"""
        g = self.tpl["图形"][idx]
        self._h("顺应性卡 · 表格线")
        self._row("左边界", "%.2f mm" % float(g.get("x0") or 0))
        self._row("右边界", "%.2f mm" % float(g.get("x1") or 0))
        self._row("行数", "%d 档" % max(0, len(g.get("ys") or []) - 2))
        j = self._comp_twin(idx)
        if j is not None:
            T.Btn(self.pnl, text="→ 看 9 档数值",
                  command=lambda i=j: self._goto_graph(i, "L4"),
                  relief="flat", bg=T.DZ_BG, pady=4,
                  ).pack(fill="x", pady=(6, 0))

    def _goto_graph(self, idx, layer):
        """跳到另一层的某个图形上（顺应性卡线/数配对时用）。"""
        self.layer = layer
        self.sel, self.sel_more = ("g", idx), []
        self._refresh_layer_btns()
        self.redraw()
        self.panel()

    def _select_group(self, i):
        """在 L2 里选中第 i 个组（标签上那块 2×2 小标签），之后能拖、能缩放。"""
        self.sel, self.sel_more = ("gr", i), []
        self.layer = "L2"
        self._refresh_layer_btns()
        self.panel()
        self.redraw()
        self.set_status("已选中整块：拖动整块一起走；单改某一处请去 L3 / L4 列表")

    # ★ 规则 3.3（2026-11-20 用户）：「字体族」和「粗细」是**两个独立维度**，
    # 必须分开选，不能合成一个下拉。
    # 之前 FAMILIES 里写 "思源黑体 · 粗" / "黑体 · 粗" 这种，等于把两件事
    # 缝在一根轴上 —— 想「换成宋体但仍加粗」就得在列表里找 "宋体 · 粗"，
    # 而宋体恰好没有粗档时就选不了，选择空间白白受限。
    #
    # 现在：
    #   · 字体族下拉：思源黑体 / 黑体 / 宋体 / 等宽 / …
    #   · 粗细按钮：细 / 常规 / 中 / 粗（该族没有的档位置灰）
    # 模板里存两个字段：`字体`（族名，如 "siyuan"）+ 每个元素的 `kind`
    # （= 族名 + 粗细后缀，如 "siyuanb"）。
    FONT_FAMILIES = [
        ("siyuan", "思源黑体"),
        ("hei", "黑体"),
        ("song", "宋体"),
        ("mono", "等宽"),
        ("impact", "Impact"),
        ("times", "Times"),
        ("georgia", "Georgia"),
        ("lat", "Arial"),
    ]

    def _family_of(self, kind: str) -> str:
        """从 kind（如 siyuanb / timesb）反推**族名**（siyuan / times）。

        ★ 2026-11-23 修：以前只认 `CJK_KINDS` 里的（全是中文字面），
        于是 `timesb` / `georgiab` / `latb` 这几个**拉丁粗体**归不成族，
        原样返回 "timesb"，在下拉列表里找不到 → 掉到默认值「黑体」。
        用户把拉丁字标设成 Times 粗，右栏却显示"黑体"，改都不知道从哪改。

        现在两套都认：`CJK_KINDS`（中文字面）和 `FAMILIES`（下拉里所有族）。
        """
        if not kind:
            return LK.cjk_kind(self._font_of_layer("L3"))
        fam_keys = {k for k, _ in self.FONT_FAMILIES}
        for suf in ("l", "m", "b"):
            if not kind.endswith(suf):
                continue
            base = kind[:-1]
            if base in LK.CJK_KINDS or base in fam_keys:
                return base
        return kind

    def _font_of_layer(self, layer: str) -> str:
        """这一层当前的字体族（P1-3：L3 / L4 各自独立）。"""
        key = LK.FONT_KEYS.get(layer) or LK.FONT_KEYS["L3"]
        return (self.tpl.get(key) or self.tpl.get(LK.LEGACY_FONT_KEY)
                or LK.DEFAULT_FONT)

    def _font_row(self, layer: str = "L3"):
        """某一层的字体族（**只管族，不管粗细**）—— L3 / L4 各自一个（P1-3）。

        为什么必须分开：L3 是固定文字（REF/LOT/mm/注意事项），L4 是数据
        （批号/日期/规格数字）。这两类的字形要求本来不同，共用一个字段
        意味着"改数据字体会把固定文字一起带偏"。
        以前 L4 面板里那行注释自己都写着「两层都是文字，没理由一层没有」，
        但共用一个字段只是把问题从"没有"变成"分不开"。

        改这一层的字体时，**只动这一层元素的 kind** —— 另一层一个字不动。
        """
        key = LK.FONT_KEYS.get(layer) or LK.FONT_KEYS["L3"]
        self._h("字体族（%s）" % layer)
        fams = self.FONT_FAMILIES
        names = dict(fams)
        # ★ 先把 kind 归一成**族名**再看下拉（2026-11-23 修）
        # 模板里存的是具体字面（siyuanm = 思源黑体·中），而 FONT_FAMILIES
        # 只有族（siyuan）。不归一会 names.get("siyuanm") 落空，
        # 掉到默认值 —— 于是"模板是思源中，下拉显示黑体"。
        # 这个 bug 以前就有（显示和实际不符），P1-3 分开两栏后更明显。
        cur = self._family_of(self._font_of_layer(layer))
        var = tk.StringVar(value=names.get(cur, names.get(LK.DEFAULT_FONT, "")))

        def apply(*_a):
            for k, v in fams:
                if v != var.get():
                    continue
                if k == cur:
                    return
                self.push_history()
                self.tpl[key] = k
                # 只改这一层元素的 kind（保留各自粗细档），另一层完全不动
                for b in self.tpl.get("文字") or []:
                    if layers.of(b, "t") != layer:
                        continue
                    old_kind = b.get("kind")
                    if not old_kind:
                        continue
                    fam0 = self._family_of(old_kind)
                    w = old_kind[len(fam0):] if old_kind.startswith(fam0) else ""
                    newk = k + w
                    b["kind"] = newk if newk in LK.CJK_KINDS else k
                self._font_cache.clear()
                self.save()
                self.redraw()
                self.panel()
                return

        cb = ttk.Combobox(self.pnl, textvariable=var, values=[v for _, v in fams],
                          state="readonly", font=(UI_FONT, 9))
        cb._is_font = True        # 面板测试靠这个把「字体族」和「绑列」两种下拉分开
        cb._font_layer = layer    # 测试要靠它区分这是 L3 还是 L4 那个下拉
        cb.pack(fill="x", pady=(0, 6))
        cb.bind("<<ComboboxSelected>>", apply)
        tk.Label(self.pnl, text="只作用于 %s（L3 固定文字 / L4 数据各管各的）" % layer,
                 bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, T.SZ_FOOT),
                 anchor="w", wraplength=238, justify="left",
                 ).pack(fill="x", pady=(0, 4))

    def _edit_graphic(self, idx):
        """图形的编辑区（对齐 / 各类专用编辑器 / 位置尺寸 / 复制）。

        抽出来是因为它不再只属于图形层：一块图形被手动挪到 L3，在 L3 里
        选中它也应该能改 —— 不然「换层」只是把东西搬到了一个改不了的地方。
        """
        self._h("对齐到画布")
        rows_align = ((("左", "left"), ("横中", "hcenter"), ("右", "right")),
                      (("上", "top"), ("纵中", "vcenter"), ("下", "bottom")))
        for r, opts in enumerate(rows_align):
            al = tk.Frame(self.pnl, bg=T.CARD)
            al.pack(fill="x", pady=(0, 1 if r == 0 else 4))
            for txt, how in opts:
                T.Btn(al, text=txt, relief="flat", bg=T.DZ_BG, padx=4, pady=2,
                      font=(UI_FONT, 8),
                      command=lambda h=how, i=idx: self.align_graphic(i, h)
                      ).pack(side="left", fill="x", expand=True, padx=1)
        g = self.tpl["图形"][idx]
        if g.get("type") in self.COMP_TYPES:
            self._comp_frame_editor(idx)
            return
        if g.get("type") == "barcode":
            self._barcode_editor_g(idx)
            return
        self._h("位置 / 尺寸")
        self._num_row("X", g["x"], lambda v: g.__setitem__("x", round(v, 2)))
        self._num_row("Y", g["y"], lambda v: g.__setitem__("y", round(v, 2)))
        # ★ 宽/高按 lib_ratio 联动（2026-11-23 P0-3）
        # 以前两个框都能独立改 —— 改了一个，圆形图标立刻变成椭圆。
        # 这是「看得见的错」，而且存盘看不出问题，只有印出来才发现。
        # 现在改宽自动算高、改高自动算宽，比例永远是图库原图的比例。
        if g.get("type") == "image" and g.get("lib_ratio"):
            ratio = float(g["lib_ratio"]) or 1.0

            def _set_w(v):
                g["w"] = round(max(0.5, v), 2)
                g["h"] = round(max(0.5, g["w"] / ratio), 2)

            def _set_h(v):
                g["h"] = round(max(0.5, v), 2)
                g["w"] = round(max(0.5, g["h"] * ratio), 2)

            self._num_row("宽", g["w"], _set_w)
            self._num_row("高", g["h"], _set_h)
            tk.Label(self.pnl,
                     text="比例锁定（图库原图 %.3f）改一个另一个自动跟" % ratio,
                     bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, T.SZ_FOOT),
                     anchor="w", wraplength=238, justify="left",
                     ).pack(fill="x", pady=(0, 4))
        else:
            self._num_row("宽", g["w"], lambda v: g.__setitem__("w", max(0.5, round(v, 2))))
            self._num_row("高", g["h"], lambda v: g.__setitem__("h", max(0.5, round(v, 2))))
        # 图像同样给「按叠影对位」：图标/图库件和文字一样要往叠影上吸
        # （2026-11-20 用户提的：这按钮以前只在文字面板，图形得手拖）。
        self._align_ref_btn(idx, "g")
        T.Btn(self.pnl, text="复制", command=self.copy_selected,
              relief="flat", bg=T.DZ_BG, pady=4,
              ).pack(fill="x", pady=(6, 0))

    # ── 字重 / 墨色 ──────────────────────────────────────────────
    def _weight_kinds(self):
        """当前字体族下能选的「粗细」四档：细 / 常规 / 中 / 粗。

        为什么不是每档都写死思源：模板可以整体换成宋体或黑体，这时「粗」
        该是 songb / heib，而不是硬塞一个思源粗进去 —— 换族之后字面
        就对不上了（同一段字一半宋一半思源，一眼能看出来）。
        """
        # 按当前编辑元素的层取字体（P1-3）
        _el = (self.tpl.get("文字") or [None])[self.sel[1]] if (
            self.sel and self.sel[0] == "t" and self.sel[1] is not None
            and self.sel[1] < len(self.tpl.get("文字") or [])) else None
        base = LK.cjk_kind(render.fam_of(self.tpl, _el))
        # 只列**这一族真的有的**档位：黑体只有常规/粗，思源四档齐全。
        # 以前缺档就往思源借一个，结果一块宋体段落里混进一句思源粗，
        # 粗细对了、字形不对，比不加粗还难看。
        cands = [(base + "l", "细"), (base, "常规"),
                 (base + "m", "中"), (base + "b", "粗")]
        out = [(k, lab) for k, lab in cands if k in LK.CJK_KINDS]
        if len(out) < 2:                      # 兜底：至少常规 + 粗
            out = [(base, "常规")]
        return out

    def _weight_row(self, idx):
        """这一块文字用多粗 —— 现场反馈「地址那几行印出来发灰」，就是细体。

        2026-11-20 合并：「字重（细/常规/中/粗）」和原来的「墨色（黑/深灰/
        中灰/浅灰）」在印刷上是同一件事 —— 都只是让字看起来更重或更轻。
        两排并排放，用户不知道该调哪个，而且可以调出「粗体+浅灰」这种
        自相矛盾的状态（字又粗又淡，印出来最脏）。

        现在只有一排「字重」，四档按粗细递增，颜色固定为 #111111（纯黑）：
          · 要让字更轻 → 选细体（字形本身变细，印得干净）
          · 要让字更重 → 选粗体
        旧模板里带灰色 color 的，读进来当「细体」处理（见 _stored_weight）。
        """
        b = self.tpl["文字"][idx]
        cur = self._stored_weight(b)
        self._h("字重")
        row = tk.Frame(self.pnl, bg=T.CARD)
        row.pack(fill="x", pady=(0, 3))
        for k, lab in self._weight_kinds():
            on = (k == cur)
            T.Btn(row, text=lab, relief="flat",
                  bg=T.PRIMARY if on else T.DZ_BG,
                  fg=T.CARD if on else T.TEXT, padx=5, pady=3,
                  font=(UI_FONT, 8),
                  command=lambda kk=k: self._set_weight(idx, kk)
                  ).pack(side="left", padx=(0, 2))

    def _stored_weight(self, b):
        """读这一块当前的字重档。

        兼容旧数据：以前有「墨色=中灰/浅灰」这种状态，语义上等于"字更轻"，
        这里映射成"细"。新数据只有 kind 一个来源。
        """
        k = b.get("kind")
        if k and k in dict(self._weight_kinds()):
            return k
        # 旧模板：靠 color 猜档位
        c = str(b.get("color") or "").upper()
        base = LK.cjk_kind(render.fam_of(self.tpl, b))   # 按层取（P1-3）
        if c in ("#8C8C8C", "#5E5E5E") and (base + "l") in LK.CJK_KINDS:
            return base + "l"                  # 浅灰/中灰 → 细体
        return base

    def _set_weight(self, idx, kind):
        b = self.tpl["文字"][idx]
        if b.get("kind") == kind:
            return
        self.push_history()
        b["kind"] = kind
        # 字重是唯一的"轻重"维度 —— 顺手把旧模板残留的灰色墨色归一到纯黑
        b["color"] = "#111111"
        if b.get("lines"):
            for ln in b["lines"]:
                ln["b"] = 1 if kind.endswith("b") else 0
        self._font_cache.clear()
        self.save()
        self.redraw()
        self.panel()
        self.set_status("字重：%s" % dict((k, v) for k, v in self._weight_kinds()).get(kind, kind))

    # 「墨色」那一排已删除（2026-11-20）：字重和墨色在印刷上是同一件事，
    # 两排并排放会让人不知道调哪个，还能调出「粗体+浅灰」这种自相矛盾态。
    # 现在只有「字重」一排，颜色恒定 #111111。见 _weight_row 的注释。

    def _align_ref_btn(self, idx, kind="t"):
        """「按叠影对位」小按钮：一次只对一块，对完可撤销。

        kind="t" 文字块 / kind="g" 图形（2026-11-20 补的：图像面板也要有，
        图标/图库件同样要往叠影上吸，别让人切到 L3 才找得到这个按钮）。
        """
        if not (self.tpl or {}).get("参考图"):
            return
        row = tk.Frame(self.pnl, bg=T.CARD)
        row.pack(fill="x", pady=(0, 4))
        T.Btn(row, text="⌖ 按叠影对位", relief="flat", bg=T.DZ_BG,
              padx=6, pady=3, font=(UI_FONT, 8),
              command=lambda i=idx, k=kind: self.align_to_ref(i, k)).pack(side="left")
        tk.Label(row, text="贴到附近最像的墨迹（%.0fmm 内）" % self.SNAP_R_MM,
                 bg=T.CARD, fg=T.TEXT3,
                 font=(UI_FONT, 8)).pack(side="left", padx=(6, 0))
        # ┖ 按叠影定宽（P1-5）：由叠影的墨迹框取 H、W，先定字号，
        #   再先词距后字距摊平差值。产出 h + tracking（**不存宽度**）。
        if kind == "t":
            row2 = tk.Frame(self.pnl, bg=T.CARD)
            row2.pack(fill="x", pady=(0, 4))
            T.Btn(row2, text="⌖ 按叠影定宽", relief="flat",
                  bg=T.DZ_BG, padx=6, pady=3, font=(UI_FONT, 8),
                  command=lambda i=idx: self.fit_width_to_ref(i)).pack(side="left")
            tk.Label(row2, text="取叠影的字高与字宽，用字距摊平（不动字形）",
                     bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, 8)).pack(
                         side="left", padx=(6, 0))

    def _edit_text_block(self, idx):
        """文字块的通用编辑区：位置 / 字号 / 内容。L2、L3 共用。

        表格走自己的编辑器（Tab 分列），多行块走 `_list_editor`。
        """
        b = self.tpl["文字"][idx]
        if b.get("type") == "table":
            bb = self._bbox_of("t", idx) or (0, 0, 0, 0)
            self._h("位置 / 字号")
            self._num_row("X", bb[0], lambda v, i=idx: self._move_block(i, v, None))
            self._num_row("Y", bb[1], lambda v, i=idx: self._move_block(i, None, v))
            self._num_row("格内字号", float(b.get("cell_sz") or 0),
                          lambda v, i=idx: b.__setitem__("cell_sz", round(v, 2)))
            tk.Label(self.pnl, text="0 = 自动（按行高的 62% 排，放不下自动缩字）",
                     bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, 8), anchor="w",
                     wraplength=250, justify="left").pack(fill="x")
            row = tk.Frame(self.pnl, bg=T.CARD)
            row.pack(fill="x", pady=(4, 0))
            tk.Label(row, text="格内对齐", bg=T.CARD, fg=T.TEXT2,
                     font=(UI_FONT, 9), width=6, anchor="w").pack(side="left")
            for lab, how in (("左", "left"), ("中", "center"), ("右", "right")):
                on = (b.get("align") or "center") == how
                T.Btn(row, text=lab, relief="flat",
                      bg=T.PRIMARY if on else T.DZ_BG,
                      fg=T.CARD if on else T.TEXT, padx=6, pady=2,
                      font=(UI_FONT, 8),
                      command=lambda h=how, i=idx: self._set_table_align(i, h)
                      ).pack(side="left", padx=(0, 2))
            self._table_editor(idx)
            return
        if b.get("lines"):
            # 内容在最上（2026-11-20 面板统一：文字信息在上，字体/位置在下）——
            # 改字是最高频的动作，不该藏在面板末尾。L2/L3/L4 三层同一顺序。
            self._text_editor(idx)
            self._h("位置 / 字号")
            bb = self._bbox_of("t", idx) or (0, 0, 0, 0)
            self._num_row("X", bb[0], lambda v, i=idx: self._move_block(i, v, None))
            self._num_row("Y", bb[1], lambda v, i=idx: self._move_block(i, None, v))
            self._num_row("字号", b["lines"][0].get("sz", 3),
                          lambda v, i=idx: self._resize_block(i, v))
        elif b.get("x") is not None:
            self._text_editor(idx)
            self._h("位置 / 字号")
            bb = self._bbox_of("t", idx) or (0, 0, 0, 0)
            self._num_row("X", bb[0], lambda v, i=idx: self._move_block(i, v, None))
            self._num_row("Y", bb[1], lambda v, i=idx: self._move_block(i, None, v))
            self._num_row("字号", b.get("h") or 3.0,
                          lambda v, i=idx: self._set_block_h(i, v))
        self._weight_row(idx)
        self._align_ref_btn(idx)

    def _panel_l3(self):
        self._layer_hint("L3")
        self._font_row("L3")          # L3 的字体（P1-3：和 L4 分开）
        # 列表按「元素自己的层」列，不看类型：一块图形被手动挪到文字层，
        # 它在这一层就该出现在这一层的列表里，否则又是「看得见、选不中」。
        cands = self._cands("L3")
        T.Btn(self.pnl, text="＋ 加文字", command=self.add_text, relief="flat",
              bg=T.DZ_BG, pady=5,
              ).pack(fill="x", pady=(4, 2))
        if not cands:
            tk.Label(self.pnl, text="（空）", bg=T.CARD, fg=T.TEXT3,
                     font=(UI_FONT, 9), anchor="w").pack(fill="x", pady=6)
            return
        lb = tk.Listbox(self.pnl, height=min(10, len(cands)), bd=0,
                        font=(UI_FONT, 9), highlightthickness=1,
                        highlightbackground=T.BORDER)
        lb.pack(fill="x", pady=6)
        for kind, i in cands:
            if kind == "t":
                b = self.tpl["文字"][i]
                lb.insert("end", " " + str(b.get("name", b.get("k"))))
            else:
                g = self.tpl["图形"][i]
                lb.insert("end", " " + (g.get("name") or self._data_graph_label(g)))
        # 高亮选中项要按「key 相同」找位置，不能拿 dict 相等去 index：
        # 这块版面里有七八个一模一样的「mm」，相等就取到第一块，选中会跳到别人身上。
        cur = tuple(self.sel) if self.sel else None
        if cur in cands:
            lb.selection_set(cands.index(cur))
            lb.see(cands.index(cur))
        lb.bind("<<ListboxSelect>>", lambda e: self._pick_items(lb, cands))
        if cur is None:
            return
        kind, idx = cur
        if kind == "g":
            self._edit_graphic(idx)
        else:
            self._edit_text_block(idx)

    # ── 数据体检 ────────────────────────────────────────────────
    def _audit(self):
        """查「印出来会错」的数据问题。返回 [(级别, 标题, 说明)]。

        为什么不能靠参考图：参考图是某一个规格的成品。换个规格，条码、
        批号、日期、9 档压力值本来就该全变 —— 拿它去比这些地方永远红着。
        所以数据这一侧的对错只能从数据本身查：缺没缺、撑没撑破框。
        """
        out = []
        if not self.tpl or not self.data.get("recs"):
            return out
        try:
            f = self._fields() or {}
        except Exception:
            return out

        # 1) 占位符到最后还是【括号】—— 这块会直接把括号印出去
        for k in (databind.holes(self.tpl, f) or [])[:5]:
            out.append(("red", "【%s】没填上" % k,
                        "数据表里没有这一列，模板里也没有示例值"))

        # 2) 映射申明了、表里却没值：现在印的是示例值 —— 最危险的一种错，
        #    看起来完全正常，实际上是别的规格的批号 / 日期。
        for k in (f.get("__缺列__") or [])[:5]:
            v = f.get(k)
            tip = "数据表里没有对应列"
            if v not in (None, ""):
                tip += "，当前显示「%s」" % str(v)[:14]
            out.append(("amber", "「%s」印的是示例值" % k, tip))

        # 2.5) fuzzy 列名命中：边界不可靠，没填值但也没报错。
        #      必须让人看见 —— 否则他只会觉得"这一栏怎么空了"，
        #      然后手动去连一个错的列。标黄，说明候选列名和它为什么可疑。
        for name, info in (f.get("__疑似__") or {}).items():
            col = str((info or {}).get("列") or "")
            pat = str((info or {}).get("模式") or "")
            out.append(("amber", "「%s」疑似连到「%s」" % (name, col),
                        "数据表里没有名字恰好是「%s」的列，只有一列在中段包含它。"
                        "为避免连错规格，**没有自动填值**，请手动确认要连哪一列"
                        % pat))

        # 3) 文字比所在框还宽 —— 长目录号、长批号换一条数据就挤出去
        for b in self.tpl.get("文字") or []:
            if layers.of(b, "t") != "L4" or b.get("fit") or not b.get("w"):
                continue
            try:
                t = PH.fill_placeholders(str(b.get("t") or ""), f)
                if not t or "【" in t:
                    continue
                w = self._text_width_mm(t, float(b.get("h") or 3.0))
                bw = float(b["w"])
            except Exception:
                continue
            if w > bw * 1.03:
                out.append(("amber", "「%s」撑破了框" % t[:12],
                            "实测 %.1f mm，框只有 %.1f mm" % (w, bw)))
        return out[:8]

    def _render_audit(self):
        """把体检结果画到面板上。没问题也要说一句，否则会以为没查。"""
        if not self.data.get("recs"):
            return
        items = self._audit()
        self._h("体检")
        if not items:
            tk.Label(self.pnl, text="✓ 无问题", bg=T.CARD, fg=T.TEXT3,
                     font=(UI_FONT, T.SZ_FOOT), anchor="w").pack(fill="x", pady=(1, 4))
            return
        for lv, title, note in items:
            fg = T.RED if lv == "red" else T.AMBER
            row = tk.Frame(self.pnl, bg=T.CARD)
            row.pack(fill="x", pady=(2, 0))
            tk.Label(row, text=("● " if lv == "red" else "▲ "), bg=T.CARD,
                     fg=fg, font=(UI_FONT, T.SZ_FOOT)).pack(side="left", anchor="n")
            col = tk.Frame(row, bg=T.CARD)
            col.pack(side="left", fill="x", expand=True)
            tk.Label(col, text=title, bg=T.CARD, fg=fg, anchor="w",
                     font=(UI_FONT, T.SZ_FOOT, "bold"),
                     wraplength=214, justify="left").pack(fill="x")
            tk.Label(col, text=note, bg=T.CARD, fg=T.TEXT3, anchor="w",
                     font=(UI_FONT, T.SZ_FOOT),
                     wraplength=214, justify="left").pack(fill="x")
        tk.Frame(self.pnl, bg=T.CARD, height=4).pack(fill="x")

    def _panel_l4(self):
        """数据层：谁在吃数据 → 换一条规格 → 还缺什么。

        顺序照「换一行数据」实际要走的步骤排，也是用户提的那三条：
        先看文字链接到哪一列，再挑规格，最后看还缺哪几项。
        """
        # 选中 L4 的图形（条码 / 二维码 / 顺应性卡数值）→ 只给它的编辑器
        if self.sel and self.sel[0] == "g":
            g = self.tpl["图形"][self.sel[1]]
            if layers.of(g, "g") == "L4":
                self._data_graph_editor(self.sel[1])
                self._data_source_row()
                return

        self._layer_hint("L4")

        # ★ 规则 3.4（2026-11-20 用户）：L4 是数据层，**必须能连接外部数据**，
        # 而且入口要显眼 —— 之前"导入数据表"藏在数据源那一小块里，
        # 进 L4 先看到一堆文字块，反而不知道数据从哪来。
        # 现在进 L4 第一件事就是这颗按钮 + 当前数据源状态。
        self._h("外部数据")
        ds = self.tpl.get("数据源") or {}
        if ds.get("path"):
            nm = os.path.basename(str(ds["path"]))
            nrec = len(self.data.get("recs") or [])
            tk.Label(self.pnl, text="已连：%s（%d 条）" % (nm, nrec),
                     bg=T.CARD, fg=T.PRIMARY, font=(UI_FONT, 8),
                     anchor="w", wraplength=250, justify="left"
                     ).pack(fill="x", pady=(0, 3))
        else:
            tk.Label(self.pnl, text="还没连数据表 —— 下面的【占位符】只能是示例值",
                     bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, 8),
                     anchor="w", wraplength=250, justify="left"
                     ).pack(fill="x", pady=(0, 3))
        T.Btn(self.pnl, text="🔗 连接外部数据表（Excel / CSV）",
              relief="flat", bg=T.PRIMARY, fg="#ffffff", pady=6,
              font=(UI_FONT, 9, "bold"),
              command=self.import_data).pack(fill="x", pady=(0, 6))

        # L4 自己的字体族（P1-3）。以前和 L3 共用一个 tpl["字体"]，
        # 改数据字体把固定文字一起带偏 —— 现在各管各的。
        self._font_row("L4")
        idxs = [i for i, b in enumerate(self.tpl.get("文字") or [])
                if layers.of(b, "t") == "L4"]
        blocks = [self.tpl["文字"][i] for i in idxs]
        self._h("数据文字（%d）" % len(blocks))
        T.Btn(self.pnl, text="＋ 加文字", command=lambda: self.add_text("L4"),
              relief="flat", bg=T.DZ_BG, pady=5).pack(fill="x", pady=(4, 2))
        if blocks:
            lb = tk.Listbox(self.pnl, height=min(10, len(blocks)), bd=0,
                            font=(UI_FONT, 9), highlightthickness=1,
                            highlightbackground=T.BORDER)
            lb.pack(fill="x", pady=6)
            for b in blocks:
                nm = str(b.get("name", b.get("k")))
                used = PH.used_keys(str(b.get("t") or ""))
                col = self._linked_col(used[0]) if used else ""
                if col:
                    nm += "  → " + str(col)[:15]
                lb.insert("end", " " + nm)
            # 高亮按索引找，不拿 dict 相等去 index：两块【球囊直径】长得一模一样，
            # 相等就取到前一块，选中会跳到别人身上。
            if self.sel and self.sel[0] == "t" and self.sel[1] in idxs:
                pos = idxs.index(self.sel[1])
                lb.selection_set(pos)
                lb.see(pos)
            lb.bind("<<ListboxSelect>>", lambda e: self._pick_list(lb, "t", "L4"))
        else:
            tk.Label(self.pnl, text="（空）", bg=T.CARD, fg=T.TEXT3,
                     font=(UI_FONT, 9), anchor="w").pack(fill="x", pady=6)

        # 选中某块 L4 文字：位置 / 字号 / 链接到哪一列 / 内容
        if self.sel and self.sel[0] == "t" and self.sel[1] < len(self.tpl["文字"]):
            idx = self.sel[1]
            b = self.tpl["文字"][idx]
            if b.get("layer") != "L4":
                self._spec_block()
                self._render_audit()
                self._data_source_row()
                return
            if b.get("type") == "table":
                self._table_editor(idx)
                self._segment_end()
                return
            if b.get("bars") and not b.get("lines"):
                self._barcode_editor(idx)
                self._segment_end()
                return
            # 先给「链接到哪一列」：换数据的时候先确认这件事，
            # 位置和字号是摆版面时才调的，放后面。
            self._bind_rows(idx)
            # 对位放在字重前面：它是「把这块摆正」的动作，和上面的位置是一件事，
            # 不该被字重墨色挤到面板最底下看不见。
            self._align_ref_btn(idx)
            # 字重 / 墨色：和 L3 一模一样的两排。数据文字往往更小、更容易
            # 印得发灰，能不能加粗比 L3 更要紧。
            self._weight_row(idx)
            if b.get("lines"):
                bb = self._bbox_of("t", idx)
                self._h("位置 / 字号")
                self._num_row("X", bb[0], lambda v, i=idx: self._move_block(i, v, None))
                self._num_row("Y", bb[1], lambda v, i=idx: self._move_block(i, None, v))
                self._num_row("字号", b["lines"][0].get("sz", 3),
                              lambda v, i=idx: self._resize_block(i, v))
            else:
                bb = self._bbox_of("t", idx) or (0, 0, 0, 0)
                self._h("位置 / 字号")
                self._num_row("X", bb[0], lambda v, i=idx: self._move_block(i, v, None))
                self._num_row("Y", bb[1], lambda v, i=idx: self._move_block(i, None, v))
                self._num_row("字号", b.get("h") or 3.0,
                              lambda v, i=idx: self._set_block_h(i, v))
            self._text_editor(idx)
            self._segment_end()
            return

        self._spec_block()
        self._render_audit()
        self._data_source_row()

    def _segment_end(self):
        """选中单块之后，把「换规格」和「缺什么」压到最下面一条细线之后。

        不放在上面：正在改某一块的字时，不该被一长串规格列表挤到看不见。
        """
        tk.Frame(self.pnl, bg=T.SEP, height=1).pack(fill="x", pady=(10, 2))
        self._spec_block()
        self._render_audit()

    def _data_source_row(self):
        """最底一行：这份模板现在接的是哪张表，点「换表」换一份。"""
        fp = self.data.get("path") or ""
        recs = self.data.get("recs") or []
        txt = ("%s · %d 行" % (os.path.basename(fp), len(recs))) if fp else "示例数据"
        row = tk.Frame(self.pnl, bg=T.CARD)
        row.pack(fill="x", pady=(10, 2))
        tk.Label(row, text=txt, bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, 8),
                 anchor="w").pack(side="left", fill="x", expand=True)
        T.Btn(row, text="换表", command=self.import_data, relief="flat",
              bg=T.DZ_BG, padx=8, pady=2,
              font=(UI_FONT, 8)).pack(side="right")

    @staticmethod
    def _data_graph_label(g) -> str:
        t = g.get("type")
        if t == "barcode":
            return "条码 · %s" % (g.get("seq") or "")
        if t == "qrcode":
            return "二维码 · %s" % str(g.get("seq") or "")[:24]
        if t == "compvals":
            return "顺应性卡 · %d 档数值（查表）" % len(g.get("rows") or [])
        if t == "comp":
            return "顺应性卡 · 表格线（L2 固定版面）"
        return g.get("name") or (t or "图形")

    def _data_graph_editor(self, idx):
        """数据层里选中条码 / 二维码 / 顺应性卡时的面板。"""
        g = self.tpl["图形"][idx]
        t = g.get("type")
        if t == "barcode":
            self._barcode_editor_g(idx)
        elif t == "qrcode":
            self._qrcode_editor(idx)
        elif t in ("compvals", "comp"):
            self._comp_editor(idx)

    def _qrcode_editor(self, idx):
        g = self.tpl["图形"][idx]
        self._h("二维码内容（占位符）")
        var = tk.StringVar(value=str(g.get("seq", "")))
        e = tk.Entry(self.pnl, textvariable=var, font=(UI_FONT, 9), relief="flat",
                     bg=T.DZ_BG, highlightthickness=1, highlightbackground=T.BORDER)
        e.pack(fill="x", ipady=3, pady=(0, 4))

        def commit(*_a):
            v = var.get().strip()
            if v == str(g.get("seq", "")):
                return
            self.push_history()
            g["seq"] = v
            self.save()
            self.redraw()
            self.panel()

        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)
        self._num_row("X", g["x"], lambda v: g.__setitem__("x", round(v, 2)))
        self._num_row("Y", g["y"], lambda v: g.__setitem__("y", round(v, 2)))
        self._num_row("边长", g.get("w"), lambda v: self._set_graph_size(idx, v))
        _v = PH.fill_placeholders(str(g.get("seq", "")), self._fields() or {})
        tk.Label(self.pnl, text="当前值：%s" % (_v or "（空）"),
                 bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, 8), justify="left",
                 anchor="w", wraplength=262).pack(fill="x", pady=(2, 4))

    def _set_graph_size(self, idx, v):
        """二维码是正方形，改边长就两边一起改。"""
        v = max(2.0, round(float(v), 2))
        g = self.tpl["图形"][idx]
        g["w"] = v
        g["h"] = v
        self.save()
        self.redraw()
        self.panel()

    def _comp_editor(self, idx):
        """顺应性卡：9 档直径从哪来、现在是多少，摊开给人核对。"""
        g = self.tpl["图形"][idx]
        f = self._fields() or {}
        rows = f.get(g.get("字段") or "顺应性") or g.get("rows") or []
        mp = (self.tpl.get("数据映射") or {})
        self._h("顺应性卡 · %d 档" % len(rows))
        grid = tk.Frame(self.pnl, bg=T.CARD)
        grid.pack(fill="x", pady=(0, 4))
        for ci, head in enumerate(("压力", "直径", "来源列")):
            tk.Label(grid, text=head, bg=T.CARD, fg=T.TEXT3,
                     font=(UI_FONT, 8, "bold"), anchor="w").grid(
                row=0, column=ci, sticky="w", padx=(0, 6))
        for ri, row in enumerate(rows[:9], start=1):
            row = list(row) + ["", "", ""]
            p, d = str(row[0]), str(row[1])
            col = ""
            m = re.match(r"\s*(\d+)", p)
            if m:
                spec = mp.get("顺应性%s" % m.group(1)) or ""
                col = spec.split("|")[0]
            tk.Label(grid, text=p, bg=T.CARD, fg=T.TEXT,
                     font=(UI_FONT, 8), anchor="w").grid(row=ri, column=0, sticky="w")
            tk.Label(grid, text=d, bg=T.CARD, fg=T.TEXT,
                     font=(UI_FONT, 8, "bold"), anchor="w").grid(row=ri, column=1, sticky="w")
            tk.Label(grid, text=col[:16], bg=T.CARD, fg=T.TEXT3,
                     font=(UI_FONT, 7), anchor="w").grid(row=ri, column=2, sticky="w")
        self._num_row("字号", g.get("th", 1.95),
                      lambda v: self._set_comp_font(idx, v))


    def _set_comp_font(self, idx, v):
        # 字号属于数值那一半（L4）；从线框那边改也落到数值上。
        g = self._comp_font_target(idx)
        v = max(0.6, round(float(v), 2))
        g["th"] = v
        g["th_r"] = round(v * 0.74, 2)
        g["th_note"] = round(v * 0.59, 2)
        self.save()
        self.redraw()
        self.panel()

    def _barcode_editor_g(self, idx):
        """L2 里选中条码时的面板：内容 / 模块宽 / 高度 / 编码模式。"""
        g = self.tpl["图形"][idx]
        self._h("条码内容（可写【NOTE 14】）")
        var = tk.StringVar(value=str(g.get("seq", "")))
        e = tk.Entry(self.pnl, textvariable=var, font=(UI_FONT, 9), relief="flat",
                     bg=T.DZ_BG, highlightthickness=1, highlightbackground=T.BORDER)
        e.pack(fill="x", ipady=3, pady=(0, 4))

        def commit(*_a):
            v = var.get().strip()
            if v == str(g.get("seq", "")):
                return
            self.push_history()
            g["seq"] = v
            self.save()
            self.redraw()
            self.panel()

        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)

        self._num_row("X", g["x"], lambda v: g.__setitem__("x", round(v, 2)))
        self._num_row("Y", g["y"], lambda v: g.__setitem__("y", round(v, 2)))
        # 模块宽：模板里没写就用「总宽 ÷ 模块数」的实测值当默认，
        # 这样数字和画面永远一致；改了它就按它排（条码会跟着变宽变窄）。
        _seq = PH.fill_placeholders(str(g.get("seq", "")), self._fields() or {})
        _w, _mod = barcode.geom(_seq, g.get("mode", "C"), g.get("mod"), g.get("w"))
        self._num_row("模块宽", _mod,
                      lambda v: g.__setitem__("mod", max(0.1, round(v, 3))),
                      unit="mm", width=9)
        self._num_row("高度", g.get("h", 9.0),
                      lambda v: g.__setitem__("h", max(1.0, round(v, 2))))

        # GS1 对 Code128 的 X 尺寸要求 0.25~0.50 mm。低于 0.25 印出来是废码，
        # 屏幕上完全看不出来 —— 必须在这里说，不能等客户扫不出来才发现。
        if _mod < barcode.X_MIN:
            msg, fg = ("%.3f mm 偏细（GS1 ≥ %.2f）" % (_mod, barcode.X_MIN)), T.RED
        elif _mod > barcode.X_MAX:
            msg, fg = ("%.3f mm 偏粗（GS1 ≤ %.2f）" % (_mod, barcode.X_MAX)), T.AMBER
        else:
            msg, fg = ("%.3f mm ✓" % _mod), T.TEXT3
        tk.Label(self.pnl, text=msg, bg=T.CARD, fg=fg,
                 font=(UI_FONT, T.SZ_FOOT), justify="left", anchor="w",
                 wraplength=238).pack(fill="x", pady=(2, 4))

        self._h("编码模式")
        mv = tk.StringVar(value="C 码（纯数字，短）" if g.get("mode", "C") == "C"
                          else "B 码（含字母）")
        cb = ttk.Combobox(self.pnl, textvariable=mv, state="readonly", font=(UI_FONT, 9),
                          values=["C 码（纯数字，短）", "B 码（含字母）"])
        cb.pack(fill="x", pady=(0, 4))

        def set_mode(*_a):
            want = "C" if mv.get().startswith("C") else "B"
            if want != g.get("mode", "C"):
                self.push_history()
                g["mode"] = want
                self.save()
                self.redraw()

        cb.bind("<<ComboboxSelected>>", set_mode)

    def _barcode_editor(self, idx):
        """条码块的面板：内容 / 模块宽 / 高度 / 编码模式。"""
        b = self.tpl["文字"][idx]
        bar = b["bars"][0]
        self._h("条码内容（可写【NOTE 14】）")
        var = tk.StringVar(value=str(bar.get("seq", "")))
        e = tk.Entry(self.pnl, textvariable=var, font=(UI_FONT, 9), relief="flat",
                     bg=T.DZ_BG, highlightthickness=1, highlightbackground=T.BORDER)
        e.pack(fill="x", ipady=3, pady=(0, 4))

        def commit(*_a):
            v = var.get().strip()
            if v == str(bar.get("seq", "")):
                return
            self.push_history()
            bar["seq"] = v
            self.save()
            self.redraw()
            self.panel()

        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)

        self._num_row("X", bar.get("x", 0),
                      lambda v: bar.__setitem__("x", round(v, 2)))
        self._num_row("Y", bar.get("y", 0),
                      lambda v: bar.__setitem__("y", round(v, 2)))
        self._num_row("模块宽", bar.get("mod", 0.3),
                      lambda v: bar.__setitem__("mod", max(0.1, round(v, 2))))
        self._num_row("高度", bar.get("h", 10.0),
                      lambda v: bar.__setitem__("h", max(1.0, round(v, 2))))

        self._h("编码模式")
        mv = tk.StringVar(value="C 码（纯数字，短）" if bar.get("mode", "C") == "C"
                          else "B 码（含字母）")
        cb = ttk.Combobox(self.pnl, textvariable=mv, state="readonly", font=(UI_FONT, 9),
                          values=["C 码（纯数字，短）", "B 码（含字母）"])
        cb.pack(fill="x", pady=(0, 4))

        def set_mode(*_a):
            want = "C" if mv.get().startswith("C") else "B"
            if want != bar.get("mode", "C"):
                self.push_history()
                bar["mode"] = want
                self.save()
                self.redraw()

        cb.bind("<<ComboboxSelected>>", set_mode)

    def _linked_col(self, name: str) -> str:
        """这个占位符最后落在数据表的哪一列（查映射、再按列名匹配）。

        【球囊直径】不叫 NOTE 03：中间隔着一层「数据映射」。不把它翻出来，
        面板上永远显示「（不链接）」，看着像没连上。
        """
        if not name:
            return ""
        head = list(self.data.get("head") or [])
        spec = (databind.mapping(self.tpl) or {}).get(name) or name
        spec = str(spec).split("|")[0]
        if spec.startswith("="):
            return ""                      # 派生字段（=【生产日期+3y】）没有对应列
        # match_col 现在返回 (列名, 置信度)；这里只要列名
        return databind.match_col(head, spec.split("#")[0], with_conf=False) or ""

    def _bind_rows(self, idx):
        """这一块链接到数据表的哪一列（下拉里就是导入表的表头）。

        两种块都要管：
          · 老结构（多行，`lines`）→ 一行一个下拉；
          · 新结构（一整块一段文字，只有 `t`）→ 一个下拉。
        以前只处理了前者，新加的 L4 文字在面板上根本看不到「链接到哪」，
        只能靠记住占位符里写的是什么。
        """
        b = self.tpl["文字"][idx]
        self._h("链接到数据列")
        if not self.data["recs"]:
            tk.Label(self.pnl, text="先导入数据表，才有列可选",
                     bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, T.SZ_FOOT),
                     anchor="w").pack(fill="x", pady=(2, 4))
            return
        cols = ["（不链接）"] + list(self.data["head"])
        lines = b.get("lines")
        if not lines:
            used = PH.used_keys(str(b.get("t") or ""))
            fld = used[0] if used else ""
            col = self._linked_col(fld) if fld else ""
            if fld:
                spec = str((databind.mapping(self.tpl) or {}).get(fld) or "")
                if spec.startswith("="):
                    hint, fg = "%s → 由 %s 推算（表里没有这一列）" % (fld, spec[1:]), T.TEXT3
                elif col:
                    hint, fg = "%s → %s" % (fld, col), T.TEXT3
                else:
                    hint, fg = "%s → 表里没有对应列" % fld, T.AMBER
                tk.Label(self.pnl, text=hint, bg=T.CARD, fg=fg,
                         font=(UI_FONT, T.SZ_FOOT), anchor="w",
                         wraplength=250, justify="left").pack(fill="x", pady=(0, 2))
            cur = col if col in cols else "（不链接）"
            var = tk.StringVar(value=cur)
            cbx = ttk.Combobox(self.pnl, textvariable=var, values=cols,
                               state="readonly", font=(UI_FONT, 9))
            cbx.pack(fill="x", pady=(2, 4))
            cbx.bind("<<ComboboxSelected>>",
                     lambda e, i=idx, v=var: self._bind_text(i, v.get()))
            return
        for li, ln in enumerate(lines):
            row = tk.Frame(self.pnl, bg=T.CARD)
            row.pack(fill="x", pady=1)
            tk.Label(row, text="行%d" % (li + 1), bg=T.CARD, fg=T.TEXT2,
                     width=4, anchor="w", font=(UI_FONT, 8)).pack(side="left")
            used = PH.used_keys(ln.get("t", ""))
            cur = used[0] if used and used[0] in cols else "（不链接）"
            var = tk.StringVar(value=cur)
            cbx = ttk.Combobox(row, textvariable=var, values=cols, state="readonly",
                               width=15, font=(UI_FONT, 8))
            cbx.pack(side="left", fill="x", expand=True)
            cbx.bind("<<ComboboxSelected>>",
                     lambda e, i=idx, k=li, v=var: self._bind_line(i, k, v.get()))

    def _bind_text(self, idx, col):
        """把整块文字链接到某一列（新结构：只有 `t`，没有 `lines`）。"""
        b = self.tpl["文字"][idx]
        t = str(b.get("t") or "")
        self.push_history()
        if col.startswith("（"):
            b["t"] = re.sub(r"【.+?】", "", t).strip()
            b["layer"] = "L3"          # 不链接数据了，它就不再是数据层
        else:
            b["t"] = (re.sub(r"【.+?】", "【%s】" % col, t, count=1) if "【" in t
                      else "【%s】" % col)
            b["layer"] = "L4"
        self.save()
        self.redraw()
        self.panel()
        self.set_status("已链接到「%s」" % col)

    def _bind_line(self, idx, li, col):
        """把某一行绑到某一列：替换（或补上）占位符，整块归 L4。"""
        b = self.tpl["文字"][idx]
        if li >= len(b.get("lines") or []):
            return
        ln = b["lines"][li]
        t = ln.get("t", "")
        self.push_history()
        if col.startswith("（"):
            ln["t"] = re.sub(r"【.+?】", "", t).strip()
        elif "【" in t:
            ln["t"] = re.sub(r"【.+?】", "【%s】" % col, t, count=1)
        else:
            # 先去掉行尾空格再加，否则「型号规格: 」+「 【NOTE】」会变成两个空格
            ln["t"] = ((t.rstrip() + " 【%s】" % col) if t.strip() else "【%s】" % col)
        b["layer"] = "L4"
        self.save()
        self.redraw()
        self.panel()
        self.set_status("行%d 已链接到「%s」" % (li + 1, col))

    def _pick_items(self, lb, items):
        """列表里混着图形和表格时按下标取真正要选的那个。"""
        cur = lb.curselection()
        if not cur or cur[0] >= len(items):
            return
        target = tuple(items[cur[0]])
        if self.sel is not None and tuple(self.sel) == target:
            return
        self.sel, self.sel_more = target, []
        self.redraw()
        self.panel()

    def _pick_list(self, lb, kind, layer=None, idx_map=None):
        """列表点选。

        注意：panel() 会重建 Listbox，重建时 selection_set() 又会触发
        <<ListboxSelect>>，如果不判断「已经选的就是它」就会无限递归 —— 表现是
        选文字时闪烁、卡顿、选中乱跳。

        idx_map: 列表是过滤过的（L2 只列固定图形、L4 只列数据块）时，
        列表第 n 行对应的是模板里的第 idx_map[n] 个 —— 不能拿 n 当索引。
        """
        cur = lb.curselection()
        if not cur:
            return
        if kind == "g":
            if idx_map is not None:
                if cur[0] >= len(idx_map):
                    return
                target = ("g", idx_map[cur[0]])
            else:
                target = ("g", cur[0])
        else:
            # 口径和面板列表同一套：读元素自己的层（没有才推断），
            # 不能用 `b.get("layer", "L3")` —— 旧模板里没写 layer 的块
            # 会被算成 L3，L4 列表点一下就选到别人身上。
            blocks = [i for i, b in enumerate(self.tpl["文字"])
                      if layers.of(b, "t") == (layer or "L3")]
            if cur[0] >= len(blocks):
                return
            target = ("t", blocks[cur[0]])
        if self.sel == target:      # 面板重建触发的重复事件，直接忽略
            return
        self.sel, self.sel_more = target, []
        self.redraw()
        self.panel()

    def _move_block(self, idx, new_x=None, new_y=None):
        """整块平移到指定左上角（保持各行相对位置）。

        面板里的 X/Y 是**包围盒左边/上沿**，而新结构块的 b["x"] 是锚点 ——
        居中对齐的块锚点在中间。直接写回会把块整体右移半个字宽，
        所以按「包围盒差多少、锚点挪多少」来算。
        """
        b = self.tpl["文字"][idx]
        if not b.get("lines"):
            bb = self._bbox_of("t", idx) or (0, 0, 0, 0)
            if new_x is not None:
                b["x"] = round(float(b.get("x") or 0) + (new_x - bb[0]), 2)
            if new_y is not None:
                b["y"] = round(float(b.get("y") or 0) + (new_y - bb[1]), 2)
            return
        minx = min(l["x"] for l in b["lines"])
        miny = min(l["y"] for l in b["lines"])
        dx = (new_x - minx) if new_x is not None else 0
        dy = (new_y - miny) if new_y is not None else 0
        for l in b["lines"]:
            l["x"] = round(l["x"] + dx, 2)
            l["y"] = round(l["y"] + dy, 2)

    def _set_block_h(self, idx, v):
        """改新结构块的字号（h 就是字高，字宽自动跟着走）。"""
        b = self.tpl["文字"][idx]
        b["h"] = round(max(0.6, float(v)), 2)
        if b.get("type") == "list" and b.get("step"):
            b["step"] = round(max(0.8, float(v) * 1.32), 2)

    def _resize_block(self, idx, sz):
        """改整块字号，行距按同比例跟着变（保持排版疏密）。"""
        b = self.tpl["文字"][idx]
        if not b.get("lines"):
            return
        old = b["lines"][0].get("sz", 3) or 3
        ratio = (sz / old) if old else 1
        miny = min(l["y"] for l in b["lines"])
        for l in b["lines"]:
            l["y"] = round(miny + (l["y"] - miny) * ratio, 2)
            l["sz"] = round(max(0.5, sz), 2)

    def _set_table_align(self, idx, how):
        b = self.tpl["文字"][idx]
        if (b.get("align") or "center") == how:
            return
        self.push_history()
        b["align"] = how
        self.save()
        self.redraw()
        self.panel()

    def open_comp_editor(self, idx):
        """顺应性卡的独立编辑窗口（2026-11-20 用户要求）。

        用户原话：「这个表要进行右键之后有单独的编辑位置。它虽然是图形，
        但是它应该要有专门的编辑空间……跳转到一个专门的地方去编辑。」
        还有：「这个表和这个字应该是分离的」—— 表格线和文字是两套字号，
        分开调。

        为什么必须独立窗口：右栏只有 270px，塞不下"档数 × 两列 + 后缀字号"，
        而且这块东西有 5 个维度（左右边界、行高、三档字号、行数），挤在
        侧栏里根本看不清谁改了什么。
        """
        win = getattr(self, "_comp_win", None)
        if win is not None:
            try:
                if win.winfo_exists():
                    win.lift()
                    win.focus_set()
                    return
            except tk.TclError:
                pass

        j = self._comp_twin(idx)
        frame_idx = idx if self.tpl["图形"][idx].get("type") == "comp" else j
        vals_idx = j if self.tpl["图形"][idx].get("type") == "comp" else idx
        if frame_idx is None or vals_idx is None:
            self.set_status("这张顺应性卡缺少配对的一半（表格线 / 数值）")
            return
        fr = self.tpl["图形"][frame_idx]
        vv = self.tpl["图形"][vals_idx]

        win = tk.Toplevel(self.root)
        win.title("顺应性卡 · 独立编辑")
        win.configure(bg=T.BG)
        win.geometry("560x620")
        win.idx = idx
        self._comp_win = win

        head = tk.Frame(win, bg=T.BG)
        head.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(head, text="顺应性卡", bg=T.BG, fg=T.TEXT,
                 font=(UI_FONT, T.SZ_DLG, "bold")).pack(side="left")
        tk.Label(head, text="表格线（L2）与数值（L4）分开调",
                 bg=T.BG, fg=T.TEXT3, font=(UI_FONT, T.SZ_FOOT)
                 ).pack(side="left", padx=(10, 0), pady=(4, 0))

        body = tk.Frame(win, bg=T.BG)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        def section(title, color):
            box = tk.Frame(body, bg=T.CARD)
            box.pack(fill="x", pady=(0, 10))
            tk.Frame(box, bg=color, width=4).pack(side="left", fill="y")
            inner = tk.Frame(box, bg=T.CARD)
            inner.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            tk.Label(inner, text=title, bg=T.CARD, fg=color,
                     font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x")
            return inner

        def num_row(parent, label, value, on_set, step=0.05, lo=0.3, hi=200.0):
            row = tk.Frame(parent, bg=T.CARD)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=T.CARD, fg=T.TEXT2, width=12,
                     anchor="w", font=(UI_FONT, 9)).pack(side="left")
            var = tk.StringVar(value="%.2f" % float(value))

            def apply(*_a):
                try:
                    v = float(var.get())
                except ValueError:
                    return
                on_set(max(lo, min(hi, v)))
                self.save()
                self.redraw()

            e = tk.Entry(row, textvariable=var, width=9, font=(UI_FONT, 9),
                         bd=0, highlightthickness=1,
                         highlightbackground=T.BORDER)
            e.pack(side="left")
            e.bind("<Return>", apply)
            e.bind("<FocusOut>", apply)
            tk.Label(row, text="mm", bg=T.CARD, fg=T.TEXT3,
                     font=(UI_FONT, 8)).pack(side="left", padx=(4, 8))

            def bump(d):
                # 以输入框当前值为准（可能是用户刚手输的），改完写回
                try:
                    base = float(var.get())
                except ValueError:
                    base = float(value)
                nv = max(lo, min(hi, round(base + d, 2)))
                on_set(nv)
                var.set("%.2f" % nv)
                self.save()
                self.redraw()

            for lab, d in (("−", -step), ("＋", step)):
                T.Btn(row, text=lab, relief="flat", bg=T.DZ_BG, padx=7, pady=1,
                      font=(UI_FONT, 9),
                      command=lambda dd=d: bump(dd)).pack(side="left", padx=1)

        # ① 表格线
        s1 = section("表格线（图形层 L2）", "#2E7D32")
        num_row(s1, "左边界", float(fr.get("x0") or 0),
                lambda v: fr.__setitem__("x0", round(v, 2)))
        num_row(s1, "右边界", float(fr.get("x1") or 0),
                lambda v: fr.__setitem__("x1", round(v, 2)))
        num_row(s1, "上限 y", float((fr.get("ys") or [0])[0]),
                lambda v: self._comp_set_edge(fr, vv, "top", v))
        num_row(s1, "下限 y", float((fr.get("ys") or [0])[-1]),
                lambda v: self._comp_set_edge(fr, vv, "bottom", v))
        row1 = tk.Frame(s1, bg=T.CARD)
        row1.pack(fill="x", pady=(4, 0))
        tk.Label(row1, text="档数", bg=T.CARD, fg=T.TEXT2, width=12, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left")
        tk.Label(row1, text="%d 档（表头 + 数据）" % max(0, len(fr.get("ys") or []) - 2),
                 bg=T.CARD, fg=T.TEXT, font=(UI_FONT, 9)).pack(side="left")

        # ② 数值文字
        s2 = section("数值文字（数据层 L4）", "#C62828")
        num_row(s2, "左列字号", float(vv.get("th") or 1.95),
                lambda v: vv.__setitem__("th", round(v, 2)), step=0.05)
        num_row(s2, "右列字号", float(vv.get("th_r") or 1.45),
                lambda v: vv.__setitem__("th_r", round(v, 2)), step=0.05)
        num_row(s2, "后缀字号", float(vv.get("th_note") or 1.15),
                lambda v: vv.__setitem__("th_note", round(v, 2)), step=0.05)
        num_row(s2, "行内留白", float(vv.get("gap") or 0.2),
                lambda v: vv.__setitem__("gap", round(v, 2)), step=0.05)

        foot = tk.Frame(body, bg=T.BG)
        foot.pack(fill="x")
        T.Btn(foot, text="在画布上选中表格线", relief="flat", bg=T.DZ_BG,
              pady=4, command=lambda: (win.destroy(),
                                       self._goto_graph(frame_idx, "L2"))
              ).pack(side="left")
        T.Btn(foot, text="关闭", relief="flat", bg=T.DZ_BG, pady=4,
              command=win.destroy).pack(side="right")

    def _comp_set_edge(self, fr, vv, which, v):
        """改表格上下边界：按比例重排所有行 y（保持等分行高）。"""
        ys = list(fr.get("ys") or [])
        if len(ys) < 2:
            return
        top = ys[0] if which != "top" else v
        bot = ys[-1] if which != "bottom" else v
        if bot <= top + 1.0:
            return
        n = len(ys) - 1
        step = (bot - top) / n
        fr["ys"] = [round(top + step * i, 2) for i in range(n + 1)]
        vv["ys"] = list(fr["ys"])

    def open_table_editor(self, idx):
        """开表格的独立编辑窗口（列头 A/B/C，点格打字，像 Excel）。

        以前表格编辑挤在右栏 270px 里：4 列就装不下，加行按钮被顶到窗口外，
        改列宽只能去画布上抓那几条蓝虚线 —— 抓歪一点整张表就改坏了。
        现在画布只负责「摆在哪、多大」，改内容一律进这个窗口。
        """
        win = getattr(self, "_table_win", None)
        if win is not None:
            try:
                if win.winfo_exists() and getattr(win, "idx", None) == idx:
                    win.lift()
                    win.focus_set()
                    return win
                if win.winfo_exists():
                    win.destroy()          # 一次只编一张表，换表就把旧窗收掉
            except Exception:
                pass
        try:
            win = table_editor.TableEditor(self, idx)
        except Exception as e:
            messagebox.showerror("打不开表格编辑", str(e))
            return None
        self._table_win = win
        self.set_status("表格编辑窗口已打开（关掉后按 ⌘Z 可整次退回）")
        return win

    def _is_table(self, idx) -> bool:
        try:
            return (self.tpl["文字"][idx].get("type") or "") == "table"
        except Exception:
            return False

    def _table_editor(self, idx):
        """右栏只放「概要 + 一个入口」，真编辑去独立窗口（ui/table_editor.py）。

        以前这里塞了一整张网格：270px 宽装不下 4 列，加行按钮被顶到窗口外，
        改列宽只能去画布上抓那几条蓝虚线 —— 抓歪一点整张表就废了。
        现在这里只回答「几行几列、多大」，要改就点按钮（或者直接双击表格）。
        """
        b = self.tpl["文字"][idx]
        cols = [float(c) for c in (b.get("cols") or [10.0])]
        rows = [float(r) for r in (b.get("rows") or [4.0])]
        self._h("表格")
        T.Btn(self.pnl, text="✎ 编辑表格…", relief="flat", bg=T.PRIMARY,
              fg="#ffffff", pady=6, font=(UI_FONT, T.SZ_BODY, "bold"),
              command=lambda: self.open_table_editor(idx)
              ).pack(fill="x", pady=(2, 5))
        tk.Label(self.pnl, text="%d 行 × %d 列 · %.0f × %.0f mm"
                 % (len(rows), len(cols), sum(cols), sum(rows)),
                 bg=T.CARD, fg=T.TEXT, font=(UI_FONT, T.SZ_SMALL),
                 anchor="w").pack(fill="x")
        tk.Label(self.pnl,
                 text="打开后：列头 A/B/C、行头 1/2/3，点格直接打字，"
                      "Tab 往右、Enter 往下；列宽行高填数字，＋行/－行按当前格插删。",
                 bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, T.SZ_SMALL),
                 wraplength=270, justify="left", anchor="w").pack(fill="x", pady=(2, 6))
        T.Btn(self.pnl, text="整表当文本改（Tab 分列）", relief="flat", bg=T.CARD,
              pady=3, font=(UI_FONT, T.SZ_SMALL),
              command=lambda: self._table_text_mode(idx)
              ).pack(fill="x", pady=(2, 0))
        T.Btn(self.pnl, text="复制这个表格", relief="flat", bg=T.CARD, pady=4,
              font=(UI_FONT, T.SZ_SMALL),
              command=self.copy_selected).pack(fill="x", pady=(4, 0))
        tk.Label(self.pnl,
                 text="画布上拖 8 个手柄 = 整表缩放；某一列多宽、某一行多高，"
                      "进编辑窗口填。",
                 bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, T.SZ_SMALL),
                 wraplength=270, justify="left", anchor="w").pack(fill="x", pady=(4, 0))


    def _grid_focus(self, r, c):
        """记住「当前格」—— 插入/删除行列按它来。"""
        if not self.tpl or not self.sel or self.sel[0] != "t":
            return
        if not hasattr(self, "_grid_cur"):
            self._grid_cur = {}
        self._grid_cur[id(self.tpl["文字"][self.sel[1]])] = (r, c)

    def _grid_commit(self, idx, kind, var, r, c):
        """格子 / 列宽 / 行高 改完就落地。不重建面板 —— 重建会把焦点弄丢。"""
        if not (0 <= idx < len(self.tpl.get("文字") or [])):
            return
        b = self.tpl["文字"][idx]
        raw = var.get()
        try:
            if kind == "col":
                v = round(max(2.0, float(raw)), 1)
                if abs(v - float(b["cols"][c])) < 1e-9:
                    return
                self.push_history()
                b["cols"][c] = v
                self.set_status("第 %d 列宽 %.1f mm" % (c + 1, v))
            elif kind == "row":
                v = round(max(1.2, float(raw)), 1)
                if abs(v - float(b["rows"][r])) < 1e-9:
                    return
                self.push_history()
                b["rows"][r] = v
                self.set_status("第 %d 行高 %.1f mm" % (r + 1, v))
            else:
                cells = b.setdefault("cells", [])
                while len(cells) < len(b.get("rows") or []):
                    cells.append([""] * len(b.get("cols") or []))
                while len(cells) <= r:
                    cells.append([""] * len(b.get("cols") or []))
                row = cells[r]
                while len(row) < len(b.get("cols") or []):
                    row.append("")
                if row[c] == raw:
                    return
                self.push_history()
                row[c] = raw
                self._auto_layer(b, "\n".join("\t".join(x) for x in cells))
        except (ValueError, IndexError, KeyError):
            return
        self.save()
        self.redraw()

    def _table_text_mode(self, idx):
        """退回「整表当文本改」：几十格的大表，网格里一格格敲太慢时用它。"""
        b = self.tpl["文字"][idx]
        win = tk.Toplevel(self.root)
        win.title("表格内容")
        win.configure(bg=T.BG)
        win.geometry("540x470")
        win.transient(self.root)
        tk.Label(win, text="Tab 分列，换行分行。可以直接从 Excel 复制一大片粘进来。",
                 bg=T.BG, fg=T.TEXT2, font=(UI_FONT, 9), anchor="w",
                 wraplength=490, justify="left").pack(fill="x", padx=16, pady=(12, 6))
        txt = tk.Text(win, font=(UI_FONT, 10), wrap="none", bd=0,
                      highlightthickness=1, highlightbackground=T.BORDER)
        txt.pack(fill="both", expand=True, padx=16, pady=4)
        txt.insert("1.0", "\n".join("\t".join(str(c) for c in row)
                                    for row in (b.get("cells") or [])))

        def ok():
            self._save_table(idx, txt)
            win.destroy()

        bar = tk.Frame(win, bg=T.BG)
        bar.pack(fill="x", padx=16, pady=(4, 12))
        T.Btn(bar, text="取消", command=win.destroy, bg=T.CARD, padx=14,
              pady=5).pack(side="right")
        T.Btn(bar, text="保存", command=ok, bg=T.PRIMARY, padx=18,
              pady=5).pack(side="right", padx=6)

    def _table_merge(self, idx, r0, r1, c0, c1):
        """合并矩形区域（像苹果备忘录/Excel 的合并单元格）。

        合并规则（和备忘录一致，简单可预期）：
          · 区域内的文字**按行从上到下、行内从左到右**拼起来，放进左上角格
          · 其它格清空
          · 在 b["merges"] 里记一条 [r0, c0, r1, c1]，渲染时这几个格子
            画成一整块（框线合并、文字居中）
        """
        if self.readonly:
            return self._ro_block()
        b = self.tpl["文字"][idx]
        rows = list(b.get("rows") or [4.0])
        cols = list(b.get("cols") or [10.0])
        cells = [list(r) for r in (b.get("cells") or [])]
        r0, r1 = max(0, min(r0, r1)), min(len(rows) - 1, max(r0, r1))
        c0, c1 = max(0, min(c0, c1)), min(len(cols) - 1, max(c0, c1))
        if r0 == r1 and c0 == c1:
            return
        parts = []
        for rr in range(r0, r1 + 1):
            if rr >= len(cells):
                continue
            for cc in range(c0, c1 + 1):
                if cc >= len(cells[rr]):
                    continue
                t = str(cells[rr][cc] or "").strip()
                if t and (rr, cc) != (r0, c0):
                    parts.append(t)
                if (rr, cc) != (r0, c0):
                    cells[rr][cc] = ""
        if parts:
            base = str(cells[r0][c0] or "").strip()
            cells[r0][c0] = (base + (" " if base else "") + " ".join(parts)).strip()
        merges = [list(m) for m in (b.get("merges") or [])]
        merges = [m for m in merges
                  if not (r0 <= m[0] <= r1 and c0 <= m[1] <= c1)]
        merges.append([r0, c0, r1, c1])
        self.push_history()
        b["cells"] = cells
        b["merges"] = merges
        self.save()
        self.redraw()
        self.set_status("已合并 %d×%d 个格" % (r1 - r0 + 1, c1 - c0 + 1))

    def _table_split(self, idx, r, c):
        """拆开包含这一格的合并块（恢复成独立格）。"""
        if self.readonly:
            return self._ro_block()
        b = self.tpl["文字"][idx]
        merges = [list(m) for m in (b.get("merges") or [])]
        keep = [m for m in merges
                if not (m[0] <= r <= m[2] and m[1] <= c <= m[3])]
        if len(keep) == len(merges):
            self.set_status("这一格没有合并，不用拆")
            return
        self.push_history()
        b["merges"] = keep
        self.save()
        self.redraw()
        self.set_status("已拆分")

    def _table_shape(self, idx, d_rows: int, d_cols: int, at=None):
        """加 / 减一行或一列；给了 at 就在那一行（列）上动手。

        以前只能往末尾加、从末尾删 —— 想在表头下面插一行，只能先加到最后
        再一格一格把内容挪下去。现在按「当前选中的格」插/删，
        跟 Excel 里右键插入行是一个意思。
        """
        if self.readonly:
            return self._ro_block()
        b = self.tpl["文字"][idx]
        cols = list(b.get("cols") or [10.0])
        rows = list(b.get("rows") or [4.0])
        cells = [list(r) for r in (b.get("cells") or [])]
        if at is None:
            cur = self._grid_cur.get(id(b)) if hasattr(self, "_grid_cur") else None
            at = cur or (0, 0)
        ar, ac = at
        ar = max(0, min(ar, len(rows) - 1))
        ac = max(0, min(ac, len(cols) - 1))
        if d_rows:
            if d_rows > 0:
                rows.insert(ar + 1, rows[ar] or 4.0)
                cells.insert(ar + 1, [""] * len(cols))
            elif len(rows) > 1:
                rows.pop(ar)
                if ar < len(cells):
                    cells.pop(ar)
        if d_cols:
            if d_cols > 0:
                cols.insert(ac + 1, cols[ac] or 10.0)
                for r in cells:
                    r.insert(min(ac + 1, len(r)), "")
            elif len(cols) > 1:
                cols.pop(ac)
                for r in cells:
                    if len(r) > ac:
                        r.pop(ac)
        while len(cells) < len(rows):
            cells.append([""] * len(cols))
        cells = [(r + [""] * len(cols))[:len(cols)] for r in cells[:len(rows)]]
        self.push_history()
        b["cols"], b["rows"], b["cells"] = cols, rows, cells
        self._grid_cur[id(b)] = (min(ar, len(rows) - 1), min(ac, len(cols) - 1))
        self.save()
        self.redraw()
        self.panel()

    def _save_table(self, idx, widget):
        b = self.tpl["文字"][idx]
        rows = widget.get("1.0", "end").rstrip("\n").split("\n")
        ncol = len(b.get("cols") or [4])
        cells = []
        for ln in rows:
            r = [c for c in ln.split("\t")]
            while len(r) < ncol:
                r.append("")
            cells.append(r[:ncol])
        self.push_history()
        b["cells"] = cells
        b["rows"] = [4.5] + [3.4] * max(0, len(cells) - 1)
        self._auto_layer(b, "\n".join("\t".join(r) for r in cells))
        self.save()
        self.redraw()
        self.panel()

    def _auto_layer(self, b, text=None):
        """按内容猜层，但只猜「没被手动钉住」的块。

        以前每次保存都重算一遍归属，于是「手动把【批号】改到 L3」这种操作
        一编辑就被打回 L4 —— 手动改的层必须留得住。
        `层手动` 是 `_move_layer_to` 打上的标记：只有手动选的层和内容本来的
        判断不一致时才会有，所以自动维护的块照样跟着内容走。
        """
        if b.get("层手动"):
            return
        txt = str(b.get("t") or "") if text is None else str(text)
        if b.get("type") == "table":
            # 表格本身是固定版面（L2）；格子里写了【】才是吃数据（L4）
            b["layer"] = "L4" if "【" in txt else "L2"
            return
        b["layer"] = "L4" if "【" in txt else "L3"

    def _text_editor(self, idx):
        b = self.tpl["文字"][idx]
        if b.get("type") == "list":
            self._list_editor(idx)
            return
        self._h("文字")
        legacy = b.get("lines") is not None
        body = ("\n".join(l.get("t", "") for l in b["lines"]) if legacy
                else (b.get("t") or ""))
        txt = tk.Text(self.pnl, height=min(9, max(3, body.count("\n") + 2)),
                      font=(UI_FONT, 9), wrap="word", bd=0,
                      highlightthickness=1, highlightbackground=T.BORDER)
        txt.pack(fill="x", pady=4)
        txt.insert("1.0", body)
        T.Btn(self.pnl, text="保存", relief="flat", bg=T.PRIMARY, fg=T.CARD,
              pady=4,
              command=lambda: self._save_text(idx, txt)).pack(fill="x")

    def _list_editor(self, idx):
        """多行块：一行一条，内容存在「字段」指向的那串里，不在 `t` 上。

        左下角那段注册信息（9 行、行距固定）就是这种块。以前这里照样去读
        `t`，读出来是空的 —— 看着像「这块没内容」，其实是根本没问对地方。
        """
        b = self.tpl["文字"][idx]
        field = str(b.get("字段") or "")
        val = (self._fields() or {}).get(field)
        items = list(val) if isinstance(val, (list, tuple)) else str(val or "").split("\n")
        items = [str(x) for x in items if str(x)]
        tk.Label(self.pnl, text="多行块 · 字段「%s」· 一行一条" % (field or "（没填）"),
                 bg=T.CARD, fg=T.TEXT3, font=(UI_FONT, 8), anchor="w",
                 wraplength=250, justify="left").pack(fill="x", pady=(2, 0))
        txt = tk.Text(self.pnl, height=min(10, max(4, len(items) + 1)),
                      font=(UI_FONT, 9), wrap="word", bd=0,
                      highlightthickness=1, highlightbackground=T.BORDER)
        txt.pack(fill="x", pady=4)
        txt.insert("1.0", "\n".join(items))
        if field and field in ((self.tpl or {}).get("数据映射") or {}):
            tk.Label(self.pnl, text="这一块跟着数据表走：改这里只改示例值。",
                     bg=T.CARD, fg=T.AMBER, font=(UI_FONT, 8), anchor="w",
                     wraplength=250, justify="left").pack(fill="x", pady=(0, 4))
        T.Btn(self.pnl, text="保存", relief="flat", bg=T.PRIMARY, fg=T.CARD,
              pady=4,
              command=lambda: self._save_list(idx, txt)).pack(fill="x")

    def _save_list(self, idx, widget):
        """多行块存回「示例数据」里那串（一行一条）。"""
        b = self.tpl["文字"][idx]
        field = str(b.get("字段") or "")
        if not field:
            self.set_status("这个多行块没写「字段」，没地方存")
            return
        rows = [ln for ln in widget.get("1.0", "end").rstrip("\n").split("\n")
                if ln.strip()]
        self.push_history()
        self.tpl.setdefault("示例数据", {})[field] = rows
        # 行数上限跟着涨：这个块的 `max` 是「最多印几行」，加行数不跟着涨的话，
        # 多出来的那几行会被静悄悄截掉 —— 界面上明明写了，印出来没有。
        b["max"] = max(int(b.get("max") or 0), len(rows))
        self.save()
        self.redraw()
        self.panel()
        self.set_status("已改「%s」（%d 行）" % (field, len(rows)))

    def _save_text(self, idx, widget):
        self.push_history()
        b = self.tpl["文字"][idx]
        lines = widget.get("1.0", "end").rstrip("\n").split("\n")
        if b.get("lines") is None:
            # 3.0 的块只有一串文字，尺寸由 x/y/w/h 和「按字高定字号」决定
            b["t"] = lines[0] if len(lines) == 1 else " ".join(lines)
            self._auto_layer(b)
            self.save()
            self.redraw()
            self.panel()
            return
        old = b["lines"]
        x0 = old[0]["x"] if old else 20
        y0 = old[0]["y"] if old else 20
        sz = old[0]["sz"] if old else 3
        b["lines"] = [{"x": x0, "y": round(y0 + i * sz * 1.5, 2), "sz": sz,
                       "b": 0, "t": t} for i, t in enumerate(lines)]
        self._auto_layer(b, "\n".join(lines))
        self.save()
        self.redraw()
        self.panel()

    # ────────────────────────── 增删 ──────────────────────────
    def _ask_page(self, fp):
        """PDF 有多页时问一句取第几页，返回 0 开始的页号。

        单页文件不问 —— 点了就换，别让人多点一次确认。
        取消（返回 None）当成没选，调用方按「什么都没发生」处理。
        """
        try:
            n = refshot.page_count(fp)
        except Exception:
            return 0
        if n <= 1:
            return 0
        p = simpledialog.askinteger(
            "取第几页", "共 %d 页，取第几页？" % n, initialvalue=1,
            minvalue=1, maxvalue=n, parent=self.root)
        return None if p is None else p - 1

    def change_ref(self):
        """换叠影：从研发给的图纸里取，或者直接选一张图。

        图纸第 1 页常把设计稿整张置入，`engine/refshot.py` 会把那张图单独取出来
        （不带 NOTE 引线），存进模板自己的 `参考/`，模板里记相对路径。
        换完自动切到「叠影」视图 —— 换它就是为了马上看对不对齐，
        还要人再点一次顶部开关就太磨叽了。
        """
        if self.readonly:
            return self._ro_block()
        fp = filedialog.askopenfilename(
            title="换叠影",
            initialdir=(paths.IMG_LIB if os.path.isdir(paths.IMG_LIB) else paths.DATA),
            filetypes=[("图纸 / 图片", "*.pdf *.png *.jpg *.jpeg *.webp *.bmp")])
        if not fp:
            return
        i = 0
        if fp.lower().endswith(".pdf"):
            i = self._ask_page(fp)
            if i is None:
                return
        self.root.config(cursor="watch")
        self.set_status("正在从图纸取叠影…")
        self.root.update_idletasks()
        try:
            im, note = refshot.take(fp, i)
            # ★ 规则 1.2（2026-11-20 用户）：叠影必须"四边等宽"贴在白画布上。
            # 从 DWG 裁出来的图四周常带黑边（框线/NOTE 引线残留），
            # 不裁掉的话"居中"是假居中 —— 元素坐标整体偏一截，
            # 后面所有对位都跟着歪。所以先裁到墨迹包围盒。
            im = refshot.trim_black_border(im, pad_ratio=0.0)
            rel = refshot.save(im, os.path.splitext(os.path.basename(fp))[0],
                               self.tpl_name)
        except Exception as e:
            messagebox.showerror("换不了叠影", str(e))
            return
        finally:
            self.root.config(cursor="")
        self.push_history()
        self.tpl["参考图"] = rel
        # 换叠影一律从 (0,0) 重新起算 —— 上一张的偏移对这张没有意义。
        # 2026-11-20 用户明确：叠影要跟着背景模板走，不能在第一版上定死。
        # 所以这里**不缓存任何上一版的位置/尺寸**，每次换都是干净的一张。
        self.tpl["参考图X"] = 0.0
        self.tpl["参考图Y"] = 0.0
        self._ref_raw.clear()
        self._ref_aligned.clear()

        # ★ 规则 1.3（2026-11-20 用户）：白色画布**不是一成不变的** ——
        # 可能是长方形也可能是正方形，必须随 DWG 图纸变化。
        # 之前只在比例差 > 2% 时才改，而且只改高。现在改成：
        #   · 保宽（刀模宽度是产线定的，不能动）
        #   · 高按新叠影的真实宽高比重算
        #   · 比例差 > 0.5% 就改 —— 叠影已被裁到内容边界，它的比例就是
        #     这一版标签的真实比例
        try:
            from PIL import Image as _Im
            _fp = store.resolve_ref(self.tpl_name, rel)
            if _fp:
                _im = _Im.open(_fp)
                _ar_ref = _im.size[0] / float(_im.size[1])
                _cv = self.tpl.get("画布") or {}
                _w, _h = float(_cv.get("宽") or 0), float(_cv.get("高") or 0)
                if _w <= 0 or _h <= 0:
                    # 没有画布：直接用叠影尺寸（按 10px/mm 折算）
                    _w = round(_im.width / 10.0, 2)
                    _h = round(_im.height / 10.0, 2)
                    self.tpl["画布"] = {"宽": _w, "高": _h}
                    note += " · 画布按叠影建为 %.1f×%.1fmm" % (_w, _h)
                else:
                    _ar_cv = _w / _h
                    if abs(_ar_ref / _ar_cv - 1.0) > 0.005:
                        _nh = round(_w / _ar_ref, 2)
                        self.tpl["画布"] = {"宽": _w, "高": _nh}
                        note += " · 画布高按新叠影改为 %.2fmm" % _nh
        except Exception:
            pass

        self.set_cmp("ghost")
        self.save()
        self.set_status("叠影已换：%s —— 已从 (0,0) 起算；点「自动对齐」微调" % note)

    def change_base(self):
        if self.readonly:
            return self._ro_block()
        self.push_history()
        fp = filedialog.askopenfilename(
            title="换底图",
            initialdir=(paths.IMG_LIB if os.path.isdir(paths.IMG_LIB) else paths.DATA),
            filetypes=[("PDF / 图片", "*.pdf *.png *.jpg *.jpeg *.webp")])
        if not fp:
            return
        try:
            ext = os.path.splitext(fp)[1].lower()
            if ext == ".pdf":
                i = self._ask_page(fp)
                if i is None:
                    return
                w_mm, h_mm = refshot.page_size(fp, i)
                # 400 dpi：底图是印厂 PDF，最后要上印刷机。
                # 150 dpi 在屏幕上看着没问题，印出来刀模线是毛的。
                im = refshot.render(fp, i, 400)
                buf = io.BytesIO()
                im.save(buf, "PNG")
                raw = buf.getvalue()
                self.tpl["画布"] = {"宽": round(w_mm, 2), "高": round(h_mm, 2)}
            else:
                raw = open(fp, "rb").read()
            self.tpl["底图"] = "data:image/png;base64," + base64.b64encode(raw).decode()
            self.save()
            self.redraw()
            self.panel()
            self.set_status("底图已换：%s（%.1f×%.1f mm%s）" %
                            (os.path.basename(fp),
                             FRAME_MOD.canvas_rect(self.tpl)["w"],
                             FRAME_MOD.canvas_rect(self.tpl)["h"],
                             "，PDF 按 400 dpi 转图" if ext == ".pdf" else ""))
        except Exception as e:
            messagebox.showerror("换不了底图", str(e))

    def apply_canvas_margin(self, live=False):
        """改画布边距。**不移动任何元素**，只改画布多大（P1-6）。

        live=True 是输入框每敲一个字符触发的：这时不写盘、不 push_history，
        只重画 —— 否则每敲一个数字就多一条撤销记录，撤销一次只退半个字符。
        失焦 / 回车时（live=False）才真正存盘。
        """
        if not self.tpl:
            return
        raw = str(self.v_margin.get()).strip()
        try:
            v = float(raw)
        except ValueError:
            return                      # 打到一半（"1." / 空）就等它打完
        v = max(FRAME_MOD.MARGIN_MIN, min(FRAME_MOD.MARGIN_MAX, v))
        old = FRAME_MOD.margin_of(self.tpl)
        if abs(v - old) < 1e-9:
            return
        # ★ 调边距前必须**先建立图框记录**（P1-6）
        #   白边的定义是"从黑框外沿往外 m 毫米"。没有图框记录时
        #   canvas_rect 不会加白边（否则会和老参考图错位，见那边的注释），
        #   于是"拖数值框没反应"。这里补一步：从参考图检出框，
        #   记下「框mm」；检不出（图里没框）就退化成"整张画布当框"。
        if not (self.tpl.get("图框") or {}).get("框mm"):
            _fr = {"原点": "黑框外沿左上角"}
            # ★ 默认框mm = 当前画布尺寸（检不出真框时的降级）
            #   一开始先取 canvas_rect 的 frame —— 但没有图框时它就是画布本身，
            #   等于"框 = 整张画布"，后面即使检出真框也覆盖不掉（顺序写错了）。
            _fr["框mm"] = [float(self.tpl["画布"]["宽"]),
                           float(self.tpl["画布"]["高"])]
            try:
                _ref = store.resolve_ref(self.tpl_name,
                                         self.tpl.get("参考图") or "")
                if _ref and os.path.isfile(_ref):
                    _info = FRAME_MOD.canvas_of(_ref)
                    if _info.get("ok"):
                        _fr["框像素"] = list(_info["box_px"])
                        _w, _h = _info["img_px"]
                        _kx = _w / float(self.tpl["画布"]["宽"])
                        _ky = _h / float(self.tpl["画布"]["高"])
                        _fr["框mm"] = [round(_info["box_w_px"] / _kx, 3),
                                       round(_info["box_h_px"] / _ky, 3)]
                        # 元素坐标要跟着平移（原点从"整图左上"移到"框外沿"）
                        _dx = (_info["box_px"][0] / _kx)
                        _dy = (_info["box_px"][1] / _ky)
                        if abs(_dx) > 0.01 or abs(_dy) > 0.01:
                            for _b in (self.tpl.get("文字") or []):
                                if _b.get("x") is not None:
                                    _b["x"] = round(float(_b["x"]) - _dx, 3)
                                    _b["y"] = round(float(_b.get("y") or 0) - _dy, 3)
                            for _g in (self.tpl.get("图形") or []):
                                if _g.get("x") is not None:
                                    _g["x"] = round(float(_g["x"]) - _dx, 3)
                                    _g["y"] = round(float(_g.get("y") or 0) - _dy, 3)
                        self.tpl["画布"] = {
                            "宽": round(_info["box_w_px"] / _kx, 3),
                            "高": round(_info["box_h_px"] / _ky, 3)}
            except Exception:
                pass
            self.tpl["图框"] = _fr
            self.set_status("已按参考图确定图框（%.1f × %.1f mm）"
                            % (_fr["框mm"][0], _fr["框mm"][1]))
        self.tpl["画布边距_mm"] = round(v, 3)
        # ★ **不写 tpl["画布"]**（P1-6 修正 4）
        #   tpl["画布"] 的语义是「框内尺寸」，白边是它外面另加的一圈。
        #   把 画布+2m 写回去，下次再 +2m 就累积放大了 ——
        #   实测把 185.5 一路涨到 265，参考图对不上，对齐率 80%→36%。
        #   画布总尺寸一律问 canvas_rect，字段不动。
        _r = FRAME_MOD.canvas_rect(self.tpl)
        self._ref_aligned.clear()       # 参考图要按新边距重贴
        self.redraw()
        if not live:
            self.push_history()
            self.save()
            self.set_status("画布边距 %.3gmm → 画布 %.2f × %.2f mm（元素未移动）"
                            % (v, _r["w"], _r["h"]))

    def apply_canvas_size(self):
        if self.readonly:
            return self._ro_block()
        self.push_history()
        try:
            w = float(self.v_w.get())
            h = float(self.v_h.get())
            if w <= 0 or h <= 0:
                raise ValueError
        except Exception:
            messagebox.showwarning("尺寸不对", "宽高要填正数")
            return
        self.tpl["画布"] = {"宽": round(w, 2), "高": round(h, 2)}
        self.save()
        self.redraw()

    def add_graphic(self):
        """加 L2 图形：**只能从图库选**（2026-11-23 P0-3 用户口径）。

        为什么不让随便选文件：
          · L2 是「固定版面」，用的必须是印厂图库里的标准件。从桌面随手挑一张
            图加进来，尺寸/线宽/风格都不对，换个人做同一张标签又不一样。
          · 以前还把原图 base64 塞进模板.json —— 一张图几百 KB，
            20 个模板就是几十 MB，而且永远和图库脱钩了。
        现在只走图库选择窗口，元素带 lib_id + lib_ratio，与规格无关、可复现。
        """
        if not self.tpl:
            return
        if self.readonly:
            return self._ro_block()
        self.choose_from_lib()

    def choose_from_lib(self):
        """图库选择窗口：按序号浏览，双击/选中就加成 L2。"""
        import lib as _lib
        ids = _lib.all_ids()
        if not ids:
            messagebox.showinfo("图库是空的",
                                "%s/ 里没有素材。\n把图库整个拷进去再试。" % paths.IMG_LIB)
            return
        win = tk.Toplevel(self.root)
        win.title("从图库选图 · %d 个" % len(ids))
        win.configure(bg=T.BG)
        win.geometry("880x620")
        win.transient(self.root)

        head = tk.Frame(win, bg=T.BG)
        head.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(head, text="从图库选一张（序号即 lib_id）", bg=T.BG, fg=T.TEXT,
                 font=(UI_FONT, T.SZ_DLG, "bold")).pack(side="left")
        tk.Label(head, text="L2 只能取自图库", bg=T.BG, fg=T.TEXT3,
                 font=(UI_FONT, T.SZ_SMALL)).pack(side="left", padx=(10, 0))

        wrap = tk.Frame(win, bg=T.BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        cv = tk.Canvas(wrap, bg=T.CARD, bd=0, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=T.CARD)
        cv.create_window((0, 0), window=inner, anchor="nw")

        def _pick(lid):
            self._add_from_lib(lid)
            win.destroy()

        cols = 6
        for n, lid in enumerate(ids):
            r, c = divmod(n, cols)
            cell = tk.Frame(inner, bg=T.CARD, bd=1, relief="solid")
            cell.grid(row=r, column=c, padx=4, pady=4, sticky="n")
            fp = _lib.path_of(lid)
            try:
                from PIL import Image, ImageTk
                with Image.open(fp) as im:
                    th = im.convert("RGB")
                    th.thumbnail((96, 96))
                    ph = ImageTk.PhotoImage(th)
                lbl = tk.Label(cell, image=ph, bg=T.CARD, cursor="hand2")
                lbl.image = ph
                lbl.pack(padx=4, pady=(4, 0))
                lbl.bind("<Double-Button-1>", lambda e, l=lid: _pick(l))
            except Exception:
                tk.Label(cell, text="（读不出）", bg=T.CARD, fg=T.TEXT3).pack(padx=8, pady=20)
            cap = tk.Label(cell, text="#%d" % lid, bg=T.CARD, fg=T.TEXT2,
                           font=(UI_FONT, T.SZ_FOOT), cursor="hand2")
            cap.pack(pady=(0, 4))
            cap.bind("<Double-Button-1>", lambda e, l=lid: _pick(l))
            cell.bind("<Double-Button-1>", lambda e, l=lid: _pick(l))

        inner.update_idletasks()
        cv.configure(scrollregion=cv.bbox("all"))

    def _add_from_lib(self, lib_id):
        """把一个图库序号加成 L2 图形（**唯一的 L2 图片入口**）。"""
        import lib as _lib
        if not self.tpl:
            return
        fp = _lib.path_of(lib_id)
        if not fp:
            messagebox.showerror("图还没找到", "图库第 %s 号取不到文件" % lib_id)
            return
        ratio = _lib.ratio_of(lib_id)
        w_mm = float(self.tpl["画布"]["宽"])
        h_mm = float(self.tpl["画布"]["高"])
        # 初始尺寸：按原图比例塞进画布 40% 的框里
        box_w, box_h = w_mm * 0.40, h_mm * 0.40
        gw = min(box_w, box_h * ratio)
        gh = gw / ratio if ratio else gw
        gw, gh = round(gw, 2), round(gh, 2)
        self.push_history()
        self.tpl.setdefault("图形", []).append({
            "name": "图库#%d" % lib_id,
            "type": "image",
            "layer": "L2",
            "lib_id": int(lib_id),
            "lib_ratio": round(ratio, 6),
            "src": _lib.rel_src(lib_id),      # 相对图库路径，不再 base64
            "x": round((w_mm - gw) / 2, 2),
            "y": round((h_mm - gh) / 2, 2),
            "w": gw, "h": gh})
        self.save()
        self.sel = ("g", len(self.tpl["图形"]) - 1)
        self.sel_more = []
        self.redraw()
        self.panel()
        self.set_status("从图库加了 #%d（%.2f × %.2f mm，等比锁定）" % (lib_id, gw, gh))

    def _on_paste(self, ev=None):
        """⌘V / Ctrl+V：剪贴板里是什么就粘什么 —— 表格、图片，都算「加」。

        优先级：先看是不是一张表（Excel 复制出来是 TSV），再当图片。
        反过来先试图片会出事：Excel 复制时剪贴板里同时带着位图，
        ImageGrab 拿得到那张缩略图，于是粘出来是一张糊掉的小截图。
        """
        w = self.root.focus_get()
        if isinstance(w, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
            return None                      # 在输入框里就让输入框自己粘文字
        if not self.tpl:
            return None
        grid = self._clip_table()
        if grid:
            self.paste_table(grid)
            return "break"
        self.paste_image()
        return "break"

    # ── 剪贴板 → 表格 ────────────────────────────────────────────
    def _clip_text(self) -> str:
        """剪贴板里的纯文本（Excel / Numbers / 网页表格复制出来都是 TSV）。"""
        for kind in ("STRING", "UTF8_STRING"):
            try:
                return self.root.clipboard_get(type=kind) or ""
            except tk.TclError:
                continue
        try:
            return self.root.clipboard_get() or ""
        except tk.TclError:
            return ""

    def _clip_table(self):
        """剪贴板里是不是一张表 → [[格, …], …]；不是就 None。

        判据放得很松：至少两行、且有一行带 Tab。单行单列不当表格 ——
        那是想粘一句话，交给文字块更合理。
        """
        txt = self._clip_text()
        if not txt or "\t" not in txt:
            return None
        rows = txt.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while rows and not rows[-1].strip():
            rows.pop()
        if len(rows) < 2:
            return None
        grid = [r.split("\t") for r in rows]
        ncol = max(len(r) for r in grid)
        return [r + [""] * (ncol - len(r)) for r in grid]

    @staticmethod
    def _text_mm(s: str, sz: float) -> float:
        """一段字大约占多宽（毫米）：中日韩按一个字高，拉丁按 0.55 个字高。"""
        u = 0.0
        for ch in str(s):
            u += 1.0 if ord(ch) > 0x2E7F else 0.55
        return u * sz

    def paste_table(self, grid=None, at=None):
        """把剪贴板里的表格粘成画布上的一个表格块（不经过 Excel）。

        Args:
            grid: [[格, …], …]；不给就从剪贴板读
            at: (x, y) 毫米；不给就落在纸中心
        """
        grid = grid or self._clip_table()
        if not grid or not self.tpl:
            return False
        self.push_history()
        w_mm = float(self.tpl["画布"]["宽"])
        h_mm = float(self.tpl["画布"]["高"])
        nrow, ncol = len(grid), len(grid[0])
        sz = 2.6                                  # 表格里的小字，行高跟着它走
        # 列宽 = 这一列最长的那个格 + 左右各 1.2mm 呼吸；行高统一
        cols = []
        for c in range(ncol):
            wd = max(self._text_mm(r[c], sz) for r in grid)
            cols.append(round(max(6.0, wd + 2.4), 1))
        rows = [round(sz * 1.7, 1)] + [round(sz * 1.45, 1)] * (nrow - 1)
        total = sum(cols)
        limit = w_mm * 0.92
        if total > limit:                          # 太宽就等比压，别让它出纸
            k = limit / total
            cols = [round(max(3.0, c * k), 1) for c in cols]
            total = sum(cols)
        if at is None:
            x, y = round((w_mm - total) / 2, 1), round(h_mm * 0.12, 1)
        else:
            x, y = round(at[0], 1), round(at[1], 1)
        n = len(self.tpl["文字"]) + 1
        head = str(grid[0][0] or "").strip()[:12]
        b = {"k": "tbl%d" % n, "name": "表格 · %s" % head if head else "表格 %d" % n,
             "type": "table", "layer": "L2", "x": x, "y": y,
             "cols": cols, "rows": rows,
             "cells": [[str(c) for c in r] for r in grid],
             "head_bold": 1, "lines": [], "bars": []}
        self.tpl["文字"].append(b)
        self._auto_layer(b, "\n".join("\t".join(r) for r in grid))
        self.layer = "L2"
        self._refresh_layer_btns()
        self.save()
        self.sel, self.sel_more = ("t", len(self.tpl["文字"]) - 1), []
        self.redraw()
        self.panel()
        self.set_status("已粘入表格：%d 行 × %d 列（%.0f × %.0f mm）"
                        % (nrow, ncol, sum(cols), sum(rows)))
        return True

    def paste_image(self):
        """⌘V 粘贴图片 —— **不再生成 L2，也不再弹窗**（2026-11-23 修）。

        P0-3 时这里会弹一个提示框 + 打开图库窗口。实测非常烦：
        用户在别处按 ⌘V 习惯了，到了这个窗口一按就被一个模态框拦住，
        得点「OK」才能继续 —— 打断了手头的事，而且提示内容每次都一样，
        看第二遍就是纯噪音。

        现在：**只写一行状态栏**，不弹框、不抢焦点、不开窗口。
        真需要加图的人会去点「＋ 加图形」，那里有图库选择窗口。
        """
        self.set_status("⌘V 不加图 —— L2 只能取自图库，请用「＋ 加图形」")

    def _add_image_file(self, fp, name=None):
        """保留函数名给老代码调用，但**不再加成 L2**（P0-3）。

        从文件加图这条路已经关掉。留着这个壳，是为了让还可能存在的
        内部调用点不至于 AttributeError，同时明确告诉它该怎么做。
        """
        # 只写状态栏，**不弹模态框**（P1-2 修：弹框会反复打断操作）
        self.set_status("L2 只能取自图库 —— 请用「＋ 加图形」从图库选")
        return None

    def align_graphic(self, idx, how: str):
        """把图形对齐到画布。"""
        if not self.tpl:
            return
        g = self.tpl["图形"][idx]
        w_mm = float(self.tpl["画布"]["宽"])
        h_mm = float(self.tpl["画布"]["高"])
        self.push_history()
        if how == "left":
            g["x"] = 0.0
        elif how == "right":
            g["x"] = round(w_mm - g["w"], 2)
        elif how == "hcenter":
            g["x"] = round((w_mm - g["w"]) / 2, 2)
        elif how == "top":
            g["y"] = 0.0
        elif how == "bottom":
            g["y"] = round(h_mm - g["h"], 2)
        elif how == "vcenter":
            g["y"] = round((h_mm - g["h"]) / 2, 2)
        self.save()
        self.redraw()
        self.panel()

    def del_graphic(self):
        self.push_history()
        if self.sel and self.sel[0] == "g":
            del self.tpl["图形"][self.sel[1]]
            self.sel = None
            self.save()
            self.redraw()
            self.panel()

    def add_barcode(self):
        """加一个条码（Code128）—— 归 L4：画出来是图，内容却是数据。"""
        if self.readonly:
            return self._ro_block()
        if not self.tpl:
            return
        self.push_history()
        w_mm = float(self.tpl["画布"]["宽"])
        n = len(self.tpl["图形"]) + 1
        self.tpl["图形"].append({
            "name": "条码 %d" % n, "type": "barcode", "layer": "L4",
            "x": round(w_mm * 0.55, 2), "y": 8.0, "w": 30.0, "h": 9.0,
            "seq": "【NOTE 14】", "mod": 0.3, "mode": "C",
        })
        self.save()
        self.sel = ("g", len(self.tpl["图形"]) - 1)
        self.layer = "L4"          # 条码归数据层，切过去才看得见它的编辑区
        self._refresh_layer_btns()
        self.redraw()
        self.panel()
        self.set_status("已加条码：右侧可改内容（可写【NOTE 14】）、模块宽、高度")

    def add_qrcode(self):
        """加一个二维码 —— 归 L4：画出来是方块图，内容来自数据表。"""
        if self.readonly:
            return self._ro_block()
        if not self.tpl:
            return
        self.push_history()
        w_mm = float(self.tpl["画布"]["宽"])
        n = len(self.tpl["图形"]) + 1
        self.tpl["图形"].append({
            "name": "二维码 %d" % n, "type": "qrcode", "layer": "L4",
            "x": round(w_mm * 0.78, 2), "y": 12.0, "w": 13.8, "h": 13.8,
            "seq": "【二维码】",
        })
        self.save()
        self.sel = ("g", len(self.tpl["图形"]) - 1)
        self.layer = "L4"
        self._refresh_layer_btns()
        self.redraw()
        self.panel()
        self.set_status("已加二维码：右侧可改内容（可写【二维码】）")

    def add_table(self):
        """加一个空表格块：4 列 10 行，单元格里可以写【占位符】。

        表格算**图形层**：表格线和色块、外框一样是固定版面。以前它挂在
        文字层，但「加表格」摆在图形区更顺手 —— 加完就在 L2 里能选中。
        """
        if self.readonly:
            return self._ro_block()
        self.push_history()
        w_mm = float(self.tpl["画布"]["宽"])
        h_mm = float(self.tpl["画布"]["高"])
        n = len(self.tpl["文字"]) + 1
        cols = [round(w_mm * 0.13, 1)] * 3 + [round(w_mm * 0.22, 1)]
        rows = [4.5] + [3.4] * 9
        self.tpl["文字"].append({
            "k": "tbl%d" % n, "name": "表格 %d" % n, "type": "table", "layer": "L2",
            "x": round(w_mm * 0.5, 1), "y": round(h_mm * 0.15, 1),
            "cols": cols, "rows": rows,
            "cells": [["表头%d" % (i + 1) if r == 0 else "" for i in range(len(cols))]
                      for r in range(len(rows))],
            "head_bold": 1, "lines": [], "bars": []})
        self.layer = "L2"
        self._refresh_layer_btns()
        self.save()
        self.sel = ("t", len(self.tpl["文字"]) - 1)
        self.redraw()
        self.panel()

    def add_text(self, layer="L3"):
        if self.readonly:
            return self._ro_block()
        self.push_history()
        w_mm = float(self.tpl["画布"]["宽"])
        h_mm = float(self.tpl["画布"]["高"])
        n = len(self.tpl["文字"]) + 1
        self.tpl["文字"].append({
            "k": "t%d" % n, "name": "文字块 %d" % n, "layer": layer,
            "lines": [{"x": round(w_mm * 0.1, 2), "y": round(h_mm * 0.3, 2),
                       "sz": 3.5, "b": 0,
                       "t": "【NOTE 03】" if layer == "L4" else "新文字"}],
            "bars": []})
        self.save()
        self.sel = ("t", len(self.tpl["文字"]) - 1)
        self.redraw()
        self.panel()

    def del_text(self):
        self.push_history()
        if self.sel and self.sel[0] == "t":
            del self.tpl["文字"][self.sel[1]]
            self.sel = None
            self.save()
            self.redraw()
            self.panel()

    # ────────────────────────── 批量出图 ──────────────────────────
    def _batch_ready(self) -> bool:
        if not self.tpl:
            return False
        if not self.data["recs"]:
            messagebox.showinfo("先导入数据表", "L4 里先导入数据表，才能按规格批量出图。")
            return False
        return True

    def batch_pdf(self):
        if not self._batch_ready():
            return
        fp = filedialog.asksaveasfilename(
            title="批量导出 PDF", defaultextension=".pdf",
            initialfile="%s_%d规格.pdf" % (self.tpl_name, len(self.data["recs"])),
            filetypes=[("PDF", "*.pdf")])
        if not fp:
            return
        tpl = copy.deepcopy(self.tpl)
        recs = copy.deepcopy(self._bound_recs())
        total = len(recs)
        job = worker.Job("批量导出 PDF", total=total)
        dlg = worker.ProgressDialog(self.root, "批量导出 PDF",
                                    "共 %d 条规格" % total, total=total)
        state = {"n": 0}

        def _work(j):
            state["n"] = render.save_batch_pdf(
                tpl, fp, recs, dpi=600,
                progress=lambda d, t: (j.step(1, "第 %d/%d 页" % (d, t))),
                cancel=j.cancelled)

        def _prog(j):
            dlg.set(j.pct())

        def _done(j):
            dlg.close()
            n = state["n"]
            self.set_status("已导出 %d 页：%s" % (n, fp))
            messagebox.showinfo("批量导出完成", "共 %d 条规格，已写成 %d 页 PDF：\n%s"
                                % (n, n, fp))

        def _err(e, j):
            dlg.close()
            worker.safe_remove(fp)
            messagebox.showerror("批量导出失败", str(e))

        dlg._on_cancel = lambda: job.cancel()
        worker.run_async(self.root, job, _work, on_done=_done, on_error=_err,
                         on_progress=_prog,
                         on_cancel=lambda j: (dlg.close(), worker.safe_remove(fp),
                                              self.set_status("已取消批量导出")))

    def batch_png(self):
        if not self._batch_ready():
            return
        d = filedialog.askdirectory(title="选一个文件夹放 PNG")
        if not d:
            return
        key_field = self.data["head"][0] if self.data["head"] else ""
        tpl = copy.deepcopy(self.tpl)
        # 必须是翻译过的那份：原始行的列名是 NOTE 03，直接画出来满纸【括号】
        recs = copy.deepcopy(self._bound_recs())
        total = len(recs)
        job = worker.Job("批量导出 PNG", total=total)
        dlg = worker.ProgressDialog(self.root, "批量导出 PNG",
                                    "共 %d 条规格" % total, total=total)
        state = {"files": []}

        def _work(j):
            state["files"] = render.save_batch_png(
                tpl, d, recs, dpi=300, key_field=key_field,
                progress=lambda dn, t: (j.step(1, "第 %d/%d 张" % (dn, t))),
                cancel=j.cancelled)

        def _done(j):
            dlg.close()
            n = len(state["files"])
            self.set_status("已导出 %d 张 PNG 到 %s" % (n, d))
            messagebox.showinfo("批量导出完成",
                                "共 %d 条规格，已写到：\n%s" % (n, d))

        def _err(e, j):
            dlg.close()
            messagebox.showerror("批量导出失败", str(e))

        dlg._on_cancel = lambda: job.cancel()
        worker.run_async(self.root, job, _work, on_done=_done, on_error=_err,
                         on_progress=lambda j: dlg.set(j.pct()),
                         on_cancel=lambda j: (dlg.close(),
                                              self.set_status("已取消批量导出")))

    # ────────────────────────── 版本 ──────────────────────────
    def bump_named(self, name):
        """目录卡片上的「升版」：不用先打开模板。"""
        if not name:
            return
        try:
            tpl = store.load(name)
        except Exception as e:
            messagebox.showerror("读不了", str(e))
            return
        old = tpl.get("版本") or "rev01"
        new = store.next_rev(old)
        if not messagebox.askyesno("升版本", "「%s」\n%s → %s\n\n旧版内容会存档到模板的 版本存档/，继续吗？"
                                   % (name, old, new)):
            return
        store.bump_version(tpl)
        store.save(tpl)
        self.refresh_tpl_list()
        if name == self.tpl_name:
            self.tpl = tpl
            self._update_title()
        self.set_status("「%s」已升到 %s" % (name, new))

    def _update_title(self):
        if not self.tpl:
            self.lbl_tpl.config(text="（未打开模板）")
            self.lbl_rev.config(text="")
            return
        self.lbl_tpl.config(text=self.tpl_name)
        rev = store.norm_rev(self.tpl.get("版本") or "")
        self.lbl_rev.config(text=(rev + " · 只读") if self.readonly else rev,
                            fg=T.TEXT3 if self.readonly else T.PRIMARY)

    def bump_version(self):
        """＋ 新版本 = 当前版存档后，L2/L3/L4 原样延续成新版。

        点一下就生效，不再弹确认：这是「延续上一版继续排版」的常态动作。
        旧版本已整份存档。

        底图（L1）清空 —— 它是「这一版印在什么纸」上的东西。
        **叠影保留**（2026-11-20 修正）：叠影来自 DWG 图纸，是整张标签的
        基准，不是某一版的附属品 —— 升版本后还要继续对位、继续微调，
        清掉它等于每次升版都得重新换一次叠影。产品线B Rev17→Rev19 那次
        被清过，用户当场发现「叠影消失了」。
        """
        if not self.tpl:
            return
        if self.readonly:
            return self._ro_block()
        self.push_history()
        old = self.tpl.get("版本") or "Rev01"
        new = store.bump_version(self.tpl)   # 旧版先存档，版本号 +1
        self.tpl["底图"] = None              # L1 清空：底图不带进新版
        self.save()
        self._update_title()       # 右上角版本号也要跟着变
        self.panel()
        self.paint_version_bar()
        self.redraw()
        self.set_status("✅ 已建 %s：L1 已清空，L2/L3/L4 延续 %s（%s 已存档）"
                        % (new, old, old))

    # ────────────────────────── 保存 / 导出 ──────────────────────────
    def save(self):
        if not self.tpl:
            return
        if self.readonly:
            # 只读看历史版本时绝对不能回写：一写就把存档冲成「当前版的内容」，
            # 用户点一下标签就把历史版本弄丢了 —— 这正是以前最难崩的地方。
            return
        try:
            # 把数据表路径记进模板：下次打开自动接回，不用再导一遍。
            # 表在模板自己文件夹里就存 数据源/xxx —— 整个模板文件夹搬走都还能接上。
            if self.data.get("path"):
                p = self.data["path"]
                try:
                    rel = os.path.relpath(p, store.tpl_dir(self.tpl_name))
                    if not rel.startswith(".."):
                        p = rel.replace(os.sep, "/")
                    else:
                        rel = os.path.relpath(p, paths.DATA)
                        if not rel.startswith(".."):
                            p = rel.replace(os.sep, "/")
                except Exception:
                    pass
                self.tpl["数据源"] = {"path": p,
                                      "sheet": self.data.get("sheet", ""),
                                      "cur": self.data.get("cur", 0)}
            store.save(self.tpl)
        except Exception as e:
            self.set_status("存盘失败：%s" % e)

    def _rec_for_export(self):
        """当前这一条规格，**翻译过的**那份（不是数据表的原始行）。

        以前这里返回 `self.data["recs"][cur]`，列名还是 `NOTE 03`，
        导出时占位符一个都填不上 —— 屏幕上是对的，导出来满纸【括号】。
        和画布走同一份（`_fields()`）才谈得上「所见即所得」。
        """
        if not self.data["recs"]:
            return None
        try:
            return databind.bind(self.tpl, self.data["recs"][self.data["cur"]])
        except Exception:
            return self.data["recs"][self.data["cur"]]

    def _spec_key_field(self) -> str:
        """规格的显示/命名用哪一列：标签框架用「成品编码」，普通表用第一列。"""
        head = self.data.get("head") or []
        if "成品编码" in head:
            return "成品编码"
        return head[0] if head else ""

    def _spec_suffix(self) -> str:
        if not self.data["recs"]:
            return ""
        v = str(self.data["recs"][self.data["cur"]].get(self._spec_key_field(), "") or "").strip()
        return ("_" + v) if v else ""

    def export_one(self):
        """导出本规格：选一个 PNG 路径，同名 PDF 一起出来。"""
        if not self.tpl:
            return
        fp = filedialog.asksaveasfilename(
            title="导出本规格（PNG + PDF 一起出）", defaultextension=".png",
            initialfile=self.tpl_name + self._spec_suffix() + ".png",
            filetypes=[("PNG", "*.png")])
        if not fp:
            return
        try:
            rec = self._rec_for_export()
            render.save_png(self.tpl, fp, rec, dpi=300)
            pdf = os.path.splitext(fp)[0] + ".pdf"
            render.save_pdf(self.tpl, pdf, rec, dpi=600)
            self.set_status("已导出 %s + 同名 PDF" % os.path.basename(fp))
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def export_all(self):
        """导出全部规格：选一个文件夹，PNG 一条一张 + 一个多页 PDF。"""
        if not self._batch_ready():
            return
        d = filedialog.askdirectory(title="选一个文件夹（PNG 和 PDF 都放这里）")
        if not d:
            return
        try:
            key = self._spec_key_field()
            recs = self._bound_recs()          # 翻译过的那份，见 _rec_for_export
            files = render.save_batch_png(self.tpl, d, recs, dpi=300,
                                          key_field=key)
            pdf = os.path.join(d, "%s_全部规格.pdf" % self.tpl_name)
            n = render.save_batch_pdf(self.tpl, pdf, recs, dpi=600)
            self.set_status("已导出 %d 张 PNG + %d 页 PDF 到 %s" % (len(files), n, d))
            messagebox.showinfo("导出完成",
                                "%d 条规格：\n· PNG %d 张\n· PDF %d 页\n\n%s"
                                % (len(self.data["recs"]), len(files), n, d))
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def export_png(self):
        if not self.tpl:
            return
        fp = filedialog.asksaveasfilename(title="导出 PNG", defaultextension=".png",
                                          initialfile=self.tpl_name + ".png",
                                          filetypes=[("PNG", "*.png")])
        if not fp:
            return
        try:
            render.save_png(self.tpl, fp, self._fields() or None, dpi=300)
            self.set_status("已导出 %s" % fp)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def export_pdf(self):
        """导出单页 PDF —— 后台线程 + 进度条 + 可取消（P1-1）。

        600dpi 渲染一页要 3~5 秒。原来跑在主线程，Tk 事件循环被占住，
        窗口不刷新、按钮点不动，用户看到的就是「点了导出软件死了」。
        """
        if not self.tpl:
            return
        fp = filedialog.asksaveasfilename(title="导出 PDF", defaultextension=".pdf",
                                          initialfile=self.tpl_name + ".pdf",
                                          filetypes=[("PDF", "*.pdf")])
        if not fp:
            return
        tpl = copy.deepcopy(self.tpl)          # 后台线程不许碰界面里的对象
        fields = copy.deepcopy(self._fields() or None)
        job = worker.Job("导出 PDF", total=3)
        dlg = worker.ProgressDialog(self.root, "导出 PDF", "正在渲染 600dpi…", total=3)

        def _work(j):
            j.step(1, "渲染位图…")
            j.check_cancel()
            render.save_pdf(tpl, fp, fields, dpi=600)
            j.step(2)
            j.check_cancel()

        def _prog(j):
            dlg.set(j.pct())

        def _done(j):
            dlg.close()
            self.set_status("已导出 %s（%.0f KB）"
                            % (fp, os.path.getsize(fp) / 1024.0))

        def _err(e, j):
            dlg.close()
            worker.safe_remove(fp)             # 半个文件会被人误用，删掉
            messagebox.showerror("导出失败", str(e))

        dlg._on_cancel = lambda: job.cancel()
        worker.run_async(self.root, job, _work, on_done=_done,
                         on_error=_err, on_progress=_prog,
                         on_cancel=lambda j: (dlg.close(), worker.safe_remove(fp),
                                              self.set_status("已取消导出")))

    def set_status(self, text):
        self.status.config(text="  " + text)


def main():
    root = tk.Tk()
    app = DesignerApp(root)

    root.mainloop()


if __name__ == "__main__":
    main()
