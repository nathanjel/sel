"""PY-P21: `_contains_unsupported_sql` did not consult the `ops` table, so a MAP whose
custom half holds a withdrawn operator (BAND/BOR/BXOR have no SQL spelling) lost the MAP
fall-through and moved every column instead of projecting the ones SQL can carry.
    PYTHONPATH=python python3 tools/perf/python/bench_p21_unsupported_ops.py"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
from sel import compile
from sel.sql import Sql
from sel.sql.binding import Binding

B = {'R': Binding.relation('r', 'r', {
    'ID': Binding.column('id', 'r', 'NUM'),
    'NAME': Binding.column('name', 'r', 'TEXT'),
    'PAD': Binding.column('pad', 'r', 'TEXT'),
})}
HEAD = 'R .> SORT_BY(_["id"]) .> MAP(RECORD("id", _["id"], "n", _["name"], "b", '
PROGRAMS = {
    'band': HEAD + 'TO_HEX(FROM_HEX("0f") BAND FROM_HEX("3c"))))',
    'bor': HEAD + 'TO_HEX(FROM_HEX("0f") BOR FROM_HEX("3c"))))',
    'bxor': HEAD + 'TO_HEX(FROM_HEX("0f") BXOR FROM_HEX("3c"))))',
    'padl (control)': HEAD + 'PADL(_["name"], 8, "0")))',
}
out = []
for dialect in ('postgresql', 'mariadb', 'sqlite'):
    for name, src in PROGRAMS.items():
        plan = Sql.plan_hybrid(compile(src), dialect, B, {'strict': False})
        sql = plan.sql_query.as_statement() if plan.sql_query is not None else None
        out.append((dialect, name, plan.pure_sql, plan.pure_memory, sql))
        cols = 'all columns (r.*)' if sql and '.*' in sql else 'projected'
        print('%-11s %-15s pure_sql=%-5s memory=%-5s %s' % (dialect, name, plan.pure_sql, plan.pure_memory, cols))
        if sql:
            print('    ', sql[:140])
print('checksum', checksum(out))
report('plan_hybrid band pipeline', lambda: Sql.plan_hybrid(compile(PROGRAMS['band']), 'postgresql', B, {'strict': False}), reps=5)
