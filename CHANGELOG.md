# Changelog

SEL's releases, newest first. This file starts at 0.6.0; the entries below it
were written afterwards, from the commits, and are shorter for it.

Six manifests and `python/sel/__init__.py` carry the version, and so does the
top heading here — `tools/check-version.sh` relates all eight, so a release
whose notes were never written fails the check before the tag is cut.

Each entry ends with the three lanes that gate a release: conformance cases
(every host runs all of them), SQL translation cases, and mutations caught.

## [Unreleased]

Every joined row is built from its own pair of elements (2026-09-23; WL-001 SEL-0053).

  - **`LINK` and `LINK_LEFT` decide each row from the two elements it joins, not from the first ones.** Every host compiled its row builder from a first pair and used it for all: over relations whose records differ in shape Lisp read each row's storage by the first row's layout (a joined `amt` holding a whole record, and crashes), and the five hosts disagreed on up to 242 of 6,000 generated programs over missing, null and boolean fields — right NULLs copied by one path and dropped by another, promotion decided by the first right element. Spec §7.4 now says it outright: which fields a joined row carries is decided for each pair from its own two elements, in each element's own field order; a *nested record* is a record with at least one field, and a list, BOOL, BIN or NULL is a scalar field; the lowercase binder key is added only where the element has none, and an element that already has a field named like its relation is bound as it is; LINK_LEFT's null record is shaped like the first right element as bound (just the name keys with no right elements, NULL with no name). 21 conformance cases (`conformance/16-joined-rows.selt`), generated from a model of §7.4 written from the text and no host, pin it, and a new gate lane, **joined rows vs spec model** (`tools/join-rows-oracle/`), checks 2,000 generated mixed-shape `LINK`/`LINK_LEFT` programs — named, positional, five-argument, literal and pipeline sides, chains — against the same model on every roster entry.
  - **The fast path stays compiled.** A plan per pair of shapes carries the only two facts that change a row — whether each left field is a nested record, and whether each right field it takes is a non-NULL scalar — and checks them as it copies the fields; the left checks run once per left row, and JS, C++ and Lisp skip the right ones when the join's own pass over the right rows saw every row flat. SEL-0052's pre-filter no longer needs its first-row ownership rule or its one-shape-per-relation gate, since no row depends on another: both are gone, and three of its conformance cases, written to the first-row rule, now say what the per-pair rule answers.
  - **Cost, against the same tree with the pre-SEL-0053 row builders, interleaved, seven runs:** S1 C++ +1.1%, JS +2.5%, PHP +3.5%, Python +2.9%, Lisp 0; S6 C++ +0.4%, JS +1.5%, PHP +0.9%, **Python +4.8%** (open as SEL-0056), Lisp −2.8%; S2–S4 within spread; S5 faster on every host (C++ −13%, JS −6%, PHP −30%, Lisp −16%, Python −1%) now that the retired gates no longer hold the pre-filter back.

Validation: 980 conformance cases per host; pytest 635, JS optimizer 142, PHP optimizer 139, C++ unit 173 and SQL unit 85, Lisp 553, SQL cases 889; `make asan` clean; the joined-row oracle 0 wrong on seven roster entries over 14 seeds × 3,000 programs; the join-filter oracle 0 cross-host disagreements on both corpora (from 14–51 on uniform shapes and 169–242 on mixed ones) and no crash; `tools/check.sh` ALL GREEN on seven roster entries (598 s) with the gate's Docker servers, the new joined-rows lane among its layers, 197 mutations caught.

Scenario 5 is recovered in every host by a join pre-filter that proves what it drops (2026-09-22; WL-001 SEL-0052).

  - **A FILTER over a LINK hands the join its conjuncts, and the join tests on its left rows those it can prove equivalent.** A conjunct is applied to the left rows when the joined row takes all its fields from them (no right side carries the field and the left input's first row does, spec §7.4); an earlier conjunct is passed over when it cannot raise on any joined row (its field on every row of the one side that has it, with the operator's kind); a row a conjunct raises on is kept, so the predicate as written raises where it would have. Below the FILTER's own join, rows are dropped only where no upper join key could raise on them and every relation has one row shape, since a join promotes by its first row. Python had a pre-filter since SEL-0049/0050; JS, PHP, C++ and Lisp now have the same, and Python's lost three ways of losing an error, found by a new differential oracle (`tools/join-filter-oracle/`) that compares each join-then-filter program with the same program through helper variables. Fifteen conformance cases pin the boundaries.
  - **Scenario 5, `0f9031d` against this tree:** C++ 647 → 277 ms, JS 675 → 276, PHP 3,129 → 1,165, Lisp 559 → 261, Python 897 → 786 — below the pre-SEL-0051 figures on four hosts; the other five scenarios within run-to-run spread on every host.
  - **Found on the way, not changed:** Lisp builds wrong joined rows, and sometimes crashes, over records of different shapes, and the hosts disagree on missing, null and boolean fields read through joined rows (SEL-0053); the physical join-predicate pushdown answers differently from the same program through helper variables on 2–3% of the oracle's programs, in every host alike (SEL-0054); the scale harness's database lane cannot run scenario 6 (SEL-0055).

Validation: 959 conformance cases per host; pytest 635, JS optimizer 142, PHP optimizer 139, C++ unit 173 and SQL unit 85, Lisp 553, SQL cases 889; `make asan` clean; the oracle over 15,000 program pairs with no host answering differently than at `0f9031d` except where it is now right; `tools/check.sh` ALL GREEN with the gate's Docker servers, 197 mutations caught; S1–S5's planned SQL executed on Docker PostgreSQL 17 and MariaDB 11.8 with the 10x fixtures, matching the reference rows every host's in-memory run matches.

The planner is pinned under every binding kind and over multi-line programs (2026-09-22; WL-001 SEL-0047; tests and docs only).

  - **Eight planner cases.** Every binding kind was pinned under `translate()` and none under `plan_hybrid()`: of 168 planner cases, none used a correlated relation, a `scalar`, a `prefilter`, a `columns` binding or a top-level `raw` binding, and none had a source of more than one line. `plan.bindings.*` now pins a correlated relation with a `scalar` pushed down whole (`orders`, `order_items`), the same in the prefix of a split plan, a `prefilter: separate` relation's two-`EXISTS` form, and `columns` and `raw` bindings in a body; `plan.multi-line.*` a three-line source split after its second line and a statement on its own line before the pipeline. The one decision written on the way, in §12.1: **a hybrid plan's `source_tables` are what its prefix reads** — a correlated relation read only by the continuation is not a physical source of the plan, since the continuation reads it from the caller's context at run time, as `run()` would. All five hosts already agreed on every case; the suite now says so.
  - **A continuation on line 3 reports its error on line 3**, from `run()` and from the executed plan alike: one row in each host's continuation table (JS and PHP optimizer checks, pytest, C++ SQL unit, FiveAM), where every row had been one line.

Validation: 889 SQL cases per host; JS optimizer 142 checks, PHP 139, pytest 635, C++ SQL unit 85, Lisp 553; `tools/check.sh` ALL GREEN with the gate's own Docker servers.

A refused plan carries no position, and the depth boundary of the planner is pinned (2026-09-22; WL-001 SEL-0046).

  - **`tools/gen-sql-cases.mjs` refuses a `--- plan refused` case that writes a position.** Every runner parsed `CODE line:col` there and compared the code alone, where its translate path compares both, so a wrong position on a refused plan passed on all five hosts. The position was never comparable: planning refuses only on the bindings or the dialect (a base dialect, an alias collision), which blame no node of the rule, and every host's `SqlError` then reports `0:0`. The generator now stops such a case at generation time, sql/cases/README.md states the rule, and each runner's refused branch says why it compares the code alone. Two planner cases pin what the planner does with a program stage 1 refuses for depth — a 200-term chain behind `A = 1;`, and the same predicate over a bound relation: `pure_memory` on every host, as `plan.pure-memory.non-normalisable-falls-back` already says of any stage 1 refusal, rather than the `E_SQL_DEPTH` translate() raises.

Validation: 881 SQL cases per host; generated artifacts, dialect map and case data current; SQL API 27 probes and the map replay agreeing on five hosts; 27 documented SQL examples right; with the gate's Docker servers, mutations 197 caught, 0 survived, 0 skipped; oracle 0 differing on every dialect; SQL fuzz 2,000 programs 0 disagreements host versus host and 0 differing against every server.

A conjunct pushed under a join is tested tentatively, and only behind conjuncts tested on the same side (2026-09-22; WL-001 SEL-0051).

  - **The physical join-predicate pushdown no longer changes which error a program raises.** Every host moved each one-sided conjunct of a `FILTER` after a `LINK` under the join on its own, whatever its place in the `AND`, so a conjunct the program's short-circuit would never have reached on a row was evaluated there: `… .> FILTER(_["orders"]["amount"] > 2 AND _["status"] $== "A")` over an order with status `"B"` and a text amount raised `E_NOT_NUM` in every host, where the same join assigned to a variable first answered rows. Spec §7.4 now states the rule — a `FILTER` after a `LINK` is evaluated as written; an early test may keep a row but never decide one an earlier conjunct might have raised on — and nine conformance cases pin it, two of which found that the tentative design as first sketched still dropped such rows. The rewrite now moves only the leading run of conjuncts naming one side, and it runs tentatively: a row the pushed body raises on is kept, and the `FILTER` above keeps its whole predicate, evaluating only the conjuncts that did not move when no row was kept on an error while its source ran (a counter on the context), or the whole predicate in source order when one was; with nothing left to test it returns the join's list as it is. The marks (`tentative`, `pushed_down`, `remaining`) are physical-tree metadata in every host, and FILTER fusion respects them.
  - **Cost, `b274aa2` against this tree, interleaved:** scenarios 1–4 and 6 within run-to-run spread on every host. **Scenario 5 is slower on JS (494 → 696 ms), PHP (2,208 → 3,133), C++ (524 → 635) and Lisp (396 → 527):** its predicate opens with two unqualified conjuncts, so the qualified one after them no longer moves under the joins; the speed those hosts had there came from the unsound rewrite. Python is 1,362 → 902 ms against `b274aa2`, since its run-time pre-filter proves side ownership from the data; the 698 ms of SEL-0050 leaned on the same push. Recovering S5 exactly on every host is SEL-0052.

Validation: 944 conformance cases per host on seven roster entries; differential fuzz 4,000 programs and SQL fuzz 2,000 programs, 0 disagreements; API 64 and SQL API 27 probes and the end-to-end scenarios agreeing; `tools/check.sh` ALL GREEN (49 layers) with the gate's own Docker servers: mutations 197 caught, 0 skipped; oracle and database-backed fuzz 0 differing on every dialect; hybrid checks 139 (JS) and 136 (PHP), pytest 634, C++ unit 173, Lisp unit 550; no compiler warning.

Python's join pre-filter reaches the base of a join chain (2026-09-22; WL-001 SEL-0050).

  - **Scenario 5 is 698 ms, from 1,382 at SEL-0049's close and 630 before it.** The conjuncts a FILTER offers its join now travel as stages, one per FILTER in the order the FILTERs run: a FILTER between two joins (the one the logical optimiser pushed) puts its whole predicate first, only when every conjunct is pre-evaluable so a row dropped below could not have raised in it, and hands the stages to its own join; each join applies them in order with the keep-on-error rule and cuts the list at the first conjunct that reads a field its right rows have. Drops below the join directly under the owning FILTER change that FILTER's keys, so the physical optimiser stamps each FILTER body with whether a following step renumbers without reading `_K` and deep drops happen only then. Three costs were taken out of the sound path: a join reports upward how many conjuncts every emitted row has passed (and whether any row was kept on an error, in which case the join above re-applies everything), a side's keys are gathered once per record shape, and a row carrying a literal join key is rejected before the key is computed, since that read could only raise for a row without the field. The other five scenarios are flat. The residual 11% against the old figure is the price of soundness: the old rewrite skipped the join key of every row it dropped and never looked at the right rows' keys. Three unit tests pin results, keys and error order; one of them found that the logical pushdown itself evaluates a pushed conjunct where the source program's `AND` would have short-circuited, in every host alike — SEL-0051, filed, not changed.

