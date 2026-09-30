"""PY-P16: execute_hybrid no longer deep-copies the caller's whole context. The
caller's context must still never be written to (PY-C51), for every way a
continuation can write: a new name, a nested write through an index, a compound
assignment, an assignment inside an aggregate body."""
import pytest
import sel
from sel.sql import hybrid


def run(src, ctx):
    program = sel.compile(src)
    plan = hybrid.HybridPlan(continuation_program=program, pure_memory=True)
    return hybrid.execute_hybrid(plan, None, ctx)


def fresh():
    return sel.Value.from_native({'A': [{'x': '1'}, {'x': '2'}], 'B': '5', 'C': {'k': '7'}})


@pytest.mark.parametrize('src', [
    'R = 1; R',
    'A[1]["x"] = 9; A[1]["x"]',
    'B += 1; B',
    'C["k"] = 0; C["new"] = 1; COUNT(C)',
    'SUM(A, X, (B = 100; 1))',
    'L = A; L[1]["x"] = 99; L[1]["x"]',
    'MAP(A, _["x"] = 5)',
])
def test_the_callers_context_is_never_written_to(src):
    ctx = fresh()
    before = ctx.dump()
    try:
        run(src, ctx)
    except sel.SelError:
        pass                               # an error is fine; a write is not
    assert ctx.dump() == before, src


def test_a_read_only_continuation_shares_the_variables():
    ctx = fresh()
    out = run('COUNT(A) + B', ctx)
    assert out.scalar == '7'


def test_the_result_of_a_written_variable_is_private():
    ctx = fresh()
    out = run('A[1]["x"] = 9; A', ctx)
    assert out.values()[0].get('x').scalar == '9'
    assert ctx.get('A').values()[0].get('x').scalar == '1'


def test_a_native_context_is_converted_fresh():
    program = sel.compile('R = 1; R + X')
    plan = hybrid.HybridPlan(continuation_program=program, pure_memory=True)
    native = {'X': '2'}
    assert hybrid.execute_hybrid(plan, None, native).scalar == '3'
    assert native == {'X': '2'}
