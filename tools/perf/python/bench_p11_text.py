"""PY-P11: text builtins (PADL/PADR, UPPER/LOWER, TRIM family) on 1M characters.
    PYTHONPATH=python python3 tools/perf/python/bench_p11_text.py"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
import sel

N = 1_000_000
ctx = {
    'ASC': 'Hello World abc XYZ ' * (N // 20),
    'MIX': 'Zażółć gęślą jaźń ABC xyz ' * (N // 26),
    'PADDED': ' \t\r\n' * (N // 8) + 'x' + ' \t\r\n' * (N // 8),
}
progs = {
    'PADL 1M fill "xy"': 'LEN(PADL("a", 1000000, "xy"))',
    'PADR 1M fill "x"': 'LEN(PADR("a", 1000000, "x"))',
    'UPPER ascii 1M': 'LEN(UPPER(ASC))',
    'LOWER ascii 1M': 'LEN(LOWER(ASC))',
    'UPPER mixed 1M': 'LEN(UPPER(MIX))',
    'LOWER mixed 1M': 'LEN(LOWER(MIX))',
    'TRIM 1M padding': 'LEN(TRIM(PADDED))',
    'LTRIM 1M padding': 'LEN(LTRIM(PADDED))',
    'RTRIM 1M padding': 'LEN(RTRIM(PADDED))',
}
sums = []
for name, src in progs.items():
    p = sel.compile(src)
    report(name, lambda p=p: p.run(dict(ctx)), reps=5)
    sums.append(p.run(dict(ctx)).scalar)
# checksum of the full outputs (not just lengths)
outs = [sel.evaluate(s, dict(ctx)).scalar for s in (
    'UPPER(MIX)', 'LOWER(MIX)', 'UPPER(ASC)', 'LOWER(ASC)', 'TRIM(PADDED)', 'LTRIM(PADDED)', 'RTRIM(PADDED)')]
import hashlib
# The pre-change per-character implementations, as the semantic reference.
SP = ' \t\r\n'
def ref_case(t, up):
    return ''.join((chr(ord(c) - 32) if up and 'a' <= c <= 'z' else chr(ord(c) + 32) if not up and 'A' <= c <= 'Z' else c) for c in t)
def ref_trim(t, l, r):
    a, b = 0, len(t)
    while l and a < b and t[a] in SP: a += 1
    while r and b > a and t[b - 1] in SP: b -= 1
    return t[a:b]
refs = [ref_case(ctx['MIX'], True), ref_case(ctx['MIX'], False), ref_case(ctx['ASC'], True), ref_case(ctx['ASC'], False),
        ref_trim(ctx['PADDED'], 1, 1), ref_trim(ctx['PADDED'], 1, 0), ref_trim(ctx['PADDED'], 0, 1)]
assert refs == outs, 'output differs from the per-character reference'
print('checksum', checksum((sums, [hashlib.sha256(o.encode('utf-8', 'surrogatepass')).hexdigest()[:12] for o in outs])))
