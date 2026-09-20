# PHP explicit scalar access — 2026-09-20

The original PHP finding did not establish a consistently slower standalone S6:
changing preceding workloads reversed the apparent historical regression.
This follow-up addresses the concrete magic-dispatch cost, without treating it
as the explanation for every full-batch difference.

Four internal property reads in `Builtins/Structure.php` now call the existing
`Value::getScalar()` directly: numeric join-key fallback, literal join keys and
the two operands of boolean sorting. Previously external-looking property reads
invoked `Value::__get('scalar')`, which then called that same getter.

```php
// Preserve lazy formatting and the scalar cache; avoid the magic dispatcher.
$scalar = $v->getScalar();
```

The backing scalar stays private. The external `$value->scalar` API, `isset`,
lazy decimal formatting and decimal-cache invalidation on public scalar writes
are unchanged. No changes were made to decimal representation, checked native
integer arithmetic, optional GMP, the packed-limb fallback, or cache ownership.
Read sites elsewhere were not changed without profiling evidence.

## Method and configuration

Baseline is `6815faa`; its PHP source is unchanged from `c5a8991`. Only
`php/src/Builtins/Structure.php` differs in the runtime. The Python improvements
already in the working tree do not participate in this comparison.

PHP 8.5.10, 64-bit Linux. Both configurations start with `php -n`, load ctype,
enable CLI OPcache, set a 128M JIT buffer and use tracing JIT setting 1255.
The GMP configuration additionally loads GMP. This build includes OPcache under
`-n`; active JIT is asserted, not inferred from the command-line flags. Exact
extension lists, ini values, commands and JIT status are saved in raw results.
This avoids attributing a comparison between different default ini files solely
to GMP. Results still depend on this machine and deployment configuration.

All six scenarios use the same 137,100-row fixture and SQL/result reference,
normal GC, three warmups and seven samples. Parsing/context creation are outside
prepared timing; the normal per-sample input-integrity checks remain outside the
timer. GMP batch order is baseline, candidate, candidate repeat, baseline repeat.
The fallback comparison is baseline then candidate. Every full batch includes
Mandelbrot with three warmups and eleven measured frames, each checked against
the historical SHA-256.

A separate GMP S4/S6 comparison uses three warmups and fifteen samples without
preceding S1/S2/S3/S5 workloads. It is a different heap/JIT history, not a reason
to discard mixed-workload results. Focused getter/join/sort probes use seven
samples; setup, warming and correctness checks are outside timing.

## GMP application results

Times are **median / mean milliseconds**, lower is faster.

| Workload | Baseline | Candidate | Baseline repeat | Candidate repeat |
| --- | ---: | ---: | ---: | ---: |
| scenario1 | 2982.67 / 2839.04 | 2928.05 / 2803.01 | 2971.44 / 2836.86 | 2980.89 / 2856.01 |
| scenario2 | 26.49 / 26.39 | 26.44 / 26.15 | 27.02 / 26.70 | 26.18 / 26.22 |
| scenario3 | 902.11 / 902.91 | 881.18 / 883.53 | 888.38 / 891.95 | 896.70 / 897.64 |
| scenario4 | 59.19 / 58.84 | 57.63 / 56.93 | 57.80 / 57.60 | 58.69 / 58.01 |
| scenario5 | 2003.10 / 1973.14 | 1983.44 / 1961.72 | 2026.57 / 1980.63 | 2025.63 / 1989.88 |
| scenario6 | 1095.92 / 1040.73 | 1092.41 / 1037.39 | 1090.20 / 1040.89 | 1099.02 / 1052.34 |
| mandelbrot | 390.57 / 392.10 | 388.97 / 391.69 | 396.70 / 397.80 | 388.24 / 389.00 |

The first comparison has small improvements throughout; reversed order does
not reproduce them uniformly. S1 ranges from 1.8% faster to 0.3% slower by
median; S3 from 2.3% faster to 0.9% slower; S4 from 2.6% faster to 1.5% slower.
S5 is approximately flat to 1% faster. **S6 is 0.3% faster in the first pair,
0.8% slower in the repeat** (mean 0.3% faster / 1.1% slower). S2 improves
about 0.2–3.1% by median. Do not turn these small mixed results into a universal
application speedup or a confirmed regression fix.

Mandelbrot is 0.4–2.1% faster in these GMP pairs. Arithmetic was not changed;
this is a configuration/output control, not evidence of a new arithmetic
optimization. All full-batch results and SQL/continuation plans pass.

### Separate S4/S6 experiment

| Workload | Baseline median / mean ms | Candidate median / mean ms |
| --- | ---: | ---: |
| S4 | 43.00 / 43.16 | 42.51 / 42.82 |
| S6 | 887.34 / 885.55 | 886.32 / 881.33 |

The S4 median improves 1.1%; S6 improves only 0.1% by median and 0.5% by mean.
The roughly 2% isolated S6 gain from the original exploratory patch does not
repeat here. More strikingly, selecting only these scenarios changes baseline
S6 from roughly 1,090–1,096 ms to 887 ms, and S4 from roughly 58–59 ms to
43 ms. These differences reinforce workload-history sensitivity. They do not
prove whether heap state, JIT traces, GC or their interaction causes it, nor do
they invalidate the fixed-order mixed-query benchmark.

## Focused probes

Median microseconds for 500-row joins/sorts; setup and validation are untimed.

