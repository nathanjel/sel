#!/usr/bin/env python3
"""Sequential before/after comparison, including reversed-order repetitions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--before', type=Path, required=True)
p.add_argument('--after', type=Path, default=ROOT)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
a.output = a.output.resolve()
a.output.mkdir(parents=True, exist_ok=True)
trees = dict(before=a.before.resolve(), after=a.after.resolve())
summary = {}
for label in ['before', 'after', 'after-repeat', 'before-repeat']:
    tree = trees[label.removesuffix('-repeat')]
    env = dict(os.environ, PYTHONPATH=str(tree/'python'), PYTHONHASHSEED='0')
    output = a.output/(label+'.json')
    with (a.output/(label+'.log')).open('w') as log:
        subprocess.run([sys.executable, str(tree/'tools/scale-test/sel_benchmarks.py'),
            '--dataset', str(ROOT/'tools/scale-test/dataset-10x.json'),
            '--reference', str(ROOT/'tools/scale-test/benchmark_results.json'),
            '--runs', '7', '--warmups', '3', '--output', str(output)],
            cwd=tree, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    report = json.loads(output.read_text())
    assert report['passed'] and len(report['scenarios']) == 6
    summary[label] = {}
    for s in report['scenarios']:
        assert s['passed'] and s['context_unchanged']
        times = [x['prepared_total_ms'] for x in s['samples']]
        summary[label][s['id']] = dict(median_ms=statistics.median(times), mean_ms=statistics.mean(times))
    m = a.output/('mandelbrot-'+label+'.json')
    subprocess.run([sys.executable, str(ROOT/'tools/commit-benchmark/mandelbrot.py'), str(m)],
                   cwd=tree, env={**env, 'MANDEL_RUNS':'11', 'MANDEL_WARMUPS':'3'}, check=True)
    report = json.loads(m.read_text())
    for frame in report['outputs']:
        assert hashlib.sha256(frame.encode()).hexdigest() == '3d50a84d3774807aaaa132e9b6c826a89ad11548ab4e53ced220b0a00854bcba'
    summary[label]['mandelbrot'] = dict(median_ms=statistics.median(report['samples_ms']), mean_ms=statistics.mean(report['samples_ms']))
    for probe, script in [('arithmetic', 'tools/python-runtime/arithmetic.py'), ('micro', 'tools/benchmark-python-runtime.py'), ('metadata', 'tools/python-runtime/metadata.py')]:
        with (a.output/(probe+'-'+label+'.json')).open('w') as f:
            subprocess.run([sys.executable, str(ROOT/script)], cwd=tree, env=env, stdout=f, check=True)
    subprocess.run([sys.executable, str(ROOT/'tools/commit-benchmark/power-profile.py'),
                    str(a.output/('power-profile-'+label+'.json'))], cwd=tree, env=env, check=True)
    print(label, summary[label], flush=True)
(a.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
