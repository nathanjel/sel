"""PY-P12: base64 / CRC32 / hex / BTL / LTB on 1 MB inputs.
    PYTHONPATH=python python3 tools/perf/python/bench_p12_binary.py"""
import hashlib
import random
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, growth, checksum
import sel

rnd = random.Random(12)
N = 900_000
data = bytes(rnd.randrange(256) for _ in range(N))
hexs = data.hex()
import base64
b64 = base64.b64encode(data).decode()
ctx = {'B': sel.Value.bin(data), 'H': hexs, 'S': b64}

progs = {
    'ENCODE_BASE64 1MB': 'LEN(ENCODE_BASE64(B))',
    'DECODE_BASE64 1.4MB': 'BLEN(DECODE_BASE64(S))',
    'CRC32 1MB': 'CRC32(B)',
    'FROM_HEX 2MB': 'BLEN(FROM_HEX(H))',
    'BTL 1MB': 'COUNT(BTL(B))',
    'LTB 1MB': 'BLEN(LTB(BTL(B)))',
}
sums = []
for name, src in progs.items():
    p = sel.compile(src)
    reps = 3 if name.startswith(('BTL', 'LTB')) else 5
    report(name, lambda p=p: p.run(dict(ctx)), reps=reps)
    sums.append(p.run(dict(ctx)).scalar)
outs = [sel.evaluate(s, dict(ctx)) for s in ('ENCODE_BASE64(B)', 'TO_HEX(DECODE_BASE64(S))', 'CRC32(B)', 'TO_HEX(FROM_HEX(H))')]
print('checksum', checksum((sums, [hashlib.sha256(str(o.scalar if o.kind == 'TEXT' else o.scalar).encode()).hexdigest()[:10] for o in outs])))
print('growth:')
for nm, src in (('ENCODE_BASE64', 'LEN(ENCODE_BASE64(B))'), ('CRC32', 'CRC32(B)')):
    def make(n, src=src):
        c = {'B': sel.Value.bin(data[:n])}
        p = sel.compile(src)
        return lambda: p.run(dict(c))
    growth(nm, make, [N // 4, N // 2, N])
