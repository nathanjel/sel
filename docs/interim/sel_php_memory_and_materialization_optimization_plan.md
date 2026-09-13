# SEL PHP Memory and Materialization Optimization Plan

## Scope and evidence

This note covers PHP-specific representation, construction, copying, and result
materialization costs. It was checked against the current implementations in:

- `php/src/Value.php`;
- `php/src/Dec.php`;
- `php/src/Builtins/Structure.php`;
- `tools/scale-test/sel_benchmarks.php`.

The PHP port already implements the important Phase A5 foundations: interned
record shapes, packed row storage, direct slot access, an exact decimal
digit-string representation with checked native-integer arithmetic fast paths,
per-join projector plans, and copy-on-write-aware alias construction. The work
below is incremental. It must preserve exact decimal behavior, key order,
duplicate-key behavior, lazy values, and SEL's copy-by-value semantics.

No fixed byte count is assigned to a zval, bucket, array, or object here. Those
sizes vary with PHP version, build options, allocator, architecture, and whether
an array is packed. Memory claims must be established on the PHP build used for
the benchmark.

## Current representation

Zend implements a PHP `array` with its `HashTable` machinery. A list with dense,
zero-based integer keys can remain in the cheaper packed mode; a string-keyed or
sparse array uses hash buckets. Packed mode removes per-element key hashing and
key storage, but its elements are still dynamically typed zvals and the array
still has allocation, reference-counting, and copy-on-write behavior.

A normal shaped SEL row is approximately:

```text
Value object
  -> shared RecordShape
       -> packed keys array
       -> string-keyed keyMap
       -> per-shape aliasCache
  -> packed storage array
       -> zval holding a child Value object handle
```

This is materially better than repeating string keys in every row. It is not an
unboxed struct or a C-style vector: each row still owns a PHP array and each
field still leads to a `Value` object. Keep ordinary rows and lists on the
packed `$storage` path. `$children` is the compatibility/fallback representation
for records that cannot retain a shape, including duplicate-key constructions
and shape-breaking mutation.

`RecordShape::$keys` and `Value::$storage` are packed when their input arrays are
lists. `RecordShape::$keyMap`, `RecordShape::$aliasCache`, fallback `$children`,
join bucket maps, and execution frames are associative arrays. Avoid describing
all of these as having the same cost.

## Verified findings

### 1. The PHP benchmark combines evaluation and native result conversion

The current timed expression is:

```php
$actualRows = benchmark_value($program->run($context));
```

Therefore `elapsed_ms` includes both SEL evaluation and recursive conversion of
the result into native PHP arrays/scalars. `benchmark_value()` calls `values()`
for lists and `entries()` for records, so the measured conversion creates
additional PHP arrays. Compilation, SQL planning, canonicalization, and parity
comparison are outside the timer.

The current cross-lane boundary is inconsistent: JavaScript and Python also
include their native conversion, while Lisp and C++ stop their principal timer
before comparable conversion. C++ additionally clones its scenario context
inside its timer. Existing numbers are useful as lane-specific end-to-end
figures, but they do not isolate evaluator performance and must not be presented
as an evaluator-only comparison.

The PHP runner reuses one loaded context across scenarios and does not clone it
inside the timer. Any corrected benchmark must specify the same context policy
for every lane and separately validate that repeated evaluation does not mutate
the shared input.

### 2. Accessor APIs materialize avoidable temporary arrays

The current methods have distinct costs:

- `keys()` returns the shared shape keys directly for shaped records, but for a
  list it allocates `array_keys()` output and then an `array_map()` result;
- `values()` always returns a newly mapped array and forces every element, even
  when `$storage` is already packed;
- `entries()` allocates an outer result and one two-element PHP array per field;
- `benchmark_value()` uses `values()` or `entries()`, so it pays these costs for
  every non-scalar result node.

`Structure::forEachElement()` already avoids pair-array materialization for
shaped records and packed lists by traversing shape keys/storage directly. Its
fallback still uses `entries()`, and its callback introduces closure dispatch.
This establishes the correct representation-aware traversal pattern, but not
necessarily the fastest API design for every hot loop.

### 3. Record construction has avoidable repeated work

