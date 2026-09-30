"""In-process A/B of SEL programs: baseline tree vs working tree, alternating rounds, best
CPU time of each. Robust on a loaded box where two separate runs are not comparable.
    python3 tools/perf/python/ab_programs.py BASELINE_DIR WORKING_DIR [ROUNDS] [SET]
SET selects a workload set: sums, walks, sorts, micro, regex, all (default)."""
import random
import sys
import time


def load(root):
    sys.path.insert(0, root)
    for k in [k for k in sys.modules if k == 'sel' or k.startswith('sel.')]:
        del sys.modules[k]
    import sel
    sys.path.pop(0)
    return sel


base, new = load(sys.argv[1]), load(sys.argv[2])
rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 5
which = sys.argv[4] if len(sys.argv) > 4 else 'all'

rnd = random.Random(25)
rows = [{'n': rnd.randrange(0, 1000), 'p': '%d.%02d' % (rnd.randrange(0, 50), rnd.randrange(0, 100)),
         's': 'k%d' % rnd.randrange(0, 500)} for _ in range(100_000)]
native = {'R': rows, 'L': [r['n'] for r in rows],
          'B': [bytes([rnd.randrange(256) for _ in range(8)]) for _ in range(20_000)]}

SETS = {
    'sums': [('SUM(R, _["n"])', 'SUM int rows'), ('SUM(R, _["p"])', 'SUM scale-2 text'),
             ('SUM(L, _)', 'SUM scalars')],
    'walks': [('COUNT(MAP(R, _["n"]))', 'MAP'), ('COUNT(FILTER(R, _["n"] > 500))', 'FILTER'),
              ('ALL(R, _["n"] >= 0)', 'ALL')],
    'micro': [('COUNT(MAP(R, "x"))', 'text literal per row'),
              ('COUNT(FILTER(R, _["s"] $== "k9"))', 'literal compare'),
              ('COUNT(MAP(R, _["s"] & "-" & "x"))', 'concat literals'),
              ('COUNT(MAP(B, _ BAND _))', 'BAND 8-byte x20k')],
    'regex': [('COUNT(FILTER(R, RMATCH("^k1", _["s"])))', 'RMATCH no flag'),
              ('COUNT(FILTER(R, RMATCH("^K1", _["s"], "i")))', 'RMATCH i flag'),
              ('COUNT(FILTER(R, RFIND("9", _["s"]) > 0))', 'RFIND')],
    'sorts': [('COUNT(SORT_BY(R, _["n"]))', 'SORT_BY n'), ('COUNT(SORT_BY(R, _["s"]))', 'SORT_BY s'),
              ('COUNT(SORT_BY(R, _K))', 'SORT_BY _K'), ('COUNT(SORT(L))', 'SORT plain')],
}
chosen = SETS[which] if which in SETS else [x for v in SETS.values() for x in v]
ctxb, ctxn = base.Value.from_native(native), new.Value.from_native(native)
for src, name in chosen:
    pb, pn = base.compile(src), new.compile(src)
    assert pb.run(ctxb).dump() == pn.run(ctxn).dump(), src
    bb = bn = 1e9
    for _ in range(rounds):
        s = time.process_time(); pb.run(ctxb); bb = min(bb, time.process_time() - s)
        s = time.process_time(); pn.run(ctxn); bn = min(bn, time.process_time() - s)
    print('%-22s base %7.1f ms   new %7.1f ms   new/base %.2f' % (name, bb * 1e3, bn * 1e3, bn / bb))
