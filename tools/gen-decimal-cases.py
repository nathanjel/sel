#!/usr/bin/env python3
"""Writes conformance/24-decimal-boundaries.selt from the exact oracle.

    python3 tools/gen-decimal-cases.py            # rewrite the file
    python3 tools/gen-decimal-cases.py --check    # fail if it is stale

Every expectation is computed by tools/decimal-oracle-exact.py, which is written
from spec/SPEC.md §4 in exact rationals and shares no code, and no rounding
idiom, with any host's decimal core. The operands are the magnitudes the reviews
found the cores wrong at: scale 18/19 on both sides of a multiplication, int64 /
uint64 / int128 boundaries with both signs, remainders past 2^126, divisor
spellings (1, 1.0, 0.1, 1e-N), half-way ties, negative zero, leading zeros, and
the carry across MAX_INT_DIGITS.

Cases come in two spellings where the difference matters: a literal source (the
optimiser folds it) and a setup-assigned variable source (it does not), because
the two reach the core through different paths.
"""
import importlib.util
import os
import random
import sys

if hasattr(sys, 'set_int_max_str_digits'):   # the 32-* chains print products of thousands of digits
    sys.set_int_max_str_digits(0)

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_lib  # noqa: E402
CHECK = gen_lib.gen_args('gen-decimal-cases', '''usage: python3 tools/gen-decimal-cases.py            rewrite the files
       python3 tools/gen-decimal-cases.py --check    fail if they are stale''')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('oracle', os.path.join(ROOT, 'tools/decimal-oracle-exact.py'))
O = importlib.util.module_from_spec(spec)
spec.loader.exec_module(O)

OUT = os.path.join(ROOT, 'conformance/24-decimal-boundaries.selt')
cases = []
names = set()


def emit(name, note, source, expect, setup=None):
    assert name not in names, name
    names.add(name)
    cases.append((name, note, setup, source, expect))


def num(op, a, b):
    return 'num ' + O.calc(op, a, b)


def lit(s):
    """Source spelling of a canonical number; a negative is unary minus on a literal."""
    return s


def binary(op, tag, a, b, note, var=True, literal=True):
    sym = op
    want = num(op, a, b)
    n = f'dec.{ {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "mod"}[op] }.{tag}'
    if literal:
        emit(n + '.literal', note, f'({lit(a)}) {sym} ({lit(b)})', want)
    if var:
        emit(n + '.var', note, f'A {sym} B', want, setup=f'A = {lit(a)}; B = {lit(b)}')


P63, P64, P127, P128 = 2**63, 2**64, 2**127, 2**128
S = str
Z37 = '0.' + '0' * 36 + '1'          # 1e-37
N38 = '9' * 38

# --- addition, subtraction: boundaries and scale gaps --------------------------------
for tag, a, b in [
    ('int64-max-plus-one', S(P63 - 1), '1'),
    ('int64-min-minus-one', '-' + S(P63), '1'),
    ('uint64-max-plus-one', S(P64 - 1), '1'),
    ('int128-max-plus-one', S(P127 - 1), '1'),
    ('int128-min-minus-one', '-' + S(P127), '1'),
    ('uint128-max-plus-one', S(P128 - 1), '1'),
    ('two-2-128', S(P128), S(P128)),
    ('scale-gap-37', '1', Z37),
    ('scale-gap-19-18', '0.1234567890123456789', '0.123456789012345678'),
    ('carry-through-nines', N38, '1'),
    ('carry-through-nines-fraction', '0.' + N38, '0.' + '0' * 37 + '1'),
    ('sign-cancel', S(P127), '-' + S(P127)),
    ('negative-zero-result', '-0.5', '0.5'),
]:
    binary('+', tag, a, b, 'Exact addition at scale max(sa, sb); no host integer width may show through.')
for tag, a, b in [
    ('int64-min-minus-one', '-' + S(P63), '1'),
    ('int128-min-minus-one', '-' + S(P127), '1'),
    ('uint64-boundary', S(P64), S(P64 - 1)),
    ('scale-gap-19', '1', '0.0000000000000000001'),
    ('equal-is-zero-with-scale', '1.50', '1.5'),
    ('negative-zero-result', '0.5', '0.5'),
]:
    binary('-', tag, a, b, 'Exact subtraction at scale max(sa, sb); zero never carries a minus sign.')

