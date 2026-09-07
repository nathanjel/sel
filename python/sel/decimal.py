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

Where the other four hosts carry digit strings — because PHP has no bigint and
JS has doubles — this carries Python `int`, which is arbitrary precision. Every
digit-string routine there (addAbs, subAbs, mulAbs, divModAbs, scaleUp, strip)
is exactly an integer operation, so the mapping is one-to-one and the leading-
zero bookkeeping simply disappears.

The arithmetic is unbounded; the *conversions* are not. CPython refuses int/str
above 4300 digits by default, and since a SEL number is text, that limit reached
straight into the language: a 4301-digit literal the other four hosts evaluated
could not be compiled here at all. It is raised below to what §6.4 admits, and
the digit count the cap needs is read from bit_length rather than str(), because
str() is the conversion being guarded.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sys

from .errors import Pos, fail

DIV_SCALE = 10

# spec/SPEC.md §6.4. These bound the *value*; ROUND's scale cap and POWER's
# exponent cap bound *arguments*, and an argument cap is not a value cap —
# POWER's base is unbounded, so nesting one POWER inside another multiplies the
# exponents and steps straight over the exponent cap. Two independent numbers
# rather than one shared budget, because ROUND(99.5, 1000000) is 1 000 002
# digits and legal under the scale cap: a shared budget would have shrunk what
# the spec already sanctions.
MAX_INT_DIGITS = 1000000
MAX_FRAC_DIGITS = 1000000

# The bit length at or above which a magnitude *may* have more than
# MAX_INT_DIGITS digits: floor(MAX_INT_DIGITS * log2(10)) + 1. Below it, it
# certainly does not. Used as an O(1) gate so the exact count is computed only
# for numbers that are actually near the cap.
_MAX_INT_BITS = 3321929

# CPython refuses int<->str conversion above 4300 digits by default — a guard
# against quadratic conversion, and a host policy, not SEL's answer. Under it a
# number this language admits could not be lexed or rendered at all: the Python
# host raised a ValueError out of the lexer for a 4301-digit literal that the
# other four hosts evaluated. Raised to exactly what SEL admits and no further.
#
# This mutates a process-global interpreter setting from inside a library, which
# is worth stating plainly. It is done in the one direction that cannot weaken a
# choice the application made: 0 means "unlimited" and is left alone, and a
# limit already above what SEL needs is left alone too.
_NEEDED_STR_DIGITS = MAX_INT_DIGITS + MAX_FRAC_DIGITS
if hasattr(sys, 'set_int_max_str_digits'):  # 3.11+
    _current = sys.get_int_max_str_digits()
    if _current != 0 and _current < _NEEDED_STR_DIGITS:
        sys.set_int_max_str_digits(_NEEDED_STR_DIGITS)


@dataclass(frozen=True, slots=True)
class Dec:
    neg: bool
    digits: int
    scale: int


def make(neg: bool, digits: int, scale: int) -> Dec:
    return Dec(False if digits == 0 else neg, digits, scale)


_POW10: dict[int, int] = {}


def _pow10(k: int) -> int:
    v = _POW10.get(k)
    if v is None:
        v = 10 ** k
        _POW10[k] = v
    return v


def _num_digits(n: int) -> int:
    """Digit count of a non-negative int, without str().

    str() is exactly what CPython refuses above its own limit, so a check that
    used it could not report on the values it exists to refuse. bit_length gives
    an upper bound (30103/100000 is just above log10(2)); one comparison walks
    it down to the exact count.
    """
    if n == 0:
        return 1
    d = (n.bit_length() * 30103) // 100000 + 1
    while n < _pow10(d - 1):
        d -= 1
    return d


