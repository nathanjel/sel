# Rust implementation progress

Current worktree evidence, 2026-09-29. The implementation plan and earlier external
status note are not completion evidence. In particular, current spec/limits.json
has newer limits and arithmetic rules than the architectural proposal.

## Changes verified so far

- Hardened build publication and availability checks with production-input
  digests and all 11 required entry points. Failed or concurrently edited
  builds do not publish a new identity; isolated shell tests verify this and
  source/config/new-file/missing-binary detection. Integration-test edits do
  not stale the published host. These checks run in the Rust unit gate.
- CLI argv now preserves OS bytes: invalid UTF-8 expressions report E_UTF8,
  and Unix non-UTF-8 paths can name valid source files. Shared CLI byte/misuse
  fixtures and two focused argv tests pass. Added rust/README.md instructions.
- Included Rust in the static error-code gate. Fixed undocumented hybrid
  wrapper errors: malformed plans return E_BAD_ARG before a query, and
  rendering preserves catalogued SQL errors. Rust covers exactly 34 codes.
- **51 Cargo tests**, isolated build checks, **700 arity / 43 binding probes**,
  and **12 bounded CLI requests** pass. All generated artifacts are current.
  All 11 published binaries match release outputs; repository entry points
  pass **2,103 core / 1,284 SQL cases**. See rust-build-cli-report.json.
  The shared SQL-budget script explicitly skips Rust: its success output is
  not a Rust pass. SQL-fuzz/database-oracle integration remains open.

- Implemented iterative execution of eager call chains and native source-first
  pipelines. Pending stages retain logical depth charges; arguments keep their
  original nodes and cache the source value. Lazy host callbacks and invalid
  pre-source binder checks retain their original evaluation path. Four focused
  tests compare values, errors, mutations and observed depth against recursive
  wrappers. **198/199 stages succeed; 200/220 return matching E_DEPTH positions
  on 256 KiB debug/release stacks**. Contexts remain reusable after failure.
  The current release eval_node frame remains **32 bytes**, including return
  address and excluding callees; assembly/hash evidence is refreshed.
- Fixed exact numeric-text SQL operands in arithmetic, unary operations, MIN
  and MAX. Constant text subexpressions fold to exact numeric literals with
  scale preserved; discarded SQL parameter slots are reclaimed. Tests verify
  slots/bindings and scale across three dialects. Latest gates: **2,103 core,
  1,284 SQL**, including 800 mirrored checks and seven type-system refusals;
  **27 release unit/focused tests pass**. Evidence:
  `rust-pipeline-evaluator-report.json`, `/tmp/sel-rust-pipeline-eval-final-release.txt`,
  `/tmp/sel-rust-pipeline-eval-final-conformance.txt`,
  `/tmp/sel-rust-pipeline-eval-final-sqlt.txt`.
  Broader stack/resource audits and the 550 ms target remain open.

- Removed quadratic copying of obsolete source subtrees while optimizing and
  rebuilding pipelines. Each stage retains its metadata and non-source arguments;
  optimizer rewrites use a placeholder until the source is reconnected once.
  The allocation regression failed before the change (40/80 MAP stages:
  12,680/49,343 allocations), then passed at **780/1,543**. A reconstruction
  check verifies the complete AST, source positions and metadata remain intact.
  All five focused pipeline/cache/hybrid tests pass. Fresh release gates:
  **2,103 core and 1,269 SQL cases pass**. Evidence:
  `rust-pipeline-optimizer-report.json`, `/tmp/sel-rust-pipeline-tests.txt`,
  `/tmp/sel-rust-pipeline-conformance.txt`, `/tmp/sel-rust-pipeline-sqlt.txt`.
  This measures optimizer allocation calls, not Scenario 1 timing. Runtime
  source evaluation was still recursive at this checkpoint; the follow-up
  above implements eligible iterative chains with logical depth preserved.

