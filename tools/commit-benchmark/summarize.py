#!/usr/bin/env python3
"""Summarize completed runs without treating failed validation as passing."""
import hashlib,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/interim/commit-benchmark'
REVS=['ef836bf','1614eed','619bc31','c5a8991']
LANES=['cpp','js','lisp','php','python']
rows=[];failures=[];hashes=set()
reference={r['id']:r for r in json.loads((ROOT/'tools/scale-test/benchmark_results.json').read_text())}
for lane in LANES:
 for rev in REVS:
  for suffix in ['', '-repeat']:
   p=OUT/(rev+'-'+lane+suffix+'.json')
   if p.exists():
    report=json.loads(p.read_text())
    for s in report['scenarios']:
     if lane=='lisp' and s['in_memory_rows'] != reference[s['id']]['in_memory_rows']:
      raise AssertionError(f"{rev}/{lane}/{s['id']}: output differs")
     samples=s['samples']; phases=[k for k,v in samples[0].items() if k.endswith('_ms')]
     stats={k:{'median':statistics.median(x[k] for x in samples),'min':min(x[k] for x in samples),'max':max(x[k] for x in samples)} for k in phases}
     rows.append(dict(commit=rev,lane=lane,workload=s['id'],round=1 if not suffix else 2,phases=stats,compile_ms=s.get('compile_ms'),passed=s.get('passed',s.get('parity',{}).get('passed')),failures=s.get('failures',s.get('parity',{}).get('failures',[]))))
     if rows[-1]['failures']:failures.append({k:rows[-1][k] for k in ['commit','lane','workload','round','failures']})
   p=OUT/(rev+'-'+lane+'-mandelbrot'+suffix+'.json')
   if p.exists():
    r=json.loads(p.read_text()); hs={hashlib.sha256(x.encode()).hexdigest() for x in r['outputs']};hashes.update(hs)
    rows.append(dict(commit=rev,lane=lane,workload='mandelbrot',round=1 if not suffix else 2,phases={'prepared_total_ms':dict(median=statistics.median(r['samples_ms']),min=min(r['samples_ms']),max=max(r['samples_ms']))},compile_ms=r['compile_ms'],warmups=r.get('warmups',2),samples=len(r['samples_ms']),passed=len(hs)==1,output_sha256=list(hs)))
(OUT/'summary.json').write_text(json.dumps({'rows':rows,'validation_failures':failures,'mandelbrot_output_hashes':sorted(hashes)},indent=2)+'\n')
for lane in LANES:
 print('\n'+lane+' (median ms, first round)')
 for work in ['scenario'+str(i) for i in range(1,7)]+['mandelbrot']:
  vals=[]
  for rev in REVS:
   row=next((r for r in rows if r['commit']==rev and r['lane']==lane and r['workload']==work and r['round']==1),None)
   vals.append(row['phases']['prepared_total_ms']['median'] if row else None)
  print(work,' '.join(f'{x:10.2f}' if x is not None else '         -' for x in vals))
print('validation failures',len(failures),'Mandelbrot distinct outputs',len(hashes))

if len(hashes)>1:
 raise SystemExit("Mandelbrot output mismatch")
