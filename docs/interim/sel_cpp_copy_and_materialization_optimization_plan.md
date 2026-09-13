# SEL C++ Copy and Materialization Optimization Plan

## Scope and verified ownership model

This note covers C++-specific construction, copying, and materialization costs.
It separates benchmark-boundary corrections from evaluator optimizations and
does not propose changing SEL ownership semantics merely to improve a timing.

The current model is explicit in `cpp/sel.hpp:108-123`:

- `Value` is a `std::shared_ptr<Impl>` handle. Copying a `Value` copies that
  handle and increments a reference count; both handles address the same value.
- Moving a `Value` transfers the handle. It is useful when a local is dead, but
  it does not make copying the underlying value tree cheaper.
- `Value::clone()` is the recursive, detached copy. Its implementation at
  `cpp/sel.cpp:803-834` allocates a new `Impl` at every node, copies scalar
  strings, recursively clones child values, and shares only immutable
  `RecordShape` metadata.
- `Value::set(std::string, Value)` accepts a handle by value and moves that
  handle into the destination (`cpp/sel.cpp:1067-1111`); it does not implicitly
  deep-clone it.

Deep clones at language copy boundaries must be treated as semantic until
tests prove otherwise. Verified examples include assignment
(`cpp/sel.cpp:2637-2652`), list construction (`cpp/sel.cpp:2438-2452`),
`LIST`/`RECORD` (`cpp/sel.cpp:3359-3382`), collection-copying operators such as
`TAKE`, `DROP`, and `SELECT_COLS` (`cpp/sel.cpp:3421-3470`), and aggregate
`MAP`/`FILTER` (`cpp/sel.cpp:3927-3948`). A cheap handle copy is correct for
temporary read-only access; it is not a substitute where the result must be
detached from a source that can later be mutated.

## Verified findings

### 1. The scale runner charges a full context clone to C++ evaluation

The timed interval in `tools/scale-test/sel_benchmarks.cpp:311-322` contains:

```cpp
Value scenario_context = context.clone();
Value actual = program.run(scenario_context);
```

The clone recursively duplicates the complete 137,100-row context before each
scenario. The previous focused probe measured about 101 ms for this clone and
effectively zero time for a root handle copy. These figures are diagnostic
measurements, not stable acceptance thresholds.

All six current scale queries are read-only, but retaining a per-run clone is a
reasonable isolation policy. The measurement error is that clone preparation
is inside `elapsed_ms`, not that isolation itself is necessarily wrong.

### 2. Benchmark JSON objects use fallback records

The benchmark parser constructs every JSON object as `Value::none()` followed
by repeated `set()` calls (`tools/scale-test/sel_benchmarks.cpp:142-154`). This
uses insertion-ordered `Impl::children` rather than a shared `RecordShape` and
flat `Impl::storage`.

Fallback lookup scans linearly until `INDEX_THRESHOLD == 16`; the representation
and threshold are in `cpp/sel.hpp:228-254`, and lookup is in
`cpp/sel.cpp:959-980`. Most benchmark rows have fewer fields, so repeated named
access in joins normally scans their child vectors. By contrast, shaped lookup
uses `RecordShape::key_map` and direct storage slots
(`cpp/sel.cpp:1011-1046`). `shape_record()` currently shapes `RECORD` and `MAP`
results (`cpp/sel.cpp:3331-3341`), not host-loaded benchmark rows.

A previous temporary shaped-input probe improved join-heavy scenario 5 by
about 15-16%. Treat that as prioritization evidence and remeasure after the
benchmark boundary is corrected.

### 3. `entries()` permanently materializes the list's ordered view

`add_distances()` iterates `customers->entries()` at
`tools/scale-test/sel_benchmarks.cpp:264-277`. For a flat list,
`Value::ensure_children()` creates a second vector with numeric strings
`"1"`, `"2"`, ... and copied `Value` handles (`cpp/sel.cpp:942-949`). The
materialized `children` vector remains attached to the list.

This also changes subsequent cloning. `clone_at()` uses the flat-storage path
only while a list's `children` is empty; after materialization it clones the
entry vector, including every numeric key (`cpp/sel.cpp:819-829`). Direct
`size()`/`slot()` access already exists at `cpp/sel.cpp:983-1008`, so no mutable
slot API is required: copying a slot's `Value` handle and calling `set()` on the
copy intentionally mutates the shared row, exactly as the current loop does.

The top-level `dataset.entries()` and `reference.entries()` loops do not have
the same scale problem: those values are JSON objects already represented by
fallback entries. The avoidable conversion is specifically calling
`entries()` on vector-backed lists or shaped records in hot paths.

### 4. The matched-row join projector allocates values it immediately discards

