# -*- coding: utf-8 -*-
"""字节模式二维码，纠错等级 M。输出 SVG，供手机 WireGuard 扫码导入。"""

_ECC_M = (
    10, 16, 26, 18, 24, 16, 18, 22, 22, 26,
    30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
    26, 28, 28, 28, 28, 28, 28, 28, 28, 28,
    28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
)
_BLOCKS_M = (
    1, 1, 1, 2, 2, 4, 4, 4, 5, 5,
    5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
    17, 17, 18, 20, 21, 23, 25, 26, 28, 29,
    31, 33, 35, 37, 38, 40, 43, 45, 47, 49,
)
_ALIGN = (
    (),
    (6, 18),
    (6, 22),
    (6, 26),
    (6, 30),
    (6, 34),
    (6, 22, 38),
    (6, 24, 42),
    (6, 26, 46),
    (6, 28, 50),
    (6, 30, 54),
    (6, 32, 58),
    (6, 34, 62),
    (6, 26, 46, 66),
    (6, 26, 48, 70),
    (6, 26, 50, 74),
    (6, 30, 54, 78),
    (6, 30, 56, 82),
    (6, 30, 58, 86),
    (6, 34, 62, 90),
    (6, 28, 50, 72, 94),
    (6, 26, 50, 74, 98),
    (6, 30, 54, 78, 102),
    (6, 28, 54, 80, 106),
    (6, 32, 58, 84, 110),
    (6, 30, 58, 86, 114),
    (6, 34, 62, 90, 118),
    (6, 26, 50, 74, 98, 122),
    (6, 30, 54, 78, 102, 126),
    (6, 26, 52, 78, 104, 130),
    (6, 30, 56, 82, 108, 134),
    (6, 34, 60, 86, 112, 138),
    (6, 30, 58, 86, 114, 142),
    (6, 34, 62, 90, 118, 146),
    (6, 30, 54, 78, 102, 126, 150),
    (6, 24, 50, 76, 102, 128, 154),
    (6, 28, 54, 80, 106, 132, 158),
    (6, 32, 58, 84, 110, 136, 162),
    (6, 26, 54, 82, 110, 138, 166),
    (6, 30, 58, 86, 114, 142, 170),
)


def _gf_tables():
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_EXP, _LOG = _gf_tables()


def _gf_mul(a, b):
    if not a or not b:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_encode(data, nsym):
    gen = [1]
    for i in range(nsym):
        nxt = [0] * (len(gen) + 1)
        for j, coef in enumerate(gen):
            nxt[j] ^= coef
            nxt[j + 1] ^= _gf_mul(coef, _EXP[i])
        gen = nxt
    res = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = res[i]
        if not coef:
            continue
        for j, g in enumerate(gen):
            res[i + j] ^= _gf_mul(g, coef)
    return res[len(data):]


def _raw_codewords(ver):
    size = 16 * ver + 128
    result = size * ver + 64
    if ver >= 2:
        num = ver // 7 + 2
        result -= (25 * num - 10) * num - 55
        if ver >= 7:
            result -= 36
    return result // 8


def _data_codewords(ver):
    return _raw_codewords(ver) - _ECC_M[ver - 1] * _BLOCKS_M[ver - 1]


def _bits_of(data):
    out = []
    for byte in data:
        for shift in range(7, -1, -1):
            out.append((byte >> shift) & 1)
    return out


def _append_bits(buf, value, n):
    for i in range(n - 1, -1, -1):
        buf.append((value >> i) & 1)


def _payload_bits(text, ver):
    raw = text.encode("utf-8")
    bits = []
    _append_bits(bits, 0x4, 4)
    _append_bits(bits, len(raw), 8 if ver <= 9 else 16)
    for b in raw:
        _append_bits(bits, b, 8)
    cap = _data_codewords(ver) * 8
    terminator = min(4, cap - len(bits))
    bits.extend([0] * terminator)
    while len(bits) % 8:
        bits.append(0)
    pad = (0xEC, 0x11)
    i = 0
    while len(bits) < cap:
        _append_bits(bits, pad[i & 1], 8)
        i += 1
    return bits[:cap]


def _choose_version(text):
    n = len(text.encode("utf-8"))
    for ver in range(1, 41):
        header = 4 + (8 if ver <= 9 else 16)
        if header + n * 8 <= _data_codewords(ver) * 8:
            return ver
    raise ValueError("内容过长，无法生成二维码")


