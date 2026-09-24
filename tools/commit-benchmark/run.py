#!/usr/bin/env python3
"""Benchmark archived runtimes, with one shared harness; no benchmark jobs overlap.
Usage: python3 tools/commit-benchmark/run.py [--prepared] [--reverse]
Archives and logs live in /tmp/sel-commit-benchmark. --prepared reuses existing builds.
"""
import io,json,os,shutil,subprocess,sys,tarfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
BASE=Path('/tmp/sel-commit-benchmark')
OUT=ROOT/'tools/commit-benchmark/results'
REVS=['ef836bf','1614eed','619bc31','c5a8991']
if '--reverse' in sys.argv: REVS.reverse()
if '--latest-first' in sys.argv: REVS=['1614eed','619bc31','c5a8991','ef836bf']
SUFFIX='-repeat' if '--reverse' in sys.argv else ''
BASE.mkdir(exist_ok=True);OUT.mkdir(exist_ok=True)
ENV=dict(os.environ,XDG_CACHE_HOME=str(BASE/'cache'),PYTHONHASHSEED='0')
FAILURES=[]
PHP=['php','-d','memory_limit=-1','-d','opcache.enable_cli=1','-d','opcache.jit_buffer_size=128M','-d','opcache.jit=1255']
def call(cmd,cwd,label,env=None):
 print(time.strftime('%H:%M:%S'),label,flush=True)
 with (BASE/(label+'.log')).open('w') as log:
  p=subprocess.run(cmd,cwd=cwd,env=env or ENV,stdout=log,stderr=subprocess.STDOUT)
 print(label,'exit',p.returncode,flush=True)
 if p.returncode: FAILURES.append(label)
 return p.returncode
if '--prepared' not in sys.argv:
 for rev in REVS:
  tree=BASE/rev;tree.mkdir(exist_ok=True)
  with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git','archive',rev],cwd=ROOT))) as t:t.extractall(tree,filter='data')
  for name in ['sel_benchmarks.py','sel_benchmarks.php','sel_benchmarks.cpp','sel_benchmarks.mjs','sel_benchmarks.lisp','benchmark_support.py']:
   shutil.copy2(ROOT/'tools/scale-test'/name,tree/'tools/scale-test'/name)
  if call(['make','-C','cpp','-j2','build/scale-bench','build/sel.o'],tree,rev+'-build'):raise SystemExit(1)
  if call(['c++','-std=c++23','-O2','-Icpp',str(ROOT/'tools/commit-benchmark/mandelbrot.cpp'),'cpp/build/sel.o','-o','cpp/build/mandelbrot'],tree,rev+'-mandel-build'):raise SystemExit(1)
for rev in REVS:
 tree=BASE/rev
 for lane in ['lisp','cpp','js','php','python']:
  output=OUT/(rev+'-'+lane+SUFFIX+'.json')
  args=['--dataset',str(ROOT/'tools/scale-test/dataset-10x.json'),'--reference',str(ROOT/'tools/scale-test/benchmark_results.json'),'--runs','5','--warmups','2','--timing-mode','steady-state','--output',str(output)]
  env=dict(ENV)
  if lane=='lisp':
   env.update(SEL_BENCHMARK_MODE='steady-state',SEL_BENCHMARK_OUTPUT=str(output),SEL_BENCHMARK_REFERENCE=str(ROOT/'tools/scale-test/benchmark_results.json'),SEL_BENCHMARK_RUNS='5',SEL_BENCHMARK_WARMUPS='2',SEL_BENCHMARK_TIMING_MODE='steady-state',SEL_DATASET_FILE=str(ROOT/'tools/scale-test/dataset-10x.json'))
   cmd=['sbcl','--dynamic-space-size','4096','--noinform','--disable-debugger','--non-interactive','--load','tools/scale-test/sel_benchmarks.lisp']
  else:
   cmd={'cpp':['cpp/build/scale-bench'],'js':['node','tools/scale-test/sel_benchmarks.mjs'],'php':PHP+['tools/scale-test/sel_benchmarks.php'],'python':[sys.executable,'tools/scale-test/sel_benchmarks.py']}[lane]+args
  if '--resume' not in sys.argv or not output.exists() or not json.loads(output.read_text()).get('passed',False):
   call(cmd,tree,rev+'-'+lane+SUFFIX,env)
  mandel=OUT/(rev+'-'+lane+'-mandelbrot'+SUFFIX+'.json')
  tool=ROOT/'tools/commit-benchmark'
  env['MANDEL_OUTPUT']=str(mandel)
  slow_baseline=rev=='ef836bf' and lane!='python'
  env['MANDEL_RUNS']='1' if slow_baseline else '5'
  env['MANDEL_WARMUPS']='0' if slow_baseline else '2'
  cmd={'lisp':['sbcl','--dynamic-space-size','4096','--noinform','--disable-debugger','--non-interactive','--load',str(tool/'mandelbrot.lisp')], 'cpp':['cpp/build/mandelbrot',str(mandel)],'js':['node',str(tool/'mandelbrot.mjs'),str(mandel)],'php':PHP+[str(tool/'mandelbrot.php'),str(mandel)],'python':[sys.executable,str(tool/'mandelbrot.py'),str(mandel)]}[lane]
  if '--resume' not in sys.argv or not mandel.exists():
   call(cmd,tree,rev+'-'+lane+'-mandelbrot'+SUFFIX,env)

if FAILURES:
 print("Failed jobs:", ", ".join(FAILURES), flush=True)
 raise SystemExit(1)
