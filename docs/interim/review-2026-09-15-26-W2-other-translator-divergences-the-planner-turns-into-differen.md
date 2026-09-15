# W2. Other translator divergences the planner turns into different plans (Lisp DISTINCT/ORDER BY wrap, C++ derived column spelling, LINK binder scoping)

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** lisp, cpp, js, php, python

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

All four reported translator divergences reproduce byte-for-byte in translate_statement and carry straight into plan_hybrid's SQL prefix / classification: (a) Lisp omits the NUM guard on columns of an `o.*` derived table, (b) Lisp does not wrap a DISTINCT/ORDER BY plan before a MAP (SELECT DISTINCT o.id returns 2 rows on SQLite where the other four hosts' SQL and the evaluator return 3), (c) C++ spells a derived-table column with the later step's key (`"_sub1"."ID"` on PostgreSQL, a runtime error there), (d) after a LINK, C++ alone resolves an ambiguous `_["name"]` to the left table and C++/Lisp alone keep the LEFT binder in scope (pure_sql `WHERE o.id > 1` vs hybrid + E_UNDEF_VAR when JS/PHP/Python execute the plan). One correction: the RIGHT binder (C / _2) is in scope after LINK in all five translators, so only the left binder differs; and Program.run() accepts `FILTER(O["id"] > 1)` in all five hosts because the physical optimiser pushes the filter into the LINK, so the evaluator raises E_UNDEF_VAR only for the MAP form. Every code path involved is identical at 8fe0e3a.

## Suggested fix — case first

Add .sqlt cases first, one per facet, then align the odd host: (b) `ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"]))` expecting the `_sub1` wrap; fix lisp/src/sql/translator.lisp:2098-2103 to also test relational-plan-distinct and relational-plan-order-by (mirror plan_has_rows_above). (a) `ORDERS .> TAKE(2) .> FILTER(_["id"] > 1)` on mariadb expecting the REGEXP/CAST guard; fix lisp plan-output-fields (1813-1821) to emit :type :unknown like the other four (or decide the reverse in all four — but pin it). (c) `ORDERS .> MAP(RECORD("id", _["id"])) .> FILTER(_["ID"] > 1)` on postgresql expecting `"_sub1"."id"`; delete the `derived.column = key` override at cpp/sel_sql_translator.cpp:511-513 (the field's column already holds the projection alias). (d) `ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"]))` expecting E_SQL_SHAPE@1:81 and `... LINK(CUSTOMERS, O, C, ...) .> FILTER(O["id"] > 1)` expecting E_SQL_UNBOUND@1:72 plus a `--- plan` hybrid case; in C++ move the left/joined ambiguity check before the `rel.field(field)` early return at 507, and drop `frame_set(frame, join.left_binder, row)` at cpp:1948 and the matching `(frame-set frame (join-plan-left-binder j) row)` at lisp:1381. Separately decide (spec) whether the right binder should survive the LINK at all — the unoptimised evaluator says no (E_UNDEF_VAR for MAP(C["name"])) while all five translators say yes, and the in-memory LINK predicate pushdown makes FILTER(O[...]) succeed where the raw tree errors; that optimiser-changes-observable-behaviour issue deserves its own conformance case.

## Verifier reasoning

Coherence is the product; these are not latent differences but different SQL text, different plan classification, and in (b)/(c) different rows or a DB error. Facet (b) is a real wrong answer in Lisp against the evaluator (row count). Facet (c) is C++-only SQL that fails on a case-sensitive-identifier engine. Facet (d) flips plan classification (pure_sql vs hybrid) between hosts, and within JS/PHP/Python the run() lane returns rows while execute_hybrid raises E_UNDEF_VAR for the same program (because the continuation `_INPUT .> FILTER(O["id"] > 1)` has no LINK for the optimiser to push into). Nothing in sql/cases pins a NUM guard over a derived column, DEDUPE/SORT_BY followed by MAP, mixed-case keys over a derived table, or a LINK binder used after the LINK, which is why tools/check.sh is green. All five code sites are unchanged between 8fe0e3a and ed16df2 (verified with git show), so the defects are pre-existing, but the commit's §12.1 promise that every host produces the same plan makes them planner-visible promises unmet.

## Verifier evidence

```
Probe harness (bindings ORDERS o{id NUM, customer_id NUM, amount NUM, name TEXT}, CUSTOMERS c{id NUM, name TEXT}) in /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-W2/{probe.mjs,probe.py,probe.php,probe.cpp,probe.lisp,all.sh}; each prints translate_statement (TS) and plan_hybrid (PLAN: kind + prefix).

(a) `ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))` mariadb: js/php/py/cpp `SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1``; lisp `SELECT CASE WHEN (`_sub1`.`id` > 1) THEN 'x' ...`. Own repro `ORDERS .> DROP(1) .> FILTER(_["amount"] + 1 > 3)` postgresql: four hosts `CAST(CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ ...) THEN CAST(... AS NUMERIC) ELSE NULL END AS NUMERIC)`, lisp `CAST("_sub1"."amount" AS NUMERIC)`. Code: lisp/src/sql/translator.lisp:1813-1821 (plan-output-fields copies `(getf (cdr f) :type :unknown)`), vs js/src/sql/translator.mjs:1733-1735 `type: 'UNKNOWN'`, python/sel/sql/translator.py:1687, php/src/Sql/Translator.php:2422, cpp/sel_sql_translator.cpp:2268-2272 `field.type = SqlKind::Unknown`. Same Lisp lines at 8fe0e3a (old-translator.lisp:1815,1820).

(b) `ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"]))`: four hosts `SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1``; lisp `SELECT DISTINCT `o`.`id` AS `id` FROM `orders` `o``. Executed on SQLite with rows (1,1,5,a),(2,1,6,b),(2,1,7,c): four hosts' SQL -> [(1,),(2,),(2,)], lisp -> [(1,),(2,)]; evaluator (node mem.mjs) -> 3 rows `-{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"2"}}`. Own repro `ORDERS .> SORT_BY(_["amount"]) .> MAP(RECORD("id", _["id"]))` sqlite: four hosts wrap in `_sub1`, lisp `SELECT "o"."id" AS "id" FROM "orders" "o" ORDER BY "o"."amount" ASC`. Code: lisp/src/sql/translator.lisp:2098-2103 (MAP arm wraps on projections/select-cols/group-by/limit/offset only) vs js/src/sql/translator.mjs:1690-1694, python/sel/sql/translator.py:1642-1646, php/src/Sql/Translator.php:2357-2362, cpp/sel_sql_translator.cpp:2208-2212 (all include distinct and order_by). Pre-existing: old-translator.lisp:2053-2058 identical.

(c) `ORDERS .> MAP(RECORD("id", _["id"])) .> FILTER(_["ID"] > 1)` postgresql: cpp `CAST("_sub1"."ID" AS TEXT)`/`CAST("_sub1"."ID" AS NUMERIC)`, other four `"_sub1"."id"`. Own repro `ORDERS .> TAKE(5) .> MAP(RECORD("n", _["Amount"] * 2))` postgresql: cpp `"_sub1"."Amount"`, others `"_sub1"."amount"`. Code: cpp/sel_sql_translator.cpp:507-515 (`ColumnSpec derived = *f; derived.column = key;` when the plan has a source_subquery). Pre-existing: old-translator.cpp:512.

(d1) `ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"]))`: js/php/py/lisp TS `E_SQL_SHAPE@1:81`, PLAN hybrid with prefix `SELECT `o`.* ... INNER JOIN ...`; cpp TS `SELECT `o`.`name` AS `name` FROM ...`, PLAN pure_sql. Own repro with LINK_LEFT + `FILTER(_["id"] > 1)` postgresql: four hosts E_SQL_SHAPE@1:74 / hybrid, cpp pure_sql `WHERE ("o"."id" > 1)`. Code: cpp/sel_sql_translator.cpp:507 returns the left relation's field before the ambiguity loop at 517-540 (which only scans joins); js/src/sql/translator.mjs:975-987 and lisp/src/sql/translator.lisp:356-362 check left vs joined first. Pre-existing: old-translator.cpp:507-517.