- Fixed the evaluator's debug stack overflow by outlining recursive dispatch
  handlers and moving arithmetic temporaries into a nonrecursive helper.
  Six deep AST shapes return E_DEPTH on 2 MiB debug / 256 KiB release stacks,
  restore context, and allow reuse; applicable 200-level expressions succeed.
  Removed the dependency test's 16 MiB workaround. Measured x86-64 release
  eval_node frame: **32 bytes**, including return address, excluding callees.
  Evidence: `rust-evaluator-stack-report.json`, `rust-evaluator-frame.asm.txt`,
  `/tmp/sel-rust-stack-debug-final-tests.txt`, and
  `/tmp/sel-rust-stack-release-tests.txt`. Broader stack auditing remains open.
- Fixed record sort keys to follow the first child to a scalar, retaining
  scalar kinds and stable ties. Key resolution follows completion of all key
  bodies so later body mutations are observed. Depth errors propagate even
  for one-row inputs. Both focused tests pass in
  `/tmp/sel-rust-sort-scalar-tests.txt`; all **2,103 core cases pass** in
  `/tmp/sel-rust-stack-final-conformance.txt`.
- SQL BUCKET now refuses sorted input, including nested source subqueries,
  before key validation at the BUCKET call position. All **1,269 SQL cases
  pass**, including 795 mirrored checks and seven explicit type-system
  refusals: `/tmp/sel-rust-bucket-order-sqlt.txt`. The performance target and
  full implementation goal remain open; no new timing claim is made.

- Core API audit found the Rust driver stopped at 86 probes while the shared
  driver had 107. Added the 21 missing probes. `rust-api-contract-report.json`
  records all 107 observations: all **20 applicable pins pass**, with four
  explicitly documented type-system exclusions in tools/api-pins.txt (non-string
  source, unsupported/fractional native input, non-callable registration).
  This is not an all-host parity pass: the current JS report violates pinned
  dependency, invalid-source and out-of-range argument-reader expectations.
  Comparison details: `/tmp/sel-rust-api-flow-comparison.txt`.
- `Program.dependencies()` now tracks definite assignments in execution order,
  retaining reads before assignment and RHS self-reads, intersecting IF/COND
  branch effects, and isolating conditional short-circuit/default/aggregate
  effects. Binding scopes come from the manifest. Aggregate outer parameters
  are analyzed before row bodies; TOP limits precede directions, and BUCKET's
  key phase precedes its projection. Plain indexed assignments vivify their
  root; compound assignments read an existing target. Names remain sorted and
  repeated analyses do not mutate the program.
- Host argument `val` and typed extractors now return E_BAD_ARG for invalid
  indices instead of a bounds panic. Tests include usize::MAX, all eight typed
  readers, repeat failures and call positions. Metadata position lookup falls
  back to the call position so it cannot panic before the reader validates.
- Three dependency tests (ordering/control-flow/binder cases and two depth
  comparisons) plus the typed-reader test pass. Release core: **2,085 passed,
  zero failures/suite errors**. CLI `--deps -e 'IF(X, A = 1, 0); A'` prints A
  and X. Logs: `/tmp/sel-rust-flow-final-tests.txt`,
  `/tmp/sel-rust-flow-conformance.txt`, `/tmp/sel-rust-api-flow.txt`.
- **Historical stack defect (fixed in the subsequent stack audit above):** logical evaluation of a 220-term
  left-associated sum in a debug test overflows the harness's default 2 MiB
  stack before returning E_DEPTH. The dependency analysis itself passes on the
  harness stack. Its error-position comparison evaluates the reference on an
  explicit 16 MiB thread stack; that is not a fix or proof of small-stack safety.
  This workaround has now been removed; the broader stack audit remains open.

- Program reuse: `run_with_context` evaluates the retained physical AST instead
  of cloning it per run, so shape-checked cache updates survive. A regression
  checks current values under the same shape, reordered shapes, transition to
  entry storage, missing fields, recovery after errors, cloned Programs and an
  unchanged logical AST.
