# Rust completion audit — in progress

2026-09-29. This is an evidence ledger, not a completion declaration. Original
proposal checkboxes and the external status note are not proof. Current SPEC,
limits.json, builtins.json and shipped dialects supersede obsolete proposal
limits, rounding, function names, ownership rules and fixture counts.

Latest shared gates (2026-09-30): core 2,128/2,128; SQL 1,304/1,304; the full
`SEL_IMPLS="js rust" tools/check.sh` gate — see the 2026-09-30 section below. The
full objective remains active. In addition to rows below, verify the named layout/build artifacts,
public API and CLI contracts, all repository gates, broader small-stack operation, bounded resources, decimal representation and
performance. Existing third-party dependencies must be reconciled with the
proposal's dependency goals rather than silently declaring them satisfied.

| Item | Requirement | Assessment | Current evidence / next proof |
|---|---|---|---|
| 1.1 | Initialize Rust Cargo crate | Verified | rust/Cargo.toml: package sel-lang 0.9.2, edition 2021. The core library depends on `regex` only (`cargo tree --no-default-features -e normal`); `serde`/`serde_json` come only with the default-on `sql` feature, which gates `sel_lang::sql` and the SQL tools, mirroring the opt-in SQL layer of the other hosts. `cargo test --no-default-features` passes. |
| 1.2 | Extend `tools/gen-limits.mjs` for Rust | Verified generation | tools/gen-limits.mjs emits rust/src/limits.rs; check-generated.sh passes. |
| 1.3 | Extend `tools/gen-builtins.mjs` for Rust | Verified generation | tools/gen-builtins.mjs emits manifest/builtins.rs; check-generated.sh passes. |
| 1.4 | Extend `tools/gen-math-ops.mjs` for Rust | Verified generation | tools/gen-math-ops.mjs emits math_ops.rs; check-generated.sh passes. |
| 1.5 | Extend `tools/gen-sql-map.mjs` for Rust | Verified generation | tools/gen-sql-map.mjs emits map_data.rs and replay data; check-generated.sh passes. Use current shipped dialects, not the obsolete plan list. |
| 1.6 | Extend `tools/gen-sql-cases.mjs` for Rust | Verified generation | tools/gen-sql-cases.mjs emits bin/sqlt/case_data.rs; check-generated.sh passes. |
| 1.7 | Update `tools/check-generated.sh` | Verified | tools/check-generated.sh includes all five Rust artifact groups and passes on this worktree. |
| 1.8 | Update `tools/impls.sh` | Partial | Published binaries are checked against content digests of all production sources, Cargo files and build scripts; all 11 entry points are required. Failed/concurrently changed builds preserve the prior publication. Shared CLI/manifest/budget gates and full Cargo tests pass. SQL-fuzz/oracle drivers and full repository gate coverage remain open; the shared SQL-budget script explicitly skips Rust. |
| 2.1 | Implement `Pos` and UTF-8 decoder in `rust/src/utf8.rs` | Needs focused audit | Shared CLI byte-position and operand-misuse probes pass. Unix argv now preserves bytes: malformed expression arguments report E_UTF8 at the code-point position and non-UTF-8 filenames work. Focused argv tests pass. REPL byte/error behavior and broader API audit remain open. |
| 2.2 | Implement portable regex compiler in `rust/src/regex.rs` | Behavior verified; resource audit open | regex.rs plus regex_ambiguity.rs pass 8,000 reference comparisons. Counted-repeat fallback preserves counters, captures and ordering; 261,252 engine comparisons and a 600,000-character positive match pass. Core conformance now 2,103/2,103. Polynomial backtracking resource behavior remains to audit. |
| 2.3 | Define `Dec` and `DecRepr` in `rust/src/dec.rs` | Verified representation | dec.rs has Small(i128) and Large(Box<LargeDec>); LargeDec stores canonical little-endian base-10^9 limbs, exposed read-only to preserve invariants. |
| 2.4 | Implement fast-path 128-bit arithmetic | Partial | Small arithmetic/promotion pass 94,040 oracle cases. Thread-local allocator test proves zero allocations for representative parsing (including i128::MAX), arithmetic, comparison, rounding and truncation. Allocation tests also cover million-digit-scale small mantissas and pre-allocation refusal of million-digit products, including scale/zero/carry boundaries. Broader resource audit remains. |
| 2.5 | Implement exact division and rounding | Verified to current spec | Current DIV_SCALE=10, half-away division/ROUND; 94,040 oracle cases and 314 boundary fixtures pass. Proposal values are superseded. |
| 2.6 | Implement multi-precision limb fallback | Implemented and oracle-verified | large_dec.rs implements base-10^9 add/subtract, schoolbook/Karatsuba multiplication, normalized Knuth D division, and direct decimal scaling. Production dependency tree has no num-bigint/num-traits; BigUint is a test-only oracle. Randomized limb tests and all 94,040 decimal oracle cases pass. Million-digit multiplication preflight is allocation-tested; worst-case accepted multiplication/division resource audit remains open. |
| 2.7 | Implement canonical number formatting and parsing | Behavior verified | Decimal oracle and boundary fixtures pass; code audit for all resource bounds remains. |
| 2.8 | Unit test decimal arithmetic against oracles | Verified recorded run | 94,040 oracle cases, zero mismatches; 39,990 oracle self-check records. See rust-progress.md. |
| 3.1 | Implement `RecordShape` | Partial | shape.rs has Arc<[String]>, key_map and bounded interning. Shape lifetime/performance audit remains. |
| 3.2 | Implement `Value` and `ValueInner` | Partial | value.rs uses Rc<RefCell<ValueInner>>; ownership suite passes. Full public mutation/error audit remains. |
| 3.3 | Implement `SlotCache` container | Implemented | ast.rs SlotCache stores shape_id and slot. Execution proof is item 5.2. |
| 3.4 | Implement canonical `dump()`, `structural_hash()`, and `eql()` | Needs focused audit | Core API driver now covers 107 probes and satisfies all 20 applicable shared pins, with four documented type-system exclusions; current JS parity fails its pinned dependency/error behavior. Focused dump/hash/equality audit remains. |
| 3.5 | Implement `deep_copy(depth, pos)` | Behavior verified | All 75 ownership cases pass after correcting collector and assignment copy depths; stack-bound audit remains. |
| 3.6 | Implement AST `Node` and `NT` enum in `rust/src/ast.rs` | Implemented | ast.rs Node has source position, children, slot cache, math plan and shape; code inspected. |
| 3.7 | Implement Lexer and Parser in `rust/src/parser.rs` | Verified, bounded stacks | The lexer is an explicit task stack with memoised brace ends (JS-C10): 100,000 nested interpolations lex in 0.4 s and report the parser's E_DEPTH at 1:101 (it overflowed the stack at ~15,000). The recursive descent now passes boxed nodes and builds them in out-of-line helpers; programs nested past MAX_DEPTH (parentheses, interpolation, unary, calls) answer E_DEPTH at JS's positions on a 256 KiB stack in release and 2 MiB in debug (before: ~1 MiB release, 4 MiB debug), and near-limit valid programs compile and run there (tests/parser_stack.rs). |
| 3.8 | Implement pipeline unwinding | Implemented; bounded-stack behavior verified | Optimizer unwinds/reconnects stages iteratively without cloning obsolete input subtrees. MAP-chain allocation counts scale from 780 at 40 stages to 1543 at 80 (previously 12680/49343); reconstruction preserves metadata. Runtime eager call chains and native source-first pipeline chains now use a bounded pending-stage vector, retaining logical depth costs. 198/199 stages succeed and 200/220 report the recursive reference E_DEPTH position on 256 KiB debug/release stacks. Lazy host callbacks retain control; invalid binder precedence, mutation ordering, frame cleanup and callback-observed depth are tested. Parser/destruction resource bounds remain separate open audits. |
| 4.1 | Implement `Context` and recursion depth guard | Partial | Current MAX_DEPTH=200 supersedes 1000. Fixed the debug logical-evaluation stack overflow by outlining dispatch and arithmetic. Six recursive AST chains return E_DEPTH on 2 MiB debug / 256 KiB release stacks, restore context, and permit reuse. Current x86-64 release eval_node frame is 32 bytes including return address (excludes callees); see rust-evaluator-stack-report.json. Pipeline chains also pass on 256 KiB debug/release stacks. Broader parser/optimizer/destruction stack audit remains. |
| 4.2 | Implement `Args` helper | Verified (host contract) | `node`, `pos_of` and `is_symbol` now answer E_BAD_ARG past the argument count like the readers (they panicked or returned the call position); built-ins use crate-private unchecked twins. tests/host_arguments.rs covers all eleven accessors. Earlier note: | All eight typed readers reject invalid indices with E_BAD_ARG, including usize::MAX, without a host panic; call positions and reuse tested. Other Args metadata/lazy-reference contracts still need audit. |
| 4.3 | Implement `MathPlan` compiler and bytecode executor | Partial | MathPlan retains loaded Value references through consuming operation; ordering fixtures pass. Allocation/stack/performance audit remains. |
| 4.4 | Implement core builtins (`rust/src/builtins/core.rs`) | Behavior verified | Core builtin fixtures pass; current manifest names supersede obsolete examples in the plan. |
| 4.5 | Implement math builtins (`rust/src/builtins/math.rs`) | Behavior verified | Math fixtures and decimal boundaries pass; proposed builtin names/rounding are superseded by manifest/spec. |
| 4.6 | Implement text & bitwise builtins (`rust/src/builtins/text.rs`, `bitwise.rs`) | Behavior verified | Text/binary/bitwise fixtures and shared CLI byte probes pass. All 12 bounded CLI resource requests return E_RANGE within the 20-second ceiling; broader worst-case resource audit remains open. |
| 4.7 | Implement regex builtins (`rust/src/builtins/regex_ops.rs`) | Behavior verified | Regex operations, bounded FIFO cache and counted-repeat fallback pass current conformance. Capture/replacement and engine differential tests pass; worst-case resource audit remains. |
| 4.8 | Implement registry manifest verification | Verified shared behavior gate | Rust passes 700 accepted-arity probes and 43 binding-form probes against spec/builtins.json. Generated manifest check also passes. |
| 5.1 | Implement AST optimizer (`rust/src/optimizer.rs`) | Partial | optimizer.rs and corrected SORT/TAKE dispatch pass fixtures; transformation-by-transformation audit remains. |
| 5.2 | Implement monomorphic inline slot cache execution | Behavior verified | eval_index reads current storage only after matching shape IDs. Program retains its physical tree; tests verify cached slot/shape persistence, changed values, reordered shapes, entry-storage transition, missing fields and cloned Programs. Overall performance remains item 7.4. |
| 5.3 | Implement `leading_field_conjuncts` extraction | Verified | FILTER over a LINK builds its stage with `leading_field_conjuncts` (conjunct identity = address in the program tree), hands stages/obligations down, and evaluates only the conjuncts the join did not apply (none left: the join's list is the result), as Go/JS. |
| 5.4 | Implement `rowPlan` for join shapes | Needs focused audit | join_plan.rs exists; shape mapping and unmatched-row behavior need direct inspection. |
| 5.5 | Implement unrolled join projectors for `LINK` / `LINK_LEFT` | Needs focused audit | LINK/LINK_LEFT fixtures pass; exact specialized projector architecture and allocations not yet verified. |
| 5.6 | Implement `JoinPrefilter` execution in `do_link` | Verified | `do_link` settles the prefilter (keys-safe check, stage walk, left/right early tests, raising tests keep the row, observed keys kept as positions, deep hand-down through LINK/FILTER sources); pipelines no longer run a FILTER-over-LINK as a pre-evaluated stage. Join-filter oracle 6 seeds x 1,500 pairs, join-rows oracle 2,000, fuzz 4 x 4,000: 0 disagreements; tests/join_prefilter.rs (6). One case knowingly differs: when both LINK sources raise and a MAP follows the FILTER, Rust reports the left source's error as written (spec §7.4) while JS/Go/C++/Python/PHP report the right one; reported to the other session. Scenario 1 ~11-13% faster in paired runs. |
| 5.7 | Implement optimized `BUCKET` | Needs focused audit | BUCKET fixtures pass; key allocation elision and COUNT/SUM fast paths not yet verified. |
| 5.8 | Implement collection operations | Behavior verified | Current collection/relational fixtures pass after ownership and fusion fixes; full optimization audit remains. |
| 6.1 | Implement `SqlError`, `SqlKind`, and `Fragment` | Partial | All 1284 SQL fixtures pass. Public Mode parsing is now fallible and strict; the probe uses it instead of a separate validator. All 60 current SQL API probe outputs match JavaScript, including canonical flags and guard reuse; broader API code audit remains. |
| 6.2 | Implement `DialectMap` loader in `rust/src/sql/map.rs` | Verified shared gates | Map replay: 254 registrations, 649 matching lookups. Generated map current; lexical registration fixtures pass. |
| 6.3 | Implement Stage 1 AST normalization & lowering | Partial | All shared normalization/helper fixtures pass; implicit binder hygiene and broader semantic guarantees still need audit. |
| 6.4 | Implement Stage 2 SQL translator | Partial | 1284 SQL fixtures pass, including 800 mirrored checks. Compound-SUM skip-NULL issue remains open in sql-kinds.md. |
| 6.5 | Implement `plan_hybrid` | Partial | All shared hybrid plan fixtures pass. Real-database hybrid parity driver not yet integrated for Rust. |
| 6.6 | Implement `execute_hybrid` | Partial | Hybrid error/position and caller-context integration tests pass; full real-server parity remains. |
| 7.1 | Implement `conformance` binary (`rust/src/bin/conformance.rs`) | Verified shared gate | Current driver passes all 2103 cases, with zero failures or suite errors. Stale proposal test count is not the target. |
| 7.2 | Implement `map_replay` binary (`rust/src/bin/map_replay.rs`) | Verified shared gate | map_replay passes 254 registration calls/649 lookups; see recorded run. |
| 7.3 | Implement `sqlt` binary (`rust/src/bin/sqlt.rs`) | Verified shared gate | sqlt passes all 1284 current fixtures; 7 type-system refusals counted as passes must remain explicit. |
| 7.4 | Benchmark Scenario 1 on `dataset-10x.json` | Met (paired) | After the value-representation work (rust-performance-plan.md §7, branch feature/rust-host), Scenario 1 runs 490.9 ms against 655.7 ms for the frozen baseline, paired 0.739x over 8 rounds (0.715-0.843; 642.4 -> 475.4 ms, 0.747x at load 2-3 on the build before the last two commits), rows validated against the independent Decimal oracle. Against its peer, C++ 0.9.2 built outside the repo, the baseline trailed (1.028x) and the final build is ahead (0.762x at load 4-6). The absolute 550 ms of the proposal is reached on this box: 475-491 ms. |

## 2026-09-30: remediation parity, integration, gate

The other hosts' review remediation (commits 2458e5f, a0c2744, 6eeec03, 480a98e
and the working tree) was mapped hunk by hunk against Rust by three read-only
audits (core, builtins, SQL), each divergence proven with a JS-vs-Rust probe
before it was fixed. Found and fixed in Rust:

