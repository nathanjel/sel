"""PY-P1: sorts and selections derive each key once (sel/builtins/aggregate.sort_key).

The precomputed native-comparable key must order exactly as the pairwise
comparator (compare_values, SPEC 7.3) does -- including ties, which keep input
order in both directions -- for every kind, numbers of different scales and
records ranked by their first field."""
import random
from functools import cmp_to_key

import pytest
import sel
from sel import Value
from sel.builtins.aggregate import compare_values, sort_key, _DecKey


def pool(r):
    kinds = [
        lambda: str(r.randrange(50)),
        lambda: '%d.%d' % (r.randrange(10), r.randrange(100)),
        lambda: '%d.%d0' % (r.randrange(10), r.randrange(10)),
        lambda: '-%d' % r.randrange(20),
        lambda: '%03d' % r.randrange(20),
        lambda: r.choice(['x', 'Xa', 'é', 'zz', 'a1', '1a', '', ' 2', '😀']),
        lambda: True,
        lambda: False,
        lambda: None,
        lambda: {'f': str(r.randrange(30)), 'g': 1},
        lambda: {'f': r.choice(['b', 'a', 'c'])},
        lambda: {'f': True},
    ]
    return r.choice(kinds)()


def reference(values, direction):
    idx = list(range(len(values)))

    def cmp(i, j):
        c = compare_values(values[i], values[j])
        if direction == 'DESC':
            c = -c
        return c if c != 0 else i - j

    return sorted(idx, key=cmp_to_key(cmp))


@pytest.mark.parametrize('seed', range(40))
@pytest.mark.parametrize('direction', ['ASC', 'DESC'])
def test_key_order_equals_the_pairwise_comparator(seed, direction):
    r = random.Random(seed)
    natives = [pool(r) for _ in range(r.randrange(1, 40))]
    values = [Value.from_native(n) for n in natives]
    keys = [sort_key(v) for v in values]
    got = sorted(range(len(values)), key=keys.__getitem__, reverse=(direction == 'DESC'))
    assert got == reference(values, direction)


@pytest.mark.parametrize('seed', range(25))
def test_sort_and_top_agree_with_the_reference_through_the_language(seed):
    r = random.Random(1000 + seed)
    natives = [pool(r) for _ in range(r.randrange(1, 30))]
    values = [Value.from_native(n) for n in natives]
    for fn, direction in (('SORT', 'ASC'), ('SORT_DESC', 'DESC')):
        out = sel.compile(f'{fn}(L)').run({'L': natives})
        want = [values[i].dump() for i in reference(values, direction)]
        assert [c.dump() for _, c in out.entries()] == want
    n = r.randrange(0, len(values) + 3)
    for fn, direction in (('TOP', 'ASC'), ('TOP_DESC', 'DESC')):
        out = sel.compile(f'{fn}(L, {n})').run({'L': natives})
        want = [values[i].dump() for i in reference(values, direction)][:n]
        assert [c.dump() for _, c in out.entries()] == want


def test_keys_of_equal_numbers_tie_whatever_their_spelling():
    a, b, c = (sort_key(Value.from_native(x)) for x in ('7', '7.0', '007'))
    assert a == b == c
    assert not (a < b) and not (b < a)


def test_a_decimal_key_compares_with_a_plain_int_key_both_ways():
    assert _DecKey(15, 1) > 1 and 1 < _DecKey(15, 1)
    assert _DecKey(5, 1) < 1 and 1 > _DecKey(5, 1)
    assert _DecKey(10, 1) == 1 and 1 == _DecKey(10, 1)
    assert _DecKey(-15, 1) < -1
