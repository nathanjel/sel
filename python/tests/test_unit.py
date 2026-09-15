"""Unit tests for the layers underneath the conformance suite.

conformance/ is what proves this implementation correct, and it is normative;
nothing here duplicates it. These tests cover the internals a conformance
failure would only point at indirectly — the UTF-8 codec, the decimal core, the
value model's ordering and dump — plus the public host interface, which the
conformance runner exercises only in one shape.

A handful exist specifically to pin *Python's* traps: the places where a
convenient built-in means something wider than SEL does. Those say so.
"""

import pytest

from sel import BOOL, SelError, Value, compile as sel_compile, evaluate
from sel import decimal as D
from sel.optimizer import optimize_ast_logical, unwind_pipeline
from sel.utf8 import bytes_compare, decode_utf8, encode_utf8


def raises(code, fn, *a, **kw):
    with pytest.raises(SelError) as ei:
        fn(*a, **kw)
    assert ei.value.code == code, f'wanted {code}, got {ei.value.code}: {ei.value.message}'
    return ei.value


# --- utf8 -------------------------------------------------------------------

def test_utf8_round_trip():
    for s in ['', 'ascii', 'héllo', 'Zażółć gęślą jaźń', '𝄞 clef']:
        assert decode_utf8(encode_utf8(s)) == s


@pytest.mark.parametrize('bad', [
    b'\xff',                  # invalid start byte
    b'\xc0\x80',              # overlong 2-byte
    b'\xe0\x80\x80',          # overlong 3-byte
    b'\xf0\x80\x80\x80',      # overlong 4-byte
    b'\xed\xa0\x80',          # surrogate
    b'\xf4\x90\x80\x80',      # above U+10FFFF
    b'\xe2\x8c',              # truncated
    b'\xc2',                  # truncated
])
def test_utf8_strict_decoding(bad):
    """None of these may become U+FFFD."""
    raises('E_UTF8', decode_utf8, bad)


def test_utf8_rejects_lone_surrogate():
    """Python str permits a lone surrogate; UTF-8 has no encoding for one."""
    raises('E_UTF8', encode_utf8, chr(0xD800))
    raises('E_UTF8', encode_utf8, 'a' + chr(0xDFFF) + 'b')
    raises('E_UTF8', Value.text, chr(0xD800))


def test_bytes_compare_is_bytewise():
    assert bytes_compare(b'\x00', b'\x01') == -1
    assert bytes_compare(b'ab', b'ab') == 0
    assert bytes_compare(b'ab', b'a') == 1
    assert bytes_compare(encode_utf8('�'), encode_utf8('\U00010000')) == -1


# --- decimal ----------------------------------------------------------------

def test_decimal_canonical_form():
    """Leading zeros go, trailing fraction zeros stay, zero is never negative."""
    assert D.format(D.parse('007')) == '7'
    assert D.format(D.parse('2.50')) == '2.50'
    assert D.format(D.parse('-0.00')) == '0.00'
    assert D.format(D.parse('-0')) == '0'


@pytest.mark.parametrize('text', [' 2', '2 ', '1.', '.5', '', 'x', '+1', '1e3',
                                  '٣', '1_2', '1,5'])
def test_decimal_rejects_non_numbers(text):
    """No trimming, no exponents, no underscores, no Unicode digits.

    The last three are Python's own trap: int('1_2') is 12, int(' 12 ') is 12
    and int('٣') is 3, so nothing may reach int() unvalidated.
    """
    assert D.parse(text) is None


def test_decimal_scale_rules():
    assert D.format(D.add(D.parse('1.50'), D.parse('2.50'))) == '4.00'
    assert D.format(D.mul(D.parse('2.5'), D.parse('4'))) == '10.0'
    assert D.format(D.div(D.parse('4'), D.parse('2'))) == '2'
    assert D.format(D.div(D.parse('1'), D.parse('3'))) == '0.3333333333'


def test_decimal_rounds_half_away_from_zero():
    """Not Python's round(), which is half to even: round(2.5) is 2 there."""
    assert D.format(D.round(D.parse('2.5'), 0)) == '3'
    assert D.format(D.round(D.parse('-2.5'), 0)) == '-3'
    assert D.format(D.round(D.parse('0.5'), 0)) == '1'
    assert round(2.5) == 2 and round(0.5) == 0        # the trap, demonstrated


