"""PY-P3: SQL pairwise folds of long operand lists were quadratic (every step
re-copied the accumulated part list).

    PYTHONPATH=python python3 tools/perf/python/bench_p3_fold.py [n ...]

Workloads (mariadb, inline and params rendering): T IN (n literals), ANY over a
literal list, SUM over a list, a value-binding allow-list. Checksum = sha of the
rendered SQL, so the bytes are compared before/after.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from harness import bench, fmt, checksum, env
import sel
from sel.sql import Binding, Sql

def lit_list(n):
    return '(' + ', '.join('"v%d"' % i for i in range(n)) + ')'

def programs(n):
    L = lit_list(n)
    return [
        ('T IN literals',   'T IN ' + L, {'T': Binding.column('t', 'tbl', 'TEXT')}),
        ('ANY over literals', 'ANY(%s, x, T == x)' % L, {'T': Binding.column('t', 'tbl', 'TEXT')}),
        ('SUM over numbers',  'SUM((%s), x, x)' % ', '.join(str(i) for i in range(n)), {}),
    ]

def main(sizes):
    print(env())
    for n in sizes:
        for name, src, b in programs(n):
            prog = sel.compile(src)
            def go(prog=prog, b=b):
                f = Sql.translate(prog, 'mariadb', b)
                return f.as_condition('params') if 'IN' in src or 'ANY' in src else f.as_value('params')
            med, lo, hi = bench(go, reps=3, warm=0)
            print('%-20s n=%-6d %10s  (min %s max %s)  chk %s' % (name, n, fmt(med), fmt(lo), fmt(hi), checksum(go())))

if __name__ == '__main__':
    main([int(a) for a in sys.argv[1:]] or [500, 1000, 2000, 4000, 8000])
