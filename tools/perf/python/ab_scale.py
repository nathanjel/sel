#!/usr/bin/env python3
"""0.9.2-vs-current A/B on the tools/scale-test scenarios, Python host.

  python3 tools/perf/python/ab_scale.py BASE_ROOT [--rounds N] [--runs K] [--only s1,s2]

BASE_ROOT is an extracted `git archive faff480` (0.9.2). Each round runs the
base and the current tree's tools/scale-test/sel_benchmarks.py in fresh
processes, alternating which goes first, one scenario at a time, and keeps the
runner's own program_run_ms / prepared_total_ms minimum plus the children's CPU
time. Output: a table of scenario, 0.9.2, current, ratio (min over rounds).
"""
import argparse, json, os, resource, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCEN = ['scenario1', 'scenario2', 'scenario3', 'scenario4', 'scenario5', 'scenario6']


def run(tree, scenario, runs, warmups):
    env = dict(os.environ, PYTHONPATH=str(tree / 'python'))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'r.json'
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        subprocess.run([sys.executable, str(tree / 'tools/scale-test/sel_benchmarks.py'),
                        '--dataset', str(tree / 'tools/scale-test/dataset-10x.json'),
                        '--reference', str(tree / 'tools/scale-test/benchmark_results.json'),
                        '--only', scenario, '--runs', str(runs), '--warmups', str(warmups),
                        '--output', str(out)], cwd=tree, env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        rep = json.loads(out.read_text())
    sc = rep['scenarios'][0]
    ph = sc['statistics']
    def pick(name):
        return ph[name]['min_ms']
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    return pick('program_run_ms'), pick('prepared_total_ms'), cpu


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('base'); ap.add_argument('--rounds', type=int, default=3)
    ap.add_argument('--runs', type=int, default=3); ap.add_argument('--warmups', type=int, default=1)
    ap.add_argument('--only', default=','.join(SCEN))
    a = ap.parse_args()
    base = Path(a.base)
    print(f'{"scenario":10} {"0.9.2 run":>10} {"now run":>10} {"ratio":>6} | {"0.9.2 tot":>10} {"now tot":>10} {"ratio":>6} | cpu s (0.9.2/now)')
    for s in a.only.split(','):
        res = {'base': [], 'cur': []}
        for r in range(a.rounds):
            order = ('base', 'cur') if r % 2 == 0 else ('cur', 'base')
            for which in order:
                res[which].append(run(base if which == 'base' else ROOT, s, a.runs, a.warmups))
        b = [min(x[i] for x in res['base']) for i in range(3)]
        c = [min(x[i] for x in res['cur']) for i in range(3)]
        print(f'{s:10} {b[0]:10.1f} {c[0]:10.1f} {c[0]/b[0]:6.2f} | {b[1]:10.1f} {c[1]:10.1f} {c[1]/b[1]:6.2f} | {b[2]:.1f}/{c[2]:.1f}', flush=True)


if __name__ == '__main__':
    main()