Validation: 634 Python tests, 935 conformance cases per host, differential fuzz 4,000 programs and SQL fuzz 0 disagreements, `tools/check.sh` ALL GREEN (49 layers, 569 s) with the gate's own Docker servers: mutations 197 caught, 0 skipped; oracle and database-backed fuzz 0 differing on every dialect; API lanes 64 and 27 probes agreeing; no compiler warning.

Python's physical tree is a function of the AST alone, and the API lanes probe the physical tree and the planner (2026-09-22; WL-001 SEL-0049, SEL-0044).

  - **`Program.physical_ast()` takes no context in Python**, and the tree is built once per AST as in the other four hosts. It used to be keyed on the context too, because Python's join-filter pushdown read the context's first row to decide which side of a LINK an unqualified field belonged to: a fresh context per run rebuilt the tree (the optimiser garbage SEL-0030 saw), the tree a program ran could differ by data, and the decision was unsound, since rows may differ. An unqualified field now names no side, as in JS, PHP, C++ and Lisp; docs/SQL-TRANSLATION.md §12.1 states the rule and a unit test pins it.
  - **The sound form of that optimisation runs in the evaluator.** A FILTER over a LINK hands the join its leading field conjuncts; the join pre-applies them to left rows only where the joined row's field is provably the left row's — the field is a key of no right row, checked over every row — keeps a row on any error so the full predicate raises where it would have, numbers the kept rows as the unfiltered join would, and carries the conjuncts down a chain of joins when both sources are pure. Scale scenarios S1–S4 and S6 are flat; **S5 is 630 → 1,382 ms**, because its conjuncts cannot pass the FILTER every host's logical optimiser pushes between its joins without changing which error surfaces; the 630 ms was the unsound rewrite's, and JS runs the same scenario in 529 ms. Recovering it soundly is SEL-0050.
  - **`tools/check-api.sh` probes the physical tree** (built once, the AST untouched, run agreeing after an explicit build, the same tree after runs over different data): 64 probes, seven roster entries agree. **`tools/check-sqlapi.sh` is new:** the planner's contract through each host's SQL binding — kind, dialect, statement, prefix and continuation presence, the continuation's dependencies, source variable, tables, selected member — over a pure-SQL, a hybrid and a pure-memory program: 27 probes, the five hosts with a SQL layer agree; a "host SQL API parity" step in the gate.

Validation: 633 Python tests, 935 conformance cases per host, differential fuzz 0 disagreements; `tools/check.sh` ALL GREEN on js, js-bundle, js-bundle-min, php, cpp, lisp and python (49 layers with the new step, 564 s) with the gate's own Docker servers: mutations 197 caught, 0 skipped; oracle and database-backed fuzz 0 differing on every dialect; no compiler warning. Benchmark, `6568201` baseline against this tree, interleaved: every host within run-to-run spread (Python Mandelbrot 354.2 / 348.3 → 349.6 / 346.0 ms, conformance 5005 → 5029 ms for ten more cases; C++ 146.7 / 150.5 → 145.8 / 147.3; JS 66.4 / 64.8 → 62.9 / 64.2; PHP 482.7 / 481.4 → 497.2 / 488.1; Lisp 94.0 / 81.0 → 78.0 / 78.0; startup flat).

The C++ SQL layer and planner run under the sanitizers (2026-09-22; WL-001 SEL-0045; build only).

  - **`cd cpp && make asan` runs four binaries, not two.** Beside the unit checks and the conformance suite it now builds and runs `sqlunit-asan` (executed plans, the shared physical tree) and `sqlt-asan` (stage 1, the translator and the planner over every committed SQL case) under the address, leak and undefined-behaviour sanitizers; until now a leak or an out-of-bounds in the translator was caught by nothing in the tree (review 2026-09-15, critic). The sanitized objects live under `build/asan/` and are compiled once, so the target is incremental. First run clean: 171 unit checks, 935 conformance cases, 85 SQL unit checks, 879 SQL cases. A deliberate heap overflow in the translator of a scratch worktree made `sqlt-asan` abort at the injected line, so the target is load-bearing. The optimised build is untouched: C++ Mandelbrot 148.6 / 147.9 → 146.3 / 148.8 ms against the `6568201` baseline, the SQL case suite 192 / 180 / 183 → 185 / 185 / 189 ms against the previous commit.

A nested index on a row has one rule in every host (2026-09-22; WL-001 SEL-0043).

  - **`_[k][field]` is a qualified read when `k` names a relation of the statement** — its binding name, its table, its alias, or a binder the join predicate declared — **and `E_SQL_SHAPE` at the outer index otherwise:** a position, a text `"1"`, a stray name, a field. JS, PHP and Python answered `E_SQL_BINDING` "unknown joined relation" for the second case, the accident of the alias lookup; C++ reported a bucket's projected row at the outer bracket where the other four refuse at the inner one; C++ accepted the positional `_`, `_1`, `_N` and Lisp `_1` as qualifiers, so `_["_2"]["NAME"]` after a join was pure SQL on one host and pure memory on the rest; and C++ rendered the qualified column bare in a single-relation statement where the others qualify it by the relation's alias. Thirteen `refuse.index.*` statement cases and six `plan.index.*` planner cases pin every variant, and `tools/gen-programs.mjs` emits the shapes so both fuzz lanes watch them.

Validation: 879 SQL cases per host; differential fuzz 4,000 programs 0 disagreements; SQL fuzz host-versus-host 0 disagreements on every dialect; hybrid checks 135 (JS, PHP), pytest 630, Lisp unit 532, C++ unit 171, SQL unit 85 and conformance 935. `tools/check.sh` ALL GREEN (48 layers, 556 s) with the gate's own Docker servers: mutations 197 caught, 0 skipped; oracle and database-backed fuzz 0 differing on every dialect; no compiler warning. Benchmark, `6568201` baseline against this tree, interleaved on an idle box: every host within run-to-run spread (C++ Mandelbrot 149.7 / 147.6 → 146.5 / 147.5 ms, JS 64.0 / 64.3 → 64.3 / 66.8, Python 365.1 / 354.3 → 355.0 / 353.7, PHP 481.0 / 485.6 → 484.3 / 485.5, Lisp 95.0 / 78.0 → 78.0 / 78.0; startup and conformance wall time flat), and each host's SQL case suite against the previous commit, 879 cases to its 856: JS 442 → 451 ms, PHP 845 → 861, Python 1,388 → 1,447 (the other round 1,629 → 1,419), C++ 191 → 189, Lisp 1,386 → 1,378.

Joined columns are qualified in Lisp, SELECT_COLS keeps a sorted statement whole, and the SQL fuzz lane runs in SQL mode (2026-09-22; WL-001 SEL-0042, SEL-0048).

  - **Lisp qualifies a joined row's fields by the alias their relation renders under.** A field binding may leave `table` out; on its own relation the column renders bare in every host, but once a join is present four hosts qualified it by the relation's alias and the Lisp host rendered the binding as written — `SELECT name AS name … ON (customer_id = id)` — different bytes, and an ambiguous-column error on a real engine when both sides share a name. One helper at the three places a joined statement renders a row's field; three `link.qualify.*` cases pin the shapes, including the unaliased ones where the left side renders under its table name and the joined side under `_2`.
  - **SELECT_COLS after a sort no longer wraps the plan.** JS, PHP, Python and C++ planned `CUSTOMERS .> SORT_BY(r, r["id"], "DESC") .> SELECT_COLS("name")` as a projection over a derived table carrying the ORDER BY — the shape MariaDB is free to return in table order, which the MAP arm's own rule already avoided. The four now apply the MAP rule (an ORDER BY alone does not wrap); Lisp already did. Pinned by a planner case.
  - **`tools/fuzz-sql.sh` passes `--sql` to the generator.** It never had, so every pipeline in its corpus read in-memory lists and was refused before the translator or the planner saw it; the lane's first run in SQL mode is what found the SELECT_COLS difference. The corpus's AMOUNT field now declares no table in every runner, so a join must qualify it.

Validation: 860 SQL cases per host; SQL fuzz host-versus-host 0 disagreements on every dialect in SQL mode; hybrid checks 135 (JS, PHP), pytest 630, Lisp unit 532, C++ SQL unit 85; benchmark, `6568201` baseline against this tree: every host within run-to-run spread (C++ Mandelbrot 151.3 / 149.3 → 146.5 / 151.0 ms, JS 65.8 / 66.8 → 64.1 / 63.5, Python 371.9 / 350.3 → 358.1 / 371.7, PHP 489.2 / 481.0 → 491.2 / 488.6, Lisp 93.0 / 80.0 → 78.0 / 80.0; startup and conformance wall time flat), and the Lisp SQL case suite 1,361 / 1,369 / 1,370 ms on the previous commit against 1,371 / 1,373 / 1,379 ms here with three more cases. Closing gate: `tools/check.sh` ALL GREEN on js, js-bundle, js-bundle-min, php, cpp, lisp and python (48 layers, 559 s) with the gate's own Docker servers: mutations 197 caught, 0 skipped; oracle and database-backed fuzz 0 differing on every dialect; differential fuzz 0 disagreements; no compiler warning.

Historical review reconciled, four small alignments, six items opened (2026-09-22; WL-001 SEL-0037).

  - **The 2026-09-15 review's unverified lists are reconciled** in a dated section of its index: 13 low-severity reports and 14 critic gaps, each closed with evidence in the tree, retained with a reason, or carried by a new worklist ID. Two reports reproduced and are now SEL-0042 (Lisp leaves join columns unqualified when fields declare no table) and SEL-0043 (a positional index on a relation row is `E_SQL_BINDING` in three hosts and `E_SQL_SHAPE` in two, with a C++ position of its own inside a bucket); SEL-0044–0047 carry the API-probe, sanitizer, refused-plan-position and planner-coverage gaps.
  - **Aligned in the same pass, each covered by an existing suite:** the Python unit lane of `tools/check.sh` fails rather than skips when pytest is missing (`SEL_SKIP_PYTHON_UNIT=1` opts out), since that lane holds checks no other host has; the C++ optimiser leaves an assignment's target as written, as the other four do; Lisp's `execute-hybrid` copies the caller's context as the other four clone it; `bucket.group-by-is-not-a-function` pins the retired verb as `E_UNKNOWN_FUNC` at the name.
  - **Every C++ translation unit compiles clean.** The four `-Wswitch` / `-Wmissing-field-initializers` warnings in `sel_sql_translator.cpp` (a `Key` shape unnamed in a switch that fell through to the scalar rule, three projections built without their `group_key`) are fixed with no behaviour change.

Closing validation for SEL-0037, SEL-0039 and SEL-0040 together: 935 conformance cases and 856 SQL cases per host; `tools/check.sh` ALL GREEN on js, js-bundle, js-bundle-min, php, cpp, lisp and python (48 layers, 560 s) with the gate's own Docker servers and the native PHP client: mutation catalogue 197 caught, 0 survived, 0 skipped; semantic oracle 0 differing on every dialect; database-backed SQL fuzz 0 differing on mariadb, mysql, postgresql, sqlite and the ANSI probe; differential fuzz 0 disagreements; no compiler warning in the log. Benchmark, `6568201` baseline against this tree, interleaved on an idle box:

| Host | Mandelbrot baseline → current | Startup, per process | Conformance wall time |
|---|---|---|---|
| C++ | 148.7 / 149.1 ms → 148.1 / 148.1 ms | 2 → 2 ms | 126 → 131 ms |
| JS | 65.0 / 65.0 ms → 64.9 / 65.4 ms | 68 → 68 ms | 2100 → 2115 ms |
| Python | 357.8 / 351.0 ms → 351.8 / 346.2 ms | 58 → 59 ms | 5056 → 5064 ms |
| PHP | 490.8 / 488.0 ms → 489.6 / 488.0 ms | 68 → 69 ms | 2499 → 2522 ms |
| Lisp | 93.0 / 80.0 ms → 77.0 / 78.0 ms | 655 → 657 ms | 30622 → 30659 ms |

Within run-to-run spread on every host; the current tree runs 935 conformance cases to the baseline's 925.

Each parser's arity check and the translators' repeated arms are shared (2026-09-22; WL-001 SEL-0039, SEL-0040; no behaviour change).

  - **One body per lane for four translator repeats.** The sargable-prefilter arm's mirror condition; `record_fields` for the `(name, value)` pairs of a RECORD call, read by the bucket projection, the bucket key and the MAP projection (fifteen loops gone); one prologue per host for `translate` and `translate_statement` — which found JS's `translate` not resetting the subquery counter, latent because every public entry builds a fresh translator; and Lisp's `check-collation` for both binding constructors, as Python already had.
  - **PHP: `Value::forEachElement` and `Optimizer::forEachChild`.** The two identical private element walks in the aggregate classes are one public method on `Value`, eleven call sites; the optimizer's two child-visit loops read one helper keyed by the node-shape constants. Scale scenarios against the previous commit within 1.6% on all six, no direction.

Validation for both: 856 SQL cases on all five hosts, SQL fuzz host-versus-host 0 disagreements on every dialect, every mutation anchor intact (197), PHP conformance 935 / optimizer 135 / runtime 35, Lisp unit 532, JS and PHP hybrid checks 135 each, pytest 630; the closing gate, database lanes and five-host benchmark are recorded with SEL-0037 below.

The C++ build is warning-free again (2026-09-21).

  - **`unsigned __int128` is spelled `__uint128_t` throughout `cpp/sel.cpp`.** GCC's `-Wpedantic`, which the Makefile asks for, flags the keyword spelling of the 128-bit extension at every use and not the typedef, so the decimal core's 128-bit fast paths printed 29 identical warnings on every rebuild of the core and of the unit tests (which include it). The file already used the typedef in 58 places; the 22 keyword uses now match. Same type, no behaviour change: sanitizer build, decimal oracle and the full gate unchanged.

The gate provides its own databases (2026-09-21).

  - **`tools/check.sh` starts the pinned Docker servers unless told otherwise, and fails if it cannot.** The semantic oracle, the seven live entries of the mutation catalogue and the oracle half of the SQL fuzz lane all print a skip and succeed without a DSN, so a plain run was ALL GREEN having asked nobody — "190 caught, 7 skipped" in the summary line and nothing louder. The gate now re-runs itself under `tools/oracle-db.sh run`, which starts mariadb 11.8, mysql 8.4 and postgres 17, exports the DSNs and removes the servers when the run ends; `SEL_SKIP_DB_TESTS=1` opts out, and any `SEL_SQL_<DIALECT>_DSN` in the environment means "use these, start nothing". The three database layers run one at a time under a lock, since they share one schema and each recreates its tables. `tools/oracle-db.sh` checks up front that the PHP client on PATH has `pdo_mysql`, `pdo_pgsql` and `pdo_sqlite`, and every refusal names the opt-out.

A filtered PHP case run no longer fails on the whole-suite pin check (2026-09-21).

  - **`php/bin/sqlt <filter>` asserts caveat pins only on a full run.** The pins are gathered from the cases that ran, so a filtered run knew only its own and reported every other caveated entry as unpinned: 59 `UNPINNED` lines and exit 1 around a passing case, saying nothing about the cases selected. The check now runs when every case ran and prints a one-line note otherwise; the mirror-parity check reads the shipped map and still runs either way. The full run is unchanged (856 passed, 0 suite errors). `CLAUDE.md`'s example filter named no case; it now names `agg.static`.

Each parser checks call arity once (2026-09-21; WL-001 SEL-0038; no behaviour change).

  - **One `finish_call` per host.** The plain call and the `.>` pipeline call shared, line for line, the min/max check, the extra arity rule and the construction of the call node — about 12 lines twice in every host. Both forms now end in one helper (`_finish_call`, `finishCall`, `Parser::finish_call`, `finish-call`), so the compile-time `E_ARITY` rule has one body per lane; positions are unchanged, every refusal still reporting the name token.
  - **The manifest gate probes the pipeline form too.** `tools/check-manifest.sh` used to probe accepted counts through the plain form only; it now also asks `1 .> NAME(1, …)` for every count of one or more, 693 count probes per host from 381, so the second parse path is pinned per builtin rather than by two hand-written cases.

Validation: 934 conformance cases per host (`11-arity.selt` and `14-pipeline.selt` 158/158 on all five); `tools/check.sh` ALL GREEN on js, js-bundle, js-bundle-min, php, cpp, lisp and python (48 layers); against pinned Docker servers: semantic oracle 0 differing on every dialect, mutation catalogue 197 caught, 0 survived, 0 skipped.

The SQL translator charges the levels stage 1 removes (2026-09-21; WL-001 SEL-0034, SEL-0041).

  - **Stage 1 counts depth from where the evaluator's count stands.** The `;` sequence costs a level and each assignment it inlines one more (spec §6.4), and stage 1 removed both before either `E_SQL_DEPTH` guard looked, so `X = <199 terms>; X` — `E_DEPTH` in the evaluator — translated, and a database answered a rule SEL has no answer for. The database-backed fuzz lane (`tools/fuzz-sql.sh 2000 20260905`) had reported five such programs per dialect since 2026-09-16. The substitution walk in every host now starts at that depth; a flat chain is untouched, and only programs the evaluator already rejects gain a refusal, at the same position. Four cases in `sql/cases/18-host-neutrality.sqlt` and five mutations (one per host) pin it; `sql/errors.md` states the rule.
  - **The oracle's SQLite is pinned by a floor.** SQLite has no server, so the `sqlite` target is the PHP client's libsqlite3, and 3.46.1 truncates a `substr()` length past 2^31 (`substr('Zażółć', 2, 4294967299)` is `ażó`; correct from 3.48.0). `tools/oracle-db.sh` now asks the client for its library version and refuses below 3.48.0; `tools/oracle-php-client.Dockerfile` is the client the repository's database runs use (Alpine, libsqlite3 3.53.4); `sql/oracle/README.md` documents the floor. The map is unchanged.

Validation: 856 SQL cases per host; `tools/check.sh` ALL GREEN on js, js-bundle, js-bundle-min, php, cpp, lisp and python (48 layers, 1,082 s); against pinned Docker servers through the rebuilt client: semantic oracle 0 differing on every dialect, mutation catalogue 197 caught, 0 survived, 0 skipped, and the database-backed SQL fuzz lane green for the first time — 0 differing on mariadb, mysql, postgresql, sqlite and the ANSI probe, every one of the 744 programs the evaluator rejects also refused by the translator.

Full five-lane gate recorded on a committed checkpoint (2026-09-21; WL-001 SEL-0033; no code change).

  - **Revision `371f090`, clean tree.** `tools/check.sh` ALL GREEN on js, js-bundle, js-bundle-min, php, cpp, lisp and python (48 layers, 1,108 s; fuzz seeds 20260813 and 20260905, 0 disagreements); `cd cpp && make asan` 171/171 unit and 934/934 conformance with no sanitizer report; the database layers against pinned Docker servers (mariadb 11.8, mysql 8.4, postgres 17, sqlite): semantic oracle 0 differing on every dialect, mutation catalogue 192 caught, 0 survived, 0 skipped. Benchmark against the `6568201` baseline, interleaved on an idle box:

| Host | Mandelbrot baseline → current | Startup, per process | Conformance wall time |
|---|---|---|---|
| C++ | 148.7 / 148.6 ms → 153.2 / 146.4 ms | 2 → 2 ms | 124 → 133 ms |
| JS | 66.2 / 65.0 ms → 65.0 / 65.4 ms | 69 → 70 ms | 2097 → 2097 ms |
| Python | 357.8 / 350.1 ms → 354.7 / 352.1 ms | 59 → 59 ms | 5047 → 5064 ms |
| PHP | 483.0 / 483.6 ms → 490.3 / 493.2 ms | 65 → 67 ms | 2480 → 2495 ms |
| Lisp | 95.0 / 81.0 ms → 79.0 / 78.0 ms | 652 → 651 ms | 30464 → 30529 ms |

