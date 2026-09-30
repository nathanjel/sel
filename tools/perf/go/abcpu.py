#!/usr/bin/env python3
"""A/B on CPU TIME (user+sys of the child), for a machine whose load makes wall
time useless. Each invocation runs ONE benchmark pattern for a fixed iteration
count in a single-CPU test binary, so CPU time ~ setup + N * ns/op, and setup is
the same for both binaries. Binaries alternate; the verdict is min/median over rounds.

    tools/perf/go/abcpu.py base.test new.test 'P7P8Interp/filter_and/n=100000' [rounds] [iters]
"""
import resource, statistics, subprocess, sys

base, new, pat = sys.argv[1], sys.argv[2], sys.argv[3]
rounds = int(sys.argv[4]) if len(sys.argv) > 4 else 7
iters = sys.argv[5] if len(sys.argv) > 5 else "10x"

def cpu(exe):
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    subprocess.run([exe, "-test.run", "^$", "-test.bench", pat, "-test.benchtime", iters, "-test.cpu", "1"],
                   capture_output=True)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)

res = {"base": [], "new": []}
for r in range(rounds):
    order = (("base", base), ("new", new)) if r % 2 == 0 else (("new", new), ("base", base))
    for tag, exe in order:
        res[tag].append(cpu(exe))
b, n = res["base"], res["new"]
print("%-44s base med %.3fs min %.3fs | new med %.3fs min %.3fs | ratio med %.2f min %.2f" % (
    pat, statistics.median(b), min(b), statistics.median(n), min(n),
    statistics.median(b) / statistics.median(n), min(b) / min(n)))