# --- multiplication -------------------------------------------------------------------
mul_note = ('Exact multiplication at scale sa + sb. The C++ core once rounded both operands to 18 '
            'fractional digits when both had scale over 18, and mishandled the INT128_MIN mantissa.')
for tag, a, b in [
    ('scale-19-squared', '0.1234567890123456789', '0.1234567890123456789'),
    ('scale-19-negative', '-0.1234567890123456789', '0.1234567890123456789'),
    ('scale-38-times-scale-19', '0.' + N38, '0.1234567890123456789'),
    ('scale-19-zero-times-scale-38', '0.0000000000000000000', '0.' + N38),
    ('scale-38-zero-times-scale-38', '0.' + '0' * 38, '0.' + N38),
    ('scale-19-tiny-times-scale-38', '0.0000000000000000002', '0.' + N38),
    ('scale-38-tiny-times-scale-38', '0.00000000000000000000000000000000000002', '0.' + N38),
    ('scale-18-times-scale-19', '0.123456789012345678', '0.1234567890123456789'),
    ('int64-min-times-uint64-pow', '-' + S(P63), S(P64)),
    ('int64-min-times-minus-one', '-' + S(P63), '-1'),
    ('uint64-max-squared', S(P64 - 1), S(P64 - 1)),
    ('two-64-squared', S(P64), S(P64)),
    ('int128-min-times-one', '-' + S(P127), '1'),
    ('int128-min-times-minus-one', '-' + S(P127), '-1'),
    ('int128-max-times-two', S(P127 - 1), '2'),
    ('two-127-times-two', S(P127), '2'),
    ('wide-times-wide', S(10**38 + 1), S(10**38 - 1)),
    ('nines-squared', N38, N38),
    ('zero-times-negative', '0', '-5'),
    ('negative-zero-scale', '-0.0', '5'),
    ('leading-zeros', '007.50', '2'),
]:
    binary('*', tag, a, b, mul_note)

# --- division: the divisor spellings, remainders past 2^126, scale gaps -----------------
div_note = ('Long division to DIV_SCALE = 10 (spec 4.3): exact at minimal scale when it terminates, else '
            'rounded half away from zero at exactly 10. Found wrong at a remainder past 2^126 (it overflowed '
            'a signed __int128) and at a divisor whose digit string is "1".')
for tag, a, b in [
    ('by-one-beyond-int128', S(10**42), '1'),
    ('by-one-negative-beyond-int128', '-' + S(10**42), '1'),
    ('by-one-int128-max', S(P127 - 1), '1'),
    ('by-one-point-zero', S(10**42), '1.0'),
    ('by-one-point-zero-zero', S(10**42), '1.00'),
    ('by-minus-one', S(10**42), '-1'),
    ('by-tenth', S(10**40), '0.1'),
    ('by-tenth-spelled-0.10', S(10**40), '0.10'),
    ('by-tiny-1e-9', '33089435705329677516', '0.000000001'),
    ('by-tiny-1e-37', '5', '0.0000000000000000000000000000000000001'),
    ('by-tiny-1e-37-int-dividend-2', '2', '0.0000000000000000000000000000000000001'),
    ('by-tiny-1e-37-negative', '-1', '0.0000000000000000000000000000000000001'),
    ('remainder-over-2-126-a', '8600000000000000000000000000.0000000000', N38),
    ('remainder-over-2-126-b', '0.' + N38, '0.' + N38),
    ('remainder-over-2-126-c', '0.9', '1.' + '0' * 37 + '1'),
    ('remainder-over-2-126-d', '0.' + N38, '3'),
    ('remainder-over-2-126-e', S(P127 + 5), S(P127 - 3)),
    ('remainder-over-2-126-f', '-' + S(P127 + 5), S(P127 - 3)),
    ('uint128-by-uint64', S(P128 - 1), S(P64 - 1)),
    ('one-third-scale-38', '1.' + '0' * 37 + '1', '3'),
    ('tie-half-away-positive', '1', '20000000000'),
    ('tie-half-away-negative', '-1', '20000000000'),
    ('tie-half-away-three', '3', '20000000000'),
    ('just-under-tie', '1', '20000000001'),
    ('exact-minimal-scale', '1', '8'),
    ('exact-at-scale-10', '1', '1024'),
    ('rounds-up-at-scale-10', '2', '3'),
    ('rounds-to-zero-with-scale', '1', S(10**38)),
    ('rounds-to-zero-negative', '-1', S(10**38)),
    ('rounds-up-to-1e-10', '86', S(10**12)),
    ('scale-gap-dividend', '1.0000000000000000000', '3'),
    ('scale-gap-divisor', '3', '1.0000000000000000000'),
    ('int64-min-by-minus-one', '-' + S(P63), '-1'),
    ('int128-min-by-minus-one', '-' + S(P127), '-1'),
    ('int128-min-by-seven', '-' + S(P127), '7'),
    ('wide-by-wide-nines', S(10**38 + 1), N38),
    ('nines-by-ten-to-38', N38, S(10**38)),
    ('nines-scale-by-nines-scale', '0.' + N38, '0.99999999999999999999999999999999999998'),
]:
    binary('/', tag, a, b, div_note)

