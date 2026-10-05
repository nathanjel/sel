"""Binary built-ins.

The codecs take their INPUT CHECKING from this file and their ARITHMETIC from the
stdlib. `base64.b64decode` is lenient in ways the spec is not — it ignores
characters outside the alphabet unless validate=True, and even then accepts some
padding the other hosts reject — so the accepted set is decided here, once, by an
explicit ASCII pattern (and padding rule), and only a string that passed it is
handed to `base64`. `zlib.crc32` is CRC-32/ISO-HDLC, exactly the reflected
0xEDB88320 table form this file used to spell out; python/tests/test_perf_binary.py
keeps that table version as the reference and compares the two on random data and
on the catalogue check value, so the hand-written algorithm is still what the
result is held to, at 5,000x the speed.
"""

import base64
import re
import zlib

from .._budget import check_collection, check_text
from ..errors import fail
from ..registry import define
from ..utf8 import bytes_to_hex, decode_utf8
from .. import decimal as D
from ..value import NONE, TEXT, Value

define('BLEN', 1, 1, fn=lambda a, ctx: Value.int(len(a.bytes(0))))
def _to_utf8(a, ctx):
    v = a.val(0).scalar_source(a.pos_of(0))
    if v.kind == TEXT:
        # Every code point is at least one byte, so a text over the cap is
        # refused without encoding it; below it the encoding is at most 4x.
        check_text(len(v.scalar), a.pos)
    b = a.bytes(0)
    check_text(len(b), a.pos)
    return Value.bin(b)


define('TO_UTF8', 1, 1, fn=_to_utf8)
define('FROM_UTF8', 1, 1,
       fn=lambda a, ctx: Value.text(decode_utf8(a.bytes(0), a.pos_of(0))))
def _to_hex(a, ctx):
    b = a.bytes(0)
    check_text(len(b) * 2, a.pos)
    return Value.text(bytes_to_hex(b))


define('TO_HEX', 1, 1, fn=_to_hex)

# .fullmatch() for the reason in sel/decimal.py. An explicit ASCII class, not
# int(pair, 16) and not bytes.fromhex on unchecked text: int() accepts Unicode
# digits, so "٣٣" would decode rather than fail, and fromhex skips whitespace.
_HEX_DIGITS = re.compile(r'[0-9a-fA-F]*')


def _from_hex(a, ctx):
    s = a.text(0)
    if len(s) % 2 != 0:
        fail('E_BAD_ARG', 'FROM_HEX needs an even number of digits', a.pos_of(0))
    if not _HEX_DIGITS.fullmatch(s):
        fail('E_BAD_ARG', 'FROM_HEX: not hex', a.pos_of(0))
    return Value.bin(bytes.fromhex(s))


define('FROM_HEX', 1, 1, fn=_from_hex)



def _encode_base64(a, ctx):
    b = a.bytes(0)
    check_text((len(b) + 2) // 3 * 4, a.pos)
    return Value.text(base64.b64encode(b).decode('ascii'))


define('ENCODE_BASE64', 1, 1, fn=_encode_base64)


# The alphabet, explicitly and in ASCII only: [A-Za-z] in a str pattern is
# already ASCII, but it is spelled out so nobody widens it to \w.
_B64_BODY = re.compile(r'[A-Za-z0-9+/]*')


def _decode_base64(a, ctx):
    """Strict: padding is required and any character outside the alphabet fails.

    Checked here, decoded by the stdlib: a length that is a multiple of 4, at most
    two '=' and only at the very end, everything before them in the alphabet.
    Leftover bits under the padding are not checked (the other hosts do not)."""
    s = a.text(0)
    pos = a.pos_of(0)
    if len(s) % 4 != 0:
        fail('E_BAD_ARG', 'DECODE_BASE64 needs a length that is a multiple of 4', pos)
    body = s.rstrip('=')
    if len(s) - len(body) > 2:
        fail('E_BAD_ARG', 'misplaced base64 padding', pos)
    if not _B64_BODY.fullmatch(body):
        fail('E_BAD_ARG', 'invalid base64 character', pos)
    return Value.bin(base64.b64decode(s.encode('ascii')))


define('DECODE_BASE64', 1, 1, fn=_decode_base64)

# CRC-32/ISO-HDLC: reflected, polynomial 0xEDB88320, init and final xor all ones,
# which is what zlib.crc32 computes (see the note at the top of the file).
def _crc32(a, ctx):
    return Value.text(f'{zlib.crc32(a.bytes(0)):08x}')


define('CRC32', 1, 1, fn=_crc32)

# One Dec per byte value, built once and shared: a Dec is immutable by convention
# (sel/decimal.py), and BTL of a megabyte built a Value.int per byte (2.2 s).
_BYTE_DECS = [D.from_int(i) for i in range(256)]


def _btl(a, ctx):
    b = a.bytes(0)
    check_collection(len(b), a.pos)
    num = Value._num_owned
    decs = _BYTE_DECS
    return Value._list_owned([num(decs[x]) for x in b])


define('BTL', 1, 1, fn=_btl)


def _ltb(a, ctx):
    v = a.val(0)
    if v.kind == NONE and v.size() == 0:
        return Value.bin(b'')                      # LTB of an empty list (SPEC 7.7)
    items = v.values() if v.size() > 0 else [v]
    check_collection(len(items), a.pos)
    out = bytearray(len(items))
    pos = a.pos_of(0)
    for i, item in enumerate(items):
        d = item.as_decimal(pos)
        if d.scale == 0:                           # the common case: a whole number as written
            n = -d.digits if d.neg else d.digits
        else:
            if not D.is_integer(d):
                fail('E_NOT_INT', f'LTB element {i + 1} must be a whole number', pos)
            n = D.to_safe_int(d)
        if n < 0 or n > 255:
            fail('E_RANGE', f'LTB element {i + 1} is not a byte value', pos)
        out[i] = n
    return Value.bin(bytes(out))


define('LTB', 1, 1, fn=_ltb)
