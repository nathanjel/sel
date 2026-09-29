# Review work-list — 2026-09-25

Consolidated from the two external review outputs in this folder. Every item started as an unverified claim; each has since been **validated** (same day) and carries a verdict, and every row a status with its evidence. Fixing started the same day, in the order given in the summary below. See **Fix progress** for where it stands (nothing is committed yet).

Tracker (live status per row, same IDs): https://claude.ai/artifact/7fMBYHgtFv818npEfchARH. This file describes each item; the tracker holds status and validation notes.

| Source | File | Scope |
|---|---|---|
| A | `code_review_sel_implementations.md` | Dead, unused and duplicated code across the five hosts |
| B | `collected_inputs.txt` | Correctness findings per host (Python, JS, PHP, C++, Lisp) and a test-suite review |

**62 findings, 277 check rows.** A finding is one defect class; a row is one place to check it (a host, the spec, a test suite, or a tool). Findings reported in one host get sweep rows for the others, since a defect ported from one host usually exists in its siblings.

**Validation progress:** 62/62 findings have a verdict; rows: REFUTED 97 · N/A 10 · FIXED 168 · WON'T FIX 2

## Fix progress (2026-09-25, uncommitted working tree)

**The list is cleared.** Every row is fixed, refuted, n/a or won't fix: rows 168 fixed, 97 refuted, 10 n/a, 2 won't fix.

- **Tests and oracles first:** TEST-01…06 and 08…10.
- **Correctness:** every SEM, SQL and HOST fix, plus DOC-03, on all five hosts, with spec text where the language changed (SPEC §8 boundary rules, §7.4 `TOP_BY` signature). The widened SQL fuzzer found two more Lisp translator defects (SQL-03, SQL-04); both are fixed and pinned by `.sqlt` cases. After the SEM-07 guard, 5 SQL mutations survived (`filter-swap-ignores-keys`); the plan case `plan.fallthrough.filter-at-the-end-stays-over-a-map-that-cannot-raise` now catches all of them.
- **Hygiene:** HYG-01/02/05…19/21, DOC-01 and SEM-11 are done. HYG-15, the JS `aliasCache` removal, went out with the 0.9.0 bump. The JS `BP_SEQ`/`BP_LIST` constants are kept on purpose (every host declares them, with a comment).
- **Won't fix:**
  - HYG-22: a process note.
  - TEST-07: the million-digit tests are already fast and required.
- **Version 0.9.0:** the version is bumped and the CHANGELOG entry written (2026-09-28). It is not tagged or published.
- **Gate:** the final full `tools/check.sh` run after the hygiene batch is **ALL GREEN** on every roster entry (js, js-bundle, js-bundle-min, php, cpp, lisp, python), including the DSN-backed database layers, the fuzzers and the oracles (871 s).
- **User-visible changes:**
  - Lisp `FALSE` ↔ `:false`.
  - `toNative` raises E_BAD_ARG when a scalar collides with a field named `"_"`.
  - Host constructors raise E_UTF8 / E_RANGE on invalid input.
  - Hybrid plans no longer move MAP or FILTER steps across steps that can raise, so some plans that were pure SQL are now split (e.g. REPEAT in a MAP).

## Validation summary (2026-09-25)

Every finding has a verdict and every row a status: 54 of the 60 findings confirmed (fully or in part), 6 refuted; rows 166 confirmed, 97 refuted, 10 n/a. Two findings were added during validation (SEM-12, DOC-03), and several confirmed findings turned out wider than reported. Each finding's section below has its verdict, evidence and exact locations.

### Worse than reported: unanimous defects

The fuzzers cannot see these, because all five hosts give the same wrong answer. They need `.selt` cases with asserted values.

- **SEM-06: the equijoin fast path disagrees with the comparator on every host.** Tested against an `IF`-wrapped control:
  - BIN `$==` TEXT matches nothing on all five.
  - BIN/BIN matches nothing on four hosts; Python gets it right.
  - `"bad" == 1` returns zero rows instead of E_NOT_NUM on all five.
  - BOOL keys return zero rows (four hosts) or a match (Python) instead of E_NOT_BIN.
- **SEM-07 and SEM-08: optimizer rewrites hide errors on every host.**
  - SORT/MAP → FILTER → a renumbering step returns a value where the written order raises.
  - MAP moved after TOP_BY skips E_DIV_ZERO.
  - Fusing FILTER+FILTER reports the second filter's error first.
  - PHP is correct for one form (`TOP_BY` with an explicit binder and a direction) only because its optimizer misreads that form (HYG-05). That is a live divergence between hosts.
- **SQL-01: a LINK's left explicit binder resolves to the right table** in JS, PHP, Python and C++, and so does a FILTER through it. Lisp is right, so the translators already disagree, and no `.sqlt` case notices.

### New defects found while validating

- **SEM-12:** Python assignment shares nested scalar leaves (`value.py:526-527` returns `self` instead of a copy). After `B = A`, `B[1]["k"] = "v"` changes `A`.
- **SEM-05:** JS and Python accept the direction `"deſc"` as `DESC` (full Unicode uppercasing). Lisp also merges `é`/`É` field names in joined rows.
- **HOST-01:** in JS, PHP and Python, `toNative` loses a value's own scalar when a child is named `"_"`. Lisp emits a duplicate `"_"` key instead.
- **HOST-05:** all five hosts accept an invalid-UTF-8 record key.
- **HOST-07:** C++ `DISTINCT` never hashes and compares every pair (O(n²)), so an over-deep value is never walked.
- **HYG-21:** the translator's `in_having` flag is written but never read on any of the five hosts. There are also unused JS imports and helpers, and one uncalled Lisp helper.
- **DOC-03:** the `TOP_BY` signature in SPEC §7.4 and the builtins manifest contradicts every host and the suite.

### Refuted, or wrong as proposed

- **HYG-03/04:** the text-direction branch in `do_sort`/`do_top` decides which form a call has, so deleting it changes behaviour. The suite pins this for `SORT_BY` only, not for `TOP_BY`.
- **HYG-20:** the Python probe imports are deliberate.
- **SEM-10:** no PHP key-coercion site beyond SEM-09.
- **DOC-02:** no misplaced doc comment beyond DOC-01.
- **TEST-11:** the "minimal case first" rule is already written.
- **SEM-11:** real, but can't be observed; a cosmetic fix.

### Suggested order of work

1. **Spec text:** DOC-03, SEM-07's missing rule on keeping errors, HOST-02/04/05/08/09's host contract (§8).
2. **`.selt`/`.sqlt` cases:** TEST-01 and TEST-02, starting with the unanimous defects above. The ten SQL probe cases are saved next to this file as `sql-probes-SQL-01-02.sqlt`.
3. **Host fixes, by risk:**
   - SEM-01/HOST-10 and SEM-12 (Python);
   - SEM-06, SEM-07/08;
   - SQL-01/02;
   - SEM-04/05, SEM-02, SEM-09.
4. **Host-API assertions (TEST-06), then the HOST fixes.**
5. **Oracles and generators:** TEST-03/04/05/08/09/10, and TEST-07's slow lane.
6. **Hygiene** in the reviewer's order, using the verdicts above. Skip HYG-03/04/20. Gate every step with `tools/check.sh`, not `check-manifest.sh` alone (HYG-22).

## Legend

Row origin:

| Mark | Origin | Meaning |
|---|---|---|
| R | reported | The reviewer names this host, usually with `file:line` (93 rows) |
| P | repro | The reviewer says they reproduced it here but gives no location (3) |
| C | control | The reviewer says this host behaves correctly; confirm it and use it as the reference (6) |
| s | sweep | Not reported here; check whether the same defect exists (93) |
| d | derived | Added while consolidating: a broader sweep, a spec question, or a test the finding implies (82) |

Row scopes beyond the five hosts: `spec` (does the normative text decide this?), `suite` (conformance `.selt` / `.sqlt` case), `sql` (translator-wide or hybrid planner), `tool` (oracles, generators, gate).

Tracker statuses: `open` → `confirmed` / `refuted` / `n/a` (the construct does not exist in this host) → `fixed` / `wontfix`. On host rows CONFIRMED means the defect exists there. On `spec` / `suite` / `tool` rows CONFIRMED means the gap is real (text or test missing or wrong) and REFUTED means it is already covered.

Validation method: every behavioural claim was run on all five hosts from the working tree at commit 7611b00 (C++ binary built from that source) (REPLs `-e`, host APIs); every code claim was read at the cited line. Validation changed no repository file except this one.

## Working rules

- Spec first, then conformance cases, then every host, then `tools/check.sh` ALL GREEN (CLAUDE.md). A confirmed SEM/SQL finding gets its `.selt`/`.sqlt` case before any host changes.
- Error cases: pin the code first, then the position once the normative position is confirmed (B §1).
- Hygiene items (HYG) are behaviour-neutral by claim only; validate each premise (e.g. "only assign nodes have `target`") before deleting code, and keep the hosts file-for-file alike.
- Suggested order from the reviewers:
  - B: implement small .selt/.sqlt cases first, then host API assertions, then the two-relation SQL oracle and focused generators.
  - A: Task 1 (dead statements, unused imports/helpers) → Task 2 (optimizer dead branches) → Task 3 (builtin dedup, Lisp do-link) → Task 4 (evaluator and binding dedup, JoinPlan).

## Index