# --- modulo: sign of the dividend, scale max(sa, sb) -----------------------------------
mod_note = ('Remainder of truncated division, sign of the dividend, scale max(sa, sb); a zero result is never '
            '"-0". `-1e42 % 1` once printed -0 in C++.')
for tag, a, b in [
    ('by-one-negative-beyond-int128', '-' + S(10**42), '1'),
    ('by-one-beyond-int128', S(10**42), '1'),
    ('by-one-point-zero', '-' + S(10**42), '1.0'),
    ('by-tenth', '-' + S(10**40), '0.1'),
    ('by-tenth-fraction', '-0.55', '0.1'),
    ('int64-min-by-three', '-' + S(P63), '3'),
    ('int64-min-by-minus-one', '-' + S(P63), '-1'),
    ('int128-min-by-seven', '-' + S(P127), '7'),
    ('int128-max-by-seven', S(P127 - 1), '7'),
    ('uint128-by-uint64', S(P128 - 1), S(P64 - 1)),
    ('remainder-over-2-126', S(P127 + 5), S(P127 - 3)),
    ('scale-gap', '5.5', '0.0000000000000000000000000000000000001'),
    ('scale-19-by-scale-19', '0.1234567890123456789', '0.0000000000000000007'),
    ('negative-dividend-positive-divisor', '-5', '3'),
    ('positive-dividend-negative-divisor', '5', '-3'),
    ('both-negative', '-5', '-3'),
    ('exact-multiple-negative', '-6', '3'),
    ('smaller-dividend', '2', '7'),
    ('fraction-dividend', '5.5', '2'),
    ('nines-by-eight', N38, '8'),
    ('leading-zeros', '007.50', '2'),
]:
    binary('%', tag, a, b, mod_note)

# --- rounding ---------------------------------------------------------------------------
rnd_note = 'ROUND is half away from zero and returns scale exactly n (spec 4.4, 7.6). Remainder past 2^126.'
for tag, a, n in [
    ('nines-38-to-0', '0.' + N38, 0),
    ('point-nine-38-to-0', '0.90000000000000000000000000000000000000', 0),
    ('point-nine-38-to-1', '0.90000000000000000000000000000000000000', 1),
    ('point-five-38-to-0', '0.50000000000000000000000000000000000000', 0),
    ('just-under-half-38', '0.49999999999999999999999999999999999999', 0),
    ('tie-positive', '2.5', 0), ('tie-negative', '-2.5', 0), ('tie-one-half', '0.5', 0),
    ('tie-negative-one-half', '-0.5', 0), ('tie-1.5', '1.5', 0),
    ('tie-scale-19', '0.0000000000000000005', 18),
    ('tie-scale-19-negative', '-0.0000000000000000005', 18),
    ('below-tie-scale-19', '0.0000000000000000004', 18),
    ('tie-scale-38', '0.00000000000000000000000000000000000005', 37),
    ('carry-across-integer', '99999999999999999999999999999999999999.5', 0),
    ('carry-negative', '-99999999999999999999999999999999999999.5', 0),
    ('negative-zero-result', '-0.4', 0),
    ('negative-zero-result-scale', '-0.004', 2),
    ('pad-scale-up', '1.5', 4),
    ('pad-integer-up', '7', 3),
    ('scale-19-to-19', '0.1234567890123456789', 19),
    ('scale-19-to-18', '0.1234567890123456789', 18),
    ('scale-19-to-20', '0.1234567890123456789', 20),
    ('int128-min-to-0', '-' + S(P127), 0),
    ('int128-min-fraction', '-170141183460469231731687303715884105728.5', 0),
    ('uint64-boundary-fraction', '18446744073709551615.5', 0),
    ('wide-fraction-to-18', '123456789012345678901234567890.123456789012345678', 18),
    ('wide-fraction-to-9', '123456789012345678901234567890.123456789012345678', 9),
    ('1.0000000000000000005-to-18', '1.0000000000000000005', 18),
    ('leading-zeros', '007.55', 1),
]:
    emit(f'dec.round.{tag}', rnd_note, f'ROUND({a}, {n})', num('round', a, S(n)))

