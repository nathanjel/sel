# C. translate()/translate_statement apply different optimisation policies per host

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "One optimiser policy in front of the translator, one rewrite order".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** mixed · **hosts:** lisp, js, python, php, cpp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

The five hosts run three different optimisation policies in front of the SQL translator: Lisp's translate-statement runs the full logical optimiser (constant folding + FILTER fusion + pipeline rewrites, opt-out only via :no-optimize), JS/Python's translate() and translateStatement() run the optimiser with foldConstants/fuseFilters forced off (pipeline rewrites only), and PHP/C++ (and Lisp's translate()) run stage 1 only. I reproduced every facet on a 12-program corpus (postgresql): the public translate_statement lane emits different SQL text, accepts vs refuses, and reports different error codes and positions per host, while plan_hybrid agrees byte-for-byte in JS, PHP and Lisp. The wiring is pre-existing (identical at 8fe0e3a), but the position half is new: at the parent Lisp translate-statement reported E_SQL_SHAPE@1:46 for `ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"])` like the other four; at ed16df2 it reports 1:37 because the commit's hoist-literal now stamps the IF's position, and only Lisp folds in this lane.

## Suggested fix — case first

Smallest fix: make the five translators run one policy. Since PHP, C++ and Lisp translate() already run stage 1 only and the fixture note says "the translator never folds", drop the optimiser pre-pass from JS/Python translate()/translateStatement (js/src/sql/translator.mjs:149-150,173-174; python/sel/sql/translator.py:165-167,189-191) and from Lisp translate-statement (lisp/src/sql/translator.lisp:2380-2382: use (sel:program-ast program) unconditionally), and remove the now-meaningless optimizeSql/noOptimize/:no-optimize keys; the planner keeps folding (that path is pinned by 25-hybrid-plans.sqlt and agreed). If the rewrites are wanted in translate() instead, port them to PHP/C++/Lisp and give Lisp's optimize-ast-logical the fold/fuse options — but then also reconcile the derived-table/numeric-guard difference on `SORT_BY .> FILTER` that Lisp vs PHP/C++ show without any optimiser. Cases to add first: extend the sqlt runners (or gen-sql-cases) so every `--- as statement` case is also run through translate_statement and must equal translate()'s text; then pin `ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])` expecting `CASE WHEN TRUE THEN 2 ELSE 1 END` and `ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"])` expecting `E_SQL_SHAPE 1:18` in both lanes, plus `ORDERS .> FILTER(TRUE) .> TAKE(1)` and `ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1)` in translate() to pin whichever shape is chosen. Also teach tools/gen-programs.mjs to emit bound `.>` pipelines so fuzz-sql can see this lane.

## Verifier reasoning

Code reading (every host): JS js/src/sql/translator.mjs:149-150 (translate) and :173-174 (translateStatement) call optimizeAstLogical(ast, {foldConstants:false, fuseFilters:false}) unless optimizeSql===false/noOptimize; Python python/sel/sql/translator.py:165-167 and :189-191 identical; PHP php/src/Sql/Translator.php:70-85 and :94-106 call Normalise::run only, no optimiser and no options besides strict (:66); C++ cpp/sel_sql_translator.cpp:115-140 and :148-166 call normalise() only; Lisp lisp/src/sql/translator.lisp:2339-2360 translate() normalises only, but :2370-2386 translate-statement calls (sel:optimize-ast-logical (sel:program-ast program)) at :2380-2382 unless :no-optimize, and lisp/src/optimizer.lisp:927 `optimize-ast-logical (node &optional (depth 1))` has no fold/fuse switch, so folding (hoist-literal, optimizer.lisp:37) and fusion always run. Hybrid planners call translate_statement in all five hosts (js/src/sql/hybrid.mjs:68, python/sel/sql/hybrid.py:109, php/src/Sql/Hybrid.php:190, cpp/sel_sql_hybrid.cpp:268/337/362, lisp/src/sql/hybrid.lisp:97/118/309) but only after the planner has already folded in every host, which is why the hybrid lane agrees and the divergence is confined to the two direct translator entry points. Runners: js/bin/sqlt.mjs:193, php/bin/sqlt:316, python/bin/sqlt:246, cpp/bin/sqlt.cpp:231, lisp/bin/sqlt.lisp:213 all call translate() then asStatement(); translate_statement is called only from cpp/tests/sql_unit.cpp:39,53 and lisp/tests/unit.lisp:342-399,679-680 (host-local, not cross-checked). js/bin/sqlfuzz.mjs:53 translates without bindings and tools/gen-programs.mjs emits no `.>` (grep count 0), so fuzz-sql cannot see any of this. Promises touched: sql/cases/05-conditionals.sqlt:70 note "The translator never folds" (false for Lisp translate-statement); docs/EXTENDING.md "Positions: the optimiser is invisible: an error a program raises after optimisation carries the same code and position as before it" (Lisp translate() E_SQL_SHAPE@1:18 vs translate-statement E_SQL_INVALID@1:18 on the same source); EXTENDING "Options: A key must mean the same thing in every host" (Lisp :no-optimize disables fold+fuse+rewrites, JS/Py noOptimize disables rewrites only, PHP/C++ have no such key). CHANGELOG's "translate() refuses the same source at the same column" is literally about translate() and holds; §12.1 is about plan_hybrid and holds (verified). Introduced assessment: the three policies existed at 8fe0e3a (git show confirms identical lines); the observable new behaviour is the position shift in Lisp translate-statement (1:46 -> 1:37; 1:27 -> 1:18) caused by the commit's hoist-literal position stamping flowing through the pre-existing Lisp-only fold. Extra observation not in the cluster: on translate() `ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1)` is a three-way split — JS/Py push the filter under the sort, PHP/C++ build a derived table and emit the numeric guard, Lisp builds the derived table but emits the unguarded `("_sub1"."id" > 1)`; and `SELECT_COLS("id") .> FILTER` needs no derived table in Lisp/JS/Py but does in PHP/C++ — a translator-level difference beyond optimiser policy. Severity high: different SQL, accept/refuse, error codes and positions between hosts through a public typed entry point (js/src/sql.d.ts:186 translateStatement).

## Verifier evidence

```
Probe harness under /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-C/ (probe.mjs, probe.py, probe.php, probe.cpp compiled as probe_cpp against cpp/build/sel.o + sel_sql*.o, probe.lisp loaded after (ql:quickload :sel-lang/sql); corpus.txt of 12 programs; ORDERS = relation orders/o {ID: column id o NUM}; dialect postgresql). Commands: `node probe.mjs corpus.txt`, `python3 probe.py corpus.txt`, `php probe.php corpus.txt`, `./probe_cpp corpus.txt`, `sbcl --load lisp/bin/boot.lisp --eval '(ql:quickload :sel-lang/sql)' --load probe.lisp --eval '(main "corpus.txt")'`. Results: `diff out-js.txt out-py.txt` empty; `diff out-cpp.txt out-php.txt` empty. translate_statement (TS) rows, JS/PY/PHP/CPP vs LISP: `ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])` -> `WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")` vs `WHERE (2 >= "o"."id")`; `FILTER(_["id"] > 1) .> FILTER(_["id"] < 9)` -> `WHERE ("o"."id" > 1) AND ("o"."id" < 9)` vs `WHERE (("o"."id" > 1) AND ("o"."id" < 9))`; `FILTER(_["id"] > 1 + 1)` -> `(CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC))` vs `2`; `FILTER(IF(TRUE, "x", 1) >= _["id"])` -> `ERR E_SQL_SHAPE@1:18` vs `ERR E_SQL_INVALID@1:18`; `FILTER(IF(FALSE, "x", TRUE))` -> `ERR E_SQL_SHAPE@1:18` vs accepted `SELECT "o".* FROM "orders" "o"`; `SORT_BY(O, IF(TRUE, "DESC", "ASC"))` -> `ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC` vs `ERR E_SQL_UNBOUND@1:19`; `TAKE(3) .> MAP(RECORD("id", _["id"], "k", 10 * 10))` -> `(CAST(10 AS NUMERIC) * CAST(10 AS NUMERIC)) AS "k"` vs `100 AS "k"`; `FILTER(NOT FALSE AND _["id"] < 5)` -> `((NOT FALSE) AND ...)` vs `(TRUE AND ...)`; `DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"])` -> `ERR E_SQL_SHAPE@1:46` (all four) vs LISP `ERR E_SQL_SHAPE@1:37`. translate() (T) rows, JS/PY vs PHP/CPP/LISP: `FILTER(TRUE)` -> `SELECT "o".* FROM "orders" "o"` vs `... WHERE TRUE`; `SORT_BY(_["id"]) .> FILTER(_["id"] > 1)` -> JS/PY `WHERE ("o"."id" > 1) ORDER BY "o"."id" ASC`, PHP/CPP `SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)`, LISP `... "_sub1" WHERE ("_sub1"."id" > 1)`; `SELECT_COLS("id") .> FILTER(_["id"] > 1)` -> JS/PY/LISP `SELECT "o"."id" FROM "orders" "o" WHERE ("o"."id" > 1)`, PHP/CPP derived table + guard. Lisp translate() (non-statement) matches PHP/C++ on every fold/fuse row. Parent commit: `git worktree add --detach <scratch>/old 8fe0e3a` (removed afterwards; `git status` clean), same probe.lisp loaded from old/lisp/bin/boot.lisp: `diff out-lisp-old.txt <(grep ^TS out-lisp-clean.txt)` shows `FILTER(IF(TRUE, "x", 1) >= _["id"])`: old `E_SQL_INVALID@1:27` -> new `E_SQL_INVALID@1:18`; `MAP(IF(TRUE, NULL, 1) + _["id"])`: old `E_SQL_SHAPE@1:46` -> new `E_SQL_SHAPE@1:37`; all other TS rows unchanged. Hybrid lane: hyb.mjs / hyb.php / hyb.lisp over the same corpus with planHybrid(postgresql): `diff hyb-js.txt hyb-lisp.txt` empty, `diff hyb-js.txt hyb-php.txt` empty (e.g. `FILTER(IF(TRUE, 2, 1) >= _["id"])` -> SQL `WHERE (2 >= "o"."id")` in all three; `FILTER(TRUE)` -> pure memory in all three). Old wiring: `git show 8fe0e3a:lisp/src/sql/translator.lisp` lines 2335-2352 already call optimize-ast-logical; `git show 8fe0e3a:js/src/sql/translator.mjs` lines 149-150/173-174 already pass foldConstants:false; `git diff 8fe0e3a ed16df2 -- <5 translator files> | grep -i optimi` touches only the PIPELINE_OPS import/comment.
```

