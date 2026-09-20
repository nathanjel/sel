# Common Lisp argument and assignment traversal — 2026-09-20

Argument wrappers now convert call-node argument lists to simple vectors once
per invocation. `args-node` uses `svref`, and `args-count` reads vector length.
The AST retains its existing list representation. The separate value cache still
evaluates each argument at most once, and lazy builtins still choose which
arguments to evaluate.

`walk-create` traverses keys sequentially instead of repeatedly using `nth`.
`resolve-target` retains the forward path, its tail and its length, appending one
fresh cons per key instead of copying every growing prefix with `append`.
It still walks from the root after each non-final index expression has run:
those expressions can replace containers resolved earlier. `eval-assign` still
re-resolves the parent after the right-hand side runs. Compound assignment still
reads its old value before evaluating that right-hand side.

For a path of length n, a single walk now performs O(n) list traversal instead
of O(n²). Repeated prefix resolution retains an O(n²) component, replacing the
previous O(n³) traversal component. Path-list construction allocates O(n) conses
instead of O(n²). These statements exclude the cost of evaluating index
expressions and looking up fields in each container. The 200-level depth limit
and the requirement to re-resolve after side effects remain unchanged.

## Benchmark

Measured with SBCL 2.6.8-1.fc44 on the local Linux host. The baseline is a copy of
the Lisp lane immediately before these edits, including prior structural-hash
corrections. The same script runs against either tree:

```sh
XDG_CACHE_HOME=/tmp/sel-bench-cache sbcl --script tools/benchmark-lisp-traversal.lisp
SEL_LISP_BENCH_ROOT=/path/to/baseline/lisp/ XDG_CACHE_HOME=/tmp/sel-bench-cache \
  sbcl --script tools/benchmark-lisp-traversal.lisp
```

Quicklisp must already provide the normal SEL dependencies. Each workload warms
up ten times, then records five samples with a full GC before each sample.
Runtime timing excludes system loading, parsing and preparation. The script
measures bytes consed with SBCL's allocator counter; these are approximate
allocated bytes per operation, not retained heap size. Raw samples are in
[lisp-traversal-benchmark.json](lisp-traversal-benchmark.json).

Median microseconds per operation:

| Operation | Size | Before | After |
| --- | ---: | ---: | ---: |
| Indexed sweep over prepared arguments | 10 | 0.200 | 0.130 |
| Indexed sweep over prepared arguments | 100 | 7.50 | 1.00 |
| Indexed sweep over prepared arguments | 1,000 | 739.01 | 10.00 |
| `COALESCE` through variable arguments | 2 | 0.340 | 0.360 |
| `COALESCE` through variable arguments | 100 | 17.00 | 11.00 |
| `COALESCE` through variable arguments | 1,000 | 838.01 | 104.00 |
| Single populated path walk | 10 | 0.60 | 0.50 |
| Single populated path walk | 100 | 11.50 | 4.50 |
| Single populated path walk | 199 | 37.30 | 8.90 |
| Resolve assignment target | 10 | 4.50 | 3.50 |
| Resolve assignment target | 100 | 530.00 | 230.00 |
| Resolve assignment target | 199 | 2970.02 | 870.01 |
| Execute assignment | 10 | 5.50 | 4.50 |
| Execute assignment | 100 | 540.00 | 235.00 |
| Execute assignment | 199 | 3010.03 | 910.01 |

The variadic expression uses null-valued variables followed by a non-null
variable so constant folding cannot remove the argument walk. Path benchmarks
reuse a populated context with one child per level; full assignments evaluate
`A["k"]... = X` using a compiled program. A 199-key path has 198 index nodes.
These tests isolate the identified costs, not general application throughput.
Small timings are limited by timer resolution and should not be read as precise
nanosecond comparisons.

The argument vector has a cost: constructing a 1,000-argument wrapper rises
from 5.3 to 7.5 µs and approximately 8,061 to 16,077 allocated bytes. The full
1,000-argument COALESCE still improves about 8×. A two-argument COALESCE allocates
about 32 additional bytes and shows a small timing regression in this sample.
This implementation favors predictable indexed access while preserving the AST
and per-call cache behavior; it does not claim allocation savings for calls.

Path resolution at 199 keys allocates approximately 340,284 bytes before and
25,210 bytes after, a 93% reduction. Full assignment allocates approximately
340,276 versus 25,201 bytes. The sequential `walk-create` itself allocates no
bytes in the populated-path workload in either version.

## Validation

- 483 Lisp unit checks pass, including argument caching, lazy evaluation,
  1,000-argument access, bounded prefix walking and both sides of the depth limit.
- New assignment checks replace an earlier ancestor inside an index expression,
  count index/RHS effects, replace the root on the RHS, exercise compound
  assignment and stop at an index error. The same 483 checks also pass against
  the pre-change snapshot, verifying preservation of existing behavior.
- 911 conformance cases and 852 SQL translation cases pass.
- All 4,000 differential programs and 54 host API probes agree with JavaScript.
- 39,990 decimal-oracle cases pass with zero mismatches.

The full repository gate was not rerun; earlier unrelated Python join-alias
differences remain documented in the changelog.
