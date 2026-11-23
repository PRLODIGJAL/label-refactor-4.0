# -*- coding: utf-8 -*-
"""barcode.py —— Code128 条码与 GS1 GTIN 校验位。

从 1.0（HTML 版）移植过来的，算法一模一样：
    · gtin14()    算 GTIN-14 的最后一位校验码（客户方前缀 00000000）
    · code128c()  纯数字用 C 码（一个字符对两个数字，条码短）
    · code128b()  含字母用 B 码
    · bits()      输出 0/1 序列，1 = 黑条，0 = 空

为什么不引第三方库：整包要离线，多一个依赖就多一个出错点；
Code128 的编码表是固定的 107 项，直接搬过来最省事。
"""

# Code128 的 107 个条空模式（每项 6 位数字 = 条空交替的宽度，末项 7 位是终止符）
PAT = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
       "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 221231 213212 "
       "223112 312131 311222 321122 321221 312212 322112 322211 212123 212321 232121 111323 "
       "131123 131321 112313 132113 132311 211313 231113 231311 112133 112331 132131 113123 "
       "113321 133121 313121 211331 231131 213113 213311 213131 311123 311321 331121 312113 "
       "312311 332111 314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
       "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 111242 121142 "
       "121241 114212 124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 "
       "131141 114113 114311 411113 411311 113141 114131 311141 411131 211412 211214 211232 "
       "2331112").split()

# 客户方的 GS1 厂商前缀（1.0 里写死的）
GTIN_PREFIX = "00000000"

START_B = 104
START_C = 105
CODE_C = 99
STOP = 106


def gtin14(serial) -> str:
    """由产品系列号算 14 位 GTIN（含校验位）。

    Args:
        serial: 系列号（如 "1209"）
    Returns:
        14 位数字字符串
    """
    d = GTIN_PREFIX + "4" + str(serial or "").strip()
    s = 0
    for i in range(13):
        v = int(d[12 - i])
        s += (v * 3) if (i % 2 == 0) else v
    return d + str((10 - s % 10) % 10)


def _finish(vals) -> list:
    """补校验符和终止符，展开成 0/1 序列。"""
    vals = list(vals)
    s = vals[0]
    for i in range(1, len(vals)):
        s += i * vals[i]
    vals.extend([s % 103, STOP])
    bits = []
    black = True
    for v in vals:
        for ch in PAT[v]:
            n = int(ch)
            bits.extend([1 if black else 0] * n)
            black = not black
    return bits


def code128c(digits: str) -> list:
    """纯数字用 C 码：一个符号对两个数字，条码更短。"""
    d = str(digits)
    if len(d) % 2:
        d = "0" + d          # 奇数位补 0，避免抛错（比直接失败好用）
    vals = [START_C, CODE_C]
    for i in range(0, len(d), 2):
        vals.append(int(d[i:i + 2]))
    return _finish(vals)


def code128b(text: str) -> list:
    """含字母/符号用 B 码。"""
    vals = [START_B]
    for ch in str(text):
        o = ord(ch)
        if not (32 <= o <= 126):
            raise ValueError("Code128B 只支持 ASCII 可见字符：%r" % ch)
        vals.append(o - 32)
    return _finish(vals)


FNC1 = 102


def code128gs1(text: str) -> list:
    """GS1-128（UCC/EAN-128）：`01...|17...|30...|10...`。

    为什么必须单独一条：GS1 的「AI 段」之间要插 FNC1（符号值 102），否则
    扫码枪会把 `(30)1` 和后面的批号连成一串读成别的 AI。而 C 码只吃偶数个
    数字 —— `30` + `1` 是三位，得多切一次 B 码。以前这条链路是「奇数位补
    个 0」，补完条码内容是错的，但屏幕上看着一样，扫出来才发现。

    按 `|` 分段，每段先用 C 码两两编；落单一位就切 B 码编完再切回来，
    段末补 FNC1。整串校验符由 `_finish` 统一算。
    """
    segs = [s for s in str(text).split("|") if s]
    if not segs:
        return []
    # 起始符后面紧跟一个 FNC1：GS1 靠它把「这是 GS1 符号」告诉扫码枪，
    # 没有它扫出来只是一串数字，AI 解析不出来。规范里它是必填项。
    vals = [START_C, FNC1]
    cur = "C"
    for k, seg in enumerate(segs):
        s = str(seg)
        pairs = len(s) // 2
        if cur != "C":
            vals.append(CODE_C)
            cur = "C"
        for i in range(pairs):
            vals.append(int(s[2 * i:2 * i + 2]))
        if len(s) % 2:                      # 落单的一位改用 B 码
            if cur != "B":
                vals.append(100)            # CODE B
                cur = "B"
            vals.append(ord(s[-1]) - 32)
        if k < len(segs) - 1:               # 末段不补 FNC1：它只是分隔符
            vals.append(FNC1)
    return _finish(vals)


def bits(seq: str, mode: str = "C") -> list:
    """按模式编码，返回 0/1 序列。"""
    seq = str(seq or "").strip()
    if not seq:
        return []
    m = str(mode or "C").upper()
    if m == "GS1":
        return code128gs1(seq)
    if m == "B":
        return code128b(seq)
    return code128c(seq)


def width_mm(seq: str, mod: float, mode: str = "C") -> float:
    """条码总宽（毫米）= 模块数 × 模块宽。"""
    return len(bits(seq, mode)) * float(mod or 0.3)


# GS1 对 Code128 的 X 尺寸要求：0.25 ~ 0.50 mm。低于 0.25 印出来容易糊成一片，
# 扫码枪读不出；高于 0.5 条码会占满版面。
X_MIN, X_MAX = 0.25, 0.50


def module_mm(seq: str, total_w_mm: float, mode: str = "C") -> float:
    """按「固定总宽」排版时，一个模块实际有多宽（毫米）。

    为什么值得算：同一块位置，换个规格条码长度就会变 —— 长度变了而总宽不
    变，模块就被挤细。0.20 mm 和 0.25 mm 在屏幕上看没区别，印出来一个是
    能扫、一个是废码。这里把这个数算出来，界面上直接标红。
    """
    n = len(bits(seq, mode))
    if n <= 0 or total_w_mm <= 0:
        return 0.0
    return float(total_w_mm) / n


def geom(seq: str, mode: str = "C", mod=None, width=None):
    """条码的实际几何：(总宽 mm, 模块宽 mm)。

    两种写法二选一，优先用「模块宽」：
      · mod 给了   → 总宽 = 模块宽 × 模块数（条码的固有尺寸，换规格不会糊）
      · 只给 width → 模块宽 = 总宽 ÷ 模块数（挤在固定位置里，长度变了会变细）

    界面、命中框、渲染都从这里取数，才不会出现「选中框比条码窄一截」。
    """
    try:
        n = len(bits(seq, mode))
    except Exception:
        n = 0                      # 内容还没填完（带【占位符】）时编不出来，别炸
    if n <= 0:
        return (float(width or 20.0), float(mod or 0.3))
    if mod:
        return (n * float(mod), float(mod))
    w = float(width or 20.0)
    return (w, w / n)