def test_decimal_division_by_zero():
    raises('E_DIV_ZERO', D.div, D.parse('1'), D.parse('0'))
    raises('E_DIV_ZERO', D.mod, D.parse('1'), D.parse('0'))


def test_decimal_mod_takes_dividend_sign():
    assert D.format(D.mod(D.parse('-7'), D.parse('3'))) == '-1'
    assert D.format(D.mod(D.parse('7'), D.parse('-3'))) == '1'


def test_decimal_power():
    assert D.format(D.power(D.parse('10'), 3)) == '1000'
    assert D.format(D.power(D.parse('2'), 0)) == '1'
    assert D.format(D.power(D.parse('1.5'), 2)) == '2.25'


def test_decimal_cache_and_signed_fast_path():
    value = Value.num('12.50')
    assert value.as_decimal() is value.as_decimal()
    assert value.as_decimal().int_val == 1250
    assert D.cmp(D.parse('-284.7418'), D.parse('-1785.77373')) == 1


# --- value ------------------------------------------------------------------

def test_children_keep_insertion_order():
    """Re-assigning an existing key keeps its position — order is normative, and
    Python dicts give it for free as long as nothing deletes and re-adds.
    """
    v = Value.none()
    v.set('b', Value.text('1'))
    v.set('a', Value.text('2'))
    assert v.keys() == ['b', 'a']
    v.set('b', Value.text('9'))
    assert v.keys() == ['b', 'a']
    assert v.size() == 2


def test_eql_is_structural_not_numeric():
    assert Value.text('5').eql(Value.text('5'))
    assert not Value.text('5.00').eql(Value.text('5'))
    assert not Value.text('5').eql(Value.bool(True))


def test_clone_is_deep():
    a = Value.list([Value.list([Value.text('x')])])
    b = a.clone()
    b.get('1').set('1', Value.text('y'))
    assert a.dump() == '-{"1"=-{"1"=t"x"}}'
    assert b.dump() == '-{"1"=-{"1"=t"y"}}'


def test_regular_values_share_shape_and_use_flat_storage():
    first = Value.from_native({'id': 1, 'name': 'a'})
    second = Value.from_native({'id': 2, 'name': 'b'})
    assert first.shape is second.shape
    assert first.shape.size == 2
    assert [item.as_text() for item in first.storage] == ['1', 'a']
    items = [Value.text('a'), Value.text('b')]
    packed = Value.list(items)
    assert packed.storage is items


def test_join_output_is_shaped_and_irregular_rows_use_fallback():
    regular = evaluate(
        'LIST(RECORD("id", 1, "left", "a"), RECORD("id", 2, "left", "b"))'
        ' .> LINK(LIST(RECORD("id", 2, "right", "x")), '
        '_1["id"] == _2["id"])')
    assert regular.storage[0].shape is not None
    assert regular.storage[0].get('right').as_text() == 'x'

    irregular = evaluate(
        'LIST(RECORD("id", 1, "left", "a"), RECORD("left", "b", "id", 2))'
        ' .> LINK(LIST(RECORD("id", 2, "right", "x")), '
        '_1["id"] == _2["id"])')
    assert irregular.size() == 1
    assert irregular.get('1').get('right').as_text() == 'x'


def test_scalar_context_takes_first_child():
    assert evaluate('(7, 8)').as_text() == '7'
    raises('E_NULL', Value.none().as_text)
    raises('E_NO_SCALAR', Value.list([]).as_text)


def test_dump_escapes():
    assert Value.text('a"b\\c\nd\te').dump() == 't"a\\"b\\\\c\\nd\\te"'
    assert Value.text(chr(1)).dump() == 't"\\u0001"'
    assert Value.bin(b'\x00\xff\x10').dump() == 'b00ff10'


def test_num_validates_at_the_boundary():
    raises('E_NOT_NUM', Value.num, 'x')
    assert Value.num('007').dump() == 't"7"'


def test_from_native_refuses_float():
    """There is no floating point in SEL, and the boundary is where to say so."""
    with pytest.raises(TypeError):
        Value.from_native(0.1)
    with pytest.raises(TypeError):
        Value.from_native({'A': 1.5})


