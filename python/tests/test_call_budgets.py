"""How many Python-level calls two fixed programs make, warm, on the
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
# rows went 5360 -> 5365 with the collector depth fix (fd24024): a fresh value a
# collector keeps without copying is depth-checked, one call per collected row
# (MAP over TAKE(5)). That is the price of a correctness fix, not a regression.
# pixel went 2253 -> 2252 when Value.entries() became a zip instead of a
# generator (one frame resumption per call, and the program asks once); rows
# 5365 -> 5364 when TOP_BY's form came from registry.sort_form (one memoised
# lookup where two Args.node calls were), then 5364 -> 5363 when the
# aggregates' "may this body write?" walk became parser.may_write (the old
# copy ran a function-level import on every call), then 5363 -> 5358 when
# lexer.ascii_upper took str.upper() for an all-ASCII string instead of a
# generator over its characters (the sort direction "DESC" is folded once).
# Both went down by one (2252 -> 2251, 5358 -> 5357) when the count started
# after the Context is built, as run() builds it before it evaluates, so that
# the count could see the write_free flag run() sets on it; rows then went
# 5357 -> 4987 because it is a write-free program (no assignment, no host
# function): FILTER, MAP and RECORD hold what they collect instead of copying
# it (SPEC 3.4, Context.write_free).
# pixel 2251 -> 2239 and rows 4987 -> 4977 when a strict call evaluated its
# arguments in _eval_call's own loop instead of through Args.val, one call per
# argument; rows 4977 -> 4937 when a RECORD with literal keys ran on its own
# evaluator (structure._eval_record: no Args, no key values, no _record call).
# pixel 2239 -> 2179 and rows 4937 -> 4047 when MAP and FILTER ran their
# loops themselves instead of through walk() with a visit (and FILTER a keep
# and a kept) call per element, and every aggregate whose body never reads
# _K took its elements without the key text iter_elements built for each.
# pixel 2179 -> 2029 and rows 4047 -> 2247 when FILTER, IF and COND asked
# whether a comparison, $==, $!=, AND or OR holds without building its BOOL
# (eval.eval_cond), and a comparison read a number or ASCII text literal on
# its right where its node keeps it instead of building a Value per row.
BUDGETS = {'pixel': 2029, 'rows': 2247}


def python_calls(label):
    """Python calls in one evaluation of the program's physical tree, warm. Counted
    below Program.run, whose wrappers (the recursion budget, the collector pause)
    make calls that depend on process state other tests leave behind."""
    program = compile(PROGRAMS[label])
    for _ in range(40):          # warm: caches built, hot math plans compiled
        program.run(CONTEXTS.get(label, {}))
    (tree, write_free), root = program._physical_plan(), Value.from_native(CONTEXTS.get(label, {}))
    ctx = Context(root)
    ctx.write_free = write_free  # as run() sets it
    seen = Counter()
    collecting = gc.isenabled()
    gc.disable()                 # no collector pass, and no gc.callbacks, inside the count
    sys.setprofile(lambda frame, event, arg: seen.update((event,)))
    try:
        eval_node(tree, ctx)
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
