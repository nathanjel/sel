"""PY-P1: SORT / SORT_BY / SORT_DESC / TOP* re-derive both keys per comparison.

    PYTHONPATH=python python3 tools/perf/python/bench_p1_sort.py [n ...]

Workloads (fixed seed): numeric-text keys, non-numeric text keys, mixed-kind keys,
TOP_BY / TOP with small and large limits, SORT of scalars. Each prints a semantic
checksum of the result so before/after runs are comparable.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from harness import bench, fmt, growth, checksum, rng, env
import sel

def rows(n, seed=7):
    r = rng(seed)
    return [{'n': str(r.randrange(10 ** 9)), 's': 'k%09d' % r.randrange(10 ** 9),
             'm': r.choice([str(r.randrange(1000)), 'x%d' % r.randrange(1000), '%d.%d' % (r.randrange(99), r.randrange(99))])}
            for _ in range(n)]

CASES = [
    ('SORT_BY numeric text',   'R .> SORT_BY(_["n"]) .> COUNT()', 'R .> SORT_BY(_["n"]) .> MAP(_["n"]) .> JOIN(",")'),
    ('SORT_BY text',           'R .> SORT_BY(_["s"]) .> COUNT()', 'R .> SORT_BY(_["s"]) .> MAP(_["s"]) .> JOIN(",")'),
    ('SORT_BY DESC text',      'R .> SORT_BY(_["s"], "DESC") .> COUNT()', 'R .> SORT_BY(_["s"], "DESC") .> MAP(_["s"]) .> JOIN(",")'),
    ('SORT_BY mixed kinds',    'R .> SORT_BY(_["m"]) .> COUNT()', 'R .> SORT_BY(_["m"]) .> MAP(_["m"]) .> JOIN(",")'),
    ('TOP_BY numeric 10',      'R .> TOP_BY(_["n"], 10) .> COUNT()', 'R .> TOP_BY(_["n"], 10) .> MAP(_["n"]) .> JOIN(",")'),
    ('TOP_BY numeric 1000',    'R .> TOP_BY(_["n"], 1000) .> COUNT()', 'R .> TOP_BY(_["n"], 1000) .> MAP(_["n"]) .> JOIN(",")'),
    ('MAP baseline',           'R .> MAP(_["n"]) .> COUNT()', None),
]

def main(sizes):
    print(env())
    for name, prog, chk in CASES:
        p = sel.compile(prog)
        for n in sizes:
            data = rows(n)
            def run(p=p, data=data):
                return p.run({'R': data})
            med, lo, hi = bench(run, reps=3, warm=0)
            c = ''
            if chk:
                c = checksum(sel.compile(chk).run({'R': data}).scalar)
            print('%-26s n=%-7d %10s  (min %s max %s)  chk %s' % (name, n, fmt(med), fmt(lo), fmt(hi), c))

if __name__ == '__main__':
    main([int(a) for a in sys.argv[1:]] or [10000, 20000, 40000])
