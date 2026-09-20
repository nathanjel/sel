# C++ container allocation — 2026-09-20

The [four-commit review](commit-benchmark-review.md) identified a repeatable
join-throughput loss after `619bc31` separated collection payloads from the value
header. This change keeps the compact scalar representation and allocates a
container's header and collection together. It does not inline a collection into
every scalar or inline decimal state.

## Implementation

A container-specific derived implementation contains the collection payload.
Lists, shaped/fallback records, internal row producers and container clones use
this allocation. Scalars still use the 72-byte header; adding fields through an
existing scalar alias lazily allocates a separate collection as before. The
8-byte aliasing handle and 96-byte decimal representation are unchanged on this
x86-64 build.

A flag in existing header padding selects destruction without a virtual table.
`CollectionImpl` releases the base's collection pointer before its embedded
payload is destroyed. The iterative child-detachment walk remains in place and
then destroys the appropriate concrete type. This avoids both deleting the
embedded payload separately and recursively destroying deeply nested values.
The inherited sized allocator bypasses the scalar-header freelist for the larger
container allocation. There is no additional pool or retained-buffer cache.

```cpp
struct CollectionImpl : Value::Impl {
  Value::Collection payload;
  CollectionImpl() {
    inline_collection = true;
    collection.reset(&payload);
  }
  ~CollectionImpl() { collection.release(); }
};

void delete_impl(Value::Impl* impl) {
  if (impl->inline_collection) delete static_cast<CollectionImpl*>(impl);
  else delete impl;
}
```

This ownership arrangement requires all internal header destruction to go
through `delete_impl`. A default `delete` through the base type would be wrong
for a combined allocation. Tests cover ordinary aliases, deep independent clones,
materialized entry views, shaped-to-fallback conversion, final shape release,
and iterative destruction of a 10,000-level value.

## Rejected experiment

A bounded raw collection pool retained up to 2,048 blocks per thread, with all
vectors, maps and shape references destroyed before caching. It preserved the
72-byte header but did not recover the join losses. In the first seven-sample
comparison, S1/S5/S6 changed from 582.03/550.94/273.44 ms to
583.57/552.50/274.78 ms. That does not justify another cache. The
[experimental patch](cpp-collection/pool-experiment.patch) and
[raw measurements](cpp-collection/pool.json) are preserved; the pool is absent
from the final implementation. Earlier globally inline payload variants remain
documented in the four-commit review and were also rejected.

## Method and results

Baseline: `c5a8991`, GCC 16.2.1, `-std=c++23 -O2`, Intel Xeon D-2141I.
Both variants use the same scale harness, 137,100-row fixture and current result
reference. Each batch uses three warmups and seven timed samples per scenario.
Reported times are medians of prepared execution plus result materialization;
input cloning, fixture loading, compilation and SQL planning are excluded.
Cloning is recorded separately in the raw files. Builds and correctness tests
finish before timed batches run sequentially. The repeat reverses the comparison
order. These are local measurements, not cross-machine performance guarantees.

Mandelbrot uses three warmups and eleven measured frames per batch, with both
orders measured and every output frame compared. The memory probe uses five fresh
processes per mode, creating 200,000 values each. It measures glibc allocator bytes,
including allocator rounding, rather than process RSS.

| Scenario | Baseline ms | Combined ms | Change | Repeat baseline ms | Repeat combined ms | Change |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S1 | 582.03 | 553.64 | -4.9% | 577.87 | 562.23 | -2.7% |
| S2 | 5.77 | 5.45 | -5.5% | 5.81 | 5.44 | -6.4% |
| S3 | 188.79 | 176.32 | -6.6% | 188.61 | 183.06 | -2.9% |
| S4 | 8.69 | 8.88 | +2.2% | 8.66 | 9.23 | +6.5% |
| S5 | 550.94 | 525.67 | -4.6% | 553.12 | 533.78 | -3.5% |
| S6 | 273.44 | 257.93 | -5.7% | 279.38 | 261.23 | -6.5% |

Five scenarios improve in both comparisons. The targeted S1/S5/S6 join losses
are partially recovered: S1 improves 2.7–4.9%, S5 3.5–4.6%, and S6 5.7–6.5%.
This does not establish complete recovery to `1614eed`. S4 **regresses 2.2–6.5%**
(0.19–0.56 ms); it is a small filter/projection/sort workload and receives no
blanket speedup claim. The first sequence was baseline → rejected pool →
combined; the repeat was combined → baseline. Result parity, SQL-reference checks
and input-context immutability pass for every scenario in all five reports.

Median context cloning is 83.23 → 82.53 ms in the first comparison and
83.15 → 81.92 ms in the repeat. These small preparation improvements are separate
from the table, not added to its throughput claims.

Mandelbrot changes from **148.065 → 149.491 ms (+1.0%)** and
**147.508 → 149.622 ms (+1.4%)**. This is a small consistent slowdown in these
batches; no arithmetic improvement is claimed. All 44 measured frames have the
same SHA-256, `3d50a84d3774807aaaa132e9b6c826a89ad11548ab4e53ced220b0a00854bcba`.

### Memory and value construction

| 200,000 values | Baseline live bytes | Combined live bytes | Baseline bytes after clear | Combined bytes after clear |
| --- | ---: | ---: | ---: | ---: |
| Short text | 16,000,848 | 16,000,848 | 165,968 | 165,968 |
| Integers | 38,400,048 | 38,400,048 | 167,728 | 167,728 |
| Two-field rows | 102,401,424 | 102,401,424 | 170,816 | 172,096 |

All five fresh-process samples give the same byte counts. Combining allocations
does not reduce these live totals: allocator rounding offsets the saved allocation
header. It preserves the earlier scalar-memory savings. Post-clear row retention
increases only 1,280 bytes in this allocator; there is no new runtime cache.
The scalar header remains 72 bytes, the collection 120 bytes and the combined
object 192 bytes, before buffers and allocator overhead.

Median row construction improves 118.12 → 113.35 ms (4.0%) and destruction
45.22 → 40.07 ms (11.4%). Text and integer construction are near baseline.
Integer destruction changes 12.24 → 12.59 ms (+2.8%), consistent with treating
scalar paths as a tradeoff rather than claiming every operation improves.

### Recommendation and remaining work

Keep the combined allocation for the measured query benefit and unchanged live
footprint. Keep decimals optional and retain compact scalar headers. The evidence
does not support the rejected raw-block pool or globally inlining payloads.
Before another allocator change, profile S4 and the scalar destruction path;
manual type dispatch and bypassing the old header freelist are possible costs,
but these experiments do not isolate their contributions. Avoid adding another
pool solely on the assumption that fewer allocator calls always help.

### Validation

- 171 C++ unit checks, including new ownership/lifetime cases; 85 SQL unit checks.
- 911 conformance cases and 852 SQL cases; bounded metadata checks.
- Address, undefined-behavior and leak sanitizers pass the 171 unit checks.
- 39,990 decimal-oracle cases, zero mismatches.
- 4,000 differential programs and 54 API probes agree with PHP; zero host crashes.
- All six benchmark scenarios pass in baseline, rejected pool and combined runs;
  all Mandelbrot measured outputs match. The full five-language gate was not rerun.

[Raw samples, source fingerprints and validation output](cpp-collection/) retain
both accepted and rejected measurements. The first combined benchmark preceded
a comment-only source edit; the repeated build and final harnesses include it.


Reproduction: [benchmark tools](../../tools/cpp-collection/README.md).