def test_from_native_bool_is_not_int():
    """bool subclasses int in Python; checked first, or True becomes "1"."""
    assert Value.from_native(True).kind == BOOL
    assert Value.from_native(1).kind == 'TEXT'


def test_to_native_round_trip():
    assert Value.from_native({'A': '1', 'B': ['x', 'y']}).to_native() == \
        {'A': '1', 'B': {'1': 'x', '2': 'y'}}


# --- host interface ---------------------------------------------------------

def test_context_is_mutated_in_place():
    ctx = Value.none()
    ctx.set('TOTAL', Value.num('10.00'))
    evaluate('SEEN = TOTAL * 2', ctx)
    assert ctx.get('SEEN').as_text() == '20.00'


def test_dependencies_excludes_assigned_and_bound():
    assert sel_compile('IF(A > B, A, C)').dependencies() == ['A', 'B', 'C']
    assert sel_compile('X = 1; X + Y').dependencies() == ['Y']
    assert sel_compile('ALL(I, IT, IT > 0)').dependencies() == ['I']
    assert sel_compile('ALL(I, _ > 0)').dependencies() == ['I']


def test_run_accepts_a_plain_dict():
    assert evaluate('A & B', {'A': 'x', 'B': 'y'}).as_text() == 'xy'


def test_error_carries_code_and_position():
    """Position and code are the contract; the message is not."""
    e = raises('E_UNDEF_VAR', evaluate, '1 +\n  X')
    assert (e.line, e.col) == (2, 3)
    assert e.offset == 6


def test_compile_time_errors():
    raises('E_UNKNOWN_FUNC', sel_compile, 'NOPE(1)')
    raises('E_ARITY', sel_compile, 'LEN()')
    raises('E_ARITY', sel_compile, 'LEN(1, 2)')
    raises('E_SYNTAX', sel_compile, '1 +')
    raises('E_BAD_ASSIGN', sel_compile, '1 = 2')
    raises('E_RESERVED', sel_compile, 'AND = 1')


def test_short_circuit_never_reaches_the_right_side():
    assert evaluate('FALSE AND (1/0 == 1)').as_bool() is False
    assert evaluate('TRUE OR (1/0 == 1)').as_bool() is True


def test_left_to_right_evaluation_is_observable():
    """Which operand's position an error reports is part of the contract."""
    e = raises('E_NOT_BIN', evaluate, 'TRUE $== FALSE')
    assert e.col == 1                      # the LEFT operand, not the right


def test_optimizer_respects_explicit_top_binder_and_key():
    """TOP's explicit binder form must inspect the key after the binder.

    Moving TOP ahead of MAP is valid only when the key is a pass-through field.
    The old off-by-one test treated `r` itself as the key for
    TOP(r, r["y"], 1), moved it anyway, and then indexed the pre-map row.
    """
    def names(source):
        _, steps = unwind_pipeline(optimize_ast_logical(sel_compile(source).ast))
        return [step.name for step in steps]

    prefix = ('LIST(RECORD("x", 3), RECORD("x", 1), RECORD("x", 2))'
              ' .> MAP(RECORD("x", _["x"], "y", _["x"] + 1)) .> ')
    assert names(prefix + 'TOP(r, r["x"], 1)') == ['TOP', 'MAP']
    assert names(prefix + 'TOP(r, r["y"], 1)') == ['MAP', 'TOP']


def test_optimizer_folded_unary_literals_keep_operator_position():
    not_node = optimize_ast_logical(sel_compile('NOT FALSE').ast)
    assert not_node.t == 'bool' and not_node.pos.col == 1

    neg_node = optimize_ast_logical(sel_compile('-1').ast)
    assert neg_node.t == 'num' and neg_node.pos.col == 1


