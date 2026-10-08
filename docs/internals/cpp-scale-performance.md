# C++ scale scenarios 1, 5, and 6 at 2e36459

Investigation on 2026-10-08. The strongest measured opportunity is scenario 6's
separate hash set of rejected join rows. Replacing it with a flag in each join
bucket entry reduces that scenario by about 19%. Four small experiments together
reduce scenarios 1/5/6 by approximately 4.3%/2.7%/25.1%. The larger remaining
opportunities in scenarios 1 and 5 concern value representation and predicate
evaluation, rather than deep copying entire records.

The repository runtime is unchanged. Experimental source, patches, reports, and
a reproduction script are saved locally in
[`tools/commit-benchmark/results/cpp-investigation-2e36459`](../../tools/commit-benchmark/results/cpp-investigation-2e36459/).
That results directory is ignored by Git; it must be saved separately if this
report is shared.

**What the timer measures.** The scale harness times `Program::run` and result
materialization. Context cloning occurs before that timer. The fixture contains
35,000 orders, 90,000 order items, 10,000 customers, 2,000 products, and 100
categories. Every fixture record is shaped. SQL translation, JSON loading,
parity comparisons, and context integrity hashing occur outside the timer.

**What is actually being copied.** `Value` is an eight-byte handle. Its copy
constructor increments a plain reference count; its move transfers the pointer.
All three physical programs are write-free, so collectors retain shared values
and check their depth instead of cloning their trees. Instrumentation around one
warmed evaluator run, with a fresh context prepared beforehand, found:

| Work during evaluation | Scenario 1 | Scenario 5 | Scenario 6 |
|---|---:|---:|---:|
| Heap allocation requests | 1,086,803 | 469,870 | 294,085 |
| Requested bytes, cumulative | 131,287,405 | 48,522,269 | 30,364,840 |
| Deep-cloned value nodes | **0** | **0** | **0** |
| Alias rows constructed | 108,360 | 137,003 | 92,001 |
| Handles inserted into alias storage | 711,649 | 991,025 | 646,007 |
| Joined rows built by the shaped projector | 115,984 | 12,107 | 0 |
| Handles inserted by that projector | 1,500,338 | 242,467 | 0 |
| Numeric/text join keys constructed | 186,922 | 147,071 | 92,000 |
| Copy-constructor calls for handles | 3,306,008 | 1,586,565 | 835,025 |
| Depth-check calls | 313,811 | 10,234 | 410 |
| Entries popped from depth-check traversal stacks | 2,112,363 | 0 | 9,300 |

Requested bytes are allocation traffic, not peak retained memory. These counters
exclude context preparation, result dumping, parity checking, and the final
destruction of input/result values after the run. They include destruction of
intermediates during evaluation. Handle copy assignments are not counted in the
copy-constructor row. The shaped-projector counters exclude fallback joins:
scenario 6 does construct unmatched rows, using the fallback because its null
right record is unshaped.

**Alias rows and join rows.** In `cpp/sel.cpp`, `alias_by_plan` constructs a vector,
copies all field handles, appends upper/lowercase table aliases, and allocates a
shaped container. `JoinProjector::build` allocates another container/vector and
copies promoted fields and binder handles. These rows subsequently require
reference-count decrements and destruction. Scenario 5 aliases the complete
35,000-order, 10,000-customer, 90,000-item, and 2,000-product inputs, although
filters greatly reduce the number of output joins. Scenario 6 still aliases all
90,000 items before rejecting their matches. Hash joins avoid pairwise predicate
evaluation, but their build phase remains proportional to the right input.

Rust performs analogous row construction and reference counting; Go and Lisp
copy pointers without per-field reference-count updates. C++'s combined
collection header requests 192 bytes on this build, including storage for
fallback entries and a hash index even for shaped rows. Rust keeps its
`ValueInner` at 104 bytes, with rare state boxed separately, plus the surrounding
`Rc`/`RefCell` overhead. The sizes are not an exact allocation comparison, but
the unused C++ fallback metadata is a concrete representation difference.
Sampling profiles repeatedly identified alias construction, field access, and
value destruction as hot paths. The profiles were diagnostic `-pg` builds;
their timings are not used for speed comparisons.

**Scenario 6: rejection bookkeeping.** `join_right_null_rejects` walks hash
buckets, reads `id`, and inserts each non-null right row's address into an
`unordered_set`. Probing later checks that set for each matched right row.
This dataset rejects all 90,000 order-item rows, producing 90,000 separately
allocated set nodes and another round of pointer hashing. Rust's buckets store
`(Value, bool)` and update the boolean in place. Go keeps a separate rejection
map, but builds a contiguous list of bucketed rows and sizes the map before
marking them. C++ walks the bucket vectors in hash-table order and grows the set
incrementally. The temporary C++ flags variant follows Rust's representation
while preserving row numbering, match status, error handling, and collection
limits. Bucket entries grow from eight to sixteen bytes, which is a tradeoff
for joins that need no rejection flags.