# --- CEIL / FLOOR / TRUNC ------------------------------------------------------------------
for tag, a in [
    ('positive-fraction', '2.1'), ('negative-fraction', '-2.1'), ('small-positive', '0.0000000000000000000000000001'),
    ('small-negative', '-0.0000000000000000000000000001'), ('negative-zero-result-ceil', '-0.4'),
    ('scale-preserved-integer', '5.000'), ('int128-min-fraction', '-170141183460469231731687303715884105728.5'),
    ('int128-max-fraction', '170141183460469231731687303715884105727.5'),
    ('uint64-fraction', '18446744073709551615.5'), ('nines-fraction', N38 + '.9999999999999999999'),
    ('negative-nines-fraction', '-' + N38 + '.9999999999999999999'),
]:
    for fn in ('CEIL', 'FLOOR', 'TRUNC'):
        emit(f'dec.{fn.lower()}.{tag}', 'CEIL, FLOOR and TRUNC return scale 0 (spec 7.6); a zero never carries a sign.',
             f'{fn}({a})', num(fn.lower(), a, '0'))

# --- POWER --------------------------------------------------------------------------------
pow_note = 'POWER is exact repeated multiplication; the result scale is scale(x) * n (spec 7.6).'
for tag, a, n in [
    ('1.05-to-30', '1.05', 30), ('half-to-100', '0.5', 100), ('point-one-to-20', '0.1', 20),
    ('point-one-to-19', '0.1', 19), ('scale-19-to-3', '0.1234567890123456789', 3),
    ('two-to-127', '2', 127), ('two-to-128', '2', 128), ('minus-two-to-127', '-2', 127),
    ('minus-two-to-128', '-2', 128), ('ten-to-38', '10', 38), ('ten-to-39', '10', 39),
    ('minus-three-to-81', '-3', 81), ('1.5-to-64', '1.5', 64), ('zero-to-zero', '0', 0),
    ('scaled-to-zero', '2.50', 0), ('scaled-zero', '0.00', 5), ('one-point-zero-to-30', '1.0', 30),
    ('uint64-max-squared', S(P64 - 1), 2), ('int64-min-squared', '-' + S(P63), 2),
    ('int64-min-cubed', '-' + S(P63), 3), ('nines-scale-38-squared', '0.' + N38, 2),
    ('scale-18-squared', '0.123456789012345678', 2), ('scale-9-cubed', '0.123456789', 3),
    ('half-to-63', '0.5', 63), ('half-to-64', '0.5', 64), ('half-to-127', '0.5', 127),
]:
    emit(f'dec.pow.{tag}', pow_note, f'POWER({a}, {n})', num('pow', a, S(n)))
emit('dec.pow.variable-base', pow_note, 'POWER(A, 30)', num('pow', '1.05', '30'), setup='A = 1.05')

