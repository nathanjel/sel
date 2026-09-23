"""tools/join-filter-oracle/check.py SIDECAR OUT... -- per host, as-written vs
helper-form disagreements, and cross-host disagreements. OUT files are each
host's batch output for the corpus, named after the host."""
import sys, re
import json
recs = [json.loads(l) for l in open(sys.argv[1])]
src = [r['src'] for r in recs]
hosts = sys.argv[2:]
res = {h: open(f'{h}').read().split('\n')[:-1] for h in hosts}
names = [h.rsplit('/', 1)[-1] for h in hosts]
n = len(src)
for h in hosts:
    assert len(res[h]) == n, (h, len(res[h]), n)
def norm(out, i):
    m = re.match(r'^!(E_[A-Z_]+)@(\d+):(\d+)$', out)
    if not m:
        return out
    col = int(m.group(3)) - 1
    for name, start, length in recs[i]['spans']:
        if start <= col < start + length:
            return f'!{m.group(1)} in {name}+{col - start}'
    return f'!{m.group(1)} outside every segment «{src[i][col:col+12]}»'
cross = 0; oracle_bad = 0; errors = 0; values = 0; shown = 0
ref = hosts[0]
for i in range(n):
    outs = [res[h][i] for h in hosts]
    if len(set(outs)) != 1:
        cross += 1
        if shown < 4:
            shown += 1
            print('CROSS-HOST', i, src[i][:400]); [print('   ', nm, o[:160]) for nm, o in zip(names, outs)]
for i in range(0, n, 2):
    for h, nm in zip(hosts, names):
        w = norm(res[h][i], i); o = norm(res[h][i + 1], i + 1)
        if w != o:
            oracle_bad += 1
            if shown < 12:
                shown += 1
                print('ORACLE', nm, i // 2, '\n  written:', src[i][:600], '\n  ->', w[:200], '\n  oracle ->', o[:200])
    first = res[ref][i]
    if first.startswith('!'): errors += 1
    else: values += 1
print(f'{n // 2} pairs x {len(hosts)} hosts: {values} answered values, {errors} raised; '
      f'{cross} cross-host disagreements, {oracle_bad} as-written vs oracle disagreements')
