#!/usr/bin/env python3
"""Diagnose S6 GC with per-sample versus per-batch context validation.
Generates an instrumented copy of the shared harness under --work, leaving the
standard benchmark unchanged. GC time is CPU time, not wall time to subtract.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--before', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--work', type=Path, default=Path('/tmp/sel-lisp-profile'))
a = p.parse_args()
a.output = a.output.resolve(); a.work = a.work.resolve()
a.output.mkdir(parents=True, exist_ok=True); a.work.mkdir(parents=True, exist_ok=True)
source = (ROOT/'tools/scale-test/sel_benchmarks.lisp').read_text()
def replace_once(text, old, new):
    assert text.count(old) == 1, 'shared harness changed: review instrumentation'
    return text.replace(old, new, 1)
source = replace_once(source,
    '(let* ((prepared-start (get-internal-real-time))',
    '(let* ((allocation-start (sb-ext:get-bytes-consed))\n         (gc-start sb-ext:*gc-run-time*)\n         (prepared-start (get-internal-real-time))')
source = replace_once(source,
    '(benchmark-ms prepared-start materialize-stop))))',
    '(benchmark-ms prepared-start materialize-stop)\n            (- (sb-ext:get-bytes-consed) allocation-start)\n            (benchmark-ms gc-start sb-ext:*gc-run-time*))))')
source = source.replace('(actual rows run-ms materialize-ms prepared-ms)',
                        '(actual rows run-ms materialize-ms prepared-ms allocated-bytes gc-cpu-ms)')
source = source.replace('(ignore actual run-ms materialize-ms prepared-ms)',
                        '(ignore actual run-ms materialize-ms prepared-ms allocated-bytes gc-cpu-ms)')
source = replace_once(source, '(list (cons "program_run_ms" run-ms)',
    '(list (cons "allocated_bytes" allocated-bytes) (cons "gc_cpu_ms" gc-cpu-ms)\n                                   (cons "program_run_ms" run-ms)')
check = '''                      (unless (string= (sel:value-dump ctx) context-signature)
                        (push "measured run changed prepared context" failures))'''
batch = replace_once(source, check, '')
batch = replace_once(batch, '              (setf samples (nreverse samples))',
    '''              (unless (string= (sel:value-dump ctx) context-signature)
                (setf context-unchanged nil)
                (push "measured batch changed prepared context" failures))
              (setf samples (nreverse samples))''')
reference = {s['id']:s for s in json.loads((ROOT/'tools/scale-test/benchmark_results.json').read_text())}
summary = {}
for validation, label in [('sample','before'), ('sample','after'), ('batch','after'), ('batch','before')]:
    name = 'profile-'+validation+'-'+label
    script = a.work/(validation+'.lisp')
    script.write_text(source if validation == 'sample' else batch)
    tree = a.before.resolve() if label == 'before' else ROOT
    env = dict(os.environ, XDG_CACHE_HOME='/tmp/sel-lisp-runtime/cache',
        SEL_BENCHMARK_MODE='steady-state', SEL_BENCHMARK_TIMING_MODE='steady-state',
        SEL_BENCHMARK_ONLY='scenario6', SEL_BENCHMARK_RUNS='20', SEL_BENCHMARK_WARMUPS='3',
        SEL_BENCHMARK_OUTPUT=str(a.output/(name+'.json')),
        SEL_BENCHMARK_REFERENCE=str(ROOT/'tools/scale-test/benchmark_results.json'),
        SEL_DATASET_FILE=str(ROOT/'tools/scale-test/dataset-10x.json'))
    with (a.output/(name+'.log')).open('w') as log:
        subprocess.run(['sbcl','--dynamic-space-size','4096','--noinform','--disable-debugger',
                        '--non-interactive','--load',str(script)],cwd=tree,env=env,
                       stdout=log,stderr=subprocess.STDOUT,check=True)
    report = json.loads((a.output/(name+'.json')).read_text())
    assert report['passed'] and len(report['scenarios']) == 1
    scenario = report['scenarios'][0]
    assert scenario['context_unchanged'] and len(scenario['samples']) == 20
    assert scenario['in_memory_rows'] == reference['scenario6']['in_memory_rows']
    report['metadata']['context_validation'] = validation
    (a.output/(name+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    summary[name] = {field:{'median':statistics.median(s[field] for s in scenario['samples']),
                            'mean':statistics.mean(s[field] for s in scenario['samples'])}
                     for field in ['prepared_total_ms','allocated_bytes','gc_cpu_ms']}
    print(name, summary[name],flush=True)
(a.output/'profile-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
