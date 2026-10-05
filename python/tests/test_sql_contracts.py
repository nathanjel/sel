"""The SQL layer's T08-T11 contract, held by the Python host (docs/internals/
sql-translation.md, sql/MAP.md, docs/internals/sql-kinds.md).

Each test names the finding it closes. The shared `.sqlt` cases pin exact bytes;
these hold the properties a byte diff cannot: what is refused, what is remembered
across calls, and what must not raise a foreign exception.
"""
import pytest

import sel
from sel import Value, compile
from sel.sql import Binding, Sql, SqlError, map as sqlmap

NUM = lambda col, table='t': Binding.column(col, table, 'NUM')          # noqa: E731
UNK = lambda col, table='t': Binding.column(col, table)                 # noqa: E731
TEXT = lambda col, table='t': Binding.column(col, table, 'TEXT')       # noqa: E731


def tr(src, bindings=None, dialect='mariadb', **kw):
    return Sql.translate(compile(src), dialect, bindings or {}, **kw)


def refused(src, code, bindings=None, dialect='mariadb'):
    with pytest.raises(SqlError) as info:
        tr(src, bindings, dialect)
    assert info.value.code == code, str(info.value)
    return info.value


# --- T09: kinds -----------------------------------------------------------

@pytest.mark.parametrize('src', [
    'IF(TRUE, F, TRUE) AND TRUE',
    'COALESCE(F, TRUE) AND TRUE',
    'F ?? TRUE AND TRUE',
    'NOT IF(TRUE, F, TRUE)',
])
def test_a_conditional_cannot_launder_an_undeclared_column_into_a_bool(src):
    # JS-C27, PHP-C28, CPP-C29, LISP-C24, GO-C15
    refused(src, 'E_SQL_SHAPE', {'F': UNK('f')})


def test_a_conditional_over_an_undeclared_branch_is_guarded_as_a_whole():
    f = tr('(U ?? 1) + 1 > 0', {'U': UNK('u')}).as_value()
    # the guard sits OUTSIDE the COALESCE: a leaf guard would turn 'abc' into 1
    assert f.count('REGEXP') == 1 and 'COALESCE(`t`.`u`, 1) REGEXP' in f
    assert 'CAST(COALESCE(`t`.`u`, 1) AS DECIMAL(65,10))' in f


def test_a_conditional_over_an_undeclared_branch_is_refused_on_sqlite():
    refused('(U ?? 1) + 1 > 0', 'E_SQL_UNSUPPORTED', {'U': UNK('u')}, 'sqlite')


@pytest.mark.parametrize('src', [
    'JOIN((T, F), ",")', 'JOIN((F, T), "-")', 'JOIN((T, T), F)', 'JOIN((X, T), ",")',
])
def test_join_refuses_bool_and_bin_elements_and_separators(src):
    # PY-C19, PHP-C31
    refused(src, 'E_SQL_SHAPE', {'T': TEXT('t'), 'F': Binding.column('f', 't', 'BOOL'),
                                 'X': Binding.column('x', 't', 'BIN')})


def test_join_of_text_still_translates():
    assert tr('JOIN((T, "a"), "-")', {'T': TEXT('t')}).as_value() == "CONCAT(CONCAT(`t`.`t`, '-'), 'a')"


REL = {'O': Binding.relation('orders', 'o', {
    'CAT': Binding.column('cat', 'o', 'TEXT'),
    'STATUS': Binding.column('status', 'o', 'TEXT'),
    'FLAG': Binding.column('flag', 'o', 'BOOL'),
    'MISC': Binding.column('misc', 'o'),
})}


@pytest.mark.parametrize('field', ['STATUS', 'FLAG'])
def test_a_grouped_sum_over_a_declared_text_or_bool_field_is_refused(field):
    # PHP-C27
    with pytest.raises(SqlError) as info:
        Sql.translate_statement(compile(f'O .> BUCKET(_["CAT"], RECORD("s", SUM(_, _["{field}"])))'),
                                'mariadb', REL)
    assert info.value.code == 'E_SQL_SHAPE'


