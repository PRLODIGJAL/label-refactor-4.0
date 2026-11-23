#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标签重构工具 v4.0 —— 环境自检与启动器。

双击启动器（Windows 是「启动标签重构工具.bat」，macOS 是「启动标签重构工具.command」）
会调到这里，由它负责三件事：
  1. 检查自带运行时（runtime/）还能不能用；
  2. 缺依赖就从 wheels/ 离线装（没网也能修）；
  3. 就绪后用 runtime 里的解释器拉起界面（app/ui/designer_app.py）。

为什么内嵌运行时
----------------
.runtime 是整个 Python 都在文件夹里，可重定位；拷到任何电脑、任何位置，
双击就能跑，不依赖系统装没装 Python、装的是哪个版本。

用法
----
    python setup_env.py              自检 → 修复 → 启动（正常双击走这条）
    python setup_env.py --check      只自检并打印结论，不启动界面
    python setup_env.py --setup      重新识别（补依赖、验证），不启动界面
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RUNTIME = os.path.join(ROOT, "runtime")
WHEELS = os.path.join(ROOT, "wheels")
APP = os.path.join(ROOT, "app", "ui", "designer_app.py")

# 自带运行时的解释器在包里的相对位置，两平台不一样：
#   Windows  runtime\python.exe
#   macOS    runtime/bin/python3
# 只在这一处按平台取名，别在别处再写死路径。
PY_EXE = os.path.join(RUNTIME, "python.exe" if os.name == "nt" else os.path.join("bin", "python3"))
PY_EXE_REL = os.path.relpath(PY_EXE, ROOT)

sys.path.insert(0, HERE)
import bootui as B  # noqa: E402

TOOL_NAME = "标签重构工具"
TOOL_VERSION = "4.0"

# 依赖：pillow 画图/缩放，pymupdf 读 PDF 底图并导出 PDF
WHEEL_NEED = [("PIL", "pillow"), ("pymupdf", "pymupdf")]
STD_IMPORTS = ("tkinter", "zipfile", "json")


def _try_import(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def check_runtime() -> bool:
    B.section("运行时")
    exe = PY_EXE
    if not os.path.exists(exe):
        B.err("找不到 %s —— 这个包不完整，把整个文件夹重新拷一次" % PY_EXE_REL)
        return False
    B.ok("解释器 %d.%d.%d" % sys.version_info[:3])
    bad = [m for m in STD_IMPORTS if not _try_import(m)]
    if bad:
        B.err("标准库缺失：%s" % "、".join(bad))
        return False
    B.ok("标准库齐全（%s）" % "、".join(STD_IMPORTS))
    return True


def ensure_deps(auto_fix: bool = True) -> bool:
    """检查 PIL / fitz，缺了就离线装。"""
    B.section("依赖")
    missing = [(m, pkg) for m, pkg in WHEEL_NEED if not _try_import(m)]
    if not missing:
        B.ok("pillow / pymupdf 都在")
        return True
    names = "、".join(pkg for _, pkg in missing)
    if not auto_fix:
        B.err("缺 %s" % names)
        return False
    B.step("缺 %s，从 %s 离线安装…" % (names, os.path.basename(WHEELS)))
    cmd = [sys.executable, "-m", "pip", "install", "--no-index",
           "--find-links", WHEELS, "--quiet"] + [pkg for _, pkg in missing]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as e:
        B.err("安装失败：%s" % e)
        return False
    if r.returncode != 0:
        B.err("安装失败：%s" % (r.stderr or r.stdout)[-300:])
        return False
    for m, _ in missing:
        if not _try_import(m):
            B.err("%s 装完仍导不进来" % m)
            return False
    B.ok("已装好 %s" % names)
    return True


def check_files() -> bool:
    B.section("程序文件")
    if not os.path.exists(APP):
        B.err("找不到界面文件 %s" % os.path.relpath(APP, ROOT))
        return False
    B.ok("界面文件就位")
    data = os.path.join(ROOT, "data")
    tpl = os.path.join(ROOT, "模板")
    for d in (data, tpl):
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
    B.ok("数据目录就位（模板存放在 %s）" % os.path.relpath(tpl, ROOT))
    return True


def launch() -> int:
    B.section("启动")
    B.step("拉起界面…")
    # 界面在 app/ui/ 下，光把 app 塞进 sys.path 找不到 —— 统一走 paths.install()
    sys.path.insert(0, os.path.join(ROOT, "app"))
    try:
        import paths
        paths.install()          # 把 ui / engine / judge / shell 四层都加进来（幂等）
        import designer_app
    except Exception as e:
        B.err("界面加载失败：%s" % e)
        import traceback
        traceback.print_exc()
        return 3
    B.ok("界面已加载，关闭窗口即退出")
    B.blank()
    designer_app.main()
    return 0


def main() -> int:
    args = sys.argv[1:]
    B.blank()
    B.info("%s v%s" % (TOOL_NAME, TOOL_VERSION))

    if not check_runtime():
        return 2
    if not check_files():
        return 2

    if "--check" in args:
        ensure_deps(auto_fix=False) and B.blank()
        return 0
    if not ensure_deps(auto_fix="--setup" in args or "--check" not in args):
        return 2
    if "--setup" in args:
        B.ok("重新识别完成，可以正常启动了")
        return 0
    return launch()


if __name__ == "__main__":
    sys.exit(main())
