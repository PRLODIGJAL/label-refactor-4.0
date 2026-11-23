# -*- coding: utf-8 -*-
"""worker.py —— 把耗时的事挪到后台线程，界面带进度条 + 取消。

为什么需要它（2026-11-23 P1-1）
--------------------------
导出 600dpi PDF 单页要 3~5 秒，批量 20 个规格要一两分钟。原来这些都跑在
**主线程**里 —— Tk 的事件循环被占住，窗口不刷新、点不动、进度条也画不出来，
用户看到的就是「点了导出，软件死了」。任务管理器里 CPU 还满着，
更让人以为卡死了。

Tk 的铁律：**控件只能在主线程碰。** 后台线程不许直接改 Label / 调 update()。
所以这里用 `root.after()` 把回主线程的动作排进事件队列：
    后台线程  ──(做活)──▶  结果放进队列
    主线程    ──(after 轮询)──▶  取出结果，更新控件

取消机制
-------
Python 线程没法从外面强杀（`kill` 只会留下半死的线程和锁）。
所以用「协作式取消」：工作函数每隔一段调一次 `job.cancelled()`，
看到 True 自己收尾退出，并把已经写了一半的文件删掉。
"""
import os
import threading
import traceback


class Job:
    """一个后台任务。放在主线程创建、交给 run_async，回调都在主线程执行。"""

    def __init__(self, title, total=0):
        self.title = title
        self.total = max(1, int(total))
        self.done = 0
        self._cancel = False
        self.error = None
        self.result = None
        self._lock = threading.Lock()

    # ── 后台线程调用 ────────────────────────────────────────
    def cancelled(self):
        return self._cancel

    def step(self, n=1, text=""):
        """报一次进度。后台线程调，主线程通过 poll 读到。"""
        with self._lock:
            self.done = min(self.total, self.done + n)
        if text:
            self.note = text

    def check_cancel(self):
        """该取消时抛这个异常，比到处 return 干净。"""
        if self._cancel:
            raise Cancelled()

    # ── 主线程调用 ──────────────────────────────────────────
    def cancel(self):
        self._cancel = True

    def pct(self):
        with self._lock:
            return min(1.0, self.done / float(self.total)) if self.total else 0.0


class Cancelled(Exception):
    """任务被用户取消。"""


def run_async(root, job, work, on_done=None, on_error=None, on_progress=None,
              on_cancel=None, interval=120):
    """在后台线程跑 work(job)，主线程轮询进度。

    Args:
        root: Tk 根窗口（用来 after 排回主线程）
        job: Job 实例
        work: `work(job)`，在后台线程执行；用 `job.step()` / `job.check_cancel()`
        on_done: `on_done(job)`，成功时在主线程调
        on_error: `on_error(exc, job)`，出错时在主线程调
        on_progress: `on_progress(job)`，每个轮询周期在主线程调（更新进度条）
        on_cancel: `on_cancel(job)`，被取消时在主线程调
        interval: 轮询间隔（毫秒）

    Returns:
        threading.Thread（已 start）
    """
    box = {"exc": None, "cancelled": False}

    def _worker():
        try:
            work(job)
        except Cancelled:
            box["cancelled"] = True
        except Exception as e:                       # noqa: BLE001
            box["exc"] = e
            box["tb"] = traceback.format_exc()

    th = threading.Thread(target=_worker, daemon=True)
    th.start()

    def _poll():
        if on_progress is not None:
            try:
                on_progress(job)
            except Exception:
                pass
        if th.is_alive():
            root.after(interval, _poll)
            return
        # 线程结束了：把结果收一下
        if box["cancelled"]:
            if on_cancel is not None:
                on_cancel(job)
            return
        if box["exc"] is not None:
            if on_error is not None:
                on_error(box["exc"], job)
            else:
                import tkinter.messagebox as mb
                mb.showerror("出错了", str(box["exc"]))
            return
        if on_done is not None:
            on_done(job)

    root.after(interval, _poll)
    return th


def safe_remove(path):
    """取消时把写了半个的文件删掉 —— 留个坏文件比不留更糟（会被人误用）。"""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


class ProgressDialog:
    """通用进度窗口：标题 + 进度条 + 百分比 + 取消按钮。

    用法：
        dlg = ProgressDialog(root, "导出 PDF", "正在渲染…")
        dlg.show()
        ...
        dlg.set(0.4, "第 3/8 页")
        dlg.close()
    """

    def __init__(self, root, title, subtitle="", total=1):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.title(title)
        self.win.transient(root)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._on_x)
        self._cancelled = False
        self._on_cancel = None

        try:
            from ui_theme import UI_FONT, T as _T
            bg, fg, fg3 = _T.BG, _T.TEXT, _T.TEXT3
            font = (UI_FONT, 11)
            font2 = (UI_FONT, 9)
        except Exception:
            bg, fg, fg3 = "#f5f5f5", "#111111", "#777777"
            font, font2 = ("Helvetica", 11), ("Helvetica", 9)

        pad = tk.Frame(self.win, bg=bg)
        pad.pack(fill="both", expand=True, padx=22, pady=18)
        tk.Label(pad, text=title, bg=bg, fg=fg, font=font, anchor="w").pack(fill="x")
        self.lbl = tk.Label(pad, text=subtitle, bg=bg, fg=fg3, font=font2, anchor="w")
        self.lbl.pack(fill="x", pady=(4, 10))
        self.bar = ttk.Progressbar(pad, length=360, mode="determinate",
                                   maximum=max(1, total))
        self.bar.pack(fill="x")
        row = tk.Frame(pad, bg=bg)
        row.pack(fill="x", pady=(8, 0))
        self.pct = tk.Label(row, text="0%", bg=bg, fg=fg3, font=font2)
        self.pct.pack(side="left")
        self.btn = tk.Button(row, text="取消", command=self.cancel,
                             relief="flat", padx=14, pady=2, font=font2)
        self.btn.pack(side="right")

        self.win.update_idletasks()
        # 居中到父窗口
        try:
            x = root.winfo_rootx() + (root.winfo_width() - self.win.winfo_width()) // 2
            y = root.winfo_rooty() + (root.winfo_height() - self.win.winfo_height()) // 3
            self.win.geometry("+%d+%d" % (max(0, x), max(0, y)))
        except Exception:
            pass

    def _on_x(self):
        self.cancel()

    def cancel(self):
        self._cancelled = True
        self.btn.config(text="正在取消…", state="disabled")
        if self._on_cancel is not None:
            self._on_cancel()

    def set(self, frac, text=None):
        try:
            self.bar["value"] = frac * self.bar["maximum"]
            self.pct.config(text="%d%%" % int(round(frac * 100)))
            if text:
                self.lbl.config(text=text)
            self.win.update_idletasks()
        except Exception:
            pass

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
