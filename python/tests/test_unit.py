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


@pytest.mark.parametrize('data,message', [
    (b'abc\xff', 'invalid start byte 0xff at byte 3'),
    (b'abc\xe2\x82', 'truncated sequence at byte 3'),
    (b'abc\xe2(\xa1', 'invalid continuation byte at byte 4'),
    (b'\xed\xa0\x80', 'invalid continuation byte at byte 1'),
    (b'\xf4\x90\x80\x80', 'invalid continuation byte at byte 1'),
])
def test_utf8_native_failure_keeps_sel_diagnostics(data, message):
    from sel import Pos
    error = raises('E_UTF8', decode_utf8, data, Pos(3, 7, 21))
    assert error.message == message
    assert (error.line, error.col, error.offset) == (3, 7, 21)
    assert error.__context__ is None


def test_utf8_native_decoder_boundaries():
    # Include a BOM and noncharacters: strict UTF-8 preserves these scalars.
    text = ''.join(chr(c) for c in [0, 0x7f, 0x80, 0x7ff, 0x800, 0xd7ff,
                                    0xe000, 0xfeff, 0xffff, 0x10000, 0x10ffff])
    assert decode_utf8(text.encode('utf-8')) == text
    assert decode_utf8(bytearray(text.encode('utf-8'))) == text


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
    assert (value.as_decimal().digits, value.as_decimal().scale) == (1250, 2)
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


def test_join_rows_of_a_left_row_use_a_plan_that_fits_that_row():
    # project_many builds a left row's first joined row through project and
    # the rest through the loop of the plan that fit it. When the first pair
    # goes the general way (an unshaped right element), the memo still names
    # the previous left row's plan, which assumed that row's facts: `m` a
    # record there, a number here. Unshaped records do not arise from SEL
    # source, hence a unit test and not a conformance case (SEL-0056).
    from sel.builtins.structure import make_join_projector, make_joined_row
    def rec(*kv):
        return Value.record(list(kv[::2]), list(kv[1::2]))
    t = Value.text
    left_1 = rec('k', t('1'), 'm', rec('x', t('1')))
    left_2 = rec('k', t('1'), 'm', t('7'))
    assert left_1.shape is left_2.shape
    unshaped = Value.none().set('id', t('1')).set('v', t('a'))
    assert unshaped.shape is None
    rights = [unshaped, rec('id', t('1'), 'v', t('b')), rec('id', t('1'), 'v', Value.none())]
    _, project_many = make_join_projector('X', 'Y', None)
    out = []
    project_many(left_1, rights[1:], out)
    project_many(left_2, rights, out)
    want = [make_joined_row(left, right, 'X', 'Y', None)
            for left, rs in ((left_1, rights[1:]), (left_2, rights)) for right in rs]
    assert [row.dump() for row in out] == [row.dump() for row in want]


def test_scalar_context_takes_first_child():
    assert evaluate('(7, 8)').as_text() == '7'
    raises('E_NULL', Value.none().as_text)
    raises('E_NO_SCALAR', Value.list([]).as_text)


def test_scalar_context_storage_and_nested_errors():
    leaf = Value.text('first')
    wide = [leaf] + [Value.none()] * 100_000
    values = [Value.list(wide), Value.record(['a', 'b'], [leaf, Value.none()]),
              Value.none().set('a', leaf).set('b', Value.none())]
    for value in values:
        assert Value.list([value]).scalar_source() is leaf
    for empty, code in [(Value.none(), 'E_NULL'), (Value.list([]), 'E_NO_SCALAR')]:
        raises(code, Value.list([empty, leaf]).as_text)
    nested = leaf
    for _ in range(1001):
        nested = Value.list([nested])
    raises('E_DEPTH', nested.as_text)


