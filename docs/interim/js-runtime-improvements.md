# JavaScript isolation and prepared records — 2026-09-20

SEL no longer installs `BigInt.prototype.toJSON` on import. Source, SQL imports,
and both built bundles work with a frozen BigInt prototype and preserve a
host-provided JSON hook. Without such a hook, ordinary `JSON.stringify(1n)` still
throws as it did before importing SEL. The two AST snapshot consumers now
serialize BigInts in their own JSON replacers; SEL value formatting is unchanged.

Prepared `RECORD` calls now collect keys and cloned values into two packed
arrays, validate the actual keys, and give the values array directly to the
prepared shape. They avoid one temporary pair per field, a destructuring callback,
and a second values-array traversal. The generic path remains small and separate.
A stale layout constructs entries only on fallback. Key/value evaluation stays
interleaved, each value is cloned at the same point, and duplicate/dynamic keys,
field order, errors and source positions keep their prior behavior.

Native BigInt arithmetic, the shift-based magnitude guard, bounded metadata
ownership, and existing join projectors are retained. No attempt was made to
change the arithmetic evaluator for the previously noisy S2 timing.

## Performance evidence

Baseline: `c5a8991`, verified against the saved pre-edit runtime sources.
Node v24.16.0, Linux x86-64, Intel Xeon D-2141I. Variants run sequentially without
concurrent checks or builds, with natural GC. Raw measurements and source hashes
are in [js-runtime/](js-runtime/).

The focused driver compiles once, warms up five executions, then records nine
samples. Small-record cases execute 20,000 times per sample; the full projection
executes five times per sample over 10,000 three-field rows. Mandelbrot executes
once per sample with a fresh context. Results below are medians per execution.
Output hashes match across variants. Two final-implementation processes bracket
the baseline process; the reported range contains their two medians.

| Workload | Before | After (two processes) | Interpretation |
| --- | ---: | ---: | --- |
| Prepared three-field record | 0.722 µs | 0.652–0.686 µs | About 5–10% less latency |
| Complete 10,000-row projection | 9.783 ms | 8.940–9.050 ms | About 7–9% less latency |
| Dynamic-key record | 0.875 µs | 0.868–0.885 µs | Approximately unchanged |
| Mandelbrot | 65.139 ms | 63.648–64.171 ms | Arithmetic throughput preserved; small variation |

The six 10× application scenarios use five warmups and nine measured executions,
including their normal output and context validation. Final implementation first,
then the baseline, in separate processes. Median prepared execution plus output
materialization, milliseconds:

| Scenario | Before | After | Latency change |
| --- | ---: | ---: | ---: |
| S1 | 577.48 | 595.80 | +3.2% |
| S2 | 12.02 | 12.16 | +1.2% |
| S3 | 225.68 | 227.25 | +0.7% |
| S4 | 9.11 | 9.31 | +2.2% |
| S5 | 494.74 | 488.57 | −1.2% |
| S6 | 227.30 | 230.02 | +1.2% |

These larger scenarios remain roughly flat in this run; do not infer a universal
query speedup from the projection benchmark. An earlier measurement with the
initial inline implementation gave opposite-sign S1/S2 deltas, underscoring the
small shifts' sensitivity to run/heap state. The reproducible benefit is the
prepared record/projection workload. The change also fixes the independent
import failure without altering the native arithmetic design.

## Verification

- 31 focused checks across source, SQL imports and both bundles: prototype
  descriptors, frozen prototypes, host JSON hooks, interleaved side effects,
  clone isolation, duplicate/dynamic/empty/special keys, stale layouts and errors.
- 911 conformance cases pass for source, bundle and minified bundle.
- 852 SQL translation cases pass, including AST immutability snapshots.
- 135 optimizer checks, 14 decimal-guard checks and metadata eviction/shape checks
  pass. Guard checks include proving that below-cap arithmetic does not stringify
  BigInts and checking the million-digit limit.
- 39,990 independent decimal-oracle cases pass with zero mismatches.
- 4,000 differential programs against PHP: zero disagreements or host crashes.
  All 54 host API probes agree with PHP.
- The six application scenarios and all focused benchmark output hashes match
  their references. Both bundles were rebuilt from the final source.

The focused runtime checks are integrated into `tools/check.sh` for each selected
JS artifact. The full five-language repository gate was not rerun.

## Reproduction

Build bundles before checking their imports:

```sh
npm run build
node tools/check-js-runtime.mjs js/src/sel.mjs js/src/sql/index.mjs dist/sel.mjs dist/sel.min.mjs
node tools/check-js-optimizer.mjs
node tools/check-js-decimal-guard.mjs
node tools/metadata/js.mjs
node tools/js-runtime/benchmark.mjs /tmp/js-after.json
SEL_JS_ROOT=/path/to/baseline node tools/js-runtime/benchmark.mjs /tmp/js-before.json
```

Run from the repository root so the unchanged Mandelbrot source is found. The
baseline directory needs its matching `js/` tree. For the application suite, run
`tools/scale-test/sel_benchmarks.mjs` from each source tree with the same absolute
`--dataset` and `--reference`, `--runs 9 --warmups 5`, and distinct `--output` paths.
