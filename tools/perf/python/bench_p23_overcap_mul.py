"""PY-P23: over-cap products were computed in full before being refused.
    PYTHONPATH=python python3 tools/perf/python/bench_p23_overcap_mul.py"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
import sel
from sel import decimal as D
from sel.errors import SelError

big = D.make(False, 10 ** 999_999, 0)          # exactly 1,000,000 integer digits


def attempt(f):
    try:
        return f().digits.bit_length()
    except SelError as e:
        return e.code


out = []
def run_mul():
    out.append(attempt(lambda: D.mul(big, big)))
report('mul 1M-digit x 1M-digit (E_RANGE)', run_mul, reps=3)
def run_pow():
    try:
        sel.evaluate('POWER(POWER(10,20),100000)')
    except SelError as e:
        out.append(e.code)
report('POWER(POWER(10,20),100000) (E_RANGE)', run_pow, reps=3)
# Near-the-cap products that are LEGAL must still be computed and returned.
ok_a = D.make(False, 10 ** 499_999, 0)          # 500,000 digits
ok = D.mul(ok_a, ok_a)                          # 999,999 digits: legal
out.append(ok.digits.bit_length())
report('mul 500k x 500k digits (legal)', lambda: D.mul(ok_a, ok_a), reps=3)
small = D.make(False, 12345, 2)
report('mul small (ordinary)', lambda: D.mul(small, small), reps=7, inner=20000)
out.append(D.mul(small, small).digits)
# Scale beyond the fractional cap also refuses without multiplying.
wide = D.make(False, 3, 600_000)
out.append(attempt(lambda: D.mul(wide, wide)))
report('mul scale past the fractional cap', lambda: attempt(lambda: D.mul(wide, wide)), reps=5, inner=50)
print('checksum', checksum(out))