def test_structural_hash_representation_and_cache_invariance():
    from sel.value import structural_hash
    leaf = Value.text('x')
    values = [Value.list([leaf]), Value.list([leaf], ['1']),
              Value.record(['1'], [leaf]), Value.none().set('1', leaf)]
    hashes = [structural_hash(v) for v in values]
    assert len(set(hashes)) == 1
    for v, h in zip(values, hashes):
        assert v.eql(values[0])
        v.entries()
        assert structural_hash(v) == h
    for spelling in ['1', '1.0', '01', '-0']:
        value = Value.text(spelling)
        before = structural_hash(value)
        value.as_decimal()
        assert structural_hash(value) == before
        assert value.eql(Value.text(spelling))


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
    # Finding AJ: a helper is read where the program reads it, and evaluated
    # once, before the pipeline -- not inlined at its definition's position
    # and re-evaluated per row. LABEL is a context variable, so a helper
    # defined from it is one no fold turns into a literal.
    ('Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)', 'hybrid', 'E_NOT_NUM@1:45'),
    ('X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])', 'hybrid', 'E_NOT_NUM@1:55'),
    ('Y = "a" & "b"; ORDERS .> TAKE(2) .> MAP(1 + Y)', 'hybrid', 'E_NOT_NUM@1:45'),
    ('Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z)', 'hybrid', 'E_NOT_NUM@1:50'),
    ('Y = "x"; (ORDERS .> TAKE(2)) .> MAP(_["id"] + Y)', 'hybrid', 'E_NOT_NUM@1:47'),
    ('Y = LABEL; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)', 'hybrid', 'E_NOT_NUM@1:47'),
    ('C = COUNT(ORDERS) + LABEL; ORDERS .> TAKE(2) .> MAP(_["id"] + C)', 'hybrid', 'E_NOT_NUM@1:21'),
    ('X = ORDERS .> TAKE(2); X .> MAP(COUNT(X) + _["id"] + "x")', 'hybrid', 'E_NOT_NUM@1:54'),
    ('Y = ABORT("x"); ORDERS .> TAKE(2) .> MAP(Y)', 'pure_memory', 'E_ABORT@1:11'),
    # SEL-0047: a continuation on line 3 reports its error on line 3.
    ('X = ORDERS .> TAKE(2);\nX .> MAP(COUNT(X) + _["id"]\n   + "x")', 'hybrid', 'E_NOT_NUM@3:6'),
])
def test_a_plan_continuation_reports_errors_where_run_does(source, kind, want):
    """The planner folds one tree for both halves of a split, so a hoisted
    literal in the continuation carries the position the in-memory half will
    report. sql/cases/25-hybrid-plans.sqlt pins the SQL side of these; only
    executing the plan can see the position the memory side reports. The
    helper rows are review finding AJ: the planner plans the program as
    written, so a helper read in the continuation fails at the read's
    position, as run() reports it, and its definition is evaluated once before
    the steps rather than once per row; LABEL is a context variable, so a
    helper built from it is something no fold turns into a literal.
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
    context = {'ORDERS': rows, 'LABEL': 'x'}
    assert failure(lambda: program.run(context)) == want
    assert failure(lambda: Sql.execute_hybrid(plan, lambda sql, params: rows,
                                              context)) == want


@pytest.mark.parametrize(('source', 'expected'), [
    ('(3, 1, 2) .> TAKE(2) .> TAKE(1)', ['TAKE']),
    ('(3, 1, 2) .> DROP(1) .> DROP(1)', ['DROP']),
    ('(3, 1, 2) .> SORT() .> TAKE(1)', ['TOP']),
    ('(3, 1, 2) .> SORT_DESC() .> TAKE(1)', ['TOP_DESC']),
    ('((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
     ' .> SORT_BY(_["x"], "DESC") .> TAKE(1)', ['TOP_BY']),
    # A FILTER moves in front of a MAP, a sort or a SELECT_COLS only when a
    # later step renumbers the rows again without reading `_K`: FILTER keeps
    # its input's keys and the three renumber (spec §7.3), so at the end of a
    # pipeline the swap would change the answer's keys.
    ('((RECORD("x", 1), RECORD("x", 2)))'
     ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
     ' .> FILTER(_["x"] > 0) .> MAP(_["heavy"])', ['FILTER', 'MAP', 'MAP']),
    ('((RECORD("x", 1), RECORD("x", 2)))'
     ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
     ' .> FILTER(_["x"] > 0)', ['MAP', 'FILTER']),
    ('((RECORD("x", 1), RECORD("x", 2)))'
     ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
     ' .> FILTER(_["x"] > 0) .> MAP(_K)', ['MAP', 'FILTER', 'MAP']),
    ('((RECORD("x", 1), RECORD("x", 2)))'
     ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
     ' .> FILTER(_["x"] > 0) .> FILTER(_["x"] > 1) .> TAKE(1)', ['FILTER', 'MAP', 'TAKE']),
    ('((RECORD("x", 1), RECORD("x", 2)))'
     ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
     ' .> FILTER(_["heavy"] > 0)', ['MAP', 'FILTER']),
    ('(1, 2) .> SORT() .> FILTER(_ > 0) .> TAKE(1)', ['FILTER', 'TOP']),
    ('(1, 2) .> SORT() .> FILTER(_ > 0)', ['SORT', 'FILTER']),
    ('((RECORD("x", 1), RECORD("x", 2)))'
     ' .> SELECT_COLS("x") .> FILTER(_["x"] > 0) .> MAP(_["x"])', ['FILTER', 'SELECT_COLS', 'MAP']),
    ('((RECORD("x", 1), RECORD("x", 2)))'
     ' .> SELECT_COLS("x") .> FILTER(_["x"] > 0)', ['SELECT_COLS', 'FILTER']),
    ('((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
     ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
     ' .> SORT_BY(_["x"], "DESC")', ['SORT_BY', 'MAP']),
    ('(1, 2) .> FILTER(_ > 0) .> FILTER(_ < 3)', ['FILTER']),
    # A sort after a sort is kept: the sorts are stable and the first breaks ties.
    ('(1, 2) .> SORT() .> SORT_DESC()', ['SORT', 'SORT_DESC']),
    ('(1, 2) .> DEDUPE() .> DISTINCT()', ['DEDUPE']),
    ('(1, 2) .> FILTER(TRUE)', []),
    # A first step over a variable is kept: the source may be a scalar.
    ('DATA .> FILTER(TRUE)', ['FILTER']),
])
def test_optimizer_logical_rules_fire(source, expected):
    _, steps = unwind_pipeline(optimize_ast_logical(sel_compile(source).ast))
    assert [step.name for step in steps] == expected


def test_optimizer_physical_join_pushdown_keeps_record():
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
    # MAP(RECORD(...)) is strict: the physical optimiser leaves the body alone.
    assert steps[0].name == 'MAP'
    assert steps[0].args[1].name == 'RECORD'

    # The physical tree never moves a FILTER across a LINK (spec §7.4;
    # SEL-0054): a FILTER moved onto a side renumbered the joined rows,
    # skipped the join keys of the rows it dropped, and read relation names
    # under explicit binders. The join tests conjuncts itself, at run time,
    # where it can prove that is the same.
    for src in (source,
                'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
                ' .> FILTER(_["customers"]["country"] $== "DE"'
                ' AND _["orders"]["status"] $== "ACTIVE")',
                'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
                ' .> FILTER(_["orders"]["status"] $== "ACTIVE")'):
        _, steps = unwind_pipeline(optimize_ast_in_memory(sel_compile(src).ast))
        assert [step.name for step in steps] == ['LINK', 'FILTER']
        assert steps[0].args[1].t == 'var'
    fixed_point = optimize_ast_in_memory(sel_compile(
        'ORDERS .> FILTER(_["status"] $== "ACTIVE")'
        ' .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
        ' .> FILTER(_["orders"]["status"] $== "ACTIVE")'
    ).ast)
    _, steps = unwind_pipeline(fixed_point)
    assert [step.name for step in steps] == ['FILTER', 'LINK', 'FILTER']
    # Whether a FILTER's keys can be seen, for the join's pre-filter: a step
    # that renumbers without reading `_K` hides them; the end does not.
    _, steps = unwind_pipeline(optimize_ast_in_memory(sel_compile(source + ' .> MAP(1)').ast))
    assert steps[1].args[1].keys_unobserved is True
    _, steps = unwind_pipeline(optimize_ast_in_memory(sel_compile(source).ast))
    assert steps[1].args[1].keys_unobserved is False

    # A qualified table reference is only affinity-safe when rooted at the
    # current filter binder or `_`; an external variable must remain above the
    # join rather than being rewritten as a joined-row field.
    external_root = optimize_ast_in_memory(sel_compile(
        'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
        ' .> FILTER(FOO["orders"]["status"] $== "ACTIVE")'
    ).ast)
    _, steps = unwind_pipeline(external_root)
    assert [step.name for step in steps] == ['LINK', 'FILTER']

    # A binder or relation name read directly after the LINK -- `O["x"]`,
    # `ORDERS["x"]`, `_2["x"]` -- is not a side: the binders are scoped to the
    # predicate (spec §7.4), so as written it is E_UNDEF_VAR or E_NO_KEY, and
    # pushing it into the side it names would turn that error into rows
    # (review 2026-09-15, W2). Only `_["O"]["x"]` names a side.
    for predicate in ('O["status"] $== "ACTIVE"', 'ORDERS["status"] $== "ACTIVE"',
                      '_1["status"] $== "ACTIVE"', 'C["country"] $== "DE"',
                      'CUSTOMERS["country"] $== "DE"', '_2["country"] $== "DE"'):
        stays = optimize_ast_in_memory(sel_compile(
            'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"])'
            f' .> FILTER({predicate})'
        ).ast)
        _, steps = unwind_pipeline(stays)
        assert [step.name for step in steps] == ['LINK', 'FILTER'], predicate
        # Left as written: the read still goes through the bare name.
        read = steps[1].args[1].l
        assert read.t == 'index' and read.obj.t == 'var'
        assert read.obj.name.upper() == predicate.split('[')[0].upper()
    through_the_key = optimize_ast_in_memory(sel_compile(
        'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"])'
        ' .> FILTER(_["C"]["country"] $== "DE")'
    ).ast)
    _, steps = unwind_pipeline(through_the_key)
    # Not even a read through the key moves: the join tests it at run time.
    assert [step.name for step in steps] == ['LINK', 'FILTER']
    assert steps[0].args[1].t == 'var'

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
        # The FILTER no longer moves in front of the MAP (it would renumber
        # the answer's keys); over the MAP's derived table sqlite cannot
        # render its NUM guard, so nothing pushes down. A later step that
        # renumbers again lets the swap through.
        ('ORDERS .> MAP(r, RECORD("id", r["id"], "shout", REPEAT(r["name"], 2))) .> FILTER(s, s["id"] > 1)', 'pure_memory'),
        ('ORDERS .> MAP(r, RECORD("id", r["id"], "shout", REPEAT(r["name"], 2))) .> FILTER(s, s["id"] > 1) .> TAKE(5)', 'hybrid'),
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
        # Finding X, second facet: COUNT of a structure-yielding call is its
        # number of children, never the literal 0 the scalar fallback wrote.
        ('ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_name", COUNT(BUCKET(_, _["name"]))))', 'pure_memory'),
        ('ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1, 2, 3)))) .> TAKE(2)', 'pure_memory'),
        ('ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(IF(TRUE, LIST(1, 2), 3)))) .> TAKE(2)', 'pure_memory'),
        ('ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"]))', 'pure_memory'),
        # Finding V: a later sort's keys come first, the earlier sort breaks
        # its ties; a sort after a TAKE sorts the page; the MAP/sort swap
        # leaves the TAKE last.
        ('ORDERS .> SORT_BY(_["name"]) .> SORT_BY(_["customer_id"], "DESC") .> TAKE(2)', 'pure_sql'),
        ('ORDERS .> SORT_BY(_["id"], "DESC") .> SORT_BY(_["customer_id"], "DESC")', 'pure_sql'),
        ('ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(1) .> SORT_BY(_["n"])', 'pure_sql'),
        ('ORDERS .> MAP(RECORD("id", _["id"], "n", _["id"] + 1)) .> SORT_BY(_["id"], "DESC") .> TAKE(2)', 'pure_sql'),
        # Finding AJ: helper assignments. A literal helper (after folding) is
        # inlined at its reads; a helper that is the source is unwound
        # through; a helper the continuation still reads is carried in front
        # of it and evaluated once, as run() does.
        ('N = 1 + 1; X = ORDERS .> TAKE(N) .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))); X .> FILTER(_["id"] > 1) .> TAKE(5)', 'hybrid'),
        ('LIMIT = 2; ORDERS .> TAKE(LIMIT) .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], LIMIT)))', 'hybrid'),
        ('C = COUNT(ORDERS); ORDERS .> FILTER(_["amount"] > C) .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2)))', 'hybrid'),
    ]

    def outcome(fn):
        # The dump is inside the try for the ordinary reason: run() itself
        # may raise, and the outcome is compared as a code@position either way.
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


def test_runner_contract_hands_params_statement_and_bindings_in_order():
    """The runner contract (finding AK): the statement in `params` mode with
    `bindings()` in placeholder order -- text literals as `?`, numbers inlined
    -- in every host, so a driver binds what it is handed as it is. Lisp
    handed the runner inline SQL and its creation-order slot list.
    """
    from sel.sql import Binding, Sql
    bindings = {'ORDERS': Binding.relation('orders', 'o', {
        'ID': Binding.column('id', 'o', 'NUM'),
        'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM'),
        'AMOUNT': Binding.column('amount', 'o', 'NUM'),
        'NAME': Binding.column('name', 'o', 'TEXT')})}
    rows = [{'id': '1', 'customer_id': '7', 'amount': '10', 'name': 'a'}]
    program = sel_compile('ORDERS .> FILTER(FIND("needle", "hay-" & _["name"]) > 0 AND _["amount"] > 5)'
                          ' .> MAP(RECORD("g", RGROUPS("(a)", _["name"])))')
    plan = Sql.plan_hybrid(program, 'mariadb', bindings)
    assert not plan.pure_sql and not plan.pure_memory, 'expected a hybrid plan'
    seen = []

    def runner(sql, params):
        seen.append((sql, [p.dump() for p in params]))
        return []

    Sql.execute_hybrid(plan, runner, {'ORDERS': rows})
    assert len(seen) == 1, 'the runner was not called once'
    sql, params = seen[0]
    assert '?' in sql and "'needle'" not in sql and "'hay-'" not in sql, sql
    assert '?, 5' not in sql and '> 5' in sql, sql
    assert params == ['t"hay-"', 't"needle"'], params


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


def test_physical_tree_is_a_function_of_the_ast_alone():
    """SEL-0049: the physical tree is built once per AST and does not depend
    on the data a program runs over. This host used to key it on the context
    and read the rows to push an unqualified field's filter under a LINK."""
    from sel.optimizer import unwind_pipeline
    program = sel_compile(
        'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
        ' .> FILTER(_["amount"] > 1 AND _["customer_id"] == 7)')
    first = program.physical_ast()
    rows = {'ORDERS': [{'id': 1, 'customer_id': 7, 'amount': 5, 'name': 'a'}],
            'CUSTOMERS': [{'id': 7, 'name': 'x'}]}
    program.run(rows)
    program.run({'ORDERS': [], 'CUSTOMERS': []})
    assert program.physical_ast() is first
    # An unqualified field names no side, so nothing is pushed under the join;
    # the qualified form still is (the test above this one).
    _, steps = unwind_pipeline(first)
    assert [step.name for step in steps] == ['LINK', 'FILTER']
    # The answer is the same either way: one joined row.
    assert program.run(rows).size() == 1


