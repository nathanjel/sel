#!/usr/bin/env python3
"""Interleaved A/B of the JS scale-test runner (tools/scale-test/sel_benchmarks.mjs):
a baseline tree (git archive of a revision, e.g. faff480 = 0.9.2) against the working
tree, N alternating repetitions, the minimum over repetitions of each scenario's median
prepared_total_ms (program run + materialisation). Load from other agents makes single
runs noisy; alternation and the minimum are the signal.

  python3 tools/perf/js/ab-scale.py BASE_TREE [reps] [runs] [dataset]
"""
import json, os, statistics, subprocess, sys, tempfile

base = sys.argv[1]
reps = int(sys.argv[2]) if len(sys.argv) > 2 else 5
runs = int(sys.argv[3]) if len(sys.argv) > 3 else 3
dataset = sys.argv[4] if len(sys.argv) > 4 else 'tools/scale-test/dataset-10x.json'
root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

def run(tree, label):
    out = tempfile.mktemp(suffix='.json')
    env = dict(os.environ)
    subprocess.run(['node', 'tools/scale-test/sel_benchmarks.mjs', '--runs', str(runs), '--warmups', '1',
                    '--dataset', dataset, '--reference', 'tools/scale-test/benchmark_results.json',
                    '--output', out], cwd=tree, env=env, check=True, capture_output=True)
    rep = json.load(open(out)); os.unlink(out)
    return {s['id']: s['statistics']['prepared_total_ms']['median_ms'] for s in rep['scenarios']}

res = {'base': {}, 'cur': {}}
for i in range(reps):
    for label, tree in (('base', base), ('cur', root)):
        for sid, ms in run(tree, label).items():
            res[label].setdefault(sid, []).append(ms)
print(f"{'scenario':<12}{'0.9.2 min':>12}{'current min':>14}{'ratio':>8}   (min of {reps} alternating reps, median of {runs} runs each; ms)")
for sid in sorted(res['base']):
    b, c = min(res['base'][sid]), min(res['cur'][sid])
    print(f"{sid:<12}{b:>12.1f}{c:>14.1f}{c / b:>8.2f}")
