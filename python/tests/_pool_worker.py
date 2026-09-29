"""Top-level, importable callables for the ProcessPoolExecutor test: a worker
function must be picklable by reference, so it cannot live in the test module's
local scope."""
import sel


def compile_source(source):
    return sel.compile(source).source


def run_source(source):
    return sel.evaluate(source).dump()


def gc_state_after_run(_):
    import gc
    sel.evaluate('SUM((1, 2, 3), _)')
    return gc.isenabled()