## Original review reports (deduplicated into this finding)

### [fold-positions] translate_statement lane folds constants only in Lisp; JS/Python pass foldConstants:false, PHP/C++ do not optimise at all

*coherence · medium · hosts: lisp, js, python, php, cpp*

Locations: `lisp/src/sql/translator.lisp:2380`; `lisp/src/sql/translator.lisp:2352`; `js/src/sql/translator.mjs:150`; `js/src/sql/translator.mjs:174`; `python/sel/sql/translator.py:165`; `python/sel/sql/translator.py:189`; `php/src/Sql/Translator.php:94`; `cpp/sel_sql_translator.cpp:148`

The commit promises the same error code and position after optimisation 'in every lane', and pins translate() with cond.refuse.* / op.logic.* cases. But translate_statement — the public full-delegation entry point the C++ and Lisp unit tests use — runs a different optimiser per host: Lisp's translate-statement calls (sel:optimize-ast-logical ...) with no way to disable folding (only :no-optimize), so it folds and fuses; JS and Python run optimizeAstLogical with {foldConstants:false, fuseFilters:false}; PHP's Translator::translateStatement and C++'s Translator::translate_statement run stage 1 only. Lisp's translate (not -statement) also does not optimise, so the two Lisp entry points disagree with each other as well. Result: for `ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])` Lisp translate-statement emits `WHERE (2 >= "o"."id")` and the other four `WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")`; for `ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))` Lisp refuses with E_SQL_UNBOUND at 1:19 while the other four emit `ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC`; for `ORDERS .> FILTER(IF(TRUE, TRUE, FALSE))` Lisp emits `SELECT "o".* FROM "orders" "o"` (filter folded away) versus `... WHERE CASE WHEN TRUE THEN TRUE ELSE FALSE END`. The sqlt runners exercise only translate(), so no shared fixture sees this. Pre-existing wiring, but it is the lane the commit's 'every lane' promise does not hold in, and the fold is what makes it visible.

Reported repro:

```
scratchpad lanes.lisp (sbcl --load lisp/bin/boot.lisp) vs lanes.mjs / lanes.py / lanes.php / lanes_cpp, postgresql, ORDERS=relation(orders,o,{ID:column id NUM}):
  ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])
    LISP translate-statement: SELECT "o".* FROM "orders" "o" WHERE (2 >= "o"."id")
    JS/PY/PHP/CPP translateStatement: ... WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
  ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))
    LISP translate-statement: SQLERR E_SQL_UNBOUND at 1:19: O is read by this rule but no binding says where it lives
    JS/PY/PHP/CPP: SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
  ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9)
    LISP translate-statement: ... WHERE (("o"."id" > 1) AND ("o"."id" < 9))   (fused)
    JS translateStatement:    ... WHERE ("o"."id" > 1) AND ("o"."id" < 9)
Lisp translate (non-statement) matches the other hosts, so the divergence is between lisp/src/sql/translator.lisp:2352 (translate: normalise only) and :2380-2382 (translate-statement: optimize-ast-logical).
```

### [runners-generator-tests] translate_statement lane is never exercised by any runner, and Lisp's translate-statement runs the full folding/fusing optimiser where the other four do not

*coherence · medium · hosts: lisp, js, python, php, cpp*

Locations: `lisp/src/sql/translator.lisp:2380`; `js/src/sql/translator.mjs:173`; `python/sel/sql/translator.py:189`; `js/bin/sqlt.mjs:200`; `php/bin/sqlt:300`; `python/bin/sqlt:240`; `cpp/bin/sqlt.cpp:206`; `lisp/bin/sqlt.lisp:200`

All five sqlt runners call translate() and then asStatement(); none calls translate_statement, and only cpp/tests/sql_unit.cpp and lisp/tests/unit.lisp touch it at all. In that unexercised lane Lisp's translate-statement (translator.lisp:2380-2382) runs `optimize-ast-logical` with constant folding and filter fusion on by default (opt-out `:no-optimize`), JS/Python run it with foldConstants/fuseFilters forced off (translator.mjs:173-174, translator.py:189-191), and PHP/C++ run no optimiser. So the same statement renders differently, and the fold changes in this commit (literal hoisting, IF-branch rule) now apply to Lisp's translate-statement output and nobody else's, contradicting the fixture note that 'the translator never folds'. The same asymmetry exists in translate() itself between JS/Python (non-folding optimiser pre-pass) and PHP/Lisp/C++ (none): `FILTER(TRUE)` is dropped by two hosts and rendered `WHERE TRUE` by three. Pre-existing, but the commit's guardrail suite cannot see it and the SQL fuzzer generates no `.>` pipelines and no bindings.

Reported repro:

```
scratchpad/runners-generator-tests/ts-* probes on `ORDERS .> FILTER(_["id"] > 1 + 1) .> FILTER(_["id"] < 9) .> SORT_BY(_["id"]) .> TAKE(2 * 2)` (postgresql): JS/PY/PHP/CPP translate and translate_statement all => `WHERE ("o"."id" > (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC))) AND ("o"."id" < 9) ORDER BY "o"."id" ASC LIMIT 4`; LISP translate => same; LISP translate-statement => `WHERE (("o"."id" > 2) AND ("o"."id" < 9)) ORDER BY "o"."id" ASC LIMIT 4`. scratchpad xlate.sh 'ORDERS .> FILTER(TRUE) .> TAKE(1)': JS/PY `SELECT "o".* FROM "orders" "o" LIMIT 1`; PHP/LISP/CPP `... WHERE TRUE LIMIT 1`. tools/fuzz-sql.sh 300 7 => 0 disagreements (tools/gen-programs.mjs emits no pipelines; js/bin/sqlfuzz.mjs:53 translates without bindings).
```

