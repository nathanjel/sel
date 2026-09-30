import os, sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'python'))
from harness import bench, fmt
import sel
from sel import Value
owned = Value._num_owned
pub = staticmethod(Value.num)
ctx = {'A': '3.5', 'B': '4', 'C': '7.25'}
progs = [('plan A*B+C','A * B + C'),('tree A*B+C - A*B*C/B','A * B + C - A * B * C / B')]
import random
r = random.Random(13)
rows = [{'p': str(r.randrange(100)), 'q': str(r.randrange(100))} for _ in range(50000)]
work = [(n, sel.compile(s), lambda p: p.run(dict(ctx)), 3000) for n,s in progs]
work.append(('MAP arith 50k', sel.compile('ROWS .> MAP(_["p"] * _["q"] + 1) .> SUM(_)'), lambda p: p.run({'ROWS': rows}), 1))
work.append(('FILTER arith 50k', sel.compile('ROWS .> FILTER(_["p"] * 2 > 50) .> COUNT()'), lambda p: p.run({'ROWS': rows}), 1))
for name, p, run, inner in work:
    res = {}
    for rnd in range(3):
        for label, impl in (('validating', pub), ('owned', staticmethod(owned))):
            Value._num_owned = impl
            res.setdefault(label, []).append(bench(lambda: run(p), reps=5, warm=1, inner=inner)[1])
    print('%-24s validating %10s   owned %10s   (min over 3 rounds)' % (name, fmt(min(res['validating'])), fmt(min(res['owned']))))
