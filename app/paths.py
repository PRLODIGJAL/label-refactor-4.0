# -*- coding: utf-8 -*-
"""paths.py —— 全项目唯一的路径来源（四层目录分层，模块名保持扁平）。

沿用桌面上其他工具（WI 一致性检查 / PDF 差异对比 / PDF 转换拼接）的目录规范：
    app/ui/       界面层    只画界面、收事件
    app/judge/    判定层    业务规则（本项目体量小，保留占位）
    app/engine/   引擎层    可复用底层能力（存储 / 渲染 / 数据源）
    app/shell/    启动壳    环境自检、装依赖、启动

⚠️ 模块名保持扁平（import store / import render）。四层只是目录分层，
   不是 Python 包分层。install() 把四层一起塞进 sys.path，扁平名字照旧能解析。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
UI = os.path.join(APP, "ui")
JUDGE = os.path.join(APP, "judge")
ENGINE = os.path.join(APP, "engine")
SHELL = os.path.join(APP, "shell")
LAYERS = (UI, JUDGE, ENGINE, SHELL)

DATA = os.path.join(ROOT, "data")
TPL_DIR = os.path.join(ROOT, "模板")          # 一模板一文件夹，独立于 data/（可直接拖拽复制）
FONT_DIR = os.path.join(DATA, "字体")         # NotoSans 字库（渲染用，不跟模板走）
# ★ 图库目录：**全项目唯一一处**（2026-11-23 P0-3）
#
# 以前有两个：
#   · data/图库        —— paths.IMG_LIB，40 个手命名文件（artimes_big.png 这类）
#   · data/标签图库     —— 界面里硬编码路径，151 个 序号NNN_imageXxx.png
# 两处并存的结果：加图对话框开的是「图库」，浏览图库开的是「标签图库」，
# 用户看到两个不一样的库；store 找素材也只在 IMG_LIB 里找，
# 图库里的原图反而找不到。
#
# 现在只留这个常量，**任何地方都读它**，不许再出现字面量 "标签图库"。
# 目录名沿用「标签图库」：里面有 `_图库索引.json`（序号 → 文件名），
# 那是 lib_id 的权威来源，改名会连索引一起废掉。
IMG_LIB = os.path.join(DATA, "标签图库")
PREVIEW_DIR = os.path.join(DATA, "预览")      # 生成的预览图缓存
SETTINGS = os.path.join(DATA, "上次选择.json")
RUNTIME = os.path.join(ROOT, "runtime")
WHEELS = os.path.join(ROOT, "wheels")


def open_path(path):
    """用系统默认程序打开文件或文件夹。**全项目唯一入口**（P1-2）。

    为什么必须统一：以前散着写，两个分支各一份：
      · 打开图库目录用 `open` / `xdg-open`
      · 打开技能文档用 `open` / `os.startfile` / `xdg-open`
    同一个「打开」动作三套写法，漏改一处就是「Mac 能开、Win 点了没反应」，
    而且这类问题只在另一平台上暴露，本机永远测不出来。

    各平台实际做法：
      · macOS   `open <path>`        —— 走 LaunchServices，文件夹→Finder 打开
      · Windows `os.startfile(path)` —— 走 ShellExecute，等价于双击
      · Linux   `xdg-open <path>`    —— freedesktop 标准

    Returns:
        True 成功；False 失败（路径不存在 / 系统没有 opener）。
        **不抛异常** —— 调用方多半在按钮回调里，抛出去就是"点了没反应"。
    """
    import subprocess
    if not path:
        return False
    if not os.path.exists(path):
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            os.startfile(path)        # noqa: S606 - 本地路径，来自软件自身
        else:
            subprocess.Popen(["xdg-open", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def install():
    """把四层加进 sys.path（幂等），返回项目根目录。"""
    for p in reversed(LAYERS):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    return ROOT


def ensure_dirs():
    """首次运行时补齐数据目录。"""
    for d in (DATA, TPL_DIR, FONT_DIR, IMG_LIB, PREVIEW_DIR):
        os.makedirs(d, exist_ok=True)