# --- comparison across widths (same value at different scales / widths) ---------------------
for tag, a, op, b, want in [
    ('int128-boundary-gt', S(P127), '>', S(P127 - 1), 'TRUE'),
    ('int128-boundary-eq-scale', S(P127) + '.000', '==', S(P127), 'TRUE'),
    ('int128-min-lt', '-' + S(P127), '<', '-' + S(P127 - 1), 'TRUE'),
    ('uint64-vs-int64', S(P64), '>', S(P63), 'TRUE'),
    ('scale-38-vs-scale-0', '0.' + '0' * 38, '==', '0', 'TRUE'),
    ('negative-zero-equals-zero', '-0.0', '==', '0', 'TRUE'),
    ('tiny-vs-tiny', Z37, '>', '0.' + '0' * 37 + '0', 'TRUE'),
    ('text-differs-for-spellings', '1.0', '$==', '1', 'FALSE'),
]:
    src = f'A {op} B'
    emit(f'dec.cmp.{tag}', 'Numeric comparison aligns scales and is exact at any width; `$==` is text.',
         src, f'bool {want}', setup=f'A = {a}; B = {b}' if op != '$==' else f'A = "{a}"; B = "{b}"')

# --- the INT128_MIN product, reached through variables ------------------------------
c41 = 'The product -2^63 * 2^64 is exactly -2^127, the C++ int128 minimum; negating or taking abs of that mantissa was UB. Variables keep the literals from folding.'
setup41 = 'A = -9223372036854775808; B = 18446744073709551616; C = A * B'
emit('dec.int128-min.abs-positive', c41, 'ABS(C) > 0', 'bool TRUE', setup=setup41)
emit('dec.int128-min.abs-value', c41, 'ABS(C)', 'num 170141183460469231731687303715884105728', setup=setup41)
emit('dec.int128-min.negate-is-positive', c41, '(-C) < 0', 'bool FALSE', setup=setup41)
emit('dec.int128-min.negate-value', c41, '-C', 'num 170141183460469231731687303715884105728', setup=setup41)
emit('dec.int128-min.is-negative', c41, 'C < 0', 'bool TRUE', setup=setup41)
emit('dec.int128-min.text', c41, 'C & ""', 'text "-170141183460469231731687303715884105728"', setup=setup41)
emit('dec.int128-min.times-minus-one', c41, 'C * -1', 'num 170141183460469231731687303715884105728', setup=setup41)
emit('dec.int128-min.minus-one', c41, 'C - 1', 'num -170141183460469231731687303715884105729', setup=setup41)
emit('dec.int128-min.divided', c41, 'C / 2', 'num -85070591730234615865843651857942052864', setup=setup41)
emit('dec.int128-min.round', c41, 'ROUND(C, 2)', num('round', '-170141183460469231731687303715884105728', '2'), setup=setup41)

# --- carry across MAX_INT_DIGITS --------------------------------------------------------
cap = ('CEIL/FLOOR of a number at the integer-digit cap can carry into a 1,000,001st digit; that is E_RANGE '
       'at the call, as ROUND already is (spec 6.4). Position 1:1 is the call, the innermost failing node. '
       'Observed when written: JS, PHP, Python and Lisp raise E_RANGE but report 0:0 (no position); C++ and Go '
       'raise nothing and return a 1,000,001-digit value.')
emit('dec.ceil.carry-past-the-digit-cap', cap, 'CEIL(REPEAT("9", 1000000) & ".5")', 'error E_RANGE at 1:1')
emit('dec.floor.carry-past-the-digit-cap', cap, 'FLOOR("-" & REPEAT("9", 1000000) & ".5")', 'error E_RANGE at 1:1')
emit('dec.round.carry-past-the-digit-cap', cap, 'ROUND(REPEAT("9", 1000000) & ".5", 0)', 'error E_RANGE at 1:1')
emit('dec.ceil.carry-just-under-the-digit-cap', cap + ' Control: one digit shorter still fits.',
     'LEN(CEIL(REPEAT("9", 999999) & ".5"))', 'num 1000000')
emit('dec.floor.carry-just-under-the-digit-cap', cap + ' Control.',
     'LEN(FLOOR("-" & REPEAT("9", 999999) & ".5")) - 1', 'num 1000000')
emit('dec.trunc.at-the-digit-cap-does-not-carry', cap + ' Control: TRUNC never carries.',
     'LEN(TRUNC(REPEAT("9", 1000000) & ".5"))', 'num 1000000')
emit('dec.floor.positive-at-the-digit-cap-does-not-carry', cap + ' Control: FLOOR of a positive never carries.',
     'LEN(FLOOR(REPEAT("9", 1000000) & ".5"))', 'num 1000000')
