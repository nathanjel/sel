# W1. Lisp keeps bound column kinds through a pass-through derived table; the other four degrade them to UNKNOWN

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "One optimiser policy in front of the translator, one rewrite order". Resolved the conservative way: Lisp degrades to UNKNOWN like the other four.

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** lisp, js, python, php, cpp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

When a pipeline step (TAKE/DROP/SORT…) forces a SELECT * derived table, Lisp's plan-output-fields copies each source column's :type into the derived relation while JS/Python/PHP/C++ stamp every derived field UNKNOWN. A later step that reads a bound NUM column therefore renders bare in Lisp but wrapped in the ISNUM/REGEXP guard in the other four, refuses on sqlite (E_SQL_UNSUPPORTED) in the four but translates in Lisp, and IF(TRUE, _["id"], "x") is NUM-vs-TEXT E_SQL_SHAPE in Lisp but a translatable UNKNOWN-vs-TEXT in the four — so the same program is pure_sql in Lisp and hybrid elsewhere (and vice versa), with different SQL text. Reproduced in all five hosts, both translate/translate_statement and plan_hybrid lanes; the four non-Lisp hosts are byte-identical to each other. The code is unchanged since 8fe0e3a in all five hosts, and none of the 33 shared plan.* cases (nor any .sqlt) exercises a numeric column after a pass-through derived table, which is why the suite is green.

## Suggested fix — case first

Decide in spec/docs (docs/SQL-KINDS.md) what kind a column has after a SELECT * derived table, then add the .sqlt case first, e.g. in sql/cases/25-hybrid-plans.sqlt: name plan.hybrid.kind-through-pass-through-derived-table, dialect sqlite, bindings ORDERS {AMOUNT: NUM}, source `ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)`, with a --- plan / --- expect that pins one answer (plus a postgresql twin pinning the guarded-or-bare WHERE text). Smallest code fix if the four-host behaviour is chosen: in lisp/src/sql/translator.lisp:1815 and :1820 replace `(getf (cdr f) :type :unknown)` with `:unknown`. If Lisp's more precise behaviour is chosen: in the other four wraps, copy `sourceField.type` (already looked up for the column name) into the derived field instead of UNKNOWN — js/src/sql/translator.mjs:1734, python/sel/sql/translator.py:1687, php/src/Sql/Translator.php:2422, cpp/sel_sql_translator.cpp:2267-2272 (C++ must also look the source field up; it currently only copies the name).

## Verifier reasoning

