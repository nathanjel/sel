"""PY-P25: SUM allocated a Dec per addition; the aggregate walk and do_sort pay per-element
frame/_K/closure costs.
    PYTHONPATH=python python3 tools/perf/python/bench_p25_aggregates.py"""
import random
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
import sel
from sel import Value

rnd = random.Random(25)
rows = [{'n': rnd.randrange(0, 1000), 'p': '%d.%02d' % (rnd.randrange(0, 50), rnd.randrange(0, 100)), 's': 'k%d' % rnd.randrange(0, 500)}
        for _ in range(100_000)]
ctx = Value.from_native({'R': rows, 'L': [r['n'] for r in rows]})
out = []

def prog(name, src, reps=5):
    p = sel.compile(src)
    report(name, lambda: p.run(ctx), reps=reps)
    out.append(p.run(ctx).dump())

prog('SUM(R, _["n"])          100k int rows', 'SUM(R, _["n"])')
prog('SUM(R, _["p"])          100k scale-2 text', 'SUM(R, _["p"])')
prog('SUM(L, _)               100k scalars', 'SUM(L, _)')
prog('COUNT(MAP(R, _["n"]))   100k', 'COUNT(MAP(R, _["n"]))')
prog('COUNT(FILTER(R, _["n"] > 500)) 100k', 'COUNT(FILTER(R, _["n"] > 500))')
prog('ALL(R, _["n"] >= 0)     100k', 'ALL(R, _["n"] >= 0)')
prog('COUNT(SORT_BY(R, _["n"])) 100k', 'COUNT(SORT_BY(R, _["n"]))', reps=3)
prog('COUNT(SORT_BY(R, _["s"])) 100k text', 'COUNT(SORT_BY(R, _["s"]))', reps=3)
prog('COUNT(SORT_BY(R, _K))   100k (body reads _K)', 'COUNT(SORT_BY(R, _K))', reps=3)
# SUM sign / scale / cap corner cases, in the checksum
for src in ('SUM((1.5, -1.5), _)', 'SUM((-2, 0.25, 3), _)', 'SUM((0.10, 0.2, -0.300), _)', 'SUM((1,2,3,4), _ * 1.25)'):
    out.append(sel.evaluate(src).dump())
print('checksum', checksum(out))