- MAP can retain a native RECORD result because RECORD has already copied its
  fields into an unpublished fresh container. It still traverses the result at
  MAP's original copy depth and position. Explicit AST callbacks merely named
  RECORD do not qualify: the bound native function must be the actual RECORD
  implementation. Collector copy failures now explicitly pop MAP's frame, in
  addition to the outer evaluator's existing frame restoration.
- Four focused tests pass: retained cache behavior, record snapshots/ownership,
  depth/context errors, and a custom AST callback returning aliased data. The
  final callback guard was tested in the debug integration run; the initial
  three tests also passed in release. Release core **2,085 passed**, SQL
  **1,265 passed** (791 mirrored checks, 7 expected refusals), no suite errors.
  Logs: `/tmp/sel-rust-retained-record-final-tests.txt`,
  `/tmp/sel-rust-retained-record-conformance.txt`,
  `/tmp/sel-rust-retained-record-sqlt.txt`.
- Performance evidence is mixed across time: the first new run measured
  1,092.56 ms, slower than the historical 935.55 ms. A same-source comparison
  then restored only per-run AST cloning and MAP record copying in an isolated
  temporary crate. Alternating two five-sample batches per variant produced
  **1,008.78 ms baseline vs 987.48 ms current** median (2.11% lower); individual
  paired batch medians were 991.50/954.59 and 1,056.16/1,038.34 ms. Every run
  validated the independent oracle and unchanged input, and the workspace
  binary was restored and hash-checked. Both raw and paired evidence are kept
  in `rust-scenario1-owned-record.json` and `rust-scenario1-record-ab.json`.
  The **550 ms target remains unmet**; do not compare different-time runs as
  proof of a causal speedup or claim the performance requirement complete.

- SQL rendering-mode API: `Mode::from_name` now returns
  `Result<Mode, ParseModeError>` and delegates to `FromStr`; only the exact
  public names `inline`, `params` and `debug` are accepted. Invalid, empty,
  case-modified, padded and NUL-containing names fail instead of silently
  selecting Inline. Explicit `Mode::default()` remains Inline.
- Corrected the SQL API probe so its helper calls the public parser, instead of
  independently rejecting bad names and hiding the library defect. The SQL
  fixture runner uses the same parser. All **60 current SQL API probe outputs
  match JavaScript**, including invalid modes with/without parameter slots,
  canonical flags and repeated guard rejection. All **20 release library
  tests pass**; full SQL: **1,265 passed**, 791 mirrored checks, 7 expected
  refusals, zero failures/suite errors. Logs:
  `/tmp/sel-rust-mode-tests.txt`, `/tmp/sel-rust-mode-sqlapi.txt`,
  `/tmp/sel-js-sqlapi-current.txt`, `/tmp/sel-rust-mode-sqlt.txt`.

- Decimal resource audit: multiplication now checks combined scale with a
  widened sum, then uses the nonzero product's lower digit bound before cloning
  large operands or allocating a result. It retains the exact final guard for
  ambiguous carry boundaries and preserves fractional limits on zero products.
  Small mantissas with scales >=39 no longer allocate a scale-sized divisor for
  integer testing, truncation, floor, ceiling or downward rounding.
- Resource proof: three allocation-counting integration tests pass, including
  million-digit integer/fractional product refusal with no allocation >=1024
  bytes, accepted exact digit boundaries, carry-induced refusal, and zero
  allocations for tiny small mantissas at million-digit scales. All 18 release
  library tests pass. Fresh decimal oracle: 94,040 cases, zero mismatches;
  core: 2,085 passed, zero failures/suite errors. Logs:
  `/tmp/sel-rust-decimal-resource-tests.txt`,
  `/tmp/sel-rust-decimal-resource-oracle.txt`,
  `/tmp/sel-rust-decimal-resource-conformance.txt`.
