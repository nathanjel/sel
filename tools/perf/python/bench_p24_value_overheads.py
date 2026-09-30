"""PY-P24: small per-value overheads in value.py / number.py (four sub-items).
    PYTHONPATH=python python3 tools/perf/python/bench_p24_value_overheads.py"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
import sel
from sel import Value, SelError

out = []
# (a) scalar Value construction
report('(a) Value.text("x") x100k', lambda: [Value.text('x') for _ in range(100_000)], reps=5)
report('(a) Value.int(7)   x100k', lambda: [Value.int(7) for _ in range(100_000)], reps=5)
prog = sel.compile('COUNT(MAP(R, _ + 1))')
R = Value.from_native({'R': list(range(100_000))})
report('(a) MAP(R, _+1) over 100k scalars', lambda: prog.run(R.clone()), reps=5)
prog2 = sel.compile('COUNT(FILTER(R, _ % 3 == 0))')
report('(a) FILTER(R, _%3==0) over 100k scalars', lambda: prog2.run(R.clone()), reps=5)
out.append((prog.run(R.clone()).dump(), prog2.run(R.clone()).dump()))

# (b) `???` (vacuous coalescing) on a million-digit number: is_vacuous formatted it first
BIGDEC = sel.decimal.make(False, 10 ** 999_998, 0)
big = Value.num(BIGDEC)
report('(b) is_vacuous(fresh 1M-digit number)', lambda: Value.num(BIGDEC).is_vacuous(), reps=5)
small = Value.int(5)
report('(b) is_vacuous(small int) x100k', lambda: [small.is_vacuous() for _ in range(100_000)], reps=5)
out.append((big.is_vacuous(), small.is_vacuous(), Value.text('  ').is_vacuous(), Value.text('').is_vacuous(), Value.text('a').is_vacuous()))

# (c) structural hash of dense lists: DEDUPE / EQL / BUCKET paths
rows = Value.from_native({'R': [[i, i + 1, i + 2, i + 3, i + 4, i + 5, i + 6, i + 7] for i in range(20_000)]})
pd = sel.compile('COUNT(DEDUPE(R))')
report('(c) DEDUPE 20k dense 8-element lists', lambda: pd.run(rows.clone()), reps=5)
out.append(pd.run(rows.clone()).dump())

# (d) error-path message building with a huge integer argument
def huge_round():
    try:
        sel.evaluate('ROUND(1, 1' + '0' * 300_000 + ')')
    except SelError as e:
        return e.code
    except Exception as e:
        return type(e).__name__
report('(d) ROUND(1, 10**300000) -> error', huge_round, reps=3)
out.append(huge_round())
print('components', out)
print('checksum', checksum(out))