def test_a_grouped_sum_over_an_undeclared_field_is_all_or_nothing():
    sql = Sql.translate_statement(compile('O .> BUCKET(_["CAT"], RECORD("s", SUM(_, _["MISC"])))'),
                                  'mariadb', REL).as_statement()
    assert 'CASE WHEN COUNT(*) = COUNT(CASE WHEN (`o`.`misc` REGEXP' in sql
    assert 'THEN COALESCE(SUM(CAST(`o`.`misc` AS DECIMAL(65,10))), 0) ELSE NULL END' in sql


def test_a_grouped_sum_over_an_undeclared_field_is_refused_on_sqlite():
    with pytest.raises(SqlError) as info:
        Sql.translate_statement(compile('O .> BUCKET(_["CAT"], RECORD("s", SUM(_, _["MISC"])))'),
                                'sqlite', REL)
    assert info.value.code == 'E_SQL_UNSUPPORTED'


def test_a_columns_unroll_uses_the_plain_operand_guard():
    b = {'V': Binding.columns(UNK('u'), NUM('n'))}
    assert tr('SUM(V, _)', b).as_value().startswith('(CASE WHEN (`t`.`u` REGEXP')


@pytest.mark.parametrize('src', ['F IN S', 'TRUE IN S', 'X IN S'])
def test_in_relation_refuses_a_bool_or_bin_needle(src):
    # PY-C21
    b = {'S': Binding.relation('sk', 's', {'SKU': TEXT('sku', 's')}, 'SKU'),
         'F': Binding.column('f', 't', 'BOOL'), 'X': Binding.column('x', 't', 'BIN')}
    refused(src, 'E_SQL_SHAPE', b)


def test_a_numeric_item_beside_an_exact_column_is_cast():
    # PY-C20
    b = {'T': Binding.column('t', 't', 'TEXT', exact=True)}
    sql = tr('T IN ("a", 3)', b).as_value()
    assert "= CAST(3 AS CHAR) COLLATE" in sql and "(`t`.`t` = 'a')" in sql


@pytest.mark.parametrize('count,want', [
    ('2.0', 'LIMIT 2'), ('0.0', 'LIMIT 0'), ('9007199254740993', 'LIMIT 9007199254740993'),
    ('9223372036854775808', 'LIMIT 9223372036854775807'),
    ('99999999999999999999999', 'LIMIT 9223372036854775807'),
])
def test_take_counts_are_exact_clamped_and_accept_a_scale(count, want):
    # JS-C56, JS-C55, PHP-C50, CPP-C59, PY-C41
    b = {'ITEMS': Binding.relation('items', 'i', {})}
    sql = Sql.translate_statement(compile(f'ITEMS .> TAKE({count})'), 'mariadb', b).as_statement()
    assert sql.endswith(want)


def test_drop_offsets_merge_flat_and_clamp():
    b = {'ITEMS': Binding.relation('items', 'i', {})}
    sql = Sql.translate_statement(compile('ITEMS .> DROP(9223372036854775807) .> DROP(1)'),
                                  'mariadb', b).as_statement()
    assert sql.endswith('LIMIT 18446744073709551615 OFFSET 9223372036854775807')
    assert '_sub' not in sql


def test_a_fractional_count_is_still_not_an_integer():
    b = {'ITEMS': Binding.relation('items', 'i', {})}
    with pytest.raises(SqlError) as info:
        Sql.translate_statement(compile('ITEMS .> TAKE(2.5)'), 'mariadb', b)
    assert info.value.code == 'E_NOT_INT'


@pytest.mark.parametrize('n,balanced', [(256, False), (257, True)])
def test_an_unroll_above_256_operands_is_a_balanced_fold(n, balanced):
    items = ', '.join(['N'] * n)
    sql = tr(f'ANY(({items}), _ > 0)', {'N': NUM('n')}).as_value()
    depth = max_depth(sql)
    assert (depth < 200) == balanced, depth     # 257 -> 129 + 128 halves, each a left fold


