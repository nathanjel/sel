"""Text built-ins. Everything counts code points — never bytes, never UTF-16
units — so positions and lengths agree with the other hosts on astral
characters. Positions are 1-based and 0 means "not found" (§7.5).

Python `str` indexes by code point already, which removes the whole class of bug
the other four hosts fight here. The traps that remain are the ones where
Python's convenience methods mean something *wider* than SEL does, and each is
avoided explicitly below: str.upper/lower are full Unicode, str.strip takes the
Unicode whitespace set, and str.find would be right but is spelled out for the
same reason the byte comparison is.
"""

from ..errors import fail
from ..registry import define
from ..value import Value

# SEL whitespace is exactly these four (§2.2). NOT str.strip(), which also takes
# \v, \f, U+00A0, U+2028 and the rest of the Unicode whitespace property.
_SPACE = ' \t\r\n'


def _index_of(hay: str, needle: str, frm: int) -> int:
    if needle == '':
        return -1
    return hay.find(needle, frm)


define('LEN', 1, 1, fn=lambda a, ctx: Value.int(len(a.text(0))))

define('LEFT', 2, 2, fn=lambda a, ctx: Value.text(a.text(0)[:a.non_neg_int(1)]))


def _right(a, ctx):
    s = a.text(0)
    n = a.non_neg_int(1)
    return Value.text(s[max(0, len(s) - n):])


define('RIGHT', 2, 2, fn=_right)


def _substr(a, ctx):
    s = a.text(0)
    start = a.int(1)
    if start < 1:
        fail('E_RANGE', 'SUBSTR start is 1-based and must be at least 1', a.pos_of(1))
    frm = start - 1
    if a.count() == 2:
        return Value.text(s[frm:])
    return Value.text(s[frm:frm + a.non_neg_int(2)])


define('SUBSTR', 2, 3, fn=_substr)


def _find(a, ctx):
    needle = a.text(0)
    hay = a.text(1)
    frm = 0
    if a.count() == 3:
        f = a.int(2)
        if f < 1:
            fail('E_RANGE', 'FIND start is 1-based and must be at least 1', a.pos_of(2))
        frm = f - 1
    if needle == '':
        fail('E_BAD_ARG', 'FIND needle must not be empty', a.pos_of(0))
    return Value.int(_index_of(hay, needle, frm) + 1)


define('FIND', 2, 3, fn=_find)


def _replace(a, ctx):
    needle = a.text(0)
    repl = a.text(1)
    hay = a.text(2)
    if needle == '':
        fail('E_BAD_ARG', 'REPLACE needle must not be empty', a.pos_of(0))
    return Value.text(hay.replace(needle, repl))


define('REPLACE', 3, 3, fn=_replace)


def _split(a, ctx):
    hay = a.text(0)
    sep = a.text(1)
    if sep == '':
        fail('E_BAD_ARG', 'SPLIT separator must not be empty', a.pos_of(1))
    return Value.list([Value.text(p) for p in hay.split(sep)])


define('SPLIT', 2, 2, fn=_split)


def _trim(s: str, left: bool, right: bool) -> str:
    a, b = 0, len(s)
    if left:
        while a < b and s[a] in _SPACE:
            a += 1
    if right:
        while b > a and s[b - 1] in _SPACE:
            b -= 1
    return s[a:b]


define('TRIM', 1, 1, fn=lambda a, ctx: Value.text(_trim(a.text(0), True, True)))
define('LTRIM', 1, 1, fn=lambda a, ctx: Value.text(_trim(a.text(0), True, False)))
define('RTRIM', 1, 1, fn=lambda a, ctx: Value.text(_trim(a.text(0), False, True)))


# ASCII only, deliberately. Python's str.upper() applies full Unicode mapping and
# can change length — "ß".upper() is "SS", "ﬁ".upper() is "FI" — while PHP's
# strtoupper is byte- and locale-based. They cannot be reconciled without
# shipping a case table, and guessing would break the invariant silently.
def _ascii_case(s: str, up: bool) -> str:
    out = []
    for ch in s:
        c = ord(ch)
        if up and 0x61 <= c <= 0x7A:
            out.append(chr(c - 32))
        elif not up and 0x41 <= c <= 0x5A:
            out.append(chr(c + 32))
        else:
            out.append(ch)
    return ''.join(out)


define('UPPER', 1, 1, fn=lambda a, ctx: Value.text(_ascii_case(a.text(0), True)))
define('LOWER', 1, 1, fn=lambda a, ctx: Value.text(_ascii_case(a.text(0), False)))

define('BACKWARDS', 1, 1, fn=lambda a, ctx: Value.text(a.text(0)[::-1]))

define('REPEAT', 2, 2, fn=lambda a, ctx: Value.text(a.text(0) * a.non_neg_int(1)))


def _pad(a, left: bool):
    s = a.text(0)
    width = a.non_neg_int(1)
    fill = a.text(2)
    if fill == '':
        fail('E_BAD_ARG', 'pad fill must not be empty', a.pos_of(2))
    if len(s) >= width:
        return Value.text(s)
    need = width - len(s)
    padding = ''.join(fill[i % len(fill)] for i in range(need))
    return Value.text(padding + s if left else s + padding)


define('PADL', 3, 3, fn=lambda a, ctx: _pad(a, True))
define('PADR', 3, 3, fn=lambda a, ctx: _pad(a, False))


def _char(a, ctx):
    n = a.int(0)
    if n < 0 or n > 0x10FFFF or 0xD800 <= n <= 0xDFFF:
        fail('E_RANGE', f'{n} is not an encodable code point', a.pos_of(0))
    return Value.text(chr(n))


define('CHAR', 1, 1, fn=_char)


def _code(a, ctx):
    s = a.text(0)
    if s == '':
        fail('E_RANGE', 'CODE of empty text', a.pos_of(0))
    return Value.int(ord(s[0]))


define('CODE', 1, 1, fn=_code)
