"""PY-P31: text/raw literal bodies were appended one character at a time.
    PYTHONPATH=python python3 tools/perf/python/bench_p31_lexer_literals.py"""
import random
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
from sel.lexer import tokenize

rnd = random.Random(31)
body = ''.join(rnd.choice('abcdefghij klmnop.,;:()[]=+-') for _ in range(1_000_000))
out = []

def tok(name, src, reps=5):
    report(name, lambda: tokenize(src), reps=reps)
    out.append([(t.type, t.value[:40], t.pos.offset) for t in tokenize(src)][:50])

tok('1 MB quoted literal', '"' + body + '"')
tok('1 MB raw literal', "'" + body + "'")
tok('1 MB quoted with escapes every ~50 chars', '"' + ''.join(body[i:i + 50] + '\\n' for i in range(0, 1_000_000, 50)) + '"', reps=3)
tok('1 MB quoted with {interp} every ~200 chars', '"' + ''.join(body[i:i + 200] + '{1}' for i in range(0, 1_000_000, 200)) + '"', reps=3)
small = ' AND '.join('NAME $== "value%d" OR CITY $== \'town%d\'' % (i, i) for i in range(2000))
tok('2000 short literals in a rule', small)
print('checksum', checksum(out))
