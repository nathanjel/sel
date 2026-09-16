"""F4: safe projection boundaries, including errors hidden by a later write."""
import sqlite3

import pytest

from sel import Value, compile
from sel.errors import SelError
from sel.eval import Context, eval_node
from sel.sql import Binding, Sql


BINDINGS = {'R': Binding.relation('r', 'r', {
    'ID': Binding.column('id', 'r', 'NUM'),
    'FK': Binding.column('fk', 'r', 'NUM'),
    'CAT': Binding.column('cat', 'r', 'TEXT'),
})}
ROWS = [dict(id='1', fk='9', cat='ab'), dict(id='2', fk='8', cat='cd')]


@pytest.mark.parametrize('dialect', ['mariadb', 'mysql', 'postgresql', 'sqlite'])
@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('suffix', [
    'MAP(RECORD("x", _["id"], "middle", 7, "x", _["fk"]))',
    'MAP(RECORD("x", _["id"], "x", _["fk"])) .> FILTER(_["x"] > 5)',
    'MAP(RECORD("x", _["id"], "x", _["fk"])) .> TAKE(1) .> FILTER(_["x"] > 5)',
    'MAP(RECORD("x", _["id"], "x", _["fk"])) .> SORT_BY(_["x"]) .> MAP(_["x"])',
    'MAP(RECORD("x", BACKWARDS(_["cat"]), "x", _["id"]))',
    'MAP(RECORD("x", _["id"], "x", BACKWARDS(_["cat"])))',
    'MAP(RECORD("x", _["id"], "X", _["fk"]))',
    'MAP(RR, RECORD("x", RR["id"], "x", RR["fk"]))',
    'BUCKET(_["cat"], RECORD("x", _K, "x", COUNT(_)))',
    'BUCKET(RECORD("x", _["id"], "x", _["fk"]), COUNT(_))',
])
def test_record_collision_retains_local_semantics(dialect, strict, suffix):
    p = compile('R .> ' + suffix)
    ctx = Value.from_native({'R': ROWS})
    expected = eval_node(p.ast, Context(ctx.clone())).dump()
    assert p.run(ctx.clone()).dump() == expected
    plan = Sql.plan_hybrid(p, dialect, BINDINGS, {'strict': strict})
    assert plan.pure_memory

    def no_sql(*_):
        pytest.fail('duplicate RECORD must not become driver-dependent columns')

    assert Sql.execute_hybrid(plan, no_sql, ctx).dump() == expected


@pytest.mark.parametrize('dialect', ['mariadb', 'mysql', 'postgresql', 'sqlite'])
@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('bad,code', [
    ('1 / 0', 'E_DIV_ZERO'),
    ('_["missing"]', 'E_NO_KEY'),
    ('BACKWARDS(TRUE)', 'E_NOT_TEXT'),
])
def test_overwritten_values_still_raise(dialect, strict, bad, code):
    p = compile(f'R .> MAP(RECORD("x", {bad}, "x", 9))')
    ctx = Value.from_native({'R': ROWS})
    plan = Sql.plan_hybrid(p, dialect, BINDINGS, {'strict': strict})
    assert plan.pure_memory
    for run in [lambda: eval_node(p.ast, Context(ctx.clone())),
                lambda: p.run(ctx.clone()),
                lambda: Sql.execute_hybrid(plan, lambda *_: pytest.fail('unsafe SQL'), ctx.clone())]:
        with pytest.raises(SelError) as e:
            run()
        assert e.value.code == code


@pytest.mark.parametrize('strict', [False, True])
def test_live_sqlite_prefix_has_no_duplicate_columns_or_orphan_parameters(strict):
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE r(id INTEGER, fk INTEGER, cat TEXT)')
    con.executemany('INSERT INTO r VALUES (:id, :fk, :cat)', ROWS)
    p = compile('R .> FILTER(_["cat"] $== "ab") .> TAKE(1)'
                ' .> MAP(RECORD("x", "discarded parameter", "middle", 7, "x", _["fk"]))')
    plan = Sql.plan_hybrid(p, 'sqlite', BINDINGS, {'strict': strict})
    assert not plan.pure_memory and not plan.pure_sql
    calls = []

    def run_sql(sql, params):
        cur = con.execute(sql, [v.as_text() for v in params])
        names = [c[0] for c in cur.description]
        assert len(names) == len(set(names)) == 3
        assert 'discarded parameter' not in sql
        result = [dict(zip(names, [str(v) for v in row])) for row in cur]
        calls.append(result)
        return Value.from_native(result)

    ctx = Value.from_native({'R': ROWS})
    assert Sql.execute_hybrid(plan, run_sql, ctx.clone()).dump() == p.run(ctx).dump()
    assert len(calls) == 1 and len(calls[0]) == 1
    f = plan.sql_statement
    assert run_sql(f.as_statement(), []).dump() == run_sql(f.as_statement('params'), f.bindings()).dump()
    con.close()
