# Lisp alias lookup and prepared arguments — 2026-09-20

The [original review](commit-benchmark-review.md) did not establish a 27% S6
regression: isolated repetitions reversed that result, and full GC before each
sample produced a different distribution. These changes address concrete
allocations identified in that review; they do not revert the bounded caches.

## Changes

Alias plans remain globally owned and bounded to **256 total plans**, not 256
plans per shape. An outer `EQ` table selects the source shape; an inner `EQUAL`
table selects the table-name string. Hits allocate neither a composite key nor a
lowercase copy of the name. Lowercasing occurs only when building a plan or
handling an ordinary, unshaped row. The original maximum destination width
(256 keys) and total key length (16,384 characters) still gate insertion.

This differs from the diagnostic single-alias-per-shape cache: alternating table
names can both stay cached. Plans are not attached to record shapes, so shape
chains cannot retain an unbounded chain of alias caches. Eviction drops cache
ownership without invalidating layouts or rows held by live values. The cost is
an extra hash table for each cached source shape, which cold/changing-schema
workloads and retained-memory measurements must include.

```lisp
(let* ((plans (gethash old-shape *alias-plan-cache*))
       (cached (and plans (gethash tbl-name plans))))
  ;; A hit reuses the destination layout and scalar metadata.
  ;; The returned row/storage still allocate and preserve alias ownership.
  ...)
```

`make-args` prepares a simple vector on the executed call node and reuses it.
It still allocates a fresh evaluated-value cache for every invocation; lazy
arguments, repeat reads within one invocation, source positions and side effects
retain their semantics. One immutable pair holds both the cached vector and the identity of its source
argument list. Replacing that list rebuilds it, including on ordinary structure
copies used by SQL rewrites. The optimizer's explicit shallow-copy helper starts
with empty metadata. Private metadata is populated lazily without changing AST
structure or storing invocation-specific values on the AST.

This removes the repeated list-to-vector conversion but adds one metadata slot
to each AST node and retains a pair/vector for each executed call. It benefits reused programs; the node-construction and
compile-and-run probes expose the first-use/storage cost. Mutating a published
AST's list cells in place remains outside the existing immutable-AST contract;
optimizer rewrites replace the argument list rather than modifying it in place.

## Measurement design

Baseline is `a003a73`; its Lisp runtime is unchanged from `c5a8991`. Runtime:
SBCL 2.6.8, x86-64 Linux, Intel Xeon D-2141I. The scale driver fixes the dynamic
heap at 4 GiB, precompiles all source trees and runs them sequentially using the
same 137,100-row fixture, harness and SQL/result reference. Each full batch has
three warmups and ten samples. Separate alias-only and argument-only trees
measure the changes independently; reversed-order combined/baseline repetitions
check drift. Prepared timing excludes parsing, fixture loading and SQL planning.

Isolated S6 comparisons use fifteen samples under each GC policy: normal GC,
and full GC before each sample. Forced GC changes subsequent allocation and
collection behavior; these are separate experiments. Mandelbrot uses three
warmups and eleven measured frames in each batch, in both comparison orders.
Every measured frame is compared for exact output identity.

Focused probes report seven samples of elapsed time and SBCL bytes consed per
operation, collecting before each sample. Aliasing probes include stable names,
alternating names, changing names and changing source shapes. Argument probes
cover 2, 10 and 100 arguments; additional cases cover a complete projection,
a small reusable program and compile-and-run. Retained heap measurements use
full GC and the existing 20,000-schema/5,000-join churn workload. These are SBCL
heap readings, not process RSS.

## Final application results

The table uses the fresh final comparison in [final/](lisp-runtime/final/).
Each cell is **median / mean**, in milliseconds. Lower is faster.

| Scenario | Baseline median / mean ms | Final median / mean ms | Repeat baseline | Repeat final |
| --- | ---: | ---: | ---: | ---: |
| S1 | 489.50 / 508.50 | 483.00 / 518.00 | 489.00 / 506.40 | 485.00 / 521.00 |
| S2 | 12.00 / 12.90 | 12.00 / 11.60 | 12.00 / 11.80 | 12.00 / 12.00 |
| S3 | 232.00 / 259.40 | 218.50 / 232.10 | 228.00 / 259.40 | 222.00 / 235.00 |
| S4 | 17.00 / 16.90 | 15.00 / 15.70 | 16.50 / 16.80 | 15.00 / 15.30 |
| S5 | 463.50 / 472.50 | 413.00 / 414.50 | 460.50 / 471.90 | 412.50 / 413.60 |
| S6 | 185.00 / 185.50 | 190.50 / 217.80 | 185.50 / 185.80 | 189.50 / 217.40 |

