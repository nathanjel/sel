"""Math-plan dispatch must retain AST results, errors and repeated-run behavior."""
import pytest

from sel import SelError, Value, compile
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