- **Core:** `--deps` missed a read of the root in an indexed assignment's key
  (`A[A] = 1`); FILTER over a scalar or record that keeps nothing returned NULL
  instead of an empty list (it now always answers a list container, as JS);
  the interpolation lexer was recursive and quadratic (stack overflow at ~15,000
  levels) and is now an explicit task stack with memoised brace ends; host `Args`
  `node`/`pos_of`/`is_symbol` panicked or silently fell back past the argument
  count (now E_BAD_ARG); `Value::num` accepted a non-canonical `Dec` (sign in the
  mantissa, negative zero, over the caps) and is now checked (`Result`), with a
  crate-private `num_trusted` for the evaluator's own results.
- **Regex:** the structure checker stepped by bytes, so a quantifier after a
  multi-byte literal bound to its last byte and `(?:é?)+` escaped the
  nullable-loop rule; POSIX forms `[:`, `[.`, `[=` are refused anywhere in a
  class, terminated or not (the peer's new rule, conformance/31).
- **SQL (nine families):** `orderDropped` (sort-then-projection, LINK over
  sorted rows, the final refusal at the last step; FILTER after a sort no longer
  wraps); hybrid continuations fed under the source name wherever a 3-argument
  LINK falls, self-joins not split; `execute_hybrid` dropped a record context
  (`is_none` vs `is_null`); NULL element in a value binding; `textEscape` pairing
  rules in `define_dialect`; STATEMENT column type; E_SQL_SIZE/E_SQL_DEPTH per
  definition at the assignment; IN-relation operand position and binder check at
  the call; key-read plan shape; unknown collation/prefilter spellings;
  case-insensitive relation aliases; `""` as a constant index key; and an
  O(n^2) free-name collection (tools/check-sql-budgets.sh chain-20000: 62 s to
  0.8 s; JS 0.4 s).

`--deps` now walks a binding form's arguments in written order, each inner
argument with its own definite set, as the six other hosts do: Rust alone had
omitted A for `TOP(L, A, (A = 1; 1))` (limit read before keys) and for SORT_BY /
BUCKET equivalents. A seven-host comparison of the Rust dependency tests found
one JS-only over-report (`A[(K = 1)][K] = B; K`), reported to the other session.

Shared fixtures: conformance/30-empty-results-and-code-point-atoms.selt (8
cases, passing on all seven hosts); 15 SQL case proposals handed to the other
session, which folded them into sql/cases/52 with every host run. Rust unit
tests added: tests/host_values.rs (canonical host decimals, digit caps, 100,000
nested interpolations, lexical error precedence), three more `Args` accessors in
tests/host_arguments.rs.

**Evidence (published build, current tree):** conformance 2,128/2,128; sqlt
1,304/1,304; map_replay 0 differences; `SEL_IMPLS="js rust" tools/check.sh`: every
layer green except one stale in-crate regex test, fixed afterwards (`impl_unit
rust` exit 0; `cargo test` debug, release and `--no-default-features` exit 0).
That run includes differential fuzz (4,000 programs, 0 disagreements), fuzz-sql
against Docker MariaDB/MySQL/PostgreSQL/SQLite (2,000 programs x 4, 0
disagreements), SQL oracle and mutations, join-then-filter-as-written (4 x 1,500
pairs, 0 disagreements), host API 110/110 (27 pinned), SQL API 60/60,
documentation examples, decimal oracle, e2e, CLI bytes, manifest, error codes,
budgets and SQL budgets. Before integration, 16,000 extra core fuzz programs and
5,000 SQL programs x 4 dialects agreed with JS.

**Performance (7.4):** the evaluator no longer allocates a key string per
element per stage (`Value::elems`), FILTER keeps preserved keys as source
positions (`ListKeys::Index`, binary-searched), binder frames are small vectors
rebound in place (no SipHash, no per-row `String`), SORT/TOP push their frame
once, and `ValueInner` shrank from 240 to 192 bytes. Paired on this box with the
independent Decimal oracle validating every run: Rust 712-721 ms vs C++ (the
current cpp/build/scale-bench) 1,131-1,149 ms at load 5-9, a ratio of ~0.63
(earlier today 820-890 vs 1,200-1,260 before the later changes). The absolute
550 ms target is **unverified**: the plan's C++ 576 ms is not reproducible on
this box under the concurrent load, so a quiet-box run is still owed.

The join prefilter (5.3/5.6) was then ported from Go (see those rows), and a
third `SEL_IMPLS="js rust" tools/check.sh` run passed every Rust layer (unit,
conformance 2,128, sqlt 1,304, api 110, sqlapi 60, fuzz 4,000, fuzz-sql 2,000 x 4
real databases, join oracles, docs, e2e, budgets). Its only two failures were
JS-only layers broken by the other session's in-flight JS edits (a stale
mutation anchor in js/src/sql/constants.mjs, JS metadata).