`JoinProjector::operator()` creates
`std::vector<Value> slots(actions.size())` at `cpp/sel.cpp:2985-3021`.
`Value::Value()` allocates a heap-backed `Impl` (`cpp/sel.cpp:747`), so every
output slot first allocates an empty value and then overwrites it with a source
handle or a new `Value::none()`.

The previous focused construction probe found reserve-and-append approximately
3-4 times faster than default-construct-and-overwrite for the isolated slot
pattern. This is not an end-to-end estimate, but it proves the allocation is
real and is paid once per projected slot of every matched output row.

Projector compilation also calls `sample_left->entries().size()` and
`sample_right->entries().size()` at `cpp/sel.cpp:3078-3091`, even after
`entry_slot()` has used shape metadata. Those checks can use `size()` and avoid
materializing an entry view on shaped samples.

The unmatched `LINK_LEFT` path bypasses the shaped projector and calls
`make_joined_row()` (`cpp/sel.cpp:2986-2989`). It therefore constructs fallback
records for unmatched rows. This is a separate path and should be measured
before deciding whether to compile a null-right shaped action plan.

### 5. Aliased shapes are looked up and rebuilt for every unaliased shaped row

For a shaped row, `ensure_row_table_alias()` copies the shape's key vector,
appends alias keys, copies the row's storage handles, and calls
`intern_record_shape()` (`cpp/sel.cpp:2875-2893`). The process-wide interner is
a mutex-protected `std::map<vector<string>, shared_ptr<...>>`
(`cpp/sel.cpp:701-713`), so repeated rows still rebuild and compare the key
vector and take the mutex even when the resulting shape already exists.

Copying the source storage handles into a new aliased row is currently required
by this representation: the destination has extra slots. Shape/alias metadata
reconstruction is avoidable; the destination storage itself cannot simply
reference the source vector while preserving the present `Value` layout.

### 6. Three join frames are copied although their local builders are dead

`do_link()` copies local frame vectors into `ctx.frames` at
`cpp/sel.cpp:3257`, `cpp/sel.cpp:3276`, and `cpp/sel.cpp:3303`. At each insertion
the frame builder is no longer needed while evaluation uses `ctx.frames.back()`.
The first moved-from builder is later cleared and rebuilt, which is valid for a
moved-from `std::vector`.

Moving these frames avoids copies of their strings and `Value` handles, but
each frame has only a few entries. References to local frame values are not a
safe replacement: nested evaluation can push more frames and the frame must
remain owned by the context until it is popped. This is a low-priority cleanup.

### 7. One additional clone candidate is concrete but outside the scale hot path

Plain `SORT` without a key body stores `{item.clone(), item.clone(), i}` at
`cpp/sel.cpp:3613-3618`. The item and comparison key are initially identical,
and comparison does not mutate either. A single deep clone followed by two
handle copies of that detached value may preserve behavior while removing the
second recursive clone. `SORT_BY`, which the six scale scenarios use, already
clones the item once and moves the evaluated key (`cpp/sel.cpp:3662-3674`).

This candidate needs a focused nested-mutation test before implementation; it
should not distract from the measured join and input-construction costs.

## Prioritized implementation plan

### P0: make the C++ timing boundary comparable

Measure and report distinct phases:

1. `context_clone_ms`: clone the prepared context before evaluator timing;
2. `evaluate_ms`: only `program.run(scenario_context)`;
3. result verification/materialization, timed separately if the cross-lane
   benchmark contract requires it.

Keep the clone for isolation initially. For the six read-only programs, a
second A/B mode may run against the prepared context directly, but only after
checking that the context is structurally unchanged before and after every
scenario. Do not silently replace the isolated mode with a shared mutable
context.

### P0: construct shaped host-input records

Add a reusable record-construction helper used by the benchmark JSON loader
and suitable for other host ingestion paths. It should:

1. collect keys and `Value` handles in insertion order;
2. preserve `Value::set()` semantics for duplicate keys: update the first
   slot's value without adding another key or changing its position;
3. intern one immutable `RecordShape` for the final unique key sequence;
4. return `Value::shaped(shape, std::move(storage))`;
5. retain a fallback path if an input contract cannot be represented by a
   unique-key shape.

Do not pass duplicate keys directly to `RecordShape`: its constructor uses
`key_map.emplace()` (`cpp/sel.cpp:694-697`), which would retain the first slot
while duplicate storage slots remained present. Prefer a public or internal
builder rather than exposing the anonymous-namespace interner to benchmark
code.

### P0: keep customer setup on flat list storage

Replace `customers->entries()` in `add_distances()` with indexed
`size()`/`slot()` traversal. For each non-null slot, copy the `Value` handle to
a local and mutate that handle. This preserves the current shared-row mutation
while avoiding numeric key strings and the persistent entry-vector view.