def _rows(*records):
    return [dict(r) for r in records]


def _joined(program_source, ctx):
    """Evaluates PROGRAM_SOURCE both as written (FILTER straight over the join,
    where the run-time pre-filter engages) and through a helper variable
    (assignment copies the joined rows, so the FILTER sees a plain list and no
    pre-filter runs), and returns both dumps or both error codes."""
    def go(src):
        try:
            return 'ok ' + sel_compile(src).run(dict(ctx)).dump()
        except SelError as e:
            return f'err {e.code}'
    head, tail = program_source.replace('FILTER_SRC', '').rsplit(' .> FILTER(', 1)
    return go(head + ' .> FILTER(' + tail), go('J = ' + head + '; J .> FILTER(' + tail)


def test_join_prefilter_keeps_results_and_errors():
    """SEL-0049: a FILTER over a LINK pre-applies its leading field conjuncts to
    the left rows at run time; the outcome must be exactly what filtering the
    joined rows gives -- rows, keys, and which error surfaces."""
    ctx = {
        'ORDERS': _rows({'id': 1, 'customer_id': 7, 'status': 'A', 'amount': 5},
                        {'id': 2, 'customer_id': 7, 'status': 'B', 'amount': 'x'},
                        {'id': 3, 'customer_id': 9, 'status': 'A', 'amount': 1},
                        {'id': 4, 'customer_id': 7, 'status': 'A'}),
        'CUSTOMERS': _rows({'id': 7, 'name': 'n7', 'tier': 'P'}),
    }
    join = 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
    left = 'ORDERS .> LINK_LEFT(CUSTOMERS, _1["customer_id"] == _2["id"])'
    for src in [
        # plain left-field conjuncts, the shape that is pre-applied
        f'FILTER_SRC{join} .> FILTER(_["status"] $== "A")',
        f'FILTER_SRC{join} .> FILTER(_["status"] $== "A" AND _["tier"] $== "P")',
        f'FILTER_SRC{left} .> FILTER(_["status"] $== "A")',
        # a conjunct that raises on a left row that joins nothing (id 3):
        # nothing may raise, in either evaluation
        f'FILTER_SRC{join} .> FILTER(_["status"] $== "B" AND _["amount"] > 2)',
        # a conjunct that raises on a row that DOES join (id 2, amount "x"):
        # the same error, at the same place
        f'FILTER_SRC{join} .> FILTER(_["amount"] > 2)',
        # a field the row lacks (id 4): E_NO_KEY where it joins, either way
        f'FILTER_SRC{join} .> FILTER(_["amount"] > 0 AND _["status"] $== "A")',
        # a field both sides carry is ambiguous on the joined row: E_NO_KEY,
        # not a pre-filter on the left side's copy
        f'FILTER_SRC{join} .> FILTER(_["id"] > 0)',
        # a named binder, and a conjunct after one that reads _K
        f'FILTER_SRC{join} .> FILTER(r, r["status"] $== "A" AND r["amount"] > 0)',
        f'FILTER_SRC{join} .> FILTER(_K > 1 AND _["status"] $== "A")',
    ]:
        as_written, through_a_variable = _joined(src, ctx)
        assert as_written == through_a_variable, src