def max_depth(sql):
    d = m = 0
    for ch in sql:
        if ch == '(':
            d += 1
            m = max(m, d)
        elif ch == ')':
            d -= 1
    return m


# --- T08: scope, slots, size ----------------------------------------------

def test_an_element_reads_the_scope_the_list_was_written_in():
    # ANY((0,0), ALL((_K, 5), I, I > 1)): the inner list's `_K` is the OUTER key
    sql = tr('ANY((0,0), ALL((_K, 5), I, I > 1))').as_value()
    assert "CAST('1' AS DECIMAL" in sql and "CAST('2' AS DECIMAL" in sql


def test_a_definition_is_not_captured_by_a_binder_of_the_same_name():
    sql = tr('X2 = A; ALL((5,6), A, X2 > 0)', {'A': NUM('a')}).as_value()
    assert sql == '((`t`.`a` > 0) AND (`t`.`a` > 0))'


def test_a_filter_binder_is_not_in_scope_in_the_body():
    b = {'V': Binding.columns(NUM('a', 'x'), NUM('b', 'x'))}
    err = refused('ANY(FILTER(V, a, a > 1), q, a < 9)', 'E_SQL_UNBOUND', b)
    assert (err.line, err.col) == (1, 29)


def test_a_binder_shadows_a_value_binding_of_the_same_name():
    b = {'V': Binding.value(Value.num('5'), 'NUM'), 'A': UNK('a')}
    sql = tr('ALL((A, A), V, V + 1 > 0)', b).as_value()
    assert 'REGEXP' in sql            # V is the element (a column), not the constant 5


def test_indexed_assignment_copies():
    assert tr('R[1] = 5; X = R; R[2] = 6; COUNT(X)').as_value() == '1'


def test_join_over_a_filter_is_refused_at_the_filter():
    err = refused('JOIN(FILTER(("a","b"), _ $== "a"), ",")', 'E_SQL_SHAPE')
    assert (err.line, err.col) == (1, 6)


def test_a_doubling_helper_chain_is_refused_for_its_size():
    lines = ['V0 = N']
    lines += [f'V{i} = V{i - 1} + V{i - 1}' for i in range(1, 19)]
    src = '; '.join(lines) + '; V18 > 0'
    refused(src, 'E_SQL_SIZE', {'N': NUM('n')})
    assert tr('; '.join(lines[:4]) + '; V3 > 0', {'N': NUM('n')}).as_value()


def test_a_helper_chain_past_the_depth_limit_is_e_sql_depth():
    lines = ['X0 = 1'] + [f'X{i} = X{i - 1} + 1' for i in range(1, 260)]
    refused('; '.join(lines) + '; X259 > 0', 'E_SQL_DEPTH')


def test_a_non_name_binder_is_e_sql_shape_at_the_binder():
    b = {'ITEMS': Binding.relation('items', 'i', {'DEPT': TEXT('dept', 'i'), 'QTY': NUM('qty', 'i')})}
    with pytest.raises(SqlError) as info:
        Sql.translate_statement(compile('ITEMS .> MAP(BUCKET("x", _["DEPT"], "x", _["QTY"]))'),
                                'mariadb', b)
    assert (info.value.code, info.value.col) == ('E_SQL_SHAPE', 27)


# --- T10: bindings, registration, rendering -------------------------------

@pytest.mark.parametrize('t', ['LIST', 'STATEMENT'])
def test_list_and_statement_are_not_column_types(t):
    with pytest.raises(SqlError) as info:
        Binding.column('c', 'o', t)
    assert info.value.code == 'E_SQL_BINDING'


def test_bindings_differing_only_by_case_are_refused():
    with pytest.raises(SqlError) as info:
        tr('X > 1', {'x': NUM('a'), 'X': NUM('b')})
    assert info.value.code == 'E_SQL_BINDING'


def test_relation_fields_differing_only_by_case_are_refused():
    with pytest.raises(SqlError) as info:
        Binding.relation('t', 't', {'A': NUM('x'), 'a': NUM('y')})
    assert info.value.code == 'E_SQL_BINDING'