Wiring the prefilter first made FILTER-over-LINK pipelines recursive through
large frames (do_link 2,664 B, fn_filter 1,496 B): 99+ stage `LINK .> FILTER`
chains overflowed a 256 KiB release stack where the previous build answered
correctly. Both are now thin wrappers (152 B each) around out-of-line
plan/hand-down/body phases, ~1.76 KB per LINK+FILTER pair: 40-220-stage chains
give JS's values, E_NO_KEY/E_DEPTH codes and positions on 256 KiB release and
2 MiB debug (tests/evaluator_stack.rs). All lanes re-run after the fix: a fourth
`SEL_IMPLS="js rust"` gate passed every Rust layer; its two failures are again
the other session's JS-only layers (stale mutation anchor in
js/src/sql/constants.mjs, JS metadata).

**Performance follow-up (2026-09-30):** interleaved runs showed the 576 ms C++
reference era (faff480) at 650-810 ms and current C++ at 1,150-1,720 ms on this
box, with Rust at 670-780 ms: 5-12% behind C++ 0.9.2 because of its value
representation (224 B cells, no small-string storage, owned-String text reads,
literal operands and booleans built into cells). Implemented on
feature/rust-host per [rust-performance-plan.md](rust-performance-plan.md):
immutable inline/shared text (`SelStr`), a 104 B `ValueInner` (was 192), literal
RECORD keys never evaluated, comparisons reading literals in place and FILTER
taking booleans without cells. Allocations per 10x run 2.28 M -> 1.06 M, bytes
280 -> 135 MB; Scenario 1 paired 0.739x of the baseline; results and the steps
tried and dropped are in the plan's section 7.