Add a regression assertion or focused test showing that setup leaves the
customer list on its flat path; performance should not depend on invoking a
private API to inspect this state.

### P0: append join projector slots without default construction

Use `reserve(actions.size())` and append exactly one value per action. Source
actions should append cheap handles. `None` and a genuinely missing lookup
still require a newly allocated `Value::none()`.

Also replace projector-compilation `entries().size()` checks with `size()`.
Keep irregular-record fallback lookups, but do not call `entries()` unless a
slot lookup actually fails. Verify output key order, nested aliases, missing
fields, and mixed-shape rows.

### P1: cache alias plans by source shape and alias

Use a thread-safe cache keyed by source `RecordShape` identity and table alias.
An external cache is preferable to making `RecordShape`, currently shared as
`const`, observably mutable. Each plan should hold:

- the interned destination shape;
- whether the original and lowercase aliases are appended;
- the destination size or append actions.

The source shape determines whether the lowercase alias already exists, so the
plan does not need to inspect every row. Rows still allocate a destination
storage vector and copy source handles. Benchmark mutex contention and cache
growth; the global shape interner already keeps shapes alive for process
lifetime, so cache ownership must not accidentally introduce a second
unbounded key-copy store.

### P1: compile the unmatched left-join projection if measurements justify it

Extend the projector with null-right actions so matched and unmatched rows can
share the same destination shape. Preserve the current rules around promoted
right fields, null values, table aliases, lowercase aliases, and `_2`. Retain
the generic fallback for heterogeneous or irregular rows.

Prioritize this only if allocation/profile data shows unmatched-row fallback
construction is material in scenario 6 or representative workloads.

### P2: move completed join frames

Use `ctx.frames.push_back(std::move(frame))` at the three verified `do_link()`
sites. Rebuild the moved-from vector before its one reuse. Treat this as a
small refcount/string-allocation reduction and benchmark it only together with
the larger join changes.

### P2: audit deep clones one call site at a time

Do not apply `std::move` mechanically to `clone()` results or replace clones
with source handles. A move can eliminate a handle/refcount operation only when
the source local is dead; it cannot turn a required detached copy into a move.

Start with concrete duplicate-work candidates such as plain `SORT`'s two clones
of the same item. For every proposed removal, prove that no subsequent
mutation—of the source, output item, comparison key, aggregate frame, or nested
child—becomes visible through another value. Add focused nested-record and
nested-list mutation tests before accepting the change.

### P3: profile a smaller `Impl` representation

Every scalar currently allocates a general-purpose `Impl` containing strings,
two vectors, an unordered map, shape pointer, and `std::function`
(`cpp/sel.hpp:232-251`). After the P0/P1 fixes, use allocation counts and peak
RSS to decide whether a tagged/split representation is justified, for example:

- scalar payload separated from list/record containers;
- thunk state allocated only for lazy values;
- fallback index allocated only after the threshold;
- allocator or small-object strategies that preserve handle and clone
  behavior.

This is a representation redesign, not a prerequisite for the targeted fixes.

## Validation and acceptance

### Correctness

Run at minimum:

```sh
make -C cpp test
cpp/build/sqlunit
cpp/build/sqlt
make -C cpp asan
```

Then run differential evaluator and SQL fuzzing with C++ and the Lisp reference,
and include all maintained hosts for final parity:

```sh
SEL_IMPLS="lisp cpp" tools/fuzz.sh 500 20260912
SEL_IMPLS="lisp cpp" tools/fuzz-sql.sh 500 20260912
```

Use a fresh seed in addition to the recorded seed when changes are ready.
Acceptance requires unchanged values, key order, generated SQL, errors, and
aliasing behavior. Add focused tests for:

- duplicate JSON/record keys and insertion order;
- shaped and irregular rows mixed in one relation;
- matched and unmatched `LINK_LEFT` output shape and null fields;
- nested source mutation after `TAKE`, `DROP`, `MAP`, `FILTER`, `SORT`, and
  assignment;
- plain `SORT` where item and key initially share a detached clone;
- repeated and concurrent alias-plan lookup if the public host is expected to
  be thread-safe.

### Performance

Run `tools/scale-test/benchmark_all.py --runs 10` only after all lane runners
share the corrected timing contract. For C++ additionally record:

- `context_clone_ms`, `evaluate_ms`, and verification/materialization time;
- shaped-input versus fallback-input A/B results;
- allocations and peak RSS per scenario;
- matched and unmatched projector rows per scenario;
- alias-plan cache hit rate and interner lock time if practical.

Compare medians and a dispersion statistic after warm-up, not one favorable
run. Each P0/P1 change should have an isolated before/after measurement so the
gain is attributable. The final C++-versus-Lisp comparison is meaningful only
after context preparation and result materialization boundaries are aligned.
