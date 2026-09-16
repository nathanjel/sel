"""F6: transferred full rows, nested values, ordering and conservative bounds."""
import sqlite3

import pytest

from sel import Value, compile
from sel.errors import SelError
from sel.sql import Binding, Sql
from sel.sql.errors import SqlError


def binding(table='r', unique=True, key_kind='TEXT'):
    b = Binding.relation(table, fields={
        'ID': Binding.column('id', type='NUM'),
        'FK': Binding.column('fk', type=key_kind),
        'CAT': Binding.column('cat', type='TEXT'),
    })
    return {'R': b.with_unique_key('id') if unique else b}


PROJECTION = 'RECORD("entity", _K, "latest", TOP_BY(_, _["id"], "DESC", 1))'
SOURCE = 'R .> BUCKET(_["fk"]) .> MAP(' + PROJECTION + ')'


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('table', ['r', '_SEL_INPUT', '_sel_latest'])
@pytest.mark.parametrize('projected', [False, True])
@pytest.mark.parametrize('filtered', [False, True])
@pytest.mark.parametrize('empty', [False, True])
def test_live_latest_members(strict, table, projected, filtered, empty):
    con = sqlite3.connect(':memory:')
    con.execute(f'CREATE TABLE "{table}"(id INTEGER PRIMARY KEY, fk TEXT COLLATE NOCASE, cat TEXT, payload TEXT)')
    # Interleaving makes latest-revision order differ from first-group order.
    rows = [(1, 'a', 'keep', 'first'), (2, 'A', 'keep', 'upper'),
            (3, 'a ', 'keep', 'space'), (4, 'a', 'keep', 'new'),
            (5, 'A', 'drop', 'excluded')]
    if not empty:
        con.executemany(f'INSERT INTO "{table}" VALUES (?,?,?,?)', rows)
    context_rows = [dict(zip(('id', 'fk', 'cat', 'payload'), map(str, r))) for r in ([] if empty else rows)]
    ctx = Value.from_native({'R': context_rows})
    prefix = 'R .> SORT_BY(_["id"])' + (' .> FILTER(_["cat"] $== "keep")' if filtered else '')
    source = prefix + (' .> BUCKET(_["fk"], ' + PROJECTION + ')' if projected else ' .> BUCKET(_["fk"]) .> MAP(' + PROJECTION + ')')
    p = compile(source)
    plan = Sql.plan_hybrid(p, 'sqlite', binding(table), {'strict': strict})
    assert plan.is_hybrid and plan.selected_member == {'partition_key': 'fk', 'revision_key': 'id'}
    transferred = []

    def run_sql(sql, params):
        cursor = con.execute(sql, [v.as_text() for v in params])
        columns = [c[0] for c in cursor.description]
        assert columns == ['id', 'fk', 'cat', 'payload']  # undeclared payload survives
        result = [dict(zip(columns, map(str, r))) for r in cursor]
        assert len(result) == (0 if empty else 3)
        transferred.append(result)
        return Value.from_native(result)

    expected = p.run(ctx.clone()).dump()
    assert Sql.execute_hybrid(plan, run_sql, ctx).dump() == expected
    f = plan.sql_statement
    assert run_sql(f.as_statement(), []).dump() == run_sql(f.as_statement('params'), f.bindings()).dump()
    assert len(transferred) == 3
    con.close()


@pytest.mark.parametrize('dialect', ['sqlite', 'postgresql', 'mariadb', 'mysql'])
@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('source', [
    SOURCE.replace('"DESC", 1', '"DESC", 0'),
    SOURCE.replace('"DESC", 1', '"DESC", 2'),
    SOURCE.replace('"DESC", 1', '"ASC", 1'),
    SOURCE.replace('BUCKET(_["fk"])', 'BUCKET(_["fk"] + 0)'),
    SOURCE.replace('_["id"], "DESC"', '_["id"] + 0, "DESC"'),
    SOURCE.replace('R .>', 'R .> TAKE(2) .>'),
    SOURCE.replace('R .>', 'R .> SORT_BY(_["id"], "DESC") .>'),
    'R .> BUCKET(LIST(_["fk"], _["cat"]), ' + PROJECTION + ')',
    SOURCE.replace('"entity", _K', '"entity", COUNT(_)'),
])
def test_unproven_shapes_do_not_preselect(dialect, strict, source):
    plan = Sql.plan_hybrid(compile(source), dialect, binding(), {'strict': strict})
    assert plan.selected_member is None


def test_unique_key_is_a_validated_copy_and_not_inferred():
    b = binding(unique=False)['R']
    c = b.with_unique_key('id')
    assert 'unique_key' not in b.spec and c.spec['unique_key'] == 'id'
    assert Sql.plan_hybrid(compile(SOURCE), 'sqlite', {'R': b}).selected_member is None
    for key in ['', 'missing', 42, None]:
        with pytest.raises(SqlError) as e:
            b.with_unique_key(key)
        assert e.value.code == 'E_SQL_BINDING'
    with pytest.raises(SqlError):
        Binding.column('id').with_unique_key('id')


def test_nullable_partition_still_raises_in_continuation():
    p = compile(SOURCE)
    plan = Sql.plan_hybrid(p, 'sqlite', binding())
    assert plan.selected_member is not None
    with pytest.raises(SelError) as e:
        Sql.execute_hybrid(plan, lambda *_: Value.from_native([{'id': '2', 'fk': None}]))
    assert e.value.code == 'E_NULL'


def test_latest_member_scale_transfers_groups_not_history():
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE r(id INTEGER PRIMARY KEY, fk INTEGER, cat TEXT, payload TEXT)')
    con.executemany('INSERT INTO r VALUES (?,?,?,?)', ((i, (i - 1) % 100, 'weight', str(i)) for i in range(1, 100001)))
    con.execute('CREATE INDEX entity_revision ON r(fk,id)')
    p = compile(SOURCE)
    plan = Sql.plan_hybrid(p, 'sqlite', binding(key_kind='NUM'))
    assert plan.selected_member is not None

    def run_sql(sql, params):
        cursor = con.execute(sql, [v.as_text() for v in params])
        result = [dict(zip(('id', 'fk', 'cat', 'payload'), map(str, r))) for r in cursor]
        assert len(result) == 100
        assert sorted(int(r['id']) for r in result) == list(range(99901, 100001))
        return Value.from_native(result)

    value = Sql.execute_hybrid(plan, run_sql)
    assert value.size() == 100
    con.close()