### [promises-vs-code] translate_statement applies three different optimisation policies (Lisp folds and fuses, JS/Python rewrite pipelines only, PHP/C++ nothing), so the full-delegation lane emits different SQL per host

*coherence · medium · hosts: lisp, js, python, php, cpp*

Locations: `lisp/src/sql/translator.lisp:2380`; `js/src/sql/translator.mjs:173`; `python/sel/sql/translator.py:189`; `php/src/Sql/Translator.php:94`; `cpp/sel_sql.cpp:197`

Pre-existing, but the commit's new notes state it as host-neutral fact: sql/cases/05-conditionals.sqlt 'The translator never folds', 02-operators 'The translator does not fold', §12.1 and plan.fold.compound-branch-stays-an-if 'the CASE WHEN TRUE … translate() has always produced'. Lisp translate-statement runs the full optimize-ast-logical (constant folding + FILTER fusion + pipeline rewrites); JS and Python run optimizeAstLogical with foldConstants:false/fuseFilters:false (pipeline rewrites, including FILTER-through-MAP pushdown); PHP and C++ run no optimiser. tools/fuzz-sql.sh only fuzzes translate() expressions, so this is not caught. It also means the EXTENDING 'Order' and 'Options' rules ('one options object forwarded to both') hold for the planner but the statement translator each host wraps is not the same function.

Reported repro:

```
TR=1 ./run.sh cases3.txt (mariadb), translate_statement column: 'ORDERS .> FILTER(_["id"] > 1 + 1)' -> js/py/php/cpp `WHERE (o.id > (1 + 1))`, lisp `WHERE (o.id > 2)`. 'ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9)' -> js/py/php/cpp `WHERE (o.id > 1) AND (o.id < 9)`, lisp `WHERE ((o.id > 1) AND (o.id < 9))`. 'ORDERS .> MAP(RECORD("id", _["id"], "h", _["id"] + 1)) .> FILTER(_["id"] > 0)' -> js/py/lisp `SELECT o.id AS id, (o.id + 1) AS h FROM orders o WHERE (o.id > 0)`, php/cpp `SELECT _sub1.* FROM (SELECT ...) _sub1 WHERE (CASE WHEN (_sub1.id REGEXP ...) ...)`. cases2: 'ORDERS .> FILTER(IF(TRUE, 1 + 1, 2) == _["id"]) .> TAKE(1)' -> four hosts `CASE WHEN TRUE THEN (1 + 1) ELSE 2 END`, lisp `2`.
```

### [probe-lanes-fold] translate_statement lane: Lisp folds constants (with the new position stamping), JS/Python run the optimiser without folding, PHP/C++ run none — four different answers for the same program

*correctness · high · hosts: lisp, js, python, php, cpp*

Locations: `lisp/src/sql/translator.lisp:2380-2382 (translate-statement calls sel:optimize-ast-logical with no fold/fuse switch)`; `js/src/sql/translator.mjs:173-174 (translateStatement: optimizeAstLogical with foldConstants:false, fuseFilters:false)`; `python/sel/sql/translator.py:189-191 (same as JS)`; `php/src/Sql/Translator.php:94-106 (translateStatement: Normalise::run only, no optimiser)`; `cpp/sel_sql_translator.cpp:148-165 (translate_statement: normalise only)`; `lisp/src/optimizer.lisp:37-40,161-165 (hoist-literal stamps the IF position, which is what now surfaces in Lisp's translate_statement refusals)`

The commit's fold/position rules were transplanted into every optimiser, but only Lisp's translate-statement runs that optimiser (unconditionally, folding included). So the SQL-full-delegation lane for pipelines disagrees across hosts on SQL text, on accept-vs-refuse, and on refusal code/position — and the position half is NEW with this commit: at the parent commit Lisp reported E_SQL_SHAPE@1:46 (the NULL literal's own column) for the first program below, at ed16df2 it reports 1:37 (the IF, via hoist-literal), while the four hosts that do not fold still say 1:46. The CHANGELOG says 'translate() refuses the same source at the same column' and cond.refuse.constant-condition-mismatch-is-at-the-if says 'the translator never folds'; that is true of translate() only. None of this is visible to the shared fixtures because every sqlt runner calls translate() (js/bin/sqlt.mjs:193, lisp/bin/sqlt.lisp:213) — translate_statement has zero shared cases, so the lane is pinned in no host.

Reported repro:

```
Per-host translate_statement(mariadb) with ORDERS bound to orders(o){ID NUM,...}:
(1) ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"])  →  JS E_SQL_SHAPE@1:46 | PY E_SQL_SHAPE@1:46 | PHP E_SQL_SHAPE@1:46 | CPP E_SQL_SHAPE@1:46 | LISP E_SQL_SHAPE@1:37 (parent commit 8fe0e3a: LISP 1:46)
(2) ORDERS .> TAKE(2) .> MAP(IF(TRUE, "x", 1) >= _["id"])  →  JS/PY/PHP/CPP E_SQL_SHAPE@1:26 | LISP E_SQL_INVALID@1:26
(3) ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])  →  JS/PY/PHP/CPP SELECT `o`.* FROM `orders` `o` WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= `o`.`id`) | LISP ... WHERE (2 >= `o`.`id`)
(4) ORDERS .> FILTER(IF(FALSE, "x", TRUE))  →  JS/PY/PHP/CPP E_SQL_SHAPE@1:18 | LISP SELECT `o`.* FROM `orders` `o` (accepted)
(5) ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > IF(TRUE, 1, "x")) .> MAP(RECORD("cid", _K))  →  JS/PY/PHP/CPP E_SQL_SHAPE@1:57 | LISP SELECT `o`.`customer_id` AS `cid` FROM `orders` `o` GROUP BY `o`.`customer_id` HAVING (COUNT(*) > 1)
(6) ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 3)  →  JS/PY/PHP/CPP ... WHERE (`o`.`id` > 1) AND (`o`.`id` < 3) | LISP ... WHERE ((`o`.`id` > 1) AND (`o`.`id` < 3))
(7) ORDERS .> FILTER(TRUE)  →  JS/PY SELECT `o`.* FROM `orders` `o` | PHP/CPP ... WHERE TRUE | LISP SELECT `o`.* FROM `orders` `o`
In the same runs translate() and plan_hybrid agreed across hosts for (1)-(7) (plan lane folds in every host by design). Harness: scratchpad probe-lanes-fold/{probe.mjs,probe.py,probe.php,probe.cpp,probe.lisp} over corpus.txt/corpus3.txt/corpus5.txt; parent-commit comparison via probe-old.lisp in a temporary worktree of 8fe0e3a.
```

### [probe-lanes-fold] translate() lane: JS and Python apply the logical rewrites (sort/select_cols pushdown, redundant-sort and FILTER(TRUE) elimination) before translating; PHP, C++ and Lisp do not — different SQL for ordinary pipelines

*coherence · high · hosts: js, python, php, cpp, lisp*

Locations: `js/src/sql/translator.mjs:119,149-150 (translate: optimizeAstLogical unless optimizeSql===false)`; `python/sel/sql/translator.py:149,165-167`; `php/src/Sql/Translator.php:70 (translate: no optimiser)`; `cpp/sel_sql_translator.cpp:115 (translate: no optimiser)`; `lisp/src/sql/translator.lisp:2339-2360 (translate: no optimiser)`

Pre-existing, but it is the other half of the previous finding and the diff's contract text ('It plans the tree the translator will see') presumes one translator behaviour. The shared sqlt corpus contains no shape where a rewrite fires, so five hosts agree on the fixtures and disagree on the first SORT_BY .> FILTER a user writes. The predicate-pushdown rewrite also changes correctness-relevant structure (a WHERE inside vs outside a derived table decides whether the numeric guard is emitted).

Reported repro:

```
translate(mariadb), ORDERS bound as above:
ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1)  →  JS/PY: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1) ORDER BY `o`.`id` ASC | PHP/CPP: SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` ORDER BY `o`.`id` ASC) `_sub1` WHERE (CASE WHEN (`_sub1`.`id` REGEXP ...) THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) | LISP: SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` ORDER BY `o`.`id` ASC) `_sub1` WHERE (`_sub1`.`id` > 1)
ORDERS .> SELECT_COLS("id") .> FILTER(_["id"] > 1)  →  JS/PY/LISP: SELECT `o`.`id` FROM `orders` `o` WHERE (`o`.`id` > 1) | PHP/CPP: SELECT `_sub1`.* FROM (SELECT `o`.`id` FROM `orders` `o`) `_sub1` WHERE (CASE WHEN (`_sub1`.`id` REGEXP ...) ...)
ORDERS .> FILTER(TRUE) .> TAKE(1)  →  JS/PY: SELECT `o`.* FROM `orders` `o` LIMIT 1 | PHP/CPP/LISP: ... WHERE TRUE LIMIT 1
(corpus5.txt, out5-*.txt in the scratchpad)
```

