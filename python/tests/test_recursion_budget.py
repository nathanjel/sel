"""The recursion budget (sel/_stack.py): a program under the SPEC 6.4 cap runs,
whatever the interpreter's own limit is, and the limit is handed back as found.

Each program here is one another host answers with a value or E_DEPTH; a
RecursionError escaping instead is the bug."""
import sys
import threading

import pytest
import sel
from sel import SelError, _stack


@pytest.fixture(autouse=True)
def default_limit():
    before = sys.getrecursionlimit()
    sys.setrecursionlimit(1000)
    yield
    sys.setrecursionlimit(before)


def pipe_nest(n):
    s = '1'
    for _ in range(n):
        s = '1 .> MAX(' + s + ')'
    return s


def run(source):
    return sel.evaluate(source)


def test_pipe_nesting_just_under_the_cap_runs():
    assert run(pipe_nest(198)).scalar == '1'


def test_pipe_nesting_at_the_cap_is_e_depth_not_a_recursion_error():
    with pytest.raises(SelError) as info:
        run(pipe_nest(199))
    assert info.value.code == 'E_DEPTH'
    assert (info.value.line, info.value.col) == (1, 1792)


@pytest.mark.parametrize('stage', ['SORT', 'MAP(_)', 'TOP(_, 2)', 'SORT_BY(_)', 'SELECT_COLS(1)'][:4])
def test_a_197_stage_chain_runs(stage):
    assert run('LIST(1, 2)' + f' .> {stage}' * 197 + ' .> COUNT()').scalar in ('1', '2')


def test_a_198_stage_chain_is_e_depth():
    with pytest.raises(SelError) as info:
        run('LIST(1, 2)' + ' .> SORT' * 198 + ' .> COUNT()')
    assert info.value.code == 'E_DEPTH'


def test_a_long_coalesce_chain_is_e_depth_at_the_operator():
    with pytest.raises(SelError) as info:
        sel.compile('a' + ' ?? a' * 3000)
    assert info.value.code == 'E_DEPTH'


def test_deep_interpolation_is_e_depth():
    for n in (49, 50, 400, 3000):
        src = '"{' * n + '1' + '}"' * n
        if n < 50:
            assert run(src).scalar == '1'
        else:
            with pytest.raises(SelError) as info:
                sel.compile(src)
            assert (info.value.code, info.value.line, info.value.col) == ('E_DEPTH', 1, 101)


def test_unterminated_deep_brace_run_is_e_unterminated():
    with pytest.raises(SelError) as info:
        sel.compile('"{' * 600)
    assert (info.value.code, info.value.col) == ('E_UNTERMINATED', 1200)


def test_the_limit_is_handed_back_after_success_and_failure():
    sel.compile(pipe_nest(150))
    assert sys.getrecursionlimit() == 1000
    with pytest.raises(SelError):
        run(pipe_nest(199))
    assert sys.getrecursionlimit() == 1000


def test_a_limit_the_application_changed_meanwhile_is_left_alone():
    with _stack.recursion_budget():
        sys.setrecursionlimit(5555)
    assert sys.getrecursionlimit() == 5555


def test_entered_from_a_deep_caller_the_budget_still_holds():
    def deeper(n):
        return run(pipe_nest(150)) if n == 0 else deeper(n - 1)
    assert deeper(600).scalar == '1'


def test_a_registered_function_that_runs_a_program_nests_budgets():
    from sel import registry
    inner = sel.compile(pipe_nest(190))

    def fn(args, ctx):
        return inner.run({})
    registry.define('BUDGET_PROBE', 0, 0, fn=fn)
    try:
        assert run('BUDGET_PROBE()').scalar == '1'
    finally:
        registry._table.pop('BUDGET_PROBE')
    assert sys.getrecursionlimit() == 1000


def test_concurrent_entries_share_one_raise_and_restore_it_once():
    program = sel.compile(pipe_nest(190))
    errors = []
    barrier = threading.Barrier(6)

    def work():
        try:
            barrier.wait()
            for _ in range(20):
                assert program.run({}).scalar == '1'
        except BaseException as e:      # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert sys.getrecursionlimit() == 1000