- The fresh SQL run exposed a shared-map update to SQLite MIN/MAX templates:
  `{numericCast:*}` requires one wrapper per argument, whereas Rust wrapped the
  joined list. Implemented generic `{key:*}` expansion per sql/MAP.md §4.2 and
  removed the older SQLite-only constant-casting workaround. Emitter regression
  verifies original parameter slots/order, empty and 1,001-argument lists,
  binary-cast elision and recursion protection. All 1,265 SQL cases now pass
  again (791 mirrored checks, 7 expected refusals). Logs:
  `/tmp/sel-rust-variadic-cast-tests.txt`,
  `/tmp/sel-rust-variadic-cast-sqlt.txt`.

- Replaced production `BigUint` mantissas with `LargeDec`, a canonical
  little-endian base-10^9 limb vector. Arithmetic includes carry/borrow,
  schoolbook and Karatsuba multiplication, normalized Knuth D division with
  estimate correction/addback, single-limb division and direct powers-of-ten
  scaling/splitting. Decimal sign and scale remain in `Dec`; results demote to
  i128 when they fit. `from_large`/`to_large` replace the dependency-specific
  conversion API. BigUint remains only a development dependency used as an
  independent binary-integer test oracle; normal `cargo tree` confirms it is
  absent from the production dependency graph.
- Digit guards and trailing-zero trimming operate directly on limbs. Decimal
  parsing reads validated integer/fraction slices without joining them into a
  temporary mantissa string, including the i128::MAX boundary.
- Limb verification: 3,006 randomized/threshold operand pairs (including
  10,000-digit multiplication), 144 quotient-estimate correction/addback
  cases, and decimal-shift/canonical-storage boundaries. Full decimal oracle:
  **94,040 cases, 0 mismatches**. Fresh full core: **2,085 passed**, SQL:
  **1,265 passed** (791 mirrored checks, 7 expected refusals), no failures or
  suite errors. Logs: `/tmp/sel-rust-limbs-oracle.txt`,
  `/tmp/sel-rust-limbs-conformance.txt`, `/tmp/sel-rust-limbs-sqlt.txt`.
- Final limb release tests: 18 library tests plus the allocation-counting
  integration test pass. The latter verifies zero allocations for representative
  small parsing (including i128::MAX), add/subtract/multiply/divide/remainder,
  comparison, rounding, floor/ceil/truncation and scale trimming. Log:
  `/tmp/sel-rust-limbs-final-tests.txt`.
- Latest Scenario 1: five runs after one warmup, median **935.55 ms**, minimum
  **922.51 ms**, with all independent-oracle rows and unchanged context verified.
  Artifact: `rust-scenario1-limbs.json`. This improves on the prior 993.34 ms
  median; the required 550 ms target remains unmet.

- Counted-repeat backend fallback (`regex_counter.rs`) replaces the old
  unmatchable placeholder. Only `regex::Error::CompiledTooBig` selects this
  backend; syntax failures stay errors. Repeats retain counters and use
  minimum-length pruning, with an explicit ordered work stack for captures,
  alternation and greedy/lazy behavior. Successful fallback compilations use
  the existing bounded cache. Replacement obtains the actual capture count
  from either backend.
- Counted fallback verification: 261,252 capture/search comparisons with the
  normal regex engine; giant-repeat nonmatches, matching alternatives, Unicode
  spans, case folding, anchors and lazy matches; a positive 600,000-character
  repeated capture. Full core conformance: **2,085 passed, 0 failed, 0 suite
  errors**. SQL: **1,265 passed, 0 failed**, including 791 mirrored checks and
  7 expected type-system refusals. Logs: `/tmp/sel-rust-counter-conformance.txt`
  and `/tmp/sel-rust-counter-sqlt.txt`. Polynomial patterns intentionally remain
  accepted by the shared specification; worst-case fallback resource usage
  still belongs to the open resource audit. Release library tests: 15 passed,
  including end-to-end fallback replacement and invalid capture references;
  all-bin cargo check passes. Unit log: `/tmp/sel-rust-counter-lib-tests.txt`.

- Portable regex validation enforces structural limits, nullable-loop and
  optional-capture rules, quantified-anchor refusal, class range endpoints and
  POSIX bracket rejection. Literal patterns are validated while parsing calls,
  including dead branches. Uppercase `I` is refused.