### [probe-lanes-bucket-hybrid] Lisp translate-statement runs the logical optimiser with folding and filter fusion on; the other hosts run it with both off

*coherence · medium · hosts: lisp*

Locations: `lisp/src/sql/translator.lisp:2382`; `python/sel/sql/translator.py:189`; `js/src/sql/translator.mjs:173`; `docs/SQL-TRANSLATION.md:2296`

translate-statement in Lisp calls (sel:optimize-ast-logical ast) with default options, so constants fold and adjacent FILTERs fuse before translation; Python and JS call optimize_ast_logical with {foldConstants: false, fuseFilters: false} and C++/PHP do not optimise at all, and those four agree byte-for-byte. The SQL text for the same call therefore differs (a folded literal instead of CASE WHEN TRUE, `5` instead of `(2 + 3)`, `((a) AND (b))` instead of `(a) AND (b)`), and so does the refusal code for a constant-condition IF with mismatched branches (Lisp folds first and reports E_SQL_INVALID; the others render the IF and report E_SQL_SHAPE). §12.1 states the planner folds "unlike translate()"; Lisp's statement translator does not honour that line, and sql/cases only pins the fold-position claim through translate(), not translate_statement. A future dialect entry or fixture that depends on the unfused WHERE shape, or a fuzz-sql seed generating IF(TRUE, …) inside a pipeline, will fail on Lisp alone.

Reported repro:

```
translate_statement, mariadb (identical in all three dialects):
d01 ORDERS .> FILTER(IF(TRUE, 2, 1) <= _["amount"])  py/js/php/cpp => ... WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END <= `o`.`amount`); lisp => ... WHERE (2 <= `o`.`amount`)
d02 ORDERS .> FILTER(IF(TRUE, "x", 1) <= _["amount"])  py/js/php/cpp => REFUSED E_SQL_SHAPE@1:18; lisp => REFUSED E_SQL_INVALID@1:18
d03 ORDERS .> FILTER((FALSE AND TRUE) OR _["amount"] > 6)  four => WHERE ((FALSE AND TRUE) OR (...)); lisp => WHERE (FALSE OR (...))
d06 ORDERS .> FILTER(_["amount"] > 2 + 3)  four => WHERE (`o`.`amount` > (2 + 3)); lisp => WHERE (`o`.`amount` > 5)
c11 ORDERS .> FILTER(_["amount"] > 1) .> FILTER(_["amount"] < 9)  four => WHERE (`o`.`amount` > 1) AND (`o`.`amount` < 9); lisp => WHERE ((`o`.`amount` > 1) AND (`o`.`amount` < 9)) (b07, b48 likewise). Command: python3 <scratchpad>/probe-lanes/compare.py py js php cpp lisp.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$D/corpus.txt`**

```
ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])
ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9)
ORDERS .> FILTER(TRUE)
ORDERS .> FILTER(_["id"] > 1 + 1)
ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1)
ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"])
ORDERS .> FILTER(IF(FALSE, "x", TRUE))
ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))
ORDERS .> TAKE(3) .> MAP(RECORD("id", _["id"], "k", 10 * 10))
ORDERS .> FILTER(NOT FALSE AND _["id"] < 5)
ORDERS .> SELECT_COLS("id") .> FILTER(_["id"] > 1)
ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"])
```

**`$D/probe.mjs`**

```js
import { readFileSync } from 'node:fs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const B = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
const lines = readFileSync(process.argv[2], 'utf8').split('\n').filter(Boolean);
for (const src of lines) {
  for (const lane of ['translate', 'translateStatement']) {
    let out;
    try { const f = Sql[lane](compile(src), 'postgresql', B); out = f.kind === 'STATEMENT' ? f.asStatement() : f.asValue(); }
    catch (e) { out = `ERR ${e.code ?? e.name}@${e.line}:${e.col}`; }
    console.log(`${lane === 'translate' ? 'T ' : 'TS'} | ${src} | ${out}`);
  }
}
```

**`$D/probe.py`**

```python
import sys
sys.path.insert(0, '/home/nathan/workspaces/nth-share/sel/python')
from sel import compile as c
from sel.sql import Sql, Binding
B = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}
for src in [l for l in open(sys.argv[1]).read().split('\n') if l]:
    for lane, fn in (('T ', Sql.translate), ('TS', Sql.translate_statement)):
        try:
            f = fn(c(src), 'postgresql', B)
            out = f.as_statement() if f.kind == 'STATEMENT' else f.as_value()
        except Exception as e:
            out = f"ERR {getattr(e,'code',type(e).__name__)}@{getattr(e,'line','?')}:{getattr(e,'col','?')}"
        print(f"{lane} | {src} | {out}")
```

**`$D/probe.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$B = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
foreach (array_filter(explode("\n", file_get_contents($argv[1]))) as $src) {
  foreach (['T ' => 'translate', 'TS' => 'translateStatement'] as $lane => $fn) {
    try { $f = Sql::$fn(Sel::compile($src), 'postgresql', $B); $out = $f->kind === 'STATEMENT' ? $f->asStatement() : $f->asValue(); }
    catch (\Throwable $e) { $out = "ERR " . ($e->code ?? get_class($e)) . "@" . ($e->line ?? '?') . ":" . ($e->col ?? '?'); }
    echo "$lane | $src | $out\n";
  }
}
```

**`$D/probe.cpp`**

```cpp
#include "sel.hpp"
#include "sel_sql.hpp"
#include <fstream>
#include <iostream>
#include <string>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main(int argc, char** argv) {
  Bindings B({{"ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::string("o"), SqlKind::Num)}})}});
  std::ifstream in(argv[1]); std::string src;
  while (std::getline(in, src)) {
    if (src.empty()) continue;
    for (int lane = 0; lane < 2; ++lane) {
      std::string out;
      try {
        auto p = sel::compile(src);
        auto f = lane == 0 ? Sql::translate(p, "postgresql", B) : Sql::translate_statement(p, "postgresql", B);
        out = f.kind() == sel::sql::FragmentKind::Statement ? f.as_statement() : f.as_value();
      } catch (const sel::sql::SqlError& e) { out = "ERR " + e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
        catch (const sel::SelError& e) { out = std::string("ERR ") + e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
        catch (const std::exception& e) { out = std::string("ERR ") + e.what(); }
      std::cout << (lane == 0 ? "T " : "TS") << " | " << src << " | " << out << "\n";
    }
  }
}
```

**`$D/probe.lisp`**

```lisp
(defun main (path)
  (let ((b (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o" (list (cons "ID" (sel.sql:binding-column "id" "o" :num))))))))
    (with-open-file (in path)
      (loop for src = (read-line in nil) while src
            unless (zerop (length src)) do
              (dolist (lane '("T " "TS"))
                (let ((out (handler-case
                               (let* ((p (sel:compile-source src))
                                      (f (if (string= lane "T ")
                                             (sel.sql:translate p "postgresql" b)
                                             (sel.sql:translate-statement p "postgresql" b))))
                                 (if (eq (sel.sql:fragment-kind f) :statement)
                                     (sel.sql:as-statement f)
                                     (sel.sql:as-value f)))
                             (sel.sql:sql-error (e) (format nil "ERR ~a@~a:~a" (sel.sql:sql-error-code e) (sel.sql:sql-error-line e) (sel.sql:sql-error-col e)))
                             (error (e) (format nil "ERR ~a" e)))))
                  (format t "~a | ~a | ~a~%" lane src out)))))))
```

