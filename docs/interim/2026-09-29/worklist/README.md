# SEL review remediation worklist — 2026-09-29

This worklist covers the six `*-code-review.md` reports in the parent directory: **324 correctness findings and 170 performance findings**, with every finding assigned an owner in [coverage.csv](coverage.csv). It plans the work; unchecked tasks do not imply that fixes or tests have been implemented, or that report claims have been independently re-verified.

There are **427 checkboxes**: 20 shared test/harness tasks, 238 correctness tasks (related findings combined at a common implementation surface), and 169 performance tasks. PHP-P7 shares the identical correctness fix with PHP-C14. Task IDs are stable; report IDs remain the unit of coverage and disposition inside combined tasks.

## 1. Create cross-lane regression coverage

Start with [the test worklist](00-tests.md): eight harness tasks and twelve focused test families. Each family has detailed report-derived scenarios, controls/boundaries, expected-result rules, destinations and six-host completion requirements. Equivalent reports should share fixtures; differences in engine, representation or API remain explicit variants.

The test families cover all six source hosts, plus applicable bundle/package, PHP GMP/no-GMP, sanitizer, race, CLI and database routes. A test that only exercises the reporting implementation does not satisfy this worklist. The default roster also contains JS bundles; Rust was outside these six reviews and is not counted as a seventh remediation lane.

## 2. Fix correctness, one language section at a time

| Language | Reported findings | Grouped tasks | Section |
|---|---:|---:|---|
| JS | 60 | 44 | [JS correctness](01-js-correctness.md) |
| PHP | 58 | 45 | [PHP correctness](02-php-correctness.md) |
| Python | 52 | 41 | [Python correctness](03-python-correctness.md) |
| C++ | 60 | 42 | [C++ correctness](04-cpp-correctness.md) |
| Lisp | 47 | 31 | [Lisp correctness](05-lisp-correctness.md) |
| Go | 47 | 35 | [Go correctness](06-go-correctness.md) |
| **Total** | **324** | **238** | |

Each finding appears in exactly one implementation checkbox. Combined tasks retain separate source targets, fix sketches, test links and evidence/contract gates for their members. Grouping by source concern keeps edits local; a monolithic source file such as `cpp/sel.cpp` is still split by mechanism. A task may require a companion header, generated source definition, specification or fixture change; “one source” is a locality preference, not permission to leave the contract inconsistent.

Report line numbers describe the reviewed tree and will drift. Locate the named symbol on the current tree before editing. Confirm the claim first, especially where the report labels it unconfirmed, latent, platform-dependent or a spec gap. A report’s proposed fix is a starting point, not proof of the desired semantics. For a non-defect or already-fixed finding, record why and retain a suitable invariant test; do not silently delete its row.

## 3. Address performance

[The performance worklist](07-performance.md) defines the measurement protocol, correctness prerequisites and six language queues. All 170 findings, including the bundled minor opportunities, have an explicit disposition path. Each task includes a durable benchmark and evidence of improvement, or a measured reason to defer/reject the experiment. Review timings are historical, not promises.

## Execution order and focus

Work in small cross-host batches: select one contract/source concern, land or extend its shared tests, fix all affected hosts, run the relevant matrix, then close the findings. Language sections organize ownership; they do not authorize leaving a shared semantic change implemented in only one language. Keep one such batch active rather than opening all 427 tasks at once.

| Wave | Focus | Entry points and exit condition |
|---|---|---|
| 0 | Reproduce and make failures observable | T00-A through T00-G as needed for the first batch; baseline the current tree, install missing adapters, capture independent expected answers. |
| 1 | Memory safety, corrupt shared state and silently wrong core results | C++ dangling references/scratchpads/overflow/decimal errors; PHP mutable BOOL/comma copying; Lisp shared shapes and cache-dependent grouping; Go regex false negatives, read-only races and lost hybrid context. Land the relevant T02–T06/T11/T12 regressions first. |
| 2 | Shared language semantics and process-exhaustion paths | T01/T03–T07: depth, iteration, aliasing, total order, optimizer error order, regex, text/collection limits. Close the contract decisions below and run all hosts before closing a batch. |
| 3 | SQL and hybrid soundness | T08–T11: lexical capture, literal/parameter loss, kind guarantees, escaping, expansion, row keys, binders, ordering and context. Require executing server evidence where the claim concerns server behavior. |
| 4 | Remaining API, platform and documentation findings | Complete the low-severity/latent cases in each source area; exercise the declared supported configurations and record justified dispositions. Severity does not exempt a finding from coverage. |
| 5 | Measured performance improvements | Follow the dependency batches in the performance worklist. A bounded, local improvement may accompany its correctness fix; preserve one implementation owner and attach benchmark evidence. |
| 6 | Completion audit | T00-H: complete the ledger, full six-host gate and applicable distribution/platform/server runs. No unchecked scenario hidden inside a checked grouped task. |

## Contract decisions to resolve with their owning batch

These are prerequisites inside the existing tasks, not extra duplicate implementation tasks. Record the chosen rule, spec/documentation change and test oracle in the coverage ledger before pinning a new behavior.

| Decision | Questions to settle | Owning test families |
|---|---|---|
| Iteration and copying | Snapshot versus live overwrite/append; collected/result aliases; mutation during sort, grouping and joins; caller-context behavior | T03, T05, T11 |
| Ordering and collection edges | Total order of numeric/non-numeric text; stable DESC/TOP; scalar/empty iteration; BUCKET collisions and binder signature | T05 |
| Evaluation observability | Evaluate/coerce order; skipped sort keys/counts; optimizer depth/position accounting; multiple pipe placeholders and binder exemption | T01, T04 |
| Resource bounds | Coalescing/interpolation/analysis depth; text/value/join/SQL expansion budgets; safe huge counts; error phase/code and boundary | T01, T06–T09 |
| Regex portability | Empty-match advance, capture reset, anchor/class syntax, legal repeat/group/program limits, compile-time validation and resource failure | T06 |
| Host API contract | Accepted native types, ownership/key order, dependency analysis, source-error positions, thread/process guarantees | T01–T03, T12 |
| SQL guarantees | Lexical scopes, UNKNOWN propagation, count/precision/server ranges, identifier and escape validation, parameter state | T08–T10 |
| Hybrid equivalence | Row keys/order, original LINK names, exception visibility, helper scope, context mutation and fallback criteria | T11 |

Do not invent cap values or error codes solely from a report’s suggestion. Update `spec/`, error/limit/manifest sources and generated consumers where the selected policy requires it. Where the existing spec already decides the outcome, implement it without reopening the decision.

## Completion records

[coverage.csv](coverage.csv) is the initial inventory, with one row per finding, task owner and required test family/benchmark. Extend it with fixture IDs, spec decision, six-lane result links, commit/evidence and disposition while implementing. Test families also explicitly require this per-finding record, so grouping cannot conceal unfinished members.

A correctness task closes only with a reproduced-and-fixed result, documented already-fixed evidence, or a supported non-defect/documentation disposition, plus the applicable regression coverage. A performance task closes according to its measurement protocol. A newly failing host discovered by a shared fixture belongs in the same batch: extend that host’s relevant source task (or add one task for the new finding) and update the ledger, rather than restricting the fixture to the original reporter.

This document set was checked for inventory counts, unique finding ownership and local link/anchor integrity. Runtime tests are work items here; creating the worklist does not execute them.