emit('dec.ceil.negative-at-the-digit-cap-does-not-carry', cap + ' Control: CEIL of a negative never carries.',
     'LEN(CEIL("-" & REPEAT("9", 1000000) & ".5")) - 1', 'num 1000000')


# --- conformance/32-numeric-plans.selt: numbers around the math plans -----------------------------
#
# A host may keep numbers in arithmetic form between operations: Go reuses big.Int registers inside a
# plan, Rust shares large mantissas, PHP carries GMP magnitudes and writes digits on demand. These cases
# pin what that must never change -- a number an operation yields is its own (spec §3.4) -- and run
# multi-operation chains on big operands. Operands come from `--- setup`, so every case reaches the plan
# executor with run-time values; sources stay short and free of "000000", so Go's planned-versus-plain
# test (go/sel/eval_order_test.go) runs them as well.

OUT32 = os.path.join(ROOT, 'conformance/32-numeric-plans.selt')
plan_cases = []
RNG = random.Random(20261001)


def emit32(name, note, source, expect, setup=None):
    assert name not in names, name
    assert len(source) < 20000 and '000000' not in source, name
    names.add(name)
    plan_cases.append((name, note, setup, source, expect))


def big(ndigits, scale, neg=False):
    """A numeral of `ndigits` significant digits, `scale` of them fractional, with no run of six zeros."""
    assert ndigits > scale
    while True:
        s = str(RNG.randint(1, 9)) + ''.join(RNG.choice('0123456789') for _ in range(ndigits - 1))
        if '000000' not in s:
            break
    return ('-' if neg else '') + (s if scale == 0 else s[:-scale] + '.' + s[-scale:])


def V(text):
    return O.parse(text)


def T(v):
    return O.fmt(*v)


def neg_(v):
    return -v[0], v[1]


def abs_(v):
    return abs(v[0]), v[1]


def extreme(vs, sign):
    """MAX (sign 1) or MIN (sign -1) of distinct values: the operand itself, scale included."""
    best = vs[0]
    for v in vs[1:]:
        assert O.cmp_(v, best) != 0
        if O.cmp_(v, best) == sign:
            best = v
    return best


own = ('A number an operation yields is its own: no later evaluation changes it, however a host keeps '
       'numbers between operations (spec §3.4). Item 1 lets hosts reuse registers, share mantissas or '
       'write digits on demand; these cases are what that must not change.')
A_, B_, C_ = big(420, 7), big(350, 3), big(300, 12, neg=True)
ABC = f'A = {A_}; B = {B_}; C = {C_}'
a, b, c = V(A_), V(B_), V(C_)
ab, bc, ca = O.mul(a, b), O.mul(b, c), O.mul(c, a)

emit32('plan.own.operand-unchanged-after-a-plan', own, 'R = A * A - B * B + C; A', 'num ' + T(a), ABC)
emit32('plan.own.copy-unchanged-after-a-square', own, 'S = A; A = A * A; S', 'num ' + T(a), ABC)
emit32('plan.own.square-through-a-copy', own, 'S = A; S * A', 'num ' + T(O.mul(a, a)), ABC)
emit32('plan.own.assignment-leaves-an-earlier-operand', own + ' The left operand was read before the '
       'assignment stored a new value in X.', 'X = A; X * (X = 2)', 'num ' + T(O.mul(a, V('2'))), ABC)
x, y = O.add(ab, c), O.sub(ca, b)
seq = 'X = A * B + C; Y = C * A - B; Z = B * B + A; '
emit32('plan.own.earlier-result-survives-later-plans', own, seq + 'X', 'num ' + T(x), ABC)
emit32('plan.own.middle-result-survives-a-later-plan', own, seq + 'Y', 'num ' + T(y), ABC)
emit32('plan.own.negated-intermediate-survives', own, 'N = -(A * B + C); W = A * A + B; N', 'num ' + T(neg_(x)), ABC)
emit32('plan.own.absolute-intermediate-survives', own, 'M = ABS(C * B - A); W = C * C + A; M',
       'num ' + T(abs_(O.sub(O.mul(c, b), a))), ABC)
