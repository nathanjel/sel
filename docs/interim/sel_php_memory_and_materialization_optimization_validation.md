# SEL PHP memory and materialization optimization validation

Date: 2026-09-13

This records the implementation and measurements for
`sel_php_memory_and_materialization_optimization_plan.md`. The PHP lane keeps
the exact decimal representation and SEL copy-by-value semantics; the changes
target row metadata, conversion, join construction, and benchmark boundaries.

## Implemented

- `Value::toNative()` now traverses packed list storage, shaped record slots,
  irregular children, lazy values, and scalar-with-children values directly.
  The compatibility `keys()`, `values()`, and `entries()` APIs remain intact.
- `Value::fromNativeRows()` validates the first ordered record shape, reuses it
  for homogeneous rows, and falls back to the ordinary recursive converter on
  a schema change or non-record row.
- The scale fixture loader uses the prepared-row path and reuses the augmented
  customer shape for `dist_berlin`.
- Join fallback/null-row construction now uses parallel key/value arrays, and
  the compiled projector uses sequential packed appends. The existing shape
  identity guards and semantic fallback remain in place.
- `RecordShape` has opt-in counters for intern calls/hits, newly created
  shapes, signature time, alias calls/hits/builds, and cache cardinality.
  Instrumentation is disabled during timed evaluator runs.
- The PHP benchmark reports monotonic evaluator/materializer/end-to-end phases,
  context preparation, shared versus fresh-clone context mode, verified JIT
  status, allocator/RSS snapshots, shape-cache counters, and the scenario
  query. `benchmark_all.py` validates the PHP-specific report fields while
  preserving the old aggregate compatibility phases.
- `tools/scale-test/benchmark_php_memory.php` provides repeatable PHP-only
  ingestion, materialization, projector-build, shape-cache, and memory probes.

## Focused correctness gates

All passed after the changes:

```text
php php/bin/conformance             801 passed
php php/bin/sqlt                    528 passed
php php/tests/a5.php                32 passed
php tools/check-php-optimizer.php   24 passed
```

The A5 checks cover shaped and packed direct materialization, fallback records,
lazy values, scalar-with-children values, homogeneous and heterogeneous
ingestion, numeric-looking and embedded-NUL keys, insertion order, copy
isolation, inner no-match joins, empty-right `LINK_LEFT`, heterogeneous join
fallback, alias-case joins, duplicate columns, and decimal overflow boundaries.

## Microbenchmark evidence

Using the 90,000-row `order_items` table from `dataset-10x.json`, three measured
runs and one warmup produced:

| probe | result |
|---|---:|
| prepared ingestion, one pass | 216.94 ms |
| generic ingestion, one pass | 347.86 ms |
| legacy `values()`/`entries()` materialization | 93.57 ms mean |
| direct `toNative()` materialization | 51.45 ms mean |
| `array_fill()` projector destination | 9.19 ms mean |
| sequential append projector destination | 8.87 ms mean |

Both ingestion and materialization outputs were checked for structural/native
parity. The reusable probe reports PHP version, INI/JIT configuration,
allocator usage, current RSS, and peak RSS.
The same probe counted one `RecordShape::intern()` call for prepared ingestion
versus 90,000 calls for generic ingestion on this homogeneous table.

The probe also screens the current associative decimal against packed-tuple and
declared-property object layouts over parsing, comparison, addition,
multiplication, division, formatting, and copying, including zero, signed-zero,
64-bit-boundary, large-integer, and scaled values. Candidate arithmetic
delegates to the exact existing `Dec` core after unpacking, so this is a layout
screen rather than evidence for a new arithmetic core; no production decimal
replacement was selected without a fair native candidate implementation.

## Full PHP scale/parity run

Command boundary: JIT explicitly enabled, 137,100 source rows, six scenarios,
two warmups, ten measured samples, shared prepared context.

The current-source run passed all six in-memory result checks and both
PostgreSQL/MariaDB SQL-plan checks. Values are mean / median milliseconds:

| scenario | evaluator | materialization | end-to-end |
|---|---:|---:|---:|
| 1 | 2820.78 / 2875.57 | 0.04 / 0.01 | 2820.83 / 2875.58 |
| 2 | 21.24 / 21.23 | 0.14 / 0.07 | 21.38 / 21.42 |
| 3 | 1265.98 / 1172.80 | 0.01 / 0.01 | 1265.99 / 1172.82 |
| 4 | 57.03 / 57.03 | 0.07 / 0.07 | 57.10 / 57.10 |
| 5 | 9155.57 / 9390.91 | 0.95 / 0.96 | 9156.52 / 9391.99 |
| 6 | 678.88 / 665.30 | 0.01 / 0.01 | 678.89 / 665.31 |

The report showed JIT `enabled=true`, `on=true`, kind 5, and the expected
137,100 shaped fixture rows plus one fallback root record. A fresh-clone
scenario-2 run also passed and measured context preparation separately at
261.64 ms, with the shared input context unchanged.

The benchmark report contract, including the new PHP phase, shape, JIT, and
memory gates, passed through `benchmark_all.validate_report`.

## Cross-host validation

- 4,000 generated evaluator programs across JS, PHP, C++, Lisp, and Python:
  **0 disagreements, 0 host crashes**.
- 2,000 SQL-fuzz programs across MariaDB, MySQL, PostgreSQL, and SQLite, with
  JS/PHP/Lisp/Python translation comparison: **0 disagreements** for every
  dialect. Database execution was skipped because no DSNs were configured.
- Decimal oracle: **199,926 cases, 0 mismatches** for JS, PHP, C++, Lisp, and
  Python.
- PHP JIT-off and verified JIT-on runs both passed all six scenarios. On this
  build, JIT-on median evaluator time was between 1.09x and 1.43x faster than
  JIT-off across the six scenarios; this is runtime evidence, not a semantic
  dependency.

No SQL implementation change was required for this memory/materialization
pass; generated SQL and hybrid flags remained covered by the exact Lisp
reference checks and SQL differential fuzzing.
