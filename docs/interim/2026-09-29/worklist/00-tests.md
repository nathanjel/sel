# Shared regression work

[Worklist](README.md)

Write each portable scenario once and run it against **JS, PHP, Python, C++, Lisp and Go**, including lanes where it passes today. Report provenance is not test ownership. The twelve linked families contain the individual repro variants and proposed expectations for all 324 correctness findings. Existing fixtures may satisfy a requirement: extend or reference them rather than duplicating source under another name.

## Harness tasks

- [x] **T00-A — Establish the coverage ledger and current baseline.** Record each report ID, shared fixture IDs, expected result/error/phase and spec citation, test route, six host results, implementation task, and resolution evidence. Use `pass`, `fail`, `blocked` or justified `N/A`; unavailable runtimes/servers are blocked, never passes. Preserve the review’s confirmed/unconfirmed status until reproduced. Audit the existing `23-runtime-regressions.selt` and Go race tests first: earlier fixed Go findings are background, not new repair tasks. Report current-tree behavior separately from the review’s historical observations.

  Done 2026-09-30 — coverage.csv ledger: every finding with task, required test, status, fixtures, decision and evidence columns (ledger script in the session scratch; 323/324 correctness closed, PHP-C26 deferred; 170 perf rows dispositioned).

- [x] **T00-B — Run the shared evaluator fixtures through every execution model.** Add test-only adapters where needed to run plain AST, logical/physical optimizer and math-plan-enabled/disabled paths on the same source. Compare ordered value dumps, errors including positions and phase, final contexts and host-function side effects. Preserve the logical AST and test repeated reuse. Extend existing optimizer probes rather than exposing a new production API solely for tests. Include source JS plus rebuilt bundles, PHP GMP/no-GMP, and the packaged Python lane when exercised by the release gate.

  Done 2026-09-30 — tools/check-eval-equivalence.{mjs,py,php} over all conformance sources (in tools/check.sh) + host hooks: C++ unit test_plain_vs_optimised, Go TestPlainAndOptimisedEvaluationAgree, Lisp plain-and-optimised-evaluation-agree; JS bundles and PHP GMP/no-GMP run the same corpus.

- [x] **T00-C — Extend equivalent public API probes across all six bindings.** Use `tools/api.*`, `python/bin/api.py`, host runtime tests and their C++/Lisp/Go counterparts. Cover constructor/registration misuse, native conversion, returned-value mutation, repeated programs and contexts, dependencies and error handling. Assert the same observable contract with host-specific inputs; PHP flyweights, Python pickling or Go backing arrays need native adapters, not fabricated SEL syntax. Add missing adapters to the gate. Do not require identical human-readable messages unless the public contract explicitly promises them.

  Done 2026-09-30 — tools/api.* probes (114, identical source on all six drivers) pinned in tools/api-pins.txt, compared by tools/check-api-compare.py in tools/check-api.sh.

- [x] **T00-D — Pair SQL snapshots with executing assertions.** Use `.sqlt` for exact SQL, refusals, positions, parameter order, dialect registration and plan shape; use `tools/sqlapi.*` for API state. Extend the existing SQL oracle/usage infrastructure to execute the semantic repros on SQLite and the relevant MariaDB/MySQL/PostgreSQL server. Cross inline/params/debug rendering where supported. Compare database results and hybrid continuations with SEL using explicit input/order contracts. A planner classification test cannot prove execution equivalence; translation agreement cannot prove server validity. Record server versions and relevant SQL modes/collations.

  Done 2026-09-30 — sql/oracle hybrid/rows/expressions lanes on MariaDB 11.8, MySQL 8.4, PostgreSQL 17, SQLite (php/bin/sqlo), tools/check-hybrid-parity*.{mjs,py} + host drivers, tools/check-sql-limits.php; .sqlt reuse twin.

- [x] **T00-E — Add bounded adversarial and platform runs.** Generate boundary and large fixtures deterministically, outside the small golden corpus when appropriate. Run stack, regex, allocation and expansion probes in child processes with wall-time and memory ceilings; preserve exit status/stderr and distinguish an expected SEL error from timeout/signal/OOM. Use a small reproducer in the ordinary gate, larger scaling probes in the stress lane. Pin error positions only after defining the accounting rule. Do not commit enormous expanded fixtures or execute the review’s unbounded commands verbatim.

  Done 2026-09-30 — tools/check-budgets.sh, tools/check-regex-resources.py, tools/check-sql-budgets.sh (child processes, wall/memory ceilings), tools/stress.sh new shapes (coalesce, interpolation, pipeline).

