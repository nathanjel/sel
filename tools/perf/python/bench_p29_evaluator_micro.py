"""PY-P29: evaluator micro-costs (text literal validation per evaluation, assignment paths, bitwise).
    PYTHONPATH=python python3 tools/perf/python/bench_p29_evaluator_micro.py"""
import random
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
import sel
from sel import Value

rnd = random.Random(29)
rows = [{'n': rnd.randrange(0, 1000), 's': 'k%d' % rnd.randrange(0, 500)} for _ in range(100_000)]
ctx = Value.from_native({'R': rows, 'B': [bytes([rnd.randrange(256) for _ in range(8)]) for _ in range(20_000)]})
out = []

def prog(name, src, reps=5, c=None):
    p = sel.compile(src)
    report(name, lambda: p.run(c if c is not None else ctx), reps=reps)
    out.append(p.run(c if c is not None else ctx).dump())

prog('COUNT(MAP(R, "x"))            text literal per row', 'COUNT(MAP(R, "x"))')
prog('COUNT(FILTER(R, _["s"] $== "k9")) literal compare', 'COUNT(FILTER(R, _["s"] $== "k9"))')
prog('COUNT(MAP(R, _["s"] & "-" & "x"))', 'COUNT(MAP(R, _["s"] & "-" & "x"))')
deep = 'A = 0; ' + ''.join('A = A + 1; ' for _ in range(1)) + 'A'
prog('assignment target depth 150 (x200 loops)', 'X = RECORD("k", 1); ' + 'Y = X; ' * 1 + 'COUNT(MAP(LIST' + '(' + ','.join(str(i) for i in range(200)) + '), _))')
# k-deep indexed assignment and compound assignment
chain = 'T = RECORD(); ' + 'T["a"] = RECORD(); ' + ''.join('T' + ''.join('["a"]' for _ in range(i)) + '["a"] = RECORD(); ' for i in range(1, 60))
deepw = chain + 'T' + ''.join('["a"]' for _ in range(60)) + ' = 1; ' + ''.join('T' + ''.join('["a"]' for _ in range(60)) + ' += 1; ' for _ in range(40)) + 'T' + ''.join('["a"]' for _ in range(60))
prog('60-deep target: 1 write + 40 compound writes', deepw, reps=7)
prog('BAND over 8-byte BINs x20k', 'COUNT(MAP(B, _ BAND _))', reps=5)
print('checksum', checksum(out))
