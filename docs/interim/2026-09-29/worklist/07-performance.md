# Performance work

[Worklist](README.md) · [Shared regression tests](00-tests.md)

All **170 performance findings** are covered below: **169 performance tasks**, plus PHP-P7 owned by the identical correctness task C-PHP-C14. Each language page retains the finding’s workload/evidence, source target, proposed experiment and measurement requirements. Bundled “smaller items” retain every subitem; do not silently omit them when closing the parent task.

| Language | Findings | Performance tasks | Queue |
|---|---:|---:|---|
| JS | 28 | 28 | [JS performance](performance/js.md) |
| PHP | 30 | 29 | [PHP performance](performance/php.md) |
| Python | 31 | 31 | [Python performance](performance/python.md) |
| C++ | 25 | 25 | [C++ performance](performance/cpp.md) |
| Lisp | 27 | 27 | [Lisp performance](performance/lisp.md) |
| Go | 29 | 29 | [Go performance](performance/go.md) |

## Measurement and completion protocol

Each checkbox includes creating a durable benchmark case, measuring the baseline, making or rejecting the proposed change, and recording the decision. Use the existing language runtime/collection/scale/commit-benchmark tooling where it fits. Historical review timings and scratchpad prototypes are leads, not acceptance results.

Use a fixed seed and semantic checksum. Record revision, runtime/compiler/options, host, warm-up, repetitions, median and variability, elapsed time and allocations/peak or retained memory. Separate compile, optimize, execute, translate, render and database time. Compare on the same machine/configuration; include n/2n/4n to establish growth, ordinary workloads to expose overhead, cache hits/misses and cold/warm reuse. Run portable workload inputs through all six hosts, even if only one implementation changes. Set a regression threshold after measuring noise; do not turn historical absolute timings into flaky CI limits.

Require exact results, error precedence/positions, key order, aliasing and final-context parity before accepting a speedup. Validate byte-identical SQL when that is the intended optimization; if SQL structure changes, update the contract-backed snapshots and execute the statement. Shared caches must be bounded, invalidated on registration/reset, and safe under supported concurrency. Internal “owned/unchecked” constructors must not weaken public input validation or introduce mutable literal/flyweight leakage.

A task may close as implemented, already addressed by a linked correctness commit, or measured deferral/rejection. A deferral must state the evidence, reason and reconsideration condition. Reasoned/guessed entries start with a profile; they are not instructions to apply an unmeasured micro-optimization. Documentation-only advice, such as Go GC tuning, needs a checked explanation of the tradeoff rather than a library-wide GC setting.

## Dependency batches

| Batch | Prior correctness/tests | Performance work unlocked |
|---|---|---|
| Text and codecs | T01/T07 UTF-8, offsets, errors and output budgets | JS-P1/P5/P6/P7/P27; PHP-P1/P2/P19; PY-P11/P12; CPP-P9/P19/P22; LISP-P18/P21; GO-P15/P20/P23 |
| Sorting and grouping | T03/T05 total order, scalar/empty cases, mutation and copying | JS-P8/P13; PHP-P3/P26; PY-P1/P15; CPP-P10/P11/P12; LISP-P4/P7; Go sort/TOP tasks |
| Numeric core | T02 independent oracle, native overflow, exact rounding and caps | All decimal arithmetic/parse/guard performance findings; PHP both GMP configurations |
| Values and interpreter | T03/T04 lifetime, error order, plan equivalence and restoration | Literal sharing, unchecked internal constructors, copy elision, scratchpad/dispatch/Args optimizations |
| Joins and records | T03/T05 cache identity, key semantics, aliases and evaluation order | Shape interning, join-plan reuse, equi/residual extraction, right-row alias reuse, record construction |
| Regex | T06 portable captures/empty matches, errors and resource policy | Pattern caching, direct match/search paths, replacement parsing, avoiding subject copies |
| Front end | T01 exact token/position/depth behavior | Operator dispatch, interpolation scanning, token/AST allocation and parse-copy work |
| SQL rendering | T08/T09/T10 lexical scope, slots, guards and expansion budgets | Pairwise folds, constant memoization, template parsing, escape/chain caches and bindings-copy removal |
| Hybrid | T11 keys, context mutation, errors and helper scope | Context-copy reduction, prefix search, helper bookkeeping, unsupported-node analysis |

Fix correctness-related complexity attacks in the correctness batch; attach their benchmark evidence there and cross-link the relevant performance task. Avoid two competing source edits for the same mechanism. Once semantic prerequisites pass, work one source area and one measurable hypothesis at a time.