def _blocks(ver, bits):
    ecc_n = _ECC_M[ver - 1]
    nblocks = _BLOCKS_M[ver - 1]
    data = []
    for i in range(0, len(bits), 8):
        val = 0
        for b in bits[i:i + 8]:
            val = (val << 1) | b
        data.append(val)
    n2 = len(data) % nblocks
    n1 = nblocks - n2
    short = len(data) // nblocks
    blocks = []
    idx = 0
    for i in range(nblocks):
        ln = short if i < n1 else short + 1
        if n2 == 0:
            ln = short
        blocks.append(data[idx:idx + ln])
        idx += ln
    eccs = [_rs_encode(b, ecc_n) for b in blocks]
    out = []
    longest = max(len(b) for b in blocks)
    for i in range(longest):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ecc_n):
        for e in eccs:
            out.append(e[i])
    return out


def _blank(size):
    return [[False] * size for _ in range(size)], [[False] * size for _ in range(size)]


def _set_func(mod, func, r, c, dark):
    mod[r][c] = dark
    func[r][c] = True


def _finder(mod, func, r0, c0):
    for dr in range(7):
        for dc in range(7):
            edge = dr in (0, 6) or dc in (0, 6)
            core = 2 <= dr <= 4 and 2 <= dc <= 4
            _set_func(mod, func, r0 + dr, c0 + dc, edge or core)


def _draw_patterns(ver):
    size = ver * 4 + 17
    mod, func = _blank(size)
    _finder(mod, func, 0, 0)
    _finder(mod, func, 0, size - 7)
    _finder(mod, func, size - 7, 0)
    for i in range(8):
        _set_func(mod, func, 7, i, False)
        _set_func(mod, func, i, 7, False)
        _set_func(mod, func, 7, size - 1 - i, False)
        _set_func(mod, func, i, size - 8, False)
        _set_func(mod, func, size - 8, i, False)
        _set_func(mod, func, size - 1 - i, 7, False)
    if ver >= 2:
        pos = _ALIGN[ver - 1]
        for r in pos:
            for c in pos:
                if func[r][c]:
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        _set_func(mod, func, r + dr, c + dc, max(abs(dr), abs(dc)) != 1)
    for i in range(size):
        if not func[6][i]:
            _set_func(mod, func, 6, i, i % 2 == 0)
        if not func[i][6]:
            _set_func(mod, func, i, 6, i % 2 == 0)
    _set_func(mod, func, size - 8, 8, True)
    for i in range(9):
        if i != 6:
            _set_func(mod, func, 8, i, False)
            _set_func(mod, func, i, 8, False)
    for i in range(8):
        _set_func(mod, func, 8, size - 1 - i, False)
        _set_func(mod, func, size - 1 - i, 8, False)
    if ver >= 7:
        for i in range(6):
            for j in range(3):
                _set_func(mod, func, i, size - 11 + j, False)
                _set_func(mod, func, size - 11 + j, i, False)
    return mod, func


def _place(mod, func, codewords):
    bits = _bits_of(codewords)
    size = len(mod)
    i = 0
    for right in range(size - 1, 0, -2):
        if right <= 6:
            right -= 1
        for vert in range(size):
            for z in range(2):
                c = right - z
                upward = ((right & 2) == 0) ^ (c < 6)
                r = (size - 1 - vert) if upward else vert
                if func[r][c]:
                    continue
                mod[r][c] = bool(bits[i]) if i < len(bits) else False
                i += 1


_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _apply_mask(mod, func, mask):
    size = len(mod)
    pred = _MASKS[mask]
    out = [row[:] for row in mod]
    for r in range(size):
        for c in range(size):
            if not func[r][c] and pred(r, c):
                out[r][c] = not out[r][c]
    return out