def test_a_text_value_holding_nul_is_refused_in_every_mode():
    err = refused('S $== "a\\u{0}b"', 'E_SQL_UNSUPPORTED', {'S': TEXT('s')})
    assert (err.line, err.col) == (1, 7)


@pytest.mark.parametrize('src,col', [
    ('ITEMS .> MAP(RECORD("a\\u{0}b", _["PRICE"]))', 21),
    ('ITEMS .> MAP(RECORD("", _["PRICE"]))', 21),
])
def test_an_alias_from_a_text_literal_is_not_empty_and_holds_no_nul(src, col):
    b = {'ITEMS': Binding.relation('items', 'i', {'PRICE': NUM('price', 'i')})}
    with pytest.raises(SqlError) as info:
        Sql.translate_statement(compile(src), 'postgresql', b)
    assert (info.value.code, info.value.col) == ('E_SQL_UNSUPPORTED', col)


def test_two_postgresql_aliases_sharing_63_bytes_are_refused():
    long = 'a' * 63
    b = {'ITEMS': Binding.relation('items', 'i', {'PRICE': NUM('price', 'i')})}
    src = f'ITEMS .> MAP(RECORD("{long}X1", _["PRICE"], "{long}X2", _["PRICE"]))'
    with pytest.raises(SqlError) as info:
        Sql.translate_statement(compile(src), 'postgresql', b)
    assert info.value.code == 'E_SQL_UNSUPPORTED'


def test_a_raw_field_cannot_be_selected_or_read_across_a_derived_table():
    b = {'ITEMS': Binding.relation('items', 'i', {
        'ID': NUM('id', 'i'), 'TOTAL': Binding.raw('i.price * i.qty', 'NUM')})}
    for src, col in (('ITEMS .> SELECT_COLS("TOTAL")', 22),
                     ('ITEMS .> SORT_BY(_["ID"]) .> TAKE(2) .> FILTER(_["TOTAL"] > 5)', 49)):
        with pytest.raises(SqlError) as info:
            Sql.translate_statement(compile(src), 'mariadb', b)
        assert (info.value.code, info.value.col) == ('E_SQL_SHAPE', col)


def test_a_supplied_correlate_is_parenthesised():
    b = {'ITEMS': Binding.relation('oi', 'oi', {'QTY': NUM('qty', 'oi')}, correlate='oi.a=o.id OR oi.b=o.id')}
    assert '(oi.a=o.id OR oi.b=o.id) AND' in tr('ANY(ITEMS, I, I["QTY"] > 0)', b).as_value()


def test_params_mode_binds_no_value_it_does_not_emit():
    # CPP-C57, LISP-C40: a fragment rendered only to be refused early
    b = {'R': Binding.relation('r', 'r', {'A': NUM('a', 'r'), 'N': TEXT('n', 'r')}),
         'S': Binding.relation('s', 's', {'A': NUM('a', 's'), 'M': TEXT('m', 's')})}
    src = ('R .> FILTER(_["N"] $== "zz") .> SORT_BY(_["N"]) .> TAKE(3) '
           '.> LINK(S, _1["A"] == _2["A"] AND _2["M"] $== "yy") '
           '.> MAP(RECORD("n", _["N"], "m", _["M"]))')
    f = Sql.translate_statement(compile(src), 'mariadb', b)
    f.as_statement('params')
    assert len(f.bindings()) == 2 and [p.as_text() for p in f.bindings()] == ['zz', 'yy']
    assert len(f.params) == 2


def test_an_unknown_render_mode_is_refused_even_with_no_slot():
    f = tr('C > C', {'C': NUM('c')})
    assert f.as_value('params')
    for call in (lambda: f.as_value('bogus'), lambda: f.as_condition('bogus')):
        with pytest.raises(RuntimeError):
            call()


# --- registration -----------------------------------------------------------

@pytest.fixture
def clean_map():
    before = set(sqlmap._extra)
    yield
    for name in list(sqlmap._extra):
        if name not in before:
            del sqlmap._extra[name]
    sqlmap._guard_checked.clear()


