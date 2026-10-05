"""mod / is_integer use divmod; the remainder is the same as `%` for every
sign and scale combination, and huge operands still work."""
import random

import sel
from sel import decimal as D


def ref_mod(a, b):
    A, B, s = D._aligned(a, b)
    return D.make(a.neg, A % B, s)


def test_mod_matches_the_percent_operator():
    rnd = random.Random(19)
    for _ in range(3000):
        a = D.make(rnd.random() < .5, rnd.randrange(0, 10 ** rnd.randrange(1, 40)), rnd.randrange(0, 6))
        b = D.make(rnd.random() < .5, rnd.randrange(1, 10 ** rnd.randrange(1, 25)), rnd.randrange(0, 6))
        assert D.format(D.mod(a, b)) == D.format(ref_mod(a, b)), (a, b)


def test_signs_follow_the_dividend_and_zero_is_unsigned():
    e = lambda s: sel.evaluate(s).scalar
    assert e('7 % 3') == '1'
    assert e('-7 % 3') == '-1'
    assert e('7 % -3') == '1'
    assert e('-6 % 3') == '0'
    assert e('7.5 % 2') == '1.5'


def test_is_integer_matches_the_percent_version():
    rnd = random.Random(2)
    for _ in range(2000):
        d = D.make(rnd.random() < .5, rnd.randrange(0, 10 ** rnd.randrange(1, 30)), rnd.randrange(0, 8))
        assert D.is_integer(d) == (True if d.scale == 0 else d.digits % (10 ** d.scale) == 0)
