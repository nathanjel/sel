"""PY-P5: frozen dataclasses (Dec, Pos, Node defaults) cost 3-4x more to construct
than necessary.

    PYTHONPATH=python python3 tools/perf/python/bench_p5_alloc.py

Micro: Dec construction, D.make/add/mul, Pos construction, Node construction.
End to end (100k rows): MAP(_["p"] * _["q"] + 1), FILTER(_["p"] * 2 > 50); the
lexer (Pos per token) on a rule-text corpus. Checksums of the results.
"""
import sys, os, timeit, statistics
sys.path.insert(0, os.path.dirname(__file__))
from harness import bench, fmt, checksum, rng, env
import sel
from sel import decimal as D
from sel.errors import Pos
from sel.parser import Node
from sel.lexer import tokenize

def micro(name, fn, n=200000):
    fn()
    samples = [v * 1e9 / n for v in timeit.repeat(fn, number=n, repeat=5)]
    print('%-34s %8.0f ns  (min %.0f max %.0f)' % (name, statistics.median(samples), min(samples), max(samples)))

def main():
    print(env())
    micro('Dec(False, 1250, 2)', lambda: D.Dec(False, 1250, 2))
    micro('D.make(False, 1250, 2)', lambda: D.make(False, 1250, 2))
    a, b = D.parse('123.45'), D.parse('67.89')
    micro('D.add', lambda: D.add(a, b))
    micro('D.mul', lambda: D.mul(a, b))
    micro('Pos(1, 2, 3)', lambda: Pos(1, 2, 3))
    micro('Node("num", pos)', lambda: Node('num', None))
    r = rng(11)
    rows = [{'p': str(r.randrange(100)), 'q': str(r.randrange(100))} for _ in range(100000)]
    for label, src in (('MAP arith', 'ROWS .> MAP(_["p"] * _["q"] + 1) .> COUNT()'),
                       ('FILTER arith', 'ROWS .> FILTER(_["p"] * 2 > 50) .> COUNT()')):
        p = sel.compile(src)
        med, lo, hi = bench(lambda: p.run({'ROWS': rows}), reps=5, warm=1)
        print('%-34s %10s  (min %s max %s)  chk %s' % (label + ' (100k rows)', fmt(med), fmt(lo), fmt(hi), checksum(p.run({'ROWS': rows}).scalar)))
    text = ' AND '.join('(A%d * B + C > %d AND IF(D, X + 1, Y - 1) >= 2.5)' % (i % 9, i) for i in range(400))
    med, lo, hi = bench(lambda: tokenize(text), reps=5, warm=1)
    print('%-34s %10s  (min %s max %s)  chk %s' % ('tokenize rule text (%d chars)' % len(text), fmt(med), fmt(lo), fmt(hi), checksum(len(tokenize(text)))))

if __name__ == '__main__':
    main()