def define(name, extends, **lexical):
    sqlmap.define_dialect(name, {'extends': extends, 'version': '1', 'lexical': lexical})


def test_a_text_escape_that_does_not_cover_the_quote_is_refused(clean_map):
    with pytest.raises(RuntimeError):
        define('t-nobs', 'mariadb', textEscape={'\\': '\\\\'})
    assert not sqlmap.exists('t-nobs')          # nothing half-registered is left behind


def test_an_empty_text_escape_is_refused(clean_map):
    with pytest.raises(RuntimeError):
        define('t-noesc', 'sqlite', textEscape={})


def test_an_escape_character_must_escape_itself(clean_map):
    with pytest.raises(RuntimeError):
        define('t-badbs', 'mariadb', textEscape={"'": "\\'"})


def test_a_text_quote_must_differ_from_the_identifier_quote(clean_map):
    with pytest.raises(RuntimeError):
        define('t-dq', 'ansi', textQuote='"')


def test_a_correct_pairing_is_accepted_and_shipped_dialects_satisfy_it(clean_map):
    define('t-ok', 'mariadb', textEscape={'\\': '\\\\', "'": "''"})
    assert tr('"it\'s"', dialect='t-ok').as_value() == "'it''s'"
    for shipped in ('ansi', 'mariadb', 'mysql', 'postgresql', 'sqlite'):
        sqlmap._check_pairing(shipped)


def test_re_registering_under_the_same_parent_replaces_and_under_another_is_refused(clean_map):
    define('t-redef', 'mariadb', textCollate=' COLLATE utf8mb4_bin')
    define('t-redef', 'mariadb', textCollate=' COLLATE utf8mb4_0900_bin')
    assert 'utf8mb4_0900_bin' in tr('"A" $== "a"', dialect='t-redef').as_value()
    with pytest.raises(RuntimeError):
        define('t-redef', 'postgresql')


def test_a_numeric_guard_that_does_not_carry_the_pattern_is_refused_on_every_use(clean_map):
    # JS-C24, PHP-C49, PY-C49, CPP-C36, LISP-C42: memoised before it was checked
    define('t-pgbad', 'postgresql',
           numericGuard="CASE WHEN ({textCast:0} ~ '^.*$') THEN CAST({0} AS NUMERIC) ELSE NULL END")
    b = {'NAME': TEXT('name', 'o')}
    for _ in range(3):
        with pytest.raises(RuntimeError):
            tr('NAME + 1', b, 't-pgbad')


# --- T11: the planner -------------------------------------------------------

ORD = {'ORDERS': Binding.relation('orders', 'o', {
    'ID': NUM('id', 'o'), 'NAME': TEXT('name', 'o'), 'CUSTOMER_ID': NUM('customer_id', 'o')})}


def plan(src, bindings=None):
    return Sql.plan_hybrid(compile(src), 'mariadb', bindings or ORD)


def kind(p):
    return 'pure_sql' if p.pure_sql else 'pure_memory' if p.pure_memory else 'hybrid'


def test_no_split_after_a_filter_when_the_continuation_reads_keys():
    assert kind(plan('ORDERS .> FILTER(_["id"] > 2) .> MAP(RECORD("i", _["id"], "k", _K))')) == 'pure_memory'


def test_no_split_after_a_filter_when_only_filters_follow():
    assert kind(plan('ORDERS .> FILTER(_["id"] > 2) '
                     '.> FILTER(COUNT(SPLIT(_["name"], "a")) > 1)')) == 'pure_memory'


def test_a_split_after_a_sort_may_end_in_a_filter():
    p = plan('ORDERS .> SORT_BY(_["id"]) .> FILTER(COUNT(SPLIT(_["name"], "a")) > 1)')
    assert kind(p) == 'hybrid'


def test_row_cutting_steps_stay_behind_a_local_map_half():
    p = plan('ORDERS .> SORT_BY(_["id"]) .> MAP(RECORD("i", _["id"], "z", IF(_["id"] > 4, ABORT("x"), 1))) '
             '.> TAKE(2)')
    assert kind(p) == 'hybrid'
    assert 'LIMIT' not in p.sql_statement.as_statement()


