"""A withdrawn operator (BAND/BOR/BXOR) in the custom half of a MAP keeps the MAP
fall-through: the SQL prefix projects the columns it can carry, and the continuation still
answers what run() answers."""
import sqlite3

import pytest

from sel import compile, Value
from sel.sql import Sql
from sel.sql.binding import Binding

BINDINGS = {'R': Binding.relation('r', 'r', {
    'ID': Binding.column('id', 'r', 'NUM'),
    'NAME': Binding.column('name', 'r', 'TEXT'),
    'PAD': Binding.column('pad', 'r', 'TEXT'),
})}
HEAD = 'R .> SORT_BY(_["id"]) .> MAP(RECORD("id", _["id"], "n", _["name"], "b", '
ROWS = [dict(id=str(i), name='n%d' % i, pad='x' * i) for i in range(1, 6)]


@pytest.mark.parametrize('op', ['BAND', 'BOR', 'BXOR'])
@pytest.mark.parametrize('dialect', ['postgresql', 'mariadb', 'sqlite'])
def test_withdrawn_operator_projects_instead_of_moving_every_column(op, dialect):
    program = compile(HEAD + 'TO_HEX(FROM_HEX("0f") %s FROM_HEX("3c"))))' % op)
    plan = Sql.plan_hybrid(program, dialect, BINDINGS, {'strict': False})
    assert plan.sql_query is not None and not plan.pure_memory
    sql = plan.sql_query.as_statement()
    assert '.*' not in sql, sql
    assert '"id"' in sql.replace('`', '"') and '"name"' in sql.replace('`', '"')
    assert 'pad' not in sql


@pytest.mark.parametrize('op', ['BAND', 'BOR', 'BXOR'])
def test_the_hybrid_answer_is_the_evaluators_answer(op):
    program = compile(HEAD + 'TO_HEX(FROM_HEX("0f") %s FROM_HEX("3c"))))' % op)
    context = Value.from_native({'R': ROWS})
    expected = program.run(context.clone()).dump()
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE r(id INTEGER, name TEXT, pad TEXT)')
        db.executemany('INSERT INTO r VALUES (:id, :name, :pad)', ROWS)

        def run_sql(sql, params):
            cur = db.execute(sql, [p.as_text() for p in params])
            names = [c[0] for c in cur.description]
            return Value.from_native([
                dict(zip(names, [str(v) if v is not None else None for v in row]))
                for row in cur.fetchall()])
        plan = Sql.plan_hybrid(program, 'sqlite', BINDINGS, {'strict': False})
        assert Sql.execute_hybrid(plan, run_sql, context.clone()).dump() == expected


def test_a_supported_operator_is_unaffected():
    program = compile(HEAD + 'PADL(_["name"], 8, "0")))')
    plan = Sql.plan_hybrid(program, 'postgresql', BINDINGS, {'strict': False})
    assert plan.pure_sql