emit32('plan.own.truncated-intermediate-survives', own, 'T = TRUNC(A * B / C); W = A * B + C; T',
       'num ' + T(O.trunc_(O.div(ab, c))), ABC)
emit32('plan.own.maximum-of-intermediates-survives', own, 'M = MAX(A * B, B * C); W = A * B - B * C; M',
       'num ' + T(extreme([ab, bc], 1)), ABC)
inner = O.sub(O.mul(a, a), b) if O.cmp_(a, b) > 0 else O.sub(O.mul(b, b), a)
emit32('plan.own.nested-plan-in-a-branch', own + ' The IF is a leaf of the outer plan, with plans of its own.',
       'R = A * B + IF(A > B, A * A - B, B * B - A) * C; R', 'num ' + T(O.add(ab, O.mul(inner, c))), ABC)
inner_len = len(T(O.sub(O.mul(a, a), b)))
emit32('plan.own.nested-plan-in-a-length', own + ' The LEN is a leaf of the outer plan, with a plan of its own.',
       'R = A * B + LEN((A * A - B) & "") * C; R', 'num ' + T(O.add(ab, O.mul(V(str(inner_len)), c))), ABC)
zero = O.sub(ab, ab)
emit32('plan.own.zero-with-scale-survives', own + ' A zero keeps its scale.',
       'Z = A * B - A * B; W = A + B; Z', 'num ' + T(zero), ABC)
emit32('plan.own.zero-with-scale-adds', own, 'Z = A * B - A * B; Z + C', 'num ' + T(O.add(zero, c)), ABC)
chain11 = ' * '.join(['X'] * 11)
col = [i for i, ch in enumerate(chain11) if ch == '*'][9] + 1
emit32('plan.own.range-error-at-an-intermediate-product', 'X has mantissa 1 and scale 100000, so each product '
       'adds 100000 fractional digits and the tenth `*` is the first past MAX_FRAC_DIGITS (spec §6.4): E_RANGE '
       'at that operator, wherever the host keeps the intermediate products.', chain11, f'error E_RANGE at 1:{col}',
       'X = POWER(0.1, 100000)')

chain = ('Multi-operation arithmetic on big operands through the math plans, with run-time operands; the '
         'expectation is composed from tools/decimal-oracle-exact.py one operation at a time.')
for tag, (na, sa, ga), (nb, sb, gb), (nc, sc, gc) in [
    ('small-scales', (300, 2, False), (320, 0, True), (280, 5, False)),
    ('wide', (1000, 40, True), (900, 3, False), (950, 77, True)),
    ('widest', (3000, 120, False), (2800, 9, False), (2500, 400, True)),
]:
    p, q, r = big(na, sa, ga), big(nb, sb, gb), big(nc, sc, gc)
    vp, vq, vr = V(p), V(q), V(r)
    emit32(f'plan.chain.difference-of-squares.{tag}', chain, 'A * A - B * B + C',
           'num ' + T(O.add(O.sub(O.mul(vp, vp), O.mul(vq, vq)), vr)), f'A = {p}; B = {q}; C = {r}')
emit32('plan.chain.quotients', chain + ' Each `/` rounds to DIV_SCALE (spec §4.3).', '(A * B / C - A) / B',
       'num ' + T(O.div(O.sub(O.div(ab, c), a), b)), ABC)
emit32('plan.chain.remainder', chain + ' `%` takes the sign of the dividend.', '(A * B) % C + A',
       'num ' + T(O.add(O.mod(ab, c), a)), ABC)
emit32('plan.chain.rounding', chain, 'ROUND(A * B - C, 5) * B', 'num ' + T(O.mul(O.rnd(O.sub(ab, c), 5), b)), ABC)
emit32('plan.chain.power', chain, 'POWER(B, 3) - A * A * B',
       'num ' + T(O.sub(O.power(b, 3), O.mul(O.mul(a, a), b))), ABC)
emit32('plan.chain.minimum-and-maximum', chain + ' MIN and MAX return an operand, scale included.',
       'MIN(A * B, B * C, C * A) + MAX(A, B, C)', 'num ' + T(O.add(extreme([ab, bc, ca], -1), extreme([a, b, c], 1))),
       ABC)