**`$D/hyb.mjs`**

```js
import { readFileSync } from 'node:fs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const B = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
for (const src of readFileSync(process.argv[2], 'utf8').split('\n').filter(Boolean)) {
  let out;
  try { const p = Sql.planHybrid(compile(src), 'postgresql', B); out = (p.pureSql ? 'SQL ' : p.pureMemory ? 'MEM ' : 'HYB ') + (p.sqlStatement ? p.sqlStatement.asStatement() : '-'); }
  catch (e) { out = `ERR ${e.code ?? e.name}@${e.line}:${e.col}`; }
  console.log(`H | ${src} | ${out}`);
}
```

**`$D/hyb.lisp`**

```lisp
(defun main (path)
  (let ((b (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o" (list (cons "ID" (sel.sql:binding-column "id" "o" :num))))))))
    (with-open-file (in path)
      (loop for src = (read-line in nil) while src
            unless (zerop (length src)) do
              (let ((out (handler-case
                             (let* ((p (sel.sql:plan-hybrid (sel:compile-source src) "postgresql" b))
                                    (s (sel.sql:hybrid-plan-sql-statement p)))
                               (format nil "~a~a" (cond ((sel.sql:hybrid-plan-pure-sql p) "SQL ") ((sel.sql:hybrid-plan-pure-memory p) "MEM ") (t "HYB "))
                                       (if s (sel.sql:as-statement s) "-")))
                           (error (e) (format nil "ERR ~a" e)))))
                (format t "H | ~a | ~a~%" src out))))))
```

**`$D/hyb.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$B = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
foreach (array_filter(explode("\n", file_get_contents($argv[1]))) as $src) {
  try { $p = Sql::planHybrid(Sel::compile($src), 'postgresql', $B); $out = ($p->pureSql ? 'SQL ' : ($p->pureMemory ? 'MEM ' : 'HYB ')) . ($p->sqlStatement ? $p->sqlStatement->asStatement() : '-'); }
  catch (\Throwable $e) { $out = "ERR " . ($e->code ?? get_class($e)) . "@" . ($e->line ?? '?') . ":" . ($e->col ?? '?'); }
  echo "H | $src | $out\n";
}
```

### Commands run and their output

Check whether the optimiser wiring in translators predates the commit

```bash
for f in lisp/src/sql/translator.lisp js/src/sql/translator.mjs python/sel/sql/translator.py php/src/Sql/Translator.php; do echo "=== $f"; git diff 8fe0e3a ed16df2 -- $f | grep -n "optimize\|optimise\|Optimi" | head; done; echo; echo "== old lisp translate-statement"; git show 8fe0e3a:lisp/src/sql/translator.lisp | grep -n "defun translate-statement" -A 18; echo "== old js"; git show 8fe0e3a:js/src/sql/translator.mjs | grep -n "optimizeAstLogical\|this.optimize" ; echo "== old py"; git show 8fe0e3a:python/sel/sql/translator.py | grep -n "optimize_ast_logical\|self.optimize"
```

```
=== lisp/src/sql/translator.lisp
13:+;; The optimizer's list, not a second copy: one vocabulary of pipeline
=== js/src/sql/translator.mjs
9:-import { optimizeAstLogical } from '../optimizer.mjs';
10:+import { optimizeAstLogical, PIPELINE_OPS as OPTIMIZER_PIPELINE_OPS } from '../optimizer.mjs';
21:+// The optimiser's list, not a second copy: one vocabulary of pipeline operators
=== python/sel/sql/translator.py
9:-from ..optimizer import optimize_ast_logical
10:+from ..optimizer import PIPELINE_OPS as OPTIMIZER_PIPELINE_OPS, optimize_ast_logical
15:+# The optimiser's list, not a second copy: one vocabulary of pipeline operators
=== php/src/Sql/Translator.php
15:+     * The optimiser's list, not a second copy: one vocabulary of pipeline
18:+    public const PIPELINE_OPS = \Sel\Optimizer::PIPELINE_OPS;

== old lisp translate-statement
2335:(defun translate-statement (program dialect &optional bindings options)
2336-  "Translate a compiled relational program into a SQL statement (SELECT ...)."
2337-  (require-target dialect)
2338-  (let* ((b (make-bindings (or bindings '())))
2339-         (tr (%translator dialect b (and (getf options :strict) t))))
2340-    (bindings-check-aliases (translator-bindings tr))
2341-    (multiple-value-bind (names root) (const-scope (translator-bindings tr))
2342-      (setf (translator-const-names tr) names
2343-            (translator-const-root tr) root)
2344-      (let* ((*subquery-counter* 0)
2345-             (ast (if (getf options :no-optimize)
2346-                      (sel:program-ast program)
2347-                      (sel:optimize-ast-logical (sel:program-ast program))))
2348-             (norm (normalise ast names root))
2349-             (plan (analyze-pipeline tr norm)))
2350-        (unless plan
2351-          (refuse "E_SQL_SHAPE" "expected a relational query or pipeline"))
2352-        (compile-statement tr plan)))))
2353-
== old js
26:import { optimizeAstLogical } from '../optimizer.mjs';
119:    this.optimize = (options ?? {}).optimizeSql !== false
149:    const sourceAst = this.optimize
150:      ? optimizeAstLogical(ast, { foldConstants: false, fuseFilters: false }) : ast;
173:    const sourceAst = this.optimize
174:      ? optimizeAstLogical(ast, { foldConstants: false, fuseFilters: false }) : ast;
== old py
33:from ..optimizer import optimize_ast_logical
149:        self.optimize = bool((options or {}).get('optimizeSql', True)
165:        source_ast = (optimize_ast_logical(ast, {'foldConstants': False,
167:                      if self.optimize else ast)
189:        source_ast = (optimize_ast_logical(ast, {'foldConstants': False,
191:                      if self.optimize else ast)
```

Write probe harnesses and check Fragment kind APIs

```bash
D=$SCRATCH/verify-C && mkdir -p $D && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
grep -n "kind()\|FragmentKind\|enum class" cpp/sel_sql.hpp | head; grep -n "fragment-kind\|:statement" lisp/src/sql/fragment.lisp 2>/dev/null | head -5; ls lisp/src/sql/
```

```
72:enum class SqlKind { Num, Text, Bool, Bin, Unknown, List, Statement };
81:enum class Mode { Inline, Params, Debug };
135:  SqlKind kind() const { return kind_; }
259:  enum class Kind { Column, Columns, Relation, Value };
305:  Kind kind() const { return kind_; }
11:(defparameter +kinds+ '(:num :text :bool :bin :unknown :list :statement))
93:  (when (eq (fragment-kind f) :list)
96:  (when (eq (fragment-kind f) :statement)
103:  (unless (eq (fragment-kind f) :statement)
106:                    (kind-name (fragment-kind f)))))
binding.lisp
emit.lisp
errors.lisp
fragment.lisp
hybrid.lisp
map-data.lisp
map.lisp
package.lisp
relational-plan.lisp
stage1.lisp
translator.lisp
```

Check C++ build flags and Lisp exports

```bash
D=$SCRATCH/verify-C && sed -i 's/sel::sql::FragmentKind::Statement/SqlKind::Statement/' $D/probe.cpp && grep -n "^CXX\|^CXXFLAGS\|^LDFLAGS\|sql_unit" cpp/Makefile | head; ls cpp/build/*.o | head -20; grep -n "export" lisp/src/sql/package.lisp | head -3; grep -n "fragment-kind\|as-statement\|as-value\|sql-error-line" lisp/src/sql/package.lisp
```

```
11:CXX      ?= c++
12:CXXFLAGS ?= -std=c++23 -O2 -Wall -Wextra -Wpedantic
113:$(BUILD)/sqlunit: tests/sql_unit.cpp $(SQL_HDRS) sel.hpp $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
cpp/build/case_data.o
cpp/build/map_replay.o
cpp/build/sel.o
cpp/build/sel_sql_binding.o
cpp/build/sel_sql_emit.o
cpp/build/sel_sql_hybrid.o
cpp/build/sel_sql_map_data.o
cpp/build/sel_sql_map.o
cpp/build/sel_sql_node.o
cpp/build/sel_sql.o
cpp/build/sel_sql_stage1.o
cpp/build/sel_sql_translator.o
14:  (:export
19:   #:sql-error-line
26:   #:fragment-kind
37:   #:as-value
39:   #:as-statement
```

