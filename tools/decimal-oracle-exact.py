#!/usr/bin/env python3
"""An exact-rational oracle for SEL's decimal arithmetic, independent of the
cores AND of tools/decimal-oracle.py.

The older oracle (Python's `decimal` module) is a third opinion on narrow
operands, but its `sel_div`/`sel_mod` re-implement the cores' own idiom
(`divmod(n * 10**10, den)`, `2 * r >= den`) and its operands stop at 12 integer
digits and scale 6. This one is written from spec/SPEC.md §4 alone, in
`fractions.Fraction`:

  * every value is (exact rational, scale); the scale is part of the value (§4.1)
  * `+ - %`  scale max(sa, sb); `*` scale sa + sb; POWER scale sa * n (§4.2, §7.6)
  * `/`  the exact quotient at its MINIMAL scale when it terminates within
         DIV_SCALE = 10 digits, otherwise rounded half away from zero at
         exactly 10 (§4.3)
  * `%`  the remainder of truncated division, sign of the dividend (§4.3)
  * ROUND half away from zero, scale exactly n; CEIL/FLOOR/TRUNC scale 0 (§4.4, §7.6)
  * a zero never carries a minus sign (§4.1)

Rounding is done by comparing the exact quotient against exact half-way points
(`floor(|q| * 10**k + 1/2)`), never by inspecting a remainder, so no operand is
too wide for it: it reaches every magnitude the cores can hold.

    python3 tools/decimal-oracle-exact.py [count] [seed]     # records on stdout
    python3 tools/decimal-oracle-exact.py --self-check [count] [seed]
        # verifies tools/decimal-oracle.py's own records against this module

Records are `op|a|b|want` like decimal-oracle.py, so every host's existing
check-decimal driver reads them unchanged. `pow` is not a driver op; POWER is
covered through conformance/24-decimal-boundaries.selt.
"""
import math
import random
import subprocess
import sys
from fractions import Fraction

if hasattr(sys, 'set_int_max_str_digits'):   # the long products below print past CPython's 4300 digits
    sys.set_int_max_str_digits(0)

DIV_SCALE = 10


# --- values ------------------------------------------------------------------

def parse(text):
    """'-12.500' -> (Fraction(-25, 2), 3). Digits only; no exponent forms."""
    neg = text.startswith('-')
    body = text[1:] if neg else text
    ip, _, fp = body.partition('.')
    v = Fraction(int(ip + fp), 10 ** len(fp))
    return (-v if neg else v), len(fp)


def fmt(v, scale):
    """Canonical text of value `v` at `scale`. `v * 10**scale` must be whole."""
    scaled = v * 10 ** scale
    assert scaled.denominator == 1, (v, scale)
    n = int(scaled)
    neg = n < 0 and n != 0          # a zero never carries a sign
    digits = str(abs(n)).rjust(scale + 1, '0')
    body = digits if scale == 0 else digits[:-scale] + '.' + digits[-scale:]
    return ('-' if neg else '') + body


def half_up(q):
    """floor(|q| + 1/2) with q's sign put back: half away from zero."""
    r = math.floor(abs(q) + Fraction(1, 2))
    return -r if q < 0 else r


# --- operations, each returning (value, scale) ------------------------------

def add(a, b): return a[0] + b[0], max(a[1], b[1])
def sub(a, b): return a[0] - b[0], max(a[1], b[1])
def mul(a, b): return a[0] * b[0], a[1] + b[1]


def div(a, b):
    q = a[0] / b[0]
    for k in range(DIV_SCALE + 1):
        if (q * 10 ** k).denominator == 1:
            return q, k                      # exact, at its minimal scale
    n = half_up(q * 10 ** DIV_SCALE)
    return Fraction(n, 10 ** DIV_SCALE), DIV_SCALE


def mod(a, b):
    t = math.trunc(a[0] / b[0])
    return a[0] - b[0] * t, max(a[1], b[1])


def rnd(a, n):
    return Fraction(half_up(a[0] * 10 ** n), 10 ** n), n


def power(a, n): return a[0] ** n, a[1] * n
def floor_(a): return Fraction(math.floor(a[0])), 0
def ceil_(a): return Fraction(math.ceil(a[0])), 0
def trunc_(a): return Fraction(math.trunc(a[0])), 0
def cmp_(a, b): return (a[0] > b[0]) - (a[0] < b[0])


def calc(op, a_text, b_text):
    """The oracle's answer for one record, as the text a host must print."""
    a = parse(a_text)
    if op == 'round':
        return fmt(*rnd(a, int(b_text)))
    if op in ('floor', 'ceil', 'trunc'):
        return fmt(*{'floor': floor_, 'ceil': ceil_, 'trunc': trunc_}[op](a))
    if op == 'pow':
        return fmt(*power(a, int(b_text)))
    b = parse(b_text)
    if op == 'cmp':
        return str(cmp_(a, b))
    return fmt(*{'+': add, '-': sub, '*': mul, '/': div, '%': mod}[op](a, b))


