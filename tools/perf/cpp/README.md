# C++ performance benchmarks

`bench.py` runs SEL programs of size n, 2n and 4n through the `sel` CLI and reports
median CPU time, growth, peak RSS and an output checksum (a speedup that changes an
answer shows). `--ab BASE` interleaves runs of a baseline binary and the current one
and reports min/median of each — the robust mode on a loaded machine.

    make -C cpp
    cp cpp/build/sel /tmp/sel-base              # before an optimisation
    ... change, rebuild ...
    python3 tools/perf/cpp/bench.py --ab /tmp/sel-base --reps 5 p7 p9

`sqlbench.cpp` measures the SQL layer (`fold`, `bindings`); its build line is in the
file header. Results and decisions per finding: `docs/interim/2026-09-29/worklist/
performance/results/cpp.md`.