ks = [O.div(O.mul(O.sub(O.mul(a, V(k)), b), O.add(a, V(k))), c) for k in ('1', '2', '3')]
emit32('plan.chain.map-body', chain + ' The body plan runs once per element in one context.',
       'L = MAP(LIST(1, 2, 3), K, (A * K - B) * (A + K) / C); L[3] - L[1]', 'num ' + T(O.sub(ks[2], ks[0])), ABC)
sq = [O.sub(O.mul(v, v), a) for v in (a, b, c)]
emit32('plan.chain.sum-body', chain, 'SUM(LIST(A, B, C), _ * _ - A)', 'num ' + T(O.add(O.add(sq[0], sq[1]), sq[2])),
       ABC)

mandel = ('Unrolled iterations of examples/mandelbrot.sel: the digits roughly double every step, and the same '
          'variables are read, squared and reassigned. Composed from tools/decimal-oracle-exact.py.')
step = 'TR = ZR * ZR - ZI * ZI + CR; ZI = 2.0 * ZR * ZI + CI; ZR = TR; '
for tag, px, py, n in [('inside', 20, 12, 8), ('escaping', 31, 3, 7)]:
    cr = O.div(O.sub(V(str(px)), V('27.0')), V('12.5'))
    ci = O.div(O.sub(V(str(py)), V('10.0')), V('7.5'))
    zr, zi = V('0.0'), V('0.0')
    for _ in range(n):
        zr, zi = O.add(O.sub(O.mul(zr, zr), O.mul(zi, zi)), cr), O.add(O.mul(O.mul(V('2.0'), zr), zi), ci)
    src = 'ZR = 0.0; ZI = 0.0; ' + step * n
    for part, want in (('zr', zr), ('zi', zi)):
        emit32(f'plan.chain.mandelbrot.{tag}.{part}', mandel + f' Point X={px}, Y={py}, {n} iterations.',
               src + part.upper(), 'num ' + T(want), f'CR = {T(cr)}; CI = {T(ci)}')


# --- writer ------------------------------------------------------------------------------------
HEADER = """% Exact decimal arithmetic at the magnitudes the cores were found wrong at.
%
% GENERATED by tools/gen-decimal-cases.py -- do not edit by hand:
%     python3 tools/gen-decimal-cases.py            # rewrite this file
%     python3 tools/gen-decimal-cases.py --check    # fail if it is stale
% Every expectation is computed by tools/decimal-oracle-exact.py (exact rationals,
% written from spec/SPEC.md section 4, sharing no code or rounding idiom with any
% host). Cases marked `.literal` are folded by the optimiser; `.var` cases take
% their operands from `--- setup` and reach the core at run time.
%
% Normative. Every implementation must pass every case.
"""


HEADER32 = """% Numbers around the math plans: what reused storage must never change, and multi-operation chains on
% big operands.
%
% GENERATED by tools/gen-decimal-cases.py -- do not edit by hand:
%     python3 tools/gen-decimal-cases.py            # rewrite this file
%     python3 tools/gen-decimal-cases.py --check    # fail if it is stale
% Every expectation is composed from tools/decimal-oracle-exact.py, one operation at a time. Operands come
% from `--- setup`, so each case reaches the plan executor with run-time values; sources stay short and free
% of "000000", so Go's planned-versus-plain test (go/sel/eval_order_test.go) runs them as well.
%
% Normative. Every implementation must pass every case.
"""

OUTPUTS = [(OUT, HEADER, cases), (OUT32, HEADER32, plan_cases)]


def render(header, items):
    out = [header]
    for name, note, setup, source, expect in items:
        out.append(f'\n### name: {name}\n--- note\n{note}\n')
        if setup:
            out.append(f'--- setup\n{setup}\n')
        out.append(f'--- source\n{source}\n--- expect\n{expect}\n===\n')
    return ''.join(out)


if __name__ == '__main__':
    for path, header, items in OUTPUTS:
        print(f'{os.path.relpath(path, ROOT)}: {len(items)} cases')
    gen_lib.write_or_check('gen-decimal-cases', ROOT,
                           [(os.path.relpath(path, ROOT), render(header, items)) for path, header, items in OUTPUTS],
                           CHECK, 'python3 tools/gen-decimal-cases.py')
