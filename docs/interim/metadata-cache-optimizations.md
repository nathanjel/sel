# Bounded metadata and prepared record layouts — 2026-09-20

## Implementation and ownership

All five shape interners now retain at most 256 entries. A layout is eligible
only when it has at most 256 keys and their combined length is at most 16,384.
Lengths are bytes in PHP/C++, Unicode code points in Python/Lisp and UTF-16 code
units in JavaScript. A miss at capacity clears the cache generation before
insertion. Hits do not update an eviction list. Larger layouts still work but
are not retained by the interner. Both width and text length matter: an entry
limit alone would still retain arbitrarily large key maps or strings.

Alias plans use separate flat caches bounded to 256 eligible entries. C++ already
stored plans outside shapes; its cache is now bounded. Python, JS, PHP and Lisp
no longer populate per-shape alias caches, avoiding chains of shapes that keep
other caches alive after interner eviction. JS and PHP retain one most recent
table alias per source shape; Python, Lisp and C++ key plans by source and table.
Existing public alias-cache fields remain available but are no longer populated
by these runtime paths. JS alias construction now uses the resolved destination
shape directly, avoiding JSON signature generation for every row.

Literal-key `RECORD` calls prepare a layout on their call node in all five
parsers. Argument wrappers carry that layout into the builtin, and optimizer
copies preserve it. The runtime checks the actual key sequence before reuse;
dynamic or duplicate keys use the ordinary path. Layout discovery and global
interning therefore leave the stable projection loop. C++ still constructs the
ordered row before attaching its prepared layout; the other four paths also
bypass their ordinary record-constructor uniqueness checks.
Existing join projectors and PHP's prepared native-row ingestion continue to
reuse their prepared schemas. Argument evaluation, copying and error reporting
retain their existing order.

Prepared layouts add an optional reference to AST/call metadata and argument
wrappers, and move some work into compilation. The timing probe excludes that
compilation cost; rules that rarely construct records may not benefit. Layout
references owned by a compiled rule remain live for that rule's lifetime.

Eviction drops cache ownership only. Values and live compiled rules retain their
own layout references, and equality/hashing depend on logical keys and values,
not interner identity. This is a bound on runtime cache retention, not a bound on
the memory an application intentionally keeps in live values or compiled rules.

Python, JS and Lisp retain their fixed small-power tables. Their dynamic powers
of ten are bounded by all three conditions:

- At most 64 entries.
- No cached exponent above 1,000,000.
- The sum of cached exponents is at most 1,048,576, bounding integer payload to
  roughly half a megabyte plus container overhead on these runtimes.

A miss that would exceed the entry or total-size budget clears the generation.
Larger powers are computed exactly and returned without caching. An initial
4,096-exponent cutoff was rejected because repeated large-number work suffered;
the aggregate budget retains useful large powers while bounding total retention.
Exact scale, rounding and SEL range/error checks are unchanged. Workloads cycling
through more distinct schemas or powers than the budget can still incur repeated
construction; no universal throughput improvement is claimed.

The new public-constructor probe also found an existing JS `Value.shaped()` bug:
it passed a `RecordShape` into a helper expecting keys. It now calls the matching
shape-based helper while retaining its defensive copy of the caller's array.
C++ SQL build prerequisites now include `sel_ast.hpp`, preventing stale SQL
objects when the shared AST layout changes.

## Benchmarks

The baseline source snapshot matches commit `619bc31` for all 22 changed runtime
files present in that snapshot. Both variants run the same probes in fresh
processes. [Raw measurements](metadata-cache-benchmark.json) include runtime
versions and five timing samples per lane. C++ uses GCC 16.2.1 with
`-std=c++23 -O2`; the other runtimes are CPython 3.14.7, Node v24.16.0,
PHP 8.5.10 and SBCL 2.6.8.

The steady workload compiles `RECORD("id", X, "name", Y)` once, warms it up,
then runs 20,000 evaluations per sample against the same context. It measures
complete record execution, not just interner lookup. Median microseconds:

| Lane | Before | After |
| --- | ---: | ---: |
| Python | 6.073 | 5.834 |
| JavaScript | 0.855 | 0.579 |
| PHP | 5.409 | 4.885 |
| Common Lisp | 0.750 | 0.600 |
| C++ | 0.731 | 0.698 |

Retention is measured after creating 20,000 distinct two-field layouts, then
running 5,000 joins with changing source keys. Each join's rows/results are
discarded. The table is the retained heap delta from before both workloads,
in decimal MB:

| Lane | Before | After |
| --- | ---: | ---: |
| Python | 25.00 | 0.674 |
| JavaScript | 24.00 | 0.515 |
| PHP | 47.71 | 0.556 |
| Common Lisp | 27.19 | 0.385 |
| C++ | 24.37 | 0.305 |

The power workload requests exponents 10,000 through 11,999 and discards the
results. Its additional retained heap delta falls from 10.59 to 0.098 MB in
Python, 9.18 to 0.030 MB in JS and 9.24 to 0.026 MB in Lisp. The fixed C++/PHP
power tables needed no retention change. Warm lookups at exponents 100 and
10,000 are also measured: Python stays around 0.20 µs; JS varies from roughly
0.07–0.08 to 0.09–0.11 µs; Lisp's approximately 0.01 µs result is near timer
resolution. These tiny timings should not be interpreted as precise speedups.

Python uses `tracemalloc` after collection, JS uses `heapUsed` with explicit GC,
PHP uses `memory_get_usage` after cycle collection, Lisp uses dynamic-space usage
after full GC, and C++ uses glibc `mallinfo2` allocated bytes. These are different
accounting mechanisms, so compare before/after within each lane, not host memory
efficiency across lanes. Memory figures are one fresh-process observation per
variant and include runtime/allocator noise; they are not RSS or exact accounting
of cache objects. The source limits and eviction probes establish the bounds.

## Reproduction

The probes run checks by default; append `bench` for measurements:

```sh
python3 tools/metadata/python.py bench
node --expose-gc tools/metadata/js.mjs bench
php tools/metadata/php.php bench
XDG_CACHE_HOME=/tmp/sel-cache sbcl --script tools/metadata/lisp.lisp bench
make -C cpp build/metadata
cpp/build/metadata bench
```

For the first four, set `SEL_METADATA_ROOT` to a baseline repository root to
measure the same probe against older sources. For C++, compile the probe against
the baseline core and matching headers:

```sh
c++ -std=c++23 -O2 -I/path/to/baseline/cpp tools/metadata/cpp.cpp \
  /path/to/baseline/cpp/sel.cpp -o /tmp/metadata-baseline
/tmp/metadata-baseline bench
```

Run variants sequentially after builds/tests finish. Lisp requires the usual
Quicklisp dependencies; C++ retained-byte benchmarking requires glibc. Its
correctness probe remains portable. The probes are wired into `tools/check.sh`
and the C++ probe is part of the default build.

## Validation

- All five metadata probes verify eviction with old values still live, hash/
  equality stability, oversized-layout bypass, prepared-layout reuse after churn,
  dynamic keys and duplicate-key behavior. Power probes additionally exercise
  both count and size pressure and exact results beyond the cacheable exponent.
- 911 conformance cases per lane and both rebuilt JS bundles; 852 SQL cases per
  lane; 39,990 decimal-oracle cases per lane, all passing.
- 585 Python, 483 Lisp and 167 C++ unit checks; 85 C++ SQL unit checks; 31 PHP
  physical-layout checks; 135 optimizer checks each in JS/PHP. The JS guard's
  14 million-digit boundary checks also pass.
- All 54 host API probes agree across the five lanes. The 4,000-program fuzz
  corpus agrees across JS/PHP/C++/Lisp. Python's outputs match its pre-change
  snapshot byte for byte; the same 11 pre-existing join-alias differences remain.
- The focused C++ eviction probe passes AddressSanitizer and UBSan, with leak
  checking disabled in the sandbox. SQL checks were rebuilt after fixing the
  missing AST-header prerequisite; the earlier stale-object crashes are resolved.

The full repository gate, including mutation and live-database checks, was not
rerun. The existing Python differential-fuzz differences are not claimed fixed.
