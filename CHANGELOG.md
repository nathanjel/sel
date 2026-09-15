# Changelog

SEL's releases, newest first. This file starts at 0.6.0; the entries below it
were written afterwards, from the commits, and are shorter for it.

Six manifests and `python/sel/__init__.py` carry the version, and so does the
top heading here — `tools/check-version.sh` relates all eight, so a release
whose notes were never written fails the check before the tag is cut.

Each entry ends with the three lanes that gate a release: conformance cases
(every host runs all of them), SQL translation cases, and mutations caught.

## [Unreleased]

Cross-language code review remediation for the hybrid planner and the optimisers (`docs/interim/sel_cross_language_code_review_remediation_plan.md`, all six parts).

  - **Planner contract, pinned.** `sql/cases/*.sqlt` gains `--- plan` and `--- tables` sections; `sql/cases/25-hybrid-plans.sqlt` holds 25 planner cases every host runs without a database, asserting classification, physical source tables, the SQL prefix, continuation presence and that the caller's AST survives planning and physical optimisation. The contract is written down in docs/SQL-TRANSLATION.md §12.1.
  - **JS and C++ plan the normalised tree.** Both unwound the raw AST, so `X = ORDERS; X .> TAKE(1)` was pure memory there and pure SQL in the other three. JS also dropped the planner's options on the way to the optimiser.
  - **`source_tables` means physical sources** in JS, Python and PHP (they reported SEL binding names; C++ already reported tables); deduplicated by physical name, a relation query reported as its text. Lisp's plan gains `dialect`, `continuation-ast`, `source-tables` and `hybrid-plan-hybrid-p`, filled on every path.
  - **A program stage 1 refuses is a pure-memory plan** in every host; Python, PHP and Lisp let `E_SQL_ASSIGN` escape `plan_hybrid`.
  - **The Lisp optimiser no longer writes into its input.** `optimize-ast-logical` and `optimize-ast-in-memory` are one copy-on-write walk, and tolerate a stage-1 clist in a child slot.
  - **`Program.run()` builds its physical tree once** (`physicalAst()` / `physical_ast()` / `physical-ast` / `physical_ast()`), keyed by the identity of the public AST, shared by copies in C++ under `call_once`. Measured against re-optimising every run: ×1.5 JS and ×3.5 Python on a small validation rule, ×1.15–1.36 on a 20-row pipeline; Lisp ×1.04. The public AST is documented immutable in every host.
  - **Dead code.** `walkNode`/`nodeHasVar` (JS), `walk_node`/`node_has_var` (Python), `opt_node_has_var` (C++), an unused import and a doubled doc block (PHP). One pipeline vocabulary per host: the C++ planner and the Lisp and dynamic-host translators read the optimiser's list.
  - **Guardrails.** A contributor checklist in docs/EXTENDING.md; host-local checks for what the shared fixtures cannot express.

Bucket pipelines, and the grouping verb.

  - **`GROUP_BY` is gone; the verb is `BUCKET`.** It was an alias of `BUCKET` in four hosts and a second implementation in PHP; SEL's vocabulary must not look like SQL, so the conformance and SQL case files, the registries, `dependencies()` and every translator now carry `BUCKET` alone (`conformance/16-bucket.selt`, `sql/cases/24-bucket.sqlt`). `dependencies()` had special-cased `GROUP_BY` and not `BUCKET` in four hosts, which the rename closes.
  - **`BUCKET(k) .> MAP(proj)` translates as one grouped statement** in every host — it is `BUCKET(k, proj)` in the evaluator, and the translator now folds the MAP into the bucket's projection (a `FILTER` between them is the `HAVING`). Before, the bare bucket was rendered as its keys and the MAP laid over that as a derived table: wrong counts, and accepted for bodies SEL refuses. A MAP after anything else has followed a bare bucket is refused (`E_SQL_SHAPE`).
  - **The hybrid planner never splits inside a bucket.** A prefix whose SQL rows would be keys rather than groups is not a split point and the MAP fall-through does not fire over one; `ORDERS .> BUCKET(k) .> MAP(RECORD("cid", _K, "n", COUNT(_)))` used to plan as `SELECT k … GROUP BY k` plus a continuation that answered `n = 1` for every group. Nine planner and statement cases pin the shapes; a Python unit test executes them against SQLite and compares with the evaluator.
  - The Python translator carried a second, dead copy of `analyze_pipeline` and `compile_statement` (~700 lines, silently shadowed); removed.

