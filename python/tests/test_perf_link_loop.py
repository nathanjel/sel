"""PY-P14: the nested-loop LINK makes each right row's table alias once, not once
per pair. The joined rows must be exactly what the per-pair version made."""
import random

import sel


def brute(left, right, pred, left_join=False):
    rows = []
    for l in left:
        hit = False
        for r in right:
            if pred(l, r):
                hit = True
                rows.append((l, r))
        if left_join and not hit:
            rows.append((l, None))
    return rows


def test_general_join_matches_a_brute_force_reference():
    rnd = random.Random(14)
    for _ in range(40):
        a = [{'x': str(rnd.randrange(6)), 'v': str(rnd.randrange(3))} for _ in range(rnd.randrange(0, 8))]
        b = [{'y': str(rnd.randrange(6)), 'v': str(rnd.randrange(3))} for _ in range(rnd.randrange(0, 8))]
        ctx = {'A': a, 'B': b}
        want = brute(a, b, lambda l, r: int(l['x']) < int(r['y']) and l['v'] != r['v'])
        got = sel.evaluate('COUNT(LINK(A, B, l, r, l["x"] < r["y"] AND l["v"] != r["v"]))', ctx).scalar
        assert int(got) == len(want)
        want_left = brute(a, b, lambda l, r: int(l['x']) + 3 < int(r['y']), True)
        got_left = sel.evaluate('COUNT(LINK_LEFT(A, B, l, r, l["x"] + 3 < r["y"]))', ctx).scalar
        assert int(got_left) == len(want_left)


def test_joined_rows_carry_both_named_sides():
    ctx = {'A': [{'x': '1'}, {'x': '2'}], 'B': [{'y': '5'}, {'y': '0'}]}
    r = sel.evaluate('LINK(A, B, l, r, l["x"] < r["y"])', ctx)
    assert r.size() == 2
    row = r.values()[0]
    assert row.get('l').get('x').scalar == '1' and row.get('r').get('y').scalar == '5'


def test_an_empty_left_side_does_no_alias_work_and_is_fine():
    assert sel.evaluate('COUNT(LINK(A, B, l, r, l["x"] < r["y"]))', {'A': [], 'B': [{'y': '1'}]}).scalar == '0'
    assert sel.evaluate('COUNT(LINK_LEFT(A, B, l, r, l["x"] < r["y"]))', {'A': [{'x': '1'}], 'B': []}).scalar == '1'
