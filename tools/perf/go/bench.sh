#!/usr/bin/env bash
# Go performance workloads (go/sel/perf_bench_test.go, go/internal/decimal bench):
# median of COUNT runs with allocations. Usage: tools/perf/go/bench.sh [pattern] [count]
#   tools/perf/go/bench.sh 'P2Sort' 5
# Heavy workloads run with -benchtime=1x via SEL_BENCHTIME=1x.
set -euo pipefail
cd "$(dirname "$0")/../../../go"
pat="${1:-.}"; count="${2:-5}"; bt="${SEL_BENCHTIME:-}"
args=(-run '^$' -bench "$pat" -benchmem -count "$count")
[ -n "$bt" ] && args+=(-benchtime "$bt")
go test ./sel ./internal/decimal "${args[@]}" 2>&1 | python3 -c '
import sys,re,statistics,collections
rows=collections.OrderedDict()
for line in sys.stdin:
    m=re.match(r"(Benchmark\S+)\s+(\d+)\s+([\d.]+) ns/op(?:\s+([\d.]+) MB/s)?\s+(\d+) B/op\s+(\d+) allocs/op",line)
    if not m: 
        if line.startswith(("FAIL","panic","---")): print(line,end="")
        continue
    rows.setdefault(m.group(1),[]).append((float(m.group(3)),int(m.group(5)),int(m.group(6))))
print("%-58s %14s %10s %12s %10s"%("benchmark","median ns/op","min ns/op","B/op","allocs/op"))
for k,v in rows.items():
    ns=[x[0] for x in v]
    print("%-58s %14.0f %10.0f %12d %10d"%(k,statistics.median(ns),min(ns),v[0][1],v[0][2]))
'
