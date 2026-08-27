"""Exact decimal arithmetic. See spec/SPEC.md §4.

A decimal is (neg, digits, scale), meaning (neg ? -1 : 1) * digits / 10^scale.
`digits` is the unscaled magnitude, always non-negative. Zero is never negative.
Scale is part of the value: 2.50 is digits 250 at scale 2, and stays "2.50"
through addition.

**This deliberately does not use the `decimal` module.** Not for speed, and not
for pride: `tools/decimal-oracle.py` generates this project's decimal test cases
*from* Python's `decimal`, as an independent third opinion on four cores that
were all written from one spec by one hand. If this host's core were `decimal`
too, `tools/check-decimal.sh` would be comparing that module against itself and
would verify precisely nothing for Python while still printing a reassuring
"0 mismatches".

Where the other three hosts carry digit strings — because PHP has no bigint and
JS has doubles — this carries Python `int`, which is arbitrary precision. Every
digit-string routine there (addAbs, subAbs, mulAbs, divModAbs, scaleUp, strip)
is exactly an integer operation, so the mapping is one-to-one and the leading-
zero bookkeeping simply disappears.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .errors import Pos, fail

DIV_SCALE = 10


@dataclass(frozen=True, slots=True)
class Dec:
    neg: bool
    digits: int
    scale: int


def make(neg: bool, digits: int, scale: int) -> Dec:
    return Dec(False if digits == 0 else neg, digits, scale)


ZERO = make(False, 0, 0)

_NUM_RE = re.compile(r'^-?[0-9]+(\.[0-9]+)?$')
# NOTE ON .fullmatch(): Python's `$` matches at the end of the string *and*
# immediately before a single trailing newline, exactly like PCRE's and unlike
# ECMAScript's — the same trap the regex built-ins lower `$` to `\Z` for, one
# layer down and in this host's own source. With `.match()`, "5\n" parsed as a
# number here and as E_NOT_NUM everywhere else. `.fullmatch()` has no such
# corner; `$` is left in the pattern only because it costs nothing and reads.


def parse(text: str) -> Dec | None:
    """None when the text is not a number; callers raise E_NOT_NUM with the
    position of the offending node. No trimming — " 2" is not a number.

    The regex is anchored and ASCII by construction. `int()` is never called on
    unvalidated text: it accepts Unicode digits ("٣" is 3), surrounding
    whitespace and underscore separators, any of which would let a value in that
    §4 does not define as a number.
    """
    if not isinstance(text, str) or not _NUM_RE.fullmatch(text):
        return None
    neg = text.startswith('-')
    body = text[1:] if neg else text
    dot = body.find('.')
    int_part = body if dot < 0 else body[:dot]
    frac_part = '' if dot < 0 else body[dot + 1:]
    return make(neg, int(int_part + frac_part), len(frac_part))


def format(d: Dec) -> str:  # noqa: A001 - mirrors format() in the other hosts
    sign = '-' if d.neg else ''
    if d.scale == 0:
        return sign + str(d.digits)
    body = str(d.digits).rjust(d.scale + 1, '0')
    return sign + body[:len(body) - d.scale] + '.' + body[len(body) - d.scale:]


def from_int(n: int) -> Dec:
    return make(n < 0, abs(n), 0)


def is_zero(d: Dec) -> bool:
    return d.digits == 0


def negate(d: Dec) -> Dec:
    return make(not d.neg, d.digits, d.scale)


def abs_(d: Dec) -> Dec:
    return make(False, d.digits, d.scale)


def sign(d: Dec) -> int:
    return 0 if is_zero(d) else (-1 if d.neg else 1)


def is_integer(d: Dec) -> bool:
    """True when the value has no fractional part left after its scale."""
    if d.scale == 0:
        return True
    return d.digits % (10 ** d.scale) == 0


def to_safe_int(d: Dec) -> int:
    t = trunc(d)
    return -t.digits if t.neg else t.digits


# --- arithmetic -------------------------------------------------------------

def _aligned(a: Dec, b: Dec) -> tuple[int, int, int]:
    s = max(a.scale, b.scale)
    return a.digits * 10 ** (s - a.scale), b.digits * 10 ** (s - b.scale), s


def add(a: Dec, b: Dec) -> Dec:
    A, B, s = _aligned(a, b)
    if a.neg == b.neg:
        return make(a.neg, A + B, s)
    if A == B:
        return make(False, 0, s)
    return make(a.neg, A - B, s) if A > B else make(b.neg, B - A, s)


def sub(a: Dec, b: Dec) -> Dec:
    return add(a, negate(b))


def mul(a: Dec, b: Dec) -> Dec:
    return make(a.neg != b.neg, a.digits * b.digits, a.scale + b.scale)


def cmp(a: Dec, b: Dec) -> int:
    if is_zero(a) and is_zero(b):
        return 0
    if a.neg != b.neg:
        return -1 if a.neg else 1
    A, B, _ = _aligned(a, b)
    c = 0 if A == B else (-1 if A < B else 1)
    return -c if a.neg else c


def div(a: Dec, b: Dec, pos: Pos | None = None) -> Dec:
    """Exact when the quotient terminates within DIV_SCALE fractional digits
    (and then reported at its minimal scale); otherwise rounded half away from
    zero to exactly DIV_SCALE digits. So 4/2 is "2" and 1/3 is "0.3333333333".
    """
    if is_zero(b):
        fail('E_DIV_ZERO', 'division by zero', pos)
    N = a.digits * 10 ** b.scale
    D = b.digits * 10 ** a.scale
    q, r = divmod(N * 10 ** DIV_SCALE, D)
    neg = a.neg != b.neg

    if r == 0:
        digits, scale = q, DIV_SCALE
        while scale > 0 and digits % 10 == 0 and digits != 0:
            digits //= 10
            scale -= 1
        if digits == 0:
            scale = 0
        return make(neg, digits, scale)
    return make(neg, q + 1 if 2 * r >= D else q, DIV_SCALE)


def mod(a: Dec, b: Dec, pos: Pos | None = None) -> Dec:
    """Remainder of truncated division: takes the sign of the dividend."""
    if is_zero(b):
        fail('E_DIV_ZERO', 'modulo by zero', pos)
    A, B, s = _aligned(a, b)
    return make(a.neg, A % B, s)


# --- rounding ---------------------------------------------------------------
#
# All of it half away from zero, on the magnitude, with the sign reattached by
# make(). Python's own round() is half-to-even and must never appear here; nor
# may math.floor/ceil, which take floats.

def round(d: Dec, n: int) -> Dec:  # noqa: A001 - mirrors round() in the other hosts
    if n >= d.scale:
        return make(d.neg, d.digits * 10 ** (n - d.scale), n)
    p = 10 ** (d.scale - n)
    q, r = divmod(d.digits, p)
    return make(d.neg, q + 1 if 2 * r >= p else q, n)


def trunc(d: Dec) -> Dec:
    if d.scale == 0:
        return d
    return make(d.neg, d.digits // (10 ** d.scale), 0)


def floor(d: Dec) -> Dec:
    if d.scale == 0:
        return d
    q, r = divmod(d.digits, 10 ** d.scale)
    return make(d.neg, q + 1 if d.neg and r != 0 else q, 0)


def ceil(d: Dec) -> Dec:
    if d.scale == 0:
        return d
    q, r = divmod(d.digits, 10 ** d.scale)
    return make(d.neg, q + 1 if not d.neg and r != 0 else q, 0)


def power(a: Dec, n: int) -> Dec:
    """n must be a non-negative integer; the result scale is scale(x) * n, which
    falls out of repeated multiplication.
    """
    result = make(False, 1, 0)
    base = a
    e = n
    while e > 0:
        if e % 2 == 1:
            result = mul(result, base)
        e //= 2
        if e > 0:
            base = mul(base, base)
    return result
