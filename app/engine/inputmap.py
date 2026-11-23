# -*- coding: utf-8 -*-
"""inputmap.py —— 输入映射表：每个操作在 Windows / Mac 上的触发方式。

**这张表是唯一来源**，界面按它绑、测试按它验，文档也从它生成。
以前是硬编码散在 `designer_app.py` 里，同一件事两套写法，
改一处忘一处 —— 表现就是「Mac 能行、Windows 不行」，而且本机永远测不出来。

字段
----
    op        操作名（内部用）
    name      人话名字（界面/文档用）
    mac       Mac 触发方式
    win       Windows 触发方式
    common    两端**相同**的那条路（没有就留空）
    note      实现要点 / 坑

测试怎么用
--------
`dev/test_inputmap.py` 会遍历这张表：
  · `common` 非空的，断言两端真的绑上了那条序列
  · 每个操作至少在两端各有一条路，不许出现"只有一边有"
"""
import sys

IS_MAC = sys.platform == "darwin"

# ══════════════════════════════════════════════════════════════
#  平移 / 缩放
# ══════════════════════════════════════════════════════════════
PAN_ZOOM = [
    {
        "op": "pan_v",
        "name": "竖向平移",
        "mac": "双指上下滑 / 右键拖动",
        "win": "右键拖动 / 滚轮（选中元素时）",
        "common": "",
        "note": "Mac 双指直接给 pan_y；Windows 靠右键拖动（drag_ref）",
    },
    {
        "op": "pan_h",
        "name": "横向平移",
        "mac": "**Shift+滚轮**（本次新增）/ 双指横扫",
        "win": "**Shift+滚轮**（本次新增）/ 右键拖动",
        "common": "Shift+滚轮",
        "note": "P1-2 补的。Mac 以前完全没有横向平移的路",
    },
    {
        "op": "zoom",
        "name": "缩放",
        "mac": "−/＋ 按钮、**Ctrl(⌘)+滚轮**（本次新增）",
        "win": "滚轮（未选中时）、**Ctrl+滚轮**（本次新增）、−/＋ 按钮",
        "common": "Ctrl+滚轮",           # ← 两端至少这一条
        "note": "Ctrl+滚轮是浏览器/编辑器的肌肉记忆，两端都做，"
                "这样「怎么缩放」不用分平台解释。"
                "Mac 的 Command 落 Mod1(0x8)，Control 落 0x4，两位都认",
    },
]

# ══════════════════════════════════════════════════════════════
#  编辑
# ══════════════════════════════════════════════════════════════
EDIT = [
    {
        "op": "undo", "name": "撤销",
        "mac": "⌘Z", "win": "Ctrl+Z", "common": "",
        "note": "Tk 里 Command 报 <Command-z>，Windows 报 <Control-z>，要分别绑",
    },
    {
        "op": "redo", "name": "重做",
        "mac": "⌘Y", "win": "Ctrl+Y", "common": "",
        "note": "",
    },
    {
        "op": "copy", "name": "复制",
        "mac": "⌘C", "win": "Ctrl+C", "common": "",
        "note": "",
    },
    {
        "op": "paste", "name": "粘贴",
        "mac": "⌘V", "win": "Ctrl+V", "common": "",
        "note": "P0-3 起：粘贴图片不再生成 L2，转成从图库选图",
    },
    {
        "op": "delete", "name": "删除",
        "mac": "Delete / Backspace", "win": "Delete",
        "common": "Delete",
        "note": "Mac 的 Backspace 就是主键盘 Delete，Tk 报 <Delete>",
    },
    {
        "op": "nudge", "name": "微调",
        "mac": "方向键（Shift+方向键 = 大步）",
        "win": "方向键（Shift+方向键 = 大步）",
        "common": "方向键",
        "note": "",
    },
    {
        "op": "escape", "name": "取消选择 / 退出",
        "mac": "Esc", "win": "Esc", "common": "Esc", "note": "",
    },
    {
        "op": "space", "name": "空格拖动画布",
        "mac": "按住空格 + 拖动", "win": "按住空格 + 拖动",
        "common": "空格",
        "note": "",
    },
]

# ══════════════════════════════════════════════════════════════
#  选择
# ══════════════════════════════════════════════════════════════
SELECT = [
    {
        "op": "select", "name": "单选",
        "mac": "左键单击", "win": "左键单击", "common": "左键", "note": "",
    },
    {
        "op": "multi", "name": "多选",
        "mac": "⌘+单击（或在右栏按住 Shift 点列表）",
        "win": "Ctrl+单击（或在右栏按住 Shift 点列表）",
        "common": "",
        "note": "Tk 绑 <Control-Button-1> 和 <Command-Button-1> 两套 —— "
                "macOS 上 Command 落哪一位随 Tk 版本变，写死一条会漏",
    },
    {
        "op": "context", "name": "右键菜单",
        "mac": "右键 / 双指轻点",
        "win": "右键",
        "common": "",
        # ★ 2026-11-23 真机实测确认（Tk 9.0.4 / macOS）
        #   Button-1 按下  num=1  state=0x0000
        #   Button-3 按下  num=3  state=0x0000   ← 右键就是 3，不是 2
        #   Button-1 释放  num=1  state=0x0100 [Button1]
        #   Button-3 释放  num=3  state=0x0400 [Button3]
        # 三个推论：
        #   ① 绑 <Button-3> 是对的，不用再猜 2 还是 3
        #   ② **不能用 state 判断"有没有按键"** —— 释放时 state 里带着
        #      按钮掩码（0x100/0x400），和按下时的 0 不一样
        #   ③ 判断"拖过没拖过"只能靠位移（on_right_up 里的 moved 标志），
        #      不能看 state
        "note": "Tk 9.0.4 实测 num=3（详见本文件 note）",
    },
]

# ══════════════════════════════════════════════════════════════
#  菜单 / 窗口
# ══════════════════════════════════════════════════════════════
UI = [
    {
        "op": "menu", "name": "菜单",
        "mac": "界面内按钮（不用系统菜单栏）",
        "win": "界面内按钮",
        "common": "界面内按钮",
        "note": "跨平台一致：不用 Tk 的 native menu，避免外观和位置两平台不同",
    },
]

ALL = PAN_ZOOM + EDIT + SELECT + UI


def by_op(op):
    for r in ALL:
        if r["op"] == op:
            return r
    return None


def common_paths():
    """两端**确实相同**的那些操作（测试要重点保证这些真的能用）。"""
    return [r for r in ALL if r.get("common")]


def platform_col():
    return "mac" if IS_MAC else "win"


def how(op, platform=None):
    """某操作在当前（或指定）平台的触发方式。"""
    r = by_op(op)
    if not r:
        return ""
    return r.get(platform or platform_col()) or ""


def as_markdown():
    """生成文档用的表格（说明书 / 交接文档引用它，别手抄）。"""
    lines = ["| 操作 | Mac | Windows | 两端共同 | 备注 |",
             "|---|---|---|---|---|"]
    for r in ALL:
        lines.append("| %s | %s | %s | %s | %s |" % (
            r["name"], r["mac"], r["win"], r.get("common") or "—",
            r.get("note") or ""))
    return "\n".join(lines)