- Regex matching explicitly resumes after empty matches by one code point and
  permits empty matches abutting nonempty ones. Rust class syntax is escaped
  separately from the portable spelling used by SQL.
- SQL API probe binary builds against the actual public API and preserves typed
  errors in its refusal probes.
- SQL map reset preserves local host functions. The SQL test runner explicitly
  resets host registrations between cases instead.
- Numeric guard checks cache success only, so rejected guards remain rejected.

- Decimal division now rounds half away from zero in both representations.
  Small division and ROUND compare the remainder without overflowing i128.
  CEIL/FLOOR enforce digit limits (their Rust core API now takes a position
  and returns Result). ROUND rejects oversized scales before allocating.
  Small decimal guarding and scale trimming no longer allocate strings;
  the signed constructor safely promotes i128::MIN.
- Math plans retain loaded Value references until the consuming operation,
  preserving evaluate-before-coerce order and later operand mutations. Numeric
  results still use Dec scratch storage; unsafe arithmetic identity elimination
  was removed.
- SORT/TAKE fusion now updates the cached callback to TOP/TOP_DESC/TOP_BY,
  instead of dispatching the rewritten call to its old SORT callback.

- Collectors now deep-copy their results at the required collection points:
  LIST, RECORD, MAP, FILTER, SORT variants, TOP variants, and BUCKET results.
  TAKE/DROP/DISTINCT/DEDUPE retain element aliases. Constructor copies count
  the new outer container; assignment copies count the target path and report
  depth failures at the target node.
- SQL normalization refuses non-name binders before constructing an incomplete
  leaf node. The scope-and-slots panic case now passes, including its mirrored
  dialect (`sqlt shape.binder.non-name-binder-is-refused`).

- SQL aggregate source elements now retain the lexical frame in which they
  were classified. Re-entering a source or reading its indexed children restores
  that scope, preventing inner binders and `_K` from capturing outer references.
  Absorbed FILTER predicates render in separate binder frames; their names no
  longer leak into other predicates or the consuming aggregate body. All shared
  agg.scope and agg.filter cases now pass. Normalized helper hygiene remains incomplete;
  constant-binding shadowing was addressed in the subsequent SUM pass.
- Raw relation correlation conditions are parenthesized in expressions and
  statements so OR conditions cannot change meaning when combined with AND.
- A custom dialect may be replaced with the same parent; changing its parent
  and overwriting shipped dialects remain refused. Replacement clears validated
  numeric-guard state for descendants. All shared re-registration cases pass.

- Bare undeclared SUM bodies now use operand guards for static/column unrolls
  and all-or-nothing guards for relation/group sums. PostgreSQL-derived dialects
  additionally guard the aggregate input cast. Unsupported dialects refuse.
  Group SUM assembly preserves Fragment parameter slots instead of discarding
  every non-SQL part. All warrant.sum and bucket.sum shared cases now pass.
- Constant detection removes active aggregate binder names from application
  constant bindings, preserving lexical shadowing. All const.binder cases pass.
- The compound-SUM skip-NULL issue remains explicitly open in
  docs/internals/sql-kinds.md section 5a; the bare-body guard is not a claim that
  all numeric translation guarantees are complete.

- Conditional result kinds preserve unknown branches while still rejecting
  incompatible known branches. SQL slice counts use decimal whole-number checks,
  clamp to signed 64-bit limits, and merge offsets with saturating arithmetic.
- Aggregate folds retain their existing shape through 256 operands and recursively
  split larger folds into balanced halves, charging each operator once.
- JOIN refuses absorbed FILTER sources and BOOL/BIN elements or separators,
  including empty and singleton sources. IN checks relation needle/column kinds
  and casts numeric items when comparing against exact text columns.
- Empty template tail lists and empty lexical values emit nothing. Grouped SUM
  validates constant bodies numerically before emitting aggregate SQL.

- Dialect registration validates inherited textQuote/textEscape/identQuote as a
  set before publishing a replacement: quote doubling or a self-escaped escape
  character is required. Full map replay still agrees with the shipped map.
