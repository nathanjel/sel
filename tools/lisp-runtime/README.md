# Lisp runtime comparison

`benchmark.lisp` reports seven timing/allocation samples for stable aliases,
alternating aliases, changing names/shapes, argument wrappers, node construction,
a 1,000-row projection, a short reusable program and compile-and-run. Each sample
collects before timing; bytes come from SBCL's cumulative allocation counter.
`SEL_LISP_RUNTIME_ROOT` selects a complete source tree; default is this checkout.

```sh
XDG_CACHE_HOME=/tmp/sel-lisp-cache sbcl --script tools/lisp-runtime/benchmark.lisp
SEL_LISP_RUNTIME_ROOT=/tmp/sel-before XDG_CACHE_HOME=/tmp/sel-lisp-cache \
  sbcl --script tools/lisp-runtime/benchmark.lisp
```

Archive the baseline into `/tmp/sel-before` (the recorded baseline is `a003a73`).
For separate effects, copy its `lisp/` directory into two further trees. Overlay
only `lisp/src/builtins/structure.lisp` for the alias variant; overlay
`lisp/src/parser.lisp`, `lisp/src/eval.lisp` and `lisp/src/sel.lisp` for the argument
variant. Then run:

```sh
python3 tools/lisp-runtime/compare.py --before /tmp/sel-before \
  --alias-only /tmp/sel-alias-only --args-only /tmp/sel-args-only \
  --output /tmp/sel-lisp-results
```

Both variant options are optional. The driver preloads/compiles every tree,
then runs timed work sequentially. All scale runs use the same current harness,
fixture and result reference, with three warmups and ten samples. Order is
baseline, alias-only, args-only, combined, combined repeat, baseline repeat.
The isolated S6 runs use fifteen samples under each of normal GC and full GC
before each sample. Those GC policies are distinct experiments, not interchangeable
latency measurements. Mandelbrot uses eleven samples and three warmups, with
both comparison orders and identical-output checks.

The recorded exploratory ablations used an earlier two-slot argument memo;
their exact source changes are preserved in the `*experiment.patch.txt` files
under `docs/interim/lisp-runtime/`. Apply those changes to the baseline to
reproduce the exploratory variants. Final measurements under `final/` use the
current single-slot pair representation and fresh controls.

The full baseline and candidate trees need `examples/mandelbrot.sel`. Quicklisp
and the repository's Lisp dependencies must already be installed. Dynamic heap
size is fixed at 4 GiB. Avoid concurrent builds/tests during timing. Raw JSON
includes every timing sample and scale parity checks; logs are local diagnostics.

For retained memory, the existing `tools/metadata/lisp.lisp bench` probe churns
20,000 schemas and 5,000 joins. Run it in each tree, with the same cache/runtime
configuration and normal process isolation. It forces full GC before retained
heap readings; it is not a process RSS measurement.

`program-memory.lisp` uses the same `SEL_LISP_RUNTIME_ROOT` override as the
focused benchmark and measures 5,000 live ten-argument programs, before and
after first execution. Run it three times per source tree in fresh processes.

For the separate S6 validation/GC diagnostic:

```sh
python3 tools/lisp-runtime/profile.py --before /tmp/sel-before \
  --output /tmp/sel-lisp-profile-results
```

This creates instrumented harness copies under `/tmp/sel-lisp-profile` and
compares twenty normal-GC samples with context checks after each sample versus
around the batch. It records query allocation and GC **CPU** time as well as wall
time. It does not modify the shared harness. Keep these diagnostic results
separate from the standard latency table; do not subtract CPU time from wall time.
