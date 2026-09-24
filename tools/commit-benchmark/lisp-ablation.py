#!/usr/bin/env python3
"""Repeat noisy Lisp S6 with ten samples; test an allocation-free cache lookup.
Experimental runtime changes are confined to /tmp.
"""
import difflib,os,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];BASE=Path('/tmp/sel-commit-benchmark');OUT=ROOT/'tools/commit-benchmark/results'
tree=BASE/'lisp-eq-cache'
shutil.copytree(BASE/'c5a8991/lisp',tree/'lisp',dirs_exist_ok=True)
(tree/'tools/scale-test').mkdir(parents=True,exist_ok=True)
shutil.copy2(ROOT/'tools/scale-test/sel_benchmarks.lisp',tree/'tools/scale-test/sel_benchmarks.lisp')
p=tree/'lisp/src/builtins/structure.lisp';s=p.read_text()
s=s.replace('(defvar *alias-plan-cache* (make-hash-table :test #\'equal))', '(defvar *alias-plan-cache* (make-hash-table :test #\'eq))')
s=s.replace('(cache-key (cons old-shape tbl-name))\n                  (cached (gethash cache-key *alias-plan-cache*))', '(cache-key old-shape)\n                  (entry (gethash cache-key *alias-plan-cache*))\n                  (cached (and entry (string= (car entry) tbl-name) (cdr entry)))')
s=s.replace('(setf (gethash cache-key *alias-plan-cache*) (list ns diff olen))', '(setf (gethash cache-key *alias-plan-cache*) (cons tbl-name (list ns diff olen)))')
p.write_text(s)
(OUT/"lisp-eq-cache.patch").write_text("".join(difflib.unified_diff((BASE/"c5a8991/lisp/src/builtins/structure.lisp").read_text().splitlines(True),s.splitlines(True),fromfile="c5a8991/structure.lisp",tofile="lisp-eq-cache/structure.lisp")))
for mode in ['steady-state','gc-controlled']:
 for name in ['c5a8991','619bc31','lisp-eq-cache']:
  print(name,mode,flush=True)
  env=dict(os.environ,XDG_CACHE_HOME=str(BASE/'cache'),SEL_BENCHMARK_MODE='steady-state',SEL_BENCHMARK_ONLY='scenario6',SEL_BENCHMARK_RUNS='10',SEL_BENCHMARK_WARMUPS='3',SEL_BENCHMARK_TIMING_MODE=mode,SEL_BENCHMARK_OUTPUT=str(OUT/(name+'-lisp-'+mode+'-repeat.json')),SEL_BENCHMARK_REFERENCE=str(BASE/'c5a8991/tools/scale-test/benchmark_results.json'),SEL_DATASET_FILE=str(ROOT/'tools/scale-test/dataset-10x.json'))
  with (BASE/(name+'-lisp-'+mode+'-repeat.log')).open('w') as log:
   subprocess.run(['sbcl','--dynamic-space-size','4096','--noinform','--disable-debugger','--non-interactive','--load','tools/scale-test/sel_benchmarks.lisp'],cwd=BASE/name,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
