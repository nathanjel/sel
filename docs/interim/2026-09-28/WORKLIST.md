# Review work-list — 2026-09-28

Consolidated from the external review in `review.md` (one review, a section per host). This is round 2: the first round is `../2026-09-25/WORKLIST.md`, and IDs continue its numbering (SQL-05…, HOST-11…, TEST-12…, DOC-04…). Every finding has been **validated** on all five hosts, at commit 809cbd1 in a scratch worktree. The probes and their raw output are in `probes/`.

Tracker (the same IDs, round "2026-09-28"): https://claude.ai/artifact/7fMBYHgtFv818npEfchARH

**21 findings, 109 check rows.** Rows: REFUTED 12 · N/A 10 · FIXED 87

## Validation summary

### Verdict
- **Every reported item reproduces** (16 reports, 8 distinct defects). Each one was also swept through the other four hosts, and the sweep found more.
- **The chained-join defect (SQL-05)** gives byte-identical wrong SQL in all five translators. It is one of three defects in the same qualifier resolution: SQL-05, SQL-06 and SQL-07. SQL-07 is new and also fires on a single five-argument `LINK` (`_["R"]["rv"]`). SQL-08 is a separate new one: a promoted right-only read after `LINK_LEFT`.
- **The boundary findings are gaps in the 2026-09-25 HOST fixes.** Those covered the constructors spec §8 names. Every new defect is in a constructor it does not name: key lists, shapes, list keys, the `Dec` form, `value-set`, and `to-native` keys.

### New beyond the report
- **SQL-07** (High): the alias, the table name (C++ and Lisp only, so the hosts disagree), and the relation name after a five-argument `LINK` are all accepted as row qualifiers.
- **SQL-08** (Medium): `LINK_LEFT` then `_["sv"]` gives NULL in SQL and E_NO_KEY in memory.
- **SQL-06** (Low): a right-only field read through a later join's binder is refused where memory answers.
- **HOST-14**: a malformed `Dec` crashes C++ (`std::length_error`) and Lisp (a CL error), and builds `-0` or `007` in the others.
- **HOST-18**: `shaped` builds a record with a duplicate key in JS, PHP, Python and C++.
- **HOST-20**: constructor misuse raises a different host exception in every host.
- **Sweep hits:**
  - HOST-12: JS `shaped` / `fromEntries` keys.
  - HOST-13: PHP `num(array)` and JS `num({…})`.
  - HOST-16: Lisp `make-list-value`.
  - HOST-17: JS `shaped`, PHP and Python list keys.
  - HOST-15: Lisp `value-set` keys.

### Decisions (2026-09-28)
- **DOC-05:** the extra constructors stay public. They are validated and copied like the ones §8 names, and documented per host.
- **HOST-20:** a malformed constructor call is E_BAD_ARG in every host.
- **0.9.0:** not shipped as is. The CHANGELOG heading reads "0.9.0 — unreleased" until these fixes land; the release follows.
- The other session's dedup/cleanup edits in the tree are adopted.

### Fix progress
- **Every row is closed:** all 21 findings are fixed, refuted or n/a in all five hosts. This includes SQL-09 and SQL-10, both found while fixing.
- **Gate (2026-09-28):** ALL GREEN in 943 s, on every host including the JS bundles.
  - 1073 conformance cases and 1065 SQL cases on every host; 222 mutations caught, none survived.
  - The cross-host oracle: 72 agree and 30 refused over 6 translators; every `refused:` entry was refused.
  - The API probes agree on all 85, including the new constructor probes.
- **0.9.0:** the CHANGELOG entry covers this round and stays "unreleased" until the release. Nothing is committed.

### Decisions that were open before fixing
- **DOC-05:** which extra constructors stay public. Validate-and-copy or internalise, per host.
- **HOST-20:** what a malformed constructor call raises (E_BAD_ARG, or a documented host exception).
- **0.9.0:** the uncommitted CHANGELOG says "host values are checked and copied at the boundary". Land HOST-11..19 in 0.9.0, or narrow that line.

## Legend

Row origin: **R** reported (the reviewer names this host) · **C** control (the reviewer or the sweep says this host is right) · **s** sweep (not reported here; checked for the same defect) · **d** derived (added while consolidating). Row scopes beyond the hosts: `spec`, `suite`, `sql-hybrid` (plan_hybrid), `tool-*`, `docs`.

Statuses: CONFIRMED means the defect exists in that host (on spec/suite/tool rows: the gap is real). REFUTED means checked and not present. N/A means the construct does not exist in that host.

## Suggested order

- Tests first: TEST-12 (.sqlt cases from the probe files), then TEST-14's assertions once DOC-05 decides the public surface.
- SQL-05, SQL-06 and SQL-07 share one root, the qualifier resolution in indexQualified() and its ports. Fix them together by resolving a qualifier against the keys the row actually has; SQL-08 follows from the same row model.
- HOST-15 and HOST-16 (aliasing, High) before the validation findings.
- The 0.9.0 CHANGELOG entry already claims "host values are checked and copied at the boundary": either land HOST-11..19 before tagging 0.9.0, or narrow that line.

## Index