(d2) `ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)`: js/php/py TS `E_SQL_UNBOUND@1:72`, PLAN hybrid; cpp/lisp TS `... WHERE (`o`.`id` > 1)`, PLAN pure_sql. Executing the plan: node exec.mjs -> `run(): rows...`, `plan: hybrid`, `hybrid: E_UNDEF_VAR@1:72`; python exec.py identical. Code: cpp/sel_sql_translator.cpp:1936-1949 binds `join.left_binder` and `join.right_binder` into every later step's frame; lisp/src/sql/translator.lisp:1370-1381 likewise (`(frame-set frame (join-plan-left-binder j) row)`); js/src/sql/translator.mjs:1257-1268, python/sel/sql/translator.py:1218, php/src/Sql/Translator.php:1567 bind only the right binder. Pre-existing: old-translator.cpp:1948, old-translator.lisp:1381. Correction: `... .> FILTER(C["id"] > 1)` and `... .> FILTER(_2["id"] > 1)` render `WHERE (`c`.`id` > 1)` in all five hosts, so the right binder is uniformly in scope. Evaluator: `MAP(O["id"])` / `MAP(C["name"])` / `MAP(_2["id"])` after LINK -> E_UNDEF_VAR in js/py/cpp (mem.mjs, mem.py, mem-cpp), but `FILTER(O["id"] > 1)` returns rows in all five (js, php, py, cpp, lisp run() with the same context) because optimizeAstInMemory rewrites it to `LINK(FILTER(ORDERS, _["id"] > 1), CUSTOMERS, O, C, ...)` (opt.mjs output).

Suite coverage: grep of sql/cases shows `_sub1` only in 25-hybrid-plans.sqlt lines 110/144/373 (LIMIT/ORDER BY LIMIT wraps and a TEXT-kind column); no NUM guard over a derived column, no DEDUPE/SORT_BY .> MAP, no mixed-case key over a derived table, no post-LINK binder use.
```

## Original review reports (deduplicated into this finding)

### [hybrid-planner] Translator divergences that the planner turns into different plans: derived-table kinds (Lisp), MAP wrap predicate without distinct/order-by (Lisp), derived column spelled by SEL key (C++), join scoping of `_` and LINK binders (C++, Lisp)

*coherence · high · hosts: lisp, cpp, js, php, python*

Locations: `lisp/src/sql/translator.lisp:1817-1829`; `lisp/src/sql/translator.lisp:2098-2103`; `js/src/sql/translator.mjs:1690-1694`; `cpp/sel_sql_translator.cpp:2266-2272`; `cpp/sel_sql_translator.cpp:508-513`; `cpp/sel_sql_translator.cpp:2251`

Because plan_hybrid probes prefixes with translate_statement, any translator disagreement becomes a different classification or SQL prefix. Observed: (a) Lisp's plan-output-fields keeps the source column :type when wrapping `o.*` as a derived table, C++/JS mark them Unknown, so Lisp emits `_sub1.id > 1` where the others emit the REGEXP/CAST guard; (b) Lisp's MAP arm wraps only on projections/select-cols/group-by/limit/offset while JS's planHasRowsAbove also includes distinct and orderBy, so `DEDUPE() .> MAP(RECORD("id", _["id"]))` renders `SELECT DISTINCT o.id AS id` in Lisp (dedupes on id alone -- a different row count) versus `(SELECT DISTINCT o.*) _sub1` elsewhere; (c) C++ sets `derived.column = key` so `_["ID"]` over a derived table renders `_sub1`.`ID` where the others render `_sub1`.`id` (breaks on PostgreSQL quoted identifiers); (d) after a LINK, C++ resolves `_["name"]` to the left table where the other four refuse as ambiguous, and both C++ and Lisp keep LINK's explicit binders (O, C) in scope for later steps although the evaluator raises E_UNDEF_VAR there and JS/PHP/Python refuse E_SQL_UNBOUND. Each of these changed the plan produced by the fall-through or prefix loop in the probe.

Reported repro:

```
translate_statement (mariadb): `ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))` -> four hosts CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP ...) THEN CAST(...) ELSE NULL END > 1) ..., lisp CASE WHEN (`_sub1`.`id` > 1) .... `ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"]))` -> four hosts SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`, lisp SELECT DISTINCT `o`.`id` AS `id` FROM `orders` `o`. `ORDERS .> MAP(RECORD("id", _["id"])) .> FILTER(_["ID"] > 1)` -> cpp `_sub1`.`ID`, others `_sub1`.`id` (postgresql: "_sub1"."ID" vs "_sub1"."id"). `ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"]))` -> js/php/py/lisp refused E_SQL_SHAPE@1:81, cpp SELECT `o`.`name` AS `name` .... `ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)` -> js/php/py refused E_SQL_UNBOUND@1:72, cpp and lisp emit WHERE (`o`.`id` > 1); the evaluator gives E_UNDEF_VAR for O. Planner consequences: probe batch 2 lines 94/184/226/250/348 and batch 3 lines 89/96 (Lisp/C++ choose the fall-through where the others choose a prefix, or vice versa).
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$D/probe.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const b = {
  ORDERS: Binding.relation('orders', 'o', { id: Binding.column('id', 'o', 'NUM'), customer_id: Binding.column('customer_id', 'o', 'NUM'), amount: Binding.column('amount','o','NUM') }),
  CUSTOMERS: Binding.relation('customers', 'c', { id: Binding.column('id', 'c', 'NUM'), name: Binding.column('name', 'c', 'TEXT') }),
};
const [src, dialect = 'mariadb'] = process.argv.slice(2);
const p = compile(src);
try { console.log('TS: ' + Sql.translateStatement(p, dialect, b).asStatement()); }
catch (e) { console.log(`TS: ${e.code}@${e.line}:${e.col}`); }
try {
  const plan = Sql.planHybrid(p, dialect, b);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  let sql = '';
  try { sql = plan.sqlPrefix ? (plan.sqlPrefix.asStatement ? plan.sqlPrefix.asStatement() : String(plan.sqlPrefix)) : ''; } catch(e) { sql = '?'+e.message; }
  console.log(`PLAN: ${kind} ${sql}`);
} catch (e) { console.log(`PLAN: ${e.code}@${e.line}:${e.col}`); }
```

**`$D/probe.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const b = {
  ORDERS: Binding.relation('orders', 'o', { id: Binding.column('id', 'o', 'NUM'), customer_id: Binding.column('customer_id', 'o', 'NUM'), amount: Binding.column('amount','o','NUM') }),
  CUSTOMERS: Binding.relation('customers', 'c', { id: Binding.column('id', 'c', 'NUM'), name: Binding.column('name', 'c', 'TEXT') }),
};
const [src, dialect = 'mariadb'] = process.argv.slice(2);
const p = compile(src);
try { console.log('TS: ' + Sql.translateStatement(p, dialect, b).asStatement()); }
catch (e) { console.log(`TS: ${e.code}@${e.line}:${e.col}`); }
try {
  const plan = Sql.planHybrid(p, dialect, b);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  console.log(`PLAN: ${kind} ${plan.sqlStatement ?? ''}`);
} catch (e) { console.log(`PLAN: ${e.code}@${e.line}:${e.col}`); }
```

**`$D/probe.py`**

```python
import sys
from sel import compile as sel_compile
from sel.sql import Sql, Binding
b = {
  'ORDERS': Binding.relation('orders', 'o', {'id': Binding.column('id','o','NUM'), 'customer_id': Binding.column('customer_id','o','NUM'), 'amount': Binding.column('amount','o','NUM'), 'name': Binding.column('name','o','TEXT')}),
  'CUSTOMERS': Binding.relation('customers', 'c', {'id': Binding.column('id','c','NUM'), 'name': Binding.column('name','c','TEXT')}),
}
src = sys.argv[1]; dialect = sys.argv[2] if len(sys.argv) > 2 else 'mariadb'
p = sel_compile(src)
try: print('TS: ' + Sql.translate_statement(p, dialect, b).as_statement())
except Exception as e: print(f'TS: {getattr(e,"code",type(e).__name__)}@{getattr(e,"line","?")}:{getattr(e,"col","?")}')
try:
    plan = Sql.plan_hybrid(p, dialect, b)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    print(f'PLAN: {kind} {plan.sql_statement.as_statement() if plan.sql_statement else ""}')