def _guard(d: Dec, pos: Pos | None) -> Dec:
    """Refuses a value SEL cannot hold, where it is built rather than where it
    is rendered. Every operation that can grow a number passes its result
    through here, so power() — repeated squaring over mul() — trips on an
    intermediate and the enormous value is never allocated.

    The four hosts that store digits as a string read the count with strlen.
    Here it is an int, so the same question costs a bit_length gate and, only
    for numbers near the cap, an exact count.
    """
    if d.scale > MAX_FRAC_DIGITS:
        fail('E_RANGE', f'number has more than {MAX_FRAC_DIGITS} fractional digits', pos)
    # Negative when the value is below 1: those render as a single "0".
    if (d.digits.bit_length() >= _MAX_INT_BITS
            and _num_digits(d.digits) - d.scale > MAX_INT_DIGITS):
        fail('E_RANGE', f'number has more than {MAX_INT_DIGITS} integer digits', pos)
    return d


ZERO = make(False, 0, 0)

_NUM_RE = re.compile(r'^-?[0-9]+(\.[0-9]+)?$')
# NOTE ON .fullmatch(): Python's `$` matches at the end of the string *and*
# immediately before a single trailing newline, exactly like PCRE's and unlike
# ECMAScript's — the same trap the regex built-ins lower `$` to `\Z` for, one
# layer down and in this host's own source. With `.match()`, "5\n" parsed as a
# number here and as E_NOT_NUM everywhere else. `.fullmatch()` has no such
# corner; `$` is left in the pattern only because it costs nothing and reads.


def parse(text: str, pos: Pos | None = None) -> Dec | None:
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
    # Measured before int(), because int() is the conversion CPython refuses on
    # a string this long. Leading zeros are stripped first: the string hosts
    # store strip(int_part + frac_part), so "0001" is one digit there and must
    # be one digit here.
    stripped = (int_part + frac_part).lstrip('0') or '0'
    if len(frac_part) > MAX_FRAC_DIGITS:
        fail('E_RANGE', f'number has more than {MAX_FRAC_DIGITS} fractional digits', pos)
    if len(stripped) - len(frac_part) > MAX_INT_DIGITS:
        fail('E_RANGE', f'number has more than {MAX_INT_DIGITS} integer digits', pos)
    return make(neg, int(stripped), len(frac_part))


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


def add(a: Dec, b: Dec, pos: Pos | None = None) -> Dec:
    A, B, s = _aligned(a, b)
    # Only true addition can grow: a difference is never wider than its
    # operands, and the aligned scale is the larger of two already legal ones.
    if a.neg == b.neg:
        return _guard(make(a.neg, A + B, s), pos)
    if A == B:
        return make(False, 0, s)
    return make(a.neg, A - B, s) if A > B else make(b.neg, B - A, s)


def sub(a: Dec, b: Dec, pos: Pos | None = None) -> Dec:
    return add(a, negate(b), pos)


def mul(a: Dec, b: Dec, pos: Pos | None = None) -> Dec:
    return _guard(make(a.neg != b.neg, a.digits * b.digits, a.scale + b.scale), pos)


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
        return _guard(make(neg, digits, scale), pos)
    return _guard(make(neg, q + 1 if 2 * r >= D else q, DIV_SCALE), pos)


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

def round(d: Dec, n: int, pos: Pos | None = None) -> Dec:  # noqa: A001 - mirrors round() in the other hosts
    if n >= d.scale:
        return _guard(make(d.neg, d.digits * 10 ** (n - d.scale), n), pos)
    p = 10 ** (d.scale - n)
    q, r = divmod(d.digits, p)
    # Rounding down still carries: 9.99 to one place is 10.0, a digit wider.
    return _guard(make(d.neg, q + 1 if 2 * r >= p else q, n), pos)


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


def power(a: Dec, n: int, pos: Pos | None = None) -> Dec:
    """n must be a non-negative integer; the result scale is scale(x) * n, which
    falls out of repeated multiplication.
    """
    result = make(False, 1, 0)
    base = a
    e = n
    while e > 0:
        if e % 2 == 1:
            result = mul(result, base, pos)
        e //= 2
        if e > 0:
            base = mul(base, base, pos)
    return result