- Column bindings refuse LIST types; binding names and relation fields refuse
  ASCII-case collisions instead of silently overwriting entries.
- Program-supplied projection and SELECT_COLS names refuse empty/NUL names.
  PostgreSQL RECORD aliases are checked for collisions after 63-byte truncation
  at UTF-8 character boundaries, reporting the offending key's position.
- Literal and bound scalar TEXT containing NUL is refused before rendering mode
  selection. A regression test covers literal, scalar binding and indexed list
  binding paths on MariaDB, PostgreSQL and SQLite.

- Mixed SQL/local MAP plans retain later sorts, TAKE and DROP in the local
  continuation, so all local expressions run before rows are reordered or cut.
  Projected pairs still pass through by key. Splits after FILTER now require
  a continuation that renumbers before observing retained input keys.
- Hybrid literal inlining and source-table discovery use manifest binder scopes;
  assignment targets are not relation reads, and names rebound by earlier
  statements or callback binders do not count as free relation references.
- Hybrid execution copies caller context for both pure-memory and split plans
  before running assignments or installing `_INPUT`, and propagates failures
  when installing continuation rows. An execution regression compares error code
  and position with run() for TAKE, DROP, and SORT_BY/TAKE after a local MAP.

- JOIN prefix validation restores parameters, parameter kinds and dispatched-node
  count after its discarded render, so preflight does not leave orphan slots or
  charge the same emitted tree twice.
- Derived relation fields retain an explicit unavailable flag for raw expressions;
  row-field reads and scalar-row reads refuse those fields at the reference.
  SELECT_COLS refuses raw fields instead of inventing named columns for them.
- SQLite multi-argument MIN/MAX cast constant arguments to NUMERIC, because SEL
  numeric parameters are text and SQLite extrema compare storage classes without
  numeric coercion. The single-argument spelling stays unchanged.

- SQL normalization alpha-renames explicit binders that overlap helper free
  names or constant bindings before substitution. Nested binder scopes are
  respected, and stale arithmetic plans are cleared from renamed nodes.
  Nested-shadowing regression tests compare against alpha-equivalent controls.
- Hybrid helper unwinding marks the catalogue source reached through a helper
  with the same name. Helper dependency collection and normalization preserve
  that distinction, preventing DROP (or another source step) from applying twice.

- Added SPEC 7.8 ambiguity analysis using regex-syntax's unsimplified AST:
  range normalization/folding before negation, finite unrolling, follow-edge
  multiplicity, fixed-length loop exception, iterative SCCs, pair-graph EDA,
  nullable-choice/finite-choice budgets and all analysis work caps. Captures
  and laziness are transparent to the analysis as specified.
- Literal regex validation runs ambiguity analysis during parsing, including
  dead branches and literal i flags. Runtime compilation and SQL pattern
  rewriting apply the same rule. The regex_verdict binary supports the shared
  differential checker: 4,000 generated patterns in each case mode give zero
  mismatches against the independent reference (seed 1).

- Successful regex compilations now share a process-wide, mutex-protected FIFO
  cache capped at 256 entries, keyed by pattern and case mode. Hits do not refresh
  insertion order; concurrent duplicate inserts do not consume extra slots.
  Flag validation precedes lookup, failures are not cached, and compilation runs
  outside the lock. Tests verify the cap, eviction, flag separation and positions.
- Added rust-completion-audit.md with evidence and remaining proof for all 50
  numbered plan items. Generator freshness passes for the current worktree;
  cache verification passes 10 unit tests, all-bin check, and 4,000 additional
  case-insensitive reference patterns (zero mismatches). Targeted regex suites
  remain 223 passed / 2 known counted-repeat engine failures.
  Logs: /tmp/sel-rust-cache-tests.txt, /tmp/sel-rust-cache-diff.txt,
  /tmp/sel-rust-cache-regex.txt, /tmp/sel-rust-generated-audit.txt.

