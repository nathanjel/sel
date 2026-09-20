#!/usr/bin/env python3
"""Separate S4/S6 repetitions without preceding S1/S2/S3/S5 heap history."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--before', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
a.output = a.output.resolve()
summary = {}
for label, tree in [('before', a.before.resolve()), ('after', ROOT)]:
    output = a.output/('targeted-'+label+'.json')
    with (a.output/('targeted-'+label+'.log')).open('w') as log:
        subprocess.run([sys.executable, str(tree/'tools/scale-test/sel_benchmarks.py'),
            '--dataset', str(ROOT/'tools/scale-test/dataset-10x.json'),
            '--reference', str(ROOT/'tools/scale-test/benchmark_results.json'),
            '--only', 'scenario4,scenario6', '--runs', '15', '--warmups', '3', '--output', str(output)],
            cwd=tree, env={**os.environ, 'PYTHONHASHSEED':'0'}, stdout=log, stderr=subprocess.STDOUT, check=True)
    report = json.loads(output.read_text())
    assert report['passed']
    summary[label] = {s['id']:dict(median_ms=statistics.median(x['prepared_total_ms'] for x in s['samples']),
                                 mean_ms=statistics.mean(x['prepared_total_ms'] for x in s['samples'])) for s in report['scenarios']}
    print(label,summary[label],flush=True)
(a.output/'targeted-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
