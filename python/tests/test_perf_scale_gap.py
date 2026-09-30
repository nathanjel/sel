"""PY-P22: cmp/is_integer across a wide scale gap decide from bit lengths before building
10**gap; they must agree with the exact alignment on every input, especially near the
power-of-ten boundaries where the bit-length estimate is inconclusive."""
import random
from fractions import Fraction

from sel import decimal as D


def exact_cmp(a, b):
    x = Fraction(-a.digits if a.neg else a.digits, 10 ** a.scale)
    y = Fraction(-b.digits if b.neg else b.digits, 10 ** b.scale)
    return (x > y) - (x < y)


def dec(digits, scale, neg=False):
    return D.make(neg, digits, scale)


def test_cmp_agrees_with_exact_arithmetic_on_random_inputs():
    rnd = random.Random(1022)
    for _ in range(6000):
        da = rnd.choice([0, rnd.randrange(1, 10 ** rnd.randrange(1, 60))])
        db = rnd.choice([0, rnd.randrange(1, 10 ** rnd.randrange(1, 60))])
        sa, sb = rnd.randrange(0, 400), rnd.randrange(0, 400)
        a, b = dec(da, sa, rnd.random() < .4), dec(db, sb, rnd.random() < .4)
        assert D.cmp(a, b) == exact_cmp(a, b), (a, b)


def test_cmp_near_a_power_of_ten_boundary_across_the_gap():
    # A is 10**k-1, 10**k, 10**k+1 at scale sa; B is the same value written at a
    # scale more than the gap threshold away — equal, or one unit off.
    for k in (1, 5, 17, 40, 130, 500):
        for delta in (-1, 0, 1):
            for extra in (65, 66, 100, 257, 1000):
                n = 10 ** k + delta
                a = dec(n, 3)
                b = dec(n * 10 ** extra, 3 + extra)        # the same value, wider scale
                assert D.cmp(a, b) == 0
                assert D.cmp(b, a) == 0
                c = dec(n * 10 ** extra + 1, 3 + extra)    # one unit in the last place above
                assert D.cmp(a, c) == -1 and D.cmp(c, a) == 1
                d = dec(n * 10 ** extra - 1, 3 + extra)    # one below
                assert D.cmp(a, d) == 1 and D.cmp(d, a) == -1
                assert D.cmp(dec(n, 3, True), d) == -1 and D.cmp(d, dec(n, 3, True)) == 1


def test_cmp_with_zero_across_scales():
    z = dec(0, 500)
    assert D.cmp(z, dec(1, 0)) == -1
    assert D.cmp(dec(1, 900), z) == 1
    assert D.cmp(z, dec(1, 0, True)) == 1
    assert D.cmp(z, dec(0, 3)) == 0
    assert D.cmp(dec(1, 0, True), z) == -1


def test_cmp_of_huge_digits_against_a_tiny_huge_scale_value():
    a = dec(10 ** 50_000 + 1, 100)
    b = dec(3, 1_000_000)
    assert D.cmp(a, b) == 1 and D.cmp(b, a) == -1
    assert D.cmp(dec(10 ** 50_000 + 1, 100, True), b) == -1


def test_is_integer_agrees_with_exact_arithmetic():
    rnd = random.Random(1023)
    for _ in range(6000):
        scale = rnd.choice([0, 1, 17, 18, 19, 63, 64, 65, 66, 70, 200, 1000])
        shape = rnd.random()
        if shape < 0.4:
            digits = rnd.randrange(0, 10 ** 30) * 10 ** scale      # an integer written with a scale
        elif shape < 0.7:
            digits = rnd.randrange(0, 10 ** 30) * 10 ** max(0, scale - rnd.randrange(1, 4))
        else:
            digits = rnd.randrange(0, 10 ** 40)
        want = Fraction(digits, 10 ** scale).denominator == 1
        assert D.is_integer(dec(digits, scale)) is want, (digits, scale)


def test_is_integer_with_many_trailing_zero_bits_but_not_enough_tens():
    # 2**70 has 70 trailing zero bits, but is not divisible by 10**66.
    assert D.is_integer(dec(2 ** 70, 66)) is False
    assert D.is_integer(dec(10 ** 66 * 7, 66)) is True
    assert D.is_integer(dec(10 ** 65, 66)) is False