except Exception as e: print(f'PLAN: {getattr(e,"code",type(e).__name__)}@{getattr(e,"line","?")}:{getattr(e,"col","?")}')
```

**`$D/probe.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/bootstrap.php';
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$b = [
  'ORDERS' => Binding::relation('orders', 'o', ['id' => Binding::column('id','o','NUM'), 'customer_id' => Binding::column('customer_id','o','NUM'), 'amount' => Binding::column('amount','o','NUM'), 'name' => Binding::column('name','o','TEXT')]),
  'CUSTOMERS' => Binding::relation('customers', 'c', ['id' => Binding::column('id','c','NUM'), 'name' => Binding::column('name','c','TEXT')]),
];
$src = $argv[1]; $dialect = $argv[2] ?? 'mariadb';
$p = Sel::compile($src);
try { echo 'TS: ' . Sql::translateStatement($p, $dialect, $b)->asStatement() . "\n"; }
catch (\Throwable $e) { echo 'TS: ' . ($e->code ?? get_class($e)) . '@' . ($e->line ?? '?') . ':' . ($e->col ?? '?') . "\n"; }
try {
  $plan = Sql::planHybrid($p, $dialect, $b);
  $kind = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
  echo 'PLAN: ' . $kind . ' ' . ($plan->sqlStatement ? $plan->sqlStatement->asStatement() : '') . "\n";
} catch (\Throwable $e) { echo 'PLAN: ' . ($e->code ?? get_class($e)) . '@' . ($e->line ?? '?') . ':' . ($e->col ?? '?') . "\n"; }
```

**`$D/probe.lisp`**

```lisp
(defun main ()
  (let* ((args sb-ext:*posix-argv*)
         (src (second args))
         (dialect (or (third args) "mariadb"))
         (orders (sel.sql:binding-relation "orders" "o"
                   (list (cons "id" (sel.sql:binding-column "id" "o" :num))
                         (cons "customer_id" (sel.sql:binding-column "customer_id" "o" :num))
                         (cons "amount" (sel.sql:binding-column "amount" "o" :num))
                         (cons "name" (sel.sql:binding-column "name" "o" :text)))))
         (customers (sel.sql:binding-relation "customers" "c"
                      (list (cons "id" (sel.sql:binding-column "id" "c" :num))
                            (cons "name" (sel.sql:binding-column "name" "c" :text)))))
         (b (list (cons "ORDERS" orders) (cons "CUSTOMERS" customers)))
         (p (sel:compile-source src)))
    (handler-case (format t "TS: ~a~%" (sel.sql:as-statement (sel.sql:translate-statement p dialect b)))
      (sel.sql:sql-error (e) (format t "TS: ~a@~a:~a~%" (sel.sql:sql-error-code e) (sel.sql:sql-error-line e) (sel.sql:sql-error-col e))))
    (handler-case
        (let* ((plan (sel.sql:plan-hybrid p dialect b))
               (kind (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                           ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                           (t "hybrid"))))
          (format t "PLAN: ~a ~a~%" kind (if (sel.sql:hybrid-plan-sql-statement plan) (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan)) "")))
      (sel.sql:sql-error (e) (format t "PLAN: ~a@~a:~a~%" (sel.sql:sql-error-code e) (sel.sql:sql-error-line e) (sel.sql:sql-error-col e))))))
```

**`$D/probe-lisp.sh`**

```bash
#!/usr/bin/env bash
exec sbcl --noinform --disable-debugger --non-interactive --load /home/nathan/workspaces/nth-share/sel/lisp/bin/boot.lisp --load /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-W2/probe.lisp --eval '(main)' --end-toplevel-options "$@"
```

**`$D/probe.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <iostream>
using namespace sel::sql;
int main(int argc, char** argv) {
  std::string src = argv[1]; std::string dialect = argc > 2 ? argv[2] : "mariadb";
  Binding orders = Binding::relation("orders", "o", {
    {"id", Binding::column("id", "o", SqlKind::Num)},
    {"customer_id", Binding::column("customer_id", "o", SqlKind::Num)},
    {"amount", Binding::column("amount", "o", SqlKind::Num)},
    {"name", Binding::column("name", "o", SqlKind::Text)}});
  Binding customers = Binding::relation("customers", "c", {
    {"id", Binding::column("id", "c", SqlKind::Num)},
    {"name", Binding::column("name", "c", SqlKind::Text)}});
  const Bindings b({{"ORDERS", orders}, {"CUSTOMERS", customers}});
  const sel::Program p = sel::compile(src);
  try { std::cout << "TS: " << Sql::translate_statement(p, dialect, b).as_statement() << "\n"; }
  catch (const SqlError& e) { std::cout << "TS: " << e.code() << "@" << e.line() << ":" << e.col() << "\n"; }
  try {
    HybridPlan plan = Sql::plan_hybrid(p, dialect, b);
    std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    std::cout << "PLAN: " << kind << " " << (plan.sql_statement ? plan.sql_statement->as_statement() : "") << "\n";
  } catch (const SqlError& e) { std::cout << "PLAN: " << e.code() << "@" << e.line() << ":" << e.col() << "\n"; }
}
```

**`$D/all.sh`**

```bash
#!/usr/bin/env bash
cd /home/nathan/workspaces/nth-share/sel
D=/tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-W2
P="$1"; DL="${2:-mariadb}"
echo "== $P [$DL]"
echo "js:   $(node $D/probe.mjs "$P" "$DL" | tr '\n' '|')"
echo "php:  $(php $D/probe.php "$P" "$DL" | tr '\n' '|')"
echo "py:   $(PYTHONPATH=$PWD/python python3 $D/probe.py "$P" "$DL" | tr '\n' '|')"
echo "cpp:  $($D/probe-cpp "$P" "$DL" | tr '\n' '|')"
echo "lisp: $($D/probe-lisp.sh "$P" "$DL" 2>&1 | grep -E '^(TS|PLAN):' | tr '\n' '|')"
```

**`$D/mem.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
const ctx = { ORDERS: [{id:'1',customer_id:'1',amount:'5',name:'a'},{id:'2',customer_id:'1',amount:'6',name:'b'},{id:'2',customer_id:'1',amount:'7',name:'c'}], CUSTOMERS: [{id:'1',name:'cust'}] };
for (const src of process.argv.slice(2)) {
  try { console.log(src, '=>', compile(src).run(ctx).dump()); } catch (e) { console.log(src, '=>', `${e.code}@${e.line}:${e.col}`); }
}
```

**`$D/mem.py`**

```python
import sys
from sel import compile as sel_compile
ctx = {'ORDERS': [{'id':'1','customer_id':'1','amount':'5','name':'a'},{'id':'2','customer_id':'1','amount':'6','name':'b'},{'id':'2','customer_id':'1','amount':'7','name':'c'}], 'CUSTOMERS': [{'id':'1','name':'cust'}]}
for src in sys.argv[1:]:
    try: print(src, '=>', sel_compile(src).run(ctx).dump()[:200])
    except Exception as e: print(src, '=>', f'{getattr(e,"code",type(e).__name__)}@{getattr(e,"line","?")}:{getattr(e,"col","?")}')
