#!/usr/bin/env python3
"""Per-function cProfile delta between two trees for one scale-test scenario.

  python3 tools/perf/python/profile_diff.py BASE_ROOT scenarioN [top]

Runs profile_scale.py's measurement on BASE_ROOT and on this checkout (each in a
fresh process), then prints the functions whose own time (tottime) or call count
changed most: where the current tree spends what 0.9.2 did not.
"""
import pickle, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CHILD = r'''
import cProfile, importlib.util, json, pickle, pstats, sys
from pathlib import Path
tree, scenario, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
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
pr = cProfile.Profile(); pr.enable(); p.run(ctx); pr.disable()
st = pstats.Stats(pr)
rows = {}
for (f, l, n), (cc, nc, tt, ct, callers) in st.stats.items():
    key = (Path(f).name, n)
    a = rows.get(key, (0, 0.0, 0.0)); rows[key] = (a[0] + nc, a[1] + tt, a[2] + ct)
pickle.dump(rows, open(out, 'wb'))
'''

def prof(tree, scenario):
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'p.pkl'
        subprocess.run([sys.executable, '-c', CHILD, str(tree), scenario, str(out)], check=True)
        return pickle.load(open(out, 'rb'))

base = prof(Path(sys.argv[1]), sys.argv[2])
cur = prof(ROOT, sys.argv[2])
top = int(sys.argv[3]) if len(sys.argv) > 3 else 25
keys = set(base) | set(cur)
delta = []
for k in keys:
    b = base.get(k, (0, 0.0, 0.0)); c = cur.get(k, (0, 0.0, 0.0))
    delta.append((c[1] - b[1], k, b, c))
delta.sort(reverse=True)
print(f'{"function":44} {"calls b":>9} {"calls c":>9} {"own b":>8} {"own c":>8} {"d own":>8}')
for d, k, b, c in delta[:top]:
    print(f'{k[0]+":"+k[1]:44} {b[0]:9d} {c[0]:9d} {b[1]:8.3f} {c[1]:8.3f} {d:8.3f}')
print('... biggest savings:')
for d, k, b, c in delta[-6:]:
    print(f'{k[0]+":"+k[1]:44} {b[0]:9d} {c[0]:9d} {b[1]:8.3f} {c[1]:8.3f} {d:8.3f}')
print('total own time', sum(v[1] for v in base.values()), sum(v[1] for v in cur.values()))
