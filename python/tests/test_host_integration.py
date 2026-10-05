"""Python's own integration lane: the process-level guarantees a host application relies on -- errors that
survive pickling and copying (multiprocessing, caching), the cyclic collector
handed back across overlapping threads and failures, import leaving the
application's interpreter settings alone, and two logical clients in one
process. The API contracts every host shares are probed by tools/api.py-style
drivers (tools/api-pins.txt)."""
import concurrent.futures
import copy
import gc
import pickle
import subprocess
import sys
import threading

import pytest
import sel
from sel import SelError

from _pool_worker import compile_source, gc_state_after_run, run_source


def error_of(source, run=False):
    with pytest.raises(SelError) as info:
        (sel.evaluate if run else sel.compile)(source)
    return info.value


# --- errors survive pickling and copying -------------------------------------

@pytest.mark.parametrize('roundtrip', [
    lambda e: pickle.loads(pickle.dumps(e)),
    lambda e: pickle.loads(pickle.dumps(e, protocol=pickle.HIGHEST_PROTOCOL)),
    copy.copy,
    copy.deepcopy,
], ids=['pickle', 'pickle-highest', 'copy', 'deepcopy'])
def test_a_sel_error_survives_pickle_and_copy(roundtrip):
    original = error_of('1 +\n  * 2')
    clone = roundtrip(original)
    assert isinstance(clone, SelError)
    assert (clone.code, clone.line, clone.col, clone.offset) == \
        (original.code, original.line, original.col, original.offset)
    assert clone.message == original.message
    assert str(clone) == str(original)


def test_a_runtime_error_with_no_position_survives_too():
    original = SelError('E_BAD_ARG', 'no position')
    clone = pickle.loads(pickle.dumps(original))
    assert (clone.code, clone.line, clone.col, clone.offset, clone.message) == \
        ('E_BAD_ARG', 0, 0, 0, 'no position')


def test_a_sel_error_raised_in_a_pool_worker_reaches_the_parent():
    # Before the fix one error killed the worker's result channel and the whole pool
    # reported BrokenProcessPool.
    with concurrent.futures.ProcessPoolExecutor(1) as pool:
        with pytest.raises(SelError) as info:
            pool.submit(compile_source, '1 +').result(timeout=60)
        assert info.value.code == 'E_SYNTAX'
        # ... and the pool is still usable afterwards.
        assert pool.submit(run_source, '1 + 2').result(timeout=60) == 't"3"'
        with pytest.raises(SelError) as again:
            pool.submit(run_source, 'A / 0').result(timeout=60)
        assert again.value.code in ('E_UNDEF_VAR', 'E_DIV_ZERO')


def test_the_collector_is_enabled_in_a_pool_worker_after_runs():
    with concurrent.futures.ProcessPoolExecutor(2) as pool:
        assert all(pool.map(gc_state_after_run, range(4)))


# --- the collector pause under overlapping threads ----------------------------

def test_collector_survives_overlapping_runs_that_fail():
    """Threads that raise part way through a run and threads that finish normally,
    overlapping: the collector is on again once the last of them leaves."""
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    ok = sel.compile('1 + 1')
    bad = sel.compile('MAP((1, 2), _ / 0)')
    try:
        for _ in range(20):
            barrier = threading.Barrier(6)

            def work(n):
                barrier.wait()
                for _ in range(150):
                    if n % 2:
                        with pytest.raises(SelError):
                            bad.run({})
                    else:
                        ok.run({})
            threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            assert gc.isenabled()
    finally:
        sys.setswitchinterval(interval)


def test_collector_left_off_by_the_application_stays_off_across_threads():
    gc.disable()
    try:
        threads = [threading.Thread(target=lambda: sel.evaluate('1 + 1')) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not gc.isenabled()
    finally:
        gc.enable()


# --- import leaves the interpreter's settings alone ---------------------------

def test_import_leaves_the_deployers_int_max_str_digits_alone():
    code = 'import sys, sel; print(sys.get_int_max_str_digits())'
    out = subprocess.run([sys.executable, '-X', 'int_max_str_digits=5000', '-c', code],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == '5000'


def test_a_limit_set_after_import_never_leaks_a_value_error():
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        assert sel.evaluate('9' * 700 + ' + 1').dump().endswith('0"')
        assert sel.evaluate('LEN(POWER(10, 1000))').as_text() == '1001'
    finally:
        sys.set_int_max_str_digits(previous)


# two clients ----------------------------------------------------------------

def test_two_logical_clients_in_one_process_do_not_see_each_other():
    failing = sel.compile('A / B')
    working = sel.compile('A / B')
    seen = {'failing': [], 'working': []}

    def failing_client():
        ctx = sel.Value.from_native({'A': 1, 'B': 0})
        for _ in range(300):
            try:
                failing.run(ctx)
                seen['failing'].append('no error')
            except SelError as e:
                seen['failing'].append(e.code)

    def working_client():
        ctx = sel.Value.from_native({'A': 6, 'B': 3})
        for _ in range(300):
            seen['working'].append(working.run(ctx).as_text())

    threads = [threading.Thread(target=failing_client), threading.Thread(target=working_client)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(seen['failing']) == {'E_DIV_ZERO'}
    assert set(seen['working']) == {'2'}


def test_a_program_is_reusable_after_caught_errors():
    program = sel.compile('MAP(L, _ / D)')
    for _ in range(5):
        with pytest.raises(SelError):
            program.run({'L': [1, 2, 3], 'D': 0})
        assert program.run({'L': [2, 4], 'D': 2}).dump() == '-{"1"=t"1", "2"=t"2"}'
