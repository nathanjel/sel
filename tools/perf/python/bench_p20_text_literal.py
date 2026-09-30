"""PY-P20: SQL text literal escaping (was a per-character Python loop).
    PYTHONPATH=python python3 tools/perf/python/bench_p20_text_literal.py"""
import hashlib
import random
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
from sel.sql import emit

from sel.sql import map as _map


def ref_text_literal(dialect, text):
    """The pre-change implementation: a per-character scan, longest key first."""
    quote = str(_map.lexical(dialect, 'textQuote'))
    escape = _map.lexical(dialect, 'textEscape')
    out = text
    if isinstance(escape, dict):
        keys = sorted(escape.keys(), key=len, reverse=True)
        buf = []
        i = 0
        while i < len(out):
            for k in keys:
                if k and out.startswith(k, i):
                    buf.append(str(escape[k]))
                    i += len(k)
                    break
            else:
                buf.append(out[i])
                i += 1
        out = ''.join(buf)
    return quote + out + quote


rnd = random.Random(20)
clean = ''.join(rnd.choice('abcdefghij kl') for _ in range(1_000_000))
dirty = ''.join(rnd.choice("abc'\\\\%_\"\n\x00 ") for _ in range(1_100_000))
sums = []
for d in ('sqlite', 'mysql', 'mariadb', 'postgresql'):
    for nm, t in (('clean 1MB', clean), ('quotes/backslashes 1.1MB', dirty)):
        report('%-10s %s' % (d, nm), lambda d=d, t=t: emit.text_literal(d, t), reps=3)
        sums.append(hashlib.sha256(emit.text_literal(d, t).encode('utf-8', 'surrogatepass')).hexdigest()[:10])
        report('%-10s   (reference loop, same input)' % d, lambda d=d, t=t: ref_text_literal(d, t), reps=1)
        assert emit.text_literal(d, t) == ref_text_literal(d, t)
    report('%-10s 3-char literal' % d, lambda d=d: emit.text_literal(d, "a'b"), reps=5, inner=5000)
    sums.append(emit.text_literal(d, "a'b\\c"))
print('checksum', checksum(sums))
