#!/usr/bin/env python3
"""Render the primary timing table from summary.json (no hand-entered numbers)."""
import csv,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'docs/interim/commit-benchmark'
rows=json.loads((OUT/'summary.json').read_text())['rows']
revs=['ef836bf','1614eed','619bc31','c5a8991']
lines=['## Primary results','', 'Milliseconds; medians of five samples except the marked old Mandelbrot single executions. Lower is faster. `Δ latest/base` is a latency change, not a speedup ratio.','', '| Lane | Workload | ef836bf | 1614eed | 619bc31 | c5a8991 | Δ latest/base |','| --- | --- | ---: | ---: | ---: | ---: | ---: |']
records=[]
for lane in ['cpp','js','lisp','php','python']:
 for workload in ['scenario'+str(i) for i in range(1,7)]+['mandelbrot']:
  selected=[next(r for r in rows if r['commit']==rev and r['lane']==lane and r['workload']==workload and r['round']==1) for rev in revs]
  vals=[r['phases']['prepared_total_ms']['median'] for r in selected]
  delta=(vals[-1]/vals[0]-1)*100
  cells=[f'{v:,.2f}' for v in vals]
  if workload=='mandelbrot' and lane!='python': cells[0]+='*'
  lines.append('| '+ ' | '.join([lane,workload]+cells+[(f'{delta:+.3f}%' if workload=='mandelbrot' else f'{delta:+.1f}%')])+' |')
  records.append(dict(lane=lane,workload=workload,**{rev:val for rev,val in zip(revs,vals)},latest_vs_base_percent=delta))
lines+=['','*One execution with no warmup; all other Mandelbrot entries are five-sample medians. The Lisp S6 first-pass median is GC-sensitive; see the repeated measurements before interpreting its delta.','']
(OUT/'table.md').write_text('\n'.join(lines))
with (OUT/'timings.csv').open('w') as f:
 writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
