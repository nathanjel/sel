# SEL four-commit application benchmark review — 2026-09-20

The changes substantially improve large-decimal arithmetic and multi-join queries. The clearest throughput regression is the C++ storage change in `619bc31`: repeated S1/S5/S6 timings regress despite the smaller header. JavaScript also has a confirmed import failure caused by modifying `BigInt.prototype`. The larger apparent Lisp/PHP slowdowns are sensitive to GC and preceding workloads and are not reproduced as consistent standalone regressions.

| Lane | Main outcome at `c5a8991` |
| --- | --- |
| C++ | All seven workloads beat `ef836bf`; S1/S5/S6 lose some gains after `1614eed`. Storage experiments expose real throughput/footprint tradeoffs. |
| JavaScript | Most scenarios improve; S2 has a small timing shift sensitive to the harness. Mandelbrot improves dramatically. Remove the global JSON hook. |
| Common Lisp | Arithmetic and most scenarios improve. The apparent 27% S6 regression does not survive longer isolated measurement. |
| PHP | Large join and arithmetic gains. Fixed-order S6 and isolated S6 disagree; no blanket slowdown conclusion is justified. The no-GMP fallback also improves substantially. |
| Python | All seven workloads improve. Native integer simplification helps; the bounded power cache does not evict in this Mandelbrot workload. |

All primary benchmark result checks pass after correcting a stale SQL reference, and all timed Mandelbrot frames agree. The four slow old Mandelbrot baselines are single executions, explicitly marked below. Recommendations and code samples follow the complete timing table.

## Method and scope

This comparison evaluates `ef836bf`, `1614eed`, `619bc31`, and `c5a8991` across C++, JavaScript, Common Lisp, PHP, and Python. It uses the six existing scale scenarios with the unchanged 10× dataset (137,100 source rows), plus the unchanged 40×20, ten-iteration `examples/mandelbrot.sel` program. Database servers are not benchmarked; SQL translation is checked by the scale harness. The old checked-in reference proved stale; affected baseline jobs use the current reference after the failure was recorded (see validation notes below).

Runtime sources come from isolated `git archive` snapshots. Every snapshot uses the scale harness from `c5a8991`; the queries and expected output rows are identical across all four references, although SQL strings and hybrid-plan metadata changed. C++ is freshly built with GCC 16.2.1, C++23 and `-O2`. Benchmark processes execute sequentially without concurrent builds or other benchmark jobs. CPU affinity and frequency are not locked, so small differences need confirmation.

The six scenarios use one untimed validation execution, two warmups, and five timed executions in a persistent process, with normal garbage collection. The primary metric is `prepared_total_ms`: execution plus output materialization, excluding source parsing, fixture loading, SQL planning, context validation and process startup. C++ uses the harness's default isolated-context mode: it deep-clones the prepared input before each execution and reports that cost separately as `context_clone_ms`, outside the primary timing. Other lanes reuse the prepared context. Compare revisions within a lane; these boundaries are not identical host-integration costs. PHP uses the shared-context mode, unlimited memory, CLI OPcache and tracing JIT (`1255`, 128 MB buffer). GMP is enabled. The recorded API compilation times are single observations and do not include every lane's lazy optimization work; they are not a rigorous compilation-throughput benchmark.

Mandelbrot uses a compiled program and a fresh empty context per execution. It normally has two warmups and five samples. The slow `ef836bf` C++, JS, Lisp and PHP runs instead have **one timed execution without warmup**. Those measurements include first-run preparation and cannot establish variance or steady-state latency. They are suitable for identifying order-of-magnitude changes, not small regressions. An earlier old-Lisp attempt with seven executions was interrupted before producing measurements and is excluded. Python's old implementation already uses native integers, so it retains normal sampling.

All Mandelbrot frames are retained for byte comparison. The scale runners check result rows against their references; Lisp's recorded validation rows are independently compared by the summary script. Lisp's existing harness checks execution/context stability on each repetition but retains only validation-run rows. This is a benchmark validation pass, not a rerun of the complete conformance suite.

Machine details, source and fixture hashes, raw phase timings, compilation observations, and validation results are in [commit-benchmark/](commit-benchmark/). Reproduction tools are in [tools/commit-benchmark/](../../tools/commit-benchmark/). Reports expose all samples rather than only the fastest run.

