"""Binary built-ins.

base64 and CRC32 are hand-written rather than taken from `base64` and `zlib`.
The stdlib versions are lenient in ways the spec is not — `base64.b64decode`
ignores characters outside the alphabet unless validate=True, and even then
accepts some padding the other hosts reject — and agreeing with three other
implementations on exactly which inputs are E_BAD_ARG matters more here than
saving twenty lines. `zlib.crc32` would in fact agree, but is left alone so that
the four cores can be read against each other.
"""

import re

from ..errors import fail
from ..registry import define
from ..utf8 import bytes_to_hex, decode_utf8
from ..value import Value

define('BLEN', 1, 1, fn=lambda a, ctx: Value.int(len(a.bytes(0))))
define('TO_UTF8', 1, 1, fn=lambda a, ctx: Value.bin(a.bytes(0)))
define('FROM_UTF8', 1, 1,
       fn=lambda a, ctx: Value.text(decode_utf8(a.bytes(0), a.pos_of(0))))
define('TO_HEX', 1, 1, fn=lambda a, ctx: Value.text(bytes_to_hex(a.bytes(0))))

# .fullmatch() for the reason in sel/decimal.py. This one happens to be safe
# with .match() because the slice is always exactly two characters, but the
# safety is accidental and the next edit would lose it.
_HEX_PAIR = re.compile(r'^[0-9a-fA-F]{2}$')


def _from_hex(a, ctx):
    s = a.text(0)
    if len(s) % 2 != 0:
        fail('E_BAD_ARG', 'FROM_HEX needs an even number of digits', a.pos_of(0))
    out = bytearray(len(s) // 2)
    for i in range(len(out)):
        pair = s[i * 2:i * 2 + 2]
        # Checked against an explicit ASCII class, not int(pair, 16): int()
        # accepts Unicode digits, so "٣٣" would decode rather than fail.
        if not _HEX_PAIR.fullmatch(pair):
            fail('E_BAD_ARG', f'FROM_HEX: "{pair}" is not hex', a.pos_of(0))
        out[i] = int(pair, 16)
    return Value.bin(bytes(out))


define('FROM_HEX', 1, 1, fn=_from_hex)

_B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
_B64_INDEX = {ch: i for i, ch in enumerate(_B64)}


def _encode_base64(a, ctx):
    b = a.bytes(0)
    out = []
    for i in range(0, len(b), 3):
        n = (b[i] << 16) \
            | ((b[i + 1] if i + 1 < len(b) else 0) << 8) \
            | (b[i + 2] if i + 2 < len(b) else 0)
        out.append(_B64[(n >> 18) & 63])
        out.append(_B64[(n >> 12) & 63])
        out.append(_B64[(n >> 6) & 63] if i + 1 < len(b) else '=')
        out.append(_B64[n & 63] if i + 2 < len(b) else '=')
    return Value.text(''.join(out))


define('ENCODE_BASE64', 1, 1, fn=_encode_base64)


def _decode_base64(a, ctx):
    """Strict: padding is required and any character outside the alphabet fails."""
    s = a.text(0)
    pos = a.pos_of(0)
    if len(s) % 4 != 0:
        fail('E_BAD_ARG', 'DECODE_BASE64 needs a length that is a multiple of 4', pos)
    out = bytearray()
    for i in range(0, len(s), 4):
        quad = []
        padding = 0
        for k in range(4):
            ch = s[i + k]
            if ch == '=':
                if i + 4 < len(s) or k < 2:
                    fail('E_BAD_ARG', 'misplaced base64 padding', pos)
                padding += 1
                quad.append(0)
                continue
            if padding > 0:
                fail('E_BAD_ARG', 'misplaced base64 padding', pos)
            v = _B64_INDEX.get(ch)
            if v is None:
                fail('E_BAD_ARG', f'invalid base64 character "{ch}"', pos)
            quad.append(v)
        n = (quad[0] << 18) | (quad[1] << 12) | (quad[2] << 6) | quad[3]
        out.append((n >> 16) & 255)
        if padding < 2:
            out.append((n >> 8) & 255)
        if padding < 1:
            out.append(n & 255)
    return Value.bin(bytes(out))


define('DECODE_BASE64', 1, 1, fn=_decode_base64)

# CRC-32/ISO-HDLC: reflected, polynomial 0xEDB88320, init and final xor all ones.
_CRC_TABLE = None


def _crc_table():
    global _CRC_TABLE
    if _CRC_TABLE is None:
        table = []
        for i in range(256):
            c = i
            for _ in range(8):
                c = (0xEDB88320 ^ (c >> 1)) if (c & 1) else (c >> 1)
            table.append(c)
        _CRC_TABLE = table
    return _CRC_TABLE


def _crc32(a, ctx):
    t = _crc_table()
    b = a.bytes(0)
    crc = 0xFFFFFFFF
    for byte in b:
        crc = t[(crc ^ byte) & 255] ^ (crc >> 8)
    return Value.text(f'{crc ^ 0xFFFFFFFF:08x}')


define('CRC32', 1, 1, fn=_crc32)

define('BTL', 1, 1,
       fn=lambda a, ctx: Value.list([Value.int(b) for b in a.bytes(0)]))


def _ltb(a, ctx):
    v = a.val(0)
    items = v.values() if v.size() > 0 else [v]
    out = bytearray(len(items))
    for i, item in enumerate(items):
        d = item.as_decimal(a.pos_of(0))
        n = -d.digits if d.neg else d.digits
        if d.scale != 0 or n < 0 or n > 255:
            fail('E_RANGE', f'LTB element {i + 1} is not a byte value', a.pos_of(0))
        out[i] = n
    return Value.bin(bytes(out))


define('LTB', 1, 1, fn=_ltb)
