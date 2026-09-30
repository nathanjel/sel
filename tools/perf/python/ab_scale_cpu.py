#!/usr/bin/env python3
"""0.9.2-vs-current on the scale-test scenarios by IN-PROCESS CPU time.

  python3 tools/perf/python/ab_scale_cpu.py BASE_ROOT [--rounds N] [--runs K] [--only s1,s2]

Like ab_scale.py, but each child loads the dataset once and times only
program.run() with time.process_time(), so a loaded machine moves the numbers
much less than wall clock does. Base and current alternate, one fresh process per
measurement; the table keeps the best median per tree.
"""
import argparse, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CHILD = r'''
import importlib.util, json, statistics, sys, time
from pathlib import Path
tree, scenario, runs = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
sys.path.insert(0, str(tree / 'python')); sys.path.insert(0, str(tree / 'tools/scale-test'))
spec = importlib.util.spec_from_file_location('sel_benchmarks', tree / 'tools/scale-test/sel_benchmarks.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.register_benchmark_builtins()
ds = mod.read_json(tree / 'tools/scale-test/dataset-10x.json', parse_float=str)
ref = json.load(open(tree / 'tools/scale-test/benchmark_results.json'))
q = next(x['query'] for x in ref if x['id'] == scenario)
ctx = mod.load_context(ds)
from sel import compile
p = compile(q); p.run(ctx)
xs = []
for _ in range(runs):
    t = time.process_time(); p.run(ctx); xs.append(time.process_time() - t)
print(json.dumps([min(xs), statistics.median(xs)]))
'''

def one(tree, scenario, runs):
    out = subprocess.run([sys.executable, '-c', CHILD, str(tree), scenario, str(runs)],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out.strip().splitlines()[-1])

ap = argparse.ArgumentParser()
ap.add_argument('base'); ap.add_argument('--rounds', type=int, default=3)
ap.add_argument('--runs', type=int, default=3)
ap.add_argument('--only', default='scenario1,scenario2,scenario3,scenario4,scenario5,scenario6')
a = ap.parse_args()
print(f'{"scenario":10} {"0.9.2 cpu s":>12} {"now cpu s":>10} {"ratio":>6}   (best median of {a.rounds} alternating processes)')
for s in a.only.split(','):
    res = {'b': [], 'c': []}
    for r in range(a.rounds):
        for which in (('b', 'c') if r % 2 == 0 else ('c', 'b')):
            res[which].append(one(a.base if which == 'b' else ROOT, s, a.runs)[1])
    b, c = min(res['b']), min(res['c'])
    print(f'{s:10} {b:12.3f} {c:10.3f} {c/b:6.2f}', flush=True)