## Workloads

| ID | Workload |
| --- | --- |
| S1 | Deep pipeline: filters, record projections, three joins, bucket aggregation, sorting |
| S2 | Custom VIP function in an early projection, filtering and sorting |
| S3 | Join followed by custom risk scoring, grouping and sorting |
| S4 | Filter precomputed spatial distances, project, round and sort |
| S5 | Four-table equi-join, filter, arithmetic projection and sorting |
| S6 | Left outer join, null filter, record projection and deduplication |
| M | Exact-decimal Mandelbrot, 800 points and ten iterations |

S4 does not time distance calculation: the harness prepares distances before execution. S2/S3 execute the complete SEL pipeline in memory; SQL pushdown speed is not part of these timings.

## Primary results

Milliseconds; medians of five samples except the marked old Mandelbrot single executions. Lower is faster. `Δ latest/base` is an observed latency change, not a speedup ratio or a statistical-significance claim.

| Lane | Workload | ef836bf | 1614eed | 619bc31 | c5a8991 | Δ latest/base |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| cpp | scenario1 | 1,254.08 | 525.80 | 581.70 | 581.81 | -53.6% |
| cpp | scenario2 | 13.06 | 8.04 | 5.43 | 5.70 | -56.4% |
| cpp | scenario3 | 348.63 | 205.95 | 192.17 | 185.69 | -46.7% |
| cpp | scenario4 | 21.23 | 11.95 | 9.18 | 8.85 | -58.3% |
| cpp | scenario5 | 2,362.28 | 520.56 | 543.86 | 554.03 | -76.5% |
| cpp | scenario6 | 343.24 | 247.47 | 270.28 | 274.50 | -20.0% |
| cpp | mandelbrot | 15,942.30* | 152.40 | 147.01 | 151.58 | -99.049% |
| js | scenario1 | 713.20 | 687.93 | 688.66 | 605.47 | -15.1% |
| js | scenario2 | 11.65 | 12.72 | 13.24 | 12.40 | +6.4% |
| js | scenario3 | 266.35 | 268.79 | 265.69 | 230.41 | -13.5% |
| js | scenario4 | 12.26 | 10.52 | 10.19 | 9.26 | -24.5% |
| js | scenario5 | 604.21 | 593.34 | 595.58 | 496.50 | -17.8% |
| js | scenario6 | 275.84 | 280.04 | 280.41 | 223.50 | -19.0% |
| js | mandelbrot | 43,442.50* | 95.35 | 64.61 | 65.08 | -99.850% |
| lisp | scenario1 | 709.01 | 494.00 | 499.00 | 503.01 | -29.1% |
| lisp | scenario2 | 14.00 | 12.00 | 13.00 | 12.00 | -14.3% |
| lisp | scenario3 | 368.00 | 247.00 | 243.00 | 227.00 | -38.3% |
| lisp | scenario4 | 25.00 | 16.00 | 16.00 | 16.00 | -36.0% |
| lisp | scenario5 | 514.00 | 428.00 | 424.00 | 426.01 | -17.1% |
| lisp | scenario6 | 213.00 | 216.00 | 214.00 | 271.00 | +27.2% |
| lisp | mandelbrot | 241,445.87* | 78.00 | 79.00 | 80.00 | -99.967% |
| php | scenario1 | 3,879.13 | 3,080.03 | 3,084.45 | 3,063.55 | -21.0% |
| php | scenario2 | 43.17 | 27.18 | 25.86 | 26.08 | -39.6% |
| php | scenario3 | 1,210.14 | 929.74 | 923.28 | 906.80 | -25.1% |
| php | scenario4 | 85.60 | 58.68 | 56.66 | 60.34 | -29.5% |
| php | scenario5 | 14,643.22 | 2,154.75 | 2,140.23 | 2,148.91 | -85.3% |
| php | scenario6 | 990.04 | 1,041.96 | 1,068.98 | 1,074.71 | +8.6% |
| php | mandelbrot | 271,601.45* | 392.26 | 382.86 | 397.58 | -99.854% |
| python | scenario1 | 5,747.64 | 2,792.38 | 2,658.13 | 2,632.86 | -54.2% |
| python | scenario2 | 82.76 | 38.65 | 38.59 | 38.84 | -53.1% |
| python | scenario3 | 2,577.68 | 1,555.56 | 1,524.70 | 1,547.12 | -40.0% |
| python | scenario4 | 144.17 | 68.72 | 66.08 | 65.10 | -54.8% |
| python | scenario5 | 3,997.77 | 505.96 | 509.24 | 502.37 | -87.4% |
| python | scenario6 | 1,842.01 | 710.93 | 740.83 | 733.17 | -60.2% |
| python | mandelbrot | 1,321.11 | 406.94 | 362.51 | 358.57 | -72.858% |

