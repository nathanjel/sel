"""PY-P10: lexer cost -- a linear scan of 34 operators per token, per-character Python
loops, and O(n) work in Lexer.__init__ that is discarded.

    PYTHONPATH=python python3 tools/perf/python/bench_p10_lexer.py

tokenize() CPU time on: big rule text, `1+1+...` (operator dense), identifiers,
a 1 MB comment, 1 MB of whitespace, a 1 MB source of 12,500 lines. Checksum =
digest of the token list (type, value, offset, line, col) so the streams compare.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from harness import bench, fmt, checksum, env
from sel.lexer import tokenize

def digest(toks):
    import hashlib
    h = hashlib.sha256()
    for t in toks:
        h.update(('%s\x00%s\x00%d:%d:%d\n' % (t.type, t.value, t.pos.line, t.pos.col, t.pos.offset)).encode())
    return h.hexdigest()[:12]

def main(scale=1):
    print(env())
    rule = ' AND '.join('(A%d * B + C > %d AND IF(D, X + 1, Y - 1) >= 2.5 OR NOT (E == "s%d"))' % (i % 9, i, i) for i in range(400 * scale))
    cases = [
        ('big rule text', rule),
        ('1+1+... (200k ops)', '1' + '+1' * (200000 // 2 * scale)),
        ('identifiers (100k)', ' '.join('name%d' % i for i in range(100000 // 2 * scale)) ),
        ('1 MB comment', '# ' + 'x' * 1000000 + '\n1'),
        ('1 MB whitespace', ' ' * 1000000 + '1'),
        ('1 MB, 12,500 lines', '\n'.join('1 + %d # line %d pad padding padding pad pad p' % (i, i) for i in range(12500))),
    ]
    for name, src in cases:
        toks = tokenize(src)
        med, lo, hi = bench(lambda: tokenize(src), reps=3, warm=0)
        print('%-24s %8d chars  %10s  (min %s max %s)  chk %s' % (name, len(src), fmt(med), fmt(lo), fmt(hi), digest(toks)))

if __name__ == '__main__':
    main()
