"""PY-P26: DISTINCT/DEDUPE/BUCKET key hashing for childless keys, and LINK's first_collection_item.
    PYTHONPATH=python python3 tools/perf/python/bench_p26_identity.py"""
import random
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
import sel
from sel import Value

rnd = random.Random(26)
texts = ['t%d' % rnd.randrange(0, 50_000) for _ in range(100_000)]
nums = [rnd.randrange(0, 5_000) for _ in range(100_000)]
recs = [{'id': i % 60_000, 'name': 'n%d' % (i % 60_000), 'a': 1, 'b': 2} for i in range(100_000)]
ctx = Value.from_native({'T': texts, 'N': nums, 'R': recs})
out = []

def prog(name, src, reps=5):
    p = sel.compile(src)
    report(name, lambda: p.run(ctx), reps=reps)
    out.append(p.run(ctx).dump())

prog('COUNT(DISTINCT(T))  100k text, ~43k unique', 'COUNT(DISTINCT(T))')
prog('COUNT(DISTINCT(N))  100k int, 5k unique', 'COUNT(DISTINCT(N))')
prog('COUNT(DISTINCT(R))  100k records', 'COUNT(DISTINCT(R))', reps=3)
prog('COUNT(BUCKET(T, _, COUNT(_)))  text keys', 'COUNT(BUCKET(T, _, COUNT(_)))')
prog('COUNT(BUCKET(N, _, COUNT(_)))  int keys', 'COUNT(BUCKET(N, _, COUNT(_)))')
prog('COUNT(BUCKET(R, _["name"], COUNT(_)))', 'COUNT(BUCKET(R, _["name"], COUNT(_)))')

# LINK over dict-mode (irregular) records: first_collection_item per call
left = [{'k': i % 97, 'x': i} for i in range(300)]
right = [{'k': i % 97, 'y': i} for i in range(300)]
for r in left[::7]:
    r['extra'] = 1                      # irregular rows stay dict-mode
ctx2 = Value.from_native({'A': left, 'B': right})
p = sel.compile('COUNT(LINK(A, B, _1["k"] == _2["k"]))')
report('COUNT(LINK(A, B, ==)) 300x300 irregular', lambda: p.run(ctx2), reps=5)
out.append(p.run(ctx2).dump())
print('checksum', checksum(out))
