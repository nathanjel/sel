"""PY-P16: execute_hybrid on a large caller context (it deep-cloned the whole context).
    PYTHONPATH=python python3 tools/perf/python/bench_p16_hybrid_ctx.py"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
import sel
from sel.sql import hybrid, bindings as B

def build():
    # A continuation program (MAP after a split) over a small SQL result, plus a
    # 100k-row context variable the program only reads.
    import importlib
    return None

rows = [{'id': i, 'n': str(i % 7)} for i in range(100_000)]
ctx = sel.Value.from_native({'BIG': rows, 'K': '3'})
program = sel.compile('R = (1, 2, 3); MAP(R, _ + COUNT(BIG) + K)')
plan = hybrid.HybridPlan(continuation_program=program, pure_memory=True)
report('execute_hybrid pure_memory, 100k-row context', lambda: hybrid.execute_hybrid(plan, None, ctx), reps=5)
out = hybrid.execute_hybrid(plan, None, ctx)
print('checksum', checksum((out.dump(), ctx.has('R'))))
assert not ctx.has('R'), 'the caller context was written to'