**Repeated field lookup.** C++ literal indexes in `eval_dispatch` call
`Value::get`, hashing the field name through the shape's `key_map` each time.
Rust's `eval_index` and Go's index evaluator cache the shape and slot on the
expression. The slots experiment adds 256 bounded hints to the evaluation
context, validates both node and shared shape before reading the slot, and
falls back to ordinary lookup when they differ. Keeping it in the context
avoids mutable state shared between concurrent executions of a `Program`.
This improves scenario 1 by about 2%; it barely affects scenario 6 because its
expensive rejection-field reads occur in the join helper, not the index
evaluator. The prototype does not cache every direct `get` in the runtime.

**Depth checking and decimal copying.** C++'s `check_clone_depth` has a cheap
flat-row path, but nested rows allocate a vector stack and push scalar leaves
as well as containers. Go skips leaf recursion; Rust and Lisp use recursive
walks. The depth experiment recursively visits only containers, checks child
depth before skipping leaves, and preserves the 200-level cap. Despite
scenario 1's 2.1 million stack entries, the measured gain is only about 0.7%.
`make_fast_join_key` also copies a complete `Dec` via `as_dec`, whereas Go reads
its cached decimal by pointer and Rust's small decimal representation is much
smaller. Borrowing C++'s existing `as_dec_ref` avoids those copies, but the
integer keys here have no large heap payload to copy, so the measured gain is
small. Neither is the principal explanation for the gap.

**Measurements.** The existing scale harness ran sequentially, using the same
10x fixture and isolated-context mode, with two warmups and five measured runs
per variant. A second batch ran in reverse order, with the baseline at both
ends. The table gives pooled medians of ten samples in milliseconds, including
result dumping. All focused runs passed result, SQL, and context-integrity
checks. Compile work and instrumented profiling/counting were separate from
these timing batches.

| Temporary variant | Scenario 1 | Scenario 5 | Scenario 6 |
|---|---:|---:|---:|
| Baseline | 545.4 | 214.7 | 156.8 |
| Container-only depth checking | 541.6 | 214.4 | 156.8 |
| Borrow cached join decimals | 543.6 | 212.0 | 156.4 |
| Rejection flags in bucket entries | 545.6 | 215.4 | **126.5** |
| Cache literal field slots | **534.1** | 212.3 | 157.5 |
| Depth + decimals + flags | 532.7 | 213.5 | 120.0 |
| All four | **522.0** | **209.0** | **117.4** |

The baseline is slightly slower than the published 524.0/206.7/153.2 snapshot;
compare variants with this investigation's adjacent baseline, not directly
with the earlier cross-language table. Effects are not additive. Changes in
allocation and code layout can interact, and sub-percent differences should
not be treated as established improvements. The flag-only scenario 6 gain
appeared in both orders (155.7 to 126.1 ms; 157.0 to 126.9 ms).

**Further opportunities, not measured in these variants.** Rust's
`compare_nodes` reads literal comparison operands without constructing value
cells, and `eval_bool` evaluates FILTER predicates and AND/OR directly into a
native boolean. C++ constructs temporary numeric/text operands and boolean
values, then immediately coerces the boolean in the consumer. Its scalar
freelist helps, but numeric literals still allocate decimal payloads, and
temporary values still need destruction. This is particularly relevant to
scenario 1's filters and scenario 5's conjuncts. A production adaptation must
preserve operand evaluation before coercion, short-circuit order, error
positions, depth charging, and the thread safety of shared literal decimals.

Rust also stores preserved list positions as `u32` keys alongside packed
values. After a gap, C++ FILTER constructs fallback `Entry` pairs containing
`std::string` keys and handles; those pairs are 40 bytes on this build. Compact
preserved keys, rare collection metadata stored separately, and avoiding
physical alias wrappers through a carefully designed row view could reduce
allocation and memory traffic. These require wider representation work and
ownership/shape-invalidation checks. They are proposals, not demonstrated
speedups. The C++ hash join also reserves by right-row count: in scenario 6
that is 90,000 rows for only 1,900 distinct product keys, another unmeasured
memory tradeoff.

The rejection flags are the best first implementation candidate. Literal
predicate handling and compact collection storage are the next investigations
for scenarios 1 and 5. C++'s advantage in Mandelbrot and ray tracing is compatible
with this result: arithmetic throughput and allocation-heavy relational
evaluation stress different parts of the interpreter. Standard-library
maturity does not remove redundant containers, hashes, or reference updates.

The combined prototype passed **1,050/1,050 C++ unit checks**, **2,317/2,317
conformance cases**, and **6/6 full scale parity scenarios**, including both
SQL dialects and context integrity. These checks establish useful initial
confidence; sanitizer and concurrency checks have not been run on the
experimental changes.

To reproduce locally, with the saved results directory available:

```sh
make -C cpp build/scale-bench
python3 tools/commit-benchmark/results/cpp-investigation-2e36459/reproduce.py --counts --checks
```

The script checks source/fixture hashes, applies patches only to fresh temporary
copies, builds all variants before timing, repeats the benchmark in reverse
order, and optionally runs allocation counters and existing C++ checks.
Use a fresh `--workdir` for another execution. The unit adapter only changes the
existing whitebox rejection test to inspect bucket flags and resets those flags
between assertions; its expected results are unchanged.
