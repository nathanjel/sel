"""F1: compare actual SQLite rows and hybrid results, not only SQL strings."""
import sqlite3

import pytest

from sel import Value, compile
from sel.sql import Binding, Sql, SqlError


@pytest.mark.parametrize('counts', [
    'TAKE(2) .> DROP(1)', 'TAKE(2) .> DROP(2)',
    'TAKE(1) .> DROP(2)', 'TAKE(0) .> DROP(2)',
    'TAKE(2) .> DROP(0)', 'DROP(1) .> TAKE(1)',
    'DROP(1) .> TAKE(2) .> DROP(1) .> TAKE(3)',
    'DROP(4503599627370496) .> DROP(4503599627370496) .> TAKE(1)',
])
@pytest.mark.parametrize('tail', [
    'MAP(RECORD("id", _["id"]))',
    'FILTER(_["id"] > 0) .> MAP(RECORD("id", _["id"]))',
    'BUCKET(_["cat"], RECORD("cat", _K, "n", COUNT(_)))',
])
@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('strict', [False, True])
def test_slice_rows_and_hybrid(counts, tail, empty, strict):
    rows = [] if empty else [dict(id=str(i), cat='a') for i in range(1, 6)]
    bindings = {'R': Binding.relation('r', 'r', {
        'ID': Binding.column('id', 'r', 'NUM'),
        'CAT': Binding.column('cat', 'r', 'TEXT'),
    })}
    program = compile('R .> SORT_BY(_["id"]) .> ' + counts + ' .> ' + tail)
    context = Value.from_native({'R': rows})
    expected = program.run(context.clone()).dump()
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE r(id INTEGER, cat TEXT)')
        db.executemany('INSERT INTO r VALUES (:id, :cat)', rows)

        def run_sql(sql, params):
            cur = db.execute(sql, [p.as_text() for p in params])
            names = [c[0] for c in cur.description]
            return Value.from_native([
                dict(zip(names, [str(v) if v is not None else None for v in row]))
                for row in cur.fetchall()
            ])

        plan = Sql.plan_hybrid(program, 'sqlite', bindings, {'strict': strict})
        assert Sql.execute_hybrid(plan, run_sql, context.clone()).dump() == expected
        # The derived FILTER is an intentional SQLite refusal; the hybrid
        # assertion above still checks its continuation against SEL.
        if 'BUCKET' in tail:
            # A GROUP BY returns its groups in no order, and SEL's are in the order of
            # their first member in the sorted list: the sort's ORDER BY must survive
            # the steps after it (docs/internals/sql-translation.md 12.1, "Order"), and
            # a LIMIT beside it does not change what the GROUP BY does to the groups. So
            # the statement is refused at the BUCKET, and the hybrid plan above (already
            # held to SEL's answer) groups the sorted rows in memory. The fixture has one
            # group, which cannot show the order; the refusal is the contract.
            with pytest.raises(SqlError) as refusal:
                Sql.translate_statement(program, 'sqlite', bindings, {'strict': strict})
            assert refusal.value.code == 'E_SQL_SHAPE'
            assert plan.sql_statement is not None and not plan.pure_sql
        elif 'FILTER' not in tail:
            statement = Sql.translate_statement(program, 'sqlite', bindings, {'strict': strict})
            assert run_sql(statement.as_statement(), []).dump() == expected
            assert run_sql(statement.as_statement('params'), statement.bindings()).dump() == expected