**Still open:** no `examples/<cat>/` worked-example lane for Rust; a Rust-only 4 x MAX_SQL_NODES copy guard in stage 1 (Rust copies helper text where
JS shares it); `cargo test --no-default-features` is not in the shared
`impl_unit`; and Rust is not in the default roster (tools/impls.sh line 25): the
other session's user keeps rust/ out of the repository history, so the default
gate must not depend on it. Run Rust with an explicit `SEL_IMPLS` until the
user decides.

Checks run for this audit: `tools/check-generated.sh` passed (all hosts' generated
artifacts current); `cargo test --release --manifest-path rust/Cargo.toml --lib`
passed 10 tests including FIFO eviction, flag separation and error positions.
Logs: /tmp/sel-rust-generated-audit.txt and /tmp/sel-rust-cache-tests.txt.

Latest decimal replacement verification: 18 release library tests and the
allocation-counting integration test pass; core 2085/2085, SQL 1265/1265, decimal
oracle 94,040/94,040. Logs: /tmp/sel-rust-limbs-final-tests.txt,
/tmp/sel-rust-limbs-conformance.txt, /tmp/sel-rust-limbs-sqlt.txt and
/tmp/sel-rust-limbs-oracle.txt. All release binaries built for the integration
run. The full goal remains active.