Read the derived-table wrap in all five hosts: lisp/src/sql/translator.lisp:1810-1823 (the `t` branch of plan-output-fields builds `:type (getf (cdr f) :type :unknown)` from the source relation's fields, and the join fields likewise), versus js/src/sql/translator.mjs:1719-1735 (`type: 'UNKNOWN'` for every field, source column name copied but not its type), python/sel/sql/translator.py:1670-1697 (`'type': 'UNKNOWN'`), php/src/Sql/Translator.php:2401-2438 (`'type' => 'UNKNOWN'`), cpp/sel_sql_translator.cpp:2251-2275 (`field.type = SqlKind::Unknown`). Only the no-projection/no-select-cols branch differs; Lisp's projection and select-cols branches (lines 1790-1808) also use :unknown, which is why MAP(RECORD(...)) .> FILTER shapes agree everywhere — matching the finding's own caveat. `git show 8fe0e3a:<path>` shows identical lines in all five (Lisp 1811-1823 byte-identical; JS 1734, C++ 2270, PHP 2422, Python 1687 all UNKNOWN), so the defect is pre-existing, not introduced by ed16df2. I then wrote my own probes with a different program set (DROP(1) .> MAP(_["id"] * 2), DROP(1) .> MAP(IF(TRUE,_["id"],"x")), TAKE(2) .> FILTER(_["amount"] > 6), and the projected-column control) and ran translate_statement + plan_hybrid across postgresql/sqlite/mariadb in JS, Python, PHP, C++ (compiled against cpp/build/*.o) and Lisp. Four hosts produced byte-identical output; Lisp differed on every pass-through shape exactly as claimed (bare column vs guarded; sqlite pure_sql vs hybrid; E_SQL_SHAPE@1:26 vs translated), and agreed on the projected-column control. Also reran the reported translate(mariadb) repro: JS E_SQL_INVALID@1:51 vs Lisp E_SQL_SHAPE@1:26, and JS translates IF(TRUE,_["id"],"x") where Lisp refuses. Both cluster members are facets of the same root cause and all facets reproduce, so one CONFIRMED verdict. Severity high under the rubric: different refusal codes, different SQL text and different plan classification between hosts for the same program and bindings, in both the SQL-delegation and hybrid lanes. Neither docs/SQL-KINDS.md nor docs/SQL-TRANSLATION.md states what kind a column has after a SELECT * derived table, so the spec/conformance corpus does not currently decide which side is right; a new .sqlt case must be added first per the project's one rule. Lisp's behaviour is arguably more precise (the kind genuinely survives SELECT *), but the product is agreement.

## Verifier evidence

```
Code read: lisp/src/sql/translator.lisp:1810-1823 (`:type (getf (cdr f) :type :unknown)` in the pass-through branch of plan-output-fields; projection branch 1790-1803 and select-cols branch 1805-1808 use `:type :unknown`); js/src/sql/translator.mjs:1733-1735 (`type: 'UNKNOWN'`); python/sel/sql/translator.py:1683-1688 (`'type': 'UNKNOWN'`); php/src/Sql/Translator.php:2418-2423 (`'type' => 'UNKNOWN'`); cpp/sel_sql_translator.cpp:2267-2273 (`field.type = SqlKind::Unknown`). Pre-commit: `git show 8fe0e3a:lisp/src/sql/translator.lisp | grep -n -A12 "All fields from source and joins"` -> identical lines 1811-1823; `git show 8fe0e3a:js/src/sql/translator.mjs` line 1734 `type: 'UNKNOWN'`, cpp 2270, php 2422, python 1687 — all unchanged.

Own repro, bindings ORDERS=relation(orders,o,{ID:NUM, AMOUNT:NUM, NAME:TEXT}); scripts under scratchpad/verify-W1/{probe.mjs,probe.py,probe.php,probe.cpp,probe.lisp}. JS, Python, PHP and C++ output was byte-identical; Lisp differed:

postgresql | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  JS/PY/PHP/CPP TS+PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END > 6)
  LISP TS+PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE ("_sub1"."amount" > 6)
sqlite | same program
  JS/PY/PHP/CPP: TS REFUSED E_SQL_UNSUPPORTED@1:30; PLAN hybrid SELECT "o".* FROM "orders" "o" LIMIT 2
  LISP: TS translates; PLAN pure_sql SELECT "_sub1".* FROM (...) "_sub1" WHERE (CAST("_sub1"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
sqlite | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  four: REFUSED E_SQL_UNSUPPORTED@1:27, PLAN hybrid SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1
  LISP: pure_sql SELECT ("_sub1"."id" * '2') FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
mariadb | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  four: SELECT (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END * 2) FROM (...) `_sub1`
  LISP: SELECT (`_sub1`.`id` * 2) FROM (...) `_sub1`
all dialects | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  four: pure_sql SELECT CASE WHEN TRUE THEN "_sub1"."id" ELSE 'x' END FROM (...)
  LISP: TS REFUSED E_SQL_SHAPE@1:26; PLAN hybrid SELECT "o".* FROM "orders" "o" OFFSET 1
control | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2): all five identical (guarded on pg/mariadb, E_SQL_UNSUPPORTED@1:59 + hybrid on sqlite).

Reported repro reran (probe2.mjs / probe2.lisp, translate mariadb, ID:NUM): `ORDERS .> TAKE(2) .> MAP(IF(TRUE, _["id"], "x") + "a")` JS REFUSED E_SQL_INVALID@1:51 vs Lisp REFUSED E_SQL_SHAPE@1:26; `ORDERS .> TAKE(2) .> MAP(IF(TRUE, _["id"], "x"))` JS translates vs Lisp E_SQL_SHAPE@1:26.

Coverage: `lisp/bin/sqlt plan.` and `node js/bin/sqlt.mjs plan.` both "33 passed, 0 failed"; sql/cases/25-hybrid-plans.sqlt plan.options.strict-* (TEXT column, line 358-395), plan.hybrid.longest-prefix (ABORT in the MAP, line 132-145) and plan.pure-sql.filter-drop-take (FILTER before DROP, line 328-341) never read a NUM column after a pass-through derived table; `grep -ln "TAKE\|DROP" sql/cases/*.sqlt` gives only 09/23/24/25 and 23-statements' TAKE/DROP cases have no downstream step. docs/SQL-KINDS.md has no mention of derived tables/subqueries (`grep -n -i "derived\|subquery\|_sub"` empty).
```

## Original review reports (deduplicated into this finding)

### [probe-lanes-fold] Lisp keeps bound column kinds through a pass-through derived table; the other four make them UNKNOWN — different SQL, different refusals, and a different plan_hybrid classification

*correctness · high · hosts: lisp, js, python, php, cpp*

Locations: `lisp/src/sql/translator.lisp:1810-1823 (plan-output-fields pass-through branch copies :type from the source field)`; `js/src/sql/translator.mjs:1719-1735 (wrapPlanAsDerivedTable: every derived field is type 'UNKNOWN')`

Pre-existing, but the planner contract this commit pins ('every host runs 25 planner cases asserting classification') is broken by any TAKE/DROP/SORT-then-MAP shape that mixes a column with a literal of another kind: four hosts see UNKNOWN and translate, Lisp sees NUM vs TEXT and refuses, so the same program is pure_sql in four hosts and hybrid in Lisp, and even the shared pure_sql shapes render different SQL (numeric guard present or absent). It also changes the refusal code and column translate() reports.

Reported repro:

```
Bindings/context as in the harness. translate(mariadb) / plan_hybrid(mariadb):
ORDERS .> TAKE(2) .> MAP(IF(TRUE, _["id"], "x"))  →  JS/PY/PHP/CPP: pure_sql, SELECT CASE WHEN TRUE THEN `_sub1`.`id` ELSE 'x' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` | LISP: translate E_SQL_SHAPE@1:26, plan hybrid (prefix SELECT `o`.* FROM `orders` `o` LIMIT 2, MAP in the continuation)
ORDERS .> TAKE(2) .> MAP(IF(TRUE, _["id"], "x") + "a")  →  JS/PY/PHP/CPP: translate E_SQL_INVALID@1:51 | LISP: E_SQL_SHAPE@1:26
ORDERS .> TAKE(2) .> MAP(_["id"] + 1)  →  JS/PY/PHP/CPP: SELECT (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END + 1) FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` | LISP: SELECT (`_sub1`.`id` + 1) FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
Same for ORDERS .> TAKE(2) .> MAP(IF(TRUE, N, 1) + _["id"]) and ... MAP(IF(TRUE, 1 + 2, 0) + _["id"]) in corpus.txt (plan lane: pure_sql in all five but different SQL). Projected columns (MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)) agree in all five: only the SELECT * pass-through differs. (corpus2.txt, out2-*.txt)
```

### [probe-lanes-bucket-hybrid] Lisp keeps column kinds through a SELECT * derived table; the other four degrade them to UNKNOWN, changing SQL text and plan classification

*coherence · medium · hosts: lisp, python, js, php, cpp*

Locations: `lisp/src/sql/translator.lisp:1810`; `python/sel/sql/translator.py:1683`; `js/src/sql/translator.mjs:1743`

plan-output-fields in Lisp carries `:type` from the wrapped plan's source fields for the no-projection (SELECT *) case, while the Python/JS/PHP/C++ derived-table wrap sets every field to 'UNKNOWN'. After any TAKE/SORT/DROP that forces a derived table, a NUM column compared or added in a later step is rendered bare in Lisp but wrapped in the REGEXP/~ ISNUM guard in the other four; on sqlite, which has no REGEXP, the four refuse (E_SQL_UNSUPPORTED) and the planner splits after the TAKE while Lisp pushes the whole pipeline down. The same program is therefore pure_sql on Lisp and hybrid on the other hosts, with different SQL, for the shapes plan.options.strict-* and plan.hybrid.longest-prefix are built on (they avoid it only because their downstream step touches a TEXT column or a custom function). Lisp is arguably right (the kind is known), but the five hosts disagree.

Reported repro:

```
c01 ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6):
  postgresql translate_statement py/js/php/cpp => SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END > 6); lisp => ... "_sub1" WHERE ("_sub1"."amount" > 6)
  sqlite: four => REFUSED E_SQL_UNSUPPORTED@1:30, plan_hybrid => hybrid with prefix SELECT "o".* FROM "orders" "o" LIMIT 2; lisp => translates, plan_hybrid => pure_sql SELECT "_sub1".* FROM (...) "_sub1" WHERE (CAST("_sub1"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)).
Same split on c02 (TAKE(2) .> MAP(RECORD("x", _["amount"] + 1))), c04 (SORT_BY .> TAKE .> FILTER) and b59 (TAKE(2) .> MAP(... IF(_["amount"] > 6, "big"))): lisp pure_sql, others hybrid.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$SCRATCH/verify-W1/probe.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', {
  ID: Binding.column('id', 'o', 'NUM'), AMOUNT: Binding.column('amount', 'o', 'NUM'), NAME: Binding.column('name','o','TEXT') }) };
const progs = [
  'ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)',
  'ORDERS .> DROP(1) .> MAP(_["id"] * 2)',
  'ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))',
  'ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)',
];
for (const dialect of ['postgresql','sqlite','mariadb']) for (const src of progs) {
  const p = compile(src);
  let ts; try { ts = Sql.translateStatement(p, dialect, orders).sql; } catch (e) { ts = `REFUSED ${e.code}@${e.line}:${e.col}`; }
  const plan = Sql.planHybrid(p, dialect, orders);
  const cls = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  console.log(`${dialect} | ${src}\n  TS: ${ts}\n  PLAN: ${cls} ${plan.sqlStatement?.sql ?? plan.sqlStatement ?? ''}`);
}
```

**`$SCRATCH/verify-W1/probe.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', {
  ID: Binding.column('id', 'o', 'NUM'), AMOUNT: Binding.column('amount', 'o', 'NUM'), NAME: Binding.column('name','o','TEXT') }) };
const progs = [
  'ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)',
  'ORDERS .> DROP(1) .> MAP(_["id"] * 2)',
  'ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))',
  'ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)',
];
for (const dialect of ['postgresql','sqlite','mariadb']) for (const src of progs) {
  const p = compile(src);
  let ts; try { ts = Sql.translateStatement(p, dialect, orders).asStatement(); } catch (e) { ts = `REFUSED ${e.code}@${e.line}:${e.col}`; }
  const plan = Sql.planHybrid(p, dialect, orders);
  const cls = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  const psql = plan.sqlStatement ? plan.sqlStatement.asStatement() : '';
  console.log(`${dialect} | ${src}\n  TS: ${ts}\n  PLAN: ${cls} ${psql}`);
}
```

**`$SCRATCH/verify-W1/probe.lisp`**

```lisp
(defun probe ()
  (let* ((orders (sel.sql:binding-relation "orders" "o"
                   (list (cons "ID" (sel.sql:binding-column "id" "o" :num))
                         (cons "AMOUNT" (sel.sql:binding-column "amount" "o" :num))
                         (cons "NAME" (sel.sql:binding-column "name" "o" :text)))))
         (bindings (list (cons "ORDERS" orders)))
         (progs '("ORDERS .> TAKE(2) .> FILTER(_[\"amount\"] > 6)"
                  "ORDERS .> DROP(1) .> MAP(_[\"id\"] * 2)"
                  "ORDERS .> DROP(1) .> MAP(IF(TRUE, _[\"id\"], \"x\"))"
                  "ORDERS .> TAKE(2) .> MAP(RECORD(\"n\", _[\"id\"])) .> FILTER(_[\"n\"] + 1 > 2)")))
    (dolist (dialect '("postgresql" "sqlite" "mariadb"))
      (dolist (src progs)
        (let* ((p (sel:compile-source src))
               (ts (handler-case (sel.sql:as-statement (sel.sql:translate-statement p dialect bindings))
                     (sel.sql:sql-error (e) (format nil "REFUSED ~a@~a:~a" (sel.sql:sql-error-code e)
                                                    (sel.sql:sql-error-line e) (sel.sql:sql-error-col e)))))
               (plan (sel.sql:plan-hybrid p dialect bindings))
               (cls (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                          ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                          (t "hybrid")))
               (psql (if (sel.sql:hybrid-plan-sql-statement plan)
                         (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan)) "")))
          (format t "~a | ~a~%  TS: ~a~%  PLAN: ~a ~a~%" dialect src ts cls psql))))))
(probe)
```

**`$SCRATCH/verify-W1/probe.py`**

```python
from sel import compile as c
from sel.sql import Sql, Binding
from sel.errors import SelError
orders = {'ORDERS': Binding.relation('orders','o',{'ID': Binding.column('id','o','NUM'),'AMOUNT': Binding.column('amount','o','NUM'),'NAME': Binding.column('name','o','TEXT')})}
progs = ['ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)','ORDERS .> DROP(1) .> MAP(_["id"] * 2)','ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))','ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)']
for d in ['postgresql','sqlite','mariadb']:
    for s in progs:
        p = c(s)
        try: ts = Sql.translate_statement(p, d, orders).as_statement()
        except SelError as e: ts = f'REFUSED {e.code}@{e.line}:{e.col}'
        plan = Sql.plan_hybrid(p, d, orders)
        cls = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
        st = plan.sql_statement
        print(f'{d} | {s}\n  TS: {ts}\n  PLAN: {cls} {st.as_statement() if st else ""}')
```

**`$SCRATCH/verify-W1/probe.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/bootstrap.php';
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding; use Sel\Sql\SqlError;
$orders = ['ORDERS' => Binding::relation('orders','o',['ID'=>Binding::column('id','o','NUM'),'AMOUNT'=>Binding::column('amount','o','NUM'),'NAME'=>Binding::column('name','o','TEXT')])];
$progs = ['ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)','ORDERS .> DROP(1) .> MAP(_["id"] * 2)','ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))','ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)'];
foreach (['postgresql','sqlite','mariadb'] as $d) foreach ($progs as $s) {
  $p = Sel::compile($s);
  try { $ts = Sql::translateStatement($p, $d, $orders)->asStatement(); } catch (SqlError $e) { $ts = "REFUSED {$e->code}@{$e->line}:{$e->col}"; }
  $plan = Sql::planHybrid($p, $d, $orders);
  $cls = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
  $st = $plan->sqlStatement ? $plan->sqlStatement->asStatement() : '';
  echo "$d | $s\n  TS: $ts\n  PLAN: $cls $st\n";
}
```

**`$D/probe.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <iostream>
using namespace sel::sql;
int main() {
  Bindings b({{"ORDERS", Binding::relation("orders", "o",
     {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
      {"AMOUNT", Binding::column("amount", std::nullopt, SqlKind::Num)},
      {"NAME", Binding::column("name", std::nullopt, SqlKind::Text)}})}});
  const char* progs[] = {"ORDERS .> TAKE(2) .> FILTER(_[\"amount\"] > 6)","ORDERS .> DROP(1) .> MAP(_[\"id\"] * 2)","ORDERS .> DROP(1) .> MAP(IF(TRUE, _[\"id\"], \"x\"))","ORDERS .> TAKE(2) .> MAP(RECORD(\"n\", _[\"id\"])) .> FILTER(_[\"n\"] + 1 > 2)"};
  for (const char* d : {"postgresql","sqlite","mariadb"}) for (const char* s : progs) {
    sel::Program p = sel::compile(s);
    std::string ts;
    try { ts = Sql::translate_statement(p, d, b).as_statement(); }
    catch (const SqlError& e) { ts = "REFUSED " + e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
    HybridPlan plan = Sql::plan_hybrid(p, d, b);
    std::string cls = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    std::string ps = plan.sql_statement ? plan.sql_statement->as_statement() : "";
    std::cout << d << " | " << s << "\n  TS: " << ts << "\n  PLAN: " << cls << " " << ps << "\n";
  }
}
```

**`$D/probe2.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
for (const src of ['ORDERS .> TAKE(2) .> MAP(IF(TRUE, _["id"], "x") + "a")', 'ORDERS .> TAKE(2) .> MAP(IF(TRUE, _["id"], "x"))']) {
  let r; try { r = Sql.translate(compile(src), 'mariadb', orders).asStatement(); } catch (e) { r = `REFUSED ${e.code}@${e.line}:${e.col}`; }
  console.log(src, '=>', r);
}
```

**`$D/probe2.lisp`**

```lisp
(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
(let* ((bindings (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o" (list (cons "ID" (sel.sql:binding-column "id" "o" :num))))))))
  (dolist (src '("ORDERS .> TAKE(2) .> MAP(IF(TRUE, _[\"id\"], \"x\") + \"a\")" "ORDERS .> TAKE(2) .> MAP(IF(TRUE, _[\"id\"], \"x\"))"))
    (format t "~a => ~a~%" src
      (handler-case (sel.sql:as-statement (sel.sql:translate (sel:compile-source src) "mariadb" bindings))
        (sel.sql:sql-error (e) (format nil "REFUSED ~a@~a:~a" (sel.sql:sql-error-code e) (sel.sql:sql-error-line e) (sel.sql:sql-error-col e)))))))
```

### Commands run and their output

Run JS probe for derived-table kind propagation

```bash
mkdir -p $SCRATCH/verify-W1 && cd /home/nathan/workspaces/nth-share/sel && # (file written, see "Reproduction scripts" above)
node $SCRATCH/verify-W1/probe.mjs
```

```
postgresql | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: undefined
  PLAN: pure_sql [object Object]
postgresql | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: undefined
  PLAN: pure_sql [object Object]
postgresql | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: undefined
  PLAN: pure_sql [object Object]
postgresql | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: undefined
  PLAN: pure_sql [object Object]
sqlite | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: REFUSED E_SQL_UNSUPPORTED@1:30
  PLAN: hybrid [object Object]
sqlite | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:27
  PLAN: hybrid [object Object]
sqlite | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: undefined
  PLAN: pure_sql [object Object]
sqlite | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:59
  PLAN: hybrid [object Object]
mariadb | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: undefined
  PLAN: pure_sql [object Object]
mariadb | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: undefined
  PLAN: pure_sql [object Object]
mariadb | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: undefined
  PLAN: pure_sql [object Object]
mariadb | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: undefined
  PLAN: pure_sql [object Object]
```

Fix probe to render SQL text and rerun

```bash
sed -i 's/Sql.translateStatement(p, dialect, orders).sql/Sql.translateStatement(p, dialect, orders).asStatement()/; s/plan.sqlStatement?.sql ?? plan.sqlStatement ?? .../plan.sqlStatement ? plan.sqlStatement.asStatement() : ""/' $SCRATCH/verify-W1/probe.mjs && sed -i "s/plan.sqlStatement?.sql ?? plan.sqlStatement ?? ''/plan.sqlStatement ? plan.sqlStatement.asStatement() : ''/" $SCRATCH/verify-W1/probe.mjs && node $SCRATCH/verify-W1/probe.mjs
```

```
Exit code 1
file://$SCRATCH/verify-W1/probe.mjs:19



SyntaxError: Unexpected end of input
    at compileSourceTextModule (node:internal/modules/esm/utils:338:16)
    at ModuleLoader.moduleStrategy (node:internal/modules/esm/translators:102:18)
    at #translate (node:internal/modules/esm/loader:437:12)
    at ModuleLoader.loadAndTranslate (node:internal/modules/esm/loader:484:27)
    at async ModuleJob._link (node:internal/modules/esm/module_job:115:19)

Node.js v22.12.0
```

Rewrite and run JS probe

```bash
# (file written, see "Reproduction scripts" above)
node $SCRATCH/verify-W1/probe.mjs
```

```
postgresql | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END > 6)
  PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END > 6)
postgresql | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT (CAST(CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT (CAST(CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
postgresql | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: SELECT CASE WHEN TRUE THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT CASE WHEN TRUE THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
postgresql | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: SELECT "_sub2".* FROM (SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE ((CAST(CASE WHEN (CAST("_sub2"."n" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."n" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 2)
  PLAN: pure_sql SELECT "_sub2".* FROM (SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE ((CAST(CASE WHEN (CAST("_sub2"."n" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."n" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 2)
sqlite | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: REFUSED E_SQL_UNSUPPORTED@1:30
  PLAN: hybrid SELECT "o".* FROM "orders" "o" LIMIT 2
sqlite | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:27
  PLAN: hybrid SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1
sqlite | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: SELECT CASE WHEN 1 THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT CASE WHEN 1 THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
sqlite | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:59
  PLAN: hybrid SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1"
mariadb | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`amount` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`amount` AS DECIMAL(65,10)) ELSE NULL END > 6)
  PLAN: pure_sql SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`amount` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`amount` AS DECIMAL(65,10)) ELSE NULL END > 6)
mariadb | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END * 2) FROM (SELECT `o`.* FROM `orders` `o` LIMIT 1
… (truncated)
```

Run Lisp probe

```bash
grep -n "sql-error-line\|sql-error-col\|sql-error-pos" lisp/src/sql/errors.lisp lisp/src/sql/*.lisp | head -5; # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $SCRATCH/verify-W1/probe.lisp 2>&1 | tail -40
```

```
lisp/src/sql/errors.lisp:12:   (line :initarg :line :initform 0 :reader sql-error-line)
lisp/src/sql/errors.lisp:13:   (col :initarg :col :initform 0 :reader sql-error-col)
lisp/src/sql/errors.lisp:16:             (format s "~a at ~a:~a: ~a" (sql-error-code c) (sql-error-line c)
lisp/src/sql/errors.lisp:17:                     (sql-error-col c) (sql-error-message c))))
lisp/src/sql/errors.lisp:12:   (line :initarg :line :initform 0 :reader sql-error-line)
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=817011 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 2, Column: 42, File-Position: 57

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-W1/probe.lisp" {1201ED6613}>

Backtrace for: #<SB-THREAD:THREAD tid=817011 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7863}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7863}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7863}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7863}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201ED7813}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-W1/probe.lisp" {1201ED6613}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {71B7BB1CF4CB}> #<SB-C::SOURCE-INFO {1201E77813}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-W1/probe.lisp" {1201ED6613}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-W1/probe.lisp" {1201ED6613}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {71B7BB1CF88B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-W1/probe.lisp" {1201ED6613}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-W1/probe.lisp" {1201ED6613}>)
9: (LOAD #P"$SCRATCH/verify-W1/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-W1/probe.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Run Lisp probe with SQL system loaded

```bash
F=$SCRATCH/verify-W1/probe.lisp && { echo '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))'; cat $F; } > $F.2 && mv $F.2 $F && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $F 2>&1 | tail -40
```

```
postgresql | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE ("_sub1"."amount" > 6)
  PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE ("_sub1"."amount" > 6)
postgresql | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT (CAST("_sub1"."id" AS NUMERIC) * CAST(2 AS NUMERIC)) FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT (CAST("_sub1"."id" AS NUMERIC) * CAST(2 AS NUMERIC)) FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
postgresql | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: REFUSED E_SQL_SHAPE@1:26
  PLAN: hybrid SELECT "o".* FROM "orders" "o" OFFSET 1
postgresql | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: SELECT "_sub2".* FROM (SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE ((CAST(CASE WHEN (CAST("_sub2"."n" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."n" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 2)
  PLAN: pure_sql SELECT "_sub2".* FROM (SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE ((CAST(CASE WHEN (CAST("_sub2"."n" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."n" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 2)
sqlite | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CAST("_sub1"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
  PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CAST("_sub1"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
sqlite | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT ("_sub1"."id" * '2') FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT ("_sub1"."id" * '2') FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
sqlite | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: REFUSED E_SQL_SHAPE@1:26
  PLAN: hybrid SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1
sqlite | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:59
  PLAN: hybrid SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1"
mariadb | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` WHERE (`_sub1`.`amount` > 6)
  PLAN: pure_sql SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` WHERE (`_sub1`.`amount` > 6)
mariadb | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT (`_sub1`.`id` * 2) FROM (SELECT `o`.* FROM `orders` `o` LIMIT 18446744073709551615 OFFSET 1) `_sub1`
  PLAN: pure_sql SELECT (`_sub1`.`id` * 2) FROM (SELECT `o`.* FROM `orders` `o` LIMIT 18446744073709551615 OFFSET 1) `_sub1`
mariadb | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: REFUSED E_SQL_SHAPE@1:26
  PLAN: hybrid SELECT `o`.* FROM `orders` `o` LIMIT 18446744073709551615 OFFSET 1
mariadb | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: SELECT `_sub2`.* FROM (SELECT `_sub1`.`id` AS `n` FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`) `_sub2` WHERE ((CASE WHEN (`_sub2`.`n` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub2`.`n` AS DECIMAL(65,10)) ELSE NULL END + 1) > 2)
  PLAN: pure_sql SELECT `_sub2`.* FROM (SELECT `_sub1`.`id` A
… (truncated)
```

Run Python probe

```bash
# (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $SCRATCH/verify-W1/probe.py 2>&1 | tail -40
```

```
return t.translate_statement(program.ast)
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py", line 196, in translate_statement
    return self.compile_statement(plan)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py", line 2104, in compile_statement
    fragment = self._with_row(
        src, filter_['binder'],
        lambda f=filter_: self._require_bool(
            self._node(f['node']), f['pos'], 'FILTER'))
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py", line 1224, in _with_row
    return render()
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py", line 2107, in <lambda>
    self._node(f['node']), f['pos'], 'FILTER'))
    ~~~~~~~~~~^^^^^^^^^^^
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py", line 241, in _node
    return self._dispatch(n)
           ~~~~~~~~~~~~~~^^^
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py", line 264, in _dispatch
    return self._binary(n)
           ~~~~~~~~~~~~^^^
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py", line 480, in _binary
    l = self._guard_numeric(l, n.l)
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py", line 1498, in _guard_numeric
    return self.emit.numeric_operand(f, n.pos)
           ~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/emit.py", line 186, in numeric_operand
    refuse('E_SQL_UNSUPPORTED',
    ~~~~~~^^^^^^^^^^^^^^^^^^^^^
           f'dialect {self._dialect} has no way to ask whether a value is a '
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
           'number, so an operand it has not been told is one cannot be read '
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
           'as one here; declare the binding NUM if the column really is '
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
           'numeric', pos)
           ^^^^^^^^^^^^^^^
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/errors.py", line 36, in refuse
    raise SqlError(code, message, pos)
sel.sql.errors.SqlError: E_SQL_UNSUPPORTED at 1:30: dialect sqlite has no way to ask whether a value is a number, so an operand it has not been told is one cannot be read as one here; declare the binding NUM if the column really is numeric
```

Rerun Python probe catching SqlError

```bash
sed -i 's/from sel.errors import SelError/from sel.sql.errors import SqlError as SelError/' $SCRATCH/verify-W1/probe.py && PYTHONPATH=$PWD/python python3 $SCRATCH/verify-W1/probe.py 2>&1 | tail -40
```

```
postgresql | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END > 6)
  PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END > 6)
postgresql | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT (CAST(CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT (CAST(CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
postgresql | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: SELECT CASE WHEN TRUE THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT CASE WHEN TRUE THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
postgresql | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: SELECT "_sub2".* FROM (SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE ((CAST(CASE WHEN (CAST("_sub2"."n" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."n" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 2)
  PLAN: pure_sql SELECT "_sub2".* FROM (SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE ((CAST(CASE WHEN (CAST("_sub2"."n" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."n" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 2)
sqlite | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: REFUSED E_SQL_UNSUPPORTED@1:30
  PLAN: hybrid SELECT "o".* FROM "orders" "o" LIMIT 2
sqlite | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:27
  PLAN: hybrid SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1
sqlite | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: SELECT CASE WHEN 1 THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT CASE WHEN 1 THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
sqlite | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:59
  PLAN: hybrid SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1"
mariadb | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`amount` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`amount` AS DECIMAL(65,10)) ELSE NULL END > 6)
  PLAN: pure_sql SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`amount` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`amount` AS DECIMAL(65,10)) ELSE NULL END > 6)
mariadb | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END * 2) FROM (SELECT `o`.* FROM `orders` `o` LIMIT 1
… (truncated)
```

Run PHP probe

```bash
head -20 tools/check-php-optimizer.php | grep -n require; # (file written, see "Reproduction scripts" above)
php $SCRATCH/verify-W1/probe.php 2>&1 | tail -40
```

```
7:require_once __DIR__ . '/../php/src/Sql/bootstrap.php';
postgresql | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END > 6)
  PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END > 6)
postgresql | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT (CAST(CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT (CAST(CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
postgresql | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: SELECT CASE WHEN TRUE THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT CASE WHEN TRUE THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
postgresql | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: SELECT "_sub2".* FROM (SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE ((CAST(CASE WHEN (CAST("_sub2"."n" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."n" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 2)
  PLAN: pure_sql SELECT "_sub2".* FROM (SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE ((CAST(CASE WHEN (CAST("_sub2"."n" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."n" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 2)
sqlite | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: REFUSED E_SQL_UNSUPPORTED@1:30
  PLAN: hybrid SELECT "o".* FROM "orders" "o" LIMIT 2
sqlite | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:27
  PLAN: hybrid SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1
sqlite | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: SELECT CASE WHEN 1 THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT CASE WHEN 1 THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
sqlite | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:59
  PLAN: hybrid SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1"
mariadb | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`amount` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`amount` AS DECIMAL(65,10)) ELSE NULL END > 6)
  PLAN: pure_sql SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`amount` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`amount` AS DECIMAL(65,10)) ELSE NULL END > 6)
mariadb | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE 
… (truncated)
```

Compile and run C++ probe

```bash
D=$SCRATCH/verify-W1 && # (file written, see "Reproduction scripts" above)
cd cpp && g++ -std=c++23 -O0 -o $D/probe $D/probe.cpp build/sel_sql_binding.o build/sel_sql_emit.o build/sel_sql_hybrid.o build/sel_sql_map_data.o build/sel_sql_map.o build/sel_sql_node.o build/sel_sql.o build/sel_sql_stage1.o build/sel_sql_translator.o build/sel.o 2>&1 | tail -5 && $D/probe
```

```
postgresql | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END > 6)
  PLAN: pure_sql SELECT "_sub1".* FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1" WHERE (CASE WHEN (CAST("_sub1"."amount" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."amount" AS NUMERIC) ELSE NULL END > 6)
postgresql | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT (CAST(CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT (CAST(CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END AS NUMERIC) * CAST(2 AS NUMERIC)) FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
postgresql | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: SELECT CASE WHEN TRUE THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT CASE WHEN TRUE THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" OFFSET 1) "_sub1"
postgresql | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: SELECT "_sub2".* FROM (SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE ((CAST(CASE WHEN (CAST("_sub2"."n" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."n" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 2)
  PLAN: pure_sql SELECT "_sub2".* FROM (SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE ((CAST(CASE WHEN (CAST("_sub2"."n" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."n" AS NUMERIC) ELSE NULL END AS NUMERIC) + CAST(1 AS NUMERIC)) > 2)
sqlite | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: REFUSED E_SQL_UNSUPPORTED@1:30
  PLAN: hybrid SELECT "o".* FROM "orders" "o" LIMIT 2
sqlite | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:27
  PLAN: hybrid SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1
sqlite | ORDERS .> DROP(1) .> MAP(IF(TRUE, _["id"], "x"))
  TS: SELECT CASE WHEN 1 THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
  PLAN: pure_sql SELECT CASE WHEN 1 THEN "_sub1"."id" ELSE 'x' END FROM (SELECT "o".* FROM "orders" "o" LIMIT -1 OFFSET 1) "_sub1"
sqlite | ORDERS .> TAKE(2) .> MAP(RECORD("n", _["id"])) .> FILTER(_["n"] + 1 > 2)
  TS: REFUSED E_SQL_UNSUPPORTED@1:59
  PLAN: hybrid SELECT "_sub1"."id" AS "n" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1"
mariadb | ORDERS .> TAKE(2) .> FILTER(_["amount"] > 6)
  TS: SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`amount` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`amount` AS DECIMAL(65,10)) ELSE NULL END > 6)
  PLAN: pure_sql SELECT `_sub1`.* FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`amount` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`amount` AS DECIMAL(65,10)) ELSE NULL END > 6)
mariadb | ORDERS .> DROP(1) .> MAP(_["id"] * 2)
  TS: SELECT (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END * 2) FROM (SELECT `o`.* FROM `orders` `o` LIMIT 1
… (truncated)
```

Run planner cases on Lisp and JS, list case names

```bash
(lisp/bin/sqlt plan. 2>&1 | tail -3; node js/bin/sqlt.mjs plan. 2>&1 | tail -2); grep -n "^### " sql/cases/25-hybrid-plans.sqlt | head -30
```

```
33 passed (28 also checked against a mirrored dialect), 0 failed, 0 suite errors

33 passed (28 also checked against a mirrored dialect), 0 failed, 0 suite errors
19:### name: plan.pure-sql.direct
34:### name: plan.pure-sql.through-helper-assignment
54:### name: plan.pure-sql.value-binding-is-inlined
73:### name: plan.pure-memory.non-normalisable-falls-back
92:### name: plan.hybrid.unsupported-suffix
113:### name: plan.hybrid.fallthrough-keeps-downstream-steps
132:### name: plan.hybrid.longest-prefix
147:### name: plan.pure-memory.bound-relation-in-source
164:### name: plan.pure-memory.unbound-source
174:### name: plan.pure-memory.not-a-pipeline
187:### name: plan.tables.physical-name-not-binding-name
206:### name: plan.tables.binding-name-is-case-insensitive
224:### name: plan.tables.first-use-order
241:### name: plan.tables.dedupe-by-physical-source
260:### name: plan.tables.relation-query-is-reported-verbatim
277:### name: plan.hybrid.left-join-then-projection
294:### name: plan.pure-sql.filters-fuse-before-planning
313:### name: plan.pure-sql.sort-take-fuses-to-top
328:### name: plan.pure-sql.filter-drop-take
343:### name: plan.pure-sql.distinct
358:### name: plan.options.strict-off-caveat-pushes-down
376:### name: plan.options.strict-on-caveat-stays-in-memory
397:### name: plan.fold.literal-branch-is-hoisted-into-the-prefix
416:### name: plan.fold.compound-branch-stays-an-if
440:### name: plan.fold.hoisted-literal-does-not-change-the-split
460:### name: plan.refuse.base-dialect
477:### name: plan.immutable.folding-does-not-write-back
499:### name: plan.immutable.folding-inside-a-kept-step
518:### name: plan.bucket.pipeline-map-pushes-down-whole
537:### name: plan.bucket.open-prefix-is-not-a-split-point
```

Check reported refusal-code facet in JS and Lisp

```bash
D=$SCRATCH/verify-W1 && # (file written, see "Reproduction scripts" above)
node $D/probe2.mjs; # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/probe2.lisp 2>&1 | tail -3
```

```
ORDERS .> TAKE(2) .> MAP(IF(TRUE, _["id"], "x") + "a") => REFUSED E_SQL_INVALID@1:51
ORDERS .> TAKE(2) .> MAP(IF(TRUE, _["id"], "x")) => SELECT CASE WHEN TRUE THEN `_sub1`.`id` ELSE 'x' END FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
ORDERS .> TAKE(2) .> MAP(IF(TRUE, _["id"], "x") + "a") => REFUSED E_SQL_SHAPE@1:26
ORDERS .> TAKE(2) .> MAP(IF(TRUE, _["id"], "x")) => REFUSED E_SQL_SHAPE@1:26
```