Compile the C++ probe against built objects

```bash
D=$SCRATCH/verify-C && sed -n 105,120p cpp/Makefile; ls -la cpp/build/sel.o cpp/sel.cpp cpp/sel_sql_translator.cpp | head; c++ -std=c++23 -O1 -Icpp $D/probe.cpp cpp/build/sel.o cpp/build/sel_sql*.o -o $D/probe_cpp 2>&1 | head -20
```

```
$(CXX) $(CXXFLAGS) -o $@ $< $(BUILD)/map_replay.o $(SQL_OBJS) $(BUILD)/sel.o

$(BUILD)/sqlt: bin/sqlt.cpp bin/case_data.hpp $(SQL_HDRS) sel.hpp $(BUILD)/case_data.o $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $< $(BUILD)/case_data.o $(SQL_OBJS) $(BUILD)/sel.o

$(BUILD)/unit: tests/unit.cpp sel.cpp sel.hpp sel_ast.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $<

$(BUILD)/sqlunit: tests/sql_unit.cpp $(SQL_HDRS) sel.hpp $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $< $(SQL_OBJS) $(BUILD)/sel.o

# The scale runner includes sel.cpp directly because it also exercises the
# public evaluator and benchmark-only builtin registration in one translation
# unit; link only the SQL layer to avoid a duplicate evaluator definition.
$(BUILD)/scale-bench: ../tools/scale-test/sel_benchmarks.cpp sel.cpp sel.hpp sel_ast.hpp $(SQL_HDRS) $(SQL_OBJS) | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $< $(SQL_OBJS)
-rw-r--r-- 1 nathan nathan 1100472 Sep 14 11:41 cpp/build/sel.o
-rw-r--r-- 1 nathan nathan  226036 Sep 14 11:41 cpp/sel.cpp
-rw-r--r-- 1 nathan nathan  118764 Sep 14 09:07 cpp/sel_sql_translator.cpp
```

Run the probe in all five hosts

```bash
D=$SCRATCH/verify-C && node $D/probe.mjs $D/corpus.txt > $D/out-js.txt 2>&1; python3 $D/probe.py $D/corpus.txt > $D/out-py.txt 2>&1; php $D/probe.php $D/corpus.txt > $D/out-php.txt 2>&1; $D/probe_cpp $D/corpus.txt > $D/out-cpp.txt 2>&1; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/probe.lisp --eval "(main \"$D/corpus.txt\")" > $D/out-lisp.txt 2>&1; wc -l $D/out-*.txt; cat $D/out-js.txt
```

```
24 $SCRATCH/verify-C/out-cpp.txt
   24 $SCRATCH/verify-C/out-js.txt
   28 $SCRATCH/verify-C/out-lisp.txt
   24 $SCRATCH/verify-C/out-php.txt
   24 $SCRATCH/verify-C/out-py.txt
  124 total
T  | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
TS | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
T  | ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) AND ("o"."id" < 9)
TS | ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) AND ("o"."id" < 9)
T  | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o"
TS | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o"
T  | ORDERS .> FILTER(_["id"] > 1 + 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC)))
TS | ORDERS .> FILTER(_["id"] > 1 + 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC)))
T  | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY "o"."id" ASC
TS | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY "o"."id" ASC
T  | ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"]) | ERR E_SQL_SHAPE@1:18
TS | ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"]) | ERR E_SQL_SHAPE@1:18
T  | ORDERS .> FILTER(IF(FALSE, "x", TRUE)) | ERR E_SQL_SHAPE@1:18
TS | ORDERS .> FILTER(IF(FALSE, "x", TRUE)) | ERR E_SQL_SHAPE@1:18
T  | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) | SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
TS | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) | SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
T  | ORDERS .> TAKE(3) .> MAP(RECORD("id", _["id"], "k", 10 * 10)) | SELECT "_sub1"."id" AS "id", (CAST(10 AS NUMERIC) * CAST(10 AS NUMERIC)) AS "k" FROM (SELECT "o".* FROM "orders" "o" LIMIT 3) "_sub1"
TS | ORDERS .> TAKE(3) .> MAP(RECORD("id", _["id"], "k", 10 * 10)) | SELECT "_sub1"."id" AS "id", (CAST(10 AS NUMERIC) * CAST(10 AS NUMERIC)) AS "k" FROM (SELECT "o".* FROM "orders" "o" LIMIT 3) "_sub1"
T  | ORDERS .> FILTER(NOT FALSE AND _["id"] < 5) | SELECT "o".* FROM "orders" "o" WHERE ((NOT FALSE) AND ("o"."id" < 5))
TS | ORDERS .> FILTER(NOT FALSE AND _["id"] < 5) | SELECT "o".* FROM "orders" "o" WHERE ((NOT FALSE) AND ("o"."id" < 5))
T  | ORDERS .> SELECT_COLS("id") .> FILTER(_["id"] > 1) | SELECT "o"."id" FROM "orders" "o" WHERE ("o"."id" > 1)
TS | ORDERS .> SELECT_COLS("id") .> FILTER(_["id"] > 1) | SELECT "o"."id" FROM "orders" "o" WHERE ("o"."id" > 1)
T  | ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"]) | ERR E_SQL_SHAPE@1:46
TS | ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"]) | ERR E_SQL_SHAPE@1:46
```

Diff the five hosts' outputs against JS

```bash
D=$SCRATCH/verify-C && cd $D && grep -v "^T \|^TS" out-lisp.txt; grep "^T \|^TS" out-lisp.txt > out-lisp-clean.txt; for h in py php cpp lisp-clean; do echo "===== js vs $h"; diff out-js.txt out-$h.txt; done
```

```
Exit code 1
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=733966 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 2, Column: 57, File-Position: 75

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-C/probe.lisp" {1201ED65C3}>

Backtrace for: #<SB-THREAD:THREAD tid=733966 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201ED77E3}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-C/probe.lisp" {1201ED65C3}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {736376DBF4CB}> #<SB-C::SOURCE-INFO {1201E77813}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-C/probe.lisp" {1201ED65C3}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-C/probe.lisp" {1201ED65C3}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {736376DBF88B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-C/probe.lisp" {1201ED65C3}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-C/probe.lisp" {1201ED65C3}>)
9: (LOAD #P"$SCRATCH/verify-C/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-C/probe.lisp") (:EVAL . "(main \"$SCRATCH/verify-C/corpus.txt\")") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
===== js vs py
===== js vs php
5,6c5,6
< T  | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o"
< TS | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o"
---
> T  | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o" WHERE TRUE
> TS | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o" WHERE TRUE
9,10c9,10
< T  | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY "o"."id" ASC
< TS | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY "o"."id" ASC
---
> T  | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)
> TS | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)
21,22c21,22
< T  | ORDERS .> SELECT_COLS("id") .> FILTE
… (truncated)
```

Inspect Lisp bootstrap

```bash
cat lisp/bin/boot.lisp; head -20 lisp/bin/sqlt.lisp
```