def test_optimizer_hoisted_literals_take_the_folded_node_position():
    """A hoisted child takes the folded node's position (spec §6.3: the operand
    an operator rejects is the IF or the AND, not the literal inside it); a
    branch with positions of its own is not hoisted at all. The run()-visible
    half of this is ctl.if.constant-condition-* and
    op.logic.*-keeps-the-*-position.
    """
    folded_if = optimize_ast_logical(sel_compile('1 + IF(TRUE, "x", 2)').ast)
    assert folded_if.r.t == 'text' and folded_if.r.v == 'x' and folded_if.r.pos.col == 5
    folded_and = optimize_ast_logical(sel_compile('1 + (FALSE AND TRUE)').ast)
    assert folded_and.r.t == 'bool' and folded_and.r.v is False and folded_and.r.pos.col == 12
    folded_or = optimize_ast_logical(sel_compile('1 + (TRUE OR FALSE)').ast)
    assert folded_or.r.t == 'bool' and folded_or.r.v is True and folded_or.r.pos.col == 11
    unfolded = optimize_ast_logical(sel_compile('IF(TRUE, 1 / 0, 2)').ast)
    assert unfolded.t == 'call' and unfolded.name == 'IF' and unfolded.args[1].pos.col == 12
    unfolded_var = optimize_ast_logical(sel_compile('IF(TRUE, X, 2)').ast)
    assert unfolded_var.t == 'call' and unfolded_var.name == 'IF'


@pytest.mark.parametrize(('source', 'kind', 'want'), [
    ('ORDERS .> TAKE(2) .> MAP(IF(TRUE, "x", 1) >= _["id"])', 'hybrid', 'E_NOT_NUM@1:26'),
    ('ORDERS .> TAKE(2) .> FILTER((FALSE AND TRUE) + _["id"] > 0)', 'hybrid', 'E_NOT_NUM@1:36'),
    ('ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"])', 'pure_memory', 'E_NOT_NUM@1:18'),
])
def test_a_plan_continuation_reports_errors_where_run_does(source, kind, want):
    """The planner folds one tree for both halves of a split, so a hoisted
    literal in the continuation carries the position the in-memory half will
    report. sql/cases/25-hybrid-plans.sqlt pins the SQL side of these; only
    executing the plan can see the position the memory side reports.
    """
    from sel.sql import Sql
    rows = [{'id': '1'}, {'id': '2'}]

    def failure(fn):
        try:
            fn()
        except SelError as e:
            return f'{e.code}@{e.line}:{e.col}'
        return 'no error'

    program = sel_compile(source)
    plan = Sql.plan_hybrid(program, 'postgresql', _orders())
    got = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    assert got == kind
    assert failure(lambda: program.run({'ORDERS': rows})) == want
    assert failure(lambda: Sql.execute_hybrid(plan, lambda sql, params: rows,
                                              {'ORDERS': rows})) == want


@pytest.mark.parametrize(('source', 'expected'), [
    ('(3, 1, 2) .> TAKE(2) .> TAKE(1)', ['TAKE']),
    ('(3, 1, 2) .> DROP(1) .> DROP(1)', ['DROP']),
    ('(3, 1, 2) .> SORT() .> TAKE(1)', ['TOP']),
    ('(3, 1, 2) .> SORT_DESC() .> TAKE(1)', ['TOP_DESC']),
    ('((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
     ' .> SORT_BY(_["x"], "DESC") .> TAKE(1)', ['TOP_BY']),
    ('((RECORD("x", 1), RECORD("x", 2)))'
     ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
     ' .> FILTER(_["x"] > 0)', ['FILTER', 'MAP']),
    ('(1, 2) .> SORT() .> FILTER(_ > 0)', ['FILTER', 'SORT']),
    ('((RECORD("x", 1), RECORD("x", 2)))'
     ' .> SELECT_COLS("x") .> FILTER(_["x"] > 0)', ['FILTER', 'SELECT_COLS']),
    ('((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
     ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
     ' .> SORT_BY(_["x"], "DESC")', ['SORT_BY', 'MAP']),
    ('(1, 2) .> FILTER(_ > 0) .> FILTER(_ < 3)', ['FILTER']),
    ('(1, 2) .> SORT() .> SORT_DESC()', ['SORT_DESC']),
    ('(1, 2) .> DEDUPE() .> DISTINCT()', ['DEDUPE']),
    ('(1, 2) .> FILTER(TRUE)', []),
    # A first step over a variable is kept: the source may be a scalar.
    ('DATA .> FILTER(TRUE)', ['FILTER']),
])
def test_optimizer_logical_rules_fire(source, expected):
    _, steps = unwind_pipeline(optimize_ast_logical(sel_compile(source).ast))
    assert [step.name for step in steps] == expected


