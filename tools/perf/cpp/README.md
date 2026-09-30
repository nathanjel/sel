# C++ performance benchmarks

`bench.py` runs SEL programs of size n, 2n and 4n through the `sel` CLI and reports
median CPU time, growth, peak RSS and an output checksum (a speedup that changes an
answer shows). `--ab BASE` interleaves runs of a baseline binary and the current one
and reports min/median of each — the robust mode on a loaded machine.

    make -C cpp
    cp cpp/build/sel /tmp/sel-base              # before an optimisation
    ... change, rebuild ...
    python3 tools/perf/cpp/bench.py --ab /tmp/sel-base --reps 5 p7 p9

`sqlbench.cpp` measures the SQL layer (`fold`, `bindings`); `evalbench.cpp` measures one-shot
`compile`/`evaluate` per call (CPP-P15). Build lines are in the file headers. Results and decisions per finding: `docs/interim/2026-09-29/worklist/
performance/results/cpp.md`.

Round 3 additions: `bench.py` scenarios `p21`–`p25` (RECORD literal keys, DISTINCT, strict calls, front-end
throughput on 2–8 MB sources); `sqlbench.cpp` modes `plan` (plan_hybrid over N FILTERs after an unsupported
step) and `hybrid` (execute_hybrid against an unrelated N-row context).