Resource follow-up: 18 release library tests plus three allocation tests pass;
94,040 decimal oracle cases and 2,085 core cases pass. The updated shared SQLite
map exposed missing generic `{key:*}` template expansion; it is now implemented,
with a focused emitter test passing and all 1,265 SQL fixtures passing again.
Evidence: rust-progress.md and /tmp/sel-rust-decimal-resource-*.txt,
/tmp/sel-rust-variadic-cast-*.txt. The full goal remains active.

SQL API follow-up: strict `Mode::from_name`/`FromStr` and exported
`ParseModeError` replace silent fallback. All 60 SQL API observations match the
current JS host; 20 release unit tests and all 1265 SQL fixtures pass. Evidence:
/tmp/sel-rust-mode-tests.txt, /tmp/sel-rust-mode-sqlapi.txt,
/tmp/sel-js-sqlapi-current.txt and /tmp/sel-rust-mode-sqlt.txt.

Program/cache and native RECORD collection follow-up: four focused integration
tests pass, with fresh full core 2085/2085 and SQL 1265/1265. Retaining a native
RECORD still checks original collection depth and excludes custom AST callbacks.
Paired performance measurements are recorded above; the full goal remains active.

Core API follow-up: dependencies use definite-assignment flow and manifest
binder scopes; the previously missing 21 probes are present. All 20 applicable
pins pass with four statically unrepresentable-input exclusions documented in
tools/api-pins.txt. Fresh core: 2085/2085. See rust-api-contract-report.json and
rust-progress.md. Current cross-host parity is not green. The debug evaluator stack overflow
identified during this follow-up is fixed in the subsequent stack audit below.


