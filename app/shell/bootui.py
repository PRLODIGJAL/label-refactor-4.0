#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bootui.py —— 控制台输出的统一格式（照抄桌面上其他工具的约定）。

标记只有四种：`[ OK ]` `[ !! ]` `[ -- ]` `[ .. ]`；分节只用 `-- 名字 ----` 一行，
宽度固定 62 列。启动壳和界面都从这里输出，别在别处直接 print。
"""
import sys
import unicodedata

W = 62


def _w(s: str) -> int:
    """字符串的显示宽度：中文/全角字符算 2 列。

    ⚠️ 不能用 len()。`-- 运行时 ` 是 8 个字符但占 11 列，
    按字符数补横线会让整行超出 62 列（实测 65~66），分节线就参差不齐了。
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _emit(line: str = "") -> None:
    try:
        print(line)
        sys.stdout.flush()
    except Exception:
        pass


def ok(msg: str) -> None:
    _emit("[ OK ] " + msg)


def err(msg: str) -> None:
    _emit("[ !! ] " + msg)


def info(msg: str) -> None:
    _emit("[ -- ] " + msg)


def step(msg: str) -> None:
    _emit("[ .. ] " + msg)


def section(title: str) -> None:
    head = "-- " + title + " "
    _emit()
    _emit(head + "-" * max(0, W - _w(head)))


def blank() -> None:
    _emit()
