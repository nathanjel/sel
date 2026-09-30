"""PY-P27: plan_hybrid's prefix search and repeated stage 1.
    PYTHONPATH=python python3 tools/perf/python/bench_p27_hybrid_planner.py"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum, growth
from sel import compile
from sel.sql import Sql
from sel.sql.binding import Binding

B = {'R': Binding.relation('r', 'r', {
    'ID': Binding.column('id', 'r', 'NUM'),
    'NAME': Binding.column('name', 'r', 'TEXT'),
})}
out = []


def chain(n):
    # n translatable MAP steps then an untranslatable tail (ABORT has no SQL form)
    steps = ' .> '.join('MAP(RECORD("id", _["id"], "name", _["name"]))' for _ in range(n))
    return compile('R .> ' + steps + ' .> MAP(ABORT("x"))')


def helpers(n):
    src = 'A0 = R .> FILTER(_["id"] > 0); ' + ''.join(
        'A%d = A%d .> TAKE(%d); ' % (i, i - 1, 1000 - i) for i in range(1, n)) + 'A%d .> MAP(ABORT("x"))' % (n - 1)
    return compile(src)


def plan_of(p):
    plan = Sql.plan_hybrid(p, 'postgresql', B, {'strict': False})
    return (plan.pure_sql, plan.pure_memory, plan.sql_query.as_statement() if plan.sql_query is not None else None)


for n in (25, 50, 90, 180, 360):
    p = chain(n)
    report('map chain + untranslatable tail  n=%d' % n, lambda p=p: plan_of(p), reps=3)
    out.append(plan_of(p))
for n in (100, 200, 300, 600):
    p = helpers(n)
    report('helper chain (statements) n=%d' % n, lambda p=p: plan_of(p), reps=3)
    out.append(plan_of(p))
print('checksum', checksum(out))
