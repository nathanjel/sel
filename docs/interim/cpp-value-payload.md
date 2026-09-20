# C++ value payload and aggregate visitor — 2026-09-20

The implementation keeps the 8-byte aliasing `Value` handle and intrusive
reference count. Decimal state and collection state now live in separately owned,
optional allocations attached to the shared implementation. Short strings,
booleans and null values no longer construct a decimal, two vectors, an unordered
map and a shape pointer. Read-only collection access on a leaf uses empty state
without allocating. Adding children through an alias allocates state on the
shared implementation, so ordinary copying continues to alias. Explicit clones
copy both optional payloads independently.

On the measured x86-64 GCC build, `sizeof(Value::Impl)` falls from 272 to 72
bytes. `sizeof(Value)` stays 8 bytes and `sizeof(Dec)` stays 96 bytes. These are
object sizes, not total allocation sizes: numeric values additionally allocate
their decimal; collections additionally allocate their collection state and
buffers. The existing bounded freelist remains in place. Destruction still
detaches child handles iteratively, including children stored in both materialized
entries and packed storage.

The aggregate `walk()` visitor is now a template, allowing each of its five
callers to expose the callback body to the compiler. Evaluation order, early
termination, exception cleanup and context-frame behavior are unchanged.

## Method

`tools/benchmark-cpp-value.cpp` measures construction, retained allocator bytes
and destruction of 200,000 values in fresh processes. The row workload creates
two-field shaped records containing a short string and an integer. Retained
allocation uses glibc `mallinfo2`, after reserving the outer handle vector;
measurements include allocator rounding, optional payloads and collection buffers.
This memory probe requires glibc and is not a portable process-RSS measurement.

The aggregate workload prepares 10,000 integers outside timing, compiles each
expression once, warms it up, then executes it 100 times. Five fresh-process
measurements are collected for each of three builds: the pre-change source
snapshot, payload separation with the original `std::function` visitor, and the
final templated visitor. Earlier structural-hash corrections are present in all
three. Compiler: GCC 16.2.1, `-std=c++23 -O2`, x86-64 Linux. Measurements run
sequentially after compilation and validation finish.

To reproduce a build:

```sh
c++ -std=c++23 -O2 -Icpp tools/benchmark-cpp-value.cpp cpp/sel.cpp -o /tmp/sel-value-bench
for mode in bool text num rows aggregate; do /tmp/sel-value-bench "$mode"; done
```

For baseline comparison, point the include directory and `sel.cpp` at a saved
source tree with its matching headers, optimizer and vendored dependencies.
For the payload-only comparison, keep the new payload and restore the original
`walk()` signature accepting its `std::function` callback. Compile all variants
with identical flags. Timing is machine- and allocator-specific.

## Results

Five-sample medians; raw samples are in [cpp-value-benchmark.json](cpp-value-benchmark.json).
Memory is retained allocator bytes expressed in decimal MB; times are milliseconds
for constructing or destroying the entire 200,000-value batch.

| Workload | Memory before → after | Construction before → after | Destruction before → after |
| --- | ---: | ---: | ---: |
| Boolean | 57.60 → 16.00 MB | 39.96 → 16.64 ms | 9.92 → 7.02 ms |
| Short text | 57.60 → 16.00 MB | 48.21 → 23.15 ms | 14.34 → 8.24 ms |
| Integer | 57.60 → 38.40 MB | 43.14 → 34.83 ms | 9.51 → 12.58 ms |
| Two-field row | 179.20 → 102.40 MB | 158.57 → 122.78 ms | 52.25 → 46.22 ms |

The payload change reduces retained memory across these workloads. Numeric
destruction regresses about 32% because it now frees a separate decimal
allocation. Short-text and boolean values gain most: their payload no longer
reserves space for either optional state.

Aggregate times below are microseconds per execution over 10,000 elements.

| Expression | Baseline | Smaller payload only | Plus templated visitor |
| --- | ---: | ---: | ---: |
| `SUM(XS, _)` | 558.19 | 599.91 | 590.42 |
| `MAP(XS, _ + 1)` | 3106.89 | 3376.41 | 3373.39 |
| `FILTER(XS, _ > 5000)` | 3554.64 | 3440.22 | 3360.45 |
| `ALL(XS, _ >= 0)` | 3248.73 | 3226.56 | 3100.39 |

Measured after allocation changes, the templated visitor improves SUM by about
1.6%, FILTER by 2.3% and ALL by 3.9%; MAP is essentially unchanged. These are small
local gains, not a general throughput guarantee. The benchmark executable's
text segment grows from 677,484 to 681,890 bytes with visitor specialization.
Relative to the original implementation, SUM and arithmetic MAP still regress
about 6% and 9%, respectively: removing type erasure does not recover the cost
of optional decimal allocation. This implementation chooses lower live footprint
and faster scalar/row construction while making that arithmetic tradeoff explicit.

## Validation

- 167 C++ unit checks, including scalar-to-collection aliasing, decimal-cache
  aliasing, independent clone state and 10,000-level iterative destruction.
- Address/undefined-behavior/leak sanitizers pass the ownership unit suite.
  Address/undefined-behavior sanitizers pass all 911 conformance cases; leak
  checking is disabled for that sandboxed conformance run.
- 911 release conformance cases, 85 SQL unit checks and 852 SQL translation cases.
- 39,990 independent decimal-oracle cases, zero mismatches.
- 4,000 differential programs against JavaScript, no disagreements or crashes;
  54 host API probes agree.

The full five-language repository gate was not rerun. Its earlier unrelated
Python join-alias differences remain documented in the changelog.
