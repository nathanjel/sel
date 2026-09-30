"""PY-P15: what the 2-argument BUCKET member copy costs (and why it stays).
    PYTHONPATH=python python3 tools/perf/python/bench_p15_bucket_copy.py"""
import random
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
import sel

rnd = random.Random(15)
rows = [{'k': rnd.randrange(1000), 'a': 1, 'b': 'x', 'c': '3.5'} for _ in range(100_000)]
ctx = {'R': rows}
bare = sel.compile('COUNT(BUCKET(R, _["k"]))')
agg = sel.compile('COUNT(BUCKET(R, _["k"], COUNT(_)))')
report('BUCKET(R, key)            100k rows / 1000 groups', lambda: bare.run(dict(ctx)), reps=3)
report('BUCKET(R, key, COUNT(_))  (no member copy)', lambda: agg.run(dict(ctx)), reps=3)
print('checksum', checksum((bare.run(dict(ctx)).scalar, agg.run(dict(ctx)).scalar)))
# the contract that forces the copy (SPEC 3.4: BUCKET copies what it collects)
r = sel.evaluate('R = LIST(RECORD("k", 1, "v", 1)); G = BUCKET(R, _["k"]); R[1]["v"] = 99; G["1"][1]["v"]')
print('member is independent of the source:', r.scalar == '1')