def test_optimizer_physical_join_pushdown_and_lazy_record():
    source = (
        'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
        ' .> FILTER(_["orders"]["status"] $== "ACTIVE"'
        ' AND _["customers"]["country"] $== "DE")'
    )
    _, steps = unwind_pipeline(optimize_ast_logical(sel_compile(source).ast))
    assert [step.name for step in steps] == ['LINK', 'FILTER']
    _, steps = unwind_pipeline(optimize_ast_logical(sel_compile(
        '((RECORD("x", 1), RECORD("x", 2)))'
        ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
    ).ast))
    assert steps[0].name == 'MAP'

    from sel.optimizer import optimize_ast_in_memory
    physical = optimize_ast_in_memory(sel_compile(
        '((RECORD("x", 1), RECORD("x", 2)))'
        ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
    ).ast)
    _, steps = unwind_pipeline(physical)
    assert steps[0].args[1].name == 'LAZY_RECORD'

    physical = optimize_ast_in_memory(sel_compile(source).ast)
    _, steps = unwind_pipeline(physical)
    assert [step.name for step in steps] == ['FILTER', 'LINK']

    # Join pushdown must return to the logical fixed point: a pushed left
    # predicate must fuse with a FILTER that was already before the LINK.
    fixed_point = optimize_ast_in_memory(sel_compile(
        'ORDERS .> FILTER(_["status"] $== "ACTIVE")'
        ' .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
        ' .> FILTER(_["orders"]["status"] $== "ACTIVE")'
    ).ast)
    _, steps = unwind_pipeline(fixed_point)
    assert [step.name for step in steps] == ['FILTER', 'LINK']

    # A qualified table reference is only affinity-safe when rooted at the
    # current filter binder or `_`; an external variable must remain above the
    # join rather than being rewritten as a joined-row field.
    external_root = optimize_ast_in_memory(sel_compile(
        'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
        ' .> FILTER(FOO["orders"]["status"] $== "ACTIVE")'
    ).ast)
    _, steps = unwind_pipeline(external_root)
    assert [step.name for step in steps] == ['LINK', 'FILTER']

    # `_K` is an unknown dependency outside BUCKET and must not be treated as
    # a neutral variable while classifying join predicates.
    group_key = optimize_ast_in_memory(sel_compile(
        'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
        ' .> FILTER(_["orders"]["status"] $== _K)'
    ).ast)
    _, steps = unwind_pipeline(group_key)
    assert [step.name for step in steps] == ['LINK', 'FILTER']


# --- the hybrid planner's contract ------------------------------------------
#
# sql/cases/25-hybrid-plans.sqlt holds the language-neutral version; what is
# here is what the shared fixtures cannot express in JSON options or cannot
# observe through the runner: an optimiser option reaching the optimiser, the
# run() cache, and immutability across a real run().

def _snapshot(node):
    if node is None:
        return None
    return (node.t, node.pos, node.v, node.name, node.op, node.grouped,
            _snapshot(node.l), _snapshot(node.r), _snapshot(node.x),
            _snapshot(node.obj), _snapshot(node.idx),
            _snapshot(node.target), _snapshot(node.value),
            tuple(_snapshot(i) for i in node.items),
            tuple(_snapshot(a) for a in node.args))


def _orders():
    from sel.sql import Binding
    return {'ORDERS': Binding.relation('orders', 'o',
                                       {'ID': Binding.column('id', 'o', 'NUM')})}


def test_planner_normalises_before_unwinding_and_names_physical_sources():
    from sel.sql import Sql
    plan = Sql.plan_hybrid(sel_compile('X = ORDERS; X .> TAKE(1)'), 'postgresql', _orders())
    assert plan.pure_sql
    assert plan.source_tables == ['orders']


def test_planner_options_reach_the_logical_optimiser():
    from sel.sql import Sql
    source = 'ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9)'
    fused = Sql.plan_hybrid(sel_compile(source), 'postgresql', _orders())
    unfused = Sql.plan_hybrid(sel_compile(source), 'postgresql', _orders(),
                              {'fuseFilters': False})
    assert len(unwind_pipeline(fused.sql_prefix_ast)[1]) == 1
    assert len(unwind_pipeline(unfused.sql_prefix_ast)[1]) == 2
    assert fused.pure_sql and unfused.pure_sql


