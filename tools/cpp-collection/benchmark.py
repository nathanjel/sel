#!/usr/bin/env python3
"""Compare two already-built C++ trees, with no builds during timed runs."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--before', type=Path, required=True)
parser.add_argument('--after', type=Path, default=ROOT)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--runs', type=int, default=7)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
summary = {}
# Opposite orders help expose drift. Startup, input loading and cloning are
# outside prepared_total_ms; clone samples remain in the raw reports.
for label in ['before', 'after', 'after-repeat', 'before-repeat']:
    tree = (args.before if label.startswith('before') else args.after).resolve()
    binary = tree / 'cpp/build/scale-bench'
    output = args.output / (label + '.json')
    with (args.output / (label + '.log')).open('w') as log:
        subprocess.run([str(binary), '--dataset', str(ROOT/'tools/scale-test/dataset-10x.json'),
                        '--reference', str(ROOT/'tools/scale-test/benchmark_results.json'),
                        '--runs', str(args.runs), '--warmups', '3', '--output', str(output.resolve())],
                       cwd=tree, stdout=log, stderr=subprocess.STDOUT, check=True)
    report = json.loads(output.read_text())
    assert report['passed'], label
    summary[label] = {s['id']: statistics.median(x['prepared_total_ms'] for x in s['samples'])
                      for s in report['scenarios']}
    print(label, summary[label], flush=True)
(args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
