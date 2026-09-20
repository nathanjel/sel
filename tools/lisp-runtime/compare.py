#!/usr/bin/env python3
"""Run sequential Lisp comparisons, using already prepared source trees."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--before', type=Path, required=True)
p.add_argument('--after', type=Path, default=ROOT)
p.add_argument('--alias-only', type=Path)
p.add_argument('--args-only', type=Path)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--cache', type=Path, default=Path('/tmp/sel-lisp-runtime/cache'))
a = p.parse_args()
a.output = a.output.resolve()
a.output.mkdir(parents=True, exist_ok=True)
trees = {'before': a.before.resolve(), 'after': a.after.resolve()}
for name, tree in [('alias-only', a.alias_only), ('args-only', a.args_only)]:
    if tree:
        trees[name] = tree.resolve()
env = dict(os.environ, XDG_CACHE_HOME=str(a.cache.resolve()))
sbcl = ['sbcl', '--dynamic-space-size', '4096', '--noinform', '--disable-debugger', '--non-interactive']
reference = {s['id']: s for s in json.loads((ROOT/'tools/scale-test/benchmark_results.json').read_text())}

def check_rows(report):
    # The Lisp runner records its rows; the wrapper owns oracle comparison.
    assert report['passed']
    for scenario in report['scenarios']:
        for field in ['in_memory_rows', 'sql_postgres', 'sql_mariadb', 'has_continuation']:
            assert scenario[field] == reference[scenario['id']][field], (scenario['id'], field)

def run(label, tree, script, extra):
    with (a.output/(label+'.log')).open('w') as log:
        subprocess.run(sbcl+['--load', str(script)], cwd=tree, env={**env, **extra},
                       stdout=log, stderr=subprocess.STDOUT, check=True)

# Finish ASDF compilation before timings. The same sources/harness serve all trees.
for name, tree in trees.items():
    with (a.output/('load-'+name+'.log')).open('w') as log:
        subprocess.run(sbcl+['--load', str(tree/'lisp/bin/boot.lisp'),
                            '--eval', '(ql:quickload :sel-lang/sql :silent t)'],
                       cwd=tree, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)

order = ['before'] + [n for n in ['alias-only', 'args-only'] if n in trees] + ['after', 'after-repeat', 'before-repeat']
summary = {}
for label in order:
    tree = trees[label.removesuffix('-repeat')]
    run(label, tree, ROOT/'tools/scale-test/sel_benchmarks.lisp', {
        'SEL_BENCHMARK_MODE':'steady-state', 'SEL_BENCHMARK_TIMING_MODE':'steady-state',
        'SEL_BENCHMARK_ONLY':','.join('scenario'+str(i) for i in range(1,7)),
        'SEL_BENCHMARK_RUNS':'10', 'SEL_BENCHMARK_WARMUPS':'3',
        'SEL_BENCHMARK_OUTPUT':str(a.output/(label+'.json')),
        'SEL_BENCHMARK_REFERENCE':str(ROOT/'tools/scale-test/benchmark_results.json'),
        'SEL_DATASET_FILE':str(ROOT/'tools/scale-test/dataset-10x.json')})
    d = json.loads((a.output/(label+'.json')).read_text())
    check_rows(d)
    assert d['passed'] and len(d['scenarios']) == 6
    summary[label] = {s['id']: statistics.median(x['prepared_total_ms'] for x in s['samples']) for s in d['scenarios']}
    print(label, summary[label], flush=True)
for mode in ['steady-state', 'gc-controlled']:
    for label in ['before', 'after']:
        name = 's6-'+mode+'-'+label
        run(name, trees[label], ROOT/'tools/scale-test/sel_benchmarks.lisp', {
            'SEL_BENCHMARK_MODE':'steady-state', 'SEL_BENCHMARK_TIMING_MODE':mode,
            'SEL_BENCHMARK_ONLY':'scenario6', 'SEL_BENCHMARK_RUNS':'15', 'SEL_BENCHMARK_WARMUPS':'3',
            'SEL_BENCHMARK_OUTPUT':str(a.output/(name+'.json')),
            'SEL_BENCHMARK_REFERENCE':str(ROOT/'tools/scale-test/benchmark_results.json'),
            'SEL_DATASET_FILE':str(ROOT/'tools/scale-test/dataset-10x.json')})
        d = json.loads((a.output/(name+'.json')).read_text())
        check_rows(d)
        assert d['passed']
        times = [s['prepared_total_ms'] for s in d['scenarios'][0]['samples']]
        summary[name] = {'median_ms':statistics.median(times), 'mean_ms':statistics.mean(times)}
        print(name, summary[name], flush=True)
frames = []
for label in ['before', 'after', 'after-repeat', 'before-repeat']:
    tree = trees[label.removesuffix('-repeat')]
    name = 'mandelbrot-'+label
    run(name, tree, ROOT/'tools/commit-benchmark/mandelbrot.lisp', {
        'MANDEL_RUNS':'11', 'MANDEL_WARMUPS':'3', 'MANDEL_OUTPUT':str(a.output/(name+'.json'))})
    d = json.loads((a.output/(name+'.json')).read_text())
    frames.extend(d['outputs'])
    summary[name] = statistics.median(d['samples_ms'])
    print(name, summary[name], flush=True)
assert len(set(frames)) == 1
assert hashlib.sha256(frames[0].encode()).hexdigest() == '3d50a84d3774807aaaa132e9b6c826a89ad11548ab4e53ced220b0a00854bcba'
(a.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