- Added a Scenario 1 release benchmark binary plus a Python validation/report
  wrapper using the shared query and 10x dataset. An independent Decimal-based
  join/group calculation matches all 10 checked-in reference rows and Rust's
  results. Every validation/warmup/measured run returns the same result and
  preserves the prepared context hash. Dataset loading, compilation, hashing
  and oracle verification stay outside execution/materialization timing.
- Scenario 1: five measured runs after one warmup, median 4525.46 ms, minimum
  4515.88 ms; the <=550 ms target is NOT met. Materialization is about 0.03 ms.
  Reproducible report: rust-scenario1-baseline.json. The new driver builds in
  release mode and passes cargo check. Profiling and optimization remain.

- Scenario 1 prefix measurements identified intermediate FILTER copies of wide
  joined rows (and their destruction) as a major cost. Physical optimization now
  elides that intermediate copy only for FILTER immediately followed by MAP,
  with both bodies proven read-only using a conservative builtin whitelist.
  FILTER still completes before MAP and checks selected-row copy depth at the
  original position; MAP still copies its final result. Mutations and host calls
  disable the optimization. Regression tests compare unoptimized evaluation,
  source mutation, retained keys and depth-error ordering.
- Validated Scenario 1 median is now 1223.79 ms (minimum 1205.85 ms), versus the
  baseline 4525.46 ms: 3.70x faster, but still above the 550 ms target. Five samples,
  one warmup, unchanged input hash, identical independent Decimal-oracle rows.
  Report: rust-scenario1-copy-elision.json. Prefix diagnostics are in
  /tmp/sel-rust-stage-profile.txt (separately optimized prefixes, not additive
  timings of the full execution).
- Verification after copy elision: 2,083 core cases pass with the same two regex
  engine-size failures; all 1,265 SQL cases pass; 17 unit/integration tests and
  all-bin cargo check pass. Logs: /tmp/sel-rust-conformance-copy-elision.txt,
  /tmp/sel-rust-sqlt-copy-elision.txt, /tmp/sel-rust-copy-elision-tests-all.txt.

- Join row alias construction now caches one layout per join side instead of
  rebuilding field names, hash maps and shapes for each row. Values are always
  loaded afresh; row references, alias collisions and shape-to-unshaped mutation
  retain the previous behavior. Regression tests compare the cached path with
  the original path across these mutations. This alone reduced the Scenario 1
  median from 1223.79 to 1070.75 ms (rust-scenario1-alias-layout.json).
- Read-only copy elision also covers FILTER followed by FILTER, with unchanged
  key preservation and complete predicate/depth checking before the next stage.
  Release builds now use ThinLTO and one code-generation unit, retaining panic
  unwinding. Combined validated Scenario 1 median: 993.34 ms, minimum 987.90 ms,
  five samples after one warmup. Results and unchanged context match the oracle;
  the 550 ms target is still NOT met. Report: rust-scenario1-alias-lto.json.
- All 18 unit/integration tests passed before the profile change. The ThinLTO
  release build then passed all 11 library tests, 1,265 SQL cases (791 mirrored),
  and 2,083 core cases with the same two regex engine-size failures. All-bin
  cargo check passed. Logs: /tmp/sel-rust-join-alias-tests-all.txt,
  /tmp/sel-rust-lto-lib-tests.txt, /tmp/sel-rust-sqlt-alias-lto.txt and
  /tmp/sel-rust-conformance-alias-lto.txt.

## Verification and remaining work

- `cargo test --manifest-path rust/Cargo.toml --lib`: 6 passed.
- `cargo check --manifest-path rust/Cargo.toml --bins`: passed.
- Decimal oracles: 94,040 cases, zero mismatches; independent oracle self-check:
  39,990 records, zero disagreements. All 314 decimal boundary cases pass.
- SQL case generation check: current, 1,265 cases.
- Release conformance, all current suites: **2,083 passed, 2 failed**.
  Output: /tmp/sel-rust-conformance-ambiguity-final.txt. Compared with the earlier
  2,041 / 44 baseline, 42 failures are resolved without new failures. The remaining
  portability and ambiguity fixtures both exercise (?:a{60000}){60000}, which the
  matching engine refuses at its compiled-size limit despite valid SEL syntax.
