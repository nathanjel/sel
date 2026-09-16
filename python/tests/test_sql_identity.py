"""Group/DISTINCT identity must survive either SQL or a safe continuation."""
import pytest

from sel import Value, compile
from sel.sql import Binding, Sql


@pytest.mark.parametrize('collation', ['NOCASE', 'RTRIM'])
@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('source', [
    'R .> BUCKET(_["cat"], RECORD("cat", _K, "n", COUNT(_)))',
    'R .> MAP(RECORD("cat", _["cat"])) .> BUCKET(_["cat"], RECORD("cat", _K, "n", COUNT(_)))',
    'R .> TAKE(3) .> BUCKET(_["cat"], RECORD("cat", _K, "n", COUNT(_)))',
    'R .> SELECT_COLS("cat") .> TAKE(3) .> BUCKET(_["cat"], RECORD("cat", _K, "n", COUNT(_)))',
    'R .> MAP(RECORD("cat", _["cat"])) .> DISTINCT()',
    'R .> FILTER(_["cat"] $== "a") .> MAP(RECORD("cat", _["cat"]))',
])
def test_sqlite_schema_collation_cannot_change_identity(collation, strict, source):
    import sqlite3
    db = sqlite3.connect(':memory:')
    try:
        db.execute(f'CREATE TABLE r(cat TEXT COLLATE {collation})')
        db.executemany('INSERT INTO r VALUES (?)', [('a',), ('A',), ('a ',)])
        bindings = {'R': Binding.relation('r', fields={
            'CAT': Binding.column('cat', type='TEXT')})}
        program = compile(source)
        plan = Sql.plan_hybrid(program, 'sqlite', bindings, {'strict': strict})
        assert plan.pure_sql

        def execute(sql, params):
            cursor = db.execute(sql, [value.to_native() for value in params])
            names = [column[0] for column in cursor.description]
            return Value.from_native([dict(zip(names, row)) for row in cursor])

        actual = Sql.execute_hybrid(plan, execute, Value.from_native({}))
        expected = program.run(Value.from_native({'R': [
            {'cat': 'a'}, {'cat': 'A'}, {'cat': 'a '}]}))
        # SQL has no implicit row order; compare exact records, not position.
        assert sorted(v.dump() for v in actual.values()) == sorted(
            v.dump() for v in expected.values())
    finally:
        db.close()


@pytest.mark.parametrize('dialect', ['mariadb', 'mysql', 'postgresql', 'sqlite'])
@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('source', [
    'R .> DISTINCT()',
    'R .> SELECT_COLS("cat") .> DISTINCT()',
    'R .> MAP(RECORD("cat", _["cat"])) .> DISTINCT()',
])
def test_unproven_distinct_identity_stays_local(dialect, strict, source):
    # CAT is deliberately UNKNOWN, and SECRET is not declared in the binding.
    rows = [dict(cat='a', secret='1'), dict(cat='a ', secret='2'),
            dict(cat='a', secret='3')]
    binding = {'R': Binding.relation('r', 'r', {'CAT': Binding.column('cat', 'r')})}
    context = Value.from_native({'R': rows})
    program = compile(source)
    plan = Sql.plan_hybrid(program, dialect, binding, {'strict': strict})
    assert not plan.pure_sql
    if source == 'R .> DISTINCT()':
        assert plan.pure_memory

    def safe_projection(sql, params):
        assert 'DISTINCT' not in sql
        assert source != 'R .> DISTINCT()'
        # Projection is safe to push; it must retain all rows for local DISTINCT.
        return Value.from_native([{'cat': r['cat']} for r in rows])

    assert Sql.execute_hybrid(plan, safe_projection, context.clone()).dump() == program.run(context).dump()


@pytest.mark.parametrize('dialect', ['mariadb', 'mysql', 'postgresql', 'sqlite'])
@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('suffix', [
    'BUCKET(_["cat"] + 0, RECORD("key", _K, "n", COUNT(_)))',
    'BUCKET(_["cat"] + 0) .> MAP(RECORD("key", _K, "n", COUNT(_)))',
    'BUCKET(LIST(_["cat"] + 0, "constant"), RECORD("key", _K, "n", COUNT(_)))',
    'BUCKET(_["cat"] + 0) .> FILTER(LEN(_K) > 1) .> MAP(RECORD("key", _K, "n", COUNT(_)))',
    'MAP(RECORD("key", _["cat"] + 0)) .> BUCKET(_["key"], RECORD("key", _K, "n", COUNT(_)))',
    'MAP(RECORD("key", _["cat"] + 0)) .> DISTINCT()',
])
def test_computed_numeric_groups_fall_back_exactly(dialect, strict, suffix):
    rows = [dict(cat=v) for v in ['1', '1.0', '1.00', '0', '-0', '0.0', '-0.0', '01']]
    binding = {'R': Binding.relation('r', 'r', {'CAT': Binding.column('cat', 'r', 'TEXT')})}
    context = Value.from_native({'R': rows})
    program = compile('R .> ' + suffix)
    plan = Sql.plan_hybrid(program, dialect, binding, {'strict': strict})
    assert plan.pure_memory

    def no_sql(*_):
        pytest.fail('numeric group-key representation would be lost in SQL')

    assert Sql.execute_hybrid(plan, no_sql, context.clone()).dump() == program.run(context).dump()


