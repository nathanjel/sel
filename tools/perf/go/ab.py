#!/usr/bin/env python3
"""A/B comparison of two compiled Go test binaries on a shared, loaded machine.

    go test -c -o /tmp/base.test ./sel   (from a tree with the old code)
    go test -c -o /tmp/new.test  ./sel
    tools/perf/go/ab.py /tmp/base.test /tmp/new.test 'P7P8Interp/.*/n=100000' [rounds] [benchtime]

The two binaries run ALTERNATELY, so load that comes and goes hits both; the
verdict is the median and the minimum over the rounds of ns/op (single CPU, so
the GC does not spread over cores another agent is using).
"""
import re, statistics, subprocess, sys

base, new, pat = sys.argv[1], sys.argv[2], sys.argv[3]
rounds = int(sys.argv[4]) if len(sys.argv) > 4 else 9
bt = sys.argv[5] if len(sys.argv) > 5 else "3x"
res = {"base": {}, "new": {}}
line = re.compile(r"(Benchmark\S+)\s+\d+\s+([\d.]+) ns/op(?:.*?(\d+) B/op\s+(\d+) allocs/op)?")
for r in range(rounds):
    for tag, exe in (("base", base), ("new", new)) if r % 2 == 0 else (("new", new), ("base", base)):
        out = subprocess.run([exe, "-test.run", "^$", "-test.bench", pat, "-test.benchtime", bt,
                              "-test.cpu", "1", "-test.benchmem"], capture_output=True, text=True).stdout
        for m in line.finditer(out):
            res[tag].setdefault(m.group(1), []).append((float(m.group(2)), m.group(3), m.group(4)))
print("%-56s %11s %11s %7s   %11s %11s %7s   allocs base->new" % ("benchmark", "base med", "new med", "ratio", "base min", "new min", "ratio"))
for k in res["base"]:
    if k not in res["new"]:
        continue
    b = [x[0] for x in res["base"][k]]; n = [x[0] for x in res["new"][k]]
    ba, na = res["base"][k][0][2], res["new"][k][0][2]
    print("%-56s %11.0f %11.0f %7.2f   %11.0f %11.0f %7.2f   %s -> %s" % (
        k.replace("Benchmark", ""), statistics.median(b), statistics.median(n), statistics.median(b) / statistics.median(n),
        min(b), min(n), min(b) / min(n), ba, na))