def test_join_prefilter_travels_down_a_chain_of_pure_joins():
    ctx = {
        'ORDERS': _rows({'id': 1, 'customer_id': 7, 'status': 'A'},
                        {'id': 2, 'customer_id': 7, 'status': 'B'}),
        'CUSTOMERS': _rows({'id': 7, 'name': 'n7'}),
        'ITEMS': _rows({'order_id': 1, 'sku': 's1'}, {'order_id': 2, 'sku': 's2'}),
    }
    src = ('FILTER_SRCORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
           ' .> LINK(ITEMS, _["orders"]["id"] == _2["order_id"])'
           ' .> FILTER(_["status"] $== "A" AND _["sku"] $== "s1")')
    as_written, through_a_variable = _joined(src, ctx)
    assert as_written == through_a_variable
    assert as_written.startswith('ok ') and 's1' in as_written and 's2' not in as_written


def test_join_prefilter_through_a_pushed_filter_keeps_results_keys_and_errors():
    """SEL-0050: a qualified conjunct is pushed below the upper join by the
    logical optimiser in every host, leaving a FILTER between the joins; the
    pre-filter travels through it only by pre-applying that FILTER's own
    predicate too, and drops rows below the upper join only when nothing
    observes the top FILTER's keys (a MAP after it) -- otherwise it keeps the
    numbering. Every shape is compared with the same program run through a
    helper variable, which engages none of this."""
    ctx = {
        'ORDERS': _rows({'id': 1, 'customer_id': 7, 'status': 'A', 'amount': 5},
                        {'id': 2, 'customer_id': 7, 'status': 'B', 'amount': 'x'},
                        {'id': 3, 'customer_id': 9, 'status': 'A', 'amount': 1},
                        {'id': 4, 'customer_id': 7, 'status': 'A', 'amount': 8},
                        {'id': 5, 'customer_id': 7, 'status': 'B'}),
        'CUSTOMERS': _rows({'id': 7, 'name': 'n7', 'tier': 'P'}, {'id': 9, 'name': 'n9', 'tier': 'G'}),
        'ITEMS': _rows({'order_id': 1, 'sku': 's1'}, {'order_id': 2, 'sku': 's1'},
                       {'order_id': 4, 'sku': 's2'}, {'order_id': 5, 'sku': 's1'}),
    }
    chain = ('ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
             ' .> LINK(ITEMS, _["orders"]["id"] == _2["order_id"])')
    left_chain = chain.replace('LINK(ITEMS', 'LINK_LEFT(ITEMS')
    import sel.builtins.aggregate as aggregate

    def without_prefilter(src):
        # The same physical tree -- the helper-variable form is not the oracle
        # here, because the logical optimiser (every host) pushes the qualified
        # conjunct below the upper join, where it is evaluated on rows the
        # source program's AND would have short-circuited -- with the join
        # pre-filter switched off.
        real = aggregate._leading_field_conjuncts
        aggregate._leading_field_conjuncts = lambda body, binder: []
        try:
            return _joined(src, ctx)[0]
        finally:
            aggregate._leading_field_conjuncts = real

    for src in [
        # the pushed conjunct reads a number that is text on order 2: an error
        # where that row reaches the pushed FILTER, in both evaluations
        f'FILTER_SRC{chain} .> FILTER(_["status"] $== "A" AND _["orders"]["amount"] > 2 AND _["sku"] $== "s1")',
        f'FILTER_SRC{chain} .> FILTER(_["status"] $== "B" AND _["orders"]["amount"] > 2)',
        # keys observed: the pipeline ends at the FILTER
        f'FILTER_SRC{chain} .> FILTER(_["status"] $== "A" AND _["orders"]["amount"] > 0 AND _["tier"] $== "P")',
        # keys unobserved: a MAP follows, so rows may be dropped at the base
        f'FILTER_SRC{chain} .> FILTER(_["status"] $== "A" AND _["orders"]["amount"] > 0 AND _["tier"] $== "P") .> MAP(RECORD("s", _["sku"]))',
        f'FILTER_SRC{left_chain} .> FILTER(_["status"] $== "A" AND _["orders"]["amount"] > 0) .> MAP(RECORD("s", _["sku"]))',
        # a field the row lacks (order 5 has no amount) and a key read _K
        f'FILTER_SRC{chain} .> FILTER(_["orders"]["amount"] > 0 AND _["status"] $== "B") .> MAP(RECORD("s", _["sku"]))',
        f'FILTER_SRC{chain} .> FILTER(_["status"] $== "A" AND _K > 1) .> MAP(RECORD("s", _["sku"]))',
    ]:
        as_written = _joined(src, ctx)[0]
        assert as_written == without_prefilter(src), src

