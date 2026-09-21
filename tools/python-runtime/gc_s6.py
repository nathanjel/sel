"""S6 with the cyclic collector on and off, counting collections: the diagnostic
behind WL-001 SEL-0030. Run from a tree root on an idle box:

    PYTHONHASHSEED=0 python3 tools/python-runtime/gc_s6.py <tree-root> <label>

Prepared timing of ten runs each way after two warmups; the dataset and result
reference are the shared scale-test files. A collector-off figure is the join's
own cost; the collector-on figure is what an application with a large resident
dataset pays under default thresholds.
"""
import sys, json, time, gc, statistics
root = sys.argv[1]
sys.path.insert(0, root + '/python'); sys.path.insert(0, root + '/tools/scale-test')
import sel_benchmarks as h
from sel import compile as sel_compile
h.register_benchmark_builtins()
ctx = h.load_context(h.read_json('/home/nathan/workspaces/nth/sel/tools/scale-test/dataset-10x.json'))
ref = json.load(open('/home/nathan/workspaces/nth/sel/tools/scale-test/benchmark_results.json'))
ref = ref if isinstance(ref, dict) else {'scenarios': ref}
prog = sel_compile(next(s for s in ref['scenarios'] if s['id'] == 'scenario6')['query'])
def timed(n):
    out = []
    for _ in range(n):
        t = time.perf_counter(); prog.run(ctx); out.append((time.perf_counter() - t) * 1000)
    return out
timed(2)
gc.collect()
before = gc.get_stats()
on = timed(10)
after = gc.get_stats()
gen2 = after[2]['collections'] - before[2]['collections']; gen1 = after[1]['collections'] - before[1]['collections']
gc.disable(); gc.collect(); off = timed(10); gc.enable()
print(f"{sys.argv[2]:8s} gc on : median {statistics.median(on):.0f} ms  min {min(on):.0f}  max {max(on):.0f}   gen2 collections during 10 runs: {gen2}, gen1: {gen1}")
print(f"{sys.argv[2]:8s} gc off: median {statistics.median(off):.0f} ms  min {min(off):.0f}  max {max(off):.0f}")
