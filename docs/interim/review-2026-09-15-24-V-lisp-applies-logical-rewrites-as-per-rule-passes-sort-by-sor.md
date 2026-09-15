# V. Lisp applies logical rewrites as per-rule passes; SORT_BY .> SORT_BY / SORT_BY .> TAKE reach the translator differently and render a wrong ORDER BY

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "One optimiser policy in front of the translator, one rewrite order". The suggested `ORDER BY id DESC LIMIT 2` expectation was superseded: the earlier sort is the later one's tie-breaker (SEL sorts are stable), so the pinned SQL is `ORDER BY id DESC, name ASC LIMIT 2` and the optimiser's sort/sort elimination is removed in all five hosts.

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** lisp, js, php, python, cpp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

All three facets reproduce. The Lisp logical optimiser runs its rewrites as ten ordered per-rule passes (lisp/src/optimizer.lisp:480-745) while JS/PHP/Python/C++ apply every rule in one left-to-right sweep to a fixed point, so `SORT_BY(a) .> SORT_BY(b) .> TAKE(n)` reaches the planner as `SORT_BY TOP_BY` in Lisp and `TOP_BY` elsewhere, and `MAP(computed) .> SORT_BY .> TAKE` as `TOP_BY MAP` in Lisp vs `SORT_BY MAP TAKE` elsewhere. Because the Lisp translator's sort-step guard (lisp/src/sql/translator.lisp:2140-2144) does not wrap a derived table when an ORDER BY already exists (the other four do), Lisp's plan_hybrid renders `ORDER BY name ASC, id DESC LIMIT 2` where every other host and run() give `ORDER BY id DESC LIMIT 2`; executing both in SQLite returns different rows (Lisp: (2,a),(3,b); others and run(): (3,b),(2,a)). Everything involved predates commit ed16df2.

## Suggested fix — case first