```

**`$D/opt.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { parse } from '/home/nathan/workspaces/nth-share/sel/js/src/parser.mjs';
import { optimizeAstLogical, optimizeAstInMemory, unwindPipeline } from '/home/nathan/workspaces/nth-share/sel/js/src/optimizer.mjs';
const src = process.argv[2];
const strip = (ast) => JSON.stringify(ast, (k, v) => (k === 'pos' || k === 'spec' ? undefined : v));
console.log('raw   ', strip(parse(src)));
console.log('logic ', strip(optimizeAstLogical(parse(src))));
```

**`$D/exec.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const b = {
  ORDERS: Binding.relation('orders', 'o', { id: Binding.column('id', 'o', 'NUM'), customer_id: Binding.column('customer_id', 'o', 'NUM'), amount: Binding.column('amount','o','NUM'), name: Binding.column('name','o','TEXT') }),
  CUSTOMERS: Binding.relation('customers', 'c', { id: Binding.column('id', 'c', 'NUM'), name: Binding.column('name', 'c', 'TEXT') }),
};
const ctx = { ORDERS: [{id:'1',customer_id:'1',amount:'5',name:'a'},{id:'2',customer_id:'1',amount:'6',name:'b'},{id:'2',customer_id:'1',amount:'7',name:'c'}], CUSTOMERS: [{id:'1',name:'cust'}] };
// fake DB runner: joined rows as o.* (what the prefix selects)
const runner = (sql) => { console.log('  runner got:', sql); return ctx.ORDERS.filter(r => r.customer_id === '1'); };
const src = process.argv[2];
const p = compile(src);
const f = (fn) => { try { return fn(); } catch (e) { return `${e.code}@${e.line}:${e.col}`; } };
console.log('run():   ', String(f(() => p.run(ctx).dump())).slice(0, 120));
const plan = Sql.planHybrid(p, 'mariadb', b);
console.log('plan:    ', plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid');
console.log('hybrid:  ', String(f(() => Sql.executeHybrid(plan, runner, ctx).dump())).slice(0, 120));
```

**`$D/mem.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/bootstrap.php';
use Sel\Sel;
$ctx = ['ORDERS' => [['id'=>'1','customer_id'=>'1','amount'=>'5','name'=>'a'],['id'=>'2','customer_id'=>'1','amount'=>'6','name'=>'b'],['id'=>'2','customer_id'=>'1','amount'=>'7','name'=>'c']], 'CUSTOMERS' => [['id'=>'1','name'=>'cust']]];
foreach (array_slice($argv, 1) as $src) {
  try { echo $src, ' => ', substr(Sel::compile($src)->run($ctx)->dump(), 0, 150), "\n"; }
  catch (\Throwable $e) { echo $src, ' => ', ($e->code ?? get_class($e)), '@', ($e->line ?? '?'), ':', ($e->col ?? '?'), "\n"; }
}
```

**`$D/mem.lisp`**

```lisp
(defun main ()
  (let* ((src (second sb-ext:*posix-argv*))
         (ctx (list (cons "ORDERS" (list (list (cons "id" "1") (cons "customer_id" "1") (cons "amount" "5") (cons "name" "a"))
                                         (list (cons "id" "2") (cons "customer_id" "1") (cons "amount" "6") (cons "name" "b"))
                                         (list (cons "id" "2") (cons "customer_id" "1") (cons "amount" "7") (cons "name" "c"))))
                    (cons "CUSTOMERS" (list (list (cons "id" "1") (cons "name" "cust")))))))
    (handler-case (format t "MEM: ~a~%" (subseq (sel:value-dump (sel:evaluate src ctx)) 0 (min 150 (length (sel:value-dump (sel:evaluate src ctx))))))
      (sel:sel-error (e) (format t "MEM: ~a@~a:~a~%" (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e))))))
```

**`$D/mem.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include <iostream>
using sel::Value;
static Value row(std::vector<std::string> k, std::vector<std::string> v) { std::vector<Value> vs; for (auto& s : v) vs.push_back(Value::text(s)); return Value::record(k, vs); }
int main(int argc, char** argv) {
  Value ctx = Value::record({"ORDERS", "CUSTOMERS"}, {
    Value::list({row({"id","customer_id","amount","name"},{"1","1","5","a"}), row({"id","customer_id","amount","name"},{"2","1","6","b"}), row({"id","customer_id","amount","name"},{"2","1","7","c"})}),
    Value::list({row({"id","name"},{"1","cust"})})});
  try { std::cout << "MEM: " << sel::compile(argv[1]).run(ctx).dump().substr(0,150) << "\n"; }
  catch (const sel::SelError& e) { std::cout << "MEM: " << e.code() << "@" << e.line() << ":" << e.col() << "\n"; }
}
```

**`$D/exec.py`**

```python
import sys
from sel import compile as sel_compile
from sel.sql import Sql, Binding
b = {
  'ORDERS': Binding.relation('orders', 'o', {'id': Binding.column('id','o','NUM'), 'customer_id': Binding.column('customer_id','o','NUM'), 'amount': Binding.column('amount','o','NUM'), 'name': Binding.column('name','o','TEXT')}),
  'CUSTOMERS': Binding.relation('customers', 'c', {'id': Binding.column('id','c','NUM'), 'name': Binding.column('name','c','TEXT')}),
}
ctx = {'ORDERS': [{'id':'1','customer_id':'1','amount':'5','name':'a'},{'id':'2','customer_id':'1','amount':'6','name':'b'},{'id':'2','customer_id':'1','amount':'7','name':'c'}], 'CUSTOMERS': [{'id':'1','name':'cust'}]}
runner = lambda sql, *a: [r for r in ctx['ORDERS'] if r['customer_id'] == '1']
p = sel_compile(sys.argv[1])
def f(fn):
    try: return fn()
    except Exception as e: return f'{getattr(e,"code",type(e).__name__)}@{getattr(e,"line","?")}:{getattr(e,"col","?")}'
print('run():  ', str(f(lambda: p.run(ctx).dump()))[:100])
plan = Sql.plan_hybrid(p, 'mariadb', b)
print('plan:   ', 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid')
print('hybrid: ', str(f(lambda: Sql.execute_hybrid(plan, runner, ctx).dump()))[:100])
```

### Commands run and their output



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
grep -n "sqlPrefix\|pureSql\|pureMemory\|continuation" js/src/sql/hybrid.mjs | head -30
```

```
3:// of truth for the in-memory continuation.
33:  constructor({ dialect = null, sqlStatement = null, sqlPrefixAst = null, continuationAst = null,
34:    continuationProgram = null, continuationSourceVar = '_INPUT', pureSql = false,
35:    pureMemory = false, sourceTables = [] } = {}) {
38:    this.sqlPrefixAst = sqlPrefixAst;
39:    this.continuationAst = continuationAst;
40:    this.continuationProgram = continuationProgram;
41:    this.continuationSourceVar = continuationSourceVar;
42:    this.pureSql = Boolean(pureSql);
43:    this.pureMemory = Boolean(pureMemory);
49:  get sql_prefix_ast() { return this.sqlPrefixAst; }
50:  get continuation_ast() { return this.continuationAst; }
51:  get continuation_program() { return this.continuationProgram; }
52:  get continuation_source_var() { return this.continuationSourceVar; }
53:  get isHybrid() { return !this.pureSql && !this.pureMemory; }
55:  get pure_sql() { return this.pureSql; }
56:  get pure_memory() { return this.pureMemory; }
57:  get pureSqlExecution() { return this.pureSql; }
58:  get pureMemoryExecution() { return this.pureMemory; }
59:  get pureSqlP() { return this.pureSql; }
60:  get pureMemoryP() { return this.pureMemory; }
120:// seals it: the members are gone, and no continuation can get them back. So a
264:  const continuationMap = { ...mapStep,
271:    sqlPrefixAst: rewrittenAst,
272:    continuationAst: continuationMap,
273:    continuationProgram: new Program('', continuationMap),
279:// continuation is the program itself, and the AST it exposes is the program's
281:function pureMemoryPlan(program, dialect, catalog) {
282:  return new HybridPlan({ dialect, pureMemory: true, continuationProgram: program,
283:    continuationAst: program.ast, sourceTables: sourceTables(program.ast, catalog) });
```



```bash
D=$SCRATCH/verify-W2 && sed -i 's/plan.sqlPrefix ? (plan.sqlPrefix.asStatement ? plan.sqlPrefix.asStatement() : String(plan.sqlPrefix)) : .*/plan.sqlStatement ?? "";/' $D/probe.mjs && node $D/probe.mjs 'ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))'
```

```
Exit code 1
file://$SCRATCH/verify-W2/probe.mjs:19



SyntaxError: Unexpected end of input
    at compileSourceTextModule (node:internal/modules/esm/utils:338:16)
    at ModuleLoader.moduleStrategy (node:internal/modules/esm/translators:102:18)
    at #translate (node:internal/modules/esm/loader:437:12)
    at ModuleLoader.loadAndTranslate (node:internal/modules/esm/loader:484:27)
    at async ModuleJob._link (node:internal/modules/esm/module_job:115:19)

Node.js v22.12.0
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
node $D/probe.mjs 'ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))'
```

```
TS: SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
PLAN: pure_sql [object Object]
```



```bash
D=$SCRATCH/verify-W2 && sed -n 25,50p js/src/sql/hybrid.mjs; grep -n "class Fragment" -A15 js/src/sql/*.mjs | grep -n "asStatement\|get \|toString" | head
```

```
import * as sqlmap from './map.mjs';
import * as constants from './constants.mjs';
import * as normalise from './normalise.mjs';
import { Bindings } from './bindings.mjs';
import { SqlError } from './errors.mjs';
import { Translator } from './translator.mjs';

export class HybridPlan {
  constructor({ dialect = null, sqlStatement = null, sqlPrefixAst = null, continuationAst = null,
    continuationProgram = null, continuationSourceVar = '_INPUT', pureSql = false,
    pureMemory = false, sourceTables = [] } = {}) {
    this.dialect = dialect;
    this.sqlStatement = sqlStatement;
    this.sqlPrefixAst = sqlPrefixAst;
    this.continuationAst = continuationAst;
    this.continuationProgram = continuationProgram;
    this.continuationSourceVar = continuationSourceVar;
    this.pureSql = Boolean(pureSql);
    this.pureMemory = Boolean(pureMemory);
    this.sourceTables = sourceTables;
  }

  get sql_query() { return this.sqlStatement; }
  get sqlQuery() { return this.sqlStatement; }
  get sql_prefix_ast() { return this.sqlPrefixAst; }
  get continuation_ast() { return this.continuationAst; }
```



```bash
D=$SCRATCH/verify-W2 && sed -i 's/plan.sqlStatement ?? ..}/plan.sqlStatement ? plan.sqlStatement.asStatement() : ""}/' $D/probe.mjs && for p in 'ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))' 'ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"]))' 'ORDERS .> MAP(RECORD("id", _["id"])) .> FILTER(_["ID"] > 1)' 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"]))' 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)'; do echo "== $p"; node $D/probe.mjs "$p"; done
```

```
== ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))
TS: SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
PLAN: pure_sql SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
== ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"]))
TS: SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`
PLAN: pure_sql SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`
== ORDERS .> MAP(RECORD("id", _["id"])) .> FILTER(_["ID"] > 1)
TS: SELECT `_sub1`.* FROM (SELECT `o`.`id` AS `id` FROM `orders` `o`) `_sub1` WHERE (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1)
PLAN: pure_sql SELECT `_sub1`.* FROM (SELECT `o`.`id` AS `id` FROM `orders` `o`) `_sub1` WHERE (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1)
== ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"]))
TS: SELECT `c`.`name` AS `name` FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)
PLAN: pure_sql SELECT `c`.`name` AS `name` FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)
== ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)
TS: E_SQL_UNBOUND@1:72
PLAN: hybrid SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)
```



```bash
D=$SCRATCH/verify-W2 && sed -i "s/amount: Binding.column('amount','o','NUM') }/amount: Binding.column('amount','o','NUM'), name: Binding.column('name','o','TEXT') }/" $D/probe.mjs && for p in 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"]))' 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["amount"]))'; do echo "== $p"; node $D/probe.mjs "$p"; done
```

```
== ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"]))
TS: E_SQL_SHAPE@1:81
PLAN: hybrid SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)
== ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["amount"]))
TS: SELECT `o`.`amount` AS `name` FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)
PLAN: pure_sql SELECT `o`.`amount` AS `name` FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)
```



```bash
D=$SCRATCH/verify-W2 && grep -n "def plan_hybrid\|class HybridPlan\|sql_statement\|pure_sql\b" python/sel/sql/hybrid.py | head; grep -n "def as_statement\|def relation\|def column" python/sel/sql/*.py | head
```

```
32:class HybridPlan:
35:    def __init__(self, *, dialect: str | None = None, sql_statement: Any = None,
40:                 pure_sql: bool = False, pure_memory: bool = False,
43:        self.sql_statement = sql_statement
48:        self.pure_sql = bool(pure_sql)
54:        return self.sql_statement
58:        return self.sql_statement
78:        return not self.pure_sql and not self.pure_memory
86:        return self.pure_sql
94:        return self.pure_sql
python/sel/sql/binding.py:52:    def column(column: Any, table: Any = None, type: Any = 'UNKNOWN',  # noqa: A002
python/sel/sql/binding.py:130:    def columns(*items: 'Binding') -> 'Binding':
python/sel/sql/binding.py:148:    def relation(from_: Any, alias: Any = None, fields: Any = None,
python/sel/sql/binding.py:174:    def relation_query(query: Any, alias: Any = None, fields: Any = None,
python/sel/sql/binder.py:39:    def column(column: dict[str, Any]) -> 'Binder':
python/sel/sql/fragment.py:64:    def as_statement(self, mode: str = 'inline') -> str:
python/sel/sql/emit.py:234:    def column(self, table: str | None, column: str) -> str:
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
grep -n "public.*\$pureSql\|public.*\$sqlStatement\|public.*\$pureMemory\|function __get" php/src/Sql/HybridPlan.php php/src/Sql/Hybrid*.php 2>/dev/null | head; grep -n "code\|line\|col" php/src/Sql/SqlError.php | head -5; grep -n "class SqlError" -A12 php/src/Sql/*.php | head -20
```

```
2:// Translator errors. See sql/errors.md — codes are contract, messages are not,
12:    // Untyped so it can override Exception::$code, whose type must be omitted.
14:    public $code;
15:    public int $line;
16:    public int $col;
php/src/Sql/SqlError.php:10:final class SqlError extends \Exception
php/src/Sql/SqlError.php-11-{
php/src/Sql/SqlError.php-12-    // Untyped so it can override Exception::$code, whose type must be omitted.
php/src/Sql/SqlError.php-13-    /** @var string */
php/src/Sql/SqlError.php-14-    public $code;
php/src/Sql/SqlError.php-15-    public int $line;
php/src/Sql/SqlError.php-16-    public int $col;
php/src/Sql/SqlError.php-17-    public int $offset;
php/src/Sql/SqlError.php-18-
php/src/Sql/SqlError.php-19-    /** @param array{line:int,col:int,offset:int}|null $pos */
php/src/Sql/SqlError.php-20-    public function __construct(string $code, string $message, ?array $pos = null)
php/src/Sql/SqlError.php-21-    {
php/src/Sql/SqlError.php-22-        parent::__construct($message);
```



```bash
D=$SCRATCH/verify-W2 && P='ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))'; PYTHONPATH=$PWD/python python3 $D/probe.py "$P"; php $D/probe.php "$P"
```

```
TS: SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
PLAN: pure_sql SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
TS: SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
PLAN: pure_sql SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
```



```bash
cat lisp/bin/sqlt | head -30; grep -n "plan-hybrid\|hybrid-plan-\|sql-statement\|pure-sql" lisp/src/sql/hybrid.lisp | head -20; grep -n "sql-error-code\|sql-error-line\|sql-error-col\|define-condition" lisp/src/sql/errors.lisp | head
```

```
#!/usr/bin/env bash
# Runs the SEL->SQL conformance suite against the Common Lisp implementation.
# Run from the repository root.
here="$(cd "$(dirname "$0")" && pwd)"
exec sbcl --noinform --disable-debugger --non-interactive \
  --load "$here/boot.lisp" \
  --load "$here/sqlt.lisp" \
  --eval '(sb-ext:exit :code (sel-sqlt:main))' \
  --end-toplevel-options "$@"
16:  (sql-statement nil)
21:  (pure-sql-p nil :type boolean)
25:(defun hybrid-plan-hybrid-p (plan)
27:  (not (or (hybrid-plan-pure-sql-p plan) (hybrid-plan-pure-memory-p plan))))
64:(defun plan-hybrid (program dialect &optional bindings options)
81:                    (sql-error () (return-from plan-hybrid (pure-memory-plan program dialect bs))))))
83:          (return-from plan-hybrid (pure-memory-plan program dialect bs)))
90:            (return-from plan-hybrid (pure-memory-plan program dialect bs)))
99:                (return-from plan-hybrid
101:                                    :sql-statement full-frag
103:                                    :pure-sql-p t
108:                (setf (hybrid-plan-dialect ft-plan) dialect
109:                      (hybrid-plan-source-tables ft-plan)
110:                      (source-tables (hybrid-plan-sql-prefix-ast ft-plan) bs))
111:                (return-from plan-hybrid ft-plan)))
126:                    (return-from plan-hybrid
128:                                        :sql-statement frag
321:                             :sql-statement sql-frag
326:                             :pure-sql-p nil
333:    ((hybrid-plan-pure-sql-p plan)
9:(define-condition sql-error (error)
10:  ((code :initarg :code :reader sql-error-code)
12:   (line :initarg :line :initform 0 :reader sql-error-line)
13:   (col :initarg :col :initform 0 :reader sql-error-col)
16:             (format s "~a at ~a:~a: ~a" (sql-error-code c) (sql-error-line c)
17:                     (sql-error-col c) (sql-error-message c))))
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
chmod +x $D/probe-lisp.sh; grep -n "type\b\|:num\|:text\|kind" lisp/src/sql/binding.lisp | sed -n 1,15p; $D/probe-lisp.sh 'ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))' 2>&1 | tail -5
```

```
14:(defstruct (binding (:constructor %binding (kind spec)))
17:  (kind :column)
18:  (spec '() :type list))
44:(defun check-binding-type (type)
45:  (unless (member type +kinds+)
47:            (format nil "a binding has type ~s; use one of ~{~a~^, ~}"
48:                    type (mapcar #'kind-name +kinds+)))))
59:            (format nil "~a declares type NUM, which asks for it to be emitted ~
73:              (format nil "~a declares type NUM and is ~s, which is not how SEL ~
77:;;; --- the four kinds -------------------------------------------------------
89:(defun binding-column (column &optional table (type :unknown) &key exact sargable guard collation prefilter split-sargable)
92:TYPE is what the kind guards read, and leaving it :UNKNOWN is honest rather than
93:free. An UNKNOWN operand still passes the guards that only rule kinds out --
101:  (check-binding-type type)
110:        (spec (list :column column :table table :type type
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```



```bash
D=$SCRATCH/verify-W2 && $D/probe-lisp.sh 'ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))' 2>&1 | head -12; grep -n "+kinds+" lisp/src/sql/*.lisp | head -3
```

```
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=819148 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 5, Column: 42, File-Position: 168

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-W2/probe.lisp" {1201EBE8C3}>

Backtrace for: #<SB-THREAD:THREAD tid=819148 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201EBFB43}> #<unused argument> :QUIT T)
lisp/src/sql/binding.lisp:45:  (unless (member type +kinds+)
lisp/src/sql/binding.lisp:48:                    type (mapcar #'kind-name +kinds+)))))
lisp/src/sql/fragment.lisp:11:(defparameter +kinds+ '(:num :text :bool :bin :unknown :list :statement))
```



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



```bash
D=$SCRATCH/verify-W2 && grep -n "in-package\|defpackage" lisp/bin/sqlt.lisp | head -3; grep -n "sql\b\|:sel\.sql\|sel-sql" lisp/sel-lang.asd | head; ls lisp/src/sql/; grep -n "defpackage" lisp/src/sql/package.lisp
```

```
28:(defpackage #:sel-sqlt
32:(in-package #:sel-sqlt)
53:;;; evaluator should not carry the dialect map. `(ql:quickload :sel-lang/sql)`
55:(defsystem "sel-lang/sql"
63:  ((:module "src/sql"
85:  :depends-on ("sel-lang" "sel-lang/sql" "fiveam")
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
12:(defpackage #:sel.sql
```



```bash
D=$SCRATCH/verify-W2 && sed -n 28,32p lisp/bin/sqlt.lisp; sed -i '1i (let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' $D/probe.lisp && $D/probe-lisp.sh 'ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))' 2>&1 | head -12
```

```
(defpackage #:sel-sqlt
  (:use #:common-lisp #:sel.sql)
  (:export #:main))

(in-package #:sel-sqlt)
TS: SELECT CASE WHEN (`_sub1`.`id` > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
PLAN: pure_sql SELECT CASE WHEN (`_sub1`.`id` > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
cd cpp && c++ -std=c++23 -O1 -o $D/probe-cpp $D/probe.cpp build/sel_sql*.o build/sel.o 2>&1 | head; $D/probe-cpp 'ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))'
```

```
TS: SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
PLAN: pure_sql SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
chmod +x $D/all.sh
$D/all.sh 'ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x"))'
$D/all.sh 'ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"]))'
```

```
== ORDERS .> TAKE(2) .> MAP(IF(_["id"] > 1, "x")) [mariadb]
js:   TS: SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`|PLAN: pure_sql SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`|
php:  TS: SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`|PLAN: pure_sql SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`|
py:   TS: SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`|PLAN: pure_sql SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`|
cpp:  TS: SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`|PLAN: pure_sql SELECT CASE WHEN (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`|
lisp: TS: SELECT CASE WHEN (`_sub1`.`id` > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`|PLAN: pure_sql SELECT CASE WHEN (`_sub1`.`id` > 1) THEN 'x' ELSE '' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`|
== ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"])) [mariadb]
js:   TS: SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`|PLAN: pure_sql SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`|
php:  TS: SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`|PLAN: pure_sql SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`|
py:   TS: SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`|PLAN: pure_sql SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`|
cpp:  TS: SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`|PLAN: pure_sql SELECT `_sub1`.`id` AS `id` FROM (SELECT DISTINCT `o`.* FROM `orders` `o`) `_sub1`|
lisp: TS: SELECT DISTINCT `o`.`id` AS `id` FROM `orders` `o`|PLAN: pure_sql SELECT DISTINCT `o`.`id` AS `id` FROM `orders` `o`|
```



```bash
D=$SCRATCH/verify-W2 && $D/all.sh 'ORDERS .> MAP(RECORD("id", _["id"])) .> FILTER(_["ID"] > 1)' postgresql; $D/all.sh 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"]))'; $D/all.sh 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)'
```

```
== ORDERS .> MAP(RECORD("id", _["id"])) .> FILTER(_["ID"] > 1) [postgresql]
js:   TS: SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)|PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)|
php:  TS: SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)|PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)|
py:   TS: SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)|PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)|
cpp:  TS: SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" WHERE (CASE WHEN (CAST("_sub1"."ID" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."ID" AS NUMERIC) ELSE NULL END > 1)|PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" WHERE (CASE WHEN (CAST("_sub1"."ID" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."ID" AS NUMERIC) ELSE NULL END > 1)|
lisp: TS: SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)|PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" WHERE (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END > 1)|
== ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"])) [mariadb]
js:   TS: E_SQL_SHAPE@1:81|PLAN: hybrid SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)|
php:  TS: E_SQL_SHAPE@1:81|PLAN: hybrid SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)|
py:   TS: E_SQL_SHAPE@1:81|PLAN: hybrid SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)|
cpp:  TS: SELECT `o`.`name` AS `name` FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)|PLAN: pure_sql SELECT `o`.`name` AS `name` FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)|
lisp: TS: E_SQL_SHAPE@1:81|PLAN: hybrid SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)|
== ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1) [mariadb]
js:   TS: E_SQL_UNBOUND@1:72|PLAN: hybrid SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)|
php:  TS: E_SQL_UNBOUND@1:72|PLAN: hybrid SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)|
py:   TS: E_SQL_UNBOUND@1:72|PLAN: hybrid SELECT `o
… (truncated)
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
node $D/mem.mjs 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)' 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"]))' 'ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"]))' 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(_)'
```

