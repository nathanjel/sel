"""UTF-8 codec, hand-written on purpose.

The host's own facilities are not used. `bytes.decode('utf-8')` raises
UnicodeDecodeError rather than E_UTF8 and its error positions are byte offsets
into a message string; `str.encode` refuses lone surrogates with a different
exception again. Both would leak a host exception type through an API whose
whole contract is that every failure is a SelError.

Python does give one thing free that the other hosts pay for: `str` is a
sequence of code points, so `len()` and slicing already count what SEL counts.
That is why `to_code_points` here is nearly the identity — it exists to *reject*
lone surrogates, which Python permits in a `str` and UTF-8 has no encoding for.
"""

from __future__ import annotations

from .errors import Pos, fail


def to_code_points(s: str, pos: Pos | None = None) -> list[int]:
    """Code points, rejecting the lone surrogates Python allows in a str.

    `chr(0xD800)` is a legal Python str element and has no UTF-8 encoding, so it
    cannot be a SEL TEXT value. The other hosts hit the same wall from the other
    side: JS strings are UTF-16 and produce lone surrogates by slicing.
    """
    out = []
    for ch in s:
        c = ord(ch)
        if 0xD800 <= c <= 0xDFFF:
            which = 'high' if c <= 0xDBFF else 'low'
            fail('E_UTF8', f'unpaired {which} surrogate', pos)
        out.append(c)
    return out


def from_code_points(cps: list[int]) -> str:
    return ''.join(map(chr, cps))


def encode_utf8(s: str, pos: Pos | None = None) -> bytes:
    cps = to_code_points(s, pos)
    out = bytearray()
    for c in cps:
        if c < 0x80:
            out.append(c)
        elif c < 0x800:
            out += bytes((0xC0 | (c >> 6), 0x80 | (c & 0x3F)))
        elif c < 0x10000:
            out += bytes((0xE0 | (c >> 12), 0x80 | ((c >> 6) & 0x3F), 0x80 | (c & 0x3F)))
        else:
            out += bytes((0xF0 | (c >> 18), 0x80 | ((c >> 12) & 0x3F),
                          0x80 | ((c >> 6) & 0x3F), 0x80 | (c & 0x3F)))
    return bytes(out)


def decode_utf8(data: bytes, pos: Pos | None = None) -> str:
    """Strict: rejects overlong forms, surrogates, values above U+10FFFF and
    truncated sequences. No replacement characters, ever.
    """
    cps: list[int] = []
    n = len(data)
    i = 0
    while i < n:
        b = data[i]
        if b < 0x80:
            cps.append(b)
            i += 1
            continue
        if 0xC2 <= b <= 0xDF:
            need, cp, lo, hi = 1, b & 0x1F, 0x80, 0xBF
        elif b == 0xE0:
            need, cp, lo, hi = 2, 0, 0xA0, 0xBF          # reject overlong 3-byte
        elif 0xE1 <= b <= 0xEC:
            need, cp, lo, hi = 2, b & 0x0F, 0x80, 0xBF
        elif b == 0xED:
            need, cp, lo, hi = 2, 0x0D, 0x80, 0x9F       # reject surrogates
        elif 0xEE <= b <= 0xEF:
            need, cp, lo, hi = 2, b & 0x0F, 0x80, 0xBF
        elif b == 0xF0:
            need, cp, lo, hi = 3, 0, 0x90, 0xBF          # reject overlong 4-byte
        elif 0xF1 <= b <= 0xF3:
            need, cp, lo, hi = 3, b & 0x07, 0x80, 0xBF
        elif b == 0xF4:
            need, cp, lo, hi = 3, 4, 0x80, 0x8F          # cap at U+10FFFF
        else:
            fail('E_UTF8', f'invalid start byte 0x{b:x} at byte {i}', pos)

        if i + need >= n:
            fail('E_UTF8', f'truncated sequence at byte {i}', pos)
        for k in range(1, need + 1):
            c = data[i + k]
            lo_k = lo if k == 1 else 0x80
            hi_k = hi if k == 1 else 0xBF
            if c < lo_k or c > hi_k:
                fail('E_UTF8', f'invalid continuation byte at byte {i + k}', pos)
            cp = (cp << 6) | (c & 0x3F)
        cps.append(cp)
        i += need + 1
    return from_code_points(cps)


def bytes_to_hex(data: bytes) -> str:
    return data.hex()


def bytes_equal(a: bytes, b: bytes) -> bool:
    return a == b


def bytes_compare(a: bytes, b: bytes) -> int:
    """Bytewise, as the spec requires.

    Python's own `<` on bytes is already bytewise and would give the same answer,
    and on str it compares code points, which happens to agree with UTF-8 byte
    order too. Neither shortcut is taken: the spec says bytes, the other three
    hosts compare bytes explicitly because their shortcuts are *wrong*, and the
    next reader should not have to work out that Python's are safe.
    """
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return -1 if a[i] < b[i] else 1
    if len(a) == len(b):
        return 0
    return -1 if len(a) < len(b) else 1