# --- operand matrix ----------------------------------------------------------

def spellings(n):
    """One integer in several scales: bare, .0, .5, scale 18, 19 and 38 forms."""
    out = [str(n), f'{n}.0', f'{n}.5']
    for sc in (18, 19, 38):
        s = str(abs(n)).rjust(sc + 1, '0')
        out.append(('-' if n < 0 else '') + s[:-sc] + '.' + s[-sc:])
    return out


BOUNDS = [0, 1, 2, 3, 7, 10, 2**31 - 1, 2**32, 10**18, 10**19, 2**63 - 1, 2**63,
          2**63 + 1, 2**64 - 1, 2**64, 2**64 + 1, 10**37, 2**126 - 1, 2**126,
          2**127 - 1, 2**127, 2**127 + 1, 2**128 - 1, 2**128, 10**38, 10**39, 10**42]

DIVISORS = ['1', '1.0', '1.00', '-1', '0.1', '0.10', '-0.1', '0.000000001',
            '0.0000000000000000000000000000000000001', '3', '7', '-7',
            '99999999999999999999999999999999999999',
            '0.99999999999999999999999999999999999999',
            '18446744073709551616', '9223372036854775808', '0.5', '2', '10', '100',
            # every odd integer operand divided by 2 * 10^10 is an exact half at the 11th digit:
            # ties on the word path too, past every native mantissa (the tie cases elsewhere are short)
            '20000000000', '-20000000000']

TIES = ['0.5', '1.5', '2.5', '-0.5', '-1.5', '-2.5', '0.49999999999999999999999999999999999999',
        '0.50000000000000000000000000000000000000', '0.99999999999999999999999999999999999999',
        '0.90000000000000000000000000000000000000', '99999999999999999999999999999999999999.5',
        '0.000000000000000000005', '0.0000000000000000000049', '1.0000000000000000005',
        '-0.0000000000000000000000000000000000005', '123456789012345678901234567890.123456789012345678']


def operands():
    out = []
    for n in BOUNDS:
        for s in spellings(n):
            out.append(s)
            if n:
                out.append('-' + s)
    out += ['007', '007.50', '0.00', '-0.4', '0.1234567890123456789', '-0.1234567890123456789',
            '1.05', '0.5'] + TIES
    seen, res = set(), []
    for s in out:
        c = fmt(*parse(s))
        if c not in seen:
            seen.add(c)
            res.append(c)
    return res


# Scales past every host's power-of-ten table (JS keeps 10^0..10^64, Python
# 10^0..10^18, C++ 10^38 in a 128-bit mantissa) and scale gaps past 64, where
# Python's comparison decides by bit length before it aligns. Nothing above
# scale 38 was generated until tools/mutate-decimal.sh showed it: a comparison
# shortcut that decided a pair its digit bounds do not separate survived every
# check, because no pair ever had a gap past 64.
WIDE_SCALES = (39, 63, 64, 65, 66, 70, 100, 129, 130)
GAP_PARTNERS = ['0', '1', '-1', '1.0', '2', '3', '7', '9', '10', '0.5']


def wide_scale_operands():
    out = []
    for sc in WIDE_SCALES:
        out += ['1.' + '0' * (sc - 1) + '1',          # just above 1
                '0.' + '9' * sc,                       # just below 1
                '9.' + '9' * sc,                       # just below 10
                '1.' + '0' * sc,                       # 1, spelled at scale sc
                '0.' + '0' * (sc - 1) + '5',           # half a unit in the last place
                '-0.' + '0' * (sc - 1) + '1']          # the smallest negative at sc
    return out


def wide_scale_records():
    recs = []
    wide = wide_scale_operands()
    for a in wide:
        for b in GAP_PARTNERS:
            for op in ('+', '-', '*', '/', '%', 'cmp'):
                recs.append((op, a, b))
                recs.append((op, b, a))
        for n in (0, 1, 18, 38, 63, 64, 65, 66, 129, 130, 131):
            recs.append(('round', a, str(n)))
        for op in ('floor', 'ceil', 'trunc'):
            recs.append((op, a, '0'))
    # wide against wide: every scale gap from 1 to 91, both orders
    for a in wide:
        for b in wide:
            if parse(a)[1] != parse(b)[1]:
                for op in ('cmp', '+', '-'):
                    recs.append((op, a, b))
    for b in GAP_PARTNERS:
        for n in (64, 65, 66, 100, 130):
            recs.append(('round', b, str(n)))
    return recs