```
ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1) => -{"1"=-{"O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b", "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b", "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}}, "_1"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b", "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}}, "C"=-{"id"=t"1", "name"=t"cust", "C"=-{"id"=t"1", "name"=t"cust"}, "c"=-{"id"=t"1", "name"=t"cust"}}, "c"=-{"id"=t"1", "name"=t"cust", "C"=-{"id"=t"1", "name"=t"cust"}, "c"=-{"id"=t"1", "name"=t"cust"}}, "_2"=-{"id"=t"1", "name"=t"cust", "C"=-{"id"=t"1", "name"=t"cust"}, "c"=-{"id"=t"1", "name"=t"cust"}}, "customer_id"=t"1", "amount"=t"6"}, "2"=-{"O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c"}, "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c", "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c"}}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c", "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c"}}, "_1"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c", "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"7", "name"=t"c"}}, "C"=-{"id"=t"1", "name"=t"cust", "C"=-{"id"=t"1", "name"=t"cust"}, "c"=-{"id"=t"1", "name"=t"cust"}}, "c"=-{"id"=t"1", "name"=t"cust", "C"=-{"id"=t"1", "name"=t"cust"}, "c"=-{"id"=t"1", "name"=t"cust"}}, "_2"=-{"id"=t"1", "name"=t"cust", "C"=-{"id"=t"1", "name"=t"cust"}, "c"=-{"id"=t"1", "name"=t"cust"}}, "customer_id"=t"1", "amount"=t"7"}}
ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("name", _["name"])) => E_NO_KEY@1:81
ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"])) => -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"2"}}
ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(_) => -{"1"=-{"ORDERS"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a"}, "orders"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a"}, "ORDERS"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a", "ORDERS"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a"}, "orders"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a"}}, "orders"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a", "ORDERS"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a"}, "orders"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a"}}, "_1"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a", "ORDERS"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a"}, "orders"=-{"id"=t"1", "customer_id"=t"1", "amount"=t"5", "name"=t"a"}}, "CUSTOMERS"=-{"id"=t"1", "name"=t"cust", "CUSTOMERS"=-{"id"=t"1", "name"=t"cust"}, "customers"=-{"id"=t"1", "name"=t"cust"}}, "customers"=-{"id"=t"1", "name"=t"cust", "CUSTOMERS"=-{"id"=t"
… (truncated)
```



