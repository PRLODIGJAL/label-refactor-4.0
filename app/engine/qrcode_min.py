# -*- coding: utf-8 -*-
"""qrcode_min.py —— 纯 Python 二维码（QR Code）编码器 + 直接画到画布。

为什么自己写：整包要离线、零依赖，多引一个 qrcode 库就多一个打包出错点。
实现范围（够标签用）：
    · byte 模式（UTF-8），纠错等级 M；
    · 版本 1~10 自动选（最多约 200 字节）；
    · 8 种掩码按标准罚分择优；
    · 格式信息用 BCH(15,5) 生成，版本 ≥7 再补 18 位版本信息。
对外只有两个函数：
    matrix(text) -> [[0/1]]        拿到模块矩阵
    draw_qr(cv, x, y, size, text)  直接画到 labelkit.Canvas
"""

# ── 版本参数（纠错等级 M）────────────────────────────────────────
# version: (每块纠错码字数, 块数)  —— 总码字数由公式算，见 total_codewords()
_ECC_M = {1: (10, 1), 2: (16, 1), 3: (26, 1), 4: (18, 2), 5: (24, 2),
          6: (16, 4), 7: (18, 4), 8: (22, 4), 9: (22, 5), 10: (26, 5)}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
          6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
          10: [6, 28, 50]}


def _size(ver):
    return 17 + 4 * ver


def _total_codewords(ver):
    """该版本能容纳的 8 位码字总数（含纠错）。"""
    n = _size(ver)
    modules = n * n
    # 功能图形占用（定位/分隔/时序/对齐/格式/版本）
    func = 3 * 64                       # 三个定位 + 分隔
    func += 2 * (n - 16)                # 时序线（去掉与定位重叠部分）
    for r in _ALIGN[ver]:
        for c in _ALIGN[ver]:
            if (r, c) in ((6, 6), (6, n - 7), (n - 7, 6)):
                continue
            func += 25
    if ver >= 7:
        func += 36
    return (modules - func) // 8


# ── GF(256) 与 Reed-Solomon ──────────────────────────────────────
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs(data, n_ec):
    """生成 n_ec 个纠错码字。"""
    gen = [1]
    for i in range(n_ec):
        nxt = [0] * (len(gen) + 1)
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= _mul(g, _EXP[i])
        gen = nxt
    res = list(data) + [0] * n_ec
    for i in range(len(data)):
        f = res[i]
        if f:
            for j, g in enumerate(gen):
                res[i + j] ^= _mul(g, f)
    return res[len(data):]


# ── 位流 ─────────────────────────────────────────────────────────
def _bits_push(buf, val, n):
    for i in range(n - 1, -1, -1):
        buf.append((val >> i) & 1)


def _encode_data(text, ver):
    data = text.encode("utf-8")
    ec_per, blocks = _ECC_M[ver]
    cap = _total_codewords(ver) - ec_per * blocks
    bits = []
    _bits_push(bits, 4, 4)                     # byte 模式
    _bits_push(bits, len(data), 8 if ver < 10 else 16)
    for b in data:
        _bits_push(bits, b, 8)
    # 结束符 + 补零到字节
    room = cap * 8 - len(bits)
    _bits_push(bits, 0, min(4, max(0, room)))
    while len(bits) % 8:
        bits.append(0)
    # 补齐码字（0xEC / 0x11 交替）
    pad = 0xEC
    while len(bits) < cap * 8:
        _bits_push(bits, pad, 8)
        pad = 0x11 if pad == 0xEC else 0xEC
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    # 分块 + 计算纠错 + 交织
    per = cap // blocks
    extra = cap % blocks
    datablocks, ecblocks = [], []
    pos = 0
    for b in range(blocks):
        n = per + (1 if b >= blocks - extra else 0)
        blk = words[pos:pos + n]
        pos += n
        datablocks.append(blk)
        ecblocks.append(_rs(blk, ec_per))
    out = []
    for i in range(max(len(d) for d in datablocks)):
        for d in datablocks:
            if i < len(d):
                out.append(d[i])
    for i in range(ec_per):
        for e in ecblocks:
            out.append(e[i])
    return out


# ── 矩阵放置 ─────────────────────────────────────────────────────
def _place_function(m, ver):
    n = _size(ver)
    finder = [(0, 0), (0, n - 7), (n - 7, 0)]
    for (r, c) in finder:
        for i in range(-1, 8):
            for j in range(-1, 8):
                rr, cc = r + i, c + j
                if not (0 <= rr < n and 0 <= cc < n):
                    continue
                on = (0 <= i <= 6 and 0 <= j <= 6 and
                      (i in (0, 6) or j in (0, 6) or (2 <= i <= 4 and 2 <= j <= 4)))
                m[rr][cc] = 1 if on else 0
                m[rr][cc] = m[rr][cc] if on else 0
    # 时序线
    for i in range(8, n - 8):
        m[6][i] = 1 if i % 2 == 0 else 0
        m[i][6] = 1 if i % 2 == 0 else 0
    # 对齐
    for r in _ALIGN[ver]:
        for c in _ALIGN[ver]:
            if (r, c) in ((6, 6), (6, n - 7), (n - 7, 6)):
                continue
            for i in range(-2, 3):
                for j in range(-2, 3):
                    on = max(abs(i), abs(j)) != 1
                    m[r + i][c + j] = 1 if on else 0
    # 预留格式区
    for i in range(9):
        if m[8][i] is None:
            m[8][i] = 0
        if m[i][8] is None:
            m[i][8] = 0
    for i in range(8):
        if m[8][n - 1 - i] is None:
            m[8][n - 1 - i] = 0
        if m[n - 1 - i][8] is None:
            m[n - 1 - i][8] = 0
    m[n - 8][8] = 1
    if ver >= 7:
        for i in range(6):
            for j in range(3):
                m[n - 11 + j][i] = 0
                m[i][n - 11 + j] = 0