S3 improves 3–6% by median and 9–11% by mean; S5 improves 10–11% by median
and about 12% by mean. S4 improves 9–12% by median. S2 is approximately flat.
S1's median improves about 1%, but its mean worsens 2–3%; do not describe S1
as an unconditional speedup. **Full-batch S6 regresses 2–3% by median and about
17% by mean.** That tradeoff is part of the result, not discarded as an outlier.

Mandelbrot is 79.000 → 80.000 ms, and 79.001 → 80.000 ms in the repeat:
about 1% slower at this timer's resolution. All 44 final measured frames match
the original output SHA-256,
`3d50a84d3774807aaaa132e9b6c826a89ad11548ab4e53ced220b0a00854bcba`.

### S6, GC and validation placement

| Separate experiment | Baseline median / mean ms | Final median / mean ms |
| --- | ---: | ---: |
| Isolated, normal GC, 15 samples | 185.00 / 198.60 | 190.00 / 191.20 |
| Full GC before each sample, 15 samples | 221.00 / 221.13 | 200.00 / 199.67 |
| Instrumented normal GC, context checked after every sample, 20 samples | 185.00 / 196.80 | 178.50 / 186.10 |
| Instrumented normal GC, context checked around the batch, 20 samples | 189.00 / 211.30 | 167.50 / 189.65 |

The isolated normal-GC median worsens about 3%, while its mean improves about
4%. The collect-before-sample experiment improves about 10%. These policies and
histories produce different distributions; none replaces the standard full-batch
result or proves the original review's alleged 27% regression.

The standard harness serializes the **entire 137,100-row input** outside timing
after every sample to check that it was not mutated. The diagnostic
[profile driver](../../tools/lisp-runtime/profile.py) copies that harness into a
temporary file, adds allocation and GC CPU counters, and separately tests
validation before/after the sample batch. Normal GC remains enabled in both
variants, and the context and result oracle still pass. The shared harness and
its default behavior are unchanged.

With batch validation, mean S6 allocation falls **73.63 → 63.41 MB per query
(13.9%)**, median latency falls 11.4% and mean latency falls 10.2%. Mean GC CPU
time is 25.14 → 22.27 ms. With per-sample validation, measured GC CPU time is
0.00 → 7.63 ms: the baseline's collector work has largely moved outside the timed
query. This supports validation-induced GC placement as a contributor to the
conflicting results. GC CPU time is not wall time and must not simply be
subtracted from elapsed time. The instrumented runs also have a different sample
count and allocation history, so they are diagnostics, not replacement scores.

The defensible conclusion is reduced work/allocation and improved S3/S5 query
performance, with **S6 latency dependent on workload history and validation/GC
policy**. An application dominated by S6 should measure its own query sequence.

## Focused allocation and cold-path costs

Seven samples per probe, with two comparison orders. Timings are per operation;
small differences near the clock resolution are not significance claims.

| Operation | Baseline µs | Final µs | Baseline bytes consed | Final bytes consed |
| --- | ---: | ---: | ---: | ---: |
| Stable alias | 0.325 | 0.190 | 207.9 | 144.0 |
| Alternating aliases | 0.340 | 0.255 | 215.7 | 144.0 |
| Changing alias name | 1.600 | 1.800 | 969.0 | 1072.8 |
| Changing source shape | 2.800 | 3.200 | 1819.6 | 2231.5 |
| AST node construction | 0.080 | 0.070 | 111.8 | 111.8 |
| Argument wrapper, 2 arguments | 0.100 | 0.080 | 127.8 | 95.9 |
| Argument wrapper, 10 arguments | 0.190 | 0.120 | 255.9 | 160.0 |
| Argument wrapper, 100 arguments | 0.790 | 0.410 | 1695.9 | 879.8 |
| Full 1,000-row projection | 930.010 | 930.010 | 871813.6 | 823786.2 |
| Reusable COALESCE program | 0.550 | 0.500 | 191.8 | 143.8 |
| Compile and run COALESCE | 10.000 | 10.200 | 2698.4 | 2718.0 |