- [ ] **T00-F — Add race, lifetime and ambient-state coverage.** Run C++ ASan/UBSan for dangling pointers, scratchpad growth and integer overflow; run TSan separately for registry/SQL caches. Run Go tests with `-race` on shared read-only values as well as shared programs. Exercise threaded Lisp caches and Python overlapping GC suppression, process pools and integer conversion limits. Test two unrelated clients in one long-lived process and successful reuse after caught errors. Include PHP supported-version/locale and GMP/no-GMP checks, and an LLP64 C++ job for width claims where support is intended. Map each platform-only probe to its portable invariant in the ledger.

  Partly done 2026-09-30 — C++ asan + tsan, tsan-registry, tsan-regex targets; Go go test -race incl. shared-value tests; Python GC/pickle/pool and Lisp threaded-cache tests; PHP 8.1 lane tools/check-php-version.sh (GMP-less) and GMP/no-GMP runs. PARTIAL: the LLP64 C++ job is not available on this box — blocked, not passed.

- [x] **T00-G — Repair independent oracles and generated-contract checks.** Extend the decimal oracle with exact rational/integer reference logic independent of SEL’s division/modulo implementation, large magnitudes and limit boundaries. Check generated limit/manifest consumers against their sources; error codes and compile/run phases against the catalogue. Validate documentation-only corrections with executable examples or metadata assertions where meaningful. This infrastructure task supplies the checks for C-PY-C29 and related finding tasks; it is not a second owner for their resolution.

  Done 2026-09-30 — tools/decimal-oracle-exact.py (exact rational oracle) in tools/check-decimal.sh; gen-limits/gen-builtins/manifest checks (tools/check-generated.sh, check-manifest.sh, check-error-codes.sh incl. Go).

- [ ] **T00-H — Wire and verify the full matrix.** Make each new fixture route part of the appropriate check/stress job. Run `SEL_IMPLS="js php python cpp lisp go" tools/check.sh` with all six available, then the default gate including built JS bundles and applicable packaging/platform checks. Inspect the roster printed by the gate; no missing host may silently count as covered. Store a concise final matrix and explicit unresolved platform/server runs. A family is complete only when its ledger rows and all required executions are complete.

## Test families

Each family is one coordinated test-creation task with detailed per-finding scenarios. Deduplicate equivalent reports into shared fixtures, but preserve differing engine/API variants. Work on a small coherent slice of a family at a time; report-ID completion rows provide the finer progress tracking.

| Task | Detailed scope |
|---|---|
| [T01](tests/01-frontend.md) | Front end, depth, interpolation, pipelines, source positions |
| [T02](tests/02-decimal.md) | Exact decimal arithmetic, constructors, numeric limits |
| [T03](tests/03-values.md) | Copy/alias boundaries, shapes, caches, native conversion |
| [T04](tests/04-evaluation.md) | Evaluation order, optimizer equivalence, scratchpads, recovery |
| [T05](tests/05-relational.md) | Mutation during iteration, total order, grouping, join paths |
| [T06](tests/06-regex.md) | Regex semantics, validator gaps, engines, resource failures |
| [T07](tests/07-text-binary.md) | Text/binary boundaries, output and collection budgets |
| [T08](tests/08-sql-scope.md) | SQL lexical scope, literal slots, normalization, expansion |
| [T09](tests/09-sql-kinds.md) | SQL kind guarantees, exact comparison, counts, server limits |
| [T10](tests/10-sql-rendering.md) | Bindings, dialect registration, escaping, fragments |
| [T11](tests/11-hybrid.md) | Executed hybrid parity: keys, binders, ordering, context |
| [T12](tests/12-integration.md) | Host API, races, CLI, ambient process state, tooling |

## Expected-result discipline

The scenario sheets preserve report repros, including **currently wrong results**, and label them as observations. Do not copy those results into golden tests. Derive the answer from the current spec or the recorded contract decision. For unsettled behavior, first land the explicit rule and then the test with one expected answer. An assertion that hosts merely agree is insufficient: all six can share a bug.

For each distinct scenario include the minimal failing example, a nearby valid control and the boundary/representation variants named in the sheet. Values require exact ordered dumps; exceptions require code and specified position/phase; aliasing requires mutation and final-context assertions; resource behavior requires bounded completion. Metamorphic checks (sort idempotence, TOP equivalence, fast/general join equivalence) supplement exact expected examples.
