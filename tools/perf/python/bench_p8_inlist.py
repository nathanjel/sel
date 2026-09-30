"""PY-P8: constant list operands (`x IN LIST("a", ...)`, `x IN ("a", ...)`) were
rebuilt and cloned for every row.

    PYTHONPATH=python python3 tools/perf/python/bench_p8_inlist.py

20,000 rows; FILTER with IN over 3 / 10 / 200 literals (tuple and LIST spellings),
against the equivalent `$== OR $==` chain and a plain equality. Checksums.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from harness import bench, fmt, checksum, rng, env
import sel

def main():
    print(env())
    r = rng(17)
    rows = [{'a': r.randrange(10), 'b': 'x%d' % r.randrange(400)} for _ in range(20000)]
    lit = lambda k: ', '.join('"x%d"' % (i * 2) for i in range(k))
    cases = [
        ('IN ("x..") 3',         'ROWS .> FILTER(_["b"] IN (%s)) .> COUNT()' % lit(3)),
        ('IN LIST(..) 3',        'ROWS .> FILTER(_["b"] IN LIST(%s)) .> COUNT()' % lit(3)),
        ('IN LIST(..) 10',       'ROWS .> FILTER(_["b"] IN LIST(%s)) .> COUNT()' % lit(10)),
        ('IN LIST(..) 200',      'ROWS .> FILTER(_["b"] IN LIST(%s)) .> COUNT()' % lit(200)),
        ('$== OR chain of 3',    'ROWS .> FILTER(%s) .> COUNT()' % ' OR '.join('_["b"] $== "x%d"' % (i * 2) for i in range(3))),
        ('plain equality',       'ROWS .> FILTER(_["a"] == 3) .> COUNT()'),
    ]
    for name, src in cases:
        p = sel.compile(src)
        med, lo, hi = bench(lambda: p.run({'ROWS': rows}), reps=5, warm=1)
        print('%-22s %10s  (min %s max %s)  chk %s' % (name, fmt(med), fmt(lo), fmt(hi), checksum(p.run({'ROWS': rows}).scalar)))

if __name__ == '__main__':
    main()
