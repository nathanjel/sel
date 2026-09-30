"""PY-P7: one un-plannable operand threw away the math plan for the whole expression,
sub-plans included.

    PYTHONPATH=python python3 tools/perf/python/bench_p7_subplans.py

Per-run CPU time of a rule, compiled once: pure math (planned), the same math beside
an IF operand (plan was lost), IF(D, math, math) (sub-plans already worked).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from harness import bench, fmt, checksum, env
import sel

CTX = {'A': '3.5', 'B': '4', 'C': '7.25', 'D': True}
M = 'A * B + C - A * B * C / B'
CASES = [
    ('pure math', M),
    ('math + IF operand', '(%s) + IF(D, 1, 2)' % M),
    ('IF around math (control)', 'IF(D, %s, A - B)' % M),
    ('math * IF * math', '(%s) * IF(D, 2, 3) - (A * B + C)' % M),
    ('2 math operands of a MAX with IF', 'MAX(%s, IF(D, 1, 2), A * B * C)' % M),
]

def main():
    print(env())
    for name, src in CASES:
        p = sel.compile(src)
        p.physical_ast()
        med, lo, hi = bench(lambda: p.run(dict(CTX)), reps=7, warm=1, inner=2000)
        print('%-36s %10s  (min %s max %s)  chk %s' % (name, fmt(med), fmt(lo), fmt(hi), checksum(p.run(dict(CTX)).dump())))

if __name__ == '__main__':
    main()
