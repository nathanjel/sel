"""PY-P9: constant folding re-parsed and re-formatted literals and dropped the decoded
Dec, so a folded literal was re-parsed on every evaluation.

    PYTHONPATH=python python3 tools/perf/python/bench_p9_fold.py

(a) per-evaluation cost of `A > 3`, `A > 1+2`, `A > 1.5*2`, `A > 3.0`;
(b) compile + first run of a rule that folds two 30,000-digit literals.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from harness import bench, fmt, checksum, env
import sel

def main():
    print(env())
    for src in ('A > 3', 'A > 1 + 2', 'A > 1.5 * 2', 'A > 3.0', 'A * 2 + (3 - 1) > 10 - 4'):
        p = sel.compile(src); p.physical_ast()
        ctx = {'A': '4'}
        med, lo, hi = bench(lambda: p.run(dict(ctx)), reps=7, warm=1, inner=5000)
        print('%-28s %10s  (min %s max %s)  chk %s' % (src, fmt(med), fmt(lo), fmt(hi), checksum(p.run(dict(ctx)).dump())))
    big = '9' * 30000
    src = 'A > %s + %s' % (big, big)
    def cold():
        p = sel.compile(src); return p.run({'A': '1'}).dump()
    med, lo, hi = bench(cold, reps=3, warm=0)
    print('%-28s %10s  (min %s max %s)  chk %s' % ('fold two 30k-digit literals', fmt(med), fmt(lo), fmt(hi), checksum(cold())))

if __name__ == '__main__':
    main()
