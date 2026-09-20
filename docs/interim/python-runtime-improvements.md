# Python native arithmetic follow-up — 2026-09-20

The original Python finding recommended preserving the native arbitrary-precision
integer representation, simple alignment/dispatch, native valid-input UTF-8
operations and bounded metadata. Those paths remain in place. This change removes
two additional pieces of redundant arithmetic work, without a second integer
representation or a new cache policy.

Subtraction aligns magnitudes and handles signs directly. Previously it allocated
a temporary immutable `Dec` for the negated right operand, then called addition.
The direct path retains the maximum input scale, normalizes zero's sign and checks
range when magnitudes add. Same-sign subtraction cannot grow the magnitude beyond
its aligned inputs.

Division cancels the common scale before multiplying:

```python
N, D = a.digits, b.digits
if b.scale > a.scale:
    N *= _pow10(b.scale - a.scale)
elif a.scale > b.scale:
    D *= _pow10(a.scale - b.scale)
```

The ratio is exactly the same as multiplying by both original scale powers.
The existing divide-by-zero check, ten-place rounding, trailing-zero removal and
range checks remain unchanged. Equal scales need no power lookup, even when both
are one million. This avoids constructing large temporary integers merely to
cancel them in the subsequent division. It does not use floating point.

## Method

Baseline is `6815faa`; only `python/sel/decimal.py` differs in the runtime.
The sequential order is baseline, candidate, candidate repeat, baseline repeat.
Both trees use the identical scale harness and 137,100-row dataset, fixed
`PYTHONHASHSEED=0`, normal GC, three warmups and seven measured samples per
scenario. Prepared timings exclude parsing and context creation. Input integrity
checks remain outside every sample. SQL, continuation plans and returned rows
must match the shared reference. Mandelbrot uses eleven measured frames per
batch with three warmups, checking every frame against the historical hash.

Focused arithmetic/UTF-8 timings use the existing microbenchmark; an additional
probe tests shared scales 0, 2, 1,000 and 10,000. Churn probes separately measure
latency, cache misses/clear events, and traced retained/peak memory. Instrumented
passes are not latency samples. Raw results and reproduction commands accompany
this report. Source, fixture and harness hashes are recorded in
[metadata.json](python-runtime/metadata.json).

## Bounded metadata under heterogeneous input

The new diagnostic starts each activity-counting pass with empty caches:

| Workload | Calls | Misses | Clear events | Final entries |
| --- | ---: | ---: | ---: | ---: |
| Three stable powers | 300 | 3 | 0 | 3 |
| Cycle of 65 powers | 650 | 650 | 10 | 10 |
| Cycle of eight powers near exponent 160,000 | 24 | 24 | 3 | 6 |
| Hot power interleaved with 300 unique powers | 600 | 305 | 4 | 49 |
| Three stable shapes | 300 | 3 | 0 | 3 |
| Cycle of 257 shapes | 2,570 | 2,570 | 10 | 10 |
| Hot shape interleaved with 1,000 unique shapes | 2,000 | 1,004 | 3 | 236 |

These cases expose the cost the original stable workload could not: both cycles
just beyond the entry budgets miss on every call. Eight large powers also miss
on every call because their combined weight exceeds the budget. Interleaving
hot and cold keys occasionally evicts the hot key when the table clears. Bounds
hold after each operation; exact power values and field lookups remain correct.
This is a deliberate retention/throughput tradeoff, not evidence that these
workloads are free of churn. The policy is unchanged by this patch.

Keep these probes alongside the application suite. Do not remove bounds to win
the cyclic benchmark, or add per-hit LRU bookkeeping without measuring its cost
on stable workloads. For applications using widely varying decimal scales,
cancel shared scales first: it removes work without increasing cache retention.


All four passes have identical activity counts and traced retained bytes. Stable
powers retain 5,736 bytes; the 65-power cycle ends at 1,308 bytes; the weighted
cycle ends at 426,252 bytes. Stable shapes retain 1,560 bytes; the 257-shape cycle
ends at 4,960 bytes; hot/cold shapes end at 119,592 bytes. These are end-of-workload
traced allocations, not process RSS or universal peak limits. Raw JSON also
records peaks. The first baseline/candidate weighted-cycle timings are
14.34/14.38 ms per lookup: recomputing large powers is expensive even though
retention is bounded. The cycle's miss cost is unchanged by this patch.

## Application results

All times are **median / mean milliseconds**; lower is faster.