*One execution with no warmup; all other Mandelbrot entries are five-sample medians. The Lisp S6 first-pass median is GC-sensitive; see the repeated measurements before interpreting its delta.

## Findings and recommendations

### 1. C++: the smaller value header trades join throughput for footprint

Follow-up implementation and measurements: [C++ container allocation](cpp-collection-improvements.md). The follow-up tests a bounded pool and a container-specific combined allocation while preserving compact scalar headers.

**Confirmed regression introduced by `619bc31`.** Compared with `1614eed`, S1 slows 10.6% in the primary batch and 7.9% in the repeat; S6 slows 9.2% and 9.0%; S5 slows 4.5% and 5.7%. These are local regressions: `c5a8991` remains substantially faster than `ef836bf` in every C++ workload.

The relevant change is in [`Value::Impl`](../../cpp/sel.hpp): decimal and collection state move to separately allocated optional payloads. The 8-byte aliasing handle is unchanged. The header shrinks from 272 to 72 bytes, but each container now needs another allocation and pointer traversal. The existing header freelist does not pool `Collection` or `Dec` allocations. Input cloning remains approximately 84–86 ms in these controls, so excluded preparation savings do not offset the observed evaluation regressions.

Two diagnostic variants of `619bc31` change only payload storage. All six benchmark parity checks pass for both. They also change header size/cache layout, so this is evidence about the storage design, not proof that allocator calls alone cause every difference.

| Variant | Header bytes | S1 ms | S2 ms | S3 ms | S4 ms | S5 ms | S6 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Repeated `1614eed` | 272 | 536.35 | 7.99 | 206.63 | 12.32 | 515.83 | 249.74 |
| Repeated `619bc31` | 72 | 578.84 | 5.30 | 187.66 | 9.01 | 545.02 | 272.11 |
| Inline optional collection | 192 | 555.51 | 6.83 | 200.65 | 10.10 | 525.77 | 251.68 |
| Inline optional decimal | 176 | 581.39 | 6.71 | 207.82 | 9.78 | 583.78 | 290.85 |

Inlining the collection recovers most of the S6 loss but worsens S2–S4 and enlarges every header. Inlining the decimal fails to recover S1 and makes most workloads worse. Thus the earlier numeric-allocation microbenchmark does **not** justify globally inlining `Dec` for these applications.

**Recommendation:** retain the compact scalar representation. Prototype a bounded pool for collection allocations, or a container-specific allocation that combines header and collection while preserving alias ownership and iterative destruction. Measure both retained memory and these six application scenarios. Do not adopt either inline variant wholesale.

This is the tested collection experiment, **not a recommended final patch**:

```cpp
// Diagnostic alternative inside Value::Impl:
std::optional<Collection> collection;

Collection& mutable_coll() {
    if (!collection) collection.emplace();
    return *collection;
}
```

Exact experiments: [collection patch](commit-benchmark/inline-collection.patch), [decimal patch](commit-benchmark/inline-decimal.patch), [header sizes](commit-benchmark/cpp-payload-sizes.json). Header size excludes payloads, buffers and allocator overhead. These variants were benchmark-validated, not subjected to the full ownership/conformance suite.

### 2. Common Lisp: the apparent 27% S6 regression is not robust

Follow-up: [Lisp alias lookup and prepared arguments](lisp-runtime-improvements.md) records the implemented cache/vector changes, cold and retained-memory costs, and separate GC/validation diagnostics. The historical measurements below remain unchanged.

The first full batch suggests 214 → 271 ms from `619bc31` to `c5a8991`. Individual samples span 179–370 ms and 181–314 ms, respectively. Ten-sample isolated repeats with three warmups do not reproduce that slowdown:

| Variant | Normal GC median / mean ms | Collect-before-sample median / mean ms |
| --- | ---: | ---: |
| `619bc31` | 190.5 / 195.1 | 212.0 / 214.3 |
| `c5a8991` | 183.5 / 193.1 | 219.5 / 220.5 |
| `c5a8991`, EQ alias-cache experiment | 175.0 / 175.5 | 216.0 / 217.3 |

Normal-GC medians reverse the supposed regression; controlled-GC results show only a 3.5% difference. Full GC also changes heap state and subsequent collection behavior, so its numbers are a separate experiment rather than a replacement for normal-GC latency. **Do not report a confirmed 27% Lisp slowdown or revert the bounded caches on that basis.**

There is still an avoidable allocation in [`ensure-row-table-alias`](../../lisp/src/builtins/structure.lisp): `(cons old-shape tbl-name)` constructs a new composite lookup key for every call. A bounded global EQ table keyed by source shape, retaining one last-used table name and plan per shape, removes that allocation without restoring recursively retained per-shape caches. The experiment helps normal-GC S6 modestly; the controlled-GC improvement is small. It is a candidate for broader testing, not a universal speedup.

```lisp
;; Cache hits allocate no composite key. Preserve existing entry/size bounds
;; on insertion; keep ownership in this global table.
(defvar *alias-plan-cache* (make-hash-table :test #'eq))

(let* ((entry (gethash old-shape *alias-plan-cache*))
       (cached (and entry
                    (string= (car entry) tbl-name)
                    (cdr entry))))
  ;; On a miss, build the plan as before, then store:
  ;; (setf (gethash old-shape *alias-plan-cache*)
  ;;       (cons tbl-name (list new-shape add-lower old-size)))
  ...)
```

The one-alias-per-shape policy can miss more often when the same shape alternates table names; retain that tradeoff in follow-up testing. [Exact diagnostic patch](commit-benchmark/lisp-eq-cache.patch).

Another unmeasured opportunity is to prepare the simple-vector argument list on the final optimized call node. Current `make-args` coerces the same AST list for each invocation. Keeping per-call evaluated-value caches while sharing immutable argument vectors would retain constant-time indexed access without the repeated conversion. That requires rebuilding vector metadata whenever optimizer rewrites change call arguments.

### 3. PHP: S6 is sensitive to preceding workloads; the small scalar-access experiment is not a regression fix

Follow-up: [explicit scalar reads, repeated application tests and matched GMP/fallback measurements](php-runtime-improvements.md).

The fixed-order full batch shows S6 worsening 990 → 1,075 ms from `ef836bf` to `c5a8991`. Repeating only S4/S6 with three warmups and ten samples reverses that result. The newer full-batch S4 regression also disappears:

| Revision / variant | Repeated S4 ms | Repeated S6 ms |
| --- | ---: | ---: |
| `ef836bf` | 69.33 | 1,117.91 |
| `1614eed` | 47.04 | 856.95 |
| `619bc31` | 43.61 | 870.19 |
| `c5a8991` | 43.32 | 880.19 |
| Latest, explicit scalar getter experiment | 42.83 | 862.74 |

This supports **workload-order/heap/JIT-state sensitivity**, not a consistently slower standalone S6. It does not prove the fixed-order result is irrelevant to a mixed-query application. Reproduce that application's query mix and context lifetime before treating the original 9% difference as a production regression.

[`Value::$scalar`](../../php/src/Value.php) became private during the first interval. External reads still look like property access but invoke `__get`, then `getScalar`. [`canonicalJoinKey`](../../php/src/Builtins/Structure.php) performs these reads in the row loop. A diagnostic replacing the four scalar reads in `Structure.php` with the explicit getter improves isolated S6 about 2% and S4 about 1%. That is a small candidate improvement, not evidence that magic dispatch explains the original full-batch delta.

```php
// Inside Structure, retain the same lazy formatting/cache semantics.
// Current spelling takes the magic-property path:
$scalar = $v->scalar;

// Candidate explicit internal call:
$scalar = $v->getScalar();
```

**Recommendation:** use explicit internal accessors where profiling supports them, while retaining the compatible external property API. Do not expose the backing scalar directly or bypass decimal-cache invalidation. Do not undertake a broad value/decimal representation rewrite to chase this small result. [Exact experiment](commit-benchmark/php-direct-scalar.patch).

