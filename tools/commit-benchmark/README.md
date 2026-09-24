# Four-commit application comparison

From the repository root:

```sh
python3 tools/commit-benchmark/run.py
python3 tools/commit-benchmark/summarize.py
python3 tools/commit-benchmark/table.py
```

The runner archives `ef836bf`, `1614eed`, `619bc31`, and `c5a8991` under
`/tmp/sel-commit-benchmark`, overlays the current scale harness, builds each
C++ lane, then runs each lane sequentially. It needs the repository's existing
10× dataset and installed language runtimes, plus the usual Lisp Quicklisp
dependencies. PHP runs with CLI OPcache and tracing JIT; GMP availability affects
Mandelbrot substantially. It does not start database servers.

Results go to `tools/commit-benchmark/results`; build and process logs stay under
`/tmp/sel-commit-benchmark`. The archived runtime code is never copied back into
the working tree. Existing result filenames are overwritten unless `--resume`
is used. `--prepared` skips archive extraction and compilation; use it only with
compatible, previously prepared snapshots/binaries. `--latest-first` changes
commit order to 1614eed, 619bc31, c5a8991, ef836bf. `--reverse` writes a separate
`-repeat` batch in reversed commit order.

The six scenarios use two warmups and five measured executions. Mandelbrot uses
the same counts except at ef836bf in C++, JS, Lisp and PHP: those slow baselines
have zero warmups and one full execution. `MANDEL_RUNS` and `MANDEL_WARMUPS` also
control the standalone Mandelbrot hosts. Timers exclude startup and parsing;
Mandelbrot's single-sample baselines include lazy first-execution preparation.

Additional diagnostics, **run only after other benchmarks stop**:

```sh
python3 tools/commit-benchmark/cpp-ablation.py
python3 tools/commit-benchmark/lisp-ablation.py
python3 tools/commit-benchmark/targeted-repeat.py
python3 tools/commit-benchmark/js-hash-control.py
```

The C++ diagnostic tests inline optional collection/decimal storage separately
against 619bc31, bracketed by original-commit controls. The Lisp diagnostic
repeats S6 with normal and forced-before-sample GC, and tests an EQ-keyed global
alias cache with one last-used alias per source shape. These are experiments,
not production fixes. Their source changes exist only in temporary snapshots.
The power-cache profiler runs from a snapshot root and accepts an output path;
it counts calls/misses over three Mandelbrot frames and is not a timing test.

The interpretation of the last published run (`docs/interim/commit-benchmark-review.md`) is in the git history.

`targeted-repeat.py` repeats PHP S4/S6 and JS S2, tests explicit PHP scalar
getters, records Python power-cache calls, reproduces the JS frozen-prototype
import failure, and measures latest PHP Mandelbrot without GMP. The no-GMP
configuration uses `php -n` with ctype and the same OPcache/JIT flags.
`js-hash-control.py` checks S2 with input-integrity hashing at batch boundaries;
every measured result still receives its normal parity check.

The runner uses the current SQL/result reference for all revisions. The original
ef836bf reference has stale SQL/continuation expectations even though its queries
and expected rows match. The report retains the original failed C++ reference
check and explains the corrected reruns. Inspect exit status and each report's
`passed` field; the runner returns nonzero if any launched job fails.
