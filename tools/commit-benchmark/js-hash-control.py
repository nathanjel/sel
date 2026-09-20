#!/usr/bin/env python3
"""Diagnostic only: move per-sample context hash checks to the batch boundary.
Output parity is still checked for every run, input before validation and after
its last measured execution. Runtime sources are unchanged.
"""
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];BASE=Path('/tmp/sel-commit-benchmark');OUT=ROOT/'docs/interim/commit-benchmark'
source=(ROOT/'tools/scale-test/sel_benchmarks.mjs').read_text()
source=source.replace('if (structuralHash(context) !== contextSignature) {','if (run === runs - 1 && structuralHash(context) !== contextSignature) {')
for rev in ['c5a8991','619bc31','1614eed','ef836bf']:
 print(rev,flush=True)
 p=BASE/rev/'tools/scale-test/sel_benchmarks-boundary-hash.mjs';p.write_text(source)
 cmd=['node',str(p),'--dataset',str(ROOT/'tools/scale-test/dataset-10x.json'),'--reference',str(ROOT/'tools/scale-test/benchmark_results.json'),'--runs','20','--warmups','5','--only','scenario2','--output',str(OUT/(rev+'-js-s2-boundary-hash.json'))]
 with (BASE/(rev+'-js-s2-boundary-hash.log')).open('w') as log:
  subprocess.run(cmd,cwd=BASE/rev,stdout=log,stderr=subprocess.STDOUT,check=True)