Evaluator stack and scalar-order follow-up: recursive evaluation now outlines
nonrecursive arithmetic and dispatch handlers. Six 220-level AST chains return
E_DEPTH and restore context on 2 MiB debug / 256 KiB release stacks; valid
200-level chains also pass where applicable. Dependency error-position tests
now use the normal harness stack. Current x86-64 release eval_node reserves no
local stack and saves three registers: 32 bytes including the return address,
excluding callees. See rust-evaluator-stack-report.json and
rust-evaluator-frame.asm.txt; this is not a whole-program stack bound.

SORT_BY/TOP_BY now resolve record keys through their first child, preserving
scalar kind and stable ties. Resolution happens after all key bodies execute,
so later mutations remain observable; single-element inputs still validate
scalar depth. Both focused regression tests pass. SQL BUCKET rejects sorting
in its source or nested source subqueries before validating the key, at the
BUCKET call position. Fresh core: 2103/2103; SQL: 1269/1269, including 795
mirrored checks and seven explicit type-system refusals. Logs:
/tmp/sel-rust-stack-final-conformance.txt,
/tmp/sel-rust-bucket-order-sqlt.txt,
/tmp/sel-rust-sort-scalar-tests.txt,
/tmp/sel-rust-stack-debug-final-tests.txt and
/tmp/sel-rust-stack-release-tests.txt. Performance was not remeasured.


