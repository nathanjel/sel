"""Math plan (physical tree, what run() evaluates) vs the plain tree walk, after the
evaluate-then-coerce change (PY-C7/C8). In-process, alternating, min of rounds.
    PYTHONPATH=python python3 tools/perf/python/ab_plan_vs_tree.py"""
import random
import sys
import time
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import sel
from sel.eval import eval_node, Context
from sel.value import Value

rnd = random.Random(7)
rows = [{'a': str(rnd.randrange(100)), 'b': str(rnd.randrange(100)), 'c': str(rnd.randrange(10))} for _ in range(20000)]
scalars = {'A': '12', 'B': '7', 'C': '3', 'D': '8'}
WORK = {
    'scalar  A*B+C-D/2 (x2000)': ('A * B + C - D / 2', scalars, 2000),
    'scalar  (A+B)*(C-D)+A*A (x2000)': ('(A + B) * (C - D) + A * A', scalars, 2000),
    'SUM     a*b+1 (20k rows)': ('SUM(R, _["a"] * _["b"] + 1)', {'R': rows}, 1),
    'FILTER  a*2+b > 100 (20k rows)': ('COUNT(FILTER(R, _["a"] * 2 + _["b"] > 100))', {'R': rows}, 1),
    'MAP     (a+b)*c (20k rows)': ('COUNT(MAP(R, (_["a"] + _["b"]) * _["c"]))', {'R': rows}, 1),
}


def time_plan(prog, ctx, reps):
    root = Value.from_native(ctx)
    t = time.process_time()
    for _ in range(reps):
        eval_node(prog.physical_ast(), Context(root))
    return time.process_time() - t


def time_tree(prog, ctx, reps):
    root = Value.from_native(ctx)
    t = time.process_time()
    for _ in range(reps):
        eval_node(prog.ast, Context(root))
    return time.process_time() - t


print('%-36s %12s %12s %8s' % ('workload', 'plan', 'tree walk', 'plan/tree'))
for name, (src, ctx, reps) in WORK.items():
    prog = sel.compile(src)
    assert sel.Value and eval_node(prog.physical_ast(), Context(Value.from_native(ctx))).scalar == eval_node(prog.ast, Context(Value.from_native(ctx))).scalar
    best = {'plan': 1e9, 'tree': 1e9}
    for _ in range(5):                      # alternate; minimum of 5 rounds
        best['plan'] = min(best['plan'], time_plan(prog, ctx, reps))
        best['tree'] = min(best['tree'], time_tree(prog, ctx, reps))
    print('%-36s %9.2f ms %9.2f ms %8.2f' % (name, best['plan'] * 1e3, best['tree'] * 1e3, best['plan'] / best['tree']))
