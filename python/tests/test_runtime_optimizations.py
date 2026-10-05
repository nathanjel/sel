"""Math-plan dispatch must retain AST results, errors and repeated-run behavior."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from sel import SelError, Value, compile
from sel import eval as E
from sel.eval import Context, eval_node
from sel.math_plan import compile_math_plan


@pytest.mark.parametrize('source', [
    'A + B', 'A - B', 'A * B', 'A / B', 'A % B', '-A', 'ABS(A)',
    'SIGN(A)', 'CEIL(A)', 'FLOOR(A)', 'TRUNC(A)', 'ROUND(A, 1)',
    'POWER(A, 3)', 'MIN(A, B, 0)', 'MAX(A, B, 0)', 'R["x"] + A',
    'A + B * 2', 'ROUND(ABS(A - B) / 3, 2)',
    'A / 0', 'ROUND(A, 1.5)', 'ROUND(A, -1)', 'POWER(A, -1)',
    'MISSING + A', 'R["missing"] + A', 'A + TRUE',
])
def test_math_plan_matches_ast(source):
    tree = compile(source).ast
    plan = compile_math_plan(tree)
    assert plan is not None

    def result(node, root):
        ctx = Context(root)
        try:
            return eval_node(node, ctx).dump()
        except SelError as e:
            return e.code, e.line, e.col, e.offset
        finally:
            assert ctx.depth == 0

    for a, b in [('-12.50', '3.00'), ('0', '0'), ('10000000000000000000', '0.01')]:
        root = Value.from_native({'A': a, 'B': b, 'R': {'x': '4.50'}})
        tree.math_plan = None
        expected = result(tree, root)
        tree.math_plan = plan
        assert result(tree, root) == expected
        assert result(tree, root) == expected


# The plan against the plain tree on awkward operands -- identity rules
# (COERCE), a leaf, folds, and names like the ones generated plan code uses.
AWKWARD = ['A + 0', '0 + A', 'A * 1', 'A - 0 + B', 'MIN(A)', 'MAX(A, B, C)', 'ROUND(A, B)',
           'POWER(B, C)', 'SIGN(A) * C', '-(A + B) * C', 'COUNT(C) * B', 'S0 * K23 + CTX']
DATA = [{'A': '-12.50', 'B': '3.00', 'C': '7'}, {'A': '0', 'B': '0', 'C': '0'},
        {'A': '10000000000000000000', 'B': '0.01', 'C': 'x'}, {'A': 'text', 'B': None, 'C': [1, 2]},
        {'A': True, 'B': '2', 'C': '1'}]
EXTRA = {'R': {'x': '4.50'}, 'S0': '2', 'K23': '3', 'CTX': '4'}


def outcome(run):
    try:
        return run().dump()
    except SelError as e:
        return e.code, e.line, e.col, e.offset


def test_math_plan_matches_ast_on_awkward_operands():
    for source in AWKWARD:
        tree = compile(source).ast
        plan = compile_math_plan(tree)
        assert plan is not None, source
        for data in DATA:
            root = Value.from_native({**EXTRA, **data})
            tree.math_plan = None
            want = outcome(lambda: eval_node(tree, Context(root)))
            tree.math_plan = plan
            assert outcome(lambda: eval_node(tree, Context(root))) == want, (source, data)


# A hot plan runs as one generated function; it answers as the
# interpreter does, compiles on its _PLAN_HOT-th run, and only up to _PLAN_MAX_STEPS.
def test_a_compiled_math_plan_answers_as_the_interpreted_one():
    for source in AWKWARD + ['A + B', 'A - B', 'A * B', 'A / B', 'A % B', '-A', 'ABS(A)', 'CEIL(A)',
                             'FLOOR(A)', 'TRUNC(A)', 'ROUND(A, 1)', 'POWER(A, 3)', 'MIN(A, B, 0)',
                             'R["x"] + A', 'MISSING + A', 'A / 0', 'ROUND(A, 1.5)', 'POWER(A, -1)']:
        plan = compile_math_plan(compile(source).ast)
        compiled = E._compile_math_plan(plan)
        for data in DATA:
            root = Value.from_native({**EXTRA, **data})
            want = outcome(lambda: E._interpret_math_plan(plan, Context(root)))
            assert outcome(lambda: compiled(Context(root))) == want, (source, data)


def test_a_plan_runs_interpreted_until_hot_then_compiled():
    program = compile('A * B - C')
    plan = program.physical_ast().math_plan
    context = {'A': '1.5', 'B': '2', 'C': '1'}
    want = program.run(context).dump()
    for runs in range(2, E._PLAN_HOT + 3):
        assert program.run(context).dump() == want
        assert (plan.run is not None) == (runs >= E._PLAN_HOT), runs


def test_a_plan_past_the_step_cap_stays_interpreted():
    program = compile('MAX(' + ', '.join(['A + B'] * 100) + ')')
    plan = program.physical_ast().math_plan
    assert len(plan.steps) > E._PLAN_MAX_STEPS
    for _ in range(E._PLAN_HOT + 1):
        program.run({'A': '1', 'B': '2'})
    assert plan.run is None


def test_threads_crossing_the_compile_threshold_agree():
    program = compile('A * A - B * B + C')
    context = {'A': '1.25', 'B': '0.5', 'C': '-3'}
    want = program.run(context).dump()
    with ThreadPoolExecutor(8) as pool:
        got = set(pool.map(lambda _: program.run(context).dump(), range(4 * E._PLAN_HOT)))
    assert got == {want}
