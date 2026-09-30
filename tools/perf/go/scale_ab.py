#!/usr/bin/env python3
"""Interleaved A/B of go/bin/scale-bench between two builds (e.g. the 0.9.2 tree and the
working tree) on each tools/scale-test scenario: CPU time is noisy under load, so the
binaries alternate and the verdict is min/median of the scenario's own measured runs.

    tools/perf/go/scale_ab.py base/scale-bench new/scale-bench [rounds] [scenarios...]
Run from the repository root (the dataset paths are relative to it)."""
import re, statistics, subprocess, sys

base, new = sys.argv[1], sys.argv[2]
rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
ids = sys.argv[4:] or ["1", "2", "3", "4", "5", "6"]

def run(exe, sid):
    out = subprocess.run([exe, "-runs", "3", "-warmups", "1", "-only", sid], capture_output=True, text=True).stdout
    m = re.search(r"median total=([\d.]+)ms", out)
    return float(m.group(1)) if m else float("nan")

for sid in ids:
    res = {"base": [], "new": []}
    for r in range(rounds):
        order = (("base", base), ("new", new)) if r % 2 == 0 else (("new", new), ("base", base))
        for tag, exe in order:
            res[tag].append(run(exe, sid))
    b, n = res["base"], res["new"]
    print("scenario%s base med %8.1f min %8.1f ms | new med %8.1f min %8.1f ms | new/base med %.2f min %.2f" % (
        sid, statistics.median(b), min(b), statistics.median(n), min(n),
        statistics.median(n) / statistics.median(b), min(n) / min(b)), flush=True)