def test_a_helper_unwound_into_the_pipeline_is_not_applied_twice():
    p = plan('ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)')
    assert p.sql_statement.as_statement().endswith('LIMIT 3 OFFSET 2')


def test_source_tables_ignore_names_that_only_look_like_relations():
    b = {**ORD, 'CUSTOMERS': Binding.relation('customers', 'c', {'ID': NUM('id', 'c')})}
    assert plan('LIST(1) .> MAP(ORDERS, ORDERS)', b).source_tables == []
    assert plan('ORDERS = LIST(1); ORDERS .> MAP(_ + 1)', b).source_tables == []
    assert plan('ORDERS .> FILTER(CUSTOMERS, CUSTOMERS["id"] > 1)', b).source_tables == ['orders']


def test_a_literal_helper_named_like_an_explicit_sort_binder_stays_a_binder():
    p = plan('N = 5; ORDERS .> FILTER(_["id"] > 1) .> SORT_BY(N, N["id"], "DESC") .> TAKE(2)')
    assert kind(p) == 'pure_sql'


def test_a_sorted_list_is_not_bucketed_or_joined_by_the_database():
    # the sort's order cannot survive a GROUP BY or a join in the statement
    b = {**ORD, 'CUSTOMERS': Binding.relation('customers', 'c', {'ID': NUM('id', 'c')})}
    p = plan('ORDERS .> SORT_BY(_["id"], "DESC") .> BUCKET(_["customer_id"], RECORD("c", _K, "n", COUNT(_)))')
    assert kind(p) == 'hybrid'
    p = plan('ORDERS .> SORT_BY(_["id"], "DESC") .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) '
             '.> MAP(RECORD("o", _["O"]["id"]))', b)
    assert kind(p) == 'hybrid'


def test_a_link_left_in_memory_keeps_the_name_of_its_left_side():
    b = {**ORD, 'CUSTOMERS': Binding.relation('customers', 'c', {'ID': NUM('id', 'c')})}
    p = plan('ORDERS .> SORT_BY(_["id"]) .> TAKE(4) '
             '.> LINK(CUSTOMERS, _1["customer_id"] == _2["id"] AND COUNT(SPLIT(_1["name"], "a")) > 0) '
             '.> MAP(RECORD("n", _["ORDERS"]["name"]))', b)
    assert kind(p) == 'hybrid'
    rows = Value.from_native([{'id': '1', 'name': 'alpha', 'customer_id': '1'}])
    got = Sql.execute_hybrid(p, lambda sql, params: rows, {'CUSTOMERS': [{'id': '1'}]})
    assert got.dump() == compile('LIST(RECORD("n", "alpha"))').run().dump()


def test_a_pure_memory_plan_never_mutates_the_callers_context():
    p = plan('A = 1; A += 1; ORDERS .> SORT_BY(_["id"]) .> TAKE(A) .> MAP(RECORD("i", _["id"]))')
    assert kind(p) == 'pure_memory'
    ctx = Value.from_native({'ORDERS': [{'id': '2'}, {'id': '1'}]})
    Sql.execute_hybrid(p, lambda sql, params: None, ctx)
    assert 'A' not in [k for k, _ in ctx.entries()]


# --- PY-C1 site f: the SQL layer under a deep program -----------------------

@pytest.mark.parametrize('n', [197, 198, 300])
def test_a_deep_helper_chain_is_a_refusal_not_a_recursion_error(n):
    b = {'C': NUM('c'), 'F': NUM('f')}
    src = 'V0 = C; ' + ''.join(f'V{i} = ANY(V{i - 1}, F); ' for i in range(1, n)) + f'V{n - 1}'
    # a refusal or a fragment, never a RecursionError (which would escape here)
    Sql.try_translate(compile(src), 'mariadb', b)