The bucket body's scope is the evaluator's (2026-09-15 review findings J, K and X).

  - **The group binder is the list of the bucket's members** (J). Inside the projection, and in a FILTER or sort written directly after a bare BUCKET, every host bound the group name to a *row*, so `MAP(g, RECORD("dept", _K, "amt", g["amount"]))` rendered `amount` as a bare column under GROUP BY — one member's value, chosen by the server — where SEL raises E_NO_KEY; `FILTER(_["total"] > 1)` *between* the BUCKET and the MAP resolved the alias the later MAP defines; and a per-group `MAX(_, body)` arm rendered `MAX(amount)` for a form SEL does not have. The five translators now carry a GROUP binder that refuses to be read or indexed (E_SQL_SHAPE at the node SEL's E_NO_KEY names), through which only `COUNT(g)` and `SUM(g, [x,] body)` reach the members — and `SUM`'s two-argument form binds `_` to the member as the evaluator does, which four hosts had refused as unbound. The MIN/MAX/AVG arm is gone. Lisp's row frame bound `_` and `_1` unconditionally, so `MAP(g, RECORD("x", _["amount"]))` translated there and was E_UNDEF_VAR everywhere else; it binds them only under a join now, as the other four do.
  - **`_K` is the group key only inside that body** (K). Every host bound `_K` to the single GROUP BY key wherever the statement had one, so `FILTER(_K $== "2")` *before* the BUCKET rendered `WHERE dept = '2'` and *after* the projection `HAVING dept = '2'`, where SEL compares the row's position in a list. Both are refused; `BUCKET(_K)` (the source row's key) is refused instead of recursing to E_SQL_DEPTH; a sort over the bare bucket's groups by `_K` still orders by the key. A PROJECTED binder covers the steps after the projection: a field is one of the projection's aliases (a key alias renders as the key, `MIN` of it in a HAVING) or a refusal, `COUNT(_)` over it is not `COUNT(*)` (X, first facet: nor is it over any relation row — `ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))` used to be one SQL row where SEL answers one per row), and the planner keeps each refused step in memory, where it raises what `run()` raises. 21 statement cases, 9 planner cases, 17 more shapes in the SQLite evaluator comparison; the `aggregateAliases` plan field is gone from all five hosts.
  - **`COUNT` of a structure is never the literal 0** (X, second facet). `source()`'s scalar fallback knew four list-yielding text functions and treated every other call as one value, so `COUNT(LIST(1, 2, 3))`, `COUNT(RECORD(…))`, `COUNT(TAKE(…))` and `COUNT(BUCKET(_, key))` inside a projection rendered `0` — a pure_sql plan answering 0 where SEL answers the number of children — and `HAS` over one folded to FALSE. The list is now the four text functions, the constructors and the optimiser's pipeline vocabulary, in all five hosts, and a scalar call is counted as one only once it has rendered as one (`COUNT(IF(TRUE, LIST(1, 2), 3))` is a refusal, not a 0). `agg.count.*-is-not-a-scalar`, `agg.has.structure-yielding-call-is-not-a-scalar`, `stmt.bucket.refuse-count-of-a-bucket-in-the-projection`, `plan.bucket.count-of-a-bucket-in-the-projection-stays-in-memory`.