| ID | Sev. | Finding | JS | PHP | Py | C++ | Lisp | Other | Verdict |
|---|---|---|:-:|:-:|:-:|:-:|:-:|---|---|
| [SEM-01](#sem-01) | High | Computed record index returns a stale cached field | s | s | R | s | s | suite:d | CONFIRMED (Python only) |
| [SEM-02](#sem-02) | Medium | TOP_BY renumbers preserved sparse list keys when supplying _K | C | s | R | s | s | suite:d | CONFIRMED (Python only) |
| [SEM-03](#sem-03) | Medium | _K on sparse inputs in the other binder-supplying builtins | d | d | d | d | d | suite:d | CONFIRMED only as SEM-02 |
| [SEM-04](#sem-04) | Medium | Joined-row field promotion folds Unicode case (ß vs SS) | R | C | P | s | s | spec:d suite:d | CONFIRMED (JS, Python, Lisp) |
| [SEM-05](#sem-05) | Medium | Native Unicode case operations anywhere the language defines case | d | d | d | d | d |  | CONFIRMED (JS, Python, Lisp; C++ latent) |
| [SEM-06](#sem-06) | High | Equijoin fast key path disagrees with the comparator | s | s | s | R | s | suite:d | CONFIRMED (all 5 hosts; broader than reported) |
| [SEM-07](#sem-07) | High | FILTER pushdown ahead of MAP/SORT hides earlier errors | P | s | s | R | s | spec:d suite:d sql:s | CONFIRMED (all 5 hosts) |
| [SEM-08](#sem-08) | High | Every optimizer rewrite must preserve errors of the stages it moves or skips | d | d | d | d | d | sql:d | CONFIRMED (2 more unsafe rewrites, all 5 hosts) |
| [SEM-09](#sem-09) | High | FILTER on records casts keys to int before deciding to preserve them | C | R | s | s | s | suite:d | CONFIRMED (PHP only; broader spellings) |
| [SEM-10](#sem-10) | High | PHP array-key coercion wherever SEL keys become PHP array keys | · | d | · | · | · |  | REFUTED beyond SEM-09 |
| [SEM-11](#sem-11) | — | LINK first-row probe tests the wrong side (Lisp do-link) | s | s | s | s | R |  | CONFIRMED as a typo; not observable |
| [SEM-12](#sem-12) | High | Python assignment shares nested scalar leaves (copy returns self) | d | d | d | d | d | suite:d | CONFIRMED (Python only; new) |
| [SQL-01](#sql-01) | High | LINK binder resolves to the wrong relation in SQL | R | R | R | R | R | suite:d sql:d | CONFIRMED (JS, PHP, Python, C++); Lisp correct |
| [SQL-02](#sql-02) | Medium | Unicode case folding creates a SQL qualifier that does not exist in memory | s | C | R | s | s | suite:d | CONFIRMED (JS, Python) |
| [SQL-03](#sql-03) | Low | Lisp reports a LINK key refusal before an earlier step's | · | · | · | · | R | suite:d | CONFIRMED (new, Lisp only) |
| [SQL-04](#sql-04) | Medium | Lisp renders a qualified read of a field the relation lacks as a column | · | · | · | · | R | suite:d | CONFIRMED (new, Lisp only) |
| [HOST-01](#host-01) | High | toNative() loses an own "__proto__" field | Rd | s | s | s | s |  | CONFIRMED (JS) + reserved-key "_" collision in JS, PHP, Python, Lisp |
| [HOST-02](#host-02) | High | Values retain caller-owned buffers and strings | R | s | s | s | R | spec:d | CONFIRMED (JS, Lisp) |
| [HOST-03](#host-03) | Medium | toNative() output shares structure with the Value | s | s | s | s | R |  | CONFIRMED (JS, Lisp) |
| [HOST-04](#host-04) | Medium | Byte inputs outside 0..255 wrap instead of raising | R | s | C | s | s | spec:d | CONFIRMED (JS); Lisp rejects with a different error class |
| [HOST-05](#host-05) | Medium | TEXT constructors accept invalid encoding | R | R | C | s | s | spec:d | CONFIRMED (JS, PHP) + record keys unvalidated in all 5 hosts |
| [HOST-06](#host-06) | Medium | Native integers bypass the decimal digit cap | s | s | P | s | R | spec:d | CONFIRMED (Lisp, Python, JS) |
| [HOST-07](#host-07) | Medium | Structural hash stops at the depth cap instead of raising | s | s | s | s | Rd |  | CONFIRMED (Lisp, JS, C++ DISTINCT); cross-host disagreement |
| [HOST-08](#host-08) | Medium | Native round trip loses sparse list keys and numeric-looking record keys | s | R | s | s | s | spec:d | CONFIRMED (PHP only) |
| [HOST-09](#host-09) | Medium | Native round trip collapses FALSE, empty list and NULL | s | s | s | s | R | spec:d | CONFIRMED (Lisp only) |
| [HOST-10](#host-10) | High | Compiled program keeps per-node state across runs | s | s | R | s | s |  | CONFIRMED (Python only) |
| [TEST-01](#test-01) | — | Normative .selt cases for every SEM finding | · | · | · | · | · | suite:d suite-15:d suite-16:d suite-21:d | CONFIRMED (gaps real) |
| [TEST-02](#test-02) | — | .sqlt cases for LINK binders and Unicode names | · | · | · | · | · | suite:d | CONFIRMED |
| [TEST-03](#test-03) | — | SQL DB oracle: two-relation contexts | · | · | · | · | · | tool:d | CONFIRMED |
| [TEST-04](#test-04) | — | SQL DB oracle runs only the PHP translator | d | · | d | d | d | tool:R | CONFIRMED |
| [TEST-05](#test-05) | — | SQL fuzzer targeted families | · | · | · | · | · | tool:d | CONFIRMED |
| [TEST-06](#test-06) | — | Host-boundary assertions in every runtime/unit lane | R | R | R | d | R |  | CONFIRMED |
| [TEST-07](#test-07) | — | Slow lane for boundary tests, inside the required gate | · | · | · | · | · | tool:d | CONFIRMED |
| [TEST-08](#test-08) | — | Joined-row oracle: Unicode fields, BIN/TEXT keys, invalid numerics | · | · | · | · | · | tool:d | CONFIRMED |
| [TEST-09](#test-09) | — | Metamorphic oracle: pipeline as written vs stages bound to variables | · | · | · | · | · | tool:d | CONFIRMED |
| [TEST-10](#test-10) | — | Program generator: targeted families with coverage counters | · | · | · | · | · | tool:d | CONFIRMED |
| [TEST-11](#test-11) | — | Keep minimized fuzz findings as permanent conformance cases | · | · | · | · | · | tool:d | REFUTED (rule exists) |
| [HYG-01](#hyg-01) | Low | is_vacuous(): is_null() check subsumed by the next test | R | R | R | R | R |  | CONFIRMED (all 5) |
| [HYG-02](#hyg-02) | Low | `is_null() \|\| (NONE && size == 0)` idiom | Rd | Rd | Rd | Rd | Rd |  | CONFIRMED (all 5) + do_top in JS, C++, Lisp |
| [HYG-03](#hyg-03) | Medium | do_sort(): 3-arg text branch identical to the final else | R | R | R | R | R |  | REFUTED as proposed |
| [HYG-04](#hyg-04) | Medium | do_top(): 3-arg text branch identical to the final else | R | R | R | R | R |  | REFUTED as proposed; TOP_BY form unpinned |
| [HYG-05](#hyg-05) | Low | Optimizer sort details: `sort_count == 3` branch is a subset of the next | R | R | R | R | R |  | CONFIRMED (JS, Python, C++, Lisp); PHP differs |
| [HYG-06](#hyg-06) | Medium | optimize_tree(): `target` branch on non-assign nodes is unreachable | R | R | R | s | s | spec:d | CONFIRMED (JS, PHP, Python) |
| [HYG-07](#hyg-07) | Medium | exceeds_depth(): non-assign `target` check is always false | R | R | R | s | s |  | CONFIRMED (JS, PHP, Python) |
| [HYG-08](#hyg-08) | Low | PATH: impossible null check after has(), duplicated fallback | R | R | R | R | R |  | CONFIRMED (all 5) |
| [HYG-09](#hyg-09) | Low | GET: same redundancy as PATH | s | s | s | s | R |  | CONFIRMED (all 5; milder outside Lisp) |
| [HYG-10](#hyg-10) | Low | ?? and ??? evaluate through duplicated blocks | R | R | R | R | R |  | CONFIRMED (all 5) |
| [HYG-11](#hyg-11) | Low | Binding.column() and Binding.raw() duplicate option parsing | R | R | R | s | R |  | CONFIRMED (JS, PHP, Python, Lisp) |
| [HYG-12](#hyg-12) | Low | JS: dead `this;` statements in kind predicates | Rd | · | · | · | · |  | CONFIRMED |
| [HYG-13](#hyg-13) | Low | JS math plan: `digits === '1'` string check is dead | R | s | s | s | s |  | CONFIRMED (JS only) |
| [HYG-14](#hyg-14) | Low | JS: unused decodeUtf8 / toCodePoints import and re-export | R | · | · | · | · |  | CONFIRMED |
| [HYG-15](#hyg-15) | Low | JS: deprecated RecordShape.aliasCache / LEGACY_ALIAS_CACHES | R | s | s | s | s |  | CONFIRMED, removal not yet due |
| [HYG-16](#hyg-16) | Low | cloneAt / copyAt: redundant branches | R | R | s | s | s |  | CONFIRMED (JS, PHP); Python's fast path is a bug (SEM-12) |
| [HYG-17](#hyg-17) | Low | PHP: unused `use` imports | · | RRR | · | · | · |  | CONFIRMED (exactly the three files) |
| [HYG-18](#hyg-18) | Low | PHP: uncalled private helpers | s | RR | s | s | s |  | CONFIRMED (PHP) + twins in JS and Lisp |
| [HYG-19](#hyg-19) | Low | PHP JoinPlan has both $kind and $type | s | R | s | s | s |  | CONFIRMED (PHP only) |
| [HYG-20](#hyg-20) | Low | Python bin scripts: unused probe imports | · | · | R | · | · |  | REFUTED (deliberate) |
| [HYG-21](#hyg-21) | Low | Unused / unreachable code: tool-assisted sweep per host | d | d | d | d | d |  | CONFIRMED (new small items in JS, Python, all-host dead flag) |
| [HYG-22](#hyg-22) | — | Regression gate for the cleanups | · | · | · | · | · | tool:d | CONFIRMED (the review names the wrong gate) |
| [DOC-01](#doc-01) | Low | isBinderName doc comment sits above the wrong function | R | R | s | s | s |  | CONFIRMED (JS, PHP) |
| [DOC-02](#doc-02) | — | Misplaced / orphaned doc comments: broader sweep | d | d | d | d | d |  | REFUTED beyond DOC-01 |
| [DOC-03](#doc-03) | Medium | TOP_BY signature in SPEC and the manifest contradicts every host and the suite | · | · | · | · | · | spec:d suite:d | CONFIRMED (new) |

## SEM — Language semantics

Wrong results or missing errors reachable from SEL source. Spec → conformance case → every host.

<a id="sem-01"></a>
### SEM-01 · Computed record index returns a stale cached field

**Severity (reviewer):** High · **Source:** B: Python #1 (python/sel/eval.py:273); B §1 table row 1; B §3 last paragraph (compiled Program reused)

Python reuses a cached record slot before evaluating the index expression, so an index that changes between evaluations reads the first slot again. Reusing one compiled Program across runs can also return the previous run's field.

> **Validation — CONFIRMED (Python only).** python/sel/eval.py:273-282 consults node._cached_slot (keyed on record shape only) BEFORE evaluating the index expression, for computed indexes too. Repro gives [1,1] in py, [1,2] elsewhere; 3-key reversed gives [3,3,3]. Worse than reported: the index expression is skipped entirely, so R[IF(_==2, ABORT("boom"), "x")] returns [1,1] instead of E_ABORT (other 4 hosts raise). Fix: only use the slot cache when node.idx is a text literal. No cache in JS/PHP/C++/Lisp index eval.

| Program / action | Reported result | Expected |
|---|---|---|
| `MAP(LIST("x","y"), RECORD("x",1,"y",2)[_])` | [1,1] (py) | [1,2] |

Variants / notes:
- Reverse the key order
- Three keys
- Repeat the lookup against differently shaped records
- One compiled Program run twice with different context keys (host test, see HOST-10)

Tests: conformance: new case (aggregates or relational file); unit: compiled-program reuse per host

Related: [HOST-10](#host-10), [TEST-06](#test-06), [TEST-10](#test-10)

Checks:

- [x] **suite** · derived · `conformance/06-aggregates.selt or 15-relational.selt` — Add the minimal case and its variants; assert values, not host agreement  
  → **FIXED**: Fixed: python/sel/eval.py caches a slot only for a literal index key; conformance val.index.* (validation: Gap: no case evaluates one computed-index node repeatedly over same-shaped records. Add: repro, 3-key reversed, ABORT-in-index)
- [x] **Python** · reported · `python/sel/eval.py:273` — Slot cache consulted before the index expression runs  
  → **FIXED**: Fixed: python/sel/eval.py caches a slot only for a literal index key; conformance val.index.* (validation: eval.py:273 cache hit returns before idx is evaluated; also skips errors in idx (E_ABORT lost))
- [x] **JS** · sweep — Any inline / shape / slot cache on index nodes (JS RecordShape, C++ node caches, Lisp caches) keyed on something other than the evaluated key  
  → **REFUTED**: js/src/eval.mjs:245 evaluates key every time, no cache
- [x] **PHP** · sweep — Any inline / shape / slot cache on index nodes (JS RecordShape, C++ node caches, Lisp caches) keyed on something other than the evaluated key  
  → **REFUTED**: php/src/Evaluator.php:171 no cache
- [x] **C++** · sweep — Any inline / shape / slot cache on index nodes (JS RecordShape, C++ node caches, Lisp caches) keyed on something other than the evaluated key  
  → **REFUTED**: cpp/sel.cpp:3541 no cache
- [x] **Lisp** · sweep — Any inline / shape / slot cache on index nodes (JS RecordShape, C++ node caches, Lisp caches) keyed on something other than the evaluated key  
  → **REFUTED**: lisp/src/eval.lisp:272 no cache (args-cache is per call only)

<a id="sem-02"></a>
### SEM-02 · TOP_BY renumbers preserved sparse list keys when supplying _K

**Severity (reviewer):** Medium · **Source:** B: Python #3 (python/sel/builtins/aggregate.py:537); B §1 table row 2

Python renumbers a packed filtered list before binding _K, so a key-based TOP_BY predicate sees 1..n instead of the preserved keys. JS gives the specified result.

> **Validation — CONFIRMED (Python only).** python/sel/builtins/aggregate.py:537-539 (do_top) iterates packed list storage as str(i+1) and ignores value.list_keys, so _K is renumbered. Affects TOP, TOP_DESC and TOP_BY (all variants: implicit/explicit binder, ASC/DESC). Repro: py 2, others 3; DESC variant: py 3, others 2. It is the only enumerate(storage) loop in python/sel that skips list_keys. Fix: use iter_elements (value.py:132).

| Program / action | Reported result | Expected |
|---|---|---|
| `TOP_BY(FILTER(LIST(1,2,3), _ > 1), _K $== "2", 1)` | 2 (py) | 3 (js agrees) |

Variants / notes:
- "DESC" → 2
- Explicit binder
- Start from a record with non-consecutive keys
- Existing tests cover key preservation and TOP_BY separately, not their interaction

Tests: conformance/06-aggregates.selt

Related: [SEM-03](#sem-03), [TEST-10](#test-10)

Checks:

- [x] **suite** · derived · `conformance/06-aggregates.selt` — Add the case + variants  
  → **FIXED**: Fixed: python do_top reads keys through list_keys; agg.top-by / agg.k cases (validation: Gap: no case combines FILTER-preserved keys with a TOP* _K read)
- [x] **Python** · reported · `python/sel/builtins/aggregate.py:537` — Renumbering of packed filtered list when binding _K  
  → **FIXED**: Fixed: python do_top reads keys through list_keys; agg.top-by / agg.k cases (validation: aggregate.py:537-539 ignores list_keys)
- [x] **JS** · control — Reviewer: JS returns 3 (correct) — confirm  
  → **REFUTED**: Returns 3 (and 2 for DESC) as specified
- [x] **PHP** · sweep — Same _K supply path in TOP_BY  
  → **REFUTED**: Correct
- [x] **C++** · sweep — Same _K supply path in TOP_BY  
  → **REFUTED**: Correct
- [x] **Lisp** · sweep — Same _K supply path in TOP_BY  
  → **REFUTED**: Correct

<a id="sem-03"></a>
### SEM-03 · _K on sparse inputs in the other binder-supplying builtins

**Severity (reviewer):** Medium · **Source:** derived from SEM-02

If one aggregate renumbers keys before binding _K, siblings built on the same helper may too. Sweep every builtin that binds _K over a list that can carry preserved (sparse) keys.

> **Validation — CONFIRMED only as SEM-02.** Probed all 13 _K-binding forms (ALL, ANY, SUM, SORT, SORT_DESC, SORT_BY implicit/explicit, TOP, TOP_DESC, TOP_BY implicit/explicit/DESC, BUCKET, MAP) over FILTER(LIST(10,20,30), _ > 10) with keys that distinguish renumbering. JS/PHP/C++/Lisp correct everywhere; Python wrong only in TOP/TOP_DESC/TOP_BY (same do_top loop, SEM-02). LINK binds L/R, not _K.

| Program / action | Reported result | Expected |
|---|---|---|
| `SORT_BY(FILTER(LIST(1,2,3), _ > 1), _K)  — and the same shape for TOP, SORT, MAP, FILTER, BUCKET, DEDUPE, LINK …` | ? | keys "2","3" visible through _K |

Variants / notes:
- Per builtin: sparse list input, record with non-consecutive numeric-looking keys

Tests: conformance/06-aggregates.selt, 15-relational.selt

Related: [SEM-02](#sem-02)

Checks:

- [x] **suite** · derived — One case per _K-binding builtin over a sparse list  
  → **FIXED**: Fixed: python do_top reads keys through list_keys; agg.k.every-binding-form cases (validation: Add the per-builtin probe as cases (one per TOP* at least))
- [x] **JS** · derived — List every builtin that binds _K; check whether any renumbers first  
  → **REFUTED**: All 13 forms correct
- [x] **PHP** · derived — List every builtin that binds _K; check whether any renumbers first  
  → **REFUTED**: All 13 forms correct
- [x] **Python** · derived — List every builtin that binds _K; check whether any renumbers first  
  → **FIXED**: Fixed: python do_top reads keys through list_keys; agg.k.every-binding-form cases (validation: TOP, TOP_DESC, TOP_BY wrong (do_top, = SEM-02); the other 10 forms correct)
- [x] **C++** · derived — List every builtin that binds _K; check whether any renumbers first  
  → **REFUTED**: All 13 forms correct
- [x] **Lisp** · derived — List every builtin that binds _K; check whether any renumbers first  
  → **REFUTED**: All 13 forms correct

<a id="sem-04"></a>
### SEM-04 · Joined-row field promotion folds Unicode case (ß vs SS)

**Severity (reviewer):** Medium · **Source:** B: JS #2 (js/src/builtins/structure.mjs:305); B: JS #2 "reproduced in Python too"; B §1 table row 3

Joined-row field promotion compares names with full Unicode uppercasing where the spec requires ASCII case comparison, so ß and SS collide and one field is discarded. PHP returns the value.

> **Validation — CONFIRMED (JS, Python, Lisp).** SPEC.md:753-755 says promoted names are compared ASCII-case-insensitively. JS (structure.mjs:305-312, fast path 347-355) and Python (structure.py:326-333, fast path 381-389) use full Unicode upper(): "ß"/"SS" and "ſ"/"s" collide and BOTH fields are dropped (reading "SS" fails too). Lisp passes ß/SS but uses SBCL string-upcase (structure.lisp:381-434), which folds 1:1 pairs: "é"/"É" collide in Lisp as well as JS/Python (E_NO_KEY on both names); PHP and C++ promote both. ASCII control (x vs X) agrees everywhere. The suite has no non-ASCII field name at all.

| Program / action | Reported result | Expected |
|---|---|---|
| `A=LIST(RECORD("ß",1)); B=LIST(RECORD("SS",2)); A .> LINK(B, TRUE) .> MAP(_["ß"])` | E_NO_KEY (js, py) | 1 (php agrees) |

Variants / notes:
- Read "SS" → 2
- Swap sides
- LINK_LEFT
- Both fields in a later record projection

Tests: conformance/16-joined-rows.selt

Related: [SEM-05](#sem-05), [SQL-02](#sql-02), [TEST-08](#test-08), [TEST-10](#test-10)

Checks:

- [x] **spec** · derived · `spec/SPEC.md (joined rows, name comparison)` — Confirm the spec states ASCII-only case comparison for promotion  
  → **REFUTED**: Spec already states ASCII-case-insensitive comparison (SPEC.md:754)
- [x] **suite** · derived · `conformance/16-joined-rows.selt` — Add the case + variants  
  → **FIXED**: Fixed: ASCII-only name folding in promotion (js upperName, py _upper_name, lisp ascii-upcase); rel.link.promotion.* cases (validation: No non-ASCII field names anywhere in conformance/*.selt; add ß/SS and ſ/s cases)
- [x] **JS** · reported · `js/src/builtins/structure.mjs:305` — Full Unicode toUpperCase in promotion  
  → **FIXED**: Fixed: ASCII-only name folding in promotion (js upperName, py _upper_name, lisp ascii-upcase); rel.link.promotion.* cases (validation: structure.mjs:305-312 toUpperCase())
- [x] **Python** · repro — Reviewer reproduced; locate the promotion code  
  → **FIXED**: Fixed: ASCII-only name folding in promotion (js upperName, py _upper_name, lisp ascii-upcase); rel.link.promotion.* cases (validation: structure.py:326-333 and 381-387 str.upper())
- [x] **PHP** · control — Reviewer: PHP returns 1 (correct) — confirm  
  → **REFUTED**: Promotes both fields (correct)
- [x] **C++** · sweep — Promotion name comparison  
  → **REFUTED**: Promotes both fields (correct)
- [x] **Lisp** · sweep — Promotion name comparison (SBCL string-upcase / char-equal are Unicode-aware)  
  → **FIXED**: Fixed: ASCII-only name folding in promotion (js upperName, py _upper_name, lisp ascii-upcase); rel.link.promotion.* cases (validation: ß/SS passes, but "é"/"É" collide: structure.lisp:381-434 string-upcase is Unicode-aware for 1:1 case pairs)

<a id="sem-05"></a>
### SEM-05 · Native Unicode case operations anywhere the language defines case

**Severity (reviewer):** Medium · **Source:** derived from SEM-04 and SQL-02; CLAUDE.md: "UPPER/LOWER are ASCII-only by decision"; never use the host's idea of case

Two independent findings used the host's Unicode case mapping. Grep each host for every native case operation and classify each hit: language-defined (must be ASCII) vs internal on ASCII-only data (fine).

> **Validation — CONFIRMED (JS, Python, Lisp; C++ latent).** Classified every native case call. Identifier/binder/function-name uses are safe (the lexer makes identifiers ASCII). User-data uses: (1) joined-row field-name sets — JS structure.mjs:305-355,453-459,587,603-617,676,732,968-981; Python structure.py:326-437,634-829,1052; Lisp structure.lisp:381-486,665 (= SEM-04); (2) sort/top direction text — JS aggregate.mjs:328-402 and Python aggregate.py:396-470 accept "deſc" as DESC (PHP/C++/Lisp give E_BAD_ARG) — NEW divergence; (3) aggregate field sets JS aggregate.mjs:157, Python aggregate.py:155, Lisp aggregate.lisp:665-ish (structure.lisp:665); (4) SQL qualifier compare JS translator.mjs:491-531, Python translator.py:429-465 (= SQL-02). PHP uses strtoupper/strcasecmp only (ASCII since PHP 8.2) — clean. C++ uses byte-wise std::toupper in the C locale — correct today but locale-dependent if an embedding app calls setlocale; cpp/sel_sql_binding.cpp:90 passes a signed char to std::tolower (UB on non-ASCII bytes).

Variants / notes:
- JS: toUpperCase/toLowerCase/localeCompare/Intl
- PHP: strtoupper/strtolower/mb_*/strcasecmp/ucfirst
- Python: upper/lower/casefold/capitalize/title
- C++: std::toupper/tolower/locale
- Lisp: string-upcase/downcase/char-equal/string-equal/char-upcase

Related: [SEM-04](#sem-04), [SQL-02](#sql-02)

Checks:

- [x] **JS** · derived — Grep native case ops; list each call site with verdict  
  → **FIXED**: Fixed: every language-defined case op is ASCII: js/py/lisp names, directions and SQL qualifiers; C++ ascii_up/ascii_down and unsigned-char casts; direction cases in 06-aggregates (validation: Field-name sets (structure.mjs), direction text (aggregate.mjs:328-402: "deſc" accepted), aggregate.mjs:157, SQL translator.mjs:491-531. Sites to switch to an ASCII upper helper (sql/bindings.mjs already has asciiUpper))
- [x] **PHP** · derived — Grep native case ops; list each call site with verdict  
  → **REFUTED**: Only strtoupper/strtolower/strcasecmp, ASCII-only on PHP >= 8.2; no mb_* case calls
- [x] **Python** · derived — Grep native case ops; list each call site with verdict  
  → **FIXED**: Fixed: every language-defined case op is ASCII: js/py/lisp names, directions and SQL qualifiers; C++ ascii_up/ascii_down and unsigned-char casts; direction cases in 06-aggregates (validation: Field-name sets (structure.py), direction (aggregate.py:396-470: "deſc" accepted), aggregate.py:155, translator.py:429-465)
- [x] **C++** · derived — Grep native case ops; list each call site with verdict  
  → **FIXED**: Fixed: every language-defined case op is ASCII: js/py/lisp names, directions and SQL qualifiers; C++ ascii_up/ascii_down and unsigned-char casts; direction cases in 06-aggregates (validation: Latent only: std::toupper/tolower on bytes depend on the C locale (no setlocale in the library, so ASCII today); sel_sql_binding.cpp:90 std::tolower(char) is UB for bytes >= 0x80)
- [x] **Lisp** · derived — Grep native case ops; list each call site with verdict  
  → **FIXED**: Fixed: every language-defined case op is ASCII: js/py/lisp names, directions and SQL qualifiers; C++ ascii_up/ascii_down and unsigned-char casts; direction cases in 06-aggregates (validation: string-upcase on field names (structure.lisp:381-486, 665): é/É collide. Direction text uses string-upcase too but no non-ASCII char upcases into ASC/DESC in SBCL ("deſc" rejected). Use lexer.lisp:134 ascii-upcase)

<a id="sem-06"></a>
### SEM-06 · Equijoin fast key path disagrees with the comparator

**Severity (reviewer):** High · **Source:** B: C++ #1 (cpp/sel.cpp:4332); B §1 table row 4

The fast key conversion used for hash equijoins drops BIN values for $== and treats invalid numeric keys as "no match" instead of raising, so the optimized path and the ordinary comparison disagree.

> **Validation — CONFIRMED (all 5 hosts; broader than reported).** SPEC.md:783-787: equijoin pairs are "matched by those values as the comparison would compare them". Every host's canonical join key (C++ make_fast_join_key sel.cpp:4332; JS canonicalJoinKey structure.mjs:189; PHP Structure.php:179; Python structure.py:186; Lisp extract-join-key structure.lisp:246) (a) swallows the numeric conversion error — "bad" == 1 gives 0 rows instead of E_NOT_NUM on both sides, and LINK_LEFT keeps the row unmatched — and (b) returns no key for non-TEXT values under $==. Results (fast path vs IF-wrapped control): BIN/TEXT $== : 0 on all hosts vs 1; BIN/BIN $== : 0 on JS/PHP/C++/Lisp, 1 on Python, control 1; BOOL/BOOL $== : 0 on JS/PHP/C++/Lisp, 1 on Python, control E_NOT_BIN; LIST(1)/"1" $== : 0 everywhere vs TRUE comparison. Python keys on value._scalar for any kind, so it is right for BIN/BIN and wrong the other way for BOOL. Also a cross-host disagreement the fuzzer has not caught.

| Program / action | Reported result | Expected |
|---|---|---|
| `LINK with $== between BIN TO_UTF8("a") and TEXT "a"; COUNT(...)` | 0 (cpp) | 1 |
| `numeric join where "bad" == 1` | 0 matches (cpp) | E_NOT_NUM |

Variants / notes:
- Swap sides
- Compare two BIN values
- LINK_LEFT
- Invalid key on the right side
- Control: wrap the same comparison as IF(cmp,TRUE,FALSE) — optimized and ordinary predicates must agree

Tests: conformance/15-relational.selt or 16-joined-rows.selt

Related: [TEST-08](#test-08), [TEST-10](#test-10)

Checks:

- [x] **suite** · derived · `conformance/15-relational.selt / 16-joined-rows.selt` — Add cases + control  
  → **FIXED**: Fixed: equijoin keys go through the comparator's coercions; a rejected key raises exactly as the comparison would, in operator order, also when the sides are swapped (all 5 hosts); rel.link.equi.* cases; join-rows oracle models it (validation: No case pins BIN, BOOL, list or invalid-numeric equijoin keys; add them with IF-wrapped controls)
- [x] **C++** · reported · `cpp/sel.cpp:4332` — Fast key conversion: BIN handling, numeric validation  
  → **FIXED**: Fixed: equijoin keys go through the comparator's coercions; a rejected key raises exactly as the comparison would, in operator order, also when the sides are swapped (all 5 hosts); rel.link.equi.* cases; join-rows oracle models it (validation: sel.cpp:4332-4395: catch → nullopt; non-TEXT → nullopt)
- [x] **JS** · sweep — Hash / fast-key equijoin path vs the plain comparator: BIN keys, $== vs ==, invalid numerics  
  → **FIXED**: Fixed: equijoin keys go through the comparator's coercions; a rejected key raises exactly as the comparison would, in operator order, also when the sides are swapped (all 5 hosts); rel.link.equi.* cases; join-rows oracle models it (validation: structure.mjs:189-202: same two defects (catch → null; non-TEXT → null))
- [x] **PHP** · sweep — Hash / fast-key equijoin path vs the plain comparator: BIN keys, $== vs ==, invalid numerics  
  → **FIXED**: Fixed: equijoin keys go through the comparator's coercions; a rejected key raises exactly as the comparison would, in operator order, also when the sides are swapped (all 5 hosts); rel.link.equi.* cases; join-rows oracle models it (validation: Structure.php:179 canonicalJoinKey: same results (0 for BIN/TEXT, BIN/BIN, BOOL, "bad"==1))
- [x] **Python** · sweep — Hash / fast-key equijoin path vs the plain comparator: BIN keys, $== vs ==, invalid numerics  
  → **FIXED**: Fixed: equijoin keys go through the comparator's coercions; a rejected key raises exactly as the comparison would, in operator order, also when the sides are swapped (all 5 hosts); rel.link.equi.* cases; join-rows oracle models it (validation: structure.py:186-216: numeric error → None; non-numeric keys on _scalar of any kind (BIN/BIN ok, BIN/TEXT miss, BOOL matches instead of E_NOT_BIN))
- [x] **Lisp** · sweep — Hash / fast-key equijoin path vs the plain comparator: BIN keys, $== vs ==, invalid numerics  
  → **FIXED**: Fixed: equijoin keys go through the comparator's coercions; a rejected key raises exactly as the comparison would, in operator order, also when the sides are swapped (all 5 hosts); rel.link.equi.* cases; join-rows oracle models it (validation: structure.lisp:246 extract-join-key: same results as JS/PHP/C++)

<a id="sem-07"></a>
### SEM-07 · FILTER pushdown ahead of MAP/SORT hides earlier errors

**Severity (reviewer):** High · **Source:** B: C++ #2 (cpp/sel.cpp:7403); B §1 table row 5 + paragraph after the table ("the direct JS pipelines return a value")

The optimizer moves FILTER ahead of MAP or SORT. Rows the FILTER drops never reach the stage that would have raised, so a pipeline that must fail returns a value. Binding the SORT/MAP result to a variable first raises the specified error.

> **Validation — CONFIRMED (all 5 hosts).** The reviewer saw C++ and JS; it is unanimous. LIST(2,1) .> SORT(_, IF(_ == 2, ABORT("sort"), _)) .> FILTER(_ == 1) .> MAP(_) returns [1] on every host (also with .> TAKE(1)); bound to a variable first it is E_ABORT at 1:43. The FILTER is only moved when a later renumbering step hides the keys (JS optimizer.mjs:358-371, C++ sel.cpp:7403), so SORT .> FILTER at the end of a pipeline raises correctly, as do SORT_BY and TOP_BY forms tried. MAP variant: LIST(RECORD("id",2),RECORD("id",3)) .> MAP(RECORD("id", _["id"], "v", 1/(_["id"]-2))) .> FILTER(_["id"] == 3) .> MAP(_["id"]) returns [3] on every host instead of E_DIV_ZERO — contradicting conformance rel.map.record-field-nothing-reads-is-still-evaluated (15-relational.selt:410). A MAP whose FILTER reads the computed value (1/(id-2) > 0) raises correctly.

| Program / action | Reported result | Expected |
|---|---|---|
| `LIST(2,1) .> SORT(_, IF(_ == 2, ABORT("sort"), _)) .> FILTER(_ == 1) .> MAP(_)` | [1] (cpp, js) | E_ABORT |
| `MAP computing 1/(_["id"]-2) before a FILTER that drops that row` | value | E_DIV_ZERO |

Variants / notes:
- SORT_BY
- TOP_BY fusion
- A final renumbering step
- Pin the code first, then the position once the normative position is confirmed
- Distinct from conformance/15-relational.selt:410 strict-record cases

Tests: conformance/15-relational.selt

Related: [SEM-08](#sem-08), [TEST-09](#test-09), [TEST-10](#test-10)

Checks:

- [x] **spec** · derived · `spec/SPEC.md (evaluation order / optimizer licence)` — Confirm the spec forbids observable reordering of errors  
  → **FIXED**: Fixed: optimizer rewrites cross only steps that cannot raise (cannotRaise, all 5 hosts); the SQL/logical path keeps the field-read rules; rel.optimiser.* cases; plan cases and lanes updated (validation: Gap: SPEC states error preservation for optimisers only for E_DEPTH (SPEC.md:540-549) and FILTER-after-LINK (§7.4); §7.3 "an implementation that filters before it maps must not let that show" names keys and _K, not errors. Add errors explicitly)
- [x] **suite** · derived · `conformance/15-relational.selt` — Add cases + variants  
  → **FIXED**: Fixed: optimizer rewrites cross only steps that cannot raise (cannotRaise, all 5 hosts); the SQL/logical path keeps the field-read rules; rel.optimiser.* cases; plan cases and lanes updated (validation: Existing strict-record cases (15-relational.selt:410-440) stop at MAP .> FILTER; none has a later renumbering step, which is what enables the move. Add SORT/MAP .> FILTER .> MAP/TAKE cases)
- [x] **C++** · reported · `cpp/sel.cpp:7403` — FILTER-before-MAP/SORT rewrites  
  → **FIXED**: Fixed: optimizer rewrites cross only steps that cannot raise (cannotRaise, all 5 hosts); the SQL/logical path keeps the field-read rules; rel.optimiser.* cases; plan cases and lanes updated (validation: sel.cpp:7403 MAP/FILTER swap (+ sort/FILTER swap); both repros suppress the error)
- [x] **JS** · repro — Reviewer: direct JS pipelines return a value  
  → **FIXED**: Fixed: optimizer rewrites cross only steps that cannot raise (cannotRaise, all 5 hosts); the SQL/logical path keeps the field-read rules; rel.optimiser.* cases; plan cases and lanes updated (validation: optimizer.mjs:358 (MAP→FILTER) and 371 (SORT*→FILTER); both repros suppress)
- [x] **PHP** · sweep — Same rewrite in the PHP optimizer  
  → **FIXED**: Fixed: optimizer rewrites cross only steps that cannot raise (cannotRaise, all 5 hosts); the SQL/logical path keeps the field-read rules; rel.optimiser.* cases; plan cases and lanes updated (validation: Both repros suppress the error)
- [x] **Python** · sweep — Same rewrite in the Python optimizer  
  → **FIXED**: Fixed: optimizer rewrites cross only steps that cannot raise (cannotRaise, all 5 hosts); the SQL/logical path keeps the field-read rules; rel.optimiser.* cases; plan cases and lanes updated (validation: Both repros suppress the error)
- [x] **Lisp** · sweep — Same rewrite in the Lisp optimizer  
  → **FIXED**: Fixed: optimizer rewrites cross only steps that cannot raise (cannotRaise, all 5 hosts); the SQL/logical path keeps the field-read rules; rel.optimiser.* cases; plan cases and lanes updated (validation: Both repros suppress the error)
- [x] **sql** · sweep · `hybrid planner` — Does the SQL-pushdown prefix / hybrid split move a FILTER past an erroring stage?  
  → **FIXED**: Fixed: optimizer rewrites cross only steps that cannot raise (cannotRaise, all 5 hosts); the SQL/logical path keeps the field-read rules; rel.optimiser.* cases; plan cases and lanes updated (validation: Not executed against a DB; docs/internals/sql-translation.md:2353 says the SQL path runs the same logical optimiser FILTER moves, so it inherits the defect)

<a id="sem-08"></a>
### SEM-08 · Every optimizer rewrite must preserve errors of the stages it moves or skips

**Severity (reviewer):** High · **Source:** derived from SEM-07

SEM-07 is one rewrite. Enumerate every optimizer rewrite (pushdowns, fusions, short-circuits, math plans, hybrid plans) per host and check each against "same value or same error code and position as unoptimized evaluation".

> **Validation — CONFIRMED (2 more unsafe rewrites, all 5 hosts).** Inventory of the logical pipeline rewrites (JS optimizer.mjs:316-455; the other hosts mirror it and gave identical results on every probe): TAKE+TAKE and DROP+DROP merge — safe; SORT*+TAKE → TOP* — safe (same first error); MAP↔FILTER and SORT*↔FILTER swap — UNSAFE (SEM-07); SELECT_COLS↔FILTER — safe (SELECT_COLS cannot raise); MAP(computed) moved after TOP*/SORT* — UNSAFE: MAP(RECORD("a",_["a"],"v",1/(_["a"]-1))) .> TOP_BY(_["a"],"DESC",1) .> MAP(_["a"]) returns [2] on all hosts, bound form E_DIV_ZERO; FILTER+FILTER fusion into AND — UNSAFE for error ORDER: LIST(1,2) .> FILTER(IF(_==2,ABORT("a"),TRUE)) .> FILTER(IF(_==1,ABORT("b"),TRUE)) gives ABORT "b" at 1:74 on all hosts, as written it is ABORT "a" at 1:40; DISTINCT/DEDUPE collapse — safe; FILTER(TRUE) drop — safe. Constant folding keeps errors (0*(1/0), IF(TRUE,1,1/0) correct).

Variants / notes:
- For each rewrite: an erroring expression in the stage that gets moved, fused or skipped

Tests: conformance/15-relational.selt; tools/check-js-optimizer.mjs; tools/check-php-optimizer.php

Related: [SEM-07](#sem-07), [TEST-09](#test-09)

Checks:

- [x] **JS** · derived — List every rewrite; mark error-preserving or not  
  → **FIXED**: Fixed: MAP-swap and FILTER fusion guarded by cannotRaise in all 5 hosts; rel.optimiser.* cases (validation: MAP→TOP/SORT swap suppresses E_DIV_ZERO; FILTER+FILTER fusion reports the second FILTER's error first; (plus SEM-07 swaps))
- [x] **PHP** · derived — List every rewrite; mark error-preserving or not  
  → **FIXED**: Fixed: MAP-swap and FILTER fusion guarded by cannotRaise in all 5 hosts; rel.optimiser.* cases (validation: Same unsafe rewrites, except that PHP sortDetails (Optimizer.php:738-747) ignores the 4-arg explicit-binder form, so MAP .> TOP_BY(X, key, "DESC", n) raises in PHP (correct) and answers [2] in the other four — a live cross-host divergence)
- [x] **Python** · derived — List every rewrite; mark error-preserving or not  
  → **FIXED**: Fixed: MAP-swap and FILTER fusion guarded by cannotRaise in all 5 hosts; rel.optimiser.* cases (validation: MAP→TOP/SORT swap suppresses E_DIV_ZERO; FILTER+FILTER fusion reports the second FILTER's error first; (plus SEM-07 swaps))
- [x] **C++** · derived — List every rewrite; mark error-preserving or not  
  → **FIXED**: Fixed: MAP-swap and FILTER fusion guarded by cannotRaise in all 5 hosts; rel.optimiser.* cases (validation: MAP→TOP/SORT swap suppresses E_DIV_ZERO; FILTER+FILTER fusion reports the second FILTER's error first; (plus SEM-07 swaps))
- [x] **Lisp** · derived — List every rewrite; mark error-preserving or not  
  → **FIXED**: Fixed: MAP-swap and FILTER fusion guarded by cannotRaise in all 5 hosts; rel.optimiser.* cases (validation: MAP→TOP/SORT swap suppresses E_DIV_ZERO; FILTER+FILTER fusion reports the second FILTER's error first; (plus SEM-07 swaps))
- [x] **sql** · derived · `hybrid / relational-plan` — Same inventory for pushdown decisions  
  → **FIXED**: Fixed: MAP-swap and FILTER fusion guarded by cannotRaise in all 5 hosts; rel.optimiser.* cases (validation: By code path (same logical optimiser, sql-translation.md:2353); not executed against a DB)

<a id="sem-09"></a>
### SEM-09 · FILTER on records casts keys to int before deciding to preserve them

**Severity (reviewer):** High · **Source:** B: PHP #1 (php/src/Builtins/Core.php:525; also php/src/Value.php:353 Value::fromEntries)

PHP casts a record key to an integer before deciding whether to preserve it, so numeric-prefixed keys are rewritten. The same comparison appears in Value::fromEntries.

> **Validation — CONFIRMED (PHP only; broader spellings).** php/src/Builtins/Core.php:525-526 and php/src/Value.php:353 decide "is this key the next position?" with (int) $key !== $expectedIndex. PHP casts "1x", "01", " 1", "1 ", "+1", "1e0", "1.0" to 1, so any of these as the first kept key is rewritten to "1" (FILTER(RECORD("1x",10),_>0) gives key "1"; Value::fromEntries([["1x",…],["2",…]], true) gives keys "1","2"). "１" and "١" survive. JS/Python/C++/Lisp preserve every spelling tried, first or later. Fix: compare the key text with the decimal spelling of the expected index (string equality), as Python does (value.py:289).

| Program / action | Reported result | Expected |
|---|---|---|
| `FILTER(RECORD("1x", 10), _ > 0)` | key "1" (php) | key "1x" (js agrees) |

Variants / notes:
- Derived key spellings to try: "1x", "01", " 1", "1 ", "+1", "-0", "1e0", "0x1", "１" (fullwidth), "٣" (Arabic-Indic)
- Same keys through MAP, SORT, DEDUPE, BUCKET, record literals

Tests: conformance (record key preservation); php runtime check for fromEntries

Related: [SEM-10](#sem-10), [HOST-08](#host-08)

Checks:

- [x] **suite** · derived — Add FILTER(RECORD("1x",10), _>0) and the derived key spellings  
  → **FIXED**: Fixed: php FILTER in-place and fromEntries compare key text, not (int) casts; agg.filter.keeps-a-first-key case (validation: No case with a numeric-looking non-canonical first key; add "1x", "01", " 1", "+1", "1e0", "1.0")
- [x] **PHP** · reported · `php/src/Builtins/Core.php:525; php/src/Value.php:353` — Int cast before preservation decision  
  → **FIXED**: Fixed: php FILTER in-place and fromEntries compare key text, not (int) casts; agg.filter.keeps-a-first-key case (validation: Core.php:525-526 (FILTER) and Value.php:353 (fromEntries): (int) cast)
- [x] **JS** · control — Reviewer: JS preserves "1x" — confirm  
  → **REFUTED**: All spellings preserved
- [x] **Python** · sweep — Numeric-key detection (int()/parse-integer/stoi) before an explicit ASCII-digit check  
  → **REFUTED**: All spellings preserved (from_entries compares strings, value.py:289)
- [x] **C++** · sweep — Numeric-key detection (int()/parse-integer/stoi) before an explicit ASCII-digit check  
  → **REFUTED**: All spellings preserved
- [x] **Lisp** · sweep — Numeric-key detection (int()/parse-integer/stoi) before an explicit ASCII-digit check  
  → **REFUTED**: All spellings preserved

<a id="sem-10"></a>
### SEM-10 · PHP array-key coercion wherever SEL keys become PHP array keys

**Severity (reviewer):** High · **Source:** derived from SEM-09 and HOST-08

PHP silently turns decimal-integer-looking string keys into ints in arrays. Any place that stores SEL record keys as PHP array keys, or compares them after an (int) cast / is_numeric / ctype_digit, is a candidate for the same class of bug.

> **Validation — REFUTED beyond SEM-09.** Swept php/src: every (int) cast outside Core.php:525 and Value.php:353 (SEM-09) works on digit strings the decimal core has already validated, or is guarded by ctype_digit / a first-digit check (Value.php:655-658, Structure.php:203-211, Translator.php:406). in_array calls on names are strict (the true flag sits on the next line, Translator.php:948-959); the 15 array_merge calls merge positional lists; array_unique uses SORT_STRING; array_flip(listKeys) is injective (PHP only canonicalises canonical decimal strings); every file declares strict_types. A battery with keys "5","1","x" through MAP/_K, BUCKET, PATH, GET, SORT_BY, DEDUPE, FILTER, SELECT_COLS and LINK agrees byte-for-byte with the other four hosts.

Variants / notes:
- Grep: (int), intval, is_numeric, ctype_digit, array_keys, array_values, array_merge, array_combine, + on arrays, sort flags

Tests: tools/check-php-runtime.php

Related: [SEM-09](#sem-09), [HOST-08](#host-08)

Checks:

- [x] **PHP** · derived · `php/src/**` — Inventory each site; verdict  
  → **REFUTED**: No further sites; see verdict

<a id="sem-11"></a>
### SEM-11 · LINK first-row probe tests the wrong side (Lisp do-link)

**Severity (reviewer):** — · **Source:** A §3.5 #3 (lisp/src/builtins/structure.lisp:1019-1021); A §4 Task 3

first-r2 guards on (value-null-p val1) instead of val2. The reviewer also calls both guards redundant because first-collection-item already handles null. May be behaviour-visible when exactly one side is null.

> **Validation — CONFIRMED as a typo; not observable.** lisp/src/builtins/structure.lisp:1019-1020: first-r2 is guarded by (value-null-p val1) instead of val2. Unobservable: the if at 1021 returns the empty list whenever val1 is NULL, so first-r2 is only used when val1 is non-NULL, where the guard is simply true; first-collection-item handles a NULL val2 itself. LINK/LINK_LEFT with a NULL or empty side agree on all 5 hosts (5 probes). Fix is cosmetic: drop both unless guards (HYG).

| Program / action | Reported result | Expected |
|---|---|---|
| `LINK where the left input is NULL and the right is not (and the reverse)` | ? | same as other hosts |

Variants / notes:
- Left null / right non-empty
- Left non-empty / right null
- Both empty

Tests: conformance/16-joined-rows.selt

Related: [HYG-02](#hyg-02)

Checks:

- [x] **Lisp** · reported · `lisp/src/builtins/structure.lisp:1019-1021` — val1 used in first-r2 guard  
  → **FIXED**: Fixed: both first-row probes call first-collection-item directly (which handles NULL), as JS does (validation: Wrong variable, but no behavioural effect)
- [x] **JS** · sweep — Same first-row probe in LINK: correct operand on each side?  
  → **REFUTED**: Calls first_collection_item on each side directly (no guard); null-side probes agree
- [x] **PHP** · sweep — Same first-row probe in LINK: correct operand on each side?  
  → **REFUTED**: Calls first_collection_item on each side directly (no guard); null-side probes agree
- [x] **Python** · sweep — Same first-row probe in LINK: correct operand on each side?  
  → **REFUTED**: Calls first_collection_item on each side directly (no guard); null-side probes agree
- [x] **C++** · sweep — Same first-row probe in LINK: correct operand on each side?  
  → **REFUTED**: Calls first_collection_item on each side directly (no guard); null-side probes agree

<a id="sem-12"></a>
### SEM-12 · Python assignment shares nested scalar leaves (copy returns self)

**Severity (reviewer):** High · **Source:** found during validation of HYG-16 (not in either review)

python/sel/value.py:526-527 (_clone_at) returns the same Value for a scalar leaf below the top level instead of copying it. A later nested assignment that gives that leaf children then changes both variables, breaking "values alias; assignment copies" (spec §5.7).

> **Validation — CONFIRMED (Python only; new).** python/sel/value.py:526-527: _clone_at returns self for a leaf (no shape, no storage, no children) at depth > 1. A = LIST("s"); B = A; B[1]["k"] = "v" changes A[1] too in Python; also RECORD("x","s") via B["x"]["k"] and two levels deep (LIST(LIST("s"))). JS, PHP, C++ and Lisp leave A unchanged. Copies made by "," are unaffected (top-level clone). No conformance case covers a copied scalar leaf that later gains children (10-limits.selt:54 copies a record leaf).

| Program / action | Reported result | Expected |
|---|---|---|
| `A = LIST("s"); B = A; B[1]["k"] = "v"; LIST(A, B)` | A[1] gains k too (py) | A = {"1"=t"s"} (js, php, cpp, lisp) |
| `A = RECORD("x", "s"); B = A; B["x"]["k"] = "v"; A` | {"x"=t"s"{"k"=t"v"}} (py) | {"x"=t"s"} |

Variants / notes:
- Scalar leaf in a list, in a record, nested two levels
- Copies made by "," (child and value) as well as by assignment

Tests: conformance (value semantics / 10-limits.selt neighbour)

Related: [HYG-16](#hyg-16)

Checks:

- [x] **suite** · derived · `conformance/10-limits.selt:54 is the nearest case (record leaf, not scalar)` — Add the scalar-leaf cases  
  → **FIXED**: Fixed: python/sel/value.py _clone_at copies leaves too; conformance val.copy.* (validation: Gap; add the three repros)
- [x] **Python** · derived · `python/sel/value.py:526-527` — clone returns self for leaves  
  → **FIXED**: Fixed: python/sel/value.py _clone_at copies leaves too; conformance val.copy.* (validation: value.py:526-527)
- [x] **JS** · derived — Control  
  → **REFUTED**: A unchanged (correct)
- [x] **PHP** · derived — Control  
  → **REFUTED**: A unchanged (correct)
- [x] **C++** · derived — Control  
  → **REFUTED**: A unchanged (correct)
- [x] **Lisp** · derived — Control  
  → **REFUTED**: A unchanged (correct)

## SQL — SQL translation

Translator output that selects different data than in-memory evaluation.

<a id="sql-01"></a>
### SQL-01 · LINK binder resolves to the wrong relation in SQL

**Severity (reviewer):** High · **Source:** B: Python #2 (python/sel/sql/translator.py:420) — "the same mapping appears in other hosts; this needs a shared fix"; B §2

The translator associates both explicit LINK binders with the joined source, so a read through the left binder selects the right table's column. In memory the program returns the left value; the emitted SQL returns the right one.

> **Validation — CONFIRMED (JS, PHP, Python, C++); Lisp correct.** Probed with 10 temporary .sqlt cases in a scratch worktree (sqlite dialect; R(id,val) and S(id,val)). In memory A["VAL"]/B["VAL"] after R .> LINK(S, A, B, …) are 7/9 on every host. JS, PHP, Python and C++ translate every read through the LEFT explicit binder to the RIGHT table: _["SS"]["VAL"] and plain _["A"]["VAL"] → s.val; LINK_LEFT the same; with sides swapped (S .> LINK(R, A, B, …)) → r.val; FILTER(_["A"]["VAL"] > 5) renders WHERE s.val > 5, so it filters on the wrong column as well. Right binder reads and relation-name reads (_["R"], _["S"]) are correct. Lisp gets every probe right, so the translators already disagree. The .sqlt suite does not notice: no case reads the same field name through both binders. _["_1"] is refused with E_SQL_SHAPE on all five (a refusal, not wrong data). Python site: translator.py:420-424 and 457-458 list join.left_binder among the join source's names.

| Program / action | Reported result | Expected |
|---|---|---|
| `MAP(_["SS"]["VAL"]) after LINK(S, SS, X, …)` | SQL selected s.val → 9 (SQLite) | 7 (in-memory) |
| `R(ID=1,VAL=7), S(ID=1,VAL=9): R .> LINK(S, SS, X, SS["ID"] == X["ID"]) .> MAP(RECORD("picked", _["SS"]["VAL"]))` | ? | picked = 7; _["X"]["VAL"] = 9 |

Variants / notes:
- Left/right reversal
- _1/_2 binders
- Named relation binders
- LINK_LEFT
- A filter before the projection
- Only same-named fields on both sides reveal it

Tests: sql/cases/26-links.sqlt (pin the alias); SQL DB oracle (TEST-03, TEST-04)

Related: [TEST-03](#test-03), [TEST-04](#test-04), [TEST-11](#test-11)

Checks:

- [x] **suite** · derived · `sql/cases/26-links.sqlt` — Same field name on both sides with different values; pin aliases; variants  
  → **FIXED**: Fixed: a left binder names the source relation in the js/php/py/cpp translators; link.binder.* sqlt cases (validation: No 26-links case reads a shared field name through the left binder; the 10 probe cases are ready to become real cases)
- [x] **Python** · reported · `python/sel/sql/translator.py:420` — Binder → source association  
  → **FIXED**: Fixed: a left binder names the source relation in the js/php/py/cpp translators; link.binder.* sqlt cases (validation: translator.py:420-424 (_index_qualified) and 457-458 (_relation_table_alias) attach left_binder to the join source)
- [x] **JS** · reported — Reviewer: same mapping in other hosts — locate  
  → **FIXED**: Fixed: a left binder names the source relation in the js/php/py/cpp translators; link.binder.* sqlt cases (validation: p01, p03-p06 wrong)
- [x] **PHP** · reported — Reviewer: same mapping in other hosts — locate  
  → **FIXED**: Fixed: a left binder names the source relation in the js/php/py/cpp translators; link.binder.* sqlt cases (validation: p01, p03-p06 wrong)
- [x] **C++** · reported — Reviewer: same mapping in other hosts — locate  
  → **FIXED**: Fixed: a left binder names the source relation in the js/php/py/cpp translators; link.binder.* sqlt cases (validation: p01, p03-p06 wrong (built sqlt in the scratch worktree))
- [x] **Lisp** · reported — Reviewer: same mapping in other hosts — locate  
  → **REFUTED**: All binder probes correct (r.val for the left binder, including LINK_LEFT, reversed and FILTER)
- [x] **sql** · derived · `sql/dialects, docs/internals/sql-translation.md` — Is the binder→alias rule written down? Hybrid/relational-plan path too  
  → **FIXED**: Fixed: a left binder names the source relation in the js/php/py/cpp translators; link.binder.* sqlt cases (validation: The binder → alias rule is not written down in docs/internals/sql-translation.md (only a passing mention at :2524))

<a id="sql-02"></a>
### SQL-02 · Unicode case folding creates a SQL qualifier that does not exist in memory

**Severity (reviewer):** Medium · **Source:** B: Python #4 (python/sel/sql/translator.py:428); B §2 last paragraph

Python uses str.upper() when resolving qualifiers, so ß becomes SS. A read through _["ß"] raises E_NO_KEY in memory but translates as the SS binder in SQL. PHP refuses the translation. If a dialect cannot keep the names distinct safely, the translator must refuse.

> **Validation — CONFIRMED (JS, Python).** R .> LINK(S, SS, X, …) .> MAP(RECORD("picked", _["ß"]["VAL"])): in memory E_NO_KEY; JS and Python translate it as the SS binder (and, via SQL-01, then read s.val); the same for _["ſS"]. PHP, C++ and Lisp refuse with E_SQL_SHAPE ("ß names no relation of this statement"). Python site translator.py:429 (str.upper()), JS translator.mjs:491-531 (toUpperCase). Field-name lookups use ascii_upper already (translator.py:447).

| Program / action | Reported result | Expected |
|---|---|---|
| `read through _["ß"] with an SS binder in scope` | translates as SS (py) | refusal (php agrees); in memory E_NO_KEY |

Variants / notes:
- Separate .sqlt binding cases for "ß" and "SS"
- Field names, not just binder names
- Dialect identifier quoting

Tests: sql/cases/*.sqlt

Related: [SEM-04](#sem-04), [SEM-05](#sem-05)

Checks:

- [x] **suite** · derived · `sql/cases/` — ß / SS binding cases; refusal expected where unsafe  
  → **FIXED**: Fixed: ASCII qualifier folding in the js and py translators; link.binder.* sqlt cases (validation: No .sqlt case has a non-ASCII qualifier)
- [x] **Python** · reported · `python/sel/sql/translator.py:428` — str.upper() on qualifier  
  → **FIXED**: Fixed: ASCII qualifier folding in the js and py translators; link.binder.* sqlt cases (validation: translator.py:429, 457-465 str.upper())
- [x] **PHP** · control — Reviewer: PHP refuses correctly — confirm  
  → **REFUTED**: Refuses (E_SQL_SHAPE)
- [x] **JS** · sweep — Qualifier / field-name resolution using native Unicode case mapping  
  → **FIXED**: Fixed: ASCII qualifier folding in the js and py translators; link.binder.* sqlt cases (validation: translator.mjs:491, 522, 531 toUpperCase())
- [x] **C++** · sweep — Qualifier / field-name resolution using native Unicode case mapping  
  → **REFUTED**: Refuses (E_SQL_SHAPE)
- [x] **Lisp** · sweep — Qualifier / field-name resolution using native Unicode case mapping  
  → **REFUTED**: Refuses (E_SQL_SHAPE)

<a id="sql-03"></a>
### SQL-03 · Lisp reports a LINK key refusal before an earlier step's

**Severity (reviewer):** Low · **Source:** found by the SQL fuzzer's new families (TEST-05) during the fix phase; present at 7611b00

For R .> SELECT_COLS(...) .> SORT_BY(_["ID"]) .> LINK(...) the sort key was checked only when the statement was rendered, after the LINK predicate had been translated, so Lisp refused at the LINK where the other four hosts refuse at the SORT_BY.

> **Validation — CONFIRMED (new, Lisp only).** Found by the SQL fuzzer once TEST-05 widened it; reproduced at 7611b00 in a clean worktree.

| Program / action | Reported result | Expected |
|---|---|---|
| `a SORT_BY on a dropped column followed by a LINK with an unbound key` | E_SQL_BINDING at the LINK (lisp) | E_SQL_BINDING at the SORT_BY (1:37) |

Tests: sql/cases/26-links.sqlt link.refusal.an-earlier-step-refuses-first

Related: [TEST-05](#test-05)

Checks:

- [x] **suite** · derived · `sql/cases/26-links.sqlt` — Pin the refusal order  
  → **FIXED**: Fixed: case link.refusal.an-earlier-step-refuses-first
- [x] **Lisp** · reported · `lisp/src/sql/translator.lisp (LINK branch)` — Render the plan so far before the LINK  
  → **FIXED**: Fixed: the translator renders the plan so far before a LINK

<a id="sql-04"></a>
### SQL-04 · Lisp renders a qualified read of a field the relation lacks as a column

**Severity (reviewer):** Medium · **Source:** found by the SQL fuzzer's link-binder family (TEST-05) during the fix phase

A read such as _["X"]["customer_id"], where X names a relation without that field, was rendered as a column the table lacks instead of refused with E_SQL_BINDING, and the planner pushed it down whole.

> **Validation — CONFIRMED (new, Lisp only).** Found by the SQL fuzzer's link-binder family (TEST-05).

| Program / action | Reported result | Expected |
|---|---|---|
| `... .> MAP(RECORD("p", _["X"]["customer_id"])) with X = CUSTOMERS` | pure_sql with c.customer_id (lisp) | E_SQL_BINDING (other four hosts) |

Tests: sql/cases/26-links.sqlt link.binder.a-field-the-relation-lacks-is-refused

Related: [SQL-01](#sql-01)

Checks:

- [x] **suite** · derived · `sql/cases/26-links.sqlt` — Pin the refusal  
  → **FIXED**: Fixed: case link.binder.a-field-the-relation-lacks-is-refused
- [x] **Lisp** · reported · `lisp/src/sql/translator.lisp (index read)` — Refuse when the relation matches and the field does not  
  → **FIXED**: Fixed: a field the relation lacks is refused with E_SQL_BINDING

## HOST — Host API boundary

Native ⇄ Value conversion and constructors. Not expressible in .selt; needs spec wording plus per-host runtime/unit assertions.

<a id="host-01"></a>
### HOST-01 · toNative() loses an own "__proto__" field

**Severity (reviewer):** High · **Source:** B: JS #1 (js/src/value.mjs:548); B §3 JS objects

toNative writes record keys into a plain {}. An own "__proto__" key is lost on a fromNative → toNative round trip; if its value is a record, the returned object's prototype changes.

> **Validation — CONFIRMED (JS) + reserved-key "_" collision in JS, PHP, Python, Lisp.** js/src/value.mjs:548-549 writes keys into {}: fromNative(JSON {"__proto__":{"x":1},"a":2}).toNative() has no own __proto__ (keys ["a"]) and the record became the prototype (n.x === 1); a scalar __proto__ value vanishes ({}); nested inside a list the same. No other JS site builds a data-keyed plain object (sql/binding.mjs:209 and translator.mjs:2078 use Object.create(null); translator.mjs:3061 holds internal slot names). NEW, all four native hosts: toNative puts a value's own scalar under the key "_" next to its children (JS {_: scalar, ...obj}, value.mjs:550), so A = "s"; A["_"] = "c"; A converts to {"_":"c"} in JS, Python and PHP — the scalar "s" is lost — and in Lisp to (("_" . "s") ("_" . "c")), a duplicate key whose round trip keeps "c". The spec (§8) does not define the native mapping at all.

| Program / action | Reported result | Expected |
|---|---|---|
| `fromNative({["__proto__"]: …}) → toNative()` | key missing / prototype changed | Object.hasOwn(result,"__proto__") with its value, no inherited role |

Variants / notes:
- Nested inside a list
- Nested inside a record
- Value a record vs scalar
- Round-trip back through fromNative
- The current runtime test checks dump() for "__proto__", not toNative()

Tests: tools/check-js-runtime.mjs

Related: [TEST-06](#test-06)

Checks:

- [x] **JS** · reported · `js/src/value.mjs:548` — toNative record construction  
  → **FIXED**: Fixed: js toNative defines __proto__ as an own key; a scalar under "_" colliding with a field is E_BAD_ARG (js, php, py, lisp); SPEC §8 (validation: value.mjs:548-549; also the "_" scalar-slot collision (value.mjs:550))
- [x] **JS (sweep)** · derived · `js/src/**` — Every other site that writes user-controlled keys into {} (context objects, SQL params, bindings, harnesses)  
  → **REFUTED**: No other data-keyed plain object in js/src
- [x] **PHP** · sweep — Any reserved-key hazard in native conversion  
  → **FIXED**: Fixed: js toNative defines __proto__ as an own key; a scalar under "_" colliding with a field is E_BAD_ARG (js, php, py, lisp); SPEC §8 (validation: No __proto__ issue, but a child named "_" overwrites the scalar in toNative)
- [x] **Python** · sweep — Any reserved-key hazard in native conversion  
  → **FIXED**: Fixed: js toNative defines __proto__ as an own key; a scalar under "_" colliding with a field is E_BAD_ARG (js, php, py, lisp); SPEC §8 (validation: No __proto__ issue, but a child named "_" overwrites the scalar in to_native)
- [x] **C++** · sweep — Any reserved-key hazard in native conversion  
  → **N/A**: No fromNative/toNative in C++ (SPEC §8)
- [x] **Lisp** · sweep — Keys interned as symbols / keywords, plist/alist collisions  
  → **FIXED**: Fixed: js toNative defines __proto__ as an own key; a scalar under "_" colliding with a field is E_BAD_ARG (js, php, py, lisp); SPEC §8 (validation: to-native (value.lisp:634-670) emits a duplicate "_" key; from-native keeps the last)

<a id="host-02"></a>
### HOST-02 · Values retain caller-owned buffers and strings

**Severity (reviewer):** High · **Source:** B: JS #3 (js/src/value.mjs:145 — Uint8Array retained); B: Lisp #1 (lisp/src/value.lisp:154 — strings and octet vectors retained); B §3 JS binary, Lisp ownership

Construction keeps a reference to mutable caller data. JS: mutating the caller's Uint8Array changes the Value. Lisp: after from-native builds a record, changing the original key string "a"→"b" makes dump show "b" while value-has finds neither key; mutating a validated text string to an unpaired surrogate makes a later unrelated TO_UTF8 raise E_UTF8.

> **Validation — CONFIRMED (JS, Lisp).** JS value.mjs:145 keeps a Uint8Array as-is: mutating it after Value.bin or fromNative changes the value (b01 → b02). Lisp make-text/make-bin (value.lisp:154-166) and from-native keep caller strings and octet vectors: after mutating a record key "a"→"b" the dump shows "b" while value-get finds neither "a" nor "b" (the shape table is keyed on the mutated string); a validated text mutated to U+D800 makes a later TO_UTF8 raise E_UTF8; a mutated byte vector changes the value. Python copies (bytes()), PHP strings/arrays are values, C++ takes std::string/vector by value. SPEC §8 says constructors validate but says nothing about copying.

| Program / action | Reported result | Expected |
|---|---|---|
| `JS: b = new Uint8Array([1]); v = Value.bin(b); b[0] = 2` | v changes | v unchanged |
| `Lisp: from-native record, then mutate original key string` | dump "b"; value-has finds neither | lookup/dump/hash stable |

Variants / notes:
- Each caller-owned string key, text string, byte vector
- Assert lookup, dump, hash and binary content stay stable

Tests: tools/check-js-runtime.mjs; lisp/tests/unit.lisp

Related: [HOST-03](#host-03), [HOST-05](#host-05), [TEST-06](#test-06)

Checks:

- [x] **spec** · derived · `spec/SPEC.md §8 host interface` — Does the spec say construction copies (or that caller data must not be mutated)?  
  → **FIXED**: Fixed: js Value.bin and lisp make-text/make-bin/from-native copy their input; SPEC §8 (validation: SPEC §8 "Constructors validate at the boundary" has no ownership/copy rule)
- [x] **JS** · reported · `js/src/value.mjs:145` — Value.bin / fromNative keep the Uint8Array  
  → **FIXED**: Fixed: js Value.bin and lisp make-text/make-bin/from-native copy their input; SPEC §8 (validation: value.mjs:145 Value.bin; fromNative path too)
- [x] **Lisp** · reported · `lisp/src/value.lisp:154` — Strings and octet vectors kept; hash keys mutable  
  → **FIXED**: Fixed: js Value.bin and lisp make-text/make-bin/from-native copy their input; SPEC §8 (validation: value.lisp:154-166 + from-native: keys, text, bytes all retained)
- [x] **PHP** · sweep — Copy-on-write should make this n/a — confirm (objects? references?)  
  → **REFUTED**: Strings and arrays have value semantics
- [x] **Python** · sweep — bytearray / memoryview / list inputs retained?  
  → **REFUTED**: Value.bin/from_native copy bytearray input; str/bytes immutable
- [x] **C++** · sweep — span / string_view / pointer inputs retained?  
  → **REFUTED**: Value::text/bin take std::string / const vector& and copy

<a id="host-03"></a>
### HOST-03 · toNative() output shares structure with the Value

**Severity (reviewer):** Medium · **Source:** B §3 Lisp ownership ("also mutate native data returned by to-native"); derived for other hosts

The mirror of HOST-02: mutating what toNative returns must not change the original Value.

> **Validation — CONFIRMED (JS, Lisp).** JS toNative returns the internal Uint8Array: writing to it changes the Value (b01 → b09). Lisp to-native returns the internal octet vector and text string: writes show up in the Value (b09, "Zbc"). Python returns immutable bytes/str and fresh dicts; PHP returns value-semantics arrays/strings; C++ has no toNative.

| Program / action | Reported result | Expected |
|---|---|---|
| `n = toNative(v); mutate n (bytes, strings, nested containers)` | ? | v unchanged |

Variants / notes:
- Bytes
- Text (Lisp strings are mutable)
- Nested list/record

Tests: per-host runtime/unit lane

Related: [HOST-02](#host-02)

Checks:

- [x] **Lisp** · reported · `lisp/src/value.lisp (to-native)` — Returned strings / vectors shared?  
  → **FIXED**: Fixed: js and lisp toNative return copies; SPEC §8 (validation: to-native shares strings and octet vectors)
- [x] **JS** · sweep — Returned native data shared with Value internals?  
  → **FIXED**: Fixed: js and lisp toNative return copies; SPEC §8 (validation: toNative shares the Uint8Array)
- [x] **PHP** · sweep — Returned native data shared with Value internals?  
  → **REFUTED**: Value semantics
- [x] **Python** · sweep — Returned native data shared with Value internals?  
  → **REFUTED**: bytes/str immutable, containers fresh
- [x] **C++** · sweep — Returned native data shared with Value internals?  
  → **N/A**: No toNative in C++

<a id="host-04"></a>
### HOST-04 · Byte inputs outside 0..255 wrap instead of raising

**Severity (reviewer):** Medium · **Source:** B: JS #3 (js/src/value.mjs:145); B §3 JS binary

JS converts Value.bin([256]) to b00 and Value.bin([-1]) to bff. Python rejects both with E_RANGE.

> **Validation — CONFIRMED (JS); Lisp rejects with a different error class.** JS Value.bin (value.mjs:145, Uint8Array.from) wraps: [256]→b00, [-1]→bff, [NaN]→b00, [1.5]→b01, ["1"]→b01. Python raises E_RANGE for 256, -1, 1.5 and "1". Lisp make-bin rejects 256, -1 and 3/2 but with a CL TYPE-ERROR, not a SelError. PHP (Value::bin(string)) and C++ (std::string / vector<uint8_t>) cannot receive an out-of-range byte. SPEC §8 lists Value.num and Value.text validation but says nothing about Value.bin.

| Program / action | Reported result | Expected |
|---|---|---|
| `Value.bin([256])` | b00 (js) | E_RANGE (py) |
| `Value.bin([-1])` | bff (js) | E_RANGE (py) |

Variants / notes:
- -1, 255, 256
- Non-integers (1.5, NaN, "1")
- Via fromNative as well as Value.bin

Tests: tools/check-js-runtime.mjs + each host lane

Related: [TEST-06](#test-06)

Checks:

- [x] **spec** · derived · `spec/SPEC.md §8` — Does the spec say how invalid byte inputs are rejected, and with which code?  
  → **FIXED**: Fixed: js Value.bin and lisp make-bin raise E_RANGE outside 0..255; SPEC §8 (validation: SPEC §8 is silent on Value.bin input validation and its error code)
- [x] **JS** · reported · `js/src/value.mjs:145` — Uint8Array coercion wraps  
  → **FIXED**: Fixed: js Value.bin and lisp make-bin raise E_RANGE outside 0..255; SPEC §8 (validation: Wraps modulo 256, NaN → 0, truncates non-integers, coerces strings)
- [x] **Python** · control — Reviewer: rejects with E_RANGE — confirm  
  → **REFUTED**: E_RANGE for every out-of-range or non-integer byte (the control)
- [x] **PHP** · sweep — Byte-list / int-array inputs validated?  
  → **N/A**: Value::bin takes a byte string
- [x] **C++** · sweep — Byte-list / int-array inputs validated?  
  → **N/A**: Takes std::string or vector<uint8_t>
- [x] **Lisp** · sweep — Byte-list / int-array inputs validated?  
  → **FIXED**: Fixed: js Value.bin and lisp make-bin raise E_RANGE outside 0..255; SPEC §8 (validation: Rejects, but with CL TYPE-ERROR instead of E_RANGE (error-class parity only))

<a id="host-05"></a>
### HOST-05 · TEXT constructors accept invalid encoding

**Severity (reviewer):** Medium · **Source:** B: JS #4 (js/src/value.mjs:144 — lone surrogate via Value.text and fromNative); B: PHP #3 (php/src/Value.php:217 — "\xFF"; spec/SPEC.md:1017)

The host interface contract requires E_UTF8 at construction. JS accepts a lone UTF-16 surrogate through Value.text() and fromNative() and fails later. PHP Value::text() accepts "\xFF" although fromNative() validates the same input. Python rejects.

> **Validation — CONFIRMED (JS, PHP) + record keys unvalidated in all 5 hosts.** SPEC.md:1017: Value.text raises E_UTF8 on invalid input. JS accepts a lone surrogate through Value.text, fromNative and as a key (value.mjs:144). PHP Value::text("\xFF") is accepted (Value.php:217) while fromNative("\xFF") raises; fromNative(["\xFF" => "x"]) accepts the key. Python and Lisp reject invalid text in both constructors (E_UTF8) but accept an invalid key through from_native. C++ Value::text raises E_UTF8 but Value::set("\xff", …) accepts the key. Valid supplementary characters (U+1F600) are accepted everywhere (control). The spec sentence names Value.text only; keys are unspecified.

| Program / action | Reported result | Expected |
|---|---|---|
| `JS Value.text("\uD800")` | accepted | E_UTF8 |
| `PHP Value::text("\xFF")` | accepted | E_UTF8 |

Variants / notes:
- Valid supplementary character as control
- Record keys (keys are text too)
- Every public constructor, not just text()

Tests: tools/check-js-runtime.mjs; tools/check-php-runtime.php

Related: [HOST-02](#host-02), [TEST-06](#test-06)

Checks:

- [x] **spec** · derived · `spec/SPEC.md:1017` — Confirm wording covers every constructor and keys  
  → **FIXED**: Fixed: text and record keys are validated (E_UTF8) at every boundary constructor in all 5 hosts; SPEC §8 (validation: SPEC.md:1017 covers Value.text only; say that every text entering through the host API (keys included) is validated)
- [x] **JS** · reported · `js/src/value.mjs:144` — Value.text / fromNative  
  → **FIXED**: Fixed: text and record keys are validated (E_UTF8) at every boundary constructor in all 5 hosts; SPEC §8 (validation: Value.text, fromNative and keys all accept U+D800)
- [x] **PHP** · reported · `php/src/Value.php:217` — Value::text  
  → **FIXED**: Fixed: text and record keys are validated (E_UTF8) at every boundary constructor in all 5 hosts; SPEC §8 (validation: Value::text accepts "\xFF"; fromNative validates values but not keys)
- [x] **Python** · control — Reviewer: rejects with E_UTF8 — confirm, incl. key paths  
  → **FIXED**: Fixed: text and record keys are validated (E_UTF8) at every boundary constructor in all 5 hosts; SPEC §8 (validation: Values validated; from_native accepts an invalid key)
- [x] **C++** · sweep — Value::text and record-key constructors  
  → **FIXED**: Fixed: text and record keys are validated (E_UTF8) at every boundary constructor in all 5 hosts; SPEC §8 (validation: Value::text validates; Value::set accepts an invalid key)
- [x] **Lisp** · sweep — make-text / from-native / keys  
  → **FIXED**: Fixed: text and record keys are validated (E_UTF8) at every boundary constructor in all 5 hosts; SPEC §8 (validation: make-text / from-native values validated; from-native accepts an invalid key)

<a id="host-06"></a>
### HOST-06 · Native integers bypass the decimal digit cap

**Severity (reviewer):** Medium · **Source:** B: Lisp #2 (lisp/src/value.lisp:178 make-int); B: Lisp #2 "Python integer constructor appears to share this gap"; B §3 Numeric limits

make-int does not apply the decimal guard, so from-native(10^1,000,000) builds a 1,000,001-digit value and A == A returns TRUE; the specified result is E_RANGE.

> **Validation — CONFIRMED (Lisp, Python, JS).** MAX_INT_DIGITS = 1,000,000 (spec/limits.json:5, §6.4). Unbounded native integers bypass it: Lisp make-int / from-native (value.lisp:178, 610) accept 10^1000000 (1,000,001 digits) and A == A is TRUE, while make-num of the same digits as a string is E_RANGE; Python Value.int and from_native accept it (A == A TRUE; also no issue with the 4300-digit str limit); JS Value.int(BigInt) (value.mjs:206, D.fromInt) accepts it, whereas fromNative(BigInt) makes TEXT and Value.num(string) are E_RANGE. PHP Value::int(int) and C++ Value::integer(long long) are 64-bit, and their num(Dec) overloads take the internal type produced by the capped parser.

| Program / action | Reported result | Expected |
|---|---|---|
| `from-native(10^1000000); A == A` | TRUE (lisp) | E_RANGE |

Variants / notes:
- Exactly 1,000,000 digits (accept) and one beyond (E_RANGE)
- Negative
- Decimal strings via other constructors
- Keep in a slow lane that is still part of the required gate (TEST-07)

Tests: lisp/tests/unit.lisp; python/tests/test_unit.py

Related: [TEST-07](#test-07)

Checks:

- [x] **spec** · derived · `spec/limits.json / spec/limits.md` — Confirm the cap applies to host-constructed numbers  
  → **FIXED**: Fixed: native ints are capped at the decimal digit limit (E_RANGE) in js, py and lisp; SPEC §8 (validation: limits.md / §6.4 state the cap for numbers but not that host constructors enforce it; say so)
- [x] **Lisp** · reported · `lisp/src/value.lisp:178` — make-int / from-native  
  → **FIXED**: Fixed: native ints are capped at the decimal digit limit (E_RANGE) in js, py and lisp; SPEC §8 (validation: make-int, from-native: 1,000,001 digits accepted)
- [x] **Python** · repro — Reviewer: Value.int / from_native appears to share the gap  
  → **FIXED**: Fixed: native ints are capped at the decimal digit limit (E_RANGE) in js, py and lisp; SPEC §8 (validation: Value.int, from_native: 1,000,001 digits accepted)
- [x] **JS** · sweep — BigInt input to Value.int / fromNative  
  → **FIXED**: Fixed: native ints are capped at the decimal digit limit (E_RANGE) in js, py and lisp; SPEC §8 (validation: Value.int(BigInt) accepts; fromNative(BigInt) and Value.num(string) enforce the cap)
- [x] **PHP** · sweep — Native int is bounded; check decimal-string / float entry points  
  → **REFUTED**: Value::int takes a 64-bit int; Value::num(string) enforces the cap
- [x] **C++** · sweep — Decimal-from-string / big-int entry points  
  → **REFUTED**: Value::integer(long long); Value::num(string) enforces the cap

<a id="host-07"></a>
### HOST-07 · Structural hash stops at the depth cap instead of raising

**Severity (reviewer):** Medium · **Source:** B: Lisp #3 (lisp/src/value.lisp:498 value-hash); B §3 Lisp hash depth

value-hash returns 0 past the depth limit, so DEDUPE on a host-supplied over-deep item succeeds; dumping the same item raises the required E_DEPTH.

> **Validation — CONFIRMED (Lisp, JS, C++ DISTINCT); cross-host disagreement.** SPEC.md:525-529: a host-built value nested past the cap "can be held; it cannot be copied, compared, dumped or converted" — the walks enforce the cap. Test: one item nested 250 deep (built with the list API, passed as context A = LIST(item)). COUNT(DEDUPE(A)) / COUNT(DISTINCT(A)) / COUNT(BUCKET(A, _, COUNT(_))): Lisp 1/1/1 (value-hash returns 0 past the cap, value.lisp:498); JS 1/1/1 (structuralHash returns 0 past the cap, value.mjs:556); C++ E_DEPTH/1/E_DEPTH (DISTINCT, sel.cpp:5215, never hashes and is pairwise O(n²), so one item is never walked); Python and PHP E_DEPTH on all three. At depth 198 every host answers 1. With two equal deep items EQL raises E_DEPTH everywhere, so the hole needs the hash alone to decide (one item, or distinct hashes).

| Program / action | Reported result | Expected |
|---|---|---|
| `COUNT(DEDUPE(A)) with one over-deep item in host context` | 1 (lisp) | E_DEPTH |

Variants / notes:
- Just below and just beyond MAX_DEPTH
- DEDUPE and DISTINCT
- Do not dump the input first (a dump would catch it)
- Other recursive walks: equality, ordering, BUCKET keys, LINK hash keys, clone, TO_JSON

Tests: lisp/tests/unit.lisp; conformance/21-structural-hash-identity.selt (where expressible)

Related: [TEST-06](#test-06)

Checks:

- [x] **Lisp** · reported · `lisp/src/value.lisp:498` — value-hash depth handling  
  → **FIXED**: Fixed: js and lisp structural hashing raise E_DEPTH; lisp BUCKET keys hash deeply; C++ DISTINCT shares DEDUPE's code (validation: value-hash (value.lisp:498) returns 0 past the cap: DEDUPE, DISTINCT, BUCKET(…, proj) answer 1)
- [x] **Lisp (sweep)** · derived · `lisp/src/**` — Other recursive walks over values: counted and raising?  
  → **FIXED**: Fixed: js and lisp structural hashing raise E_DEPTH; lisp BUCKET keys hash deeply; C++ DISTINCT shares DEDUPE's code (validation: EQL, dump, from-native, to-native raise correctly; only the hash (and so every hash-based walk: DEDUPE, DISTINCT, BUCKET identity) is silent)
- [x] **JS** · sweep — Structural hash and every recursive walk over host-supplied values: E_DEPTH past the cap?  
  → **FIXED**: Fixed: js and lisp structural hashing raise E_DEPTH; lisp BUCKET keys hash deeply; C++ DISTINCT shares DEDUPE's code (validation: structuralHash (value.mjs:556) returns 0 past the cap: same three answers of 1)
- [x] **PHP** · sweep — Structural hash and every recursive walk over host-supplied values: E_DEPTH past the cap?  
  → **REFUTED**: E_DEPTH on all three
- [x] **Python** · sweep — Structural hash and every recursive walk over host-supplied values: E_DEPTH past the cap?  
  → **REFUTED**: E_DEPTH on all three
- [x] **C++** · sweep — Structural hash and every recursive walk over host-supplied values: E_DEPTH past the cap?  
  → **FIXED**: Fixed: js and lisp structural hashing raise E_DEPTH; lisp BUCKET keys hash deeply; C++ DISTINCT shares DEDUPE's code (validation: DEDUPE and BUCKET raise; DISTINCT (sel.cpp:5215) never hashes and answers 1 (and is O(n²) unlike DEDUPE))

<a id="host-08"></a>
### HOST-08 · Native round trip loses sparse list keys and numeric-looking record keys

**Severity (reviewer):** Medium · **Source:** B: PHP #2 (php/src/Value.php:1097); B §3 PHP conversion

PHP toNative exports every list as a packed array. A filtered list with keys "2","3" comes back as "1","2"; a record keyed "0","1" comes back as a list keyed "1","2".

> **Validation — CONFIRMED (PHP only).** php/src/Value.php:1097 exports list-shaped values as packed PHP arrays: FILTER(LIST(1,2,3), _ > 1) (keys "2","3") → ["2","3"] → keys "1","2"; RECORD("0","a","1","b") → ["a","b"] → keys "1","2". A record keyed "1","2" survives. JS, Python and Lisp round-trip all three exactly (they export keyed maps / alists). C++ has no native conversion. SPEC §8 does not define the native mapping, so "round trip preserves keys" is not written down.

| Program / action | Reported result | Expected |
|---|---|---|
| `toNative/fromNative of FILTER(LIST(1,2,3), _ > 1)` | keys "1","2" (php) | keys "2","3" or a documented refusal |
| `toNative/fromNative of RECORD("0",…, "1",…)` | list keyed "1","2" (php) | record keyed "0","1" |

Variants / notes:
- Compare SEL dumps before and after
- Compare the native array shape
- Value::fromEntries with "1x"/"1y"

Tests: tools/check-php-runtime.php + each host lane

Related: [SEM-09](#sem-09), [SEM-10](#sem-10), [HOST-09](#host-09), [TEST-06](#test-06)

Checks:

- [x] **spec** · derived · `spec/SPEC.md §8` — Is the native mapping of sparse lists / numeric-looking keys specified?  
  → **FIXED**: Fixed: php toNative keeps the kept keys of a sparse list; SPEC §8 names the 0..n-1 record exception (validation: SPEC §8 defines no native mapping or round-trip guarantee)
- [x] **PHP** · reported · `php/src/Value.php:1097` — toNative list export  
  → **FIXED**: Fixed: php toNative keeps the kept keys of a sparse list; SPEC §8 names the 0..n-1 record exception (validation: Value.php:1097 packs lists; "0","1" records read back as a list)
- [x] **JS** · sweep — toNative/fromNative of sparse lists and records with "0","1" keys  
  → **REFUTED**: All three round trips exact
- [x] **Python** · sweep — toNative/fromNative of sparse lists and records with "0","1" keys  
  → **REFUTED**: All three round trips exact
- [x] **C++** · sweep — toNative/fromNative of sparse lists and records with "0","1" keys  
  → **N/A**: No native conversion
- [x] **Lisp** · sweep — toNative/fromNative of sparse lists and records with "0","1" keys  
  → **REFUTED**: All three round trips exact

<a id="host-09"></a>
### HOST-09 · Native round trip collapses FALSE, empty list and NULL

**Severity (reviewer):** Medium · **Source:** B: Lisp #4 (lisp/src/value.lisp:604); B §3 Lisp native booleans

to-native / from-native map FALSE, an empty list and NULL to NIL; round-tripping FALSE or an empty list yields NULL. Needs an explicit representation contract. (Empty LIST() and NULL already share the empty NONE structure in SEL.)

> **Validation — CONFIRMED (Lisp only).** lisp/src/value.lisp:634-670 to-native maps FALSE to NIL, the same as NULL and an empty list, so FALSE round-trips to NULL — also nested: RECORD("a",FALSE) → (("a")) → {"a"=NULL}. JS, Python and PHP map FALSE to their native false and back to FALSE, nested too. LIST() and NULL are the same empty NONE in SEL and become null/None/NULL/NIL everywhere (not a defect). The Lisp representation needs a distinct false (e.g. :false) and a written contract.

| Program / action | Reported result | Expected |
|---|---|---|
| `from-native(to-native(FALSE))` | NULL (lisp) | FALSE |

Variants / notes:
- FALSE and NULL independently; assert kinds
- Empty record vs empty list (PHP [] ambiguity)
- Nested inside records

Tests: lisp/tests/unit.lisp + each host lane

Related: [HOST-08](#host-08)

Checks:

- [x] **spec** · derived · `spec/SPEC.md §8` — Write the representation contract per host  
  → **FIXED**: Fixed: lisp FALSE converts to and from :false; SPEC §8 (validation: No native representation contract in SPEC §8)
- [x] **Lisp** · reported · `lisp/src/value.lisp:604` — NIL ambiguity  
  → **FIXED**: Fixed: lisp FALSE converts to and from :false; SPEC §8 (validation: FALSE → NIL → NULL, top level and nested)
- [x] **JS** · sweep — FALSE / NULL / empty list / empty record round trips  
  → **REFUTED**: false round-trips
- [x] **PHP** · sweep — FALSE / NULL / empty list / empty record round trips  
  → **REFUTED**: false round-trips
- [x] **Python** · sweep — FALSE / NULL / empty list / empty record round trips  
  → **REFUTED**: False round-trips
- [x] **C++** · sweep — FALSE / NULL / empty list / empty record round trips  
  → **N/A**: No native conversion

<a id="host-10"></a>
### HOST-10 · Compiled program keeps per-node state across runs

**Severity (reviewer):** High · **Source:** B: Python #1 ("reusing a compiled program can also return the previous run's field"); B §3 last paragraph

Separate .selt cases compile separately and cannot see state retained across runs. Any per-node cache must be keyed or reset so a second run with different context behaves like a fresh compile.

> **Validation — CONFIRMED (Python only).** One compiled program, two runs with different context keys: Python p = compile("A[K]") gives 1 then 1 (expected 1 then 2); compile("MAP(L, A[_])") with L = ["x"] then ["y"] gives 1 then 1. Same cause as SEM-01 (node._cached_slot keyed on the record shape, which is shared by equal-keyed records across runs). JS, PHP, C++ and Lisp give 1 then 2. Only the parser-node slot cache was found holding state across runs in Python (parser.py:129; structure.py:643 resets it on copies).

| Program / action | Reported result | Expected |
|---|---|---|
| `p = compile('A[K]'); p.run({A:…, K:"x"}); p.run({A:…, K:"y"})` | second run reads "x" field (py) | "y" field |

Variants / notes:
- Different context keys
- Differently shaped records
- Concurrent/interleaved runs where the host allows

Tests: per-host unit / API lane (tools/check-api.sh?)

Related: [SEM-01](#sem-01)

Checks:

- [x] **Python** · reported · `python/sel/eval.py:273` — Run the compiled program twice  
  → **FIXED**: Fixed: same change as SEM-01 (validation: Second run returns the first run's field)
- [x] **JS** · sweep — Any cache attached to compiled nodes; test the two-run scenario  
  → **REFUTED**: 1 then 2
- [x] **PHP** · sweep — Any cache attached to compiled nodes; test the two-run scenario  
  → **REFUTED**: 1 then 2
- [x] **C++** · sweep — Any cache attached to compiled nodes; test the two-run scenario  
  → **REFUTED**: 1 then 2
- [x] **Lisp** · sweep — Any cache attached to compiled nodes; test the two-run scenario  
  → **REFUTED**: 1 then 2

## TEST — Test-suite and oracle gaps

Infrastructure the reviewer asked for so each finding and its neighbours stay caught.

<a id="test-01"></a>
### TEST-01 · Normative .selt cases for every SEM finding

**Severity (reviewer):** — · **Source:** B §1

Add minimal cases to conformance/06-aggregates.selt, 15-relational.selt, 16-joined-rows.selt, 21-structural-hash-identity.selt. Each asserts the result or error directly; host agreement is not enough. For errors, pin the code first, then the position once the normative position is confirmed.

> **Validation — CONFIRMED (gaps real).** None of the confirmed SEM behaviours is pinned by a .selt case (checked per finding: SEM-01, 02/03, 04, 06, 07/08, 09). Minimal cases and variants are recorded under each SEM finding. HOST-07 (over-deep values) cannot be written in .selt: SEL source cannot build a value past the cap without raising at the assignment target (SPEC.md:514-522), so it belongs in the host lanes (TEST-06).

Related: [SEM-01](#sem-01), [SEM-02](#sem-02), [SEM-04](#sem-04), [SEM-06](#sem-06), [SEM-07](#sem-07), [SEM-09](#sem-09)

Checks:

- [x] **suite** · derived · `conformance/06-aggregates.selt` — SEM-02, SEM-03  
  → **FIXED**: Fixed: cases added in conformance 04, 06, 15 and 16 (validation: 06-aggregates: SEM-02/03 (TOP* with FILTER-kept keys))
- [x] **suite (15)** · derived · `conformance/15-relational.selt` — SEM-06, SEM-07  
  → **FIXED**: Fixed: cases added in conformance 04, 06, 15 and 16 (validation: 15-relational: SEM-06 (BIN/BOOL/list/invalid-numeric equijoin keys + IF controls), SEM-07/08 (SORT/MAP→FILTER→MAP, MAP→TOP_BY, FILTER fusion error order))
- [x] **suite (16)** · derived · `conformance/16-joined-rows.selt` — SEM-04, SEM-11  
  → **FIXED**: Fixed: cases added in conformance 04, 06, 15 and 16 (validation: 16-joined-rows: SEM-04 (ß/SS, ſ/s, é/É promotion). SEM-11 needs no case (unobservable))
- [x] **suite (21)** · derived · `conformance/21-structural-hash-identity.selt` — HOST-07 where expressible  
  → **N/A**: Over-deep values are not constructible from SEL source; HOST-07 goes to TEST-06

<a id="test-02"></a>
### TEST-02 · .sqlt cases for LINK binders and Unicode names

**Severity (reviewer):** — · **Source:** B §2

sql/cases/26-links.sqlt: same field name on both sides with different values; pin the actual SQL alias. Separate binding cases for "ß" and "SS". Snapshots alone can agree on the wrong alias, hence TEST-03/04.

> **Validation — CONFIRMED.** The 10 probe cases written for SQL-01/SQL-02 fail on 3-4 hosts each and no existing case catches either defect. They are saved as docs/interim/2026-09-25/sql-probes-SQL-01-02.sqlt (sqlite dialect; expected strings for p06 and the error cases are placeholders — take them from the corrected translator).

Related: [SQL-01](#sql-01), [SQL-02](#sql-02)

Checks:

- [x] **suite** · derived · `sql/cases/26-links.sqlt` — Cases + regenerate case data (node tools/gen-sql-cases.mjs)  
  → **FIXED**: Fixed: sql/cases/26-links.sqlt link.binder.* and link.refusal.* (validation: Gap real; probe cases saved in docs/interim/2026-09-25/sql-probes-SQL-01-02.sqlt (not in sql/cases))

<a id="test-03"></a>
### TEST-03 · SQL DB oracle: two-relation contexts

**Severity (reviewer):** — · **Source:** B §2

Extend sql/oracle/statements.json and its loader (php/bin/sqlo:735) to load two relation contexts from the database fixture. Compare the projected SEL value with executed SQL for every supported dialect, inline and prepared.

> **Validation — CONFIRMED.** php/bin/sqlo:737-745 builds exactly one relation context ($context = [relation => rows]) from spec.context; statements.json has one context. Two-relation LINK statements cannot be checked against a database today.

Related: [SQL-01](#sql-01)

Checks:

- [x] **tool** · derived · `sql/oracle/statements.json; php/bin/sqlo:735` — Two-relation fixture + comparisons  
  → **FIXED**: Fixed: sql/oracle/cross.selc with fixture-cross-*.sql; php/bin/sqlo cross mode (validation: Single-relation loader)

<a id="test-04"></a>
### TEST-04 · SQL DB oracle runs only the PHP translator

**Severity (reviewer):** — · **Source:** B §2 (tools/impls.sh:346)

A defect shared by all translators passes the oracle. Have each host emit its SQL into the existing PHP database executor, or give each host an equivalent execution lane.

> **Validation — CONFIRMED.** tools/impls.sh:346-353 runs the DB oracle for PHP only; the comment reasons that the dialect map is shared data, so a second harness would ask the server the same question. SQL-01 shows the per-host translator WALK disagrees (Lisp right, the other four wrong), which that reasoning does not cover.

Related: [SQL-01](#sql-01), [TEST-03](#test-03)

Checks:

- [x] **tool** · reported · `tools/impls.sh:346` — Oracle roster  
  → **FIXED**: Fixed: each host's sqlfuzz has a statement mode; tools/check-sql-oracle.sh runs every host's statements through sqlo cross (validation: impls.sh:346-353 php only)
- [x] **JS** · derived — Emit SQL for the oracle executor  
  → **FIXED**: Fixed: each host's sqlfuzz has a statement mode; tools/check-sql-oracle.sh runs every host's statements through sqlo cross (validation: No oracle lane for this translator)
- [x] **Python** · derived — Emit SQL for the oracle executor  
  → **FIXED**: Fixed: each host's sqlfuzz has a statement mode; tools/check-sql-oracle.sh runs every host's statements through sqlo cross (validation: No oracle lane for this translator)
- [x] **C++** · derived — Emit SQL for the oracle executor  
  → **FIXED**: Fixed: each host's sqlfuzz has a statement mode; tools/check-sql-oracle.sh runs every host's statements through sqlo cross (validation: No oracle lane for this translator)
- [x] **Lisp** · derived — Emit SQL for the oracle executor  
  → **FIXED**: Fixed: each host's sqlfuzz has a statement mode; tools/check-sql-oracle.sh runs every host's statements through sqlo cross (validation: No oracle lane for this translator)

<a id="test-05"></a>
### TEST-05 · SQL fuzzer targeted families

**Severity (reviewer):** — · **Source:** B §4 last paragraph

Add the same targeted families to the SQL fuzzer (tools/fuzz-sql.sh): same-named fields across LINK sides, Unicode key pairs, direct equijoin comparisons, error-producing operations before filters. Coverage counters; each family at least once.

> **Validation — CONFIRMED.** tools/fuzz-sql.sh uses tools/gen-programs.mjs --sql. Its LINK step (gen-programs.mjs:158) only puts binders in the predicate; later steps read promoted fields via rowRef(_), never _["O"]["field"], so SQL-01 is unreachable; field names and relations are fixed ASCII (ORDERS/CUSTOMERS), so SQL-02 is unreachable. No coverage counters.

Related: [SQL-01](#sql-01), [SQL-02](#sql-02), [TEST-10](#test-10)

Checks:

- [x] **tool** · derived · `tools/fuzz-sql.sh + generator` — Families + counters  
  → **FIXED**: Fixed: tools/gen-programs.mjs --sql families (link binders, qualifiers, refusals) with per-family counters (validation: Families absent)

<a id="test-06"></a>
### TEST-06 · Host-boundary assertions in every runtime/unit lane

**Severity (reviewer):** — · **Source:** B §3

The HOST findings cannot be written as .selt. Add assertions per host lane. The reviewer named four lanes; C++ has build/unit and build/api too.

> **Validation — CONFIRMED.** All four named lanes exist (tools/check-js-runtime.mjs, tools/check-php-runtime.php, python/tests/test_unit.py, lisp/tests/unit.lisp; C++ has cpp/tests/unit.cpp) but none asserts toNative round trips, byte-range input, caller-buffer ownership, digit caps on native ints, hash depth or compiled-program reuse. The JS lane checks "__proto__" through dump() only.

Variants / notes:
- JS: __proto__ toNative (nested), Uint8Array mutation, bytes -1/255/256/non-integer, lone surrogate + supplementary control
- PHP: filtered list keys "2","3" round trip; numeric-looking record keys; fromEntries "1x"/"1y"; Value::text malformed UTF-8
- Python: compiled Program run twice; Value.int / from_native digit cap
- Lisp: ownership mutations (keys, text, bytes, to-native output); digit cap; DEDUPE/DISTINCT depth; FALSE/NULL round trip
- C++: the same set (derived)

Related: [HOST-01](#host-01), [HOST-02](#host-02), [HOST-03](#host-03), [HOST-04](#host-04), [HOST-05](#host-05), [HOST-06](#host-06), [HOST-07](#host-07), [HOST-08](#host-08), [HOST-09](#host-09), [HOST-10](#host-10)

Checks:

- [x] **JS** · reported · `tools/check-js-runtime.mjs` — HOST-01/02/04/05  
  → **FIXED**: Fixed: host-boundary assertions in the js and php runtime lanes, python/tests/test_host_boundary.py, lisp host-boundary-* tests, cpp test_host_boundary (validation: Lane exists; the HOST-finding assertions are absent)
- [x] **PHP** · reported · `tools/check-php-runtime.php` — HOST-05/08, SEM-09 fromEntries  
  → **FIXED**: Fixed: host-boundary assertions in the js and php runtime lanes, python/tests/test_host_boundary.py, lisp host-boundary-* tests, cpp test_host_boundary (validation: Lane exists; the HOST-finding assertions are absent)
- [x] **Python** · reported · `python/tests/test_unit.py` — HOST-06/10  
  → **FIXED**: Fixed: host-boundary assertions in the js and php runtime lanes, python/tests/test_host_boundary.py, lisp host-boundary-* tests, cpp test_host_boundary (validation: Lane exists; the HOST-finding assertions are absent)
- [x] **Lisp** · reported · `lisp/tests/unit.lisp` — HOST-02/03/06/07/09  
  → **FIXED**: Fixed: host-boundary assertions in the js and php runtime lanes, python/tests/test_host_boundary.py, lisp host-boundary-* tests, cpp test_host_boundary (validation: Lane exists; the HOST-finding assertions are absent)
- [x] **C++** · derived · `cpp unit / api` — Same set for C++  
  → **FIXED**: Fixed: host-boundary assertions in the js and php runtime lanes, python/tests/test_host_boundary.py, lisp host-boundary-* tests, cpp test_host_boundary (validation: Lane exists; the HOST-finding assertions are absent)

<a id="test-07"></a>
### TEST-07 · Slow lane for boundary tests, inside the required gate

**Severity (reviewer):** — · **Source:** B §3 Numeric limits

The 1,000,000-digit boundary tests are expensive. Put them in a defined slow lane that tools/check.sh still runs.

> **Validation — CONFIRMED.** tools/check.sh has no slow lane (no "slow" anywhere in it); the 1,000,000-digit boundary tests need one.

Related: [HOST-06](#host-06)

Checks:

- [x] **tool** · derived · `tools/check.sh` — Slow lane wiring  
  → **WON'T FIX**: Not needed: the million-digit boundary tests take under 6 s per host and already run in the required lanes (validation: No slow lane)

<a id="test-08"></a>
### TEST-08 · Joined-row oracle: Unicode fields, BIN/TEXT keys, invalid numerics

**Severity (reviewer):** — · **Source:** B §4 (tools/join-rows-oracle/gen.py)

The generator draws equality keys from integers and NULL only, so it cannot reach SEM-04/SEM-06. Add Unicode field pairs, BIN/TEXT join keys and invalid numeric keys; have the model predict errors as well as rows; require a non-zero count of each category per corpus.

> **Validation — CONFIRMED.** tools/join-rows-oracle/gen.py:57-58 draws join keys from ("t","1".."3") and NULL; FIELDS are ASCII (gen.py:18). It cannot reach SEM-04 or SEM-06.

Related: [SEM-04](#sem-04), [SEM-06](#sem-06)

Checks:

- [x] **tool** · derived · `tools/join-rows-oracle/gen.py` — Families + error prediction + counters  
  → **FIXED**: Fixed: join-rows model covers Unicode names, BIN/TEXT keys and predicted errors, with per-category counts that must be non-zero (validation: Integer/NULL keys, ASCII fields only)

<a id="test-09"></a>
### TEST-09 · Metamorphic oracle: pipeline as written vs stages bound to variables

**Severity (reviewer):** — · **Source:** B §4 (generalise tools/join-filter-oracle/run.sh)

Generate each pipeline as written and with each stage assigned to a helper variable. Compare a host with itself before comparing hosts, so a rewrite shared by all hosts is caught. Normalise error positions relative to the source segment. Include deliberately failing expressions and sparse-key observations.

> **Validation — CONFIRMED.** tools/join-filter-oracle binds only LINK results to helper variables (run.sh header, gen.py docstring). The "as written vs bound stage" comparison is never applied to SORT/MAP/TOP/FILTER stages, which is exactly where SEM-07/08 live.

Related: [SEM-07](#sem-07), [SEM-08](#sem-08)

Checks:

- [x] **tool** · derived · `tools/join-filter-oracle/run.sh` — Generalise  
  → **FIXED**: Fixed: join-filter oracle runs a second pipeline pass (validation: LINK stages only)

<a id="test-10"></a>
### TEST-10 · Program generator: targeted families with coverage counters

**Severity (reviewer):** — · **Source:** B §4 (tools/gen-programs.mjs)

Random combinations of ASCII fields and short pipelines rarely produce these shapes. Add families: computed keys that change between rows; sparse _K followed by TOP_BY; Unicode key pairs; direct equijoin comparisons; error-producing operations before filters. Require each family at least once per run.

> **Validation — CONFIRMED.** tools/gen-programs.mjs has no computed-key-per-row index, no FILTER→TOP_BY _K family, only ASCII field names in the relational steps, no BIN/BOOL join keys, and no erroring stage before FILTER; its 20% "X = pipeline; X .> step" form compares hosts with each other, never a host with itself. No coverage counters.

Related: [SEM-01](#sem-01), [SEM-02](#sem-02), [SEM-04](#sem-04), [SEM-06](#sem-06), [SEM-07](#sem-07)

Checks:

- [x] **tool** · derived · `tools/gen-programs.mjs` — Families + counters  
  → **FIXED**: Fixed: gen-programs families for the review's constructs, with counters (validation: Families absent)

<a id="test-11"></a>
### TEST-11 · Keep minimized fuzz findings as permanent conformance cases

**Severity (reviewer):** — · **Source:** B §4

Process rule: every minimized disagreement becomes a .selt/.sqlt case before any host is fixed (already the repo rule; make the new families feed it).

> **Validation — REFUTED (rule exists).** docs/contributing.md:445 already says "When the fuzzer finds a disagreement, add the minimal case first, then fix". What is missing is the families that would find these (TEST-05, TEST-10).

Related: [TEST-05](#test-05), [TEST-10](#test-10)

Checks:

- [x] **tool** · derived · `docs/contributing.md` — Confirm the rule is written; wire the families to it  
  → **REFUTED**: Rule written at docs/contributing.md:445

## HYG — Dead, unused and duplicated code

Behaviour-neutral cleanups (review A). Validate each claim before touching; keep hosts file-for-file alike.

<a id="hyg-01"></a>
### HYG-01 · is_vacuous(): is_null() check subsumed by the next test

**Severity (reviewer):** Low · **Source:** A §2 Pattern 1; A §4 Task 3

is_null() is kind == NONE && size() == 0 && !is_list; the next line tests kind == NONE && size() == 0, a superset.

> **Validation — CONFIRMED (all 5).** is_null() is kind NONE && size 0 && !is_list; the next test (kind NONE && size 0) is a superset, so the first line never changes the answer. Verified at js/src/value.mjs:131-132, php/src/Value.php:440-445, python/sel/value.py:208-211, cpp/sel.cpp:1765-1766, lisp/src/value.lisp:214-215.

Related: [HYG-02](#hyg-02)

Checks:

- [x] **JS** · reported · `js/src/value.mjs:130-132`  
  → **FIXED**: Fixed: is_vacuous opens with the NONE-and-empty test alone (all 5 hosts) (validation: Redundant first test (behaviour-neutral))
- [x] **PHP** · reported · `php/src/Value.php:440-442`  
  → **FIXED**: Fixed: is_vacuous opens with the NONE-and-empty test alone (all 5 hosts) (validation: Redundant first test (behaviour-neutral))
- [x] **Python** · reported · `python/sel/value.py:208-211`  
  → **FIXED**: Fixed: is_vacuous opens with the NONE-and-empty test alone (all 5 hosts) (validation: Redundant first test (behaviour-neutral))
- [x] **C++** · reported · `cpp/sel.cpp:1765-1766`  
  → **FIXED**: Fixed: is_vacuous opens with the NONE-and-empty test alone (all 5 hosts) (validation: Redundant first test (behaviour-neutral))
- [x] **Lisp** · reported · `lisp/src/value.lisp:214-215`  
  → **FIXED**: Fixed: is_vacuous opens with the NONE-and-empty test alone (all 5 hosts) (validation: Redundant first test (behaviour-neutral))

<a id="hyg-02"></a>
### HYG-02 · `is_null() || (NONE && size == 0)` idiom

**Severity (reviewer):** Low · **Source:** A §2 Pattern 2; A §3.3 Duplicated #4 (python do_top)

first_collection_item() tests is_null() before the superset condition. Python do_top repeats it. Sweep for the idiom elsewhere.

> **Validation — CONFIRMED (all 5) + do_top in JS, C++, Lisp.** first_collection_item tests is_null() || (NONE && size 0): the right side is a superset. Actual lines: js/src/builtins/structure.mjs:13 (review said :30), php/src/Builtins/Structure.php:68 (review said :33), python structure.py:25, cpp/sel.cpp:3806, lisp structure.lisp:114. The same pair opens do_top in Python (aggregate.py:441-444, reported), JS (aggregate.mjs:374-375), C++ (sel.cpp:5532-5534, size check via collection_size) and Lisp (aggregate.lisp:431); PHP doTop tests isNull only.

Related: [HYG-01](#hyg-01), [SEM-11](#sem-11)

Checks:

- [x] **JS** · reported · `js/src/builtins/structure.mjs:30`  
  → **FIXED**: Fixed: first_collection_item and do_top drop the is_null half of the idiom (all 5 hosts; C++ do_top tests collection_size once) (validation: first_collection_item: redundant is_null())
- [x] **PHP** · reported · `php/src/Builtins/Structure.php:33`  
  → **FIXED**: Fixed: first_collection_item and do_top drop the is_null half of the idiom (all 5 hosts; C++ do_top tests collection_size once) (validation: first_collection_item: redundant is_null())
- [x] **Python** · reported · `python/sel/builtins/structure.py:25; python/sel/builtins/aggregate.py:441-444 (do_top)`  
  → **FIXED**: Fixed: first_collection_item and do_top drop the is_null half of the idiom (all 5 hosts; C++ do_top tests collection_size once) (validation: first_collection_item: redundant is_null())
- [x] **C++** · reported · `cpp/sel.cpp:3806`  
  → **FIXED**: Fixed: first_collection_item and do_top drop the is_null half of the idiom (all 5 hosts; C++ do_top tests collection_size once) (validation: first_collection_item: redundant is_null())
- [x] **Lisp** · reported · `lisp/src/builtins/structure.lisp:114`  
  → **FIXED**: Fixed: first_collection_item and do_top drop the is_null half of the idiom (all 5 hosts; C++ do_top tests collection_size once) (validation: first_collection_item: redundant is_null())
- [x] **JS (sweep)** · derived — Other occurrences of the idiom (e.g. do_top in non-Python hosts)  
  → **FIXED**: Fixed: first_collection_item and do_top drop the is_null half of the idiom (all 5 hosts; C++ do_top tests collection_size once) (validation: doTop aggregate.mjs:374-375)
- [x] **PHP (sweep)** · derived — Other occurrences of the idiom (e.g. do_top in non-Python hosts)  
  → **REFUTED**: doTop tests isNull() only (Structure.php:1471)
- [x] **Python (sweep)** · derived — Other occurrences of the idiom (e.g. do_top in non-Python hosts)  
  → **REFUTED**: Only the reported do_top site
- [x] **C++ (sweep)** · derived — Other occurrences of the idiom (e.g. do_top in non-Python hosts)  
  → **FIXED**: Fixed: first_collection_item and do_top drop the is_null half of the idiom (all 5 hosts; C++ do_top tests collection_size once) (validation: do_top sel.cpp:5532-5534)
- [x] **Lisp (sweep)** · derived — Other occurrences of the idiom (e.g. do_top in non-Python hosts)  
  → **FIXED**: Fixed: first_collection_item and do_top drop the is_null half of the idiom (all 5 hosts; C++ do_top tests collection_size once) (validation: do-top-sort aggregate.lisp:431 (value-null-p then zerop size))

<a id="hyg-03"></a>
### HYG-03 · do_sort(): 3-arg text branch identical to the final else

**Severity (reviewer):** Medium · **Source:** A §2 Pattern 3; A §4 Task 3

In the 3-argument SORT/SORT_BY form, the `node(2) is text` branch and the fallback else set binder "_", body node(1), direction text(2) upper-cased identically.

> **Validation — REFUTED as proposed.** The two bodies are identical, but the text branch is load-bearing: it sits BEFORE the is_symbol(1) branch, so SORT_BY(L, X, "DESC") reads X as the key and "DESC" as the direction (spec §7.3: a text literal in the third slot is the direction). Deleting it lets is_symbol(1) take X as a binder and "DESC" as the key. Evidence: SORT_BY(LIST(1,2), Q, "DESC") is E_UNDEF_VAR at 1:20 on all five hosts, pinned by conformance agg.forms.sort-by.text-direction-wins-over-bare-name (06-aggregates.selt:233). The only safe simplification is merging the conditions (is_symbol(1) && node(2) is not text), which is cosmetic.

Variants / notes:
- Check the branch order really makes the text test redundant (is_symbol(1) sits between them)

Related: [HYG-04](#hyg-04)

Checks:

- [x] **JS** · reported · `js/src/builtins/aggregate.mjs:325-337`  
  → **REFUTED**: Branch order decides the form; removal changes behaviour
- [x] **PHP** · reported · `php/src/Builtins/Core.php:260-271`  
  → **REFUTED**: Branch order decides the form; removal changes behaviour
- [x] **Python** · reported · `python/sel/builtins/aggregate.py:393-404`  
  → **REFUTED**: Branch order decides the form; removal changes behaviour
- [x] **C++** · reported · `cpp/sel.cpp:5454-5470`  
  → **REFUTED**: Branch order decides the form; removal changes behaviour
- [x] **Lisp** · reported · `lisp/src/builtins/aggregate.lisp:340-351`  
  → **REFUTED**: Branch order decides the form; removal changes behaviour

<a id="hyg-04"></a>
### HYG-04 · do_top(): 3-arg text branch identical to the final else

**Severity (reviewer):** Medium · **Source:** A §2 Pattern 3; A §4 Task 3

Same pattern as HYG-03 in TOP/TOP_BY.

> **Validation — REFUTED as proposed; TOP_BY form unpinned.** Same as HYG-03 for do_top: TOP_BY(LIST(1,2), Q, "DESC", 1) is E_UNDEF_VAR at 1:19 on all five hosts because the text branch precedes is_symbol(1). Unlike SORT_BY, no conformance case pins the TOP_BY form, so the proposed deletion would pass the suite while changing behaviour — add a TOP_BY twin of agg.forms.sort-by.text-direction-wins-over-bare-name.

Variants / notes:
- Same branch-order caveat as HYG-03

Related: [HYG-03](#hyg-03)

Checks:

- [x] **JS** · reported · `js/src/builtins/aggregate.mjs:389-398`  
  → **REFUTED**: Branch order decides the form; removal changes behaviour (not pinned for TOP_BY)
- [x] **PHP** · reported · `php/src/Builtins/Structure.php:1484-1493`  
  → **REFUTED**: Branch order decides the form; removal changes behaviour (not pinned for TOP_BY)
- [x] **Python** · reported · `python/sel/builtins/aggregate.py:458-466`  
  → **REFUTED**: Branch order decides the form; removal changes behaviour (not pinned for TOP_BY)
- [x] **C++** · reported · `cpp/sel.cpp:5547-5557`  
  → **REFUTED**: Branch order decides the form; removal changes behaviour (not pinned for TOP_BY)
- [x] **Lisp** · reported · `lisp/src/builtins/aggregate.lisp:451-462 (do-top-sort)`  
  → **REFUTED**: Branch order decides the form; removal changes behaviour (not pinned for TOP_BY)

<a id="hyg-05"></a>
### HYG-05 · Optimizer sort details: `sort_count == 3` branch is a subset of the next

**Severity (reviewer):** Low · **Source:** A §2 Pattern 4; A §4 Task 2

The `sort_count == 3 && is_var(args[1])` branch performs the same assignments as the following general bare-variable branch.

> **Validation — CONFIRMED (JS, Python, C++, Lisp); PHP differs.** In JS optimizer.mjs:270-276, Python optimizer.py:328-332, C++ sel.cpp:7279-7285 and Lisp optimizer.lisp:373-376 the sort_count == 3 bare-name branch is a subset of the next (bare name, any count > 2) with the same body; the text-direction case is already taken by the first branch, so the middle one is dead weight. PHP is different (and its cited line is wrong): Optimizer.php:738-747 has ONLY the sortCount == 3 branch and no general one, and does not check "grouped". So PHP returns no key for the 4-argument explicit-binder form, and its optimiser skips the MAP→TOP/SORT swap there — which makes PHP the one host that answers correctly (E_DIV_ZERO) for MAP(…1/(a-1)…) .> TOP_BY(X, X["a"], "DESC", 1) where the other four answer [2] (see SEM-08). Deleting the middle branch in the four hosts is behaviour-neutral; aligning PHP is not.

Variants / notes:
- Check the general branch's extra conditions (grouped, count > 2) do not differ in the 3-arg case

Checks:

- [x] **JS** · reported · `js/src/optimizer.mjs:270-276`  
  → **FIXED**: Fixed: middle sort_count == 3 bare-name branch removed (js, py, cpp, lisp); PHP gained the general branch in the SEM-08 fix (validation: Middle branch is a subset of the next with the same body)
- [x] **PHP** · reported · `php/src/Optimizer.php:191-197`  
  → **REFUTED**: No redundancy: PHP lacks the general branch (and the grouped check) — a parity gap, see SEM-08
- [x] **Python** · reported · `python/sel/optimizer.py:328-332`  
  → **FIXED**: Fixed: middle sort_count == 3 bare-name branch removed (js, py, cpp, lisp); PHP gained the general branch in the SEM-08 fix (validation: Middle branch is a subset of the next with the same body)
- [x] **C++** · reported · `cpp/sel.cpp:7279-7285 (opt_sort_info)`  
  → **FIXED**: Fixed: middle sort_count == 3 bare-name branch removed (js, py, cpp, lisp); PHP gained the general branch in the SEM-08 fix (validation: Middle branch is a subset of the next with the same body)
- [x] **Lisp** · reported · `lisp/src/optimizer.lisp:373-376 (sort-key)`  
  → **FIXED**: Fixed: middle sort_count == 3 bare-name branch removed (js, py, cpp, lisp); PHP gained the general branch in the SEM-08 fix (validation: Middle branch is a subset of the next with the same body)

<a id="hyg-06"></a>
### HYG-06 · optimize_tree(): `target` branch on non-assign nodes is unreachable

**Severity (reviewer):** Medium · **Source:** A §2 Pattern 5; A §4 Task 2

Claim: only assign nodes carry `target`, so the else-if(copy.target) branch after the assign case never runs. Needs a check of every node constructor (compound assignment? index-assign? parser variants).

> **Validation — CONFIRMED (JS, PHP, Python).** Only the parser's assign node carries `target` (js parser.mjs:202, php Parser.php:400, python parser.py:264; other "target" hits are dialect-map data, not AST). So the else-if(copy.target) branch after the assign case in optimize_tree is unreachable: js optimizer.mjs:512-515, php Optimizer.php:159, python optimizer.py:579-582. C++ and Lisp have no target field (C++ uses l/r; Lisp dispatches on node kind), so no equivalent branch exists.

Related: [HYG-07](#hyg-07)

Checks:

- [x] **JS** · reported · `js/src/optimizer.mjs:512-515`  
  → **FIXED**: Fixed: optimize_tree walks `value` once; the non-assign target branch is gone (js, php, py) (validation: optimizer.mjs:512-515 unreachable)
- [x] **PHP** · reported · `php/src/Optimizer.php:159`  
  → **FIXED**: Fixed: optimize_tree walks `value` once; the non-assign target branch is gone (js, php, py) (validation: Optimizer.php:159 non-assign loop over target is unreachable for target)
- [x] **Python** · reported · `python/sel/optimizer.py:579-582`  
  → **FIXED**: Fixed: optimize_tree walks `value` once; the non-assign target branch is gone (js, php, py) (validation: optimizer.py:579-582 unreachable)
- [x] **C++** · sweep — Equivalent branch in the C++ optimizer?  
  → **N/A**: No target field in the C++ AST
- [x] **Lisp** · sweep — Equivalent branch in the Lisp optimizer?  
  → **N/A**: optimize-children dispatches on node kind; no target slot
- [x] **spec** · derived · `parsers in all hosts` — Which node kinds can carry target  
  → **FIXED**: Fixed: optimize_tree walks `value` once; the non-assign target branch is gone (js, php, py) (validation: Premise holds: only assign nodes have target in every host that has the field)

<a id="hyg-07"></a>
### HYG-07 · exceeds_depth(): non-assign `target` check is always false

**Severity (reviewer):** Medium · **Source:** A §2 Pattern 5; A §4 Task 2

Same premise as HYG-06, in the depth check.

> **Validation — CONFIRMED (JS, PHP, Python).** Same premise: `node.t !== assign && node.target` can never be true in exceeds_depth — js optimizer.mjs:542, php Optimizer.php:56-57, python optimizer.py:614-615. C++ opt_exceeds_depth (sel.cpp:7817) tests `node.t != Assign && node.l`, which is live (l is every binary/index/unary left child); Lisp exceeds-depth-p dispatches on kind.

Related: [HYG-06](#hyg-06)

Checks:

- [x] **JS** · reported · `js/src/optimizer.mjs:542`  
  → **FIXED**: Fixed: exceeds_depth's always-false target test removed (js, php, py) (validation: optimizer.mjs:542 always false)
- [x] **PHP** · reported · `php/src/Optimizer.php:56-57`  
  → **FIXED**: Fixed: exceeds_depth's always-false target test removed (js, php, py) (validation: Optimizer.php:56-57 always false)
- [x] **Python** · reported · `python/sel/optimizer.py:614-615`  
  → **FIXED**: Fixed: exceeds_depth's always-false target test removed (js, php, py) (validation: optimizer.py:614-615 always false)
- [x] **C++** · sweep — Equivalent check?  
  → **REFUTED**: sel.cpp:7817 checks node.l, which is live
- [x] **Lisp** · sweep — Equivalent check?  
  → **N/A**: Kind dispatch, no equivalent

<a id="hyg-08"></a>
### HYG-08 · PATH: impossible null check after has(), duplicated fallback

**Severity (reviewer):** Low · **Source:** A §2 Pattern 6; A §4 Task 3

After cur.has(seg), cur.get(seg) is checked for null/undefined, which cannot happen; the "default or NULL" fallback appears twice in the loop.

> **Validation — CONFIRMED (all 5).** PATH checks has(seg) and then treats get(seg) returning nothing as a miss; get never misses after has, and the default/NULL fallback is written twice in the loop. Verified at js null.mjs:49-58, php NullOps.php:69-81, python null.py:41-49, cpp sel.cpp:6826-6834, lisp null.lisp:55-64. Behaviour-neutral.

Variants / notes:
- Confirm has()/get() contract per host (e.g. a stored NULL member vs absent)

Related: [HYG-09](#hyg-09)

Checks:

- [x] **JS** · reported · `js/src/builtins/null.mjs:49-58`  
  → **FIXED**: Fixed: PATH returns get() after has() directly; one fallback (all 5 hosts) (validation: Dead post-has() check + duplicated fallback)
- [x] **PHP** · reported · `php/src/Builtins/NullOps.php:70-80`  
  → **FIXED**: Fixed: PATH returns get() after has() directly; one fallback (all 5 hosts) (validation: Dead post-has() check + duplicated fallback)
- [x] **Python** · reported · `python/sel/builtins/null.py:41-49`  
  → **FIXED**: Fixed: PATH returns get() after has() directly; one fallback (all 5 hosts) (validation: Dead post-has() check + duplicated fallback)
- [x] **C++** · reported · `cpp/sel.cpp:6826-6834`  
  → **FIXED**: Fixed: PATH returns get() after has() directly; one fallback (all 5 hosts) (validation: Dead post-has() check + duplicated fallback)
- [x] **Lisp** · reported · `lisp/src/builtins/null.lisp:55-64`  
  → **FIXED**: Fixed: PATH returns get() after has() directly; one fallback (all 5 hosts) (validation: Dead post-has() check + duplicated fallback)

<a id="hyg-09"></a>
### HYG-09 · GET: same redundancy as PATH

**Severity (reviewer):** Low · **Source:** A §3.5 #6 (lisp/src/builtins/null.lisp:30-40)

Lisp GET has the redundant (if val …) after value-has and the duplicated fallback. Sweep GET in the other hosts.

> **Validation — CONFIRMED (all 5; milder outside Lisp).** GET has the same dead post-has() check in every host (js null.mjs:30-33, php NullOps.php:45-50, python null.py:24-27, cpp sel.cpp:6799-6802, lisp null.lisp:30-40). Only Lisp also duplicates the fallback expression; the others fall through to one fallback.

Related: [HYG-08](#hyg-08)

Checks:

- [x] **Lisp** · reported · `lisp/src/builtins/null.lisp:30-40`  
  → **FIXED**: Fixed: GET returns get() after has() directly (all 5 hosts; Lisp's duplicated fallback is one cond) (validation: Dead (if val …) and duplicated fallback)
- [x] **JS** · sweep — GET builtin: same pattern?  
  → **FIXED**: Fixed: GET returns get() after has() directly (all 5 hosts; Lisp's duplicated fallback is one cond) (validation: Dead post-has() check only; single fallback)
- [x] **PHP** · sweep — GET builtin: same pattern?  
  → **FIXED**: Fixed: GET returns get() after has() directly (all 5 hosts; Lisp's duplicated fallback is one cond) (validation: Dead post-has() check only; single fallback)
- [x] **Python** · sweep — GET builtin: same pattern?  
  → **FIXED**: Fixed: GET returns get() after has() directly (all 5 hosts; Lisp's duplicated fallback is one cond) (validation: Dead post-has() check only; single fallback)
- [x] **C++** · sweep — GET builtin: same pattern?  
  → **FIXED**: Fixed: GET returns get() after has() directly (all 5 hosts; Lisp's duplicated fallback is one cond) (validation: Dead post-has() check only; single fallback)

<a id="hyg-10"></a>
### HYG-10 · ?? and ??? evaluate through duplicated blocks

**Severity (reviewer):** Low · **Source:** A §2 Pattern 7; A §4 Task 4 (medium risk)

Evaluation, E_NO_KEY/E_UNDEF_VAR handling and fallback are identical; only is_null vs is_vacuous differs. Proposed: one helper parameterised by the predicate.

> **Validation — CONFIRMED (all 5).** ?? and ??? are two copies of: evaluate left, on E_NO_KEY / E_UNDEF_VAR evaluate right, else test is_null / is_vacuous. js eval.mjs:308-334, php Evaluator.php:263-291, python eval.py:360-380, cpp sel.cpp:3289-3307 (compact, ~8 lines each), lisp eval.lisp:374-395. A shared helper parameterised by the predicate keeps behaviour; error positions come from the operands, not the operator, so they would not move.

Variants / notes:
- Error position of the fallback must stay identical

Checks:

- [x] **JS** · reported · `js/src/eval.mjs:308-334`  
  → **FIXED**: Fixed: ?? and ??? share one block that picks the predicate (all 5 hosts) (validation: Two copies differing only in the predicate)
- [x] **PHP** · reported · `php/src/Evaluator.php:263-291`  
  → **FIXED**: Fixed: ?? and ??? share one block that picks the predicate (all 5 hosts) (validation: Two copies differing only in the predicate)
- [x] **Python** · reported · `python/sel/eval.py:360-380`  
  → **FIXED**: Fixed: ?? and ??? share one block that picks the predicate (all 5 hosts) (validation: Two copies differing only in the predicate)
- [x] **C++** · reported · `cpp/sel.cpp:3289-3307`  
  → **FIXED**: Fixed: ?? and ??? share one block that picks the predicate (all 5 hosts) (validation: Two copies differing only in the predicate)
- [x] **Lisp** · reported · `lisp/src/eval.lisp:374-395`  
  → **FIXED**: Fixed: ?? and ??? share one block that picks the predicate (all 5 hosts) (validation: Two copies differing only in the predicate)

<a id="hyg-11"></a>
### HYG-11 · Binding.column() and Binding.raw() duplicate option parsing

**Severity (reviewer):** Low · **Source:** A §2 Pattern 8; A §4 Task 4

Collation check, exact/sargable/guard flags and split_sargable/prefilter resolution are duplicated verbatim between the two factories.

> **Validation — CONFIRMED (JS, PHP, Python, Lisp).** Binding.column and Binding.raw repeat the collation → exact/sargable merge, three checkBool calls, split_sargable → prefilter default and checkPrefilter (js sql/binding.mjs:55-66 and 85-96; php/python/lisp likewise). The copies differ only in the label used in error messages ("a column binding" vs "a raw column binding"), so a helper needs that label. C++ Binding::column/raw (sel_sql_binding.cpp:149-181) take no collation or split_sargable argument and share only field assignments.

Checks:

- [x] **JS** · reported · `js/src/sql/binding.mjs:56-66, 86-96`  
  → **FIXED**: Fixed: column()/raw() share a columnFlags helper that takes the label (js, php, py, lisp) (validation: Duplicated option handling (label differs))
- [x] **PHP** · reported · `php/src/Sql/Binding.php:76-90, 109-122`  
  → **FIXED**: Fixed: column()/raw() share a columnFlags helper that takes the label (js, php, py, lisp) (validation: Duplicated option handling (label differs))
- [x] **Python** · reported · `python/sel/sql/binding.py:85-101, 119-134`  
  → **FIXED**: Fixed: column()/raw() share a columnFlags helper that takes the label (js, php, py, lisp) (validation: Duplicated option handling (label differs))
- [x] **Lisp** · reported · `lisp/src/sql/binding.lisp:113-122, 136-145`  
  → **FIXED**: Fixed: column()/raw() share a columnFlags helper that takes the label (js, php, py, lisp) (validation: Duplicated option handling (label differs))
- [x] **C++** · sweep · `cpp/sel_sql*` — Same duplication in the C++ binding?  
  → **REFUTED**: No collation/split_sargable parameters; only trivial field assignments repeat

<a id="hyg-12"></a>
### HYG-12 · JS: dead `this;` statements in kind predicates

**Severity (reviewer):** Low · **Source:** A §3.1 Dead #1; A §4 Task 1

isNone(), isText(), isBin(), isBool() start with a bare `this;`.

> **Validation — CONFIRMED.** js/src/value.mjs:128, 138, 139, 140: isNone/isText/isBin/isBool start with a bare `this;`. No other bare `this;` statement in js/src.

Checks:

- [x] **JS** · reported · `js/src/value.mjs:128, 138, 139, 140`  
  → **FIXED**: Fixed: four bare `this;` statements removed from js/src/value.mjs (validation: Four dead statements)
- [x] **JS (sweep)** · derived · `js/src/**` — Other bare expression statements  
  → **REFUTED**: No other occurrence

<a id="hyg-13"></a>
### HYG-13 · JS math plan: `digits === '1'` string check is dead

**Severity (reviewer):** Low · **Source:** A §3.1 Dead #4; A §4 Task 2

digits is always a BigInt in JS, so `|| constR.digits === '1'` never matches.

> **Validation — CONFIRMED (JS only).** js/src/math_plan.mjs:106, 113 compare constVal.digits to 1n OR the string "1". constVal comes from node.dec or D.parse (math_plan.mjs:64-74), and every decimal is built by decimal.mjs make(), which converts digits to BigInt (decimal.mjs:70-72), so the string branch is dead. No other host has a check against a representation its digits never take (PHP digits are strings natively and compare as strings).

Variants / notes:
- Confirm no path builds digits as a string (e.g. deserialised plans, math-ops rendering)

Checks:

- [x] **JS** · reported · `js/src/math_plan.mjs:106, 113`  
  → **FIXED**: Fixed: math plan compares digits to 1n only (validation: String comparison dead)
- [x] **PHP** · sweep — Math plan: checks against a representation the digits field never has?  
  → **REFUTED**: No dual-representation check in the math plan
- [x] **Python** · sweep — Math plan: checks against a representation the digits field never has?  
  → **REFUTED**: No dual-representation check in the math plan
- [x] **C++** · sweep — Math plan: checks against a representation the digits field never has?  
  → **REFUTED**: No dual-representation check in the math plan
- [x] **Lisp** · sweep — Math plan: checks against a representation the digits field never has?  
  → **REFUTED**: No dual-representation check in the math plan

<a id="hyg-14"></a>
### HYG-14 · JS: unused decodeUtf8 / toCodePoints import and re-export

**Severity (reviewer):** Low · **Source:** A §3.1 Unused #1; A §4 Task 1

Imported at value.mjs:6 and re-exported at :621; not used in value.mjs and reportedly never imported from it. Check package exports / public API before removing.

> **Validation — CONFIRMED.** js/src/value.mjs:6 imports decodeUtf8 and toCodePoints and :621 re-exports them; value.mjs uses neither, nothing in js/, tools/ or examples/ imports them from value.mjs, and package.json "exports" exposes only js/src/sel.mjs and js/src/sql/index.mjs, so the re-export is unreachable for users. Safe to drop both from the import and the export.

Variants / notes:
- Public entry points, dist bundle, docs referencing the re-export

Related: [HYG-21](#hyg-21)

Checks:

- [x] **JS** · reported · `js/src/value.mjs:6, 621`  
  → **FIXED**: Fixed: decodeUtf8/toCodePoints dropped from value.mjs's import and re-export (validation: Unused import + unreachable re-export)

<a id="hyg-15"></a>
### HYG-15 · JS: deprecated RecordShape.aliasCache / LEGACY_ALIAS_CACHES

**Severity (reviewer):** Low · **Source:** A §3.1 Unused #2

JSDoc says "@deprecated always empty; removed in the next minor release". Current version is 0.8.1. Decide when it goes and whether CHANGELOG announces it. Sweep other hosts for deprecated shims awaiting removal.

> **Validation — CONFIRMED, removal not yet due.** js/src/value.mjs:24-34 RecordShape.aliasCache (lazy WeakMap-backed, always empty) and sel.d.ts:39-43 are deprecated. CHANGELOG 0.8.0 (line 455) announces removal "in the next minor release"; the current version is 0.8.1, so it is due in 0.9.0, not overdue. PHP/Python/Lisp already dropped their alias caches in 0.8.0; no other @deprecated shim exists in php/src, python/sel, lisp/src or cpp/sel.hpp.

Checks:

- [x] **JS** · reported · `js/src/value.mjs:28-36`  
  → **FIXED**: Fixed in 0.9.0: RecordShape.aliasCache, its WeakMap and the RecordShapeAlias type removed from value.mjs and sel.d.ts, as 0.8.0 announced
- [x] **PHP** · sweep — Deprecated shims past their announced removal?  
  → **REFUTED**: No deprecated shims awaiting removal
- [x] **Python** · sweep — Deprecated shims past their announced removal?  
  → **REFUTED**: No deprecated shims awaiting removal
- [x] **C++** · sweep — Deprecated shims past their announced removal?  
  → **REFUTED**: No deprecated shims awaiting removal
- [x] **Lisp** · sweep — Deprecated shims past their announced removal?  
  → **REFUTED**: No deprecated shims awaiting removal

<a id="hyg-16"></a>
### HYG-16 · cloneAt / copyAt: redundant branches

**Severity (reviewer):** Low · **Source:** A §3.1 Duplicated #2 (js cloneAt); A §3.2 Duplicated #2 (php copyAt)

JS instantiates new Value(...) redundantly across branches; PHP has an empty check that returns the same object as the following empty foreach. Copy semantics are load-bearing (values alias; assignment copies), so change only with care.

> **Validation — CONFIRMED (JS, PHP); Python's fast path is a bug (SEM-12).** JS cloneAt (value.mjs:434-438) has a leaf fast path that builds the same Value the general path builds next (then skips entries/children), and PHP copyAt (Value.php:793-797) the same for children === []: duplicate construction, behaviour-neutral, fast paths rather than dead code. The sweep found that Python's equivalent fast path (value.py:526-527) returns self instead of a copy — a real defect, filed as SEM-12. C++ clone_at always builds a new Impl; Lisp value-copy-at's leaf fast path builds a fresh value.

Checks:

- [x] **JS** · reported · `js/src/value.mjs:434-444`  
  → **FIXED**: Fixed: cloneAt's duplicate leaf construction removed (validation: Duplicate construction in the leaf fast path)
- [x] **PHP** · reported · `php/src/Value.php:793-797`  
  → **FIXED**: Fixed: copyAt's duplicate childless construction removed (validation: Duplicate construction for childless values)
- [x] **Python** · sweep — Same shape in the clone/copy-at-path helper?  
  → **FIXED**: Fixed: fixed as SEM-12 (clone copies leaves) (validation: Not redundancy — returns self (SEM-12))
- [x] **C++** · sweep — Same shape in the clone/copy-at-path helper?  
  → **REFUTED**: Single construction path
- [x] **Lisp** · sweep — Same shape in the clone/copy-at-path helper?  
  → **REFUTED**: Fast path builds a fresh value; no duplicate

<a id="hyg-17"></a>
### HYG-17 · PHP: unused `use` imports

**Severity (reviewer):** Low · **Source:** A §3.2 Unused #1-3; A §4 Task 1

use Sel\Dec in Builtins/Structure.php:12; use Sel\Value in Sql/Bindings.php:12; use Sel\Sql\Binding and Sel\Sql\Fragment in bin/sqlt:20-21.

> **Validation — CONFIRMED (exactly the three files).** A per-file scan of every `use` in php/src and php/bin (comments excluded) finds exactly the reported four: Sel\Dec in Builtins/Structure.php:12, Sel\Value in Sql/Bindings.php:12, Sel\Sql\Binding and Sel\Sql\Fragment in bin/sqlt:20-21 (their only mentions in sqlt are comments; PHP use is per-file, so the included CaseData.php does not need them).

Related: [HYG-21](#hyg-21)

Checks:

- [x] **PHP** · reported · `php/src/Builtins/Structure.php:12`  
  → **FIXED**: Fixed: unused use lines removed (validation: Structure.php:12 Dec unused)
- [x] **PHP (2)** · reported · `php/src/Sql/Bindings.php:12`  
  → **FIXED**: Fixed: unused use lines removed (validation: Bindings.php:12 Value unused)
- [x] **PHP (3)** · reported · `php/bin/sqlt:20-21`  
  → **FIXED**: Fixed: unused use lines removed (validation: bin/sqlt:20-21 Binding, Fragment unused (comment mentions only))

<a id="hyg-18"></a>
### HYG-18 · PHP: uncalled private helpers

**Severity (reviewer):** Low · **Source:** A §3.2 Unused #4-5; A §4 Task 1

Optimizer::callNode (221-225) and Builtins\Core::elements (341-347) are reportedly never invoked. Sweep every host for uncalled helpers.

> **Validation — CONFIRMED (PHP) + twins in JS and Lisp.** PHP Optimizer::callNode (221) and Builtins\Core::elements (341) are private and never called. Sweep: JS optimizer.mjs:24 defines an unused `call` helper (the twin of callNode), and Lisp optimizer.lisp:415 filter-predicate-of is never called. Python (vulture) and C++ (reference count over every top-level function of sel.cpp and sel_sql*.cpp) have no uncalled internal helpers.

Related: [HYG-21](#hyg-21)

Checks:

- [x] **PHP** · reported · `php/src/Optimizer.php:221-225 (callNode)`  
  → **FIXED**: Fixed: uncalled helper removed (validation: callNode uncalled)
- [x] **PHP (2)** · reported · `php/src/Builtins/Core.php:341-347 (elements)`  
  → **FIXED**: Fixed: uncalled helper removed (validation: elements uncalled)
- [x] **JS** · sweep — Uncalled private/internal helpers  
  → **FIXED**: Fixed: uncalled helper removed (validation: optimizer.mjs:24 `call` unused (eslint))
- [x] **Python** · sweep — Uncalled private/internal helpers  
  → **REFUTED**: vulture: only public API entry points flagged
- [x] **C++** · sweep — Uncalled private/internal helpers  
  → **REFUTED**: Every top-level function referenced
- [x] **Lisp** · sweep — Uncalled private/internal helpers  
  → **FIXED**: Fixed: uncalled helper removed (validation: optimizer.lisp:415 filter-predicate-of never called)

<a id="hyg-19"></a>
### HYG-19 · PHP JoinPlan has both $kind and $type

**Severity (reviewer):** Low · **Source:** A §3.2 Structural #1; A §4 Task 4

JoinPlan declares public string $kind = 'INNER' and public string $type = 'INNER'. Find which is read; compare with the other hosts' relational-plan join struct.

> **Validation — CONFIRMED (PHP only).** php/src/Sql/RelationalPlan.php:13-14 declares both $kind and $type; setKind() writes both, the translator reads only $type (Translator.php:3669), and nothing calls getKind() or reads ->kind on a join. The docblock calls $kind the "portable plan contract" name, but JS (relational-plan.mjs:5), Python (relational_plan.py:10) and Lisp (relational-plan.lisp:9, join-plan-type) have only `type`. Drop $kind/getKind or rename consistently.

Checks:

- [x] **PHP** · reported · `php/src/Sql/RelationalPlan.php:11-13`  
  → **FIXED**: Fixed: JoinPlan keeps only $type (as in the other hosts); getKind/setKind removed, the translator sets type (validation: $kind is write-only)
- [x] **JS** · sweep — Join plan struct field names (parity with PHP after the fix)  
  → **REFUTED**: Join plan has only type
- [x] **Python** · sweep — Join plan struct field names (parity with PHP after the fix)  
  → **REFUTED**: Join plan has only type
- [x] **C++** · sweep — Join plan struct field names (parity with PHP after the fix)  
  → **REFUTED**: No duplicate field in the C++ plan
- [x] **Lisp** · sweep — Join plan struct field names (parity with PHP after the fix)  
  → **REFUTED**: Join plan has only type

<a id="hyg-20"></a>
### HYG-20 · Python bin scripts: unused probe imports

**Severity (reviewer):** Low · **Source:** A §3.3 Unused #1; A §4 Task 1

`_sel_probe = …` / `_probe = …` imported and never referenced in eight scripts. May be a deliberate "is sel importable" probe; find out before removing.

> **Validation — REFUTED (deliberate).** The eight `import sel as _sel_probe/_probe` lines are marked `# noqa: F401` and sit in a try/except ImportError that decides whether to put the source tree on sys.path, so the python-wheel roster entry tests the installed package (python/bin/api.py:18-26). Not dead.

Related: [HYG-21](#hyg-21)

Checks:

- [x] **Python** · reported · `python/bin/api.py:24; batch.py:18; check-decimal.py:20; conformance.py:20; sqlapi:17; sqlfuzz:20; sqlreplay:22; sqlt:45`  
  → **REFUTED**: Deliberate import probe (noqa: F401)

<a id="hyg-21"></a>
### HYG-21 · Unused / unreachable code: tool-assisted sweep per host

**Severity (reviewer):** Low · **Source:** derived from HYG-12..HYG-20

The review found these by reading. Run one mechanical pass per host (no linter is configured; one-off runs only) and triage the hits.

> **Validation — CONFIRMED (new small items in JS, Python, all-host dead flag).** Tools run (none are in the repo): eslint 9 (no-unused-vars/no-unreachable/no-unused-expressions) on js/src; pyflakes + vulture on python/; a per-file use/private-method reference scan on php/; g++ -Wall -Wextra -Wunused -Wunreachable-code plus a reference count on cpp/; a defun reference count on lisp/. New beyond the review: JS unused imports BIN (eval.mjs:8), Value (sql/emit.mjs:5), dec (sql/translator.mjs:15), unused constants BP_SEQ/BP_LIST (parser.mjs:49-50), unused helper call (optimizer.mjs:24); Python unused `nonlocal steps` (math_plan.py:92) and unreachable `return 0` after `while True` (_cli.py:85); ALL FIVE translators keep an in_having/inHaving flag that is written and never read (js translator.mjs:137/2853, php Translator.php:59/3738, py translator.py:156/2613, cpp sel_sql_translator.cpp:3489, lisp translator.lisp:32/2922); Lisp filter-predicate-of uncalled. PHP and C++ otherwise clean.

Variants / notes:
- JS: eslint no-unused-vars/no-unreachable or tsc --noUnusedLocals --checkJs
- PHP: phpstan / psalm unused
- Python: pyflakes / vulture
- C++: -Wunused -Wunreachable-code, clang-tidy
- Lisp: SBCL style-warnings on compile

Related: [HYG-14](#hyg-14), [HYG-17](#hyg-17), [HYG-18](#hyg-18), [HYG-20](#hyg-20)

Checks:

- [x] **JS** · derived — Run the tool; triage  
  → **FIXED**: Fixed: unused imports BIN, Value (emit), dec (translator) and the `call` helper removed; inHaving removed. BP_SEQ/BP_LIST kept on purpose: every host declares them, with a comment, so the ladder reads as SPEC §5 (validation: eslint: 3 unused imports, 2 unused constants, 1 unused helper, the 4 `this;` (HYG-12); decodeUtf8/toCodePoints hidden by the re-export (HYG-14))
- [x] **PHP** · derived — Run the tool; triage  
  → **FIXED**: Fixed: write-only inHaving removed (imports and helpers under HYG-17/18) (validation: Only the reported imports/helpers, plus the write-only inHaving flag)
- [x] **Python** · derived — Run the tool; triage  
  → **FIXED**: Fixed: unused `nonlocal steps` and the unreachable `return 0` removed; write-only in_having removed (validation: nonlocal steps unused; unreachable return; write-only in_having; probe imports deliberate)
- [x] **C++** · derived — Run the tool; triage  
  → **FIXED**: Fixed: write-only in_having_ and its reset guard removed (validation: Clean except the write-only in_having_ flag)
- [x] **Lisp** · derived — Run the tool; triage  
  → **FIXED**: Fixed: filter-predicate-of and the write-only in-having slot removed (validation: filter-predicate-of uncalled; write-only in-having slot)

<a id="hyg-22"></a>
### HYG-22 · Regression gate for the cleanups

**Severity (reviewer):** — · **Source:** A §4 Task 4 last bullet

Review A says to run tools/check-manifest.sh "across all 7 roster configurations". The repo's definition of done is tools/check.sh ALL GREEN (which includes the manifest layer). Plus tools/fuzz.sh on a few seeds for the evaluator changes (HYG-10).

> **Validation — CONFIRMED (the review names the wrong gate).** Review A's regression step runs tools/check-manifest.sh "across all 7 roster configurations". That script only compares builtin arities and binding forms against spec/builtins.json (check-manifest.sh:2-4) and is one step of tools/check.sh (check.sh:129). It would not notice any of the behavioural traps found here (e.g. deleting the HYG-03/04 text branch). The gate for every cleanup is tools/check.sh ALL GREEN, plus tools/fuzz.sh for the evaluator refactor (HYG-10).

Checks:

- [x] **tool** · derived · `tools/check.sh` — Run after each cleanup batch  
  → **WON'T FIX**: Process note, no code change: every step in this fix phase was gated with tools/check.sh (validation: Use tools/check.sh, not check-manifest.sh alone)

## DOC — Documentation and spec text

Misplaced doc comments, and spec text that contradicts the conformance suite.

<a id="doc-01"></a>
### DOC-01 · isBinderName doc comment sits above the wrong function

**Severity (reviewer):** Low · **Source:** A §3.1 Documentation #1; A §3.2 Structural #2; A §4 Task 1

JS: the docblock at sql/constants.mjs:42-51 sits above textLiteralResults (52); isBinderName is at 144. PHP: Sql/Constants.php:54-67 sits above identityProjection (68); isBinderName is at 186. Sweep the other SQL constants files.

> **Validation — CONFIRMED (JS, PHP).** JS js/src/sql/constants.mjs:42-51: the isBinderName paragraph sits between the "lifted constants" comment and the textLiteralResults comment (textLiteralResults at :58); isBinderName itself is at :144 with no comment. PHP php/src/Sql/Constants.php:55-67: the same docblock sits above identityProjection (:68); isBinderName is at :186. Python (constants.py:137-138 docstring), C++ (sel_sql_stage1.cpp:277) and Lisp (stage1.lisp:44 docstring) have it in place.

Checks:

- [x] **JS** · reported · `js/src/sql/constants.mjs:42-51`  
  → **FIXED**: Fixed: the isBinderName block moved onto isBinderName, and the orphaned scope() block (found next to it) moved onto scope() (validation: constants.mjs:42-51 orphaned; function at :144)
- [x] **PHP** · reported · `php/src/Sql/Constants.php:54-67`  
  → **FIXED**: Fixed: the isBinderName block moved onto isBinderName, and the orphaned scope() block (found next to it) moved onto scope() (validation: Constants.php:55-67 above identityProjection; function at :186)
- [x] **Python** · sweep — Same misplaced comment in the SQL constants module?  
  → **REFUTED**: Docstring on is_binder_name
- [x] **C++** · sweep — Same misplaced comment in the SQL constants module?  
  → **REFUTED**: In place
- [x] **Lisp** · sweep — Same misplaced comment in the SQL constants module?  
  → **REFUTED**: Docstring on is-binder-name

<a id="doc-02"></a>
### DOC-02 · Misplaced / orphaned doc comments: broader sweep

**Severity (reviewer):** — · **Source:** derived from DOC-01

A doc comment moved once by a refactor suggests others. Look for doc comments whose named symbol is not the next definition.

> **Validation — REFUTED beyond DOC-01.** Heuristic scans (tools in the scratchpad): (1) every comment block directly above a function that names another function of the file but not the one it documents — 40 hits in JS/PHP, all read and all legitimate cross-references; (2) question-style doc paragraphs ("Is this…/Whether…/Does…") that run into another paragraph before code, over js/src, php/src, cpp and lisp/src — only the known JS isBinderName block. Python docstrings and Lisp docstrings cannot drift from their function.

Related: [DOC-01](#doc-01)

Checks:

- [x] **JS** · derived — Scan for comments naming a symbol other than the following definition  
  → **REFUTED**: No further orphaned doc comments found
- [x] **PHP** · derived — Scan for comments naming a symbol other than the following definition  
  → **REFUTED**: No further orphaned doc comments found
- [x] **Python** · derived — Scan for comments naming a symbol other than the following definition  
  → **REFUTED**: No further orphaned doc comments found
- [x] **C++** · derived — Scan for comments naming a symbol other than the following definition  
  → **REFUTED**: No further orphaned doc comments found
- [x] **Lisp** · derived — Scan for comments naming a symbol other than the following definition  
  → **REFUTED**: No further orphaned doc comments found

<a id="doc-03"></a>
### DOC-03 · TOP_BY signature in SPEC and the manifest contradicts every host and the suite

**Severity (reviewer):** Medium · **Source:** found during validation of SEM-02/SEM-03 (not in either review); already noted in auto-memory sel-sql-layer-known-limits

spec/SPEC.md:738 and spec/builtins.json:1213 (rendered into docs/reference/builtins.md:83) give TOP_BY(list, [binder,] key, n [, dir]). All five hosts, docs/functions.md:132 and conformance/06-aggregates.selt:194,263 take a text direction BEFORE n; TOP_BY(L, key, 1, "DESC") raises E_NOT_NUM on "DESC" in every host.

> **Validation — CONFIRMED (new).** Spec §7.4 table and the manifest signature string say key, n [, dir]; implementations, docs/functions.md and the suite say key, dir, n. Fix the spec text + builtins.json signature (then regenerate docs/reference/builtins.md), not the hosts.

| Program / action | Reported result | Expected |
|---|---|---|
| `TOP_BY(LIST(1,2), _, 1, "DESC")` | E_NOT_NUM (all hosts) | what SPEC.md:738 describes |

Variants / notes:
- Decide which is normative; the suite already pins dir-before-n

Related: [SEM-02](#sem-02)

Checks:

- [x] **spec** · derived · `spec/SPEC.md:738; spec/builtins.json:1213` — Signature text  
  → **FIXED**: Fixed: SPEC §7.4 and spec/builtins.json: TOP_BY(list, [binder,] key, [dir,] n); regenerated manifests and docs/reference/builtins.md (validation: SPEC.md:738 and builtins.json:1213 wrong/misleading)
- [x] **suite** · derived · `conformance/06-aggregates.selt:194,263` — Pins dir before n  
  → **REFUTED**: Suite already pins dir-before-n (06-aggregates.selt:194, 263)
