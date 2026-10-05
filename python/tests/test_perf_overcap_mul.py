"""mul refuses an over-cap product from the operand bit lengths, before multiplying.
It must refuse exactly what guard() refuses, with the same code, message and position,
and multiply everything else. The cap is shrunk (monkeypatched) so the boundary is
cheap to reach; the real cap is exercised once with million-digit operands."""
import random

import pytest

from sel import decimal as D
from sel.errors import SelError, Pos


def reference(a, b, pos):
    return D.guard(D.make(a.neg != b.neg, a.digits * b.digits, a.scale + b.scale), pos)


def outcome(f):
    try:
        r = f()
        return ('ok', r.neg, r.digits, r.scale)
    except SelError as e:
        return ('err', e.code, str(e), e.line, e.col)


@pytest.mark.parametrize('cap', [30, 64, 200])
def test_mul_refuses_exactly_what_guard_refuses_near_a_small_cap(monkeypatch, cap):
    monkeypatch.setattr(D, 'MAX_INT_DIGITS', cap)
    monkeypatch.setattr(D, 'MAX_FRAC_DIGITS', cap)
    monkeypatch.setattr(D, 'MAX_INT_BITS', 0)
    monkeypatch.setattr(D, '_MUL_PRECHECK_BITS', 0)      # always take the estimate path
    rnd = random.Random(cap)
    pos = Pos(1, 7, 6)
    for _ in range(4000):
        ka, kb = rnd.randrange(0, cap + 10), rnd.randrange(0, cap + 10)
        a = D.make(rnd.random() < .5, rnd.choice([0, 10 ** ka, 10 ** ka + rnd.randrange(0, 10 ** ka + 1), rnd.randrange(1, 10 ** (ka + 1))]),
                   rnd.randrange(0, cap + 5))
        b = D.make(rnd.random() < .5, rnd.choice([0, 10 ** kb, rnd.randrange(1, 10 ** (kb + 1))]),
                   rnd.randrange(0, cap + 5))
        assert outcome(lambda: D.mul(a, b, pos)) == outcome(lambda: reference(a, b, pos)), (a, b)


def test_mul_at_the_boundary_of_the_cap(monkeypatch):
    monkeypatch.setattr(D, 'MAX_INT_DIGITS', 100)
    monkeypatch.setattr(D, 'MAX_INT_BITS', 0)
    monkeypatch.setattr(D, '_MUL_PRECHECK_BITS', 0)
    for k in range(0, 101):
        a = D.make(False, 10 ** k, 0)
        b = D.make(False, 10 ** (100 - k), 0)     # (k+1) + (101-k) - 1 = 101 digits -> over
        c = D.make(False, 10 ** (99 - k) if k < 100 else 1, 0)
        assert outcome(lambda: D.mul(a, b)) == outcome(lambda: reference(a, b, None))
        assert outcome(lambda: D.mul(a, c)) == outcome(lambda: reference(a, c, None))


def test_a_zero_operand_is_never_refused_however_large_the_other(monkeypatch):
    monkeypatch.setattr(D, '_MUL_PRECHECK_BITS', 0)
    huge = D.make(False, 10 ** 5000, 3)
    assert D.mul(D.make(False, 0, 0), huge).digits == 0
    assert D.mul(huge, D.make(False, 0, 2)).digits == 0


class _NoMultiply(int):
    """A mantissa that fails the test if anything multiplies it."""

    def __mul__(self, other):
        raise AssertionError('multiplied a mantissa the cap had already settled')

    __rmul__ = __mul__


def test_the_real_cap_refuses_a_million_digit_square_without_multiplying():
    # Deterministic rather than timed: the multiplication alone took over a second.
    big = D.make(False, _NoMultiply(10 ** 999_999), 0)
    with pytest.raises(SelError) as info:
        D.mul(big, big)
    assert info.value.code == 'E_RANGE'


def test_a_legal_near_cap_product_is_still_computed():
    a = D.make(False, 10 ** 499_999, 0)            # 500,000 digits squared: 999,999 digits
    r = D.mul(a, a)
    assert r.digits == 10 ** 999_998
