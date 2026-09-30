
## JS — against 0.9.2 (faff480)

Baseline: `git archive faff480` into a scratch tree (the working tree was never touched by git), node v24.16.0, current = working tree on top of 223885e (dirty). The box was loaded by other agents, so every number is the minimum of alternating repetitions (baseline, current, baseline, ...) and ratios are the signal.

tools/scale-test runner (`tools/perf/js/ab-scale.py BASE_TREE 5 3`, 10x dataset, steady state; prepared_total_ms = program run + materialisation, min over 5 reps of 3-run medians):

| scenario | 0.9.2 (ms) | current (ms) | ratio |
|---|---:|---:|---:|
| scenario1 | 731.0 | 705.2 | 0.96 |
| scenario2 | 13.5 | 8.4 | 0.62 |
| scenario3 | 252.7 | 220.4 | 0.87 |
| scenario4 | 10.4 | 9.5 | 0.91 |
| scenario5 | 290.2 | 267.5 | 0.92 |
| scenario6 | 249.1 | 249.7 | 1.00 |

`tools/js-runtime/benchmark.mjs` (ms per run, min of 3 alternating reps of 9-sample medians):

| case | 0.9.2 | before fix | after fix | ratio (after / 0.9.2) |
|---|---:|---:|---:|---:|
| record | 0.0008 | 0.0007 | 0.0007 | 0.89 |
| projection (`MAP` of `RECORD` over 10,000 rows) | 10.78 | 12.45 (1.15) | 11.01 | 0.96 |
| dynamic-record | 0.0010 | 0.0007 | 0.0007 | 0.66 |
| mandelbrot | 67.6 | 68.4 | 70.2 | 1.00 |

**Attribution and recovery (JS-REG-1).** JS is not slower than 0.9.2 on the six scale-test scenarios (0.62-1.00). The one workload over the noise floor was `MAP` over rows with a `RECORD` body (+15%): since SPEC 3.4 `MAP` copies what it collects, and the body's freshly built record was being copied a second time. `MAP` now skips the copy when the body itself builds its result (a `RECORD` or `LIST` call, whose arguments were copied into it, or arithmetic); a body returning anything by reference (`MAP(X, _)`) is still copied. The projection case is at 0.96 of 0.9.2. The alias cases in conformance/25 (`alias.aggregate-copies.map*`) and a new runtime check (`JS-REG-1 MAP copies what it collects unless the body built it`) hold the semantics. Lanes: conformance 2128/2128 on src, dist/sel.mjs and dist/sel.min.mjs; runtime 395; optimizer 149; plain-vs-optimised 0 differ; sqlt 1305; JS SQL unit 48/48; hybrid parity 68/0.

Not compared against 0.9.2 (the 0.9.2 tree has none of their entry points): the tools/perf/js/p* benchmarks, which use APIs added in this wave; their baselines are in results/js.md.

## Lisp — against 0.9.2 (faff480)

Method and full tables: `lisp.md`, row LISP-REG-1 (baseline `git archive faff480` in a scratch tree; same harness/dataset for both; interleaved runs; minimum and median of 15+ samples; load 5–10 from other agents).

| scenario | 0.9.2 (min ms) | current (min ms) | ratio |
|---|---:|---:|---:|
| scale 1 (deep 14-stage pipeline) | 507 | 488 | 0.96 |
| scale 2 | 11 | 8 | 0.73 |
| scale 3 | 233 | 210 | 0.90 |
| scale 4 | 15 | 7 | 0.47 |
| scale 5 | 218 | 197 | 0.90 |
| scale 6 | 198 | 198 | 1.00 |

No scale scenario is slower than 0.9.2 (the C++ +75% does not reproduce in Lisp). Micro-benchmarks showed the SPEC 3.4 copy cost on aggregates; fresh results are now adopted instead of copied again (MAP over RECORD/LIST, pipelines of fresh stages, `B = <fresh call>`): all measured cases are at or below 1.07× of 0.9.2 except FILTER/SORT over a *variable* source (1.28× / 1.62×), where the copy is mandated — deferred with its reconsideration condition in `lisp.md`.

## Go — against 0.9.2 (faff480)

Method and full row: `go.md`, GO-REG-1. Baseline = `git archive faff480 go` built in a scratch tree; both binaries are `go/bin/scale-bench` on the same `tools/scale-test/dataset-10x.json` (steady state), run alternately (`tools/perf/go/scale_ab.py`, 5 rounds × 2 runs, median / min of `prepared_total` ms, load 6–10 from other agents).

| scenario | 0.9.2 (ms, med / min) | current (ms, med / min) | ratio (med) | ratio (min) |
|---|---:|---:|---:|---:|
| scenario1 | 640.0 / 624.6 | 573.5 / 568.3 | 0.90 | 0.91 |
| scenario2 | 5.3 / 5.0 | 8.1 / 7.9 | 1.53 | 1.58 |
| scenario3 | 250.8 / 231.8 | 218.8 / 203.9 | 0.87 | 0.88 |
| scenario4 | 12.1 / 11.6 | 14.3 / 13.4 | 1.17 | 1.16 |
| scenario5 | 354.5 / 337.9 | 248.1 / 241.7 | 0.70 | 0.72 |
| scenario6 | 302.0 / 294.8 | 244.4 / 238.2 | 0.81 | 0.81 |

**Attribution and recovery (GO-REG-1).** Go is faster than 0.9.2 on scenarios 1, 3, 5, 6 (0.70–0.90×). Scenarios 2 and 4 are 5–12 ms runs with the 10× dataset resident; ≈ 44 % of their CPU is the garbage collector's mark phase. Longer single-scenario runs (200 runs, 20 warmups) put scenario 2 at +10–15 % (4.0–4.8 → 5.1–5.4 ms); with `GOGC=off` +4–11 % (scenario 2) and 0.78× (scenario 4), so most of the gap is GC work over larger row objects and the copies SPEC §3.4 now requires (`MAP`/`FILTER`/TOP*/assignment copy what they keep), plus the atomic derived caches that made `Value` larger (GO-C6). Recovered with no semantic change: a `FILTER` whose parent only reads or copies its rows now keeps them aliased (`Context.NoCopy`; declined wherever a value could change while the rows are read; `nocopy_test.go` holds elision on/off to identical outcomes including depth and row-copy failures); the decimal, one-shot-`Eval`, slot-cache and structural-hash items (GO-P21, P24–P26). **Residual, not recovered:** +10–15 % (≈ 0.5–2.8 ms absolute) on scenarios 2 and 4; the next idea is shrinking `Value` back toward 0.9.2's 152 B (merge the atomic caches, reorder the flag bytes), which touches every constructor and was not attempted. Not compared: the round-3 benchmark files (`perf3_*`), which use entry points added in this wave.