def test_planner_falls_back_to_memory_when_stage_1_refuses():
    from sel.sql import Sql
    program = sel_compile('A += 1; ORDERS .> TAKE(1)')
    plan = Sql.plan_hybrid(program, 'postgresql', _orders())
    assert plan.pure_memory
    assert plan.continuation_program is program
    assert plan.continuation_ast is program.ast
    assert plan.source_tables == ['orders']


def test_fold_constants_option_reaches_the_logical_optimiser():
    from sel.sql import Sql
    foldable = 'ORDERS .> FILTER(_["id"] > 1 + 1)'
    folded = Sql.plan_hybrid(sel_compile(foldable), 'postgresql', _orders())
    unfolded = Sql.plan_hybrid(sel_compile(foldable), 'postgresql', _orders(),
                               {'foldConstants': False})
    assert '> 2' in folded.sql_statement.as_statement()
    assert '> 2' not in unfolded.sql_statement.as_statement()


def test_program_ast_survives_run_optimisation_and_planning():
    from sel.optimizer import optimize_ast_in_memory
    from sel.sql import Sql
    program = sel_compile(
        '(3, 1, 2) .> FILTER(NOT (_ < 1 + 1)) .> MAP(RECORD("x", _, "y", _ * 2, "z", _ + 1))'
        ' .> TAKE(2 * 1)')
    before = _snapshot(program.ast)
    first = program.run().dump()
    optimize_ast_logical(program.ast)
    optimize_ast_in_memory(program.ast)
    Sql.plan_hybrid(program, 'postgresql', _orders())
    assert _snapshot(program.ast) == before
    assert program.run().dump() == first