```bash
cd $SCRATCH/verify-W2; grep -n '(equal sname "MAP")' -A25 old-translator.lisp | grep -n "wrap-plan-as-derived\|relational-plan-\(limit\|offset\|distinct\|order\|projections\|select-cols\|group-by\)" | head; echo ----; cd /home/nathan/workspaces/nth-share/sel; grep -n '"MAP"' lisp/src/sql/translator.lisp | head
```

```
2:2053-                 (when (or (relational-plan-projections plan)
3:2054-                           (relational-plan-select-cols plan)
4:2055-                           (relational-plan-group-by plan)
5:2056-                           (relational-plan-limit plan)
6:2057-                           (relational-plan-offset plan))
7:2058-                   (setf plan (wrap-plan-as-derived-table plan)))
----
944:(defparameter +aggregates+ '("ALL" "ANY" "MAP" "FILTER" "SUM" "JOIN"))
1216:      (when (equal name "MAP")
1440:    (when (member name '("MAP" "FILTER") :test #'equal)
1921:                         (not (member sname '("FILTER" "MAP") :test #'equal)))
2076:                ((equal sname "MAP")
```



```bash
cd $SCRATCH/verify-W2; grep -n "derived.column = key" old-translator.cpp; grep -n "Joined SEL rows promote unambiguous" old-translator.cpp; cd /home/nathan/workspaces/nth-share/sel; git diff 8fe0e3a ed16df2 -- cpp/sel_sql_translator.cpp | grep -n "^[-+].*\(derived.column\|promote unambiguous\|match\b\)" | head
```

```
512:        derived.column = key;
517:    // Joined SEL rows promote unambiguous fields from the other relations.
```



```bash
cd $SCRATCH/verify-W2; sed -n 500,520p old-translator.cpp; grep -n "if (name === '_' && this.statementPlan !== null && this.statementPlan.joins" old-translator.mjs
```

```
std::string field = ascii_upper(key);
    if (!in_where_ && statement_plan_ && statement_plan_->group_by) {
      auto it = statement_plan_->aggregate_aliases.find(key);
      if (it != statement_plan_->aggregate_aliases.end()) return node(it->second);
      it = statement_plan_->aggregate_aliases.find(field);
      if (it != statement_plan_->aggregate_aliases.end()) return node(it->second);
    }
    if (const ColumnSpec* f = rel.field(field)) {
      if (statement_plan_ && statement_plan_->source_subquery &&
          rel.from == statement_plan_->source_relation.from &&
          rel.alias == statement_plan_->source_relation.alias) {
        ColumnSpec derived = *f;
        derived.column = key;
        return relation_column(rel, derived);
      }
      return relation_column(rel, *f);
    }
    // Joined SEL rows promote unambiguous fields from the other relations.
    // Resolve that same shape here instead of refusing a field that is absent
    // from the left relation but present on exactly one joined relation.
    const auto same_relation = [](const RelationSpec& left, const RelationSpec& right) {
975:      if (name === '_' && this.statementPlan !== null && this.statementPlan.joins?.length) {
```