```
;;;; Shared preamble for the scripts in this directory.
;;;;
;;;; They run under `sbcl --non-interactive`, which does not read ~/.sbclrc, so
;;;; Quicklisp is loaded explicitly. The system itself is found through
;;;; asdf:*central-registry* rather than by being installed, so a checkout runs
;;;; without being registered anywhere.

(require :asdf)

(let ((setup (merge-pathnames "quicklisp/setup.lisp" (user-homedir-pathname))))
  (if (probe-file setup)
      (load setup)
      (progn
        (format *error-output*
                "~&Quicklisp not found at ~a.~%~
                 The Lisp implementation depends on cl-ppcre; install Quicklisp~%~
                 (https://www.quicklisp.org) and run (ql:quickload :cl-ppcre).~%"
                setup)
        (sb-ext:exit :code 2))))

;;; This file lives in lisp/bin/, so the system definition is one directory up.
(push (truename (merge-pathnames "../" (directory-namestring *load-truename*)))
      asdf:*central-registry*)

(handler-case
    (let ((*standard-output* (make-broadcast-stream)))   ; quiet the build chatter
      (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang))
  (error (e)
    (format *error-output* "~&cannot load the SEL system: ~a~%" e)
    (sb-ext:exit :code 2)))

(defpackage #:sel-cli
  (:use #:common-lisp)
  (:export #:script-args #:read-text-file #:starts-with #:trim-ws #:split-lines
           #:join-lines #:main))

(in-package #:sel-cli)

(defun script-args ()
  "The arguments after --end-toplevel-options, which is how the wrappers pass
them through SBCL's own option parsing. SBCL leaves the marker in *posix-argv*
on some versions and removes it on others, so handle both."
  (let ((argv sb-ext:*posix-argv*))
    (let ((marker (member "--end-toplevel-options" argv :test #'string=)))
      (if marker
          (rest marker)
          ;; No marker left: everything after the last option SBCL understands is
          ;; ours. The wrappers only ever pass file paths and --show.
          (remove-if (lambda (a)
                       (or (string= a "--noinform")
                           (string= a "--disable-debugger")
                           (string= a "--non-interactive")
                           (string= a "--load")
                           (string= a "--eval")
                           (search ".lisp" a)
                           (search "(sel-cli:main)" a)))
                     (rest argv))))))

(defun read-text-file (path)
  (with-open-file (in path :external-format :utf-8)
    (let ((s (make-string (file-length in))))
      (subseq s 0 (read-sequence s in)))))

;;; --- small text helpers shared by the scripts ------------------------------

(defun starts-with (prefix s)
  (and (>= (length s) (length prefix)) (string= prefix s :end2 (length prefix))))

(defun trim-ws (s) (string-trim '(#\Space #\Tab #\Return #\Newline) s))

(defun split-lines (text)
  (let ((lines '())
        (start 0))
    (loop for i from 0 below (length text)
          when (char= (char text i) #\Newline)
            do (push (subseq text start i) lines)
               (setf start (1+ i)))
    (push (subseq text start) lines)
    (nreverse lines)))

(defun join-lines (reversed-lines)
  "Joins lines that were accumulated with PUSH, so in reverse order."
  (format nil "~{~a~^~%~}" (reverse reversed-lines)))
;;;; The SEL->SQL conformance suite for the Common Lisp host.
;;;;
;;;;   lisp/bin/sqlt                 every case
;;;;   lisp/bin/sqlt bind. agg.      only cases whose name contains one 
… (truncated)
```

Run the Lisp probe with the SQL system loaded

```bash
D=$SCRATCH/verify-C && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $D/probe.lisp --eval "(main \"$D/corpus.txt\")" > $D/out-lisp.txt 2>&1; grep "^T \|^TS" $D/out-lisp.txt > $D/out-lisp-clean.txt; grep -v "^T \|^TS" $D/out-lisp.txt | head -5; echo "===== js vs lisp"; diff $D/out-js.txt $D/out-lisp-clean.txt
```

```
===== js vs lisp
2c2
< TS | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
---
> TS | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) | SELECT "o".* FROM "orders" "o" WHERE (2 >= "o"."id")
4,5c4,5
< TS | ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) AND ("o"."id" < 9)
< T  | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o"
---
> TS | ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9) | SELECT "o".* FROM "orders" "o" WHERE (("o"."id" > 1) AND ("o"."id" < 9))
> T  | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o" WHERE TRUE
8,9c8,9
< TS | ORDERS .> FILTER(_["id"] > 1 + 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC)))
< T  | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY "o"."id" ASC
---
> TS | ORDERS .> FILTER(_["id"] > 1 + 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 2)
> T  | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC) "_sub1" WHERE ("_sub1"."id" > 1)
12c12
< TS | ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"]) | ERR E_SQL_SHAPE@1:18
---
> TS | ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"]) | ERR E_SQL_INVALID@1:18
14c14
< TS | ORDERS .> FILTER(IF(FALSE, "x", TRUE)) | ERR E_SQL_SHAPE@1:18
---
> TS | ORDERS .> FILTER(IF(FALSE, "x", TRUE)) | SELECT "o".* FROM "orders" "o"
16c16
< TS | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) | SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
---
> TS | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) | ERR E_SQL_UNBOUND@1:19
18c18
< TS | ORDERS .> TAKE(3) .> MAP(RECORD("id", _["id"], "k", 10 * 10)) | SELECT "_sub1"."id" AS "id", (CAST(10 AS NUMERIC) * CAST(10 AS NUMERIC)) AS "k" FROM (SELECT "o".* FROM "orders" "o" LIMIT 3) "_sub1"
---
> TS | ORDERS .> TAKE(3) .> MAP(RECORD("id", _["id"], "k", 10 * 10)) | SELECT "_sub1"."id" AS "id", 100 AS "k" FROM (SELECT "o".* FROM "orders" "o" LIMIT 3) "_sub1"
20c20
< TS | ORDERS .> FILTER(NOT FALSE AND _["id"] < 5) | SELECT "o".* FROM "orders" "o" WHERE ((NOT FALSE) AND ("o"."id" < 5))
---
> TS | ORDERS .> FILTER(NOT FALSE AND _["id"] < 5) | SELECT "o".* FROM "orders" "o" WHERE (TRUE AND ("o"."id" < 5))
24c24
< TS | ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"]) | ERR E_SQL_SHAPE@1:46
---
> TS | ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"]) | ERR E_SQL_SHAPE@1:37
```

Show full Lisp and PHP outputs, and confirm cpp==php, js==py

```bash
D=$SCRATCH/verify-C && cat $D/out-lisp-clean.txt; echo; echo ==== PHP; cat $D/out-php.txt; echo ==== cpp-vs-php; diff $D/out-cpp.txt $D/out-php.txt && echo IDENTICAL; diff $D/out-js.txt $D/out-py.txt && echo JS-PY-IDENTICAL
```

```
T  | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
TS | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) | SELECT "o".* FROM "orders" "o" WHERE (2 >= "o"."id")
T  | ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) AND ("o"."id" < 9)
TS | ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9) | SELECT "o".* FROM "orders" "o" WHERE (("o"."id" > 1) AND ("o"."id" < 9))
T  | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o" WHERE TRUE
TS | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o"
T  | ORDERS .> FILTER(_["id"] > 1 + 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC)))
TS | ORDERS .> FILTER(_["id"] > 1 + 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 2)
T  | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC) "_sub1" WHERE ("_sub1"."id" > 1)
TS | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY "o"."id" ASC
T  | ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"]) | ERR E_SQL_SHAPE@1:18
TS | ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"]) | ERR E_SQL_INVALID@1:18
T  | ORDERS .> FILTER(IF(FALSE, "x", TRUE)) | ERR E_SQL_SHAPE@1:18
TS | ORDERS .> FILTER(IF(FALSE, "x", TRUE)) | SELECT "o".* FROM "orders" "o"
T  | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) | SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
TS | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) | ERR E_SQL_UNBOUND@1:19
T  | ORDERS .> TAKE(3) .> MAP(RECORD("id", _["id"], "k", 10 * 10)) | SELECT "_sub1"."id" AS "id", (CAST(10 AS NUMERIC) * CAST(10 AS NUMERIC)) AS "k" FROM (SELECT "o".* FROM "orders" "o" LIMIT 3) "_sub1"
TS | ORDERS .> TAKE(3) .> MAP(RECORD("id", _["id"], "k", 10 * 10)) | SELECT "_sub1"."id" AS "id", 100 AS "k" FROM (SELECT "o".* FROM "orders" "o" LIMIT 3) "_sub1"
T  | ORDERS .> FILTER(NOT FALSE AND _["id"] < 5) | SELECT "o".* FROM "orders" "o" WHERE ((NOT FALSE) AND ("o"."id" < 5))
TS | ORDERS .> FILTER(NOT FALSE AND _["id"] < 5) | SELECT "o".* FROM "orders" "o" WHERE (TRUE AND ("o"."id" < 5))
T  | ORDERS .> SELECT_COLS("id") .> FILTER(_["id"] > 1) | SELECT "o"."id" FROM "orders" "o" WHERE ("o"."id" > 1)
TS | ORDERS .> SELECT_COLS("id") .> FILTER(_["id"] > 1) | SELECT "o"."id" FROM "orders" "o" WHERE ("o"."id" > 1)
T  | ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"]) | ERR E_SQL_SHAPE@1:46
TS | ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"]) | ERR E_SQL_SHAPE@1:37

==== PHP
T  | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
TS | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
T  | ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) AND ("o"."id" < 9)
TS | ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) AND ("o"."id" < 9)
T  | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o" WHERE TRUE
TS | ORDERS .> FILTER(TRUE) | SELECT "o".* FROM "orders" "o" WHERE TRUE
T  | ORDERS .> FILTER(_["id"] > 1 + 1) | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC)))
… (truncated)
```