Pipeline optimizer follow-up: removed cloning of replaced source subtrees in
build_pipeline and the optimizer's stage extraction. Non-source arguments and
all stage metadata are retained. An allocation-growth regression demonstrably
fails before the fix and passes afterward; full AST reconstruction and existing
hybrid/context regressions pass. Fresh release core: 2103/2103; SQL: 1269/1269.
See rust-pipeline-optimizer-report.json and /tmp/sel-rust-pipeline-*.txt.
At this checkpoint runtime source evaluation was still recursive. The subsequent
follow-up below implements iterative eligible call chains; neither checkpoint
establishes a whole-program stack bound.


Runtime pipeline follow-up: eval_pipeline unwinds eligible eager calls and
known native source-first pipeline calls into a bounded pending-stage vector,
evaluates the source once, and reconnects values through Args caches. Logical
depth remains charged until each stage returns; errors restore entry depth.
Invalid pre-source binders and lazy host callbacks use their original call path.
Four focused tests compare values/errors/mutations and callback-observed depth
against recursive wrappers; 198/199 stages succeed and 200/220 fail at matching
positions on 256 KiB debug/release stacks. The measured release eval_node frame
remains 32 bytes including return address, excluding callees; artifacts refreshed.

New shared SQL fixtures exposed quoted numeric-text arithmetic. Arithmetic,
unary numeric operations, MIN and MAX now emit exact numeric literals for
constant TEXT operands, preserving decimal scale. Folded text-expression slots
are reclaimed before emitting the numeric slot; a focused test covers remaining
parameter order/bindings across MariaDB, PostgreSQL and SQLite. Fresh gates:
2103 core / 1284 SQL pass (800 mirrored checks, seven type-system refusals),
plus 20 release unit tests and seven focused release tests. Evidence:
rust-pipeline-evaluator-report.json, /tmp/sel-rust-pipeline-eval-final-*.txt.
No Scenario 1 performance claim or whole-program stack bound is made.


Build/CLI integration follow-up: production-input digests replace the unreliable
newest-binary timestamp check. All 11 entry points must exist. Isolated shell
regressions cover edits to production/config/new files, test-file independence,
missing artifacts, compilation failure and concurrent input edits. Build
publication preserves its prior identity on failure. These checks now run with
the Rust unit gate. All 51 Cargo tests pass; all 11 published binaries hash-match
target/release. Shared entry points pass 2103 core / 1284 SQL cases.

Shared CLI source/misuse fixtures, 700 arity + 43 binder probes, and 12 bounded
CLI requests pass. Rust is now included in the error-code scanner: it exposed
undocumented hybrid wrapper codes. Malformed plans now return E_BAD_ARG before
querying; SQL rendering preserves catalogued code/message/position. All 34
catalogued codes are represented, with none uncatalogued. Generated artifacts
are current. See rust-build-cli-report.json and /tmp/sel-rust-build-*.txt,
/tmp/sel-rust-full-unit-gate.txt, /tmp/sel-rust-cli-*.txt. The shared SQL-budget
script explicitly skips Rust, so its nominal success is NOT Rust evidence;
SQL-fuzz/oracle integration remains open. rust/README.md documents the workflow.