def _reserved(ver):
    """返回功能图形占用标记（True = 已被功能图形占用）。"""
    n = _size(ver)
    f = [[False] * n for _ in range(n)]
    for (r, c) in [(0, 0), (0, n - 7), (n - 7, 0)]:
        for i in range(-1, 8):
            for j in range(-1, 8):
                rr, cc = r + i, c + j
                if 0 <= rr < n and 0 <= cc < n:
                    f[rr][cc] = True
    for i in range(n):
        f[6][i] = True
        f[i][6] = True
    for r in _ALIGN[ver]:
        for c in _ALIGN[ver]:
            if (r, c) in ((6, 6), (6, n - 7), (n - 7, 6)):
                continue
            for i in range(-2, 3):
                for j in range(-2, 3):
                    f[r + i][c + j] = True
    for i in range(9):
        f[8][i] = True
        f[i][8] = True
    for i in range(8):
        f[8][n - 1 - i] = True
        f[n - 1 - i][8] = True
    f[n - 8][8] = True
    if ver >= 7:
        for i in range(6):
            for j in range(3):
                f[n - 11 + j][i] = True
                f[i][n - 11 + j] = True
    return f


def _format_bits(mask, level_bits=0b00):
    """格式信息 15 位（level M = 00）。"""
    data = (level_bits << 3) | mask
    v = data << 10
    g = 0b10100110111
    for i in range(14, 9, -1):
        if (v >> i) & 1:
            v ^= g << (i - 10)
    return ((data << 10) | v) ^ 0b101010000010010


def _apply_format(m, ver, mask):
    n = _size(ver)
    fmt = _format_bits(mask)
    bits = [(fmt >> i) & 1 for i in range(14, -1, -1)]
    # 左上
    for i in range(6):
        m[8][i] = bits[i]
    m[8][7] = bits[6]
    m[8][8] = bits[7]
    m[7][8] = bits[8]
    for i in range(9, 15):
        m[14 - i][8] = bits[i]
    # 右上 / 左下
    for i in range(8):
        m[8][n - 1 - i] = bits[i]
    for i in range(8, 15):
        m[n - 15 + i][8] = bits[i]
    m[n - 8][8] = 1


def _apply_version(m, ver):
    if ver < 7:
        return
    n = _size(ver)
    v = ver
    rem = v
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    bits = (v << 12) | rem
    for i in range(18):
        bit = (bits >> i) & 1
        r, c = i // 3, i % 3
        m[n - 11 + c][r] = bit
        m[r][n - 11 + c] = bit


def _mask_fn(mask, r, c):
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if mask == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _penalty(m):
    n = len(m)
    score = 0
    for grid in (m, [[m[r][c] for r in range(n)] for c in range(n)]):
        for row in grid:
            run, prev = 1, row[0]
            for v in row[1:]:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run, prev = 1, v
            if run >= 5:
                score += 3 + (run - 5)
    dark = sum(sum(r) for r in m)
    ratio = dark * 100.0 / (n * n)
    score += int(abs(ratio - 50) / 5) * 10
    return score


def matrix(text):
    """生成二维码模块矩阵（0/1 二维列表）。"""
    text = str(text or "")
    ver = None
    for v in range(1, 11):
        ec_per, blocks = _ECC_M[v]
        if len(text.encode("utf-8")) <= _total_codewords(v) - ec_per * blocks - 3:
            ver = v
            break
    if ver is None:
        ver = 10
    n = _size(ver)
    words = _encode_data(text, ver)

    # 数据位流
    bits = []
    for w in words:
        _bits_push(bits, w, 8)

    m = [[None] * n for _ in range(n)]
    _place_function(m, ver)
    res = _reserved(ver)

    # 之字形填数据
    idx, up = 0, True
    col = n - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if up else range(n)
        for r in rows:
            for cc in (col, col - 1):
                if not res[r][cc] and m[r][cc] is None:
                    m[r][cc] = bits[idx] if idx < len(bits) else 0
                    idx += 1
        up = not up
        col -= 2

    # 选掩码
    best, best_score = 0, None
    for mk in range(8):
        trial = [[m[r][c] for c in range(n)] for r in range(n)]
        for r in range(n):
            for c in range(n):
                if not res[r][c] and _mask_fn(mk, r, c):
                    trial[r][c] ^= 1
        _apply_format(trial, ver, mk)
        _apply_version(trial, ver)
        sc = _penalty(trial)
        if best_score is None or sc < best_score:
            best, best_score = mk, sc
    for r in range(n):
        for c in range(n):
            if not res[r][c] and _mask_fn(best, r, c):
                m[r][c] ^= 1
    _apply_format(m, ver, best)
    _apply_version(m, ver)
    return [[1 if v else 0 for v in row] for row in m]


def draw_qr(cv, x, y, size, text, quiet=2, color="#000000"):
    """把二维码画到 labelkit.Canvas。size 是码区边长（不含静区）。"""
    m = matrix(text)
    n = len(m)
    cell = size / float(n)
    k = cv.k
    for r in range(n):
        for c in range(n):
            if not m[r][c]:
                continue
            x0 = (x + c * cell) * k
            y0 = (y + r * cell) * k
            x1 = (x + (c + 1) * cell) * k
            y1 = (y + (r + 1) * cell) * k
            # 缩小预览时一个模块可能不到 1 像素，减 1 会让 x1 < x0 直接抛错
            cv.draw.rectangle([x0, y0, max(x0, x1 - 1), max(y0, y1 - 1)], fill=color)
    return size
