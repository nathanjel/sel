"""PY-P13: RREPLACE per-match cost (replacement parsing, slicing, cap check).
    PYTHONPATH=python python3 tools/perf/python/bench_p13_rreplace.py"""
import hashlib
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, growth, checksum
import sel

S1 = 'a' * 1_000_000
ctx = {'S': S1, 'W': ('ab' * 50 + ' ') * 10000, 'Z': 'xyz' * 100000}
progs = {
    'literal replacement, 1M matches': 'LEN(RREPLACE("a", "bb", S))',
    'replacement with $0, 1M matches': 'LEN(RREPLACE("a", "<$0>", S))',
    'two groups swap, 10k matches': 'LEN(RREPLACE("(a+)b(a+)", "$2-$1", W))',
    'zero-width matches, 300k': 'LEN(RREPLACE("", "-", Z))',
    'no match (control)': 'LEN(RREPLACE("q", "bb", S))',
}
sums = []
for name, src in progs.items():
    p = sel.compile(src)
    report(name, lambda p=p: p.run(dict(ctx)), reps=3)
    sums.append(p.run(dict(ctx)).scalar)
outs = [sel.evaluate(s, dict(ctx)).scalar for s in (
    'RREPLACE("a", "<$0$$>", LEFT(S, 5000))', 'RREPLACE("(a+)b(a+)", "$2-$1", LEFT(W, 4000))', 'RREPLACE("", "-", LEFT(Z, 300))')]
print('checksum', checksum((sums, [hashlib.sha256(o.encode()).hexdigest()[:10] for o in outs])))
for nm, src in (('literal', 'LEN(RREPLACE("a", "bb", LEFT(S, @)))'),):
    def make(n, src=src):
        p = sel.compile(src.replace('@', str(n)))
        return lambda: p.run(dict(ctx))
    growth('RREPLACE ' + nm, make, [250_000, 500_000, 1_000_000])