def test_bucket_plans_answer_what_the_evaluator_answers_on_sqlite():
    """Every string assertion in sql/cases was green while the planner split
    `BUCKET(k) .> MAP(...)` after the bucket and counted one row per group --
    the rows a SELECT ... GROUP BY returns are keys, not SEL's groups. Only an
    executed plan can see that, and sqlite3 ships with Python, so this runs
    each bucket shape both ways and compares.
    """
    import sqlite3
    from sel.sql import Binding, Sql
    bindings = {'ORDERS': Binding.relation('orders', 'o', {
        'ID': Binding.column('id', 'o', 'NUM'),
        'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM'),
        'AMOUNT': Binding.column('amount', 'o', 'NUM'),
        'NAME': Binding.column('name', 'o', 'TEXT')})}
    rows = [{'id': '1', 'customer_id': '7', 'amount': '10', 'name': 'a'},
            {'id': '2', 'customer_id': '7', 'amount': '5', 'name': 'b'},
            {'id': '3', 'customer_id': '9', 'amount': '7', 'name': 'c'}]
    db = sqlite3.connect(':memory:')
    db.execute('create table orders(id, customer_id, amount, name)')
    db.executemany('insert into orders values (?, ?, ?, ?)',
                   [(r['id'], r['customer_id'], r['amount'], r['name']) for r in rows])

    def runner(sql, params):
        cur = db.execute(sql, [p.as_text() for p in params])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]

    shapes = [
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))', 'pure_sql'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "total", SUM(g, x, x["amount"])))', 'pure_sql'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> MAP(RECORD("cid", _K))', 'pure_sql'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> MAP(RECORD("c", _["cid"], "big", _["n"] > 1))', 'hybrid'),
        ('ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))', 'pure_sql'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1) .> MAP(RECORD("cid", _K, "n", COUNT(_)))', 'pure_memory'),
        # Review 2026-09-15 findings I, AI and P: a bare bucket at the end of
        # the pipeline, a bucket over a bucket, and the MAP fall-through over
        # an aliased or computed pair, a whole-row read, a colliding
        # dependency, a downstream read of a dependency, a downstream BUCKET.
        ('ORDERS .> BUCKET(_["customer_id"])', 'pure_memory'),
        ('ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["customer_id"])', 'hybrid'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)', 'pure_memory'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1)', 'pure_memory'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))', 'pure_memory'),
        ('ORDERS .> MAP(RECORD("cid", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> TAKE(2)', 'hybrid'),
        ('ORDERS .> MAP(RECORD("plus", _["amount"] + 1, "shout", REPEAT(_["name"], 2))) .> SORT_BY(_["plus"])', 'hybrid'),
        ('ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"])', 'pure_memory'),
        ('ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> FILTER(_["name"] $== "a")', 'pure_memory'),
        ('ORDERS .> MAP(RECORD("id", _["id"], "row", REPEAT(GET(_, "name"), 2))) .> TAKE(3)', 'pure_memory'),
        ('ORDERS .> MAP(RECORD("customer_id", _["amount"], "tag", REPEAT(_["customer_id"], 2))) .> TAKE(3)', 'pure_memory'),
        ('ORDERS .> TAKE(5) .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> DEDUPE()', 'hybrid'),
        # Cross-validation of that fix: a downstream step's own binder, keys
        # differing only by case, a list literal around the custom call.
        ('ORDERS .> MAP(r, RECORD("id", r["id"], "shout", REPEAT(r["name"], 2))) .> SORT_BY(s, s["name"])', 'pure_memory'),
        ('ORDERS .> MAP(r, RECORD("id", r["id"], "shout", REPEAT(r["name"], 2))) .> FILTER(s, s["id"] > 1)', 'hybrid'),
        ('ORDERS .> MAP(RECORD("Name", _["name"], "shout", REPEAT(_["name"], 2))) .> TAKE(2)', 'pure_memory'),
        ('ORDERS .> MAP(RECORD("x", _["id"], "X", REPEAT(_["name"], 2))) .> TAKE(2)', 'hybrid'),
        ('ORDERS .> MAP(RECORD("id", _["id"], "shout", (REPEAT(_["name"], 2), 1))) .> TAKE(2)', 'hybrid'),
        # Findings H and G: a FILTER after pagination is a WHERE over the
        # paginated groups (on sqlite the split lands after the TAKE); a bare
        # bucket over a record key is refused where the projected spelling
        # groups by all its fields.
        ('ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(1) .> FILTER(_["n"] > 1)', 'hybrid'),
        ('ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)', 'hybrid'),
        ('ORDERS .> BUCKET(RECORD("c", _["customer_id"]), RECORD("n", COUNT(_)))', 'pure_sql'),
        ('ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_)))', 'pure_memory'),
        # Findings J, K and X: the bucket body's scope. The group binder is
        # the member list (indexing it is E_NO_KEY; only COUNT and SUM read
        # through it, and SUM's two-argument form binds `_` to the member),
        # `_K` is the group key only inside that body (before the bucket and
        # after the projection it is a list position), and after the
        # projection the row has the projection's fields alone. Where SEL
        # raises, the plan keeps the failing step in memory and raises too.
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))', 'pure_memory'),
        ('ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))', 'pure_memory'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 1) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))', 'pure_memory'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["amount"] > 1) .> MAP(RECORD("cid", _K))', 'pure_memory'),
        ('ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "mx", MAX(_, _["amount"])))', 'pure_memory'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(g), "s", SUM(g, _["amount"]), "t", SUM(g, x, x["amount"])))', 'pure_sql'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(_)))', 'pure_memory'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1 AND SUM(_, _["amount"]) > 5 AND _K == 7) .> MAP(RECORD("cid", _K))', 'pure_sql'),
        ('ORDERS .> FILTER(_K $== "2") .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_)))', 'pure_memory'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> FILTER(_K $== "2")', 'hybrid'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> SORT_BY(_K, "DESC")', 'hybrid'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> SORT_BY(_["customer_id"])', 'hybrid'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> FILTER(_["cid"] == 7)', 'pure_sql'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> SORT_BY(_["n"], "DESC")', 'pure_sql'),
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K)) .> FILTER(COUNT(_) > 0)', 'hybrid'),
        ('ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))', 'pure_memory'),
        ('ORDERS .> MAP(g, RECORD("x", _["amount"]))', 'pure_memory'),
    ]

    def outcome(fn):
        # The dump is inside the try: run() answers a LAZY_RECORD whose
        # fields are evaluated when read, so a body that raises does so here.
        try:
            got = fn()
            return (got if isinstance(got, Value) else Value.from_native(got)).dump()
        except SelError as e:
            return f'{e.code}@{e.line}:{e.col}'

    for source, expected in shapes:
        program = sel_compile(source)
        want = outcome(lambda: program.run({'ORDERS': rows}))
        plan = Sql.plan_hybrid(program, 'sqlite', bindings)
        got_kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
        assert got_kind == expected, (source, got_kind)
        got = outcome(lambda: Sql.execute_hybrid(plan, runner, {'ORDERS': rows}))
        assert got == want, (source, got, want)


