"""Item 2: how many Python-level calls two fixed programs make, warm, on the
interpreter the gate runs. A deterministic contract for the evaluator's cost:
lower these when it gets cheaper; never raise them. C calls are not counted --
each costs a fraction of a Python call, and a dispatch table trades Python
calls for dict lookups."""
import gc
import sys
from collections import Counter

import pytest

from sel import Value, compile
from sel.eval import Context, eval_node

PROGRAMS = {
    # One Mandelbrot pixel inside the set: ten iterations under three binder frames.
    'pixel': 'MAP(LIST(1), Y, MAP(LIST(1), X, (cr = -0.25; ci = 0.25; zr = 0.0; zi = 0.0; '
             'escaped = FALSE; iter = 0; MAP(LIST(0, 1, 2, 3, 4, 5, 6, 7, 8, 9), IF(NOT escaped, '
             'IF(zr * zr + zi * zi > 4.0, escaped = TRUE, (tr = zr * zr - zi * zi + cr; '
             'zi = 2.0 * zr * zi + ci; zr = tr; iter += 1)))); iter)))',
    # 200 rows through FILTER, SORT_BY + TAKE (fused into TOP_BY) and MAP.
    'rows': 'L .> FILTER(_["a"] $== "x") .> SORT_BY(_["b"], "DESC") .> TAKE(5) .> MAP(RECORD("b", _["b"]))',
}
CONTEXTS = {'rows': {'L': [{'a': 'x' if i % 3 == 0 else 'y', 'b': i} for i in range(1, 201)]}}
BUDGETS = {'pixel': 2808, 'rows': 5690}


def python_calls(label):
    """Python calls in one evaluation of the program's physical tree, warm. Counted
    below Program.run, whose wrappers (the recursion budget, the collector pause)
    make calls that depend on process state other tests leave behind."""
    program = compile(PROGRAMS[label])
    for _ in range(40):          # warm: caches built, hot math plans compiled
        program.run(CONTEXTS.get(label, {}))
    tree, root = program.physical_ast(), Value.from_native(CONTEXTS.get(label, {}))
    seen = Counter()
    collecting = gc.isenabled()
    gc.disable()                 # no collector pass, and no gc.callbacks, inside the count
    sys.setprofile(lambda frame, event, arg: seen.update((event,)))
    try:
        eval_node(tree, Context(root))
    finally:
        sys.setprofile(None)
        if collecting:
            gc.enable()
    return seen['call']


@pytest.mark.skipif(sys.version_info[:2] != (3, 14), reason='call counts belong to one interpreter')
def test_python_call_budgets():
    for label, budget in BUDGETS.items():
        calls = python_calls(label)
        assert calls == budget, f'{label}: {calls} Python calls, budget {budget}'