Run the Lisp probe against the parent commit in a scratch worktree

```bash
D=$SCRATCH/verify-C && git worktree list; git worktree add --detach $D/old 8fe0e3a 2>&1 | tail -1 && sbcl --noinform --disable-debugger --non-interactive --load $D/old/lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $D/probe.lisp --eval "(main \"$D/corpus.txt\")" 2>&1 | grep "^TS" > $D/out-lisp-old.txt; diff $D/out-lisp-old.txt <(grep "^TS" $D/out-lisp-clean.txt); echo "exit=$?"
```

```
/home/nathan/workspaces/nth-share/sel ed16df2 [main]
HEAD is now at 8fe0e3a wip
6c6
< TS | ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"]) | ERR E_SQL_INVALID@1:27
---
> TS | ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"]) | ERR E_SQL_INVALID@1:18
12c12
< TS | ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"]) | ERR E_SQL_SHAPE@1:46
---
> TS | ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"]) | ERR E_SQL_SHAPE@1:37
exit=1
```

Remove scratch worktree and find the fixture notes about folding

```bash
D=$SCRATCH/verify-C && git worktree remove --force $D/old && git worktree prune && git worktree list && git status --short | head; grep -n "never folds\|does not fold\|has always produced" sql/cases/*.sqlt docs/SQL-TRANSLATION.md | head
```

```
/home/nathan/workspaces/nth-share/sel ed16df2 [main]
sql/cases/05-conditionals.sqlt:70:The translator never folds, so `IF(TRUE, "x", 1)` is still the IF when its two
docs/SQL-TRANSLATION.md:1654:else: `IS TRUE` folds a NULL to false, but it does not fold a *number*, and
```

Probe the hybrid lane in JS and Lisp for the same corpus

```bash
D=$SCRATCH/verify-C && grep -n "hybrid-plan-sql-statement\|hybrid-plan-pure\|defun plan-hybrid" lisp/src/sql/hybrid.lisp | head -5; grep -n "sqlStatement\|pureSql\|pureMemory" js/src/sql/hybrid.mjs | head -5
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
node $D/hyb.mjs $D/corpus.txt > $D/hyb-js.txt; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $D/hyb.lisp --eval "(main \"$D/corpus.txt\")" 2>&1 | grep "^H " > $D/hyb-lisp.txt; cat $D/hyb-js.txt; echo ==== diff js lisp; diff $D/hyb-js.txt $D/hyb-lisp.txt
```

```
27:  (not (or (hybrid-plan-pure-sql-p plan) (hybrid-plan-pure-memory-p plan))))
64:(defun plan-hybrid (program dialect &optional bindings options)
333:    ((hybrid-plan-pure-sql-p plan)
334:     (let ((frag (hybrid-plan-sql-statement plan)))
336:    ((hybrid-plan-pure-memory-p plan)
33:  constructor({ dialect = null, sqlStatement = null, sqlPrefixAst = null, continuationAst = null,
34:    continuationProgram = null, continuationSourceVar = '_INPUT', pureSql = false,
35:    pureMemory = false, sourceTables = [] } = {}) {
37:    this.sqlStatement = sqlStatement;
42:    this.pureSql = Boolean(pureSql);
H | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) | SQL SELECT "o".* FROM "orders" "o" WHERE (2 >= "o"."id")
H | ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9) | SQL SELECT "o".* FROM "orders" "o" WHERE (("o"."id" > 1) AND ("o"."id" < 9))
H | ORDERS .> FILTER(TRUE) | MEM -
H | ORDERS .> FILTER(_["id"] > 1 + 1) | SQL SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 2)
H | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SQL SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY "o"."id" ASC
H | ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"]) | MEM -
H | ORDERS .> FILTER(IF(FALSE, "x", TRUE)) | MEM -
H | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) | MEM -
H | ORDERS .> TAKE(3) .> MAP(RECORD("id", _["id"], "k", 10 * 10)) | SQL SELECT "_sub1"."id" AS "id", 100 AS "k" FROM (SELECT "o".* FROM "orders" "o" LIMIT 3) "_sub1"
H | ORDERS .> FILTER(NOT FALSE AND _["id"] < 5) | SQL SELECT "o".* FROM "orders" "o" WHERE (TRUE AND ("o"."id" < 5))
H | ORDERS .> SELECT_COLS("id") .> FILTER(_["id"] > 1) | SQL SELECT "o"."id" FROM "orders" "o" WHERE ("o"."id" > 1)
H | ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"]) | HYB SELECT "o".* FROM "orders" "o" LIMIT 1 OFFSET 1
==== diff js lisp
1,12d0
< H | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) | SQL SELECT "o".* FROM "orders" "o" WHERE (2 >= "o"."id")
< H | ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9) | SQL SELECT "o".* FROM "orders" "o" WHERE (("o"."id" > 1) AND ("o"."id" < 9))
< H | ORDERS .> FILTER(TRUE) | MEM -
< H | ORDERS .> FILTER(_["id"] > 1 + 1) | SQL SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 2)
< H | ORDERS .> SORT_BY(_["id"]) .> FILTER(_["id"] > 1) | SQL SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY "o"."id" ASC
< H | ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"]) | MEM -
< H | ORDERS .> FILTER(IF(FALSE, "x", TRUE)) | MEM -
< H | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) | MEM -
< H | ORDERS .> TAKE(3) .> MAP(RECORD("id", _["id"], "k", 10 * 10)) | SQL SELECT "_sub1"."id" AS "id", 100 AS "k" FROM (SELECT "o".* FROM "orders" "o" LIMIT 3) "_sub1"
< H | ORDERS .> FILTER(NOT FALSE AND _["id"] < 5) | SQL SELECT "o".* FROM "orders" "o" WHERE (TRUE AND ("o"."id" < 5))
< H | ORDERS .> SELECT_COLS("id") .> FILTER(_["id"] > 1) | SQL SELECT "o"."id" FROM "orders" "o" WHERE ("o"."id" > 1)
< H | ORDERS .> DROP(1) .> TAKE(1) .> MAP(IF(TRUE, NULL, 1) + _["id"]) | HYB SELECT "o".* FROM "orders" "o" LIMIT 1 OFFSET 1
```

Rerun the Lisp hybrid probe with the correct predicate names

```bash
D=$SCRATCH/verify-C && sed -i 's/hybrid-plan-pure-sql p/hybrid-plan-pure-sql-p p/; s/hybrid-plan-pure-memory p/hybrid-plan-pure-memory-p p/' $D/hyb.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $D/hyb.lisp --eval "(main \"$D/corpus.txt\")" 2>&1 | grep "^H " > $D/hyb-lisp.txt; diff $D/hyb-js.txt $D/hyb-lisp.txt && echo HYBRID-JS-LISP-IDENTICAL
```

```
HYBRID-JS-LISP-IDENTICAL
```

Run the PHP hybrid probe and compare with JS

```bash
D=$SCRATCH/verify-C && grep -n "public.*pureSql\|public.*sqlStatement\|public.*pureMemory" php/src/Sql/HybridPlan.php 2>/dev/null | head -3; ls php/src/Sql/ | grep -i hybrid
# (file written, see "Reproduction scripts" above)
php $D/hyb.php $D/corpus.txt > $D/hyb-php.txt 2>&1; diff $D/hyb-js.txt $D/hyb-php.txt && echo HYBRID-JS-PHP-IDENTICAL
```

```
Hybrid.php
HYBRID-JS-PHP-IDENTICAL
```