| Workload | Baseline | Candidate | Baseline repeat | Candidate repeat |
| --- | ---: | ---: | ---: | ---: |
| scenario1 | 2704.21 / 2750.26 | 2593.42 / 2671.48 | 2628.65 / 2705.35 | 2583.63 / 2666.19 |
| scenario2 | 38.92 / 38.93 | 38.21 / 38.80 | 38.79 / 39.11 | 39.00 / 38.77 |
| scenario3 | 998.34 / 1231.72 | 999.12 / 1226.99 | 1013.31 / 1229.03 | 1019.89 / 1233.61 |
| scenario4 | 64.56 / 64.86 | 66.51 / 65.63 | 63.56 / 63.86 | 65.98 / 66.47 |
| scenario5 | 502.91 / 507.70 | 499.19 / 503.30 | 498.37 / 498.70 | 496.09 / 498.99 |
| scenario6 | 723.74 / 958.57 | 732.79 / 963.07 | 715.92 / 952.57 | 723.94 / 960.50 |
| mandelbrot | 357.28 / 361.04 | 350.32 / 350.79 | 360.47 / 360.42 | 351.91 / 352.53 |

S1 medians improve 1.7–4.1%, and Mandelbrot medians improve 1.9–2.4%.
S2/S3 are approximately flat; S5 improves less than 1% by median.
**S4 medians worsen 3.0–3.8%, and S6 medians worsen 1.1–1.3%.**
These small slowdowns remain part of the full-batch result. S3/S6 means show
substantial GC/history effects; medians alone do not describe their latency.
All 168 full-batch scenario samples pass, and all 44 Mandelbrot frames match.


### S4/S6 follow-up

The untimed activity probe counts zero calls to `sub` or `div` during prepared
S4/S6 execution. Both queries use unchanged runtime paths; this rules out extra
arithmetic work in those functions as the direct explanation, but not indirect
heap/layout effects or ordinary measurement variation.

With only S4 and S6 selected, fifteen samples and three warmups:

| Scenario | Baseline median / mean ms | Candidate median / mean ms |
| --- | ---: | ---: |
| S4 | 63.89 / 64.01 | 62.98 / 63.76 |
| S6 | 718.20 / 1038.40 | 728.08 / 1044.30 |

S4 reverses direction (median about 1.4% faster), so its full-batch slowdown is
not consistent across workload histories. S6 remains about 1.4% slower by median
and 0.6% by mean. Keep that small unresolved cost visible; the probe does not
prove its cause. These are separate experiments, not samples to pool with the
full batches or use to erase their results. All 60 targeted samples pass.

## Focused arithmetic results

Median microseconds per operation, first comparison (the reverse-order repeat
confirms the direction). These do not imply equal whole-query speedups.

| Operation | Baseline µs | Candidate µs |
| --- | ---: | ---: |
| decimal.small.sub | 1.569 | 0.892 |
| decimal.mixed_sign.sub | 1.664 | 0.960 |
| decimal.unequal_scale.sub | 1.699 | 1.023 |
| decimal.large.sub | 1.906 | 1.151 |
| decimal.small.div | 1.427 | 1.221 |
| decimal.unequal_scale.div | 1.390 | 1.332 |
| div.shared_scale_1000 | 4.244 | 2.244 |
| div.shared_scale_10000 | 16.560 | 2.350 |

Across the six existing operand pairs and both orders, subtraction improves
about 38–44%; equal-scale small division improves 14–16%. Mixed-scale division
improves about 3–8%. At shared scale 10,000, division improves about 86%.
Subtraction removes one temporary `Dec` construction per operation; division
avoids redundant large integer temporaries. No change to permanent metadata
ownership is involved. UTF-8 controls show no consistent directional change.

Three instrumented Mandelbrot frames make **66,405 → 56,805 power calls**
(14.5% fewer), with 57 misses, 57 retained large powers and zero clear events
in both versions. The activity probe is separate from timed runs.

## Correctness and scope

- 624 Python unit tests pass, including 39 new independent-decimal and
  shared-scale regression cases. They cover sign combinations, scaled zeros,
  retained subtraction scale, half-away division rounding, and avoiding large
  equal-scale powers. The independent stdlib `decimal` oracle is not used by
  the production runtime.
- 911 conformance cases, 852 SQL cases and bounded-metadata checks pass.
- 39,990 decimal-oracle cases have zero mismatches.
- 54 API probes agree across Python, C++ and JavaScript.
- The 4,000-program differential run reports **11 pre-existing join-output
  disagreements**, 1,511 agreed SEL errors and zero host crashes. Python baseline
  and candidate outputs match byte-for-byte over the complete corpus. This is
  not a clean cross-language fuzz pass; see the [disagreement log](python-runtime/fuzz-disagreements.txt)
  and [baseline comparison](python-runtime/fuzz-baseline-comparison.json).
- No database execution or full five-language repository gate was run.

Retain the native-int and native UTF-8 paths. Keep the arithmetic simplifications
for their exactness, repeatable focused gains and modest S1/Mandelbrot gains.
Keep the cache churn diagnostics with the application suite; bounded retention
alone does not establish acceptable throughput for heterogeneous inputs.

[Raw measurements](python-runtime/) · [Reproduction commands](../../tools/python-runtime/README.md).
