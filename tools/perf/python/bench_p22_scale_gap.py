"""PY-P22: comparisons (and is_integer) across a huge scale gap built 10**gap.
    PYTHONPATH=python python3 tools/perf/python/bench_p22_scale_gap.py"""
import random
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
from sel import decimal as D

def dec(digits, scale, neg=False):
    return D.make(neg, digits, scale)

one = dec(1, 0)
cases = {
    'cmp(1, 0.<999,999 zeros>1) gap 1e6': (one, dec(1, 1_000_000)),
    'cmp gap 999,999': (one, dec(1, 999_999)),
    'cmp gap 999,998': (one, dec(1, 999_998)),
    'cmp scale 500,000 vs 1': (dec(7, 500_000), dec(1, 0)),
    'cmp big digits vs tiny scale': (dec(10**200_000 + 1, 100), dec(3, 1_000_000)),
}
for name, (a, b) in cases.items():
    report(name, lambda a=a, b=b: D.cmp(a, b), reps=5)
report('is_integer(1 at scale 1e6)', lambda: D.is_integer(dec(1, 1_000_000)), reps=5)
report('is_integer(odd at scale 500k)', lambda: D.is_integer(dec(10**10 + 1, 500_000)), reps=5)
report('cmp small (ordinary)', lambda: D.cmp(dec(12345, 2), dec(999, 1)), reps=7, inner=20000)
report('cmp small equal scale', lambda: D.cmp(dec(12345, 2), dec(12346, 2)), reps=7, inner=20000)
report('is_integer small', lambda: D.is_integer(dec(1200, 2)), reps=7, inner=20000)

rnd = random.Random(22)
out = []
for _ in range(4000):
    a = dec(rnd.choice([0, rnd.randrange(1, 10**rnd.randrange(1, 40))]), rnd.randrange(0, 3000), rnd.random() < .4)
    b = dec(rnd.choice([0, rnd.randrange(1, 10**rnd.randrange(1, 40))]), rnd.randrange(0, 3000), rnd.random() < .4)
    out.append((D.cmp(a, b), D.is_integer(a)))
print('checksum', checksum(out))

# Cold exponents: every call uses a NEW large gap, so a power cache cannot help (the
# review's 0.3 s cases came from each new large exponent clearing the cache).
import itertools
_c = itertools.count(0)
report('cmp cold gap ~1e6 (new exponent per call)',
       lambda: D.cmp(one, dec(1, 900_000 + next(_c) % 100_000)), reps=5, inner=20)
_d = itertools.count(0)
report('is_integer cold scale ~1e6 (new exponent)',
       lambda: D.is_integer(dec(7, 900_000 + next(_d) % 100_000)), reps=5, inner=20)
