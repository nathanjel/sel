"""PY-P14: nested-loop LINK re-created the right row's table alias for every pair.
    PYTHONPATH=python python3 tools/perf/python/bench_p14_link_loop.py"""
import random
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, growth, checksum
import sel

rnd = random.Random(14)


def rows(n, key):
    return [{key: rnd.randrange(1000), 'v': rnd.randrange(10)} for _ in range(n)]


def ctx(n, m):
    return {'A': rows(n, 'x'), 'B': rows(m, 'y')}


P_LT = sel.compile('COUNT(LINK(A, B, _1["x"] < _2["y"]))')                 # general path
P_FALSE = sel.compile('COUNT(LINK(A, B, _1["x"] < 0))')                    # predicate does no work per pair
P_NAMED = sel.compile('COUNT(LINK(A, B, a, b, a["x"] < b["y"] AND a["v"] != b["v"]))')
P_LEFT = sel.compile('COUNT(LINK_LEFT(A, B, _1["x"] + 1000 < _2["y"]))')
C500, C1500 = ctx(500, 500), ctx(1500, 1500)
sums = []
for name, p, c in (('LINK < 500x500', P_LT, C500), ('LINK always FALSE 1500x1500', P_FALSE, C1500),
                   ('LINK named AND 500x500', P_NAMED, C500), ('LINK_LEFT 500x500', P_LEFT, C500)):
    report(name, lambda p=p, c=c: p.run(dict(c)), reps=3)
    sums.append(p.run(dict(c)).scalar)
print('checksum', checksum(sums))
def make(n):
    c = ctx(n, n)
    return lambda: P_FALSE.run(dict(c))
growth('LINK always FALSE (n x n)', make, [375, 750, 1500])
