"""PY-P19: `%` on huge integers (CPython >= 3.12: % is ~9x slower than divmod).
    PYTHONPATH=python python3 tools/perf/python/bench_p19_mod.py"""
import hashlib
import random
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, growth, checksum
import sel

rnd = random.Random(19)
def digits(n):
    return str(rnd.randrange(1, 10)) + ''.join(rnd.choice('0123456789') for _ in range(n - 1))
X, Y = digits(400_000), digits(200_000)
ctx = {'X': X, 'Y': Y}
p = sel.compile('X % Y')
q = sel.compile('X / Y')
report('X % Y   (400k digits % 200k digits)', lambda: p.run(dict(ctx)), reps=3)
report('X / Y   (same operands, for scale)', lambda: q.run(dict(ctx)), reps=3)
r = p.run(dict(ctx)).scalar
print('checksum', checksum((len(r), hashlib.sha256(r.encode()).hexdigest()[:12])))
# small operands must not regress
small = sel.compile('A % B')
report('A % B (small operands)', lambda: small.run({'A': '123456789', 'B': '97'}), reps=5, inner=2000)
def make(n):
    c = {'X': X[:n * 2], 'Y': Y[:n]}
    return lambda: p.run(dict(c))
growth('X % Y', make, [50_000, 100_000, 200_000])
