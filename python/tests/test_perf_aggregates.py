"""SUM accumulates a signed integer at the widest scale instead of a Dec per addition,
and SORT_BY evaluates its keys in one frame. Both must give exactly what the per-element
versions gave: same value, same scale, same error at the same position."""
import random

import pytest

import sel
from sel import Value, SelError
from sel import decimal as D


def reference_sum(decs, pos=None):
    total = D.make(False, 0, 0)
    for d in decs:
        total = D.add(total, d, pos)
    return total


def rand_dec(rnd):
    digits = rnd.choice([0, rnd.randrange(1, 10 ** rnd.randrange(1, 25))])
    return D.make(rnd.random() < .5, digits, rnd.randrange(0, 12))


def test_sum_equals_the_chain_of_additions_on_random_inputs():
    rnd = random.Random(2525)
    for _ in range(1500):
        decs = [rand_dec(rnd) for _ in range(rnd.randrange(0, 9))]
        want = reference_sum(decs)
        got = sel.evaluate('SUM(L, _)', {'L': [Value.num(d) for d in decs]}) if decs else sel.evaluate('SUM(LIST(), _)')
        assert got.kind == 'TEXT'
        assert got.as_decimal(None) == want, (decs, got.scalar, D.format(want))
        assert got.scalar == D.format(want)


@pytest.mark.parametrize('src,want', [
    ('SUM((1.5, -1.5), _)', '0.0'),            # cancellation keeps the widest scale
    ('SUM((-2, 0.25, 3), _)', '1.25'),
    ('SUM((0.10, 0.2, -0.300), _)', '0.000'),
    ('SUM((1, 2, 3, 4), _ * 1.25)', '12.50'),
    ('SUM((5), _)', '5'),
    ('SUM((-7), _)', '-7'),
    ('SUM((0), _)', '0'),
    ('SUM((0.000), _)', '0.000'),
])
def test_sum_scale_and_sign_corner_cases(src, want):
    assert sel.evaluate(src).scalar == want


def test_sum_reports_a_non_number_at_the_body_as_before():
    with pytest.raises(SelError) as info:
        sel.evaluate('SUM((1, "x", 3), _)')
    assert info.value.code == 'E_NOT_NUM'


@pytest.mark.parametrize('cap', [12, 40])
def test_sum_refuses_the_cap_where_the_chain_of_additions_does(monkeypatch, cap):
    monkeypatch.setattr(D, 'MAX_INT_DIGITS', cap)
    monkeypatch.setattr(D, '_MAX_INT_BITS', 0)
    rnd = random.Random(cap)
    for _ in range(400):
        decs = [D.make(rnd.random() < .3, rnd.randrange(1, 10 ** rnd.randrange(1, cap + 1)),
                       rnd.randrange(0, 3)) for _ in range(rnd.randrange(1, 7))]
        def want():
            try:
                return ('ok', D.format(reference_sum(decs)))
            except SelError as e:
                return ('err', e.code)
        def got():
            try:
                return ('ok', sel.evaluate('SUM(L, _)', {'L': [Value.num(d) for d in decs]}).scalar)
            except SelError as e:
                return ('err', e.code)
        assert got() == want(), decs


def test_sort_by_unchanged_with_and_without_the_key_variable():
    data = {'L': [{'n': n, 's': s} for n, s in ((3, 'c'), (1, 'a'), (2, 'b'), (1, 'z'), (3, 'a'))]}
    assert sel.evaluate('JOIN(MAP(SORT_BY(L, _["n"]), _["s"]), ",")', data).scalar == 'a,z,b,c,a'
    assert sel.evaluate('JOIN(MAP(SORT_BY(L, _["n"], "DESC"), _["s"]), ",")', data).scalar == 'c,a,b,a,z'
    # A body that reads _K sees each element's own key.
    keyed = {'R': {'b': 2, 'a': 1, 'c': 3}}
    assert sel.evaluate('JOIN(MAP(SORT_BY(R, _K, "DESC"), _), ",")', keyed).scalar == '3,2,1'
    assert sel.evaluate('JOIN(MAP(SORT_BY(R, _K), _), ",")', keyed).scalar == '1,2,3'


def test_sort_by_leaves_no_frame_behind_when_its_key_body_raises():
    ctx = Value.from_native({'L': [1, 2, 0]})
    with pytest.raises(SelError) as info:
        sel.compile('SORT_BY(L, X, 1 / X)').run(ctx)
    assert info.value.code == 'E_DIV_ZERO'
    # The binder is out of scope again, and the same context still runs programs.
    with pytest.raises(SelError) as info2:
        sel.compile('X').run(ctx)
    assert info2.value.code == 'E_UNDEF_VAR'
    assert sel.compile('SUM(L, _)').run(ctx).scalar == '3'


def test_sort_by_binder_is_not_visible_to_the_next_element_key_through_assignment():
    # A key body that assigns to a variable of its own does not make it survive the pass
    # in the binder slot: each element's key is computed from its own element.
    data = {'L': [3, 1, 2]}
    assert sel.evaluate('JOIN(SORT_BY(L, X, (T = X; T)), ",")', data).scalar == '1,2,3'