`Value::fromNative()` converts an associative host array into packed `$keys` and
`$values`, then calls `Value::record()`. `record()` builds a temporary `$seen`
hash to detect duplicates; for unique records it calls `shaped()`, which calls
`RecordShape::intern()`. `intern()` normalizes the key array if necessary and
computes `serialize($keys)` before every cache lookup.

The shape and its key map are shared after a cache hit, but duplicate detection
and signature generation still occur for every imported row. This is likely
important for large homogeneous JSON/database relations and should be measured
separately from evaluator execution.

`fromEntries()` has a different cost: it starts with already materialized pair
arrays and then creates parallel key/value arrays before calling `record()`.
Common compiler-generated `RECORD` paths already use parallel arrays, but
`LAZY_RECORD`, alias fallback, null-record construction, and generic joined-row
fallbacks still use entry pairs.

### 4. Shape caches save row metadata but are process-lifetime caches

`RecordShape::$cache` interns by serialized ordered key sequence. `aliasCache`
stores an aliased shape descriptor per shape/table-name pair. Both are effective
for repetitive schemas, and the alias cache prevents rebuilding an identical
alias shape for every homogeneous row.

Both caches are unbounded for the lifetime of the PHP process. This is normally
benign in the finite CLI benchmark, but long-lived workers receiving adversarial
or highly variable schemas/table aliases need cardinality measurements before
the cache is treated as free. Do not add eviction without proving that it helps:
eviction can increase construction work and complicate shape-identity fast paths.

### 5. Decimal arrays are associative, but their sharing matters

The exact decimal representation is currently:

```text
['neg' => bool, 'digits' => string, 'scale' => int]
```

`Value::$decVal` caches that parsed representation. `Dec` already attempts
native-integer mantissa arithmetic for aligned addition/comparison and
multiplication; it checks that PHP did not promote an overflowing operation to
`float`, then falls back to exact digit-string arithmetic. The plan must not
describe native-integer arithmetic as missing.

The three-field decimal array is associative and arithmetic creates many such
arrays, so it is a legitimate profiling target. However, assigning `$decVal`
during `Value::copyAt()` uses PHP array copy-on-write: it does not immediately
duplicate the decimal array. The current `Dec` functions treat input decimals as
values and return new arrays, so cached representations can remain shared until
released. Measure live allocations and arithmetic throughput before selecting a
replacement.

### 6. Copy-on-write does not provide SEL object isolation

Assigning a PHP array shares its backing storage until one copy is mutated.
Assigning a `Value` object shares an object handle; it does not clone the object.
Consequently, PHP COW cannot replace recursive `Value::copy()` where SEL requires
an independent object graph.

`Value::copyAt()` correctly deep-copies shaped storage, list storage, and
fallback children while reusing the immutable `RecordShape`. Its `$decVal`
assignment is COW sharing of a value-like cache. Replacing recursive child copies
with object-handle copies would change observable mutation semantics.

For shaped alias construction, `ensureRowTableAlias()` assigns the existing
packed storage to a local and appends alias values. The append triggers the one
required COW separation, avoiding an eager `array_slice()` followed by another
mutation. The child `Value` handles intentionally remain shared in the joined
view. This path should only be changed with mutation-isolation tests.

### 7. The join projector is per evaluation, with guarded fallbacks

`makeJoinProjector()` compiles matched and `LINK_LEFT`-miss plans once for a
single `LINK`/`LINK_LEFT` evaluation. It is not a process-wide projector cache.
For rows whose left/right shape identities match the sampled shapes, its build
closure reads direct integer slots and returns `Value::fromShape()` with packed
storage. Heterogeneous rows, missing plans, and mismatched shapes fall back to
`makeJoinedRow()`.

The current builder uses `array_fill(..., null)` and then overwrites every slot
through integer action codes. This guarantees a packed destination of the right
length, but it also writes placeholder zvals. PHP offers no direct userland
equivalent of `vector::reserve()`: benchmark `array_fill()` plus indexed writes
against ordered `$storage[] = ...` appends before deciding which is faster.

The hot path still pays closure invocation, a `match` per output slot, creation
of a destination array and `Value`, and object-handle/refcount operations. These
are optimization candidates, not evidence that the projector is ineffective.
The fallback and unmatched-left semantics must remain intact.

### 8. JIT can reduce dispatch, not erase the representation

