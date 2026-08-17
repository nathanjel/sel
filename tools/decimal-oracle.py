#!/usr/bin/env python3
"""Generates decimal test cases from Python's `decimal` module as an independent
oracle for SEL's hand-written decimal core.

This exists because both SEL implementations were written by the same hand from
the same spec: if the algorithm is wrong, they would agree with each other and
still be wrong. Python's decimal is a third opinion that shares no code with
either. It is a development tool, not a runtime dependency.

Output is one `op|a|b|want` record per line — see tools/README.md. None of the
fields can contain a `|`, so reading it needs no parser in any host.

    python3 tools/decimal-oracle.py [count] [seed] > oracle.txt
"""

import random
import sys
from decimal import Decimal, getcontext, ROUND_HALF_UP

getcontext().prec = 200


def unscaled(d):
    t = d.as_tuple()
    return int(''.join(map(str, t.digits))), -t.exponent


def fmt(digits, scale, neg):
    if digits == 0:
        neg = False
    body = str(digits).rjust(scale + 1, '0')
    out = body if scale == 0 else body[:len(body) - scale] + '.' + body[len(body) - scale:]
    return ('-' if neg else '') + out


def canon(d):
    """SEL canonical form: scale preserved, no -0, no leading zeros."""
    u, sc = unscaled(d)
    return fmt(u, sc, d < 0)


def sel_div(a, b):
    """SEL division: exact at minimal scale when it terminates within
    DIV_SCALE digits, else rounded half away from zero at exactly DIV_SCALE."""
    ua, sa = unscaled(a)
    ub, sb = unscaled(b)
    n, den = ua * 10 ** sb, ub * 10 ** sa
    q, r = divmod(n * 10 ** 10, den)
    if r == 0:
        sc = 10
        while sc > 0 and len(str(q)) > 1 and str(q).endswith('0'):
            q //= 10
            sc -= 1
        if q == 0:
            sc = 0
    else:
        if 2 * r >= den:
            q += 1
        sc = 10
    return fmt(q, sc, (a < 0) != (b < 0))


def sel_mod(a, b):
    """Remainder of truncated division, taking the sign of the dividend."""
    ua, sa = unscaled(a)
    ub, sb = unscaled(b)
    s = max(sa, sb)
    r = (ua * 10 ** (s - sa)) % (ub * 10 ** (s - sb))
    return fmt(r, s, a < 0 and r != 0)


def rnd(rng):
    ip = rng.randint(0, 10 ** rng.randint(1, 12))
    sc = rng.randint(0, 6)
    frac = str(rng.randint(0, 10 ** sc - 1)).rjust(sc, '0') if sc else ''
    s = str(ip) + ('.' + frac if sc else '')
    return Decimal(('-' if rng.random() < 0.45 else '') + s)


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    rng = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 20260813)

    out = []

    def case(op, a, b, want):
        out.append(f'{op}|{a}|{b}|{want}')

    for _ in range(count):
        a, b = rnd(rng), rnd(rng)
        ca, cb = canon(a), canon(b)
        case('+', ca, cb, canon(a + b))
        case('-', ca, cb, canon(a - b))
        case('*', ca, cb, canon(a * b))
        case('cmp', ca, cb, str((a > b) - (a < b)))
        if b != 0:
            case('/', ca, cb, sel_div(a, b))
            case('%', ca, cb, sel_mod(a, b))
        n = rng.randint(0, 8)
        case('round', ca, str(n),
             canon(a.quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP)))
        for op, mode in (('floor', 'ROUND_FLOOR'), ('ceil', 'ROUND_CEILING'), ('trunc', 'ROUND_DOWN')):
            case(op, ca, '0', canon(a.to_integral_value(rounding=mode)))

    sys.stdout.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