@pytest.mark.parametrize('dialect', ['mariadb', 'mysql', 'postgresql', 'sqlite'])
@pytest.mark.parametrize('strict', [False, True])
def test_identity_barrier_retains_safe_prefix(dialect, strict):
    rows = [dict(cat=v) for v in ['1', '1.0', '1.00']]
    binding = {'R': Binding.relation('r', 'r', {'CAT': Binding.column('cat', 'r', 'TEXT')})}
    context = Value.from_native({'R': rows})
    program = compile('R .> TAKE(2) .> MAP(RECORD("key", _["cat"] + 0))'
                      ' .> BUCKET(_["key"], RECORD("key", _K, "n", COUNT(_)))')
    plan = Sql.plan_hybrid(program, dialect, binding, {'strict': strict})
    assert not plan.pure_memory and not plan.pure_sql

    def run_prefix(sql, params):
        assert 'LIMIT 2' in sql and 'CAST' not in sql and 'GROUP BY' not in sql
        return Value.from_native(rows[:2])

    assert Sql.execute_hybrid(plan, run_prefix, context.clone()).dump() == program.run(context).dump()


@pytest.mark.parametrize('dialect', ['mariadb', 'mysql', 'postgresql', 'sqlite'])
@pytest.mark.parametrize('strict', [False, True])
def test_unknown_group_identity_keeps_projection_prefix(dialect, strict):
    bindings = {'R': Binding.relation('r', fields={'CAT': Binding.column('cat')})}
    source = 'R .> MAP(RECORD("key", _["cat"])) .> BUCKET(_["key"], RECORD("key", _K, "n", COUNT(_)))'
    program = compile(source)
    plan = Sql.plan_hybrid(program, dialect, bindings, {'strict': strict})
    assert not plan.pure_sql and not plan.pure_memory
    rows = [{'cat': 'a'}, {'cat': 'A'}, {'cat': 'a '}]

    def run_prefix(sql, params):
        assert 'GROUP BY' not in sql
        return Value.from_native([{'key': row['cat']} for row in rows])

    assert Sql.execute_hybrid(plan, run_prefix, Value.from_native({})).dump() == program.run({'R': rows}).dump()


@pytest.mark.parametrize('dialect', ['mariadb', 'mysql', 'postgresql', 'sqlite'])
@pytest.mark.parametrize('strict', [False, True])
def test_unused_computed_field_does_not_block_grouping(dialect, strict):
    bindings = {'R': Binding.relation('r', fields={
        'CAT': Binding.column('cat', type='TEXT'), 'V': Binding.column('v', type='NUM')})}
    program = compile('R .> MAP(RECORD("key", _["cat"], "computed", _["v"] + 0))'
                      ' .> BUCKET(_["key"], RECORD("key", _K, "n", COUNT(_)))')
    plan = Sql.plan_hybrid(program, dialect, bindings, {'strict': strict})
    # SQLite strict mode independently rejects inexact SQL arithmetic.
    if dialect == 'sqlite' and strict:
        assert plan.pure_memory
    else:
        assert plan.pure_sql


@pytest.mark.parametrize('dialect', ['mariadb', 'mysql', 'postgresql'])
@pytest.mark.parametrize('source_field', [
    Binding.column('v', type='NUM', guard=True),
    Binding.raw('v', type='NUM'),
])
def test_derived_type_proof_does_not_discard_a_guard_or_trust_raw_sql(dialect, source_field):
    bindings = {'R': Binding.relation('r', fields={'V': source_field})}
    program = compile('R .> MAP(RECORD("v", _["v"])) .> FILTER(_["v"] > 0)')
    sql = Sql.translate_statement(program, dialect, bindings).as_statement()
    assert 'CASE WHEN' in sql