Within run-to-run spread on every host (the current tree runs 934 conformance cases to the baseline's 925). Earlier entries below counted the gate runner's banner as a layer and say 49; the layer count is 48.

Python pauses the cyclic collector for a run (2026-09-21; WL-001 SEL-0030).

  - **`Program.run` runs with CPython's cyclic collector paused** (`python/sel/_gc.py`), handed back exactly as found: re-entrant, exception-safe, a no-op if the application had it off. A profile through the collector's own callbacks showed 27–40% of the join-heavy scenarios' time was collector wall time that found nothing — SEL values are trees, a run creates no cycles, and reference counting frees its garbage regardless — with each full pass walking the whole resident context. Measured on the 137,100-row fixture: S1 2,860 → 1,974 ms, S3 1,447 → 911, S5 717 → 624, S6 1,385 → 775 (isolated 1,265 → 786); S2, S4 and Mandelbrot unchanged; sample spread collapses from ±25% to ±3%. This also retires the S6 regression that SEL-0003's spec-required right-side aliasing had introduced, which stays as the other hosts have it. Six tests pin the collector's restoration and the no-cycles claim; the contributor guide records the rule a new builtin must keep.

Validation: 630 Python tests, 934 conformance and 852 SQL cases; `tools/check.sh`
ALL GREEN on js, js-bundle, js-bundle-min, php, cpp, lisp and python (49
layers); the database layers against pinned Docker servers (mariadb 11.8,
mysql 8.4, postgres 17, sqlite): semantic oracle 0 differing on every dialect,
mutation catalogue 192 caught, 0 survived, 0 skipped. Closing benchmark,
`6568201` baseline against this tree on an idle box, interleaved — the pause
is Python's alone, and the other hosts are the control:

| Host | Mandelbrot baseline → current | Startup, per process | Conformance wall time |
|---|---|---|---|
| C++ | 146.7 / 146.9 ms → 145.9 / 144.8 ms | 2 → 2 ms | 126 → 130 ms |
| JS | 63.6 / 64.0 ms → 66.3 / 66.3 ms | 66 → 68 ms | 2060 → 2080 ms |
| Python | 352.8 / 351.8 ms → 350.0 / 352.3 ms | 57 → 58 ms | 5011 → 5009 ms |
| PHP | 478.2 / 476.8 ms → 483.5 / 481.1 ms | 65 → 66 ms | 2471 → 2480 ms |
| Lisp | 93.0 / 78.0 ms → 78.0 / 78.0 ms | 649 → 642 ms | 30257 → 30264 ms |

Within run-to-run spread on every host (the current tree runs 934 conformance
cases to the baseline's 925); Python's Mandelbrot, the arithmetic-bound case
that allocates no rows, is unchanged by the pause, as expected.

Performance investigations measured, one regression found (2026-09-21; WL-001 SEL-0027–0032; no runtime change).

  - **Five tradeoffs accepted with current numbers.** C++: a gprof profile shows the remaining S4 cost is the harness's context clone, teardown and result hashing, not the query. Lisp: isolated S6 sits at the recorded baseline (184–189 ms) and the full-batch mean gap is GC placement; cold paths are back at baseline latency since the per-shape alias table went. Python: the metadata churn profile reproduces and the clear-all policy stands. PHP: the fixed-order and isolated S4/S6 figures reproduce the September 20 report to the millisecond, and boolean SORT is 26% faster now that SORT shares TOP's comparator.
  - **A Python S6 regression, cause established, decision pending (SEL-0030).** The right-side aliasing that SEL-0003 added for spec conformance costs Python's S6 left join 712 → 1,265 ms median: 92,002 aliased right rows add two container objects each, which doubles the cyclic collector's full collections over the resident 137,100-row dataset (+27% with the collector off, +75% with it on). No low-risk fix exists; the options are recorded in the worklist for the owner to choose.

Validation: no runtime code changed under these items, so the closing check is
the same five-host benchmark as the two entries below, `6568201` baseline
against the final tree on an idle box, interleaved:

| Host | Mandelbrot baseline → current | Startup, per process | Conformance wall time |
|---|---|---|---|
| C++ | 146.4 / 145.5 ms → 145.2 / 146.9 ms | 2 → 2 ms | 124 → 128 ms |
| JS | 64.9 / 63.1 ms → 64.2 / 63.2 ms | 67 → 70 ms | 2076 → 2084 ms |
| Python | 350.6 / 349.9 ms → 348.9 / 348.2 ms | 57 → 59 ms | 4997 → 4999 ms |
| PHP | 480.0 / 477.7 ms → 480.6 / 482.4 ms | 66 → 67 ms | 2469 → 2475 ms |
| Lisp | 92.0 / 78.0 ms → 82.0 / 78.0 ms | 649 → 647 ms | 30292 → 30294 ms |

Within run-to-run spread on every host (the current tree runs 934 conformance
cases to the baseline's 925). The Python S6 cost above is the one exception,
and it is SEL-0003's, reported for decision rather than hidden in this table.

The SQL map's arity authority is the manifest, and the manifest is checked against behaviour (2026-09-21; WL-001 SEL-0025, SEL-0026).

  - **`tools/gen-sql-map.mjs` no longer loads the JS host.** An entry's `arity` is checked against `spec/builtins.json`, including the accepted-count rules; the renderings are byte-identical, and no implementation is the map's authority any more.
  - **`tools/check-manifest.sh` holds every host to the manifest's meaning, not its table.** It calls every builtin with every count around its range (381 probes: compiles, or `E_ARITY` at the call) and every binding form with a distinct name in each slot plus a guard-defeating variant (45 probes, through `dependencies()`), all predicted from the manifest alone. Seven roster entries agree; a doctored manifest fails it. Together with the freshness groups for all three manifests and the error-code gate, generated facts can be neither stale, hand-edited, nor silently disobeyed.

Validation: `tools/check.sh` ALL GREEN on js, js-bundle, js-bundle-min, php,
cpp, lisp and python — 49 layers now, "manifest semantics" and "error codes"
among them.
The database layers against pinned Docker servers (mariadb 11.8, mysql 8.4,
postgres 17, sqlite): semantic oracle 0 differing on every dialect, mutation
catalogue 192 caught, 0 survived, 0 skipped.

Performance, the `6568201` baseline against this tree, same protocol as the
SEL-0023 entry (idle box, interleaved; the runtime is unchanged by these two
items, the tooling is what changed):

| Host | Mandelbrot baseline → current | Startup, per process | Conformance wall time |
|---|---|---|---|
| C++ | 147.0 / 146.4 ms → 145.2 / 147.5 ms | 2 → 2 ms | 125 → 129 ms |
| JS | 63.5 / 63.8 ms → 64.2 / 65.5 ms | 66 → 69 ms | 2080 → 2085 ms |
| Python | 355.1 / 345.7 ms → 345.8 / 347.0 ms | 58 → 58 ms | 4988 → 4988 ms |
| PHP | 485.8 / 475.8 ms → 480.1 / 477.7 ms | 65 → 66 ms | 2460 → 2469 ms |
| Lisp | 91.0 / 77.0 ms → 77.0 / 78.0 ms | 646 → 645 ms | 30242 → 30241 ms |

Within run-to-run spread on every host; no regression to reject.

Limits and error codes are stated once and held to the spec (2026-09-21; WL-001 SEL-0023).

  - **`spec/limits.json` restates the four normative numbers and the 25 error codes**, and its generator refuses to render unless `spec/SPEC.md` and `spec/errors.md` still say the same — the prose stays the authority. Each host's `MAX_DEPTH`, decimal digit caps and division scale are now defined from its rendering rather than as literals; `docs/LIMITS.md` lists them.
  - **A new gate holds every host to the catalogue.** `tools/check-error-codes.sh` reads each host's sources and requires the codes it raises to be exactly the language catalogue plus the SQL layer's own: all five raise exactly the 33. Host budgets stay out of the manifest, and the limit fixtures keep their literal numbers as independent oracles.

Validation, for this entry and the two below it: `tools/check.sh` ALL GREEN on
js, js-bundle, js-bundle-min, php, cpp, lisp and python (48 layers, the new
error-code gate among them), and the database layers against pinned Docker
servers (mariadb 11.8, mysql 8.4, postgres 17, sqlite): semantic oracle 0
differing on every dialect, mutation catalogue 192 caught, 0 survived, 0
skipped. A first gate run found the decimal-oracle tool loading `SelError.php`
without the bootstrap; the two PHP files that use the limits now require them
themselves.

Performance, the `6568201` baseline (a separate worktree) against this tree on
an idle box, interleaved, medians of 7 Mandelbrot runs, best of 3 batches of
10 startups, best of 2 conformance runs (the current tree runs 934 cases to
the baseline's 925):

| Host | Mandelbrot baseline → current | Startup, per process | Conformance wall time |
|---|---|---|---|
| C++ | 145.3 / 147.6 ms → 147.7 / 145.3 ms | 2 → 2 ms | 124 → 126 ms |
| JS | 63.9 / 64.8 ms → 63.8 / 67.0 ms | 68 → 68 ms | 2085 → 2067 ms |
| Python | 354.8 / 346.1 ms → 345.2 / 349.4 ms | 58 → 58 ms | 4983 → 5019 ms |
| PHP | 483.3 / 478.7 ms → 487.4 / 484.9 ms | 65 → 66 ms | 2469 → 2475 ms |
| Lisp | 91.0 / 80.0 ms → 78.0 / 78.0 ms | 643 → 644 ms | 30225 → 30226 ms |

Every difference is inside the run-to-run spread of its own column (the two
Mandelbrot rounds of one tree differ by more than the trees do); the
manifest checks at startup cost nothing measurable, and compile-heavy work
is flat. No regression to reject.

The math plans share one vocabulary (2026-09-21; WL-001 SEL-0022).

  - **`spec/math-ops.json` names the 15 operations the native math plans compile** — five binary operators, the `NEG` prefix and nine builtins — with each one's source token, operand count (or left fold for `MIN`/`MAX`) and the argument that carries its auxiliary error position (`ROUND`'s scale, `POWER`'s exponent). The generator cross-checks every builtin's arity against `spec/builtins.json` and renders a table per host and `docs/MATH-OPS.md`; `tools/check-generated.sh` keeps them current.
  - **Each host's compiler classifies through its table and keeps its own opcodes.** The hand-written operator switches and the three builtin arms per host are one table-driven arm; Python and JS keep their numbered opcodes, PHP its constants, C++ its enum, Lisp its keywords, and a host refuses to load if the manifest names an operation its executor lacks. Executors, scratchpads and the copy-propagation rules are untouched.

Validation: 934 conformance cases on all five hosts, decimal oracle 199,942
cases with 0 mismatches on all five, JS/PHP optimizer checks, and the database
layers against pinned Docker servers (mariadb 11.8, mysql 8.4, postgres 17,
sqlite): semantic oracle 0 differing on every dialect, mutation catalogue 192
caught, 0 survived, 0 skipped. The full gate for this entry is the one recorded
under SEL-0023 below, which ran on the tree carrying both.

Binding forms are declared once, and `dependencies()` agrees everywhere (2026-09-21; WL-001 SEL-0021).

  - **`spec/builtins.json` now carries the binding forms** of the 14 binding builtins: for each accepted argument count, in the evaluator's order, which argument is the source, a binder name, a body run per element, or an outer expression (TOP's limit, SORT_BY's direction), the guard that picks between two forms of one count, and the names bound inside. Rendered into every host's table and into a "Binding forms" table in `docs/BUILTINS.md`; each host exposes one classifier over it.
  - **The dependency walkers and the SQL layer's stage 1 classify through it**, replacing five hand-typed copies of the SORT/TOP/BUCKET/LINK arms. This fixes a live divergence: `dependencies()` disagreed across hosts on TOP's binder and limit, LINK's named binders and TOP_BY's direction (nine of thirteen probed forms), and every host hid `K` in `SORT_BY(L, K, "DESC")`, which the evaluator reads as the key. Stage 1 had also inlined a same-named helper into LINK's binder slots and substituted TOP's limit inside the binder. Six new `program.deps.forms.*` API probes and nine `agg.forms.*` conformance cases pin the evaluator's scoping and both guard orders.

Validation: `tools/check.sh` ALL GREEN on js, js-bundle, js-bundle-min, php,
cpp, lisp and python (47 layers; 60 API probes agree), and the database
layers against pinned Docker servers (mariadb 11.8, mysql 8.4, postgres 17,
sqlite): semantic oracle 0 differing on every dialect, mutation catalogue 192
caught, 0 survived, 0 skipped.

The builtin table is authored once (2026-09-21; WL-001 SEL-0020).

  - **`spec/builtins.json` is the manifest of the 77 builtins** — name, min/max, the extra arity rules (`COND` odd, `RECORD` even, `LINK`/`LINK_LEFT` three or five) with their messages, lazy/binds, spec forms and section; `spec/builtins.md` is the format. `tools/gen-builtins.mjs` validates and renders it into a native table per host and into `docs/BUILTINS.md`, all committed and held current by `tools/check-generated.sh`.
  - **Every host holds its own table to the manifest at startup**, natively and without reading JSON: `define` refuses a shipped builtin whose min/max/lazy/binds disagree, takes the extra arity rule from the manifest (the four hand-written copies per host are gone), and registration refuses to finish with a manifest name no module defined. A function outside the manifest — `register`, the worked examples, an application's own — passes through as before.
  - **`DEDUPE` is in the spec.** Seeding the manifest found it defined and tested in every host but missing from §7.4; it is now listed as the relational spelling of `DISTINCT`.

Validation: `tools/check.sh` ALL GREEN on js, js-bundle, js-bundle-min, php,
cpp, lisp and python (47 layers, the builtin-manifest renderings now among the
generated artifacts it checks), and the database layers against pinned Docker
servers (mariadb 11.8, mysql 8.4, postgres 17, sqlite): semantic oracle 0
differing on every dialect, mutation catalogue 192 caught, 0 survived, 0 skipped.

Raw scan inventory triaged (2026-09-20; WL-001 SEL-0019).

  - **Every raw scan entry now has a disposition** in `docs/worklists/001-scan-triage.md`: 32 clone windows, 25 single-reference declarations and 25 import candidates, each checked against the current tree. Thirteen windows were already closed by earlier items or belong to the binding-forms work; seven are kept on purpose (deliberate exception-type boundaries, per-lane fast paths); twelve are tracked as three new items — the parser's twice-written arity check (all five hosts), the SQL translators' repeated arms (sargable prefilter in all five, RECORD projection and entry-point reset in three, a Lisp collation check), and PHP's duplicated element iterator and optimizer child visits. Every flagged declaration is either already removed or a live surface: mirrored host API, a caller in tools, examples or generated case data, or a Python protocol method. No runtime code changed.

Validation: unchanged runtime, re-run in full as the rule requires —
`tools/check.sh` ALL GREEN on js, js-bundle, js-bundle-min, php, cpp, lisp and
python (47 layers), and the database layers against pinned Docker servers
(mariadb 11.8, mysql 8.4, postgres 17, sqlite): semantic oracle 0 differing on
every dialect, mutation catalogue 192 caught, 0 survived, 0 skipped.

Duplication assessed and settled with measurements (2026-09-20; WL-001 SEL-0013–0018).

  - **C++: one checked alignment for the native fast path.** `dec_add` and `dec_cmp` carried the same `__int128` scale-alignment block; `align_small` now owns it, with the limb fallback, signed bounds and overflow builtins unchanged. Decimal oracle 199,942 cases agree; Mandelbrot is unchanged within noise. The `const Dec&` overload of `dec_guard` is gone — deleting it and rebuilding proved every call site passes an rvalue.
  - **JS: one entry-splitting helper.** `fromEntries` and `fromEntriesPreserveDuplicates` share `shapedFromUniqueEntries`; the overwrite-versus-ordered-duplicates fallback stays with each caller. The call boundary measured free.
  - **PHP: the second `getScalar()` read in the text join-key branch is gone**; the first call already formats a cached decimal.
  - **Kept on purpose, with numbers.** Python's `iter_entries`/`iter_elements` stay separate: sharing them through `yield from` costs 4.8% on packed lists and 11.9% on shaped records in the aggregate loops. The Lisp hybrid/stage-1 copiers stay separate: they encode different refusal, node-kind and depth contracts, and the walkers already share `walk-node-children`.

Validation: the full repository gate, `tools/check.sh`, ALL GREEN on js,
js-bundle, js-bundle-min, php, cpp, lisp and python (47 layers) on the working
tree carrying SEL-0001–0018, and the database layers against pinned Docker
servers (mariadb 11.8, mysql 8.4, postgres 17, sqlite): semantic oracle 0
differing expressions, rows or statements on every dialect; mutation
catalogue 192 caught, 0 survived, 0 skipped. `make asan` was not part of the run.

Dead per-shape alias caches and unused helpers removed (2026-09-20; WL-001 SEL-0005–0012).

  - **Record shapes no longer carry an alias cache.** Alias plans have lived in one bounded per-host cache since the metadata work; the per-shape container each host still allocated was never read or written. Python drops the `alias_cache` slot, Lisp the `alias-cache` hash-table slot, and PHP the public `RecordShape::$aliasCache` array — measured at 16 bytes per shape on PHP 8.5 (1078.7 to 1062.7 bytes over 20,000 shapes), so a reader of that property now sees PHP's undefined-property warning. JS keeps `RecordShape.aliasCache` in `sel.d.ts` as `@deprecated`: it is served lazily from a module `WeakMap`, so a shape owns only `keys`, `keyMap` and `size`; it goes away in the next minor release.
  - **Unused code out.** Python: seven unused imports across five modules and `utf8.bytes_equal` (no caller; `Value` compares bytes directly). Lisp: `collect-all-step-field-references` in the hybrid planner. C++: `cp_length`, `mul_abs` and the `for_each_collection_item` template. Each had exactly one occurrence in the tree, its definition, checked against `tools/`, tests, headers and package exports.

Validation: the full repository gate, `tools/check.sh`, ALL GREEN on
js, js-bundle, js-bundle-min, php, cpp, lisp and python (47 layers, including
925 conformance cases per host, the SQL cases, differential and SQL fuzz, the
SQL mutation catalogue, decimal oracle, documented and worked examples,
metadata and generated-artifact checks) on the working tree carrying
SEL-0001–0012. The database layers then ran against pinned Docker servers
(`tools/oracle-db.sh`: mariadb 11.8, mysql 8.4, postgres 17, plus sqlite):
the semantic oracle reports 0 differing expressions, rows or statements on
every dialect, and the mutation catalogue is 192 caught, 0 survived, 0 skipped.
`make asan` was not part of the run.

Join keys, join-row shape and LINK arity agree in every host (2026-09-20; WL-001 SEL-0001–0004).

  - **One numeric join key per number.** An equi-join on `==` pairs elements as `==` compares them (spec §7.4). PHP built the key from the cached decimal on one path and from `Dec::format` of a freshly parsed text on the other, so a fresh process joined `1.00` to `"1.00"` only after some other comparison had parsed the text; JS and Lisp keyed on the formatted decimal, so `1.50` never met `"1.5"`. All three now strip trailing fraction zeros and fold negative zero through one helper, as C++ and Python did. The PHP runtime check runs the cold/warm/both-orders probe; `rel.link.numeric-key-matches-as-equality-does` and `rel.link.numeric-key-repeated-run-agrees` pin it suite-wide.
  - **`LINK`/`LINK_LEFT` refuse four arguments at compile time everywhere.** Only Python declared the "3 or 5" rule through the registry's extra arity hook; the other four checked it inside the builtin, so `IF(TRUE, 1, LINK(1, 1, 1, 1))` answered `1` there and `E_ARITY` in Python. Eight `arity.link*` cases, two of them never-reached calls that pin the compile-time position.
  - **The right binders hold the element extended with its name.** Python bound the bare right element (`Y`/`y`/`_2` with two keys where the other hosts had four), the eleven disagreements in the saved seed-20260813 differential corpus; `rel.link.row-shape-named-right` and its five-argument twin cover it. In the other direction, JS, PHP, C++ and Lisp gave an unnamed right argument a `_2` key holding itself; `_1`/`_2` are positions, not names, and are never added (`rel.link.literal-binders-are-bare`, `rel.link-left.literal-right-null-row-is-bare`; spec §7.4 now says so).
  - **PHP `TOP` orders with `SORT`'s comparator.** `Structure::doTop` had a private copy of `Core::compareValues` that had drifted (explicit boolean getters on one side only); the copy is gone and the one routine uses the getter. Bounded selection and the sort itself stay separate.

Validation: 925 conformance cases on all five hosts; the seed-20260813 4,000-program
corpus that carried the eleven disagreements and a second seed both agree across
five hosts with zero host crashes; C++ unit, Lisp FiveAM and 624 Python tests;
PHP runtime (35 checks) and PHP/JS optimizer (135 each) checks. Scoped run —
the full repository gate was not rerun.

PHP explicit scalar reads and matched-extension measurements (2026-09-20).

  - **Use the existing getter in four internal reads.** Numeric/literal join keys and boolean sorting call `getScalar()` directly, avoiding `__get` dispatch while preserving private storage, lazy decimal formatting, the compatible public property API and decimal-cache invalidation on writes. Native integer, GMP and packed-limb arithmetic paths remain unchanged.
  - **Measure the local gain without claiming a regression fix.** Repeated GMP probes improve numeric-text joins 4–14%, literal joins 5–6% and boolean sorting about 2%. Full application shifts are small and mixed; S6 changes from 0.3% faster to 0.8% slower across full batches, and is nearly flat in the separate repeat. Matched ctype/OPcache/JIT configurations with and without GMP pass all six scenarios and Mandelbrot. Mandelbrot remains about 0.39 s with GMP and 1.36 s without it. [Results, limitations and reproduction](docs/interim/php-runtime-improvements.md).
  - **Record a pre-existing cold-key defect.** Mixed cached-decimal/text numeric join keys can miss before normalization caches warm. Baseline and candidate reproduce it identically; the report preserves the reproducer and recommends shared decimal-key normalization as a separate correctness fix.

Validation: 43 layout/API, 21 runtime and 135 optimizer checks, 911 conformance
cases, metadata checks and 39,990 decimal-oracle cases pass with and without
GMP. All 852 SQL cases pass with GMP; 4,000 differential programs and 54 API
probes agree across PHP/C++/JS. All 312 scenario samples and 66 Mandelbrot frames
pass. The full five-language repository gate was not rerun.


Python native arithmetic and heterogeneous metadata measurements (2026-09-20).

  - **Remove redundant decimal work.** Subtraction handles aligned magnitudes/signs directly, avoiding a temporary negated decimal. Division cancels common scales before constructing powers/products; exact integer arithmetic, rounding, zero normalization and range checks are preserved. Native UTF-8 and bounded metadata ownership remain unchanged.
  - **Measure gains and small tradeoffs.** Subtraction probes improve 38–44%, small equal-scale division 14–16%, and shared-scale-10,000 division about 86%. Repeated S1/Mandelbrot medians improve 1.7–4.1%/1.9–2.4%. Full-batch S4/S6 medians worsen 3–4%/about 1%; a separate repeat reverses S4 but retains a roughly 1.4% S6 cost. Neither prepared query calls the changed functions; the small S6 cost remains unresolved. [Results and reproduction](docs/interim/python-runtime-improvements.md).
  - **Expose churn without removing bounds.** Cycles exceeding 64 powers, the weighted power budget, or 256 shapes miss on every lookup. New probes retain raw latency, activity and memory results alongside the six scenarios and Mandelbrot. Cache activity/retention is identical before and after; Mandelbrot makes 14.5% fewer power calls with unchanged misses.

Validation: 624 Python unit tests, 911 conformance cases, 852 SQL cases,
metadata checks and 39,990 decimal-oracle cases pass; 54 API probes agree across
Python/C++/JS. All 228 scenario samples and 44 Mandelbrot frames pass parity.
The 4,000-program fuzzer has 11 pre-existing join-output disagreements and no
host crashes; complete Python baseline/candidate outputs are identical. The
full five-language repository gate was not rerun.


Common Lisp alias lookups, prepared arguments and GC-aware measurements (2026-09-20).

  - **Remove recurring lookup and argument allocations.** Alias plans use a global EQ shape index and EQUAL name tables, preserving alternating aliases and the 256-total-plan/key-size bounds. Hits avoid composite keys and lowercase strings. Executed call nodes share a prepared argument vector, with list identity and vector published together; evaluated-value caches remain private to each invocation, and rewritten argument lists rebuild the metadata.
  - **Measure application gains and tradeoffs.** Repeated S3/S5 medians improve 3–6%/10–11%. S6 allocates about 14% less, but its standard full-batch mean regresses about 17%; a separate normal-GC probe with context validation around the batch improves mean latency about 10%. Mandelbrot is about 1% slower. The measurements expose GC interference from untimed input serialization rather than claiming a universal speedup or a confirmed historical 27% regression. [Results, diagnostics and reproduction](docs/interim/lisp-runtime-improvements.md).
  - **Keep retention bounded and account for cold costs.** Alias/schema churn retains about 47 KB more; 5,000 executed ten-argument programs retain about 0.56 MB more for shared vectors. AST node allocation size stays unchanged on SBCL. Changing schemas is about 14% slower in the focused probe, and compile-and-run about 2% slower. Cache eviction, oversized layouts and live-value ownership remain covered by tests.

Validation: 532 Lisp unit checks, 911 conformance cases, 852 SQL cases,
metadata checks and 39,990 decimal-oracle cases pass. All 4,000 differential
programs and 54 API probes agree across Lisp, C++ and JavaScript. Six-scenario
results and generated SQL match the shared reference; all final Mandelbrot
frames match. The full five-language repository gate was not rerun.

C++ container allocation and regression measurements (2026-09-20).

  - **Allocate container headers and payloads together.** Lists, records and container clones share one allocation, while scalar headers remain 72 bytes and decimals remain optional. Alias mutation, independent cloning and iterative deep destruction are preserved. No additional cache is introduced.
  - **Recover part of the join-throughput loss.** Against `c5a8991`, repeated measurements improve S1 by 3–5%, S5 by 3–5% and S6 by 6%, with S2/S3 also improving. S4 regresses 2–7% and Mandelbrot about 1%; these remain explicit tradeoffs. Live allocator memory is unchanged in the 200,000-value probes; row construction/destruction improve about 4%/11%. A bounded collection-block pool failed to recover the join losses and was rejected. [Benchmarks, raw samples and ownership details](docs/interim/cpp-collection-improvements.md).

Validation: 171 C++ unit checks, 85 SQL unit checks, 911 conformance cases,
852 SQL cases, metadata checks and 39,990 decimal-oracle cases pass. Address,
undefined-behavior and leak sanitizers pass the ownership tests. All 4,000
differential programs and 54 API probes agree with PHP. All six scenario checks
and Mandelbrot outputs agree; the full five-language gate was not rerun.

JavaScript import isolation and prepared record allocation (2026-09-20).

  - **Preserve the host environment.** Importing SEL no longer installs `BigInt.prototype.toJSON`, so frozen prototypes work and host JSON behavior remains intact. AST test snapshots serialize BigInts with local replacers. Source, SQL imports and both bundles have regression checks in the repository gate.
  - **Reduce prepared projection allocations.** Literal-key `RECORD` calls use packed key/value arrays instead of temporary field pairs. Evaluation order, cloning, stale-layout fallback, dynamic/duplicate keys and error positions are preserved. Focused measurements show 5–10% lower prepared-record latency and 7–9% lower latency for a complete 10,000-row projection. Dynamic records and Mandelbrot remain near baseline; the six larger scenarios vary by roughly −1% to +3%, without a general query-speedup claim. [Results and reproduction](docs/interim/js-runtime-improvements.md).
  - **Retain arithmetic gains.** Native BigInts, the shift-based magnitude guard and bounded metadata caches are unchanged.

Validation: 911 conformance cases pass for source and both rebuilt bundles;
852 SQL cases, 135 optimizer checks, 31 focused runtime checks, 14 guard checks,
metadata checks and 39,990 decimal-oracle cases pass. All 4,000 differential
programs and 54 API probes agree with PHP. The full five-language gate was not rerun.

Bounded metadata caches and prepared record layouts across all five lanes (2026-09-20).

  - **Bound shape and alias retention.** Shape interners and flat alias-plan caches retain at most 256 entries each, excluding layouts above 256 keys or 16,384 total key bytes/characters. Cache eviction preserves layouts owned by live values and compiled programs. Flat alias ownership prevents per-shape caches from retaining chains of other layouts.
  - **Prepare stable projections once.** Literal-key `RECORD` calls retain a prepared layout on the call node. Builtins verify the key sequence before reuse, preserving dynamic/duplicate-key fallbacks and evaluation order. JS aliases reuse resolved shapes directly. The two-field record benchmark improves in every lane, from about 4% less time in Python/C++ to 32% in JS.
  - **Bound large-power caches by size.** Python, JS and Lisp retain at most 64 dynamic powers, with exponent at most 1,000,000 and summed exponents at most 1,048,576. Larger powers remain exact but uncached. This bounds numeric cache payload while retaining useful large powers; fixed small-power tables remain unchanged.
  - **Measured retention.** After 20,000 changing schemas and 5,000 changing-schema joins, measured retained heap deltas fall from roughly 24–48 MB to 0.3–0.7 MB across the lanes. The large-power workload falls from roughly 9–11 MB to 0.03–0.10 MB. [The benchmark report](docs/interim/metadata-cache-optimizations.md) records raw samples, runtime-specific accounting, limits and reproduction commands.
  - **Constructor and build checks.** Fixed JS `Value.shaped()` passing a layout to a keys-based helper, retaining its array-copy behavior. C++ SQL targets now depend on the shared AST header, preventing stale objects after layout changes. Metadata checks run under the repository gate.

Validation: 911 conformance cases per lane and both JS bundles, 852 SQL cases per
lane, and 39,990 decimal-oracle cases per lane pass. All 54 host API probes agree.
Four lanes agree on 4,000 fuzz programs; Python matches its pre-change outputs
exactly, retaining 11 existing join-alias differences. Focused metadata and C++
sanitizer checks pass. The full repository gate was not rerun.

Common Lisp indexed arguments and sequential assignment paths (2026-09-20).

  - **Vector-backed argument access.** Call wrappers convert argument nodes to a simple vector, making indexed lookup and argument counts constant-time while retaining per-invocation value caching and lazy evaluation. A local 1,000-argument COALESCE improves from 838 to 104 µs. The extra vector increases call allocation; small calls can regress.
  - **Sequential path traversal.** `walk-create` walks list keys in order. `resolve-target` maintains a tail and length instead of repeatedly copying growing prefixes. It still re-resolves after index side effects, and assignment still re-resolves after the RHS. The path-traversal component falls from cubic to quadratic across repeated prefixes. At 199 keys, the local assignment benchmark improves from 3.01 to 0.91 ms and allocated bytes fall about 93%.
  - **Reproducible scaling measurements.** `tools/benchmark-lisp-traversal.lisp` covers argument access/construction, variadic execution, individual path walks, target resolution and full assignments. [Method, raw samples and allocation tradeoffs](docs/interim/lisp-traversal-optimizations.md) accompany the change.

Validation: 483 Lisp unit checks pass against both current and pre-change sources,
including new assignment side-effect and depth-boundary checks. All 911 conformance
cases, 852 SQL cases and 39,990 decimal-oracle cases pass; 4,000 differential
programs and 54 API probes agree with JavaScript. The full repository gate was
not rerun.

C++ optional value payloads and aggregate visitor specialization (2026-09-20).

  - **Smaller shared value implementation.** Decimal and collection state allocate only when needed. On the measured x86-64 build, `Value::Impl` shrinks from 272 to 72 bytes; the aliasing handle stays 8 bytes. Ordinary copies still share subsequent mutations, explicit clones own independent state, and deep destruction remains iterative.
  - **Measured footprint and construction gains.** For 200,000 short-text values, retained allocator memory falls from 57.6 to 16.0 MB and construction from 48.2 to 23.1 ms. Two-field rows fall from 179.2 to 102.4 MB and 158.6 to 122.8 ms. Separate decimal allocation has a cost: integer destruction is about 32% slower, and the measured SUM/arithmetic-MAP workloads regress about 6%/9% overall.
  - **Templated aggregate visitor.** `walk()` exposes its callback to compiler specialization. Against the smaller-payload build with its original `std::function`, measured SUM, FILTER and ALL improve about 1.6%, 2.3% and 3.9%; MAP is essentially unchanged. Benchmark executable text grows about 4.4 KB. [Method, samples and tradeoffs](docs/interim/cpp-value-payload.md) accompany the reproducible C++ benchmark.

Validation: 167 C++ unit checks, 911 conformance cases, 85 SQL unit checks,
852 SQL cases and 39,990 decimal-oracle cases pass. All 4,000 differential
programs and 54 API probes agree with JavaScript. Ownership tests pass address,
undefined-behavior and leak sanitizers; sanitized conformance passes with leak
checking disabled in the sandbox. The full repository gate was not rerun.

PHP scalar access, checked decimal mantissas and JavaScript range guards (2026-09-20).

  - **Direct PHP scalar reads.** Scalar context reads the first packed slot or ordinary child without materializing all children. A local 100,000-field record benchmark drops from about 1.41 ms to 0.40 µs per read; packed lists were already constant-time.
  - **Cached PHP native mantissas.** Decimal descriptors retain checked native integers between operations, validate cached source fields and bypass scale alignment for equal scales. Legacy descriptors, integer-overflow fallback and execution without GMP remain supported. Canonical digit strings remain available. Local addition, multiplication and comparison improve; division regresses and the measured full expression is essentially unchanged.
  - **Cheaper JavaScript range checks.** A conservative BigInt magnitude shift bypasses hexadecimal conversion below the million-digit boundary. Exact digit counting remains at the boundary. The local 100,000-digit addition sample improves from 195 to 10.7 µs without changing arithmetic precision.
  - **Representation measurements.** A declared-property PHP object uses less memory than a three-field array but takes longer to construct in the local probe. Arrays remain the production representation. Reproducible benchmarks, tradeoffs and validation are recorded in [the PHP/JS measurement report](docs/interim/php-js-runtime-optimizations.md).

Validation: 21 focused PHP checks and 14 JavaScript guard checks; 39,990 decimal
oracle cases per lane, also passing in PHP with optional extensions disabled;
911 conformance cases each in PHP, JavaScript, both rebuilt JS bundles and PHP
without GMP. All 4,000 differential programs and 54 host API probes agree across
the affected lanes. The full repository gate was not rerun for this change.

Python UTF-8 and arithmetic runtime optimizations (2026-09-20).

  - **Native decoding with SEL diagnostics.** Valid UTF-8 uses Python's strict native decoder. Invalid input falls back to the original validator, preserving `E_UTF8`, the first invalid-byte diagnostic and SEL source positions without chaining a host Unicode exception. A 75,792-input comparison with the original decoder produced identical results and diagnostics.
  - **One Python integer representation for decimals.** Removed the redundant signed small-mantissa cache and its 60-bit/18-scale eligibility checks. Arithmetic uses native arbitrary-precision magnitudes directly; scale alignment changes only the operand that needs it. Same-scale comparisons retain a direct integer path. Decimal objects shrink from 64 to 56 bytes on the measured CPython build, while scale, rounding and range limits remain unchanged.
  - **Cheaper math-plan dispatch.** Opcode constants are resolved once instead of looking up `IntEnum` attributes for every instruction. Plans remain faster than recursive AST evaluation on the measured arithmetic workloads, with separate scratch storage for each invocation. A fixed-expression fusion probe measures remaining dispatch overhead; no new instruction or production fusion compiler is introduced.
  - **Reproducible measurements.** `tools/benchmark-python-runtime.py` compares individual decimal operations, recursive AST evaluation, math plans and compiled-program execution. Local CPython 3.14.7 measurements show about 176× faster decoding on the repeated Polish-text sample and 1.2–1.3× faster arithmetic-expression execution. Tiny positive same-scale decimal comparisons regress by about 19 ns, while the full comparison-expression benchmark remains essentially unchanged. Inputs, timings and the fusion assessment are recorded in [the runtime measurement report](docs/interim/python-runtime-optimizations.md).

Validation: **585 Python unit tests**, including 25 plan-versus-AST cases over
multiple contexts; **911 Python conformance cases**; **39,990 decimal-oracle
cases**, with zero mismatches; and 54 host API probes agreeing with JavaScript.
All 4,000 fuzz outputs match the pre-change Python snapshot byte for byte. The
11 existing Python/JavaScript join-alias disagreements remain.

Relational execution optimizations (commits from 2026-09-18 through 2026-09-20).
Reviewed from the four most recent commits, oldest first:

  - **Common Lisp aggregate allocation and lookup (`3e871cf`).** Aggregate walks construct keys only when the body or result needs them, with cached index strings and values for the first 10,000 positions. Sort and top-N entries use structs instead of property lists, and sorted results retain member values without redundant deep copies. Inlined context lookups check string identity before string equality.
  - **C++ join execution (`5220ea3`).** Typed integer, decimal and text join keys avoid intermediate string keys. Direct packed-storage traversal, reserved context frames and shaped output construction reduce temporary entry allocation in relational pipelines.
  - **C++ value ownership (`a913f20`).** Value handles use intrusive reference counts, iterative destruction and a bounded thread-local allocation freelist. Decimal caches live directly in the value implementation, and ordinary child lookup starts building its index at four entries instead of sixteen.
  - **PHP join specialization and storage (`a913f20`).** Equijoins compile key extractors, table-alias builders and output projectors for regular row shapes, with fallbacks for other layouts. Packed collection traversal and direct field slots avoid repeated evaluator dispatch and entry-array construction. Packed lists can retain their original keys, building a lookup map on demand. Structural hashing switches from SHA-256 to xxh3 and reuses record-shape key fragments.
  - **Python execution and storage (`1614eed`).** Join projectors specialize regular shapes; context/schema column information guides join-filter pushdown, and physical plans rebuild when the supplied context object changes. Literal field access caches a slot guarded by record-shape identity. Packed lists with preserved keys, shared record layouts and direct collection iteration reduce temporary values and dictionaries. UTF-8 encoding and valid-text checks use native operations, retaining SEL error handling for invalid text; byte comparison uses Python's native byte ordering.
  - **Benchmark harness corrections (`3e871cf`).** Pure-memory, pure-SQL and hybrid expectations are derived from SQL and continuation presence. Dynamic-host runners no longer call the removed `force()` method; database benchmark failures are reported as skips so in-memory measurements can still complete.

Subsequent structural identity and scalar-access corrections (2026-09-20):

  - **Structural hashes agree with equality in all five hosts.** Lists, shaped records and ordinary records now hash the same logical keys and values. Materializing entries or populating a decimal cache no longer changes a value's hash. Numeric equality fast paths apply only to unformatted numeric values: coercing text such as `"01"` or `"-0"` to a number preserves its original spelling for structural equality. This fixes missed duplicates and split groups in `DISTINCT` and projected `BUCKET`. C++ also avoids its fixed-size formatting buffer for large or high-scale decimal hashes. Thirteen shared conformance cases and host unit tests cover these boundaries.
  - **Constant-time Python scalar access.** Scalar context reads the first packed slot or dictionary value directly instead of materializing every child. A local CPython measurement is approximately 0.5 microseconds per read for collections of 10, 1,000 and 100,000 elements; null, empty-list and depth errors retain their behavior.

Validation: **911 conformance cases** pass in all five hosts and both JavaScript
bundles; **852 SQL translation cases** pass in every lane; **185 mutations** are
caught, with seven database-dependent cases skipped because no DSNs were set.
The decimal oracle reports zero mismatches across 39,990 cases per language.
C++ address, undefined-behavior and leak sanitizer runs pass all 160 unit checks
and 911 conformance cases. The full gate remains non-green: its 4,000-program
differential fuzz run reports 11 pre-existing join-alias disagreements, reproduced
against the original Python and JavaScript sources. Database oracles were skipped.

Universal lazy decimal-to-string representation and cross-lane execution acceleration across all five host implementations (Common Lisp, C++, JavaScript, PHP, Python).

  - **Universal lazy decimal representation (Aster concept).** Values produced by decimal operations (`Value::num`, `integer`, arithmetic operators, and `MathPlan` execution) retain their native arbitrary-precision decimal structures (`dec_val` / `_decimal` / `decVal` / `_dec_val`), leaving their scalar string representation uncomputed. String formatting is deferred lazily on-demand until explicitly observed in string context (`as_text()`, string concatenation `&`, canonical dump, or host interop).
  - **Leaf cloning and direct numeric equality.** Leaf values without child structures bypass allocations in `clone_at` while preserving deferred decimal state. Structural equality (`eql_at`) checks decimal operands directly (comparing sign, scale, and mantissa/digits) without triggering string serialization or allocations.
  - **Parser AST decimal caching and index literal fast paths.** Numeric literals parsed in `parse_primary` store their parsed `Dec` directly on the AST `Node`, eliminating string re-parsing in the evaluator and `MathPlan` compiler. Property lookup on text literals (`obj["field"]`) reads the node's key string directly without wrapping in an intermediate `Value`.
  - **Verified parity across all test suites.** 100% green across all 898 conformance test cases, 39,990 random decimal oracle cases (0 mismatches), 185 mutant warrants caught, 4,000 differential fuzz programs, and identical byte-for-byte ASCII art output on `examples/mandelbrot.sel`.
  - **JavaScript native BigInt mantissa migration.** Migrated `Dec` in `js/src/decimal.mjs` to native `BigInt` mantissas (`digits` as `BigInt`), matching Python's `int` and SBCL's bignum cores. Eliminated intermediate base-10 string serialization and parsing in `mul`, `add`, `sub`, `div`, and `cmp`, reducing Mandelbrot pure runtime from 1.203s to 0.119s (0.191s CLI, over 10x acceleration).
  - **Benchmark results across all 5 lanes (empirically measured):**
    - *Mandelbrot (`examples/mandelbrot.sel`):*
      - Common Lisp (SBCL): pure run time **0.078 s** (#1 fastest pure math execution, zero consing) (CLI 0.767 s)
      - JavaScript (Node.js): pure run time **0.119 s** (CLI **0.191 s**, fastest CLI execution)
      - C++: total CLI **0.298 s**
      - Python 3 (CPython): pure run time **0.389 s** (CLI 0.455 s)
      - PHP 8.3 (with GMP): pure run time **0.524 s** (CLI 1.043 s)
    - *10,000-row relational pipeline (`PROJECT .> FILTER .> BUCKET .> SORT_BY .> TAKE`):*
      - JavaScript: run **0.239 s**, compile 0.286 s (total CLI: **0.615 s**)
      - C++: total CLI **0.274 s**
      - Common Lisp: run **0.276 s**, compile 0.200 s (total CLI: **1.136 s**)
      - PHP: run **0.965 s**, compile 1.087 s (total CLI: **2.655 s**)
      - Python: run **1.755 s**, compile 2.149 s (total CLI: **4.033 s**)

Cross-lane big-decimal execution acceleration across all host implementations (JavaScript, Common Lisp, C++, PHP, Python), as documented in `docs/interim/sel_big_decimal_cross_lane_acceleration_analysis.md` (2026-09-17).

  - **JavaScript native `BigInt` core.** Replaced character-by-character string arithmetic loops in `addAbs`, `subAbs`, `mulAbs`, and `divModAbs` with native `BigInt` operations while strictly preserving decimal scaling, minimal scale normalization, rounding half-away-from-zero rules, and 1M-digit limit guards (`MAX_INT_DIGITS`, `MAX_FRAC_DIGITS`). Mandelbrot execution accelerated from 44.8s to 1.28s (~35x speedup).
  - **Common Lisp (SBCL) native bignum core and execution acceleration.** Migrated `Dec` representation to native integer bignums (`digits` as integer), mirroring Python's `decimal.py`. Implemented divide-and-conquer radix parsing (`parse-bignum-string`), $O(1)$ bit-length capacity checks (`+max-int-bits+ 3321929`), explicit base-10 formatting (`write-to-string ... :base 10`), and eliminated intermediate string conversions during expression evaluation and comparisons. Replaced linear $O(N)$ token position scan in `lexer-pos-at` with binary search over `(simple-array fixnum (*))`, reducing 10k-line compilation time from 6.42s to 0.20s (32x speedup). Implemented lazy decimal string formatting (`%scalar` deferred until text access) and stack-allocated math execution scratchpad (`dynamic-extent`), driving Mandelbrot pure runtime from 1.074s to 0.079s (13.6x speedup, fastest of all hosts).
  - **C++ 128-bit fast path and base-$10^9$ limbs.** Added native `__int128_t` mantissa fast path (covering values up to $10^{38}$ with scale $\le 38$), multi-limb base-$10^9$ arithmetic (`uint32_t` limbs, `uint64_t` accumulators), $O(1)$ substring division for powers of 10, and cached `dec_val` on `Value::Impl` to eliminate string re-parsing during math plan execution. Mandelbrot execution accelerated from 6.5s to 0.306s (21x speedup).
  - **PHP hybrid GMP and native multi-limb engine.** Implemented automatic GMP extension support (`extension_loaded('gmp')`) with a pure-PHP accelerated fallback featuring 64-bit native int fast paths, 2-limb 18-digit products, base-$10^{14}$ addition/subtraction, base-$10^7$ multi-limb multiplication, and $O(1)$ power-of-10 slice operations for `isInteger`, `trunc`, `floor`, `ceil`, and `round`. Mandelbrot execution accelerated from ~48s to 1.10s (~43x speedup with GMP; 4.65s on pure-PHP fallback without GMP).
  - **100% Cross-Lane Parity.** Verified with 39,990 random test cases against Python's decimal oracle (`tools/check-decimal.sh 4000`, 0 mismatches), 898/898 conformance cases in all 7 host configurations, 4,000 differential fuzz programs with 0 disagreements, and byte-identical ASCII art on `examples/mandelbrot.sel`.

In-memory AST mathematical execution optimizer (`MathPlan`) across all five host implementations (Python, JavaScript, C++, PHP, Common Lisp), as planned in `docs/interim/ast-execution-optimizer.md` (2026-09-16).

  - **Subtree compilation to flat three-address execution plans.** Arithmetic expressions over numerical operators (`+`, `-`, `*`, `/`, `%`, `^`, unary `-`) are compiled during physical optimization into an array of linear 3-address instructions (`LOAD_VAL`, `LOAD_VAR`, `LOAD_LEAF`, `ADD`, `SUB`, `MUL`, `DIV`, `MOD`, `POW`, `NEG`). Nested arithmetic walks bypass recursive AST node dispatch and intermediate decimal serialization, operating directly over native decimal representations with temporary register reuse.
  - **Strict spec parity, error positions, and evaluation order.** Retains strict left-to-right operand evaluation (Spec §6.2) and exact innermost error position reporting (Spec §6.3) for `E_NOT_NUM`, `E_DIV_ZERO`, and `E_NUM_OVERFLOW`. Non-math subtrees and variables are evaluated in order and asserted as decimals. Expressions reaching or exceeding the evaluator depth cap ($\ge 200$) remain unoptimized per Spec §6.4 (Finding AF), evaluating as written through the standard depth-checked evaluator.
  - **Safe algebraic identity copy-propagation.** Zero-scale additive identities (`x + 0`, `0 + x`, `x - 0`) and multiplicative identities (`x * 1`, `1 * x`) eliminate redundant binary arithmetic steps at plan compilation time, while runtime validation preserves type safety (`E_NOT_NUM`) at the operand's source position.
  - **Iterative arithmetic acceleration.** Eliminates AST overhead in tight loops; verified byte-for-byte identical ASCII output across all five hosts on `examples/mandelbrot.sel` with dramatic runtime speedups.

Adversarial SQL/local-processing gaps F1–F4 and F6, verified across all five implementations (2026-09-16); the existing F5 joined-row fallback was rechecked.

  - **Slices compose without widening the result** (F1). Repeated `TAKE`/`DROP`, empty slices and large offsets preserve the bounded input window, with safe-integer overflow handling.
  - **Grouping and DISTINCT preserve SEL identity** (F2, F3). MariaDB/MySQL use binary no-pad text collations and SQLite explicitly overrides inherited column collations. Direct derived projections carry proved field types; unproved row identity and computed numeric group keys fall back locally. Hybrid splits retain identity-sensitive calculations and ordinal keys instead of silently changing groups or filter results.
  - **RECORD projection collisions stay local** (F4). Duplicate and case-colliding SQL aliases are refused before projection splitting, preserving SEL's last-write behavior and evaluation errors.
  - **Latest-revision selection can transfer only winners** (F6). An explicit single-column, non-null unique-key binding declaration enables a narrow `BUCKET`/`TOP_BY(..., "DESC", 1)` hybrid strategy: SQL selects complete winning rows and the local continuation rebuilds the groups. Unsupported shapes retain the existing fallback. The 100,000-revision regression transfers 100 winner rows; this is a transfer-volume result, not a timing claim.
  - **Reproducible database regressions.** Disposable Docker tooling covers PostgreSQL, MariaDB, MySQL and SQLite, including inline/prepared execution and the installed Python wheel. The verification report records 2,568 live SQL executions and 1,188 hybrid replays. The separate C1 depth/error-policy boundary remains open; optional database-backed SQL fuzzing is not claimed green.

Verification: 898 conformance cases across seven runtime variants; 852 SQL cases across all five hosts (including declared type-system refusals); all 192 mutations caught across the default and supplementary live runs. Scope, reproductions and remaining limits: [fix verification](docs/interim/sel-gaps-2026-09-15-08-fix-verification.md).

The last of the 2026-09-15 review (W2, the lane facet of Y, #31), and what the widened fuzzer found.

  - **The fuzzer reaches the pipelines** (#31). `tools/gen-programs.mjs` now emits `.>` chains of every step (the sorts' three forms, `_K` after a renumbering step, bare and projected buckets, `LINK`/`LINK_LEFT` with and without named binders, helpers as sources, `COUNT`/`SUM` over a pipeline) and chains at the depth cap; with `--sql` the pipelines read relations every host's `sqlfuzz` runner now binds, and the runners compare three lanes per program — `translate`, `translate_statement` and `plan_hybrid`. Ten planner mutations (`*-filter-swap-ignores-keys`, `*-bucket-keys-are-rows`) join `sql/mutations.json`. Its first 8000 programs found four cross-host divergences no case had pinned, fixed here: C++ took a `LINK` side whose first element is `NULL` or has no fields for an empty side and answered the empty list where the other four evaluate the predicate (`rel.link.predicate-reads-an-element-with-no-fields`, `rel.link-left.*-element`); Lisp evaluated a `LINK_LEFT` predicate with no right elements (`rel.link-left.no-right-elements-never-evaluates-the-predicate`); and Lisp's `TOP`/`TOP_DESC`/`TOP_BY` with `n` of `0` — which its optimiser makes of `SORT() .> TAKE(0)` — never evaluated the list, so `COUNT(LST .> SORT() .> TAKE(0))` was `0` with `LST` unbound (`rel.top.*`, `rel.take.zero-after-a-sort-still-evaluates-the-list`). Spec §7.4 now says how a `LINK` evaluates (the right side's key expression over every right element first, then the left's; `NULL` and field-less elements are elements; no pairs, no evaluation) and that a count of zero still evaluates the list.
  - **A `LINK`'s binders are scoped to its predicate, in every lane** (W2 d2; spec §7.4). After the `LINK` the joined row carries `O`, `C`, `_1`, `_2` as keys and `run()` raises `E_UNDEF_VAR` for `C["id"]` in a later step — yet JS, PHP and Python translated the right binder there, C++ and Lisp translated both, and every host's optimiser pushed `FILTER(O["id"] > 1)` into the `LINK`'s left source, where `O` is bound, answering rows for a program that raises as written (the same pipeline through a helper never took the rewrite). Every translator now refuses every binder after the join (`E_SQL_UNBOUND` at the read), and the pushdown attributes only a read through the row, `_["O"]["id"]`, to a side. `link.binders.*`, `rel.link.then-filter.*`.
  - **A joined row in SQL is its promoted fields, and a join alone is not a split point** (Y, lanes; W2 d1). Every host rendered a `LINK` as `SELECT o.*` — the left table's columns, a row SEL never produces — so a continuation over a `LINK` prefix read the right side's columns where the database had sent none, a derived table over a join named columns it did not have, and C++ resolved a field both sides carry to the left one where the other four refuse. The row is now the promoted fields (`SELECT o.customer_id, o.amount FROM … INNER JOIN …`), in the statement and in a derived table, a name both sides have is `E_SQL_SHAPE` at the read in all five — through a step's own binder too, where every host had gated the check on the binder being `_` (`link.row.a-named-binder-is-the-same-row`) — and `joinRowsLackBinders` beside `bucketRowsAreKeys` keeps an unprojected join from being a split point or a full pushdown: `ORDERS .> LINK(…) .> TAKE(2)` is pure memory, `… .> MAP(RECORD("a", _["amount"]))` pure SQL. `sql/cases/26-links.sqlt` (eighteen cases); three planner cases re-pinned.
  - Facets (a), (b) and (c) of W2 — the NUM guard over a derived column, the `DISTINCT`/`ORDER BY` wrap before a `MAP`, C++'s derived-column spelling — were found already aligned by the earlier fixes (W1, V) and are pinned by the existing cases.

Three more findings of the 2026-09-15 review (AF, Y, AK) and one found during its remediation (#33).

  - **The evaluator is the depth authority** (AF; spec §6.4). Four hosts folded a chain of 201 additions to 201 where the evaluator raises E_DEPTH at 1:1 — the leaf at the boundary was returned unrewritten and its parent folded it — and Lisp's optimiser raised E_DEPTH at its own count, on `IF(TRUE, 7, <chain>)` too, a branch the evaluator never visits. Every optimiser now returns a tree that reaches the cap as written (a bounded walk at the entry point, counting as the evaluator counts) and none raises; `lim.eval-depth-*` pins both sides of the boundary, through an `IF` and behind an assignment. `X = <199 ones>; X` is E_DEPTH at 1:5, as it always was for a chain that does not fold.
  - **Keys survive the FILTER rewrites** (#33; spec §7.3 "Keys are part of the value"). Every host's logical optimiser moved a FILTER in front of a MAP, a sort or a SELECT_COLS whenever its predicate read only pass-through fields; FILTER keeps its input's keys and those three renumber, so `LIST(…) .> MAP(RECORD("id", _["id"])) .> FILTER(_["id"] > 1)` answered `{"1": …}` where the program as written — and the same pipeline through a helper — answers `{"2": …}`, and a later `_K` read the renumbered key. The swap is now taken only when the step after the FILTER renumbers again without reading `_K`. In the SQL lane a `MAP .> FILTER` tail is rendered over the MAP's derived table rather than in front of it (one planner case re-pinned; on sqlite that shape no longer pushes down, because the derived column's NUM guard cannot be rendered there). `rel.map.then-filter-*`, `rel.sort.then-filter-keeps-the-sorted-keys`, `rel.select-cols.then-filter-keeps-the-keys`.
  - **LINK builds one row shape** (Y; spec §7.4 "Joined rows", the first specification of the joined row). JS and Lisp built a joined row with the relation's name twice (`X`, `x`, `X`, `x`, `_1`, …), the first holding the bare element, so `COUNT`, `INDEXES` and `J[1]["X"]["x"]` differed from PHP, Python and C++; and in all five the uncompiled row builder kept a carried `_1` from an earlier LINK where the compiled projector bound the new row. Each key now appears once, where it first occurred, and a binder key holds the row this LINK bound; `rel.link.*` and `rel.link-left.unmatched-row`. The lane facet of Y — a LINK prefix renders as the left alias's columns only, so a continuation over it cannot see the right side — is not fixed here; it is scheduled with W2.
  - **Lisp hands the runner placeholders** (AK). `execute-hybrid` called the runner with inline-mode SQL and the fragment's creation-order slot list; it now passes `(as-statement frag :params)` and `(bindings frag)` like the other four, and every host's unit lane pins the contract (`?` for the text literals, the number inlined, bindings in placeholder order).

Two more findings of the 2026-09-15 review (AJ and R).

  - **The hybrid continuation reports errors where `run()` does, helpers included** (AJ). The planner planned stage 1's tree, in which a helper assignment is inlined with its definition-site position, so `Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)` ran in memory as `_["id"] + "x"` and reported E_NOT_NUM at 1:5 where `run()` reports the read at 1:45 — in all five hosts — and a helper was evaluated once per row rather than once. The planner now plans the program as written: a helper that is a literal (after folding, so `N = 1 + 1` too) is inlined at its reads stamped with the read's position, a helper read as the pipeline's source is unwound through, and every tree handed to the translator or kept as the continuation carries the assignments it still reads in front of it, as written — the translator's own stage 1 inlines them, the continuation evaluates them once. The SQL side is byte for byte what it was on every existing case. Along the way: C++ read a stage-1 result it could only express as a keyed list (`R[1] = 5; …`) as a refusal of the whole program and planned pure memory where the other four pushed the prefix down; it no longer does (`plan.helper.indexed-helper-is-carried-as-written`). `plan.helper.*` (six cases); nine more executed-plan position probes and three value probes per host.
  - **`LAZY_RECORD` is gone** (R). Every host rewrote `MAP(RECORD(…))` with two or more pairs into a `LAZY_RECORD` whose non-literal fields were evaluated on first read, and RECORD is strict: `COUNT(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))` was 1 in four hosts and E_DIV_ZERO in C++ (whose MAP clones what it collects, forcing the thunks), and a FILTER after the MAP split the hosts three ways. The rewrite, the builtin (which no spec named) and the thunk plumbing in every value layer are removed; `rel.map.record-field-*`, `mis.compile.lazy-record-is-not-a-function`. docs/EXTENDING.md's "five copy sites" now says what the hosts do: `,` and `=` copy everywhere, C++ also clones what MAP and FILTER collect and PHP what FILTER collects, which no program can observe because a binder cannot be assigned.

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

One optimiser policy in front of the translator, one rewrite order (2026-09-15 review findings C, V, B and W1).

  - **The translator never optimises** (C). JS and Python ran the pipeline rewrites inside `translate()`/`translateStatement()`, Lisp's `translate-statement` ran the full optimiser with constant folding, and PHP, C++ and Lisp's `translate` ran stage 1 only — so `ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])` was `WHERE (2 >= id)` on one host and a `CASE` on four, and `FILTER(TRUE)` was dropped by two. All five now run stage 1 alone in both entry points (the planner is the one place that optimises, and it does so everywhere); the `optimizeSql`/`noOptimize`/`:no-optimize` keys are gone, and every `.sqlt` runner puts each `--- as statement` case through both entry points and requires the same text or the same refusal at the same column. `stmt.lane.*`.
  - **A later sort's keys come first, and no optimiser drops the earlier sort** (V, and a finding of its own). SEL's sorts are stable, so `SORT_BY(a) .> SORT_BY(b)` breaks `b`'s ties by `a`; the optimiser's sort/sort elimination dropped `a` in all five hosts (`rel.sort.then-sort-keeps-the-tie-order` — wrong in every `run()`), four translators wrapped the first sort in a derived table (which loses it) and Lisp appended the later keys after the earlier ones (which sorts by the wrong key). The rule is gone, and every translator renders `ORDER BY b, a`. A sort after a `TAKE`/`DROP` on a grouped statement now wraps the page instead of sorting before the `LIMIT` (four hosts). And the Lisp optimiser applies the rewrites as the other four do — one left-to-right sweep over adjacent steps to a fixed point (`logical-step-pair`) — instead of ten ordered passes that fused `SORT_BY .> TAKE` before the MAP/sort swap could see it. `stmt.order-by.later-sort-*`, `stmt.order-by.sort-after-pagination-sorts-the-page`, `plan.sort.later-sort-is-the-primary-key`, `plan.map.computed-then-sort-then-take`.
  - **The unary-minus fold fires in PHP, C++ and Lisp** (B): the arm tested for `"-"` where every parser emits `NEG`, so a negative literal folded in two hosts and rendered as a CAST of a negation in three; the Lisp arm also negated by string surgery (`"-0"`) and goes through the decimal core now. `plan.fold.negative-literal*`.
  - **A derived table's columns are UNKNOWN in Lisp too** (W1): it alone kept the source column's declared kind through a pass-through derived table and rendered an unguarded comparison where the other four render the numeric guard. `stmt.lane.sort-then-filter-wraps`.

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