The largest PHP win is robust: S5 drops from 14.64 s to 2.15 s. Mandelbrot drops from a 271.60 s single baseline execution to a 397.58 ms median with GMP. A separate latest-revision run without GMP takes **1,358.51 ms** (one warmup, three samples) and produces the same frame. That fallback run uses `php -n`, explicitly loads ctype, and retains the same OPcache/JIT flags; other ini-loaded extensions are also absent. It demonstrates the portable fallback's practical speed, not an otherwise perfectly identical extension-ablation environment.

**Keep both arithmetic paths.** PHP's native integers help bounded mantissas, GMP helps large magnitudes when available, and the packed-limb fallback delivers a substantial improvement without GMP. Publish extension/configuration details with benchmark claims.

### 4. JavaScript: keep the arithmetic/layout wins, remove the import-time prototype mutation

`c5a8991` improves the join-heavy scenarios relative to `619bc31`: S1 −12.1%, S3 −13.3%, S5 −16.6%, S6 −20.3%. Source review points to using resolved shapes directly and preparing record layouts instead of repeatedly constructing/interpreting layout signatures. These changes are bundled, so the timings do not isolate each contribution.

Mandelbrot improves from 43.44 s in the single old execution to 65.08 ms at the latest revision. The `619bc31` guard change also reduces the newer implementation's Mandelbrot median from 95.35 to 64.61 ms: it avoids hexadecimal conversion for magnitudes far below the integer-digit cap. Keep that gain.

S2 is a small, less clear result. Its first-pass latest/baseline difference is +6.4% (12.40 versus 11.65 ms). With five warmups and twenty samples, that narrows to +3.0% (12.16 versus 11.80 ms). Moving input-integrity hashing from between samples to the batch boundary gives +1.2% (10.77 versus 10.64 ms), while preserving result checks on every sample and input checks before/after the batch.

| Revision | S2, integrity hash between samples ms | S2, integrity hash at boundary ms |
| --- | ---: | ---: |
| `ef836bf` | 11.801 | 10.644 |
| `1614eed` | 11.317 | 10.519 |
| `619bc31` | 12.156 | 11.219 |
| `c5a8991` | 12.157 | 10.767 |

`619bc31` is modestly slower than `1614eed` across these S2 measurements, but the absolute gap is below a millisecond and sample ranges overlap. The changed structural hash runs outside S2's timer yet changes allocation/GC state before subsequent samples. This is a plausible contributor, not a proven sole cause; the boundary experiment does not remove every difference. **Do not trade away the large arithmetic wins for an unisolated S2 micro-optimization.** Retain the observed deltas and use a profiler plus stable application-level timing boundaries before changing the evaluator.

There is a separate **confirmed functional regression** in [`decimal.mjs`](../../js/src/decimal.mjs): importing SEL installs `BigInt.prototype.toJSON`. With an immutable intrinsic prototype, the old revision imports and evaluates successfully, while the latest import throws:

```js
Object.freeze(BigInt.prototype);
const { compile, Value } = await import('./js/src/sel.mjs');
console.log(compile('1+2').run(Value.none()).asText());
// ef836bf: "3"
// c5a8991: TypeError: Cannot add property toJSON, object is not extensible
```

**Recommendation, high priority:** remove the prototype mutation. Keep serialization policy at the actual diagnostic/JSON boundary, without changing application-global behavior:

```js
// For diagnostic structures containing primitive BigInt fields.
const json = JSON.stringify(diagnostic, (_key, value) =>
  typeof value === 'bigint' ? value.toString() : value
);
```

Use SEL's own value serialization for SEL results; this replacer is not a replacement decimal formatter. [Reproduction results](commit-benchmark/js-prototype-probe.json).

A further unmeasured layout opportunity is visible in `recordFromArgs`: even with a prepared shape, it constructs `[key, value]` pairs, traverses them to validate keys, then maps them into a second values array. A specialized prepared-layout path could avoid those temporary pairs. Preserve interleaved key/value evaluation, cloning and the dynamic-key fallback; benchmark the complete projection before adding more complexity.

### 5. Python: the native-integer simplification helps; bounded powers do not churn in this example