Two fixes, both needed. (1) Restructure lisp/src/optimizer.lisp optimize-logical-pipeline-steps into the same single left-to-right sweep as the other four hosts (one loop over positions trying the rules in the shared order: TAKE/TAKE, DROP/DROP, SORT+TAKE->TOP, MAP/FILTER, SORT/FILTER, SELECT_COLS/FILTER, MAP/sort swap, FILTER/FILTER, sort/sort, dedupe/dedupe, FILTER(TRUE)) so the rewritten step list is host-independent. (2) Extend the Lisp sort-step guard at lisp/src/sql/translator.lisp:2140-2144 to also wrap when the plan already has an order-by, select-cols or distinct (matching js translator.mjs:2076-2079), so a later sort cannot be appended to an earlier ORDER BY. Cases to add first: a planner fixture in sql/cases/25-hybrid-plans.sqlt for `ORDERS .> SORT_BY(_["name"]) .> SORT_BY(_["id"], "DESC") .> TAKE(2)` expecting `... ORDER BY "o"."id" DESC LIMIT 2`, one for `MAP(computed) .> SORT_BY .> TAKE` pinning the LIMIT placement, and a translate() statement case for `SORT_BY(a) .> SORT_BY(b)` (which will also expose the pre-existing three-way translate()-optimises-or-not split between JS/PY, Lisp and PHP/C++ noted above, and C++'s `"_sub1"."ID"` casing).

## Verifier reasoning

Read all five optimisers: JS logicalSteps (js/src/optimizer.mjs:268-395), PHP logicalSteps (php/src/Optimizer.php:293-420), Python logical_steps (python/sel/optimizer.py:327-440), C++ opt_logical_steps (cpp/sel.cpp:5512-5640) are each ONE `for i` loop trying the rules in order at each position; Lisp optimize-logical-pipeline-steps (lisp/src/optimizer.lisp:480-745) is a `loop while changed` around ten separate full passes (Pass 2 SORT+TAKE fusion at :534, Pass 6 MAP/sort swap at :625, Pass 8 redundant-sort elimination at :700). The pass ordering is not merely a coherence issue: for [SORT_BY(a), SORT_BY(b), TAKE] Lisp's Pass 2 fuses SORT_BY(b)+TAKE into TOP_BY before Pass 8 runs, and Pass 8 only matches SORT*/SORT* pairs, so SORT_BY(a) survives; the single-sweep hosts hit the sort/sort rule at i=0 first, drop the first sort, then fuse on the next iteration. Verified by printing step lists (Lisp `SORT_BY TOP_BY`, JS/Python `TOP_BY`; Lisp `TOP_BY MAP`, JS/Python `SORT_BY MAP TAKE`). Then the translator: JS (js/src/sql/translator.mjs:2076-2079), Python (python/sel/sql/translator.py:1929-1933), PHP (php/src/Sql/Translator.php:2787-2790) and C++ (cpp/sel_sql_translator.cpp:2558-2563) wrap a derived table when order_by/select_cols/distinct/limit/offset/projections exist; Lisp (lisp/src/sql/translator.lisp:2140-2144) only on limit/offset/projections, so a second sort key is appended to the same ORDER BY — wrong semantics (a later SORT_BY must become the primary key). §12.1 promises the planner "plans ... the logical optimiser's rewrite" and the 25-hybrid-plans fixtures assert the exact SQL prefix per host; the only sort fixture (plan.pure-sql.sort-take-fuses-to-top, sql/cases/25-hybrid-plans.sqlt:313) has one sort and no MAP, and no .sqlt case anywhere has two successive sorts (grep found none), so the suite cannot see it. Member 1's MariaDB "free to ignore ORDER BY in a derived table" remark about the four-host `(... ORDER BY) _sub1 LIMIT 1` shape is a real SQL-standard caveat but I did not demonstrate a database actually reordering; SQLite honours it. Side observations outside this cluster, seen while reproducing: (a) translate() (not plan_hybrid) runs the logical optimiser in JS/Python (translator.mjs:149-150, translator.py:165) and in Lisp only in translate-statement (translator.lisp:2380-2382, not in `translate` at :2339), never in PHP/C++ — so translate() of the same two-sort program gives three different SQL texts, and JS/PY even accept `SORT_BY(_["name"])` when NAME is unbound (optimised away before binding check) while PHP/Lisp/C++ raise E_SQL_BINDING; (b) C++ renders the outer key of a keyless SORT() over a derived table as `"_sub1"."ID"` (uppercase) where PHP renders `"_sub1"."id"`; (c) Lisp keeps NUM kind through a derived table (`CAST("_sub1"."id" AS NUMERIC)`) where the four others emit the UNKNOWN-kind regex guard. All pre-existing.

## Verifier evidence

```
Pre-existence: `git show 8fe0e3a:lisp/src/optimizer.lisp | grep -n "Pass 2\|Pass 8"` -> 511, 677 (same pass structure); `git show 8fe0e3a:lisp/src/sql/translator.lisp | sed -n 2105,2110p` shows the identical limit/offset/projections-only guard; `git show 8fe0e3a:js/src/sql/translator.mjs | grep -n candidate.orderBy.length` -> 1785, 2046 (and PHP :2749, PY :2199, CPP :2318 likewise). The commit's diff to js/src/optimizer.mjs only touches fold/hoist code, not logicalSteps.

Reported repro (plan_hybrid, postgresql, ORDERS->orders): `./plan.sh 'ORDERS .> SORT_BY(_["id"]) .> SORT_BY(_["id"], "DESC") .> TAKE(1)'` ->
JS/PY/PHP/CPP pure_sql `SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" DESC LIMIT 1`; LISP pure_sql `SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC, "o"."id" DESC LIMIT 1`.

My repro A (different keys, TAKE(2)): `./plan.sh 'ORDERS .> SORT_BY(_["name"]) .> SORT_BY(_["id"], "DESC") .> TAKE(2)'` -> JS/PY/PHP/CPP `... ORDER BY "o"."id" DESC LIMIT 2`; LISP `... ORDER BY "o"."name" ASC, "o"."id" DESC LIMIT 2`.
Optimiser step lists for repro A: Lisp (steps.lisp via optimize-ast-logical + unwind-pipeline) `SORT_BY TOP_BY`; JS `TOP_BY`; Python `TOP_BY`.
run() in all five hosts of `ORDERS = LIST(RECORD("id",1,"name","c"),RECORD("id",2,"name","a"),RECORD("id",3,"name","b")); ORDERS .> SORT_BY(_["name"]) .> SORT_BY(_["id"], "DESC") .> TAKE(2)` -> `-{"1"=-{"id"=t"3", "name"=t"b"}, "2"=-{"id"=t"2", "name"=t"a"}}` (identical in node/php/cpp/lisp/python).
SQLite execution over rows (1,c),(2,a),(3,b): Lisp SQL -> [(2,'a'),(3,'b')]; the other hosts' SQL -> [(3,'b'),(2,'a')]. Lisp's SQL lane disagrees with run() and with the four other hosts.

Member 3 (translate(), NAME bound): `./xlate.sh 'ORDERS .> SORT_BY(_["id"]) .> SORT_BY(_["name"])'` -> JS/PY `ORDER BY "o"."name" ASC`; PHP/CPP `SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC) "_sub1" ORDER BY "_sub1"."name" ASC`; LISP `ORDER BY "o"."id" ASC, "o"."name" ASC`. My variant `ORDERS .> SORT_DESC() .> SORT()` -> LISP `ORDER BY "o"."id" DESC, "o"."id" ASC`, PHP derived table with `"_sub1"."id" ASC`, CPP derived table with `"_sub1"."ID" ASC`. plan_hybrid of the same two-sort-no-TAKE program agrees in all five (`ORDER BY "o"."name" ASC`), as the finding said.

Member 1: `./plan.sh 'ORDERS .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> SORT_BY(_["id"]) .> TAKE(1)'` -> JS/PY/PHP/CPP hybrid `SELECT "_sub1"."id" AS "id" FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC) "_sub1" LIMIT 1`; LISP hybrid `SELECT "_sub1"."id" AS "id" FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC LIMIT 1) "_sub1"`. My repro C `ORDERS .> MAP(RECORD("id", _["id"], "x", _["id"] + 1)) .> SORT_BY(_["id"], "DESC") .> TAKE(2)` -> same LIMIT-placement split (all pure_sql); Lisp steps `TOP_BY MAP`, JS/PY `SORT_BY MAP TAKE`.

Fixture coverage: `grep` over sql/cases/*.sqlt programs for two successive SORT/TOP steps -> no matches; the sole sort planner fixture is sql/cases/25-hybrid-plans.sqlt:313 `plan.pure-sql.sort-take-fuses-to-top` (`ORDERS .> SORT_BY(_["id"]) .> TAKE(1)`).

Probe scripts: /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-V/{plan.sh,xlate.sh,steps.lisp}.
```

## Original review reports (deduplicated into this finding)

### [hybrid-planner] Lisp applies logical rewrites as separate per-rule passes; the other hosts apply all rules in one left-to-right sweep, so the same pipeline reaches the planner as a different step list

*coherence · high · hosts: lisp, js, php, python, cpp*

Locations: `lisp/src/optimizer.lisp:480-745`; `js/src/optimizer.mjs:268-395`; `php/src/Optimizer.php:293`; `python/sel/optimizer.py:327`; `cpp/sel.cpp:5512`

For `MAP(computed) .> SORT_BY(k) .> TAKE(n)` Lisp's pass 2 fuses SORT_BY+TAKE into TOP_BY before pass 6 swaps it in front of the MAP, giving `TOP_BY .> MAP`; the single-sweep hosts see MAP+SORT_BY first, swap them, and the TAKE can no longer fuse, giving `SORT_BY .> MAP .> TAKE`. §12.1 promises the planner plans 'the logical optimiser's rewrite', but that rewrite is host-specific, so the SQL prefix differs. The four-host rendering also puts ORDER BY inside a derived table with the LIMIT outside (`(SELECT o.* ... ORDER BY o.id ASC) _sub1 LIMIT 1`), which MariaDB is free to ignore, so the pushed-down TAKE(1) is not guaranteed to be the sorted first row. The fixture plan.pure-sql.sort-take-fuses-to-top has no MAP before the sort.

Reported repro:

```
steps probe: `ORDERS .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> SORT_BY(_["id"]) .> TAKE(1)` -> js/py: SORT_BY .> MAP .> TAKE; lisp: TOP_BY .> MAP. plan_hybrid (mariadb): js/php/py/cpp sql=SELECT `_sub1`.`id` AS `id` FROM (SELECT `o`.* FROM `orders` `o` ORDER BY `o`.`id` ASC) `_sub1` LIMIT 1; lisp sql=SELECT `_sub1`.`id` AS `id` FROM (SELECT `o`.* FROM `orders` `o` ORDER BY `o`.`id` ASC LIMIT 1) `_sub1`.
```

### [runners-generator-tests] Lisp plans wrong SQL for successive sorts: pass-ordered optimiser leaves SORT_BY before TOP_BY, translator appends both keys to one ORDER BY

*correctness · high · hosts: lisp*

Locations: `lisp/src/optimizer.lisp:534`; `lisp/src/optimizer.lisp:700`; `lisp/src/sql/translator.lisp:2140`; `python/sel/optimizer.py:358`; `python/sel/optimizer.py:421`; `js/src/optimizer.mjs:302`; `js/src/sql/translator.mjs:2076`; `python/sel/sql/translator.py:1929`; `php/src/Sql/Translator.php:2787`

The Lisp logical optimiser runs its rewrites as ordered passes (Pass 2 SORT+TAKE fusion at line 534 runs before Pass 8 redundant-sort elimination at line 700), while JS/Python/PHP apply all pair rules in one loop to a fixed point. For `ORDERS .> SORT_BY(_["id"]) .> SORT_BY(_["id"], "DESC") .> TAKE(1)` Lisp fuses the second sort into TOP_BY first, so Pass 8 no longer sees two sorts and the steps come out `SORT_BY TOP_BY` where every other host produces `TOP_BY`. The Lisp translator's sort-step guard (translator.lisp:2140-2144) then wraps in a derived table only on limit/offset/projections — it lacks the order-by/select-cols/distinct conditions JS (2076-2079), Python (1929-1933) and PHP (2787-2790) have — so it appends the second key to the same ORDER BY. The resulting pure_sql plan `ORDER BY "o"."id" ASC, "o"."id" DESC LIMIT 1` returns the minimum id; the evaluator and the other four hosts' SQL return the maximum. The 33 new plan fixtures only ever contain one sort (plan.pure-sql.sort-take-fuses-to-top), which is why the planner-lane divergence is invisible to the suite; the same shape through translate() also diverges (Lisp appends keys, PHP/C++ derived table, JS/Python fuse via their optimiser pre-pass). Pre-existing code, but it is exactly the class of coincidence-on-fixtures divergence the new fixture format claims to pin.

Reported repro:

```
scratchpad/runners-generator-tests/plan.sh 'ORDERS .> SORT_BY(_["id"]) .> SORT_BY(_["id"], "DESC") .> TAKE(1)' (postgresql, ORDERS->orders): JS/PY/PHP/CPP: pure_sql `SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" DESC LIMIT 1`; LISP: pure_sql `SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC, "o"."id" DESC LIMIT 1`. Optimiser steps (scratchpad steps.lisp vs node/python/php one-liners): LISP logical => `SORT_BY TOP_BY`; JS/PY/PHP => `TOP_BY`. Semantics: `lisp/bin/sel -e 'ORDERS = LIST(RECORD("id",1),RECORD("id",2),RECORD("id",3)); ORDERS .> SORT_BY(_["id"]) .> SORT_BY(_["id"],"DESC") .> TAKE(1)'` => id 3; sqlite on Lisp's SQL => id 1, on the others' SQL => id 3. Same source through translate(): JS/PY `ORDER BY id DESC LIMIT 1`, PHP/CPP derived table `_sub1`, LISP `ORDER BY id ASC, id DESC`.
```

### [probe-lanes-fold] Lisp translate() renders SORT_BY(a) .> SORT_BY(b) as ORDER BY a, b — wrong ordering versus run() and versus every other host

*correctness · high · hosts: lisp*

Locations: `lisp/src/sql/translator.lisp:2140-2146 (sort step wraps a derived table only for limit/offset/projections; an existing order-by is appended to at 1723-1749,1782-1783)`; `js/src/sql/translator.mjs:2076-2079 (reference behaviour: ensureDerived also on candidate.orderBy.length / distinct)`

Pre-existing, no fixture covers two sorts. A second SORT_BY must make the new key primary; Lisp appends it as a secondary key, so the SQL orders by the first key. run() in all five hosts returns ids 2,3,1 (ordered by amount); Lisp's SQL orders 1,2,3. The plan_hybrid lane hides this because the logical optimiser drops the redundant first sort in every host, so only translate()/translate_statement-with-:no-optimize show it.

Reported repro:

```
Program: ORDERS .> SORT_BY(_["id"]) .> SORT_BY(_["amount"])
run() (all five hosts): -{"1"=-{"id"=t"2", "amount"=t"3",...}, "2"=-{"id"=t"3", "amount"=t"5",...}, "3"=-{"id"=t"1", "amount"=t"10.50",...}}
translate(mariadb): JS/PY: SELECT `o`.* FROM `orders` `o` ORDER BY `o`.`amount` ASC | PHP/CPP: SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` ORDER BY `o`.`id` ASC) `_sub1` ORDER BY `_sub1`.`amount` ASC | LISP: SELECT `o`.* FROM `orders` `o` ORDER BY `o`.`id` ASC, `o`.`amount` ASC
plan_hybrid(mariadb): all five pure_sql, SELECT `o`.* FROM `orders` `o` ORDER BY `o`.`amount` ASC
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$S/steps.lisp`**

```lisp
(in-package #:sel-sqlt)
(dolist (src '("ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"x\", _[\"id\"] + 1)) .> SORT_BY(_[\"id\"], \"DESC\") .> TAKE(2)"
               "ORDERS .> SORT_BY(_[\"name\"]) .> SORT_BY(_[\"id\"], \"DESC\") .> TAKE(2)"
               "ORDERS .> SORT_DESC() .> SORT()"))
  (multiple-value-bind (src-node steps) (sel::unwind-pipeline (sel:optimize-ast-logical (sel:program-ast (sel:compile-source src))))
    (declare (ignore src-node))
    (format t "LISP logical  ~a => ~{~a~^ ~}~%" src (mapcar #'sel::node-s steps))))
```

### Commands run and their output

Copy probe scripts into verify-V and show xlate and steps probes

```bash
cd $SCRATCH && mkdir -p verify-V && sed 's#scratchpad/runners-generator-tests#scratchpad/verify-V#' runners-generator-tests/plan.sh > verify-V/plan.sh && sed 's#scratchpad/runners-generator-tests#scratchpad/verify-V#' runners-generator-tests/xlate.sh > verify-V/xlate.sh && chmod +x verify-V/*.sh && cat verify-V/xlate.sh; cat runners-generator-tests/steps.lisp
```

```
#!/usr/bin/env bash
# usage: xlate.sh 'SEL source'  -- prints translate() output from all five hosts
cd /home/nathan/workspaces/nth-share/sel
S=$SCRATCH/verify-V
SRC="$1"
node -e '
import("./js/src/sel.mjs").then(async ({compile}) => { const {Sql, Binding} = await import("./js/src/sql/index.mjs");
const orders = { ORDERS: Binding.relation("orders", "o", { ID: Binding.column("id", "o", "NUM") }) };
try { console.log("JS  ", Sql.translate(compile(process.argv[1]), "postgresql", orders).asStatement("inline")); } catch (e) { console.log("JS  ", e.code, e.line+":"+e.col, e.message); }
})' "$SRC"
PYTHONPATH=$PWD/python python3 -c '
import sys
from sel import compile
from sel.sql import Sql, Binding
orders = {"ORDERS": Binding.relation("orders", "o", {"ID": Binding.column("id", "o", "NUM")})}
try: print("PY  ", Sql.translate(compile(sys.argv[1]), "postgresql", orders).as_statement("inline"))
except Exception as e: print("PY  ", getattr(e,"code",e), f"{getattr(e,"line","")}:{getattr(e,"col","")}", getattr(e,"message",""))' "$SRC"
php -r 'require "php/src/Sql/bootstrap.php"; use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$orders = ["ORDERS" => Binding::relation("orders", "o", ["ID" => Binding::column("id", "o", "NUM")])];
try { echo "PHP ", Sql::translate(Sel::compile($argv[1]), "postgresql", $orders)->asStatement("inline"), "\n"; } catch (\Sel\Sql\SqlError $e) { echo "PHP ", $e->code, " {$e->line}:{$e->col} ", $e->getMessage(), "\n"; }' "$SRC"
cat > $S/xl.lisp <<EOF2
(in-package #:sel-sqlt)
(let* ((orders (list (cons "ORDERS" (binding-relation "orders" "o" (list (cons "ID" (binding-column "id" "o" :num)))))))
       (src "$(printf '%s' "$SRC" | sed 's/\\/\\\\/g; s/"/\\"/g')"))
  (handler-case (format t "LISP ~a~%" (as-statement (translate (sel:compile-source src) "postgresql" orders) :inline))
    (sql-error (e) (format t "LISP ~a ~a:~a ~a~%" (sql-error-code e) (sql-error-line e) (sql-error-col e) (sql-error-message e)))))
EOF2
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load lisp/bin/sqlt.lisp --load $S/xl.lisp 2>&1 | grep "^LISP"
cat > $S/xl.cpp <<EOF2
#include "sel.hpp"
#include "sel_sql.hpp"
#include <iostream>
using namespace sel::sql;
int main(){ const Bindings b({{"ORDERS", Binding::relation("orders","o",{{"ID", Binding::column("id","o",SqlKind::Num)}})}});
 const char* src = R"SEL($SRC)SEL";
 try { std::cout << "CPP  " << Sql::translate(sel::compile(src), "postgresql", b).as_statement() << "\n"; }
 catch (const SqlError& e) { std::cout << "CPP  " << e.code() << " " << e.line() << ":" << e.col() << " " << e.message() << "\n"; } }
EOF2
(cd cpp && c++ -std=c++23 -O0 -I. -o $S/xl $S/xl.cpp build/sel_sql.o build/sel_sql_binding.o build/sel_sql_emit.o build/sel_sql_map.o build/sel_sql_map_data.o build/sel_sql_node.o build/sel_sql_stage1.o build/sel_sql_translator.o build/sel_sql_hybrid.o build/sel.o) && $S/xl
(in-package #:sel-sqlt)
(dolist (src '("ORDERS .> SORT_BY(_[\"id\"]) .> SORT_BY(_[\"id\"], \"DESC\") .> TAKE(1)"
               "ORDERS .> SORT() .> SORT_DESC() .> TAKE(1)"
               "ORDERS .> FILTER(_[\"id\"] > 1) .> FILTER(_[\"id\"] < 9) .> SORT_BY(_[\"id\"]) .> TAKE(1)"))
  (multiple-value-bind (src-node steps) (sel::unwind-pipeline (sel:optimize-ast-logical (sel:program-ast (sel:compile-source src))))
    (declare (ignore src-node))
    (format t "LISP logical  ~a => ~{~a~^ ~}~%" src (mapcar #'sel::node-s steps)))
  (multiple-value-bind (src-node steps) (sel::unwind-pipeline (sel:optimiz
… (truncated)
```

Run translate() lane with NAME bound for two-sort programs

```bash
cd $SCRATCH/verify-V && sed -i 's#"ID", Binding.column("id", "o", "NUM") }#"ID": Binding.column("id", "o", "NUM"), NAME: Binding.column("name", "o", "TEXT") }#; s#{ ID: Binding.column("id", "o", "NUM") }#{ ID: Binding.column("id", "o", "NUM"), NAME: Binding.column("name", "o", "TEXT") }#; s#{"ID": Binding.column("id", "o", "NUM")}#{"ID": Binding.column("id", "o", "NUM"), "NAME": Binding.column("name", "o", "TEXT")}#; s#\["ID" => Binding::column("id", "o", "NUM")\]#["ID" => Binding::column("id", "o", "NUM"), "NAME" => Binding::column("name", "o", "TEXT")]#; s#(list (cons "ID" (binding-column "id" "o" :num)))#(list (cons "ID" (binding-column "id" "o" :num)) (cons "NAME" (binding-column "name" "o" :text)))#; s#{{"ID", Binding::column("id","o",SqlKind::Num)}}#{{"ID", Binding::column("id","o",SqlKind::Num)},{"NAME", Binding::column("name","o",SqlKind::Text)}}#' xlate.sh && ./xlate.sh 'ORDERS .> SORT_BY(_["name"]) .> SORT_BY(_["id"], "DESC") .> TAKE(2)' 2>&1; echo "=== two sorts no take ==="; ./xlate.sh 'ORDERS .> SORT_BY(_["id"]) .> SORT_BY(_["name"])' 2>&1
```

```
JS   SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" DESC LIMIT 2
PY   SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" DESC LIMIT 2
PHP SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC) "_sub1" ORDER BY "_sub1"."id" DESC LIMIT 2
LISP SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC, "o"."id" DESC LIMIT 2
CPP  SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC) "_sub1" ORDER BY "_sub1"."id" DESC LIMIT 2
=== two sorts no take ===
JS   SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC
PY   SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC
PHP SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC) "_sub1" ORDER BY "_sub1"."name" ASC
LISP SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC, "o"."name" ASC
CPP  SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" ASC) "_sub1" ORDER BY "_sub1"."name" ASC
```

Compare run() results across hosts with sqlite execution of each host's SQL

```bash
P='ORDERS = LIST(RECORD("id",1,"name","c"),RECORD("id",2,"name","a"),RECORD("id",3,"name","b")); ORDERS .> SORT_BY(_["name"]) .> SORT_BY(_["id"], "DESC") .> TAKE(2)'; for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel"; do echo "-- $h"; $h -e "$P" 2>&1 | tr -d '\n'; echo; done; echo "-- python"; PYTHONPATH=$PWD/python python3 -m sel -e "$P"; echo; echo "-- sqlite on Lisp SQL vs others"; python3 - <<'EOF'
import sqlite3
c=sqlite3.connect(':memory:'); c.execute('create table orders(id int, name text)')
c.executemany('insert into orders values(?,?)',[(1,'c'),(2,'a'),(3,'b')])
for label,sql in [("LISP",'SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC, "o"."id" DESC LIMIT 2'),
                  ("JS/PY/PHP/CPP(hybrid)",'SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" DESC LIMIT 2'),
                  ("PHP/CPP translate()",'SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC) "_sub1" ORDER BY "_sub1"."id" DESC LIMIT 2')]:
    print(label, c.execute(sql).fetchall())
EOF
```

```
-- node js/bin/sel.mjs
-{"1"=-{"id"=t"3", "name"=t"b"}, "2"=-{"id"=t"2", "name"=t"a"}}
-- php php/bin/sel
-{"1"=-{"id"=t"3", "name"=t"b"}, "2"=-{"id"=t"2", "name"=t"a"}}
-- cpp/build/sel
-{"1"=-{"id"=t"3", "name"=t"b"}, "2"=-{"id"=t"2", "name"=t"a"}}
-- lisp/bin/sel
-{"1"=-{"id"=t"3", "name"=t"b"}, "2"=-{"id"=t"2", "name"=t"a"}}
-- python
-{"1"=-{"id"=t"3", "name"=t"b"}, "2"=-{"id"=t"2", "name"=t"a"}}

-- sqlite on Lisp SQL vs others
LISP [(2, 'a'), (3, 'b')]
JS/PY/PHP/CPP(hybrid) [(3, 'b'), (2, 'a')]
PHP/CPP translate() [(3, 'b'), (2, 'a')]
```

Print Lisp logical step lists for three programs

```bash
S=$SCRATCH/verify-V; grep -n "^export" js/src/optimizer.mjs | head; # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load lisp/bin/sqlt.lisp --load $S/steps.lisp 2>&1 | grep "^LISP"
```

```
630:export function optimizeAstLogical(ast, options = {}) { return optimizeTree(ast, false, 1, options); }
631:export function optimizeAstInMemory(ast) { return optimizeTree(ast, true); }
632:export function optimizeAst(ast) { return optimizeAstInMemory(ast); }
633:export { PIPELINE_OPS, unwindPipeline, buildPipeline };
LISP logical  ORDERS .> MAP(RECORD("id", _["id"], "x", _["id"] + 1)) .> SORT_BY(_["id"], "DESC") .> TAKE(2) => TOP_BY MAP
LISP logical  ORDERS .> SORT_BY(_["name"]) .> SORT_BY(_["id"], "DESC") .> TAKE(2) => SORT_BY TOP_BY
LISP logical  ORDERS .> SORT_DESC() .> SORT() => SORT
```

Print JS and Python logical step lists for the same programs

```bash
for src in 'ORDERS .> MAP(RECORD("id", _["id"], "x", _["id"] + 1)) .> SORT_BY(_["id"], "DESC") .> TAKE(2)' 'ORDERS .> SORT_BY(_["name"]) .> SORT_BY(_["id"], "DESC") .> TAKE(2)' 'ORDERS .> SORT_DESC() .> SORT()'; do node -e '
import("./js/src/sel.mjs").then(async ({compile}) => { const o = await import("./js/src/optimizer.mjs");
const p = compile(process.argv[1]); const ast = o.optimizeAstLogical(p.ast ?? p._ast ?? p);
const [, steps] = o.unwindPipeline(ast); console.log("JS   logical", process.argv[1], "=>", steps.map(s=>s.name).join(" ")); })' "$src"; PYTHONPATH=$PWD/python python3 -c '
import sys
from sel import compile
from sel import optimizer as o
p = compile(sys.argv[1]); ast = o.optimize_ast_logical(p.ast)
src, steps = o.unwind_pipeline(ast); print("PY   logical", sys.argv[1], "=>", " ".join(s.name for s in steps))' "$src"; done 2>&1 | grep -v "^$"
```

```
[eval]:4
const [, steps] = o.unwindPipeline(ast); console.log("JS   logical", process.argv[1], "=>", steps.map(s=>s.name).join(" ")); })
                  ^
TypeError: object is not iterable (cannot read property Symbol(Symbol.iterator))
    at [eval]:4:19
Node.js v22.12.0
PY   logical ORDERS .> MAP(RECORD("id", _["id"], "x", _["id"] + 1)) .> SORT_BY(_["id"], "DESC") .> TAKE(2) => SORT_BY MAP TAKE
[eval]:4
const [, steps] = o.unwindPipeline(ast); console.log("JS   logical", process.argv[1], "=>", steps.map(s=>s.name).join(" ")); })
                  ^
TypeError: object is not iterable (cannot read property Symbol(Symbol.iterator))
    at [eval]:4:19
Node.js v22.12.0
PY   logical ORDERS .> SORT_BY(_["name"]) .> SORT_BY(_["id"], "DESC") .> TAKE(2) => TOP_BY
[eval]:4
const [, steps] = o.unwindPipeline(ast); console.log("JS   logical", process.argv[1], "=>", steps.map(s=>s.name).join(" ")); })
                  ^
TypeError: object is not iterable (cannot read property Symbol(Symbol.iterator))
    at [eval]:4:19
Node.js v22.12.0
PY   logical ORDERS .> SORT_DESC() .> SORT() => SORT
```

Confirm plan_hybrid agrees for two sorts without TAKE

```bash
cd $SCRATCH/verify-V && ./plan.sh 'ORDERS .> SORT_BY(_["id"]) .> SORT_BY(_["name"])' 2>&1
```

```
JS   pure_sql ["orders"] SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC
PY   pure_sql ["orders"] SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC
PHP pure_sql ["orders"] SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC
LISP pure_sql ("orders") SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC
CPP  pure_sql [orders,] SELECT "o".* FROM "orders" "o" ORDER BY "o"."name" ASC
```