```bash
D=$SCRATCH/verify-W2 && node $D/mem.mjs 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> MAP(O["id"])' 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> TAKE(1) .> MAP(C["name"])' 'ORDERS .> TAKE(1) .> MAP(Q["id"])' 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> TAKE(1) .> MAP(KEYS(_))'
```

```
ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> MAP(O["id"]) => E_UNDEF_VAR@1:69
ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> TAKE(1) .> MAP(C["name"]) => E_UNDEF_VAR@1:80
ORDERS .> TAKE(1) .> MAP(Q["id"]) => E_UNDEF_VAR@1:26
ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> TAKE(1) .> MAP(KEYS(_)) => E_UNKNOWN_FUNC@1:80
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
P1='ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)'
P2='ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> MAP(O["id"])'
PYTHONPATH=$PWD/python python3 $D/mem.py "$P1" "$P2"; node $D/mem.mjs "$P1" "$P2" | cut -c1-200
```

```
ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1) => -{"1"=-{"O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b", "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"
ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> MAP(O["id"]) => E_UNDEF_VAR@1:69
ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1) => -{"1"=-{"O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "o"=-{"id"=t"2", "customer_id"=t"1", "a
ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> MAP(O["id"]) => E_UNDEF_VAR@1:69
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
node $D/opt.mjs 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)'
```

```
raw    {"t":"call","name":"FILTER","args":[{"t":"call","name":"LINK","args":[{"t":"var","name":"ORDERS"},{"t":"var","name":"CUSTOMERS"},{"t":"var","name":"O"},{"t":"var","name":"C"},{"t":"bin","op":"==","l":{"t":"index","obj":{"t":"var","name":"O"},"idx":{"t":"text","v":"customer_id"}},"r":{"t":"index","obj":{"t":"var","name":"C"},"idx":{"t":"text","v":"id"}}}]},{"t":"bin","op":">","l":{"t":"index","obj":{"t":"var","name":"O"},"idx":{"t":"text","v":"id"}},"r":{"t":"num","v":"1"}}]}
logic  {"t":"call","name":"FILTER","args":[{"t":"call","name":"LINK","args":[{"t":"var","name":"ORDERS"},{"t":"var","name":"CUSTOMERS"},{"t":"var","name":"O"},{"t":"var","name":"C"},{"t":"bin","op":"==","l":{"t":"index","obj":{"t":"var","name":"O"},"idx":{"t":"text","v":"customer_id"}},"r":{"t":"index","obj":{"t":"var","name":"C"},"idx":{"t":"text","v":"id"}}}]},{"t":"bin","op":">","l":{"t":"index","obj":{"t":"var","name":"O"},"idx":{"t":"text","v":"id"}},"r":{"t":"num","v":"1"}}]}
```



```bash
D=$SCRATCH/verify-W2 && node $D/mem.mjs 'ORDERS .> FILTER(id > 1)' 'ORDERS .> MAP(id)' 'ORDERS .> MAP(RECORD("O", 5)) .> FILTER(O > 1)' 'ORDERS .> MAP(RECORD("O", 5)) .> MAP(O)' | cut -c1-200; grep -n "E_UNDEF_VAR" js/src/evaluator.mjs js/src/builtins/*.mjs | head
```

```
ORDERS .> FILTER(id > 1) => E_UNDEF_VAR@1:18
ORDERS .> MAP(id) => E_UNDEF_VAR@1:15
ORDERS .> MAP(RECORD("O", 5)) .> FILTER(O > 1) => E_UNDEF_VAR@1:41
ORDERS .> MAP(RECORD("O", 5)) .> MAP(O) => E_UNDEF_VAR@1:38
ugrep: warning: js/src/evaluator.mjs: No such file or directory
```



```bash
D=$SCRATCH/verify-W2 && cat >> $D/opt.mjs <<'EOF'
console.log('phys  ', strip(optimizeAstInMemory(parse(src))));
EOF
node $D/opt.mjs 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)' | tail -1
```

```
phys   {"t":"call","name":"LINK","args":[{"t":"call","name":"FILTER","args":[{"t":"var","name":"ORDERS"},{"t":"bin","op":">","l":{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"id"}},"r":{"t":"num","v":"1"}}]},{"t":"var","name":"CUSTOMERS"},{"t":"var","name":"O"},{"t":"var","name":"C"},{"t":"bin","op":"==","l":{"t":"index","obj":{"t":"var","name":"O"},"idx":{"t":"text","v":"customer_id"}},"r":{"t":"index","obj":{"t":"var","name":"C"},"idx":{"t":"text","v":"id"}}}]}
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
node $D/exec.mjs 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)'
```

```
run():    -{"1"=-{"O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=
plan:     hybrid
  runner got: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)
hybrid:   E_UNDEF_VAR@1:72
```



```bash
D=$SCRATCH/verify-W2 && P1='ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)'; # (file written, see "Reproduction scripts" above)
php $D/mem.php "$P1"; grep -n "run\b\|--context\|context" cpp/bin/sel.cpp 2>/dev/null | head -5; ls cpp/bin/
```

```
ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1) => -{"1"=-{"O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b", "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "o"=-{"id"=
7://   sel                    REPL, keeping one context across lines
73:        std::cout << show(program.run()) << "\n";
82:  // REPL: one context for the whole session, so assignments persist.
89:        std::cout << show(sel::compile(line).run(root)) << "\n";
api.cpp
ast.cpp
batch.cpp
case_data.cpp
case_data.hpp
check-decimal.cpp
conformance.cpp
e2e.cpp
map_replay.cpp
map_replay.hpp
sel.cpp
sqlfuzz.cpp
sqlmap.cpp
sqlreplay.cpp
sqlt.cpp
```



```bash
P='((RECORD("id",1,"customer_id",1), RECORD("id",2,"customer_id",1))) .> LINK((RECORD("id",1,"name","c")), O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)'; Q='((RECORD("id",1,"customer_id",1), RECORD("id",2,"customer_id",1))) .> LINK((RECORD("id",1,"name","c")), O, C, O["customer_id"] == C["id"]) .> MAP(O["id"])'; for e in "$P" "$Q"; do echo "== $e"; echo "js:   $(node js/bin/sel.mjs -e "$e" 2>&1 | cut -c1-120)"; echo "php:  $(php php/bin/sel -e "$e" 2>&1 | cut -c1-120)"; echo "py:   $(PYTHONPATH=$PWD/python python3 -m sel -e "$e" 2>&1 | cut -c1-120)"; echo "cpp:  $(cpp/build/sel -e "$e" 2>&1 | cut -c1-120)"; echo "lisp: $(lisp/bin/sel -e "$e" 2>&1 | cut -c1-120)"; done
```

```
== ((RECORD("id",1,"customer_id",1), RECORD("id",2,"customer_id",1))) .> LINK((RECORD("id",1,"name","c")), O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)
js:   E_NO_KEY at line 1 column 151: no key "id"
php:  E_NO_KEY at line 1 column 151: no key "id"
py:   E_NO_KEY at line 1 column 151: no key "id"
cpp:  E_NO_KEY at line 1 column 151: no key "id"
lisp: E_NO_KEY at line 1 column 151: no key "id"
== ((RECORD("id",1,"customer_id",1), RECORD("id",2,"customer_id",1))) .> LINK((RECORD("id",1,"name","c")), O, C, O["customer_id"] == C["id"]) .> MAP(O["id"])
js:   E_NO_KEY at line 1 column 132: no key "id"
php:  E_NO_KEY at line 1 column 132: no key "id"
py:   E_NO_KEY at line 1 column 132: no key "id"
cpp:  E_NO_KEY at line 1 column 132: no key "id"
lisp: E_NO_KEY at line 1 column 132: no key "id"
```



```bash
D=$SCRATCH/verify-W2 && grep -n "from-native" lisp/src/value.lisp | head -3; # (file written, see "Reproduction scripts" above)
grep -n "sel-error-code\|sel-error-line\|#:sel-error" lisp/src/package.lisp | head -4; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/mem.lisp --eval '(main)' --end-toplevel-options 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)' 2>&1 | grep -E "^MEM|rror" | head -5
```

```
598:(defun from-native (x)
601:  (from-native-at x 1))
603:(defun from-native-at (x depth)
7:   #:sel-error
8:   #:sel-error-code
9:   #:sel-error-message
10:   #:sel-error-line
MEM: -{"1"=-{"O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "o"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "O"=-{"id"
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
grep -n "class SelError" -A8 cpp/sel.hpp | grep -n "code\|line\|col" | head -4; cd cpp && c++ -std=c++23 -O1 -o $D/mem-cpp $D/mem.cpp build/sel.o 2>&1 | head -5; $D/mem-cpp 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)'; $D/mem-cpp 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> MAP(O["id"])'
```