def _penalty(mod):
    n = len(mod)
    runs = [0] * (n + 1)
    for row in mod:
        prev, length = row[0], 0
        for cell in row:
            if cell == prev:
                length += 1
            else:
                if length >= 5:
                    runs[length] += 1
                prev, length = cell, 1
        if length >= 5:
            runs[length] += 1
    for c in range(n):
        prev, length = mod[0][c], 0
        for r in range(n):
            cell = mod[r][c]
            if cell == prev:
                length += 1
            else:
                if length >= 5:
                    runs[length] += 1
                prev, length = cell, 1
        if length >= 5:
            runs[length] += 1
    pen = sum(runs[length] * (length - 2) for length in range(5, n + 1))
    for r in range(n - 1):
        cols = iter(range(n - 1))
        for c in cols:
            top_right = mod[r][c + 1]
            if top_right != mod[r + 1][c + 1]:
                next(cols, None)
            elif top_right == mod[r][c] == mod[r + 1][c]:
                pen += 3
    for row in mod:
        cols = iter(range(n - 10))
        for c in cols:
            if (
                not row[c + 1]
                and row[c + 4]
                and not row[c + 5]
                and row[c + 6]
                and not row[c + 9]
                and (
                    row[c]
                    and row[c + 2]
                    and row[c + 3]
                    and not row[c + 7]
                    and not row[c + 8]
                    and not row[c + 10]
                    or not row[c]
                    and not row[c + 2]
                    and not row[c + 3]
                    and row[c + 7]
                    and row[c + 8]
                    and row[c + 10]
                )
            ):
                pen += 40
            if row[c + 10]:
                next(cols, None)
    for c in range(n):
        rows = iter(range(n - 10))
        for r in rows:
            if (
                not mod[r + 1][c]
                and mod[r + 4][c]
                and not mod[r + 5][c]
                and mod[r + 6][c]
                and not mod[r + 9][c]
                and (
                    mod[r][c]
                    and mod[r + 2][c]
                    and mod[r + 3][c]
                    and not mod[r + 7][c]
                    and not mod[r + 8][c]
                    and not mod[r + 10][c]
                    or not mod[r][c]
                    and not mod[r + 2][c]
                    and not mod[r + 3][c]
                    and mod[r + 7][c]
                    and mod[r + 8][c]
                    and mod[r + 10][c]
                )
            ):
                pen += 40
            if mod[r + 10][c]:
                next(rows, None)
    dark = sum(cell for row in mod for cell in row)
    percent = dark / (n * n)
    pen += int(abs(percent * 100 - 50) / 5) * 10
    return pen


def _format_bits(mask):
    data = mask  # ECC M is 00
    rem = data << 10
    for i in range(4, -1, -1):
        if rem & (1 << (i + 10)):
            rem ^= 0x537 << i
    return ((data << 10) | (rem & 0x3FF)) ^ 0x5412


def _version_bits(ver):
    rem = ver << 12
    for i in range(5, -1, -1):
        if rem & (1 << (i + 12)):
            rem ^= 0x1F25 << i
    return (ver << 12) | (rem & 0xFFF)


def _bit(val, i):
    return bool((val >> i) & 1)


def _draw_format(mod, mask):
    bits = _format_bits(mask)
    size = len(mod)
    for i in range(15):
        dark = _bit(bits, i)
        if i < 6:
            mod[i][8] = dark
        elif i < 8:
            mod[i + 1][8] = dark
        else:
            mod[size - 15 + i][8] = dark
        if i < 8:
            mod[8][size - i - 1] = dark
        elif i < 9:
            mod[8][15 - i] = dark
        else:
            mod[8][15 - i - 1] = dark
    mod[size - 8][8] = True


def _draw_version(mod, ver):
    if ver < 7:
        return
    bits = _version_bits(ver)
    size = len(mod)
    i = 0
    for r in range(6):
        for c in range(3):
            dark = _bit(bits, i)
            mod[r][size - 11 + c] = dark
            mod[size - 11 + c][r] = dark
            i += 1


def encode_modules(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    text = str(text)
    if not text:
        raise ValueError("二维码内容为空")
    ver = _choose_version(text)
    bits = _payload_bits(text, ver)
    words = _blocks(ver, bits)
    base, func = _draw_patterns(ver)
    _place(base, func, words)
    size = len(base)
    best, best_pen, best_mask = None, None, 0
    for mask in range(8):
        cand = _apply_mask(base, func, mask)
        cand[size - 8][8] = False
        pen = _penalty(cand)
        if best is None or pen < best_pen:
            best, best_pen, best_mask = cand, pen, mask
    _draw_format(best, best_mask)
    _draw_version(best, ver)
    return best


def qr_svg(text, quiet=4):
    mod = encode_modules(text)
    n = len(mod)
    span = n + quiet * 2
    parts = []
    for r, row in enumerate(mod):
        for c, dark in enumerate(row):
            if dark:
                parts.append("M%d %dh1v1h-1z" % (c + quiet, r + quiet))
    body = "".join(parts)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
        'shape-rendering="crispEdges" role="img">'
        '<rect width="100%%" height="100%%" fill="#fff"/>'
        '<path fill="#000" d="%s"/></svg>'
    ) % (span, span, body)
