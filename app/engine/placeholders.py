# -*- coding: utf-8 -*-
"""placeholders.py —— 占位符解析与替换。

标签上的文字用 `【NOTE 03】` 这种写法指代「图纸上箭头指的那个号」，
数据从数据表里按列名取。也支持日期运算：`【生产日期+2y】`、`【-1d】`、`【+36m】`。

为什么占位符用中文书名号而不是 {{}}：
  现场同事看标签稿时，【】在中文排版里一眼能认出来是「要填的地方」，
  {{}} 在密集小字里几乎看不见。
"""
import re
from datetime import date, datetime, timedelta

PH = re.compile(r"【(.+?)】")
# 日期链：字段名 + 一串「±N天/月/年」。名字部分不吃 +-，运算部分必须
# 从某个位置起一路铺到结尾，中间不允许夹杂别的字符。
#   【生产日期+3y】            一层（老写法，继续支持）
#   【生产日期+3y-1d】         两层（模板「有效期」实际就是这种，见下）
# 为什么原来只支持一层：DATE_EXPR = ^(.+?)([+-])(\d+)([dmy])$ 的懒惰匹配
# 把「生产日期+3y」当成 base 名去查表，查不到 → 整串原样保留 → 标签上印
# 一串【括号】。模板里 有效期 = =【生产日期+3y-1d】 一直在用这种写法，
# 只是被 示例数据.有效期 兜底盖住，看不出失败（B1 修复，2026-11-20）。
DATE_CHAIN = re.compile(r"^(.+?)((?:[+-]\d+[dmy])+$)")
DATE_OP = re.compile(r"([+-])(\d+)([dmy])")

# 占位符后面可以挂一个「格式」：`【有效期:ymd】` → 270804。
# 为什么需要：条码里那串 GS1 是拼出来的（01+GTIN+17+日期+10+批号），
# 日期得是 YYMMDD、GTIN 得去掉前面的 (01)。没有格式的话，这些拼接只能
# 在生成脚本里预先算好写进示例数据 —— 换个规格渲染出来就还是演示值。
FORMATS = ("ymd", "ymd4", "y2", "gtin", "ai")


def strip_ai(s: str) -> str:
    """去掉「(01)」这种 AI 前缀，只留数据本身。"""
    return re.sub(r"^\(\d{2}\)", "", str(s or "")).strip()


def format_value(val, fmt: str) -> str:
    """按格式加工一个字段值。认不出来就原样返回。"""
    s = str(val if val is not None else "").strip()
    if not s or not fmt:
        return s
    if fmt in ("ymd", "ymd4", "y2"):
        d = parse_date(s)
        if not d:
            return s
        return d.strftime({"ymd": "%y%m%d", "ymd4": "%Y%m%d", "y2": "%y"}[fmt])
    if fmt == "ai":
        return strip_ai(s)
    if fmt == "gtin":
        g = strip_ai(s)
        # 印刷稿把 14 位 GTIN 断成 1/7/6 三组，人眼对位校验位更好读
        return "%s %s %s" % (g[:1], g[1:8], g[8:]) if len(g) == 14 else g
    return s


def add_months(d: date, n: int) -> date:
    """月末安全地加月：1/31 +1m → 2/28（闰年 2/29），不会滚到 3 月。"""
    y, m = d.year, d.month - 1 + n
    y += m // 12
    m = m % 12 + 1
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    last = (nxt - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def parse_date(v) -> date | None:
    if isinstance(v, (date, datetime)):
        return v.date() if isinstance(v, datetime) else v
    s = str(v or "").strip().replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(s[:len(fmt) + 2].strip("-"), fmt).date()
        except Exception:
            continue
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    return None


def date_calc(base, n: int, unit: str) -> str:
    """一层日期运算：base ± n 天(d)/月(m)/年(y)，月末安全。失败返回 ""。"""
    d = parse_date(base)
    if not d:
        return ""
    if unit == "d":
        d = d + timedelta(days=n)
    else:
        d = add_months(d, n * 12 if unit == "y" else n)
    return d.strftime("%Y-%m-%d")


def date_chain(base, ops: list) -> str:
    """多层日期运算：按顺序做一串 [±n, 单位]。

    例：date_chain("2024-08-05", [(+3, "y"), (-1, "d")]) → "2027-08-04"。
    顺序就是书写顺序（先 +3y 再 -1d），与手工心算一致。
    base 解析失败或中途失败返回 ""，由调用方决定「原样保留」。
    """
    d = parse_date(base)
    if not d:
        return ""
    for n, unit in ops:
        if unit == "d":
            d = d + timedelta(days=n)
        else:
            d = add_months(d, n * 12 if unit == "y" else n)
    return d.strftime("%Y-%m-%d")


def fill_placeholders(text: str, fields: dict) -> str:
    """把 【名字】 换成 fields 里的值；找不到就原样保留（好让人看见哪儿没填上）。"""
    if not text or "【" not in text:
        return text

    def one(m):
        key = m.group(1).strip()
        fmt = ""
        if ":" in key:
            base, tail = key.rsplit(":", 1)
            if tail.strip() in FORMATS:
                key, fmt = base.strip(), tail.strip()
        val = None
        if key in fields and fields[key] not in (None, ""):
            val = fields[key]
        else:
            # 日期链（含一层老写法）：【生产日期+3y-1d】、【生产日期+2y】
            e = DATE_CHAIN.match(key)
            if e:
                base = fields.get(e.group(1).strip())
                if base:
                    ops = []
                    for s, n, u in DATE_OP.findall(e.group(2)):
                        sign = 1 if s == "+" else -1
                        ops.append((sign * int(n), u))
                    r = date_chain(base, ops)
                    if r:
                        val = r
        if val is None:
            return m.group(0)
        if not fmt:
            return str(val)
        return format_value(val, fmt)

    return PH.sub(one, text)


def used_keys(text: str) -> list:
    """这段文字用到了哪些占位符（用于右侧绑定面板高亮）。"""
    return [m.group(1).strip() for m in PH.finditer(text or "")]
