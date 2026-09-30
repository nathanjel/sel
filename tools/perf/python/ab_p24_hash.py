"""PY-P24(c): A/B of structural_hash over dense lists, baseline tree vs working tree.
    python3 tools/perf/python/ab_p24_hash.py BASELINE_DIR WORKING_DIR"""
import sys, time


def load(root):
    sys.path.insert(0, root)
    for k in [k for k in sys.modules if k == 'sel' or k.startswith('sel.')]:
        del sys.modules[k]
    import sel.value as v
    sys.path.pop(0)
    return v


base, new = load(sys.argv[1]), load(sys.argv[2])


def mk(v, n, w):
    return [v.Value.from_native([[i + j for j in range(w)] for i in range(n)])][0]


def t(v, val, reps=5):
    best = 1e9
    for _ in range(reps):
        s = time.process_time()
        v.structural_hash(val)
        best = min(best, time.process_time() - s)
    return best * 1e3


for n, w in ((20000, 8), (2000, 100), (200, 1000)):
    vb, vn = mk(base, n, w), mk(new, n, w)
    print('%6d lists x %4d: base %.1f ms  new %.1f ms' % (n, w, t(base, vb), t(new, vn)))