The optimiser keeps the program's value (2026-09-15 review findings A, E, C2).

  - **A sort or a FILTER moves in front of a MAP or a SELECT_COLS only when it cannot tell** (C2, and the same rule's siblings). The MAP/SORT swap fired whenever the sort key named no field, so a keyless `SORT()` — which compares the MAP's outputs — ran on the MAP's inputs: `LIST(3, 1, 2) .> MAP(0 - _) .> SORT()` was `-1, -2, -3` in all five hosts and `-3, -2, -1` in the unoptimised evaluator, and `BUCKET(k) .> MAP(RECORD("cat", _K, …)) .> SORT_BY(_K)` renumbered the buckets before the MAP read their keys. The five optimisers now require a non-empty key over pass-through fields, and refuse to move any body that reads the whole row (`COUNT(_)`) or `_K` across a MAP, a SELECT_COLS or a sort — including the SORT/FILTER swap and the SORT/SORT elimination, which read `_K` after a sort had renumbered the rows. `rel.map.*`, `rel.select-cols.then-filter-*`, `rel.bucket.map-of-key-then-sort-by-key`; `plan.map.*`, `plan.sort.then-filter-on-the-key-splits`, `stmt.map.computed-field-then-keyless-sort`.
  - **`FILTER(5, TRUE)` is `(5)`** (E), as spec §7.3 says: the FILTER(TRUE) elimination fired on a first step over a scalar source, and `COUNT(FILTER(5, 1 > 0))` was 0. It is dropped only after a step, or over a list literal or constructor. A bound relation is not known to be a list, so `ORDERS .> FILTER(TRUE)` is a pipeline the planner pushes down (`WHERE TRUE`) instead of a bare relation it left in memory — where `execute_hybrid` then raised `E_UNDEF_VAR` for the relation. `agg.filter.scalar-source-with-constant-predicate`, `plan.pure-sql.constant-true-filter`, `stmt.filter.constant-true-is-a-where`.
  - **A fold never changes the form of a three-argument `SORT_BY`/`TOP_BY`** (A). The evaluator resolves that form by shape — a text literal third is the direction, else a bare name second is the binder — and the IF fold turned `SORT_BY(list, X, IF(TRUE, "DESC", "ASC"))` into the direction form, with `X` an undefined variable in `run()` and in the hybrid continuation while `translate_statement` rendered the binder form. That slot is now walked with folding off in all five hosts. `agg.sort_by.binder-with-folded-constant-key` and twins, `plan.sort.binder-form-keeps-its-form-when-the-key-folds`.
  - **The translators agree on a three-argument `SORT_BY` that is neither form** — `SORT_BY(_["qty"], IF(TRUE, "DESC", "ASC"))` — refusing `E_BAD_ARG` at the direction, as the four-argument form does; JS, PHP and Python threw a host TypeError, C++ refused elsewhere and Lisp refused at the key. Lisp also upper-cases the direction before checking it, as the other four and the evaluator do (`"desc"` was refused there). C++ rendered a derived table's columns by the SEL spelling that read them (`_sub1`.`Q` for a field named `q`) where the other hosts render the projection's alias.

Five quick wins from the 2026-09-15 review (findings AA, D, Q, U and the check.sh wiring).

  - **PHP's `physicalAst()` is keyed by the identity of `$ast`** (AA): reassigning `$program->ast` was noticed by JS, Python and Lisp and ignored by PHP, which kept running the old tree. The destructor drops the key's share before dismantling. `sel.d.ts` no longer declares `ast` readonly, since the documented contract is that reassigning the whole tree is fine.
  - **PHP honours `foldConstants: false`** (D), as §12.1 promised of all three dynamic hosts; the three host-local checks now assert the option reaches the optimiser.
  - **C++ `BUCKET` binds `_K` in the key expression to the source element's key** (Q) — a record's field name — as the other four and `SORT_BY` do, not to the position. `rel.bucket.key-expression-sees-the-source-key`, `rel.bucket.rebucket-by-group-key`.
  - **A `TAKE`/`DROP` count SEL rejects is `E_SQL_INVALID`** (U) in JS, PHP and Python, as in C++ and Lisp: the helper that turns a SEL error into that refusal was private to the constants module, so `ORDERS .> TAKE(1 / 0)` threw a host TypeError out of `translate_statement` and `plan_hybrid`. `stmt.take.count-sel-refuses-is-a-refusal`, `plan.take.count-sel-refuses-stays-in-memory`.
  - **`tools/check.sh` runs the PHP optimiser check.** A first-match `case` chose the JS arm whenever js was on the roster, so the PHP check — 86 assertions, including the executed-plan comparisons — had never run under the gate.

Grouped and sorted TEXT keys are collated (review 2026-09-15, finding L; the statement oracle).

  - **A TEXT group key is cast and collated like the `$` family compares text**, in every host: `GROUP BY CAST(`dept` AS CHAR) COLLATE utf8mb4_bin`, the `_K` projection as the identical expression (MySQL's `only_full_group_by` accepts nothing else), and `_K` inside a `HAVING` as `MIN(…)` of it — MariaDB and MySQL resolve a `HAVING` column only against the grouping *columns*, so the bare column is "unknown column in HAVING" once the grouping is the collated expression; the key is constant within its group, so `MIN` of it is the key. A TEXT `ORDER BY` key is collated the same way. Before, the evaluator answered four groups for `'A'`, `'a'`, `'B'`, `'b'` and MariaDB 11.8 answered two, from SQL every host emitted identically. `exact` columns stay bare, numeric keys are untouched. `utf8mb4_bin` is PAD SPACE, so `'A'` and `'A '` still merge on the MySQL family — recorded under the `text-collation` caveat (§11), with the NO PAD collations that would close it.
  - **A statement-parity oracle**, `sql/oracle/statements.json` with its own fixture per dialect family: pipelines translated with `translateStatement`, run on a live server and compared row for row with `run()` (`tools/oracle-db.sh`, `php php/bin/sqlo statements`). Eight statements agree on MariaDB 11.8, MySQL 8.4, PostgreSQL 17 and SQLite; the lane is what found the two server behaviours above.
  - **A MAP after a SORT_BY no longer wraps the sorted rows in a derived table**: MariaDB drops an `ORDER BY` inside a derived table that has no `LIMIT`, so the sorted rows came back in table order; the projection and the sort now share one statement, and only a `LIMIT`/`OFFSET`, `DISTINCT`, grouping or earlier projection wraps (Lisp had not wrapped on `DISTINCT` either; all five use one rule).
  - The Lisp translator called an undefined `SNODE-R` when a derived table wrapped a projection with no alias (review finding F): a crash that escaped `plan-hybrid`; it reads the node's right child now.

Bucket keys, and a FILTER after pagination (review 2026-09-15, findings G and H).

  - **A bare bucket's key is an index key.** `BUCKET(src, key)` builds a record keyed by the group key, and the evaluator used to make that key by stringifying whatever the key was: `NULL` became `""` / `"null"` / `"None"` depending on the host, a list or record collapsed onto one entry (rows lost), and Lisp crashed — while `BUCKET(src, key, proj)` grouped by identity. Now the two-argument spelling applies the index-key rule (spec §3.3): text or a number, verbatim; `NULL` is `E_NULL`, a boolean, binary, list or record key is `E_NOT_TEXT`, at the key expression, in all five hosts. The three-argument spelling is unchanged (identity, any key, `_K` the value), and for every key the bare spelling accepts `BUCKET(k) .> MAP(p)` and `BUCKET(k, p)` are one value — which is what the translator's fold needs. Spec §7.3 and docs/LANGUAGE.md now describe `BUCKET`; nine conformance cases in `16-bucket.selt` pin the rule and a Lisp `value-eql` crash on nested lists (`rel.distinct.nested-lists`).
  - **The translators follow:** a bare `BUCKET` over a `LIST`/`RECORD` key, or a key of kind BOOL/BIN, is refused (`E_SQL_SHAPE`) with or without the `MAP` that closes it; multi-column grouping is the projected spelling's (`stmt.bucket.multi-*` now spell it that way). A `NULL` key at run time joins the documented structural NULL caveat (§11).
  - **A `FILTER` after `TAKE`/`DROP` on a grouped statement is a `WHERE`** over the paginated rows, in every host: four hosts appended a `HAVING` (which runs before the `LIMIT` — SQLite answered two rows where `run()` answers one) and Lisp wrapped; all five now use one predicate (a `LIMIT`/`OFFSET` on the plan always forces the derived table; an `ORDER BY` alone never does, since `HAVING` then `ORDER BY` is sort-then-filter's rows). A `FILTER` over a bare bucket whose members are spent is refused like the `MAP`. Cases `stmt.bucket.filter-after-*`, `plan.bucket.filter-after-pagination-*`; executed in every host's unit lane.

Hybrid planner: the shape guard, completed (review 2026-09-15, findings I, AI, P — `docs/interim/review-2026-09-15-*`).

  - **A pipeline ending in a bare or sealed bucket is no longer `pure_sql`.** The full-pushdown probe never consulted `bucketRowsAreKeys`, so `ORDERS .> BUCKET(k)`, `… .> BUCKET(k) .> TAKE(1)` and `… .> BUCKET(k) .> FILTER(COUNT(_) > 1)` pushed `SELECT k … GROUP BY k` whole and `execute_hybrid` answered key rows where `run()` answers groups. Every host now guards the whole pipeline the way it guards the prefixes (`plan.bucket.*-at-the-end-*`).
  - **A `BUCKET` over an open or sealed bucket is refused (`E_SQL_SHAPE`)** instead of re-grouping the keys — `BUCKET(k) .> BUCKET(COUNT(_)) .> MAP(…)` used to translate to `GROUP BY COUNT(*)` (`stmt.bucket.refuse-bucket-over-*`); a bucket over a *projected* bucket is ordinary re-grouping and still translates.
  - **The MAP fall-through keeps the rows it splits over.** It fires only when every step after the `MAP` keeps the rows as they are (`FILTER`, `SORT_BY`, `TOP_BY`, `TAKE`, `DROP`) and reads only the projected keys; a downstream `BUCKET`/`MAP`/`SELECT_COLS`/`LINK`, a `DEDUPE`/`DISTINCT`/keyless sort, a custom pair that reads the whole row, or a dependency colliding with a projected key moves the split before the `MAP`. The continuation passes projected pairs through by key: `"cid", _["customer_id"]` no longer re-reads `customer_id` from a row that only has `cid` (E_NO_KEY), and `_["amount"] + 1` is no longer added twice. Eighteen planner cases in `25-hybrid-plans.sqlt` and four statement cases in `24-bucket.sqlt`; every host's unit lane executes the shapes (Python against SQLite, the others with the SQL prefix's own AST standing in for the database) and compares with `run()`. On the way, the Lisp fall-through was rewritten to the shared structure: it now walks every node kind (list literals included), accepts `LAZY_RECORD` bodies, and emits dependency columns in first-seen order like the other four (findings S and T of the same review).
  - Cross-validated by an independent agent after the fix, which found two holes in the new logic (closed, every host, cases added): a downstream step reading a dependency column under its *own* binder (`SORT_BY(s, s["name"])`) went unnoticed because only `_` was looked for — any name now counts; and names were compared case-folded where SEL's keys are case-sensitive, so `"Name", _["name"]` swallowed the `name` dependency — names are compared exactly, and a dependency differing from a projected key only by case keeps the MAP in memory (SQL aliases are not case-sensitive everywhere). PHP told pushable pairs from custom ones by folded name rather than by position (`"x"` / `"X"`); it uses the position now. `LAZY_RECORD` was in PHP's and C++'s SQL-special list only; it is in none. The Lisp unsupported-call walker now descends every node kind too. A second pass found the case folding itself done with the host's own upper-casing in three hosts (`namé`/`NAMÉ` one name to JS, Python and Lisp, two to PHP and C++) — it is ASCII case now, as everywhere in SEL — and that two *dependencies* differing only by case were still projected side by side; they keep the MAP in memory like a dependency clashing with a key.
  - The JS translator's dead `GROUP_BY` case label and wording are gone.

Constant folding keeps error positions.

  - **A folded `IF` or short-circuit reports where the unoptimised program does.** `IF(TRUE, "x", 1) >= 1` raised `E_NOT_NUM` at the `"x"` (1:10) in JS, PHP, Python and Lisp, whose optimisers replaced the IF by its branch node, and at the IF (1:1) in C++, whose IF arm tested for four arguments and never fired; `(FALSE AND TRUE) + 1` reported the `FALSE` in all five. Spec §6.3 names the node that actually failed — the operand the operator was handed, which is the IF or the AND — so a hoisted literal is now stamped with the folded node's position in every host, an IF is folded only when the chosen branch is a leaf literal (a compound branch keeps its own positions by staying where it is), and the C++ arm fires. This was the one disagreement `tools/check.sh`'s default fuzz seed had been finding.
  - Pinned three ways: nine conformance cases (`ctl.if.constant-condition-*`, `op.logic.*-keeps-the-*-position`), SQL cases showing translate() refuses the same source at the same column (`cond.refuse.constant-condition-mismatch-is-at-the-if`, `op.logic.short-circuit-constant-is-still-the-*`) and three planner cases (`plan.fold.*`), and a unit test per host that executes a hybrid and a pure-memory plan over the folded shapes and compares the error position with `run()`'s. The C++ SQL unit binary now runs under `make test` and `tools/check.sh`; it did not before.
  - The planner folds the same way, so `ORDERS .> FILTER(IF(TRUE, _["id"] > 1, FALSE))` now pushes down as the `CASE WHEN TRUE …` translate() always produced for it, rather than as `WHERE (id > 1)`; `plan.fold.compound-branch-stays-an-if` records the decision.

In-memory query engine optimizations, AST logical pipeline rewrites, and physical memory layout redesign. *(Note: Implemented in the Common Lisp / SBCL reference engine first; ports to PHP, JavaScript, Python, and C++ to follow per the porting roadmap.)*

  - **Group 1: Logical AST Pipeline Optimizer (`lisp/src/optimizer.lisp`).**
    - Automatic `FILTER` fusion (`FILTER(p1) .> FILTER(p2)` $\rightarrow$ `FILTER(p1 AND p2)`).
    - Predicate pushdown across `LINK` / `LINK_LEFT` (splits conjuncts, pushes left-dependent checks before joins, and pushes right-dependent checks into the inner relation).
    - Predicate pushdown across `MAP` (moves filters upstream when referencing unmodified fields).
    - Redundant `SORT` / `SORT_BY` elimination and `SORT_BY` + `TAKE` $\rightarrow$ `TOP_BY` bounded-heap selection fusion.
  - **Group 2: Physical Data Structures & Memory Layout (`lisp/src/value.lisp`, `decimal.lisp`).**
    - Shared Record Shapes (hidden classes / schema descriptors) decoupling shape metadata from contiguous simple-vector row storage for $O(1)$ slot lookups.
    - Scaled 64-bit fixed-point decimal arithmetic (`dec`: native 62-bit fixnum math with automatic bignum fallback; cached `dec-val` in numeric text values).
    - Pre-compiled join projectors in `LINK` / `LINK_LEFT` (pre-calculates slot offsets on the first matched row for zero-reflection copying).
    - Vector-backed `make-list-value` with zero-cons collection iteration.
    - Structural record deduplication in `DEDUPE` via integer hash combining, completely bypassing string serialization.
  - **Hybrid SQL Execution Planner (`lisp/src/sql/hybrid.lisp`).**
    - Partitions pipeline AST into maximal SQL pushdown prefixes and in-memory continuation suffixes.
    - Extended relational plan compiler with multi-table `INNER JOIN` / `LEFT JOIN` and derived table (`FROM (SELECT ...) AS _sub1`) subquery nesting.
  - **Scale Benchmarks & Parity Certification (`tools/scale-test/`).**
    - 10x scale enterprise benchmark battery (137,100 rows across 5 tables).
    - Validated 100% exact value parity against PostgreSQL 17 and MariaDB 11.8 across all 6 enterprise scenarios.
    - Achieved 7.0× in-memory speedup on deep 14-stage pipelines (from 5,604 ms down to 804 ms, approaching PostgreSQL's 713 ms).
  - **Status & Cross-Host Roadmap.**
    - Current implementation: Common Lisp (SBCL) reference engine.
    - PHP, JavaScript, Python, and C++ ports, followed by greenfield Go, Rust, and Java implementations, are scheduled per `docs/interim/sel_porting_worklist_and_checklist.md`.

## 0.7.4 — 2026-09-09

Optimizer-transparent existential precondition subqueries (`skel.prefilter`) without `IS TRUE` wrappers, enabling composite B-tree index seeks and semijoin decorrelation on MariaDB and MySQL.

  - **First-class `skel.prefilter` in the SQL dialect map.**
    - Adds `prefilter: ['from', 'corr', 'body']` to `skel` in `sql/dialects/ansi.json` (`EXISTS (SELECT 1 FROM {from} WHERE {corr} AND {body})`).
    - Omits the three-valued logic fold (`IS TRUE`) from the coarse precondition subquery emitted for separate relation prefilters (`prefilter: 'separate'`).
    - Eliminates the root `Item_func_istrue` AST node that previously prevented MariaDB/MySQL range optimizers from recognizing composite index keys (`(fname, value, cmsid)`) and caused subquery decorrelation to fail. Live query plans now successfully decorrelate into `LooseScan` semijoins driven by multi-part constant index seeks (`const,const`), reducing execution time to baseline (~1.30× legacy).
    - Preserves `({body}) IS TRUE` in the authoritative aggregate subquery (`skel.any`) where strict two-valued SEL semantics are enforced.
  - **Cross-runtime parity & test coverage.**
    - Implemented uniformly across PHP, JavaScript, Python, C++23, and Common Lisp.
    - Updated MariaDB separate prefilter test cases in `sql/cases/22-sargable-bindings.sqlt`, passing across all 5 host runtimes.

conformance 720 · sql cases 485 · mutations 161

## 0.7.3 — 2026-09-09

Separately planable necessary prefilters for relation bindings (`prefilter: 'separate' | 'inline'`), enabling composite B-tree index acceleration in MariaDB/MySQL aggregate subqueries.

  - **Separately planable necessary prefilters (`prefilter`, `splitSargable`).**
    - Adds `prefilter: 'separate' | 'inline'` (with boolean aliases `true` -> `'separate'`, `false` -> `'inline'`, and `splitSargable: true` -> `'separate'`) to both relation bindings and column/raw bindings across PHP, JavaScript, Python, C++23, and Common Lisp.
    - Resolves severe query plan degradations (up to 118.91× slowdown) on MariaDB and MySQL where residual collation expressions (`COLLATE utf8mb4_bin`) inside correlated subqueries prevent the optimizer from utilizing composite indexes on EAV tables.
    - In `ANY` aggregate subqueries on engines where `sargablePrefilter: true` (MariaDB, MySQL) when `prefilter === 'separate'` (on relation or column), SEL emits two sibling `EXISTS` subqueries conjoined by `AND`:
      `(EXISTS (SELECT 1 FROM rel WHERE corr AND coarse_prefilter IS TRUE) AND EXISTS (SELECT 1 FROM rel WHERE corr AND body IS TRUE))`
    - The coarse precondition subquery retains clean, uncast conditions and conjoins exact conditions (`g.fname = 'f_group' AND g.value = 'news'`), enabling MariaDB/MySQL optimizers to perform composite index range scans on `(fname, value, cmsid)` to dramatically restrict candidate rows before evaluating collation-sensitive residuals.
    - On PostgreSQL and SQLite (`sargablePrefilter: false`), sargable equality produces bare equality without coarse/residual splitting, emitting a single clean `EXISTS` subquery without duplication.
    - Relation-level `prefilter: 'inline'` overrides column-level `prefilter: 'separate'`, keeping all checks in a single subquery.
  - **Comprehensive test coverage & cross-runtime verification.**
    - Adds 6 new test cases across MariaDB, PostgreSQL, and SQLite in `sql/cases/22-sargable-bindings.sqlt`, expanding the SQL test suite to 485 cases.
    - 100% verified across all 5 host implementations (PHP, JavaScript, Python, C++23, Common Lisp).

conformance 720 · sql cases 485 · mutations 161

## 0.7.2 — 2026-09-09

Dialect map driven `sargablePrefilter` lexical configuration, enabling custom and derived SQL dialects extending MariaDB/MySQL to inherit sargable index prefilters.

  - **Architectural `sargablePrefilter` lexical configuration.**
    - Moves the engine-specific decision for emitting coarse equality prefilters (`col = 'val' AND ...`) from hardcoded dialect name string checks into the SQL dialect map (`sql/dialects/*.json`).
    - Configures `"sargablePrefilter": "true"` in `mysql-family.json` and `"false"` in `ansi.json`.
    - Custom and derived dialects extending MariaDB or MySQL (e.g. `Map::defineDialect('cms-mariadb', ['extends' => 'mariadb'])`) now properly inherit `sargablePrefilter: true` via standard dialect inheritance across all host implementations (PHP, JavaScript, Python, C++23, Common Lisp).
  - **Test coverage & dialect inheritance verification.**
    - Adds `bind.sargable.derived-dialect` to `sql/cases/22-sargable-bindings.sqlt` verifying that custom dialects extending `mariadb` inherit index-sargable prefilters.
    - SQL test suite expanded to 479 cases, passing across all 5 host implementations.

conformance 720 · sql cases 479 · mutations 161

## 0.7.1 — 2026-09-09

Index-sargable SQL bindings and numeric guard controls for relational database query optimizers, addressing 54×–102× execution regressions on indexed production tables.

  - **Index-sargable SQL bindings (`exact`, `sargable`, `collation`).**
    - Adds database-agnostic binding metadata parameters across all 5 host languages: `exact: bool = false`, `sargable: bool = false`, `guard: bool = false`, and `collation: ?string = null`.
    - `exact: true` skips defensive `CAST(... AS CHAR)` and `COLLATE` wrapping for string equality and ordering (`$==`, `$!=`, `$<`, `$<=`, `$>`, `$>=`) and `IN ("a", "b")` literal list expansions, restoring B-tree index seek and range scans across MariaDB, MySQL, PostgreSQL, and SQLite. Resolves 54×–102× query plan regressions on indexed production tables.
    - `sargable: true` emits a coarse equality prefilter combined with the exact binary check on MariaDB and MySQL (`((col = 'val') AND (CAST(col AS CHAR) COLLATE utf8mb4_bin = CAST('val' AS CHAR) COLLATE utf8mb4_bin))`), and clean bare equality on PostgreSQL and SQLite where text equality is already exact by default.
  - **Numeric guard control on EAV columns (`guard`).**
    - `guard: true` forces safe `numericGuard` evaluation on dirty EAV string columns even when declared `NUM`, preventing MariaDB error 1292 (`Truncated incorrect DOUBLE value`).
  - **Test coverage & cross-host parity.**
    - Adds 16 new test cases across all dialects in `sql/cases/22-sargable-bindings.sqlt`, expanding SQL test suite to 478 cases.
    - 100% verified across JavaScript, PHP, Python, C++23, and Common Lisp, with live database oracle validation on MariaDB 11.8, MySQL 8.4, PostgreSQL 17, and SQLite 3.51 (161 caught mutations, 0 survived).

conformance 720 · sql cases 478 · mutations 161

## 0.7.0 — 2026-09-09

Strategic release introducing first-class `NULL` semantics, loud refusal (`E_NULL`)
on unhandled operations, null and vacuous coalescing (`??`, `???`), safe container
navigation (`GET`, `PATH`), presence predicates (`IS_NULL`, `IS_NOT_NULL`, `IS_BLANK`,
`IS_PRESENT`), and comprehensive SQL pushdown verified against live database engines.

  - **First-class `NULL` semantics and loud refusal (`E_NULL`).**
    `NULL` is now a distinct absence value (`NONE` with no scalar and no children).
    SEL strictly refuses silent coercion: arithmetic (`NULL + 1`), string operations
    (`UPPER(NULL)`, `NULL & "x"`), and comparisons (`NULL == 0`, `NULL $== ""`, `NULL < 5`)
    all fail loudly with `E_NULL` instead of quietly evaluating to 0, empty text, or false.
    `EQL` structurally checks nullness: `NULL EQL NULL` is TRUE, and `NULL EQL ""` is FALSE.

  - **Coalescing operators `??` and `???`.**
    - `A ?? B` (Null coalescing): evaluates `A`; if `A` is `NULL` or encounters a missing
      key / undefined variable (`E_NO_KEY`, `E_UNDEF_VAR`), evaluates and returns `B`.
    - `A ??? B` (Vacuous / data-invariant coalescing): evaluates `A`; if `A` is vacuous
      (`NULL`, missing, empty text `""`, whitespace-only text, or empty container),
      evaluates and returns `B`. Right-associative with binding power 9.

  - **Safe container navigation (`GET`, `PATH`).**
    - `GET(container, key [, default])`: safe key retrieval from maps and lists. If the key
      is missing or the container is non-indexable/NULL, returns `default` (or `NULL` if omitted),
      suppressing `E_NO_KEY` and `E_UNDEF_VAR`.
    - `PATH(container, path [, default])`: safe multi-step navigation along a dot/slash-separated
      string (e.g. `"order.customer.name"`) or list of keys. Returns `default` (or `NULL` if omitted)
      if any segment is missing or not a container.

  - **Null and blank inspection functions.**
    - `IS_NULL(x)`: returns TRUE if `x` is `NULL`, FALSE otherwise.
    - `IS_NOT_NULL(x)`: returns FALSE if `x` is `NULL`, TRUE otherwise.
    - `COALESCE(v1, v2, ...)`: variadic, returns the first non-NULL argument (or NULL if all NULL).
    - `IS_BLANK(x)`: returns TRUE if `x` is NULL, empty string `""`, or only whitespace (`[ \t\r\n]`).
    - `IS_PRESENT(x)`: returns TRUE if `x` is not blank (not NULL and has non-whitespace characters).

  - **SQL pushdown & database oracle validation.**
    - All null operations and functions (`??`, `???`, `IS_NULL`, `IS_NOT_NULL`, `COALESCE`,
      `IS_BLANK`, `IS_PRESENT`) translate directly to SQL expressions across MariaDB, MySQL,
      PostgreSQL, and SQLite.
    - PostgreSQL strictly types parameters and text functions using `{textCast:0}` to prevent
      prepared statement `42P18: Indeterminate datatype` and `btrim(integer)` errors.
    - MariaDB and MySQL leverage regex replacement (`REGEXP_REPLACE`) for exact 4-character
      whitespace handling in `IS_BLANK`, `IS_PRESENT`, and `???`.
    - SQLite utilizes multi-character trim (`' ' || char(9) || char(13) || char(10)`).
    - Fully validated against pinned Docker instances of MariaDB 11.8, MySQL 8.4, PostgreSQL 17,
      and SQLite with 0 differences across 433 expressions and 22 row rules.
    - Container functions `GET` and `PATH` remain strictly in-memory (registered with refusal
      `takes a container, and a SQL expression is a scalar`).

conformance 720 · sql cases 462 · mutations 161

## 0.6.1 — 2026-09-09

A release closing §4.1a of the kind warrant, shipping TypeScript typings,
and standardizing dialect representations.

  - **Function arguments read as numbers receive `numericGuard` (§4.1a).**
    Previously, numeric guards were applied in `binary()` and `unary()` while
    function calls reached neither. An undeclared or TEXT column passed to a
    numeric function argument (such as `ABS(T)`, `ROUND(T, 2)`, or `LEFT("abc", T)`)
    reached the database unguarded. On SQLite, MariaDB, and MySQL, passing
    non-numeric text returned 0 rather than raising `E_NOT_NUM`, so `ABS(T) == 0`
    evaluated to TRUE for every row.
    Numeric argument positions are now tracked across all built-in functions.
    Arguments in numeric positions not known to be NUM are wrapped in
    `numericGuard` on MariaDB, MySQL, and PostgreSQL; on dialects without a
    numeric guard (SQLite, ANSI), translation safely refuses with
    `E_SQL_UNSUPPORTED`.

  - **Standardized ANSI `binaryCast`.**
    ISO/IEC 9075-2 specifies `BLOB` rather than MySQL's `BINARY`. `ansi.json`
    now specifies `CAST({0} AS BLOB)` for `binaryCast`, and `mysql-family.json`
    explicitly defines `CAST({0} AS BINARY)`.

  - **SQLite TRIM family emits single-line SQL.**
    SQLite's `TRIM`, `LTRIM`, and `RTRIM` templates now concatenate character
    codes via `' ' || char(9) || char(13) || char(10)` rather than embedding
    raw tab, CR, and newline literals inside JSON string templates, preventing
    multi-line query emissions while preserving exact whitespace stripping.

  - **Cross-host Map Replay comparison.**
    `tools/check.sh` now asserts that all five hosts produce byte-identical
    summary output (rebuilding registrations and lookups compared) during
    sql map replay.

  - **TypeScript definitions shipped.**
    `js/src/sel.d.ts`, `js/src/sql.d.ts`, and `js/src/sql/index.d.ts` are now
    provided and declared via `"types": "./js/src/sel.d.ts"` in `package.json`.

  - **Python integration example.**
    `examples/integration-python.py` added to mirror the JS and PHP worked
    integration examples with byte-identical output.

conformance 631 · sql cases 454 · mutations 161

## 0.6.0 — 2026-09-09

**If you registered your own dialect with `extends: 'ansi'` and overrode
nothing, your emitted SQL changes in this release.** `ansi` now says what the
standard says: `CAST(x AS CHARACTER VARYING)` rather than `AS CHAR`,
`CAST(x AS NUMERIC)` rather than `AS DECIMAL(38,10)`, `CHAR_LENGTH`,
`TRIM(BOTH FROM x)`, `SUBSTRING(x FROM n FOR m)`, `||` for concatenation, and
`COLLATE UCS_BASIC` where the old map collated with whatever the server felt
like. The old output was MySQL wearing the word ANSI, and on a conformant
server it produced matches SEL refuses — so this is a correction, not a
preference. Run your suite against it.

The four shipped dialects — `mariadb`, `mysql`, `postgresql`, `sqlite` — emit
byte-identical SQL to 0.5.0. Not one expected string in `sql/cases` moved;
`mysql-family` now states the eight keys it used to inherit, so making `ansi`
standard could not drag it along.

  - **`ansi` is checked, with no ANSI server to check against.** A dialect
    registered only inside the oracle and the case runner, `ansi-probe`,
    extends `ansi` and runs its emitted SQL against PostgreSQL — the closest
    conformant server there is. Where PostgreSQL is stricter than the standard
    the probe still refuses honestly rather than passing quietly. `sql/MAP.md`
    §1.1 has the measured table of what `ansi` used to claim and what a
    conformant server actually did with it.

  - **The guard evaluates a raw binding twice, and now says so.**
    `numericGuard` names its operand in both the test and the value, so a
    binding whose SQL is a raw expression — a subquery, a function call — is
    evaluated twice per row. It has always done this. `docs/SQL-KINDS.md` §6
    now states it as an exclusion and gives the fix: name the expression once
    in a derived table or CTE and bind the column, `SELECT a.b AS c FROM x`,
    then let SEL use `c`.

  - **One reader lied about whitespace.** JavaScript's `String.prototype.trim`
    strips U+FEFF, so the JS conformance reader silently deleted a byte-order
    mark from a case body while the other four kept it — five readers, five
    slightly different files. All five now trim exactly the four characters
    SEL calls whitespace (space, tab, CR, LF) and nothing else, and
    `conformance/README.md` states that as a normative rule rather than
    leaving it to each host's standard library.

  - **The outliers nothing looked at.** Cases for the corners a language
    usually dies on: nesting at `MAX_DEPTH`, `NONE` reaching a scalar
    position by every route, the ASCII-only edge of `UPPER`/`LOWER`,
    short-circuit in `ANY`/`ALL`, and the numeric-looking strings that are
    text because in SEL numbers are text.

  - **The caveat gate could not see `ansi`.** A caveated map entry must be
    pinned by a string in a case file, for every target — but `ansi` is not a
    target, so entries reachable only through it were never pinned, and a
    mutation to one of them survived. The gate now registers the probe before
    it walks the dialects, and it immediately found two more unpinned entries.

  - **The sdist could ship a virtualenv.** Hatchling's `include` patterns are
    gitignore syntax, and an unanchored `python/` matches that directory at any
    depth — so it swept in `python/.venv-wheel/`, the throwaway venv
    `PACKAGING.md` tells you to build in order to verify the wheel, and in a
    checkout with a git worktree under `.claude/`, a second copy of the whole
    repository. Every pattern is anchored now and the venv and `__pycache__`
    are excluded by name. The wheel was never affected, only the sdist.

  - **Two holes in the release gate itself.** `tools/impls.sh` checks that the
    JS bundle and the installed Python wheel are newer than the sources they
    were built from, and did not check the same for C++ — so a `cpp/build`
    left over from an earlier branch passed 631 conformance cases and
    disagreed with the other four hosts only in the SQL fuzz, which reads
    exactly like a translator bug and is nothing of the kind. (`cmake --build`
    builds the installable library; the harness binaries come from the
    Makefile, so a plausible build command leaves them untouched.) And
    `tools/fuzz-sql.sh` ended its per-dialect summary with a literal
    `0 disagreements` printed immediately after printing a diff — the lane
    failed correctly, but the sentence was false. It counts them now.

Upgrading from 0.4.x: you also meet everything 0.5.0 refuses — see below.

conformance 631 · sql cases 450 · mutations 161

## 0.5.0 — 2026-09-08

The kind warrant: *SQL never reports a match for a row SEL would have
refused*. Reported by the first external user of the translation layer, and
closing it takes functionality away. A minor bump and not a patch, because
programs that translated in 0.4.2 refuse now.

What refuses that did not: an undeclared column in a boolean position
(`E_SQL_SHAPE` — in the MySQL family a boolean *is* a TINYINT, so no dialect
can be asked); a TEXT or undeclared column in a numeric position on `sqlite`
and `ansi` (`E_SQL_UNSUPPORTED` — neither can ask whether a value is a
number); and a registered dialect whose `numericGuard` disagrees with its
`funcs.ISNUM`, which used to fail silently. Declare `type: 'BOOL'` or
`type: 'NUM'` to buy the old behaviour back where the column really is one.

What emits differently: on `mariadb`, `mysql` and `postgresql` an uncertain
numeric operand is wrapped `CASE WHEN <ISNUM> THEN <cast> ELSE NULL END`, so a
row holding `'25/298'` contributes NULL instead of 25. It costs the index on
that column; declaring `NUM` returns the unguarded path.

conformance 621 · sql cases 421 · mutations 160

## 0.4.2 — 2026-09-08

Why `postgresql`'s UTF8 is not the key `mysql-family` just got, written down
where the map can be read. The artifact generators stop rewriting files whose
content did not change.

conformance 621 · sql cases 391 · mutations 115

## 0.4.1 — 2026-09-08

`textCharset` becomes a lexical key of the dialect map rather than a spelling
choice. Worked examples in seven categories, in all five languages, with a
lane that diffs them against each other. How to derive the dialect from the
connection rather than write it down.

conformance 621 · sql cases 391 · mutations 115

## 0.4.0 — 2026-09-07

The SEL→SQL translation layer, in all five hosts, over 118 commits: the
dialect map as data with one place that defines it, the translator, the
aggregates, the oracle harness that runs generated SQL against real servers,
and the mutation lane.

conformance 621 · sql cases 390 · mutations 114

## 0.3.0 — 2026-08-27

Python joins as the fifth host, and major bugfixes across the others that
having a fifth reading of the spec turned up.

*(A stray tag named `v` points at this release's tag object. Harmless, left
alone.)*

conformance 587

## 0.1.4 — 2026-08-17

A clean release tag. `CMakeUserPresets.json` stops being tracked.

conformance 316

## 0.1.3 — 2026-08-17

Fix the Conan recipe, which had never been run.

## 0.1.2 — 2026-08-17

Host API parity — predicates, kind constants, `size()`, and a harness that
checks the four hosts expose the same surface.

## 0.1.1 — 2026-08-17

Single-file JS bundle.

## 0.1.0 — 2026-08-17

First tag. Four hosts — JavaScript, PHP, C++ and Common Lisp — against one
conformance corpus. No SQL layer yet.

conformance 316

*(0.2 was never tagged.)*