# Half-way ties at the tenth quotient digit through a divisor that no native
# word holds once scaled: 2 * 10^10 spelled at scale 9 and up, and 2 * 10^30.
# The tie cases elsewhere all divide by a short integer, which every host
# answers on its native path; PHP's general path rounded a half down unseen.
TIE_DIVIDENDS = ['1', '-1', '3', '-3', '7', '100000000000000000000', '-100000000000000000000']
TIE_DIVISORS = ['20000000000', '20000000000.000000000', '20000000000.0000000000',
                '20000000000.0000000000000000000', '2' + '0' * 30, '-2' + '0' * 30,
                '0.2', '0.20000000000000000000', '2' + '0' * 40 + '.' + '0' * 20]

# Products long enough for every host's Karatsuba (PHP's pure-PHP one at 40
# limbs of 7 digits, Rust's and C++'s portable rows at 32 words, C++'s ADX rows
# at 64 words / 96 for a square, about 1240 / 1850 digits): all nines, which
# carry at every word, and random digits. The random pairs above stop at 45
# integer digits, so no product reached a Karatsuba split until
# tools/mutate-decimal.sh showed one surviving.
LONG_LENGTHS = (300, 700, 1300, 2000, 2700)


def long_records(rng):
    def nines(n):
        return '9' * n

    def rand(n):
        return str(rng.randint(1, 9)) + ''.join(str(rng.randint(0, 9)) for _ in range(n - 1))

    recs = []
    for la in LONG_LENGTHS:
        for lb in LONG_LENGTHS:
            if lb > la:
                continue
            recs.append(('*', nines(la), nines(lb)))
            a, b = rand(la), rand(lb)
            recs.append(('*', a, b))
            recs.append(('*', '-' + a[:-20] + '.' + a[-20:], b))
            recs.append(('+', a, nines(lb)))
            recs.append(('-', a, nines(la)))
        a = rand(la)
        recs.append(('*', a, a))                       # a square
        recs.append(('/', a, rand(la // 2)))
        recs.append(('%', a, rand(la // 3)))
    return recs


def wide_records(count, seed):
    rng = random.Random(seed)
    ops = operands()
    recs = wide_scale_records()
    for a in TIE_DIVIDENDS:
        for b in TIE_DIVISORS:
            recs.append(('/', a, b))
            recs.append(('%', a, b))
    recs += long_records(random.Random(seed + 1))
    # the structured matrix: every operand against every divisor spelling
    for a in ops:
        for b in DIVISORS:
            for op in ('+', '-', '*', '/', '%'):
                recs.append((op, a, b))
            recs.append(('cmp', a, b))
    for a in ops:
        for n in (0, 1, 2, 18, 19, 20, 38):
            recs.append(('round', a, str(n)))
        for op in ('floor', 'ceil', 'trunc'):
            recs.append((op, a, '0'))
    # random wide pairs: up to 45 integer digits, scale up to 25
    def wide():
        ip = rng.randint(0, 10 ** rng.randint(1, 45))
        sc = rng.randint(0, 25)
        frac = str(rng.randint(0, 10 ** sc - 1)).rjust(sc, '0') if sc else ''
        return ('-' if rng.random() < 0.45 else '') + str(ip) + ('.' + frac if sc else '')
    for _ in range(count):
        a, b = fmt(*parse(wide())), fmt(*parse(wide()))
        for op in ('+', '-', '*', '/', '%', 'cmp'):
            recs.append((op, a, b))
        recs.append(('round', a, str(rng.randint(0, 30))))
    out = []
    for op, a, b in recs:
        if op in ('/', '%') and parse(b)[0] == 0:
            continue
        out.append(f'{op}|{a}|{b}|{calc(op, a, b)}')
    return out


def self_check(count, seed):
    """decimal-oracle.py's records, re-derived here. A disagreement means one of
    the two oracles is wrong, which is worth knowing before either grades a host."""
    text = subprocess.run([sys.executable, 'tools/decimal-oracle.py', str(count), str(seed)],
                          capture_output=True, text=True, check=True).stdout
    bad = 0
    lines = [l for l in text.split('\n') if l]
    for line in lines:
        op, a, b, want = line.split('|')
        got = calc(op, a, b)
        if got != want:
            bad += 1
            if bad <= 10:
                print(f'oracles disagree: {a} {op} {b}: exact {got}, decimal-module {want}')
    print(f'oracle self-check: {len(lines)} records, {bad} disagreements')
    return 1 if bad else 0


def main():
    args = sys.argv[1:]
    check = '--self-check' in args
    args = [a for a in args if a != '--self-check']
    count = int(args[0]) if args else 1500
    seed = int(args[1]) if len(args) > 1 else 20260929
    if check:
        sys.exit(self_check(count, seed))
    sys.stdout.write('\n'.join(wide_records(count, seed)) + '\n')


if __name__ == '__main__':
    main()