- Ambiguity differential: 8,000 patterns (4,000 per case mode, seed 1), zero
  mismatches. Logs: /tmp/sel-rust-ambiguity-diff.txt and
  /tmp/sel-rust-ambiguity-diff-i.txt. The shared ambiguity suite passes 115/116.
- Latest SQL after regex integration: 1,265 passed, 0 failed, 0 suite errors
  (791 mirrored checks; 7 type-system refusals counted as passes).
  Output: /tmp/sel-rust-sqlt-ambiguity.txt.
- Latest Rust verification: 8 unit tests and 5 integration tests passed;
  all-bin cargo check passed. Output: /tmp/sel-rust-ambiguity-final-tests.txt.
  All 75 ownership cases now pass, as do decimal boundaries, evaluation order
  and relational edge suites.
- Regex portability improved from **63 passed / 46 failed** to **108 / 1**.
  The remaining failure is nested counted-repeat program size. The current
  engine also still contains a resource-error-to-unmatchable fallback; that is
  not a conforming general solution and must be replaced. Ambiguity analysis
  is now implemented and differentially checked; bounded FIFO caching is implemented.
- All 60 SQL API probe outputs produced. 58 match the existing C++ binary;
  `guard.reuse.2` and `.3` correctly report `refused` in Rust, as explicitly
  required by tools/check-sqlapi.sh, whereas that C++ binary reports `accepted`.
  After SQL reset, the local host function still returns `local:A`.
- Latest full SQL release run: **1,265 passed, 0 failed, 0 suite errors**
  (791 mirrored-dialect checks; 7 type-system refusals counted as passes).
  Output: /tmp/sel-rust-sqlt-hygiene.txt. The final three shared helper failures
  are resolved without regressions. This proves the current shared SQL fixture
  suite, not full implementation completion or all SQL semantic guarantees.
  All SQL runs from this checkpoint are terminal.
- Latest release tests: 6 unit tests, 1 SQL budget test, 1 literal test,
  2 hybrid execution/context tests and 1 nested helper hygiene test pass.
  All-bin cargo check passes. Output: /tmp/sel-rust-hygiene-tests.txt.
- SQL literal regression: 1 passed, covering three input paths on three dialects.
  Release unit tests: 6 passed; SQL budget integration: 1 passed (1.01 seconds).
  All-bin cargo check passed. Registration map replay: 254 calls, 649 lookups,
  zero differences; disagreeing numericGuard still refused.
- SQL depth/size fixtures: 8 passed, including 8 mirrored-dialect checks.
  Normalization checks expanded helper depth and size before cloning/evaluation;
  dispatch and unrolled folds enforce MAX_SQL_NODES. Normalized nodes share
  immutable source metadata through Arc. Scope/hygiene defects remain separate.
- `cargo test --release --manifest-path rust/Cargo.toml --test sql_budget`:
  accepts and renders 200,001 nodes; refuses 300,001 with E_SQL_SIZE; passed
  in 0.88 seconds on the latest verification. The old performance run was explicitly terminated after
  identifying whole-dialect cloning in each parent lookup, then rerun with the
  fix. Lookups now copy only requested metadata, without caching stale overrides.
- Unicode template emission now copies full UTF-8 characters in both positional
  and named templates. Both previously panicking shared fixtures pass.
- Map replay: 254 registration calls, 649 lookups, zero differences; disagreement
  in numericGuard is refused. `cargo check --bins` passes.
- Full conformance details: /tmp/sel-rust-conformance.txt. Regex portability
  details: /tmp/sel-rust-regex-after.txt. SQL API outputs:
  /tmp/sel-rust-sqlapi.txt. These temporary logs are local evidence, not fixtures.

The full 50-item plan, generated metadata checks, current SQL fixtures, public
API, stack/resource safety and scenario-1
performance target still need a requirement-by-requirement completion audit.
The goal remains active.