The aggregate benchmark wrapper requests CLI OPcache and JIT with
`opcache.enable_cli=1`, a non-zero JIT buffer, and `opcache.jit=1255`. The PHP
runner itself neither enables nor verifies JIT when invoked directly.

JIT may improve repeated branches, arithmetic, and call dispatch, but it cannot
be assumed to unbox arbitrary `Value` graphs or remove PHP array/refcount/COW
semantics. Each benchmark report must include PHP version, relevant INI values,
and runtime JIT status from `opcache_get_status(false)` when available. A
configured JIT mode is not sufficient evidence that useful code was compiled;
warm-up and repeated same-process runs must be reported.

## Recommended work

### P0: correct and label benchmark phases across all lanes

Report at least these non-overlapping phases for every in-memory lane:

1. input/context construction, outside scenario timing;
2. scenario preparation required by semantics, including any context clone;
3. evaluator-only execution;
4. native result materialization, with canonicalization separately reported or
   included only when every lane uses the same boundary;
5. evaluator plus materialization end-to-end time.

Use equivalent boundaries in Lisp, C++, JavaScript, PHP, and Python. Keep
compilation and SQL planning out of evaluator timing, unless separately reported.
Do not hide preparation in one lane's evaluator phase. Run parity after the
timed phases, and consume/check the materialized result so no host can optimize
away work.

For PHP, split the current expression into `$result = $program->run($context)`
and `$actualRows = benchmark_value($result)` with separate monotonic timestamps.
Prefer `hrtime(true)` for nanosecond monotonic intervals; `microtime(true)` is
acceptable only if used consistently and its resolution is recorded. Preserve a
clearly labelled end-to-end figure for application realism.

Benchmark context reuse and fresh-context/clone modes separately if mutation
isolation requires both. Before reusing one context, compare a structural hash or
canonical snapshot before and after each scenario outside the timer.

### P1: eliminate pair-array materialization from native conversion

Add a representation-aware internal conversion path that traverses:

- packed lists directly through `$storage`;
- shaped records through `$shape->keys[$i]` and `$storage[$i]`;
- irregular records directly through `$children`;
- scalar-with-children values with the same `_` handling as today.

Do not implement this as a generator or callback without measurement; either can
replace pair arrays with call/yield overhead. A dedicated recursive conversion
method can be simpler and faster. Keep `keys()`, `values()`, and `entries()` as
compatibility APIs unless all callers and contracts are audited.

After this change, report materialization time independently. It is valid for
end-to-end time to improve while evaluator-only time stays unchanged.

### P1: reuse prepared shapes during homogeneous ingestion

Introduce an internal trusted path for sources that guarantee unique ordered
keys:

```text
validate first row -> intern RecordShape once
subsequent rows with the same ordered keys -> convert values -> fromShape
schema change or duplicate/invalid key -> correctness-preserving generic path
```

Good candidates are the benchmark JSON loader and database adapters with stable
column metadata. Do not expose an unchecked public constructor to arbitrary host
arrays. Preserve string conversion of native keys and PHP's key-normalization
behavior.

This removes the per-row `$seen` hash and repeated serialized signature from the
homogeneous path. It does not remove per-row packed storage or child `Value`
objects. Measure ingestion time, peak memory, number of intern calls, and number
of distinct shapes.

### P1: reduce `fromEntries()` use where callers already know keys and values

Change hot internal producers to accumulate parallel packed key/value arrays and
call `record()` or `fromShape()` directly when uniqueness is guaranteed. Review
in this order:

1. homogeneous join fallback/result builders;
2. null-right record construction;
3. alias fallback;
4. `LAZY_RECORD` and other generic APIs.

Do not convert a genuine pair-array API merely to recreate pairs later. Keep the
duplicate-key fallback and insertion order exactly as currently observed.

### P1: measure and specialize the join build loop

Benchmark the current `array_fill()`/indexed-write builder against sequential
append construction for representative row widths and match counts. Then profile:

- closure calls in `$each`, bucket construction, and projection;
- the per-slot `match` dispatch;
- alias lookup and `ensureRowTableAlias()` calls;
- homogeneous fast-path hit rate and heterogeneous fallback rate;
- matched rows versus `LINK_LEFT` misses.

If action dispatch dominates, compile specialized action groups or straight
loops per plan, while retaining source order and output shape. If projector setup
is material for repeated evaluations, consider a bounded cache keyed by source
shape identities plus binders/projection specification; first prove the cache
key is complete and that long-lived-worker cardinality is controlled.