@pytest.mark.parametrize('n', [340, 400, 3000])
def test_a_long_pipeline_of_helpers_plans_without_a_recursion_error(n):
    src = ('A0 = R .> FILTER(_["ID"] > 0); '
           + ''.join(f'A{i} = A{i - 1} .> TAKE(11); ' for i in range(1, n)) + f'A{n - 1}')
    r = {'R': Binding.relation('r', 'r', {'ID': NUM('id', 'r')})}
    p = Sql.plan_hybrid(compile(src), 'mariadb', r)
    assert p is not None
    Sql.try_translate_statement(compile(src), 'mariadb', r)


def test_is_constant_is_bounded_on_a_deep_tree():
    from sel.sql import constants
    node = compile('1' + ' + 1' * 150).ast
    assert constants.is_constant(node) is True
    assert constants.is_constant(compile('1' + ' + 1' * 400).ast) is False   # past the cap: not checkable


def test_a_doubling_helper_dag_is_refused_before_anything_walks_it():
    # 2^30 nodes as a tree, from 30 statements: stage 1 refuses it by size rather
    # than validating an exponential constant, and the planner answers pure_memory
    import time
    lines = ['X0 = 1'] + [f'X{i} = X{i - 1} + X{i - 1}' for i in range(1, 30)]
    src = '; '.join(lines) + '; X29 > 0'
    t0 = time.process_time()
    refused(src, 'E_SQL_SIZE')
    assert kind(plan('; '.join(lines) + '; ORDERS .> FILTER(_["id"] > X29)')) == 'pure_memory'
    assert time.process_time() - t0 < 30


def test_a_20000_step_pipeline_is_a_refusal_and_a_pure_memory_plan():
    src = 'ORDERS' + ' .> TAKE(1)' * 20000
    assert Sql.try_translate_statement(compile(src), 'mariadb', ORD) is None
    assert kind(plan(src)) == 'pure_memory'


def test_a_helper_chain_of_thousands_plans_in_linear_time():
    import time
    n = 6000
    src = ''.join(f'X{i} = {"COUNT(ORDERS)" if i == 0 else f"X{i - 1} + 1"}; ' for i in range(n))
    src += f'ORDERS .> FILTER(_["id"] > X{n - 1})'
    t0 = time.process_time()
    p = plan(src)
    assert p is not None and time.process_time() - t0 < 20     # 8000 helpers took 86s when it was quadratic


def test_source_tables_are_read_off_a_deep_tree_without_recursion():
    from sel.sql.hybrid import _source_tables
    from sel.sql import Bindings
    assert _source_tables(compile('ORDERS' + ' .> TAKE(1)' * 5000).ast, Bindings(ORD)) == ['orders']


@pytest.mark.parametrize('t', ['TEXT', 'BOOL', 'BIN', 'UNKNOWN', 'LIST'])
def test_a_value_binding_takes_num_or_nothing(t):
    with pytest.raises(SqlError) as info:
        Binding.value(Value.text('x'), t)
    assert info.value.code == 'E_SQL_BINDING'
    assert Binding.value(Value.text('x')) and Binding.value(Value.num('5'), 'NUM')


def test_postgresql_sums_the_guarded_cast_and_mariadb_the_bare_one():
    b = {'ITEMS': Binding.relation('oi', 'oi', {'QTY': UNK('qty', 'oi')})}
    pg = tr('SUM(ITEMS, _["QTY"])', b, 'postgresql').as_value()
    assert 'SUM(CASE WHEN (CAST("oi"."qty" AS TEXT) ~' in pg
    md = tr('SUM(ITEMS, _["QTY"])', b).as_value()
    assert 'SUM(CAST(`oi`.`qty` AS DECIMAL(65,10)))' in md


def test_sqlite_min_and_max_read_every_operand_as_a_number():
    # GO-C17: sqlite's max() orders by storage class, an integer below any text
    assert tr('MAX(1, 2)', dialect='sqlite').as_value() == "max(CAST('1' AS NUMERIC), CAST('2' AS NUMERIC))"
    assert tr('MIN(N, 60) > 70', {'N': NUM('n')}, 'sqlite').as_value().count('CAST(') >= 2
    assert tr('MIN("1")', dialect='sqlite').as_value() == "min('1')"       # nothing to compare with
