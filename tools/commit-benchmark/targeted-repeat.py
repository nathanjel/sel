#!/usr/bin/env python3
"""Confirm PHP S4/S6 and count Python power-cache misses after the main runs."""
import difflib,os,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];BASE=Path('/tmp/sel-commit-benchmark');OUT=ROOT/'tools/commit-benchmark/results'
variant=BASE/'php-direct-scalar'
shutil.copytree(BASE/'c5a8991/php',variant/'php',dirs_exist_ok=True)
(variant/'tools/scale-test').mkdir(parents=True,exist_ok=True)
shutil.copy2(ROOT/'tools/scale-test/sel_benchmarks.php',variant/'tools/scale-test/sel_benchmarks.php')
p=variant/'php/src/Builtins/Structure.php';before=p.read_text();after=re.sub(r'->scalar\b','->getScalar()',before);p.write_text(after)
(OUT/'php-direct-scalar.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='c5a8991/Structure.php',tofile='php-direct-scalar/Structure.php')))
for rev in ['c5a8991','619bc31','1614eed','ef836bf','php-direct-scalar']:
 print('PHP S4/S6',rev,flush=True)
 cmd=['php','-d','memory_limit=-1','-d','opcache.enable_cli=1','-d','opcache.jit_buffer_size=128M','-d','opcache.jit=1255','tools/scale-test/sel_benchmarks.php','--dataset',str(ROOT/'tools/scale-test/dataset-10x.json'),'--reference',str(ROOT/'tools/scale-test/benchmark_results.json'),'--runs','10','--warmups','3','--only','scenario4,scenario6','--output',str(OUT/(rev+'-php-s4-s6-repeat.json'))]
 with (BASE/(rev+'-php-s4-s6-repeat.log')).open('w') as log:
  subprocess.run(cmd,cwd=BASE/rev,stdout=log,stderr=subprocess.STDOUT,check=True)
for rev in ['1614eed','619bc31','c5a8991']:
 print('power profile',rev,flush=True)
 subprocess.run([sys.executable,str(ROOT/'tools/commit-benchmark/power-profile.py'),str(OUT/(rev+'-python-power-profile.json'))],cwd=BASE/rev,env=dict(os.environ,PYTHONHASHSEED='0'),check=True)
import json
probes={}
for rev in ['ef836bf','c5a8991']:
 path=(BASE/rev/'js/src/sel.mjs').as_uri()
 source=f'''Object.freeze(BigInt.prototype);
try {{ const {{compile,Value}}=await import({json.dumps(path)});
console.log(compile("1+2").run(Value.none()).asText()); }}
catch(e) {{ console.error(e.toString()); process.exitCode=1; }}'''
 p=subprocess.run(['node','--input-type=module','-e',source],text=True,capture_output=True)
 probes[rev]=dict(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
(OUT/'js-prototype-probe.json').write_text(json.dumps(probes,indent=2)+'\n')
print('PHP latest Mandelbrot without GMP',flush=True)
cmd=['php','-n','-d','extension=ctype','-d','memory_limit=-1','-d','opcache.enable_cli=1','-d','opcache.jit_buffer_size=128M','-d','opcache.jit=1255',str(ROOT/'tools/commit-benchmark/mandelbrot.php'),str(OUT/'c5a8991-php-no-gmp-mandelbrot.json')]
with (BASE/'php-no-gmp-mandelbrot.log').open('w') as log:
 subprocess.run(cmd,cwd=BASE/'c5a8991',env=dict(os.environ,MANDEL_WARMUPS='1',MANDEL_RUNS='3'),stdout=log,stderr=subprocess.STDOUT,check=True)
for rev in ['c5a8991','619bc31','1614eed','ef836bf']:
 print('JS S2',rev,flush=True)
 cmd=['node','tools/scale-test/sel_benchmarks.mjs','--dataset',str(ROOT/'tools/scale-test/dataset-10x.json'),'--reference',str(ROOT/'tools/scale-test/benchmark_results.json'),'--runs','20','--warmups','5','--only','scenario2','--output',str(OUT/(rev+'-js-s2-repeat.json'))]
 with (BASE/(rev+'-js-s2-repeat.log')).open('w') as log:
  subprocess.run(cmd,cwd=BASE/rev,stdout=log,stderr=subprocess.STDOUT,check=True)