| Operation | Baseline | Candidate | Baseline repeat | Candidate repeat |
| --- | ---: | ---: | ---: | ---: |
| join.numeric_text | 1815.49 | 1564.04 | 1634.04 | 1567.21 |
| join.numeric_cached | 1405.62 | 1367.02 | 1355.76 | 1355.58 |
| join.literal_lazy | 1309.27 | 1224.84 | 1289.04 | 1225.23 |
| sort.booleans | 1973.72 | 1935.72 | 2007.33 | 1968.51 |

Numeric-text joins improve 4–14%; literal joins improve 5–6%; boolean sorting
improves about 2% in both orders. Already-cached numeric keys bypass the changed
scalar reads, and their repeat is flat; do not attribute their first small shift
to fewer getter calls. Individual samples remain available to expose warm/JIT
and GC variation rather than selecting only the fastest observation.

Within each tree, a cached explicit getter costs about 31 ns versus 95–97 ns
for the magic property path. Both APIs retain the same costs in both source
trees; the change is which spelling the internal hot paths use. A threefold
difference in a tiny read probe is not a threefold query speedup.

## Without GMP

The same `-n` configuration and active OPcache/JIT are used, with only GMP
omitted. This is one baseline/candidate pair, not a reversed-order repeat.

| Workload | Baseline median / mean ms | Candidate median / mean ms |
| --- | ---: | ---: |
| scenario1 | 2945.03 / 2823.58 | 2932.00 / 2819.35 |
| scenario2 | 26.83 / 26.83 | 26.60 / 26.26 |
| scenario3 | 883.42 / 887.30 | 886.75 / 889.39 |
| scenario4 | 58.10 / 57.77 | 56.93 / 57.01 |
| scenario5 | 2006.41 / 1968.39 | 2006.14 / 1971.88 |
| scenario6 | 1101.66 / 1042.56 | 1090.76 / 1037.04 |
| mandelbrot | 1362.68 / 1365.44 | 1361.23 / 1360.46 |

Most application shifts are within about 1%; S4 improves about 2%. S3 is about
0.4% slower by median, and S5 is flat by median with a 0.2% slower mean.
Mandelbrot is effectively unchanged at 1.36 seconds. Focused numeric-text and
literal joins improve about 7.4% and 6.1%; boolean sorting improves only 0.5%.
The native/cached numeric probe shifts about 1.9%, again without exercising the
changed scalar-key read. These are local measurements, not proof of a universal
fallback speedup.

GMP Mandelbrot medians are approximately 0.39 seconds versus 1.36 seconds for
the packed-limb fallback. Both configurations render identical frames. Keep
both paths: checked native integers serve bounded mantissas, GMP accelerates
large magnitudes where installed, and the portable fallback remains usable.
No arithmetic path was rewritten by this change.

## Additional correctness finding: cold numeric join keys

A new edge-case check exposed an existing normalization inconsistency:
`Value::num("1.00")` and `Value::text("1.00")` compare equal numerically, but a
fresh equi-join over those keys returns zero rows. After decimal parsing warms
the cache, the same join returns one row. The untouched baseline and candidate
both reproduce the same behavior; this is not introduced by explicit getters.

The cached-decimal branch strips trailing scale zeros before constructing its
key. The uncached-text fallback parses the decimal but returns `Dec::format`
with the retained scale. Those branches can produce different hash keys for
equal numbers. Subsequent calls use the now-populated decimal cache.

**Recommendation:** address this separately by sharing decimal-key
normalization between cached and newly parsed values, while retaining the
integer-text shortcut. For example, after that shortcut, use the same helper
as the cached-decimal branch (proposed helper, not part of this patch):

```php
return self::canonicalDecimalKey($v->asDecimal());
```

Add cold/warm, mixed-representation and repeated-run cases before measuring
that change. [Reproducer](../../tools/php-runtime/scaled-key-control.php) and
[baseline/candidate evidence](php-runtime/scaled-key-control.json) are preserved.
The general fuzz suite did not include this case; a passing fuzz run does not
prove the existing join implementation is free of such defects.

## Validation and recommendation

- 43 PHP layout/API checks, 21 runtime checks and 135 optimizer checks pass
  both with the default GMP installation and with `php -n -d extension=ctype`.
  New checks preserve lazy numeric scale, public writes and cache invalidation,
  private backing storage, reused numeric/literal joins and boolean sorting.
- 911 conformance cases pass with and without GMP; 852 SQL cases pass with GMP
  (564 mirrored dialect checks and one leaf-pair check).
- Bounded metadata checks pass with and without GMP.
- 39,990 independent decimal-oracle cases pass in each arithmetic configuration.
- 4,000 differential programs agree across PHP/C++/JS, including 1,511 agreed
  SEL errors; zero disagreements or host crashes. All 54 API probes agree.
- All 312 timed scenario samples pass the row/input/SQL checks; all 66 timed
  Mandelbrot frames match the historical hash.
- No database execution or full five-language repository gate was run.

Keep the explicit internal getters for the measured focused improvements and
clear access semantics. Preserve the compatible external property API and both
arithmetic implementations. For S6-heavy applications, benchmark the actual
query sequence and context lifetime; this change is not a demonstrated fix for
the original full-batch delta. Follow up on the cold-key normalization defect
independently rather than hiding it behind warm benchmark execution.

[Raw measurements and validation](php-runtime/) · [Reproduction commands](../../tools/php-runtime/README.md).
