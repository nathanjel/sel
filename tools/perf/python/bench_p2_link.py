"""PY-P2: every LINK call exec's freshly generated source per (left shape, right
shape) pair; nested LINKs and heterogeneous rows paid a 40-150x overhead.

    PYTHONPATH=python python3 tools/perf/python/bench_p2_link.py [n ...]

Workloads (fixed seeds): (a) a LINK inside a MAP body (one LINK call per outer row),
(b) one distinct record layout per row, (c) 300 layouts (the shape-cache thrash),
(d) the one-LINK control the others are compared with.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from harness import bench, fmt, checksum, rng, env
import sel

def nested(n):
    r = rng(3)
    a = [{'id': i, 'x': r.randrange(7)} for i in range(n)]
    b = [{'bid': j, 'w': str(j)} for j in range(7)]
    return {'A': a, 'B': b}

def layouts(n, distinct):
    r = rng(5)
    a = []
    for i in range(n):
        k = i % distinct
        a.append({'f%d' % k: i, 'x': r.randrange(5), 'g%d' % (k % 7): 'v'})
    b = [{'bid': j, 'w': str(j)} for j in range(5)]
    return {'A': a, 'B': b}

CASES = [
    ('nested LINK in MAP', 'A .> MAP(LINK(LIST(_), B, _1["x"] == _2["bid"]) .> COUNT()) .> SUM(_)',
     lambda n: nested(n)),
    ('one LINK (control)', 'LINK(A, B, _1["x"] == _2["bid"]) .> COUNT()', lambda n: nested(n)),
    ('distinct layout per row', 'LINK(A, B, _1["x"] == _2["bid"]) .> COUNT()',
     lambda n: layouts(n, n)),
    ('300 layouts', 'LINK(A, B, _1["x"] == _2["bid"]) .> COUNT()', lambda n: layouts(n, 300)),
    ('100 layouts', 'LINK(A, B, _1["x"] == _2["bid"]) .> COUNT()', lambda n: layouts(n, 100)),
]

def main(sizes):
    print(env())
    for name, prog, mk in CASES:
        p = sel.compile(prog)
        for n in sizes:
            data = mk(n)
            med, lo, hi = bench(lambda: p.run(data), reps=3, warm=0)
            print('%-26s n=%-6d %10s  (min %s max %s)  chk %s' % (
                name, n, fmt(med), fmt(lo), fmt(hi), checksum(p.run(data).scalar)))

if __name__ == '__main__':
    main([int(a) for a in sys.argv[1:]] or [750, 1500, 3000])
