
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

## Python — against 0.9.2 (faff480)

Method and full row: `python.md`, PY-REG-1. Baseline = `git archive faff480` in a scratch tree (the working tree was never touched by git); both trees run `tools/scale-test/sel_benchmarks.py`'s fixtures on `dataset-10x.json`, steady state. Because the wall clock of the runner moved by up to 25 % with the other agents' load (3–15), the table is **in-process CPU time of `program.run()`** (`tools/perf/python/ab_scale_cpu.py BASE_TREE --rounds 6 --runs 5`: fresh process per measurement, base and current alternating, best median per tree, CPython 3.14.7).

| scenario | 0.9.2 (cpu s) | current (cpu s) | ratio | before the recovery work |
|---|---:|---:|---:|---:|
| scenario1 (14-stage pipeline) | 2.312 | 2.686 | 1.16 | 1.19 |
| scenario2 | 0.043 | 0.049 | 1.14 | 1.19 |
| scenario3 | 1.022 | 1.134 | 1.11 | 1.25 |
| scenario4 | 0.065 | 0.072 | 1.12 | 1.20 |
| scenario5 | 0.835 | 0.858 | 1.03 | 1.04 |
| scenario6 | 0.881 | 0.901 | 1.02 | 1.00 |

Python is the one host that is still slower than 0.9.2 on the scale-test (the JS, Lisp and Go sections show 0.6–1.0, with Go's two small scenarios at +10–15 %); the C++ +75 % does not reproduce here (+2 … +16 %). Scenario 1 is on the 15 % line.

**Attribution (cProfile, `tools/perf/python/profile_diff.py BASE_TREE scenarioN`, own-time deltas against 0.9.2 on scenario 1, profiler inflates both sides about equally):** the extra time is (a) the copies SPEC 3.4 now requires — `Value.clone` / `_clone_at` (about 295 k leaf and row copies, 0.36 s of a 9.9 s profile) and `RECORD`'s per-field copy loop (+0.17 s), i.e. the clone-on-collect contract itself; (b) `Value.__init__` for those copies (+0.10 s); (c) `eval_node`/`_dispatch` (+0.16 s together: the snapshot `list(...)` in `walk`, `Args` bookkeeping, evaluate-then-coerce loads in the math plan (`eval.dec` 150 k calls, +0.08 s), size/cap counters `check_collection` (+0.03 s)); (d) `_link` +0.08 s. Offsetting savings: the validation removed from `Value.text` (-0.26 s), `Value.num` / `_eql_at` / join-plan build (-0.3 s together).

**Recovered with no change of semantics** (same answers: conformance 2128/2128, pytest 1683, plain-vs-optimised 2128 sources / 0 differ, sqlt 1305):
1. `FILTER` followed by `MAP` hands its kept elements on without copying them when the `MAP` body only reads (no assignment, no host function): the physical optimiser stamps `adopt_items` on the FILTER step (`optimizer.adopts_elements`); the depth check that the skipped copy performed is kept as `Value.check_depth` (error code and position identical, tested), and `MAP` copies what it collects, as before. A body that assigns or calls a host function keeps the copy (tests: `python/tests/test_perf_regression.py`).
2. `RECORD` skips the copy of a field whose node computes a scalar (arithmetic, unary, scalar builtins) — never of a `LIST`/`RECORD` argument, whose nesting the constructor still bounds; evaluation order (keys, then values) unchanged.
3. `Value.clone` copies a leaf inline and `_clone_at` copies the leaf children of records/lists without a call each (same depth refusals).
4. `Args.val/node/pos_of`: the bounds check is a sign test plus the list's own `IndexError` (the two `len()` calls per read are gone); `record_shape`/`adopt` are read from the call node on demand instead of two slots stored per call.

**Not recovered:** scenario 1/2/3/4 remain at +11 … +16 % CPU. The rest is the copy contract (a leaf copy is one `Value` allocation, 9 slot stores) and the snapshot/budget counters; further gain needs copy-on-write leaves or a lighter `Value` (PY-P24(a), rejected in round 3 at ~45 ns of ~700 ns per Value) — deferred, reconsider if the scale scenarios become a release criterion.

Raw tables: `$CLAUDE_JOB_DIR/tmp/py_abcpu_final2.txt` (this run), `tools/perf/python/ab_scale.py` (runner wall clock variant: scenario1 1.19 → 1.16, scenario3 1.25 → 1.14).
