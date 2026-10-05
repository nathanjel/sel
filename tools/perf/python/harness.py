"""Shared harness for the Python performance benchmarks (labels: tools/perf/cpp/README.md).

Protocol: fixed seeds, a semantic checksum per workload, CPU time (process_time,
robust against the other jobs on the box), warm-up, >= 5 repetitions, median and
spread, and n / 2n / 4n growth runs. Select a checkout with PYTHONPATH=... .

    from harness import bench, growth, checksum
"""
import hashlib
import platform
import random
import statistics
import sys
import time


def bench(fn, reps=5, warm=1, inner=1):
    """CPU seconds per call: median over `reps` repetitions of `inner` calls."""
    for _ in range(warm):
        fn()
    samples = []
    for _ in range(reps):
        t = time.process_time()
        for _ in range(inner):
            fn()
        samples.append((time.process_time() - t) / inner)
    return statistics.median(samples), min(samples), max(samples)


def fmt(t):
    if t < 1e-3:
        return '%.1f us' % (t * 1e6)
    if t < 1:
        return '%.1f ms' % (t * 1e3)
    return '%.2f s' % t


def report(name, fn, **kw):
    med, lo, hi = bench(fn, **kw)
    print('%-44s median %10s  (min %s, max %s)' % (name, fmt(med), fmt(lo), fmt(hi)))
    return med


def growth(name, make, sizes, reps=3):
    """make(n) -> zero-arg callable. Prints time at each size and the ratio."""
    prev = None
    out = []
    for n in sizes:
        fn = make(n)
        med, _, _ = bench(fn, reps=reps)
        ratio = '' if prev is None else '  x%.2f' % (med / prev)
        print('%-44s n=%-8d %10s%s' % (name, n, fmt(med), ratio))
        prev = med
        out.append(med)
    return out


def checksum(obj):
    return hashlib.sha256(repr(obj).encode()).hexdigest()[:12]


def env():
    return 'python %s, %s, rev %s' % (platform.python_version(), platform.machine(),
                                      _rev())


def _rev():
    import subprocess
    try:
        r = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True,
                           text=True).stdout.strip()
        d = subprocess.run(['git', 'status', '--porcelain', '--', 'python'],
                           capture_output=True, text=True).stdout.strip()
        return r + ('+dirty' if d else '')
    except Exception:
        return '?'


def rng(seed):
    return random.Random(seed)
