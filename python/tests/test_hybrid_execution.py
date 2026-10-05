"""Hybrid execution against a real SQLite database, compared with run().

A helper that rebinds a relation under its own name (`ORDERS = ORDERS .>
DROP(2)`) is unwound into the pipeline once. A later step that reads the name
as a value still sees the helper, and the SQL prefix applies DROP once, so the
hybrid answer is run()'s, row for row (JS is the reference: the unwound source
is marked as a read of the binding).

And whatever the plan's shape, an application function that mutates its
argument never writes the caller's context: directly, under IF, or inside an
aggregate body, in a pure-memory plan, a split, and a MAP fallthrough."""
import sqlite3

import pytest

import sel
from sel.sql import Binding, hybrid

BINDINGS = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}


@pytest.fixture(scope='module')
def runner():
    def poke(a):
        v = a.val(0)
        v.set('k', sel.Value.text('9'))
        return v
    sel.register_function('HYBRID_HELPERS_ID', 1, 1, lambda a: a.val(0))
    sel.register_function('HYBRID_POKE', 1, 1, poke)
    db = sqlite3.connect(':memory:')
    db.execute('create table orders (id integer)')
    db.executemany('insert into orders values (?)', [(i,) for i in range(1, 7)])

    def run(sql, params):
        cur = db.execute(sql, [p.as_text() for p in params])
        cols = [d[0] for d in cur.description]
        return sel.Value.from_native([{c: str(v) for c, v in zip(cols, r)} for r in cur.fetchall()])
    yield run
    for name in ('HYBRID_HELPERS_ID', 'HYBRID_POKE'):
        sel.registry._table.pop(name, None)
        sel.registry._host.discard(name)


@pytest.mark.parametrize('src, kind', [
    ('ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3) .> '
     'MAP(RECORD("n", COUNT(ORDERS), "x", HYBRID_HELPERS_ID(_["id"])))', 'hybrid'),
    ('ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3) .> MAP(RECORD("n", COUNT(ORDERS), "x", _["id"]))',
     'hybrid'),
    ('ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3) .> MAP(RECORD("x", HYBRID_HELPERS_ID(_["id"])))',
     'hybrid'),
    ('ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)', 'pure_sql'),
])
def test_a_reread_rebinding_helper_answers_as_run(runner, src, kind):
    program = sel.compile(src)
    plan = hybrid.plan_hybrid(program, 'sqlite', BINDINGS)
    assert plan.kind == kind
    assert 'LIMIT 3 OFFSET 2' in plan.sql_statement.as_statement()
    def orders():
        return sel.Value.from_native({'ORDERS': [{'id': str(i)} for i in range(1, 7)]})
    ctx = orders()
    got = hybrid.execute_hybrid(plan, runner, ctx)
    assert ctx.get('ORDERS').size() == 6          # the caller's context is not written
    assert got.dump() == program.run(orders()).dump()


def test_the_marked_read_is_the_planners_copy():
    program = sel.compile('ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3) .> MAP(COUNT(ORDERS))')
    hybrid.plan_hybrid(program, 'sqlite', BINDINGS)
    stack = [program.ast]
    while stack:
        n = stack.pop()
        if n is not None:
            assert not n.binding
            stack.extend((*n.args, *n.items, n.l, n.r, n.x, n.obj, n.idx, n.target, n.value))


@pytest.mark.parametrize('body', ['HYBRID_POKE(A)', 'IF(TRUE, HYBRID_POKE(A), 0)',
                                  'MAP(LIST(1), HYBRID_POKE(A))'])
@pytest.mark.parametrize('shape, kind', [
    ('{}', 'pure_memory'),
    ('ORDERS .> SORT_BY(_["id"]) .> MAP({})', 'hybrid'),
    ('ORDERS .> SORT_BY(_["id"]) .> MAP(RECORD("a", _["id"], "b", {}))', 'hybrid'),
    ('ORDERS = ORDERS .> DROP(1); ORDERS .> MAP({})', 'hybrid'),
])
def test_an_application_function_never_writes_the_callers_context(runner, body, shape, kind):
    plan = hybrid.plan_hybrid(sel.compile(shape.format(body)), 'sqlite', BINDINGS)
    assert plan.kind == kind
    ctx = sel.Value.from_native({'A': {'k': '1'}, 'ORDERS': [{'id': '1'}]})
    hybrid.execute_hybrid(plan, runner, ctx)
    assert ctx.dump() == '-{"A"=-{"k"=t"1"}, "ORDERS"=-{"1"=-{"id"=t"1"}}}'
