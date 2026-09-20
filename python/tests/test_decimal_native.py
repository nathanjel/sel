"""Exact arithmetic and allocation-path regressions for native Python ints."""
from decimal import Decimal, localcontext, ROUND_HALF_UP
import pytest
from sel import decimal as D


@pytest.mark.parametrize('a', ['0', '-0.00', '1.00', '-1.00', '12.345', '-999999999999999999.99'])
@pytest.mark.parametrize('b', ['0.000', '1', '-2.00', '0.003', '-999999999999999999.99'])
def test_subtraction_against_independent_decimal(a, b):
    with localcontext() as ctx:
        ctx.prec = 100
        expected = Decimal(a) - Decimal(b)
    actual = D.sub(D.parse(a), D.parse(b))
    assert Decimal(D.format(actual)) == expected
    assert actual.scale == max(D.parse(a).scale, D.parse(b).scale)
    assert actual.digits or not actual.neg


@pytest.mark.parametrize('a,b', [('1.00', '3.00'), ('-1.000', '6.00'),
    ('5.0000', '2.00'), ('0.000', '-2'), ('1.2345', '0.003'),
    ('999999999999999999999.000', '-7.00'), ('0.00000000005', '1.0')])
def test_division_rounding_against_independent_decimal(a, b):
    with localcontext() as ctx:
        ctx.prec = 100
        expected = (Decimal(a) / Decimal(b)).quantize(Decimal('1e-10'), rounding=ROUND_HALF_UP)
    actual = D.div(D.parse(a), D.parse(b))
    assert Decimal(D.format(actual)) == expected
    assert actual.digits or not actual.neg


def test_shared_large_scale_does_not_require_powers(monkeypatch):
    def forbidden(k):
        pytest.fail('equal scales need no power of ten')
    monkeypatch.setattr(D, '_pow10', forbidden)
    a, b = D.make(False, 7, 1000000), D.make(False, 2, 1000000)
    assert D.format(D.div(a, b)) == '3.5'
    assert D.sub(a, b) == D.make(False, 5, 1000000)


def test_division_only_scales_the_difference(monkeypatch):
    calls = []
    original = D._pow10
    def power(k):
        calls.append(k)
        return original(k)
    monkeypatch.setattr(D, '_pow10', power)
    assert D.format(D.div(D.make(False, 7, 10000), D.make(False, 2, 10001))) == '35'
    assert calls == [1]