| ID | Sev. | Finding | JS | PHP | Py | C++ | Lisp | Other | Verdict |
|---|---|---|:-:|:-:|:-:|:-:|:-:|---|---|
| [SQL-05](#sql-05) | High | Chained LINK: a later join's left binder is read as the source relation | RF | RF | RF | RF | RF | suite:F sql-hybrid:F spec:✓ | CONFIRMED (all five hosts) |
| [SQL-06](#sql-06) | Low | A right-only field read through a later join's left binder is refused | dF | dF | dF | dF | dF | suite:F | CONFIRMED (all five hosts) |
| [SQL-07](#sql-07) | High | Row qualifiers the joined row does not have are accepted: alias, table name, relation name after a five-argument LINK | dF | dF | dF | dF | dF | suite:F spec:✓ | CONFIRMED (all five hosts; table name only in C++ and Lisp) |
| [SQL-08](#sql-08) | Medium | LINK_LEFT: a promoted right-only field reads NULL in SQL where an unmatched row has no such key | dF | dF | dF | dF | dF | suite:F sql-hybrid:F | CONFIRMED (all five hosts) |
| [SQL-09](#sql-09) | Medium | A relation joined twice under its alias renders the alias twice | dF | dF | dF | dF | dF | suite:F | CONFIRMED (all five hosts) |
| [SQL-10](#sql-10) | Low | A later step's refusal is reported before an earlier step's across a LINK | dF | dF | dF | dF | C✓ | suite:F | CONFIRMED (JS, PHP, Python, C++; Lisp correct) |
| [HOST-11](#host-11) | Medium | JS: a native bigint bypasses the integer digit cap in fromNative and Program.run | RF | s– | s✓ | s– | s✓ |  | CONFIRMED (JS only) |
| [HOST-12](#host-12) | Medium | Record, shape and list-key constructors skip the key UTF-8 check | sF | RF | RF | RF | s✓ |  | CONFIRMED (JS, PHP, Python, C++); Lisp refuted |
| [HOST-13](#host-13) | Medium | Value.num(Dec) bypasses the decimal digit caps | sF | sF | RF | RF | RF |  | CONFIRMED (all five hosts) |
| [HOST-14](#host-14) | Medium | Value.num(Dec) trusts a malformed decimal: -0, leading zeros, non-digits, negative scale | dF | dF | dF | dF | dF |  | CONFIRMED (all five hosts) |
| [HOST-15](#host-15) | High | Lisp: to-native returns the value's own key strings, and value-set keeps the caller's | s– | s– | s– | s– | RF dF |  | CONFIRMED (Lisp); other hosts n/a |
| [HOST-16](#host-16) | High | Collection constructors keep the caller's list | s✓ | s✓ | RF | s✓ | sF |  | CONFIRMED (Python, Lisp) |
| [HOST-17](#host-17) | Medium | Key and value counts are not checked against each other | sF | sF | RF | s✓ | s– |  | CONFIRMED (Python, JS, PHP list keys); PHP/C++ records checked |
| [HOST-18](#host-18) | Medium | Constructors build a record that holds the same key twice | dF | dF | dF | dF | d– |  | CONFIRMED (JS, PHP, Python, C++) |
| [HOST-19](#host-19) | Medium | Python: Value.bin accepts booleans as bytes | C✓ | s– | RF | s– | C✓ |  | CONFIRMED (Python only) |
| [HOST-20](#host-20) | Low | Constructor misuse raises host exceptions (or crashes) instead of a SEL error | dF | dF | dF | dF | dF | spec:F | CONFIRMED (a spec gap; every host differs) |
| [TEST-12](#test-12) | — | .sqlt cases: chained joins, qualifier vocabulary, LINK_LEFT promoted reads | · | · | · | · | · | suite:F | CONFIRMED (gap) |
| [TEST-13](#test-13) | — | SQL oracle and fuzzer never reach these shapes | · | · | · | · | · | tool-oracle:F tool-fuzz:F | CONFIRMED (gap) |
| [TEST-14](#test-14) | — | Boundary lanes cover text, fromNative, set and int, but not the other constructors | dF | dF | dF | dF | dF | tool:F | CONFIRMED (gap) |
| [DOC-04](#doc-04) | Low | Spec §7.4 names the source relation as a binder in the five-argument form; the suite and every host do not | · | · | · | · | · | spec:F | CONFIRMED (spec wording) |
| [DOC-05](#doc-05) | Medium | The public constructor surface beyond spec §8 is undocumented and unchecked | dF | dF | dF | dF | dF | spec:F docs:F | CONFIRMED (decision needed) |

Cell = origin mark + status (✗ confirmed, ✓ refuted, – n/a, ? open).

## SQL — SQL translation

<a id="sql-05"></a>
### SQL-05 · Chained LINK: a later join's left binder is read as the source relation

**Severity:** High · **Source:** JS #1 (js/src/sql/translator.mjs:480); PHP #1 (php/src/Sql/Translator.php:981); C++ #1 (cpp/sel_sql_translator.cpp:422); Lisp #2 (lisp/src/sql/translator.lisp:245); Python #1 (python/sel/sql/translator.py:418)

Every translator lists each join's left binder under the pipeline's source relation. The fix for SQL-01 (2026-09-25) introduced this. In a second LINK the left element is the first joined row, so a read of a name both earlier relations carry is E_NO_KEY in memory. The translator instead emits a join on the source relation's column, and the query returns rows.

> **Validation — CONFIRMED (all five hosts).** The probe file 99-zprobe.sqlt (p01–p22) gives byte-identical SQL in all five translators. In memory, all five agree on E_NO_KEY for p01, p06, p08, p09, p10, p14, p15 and p20, and all five translators emit SQL for each. The single-join control p16 is correct. Root cause: indexQualified() and its ports build the source entry's names from `plan.joins.map(j => j.leftBinder)`, and the left element of join n>1 is the joined row, not the source.

| Program / action | Today | Expected |
|---|---|---|
| `R .> LINK(S, X, Y, X["id"] == Y["id"]) .> LINK(T, A, B, A["id"] == B["tid"]) .> MAP(RECORD("v", _["tv"]))` | SQL … INNER JOIN "t" ON r.id = t.tid (all five) | refusal (E_SQL_SHAPE), as run() raises E_NO_KEY |

Variants / notes:
- p06: the pipeline source name in the second predicate (`R["id"] == B["tid"]`): R is bound to the joined row, and every host emits r.id
- p08: `_1["id"]` in the second three-argument LINK: emits r.id
- p09/p10: LINK_LEFT as the first or the second join: emits r.id
- p14/p15: the second join reuses an earlier binder name as its left binder (`Y`, `X`): emits r.id
- p20: `MAP(_["A"]["id"])` after the chain reads the nested joined row: emits r.id, memory E_NO_KEY
- Correct today: `A["rv"]` (R-only, promoted) → r.rv; `A["X"]["id"]` → r.id; `A["Y"]["id"]` → s.id; `_["A"]["rv"]` → r.rv; p07 stale `X` → E_SQL_UNBOUND; p11/p13 an ambiguous `_["id"]` after the chain → E_SQL_SHAPE
- The hybrid planner plans the reported program as pure_sql in every host, so execute_hybrid runs the wrong statement

Tests: sql/cases/26-links.sqlt: chained-join cases (the reported probe, p06, p08, p09, p10, p14, p15, p20, plus the correct reads as controls); cross-host SQL oracle: a chained join over relations sharing a column (TEST-13)

Related: SQL-01 (round 1), [SQL-06](#sql-06), [SQL-07](#sql-07), [TEST-12](#test-12), [TEST-13](#test-13)

Checks:

- [x] **suite** · derived · `sql/cases/26-links.sqlt` — No .sqlt case chains two LINKs  
  → **FIXED**: sql/cases/47-link-rows.sqlt link.chain.* (18 cases + plan case) (validation: No case in sql/cases/*.sqlt has two LINK steps (awk over the sources))
- [x] **JS** · reported · `js/src/sql/translator.mjs:480` — Left binders of every join listed under the source relation  
  → **FIXED**: joinRows(): the joined row modelled per spec §7.4; a later LINK's left binder is the joined row so far (validation: p01/p06/p08/p09/p10/p14/p15/p20 emit r.* where run() is E_NO_KEY (docs/interim/2026-09-28/probes/sqlt-js.out))
- [x] **PHP** · reported · `php/src/Sql/Translator.php:981` — Same  
  → **FIXED**: ported (validation: Same SQL as JS for every probe (diff empty))
- [x] **Python** · reported · `python/sel/sql/translator.py:418` — Same  
  → **FIXED**: ported (validation: Same SQL as JS for every probe (diff empty))
- [x] **C++** · reported · `cpp/sel_sql_translator.cpp:422` — Same  
  → **FIXED**: ported (validation: Same SQL as JS for every probe (diff empty))
- [x] **Lisp** · reported · `lisp/src/sql/translator.lisp:245` — Same  
  → **FIXED**: ported (validation: Same SQL as JS; only the E_SQL_SHAPE message text differs on p11/p13)
- [x] **sql (hybrid)** · derived — Does plan_hybrid route the chained program to SQL?  
  → **FIXED**: link.plan.chain-read-the-row-lacks-stays-in-memory: pure_memory in all five (validation: zplan.q01: pure_sql in all five hosts)
- [x] **spec** · derived · `spec/SPEC.md §7.4 "Joined rows"` — Does the spec decide what a later join's left binder holds?  
  → **REFUTED**: Decided: "the nested records the left element already carried … the left binders"; the left element of a second LINK is the joined row. All five interpreters agree

<a id="sql-06"></a>
### SQL-06 · A right-only field read through a later join's left binder is refused

**Severity:** Low · **Source:** derived from SQL-05 (probes p03, p21)

This is the other face of SQL-05. A field only the first join's right relation has is a promoted field of the first joined row, so `A["sv"]` (second predicate) or `_["A"]["sv"]` (after the chain) answers in memory. The translator looks the field up in the source relation and refuses with E_SQL_BINDING. The refusal is safe, but the program is valid and pushable.

> **Validation — CONFIRMED (all five hosts).** p03 and p21 are refused with E_SQL_BINDING in every translator, and every interpreter answers them. The fix belongs with SQL-05: resolve a later join's left binder against the joined row's promoted fields.

| Program / action | Today | Expected |
|---|---|---|
| `R .> LINK(S, X, Y, X["id"] == Y["id"]) .> LINK(T, A, B, A["sv"] == B["tid"]) .> MAP(RECORD("v", _["tv"]))` | E_SQL_BINDING (A["sv"] is not a field of that relation) — all five | s.sv (memory answers 7) |

Variants / notes:
- p21: `MAP(_["A"]["sv"])` after the chain: E_SQL_BINDING, memory 1
- The LINK_LEFT form needs SQL-08's rule: an unmatched first-join row promotes nothing from the right

Tests: sql/cases/26-links.sqlt, beside SQL-05's cases

Related: [SQL-05](#sql-05), [SQL-08](#sql-08)

Checks:

- [x] **suite** · derived · `sql/cases/26-links.sqlt` — Case for the right-only promoted read  
  → **FIXED**: link.chain.right-only-field-through-a-later-left-binder, link.chain.map-nested-right-only-field (validation: Not covered)
- [x] **JS** · derived — p03 / p21 refused  
  → **FIXED**: promoted fields of the joined row resolve through a later left binder (validation: E_SQL_BINDING; memory answers)
- [x] **PHP** · derived — p03 / p21 refused  
  → **FIXED**: ported (validation: E_SQL_BINDING; memory answers)
- [x] **Python** · derived — p03 / p21 refused  
  → **FIXED**: ported (validation: E_SQL_BINDING; memory answers)
- [x] **C++** · derived — p03 / p21 refused  
  → **FIXED**: ported (validation: E_SQL_BINDING; memory answers)
- [x] **Lisp** · derived — p03 / p21 refused  
  → **FIXED**: ported (validation: E_SQL_BINDING; memory answers)

<a id="sql-07"></a>
### SQL-07 · Row qualifiers the joined row does not have are accepted: alias, table name, relation name after a five-argument LINK

**Severity:** High · **Source:** derived from SQL-05 (probes p22–p27, zalias a01–a04)

A joined row's keys are its binders (spec §7.4). In the five-argument form those are `_1`, the given name and its lowercase, not the relation's own name, and a relation's SQL alias or table name is never one. The translator accepts any of these as a qualifier. So `R .> LINK(S, X, Y, …) .> MAP(_["R"]["rv"])` is SQL in every host while run() raises E_NO_KEY, even with a single join. The JS source comment states the rule on purpose ("a qualifier names a relation by its binding name, its table, its alias or a binder"), and that rule disagrees with the language.

> **Validation — CONFIRMED (all five hosts; table name only in C++ and Lisp).** In memory, all five hosts agree on E_NO_KEY for p22–p26 and a01/a02. Every translator emits SQL for p22–p26 and a01. For a02 (table name), C++ and Lisp emit SQL, while JS, PHP and Python refuse with E_SQL_SHAPE: a cross-host disagreement that the differential SQL fuzzer never drew.

| Program / action | Today | Expected |
|---|---|---|
| `R .> LINK(S, X, Y, X["id"] == Y["id"]) .> MAP(RECORD("v", _["R"]["rv"]))` | SELECT r.rv … (all five) | refusal; run() raises E_NO_KEY "R" |
| `R .> LINK(S, X, Y, R["id"] == Y["id"]) .> MAP(RECORD("v", _["sv"]))` | SQL joining on r.id (all five) | refusal; in memory R is the list variable, R["id"] is E_NO_KEY |
| `R(alias "ra") .> LINK(S, _1["id"] == _2["id"]) .> MAP(RECORD("v", _["ra"]["rv"]))` | SELECT ra.rv … (all five) | refusal; the row has no key "ra" |
| `R(table "rtab") .> LINK(S, _1["id"] == _2["id"]) .> MAP(RECORD("v", _["rtab"]["rv"]))` | C++, Lisp: SELECT ra.rv; JS, PHP, Py: E_SQL_SHAPE | refusal (and the hosts disagree today) |

Variants / notes:
- p24/p26: the right relation's name (`_["S"]`, `S["id"]`) after a five-argument LINK: the same
- p22: `_["R"]["id"]` after a chain
- Correct today: the three-argument form's `_["R"]` / `_["r"]` (p27, a03), and an unbound `ra` in a predicate (a04, E_SQL_UNBOUND)

Tests: sql/cases/26-links.sqlt: qualifier vocabulary per LINK form (name, lowercase, alias, table, five-argument names); SQL fuzzer: draw qualifiers from the alias and table as well (TEST-13)

Related: [SQL-05](#sql-05), [DOC-04](#doc-04), [TEST-13](#test-13)

Checks:

- [x] **suite** · derived · `sql/cases/26-links.sqlt` — Cases for each qualifier spelling  
  → **FIXED**: link.qualifier.* (11 cases); 09-refusals positions now run()'s; plan.index.* rewritten (alias → pure_memory; _1/_2 are row keys → pure_sql) (validation: Only the three-argument name/lowercase and the binder forms are covered)
- [x] **JS** · derived · `js/src/sql/translator.mjs:476-490` — Qualifier names = binding name, alias, table, binders  
  → **FIXED**: a qualifier must be a key the row has; aliases, tables and 5-arg relation names no longer bind (validation: p22–p26, a01 emit; a02 refused)
- [x] **PHP** · derived · `php/src/Sql/Translator.php` — Same  
  → **FIXED**: ported (validation: p22–p26, a01 emit; a02 refused)
- [x] **Python** · derived · `python/sel/sql/translator.py` — Same  
  → **FIXED**: ported (validation: p22–p26, a01 emit; a02 refused)
- [x] **C++** · derived · `cpp/sel_sql_translator.cpp` — Same, plus the table name  
  → **FIXED**: ported (validation: p22–p26, a01 and a02 (table name) emit)
- [x] **Lisp** · derived · `lisp/src/sql/translator.lisp` — Same, plus the table name  
  → **FIXED**: ported (validation: p22–p26, a01 and a02 (table name) emit)
- [x] **spec** · derived · `docs/internals/sql-translation.md` — Is the alias / table qualifier a documented SQL-side extension?  
  → **REFUTED**: Not documented anywhere; only a source comment in each translator. The row has no such keys (spec §7.4)

<a id="sql-08"></a>
### SQL-08 · LINK_LEFT: a promoted right-only field reads NULL in SQL where an unmatched row has no such key

**Severity:** Medium · **Source:** derived from SQL-05 (probe p17)

An unmatched LINK_LEFT row "promotes nothing from the right" (spec §7.4), so `_["sv"]` on it is E_NO_KEY in memory. The translator renders `s.sv`, which the LEFT JOIN fills with NULL, and the SQL result holds a row the program never produces. The general "only as sound as the schema's nullability" caveat does not cover this: the NULL comes from the join, not from a nullable column.

> **Validation — CONFIRMED (all five hosts).** p17 renders the same LEFT JOIN statement in all five translators, and all five interpreters raise E_NO_KEY "sv". The planner calls it pure_sql (zplan.q03).

| Program / action | Today | Expected |
|---|---|---|
| `R .> LINK_LEFT(S, X, Y, X["id"] == Y["id"]) .> MAP(RECORD("v", _["sv"]))` | SELECT s.sv … LEFT JOIN (all five) | refusal; run() raises E_NO_KEY on the unmatched row |

Variants / notes:
- Correct today: through the binder, `_["Y"]["sv"]` is NULL on an unmatched row in memory and in SQL (p18)
- A right field that is NULL in a matched row is also not promoted. That case is the nullable-column caveat already documented in docs/internals/sql-translation.md (NULL row of the structural table)

Tests: sql/cases/26-links.sqlt; cross-host SQL oracle: LINK_LEFT with an unmatched row and a promoted right-only read

Related: [SQL-05](#sql-05), [SQL-06](#sql-06), [TEST-13](#test-13)

Checks:

- [x] **suite** · derived · `sql/cases/26-links.sqlt` — Case for a promoted right-only read after LINK_LEFT  
  → **FIXED**: link.left.* (3 cases + plan case) (validation: No LINK_LEFT case reads a promoted right field)
- [x] **JS** · derived — p17  
  → **FIXED**: LINK_LEFT right-side promoted fields are optional; a read is refused, through the binder it is SQL (validation: SELECT s.sv … LEFT JOIN; memory E_NO_KEY)
- [x] **PHP** · derived — p17  
  → **FIXED**: ported (validation: SELECT s.sv … LEFT JOIN; memory E_NO_KEY)
- [x] **Python** · derived — p17  
  → **FIXED**: ported (validation: SELECT s.sv … LEFT JOIN; memory E_NO_KEY)
- [x] **C++** · derived — p17  
  → **FIXED**: ported (validation: SELECT s.sv … LEFT JOIN; memory E_NO_KEY)
- [x] **Lisp** · derived — p17  
  → **FIXED**: ported (validation: SELECT s.sv … LEFT JOIN; memory E_NO_KEY)
- [x] **sql (hybrid)** · derived — Planned as pure_sql?  
  → **FIXED**: link.plan.left-join-right-only-read-stays-in-memory, all five (validation: zplan.q03 pure_sql in all five)

<a id="sql-09"></a>
### SQL-09 · A relation joined twice under its alias renders the alias twice

**Severity:** Medium · **Source:** found while extending the cross-host SQL oracle (TEST-13)

A self-join, or a chain that joins an aliased relation a second time, rendered the binding's table alias for both occurrences (`INNER JOIN "customers" "c" … INNER JOIN "customers" "c"`), which the server rejects as ambiguous. SEL answers these programs. The translator now refuses them, E_SQL_SHAPE at the relation joined again, and the planner keeps them in memory.

> **Validation — CONFIRMED (all five hosts).** Probed with a temporary case in all five translators: identical duplicate-alias SQL. SQLite rejects it (ambiguous column name).

| Program / action | Today | Expected |
|---|---|---|
| `ORDERS .> LINK(CUSTOMERS, O, C, …) .> LINK(CUSTOMERS, A, B, A["customer_id"] == B["id"]) .> MAP(…)` | SQL with alias c twice (all five); SQLite: ambiguous column name | refusal (or distinct aliases) |

Variants / notes:
- `ORDERS .> LINK(ORDERS, A, B, …)`: alias o twice
- Unaliased relations were already apart: the joined side takes the five-argument right binder or `_2` as its alias; two three-argument joins of one unaliased relation both took `_2`

Tests: sql/cases/47-link-rows.sqlt link.twice.* (5); sql/oracle/cross.selc: a relation joined again under its alias

Related: [SQL-05](#sql-05), [TEST-13](#test-13)

Checks:

- [x] **suite** · derived · `sql/cases/47-link-rows.sqlt` — link.twice.*  
  → **FIXED**: link.twice.* (5 cases); cross.selc entry (validation: No case joined one relation twice)
- [x] **JS** · derived — Duplicate table alias on a second join  
  → **FIXED**: LINK analysis refuses an alias already open in the statement (validation: Same SQL as JS)
- [x] **PHP** · derived — Duplicate table alias on a second join  
  → **FIXED**: ported (validation: Same SQL as JS)
- [x] **Python** · derived — Duplicate table alias on a second join  
  → **FIXED**: ported (validation: Same SQL as JS)
- [x] **C++** · derived — Duplicate table alias on a second join  
  → **FIXED**: ported (validation: Same SQL as JS)
- [x] **Lisp** · derived — Duplicate table alias on a second join  
  → **FIXED**: ported (validation: Same SQL as JS)

<a id="sql-10"></a>
### SQL-10 · A later step's refusal is reported before an earlier step's across a LINK

**Severity:** Low · **Source:** found by the widened SQL fuzzer (TEST-13), seed 4242

Round 1's SQL-03 made Lisp compile the plan built so far before analysing a LINK, so an earlier step's key (a sort's, a bucket's) refuses first, where run() raises. The other four hosts did not, and reported a later step's refusal instead: the derived table over the join, or a FILTER three steps on. The differential SQL fuzzer flags the disagreement once its programs reach the shape.

> **Validation — CONFIRMED (JS, PHP, Python, C++; Lisp correct).** fuzz-sql 1500 4242 with the widened generator: two disagreements, Lisp against the rest; reproduced at 809cbd1.

| Program / action | Today | Expected |
|---|---|---|
| `CUSTOMERS .> TOP_BY(_["QTY"], 1) .> LINK(CUSTOMERS, O, C, O["id"] == C["id"]) .> TOP_BY(_["name"], 1)` | JS/PHP/Py: E_SQL_SHAPE (the joined row has no field); Lisp: E_SQL_BINDING at _["QTY"] | E_SQL_BINDING at the first TOP_BY key |
| `ORDERS .> BUCKET(_) .> LINK(CUSTOMERS, …) .> SELECT_COLS("name") .> FILTER(_["ID"] == 0)` | JS/PHP/Py: at the FILTER; Lisp: at BUCKET(_) | at BUCKET(_) |

Variants / notes:
- Pre-existing: same answers at 809cbd1

Tests: sql/cases/47-link-rows.sqlt link.order.* (2)

Related: SQL-03 (round 1), [TEST-13](#test-13)

Checks:

- [x] **suite** · derived · `sql/cases/47-link-rows.sqlt` — link.order.*  
  → **FIXED**: link.order.* (2 cases) (validation: Missing)
- [x] **JS** · derived · `js/src/sql/translator.mjs LINK case` — compile the plan so far before a LINK  
  → **FIXED**: the plan so far is compiled before a LINK, as Lisp does (validation: later refusal reported)
- [x] **PHP** · derived — same  
  → **FIXED**: ported (validation: later refusal reported)
- [x] **Python** · derived — same  
  → **FIXED**: ported (validation: later refusal reported)
- [x] **C++** · derived — same  
  → **FIXED**: ported; the widened fuzz found it too (confirmed by the link.order.* cases failing before the port) (validation: not run in the fuzz pass (SEL_IMPLS without cpp))
- [x] **Lisp** · control · `lisp/src/sql/translator.lisp:2674` — SQL-03 fix  
  → **REFUTED**: already compiles the plan before a LINK

## HOST — Host API boundary

<a id="host-11"></a>
### HOST-11 · JS: a native bigint bypasses the integer digit cap in fromNative and Program.run

**Severity:** Medium · **Source:** JS #2 (js/src/value.mjs:561)

`Value.int` checks the cap, but `fromNative` turns a bigint into text directly (`Value.text(x.toString())`), and a run context goes through fromNative. A 1,000,001-digit integer enters, and it fails later at the first arithmetic, at an innocent position.

> **Validation — CONFIRMED (JS only).** JS accepts the integer in fromNative, in nested fromNative and in run(). `X + 0` then raises E_RANGE inside the program, and `LEN(X)` answers 1000001. Python and Lisp route native integers through Value.int / make-int and raise E_RANGE. PHP ints are 64-bit and C++ has no fromNative, so those rows are n/a.

| Program / action | Today | Expected |
|---|---|---|
| `Value.fromNative(10n ** 1000000n)` | a 1,000,001-digit value | E_RANGE, as Value.int raises |
| `compile("LEN(X)").run({ X: 10n ** 1000000n })` | 1000001 | E_RANGE at the boundary |

Variants / notes:
- `Value.int(1.5)` and `Value.int(NaN)` throw a native RangeError from BigInt(), not a SEL error (see HOST-20)

Tests: JS host-boundary lane (TEST-14)

Related: HOST-06 (round 1), [HOST-13](#host-13), [TEST-14](#test-14)

Checks:

- [x] **JS** · reported · `js/src/value.mjs:561` — fromNativeAt bigint branch  
  → **FIXED**: fromNative routes a bigint through Value.int (cap); tools/check-js-runtime.mjs (validation: fromNative / {a: big} / run() accept; host-js.mjs)
- [x] **Python** · sweep · `python/sel/value.py:639` — from_native int branch  
  → **REFUTED**: from_native and run raise E_RANGE (goes through Value.int)
- [x] **Lisp** · sweep · `lisp/src/value.lisp:636` — from-native integer branch  
  → **REFUTED**: from-native and run raise E_RANGE (make-int)
- [x] **PHP** · sweep — Native ints are 64-bit  
  → **N/A**: PHP_INT_MAX is 19 digits; no bignum input path
- [x] **C++** · sweep — No fromNative; integer(long long)  
  → **N/A**: Value::integer takes long long

<a id="host-12"></a>
### HOST-12 · Record, shape and list-key constructors skip the key UTF-8 check

**Severity:** Medium · **Source:** PHP #2 (php/src/Value.php:416 fromNativeRows; record, shaped); C++ #2 (cpp/sel.cpp:1546 Value::record); Python #4 (python/sel/value.py:252 record/shaped)

Spec §8: "every key … is valid UTF-8" (or, in UTF-16 or code-point hosts, free of unpaired surrogates), whatever way it enters. `fromNative` and `set` check it, but the constructors that take a key list do not. The bad key enters and fails later, in INDEXES or dump, far from the input.

> **Validation — CONFIRMED (JS, PHP, Python, C++); Lisp refuted.** Controls: every host's text(), fromNative and set() raise E_UTF8. The key-list constructors accept the bad key in JS, PHP, Python and C++. Lisp exports no key-list constructor: from-native and value-set both check.

| Program / action | Today | Expected |
|---|---|---|
| `PHP Value::fromNativeRows([["a\xff" => 1]])` | accepted; INDEXES later E_UTF8 | E_UTF8 at the boundary |
| `C++ Value::record({"a\xff"}, {…})` | built; dump/INDEXES later E_UTF8 | E_UTF8 |
| `Python Value.record(["a\ud800"], […])` | built; INDEXES later E_UTF8 | E_UTF8 |

Variants / notes:
- PHP: `shaped`, `record`, `fromEntries`, `list($values, $keys)` keys; `fromNativeRows` trusts the first row only (a bad key in a later row is caught)
- C++: the unique-key path of `record` and `shaped(RecordShape)`; the duplicate-key path goes through set() and is checked
- Python: `shaped`, `record`, `from_entries`, `list(values, keys)`
- JS (sweep): `Value.shaped` and `Value.fromEntries` accept a lone surrogate key

Tests: each host's boundary lane: one assertion per constructor that takes keys (TEST-14)

Related: HOST-05 (round 1), [DOC-05](#doc-05), [TEST-14](#test-14)

Checks:

- [x] **PHP** · reported · `php/src/Value.php:289-416` — shaped / record / fromEntries / list keys / fromNativeRows first row  
  → **FIXED**: RecordShape::intern checks keys on a cache miss (covers shaped/record/fromShape/fromNativeRows); list keys checked; check-php-runtime (validation: All accept "a\xff"; fromNativeRows → INDEXES raises later (host-php.php))
- [x] **C++** · reported · `cpp/sel.cpp:1546` — record (unique path), shaped(RecordShape)  
  → **FIXED**: record and shaped check keys; unit.cpp (validation: Built; dump and INDEXES raise E_UTF8 later (host-cpp.cpp))
- [x] **Python** · reported · `python/sel/value.py:252` — shaped / record / from_entries / list keys  
  → **FIXED**: _pair_up/_check_list_keys; test_host_boundary.py (validation: All accept "a\ud800"; INDEXES raises later (host-py.py))
- [x] **JS** · sweep · `js/src/value.mjs:169-200` — shaped / fromEntries  
  → **FIXED**: shaped/fromEntries check keys (checkKey); check-js-runtime (validation: Both accept "a\uD800" (host-js.mjs))
- [x] **Lisp** · sweep — Any exported constructor taking keys unchecked?  
  → **REFUTED**: Only from-native and value-set take keys; both raise E_UTF8

<a id="host-13"></a>
### HOST-13 · Value.num(Dec) bypasses the decimal digit caps

**Severity:** Medium · **Source:** C++ #3 (cpp/sel.cpp:1482); Lisp #3 (lisp/src/value.lisp:178 make-num); Python #6 (python/sel/value.py:303)

Spec §8: "a number obeys the digit caps however it is built". The string form of `Value.num` enforces MAX_INT_DIGITS / MAX_FRAC_DIGITS, but the decimal-object form stores what it is given.

> **Validation — CONFIRMED (all five hosts).** Scale 1,000,001 is accepted by C++ `num(const Dec&)`, Lisp `make-num`, Python `Value.num(Dec)`, PHP `Value::num([...])` and JS `Value.num({…})`. 1,000,001 integer digits are accepted too. The string form raises E_RANGE in all five.

| Program / action | Today | Expected |
|---|---|---|
| `Value.num(Dec{digits 1, scale 1000001})` | accepted (C++, Lisp, Python, PHP, JS) | E_RANGE |

Variants / notes:
- 1,000,001 integer digits through the Dec form: accepted too (C++, PHP, Python, Lisp)
- PHP `Value::num(array)` is typed public (`array|string`); JS `Value.num` is typed `string` in sel.d.ts but takes a decimal record at run time

Tests: boundary lanes: Dec at the cap and one past it, for both caps (TEST-14)

Related: HOST-06 (round 1), [HOST-14](#host-14), [DOC-05](#doc-05)

Checks:

- [x] **C++** · reported · `cpp/sel.cpp:1482` — num(const Dec&) → from_dec  
  → **FIXED**: Value::num(const Dec&) rebuilds via dec_make + dec_guard (validation: scale 1000001 and 1000001 int digits accepted; small-mantissa form too)
- [x] **Lisp** · reported · `lisp/src/value.lisp:178` — make-num dec branch  
  → **FIXED**: make-num dec branch → dec-guard (validation: scale 1000001 and 10^1000001 accepted)
- [x] **Python** · reported · `python/sel/value.py:303` — num(Dec)  
  → **FIXED**: _check_decimal → D.guard (validation: scale 1000001 and 10^1000001-1 accepted)
- [x] **PHP** · sweep · `php/src/Value.php:253` — num(array)  
  → **FIXED**: Value::num(array) → Dec::checked → guard (validation: scale 1000001 and 1000001 int digits accepted)
- [x] **JS** · sweep · `js/src/value.mjs:217` — num(decimal record) — not in sel.d.ts  
  → **FIXED**: Value.num(decimal) → checkDecimal → D.guard (validation: Reachable at run time; scale 1000001 accepted. `num(5)` builds a broken value (TypeError at dump))

<a id="host-14"></a>
### HOST-14 · Value.num(Dec) trusts a malformed decimal: -0, leading zeros, non-digits, negative scale

**Severity:** Medium · **Source:** derived from HOST-13

The decimal-object constructor skips all of `Value.num`'s checks: no canonicalisation (spec §4.1) and no validity check. A hand-built decimal can be a negative zero, keep leading zeros, hold non-digits, or carry a negative scale. The results range from a non-canonical number to a native crash.

> **Validation — CONFIRMED (all five hosts).** Every host that has a public Dec path takes malformed input. The fix is either a validating, canonicalising constructor or an internal-only Dec path (DOC-05).

| Program / action | Today | Expected |
|---|---|---|
| `C++ Value::num(Dec{digits "7", scale -1})` | std::length_error (basic_string::_M_create) | a SEL error |
| `Lisp (make-num (dec-make nil 7 -1))` | CL bounding-indices-bad-error | a SEL error |
| `C++/PHP/JS num(Dec{digits "x"})` | t"x" (a number that is not a number) | E_NOT_NUM or E_BAD_ARG |

Variants / notes:
- neg zero: `t"-0"` in all five (Lisp via %make-dec)
- leading zeros: `t"007"` in C++ and PHP (string digits)
- negative mantissa with neg=false: `t"-5"` in JS, Python and Lisp
- negative scale: `t"1."` (PHP), `t"7."` (JS, Python), a crash (C++, Lisp)

Tests: boundary lanes (TEST-14), once DOC-05 decides whether the Dec form stays public

Related: [HOST-13](#host-13), [HOST-20](#host-20), [DOC-05](#doc-05)

Checks:

- [x] **C++** · derived · `cpp/sel.cpp:1482` — num(const Dec&)  
  → **FIXED**: E_BAD_ARG for malformed (no std::length_error), leading zeros/-0 canonical (validation: -0, "007", "x" accepted; scale -1 → std::length_error)
- [x] **PHP** · derived · `php/src/Value.php:253` — num(array)  
  → **FIXED**: Dec::checked: E_BAD_ARG for malformed, leading zeros and -0 canonical (validation: -0, "007", "x", scale -1 ("1.") accepted)
- [x] **Python** · derived · `python/sel/value.py:303` — num(Dec)  
  → **FIXED**: E_BAD_ARG for malformed, -0 canonical (validation: -0, negative digits, scale -1 ("7.") accepted)
- [x] **Lisp** · derived · `lisp/src/value.lisp:178` — make-num dec  
  → **FIXED**: E_BAD_ARG for malformed, -0 canonical (validation: %make-dec neg zero, negative digits accepted; scale -1 → CL error)
- [x] **JS** · derived · `js/src/value.mjs:217` — num(decimal record) — reachable though sel.d.ts types it string  
  → **FIXED**: checkDecimal: E_BAD_ARG for malformed, -0 canonical (validation: -0, negative digits, scale -1 ("7."), digits "x" all accepted (host-js3.mjs); num(5) builds a broken value)

<a id="host-15"></a>
### HOST-15 · Lisp: to-native returns the value's own key strings, and value-set keeps the caller's

**Severity:** High · **Source:** Lisp #1 (lisp/src/value.lisp:683 to-native)

SBCL strings are mutable. `to-native` puts the record shape's interned key strings into the alist it returns. Changing one renames the key in that value, in every value sharing the shape, and in a compiled program that built it: a re-run of `RECORD("foo", "x")` gave `{"boo"="x"}`. `value-set` stores the caller's key string without a copy, so the same happens from the other side.

> **Validation — CONFIRMED (Lisp); other hosts n/a.** Reproduced as reported: the shaped path renamed the key in the value and in the re-run of the same compiled program. The children path and value-set share strings too. JS, Python and PHP strings are immutable or copied by value, and C++ copies std::string, so the class cannot occur there.

| Program / action | Today | Expected |
|---|---|---|
| `(setf (char (car (first (to-native (run (compile-source "RECORD(\"foo\", \"x\")"))))) 0) #\b)` | the value and the program's next run read {"boo"="x"} | unchanged |

Variants / notes:
- The non-shaped children path of to-native shares keys too (value built with value-set)
- `value-set` with a caller string mutated afterwards: `{"zb"=…}` (derived)
- Controls: from-native copies keys, make-text copies, to-native copies text

Tests: lisp/tests/unit.lisp host-boundary-to-native-returns-host-owned-data: add keys; value-set copy

Related: HOST-02 (round 1), HOST-03 (round 1), [HOST-16](#host-16), [TEST-14](#test-14)

Checks:

- [x] **Lisp** · reported · `lisp/src/value.lisp:683` — to-native keys (shaped and children paths)  
  → **FIXED**: to-native copies keys (shaped and children paths); unit.lisp host-boundary-to-native-keys-are-the-hosts (validation: host-lisp.lisp: value and program re-run read "boo")
- [x] **Lisp (set)** · derived · `lisp/src/value.lisp:301` — value-set stores the caller's key string  
  → **FIXED**: value-set copies a new key (validation: mutating the key afterwards renames it)
- [x] **JS** · sweep — Mutable key strings?  
  → **N/A**: JS strings are immutable
- [x] **PHP** · sweep — Mutable key strings?  
  → **N/A**: PHP strings are values (copy on write)
- [x] **Python** · sweep — Mutable key strings?  
  → **N/A**: Python str is immutable
- [x] **C++** · sweep — Mutable key strings?  
  → **N/A**: std::string copied into the value; no to-native

<a id="host-16"></a>
### HOST-16 · Collection constructors keep the caller's list

**Severity:** High · **Source:** Python #2 (python/sel/value.py:267 record/shaped, :331 list)

Spec §8: "the boundary copies". Python's `record`, `shaped` and `list` store the list they are given, so replacing or appending an item in the caller's list changes the SEL value. `list(values, keys)` keeps the keys list too. Lisp's `make-list-value` keeps a simple-vector it is given.

> **Validation — CONFIRMED (Python, Lisp).** Python: record, shaped (values and keys), list and list keys all alias the caller's list. Lisp: make-list-value aliases a simple-vector. JS copies, PHP arrays copy on write, and C++ takes the vectors by value.

| Program / action | Today | Expected |
|---|---|---|
| `vs = [Value.text("1")]; v = Value.list(vs); vs[0] = Value.text("X"); vs.append(…)` | v is {"1"="X","2"="Y"} | v is {"1"="1"} |

Variants / notes:
- Python `list(values, keys)`: mutating keys afterwards renames the key
- Lisp (sweep): `(make-list-value vec)` then `(setf (svref vec 0) …)` changes the value; a CL list argument is copied by coerce
- Controls: Python from_entries and JS list/shaped/fromEntries copy; PHP arrays are values

Tests: boundary lanes (TEST-14)

Related: HOST-02 (round 1), [HOST-15](#host-15), [TEST-14](#test-14)

Checks:

- [x] **Python** · reported · `python/sel/value.py:267,331` — record / shaped / list / list keys  
  → **FIXED**: record/shaped/list/list keys copy; builtins use _list_owned/_record_owned (validation: host-py.py: all four alias)
- [x] **Lisp** · sweep · `lisp/src/value.lisp:206` — make-list-value simple-vector  
  → **FIXED**: make-list-value copies a vector (validation: host-lisp.lisp)
- [x] **JS** · sweep · `js/src/value.mjs:169,240` — list / shaped / fromEntries  
  → **REFUTED**: All copy (slice / map)
- [x] **PHP** · sweep — list / shaped / record  
  → **REFUTED**: PHP arrays are values
- [x] **C++** · sweep — list / record / shaped  
  → **REFUTED**: Taken by value (moved from the caller's copy)

<a id="host-17"></a>
### HOST-17 · Key and value counts are not checked against each other

**Severity:** Medium · **Source:** Python #3 (python/sel/value.py:270)

A record built from parallel key and value lists of different lengths is inconsistent. Python: fewer values than keys fails later with IndexError (dump, or any read); extra values are kept in storage, so `size()` is 2 while the dump shows 1. JS `shaped` does the same. The list-with-keys constructors silently drop extra keys.

> **Validation — CONFIRMED (Python, JS, PHP list keys); PHP/C++ records checked.** Reproduced in Python as reported. JS `shaped` behaves the same. PHP and Python list-with-keys drop extra keys, and JS fromEntries(…, true) drops all of them. PHP and C++ record constructors do check the counts. Lisp has no parallel-list constructor.

| Program / action | Today | Expected |
|---|---|---|
| `Python Value.record(["a","b"], [one])` | IndexError at dump / X["b"] | a boundary error |
| `Python Value.record(["a"], [one, two]).size()` | 2 (dump shows 1 field) | a boundary error |

Variants / notes:
- JS (sweep): `shaped(["a"], [1, 2])` → `{"a"=1}`; `shaped(["a","b"], [1])` → TypeError at dump
- PHP `list([1], ["1","2"])` and Python `list([1], ["1","2"])`: extra key dropped
- JS `fromEntries(entries, true)` ignores the given keys and renumbers ("5","7" → "1","2")
- PHP `shaped`/`record` and C++ `record`/`shaped` do check, but raise InvalidArgumentException / std::invalid_argument rather than a SEL error (HOST-20)

Tests: boundary lanes (TEST-14)

Related: [HOST-18](#host-18), [HOST-20](#host-20), [DOC-05](#doc-05)

Checks:

- [x] **Python** · reported · `python/sel/value.py:270` — record / shaped / list keys  
  → **FIXED**: E_BAD_ARG via _pair_up / _check_list_keys (validation: IndexError later; size 2 vs dump 1; list keys dropped)
- [x] **JS** · sweep · `js/src/value.mjs:169,189` — shaped / fromEntries isList  
  → **FIXED**: pairUp → E_BAD_ARG; fromEntries(…, true) keeps keys (validation: extra value dropped; missing value TypeError at dump; list keys discarded)
- [x] **PHP** · sweep · `php/src/Value.php:278` — shaped / record / list keys  
  → **FIXED**: record/fromShape/list keys → E_BAD_ARG (validation: records throw InvalidArgumentException; list keys mismatch silently dropped)
- [x] **C++** · sweep · `cpp/sel.cpp:1540,1571` — record / shaped  
  → **REFUTED**: Both throw std::invalid_argument (a non-SEL error, HOST-20)
- [x] **Lisp** · sweep — Parallel key/value constructor?  
  → **N/A**: None exported

<a id="host-18"></a>
### HOST-18 · Constructors build a record that holds the same key twice

**Severity:** Medium · **Source:** derived from HOST-17

A record cannot have a key twice: RECORD and set() keep the last value in the first position, and fromNative dedupes. The shape-taking constructors do not check, and they build `{"a"=1, "a"=2}`, a value no program can produce and whose reads depend on the key map.

> **Validation — CONFIRMED (JS, PHP, Python, C++).** shaped with a duplicate key dumps the key twice in all four hosts that export it, and so does Python record. Lisp exports no shape constructor, and from-native dedupes.

| Program / action | Today | Expected |
|---|---|---|
| `Value.shaped(["a","a"], [one, two])` | -{"a"=t"1", "a"=t"2"} (JS, PHP, Python, C++) | either RECORD's last-write-wins or a boundary error |

Variants / notes:
- Python `record(["a","a"], …)` also keeps both. PHP and C++ `record` dedupe (last wins)
- List keys: PHP and Python `list([…], ["5","5"])` keep both; PHP `list([…], ["x"])` makes a list keyed "x"

Tests: boundary lanes (TEST-14)

Related: [HOST-17](#host-17), [DOC-05](#doc-05)

Checks:

- [x] **JS** · derived · `js/src/value.mjs:169` — shaped  
  → **FIXED**: shaped follows RECORD (last value, first position); list keys distinct (validation: {"a"=1,"a"=2}; fromEntries dedupes)
- [x] **PHP** · derived · `php/src/Value.php:289,278` — shaped / list keys  
  → **FIXED**: shaped = record (RECORD semantics); intern and list keys refuse duplicates (validation: shaped and list keys keep duplicates; record dedupes)
- [x] **Python** · derived · `python/sel/value.py:260-290` — shaped / record / list keys  
  → **FIXED**: shaped/record follow RECORD; list keys distinct (validation: all three keep duplicates)
- [x] **C++** · derived · `cpp/sel.cpp:1571` — shaped(RecordShape)  
  → **FIXED**: shaped refuses a repeated key; record keeps RECORD semantics (validation: RecordShape with duplicate names accepted; record dedupes)
- [x] **Lisp** · derived — Exported shape constructor?  
  → **N/A**: None; from-native dedupes

<a id="host-19"></a>
### HOST-19 · Python: Value.bin accepts booleans as bytes

**Severity:** Medium · **Source:** Python #5 (python/sel/value.py:241)

`bytes([True])` is `b"\x01"` because bool subclasses int, so `Value.bin([True, False])` is `b0100`. JS and Lisp reject a boolean byte with E_RANGE ("bytes are bytes", spec §8).

> **Validation — CONFIRMED (Python only).** Reproduced. JS and Lisp reject booleans. PHP bin takes a byte string and C++ takes std::string or vector<uint8_t>, so there is no boolean path in either.

| Program / action | Today | Expected |
|---|---|---|
| `Value.bin([True])` | b01 | E_RANGE |

Variants / notes:
- Controls: Python `bin([1.0])` → E_RANGE; JS `bin([true])` → E_RANGE; Lisp `(make-bin (list t))` → E_RANGE

Tests: python/tests/test_host_boundary.py test_bin_rejects_what_is_not_a_byte: add True/False

Related: HOST-04 (round 1)

Checks:

- [x] **Python** · reported · `python/sel/value.py:241` — bytes(b) accepts bool  
  → **FIXED**: Value.bin: E_RANGE for a bool; E_BAD_ARG for a non-sequence (bytes(5) was 5 zero bytes) (validation: bin([True]) → b01; bin([True, False]) → b0100)
- [x] **JS** · control — Rejects true?  
  → **REFUTED**: E_RANGE
- [x] **Lisp** · control — Rejects t?  
  → **REFUTED**: E_RANGE
- [x] **PHP** · sweep — Boolean byte path?  
  → **N/A**: bin(string)
- [x] **C++** · sweep — Boolean byte path?  
  → **N/A**: typed bytes

<a id="host-20"></a>
### HOST-20 · Constructor misuse raises host exceptions (or crashes) instead of a SEL error

**Severity:** Low · **Source:** derived from HOST-13, HOST-14, HOST-17

Spec §8 says what a boundary rejects (E_UTF8, E_RANGE, E_NOT_NUM) but not what a malformed call raises. Today each host throws something different. Decide whether misuse is E_BAD_ARG everywhere or a documented host exception, and make the hosts agree.

> **Validation — CONFIRMED (a spec gap; every host differs).** Collected from the probes above. Not a data-safety defect once HOST-13/14/17 raise at the boundary, but the error type is part of the host API.

| Program / action | Today | Expected |
|---|---|---|
| `record/shaped count mismatch; num(Dec{scale -1}); Value.int(1.5)` | PHP InvalidArgumentException; C++ std::invalid_argument / std::length_error; JS TypeError / RangeError; Lisp CL error; Python IndexError later | one decided behaviour |

Variants / notes:
- JS `Value.num(5)` builds a value that throws TypeError at dump
- fromNative of a float: JS TypeError, PHP InvalidArgumentException, Lisp CL error (deliberate refusals, documented per host?)

Tests: check-api probes once decided

Related: [HOST-14](#host-14), [HOST-17](#host-17), [DOC-05](#doc-05)

Checks:

- [x] **spec** · derived · `spec/SPEC.md §8` — Say what a malformed constructor call raises  
  → **FIXED**: spec §8 'A malformed call is E_BAD_ARG'; spec/errors.md E_BAD_ARG row (validation: Not stated)
- [x] **JS** · derived — TypeError / RangeError  
  → **FIXED**: badArg() for text/bin/int/num/list/shaped/fromEntries/fromNative (validation: num(5), int(1.5), shaped mismatch)
- [x] **PHP** · derived — InvalidArgumentException  
  → **FIXED**: InvalidArgumentException → E_BAD_ARG (fromNative, record, fromShape, list) (validation: shaped/record mismatch, float fromNative)
- [x] **Python** · derived — IndexError later  
  → **FIXED**: _bad_arg: TypeError/IndexError → E_BAD_ARG (validation: record mismatch)
- [x] **C++** · derived — std::invalid_argument / std::length_error  
  → **FIXED**: std::invalid_argument → E_BAD_ARG (validation: record mismatch; num(Dec scale -1))
- [x] **Lisp** · derived — CL errors  
  → **FIXED**: bad-arg: CL errors → E_BAD_ARG (validation: make-num negative scale; float/ratio from-native)

## TEST — Test-suite and oracle gaps

<a id="test-12"></a>
### TEST-12 · .sqlt cases: chained joins, qualifier vocabulary, LINK_LEFT promoted reads

**Severity:** — · **Source:** derived from SQL-05..08

The minimal cases for every SQL finding in this round, written before any translator changes. The probe file is a ready draft: docs/interim/2026-09-28/probes/99-zprobe.sqlt and 98-zalias.sqlt.

> **Validation — CONFIRMED (gap).** No case chains two LINKs, reads an alias/table qualifier, or reads a promoted right field after LINK_LEFT.

Variants / notes:
- Assert refusal codes where memory raises, SQL where memory answers (the correct reads double as controls)
- A `--- plan` case per shape (pure_sql vs refused/pure_memory)

Tests: sql/cases/26-links.sqlt; sql/cases/25-hybrid-plans.sqlt

Related: [SQL-05](#sql-05), [SQL-06](#sql-06), [SQL-07](#sql-07), [SQL-08](#sql-08)

Checks:

- [x] **suite** · derived · `sql/cases/26-links.sqlt` — Cases for SQL-05..08  
  → **FIXED**: sql/cases/47-link-rows.sqlt: 32 statement cases + 3 plan cases, expectations derived from run() (validation: Missing)

<a id="test-13"></a>
### TEST-13 · SQL oracle and fuzzer never reach these shapes

**Severity:** — · **Source:** derived from SQL-05..08

All four SQL defects give the same wrong answer in all five hosts, so the differential SQL fuzzer cannot see them. The in-memory ⇄ database oracle can, but its corpus lacks the shapes. The fuzzer's second LINK always reads `O["customer_id"]`, a field only ORDERS has, so it never hits a shared name. Its qualifier vocabulary omits the relation's alias, table and five-argument names, and no program reads a promoted right field after LINK_LEFT.

> **Validation — CONFIRMED (gap).** Read tools/gen-programs.mjs:158 (one LINK form, customer_id only) and sql/oracle/cross.selc (single joins only).

Variants / notes:
- cross.selc: add a third relation (or a self-join) with shared column names; chained LINK and LINK_LEFT
- gen-programs.mjs (SQL mode): vary the second join's left read over shared / left-only / right-only names; draw qualifiers from alias/table/name
- Unmatched LINK_LEFT rows in the fixtures, with promoted reads

Tests: tools/check-sql-oracle.sh; tools/fuzz-sql.sh

Related: [SQL-05](#sql-05), [SQL-07](#sql-07), [SQL-08](#sql-08), TEST-03 (round 1), TEST-05 (round 1)

Checks:

- [x] **tool (oracle)** · derived · `sql/oracle/cross.selc` — Chained joins, qualifiers, LEFT promoted reads  
  → **FIXED**: cross.selc: 7 entries incl. 4 "refused:" ones; sqlo cross requires every translator to refuse those (validation: Single joins only)
- [x] **tool (fuzz)** · derived · `tools/gen-programs.mjs:158` — Second-join predicate and qualifier vocabulary  
  → **FIXED**: gen-programs: the join read varies over shared/left/right-only names; qualifiers _1/_2/O/C/ORDERS/CUSTOMERS; found SQL-10 (validation: Always O["customer_id"] == C["id"])

<a id="test-14"></a>
### TEST-14 · Boundary lanes cover text, fromNative, set and int, but not the other constructors

**Severity:** — · **Source:** derived from HOST-11..20

The 2026-09-25 boundary tests (TEST-06) assert the rules through the constructors spec §8 names. Every defect in this round is in a constructor they do not call: key lists, shapes, list keys, the Dec form, to-native keys, value-set keys and list aliasing. Add a per-host assertion for each public constructor and each rule, and a numbered check-api probe so the answers stay identical.

> **Validation — CONFIRMED (gap).** python/tests/test_host_boundary.py and lisp/tests/unit.lisp host-boundary-* call only text/bin/int/from_native/set/to_native.

Variants / notes:
- Rules × constructors: UTF-8 keys, count match, duplicate keys, digit caps and canonical form for Dec, copies in and out
- Depends on DOC-05 (which constructors stay public)

Tests: python/tests/test_host_boundary.py; lisp/tests/unit.lisp host-boundary-*; cpp/tests/unit.cpp test_host_boundary; JS/PHP lanes; tools/check-api.sh

Related: TEST-06 (round 1), [DOC-05](#doc-05)

Checks:

- [x] **JS** · derived — Boundary assertions for every public constructor  
  → **FIXED**: tools/check-js-runtime.mjs: HOST-11..20 section (validation: Missing)
- [x] **PHP** · derived — Boundary assertions for every public constructor  
  → **FIXED**: tools/check-php-runtime.php: HOST-12..20 section (validation: Missing)
- [x] **Python** · derived — Boundary assertions for every public constructor  
  → **FIXED**: python/tests/test_host_boundary.py (validation: Missing)
- [x] **C++** · derived — Boundary assertions for every public constructor  
  → **FIXED**: cpp/tests/unit.cpp test_host_boundary (validation: Missing)
- [x] **Lisp** · derived — Boundary assertions for every public constructor  
  → **FIXED**: lisp/tests/unit.lisp host-boundary-* (validation: Missing)
- [x] **tool** · derived · `tools/api.mjs` — check-api probes for the constructor rules  
  → **FIXED**: check-api: error.host.dec.*, error.host.key.utf8, error.host.malformed, ctor.dec.negzero (85 probes agree) (validation: No probe builds a record from key lists or a Dec)

## DOC — Documentation and spec text

<a id="doc-04"></a>
### DOC-04 · Spec §7.4 names the source relation as a binder in the five-argument form; the suite and every host do not

**Severity:** Low · **Source:** derived from SQL-07

The spec says the left binders are "`_1`, the name given in the five-argument form, and — when the argument is a bare name, or a pipeline whose source is one — that name and its ASCII lowercase". That reads as naming both the five-argument binder and the source. The suite (`rel.link.row-shape-named-right-five-argument-form`) and all five hosts bind only `_1`, the given name and its lowercase. The translators followed the wider reading (SQL-07).

> **Validation — CONFIRMED (spec wording).** The hosts agree with the suite. Only the sentence needs to separate the five-argument case from the named-argument case.

| Program / action | Today | Expected |
|---|---|---|
| `R .> LINK(S, X, Y, X["id"] == Y["id"]) .> MAP(INDEXES(_))` | X, x, _1, Y, y, _2, rv, sv (all five) | spec text that says so |

Tests: conformance/15-relational.selt already pins it

Related: [SQL-07](#sql-07)

Checks:

- [x] **spec** · derived · `spec/SPEC.md:760-762` — Reword the binder list per form  
  → **FIXED**: §7.4 binder sentence rewritten per form (validation: Ambiguous as written)

<a id="doc-05"></a>
### DOC-05 · The public constructor surface beyond spec §8 is undocumented and unchecked

**Severity:** Medium · **Source:** derived from HOST-12..18

Spec §8 names `Value.text/bin/num/bool/list/null`, `int`, `set` and `fromNative/toNative`, and says the boundary rules cover "every way data enters". Each host also exports constructors the spec does not name, and none are in the user docs except Lisp `make-list-value` (docs/usage/README.md:281). Decide per constructor: validate and copy like the named ones, or make it internal (underscore, drop from sel.d.ts, not exported). Then document what stays public.

> **Validation — CONFIRMED (decision needed).** Listed from sel.d.ts, the PHP public statics, Python staticmethods, sel.hpp and lisp/src/package.lisp. The spec text itself already implies these constructors must follow the rules.

Variants / notes:
- JS: `shaped`, `fromEntries`, `num(decimal)` at run time
- PHP: `shaped`, `record`, `fromShape`, `fromEntries`, `fromNativeRows`, `list($values, $keys)`, `num(array)`, `RecordShape::intern`
- Python: `shaped`, `record`, `from_entries`, `list(values, keys)`, `num(Dec)`
- C++: `record`, `shaped(RecordShape)`, `num(const Dec&)`, `num(shared_ptr<const Dec>)`
- Lisp: `make-num` with a dec, `make-list-value`

Tests: TEST-14

Related: [HOST-12](#host-12), [HOST-13](#host-13), [HOST-14](#host-14), [HOST-16](#host-16), [HOST-17](#host-17), [HOST-18](#host-18), [HOST-20](#host-20)

Checks:

- [x] **spec** · derived · `spec/SPEC.md §8` — Name the constructor set, or say "every public constructor"  
  → **FIXED**: §8: the rules bind every public constructor; new bullets for pairing, decimals, malformed calls (validation: Rule covers them only by implication)
- [x] **docs** · derived · `docs/usage/README.md` — Document what stays public  
  → **FIXED**: docs/usage/README.md#constructors: rules + per-host table (validation: Only make-list-value appears)
- [x] **JS** · derived — Validate-or-internalise decision per extra constructor  
  → **FIXED**: decision: public; validated + sel.d.ts documents Decimal, shaped, fromEntries
- [x] **PHP** · derived — Validate-or-internalise decision per extra constructor  
  → **FIXED**: decision: public; validated
- [x] **Python** · derived — Validate-or-internalise decision per extra constructor  
  → **FIXED**: public; validated
- [x] **C++** · derived — Validate-or-internalise decision per extra constructor  
  → **FIXED**: public; validated
- [x] **Lisp** · derived — Validate-or-internalise decision per extra constructor  
  → **FIXED**: public; validated; dec/dec-make/as-dec exported
