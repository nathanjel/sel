"""PY-P26: DISTINCT/DEDUPE/BUCKET identify a childless value by (kind, text) with a plain
dict/set, and first_collection_item reads one element instead of building them all.
Both must agree with the EQL-based definition (spec 5.4) on every input."""
import random

import sel
from sel import Value
from sel.builtins import structure


def values(rnd, n):
    pool = []
    for _ in range(n):
        pick = rnd.randrange(9)
        if pick == 0:
            pool.append(Value.int(rnd.randrange(0, 6)))
        elif pick == 1:
            pool.append(Value.from_native(rnd.choice(['1', '1.0', '1.00', '01', '-0', '0', 'a', 'A', ''])))
        elif pick == 2:
            pool.append(Value.bool(rnd.random() < .5))
        elif pick == 3:
            pool.append(Value.from_native(None))
        elif pick == 4:
            pool.append(Value.from_native([]))
        elif pick == 5:
            pool.append(Value.bin(bytes([rnd.randrange(0, 3)])))
        elif pick == 6:
            pool.append(Value.from_native({'a': rnd.randrange(0, 3), 'b': 'x'}))
        elif pick == 7:
            pool.append(Value.from_native([rnd.randrange(0, 3), rnd.randrange(0, 2)]))
        else:
            pool.append(Value.from_native(rnd.choice(['t', 'u', 'tt'])))
    return pool


def naive_distinct(items):
    out = []
    for it in items:
        if not any(it.eql(o) for o in out):
            out.append(it)
    return out


def test_dedupe_equals_the_eql_definition():
    rnd = random.Random(2626)
    for _ in range(300):
        items = values(rnd, rnd.randrange(0, 30))
        ctx = Value.from_native({})
        ctx.set('L', Value._list_owned(items))
        for fn in ('DISTINCT', 'DEDUPE'):
            got = sel.compile(fn + '(L)').run(ctx)
            want = naive_distinct(items)
            assert [v.dump() for _, v in sel.value.elements(got)] == [v.dump() for v in want], (fn, [v.dump() for v in items])


def test_dedupe_distinguishes_spellings_kinds_and_keeps_first_seen_order():
    assert sel.evaluate('COUNT(DISTINCT(LIST(1, 1.0, 1.00, "1", "1.0", 1)))').scalar == '3'
    assert sel.evaluate('COUNT(DISTINCT(LIST(NULL, LIST(), NULL)))').scalar == '1'
    assert sel.evaluate('JOIN(DISTINCT(LIST("b", "a", "b", "c", "a")), ",")').scalar == 'b,a,c'
    assert sel.evaluate('COUNT(DISTINCT(LIST(TRUE, FALSE, TRUE, "TRUE")))').scalar == '3'
    assert sel.evaluate('COUNT(DISTINCT(LIST(FROM_HEX("61"), "a", FROM_HEX("61"))))').scalar == '2'


def test_bucket_with_a_projection_groups_by_eql():
    rnd = random.Random(2627)
    for _ in range(200):
        items = values(rnd, rnd.randrange(1, 25))
        ctx = Value.from_native({})
        ctx.set('L', Value._list_owned(items))
        # Keys that are lists/records/NULL are refused by BUCKET's own rules; use scalar
        # projections of every item: the item's scalar text, through a wrapper.
        usable = [v for v in items if v.size() == 0 and v.kind != 'NONE' and v.kind != 'BOOL']
        ctx.set('L', Value._list_owned(usable))
        if not usable:
            continue
        got = sel.compile('BUCKET(L, _, COUNT(_))').run(ctx)
        groups = []
        for v in usable:
            for g in groups:
                if g[0].eql(v):
                    g[1] += 1
                    break
            else:
                groups.append([v, 1])
        assert [c.as_text(None) for _, c in sel.value.elements(got)] == [str(g[1]) for g in groups]


def test_first_collection_item_reads_the_first_element_only():
    f = structure.first_collection_item
    rec = Value.from_native({'a': 1, 'b': 2})
    rec.set('c', Value.int(3))                        # an irregular, dict-mode record
    assert f(rec).dump() == Value.int(1).dump()
    assert f(Value.from_native([7, 8])).dump() == Value.int(7).dump()
    assert f(Value.from_native(None)) is None
    scalar = Value.from_native('x')
    assert f(scalar) is scalar
    empty_list = Value.from_native([])
    assert f(empty_list) is None or f(empty_list) is empty_list       # as before: NULL-like


def test_link_over_irregular_rows_is_unchanged():
    left = [{'k': i % 5, 'x': i} for i in range(20)]
    for r in left[::3]:
        r['extra'] = 1
    right = [{'k': i % 5, 'y': i} for i in range(10)]
    ctx = Value.from_native({'A': left, 'B': right})
    assert sel.compile('COUNT(LINK(A, B, _1["k"] == _2["k"]))').run(ctx).scalar == '40'