def test_program_caches_the_physical_ast_per_source_tree():
    program = sel_compile('1 + 1')
    assert program.physical_ast() is program.physical_ast()
    first = program.physical_ast()
    program.ast = sel_compile('2 + 2').ast
    assert program.physical_ast() is not first
    assert program.run().as_text() == '4'


# --- parser: the precedence-climbing pilot ----------------------------------

def test_not_is_a_loose_prefix_operator():
    """NOT binds looser than comparison, so it takes the whole comparison."""
    assert evaluate('NOT 1 == 2').as_bool() is True
    assert evaluate('NOT TRUE AND TRUE').as_bool() is False   # (NOT TRUE) AND TRUE


def test_not_is_refused_where_its_binding_power_does_not_reach():
    """Inside a comparison operand NOT is not a prefix operator, so it is read
    as the reserved word it is — the same answer the transcribed parsers give.
    """
    raises('E_RESERVED', sel_compile, '1 == NOT TRUE')
    raises('E_RESERVED', sel_compile, '-NOT TRUE')


def test_comparison_does_not_chain():
    raises('E_SYNTAX', sel_compile, '1 < 2 < 3')
    assert evaluate('(1 < 2) AND (2 < 3)').as_bool() is True


def test_comma_and_semicolon_build_n_ary_nodes():
    assert sel_compile('1, 2, 3').ast.t == 'list'
    assert len(sel_compile('1, 2, 3').ast.items) == 3
    assert sel_compile('1; 2; 3').ast.t == 'seq'
    assert len(sel_compile('1; 2; 3').ast.items) == 3


def test_grouped_flag_distinguishes_one_list_from_two_arguments():
    assert evaluate('JOIN((1, 2), "-")').as_text() == '1-2'
    raises('E_BAD_ASSIGN', sel_compile, '(A) = 1')


def test_prefix_chains_are_depth_counted():
    """Uncounted, these reached the host's own stack limit instead of E_DEPTH."""
    raises('E_DEPTH', sel_compile, '-' * 300 + '1')
    raises('E_DEPTH', sel_compile, 'NOT ' * 300 + 'TRUE')


def test_deep_nesting_raises_e_depth_not_recursion_error():
    """The reason this parser is precedence climbing: a transcribed one needs
    about 3500 Python frames to reach the same trip point, against a default
    recursion limit of 1000.
    """
    e = raises('E_DEPTH', sel_compile, '(' * 100 + '1' + ')' * 100)
    assert (e.line, e.col) == (1, 101)


# --- regex: Python's folding disagreement -----------------------------------

def test_ignore_case_folds_exactly_two_non_ascii_code_points():
    assert evaluate("RMATCH('^k$', \"K\", 'i')").as_bool() is True
    assert evaluate("RMATCH('^s$', \"ſ\", 'i')").as_bool() is True
    # Python's re.IGNORECASE also folds these two, and must not here.
    assert evaluate("RMATCH('^i$', \"İ\", 'i')").as_bool() is False
    assert evaluate("RMATCH('^i$', \"ı\", 'i')").as_bool() is False
    # Full folding would make this true; SEL specifies simple folding.
    assert evaluate("RMATCH('^ss$', \"ß\", 'i')").as_bool() is False


def test_folded_subject_does_not_leak_into_groups():
    """Offsets come from the folded subject, text from the original."""
    v = evaluate("RGROUPS('(k)', \"K\", 'i')")
    assert v.get('2').as_text() == 'K'
    assert evaluate("RREPLACE('k', '[$0]', \"K\", 'i')").as_text() == '[K]'


def test_anchors_do_not_match_before_a_trailing_newline():
    """Python's $ does, like PCRE's; SEL anchors to the ends of the subject."""
    assert evaluate('RMATCH(\'^a$\', "a\\n")').as_bool() is False
    assert evaluate('RMATCH(\'^a$\', "a")').as_bool() is True
