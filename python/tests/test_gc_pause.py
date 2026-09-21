"""The collector pause around Program.run (sel/_gc.py): it is handed back as
found, on every exit, at every nesting depth -- and a run leaves no cyclic
garbage for it to have missed."""
import gc
import pytest
import sel
from sel import registry


@pytest.fixture(autouse=True)
def collector_enabled():
    gc.enable()
    yield
    gc.enable()


def test_collector_is_enabled_again_after_a_run():
    assert gc.isenabled()
    assert sel.evaluate('MAP((1, 2, 3), _ * 2)').size() == 3
    assert gc.isenabled()


def test_collector_stays_off_when_the_application_turned_it_off():
    gc.disable()
    try:
        sel.evaluate('SUM((1, 2, 3), _)')
        assert not gc.isenabled()
    finally:
        gc.enable()


def test_collector_is_restored_when_the_program_fails():
    with pytest.raises(sel.SelError) as info:
        sel.evaluate('MAP((1, 2), _["nope"])')
    assert info.value.code == 'E_NO_KEY'
    assert gc.isenabled()


def test_nested_runs_restore_the_collector_once():
    # A host's own builtin that runs a second program inside the first.
    seen = []
    inner = sel.compile('COUNT(L)')

    def fn(args, ctx):
        seen.append(gc.isenabled())
        return inner.run({'L': [1, 2, 3]})
    registry.define('GC_PAUSE_PROBE', 0, 0, fn=fn)
    try:
        assert sel.evaluate('GC_PAUSE_PROBE() + 1').scalar == '4'
    finally:
        registry._table.pop('GC_PAUSE_PROBE')
    assert seen == [False]          # paused inside the outer run
    assert gc.isenabled()           # and back exactly once


PROGRAM = ('L .> LINK_LEFT(R, _1["id"] == _2["id"]) .> MAP(RECORD("id", _["l"]["id"], "k", _["l"]["k"])) '
           '.> BUCKET(_["k"], COUNT(_)) .> SORT_BY(_, "DESC")')


def context():
    return {'L': [{'id': i, 'k': i % 2} for i in range(1, 5)], 'R': [{'id': 1, 'name': 'a'}, {'id': 2, 'name': 'b'}]}


def test_evaluation_creates_no_cyclic_garbage():
    # The justification for pausing: a join, projection, grouping and sort over
    # rows leave nothing for the collector to find. One context reused across
    # runs, as an application serving queries does, so the optimiser's
    # physical tree is built once.
    program = sel.compile(PROGRAM)
    ctx = sel.Value.from_native(context())
    assert program.run(ctx).size() == 2
    gc.collect()
    assert program.run(ctx).size() == 2
    assert gc.collect() == 0


def test_optimiser_garbage_is_bounded_and_collected_when_the_pause_ends():
    # A fresh context per run rebuilds the physical tree, and the math-plan
    # compiler's nested closures form a few reference cycles per plan. They are
    # the only cycles a run makes; they are few, and the collector, handed back
    # at the end of the run, reclaims them at its next pass.
    program = sel.compile(PROGRAM)
    program.run(context())
    gc.collect()
    program.run(context())
    assert gc.isenabled()
    assert gc.collect() < 50
    assert gc.collect() == 0
