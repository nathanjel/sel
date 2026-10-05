"""Evaluation order and optimiser transparency: the optimised tree must be
indistinguishable from the plain one -- same value, same error, same position --
and a program stays usable after it raised."""
import pytest
import sel
from sel import SelError
from sel.eval import Context, eval_node
from sel.optimizer import optimize_ast


def plain(source, ctx=None):
    program = sel.compile(source)
    return eval_node(program.ast, Context(sel.Value.from_native(ctx or {})))


def planned(source, ctx=None):
    return sel.compile(source).run(ctx or {})


def outcome(fn, source, ctx=None):
    try:
        return ('ok', fn(source, ctx).dump())
    except SelError as e:
        return (e.code, e.line, e.col)


PROGRAMS = [
    'MAX(TRUE, U)', 'MIN(1, "x", Y)', 'ROUND("x", Y)', '"abc" + 1/0', 'A = "x"; A + B',
    'A = LIST(1,2); A + LEN((A[1] = 10))', 'A = LIST(1,2); A * LEN((A[1] = 10))',
    'A = LIST(1,2); MAX(A, LEN((A[1] = 10)))', 'A = "x"; (A + 0) * B', 'A = "x"; (0 + A) * B',
    'A = "x"; (A * 1) + B', 'A = "x"; (A - 0) + B', 'A = "x"; A + 0', 'A = "1.50"; A + 0',
    'X = "x"; ROUND(X, Y)', 'X = "x"; ROUND(1.5, X)', 'X = "x"; POWER(X, Y)',
    'MIN(1, 2, "x", Y)', 'MAX(1 / 0, "x")', 'A = 1; A + (A = 5)', '-"x" + Y', 'ABS(Z)',
]


@pytest.mark.parametrize('source', PROGRAMS)
def test_the_math_plan_is_transparent(source):
    assert outcome(planned, source) == outcome(plain, source)


def test_a_later_operands_error_beats_an_earlier_coercion():
    with pytest.raises(SelError) as info:
        planned('MAX(TRUE, U)')
    assert (info.value.code, info.value.col) == ('E_UNDEF_VAR', 11)


def test_an_operand_read_earlier_sees_a_later_mutation():
    assert planned('A = LIST(1,2); A + LEN((A[1] = 10))').scalar == '12'


def test_a_dropped_identity_operation_still_coerces_at_its_own_point():
    # `A + 0` is dropped by copy propagation; the coercion must not move to
    # whatever consumes the slot, or B's undefined error would come first.
    with pytest.raises(SelError) as info:
        planned('A = "x"; (A + 0) * B')
    assert info.value.code == 'E_NOT_NUM'


def top_names(source):
    tree = optimize_ast(sel.compile(source).ast)
    names = []
    node = tree
    while node is not None and node.t == 'call':
        names.append(node.name)
        node = node.args[0] if node.args else None
    return names


def test_sort_and_take_fuse_only_for_a_literal_count_of_one_or_more():
    assert 'TOP' in top_names('LIST(3,1,2) .> SORT() .> TAKE(2)')
    for count in ('0', 'N', '1 + 1', '(N = 2)'):
        assert 'TOP' not in top_names(f'N = 2; LIST(3,1,2) .> SORT() .> TAKE({count})'), count


def test_sort_by_key_error_comes_before_a_bad_count():
    with pytest.raises(SelError) as info:
        planned('LIST(RECORD("a",1)) .> SORT_BY(_["z"]) .> TAKE(0)')
    assert info.value.code == 'E_NO_KEY'


def test_take_zero_still_evaluates_the_sort_keys():
    with pytest.raises(SelError) as info:
        planned('LIST(1) .> SORT_BY(1 / 0) .> TAKE(0)')
    assert info.value.code == 'E_DIV_ZERO'


def test_filter_fusion_keeps_the_first_predicates_error():
    for second in ('_', '1', '"x"', 'NULL', '_K'):
        with pytest.raises(SelError) as info:
            planned(f'LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER({second})')
        assert info.value.code == 'E_DIV_ZERO', second


def test_a_fused_or_dropped_stage_reports_at_the_call_it_replaced():
    for source, code, col in [
        ('NOT TAKE(TAKE(LIST(1), 3), 2)', 'E_NOT_BOOL', 5),
        ('NOT DROP(DROP(LIST(1),1),1)', 'E_NO_SCALAR', 5),
        ('NOT FILTER(LIST(1), TRUE)', 'E_NOT_BOOL', 5),
        ('X = LIST(1); NOT TAKE(SORT(X), 1)', 'E_NOT_BOOL', 18),
    ]:
        with pytest.raises(SelError) as info:
            planned(source)
        assert (info.value.code, info.value.col) == (code, col), source


def test_a_program_is_reusable_after_it_raised():
    program = sel.compile('A + B')
    with pytest.raises(SelError):
        program.run({'A': 'x', 'B': 1})
    assert program.run({'A': 1, 'B': 2}).scalar == '3'
    with pytest.raises(SelError):
        program.run({'A': 1})
    assert program.run({'A': 2, 'B': 2}).scalar == '4'


@pytest.mark.parametrize('body', [
    'MAP(LIST(1,2), {c})', 'FILTER(LIST(1,2), {c})', 'SORT_BY(LIST(1,2), {c})',
    'TOP_BY(LIST(1,2), {c}, 1)', 'BUCKET(LIST(1,2), {c})', 'SUM(LIST(1,2), {c})',
    'ALL(LIST(1,2), {c} > 0)',
])
def test_a_long_flat_chain_in_an_aggregate_body_is_e_depth(body):
    chain = '+'.join(['_'] * 20000)
    with pytest.raises(SelError) as info:
        sel.evaluate(body.format(c=chain))
    assert info.value.code == 'E_DEPTH'


def test_a_long_flat_chain_in_a_join_filter_is_e_depth():
    chain = '+'.join(['_["a"]'] * 20000)
    source = ('COUNT(FILTER(LINK(LIST(RECORD("a",1)), LIST(RECORD("a",1)), _1["a"]==_2["a"]), '
              + chain + '))')
    with pytest.raises(SelError) as info:
        sel.evaluate(source)
    assert info.value.code == 'E_DEPTH'
    chain1 = '+'.join(['_1["a"]'] * 20000)
    with pytest.raises(SelError) as info:
        sel.evaluate('COUNT(LINK(LIST(RECORD("a",1)), LIST(RECORD("a",1)), ' + chain1 + '))')
    assert info.value.code == 'E_DEPTH'
