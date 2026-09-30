#!/usr/bin/env python3
"""C++ host performance scenarios (worklist 2026-09-29, CPP-P*).

Each scenario is a SEL program generator of size n run through the `sel` CLI
(`cpp/build/sel file`); the harness reports the MEDIAN CPU time (user+sys, from
wait4) of several runs at n, 2n and 4n, the growth ratios, peak RSS and a
checksum of the output (so a speedup that changes an answer is visible).

    python3 tools/perf/cpp/bench.py                      # every scenario
    python3 tools/perf/cpp/bench.py p1 p10               # some
    python3 tools/perf/cpp/bench.py --bin /path/sel p1   # compare two builds
    python3 tools/perf/cpp/bench.py --reps 3 --max-s 60 p5

Use an optimised build (make -C cpp; -O2) — never the asan build — and run on
a quiet machine or repeat: CPU time is used, but cache and frequency effects
from other jobs still add noise (the spread column shows it).
"""
import argparse, hashlib, os, resource, statistics, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

LIST = lambda n: 'SPLIT(REPEAT("a,", %d), ",")' % (n - 1)   # n elements
IDX = lambda n: 'MAP(%s, _K)' % LIST(n)                      # 1..n numbers

SCENARIOS = {
  # name: (sizes, generator(n) -> source, note)
  'p1-div-big':     ([2500, 5000, 10000],  lambda n: 'LEN(POWER(9, %d) / POWER(7, %d))' % (n, n // 2), 'CPP-P1 big/big division'),
  'p1-div-30digit': ([10000, 20000, 40000], lambda n: 'A = 123456789012345678901234567890; B = 12345678901234567890; COUNT(MAP(%s, A / B))' % LIST(n), 'CPP-P1 30/20-digit division, many'),
  'p1-mod-61digit': ([10000, 20000, 40000], lambda n: 'A = 1234567890123456789012345678901234567890123456789012345678901; B = 98765432109876543210; COUNT(MAP(%s, A %% B))' % LIST(n), 'CPP-P1 61-digit %'),
  'p2-pow-range':   ([12500, 25000, 50000], lambda n: 'LEN(POWER(99999999, %d))' % n, 'CPP-P2 legal big power'),
  'p2-mul-range':   ([6000, 12000, 24000], lambda n: 'A = POWER(99999999, %d); A * A' % (n * 4), 'CPP-P2 doomed product'),
  'p4-coalesce-direct': ([50000, 100000, 200000], lambda n: 'L = %s; COUNT(MAP(L, _["k"] ?? 0))' % LIST(n), 'CPP-P4 ?? directly on a missing key (probe path)'),
  'p4-coalesce-miss': ([25000, 50000, 100000], lambda n: 'L = %s; COUNT(MAP(L, (_["k"] & "x") ?? 0))' % LIST(n), 'CPP-P4 ?? on a missing key'),
  'p5-small-pred':  ([250000, 500000, 1000000], lambda n: 'L = %s; COUNT(FILTER(L, _ > 5 AND _ < 900000))' % IDX(n), 'CPP-P5 small predicate loop'),
  'p6-format':      ([100000, 200000, 400000], lambda n: 'L = %s; COUNT(MAP(L, (_ / 7) & "x"))' % IDX(n), 'CPP-P6 format/divide'),
  'p6-round':       ([100000, 200000, 400000], lambda n: 'L = %s; SUM(MAP(L, ROUND(_ * 1.37 / 3, 2)), _)' % IDX(n), 'CPP-P6 ROUND'),
  'p7-list-record': ([75000, 150000, 300000], lambda n: 'S = %s; COUNT(LIST(MAP(S, RECORD("id", _K, "g", _K, "v", _K))))' % LIST(n), 'CPP-P7 LIST/RECORD clone'),
  'p7-record-only': ([75000, 150000, 300000], lambda n: 'S = %s; COUNT(MAP(S, RECORD("id", _K, "g", _K, "v", _K)))' % LIST(n), 'CPP-P7 baseline (no LIST wrapper)'),
  'p8-sum':         ([250000, 500000, 1000000], lambda n: 'XS = %s; SUM(XS, _)' % IDX(n), 'CPP-P8 numeric Values'),
  'p8-map-plus':    ([125000, 250000, 500000], lambda n: 'XS = %s; COUNT(MAP(XS, _ + 1))' % IDX(n), 'CPP-P8 numeric Values (MAP)'),
  'p9-upper':       ([500000, 1000000, 2000000], lambda n: 'S = REPEAT("abcdefgh", %d); BLEN(UPPER(S))' % n, 'CPP-P9 UPPER on a big text'),
  'p9-len':         ([500000, 1000000, 2000000], lambda n: 'S = REPEAT("abcdefgh", %d); LEN(S)' % n, 'CPP-P9 LEN'),
  'p9-left':        ([500000, 1000000, 2000000], lambda n: 'S = REPEAT("abcdefgh", %d); LEN(LEFT(S, 3))' % n, 'CPP-P9 LEFT'),
  'p9-short-len':   ([100000, 200000, 400000], lambda n: 'L = %s; COUNT(FILTER(L, LEN(_) == 1))' % LIST(n), 'CPP-P9 short strings'),
  'p10-sort-numtext': ([50000, 100000, 200000], lambda n: 'X = MAP(%s, ((_K * 7919) %% 100003) & ""); JOIN(TAKE(SORT(X), 3), ",")' % LIST(n), 'CPP-P10 sort numeric text'),
  'p10-build-only': ([50000, 100000, 200000], lambda n: 'X = MAP(%s, ((_K * 7919) %% 100003) & ""); JOIN(TAKE(X, 3), ",")' % LIST(n), 'CPP-P10 baseline: the same program without the sort'),
  'p10-sort-text':  ([50000, 100000, 200000], lambda n: 'X = MAP(%s, "k" & ((_K * 7919) %% 100003)); JOIN(TAKE(SORT(X), 3), ",")' % LIST(n), 'CPP-P10 sort text'),
  # ---- round 2 (CPP-P11 .. CPP-P20)
  'p11-top-mid':    ([25000, 50000, 100000], lambda n: 'X = MAP(%s, ((_K * 7919) %% 100003) & ""); COUNT(TOP(X, %d))' % (LIST(n), n // 10), 'CPP-P11 TOP with k = n/10'),
  'p11-top-half':   ([25000, 50000, 100000], lambda n: 'X = MAP(%s, ((_K * 7919) %% 100003) & ""); COUNT(TOP(X, %d))' % (LIST(n), n // 2), 'CPP-P11 TOP with k = n/2 (near a full sort)'),
  'p11-top-desc-asc': ([50000, 100000, 200000], lambda n: 'X = MAP(%s, _K); COUNT(TOP_DESC(X, 1000))' % LIST(n), 'CPP-P11 TOP_DESC on ascending data'),
  'p11-top-small':  ([50000, 100000, 200000], lambda n: 'X = MAP(%s, ((_K * 7919) %% 100003) & ""); COUNT(TOP(X, 10))' % LIST(n), 'CPP-P11 TOP(X,10), must not regress'),
  'p11-sort-ref':   ([25000, 50000, 100000], lambda n: 'X = MAP(%s, ((_K * 7919) %% 100003) & ""); COUNT(SORT(X))' % LIST(n), 'CPP-P11 full SORT reference'),
  'p12-bucket-bare': ([50000, 100000, 200000], lambda n: 'X = MAP(%s, ((_K * 7919) %% 1000003) & ""); COUNT(BUCKET(X, _))' % LIST(n), 'CPP-P12 bare BUCKET, unique keys'),
  'p12-bucket-few': ([100000, 200000, 400000], lambda n: 'X = MAP(%s, (_K %% 6) & ""); COUNT(BUCKET(X, _))' % LIST(n), 'CPP-P12 bare BUCKET, 6 groups'),
  'p12-bucket-agg': ([50000, 100000, 200000], lambda n: 'X = MAP(%s, ((_K * 7919) %% 1000003) & ""); COUNT(BUCKET(X, _, COUNT(_)))' % LIST(n), 'CPP-P12 3-arg BUCKET reference'),
  'p13-paren-wide': ([50000, 100000, 200000], lambda n: 'COUNT(' + '(' * 90 + ', '.join(['1'] * n) + ')' * 90 + ')', 'CPP-P13 90 parens around a wide list'),
  'p13-paren-one':  ([50000, 100000, 200000], lambda n: 'COUNT((' + ', '.join(['1'] * n) + '))', 'CPP-P13 baseline: one paren pair'),
  'p16-rmatch-lit': ([250000, 500000, 1000000], lambda n: 'L = %s; COUNT(FILTER(L, RMATCH("^a", _)))' % LIST(n), 'CPP-P16 literal pattern per call'),
  'p16-rmatch-i':   ([250000, 500000, 1000000], lambda n: 'L = %s; COUNT(FILTER(L, RMATCH("^A", _, "i")))' % LIST(n), 'CPP-P16 literal pattern with the i flag'),
  'p16-rmatch-big': ([500000, 1000000, 2000000], lambda n: 'S = REPEAT("abcdefgh", %d); RMATCH("^abc", S)' % n, 'CPP-P16 anchored RMATCH on a big subject'),
  'p17-nested-agg': ([25000, 50000, 100000], lambda n: 'L = %s; COUNT(MAP(L, SUM(LIST(1), %s)))' % (LIST(n), '+'.join(['_'] * 40)), 'CPP-P17 small inner aggregate per row'),
  'p17-nested-any': ([50000, 100000, 200000], lambda n: 'L = %s; COUNT(FILTER(L, ANY(LIST(1), TRUE)))' % LIST(n), 'CPP-P17 tiny inner ANY per row'),
  'p18-rreplace-big': ([500000, 1000000, 2000000], lambda n: 'S = REPEAT("a", %d); LEN(RREPLACE("a", "bb", S))' % n, 'CPP-P18 many matches in one big text'),
  'p18-rreplace-short': ([100000, 200000, 400000], lambda n: 'L = %s; COUNT(MAP(L, RREPLACE("a", "<$0>", _)))' % LIST(n), 'CPP-P18 RREPLACE per short string'),
  'p19-b64-decode': ([250000, 500000, 1000000], lambda n: 'S = ENCODE_BASE64(TO_UTF8(REPEAT("abcdefgh", %d))); BLEN(DECODE_BASE64(S))' % n, 'CPP-P19 base64 encode+decode'),
  'p19-b64-encode': ([250000, 500000, 1000000], lambda n: 'S = TO_UTF8(REPEAT("abcdefgh", %d)); LEN(ENCODE_BASE64(S))' % n, 'CPP-P19 base64 encode'),
  'p20-filter-keep': ([100000, 200000, 400000], lambda n: 'A = %s; COUNT(FILTER(A, _K %% 2 == 0 OR _K %% 2 == 1))' % LIST(n), 'CPP-P20 FILTER keeping everything'),
  'p20-filter-half': ([100000, 200000, 400000], lambda n: 'A = %s; COUNT(FILTER(A, _K %% 2 == 0))' % LIST(n), 'CPP-P20 FILTER keeping half'),
  'p20-map-ref':    ([100000, 200000, 400000], lambda n: 'A = %s; COUNT(MAP(A, _))' % LIST(n), 'CPP-P20 MAP reference'),
}

def measure_one(binary, path, max_s):
    """Run once; return (output, cpu seconds, peak RSS MB) or (None, err)."""
    import threading
    p = subprocess.Popen([binary, path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    timer = threading.Timer(max_s, p.kill); timer.start()
    out = p.stdout.read()
    _, status, ru = os.wait4(p.pid, 0)
    p.returncode = 0           # reaped by wait4; Popen must not wait again
    p.stdout.close(); timer.cancel()
    if os.WIFSIGNALED(status):
        return None, 'TIMEOUT/SIGNAL %d' % os.WTERMSIG(status), 0
    return out, ru.ru_utime + ru.ru_stime, ru.ru_maxrss // 1024

def measure(binary, src, reps, max_s):
    with tempfile.NamedTemporaryFile('w', suffix='.sel', delete=False) as f:
        f.write(src); path = f.name
    try:
        cpu, sums, peak = [], set(), 0
        for _ in range(reps):
            out, t, mb = measure_one(binary, path, max_s)
            if out is None: return None, None, None, t
            cpu.append(t); peak = max(peak, mb)
            sums.add(hashlib.sha256(out).hexdigest()[:10])
        med = statistics.median(cpu)
        return med, (max(cpu) - min(cpu)) / max(med, 1e-9), peak, ','.join(sorted(sums))
    finally:
        os.unlink(path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--bin', default=os.path.join(ROOT, 'cpp', 'build', 'sel'))
    ap.add_argument('--reps', type=int, default=5)
    ap.add_argument('--ab', metavar='BASEBIN', help='interleave runs of BASEBIN (A) and --bin (B) and report min/median of each; robust under machine load')
    ap.add_argument('--max-s', type=float, default=120)
    a = ap.parse_args()
    names = [n for n in SCENARIOS if not a.names or any(n.startswith(x) for x in a.names)]
    if a.ab:
        print('| scenario | n | A min | A med | B min | B med | B/A (min) | checksums equal |')
        print('|---|---:|---:|---:|---:|---:|---:|---|')
        for name in names:
            sizes, gen, note = SCENARIOS[name]
            n = sizes[-1]
            src = gen(n)
            with tempfile.NamedTemporaryFile('w', suffix='.sel', delete=False) as f:
                f.write(src); path = f.name
            ta, tb, ca, cb = [], [], set(), set()
            for _ in range(a.reps):
                for binary, acc, ck in ((a.ab, ta, ca), (a.bin, tb, cb)):
                    out, t, mb = measure_one(binary, path, a.max_s)
                    if out is None: acc.append(float('nan')); continue
                    acc.append(t); ck.add(hashlib.sha256(out).hexdigest()[:10])
            os.unlink(path)
            f = lambda v: f'{v:.3f}'
            print(f'| {name} | {n} | {f(min(ta))} | {f(statistics.median(ta))} | {f(min(tb))} | {f(statistics.median(tb))} | {min(tb)/min(ta):.2f} | {"yes" if ca == cb else "NO"} |', flush=True)
        return
    print('| scenario | n | cpu s (median) | spread | x growth | peak MB | checksum |')
    print('|---|---:|---:|---:|---:|---:|---|')
    for name in names:
        sizes, gen, note = SCENARIOS[name]
        prev = None
        for n in sizes:
            cpu, spread, mb, ck = measure(a.bin, gen(n), a.reps, a.max_s)
            if cpu is None:
                print(f'| {name} | {n} | {ck} | | | | |'); break
            g = f'{cpu / prev:.2f}' if prev else ''
            print(f'| {name} | {n} | {cpu:.3f} | {spread:.0%} | {g} | {mb} | {ck} |', flush=True)
            prev = cpu

if __name__ == '__main__':
    main()