Stable/alternating alias hits improve about 40%/25% in both orders and avoid
64–72 bytes per returned row. Argument wrappers allocate 25–48% less and improve
10–48% depending on arity and round. The full projection allocates about 5.5%
less but its latency is essentially flat. Reused COALESCE improves 7–9%.

Cold paths cost more. Changing source shapes takes about 14% longer and allocates
about 23% more; changing alias names allocates about 11% more, with timings flat
to 13% slower. Compile-and-run is about 2% slower. These costs argue for reuse
of compiled programs and stable schemas, not a universal allocation/speed claim.

### Retained heap

Three fresh-process repetitions, full GC before heap readings:

| Workload | Baseline retained bytes (median) | Final retained bytes (median) |
| --- | ---: | ---: |
| 20,000 changing schemas | 69,856 | 69,984 |
| Schemas plus 5,000 changing-schema joins | 384,672 | 431,664 |
| 5,000 live programs, compiled only | 10,605,088 | 10,605,152 |
| Same programs after first execution | 17,625,120 | 18,185,184 |

The nested alias maps add about **47 KB** in the churn probe, while total plans
remain bounded at 256. The extra AST slot fits the existing SBCL allocation size:
the node-construction probe remains approximately 112 bytes per node. Executed
ten-argument programs retain about **112 additional bytes per program** for the
pair and vector, or 0.56 MB / 3.2% across 5,000 programs. They do not retain
invocation value caches or contexts. The new
[program-memory probe](../../tools/lisp-runtime/program-memory.lisp) keeps every
program live during these measurements. These are implementation-specific heap
readings, not portable object-size or RSS guarantees.

## Rejected alternatives and recommendations

The exploratory ablations at the top of [the results directory](lisp-runtime/)
use an initial two-slot argument memo. The final version stores the identity and
vector as one immutable pair and has its own fresh baseline/candidate repetitions
under `final/`. Do not combine those rounds into one sample population.

A single-alias-per-shape cache was not adopted: it can repeatedly rebuild plans
when table names alternate. The chosen nested cache preserves both names while
keeping the **total** plan bound.

A flat-cache alternative gave its temporary probe key `DYNAMIC-EXTENT` and used
a separate heap key on insertion. SBCL eliminated the probe's heap allocation,
and its cold paths avoided the nested-table cost. It nevertheless trailed the
two-level prototype on S3/S5 and isolated S6 mean/controlled-GC measurements;
for example, controlled S6 mean was 210.40 versus 203.60 ms. It is preserved as
an experiment, not shipped. The patches reproduce both
[the initial two-level/vector prototype](lisp-runtime/two-level-two-slots-experiment.patch.txt)
and [the stack-key alternative](lisp-runtime/stack-key-experiment.patch.txt).
The initial two-slot memo also allocated about 128 bytes per AST node; the final
single-slot form restores the approximately 112-byte allocation size.

Keep the final representation for its repeatable allocation reductions, S3/S5
improvements and bounded ownership. For workloads with constantly changing
schemas, profile the inner-table miss cost before adding more cache machinery.
For many long-lived compiled programs, account for the retained argument vectors.
For S6, validate representative normal-GC sequences rather than tuning solely
against a median from one heap history or replacing normal GC with forced GC.

## Validation

- 532 Lisp unit checks, including lazy arguments, per-invocation value isolation,
  optimizer/SQL-style copies, vector rebuilding, alternating aliases, entry/key/character
  bounds, eviction ownership, lowercase aliases and ordinary-row fallback.
- 911 conformance cases; 852 SQL cases; bounded metadata checks.
- 39,990 decimal-oracle cases, zero mismatches.
- 4,000 differential programs and 54 API probes agree across Lisp, C++ and JS;
  zero host crashes.
- Every final six-scenario result, generated SQL and continuation flag matches
  the shared oracle; standard runs preserve context on each sample, the profiling
  batch variant verifies it before and after the batch. Mandelbrot outputs match.
- The full five-language repository gate was not rerun.

[Raw samples and validation](lisp-runtime/final/) and
[reproduction commands](../../tools/lisp-runtime/README.md).

