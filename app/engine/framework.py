# -*- coding: utf-8 -*-
"""framework.py —— 读「标签框架.xlsx」的产品参数信息表。

这张表才是标签的完整数据模型：一行 = 一个成品编码 = 一张标签的全部内容。
    静态数据：商品名 / 学术名 / 证书号 / 厂商信息 / 注册人信息 / 存贮条件
    技术参数：导管图 / GTIN / 目录代码 / LENGTH / OD / BALLOONLENGTHOD /
             DISTALSHAFTODF / DISTALSHAFTODE / guiding catheter
    顺应性  ：2 / 4 / 6(NOM) / 8 / 10 / 12 / 14(RBP) / 16 / 18（各档数值）+ (NOM)数值 / (RBP)数值

为什么单独一个模块：
    以前是拿「NOTE 投喂表」当数据源，那是工具的中间产物，列名是 NOTE 01/02/03，
    跟标签上的位置对不上，只能靠猜。这张表的列名本身就是语义
    （「学术名」「DISTALSHAFTODF」），模板里写【学术名】就直接对上，
    不用再维护一层 NOTE 位号映射 —— 这才是 AI 能接手的前提。
"""
import os

import data_src

SHEET = "产品参数信息"
KEY = "成品编码"


def _merged_headers(rows) -> list:
    """两行表头合并：第二行（具体列名）优先，没有就用第一行（分组名）。"""
    if len(rows) < 2:
        return [str(x).strip() for x in rows[0]] if rows else []
    h1, h2 = rows[0], rows[1]
    out = []
    for i in range(max(len(h1), len(h2))):
        a = str(h1[i]).strip() if i < len(h1) else ""
        b = str(h2[i]).strip() if i < len(h2) else ""
        out.append(b or a)
    return out


def load(path: str) -> tuple:
    """读参数表。

    Args:
        path: 标签框架.xlsx 路径
    Returns:
        (列名列表, [每行 dict], 表名)
    Raises:
        ValueError: 找不到「产品参数信息」表，或表里没有成品编码列
    """
    tables = data_src.read_table(path)
    sheet = SHEET if SHEET in tables else None
    if sheet is None:
        # 退一步：找任何含「成品编码」列的表
        for nm, rows in tables.items():
            if rows and any(str(c).strip() == KEY for c in rows[0] + (rows[1] if len(rows) > 1 else [])):
                sheet = nm
                break
    if sheet is None:
        raise ValueError("这个文件里没有「%s」表" % SHEET)

    rows = tables[sheet]
    if len(rows) < 3:
        raise ValueError("「%s」表里没有数据行" % sheet)
    cols = _merged_headers(rows)
    if KEY not in cols:
        raise ValueError("「%s」表里没有「%s」列" % (sheet, KEY))

    recs = []
    for r in rows[2:]:
        rec = {}
        for i, c in enumerate(cols):
            if c:
                rec[c] = str(r[i]).strip() if i < len(r) else ""
        if rec.get(KEY):
            recs.append(rec)
    return cols, recs, sheet


def is_framework(path: str) -> bool:
    """这个 xlsx 是不是标签框架（含产品参数信息表）。"""
    try:
        tables = data_src.read_table(path)
        return SHEET in tables
    except Exception:
        return False


def product_list(path: str) -> list:
    """返回 [(成品编码, 学术名, Brand)]，给下拉列表用。"""
    try:
        _, recs, _ = load(path)
    except Exception:
        return []
    return [(r.get(KEY, ""), r.get("学术名", ""), r.get("产品 Brand", "")) for r in recs]