```
3:49-  SelError(std::string code, std::string message, Pos pos);
5:51-  const std::string& code() const noexcept { return code_; }
7:53-  int line() const noexcept { return pos_.line; }
8:54-  int col() const noexcept { return pos_.col; }
MEM: -{"1"=-{"O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b", "O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b"}, "o"=-{"id"=
MEM: MEM: E_UNDEF_VAR@1:69
```



```bash
D=$SCRATCH/verify-W2 && P='ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"]))'; JS=$(node $D/probe.mjs "$P" sqlite | head -1 | sed 's/^TS: //'); LI=$($D/probe-lisp.sh "$P" sqlite 2>&1 | grep '^TS:' | sed 's/^TS: //'); echo "js:   $JS"; echo "lisp: $LI"; python3 - "$JS" "$LI" <<'EOF'
import sqlite3, sys
db = sqlite3.connect(':memory:')
db.execute('create table orders(id integer, customer_id integer, amount integer, name text)')
db.executemany('insert into orders values (?,?,?,?)', [(1,1,5,'a'),(2,1,6,'b'),(2,1,7,'c')])
for label, sql in zip(['js/php/py/cpp', 'lisp'], sys.argv[1:]):
    print(label, '->', db.execute(sql).fetchall())
EOF
node $D/mem.mjs "$P"
```

```
js:   SELECT "_sub1"."id" AS "id" FROM (SELECT DISTINCT "o".* FROM "orders" "o") "_sub1"
lisp: SELECT DISTINCT "o"."id" AS "id" FROM "orders" "o"
js/php/py/cpp -> [(1,), (2,), (2,)]
lisp -> [(1,), (2,)]
ORDERS .> DEDUPE() .> MAP(RECORD("id", _["id"])) => -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"2"}}
```



```bash
D=$SCRATCH/verify-W2 && $D/all.sh 'ORDERS .> SORT_BY(_["amount"]) .> MAP(RECORD("id", _["id"]))' sqlite 2>&1 | sed 's/|PLAN.*//'; $D/all.sh 'ORDERS .> DROP(1) .> FILTER(_["amount"] + 1 > 3)' postgresql | sed 's/|PLAN.*//'
```

```
== ORDERS .> SORT_BY(_["amount"]) .> MAP(RECORD("id", _["id"])) [sqlite]
js:   TS: SELECT "_sub1"."id" AS "id" FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."amount" ASC) "_sub1"
php:  TS: SELECT "_sub1"."id" AS "id" FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."amount" ASC) "_sub1"
py:   TS: SELECT "_sub1"."id" AS "id" FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."amount" ASC) "_sub1"
cpp:  TS: SELECT "_sub1"."id" AS "id" FROM (SELECT "o".* FROM "orders" "o" ORDER BY "o"."amount" ASC) "_sub1"
lisp: TS: SELECT "o"."id" AS "id" FROM "orders" "o" ORDER BY "o"."amount" ASC
== ORDERS .> DROP(1) .> FILTER(_["amount"] + 1 > 3) [postgresql]
js:   TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1" WHERE ((CAST(CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 3)
php:  TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1" WHERE ((CAST(CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 3)
py:   TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1" WHERE ((CAST(CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 3)
cpp:  TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1" WHERE ((CAST(CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 3)
lisp: TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1" WHERE ((CAST("_sub1"."amount" AS NUMERIC) + CAST(1 AS NUMERIC)) > 3)
```



```bash
D=$SCRATCH/verify-W2 && $D/all.sh 'ORDERS .> TAKE(5) .> MAP(RECORD("n", _["Amount"] * 2))' postgresql | sed 's/|PLAN.*//'; $D/all.sh 'ORDERS .> LINK_LEFT(CUSTOMERS, _1["customer_id"] == _2["id"]) .> FILTER(_["id"] > 1)' postgresql
```

```
== ORDERS .> TAKE(5) .> MAP(RECORD("n", _["Amount"] * 2)) [postgresql]
js:   TS: SELECT (CAST(CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 5) "_sub1"
php:  TS: SELECT (CAST(CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 5) "_sub1"
py:   TS: SELECT (CAST(CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 5) "_sub1"
cpp:  TS: SELECT (CAST(CASE WHEN (CAST("_sub1"."Amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."Amount" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 5) "_sub1"
lisp: TS: SELECT (CAST("_sub1"."amount" AS NUMERIC) * CAST(2 AS NUMERIC)) AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 5) "_sub1"
== ORDERS .> LINK_LEFT(CUSTOMERS, _1["customer_id"] == _2["id"]) .> FILTER(_["id"] > 1) [postgresql]
js:   TS: E_SQL_SHAPE@1:74|PLAN: hybrid SELECT "o".* FROM "orders" "o" LEFT JOIN "customers" "c" ON ("o"."customer_id" = "c"."id")|
php:  TS: E_SQL_SHAPE@1:74|PLAN: hybrid SELECT "o".* FROM "orders" "o" LEFT JOIN "customers" "c" ON ("o"."customer_id" = "c"."id")|
py:   TS: E_SQL_SHAPE@1:74|PLAN: hybrid SELECT "o".* FROM "orders" "o" LEFT JOIN "customers" "c" ON ("o"."customer_id" = "c"."id")|
cpp:  TS: SELECT "o".* FROM "orders" "o" LEFT JOIN "customers" "c" ON ("o"."customer_id" = "c"."id") WHERE ("o"."id" > 1)|PLAN: pure_sql SELECT "o".* FROM "orders" "o" LEFT JOIN "customers" "c" ON ("o"."customer_id" = "c"."id") WHERE ("o"."id" > 1)|
lisp: TS: E_SQL_SHAPE@1:74|PLAN: hybrid SELECT "o".* FROM "orders" "o" LEFT JOIN "customers" "c" ON ("o"."customer_id" = "c"."id")|
```



```bash
D=$SCRATCH/verify-W2 && $D/all.sh 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(C["name"] == "x")' | sed 's/|PLAN.*//'; $D/all.sh 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> FILTER(_2["name"] == "x")' | sed 's/|PLAN.*//'; $D/mem-cpp 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> MAP(C["name"])'; $D/mem-cpp 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(_2["name"])'
```

```
== ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(C["name"] == "x") [mariadb]
js:   TS: E_SQL_INVALID@1:85
php:  TS: E_SQL_INVALID@1:85
py:   TS: E_SQL_INVALID@1:85
cpp:  TS: TS: E_SQL_INVALID@1:85
lisp: TS: E_SQL_INVALID@1:85
== ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> FILTER(_2["name"] == "x") [mariadb]
js:   TS: E_SQL_INVALID@1:82
php:  TS: E_SQL_INVALID@1:82
py:   TS: E_SQL_INVALID@1:82
cpp:  TS: TS: E_SQL_INVALID@1:82
lisp: TS: E_SQL_INVALID@1:82
MEM: MEM: E_UNDEF_VAR@1:69
MEM: MEM: E_UNDEF_VAR@1:65
```



```bash
D=$SCRATCH/verify-W2 && $D/all.sh 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(C["id"] > 1)' | sed 's/|PLAN.*//'; $D/all.sh 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> FILTER(_2["id"] > 1)' | sed 's/|PLAN.*//'; $D/mem-cpp 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(C["id"] > 1)';  $D/mem-cpp 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> FILTER(_2["id"] > 1)'; $D/mem-cpp 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(_2["id"])'
```

```
== ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(C["id"] > 1) [mariadb]
js:   TS: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) WHERE (`c`.`id` > 1)
php:  TS: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) WHERE (`c`.`id` > 1)
py:   TS: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) WHERE (`c`.`id` > 1)
cpp:  TS: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) WHERE (`c`.`id` > 1)
lisp: TS: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) WHERE (`c`.`id` > 1)
== ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> FILTER(_2["id"] > 1) [mariadb]
js:   TS: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) WHERE (`c`.`id` > 1)
php:  TS: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) WHERE (`c`.`id` > 1)
py:   TS: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) WHERE (`c`.`id` > 1)
cpp:  TS: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) WHERE (`c`.`id` > 1)
lisp: TS: SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) WHERE (`c`.`id` > 1)
MEM: -
MEM: -
MEM: MEM: E_UNDEF_VAR@1:65
```



```bash
D=$SCRATCH/verify-W2 && # (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $D/exec.py 'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> FILTER(O["id"] > 1)'
```

```
run():   -{"1"=-{"O"=-{"id"=t"2", "customer_id"=t"1", "amount"=t"6", "name"=t"b", "O"=-{"id"=t"2", "customer_
plan:    hybrid
hybrid:  E_UNDEF_VAR@1:72
```

