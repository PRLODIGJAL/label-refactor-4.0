# -*- coding: utf-8 -*-
"""table_editor.py —— 表格的独立编辑窗口（像 Excel，不挤在右栏里）。

为什么单独开一个窗口
--------------------
第一版把网格塞在右栏（270px 宽），结果：4 列就装不下、加行按钮被顶到
窗口外面、改列宽只能去画布上抓那几条蓝虚线 —— 抓歪一点就把整张表改坏。
这一版把表格编辑搬到独立窗口：列头 A/B/C、行头 1/2/3，点格子就打字，
Tab 往右、Enter 往下，列宽行高在工具条上填数字改。

和主窗口的关系
--------------
· 所有改动**立刻写回** `tpl["文字"][idx]` 并让主窗口重绘（边改边看）
· 打开窗口时压一次撤销点，整次编辑算作一步（关掉后按一次 ⌘Z 全退回）
· 只改数据，不负责选中/删除元素 —— 那是主窗口的事
"""
import tkinter as tk
from tkinter import ttk

import ui_theme as T

COLS_A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def col_name(i: int) -> str:
    """0 → A，25 → Z，26 → AA。"""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = COLS_A[r] + s
    return s


class TableEditor(tk.Toplevel):
    """一张表格的编辑器。`idx` 是 `tpl["文字"]` 里的下标。"""

    def __init__(self, app, idx: int):
        tk.Toplevel.__init__(self, app.root)
        self.app = app
        self.idx = idx
        self.b = app.tpl["文字"][idx]
        self.cur = (0, 0)                    # 当前格 (行, 列)
        self._cells = {}                     # (r, c) → StringVar
        self._heads = []                     # 列头 Entry
        self._rows = []                      # 行头 Entry
        self._tick = None
        self._building = False

        self.title("编辑表格 · %s" % (self.b.get("name") or "表格"))
        self.configure(bg=T.CARD)
        self.geometry("860x620")
        self.minsize(620, 420)
        self.transient(app.root)

        self._build_toolbar()
        self._mark_align()
        self.rebuild()
        self.after(80, self._focus_cur)
        # 整次编辑压成一个撤销点：打开时记一次，之后不再往栈里塞
        self.app.push_history()

    # ───────────────────────── 工具条 ─────────────────────────
    def _build_toolbar(self):
        top = tk.Frame(self, bg=T.CARD)
        top.pack(side="top", fill="x", padx=14, pady=(12, 0))
        tk.Label(top, text="编辑表格", bg=T.CARD, fg=T.TEXT,
                 font=(T.UI_FONT, T.SZ_DLG, "bold")).pack(side="left")
        self.lbl_info = tk.Label(top, text="", bg=T.CARD, fg=T.TEXT3,
                                 font=(T.UI_FONT, T.SZ_SMALL))
        self.lbl_info.pack(side="left", padx=10)
        tk.Label(top, text="点格直接打字 · Tab 往右 · Enter 往下 · 列宽行高在上面填",
                 bg=T.CARD, fg=T.TEXT3,
                 font=(T.UI_FONT, T.SZ_FOOT)).pack(side="right")

        bar = tk.Frame(self, bg=T.CARD)
        bar.pack(side="top", fill="x", padx=14, pady=(10, 0))

        def btn(parent, text, cmd, tip=None):
            w = T.Btn(parent, text=text, relief="flat", bg=T.DZ_BG, padx=8, pady=4,
                      font=(T.UI_FONT, T.SZ_SMALL), command=cmd)
            w.pack(side="left", padx=(0, 4))
            if tip:
                self._tip(w, tip)
            return w

        self._tip_note = None
        btn(bar, "＋ 行", lambda: self.shape(1, 0), "在当前格下面插一行")
        btn(bar, "－ 行", lambda: self.shape(-1, 0), "删掉当前格这一行")
        btn(bar, "＋ 列", lambda: self.shape(0, 1), "在当前格右边插一列")
        btn(bar, "－ 列", lambda: self.shape(0, -1), "删掉当前格这一列")

        tk.Frame(bar, bg=T.SEP, width=1, height=22).pack(side="left", padx=8)

        self.var_col = tk.StringVar()
        self.var_row = tk.StringVar()
        self.var_sz = tk.StringVar()
        for lab, var, cb, unit in (("列宽", self.var_col, self._set_col_w, "mm"),
                                   ("行高", self.var_row, self._set_row_h, "mm"),
                                   ("字号", self.var_sz, self._set_sz, "mm")):
            tk.Label(bar, text=lab, bg=T.CARD, fg=T.TEXT3,
                     font=(T.UI_FONT, T.SZ_SMALL)).pack(side="left", padx=(4, 2))
            e = tk.Entry(bar, textvariable=var, width=5, relief="flat", bd=0,
                         bg=T.DZ_BG, justify="center", font=(T.UI_FONT, T.SZ_SMALL),
                         highlightthickness=1, highlightbackground=T.BORDER)
            e.pack(side="left", ipady=3)
            e.bind("<Return>", lambda ev: cb())
            e.bind("<FocusOut>", lambda ev: cb())
            tk.Label(bar, text=("（0=自动）" if lab == "字号" else unit),
                     bg=T.CARD, fg=T.TEXT3,
                     font=(T.UI_FONT, T.SZ_FOOT)).pack(side="left", padx=(2, 6))

        tk.Frame(bar, bg=T.SEP, width=1, height=22).pack(side="left", padx=6)

        # 对齐 / 表头加粗
        self._align_btns = {}
        seg = tk.Frame(bar, bg=T.CARD, highlightthickness=1,
                       highlightbackground=T.BORDER)
        seg.pack(side="left", padx=(0, 10))
        for text, how in (("左", "left"), ("中", "center"), ("右", "right")):
            b = T.Btn(seg, text=text, relief="flat", bg=T.CARD, fg=T.TEXT3,
                      padx=9, pady=3, font=(T.UI_FONT, T.SZ_SMALL),
                      command=lambda h=how: self._set_align(h))
            b.pack(side="left")
            self._align_btns[how] = b
        self.var_bold = tk.BooleanVar(value=bool(self.b.get("head_bold", True)))
        tk.Checkbutton(bar, text="首行加粗", variable=self.var_bold,
                       command=self._set_bold, bg=T.CARD, fg=T.TEXT,
                       activebackground=T.CARD, selectcolor=T.CARD,
                       font=(T.UI_FONT, T.SZ_SMALL)).pack(side="left")

        # ── 网格 ──
        mid = tk.Frame(self, bg=T.CARD)
        mid.pack(side="top", fill="both", expand=True, padx=14, pady=(10, 0))
        self.cv = tk.Canvas(mid, bg=T.CARD, highlightthickness=1,
                            highlightbackground=T.BORDER, bd=0)
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.cv.yview)
        hs = ttk.Scrollbar(self, orient="horizontal", command=self.cv.xview)
        self.cv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x", padx=14, pady=(0, 4))
        self.cv.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.cv, bg=T.CARD)
        self.cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.cv.configure(
            scrollregion=self.cv.bbox("all")))

        # ── 底部 ──
        foot = tk.Frame(self, bg=T.CARD)
        foot.pack(side="bottom", fill="x", padx=14, pady=10)
        self.lbl_foot = tk.Label(foot, text="", bg=T.CARD, fg=T.TEXT3,
                                 font=(T.UI_FONT, T.SZ_FOOT), anchor="w")
        self.lbl_foot.pack(side="left")
        T.Btn(foot, text="完成", relief="flat", bg=T.PRIMARY, fg="#ffffff",
              padx=18, pady=5, font=(T.UI_FONT, T.SZ_SMALL, "bold"),
              command=self.close).pack(side="right")

    def _tip(self, w, text):
        """悬停提示：不占地方，鼠标停上去才说。"""
        def show(ev=None):
            if self._tip_note:
                self._tip_note.destroy()
            x = w.winfo_rootx() - self.winfo_rootx() + 10
            y = w.winfo_rooty() - self.winfo_rooty() + w.winfo_height() + 2
            self._tip_note = tk.Label(self, text=text, bg="#33383f", fg="#ffffff",
                                      font=(T.UI_FONT, T.SZ_FOOT), padx=6, pady=3)
            self._tip_note.place(x=x, y=y)

        def hide(ev=None):
            if self._tip_note:
                self._tip_note.destroy()
                self._tip_note = None
        w.bind("<Enter>", show)
        w.bind("<Leave>", hide)

    # ───────────────────────── 网格 ─────────────────────────
    def _dims(self):
        b = self.b
        cols = [float(c) for c in (b.get("cols") or [10.0])]
        rows = [float(r) for r in (b.get("rows") or [4.0])]
        cells = [list(r) for r in (b.get("cells") or [])]
        while len(cells) < len(rows):
            cells.append([""] * len(cols))
        cells = [(r + [""] * len(cols))[:len(cols)] for r in cells[:len(rows)]]
        return cols, rows, cells

    def rebuild(self):
        """按当前行列数把整张网格重建一遍（行列变了就整块重建，最省心）。"""
        self._building = True
        for w in self.inner.winfo_children():
            w.destroy()
        self._cells.clear()
        self._heads, self._rows = [], []
        cols, rows, cells = self._dims()

        tk.Label(self.inner, text="", bg=T.DZ_BG, width=4).grid(row=0, column=0)
        # 列头只放字母（A/B/C），行头只放行号 —— 宽和高在工具条上填数字，
        # 挤进格子里只会变成「A23.6」这种看不懂的东西。
        for ci in range(len(cols)):
            e = tk.Label(self.inner, text=col_name(ci), bg=T.DZ_BG, fg=T.TEXT,
                         font=(T.UI_FONT, T.SZ_SMALL, "bold"), width=4,
                         cursor="hand2")
            e.grid(row=0, column=ci + 1, sticky="nsew", padx=(0, 1), pady=(0, 1))
            e.bind("<Button-1>", lambda ev, cc=ci: self._pick_col(cc))
            self._heads.append(e)
        for ri in range(len(rows)):
            e = tk.Label(self.inner, text=str(ri + 1), bg=T.DZ_BG, fg=T.TEXT,
                         font=(T.UI_FONT, T.SZ_SMALL), width=4, cursor="hand2")
            e.grid(row=ri + 1, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))
            e.bind("<Button-1>", lambda ev, rr=ri: self._pick_row(rr))
            self._rows.append(e)
            for ci in range(len(cols)):
                e = self._mk(cells[ri][ci], self._width_of(cols[ci]),
                             kind="cell", r=ri, c=ci)
                e.grid(row=ri + 1, column=ci + 1, sticky="nsew", padx=(0, 1),
                       pady=(0, 1))
        for ci in range(len(cols)):
            self.inner.columnconfigure(ci + 1, weight=0)
        self._building = False
        self._refresh_marks()
        self._refresh_info()

    def _width_of(self, w_mm):
        # 列宽 10mm 大概能放 5 个汉字，给足但别把窗口撑爆
        return max(6, min(22, int(round(w_mm * 0.55))))

    def _mk(self, value, width, kind="cell", r=None, c=None):
        var = tk.StringVar(value=str(value))
        e = tk.Entry(self.inner, textvariable=var, width=width,
                     font=(T.UI_FONT, T.SZ_SMALL,
                           "bold" if kind != "cell" else "normal"),
                     justify="center", relief="flat", bd=0,
                     bg=T.DZ_BG if kind != "cell" else T.CARD,
                     highlightthickness=1,
                     highlightbackground=T.BORDER,
                     highlightcolor=T.PRIMARY)
        if kind == "cell":
            self._cells[(r, c)] = var
            var.trace_add("write", lambda *a, rr=r, cc=c, v=var: self._cell_edit(rr, cc, v))
            e.bind("<FocusIn>", lambda ev, rr=r, cc=c: self._set_cur(rr, cc))
            e.bind("<Return>", lambda ev, rr=r, cc=c: self._move(rr + 1, cc))
            e.bind("<Tab>", lambda ev, rr=r, cc=c: self._move(rr, cc + 1, ev))
            e.bind("<Shift-Tab>", lambda ev, rr=r, cc=c: self._move(rr, cc - 1, ev))
            e.bind("<ISO_Left_Tab>", lambda ev, rr=r, cc=c: self._move(rr, cc - 1, ev))
            # 右键：先把这一格设为当前格，再弹菜单（像备忘录/Excel 那样）
            for seq in ("<Button-2>", "<Button-3>"):
                e.bind(seq, lambda ev, rr=r, cc=c: self._ctx_menu(ev, rr, cc))
        return e

    # ── 右键菜单：加行/减行/加列/减列/合并/拆分 ──────────────────
    def _ctx_menu(self, ev, r=None, c=None):
        """在单元格上弹菜单。r/c 是点中的格；不传就用当前格。"""
        if r is not None and c is not None:
            self._set_cur(r, c)
        cur = self.cur or (0, 0)
        r, c = cur
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="↑ 在上面插一行",
                      command=lambda: self._ins_row(r))
        m.add_command(label="↓ 在下面插一行",
                      command=lambda: self._ins_row(r + 1))
        m.add_separator()
        m.add_command(label="← 在左边插一列",
                      command=lambda: self._ins_col(c))
        m.add_command(label="→ 在右边插一列",
                      command=lambda: self._ins_col(c + 1))
        m.add_separator()
        m.add_command(label="✕ 删掉这一行", command=lambda: self._del_row(r))
        m.add_command(label="✕ 删掉这一列", command=lambda: self._del_col(c))
        m.add_separator()
        m.add_command(label="▭ 合并选中区域", command=self._merge_sel)
        m.add_command(label="▯ 拆分这一格", command=self._split_cur)
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()
        return "break"

    def _ins_row(self, at):
        """在第 at 行插入一行（行高照抄相邻行）。"""
        rows = [float(v) for v in (self.b.get("rows") or [4.0])]
        at = max(0, min(at, len(rows)))
        h = rows[at - 1] if at > 0 else (rows[0] if rows else 4.0)
        self.app._table_shape(self.idx, 1, 0, at=(at, 0))
        self.rebuild()
        self._focus_cur()

    def _del_row(self, r):
        rows = self.b.get("rows") or []
        if len(rows) <= 1:
            return
        self.app._table_shape(self.idx, -1, 0, at=(r, 0))
        self.rebuild()
        self._focus_cur()

    def _ins_col(self, at):
        cols = [float(v) for v in (self.b.get("cols") or [10.0])]
        at = max(0, min(at, len(cols)))
        self.app._table_shape(self.idx, 0, 1, at=(0, at))
        self.rebuild()
        self._focus_cur()

    def _del_col(self, c):
        cols = self.b.get("cols") or []
        if len(cols) <= 1:
            return
        self.app._table_shape(self.idx, 0, -1, at=(0, c))
        self.rebuild()
        self._focus_cur()

    def _merge_sel(self):
        """合并：把「选中区域」的文字并到一个格子里（像备忘录）。

        选中区域 = 当前格 与 焦点格 围成的矩形；没选范围就只并当前格上下两行
        同列的两个格 —— 那是最常见的"表头跨两行"。
        """
        a = getattr(self, "_anchor", None)
        cur = self.cur or (0, 0)
        if not a:
            a = (cur[0], cur[0] + 1) if cur[0] + 1 < len(self.b.get("rows") or []) \
                else (cur[0], cur[0])
            b = (cur[1], cur[1])
        else:
            b = (min(a[1], cur[1]), max(a[1], cur[1]))
            a = (min(a[0], cur[0]), max(a[0], cur[0]))
        self.app._table_merge(self.idx, a[0], a[1], b[0], b[1])
        self.rebuild()
        self._focus_cur()

    def _split_cur(self):
        cur = self.cur or (0, 0)
        self.app._table_split(self.idx, cur[0], cur[1])
        self.rebuild()
        self._focus_cur()

    def _pick_col(self, c):
        self._set_cur(0, c)
        self._focus_cur()

    def _pick_row(self, r):
        self._set_cur(r, 0)
        self._focus_cur()

    def _set_cur(self, r, c):
        if self._building:
            return
        self.cur = (r, c)
        self._refresh_marks()
        self._refresh_info()

    def _move(self, r, c, ev=None):
        if ev is not None:
            return None                     # 让 Tk 走默认的 Tab 焦点切换
        self.after(10, lambda: self._focus_cell(r, c))
        return "break"

    def _focus_cell(self, r, c):
        cols, rows, _ = self._dims()
        r = max(0, min(r, len(rows) - 1))
        c = max(0, min(c, len(cols) - 1))
        var = self._cells.get((r, c))
        if var is None:
            return
        for w in self.inner.winfo_children():
            if isinstance(w, tk.Entry) and w.cget("textvariable") == str(var):
                w.focus_set()
                w.select_range(0, "end")
                break
        self._set_cur(r, c)

    def _focus_cur(self):
        self._focus_cell(*self.cur)

    def _refresh_marks(self):
        """当前格 + 它所在的列头行头描蓝边 —— 一眼知道「插在哪」。"""
        r, c = self.cur
        for i, e in enumerate(self._heads):
            on = (i == c)
            e.configure(bg=T.TINT_HEAD if on else T.DZ_BG,
                        fg=T.TINT_HEAD_FG if on else T.TEXT)
        for i, e in enumerate(self._rows):
            on = (i == r)
            e.configure(bg=T.TINT_HEAD if on else T.DZ_BG,
                        fg=T.TINT_HEAD_FG if on else T.TEXT)

    def _refresh_info(self):
        cols, rows, _ = self._dims()
        r, c = self.cur
        r = min(r, len(rows) - 1)
        c = min(c, len(cols) - 1)
        self.lbl_info.config(text="当前格 %s%d" % (col_name(c), r + 1))
        self.lbl_foot.config(text="%d 行 × %d 列 · 总宽 %.1f × 高 %.1f mm"
                                  % (len(rows), len(cols), sum(cols), sum(rows)))
        self.var_col.set("%.1f" % cols[c])
        self.var_row.set("%.1f" % rows[r])
        self.var_sz.set("%.2f" % float(self.b.get("cell_sz") or 0))

    # ───────────────────────── 改内容 ─────────────────────────
    def _cell_edit(self, r, c, var):
        if self._building:
            return
        cols, rows, cells = self._dims()
        cells[r][c] = var.get()
        self.b["cells"] = cells
        self._touch()

    def _head_col(self, c, var):
        if self._building:
            return
        try:
            v = max(1.0, float(var.get()))
        except ValueError:
            return
        cols, _, _ = self._dims()
        cols[c] = round(v, 2)
        self.b["cols"] = cols
        self._touch()

    def _head_row(self, r, var):
        if self._building:
            return
        try:
            v = max(0.8, float(var.get()))
        except ValueError:
            return
        _, rows, _ = self._dims()
        rows[r] = round(v, 2)
        self.b["rows"] = rows
        self._touch()

    def _set_col_w(self):
        try:
            v = max(1.0, float(self.var_col.get()))
        except ValueError:
            return
        cols, _, _ = self._dims()
        cols[self.cur[1]] = round(v, 2)
        self.b["cols"] = cols
        self._refresh_info()
        self._touch()

    def _set_row_h(self):
        try:
            v = max(0.8, float(self.var_row.get()))
        except ValueError:
            return
        _, rows, _ = self._dims()
        rows[self.cur[0]] = round(v, 2)
        self.b["rows"] = rows
        self._refresh_info()
        self._touch()

    def _set_sz(self):
        try:
            v = max(0.0, float(self.var_sz.get()))
        except ValueError:
            return
        self.b["cell_sz"] = round(v, 2)
        self._touch()

    def _set_align(self, how):
        self.b["align"] = how
        self._mark_align()
        self._touch()

    def _mark_align(self):
        cur = self.b.get("align") or "center"
        for how, b in self._align_btns.items():
            if how == cur:
                b.config(bg=T.TINT_HEAD, fg=T.TINT_HEAD_FG)
            else:
                b.config(bg=T.CARD, fg=T.TEXT3)

    def _set_bold(self):
        self.b["head_bold"] = bool(self.var_bold.get())
        self._touch()

    def shape(self, dr, dc):
        self.app._grid_cur = getattr(self.app, "_grid_cur", {})
        self.app._grid_cur[id(self.b)] = self.cur
        self.app._table_shape(self.idx, dr, dc, at=self.cur)
        self.rebuild()
        self._focus_cur()

    def _touch(self):
        """改动合并到下一帧再重绘 —— 连着打字时不至于每个字母重画一次。"""
        if self._tick is not None:
            try:
                self.after_cancel(self._tick)
            except Exception:
                pass
        self._tick = self.after(180, self._flush)

    def _flush(self):
        self._tick = None
        try:
            self.app.redraw()
        except Exception:
            pass

    def close(self):
        self._flush()
        try:
            self.app.save()
        except Exception:
            pass
        self.app.panel()
        self.destroy()