### P2: benchmark decimal representation alternatives

Compare the current associative decimal against:

- a packed tuple with constants/accessors for indices;
- a small final declared-property value object;
- a dual representation that caches a checked native mantissa and scale while
  retaining exact digit/sign data or falling back to it at boundaries.

Measure parsing, comparison, addition, multiplication, division, formatting,
copying, peak live memory, and numeric-heavy SEL scenarios. Include values around
`PHP_INT_MIN`, `PHP_INT_MAX`, scale alignment overflow, negative zero
normalization, maximum integer/fractional digits, and non-terminating division.

Do not use floating point as an overflow fallback. Do not assume a small object
is cheaper than a three-element array without measurements on the target PHP
build. Preserve the existing checked native fast paths and exact string fallback.

### P2: reduce other hot temporary arrays only after profiling

Candidates include list `keys()`/`values()`, `array_map()` result construction,
destructured entry pairs, repeated uppercase/lowercase key sets, frame arrays,
and join/group bucket metadata. Prefer direct `foreach` over packed arrays where
it removes a materialized intermediate.

Do not replace a required `Value::copy()` with an object alias. Do not force
`array_values()` or `array_slice()` on an already packed COW array without a
demonstrated need.

### P2: instrument shape-cache cardinality

Before changing cache policy, add benchmark-only counters or profiling for:

- `RecordShape::intern()` calls and hits;
- distinct base shapes;
- aliases per shape;
- serialized signature time;
- retained memory in short-lived CLI and long-lived worker simulations.

Only then decide whether a prepared-shape builder is sufficient or whether cache
bounds/eviction are needed.

### P3: avoid PHP materialization through SQL/hybrid execution

For large relations, the highest-leverage path remains avoiding a PHP `Value`
graph for rows and intermediates that SQL can process. Extend pushdown only where
generated SQL, result identities, hybrid continuation behavior, and live query
plans remain equivalent to the Lisp reference. Keep database execution timings
separate from PHP evaluator and materializer timings.

## Measurement protocol

Run each microbenchmark in a fresh process when measuring allocator/RSS effects,
and run enough same-process iterations to observe JIT warm-up for throughput.
Record:

- PHP version, architecture, allocator/build information where available;
- OPcache/JIT configuration and runtime status;
- dataset identity, row counts, result row counts, and scenario query;
- warm-up count, measured count, median, dispersion, and peak rather than only
  one arithmetic mean;
- evaluator, materializer, and combined timings;
- `memory_get_usage(false)`, `memory_get_usage(true)`, peak usage, and external
  peak RSS where available.

`memory_get_usage(true)` reflects allocator arenas and can stay flat when reused
memory serves new allocations. It is not an allocation counter and must not be
used to infer a per-zval or per-row byte size. Keep instrumentation outside the
timed inner loop or quantify its cost.

## Validation requirements

Every implementation change must pass:

- PHP conformance and SQL translation/parity suites;
- the cross-host decimal oracle, including native-integer boundaries and exact
  fallback cases;
- differential evaluator and SQL fuzzing against Lisp and the other hosts;
- duplicate-key, numeric-string-key, embedded-NUL-key, and insertion-order tests;
- shaped record, packed list, fallback record, lazy value, and scalar-with-child
  conversion tests;
- homogeneous and heterogeneous `LINK`/`LINK_LEFT`, no-match, null-right,
  alias-case, and duplicate-column tests;
- nested mutation/assignment tests proving independent SEL values remain
  isolated;
- repeated-context tests proving benchmark reuse does not mutate input;
- JIT-off and verified-JIT-on benchmarks with identical datasets and timing
  boundaries;
- fresh-process memory/RSS measurements and same-process warm throughput runs.

For benchmark-framework changes, require all lanes to report the new fields and
retain the old combined figure under an explicitly named compatibility field or
document the schema break. Result parity must be checked from the same materialized
value whose conversion was timed.

The success criterion is not merely a lower PHP latency. Result rows, generated
SQL, exact decimal output, errors and positions, key order, lazy evaluation, and
mutation isolation must remain equivalent. Report evaluator and materializer
improvements separately so benchmark-boundary corrections are not mistaken for
runtime optimizations.
