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

Mantissas use Python's arbitrary-precision `int` directly. Separate small-int
and bigint representations would both use that same Python type; scale and sign
are retained explicitly, without a second cached signed mantissa.

The arithmetic is unbounded; the *conversions* are not. CPython refuses int/str
above 4300 digits by default, and since a SEL number is text, that limit reached
straight into the language: a 4301-digit literal the other four hosts evaluated
could not be compiled here at all. It is raised below to what §6.4 admits, and
the digit count the cap needs is read from bit_length rather than str(), because
str() is the conversion being guarded.
"""

from __future__ import annotations

import re

from .errors import Pos, fail

from . import _limits as _limits

DIV_SCALE = _limits.DIV_SCALE   # spec/limits.json

# spec/SPEC.md §6.4. These bound the *value*; ROUND's scale cap and POWER's
# exponent cap bound *arguments*, and an argument cap is not a value cap —
# POWER's base is unbounded, so nesting one POWER inside another multiplies the
# exponents and steps straight over the exponent cap. Two independent numbers
# rather than one shared budget, because ROUND(99.5, 1000000) is 1 000 002
# digits and legal under the scale cap: a shared budget would have shrunk what
# the spec already sanctions.
MAX_INT_DIGITS = _limits.MAX_INT_DIGITS
MAX_FRAC_DIGITS = _limits.MAX_FRAC_DIGITS

# The bit length at or above which a magnitude *may* have more than
# MAX_INT_DIGITS digits: floor(MAX_INT_DIGITS * log2(10)) + 1. Below it, it
# certainly does not. Used as an O(1) gate so the exact count is computed only
# for numbers that are actually near the cap.
_MAX_INT_BITS = 3321929
# Below this many operand bits a product cannot be near the cap; skip the estimate.
_MUL_PRECHECK_BITS = 1 << 20

# CPython refuses int<->str conversion above 4300 digits by default (and above
# whatever the deployer set with -X int_max_str_digits or sys.set_int_max_str_digits)
# -- a guard against quadratic conversion, and a host policy, not SEL's answer.
# Under it a number this language admits could not be lexed or rendered at all.
#
# This module used to raise that process-global limit at import, which overrode
# a choice the application had made, and a limit set afterwards leaked a raw
# ValueError out of the first long number. Neither is acceptable in a library:
# the two conversions below are exact for any size and never depend on the
# setting. The fast path is the interpreter's own conversion; only when it
# refuses does the recursive one run, splitting until each piece is under the
# smallest limit CPython allows (640 digits).
_SAFE_DIGITS = 500


def _int_from_digits(text: str) -> int:
    """int(text) for an ASCII digit string of any length."""
    if len(text) <= _SAFE_DIGITS:
        return int(text)
    try:
        return int(text)
    except ValueError:      # over the interpreter's digit limit
        pass
    return _int_split(text)


def _int_split(text: str) -> int:
    n = len(text)
    if n <= _SAFE_DIGITS:
        return int(text)
    low = n // 2
    return _int_split(text[:n - low]) * (10 ** low) + _int_split(text[n - low:])


def _digits_of(value: int) -> str:
    """str(value) for a non-negative int of any size."""
    try:
        return str(value)
    except ValueError:      # over the interpreter's digit limit
        pass
    return _str_split(value, _num_digits(value))


def _str_split(value: int, width: int) -> str:
    """Exactly `width` digits, zero-padded on the left."""
    if width <= _SAFE_DIGITS:
        return str(value).rjust(width, '0')
    low = width // 2
    high, rest = divmod(value, 10 ** low)
    return _str_split(high, width - low) + _str_split(rest, low)


class Dec:
    """A decimal: sign, unscaled digits, scale. Immutable BY CONVENTION -- nothing
    in the library assigns to one after construction. It was a frozen slots
    dataclass, and constructing one cost 3-4x this (frozen's __init__ goes through
    object.__setattr__ per field); a Dec is built for every literal and every
    arithmetic result. Equality and hashing are the representation's, as the
    dataclass gave them: 150/2 != 15/1 (EQL is structural; compare by value with
    cmp), so they are not normalised here either."""

    __slots__ = ('neg', 'digits', 'scale')

    def __init__(self, neg: bool, digits: int, scale: int) -> None:
        self.neg = neg
        self.digits = digits
        self.scale = scale

    def __eq__(self, other: object) -> bool:
        if other.__class__ is not Dec:
            return NotImplemented
        return (self.neg == other.neg and self.digits == other.digits
                and self.scale == other.scale)

    def __hash__(self) -> int:
        return hash((self.neg, self.digits, self.scale))

    def __repr__(self) -> str:
        return f'Dec(neg={self.neg!r}, digits={self.digits!r}, scale={self.scale!r})'


# Small powers are shared; mantissas already use native arbitrary-size ints.
_POW10_LIMIT = 18


def make(neg: bool, digits: int, scale: int) -> Dec:
    return Dec(bool(neg and digits), digits, scale)


_POW10_SMALL = tuple(10 ** i for i in range(_POW10_LIMIT + 1))
_POW10: dict[int, int] = {}
# Keep common scales hot without retaining arbitrarily large host integers.
_POW10_CACHE_ENTRIES = 64
_POW10_CACHE_MAX_EXPONENT = 1000000
_POW10_CACHE_DIGITS = 1048576
_POW10_WEIGHT = 0


def _pow10(k: int) -> int:
    global _POW10_WEIGHT
    if 0 <= k <= _POW10_LIMIT:
        return _POW10_SMALL[k]
    v = _POW10.get(k)
    if v is None:
        v = 10 ** k
        if 0 <= k <= _POW10_CACHE_MAX_EXPONENT:
            if len(_POW10) >= _POW10_CACHE_ENTRIES or _POW10_WEIGHT + k > _POW10_CACHE_DIGITS:
                _POW10.clear()
                _POW10_WEIGHT = 0
            _POW10[k] = v
            _POW10_WEIGHT += k
    return v


# 10**DIV_SCALE, computed once: div multiplies every dividend by it. Through
# _pow10 rather than an index into the small table, which would silently assume
# the generated DIV_SCALE stays within _POW10_LIMIT.
_DIV_FACTOR = _pow10(DIV_SCALE)


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


def guard(d: Dec, pos: Pos | None) -> Dec:
    """Refuses a value SEL cannot hold, where it is built rather than where it
    is rendered. Every operation that can grow a number passes its result
    through here, so power() — repeated squaring over mul() — trips on an
    intermediate and the enormous value is never allocated.

    With an integer magnitude, this costs a bit_length gate and, only
    for numbers near the cap, an exact count.
    """
    if d.scale > MAX_FRAC_DIGITS:
        fail('E_RANGE', f'number has more than {MAX_FRAC_DIGITS} fractional digits', pos)
    # Negative when the value is below 1: those render as a single "0".
    if (d.digits.bit_length() >= _MAX_INT_BITS
            and _num_digits(d.digits) - d.scale > MAX_INT_DIGITS):
        fail('E_RANGE', f'number has more than {MAX_INT_DIGITS} integer digits', pos)
    return d


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
    return make(neg, _int_from_digits(stripped), len(frac_part))


def format(d: Dec) -> str:  # noqa: A001 - mirrors format() in the other hosts
    sign = '-' if d.neg else ''
    if d.scale == 0:
        return sign + _digits_of(d.digits)
    body = _digits_of(d.digits).rjust(d.scale + 1, '0')
    return sign + body[:len(body) - d.scale] + '.' + body[len(body) - d.scale:]


def trim_scale(d: Dec) -> Dec:
    """The value with the fraction's trailing zeros removed (§7.6 CANON):
    1.50 is 1.5, 2.000 is 2, 100 stays 100, and zero is 0 with no scale and no
    sign. Counted on the digit string, not by repeated division, so a number
    with a million-digit scale costs one pass."""
    if d.digits == 0:
        return make(False, 0, 0)
    if d.scale == 0:
        return d
    text = _digits_of(d.digits)
    zeros = min(d.scale, len(text) - len(text.rstrip('0')))
    if zeros == 0:
        return d
    return make(d.neg, _int_from_digits(text[:len(text) - zeros]), d.scale - zeros)


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
    if d.scale == 0 or d.digits == 0:
        return True
    if d.scale > _GAP:
        # 10**scale divides digits only if 2**scale does, i.e. the digits have at
        # least `scale` trailing zero bits. Decides most values without building
        # a power of ten as large as the scale.
        if ((d.digits & -d.digits).bit_length() - 1) < d.scale:
            return False
    return divmod(d.digits, _pow10(d.scale))[1] == 0      # divmod, not %: see mod()


def to_safe_int(d: Dec) -> int:
    t = trunc(d)
    return -t.digits if t.neg else t.digits


# --- arithmetic -------------------------------------------------------------

def _aligned(a: Dec, b: Dec) -> tuple[int, int, int]:
    if a.scale == b.scale:
        return a.digits, b.digits, a.scale
    if a.scale > b.scale:
        return a.digits, b.digits * _pow10(a.scale - b.scale), a.scale
    return a.digits * _pow10(b.scale - a.scale), b.digits, b.scale


def add(a: Dec, b: Dec, pos: Pos | None = None) -> Dec:
    A, B, s = _aligned(a, b)
    # Only true addition can grow: a difference is never wider than its
    # operands, and the aligned scale is the larger of two already legal ones.
    if a.neg == b.neg:
        return guard(make(a.neg, A + B, s), pos)
    if A == B:
        return make(False, 0, s)
    return make(a.neg, A - B, s) if A > B else make(b.neg, B - A, s)


def sub(a: Dec, b: Dec, pos: Pos | None = None) -> Dec:
    A, B, s = _aligned(a, b)
    # Subtraction needs no temporary Dec merely to reverse b's sign.
    if a.neg != b.neg:
        return guard(make(a.neg, A + B, s), pos)
    if A == B:
        return make(False, 0, s)
    return make(a.neg, A - B, s) if A > B else make(not a.neg, B - A, s)


def mul(a: Dec, b: Dec, pos: Pos | None = None) -> Dec:
    scale = a.scale + b.scale
    # Refuse before multiplying when the operand sizes already settle it:
    # a product of an m-bit and an n-bit integer has at least m+n-1 bits, so its
    # digit count is bounded below by the same estimate _cmp_by_magnitude uses, and
    # multiplying two million-digit numbers to learn they are too wide took seconds.
    # The same two refusals guard() makes, in the same order; everything not
    # clearly over the cap is multiplied and guarded exactly as before.
    bits = a.digits.bit_length() + b.digits.bit_length()
    if bits >= _MUL_PRECHECK_BITS and a.digits and b.digits:
        if scale > MAX_FRAC_DIGITS:
            fail('E_RANGE', f'number has more than {MAX_FRAC_DIGITS} fractional digits', pos)
        pb = bits - 1
        if ((pb - 1) * 30102) // 100000 + 1 - scale > MAX_INT_DIGITS:
            fail('E_RANGE', f'number has more than {MAX_INT_DIGITS} integer digits', pos)
    return guard(make(a.neg != b.neg, a.digits * b.digits, scale), pos)


# A scale gap this wide is where aligning (multiplying by 10**gap) starts to cost
# real time; below it the exact path is at least as cheap as the estimate.
_GAP = 64


def _cmp_by_magnitude(A: int, sa: int, B: int, sb: int) -> int:
    """Sign of |A*10**-sa| - |B*10**-sb| when bit lengths already decide it, else 0.

    With bl = bit_length, A has between floor((bl-1)*0.30102)+1 and
    floor(bl*0.30103)+1 digits (0.30102 < log10 2 < 0.30103), and a value with
    nd digits at scale s lies in [10**(nd-1-s), 10**(nd-s)). One side's lower bound
    at or above the other's upper bound is a strict inequality.
    """
    bla, blb = A.bit_length(), B.bit_length()
    lo_a = ((bla - 1) * 30102) // 100000 + 1
    hi_a = (bla * 30103) // 100000 + 1
    lo_b = ((blb - 1) * 30102) // 100000 + 1
    hi_b = (blb * 30103) // 100000 + 1
    if lo_a - 1 - sa >= hi_b - sb:
        return 1
    if lo_b - 1 - sb >= hi_a - sa:
        return -1
    return 0


def cmp(a: Dec, b: Dec) -> int:
    if a.neg != b.neg:
        return -1 if a.neg else 1
    if a.scale == b.scale:
        A, B = a.digits, b.digits
    else:
        A, B = a.digits, b.digits
        if A == 0 or B == 0:
            c = (A > 0) - (B > 0)        # a zero is below any positive, equal to zero
            return -c if a.neg else c
        gap = a.scale - b.scale
        if gap > _GAP or gap < -_GAP:
            # Far apart in scale: the magnitudes usually differ by far more than the
            # imprecision of a bit-length estimate, which decides without building
            # 10**gap. Inconclusive (within about one power) falls through
            # to the exact alignment below.
            c = _cmp_by_magnitude(A, a.scale, B, b.scale)
            if c:
                return -c if a.neg else c
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
    # Cancel the shared scale before constructing large powers/products.
    # The remaining ratio is exact; rounding below is unchanged.
    N, D = a.digits, b.digits
    if b.scale > a.scale:
        N *= _pow10(b.scale - a.scale)
    elif a.scale > b.scale:
        D *= _pow10(a.scale - b.scale)
    q, r = divmod(N * _DIV_FACTOR, D)
    neg = a.neg != b.neg

    if r == 0:
        digits, scale = q, DIV_SCALE
        while scale > 0 and digits % 10 == 0 and digits != 0:
            digits //= 10
            scale -= 1
        if digits == 0:
            scale = 0
        return guard(make(neg, digits, scale), pos)
    return guard(make(neg, q + 1 if 2 * r >= D else q, DIV_SCALE), pos)


def mod(a: Dec, b: Dec, pos: Pos | None = None) -> Dec:
    """Remainder of truncated division: takes the sign of the dividend."""
    if is_zero(b):
        fail('E_DIV_ZERO', 'modulo by zero', pos)
    A, B, s = _aligned(a, b)
    # divmod, not %: since CPython 3.12 `divmod` and `//` take a sub-quadratic
    # path for huge ints and `%` does not -- about 9x slower on a million digits.
    # The remainder is the same.
    return make(a.neg, divmod(A, B)[1], s)


# --- rounding ---------------------------------------------------------------
#
# All of it half away from zero, on the magnitude, with the sign reattached by
# make(). Python's own round() is half-to-even and must never appear here; nor
# may math.floor/ceil, which take floats.

def round(d: Dec, n: int, pos: Pos | None = None) -> Dec:  # noqa: A001 - mirrors round() in the other hosts
    if n >= d.scale:
        return guard(make(d.neg, d.digits * _pow10(n - d.scale), n), pos)
    p = _pow10(d.scale - n)
    q, r = divmod(d.digits, p)
    # Rounding down still carries: 9.99 to one place is 10.0, a digit wider.
    return guard(make(d.neg, q + 1 if 2 * r >= p else q, n), pos)


def trunc(d: Dec) -> Dec:
    if d.scale == 0:
        return d
    return make(d.neg, d.digits // _pow10(d.scale), 0)


def floor(d: Dec, pos: Pos | None = None) -> Dec:
    if d.scale == 0:
        return d
    q, r = divmod(d.digits, _pow10(d.scale))
    # Rounding away from zero carries: 99.5 floored for a negative is -100, a
    # digit wider, and at the integer-digit cap that is one too many.
    return guard(make(d.neg, q + 1 if d.neg and r != 0 else q, 0), pos)


def ceil(d: Dec, pos: Pos | None = None) -> Dec:
    if d.scale == 0:
        return d
    q, r = divmod(d.digits, _pow10(d.scale))
    return guard(make(d.neg, q + 1 if not d.neg and r != 0 else q, 0), pos)


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
