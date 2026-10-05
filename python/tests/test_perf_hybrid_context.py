"""execute_hybrid no longer deep-copies the caller's whole context. The
caller's context must still never be written to, for every way a
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


def test_pure_memory_plan_invokes_mutating_callback():
    def poke(args):
        value = args.val(0)
        value.set('k', sel.Value.text('9'))
        return value

    sel.register_function('REVIEW_POKE_1', 1, 1, poke)
    try:
        ctx = sel.Value.from_native({'A': {'k': '1'}})
        plan = hybrid.plan_hybrid(sel.compile('REVIEW_POKE_1(A)'), 'sqlite')
        assert plan.pure_memory
        result = hybrid.execute_hybrid(plan, None, ctx)
        assert result.get('k').as_text() == '9'
        assert ctx.get('A').get('k').as_text() == '1'
    finally:
        sel.registry._table.pop('REVIEW_POKE_1', None)
        sel.registry._host.discard('REVIEW_POKE_1')


def test_callback_mutates_then_raises_sel_error():
    def bad_poke(args):
        value = args.val(0)
        value.set('k', sel.Value.text('99'))
        raise sel.SelError('E_CUSTOM', 'callback error')

    sel.register_function('REVIEW_POKE_ERR', 1, 1, bad_poke)
    try:
        ctx = sel.Value.from_native({'A': {'k': '1'}})
        plan = hybrid.plan_hybrid(sel.compile('REVIEW_POKE_ERR(A)'), 'sqlite')
        with pytest.raises(sel.SelError) as exc:
            hybrid.execute_hybrid(plan, None, ctx)
        assert exc.value.code == 'E_CUSTOM'
        assert ctx.get('A').get('k').as_text() == '1'
    finally:
        sel.registry._table.pop('REVIEW_POKE_ERR', None)
        sel.registry._host.discard('REVIEW_POKE_ERR')


def test_callback_nested_inside_aggregate_or_lazy_branch():
    def poke(args):
        value = args.val(0)
        value.set('k', sel.Value.text('999'))
        return value

    sel.register_function('REVIEW_POKE_NESTED', 1, 1, poke)
    try:
        # Inside lazy branch IF:
        ctx1 = sel.Value.from_native({'A': {'k': '1'}})
        plan1 = hybrid.plan_hybrid(sel.compile('IF(TRUE, REVIEW_POKE_NESTED(A), 0)'), 'sqlite')
        assert plan1.pure_memory
        res1 = hybrid.execute_hybrid(plan1, None, ctx1)
        assert res1.get('k').as_text() == '999'
        assert ctx1.get('A').get('k').as_text() == '1'

        # Inside aggregate body MAP:
        ctx2 = sel.Value.from_native({'A': {'k': '1'}})
        plan2 = hybrid.plan_hybrid(sel.compile('MAP(LIST(1), REVIEW_POKE_NESTED(A))'), 'sqlite')
        assert plan2.pure_memory
        res2 = hybrid.execute_hybrid(plan2, None, ctx2)
        assert res2.values()[0].get('k').as_text() == '999'
        assert ctx2.get('A').get('k').as_text() == '1'
    finally:
        sel.registry._table.pop('REVIEW_POKE_NESTED', None)
        sel.registry._host.discard('REVIEW_POKE_NESTED')


def test_application_function_uses_lower_level_definition_api():
    def poke_low(args, ctx):
        value = args.val(0)
        value.set('k', sel.Value.text('888'))
        return value

    sel.registry.define('REVIEW_POKE_LOW', 1, 1, fn=poke_low)
    try:
        ctx = sel.Value.from_native({'A': {'k': '1'}})
        plan = hybrid.plan_hybrid(sel.compile('REVIEW_POKE_LOW(A)'), 'sqlite')
        assert plan.pure_memory
        result = hybrid.execute_hybrid(plan, None, ctx)
        assert result.get('k').as_text() == '888'
        assert ctx.get('A').get('k').as_text() == '1'
    finally:
        sel.registry._table.pop('REVIEW_POKE_LOW', None)


def test_sql_prefix_followed_by_local_callback():
    from sel.sql import Binding

    def poke(args):
        value = args.val(0)
        value.set('k', sel.Value.text('777'))
        return value

    sel.register_function('REVIEW_POKE_SPLIT', 1, 1, poke)
    try:
        bindings = {'ORDERS': Binding.relation('orders', 'o',
                                               {'ID': Binding.column('id', 'o', 'NUM')})}
        src = 'ORDERS .> SORT_BY(_["id"]) .> MAP(REVIEW_POKE_SPLIT(A))'
        plan = hybrid.plan_hybrid(sel.compile(src), 'sqlite', bindings)
        assert not plan.pure_memory
        assert not plan.pure_sql
        assert plan.sql_statement is not None

        calls = 0
        def fake_runner(sql_text, params):
            nonlocal calls
            calls += 1
            return sel.Value.from_native([{'id': 1}, {'id': 2}])

        ctx = sel.Value.from_native({'A': {'k': '1'}})
        res = hybrid.execute_hybrid(plan, fake_runner, ctx)
        assert calls == 1
        assert res.values()[0].get('k').as_text() == '777'
        assert ctx.get('A').get('k').as_text() == '1'
    finally:
        sel.registry._table.pop('REVIEW_POKE_SPLIT', None)
        sel.registry._host.discard('REVIEW_POKE_SPLIT')


def test_same_plan_executes_twice_no_mutation_leak():
    def poke(args):
        value = args.val(0)
        value.set('k', sel.Value.text('555'))
        return value

    sel.register_function('REVIEW_POKE_TWICE', 1, 1, poke)
    try:
        plan = hybrid.plan_hybrid(sel.compile('REVIEW_POKE_TWICE(A)'), 'sqlite')
        ctx1 = sel.Value.from_native({'A': {'k': '1'}})
        ctx2 = sel.Value.from_native({'A': {'k': '1'}})
        r1 = hybrid.execute_hybrid(plan, None, ctx1)
        r2 = hybrid.execute_hybrid(plan, None, ctx2)
        assert r1.get('k').as_text() == '555'
        assert r2.get('k').as_text() == '555'
        assert ctx1.get('A').get('k').as_text() == '1'
        assert ctx2.get('A').get('k').as_text() == '1'
    finally:
        sel.registry._table.pop('REVIEW_POKE_TWICE', None)
        sel.registry._host.discard('REVIEW_POKE_TWICE')


def test_ast_replacement_from_readonly_to_callback_invalidates_cache():
    def poke(args):
        value = args.val(0)
        value.set('k', sel.Value.text('333'))
        return value

    sel.register_function('REVIEW_POKE_REPLACE', 1, 1, poke)
    try:
        prog = sel.compile('A')
        plan = hybrid.plan_hybrid(prog, 'sqlite')
        ctx = sel.Value.from_native({'A': {'k': '1'}})
        r1 = hybrid.execute_hybrid(plan, None, ctx)
        assert r1.get('k').as_text() == '1'
        assert ctx.get('A').get('k').as_text() == '1'

        # Now replace AST with one containing callback:
        prog.ast = sel.compile('REVIEW_POKE_REPLACE(A)').ast
        r2 = hybrid.execute_hybrid(plan, None, ctx)
        assert r2.get('k').as_text() == '333'
        assert ctx.get('A').get('k').as_text() == '1'
    finally:
        sel.registry._table.pop('REVIEW_POKE_REPLACE', None)
        sel.registry._host.discard('REVIEW_POKE_REPLACE')

