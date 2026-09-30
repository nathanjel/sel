"""PY-P6: Value.num(dec) re-validated every internal arithmetic result.

    PYTHONPATH=python python3 tools/perf/python/bench_p6_num.py

Micro: Value.num(dec) (public, validating) vs Value._num_owned(dec) where it exists.
End to end: the pure-math plan (A*B+C), the tree walk (A*B+C - A*B*C/B), 100k-row
MAP / FILTER arithmetic. Checksums of the results.
"""
import sys, os, timeit, statistics
sys.path.insert(0, os.path.dirname(__file__))
from harness import bench, fmt, checksum, rng, env
import sel
from sel import Value
from sel import decimal as D

def micro(name, fn, n=300000):
    fn()
    samples = [v * 1e9 / n for v in timeit.repeat(fn, number=n, repeat=5)]
    print('%-34s %8.0f ns  (min %.0f max %.0f)' % (name, statistics.median(samples), min(samples), max(samples)))

def main():
    print(env())
    d = D.parse('1250.75')
    micro('Value.num(dec)   [public]', lambda: Value.num(d))
    if hasattr(Value, '_num_owned'):
        micro('Value._num_owned(dec)', lambda: Value._num_owned(d))
    ctx = {'A': '3.5', 'B': '4', 'C': '7.25'}
    for label, src in (('plan A*B+C', 'A * B + C'),
                       ('plan A*B+C - A*B*C/B', 'A * B + C - A * B * C / B')):
        p = sel.compile(src); p.physical_ast()
        med, _, _ = bench(lambda: p.run(dict(ctx)), reps=7, warm=1, inner=3000)
        print('%-34s %10s   chk %s' % (label + ' (run)', fmt(med), checksum(p.run(dict(ctx)).dump())))
    r = rng(13)
    rows = [{'p': str(r.randrange(100)), 'q': str(r.randrange(100))} for _ in range(100000)]
    for label, src in (('MAP arith 100k', 'ROWS .> MAP(_["p"] * _["q"] + 1) .> SUM(_)'),
                       ('FILTER arith 100k', 'ROWS .> FILTER(_["p"] * 2 > 50) .> COUNT()'),
                       ('MAP ABS/ROUND 100k', 'ROWS .> MAP(ROUND(ABS(_["p"] - _["q"]) / 3, 2)) .> SUM(_)')):
        p = sel.compile(src)
        med, lo, hi = bench(lambda: p.run({'ROWS': rows}), reps=5, warm=1)
        print('%-34s %10s  (min %s max %s)  chk %s' % (label, fmt(med), fmt(lo), fmt(hi), checksum(p.run({'ROWS': rows}).scalar)))

if __name__ == '__main__':
    main()