Follow-up: [native arithmetic simplifications, repeated benchmarks and heterogeneous cache probes](python-runtime-improvements.md).

The latest Python implementation improves all seven workloads over `ef836bf`: S1/S2/S4 roughly halve, S5 falls from 3,998 to 502 ms, and Mandelbrot falls from 1,321 to 359 ms. From `1614eed` to `619bc31`, Mandelbrot improves another 10.9%; `c5a8991` is close to that result.

Python already uses arbitrary-precision `int`. A second cached "small integer" representation adds branches and bookkeeping without changing the underlying arithmetic type. The simplified alignment path avoids multiplication and power lookups when scales match:

```python
# Keep magnitude as int and scale/sign explicit; do not round through float.
if a.scale == b.scale:
    return a.digits, b.digits, a.scale
if a.scale > b.scale:
    return a.digits, b.digits * _pow10(a.scale - b.scale), a.scale
return a.digits * _pow10(b.scale - a.scale), b.digits, b.scale
```

A diagnostic over three Mandelbrot frames counts 93,456 `_pow10` calls at `1614eed` versus 66,405 at both later commits. This supports the reduction in redundant alignment work, although the bundled commit also changes other paths and the diagnostic itself is not a timing experiment.

The bounded-cache implementation retains 57 large powers and records **zero evictions** in this workload, the same miss count as the unbounded predecessor. Thus these timings show no churn penalty here; they do **not** validate workloads exceeding the 64-entry/weighted-size budget. Similarly, the six scenarios use stable schemas and cannot establish worst-case behavior for the 256-entry shape cache. Keep a heterogeneous-schema/power workload alongside this application suite; the existing [metadata probes](../../tools/metadata/) cover that separate concern.

**Recommendation:** retain native integer arithmetic, the simpler alignment/dispatch paths, native valid-input UTF-8 operations, and bounded metadata ownership. Avoid reintroducing a language-neutral "small integer fast path" merely to resemble the C++ implementation.

## Validation, reproducibility and next steps

- All 20 primary six-scenario reports pass after correcting the stale baseline reference; all 120 scenario/commit/lane cells have five timing samples. Expected SEL queries and row values were verified identical across the four original references.
- All 20 primary Mandelbrot cases render the same frame across their 84 timed executions. The latest PHP no-GMP output also matches.
- All C++ storage, Lisp cache, PHP getter, and JS hashing-boundary diagnostic reports pass their benchmark result checks. Lisp diagnostic validation rows are also checked against the shared oracle during the final artifact audit.
- The first old-reference C++ report is retained as [`ef836bf-cpp-old-reference.json`](commit-benchmark/ef836bf-cpp-old-reference.json). Its failures were SQL/reference discrepancies, not row mismatches. The initial JS attempt stopped at its SQL gate. Both jobs were rerun against the current reference, with runtime sources unchanged; PHP/Python baseline jobs subsequently used the corrected reference as well. Original Lisp validation rows matched without a rerun.
- No database execution, cold-process startup comparison, full conformance rerun, or retained-memory measurement is included here. Header sizes are measured object sizes, not process RSS. The C++ prototype and PHP getter improvements should not be treated as production-ready patches without the corresponding functional/ownership checks.

Reproduce the main batch with `tools/commit-benchmark/run.py`, then run `summarize.py` and `table.py`. The same directory contains the diagnostic drivers and a README. Raw primary and repeat measurements, source patches and machine metadata are retained in [commit-benchmark/](commit-benchmark/); [timings.csv](commit-benchmark/timings.csv) is the primary comparison in spreadsheet form.

Recommended order of follow-up work:

1. Remove JavaScript's global BigInt JSON hook; preserve native BigInt arithmetic.
2. Prototype C++ collection allocation strategies that retain the compact scalar header, then compare throughput **and** retained memory. Avoid wholesale reverts or globally inline `Dec`.
3. Improve benchmark isolation: report natural-GC distributions, test both isolated scenarios and representative query sequences, and keep input-integrity work outside the repeated timing interval where practical.
4. Consider the bounded EQ alias cache in Lisp and explicit PHP scalar getters as small, separately measured improvements. Keep them lower priority than the confirmed C++ regression and JS import failure.
5. Preserve the successful language-specific arithmetic implementations; continue testing the stable-schema application suite and metadata-churn suite separately.
