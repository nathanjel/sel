"""Diagnostic (not a timing benchmark): count Python Mandelbrot power-cache activity.
Run from a snapshot root; output path is argv[1].
"""
import collections,json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'python'))
from sel import compile,Value
from sel import decimal as D
original=D._pow10
counts=collections.Counter();misses=collections.Counter();clears=0
limit=getattr(D,'_POW10_LIMIT',getattr(D,'_FAST_SCALE',18))
def probe(n):
 global clears
 counts[n]+=1
 if n>limit and n not in D._POW10: misses[n]+=1
 before=len(D._POW10)
 value=original(n)
 if len(D._POW10)<before: clears+=1
 return value
D._pow10=probe
p=compile(Path('examples/mandelbrot.sel').read_text())
for _ in range(3):p.run(Value.none())
Path(sys.argv[1]).write_text(json.dumps(dict(calls=dict(counts),misses=dict(misses),clears=clears,retained_entries=len(D._POW10)),indent=2)+'\n')
