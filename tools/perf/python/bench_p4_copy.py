"""PY-P4: dataclasses.replace in copy_node made the optimiser about as expensive as
parsing (every one-shot evaluate() and cold compile pays it).

    PYTHONPATH=python python3 tools/perf/python/bench_p4_copy.py

Workloads: an ~840-node rule set (40 rules), the 13-node reference rule; compile,
compile + physical_ast (the optimiser), and a one-shot evaluate(source, ctx).
Checksum = the run result over a fixed context, so the optimised trees agree.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from harness import bench, fmt, checksum, env
import sel

def big_program(rules=40):
    parts = []
    for i in range(rules):
        parts.append('(A%d * B%d + C%d > %d AND IF(D%d, X%d + %d, Y%d - 1) >= %d.5 AND NOT (E%d == %d))'
                     % (i % 5, i % 7, i % 3, i, i % 4, i % 6, i, i % 2, i % 9, i % 8, i))
    return ' AND '.join(parts)

def ctx():
    c = {}
    for k in 'ABCXY':
        for i in range(10):
            c['%s%d' % (k, i)] = i + 1
    c.update({'A': 3, 'B': 4, 'C': 5, 'X': 6, 'Y': 7, 'D': True})
    for i in range(10):
        c['D%d' % i] = i % 2 == 0
        c['E%d' % i] = i
    return c

def main():
    print(env())
    big = big_program()
    ref = 'A * B + C > 3 AND IF(D, X + 1, Y - 1) >= 2.5'
    cx = ctx()
    for name, src in (('40-rule set', big), ('13-node rule', ref)):
        nodes = len(src) // 6
        p = sel.compile(src)
        chk = checksum(p.run(dict(cx)).dump())
        for label, fn in (
            ('compile', lambda src=src: sel.compile(src)),
            ('compile + physical_ast', lambda src=src: sel.compile(src).physical_ast()),
            ('one-shot evaluate', lambda src=src: sel.evaluate(src, dict(cx))),
        ):
            med, lo, hi = bench(fn, reps=5, warm=1, inner=max(1, 200 // (1 + len(src) // 200)))
            print('%-14s %-24s %10s  (min %s max %s)  chk %s' % (name, label, fmt(med), fmt(lo), fmt(hi), chk))

if __name__ == '__main__':
    main()
