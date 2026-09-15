# U. A stage-1 clist in the planned tree and TAKE(1/0): host-native exceptions escape translate_statement and plan_hybrid in JS/PHP/Python

**Verdict:** CONFIRMED · **severity:** high · **introduced:** mixed · **hosts:** python, cpp, js, php, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

Every facet reproduces exactly as reported, and the finding under-states it: the `refuseAsSel` crash in the int-parameter path also exists in Python's translator (`_constants.refuse_as_sel` vs the module's `_refuse_as_sel`), and it needs no clist at all — `ORDERS .> TAKE(1/0)` throws a host-native TypeError/Error/AttributeError out of both `translate_statement` and `plan_hybrid` in JS, PHP and Python, while C++ and Lisp refuse `E_SQL_INVALID` / plan `pure_memory`. For a valid SEL program with an indexed-assignment clist in a FILTER predicate the five planners answer three different things (JS/PHP/Lisp pure_sql, C++ pure_memory even though C++'s own translate_statement pushes it down, Python AttributeError), which breaks §12.1's "plans the tree the translator will see" and "planning refuses only E_SQL_DIALECT/E_SQL_BINDING". All underlying defects pre-date the commit; the commit's planner rewrite (JS now plans the normalised tree, Lisp's optimiser now tolerates a clist) moved JS and Lisp from pure_memory/TYPE-ERROR to PHP's pure_sql answer, and thereby newly exposes the JS planner to the translator crash on `R[1] = 5; ORDERS .> TAKE(COUNT(R))`, but left Python and C++ behind.

## Suggested fix — case first

Smallest fix, in order: (1) make the SEL-refusal helper reachable — `export` refuseAsSel in js/src/sql/constants.mjs, make PHP Constants::refuseAsSel public (or `@internal` public static), and call `_constants._refuse_as_sel` (or rename it to `refuse_as_sel`) in python/sel/sql/translator.py:1707 — then add sql/cases `ORDERS .> TAKE(1/0)` expecting E_SQL_INVALID (what C++ and Lisp already answer) and a 25-hybrid-plans case expecting pure_memory; that one case is the first to add because it crashes three hosts with no clist involved. (2) Give every dynamic host C++'s containment guard in evalIntParam ("count cannot contain dynamic lists", E_SQL_SHAPE) and make Lisp's `clist-p` check recursive, with case `R[1] = 5; ORDERS .> TAKE(COUNT(R))` → translate E_SQL_SHAPE, plan pure_memory. (3) Make Python's optimizer walkers (copy_node and the child-visiting loops) pass a CList through untouched, as the Lisp copy-on-write walk now does, and make the C++ planner stop treating "tree contains a clist" as pure_memory (walk the SNode tree, or fall back to try_translate_statement on it) so `R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))` plans pure_sql everywhere; pin with a plan case (`--- plan pure_sql`, `--- tables orders`) plus `R[1] = 5; R` → pure_memory.

## Verifier reasoning

Facet 1 (clist in a FILTER predicate): `R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))` is valid SEL (all five REPLs agree on `R[1] = 5; (1,2,3) .> FILTER(_ > COUNT(R))`). Stage 1 does not refuse it; it yields a CList node. Python's plan_hybrid (python/sel/sql/hybrid.py:345) calls optimize_ast_logical outside the SqlError try, and copy_node (python/sel/optimizer.py:31, `replace(node, args=list(node.args), ...)`) reads `.args` on the CList → AttributeError escapes. C++ (cpp/sel_sql_hybrid.cpp:321-325) calls `normalise(...)->to_node()`, and SNode::to_node returns nullptr for any tree containing a CList (cpp/sel_sql_node.cpp:79 `if (t_ == T::CList) return nullptr;`), so the planner answers pure_memory — yet C++'s own translate_statement renders the same program as `SELECT o.* FROM orders o WHERE (id > 1)`, so the C++ planner does not "plan the tree the translator will see". JS, PHP and Lisp optimise around the clist and answer pure_sql with byte-identical SQL. Pre-existing: the pre-commit Python tree (extracted with git archive) throws the identical AttributeError; pre-commit C++ was pure_memory too (raw-AST unwinding); pre-commit JS was pure_memory, pre-commit Lisp TYPE-ERROR — the commit changed those two to pure_sql.

Facet 2 (`R[1] = 5; R`): JS/PHP/C++/Lisp pure_memory, Python AttributeError (same root cause). Pre-existing.

Facet 3 (clist reaching an int parameter): `R[1] = 5; ORDERS .> TAKE(COUNT(R))` and `R[1] = 3; ORDERS .> DROP(MAX(R))`. JS evalIntParam (js/src/sql/translator.mjs:2132) calls `constants.refuseAsSel`, but js/src/sql/constants.mjs:183 declares `function refuseAsSel` without `export` → TypeError. PHP (php/src/Sql/Translator.php:2847) calls `Constants::refuseAsSel`, declared `private static` at php/src/Sql/Constants.php:270 → Error. Python (python/sel/sql/translator.py:1707) calls `_constants.refuse_as_sel`; python/sel/sql/constants.py:192 defines `_refuse_as_sel` → AttributeError (the planner never gets that far in Python because facet 1 fires first, but translate_statement does). Lisp eval-int-param (lisp/src/sql/translator.lisp:1679-1683) guards `(clist-p n)` only at the top node, so a clist nested under COUNT reaches `sel:run` and the evaluator signals TYPE-ERROR. C++ (cpp/sel_sql_translator.cpp:2610-2614) uses `to_node()==nullptr` as a containment check and refuses E_SQL_SHAPE "count cannot contain dynamic lists"; its planner answers pure_memory. None of the JS/PHP/Python/Lisp errors is a SqlError, so they escape plan_hybrid, contradicting §12.1. All four pre-exist (old translator.mjs:2099, old Translator.php:2805, old translator.py:1917, old translator.lisp:1679 have the same code); the old JS planner never reached the translator for a `seq` program, so the JS *planner* crash on the clist program is newly reachable because of this commit's "JS plans the normalised tree", while `ORDERS .> TAKE(1/0)` already crashed the old JS/PHP/Python planners and translators alike.

Severity high: host-native exceptions escaping both public lanes in three hosts, and three different planner classifications across hosts for one valid program, against a contract this commit wrote down. No sqlt/plan fixture covers any of these shapes (the only clist statement cases are `R[1] = 1 / 0; COUNT(R)` and two ALL/JOIN unrolls, none in a pipeline; no case evaluates a non-literal TAKE/DROP count that fails).

## Verifier evidence

```
Probe scripts in /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-U/ (js.mjs, php.php, py.py, cpp.cpp, lisp.lisp; js2.mjs/php2.php/py2.py/cpp2.cpp/lisp2.lisp for TAKE(1/0); old/ holds `git archive 8fe0e3a python js php lisp`). Bindings: ORDERS = relation orders o {ID: column id o NUM}, dialect mariadb.

HEAD, `R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))`:
- js: plan pure_sql sql=SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1); translateStatement same
- php: plan pure_sql (same SQL); translateStatement same
- lisp: plan pure_sql (same SQL); translate-statement same
- cpp: plan pure_memory; translate_statement: SELECT `o`.* FROM `orders` `o` WHERE (`id` > 1)
- python: plan THROW AttributeError 'CList' object has no attribute 'args' at python/sel/optimizer.py:31; translate_statement: SELECT ... WHERE (`o`.`id` > 1)
Own variant `R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))`: js/php/lisp pure_sql `... > 2`, cpp pure_memory, python AttributeError.

HEAD, `R[1] = 5; R`: js/php/cpp/lisp plan pure_memory; python AttributeError.

HEAD, `R[1] = 5; ORDERS .> TAKE(COUNT(R))` (and own variant `R[1] = 3; ORDERS .> DROP(MAX(R))`):
- js: plan THROW TypeError constants.refuseAsSel is not a function; translateStatement same; translate same
- php: plan THROW Error Call to private method Sel\Sql\Constants::refuseAsSel() from scope Sel\Sql\Translator; translateStatement same
- python: plan AttributeError (CList .args); translate_statement THROW AttributeError module 'sel.sql.constants' has no attribute 'refuse_as_sel' at python/sel/sql/translator.py:1707
- lisp: plan THROW TYPE-ERROR The value #S(SEL.SQL::CLIST ...) is not of type SEL::NODE; translate-statement same
- cpp: plan pure_memory; translate_statement THROW SqlError E_SQL_SHAPE TAKE count cannot contain dynamic lists

HEAD, `ORDERS .> TAKE(1/0)` (no clist):
- js: planHybrid and translateStatement both THROW TypeError constants.refuseAsSel is not a function
- php: both THROW Error Call to private method Sel\Sql\Constants::refuseAsSel()
- python: both THROW AttributeError module 'sel.sql.constants' has no attribute 'refuse_as_sel'
- cpp: plan pure_memory; translate_statement THROW SqlError E_SQL_INVALID SEL rejects this expression (E_DIV_ZERO ...)
- lisp: plan pure_memory; translate-statement THROW SqlError E_SQL_INVALID at 1:17 ...

Pre-commit tree (8fe0e3a, same scripts against old/): python plan AttributeError at old optimizer.py:31 and translate_statement AttributeError refuse_as_sel at old translator.py:1917; js plan pure_memory for both clist programs, translateStatement TypeError refuseAsSel; php identical to HEAD (pure_sql / Error); lisp plan TYPE-ERROR for every clist program (incl. FILTER one), translate-statement TYPE-ERROR for TAKE; TAKE(1/0) crashes old js/php/python in both lanes.

In-memory check (all five REPLs): `R[1] = 5; (1,2,3) .> FILTER(_ > COUNT(R))` → {"2","3"}; `R[1] = 5; (1,2,3) .> TAKE(COUNT(R))` → {"1"} — valid programs.

Code read: python/sel/sql/hybrid.py:341-345 (optimize_ast_logical outside try), python/sel/optimizer.py:28-31, cpp/sel_sql_hybrid.cpp:313-325, cpp/sel_sql_node.cpp:79, cpp/sel_sql_translator.cpp:2610-2614, js/src/sql/translator.mjs:2126-2134, js/src/sql/constants.mjs:183 (`function refuseAsSel` not exported; only validate/requireNumeric use it internally), php/src/Sql/Translator.php:2842-2847, php/src/Sql/Constants.php:270 (`private static function refuseAsSel`), python/sel/sql/translator.py:1703-1707, python/sel/sql/constants.py:192 (`def _refuse_as_sel`), lisp/src/sql/translator.lisp:1679-1683. `git show 8fe0e3a:` of each shows the same call sites (translator.mjs:2099, Translator.php:2805, translator.py:1917, translator.lisp:1679). Fixtures: grep of sql/cases finds no pipeline case with an indexed-assignment clist and no failing non-literal TAKE/DROP count; sql/cases/25-hybrid-plans.sqlt has none.
```

## Original review reports (deduplicated into this finding)

### [hybrid-planner] A stage-1 clist in the normalised tree: Python plan_hybrid raises AttributeError, C++ answers pure_memory, JS/PHP/Lisp answer pure_sql; and a clist reaching TAKE's count crashes three translators inside the planner

*coherence · high · hosts: python, cpp, js, php, lisp*

Locations: `python/sel/sql/hybrid.py:345`; `python/sel/optimizer.py:327`; `cpp/sel_sql_hybrid.cpp:321-325`; `cpp/sel_sql_node.cpp:79-80`; `js/src/sql/hybrid.mjs:309`; `js/src/sql/translator.mjs:2132`; `php/src/Sql/Translator.php:2847`; `php/src/Sql/Constants.php:270`; `lisp/src/sql/translator.lisp:1697-1705`

§12.1 says a program stage 1 refuses is pure_memory and the CHANGELOG says the Lisp optimiser now tolerates a stage-1 clist in a child slot. An indexed assignment (`R[1] = 5; ...`) is NOT refused by stage 1 -- it yields a CList node inside the tree -- and the hosts then diverge three ways: Python's optimize_ast_logical reads `.args` on the CList and throws AttributeError out of plan_hybrid; C++'s SNode::to_node returns null for any tree containing a clist and the planner turns that into pure_memory; JS, PHP and Lisp optimise around the clist and reach the translator, which renders `COUNT(R)` as 1 and classifies pure_sql. Additionally, when the clist reaches an integer parameter (`TAKE(COUNT(R))`), JS calls `constants.refuseAsSel`, which constants.mjs does not export (TypeError), PHP calls a private method (Error), and Lisp signals a type error; none of these is a SqlError, so they escape plan_hybrid, contradicting the contract that planning refuses only E_SQL_DIALECT/E_SQL_BINDING. C++ refuses E_SQL_SHAPE and stays pure_memory.

Reported repro:

```
mariadb, `R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))`: js/php/lisp class=pure_sql sql=SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1); cpp class=pure_memory; python: AttributeError("'CList' object has no attribute 'args'") from hybrid.py:345.  `R[1] = 5; R`: js/php/cpp/lisp pure_memory, python AttributeError.  `R[1] = 5; ORDERS .> TAKE(COUNT(R))`: js THROW TypeError: constants.refuseAsSel is not a function; php THROW Error: Call to private method Sel\Sql\Constants::refuseAsSel(); lisp THROW type error on #S(CLIST ...); cpp pure_memory; python AttributeError. Sql.translate_statement on the same source reproduces the three crashes without the planner.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`js.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
const srcs = [
  'R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))',
  'R[1] = 5; R',
  'R[1] = 5; ORDERS .> TAKE(COUNT(R))',
  // own repros
  'R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > SUM(R))',
  'R[1] = 3; ORDERS .> DROP(MAX(R))',
  'R[1] = 3; ORDERS .> FILTER(_["id"] > MAX(R))',
  'R[1] = 3; ORDERS .> FILTER(_["id"] > R[1])',
  'R[1] = 3; ORDERS .> FILTER(_["id"] > COUNT(R)) .> TAKE(COUNT(R))',
];
for (const dialect of ['mariadb', 'postgresql']) for (const s of srcs) {
  let out;
  try {
    const p = Sql.planHybrid(compile(s), dialect, orders);
    out = (p.pureSql ? 'pure_sql' : p.pureMemory ? 'pure_memory' : 'hybrid') + (p.sqlStatement ? ' sql=' + p.sqlStatement.sql : '');
  } catch (e) { out = 'THROW ' + (e.constructor.name) + ' ' + (e.code || '') + ' ' + e.message; }
  console.log(`[${dialect}] ${s}\n   plan: ${out}`);
  try {
    const f = Sql.translateStatement(compile(s), dialect, orders);
    console.log('   translateStatement: ' + f.sql);
  } catch (e) { console.log('   translateStatement THROW ' + e.constructor.name + ' ' + (e.code || '') + ' ' + e.message); }
  try {
    const f = Sql.translate(compile(s), dialect, orders);
    console.log('   translate: ' + f.sql);
  } catch (e) { console.log('   translate THROW ' + e.constructor.name + ' ' + (e.code || '') + ' ' + e.message); }
}
```

**`php.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$orders = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
$srcs = [
  'R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))',
  'R[1] = 5; R',
  'R[1] = 5; ORDERS .> TAKE(COUNT(R))',
  'R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))',
  'R[1] = 3; ORDERS .> DROP(MAX(R))',
  'R[1] = 3; ORDERS .> FILTER(_["id"] > MAX(R))',
  'R[1] = 3; ORDERS .> FILTER(_["id"] > R[1])',
];
foreach ($srcs as $s) {
  echo "[mariadb] $s\n";
  try {
    $p = Sql::planHybrid(Sel::compile($s), 'mariadb', $orders);
    $cls = $p->pureSql ? 'pure_sql' : ($p->pureMemory ? 'pure_memory' : 'hybrid');
    echo "   plan: $cls" . ($p->sqlStatement ? ' sql=' . $p->sqlStatement->asStatement() : '') . "\n";
  } catch (\Throwable $e) { echo "   plan THROW " . get_class($e) . " " . ($e->code ?? '') . " " . $e->getMessage() . "\n"; }
  try { echo "   translateStatement: " . Sql::translateStatement(Sel::compile($s), 'mariadb', $orders)->asStatement() . "\n"; }
  catch (\Throwable $e) { echo "   translateStatement THROW " . get_class($e) . " " . ($e->code ?? '') . " " . $e->getMessage() . "\n"; }
}
```

**`py.py`**

```python
import traceback
from sel import compile
from sel.sql import Sql, Binding
orders = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}
srcs = [
  'R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))',
  'R[1] = 5; R',
  'R[1] = 5; ORDERS .> TAKE(COUNT(R))',
  'R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))',
  'R[1] = 3; ORDERS .> DROP(MAX(R))',
  'R[1] = 3; ORDERS .> FILTER(_["id"] > MAX(R))',
  'R[1] = 3; ORDERS .> FILTER(_["id"] > R[1])',
]
for s in srcs:
    print('[mariadb]', s)
    try:
        p = Sql.plan_hybrid(compile(s), 'mariadb', orders)
        cls = 'pure_sql' if p.pure_sql else 'pure_memory' if p.pure_memory else 'hybrid'
        print('   plan:', cls, ('sql=' + p.sql_statement.as_statement()) if p.sql_statement else '')
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__)[-1]
        print('   plan THROW', type(e).__name__, getattr(e, 'code', ''), e, f'at {tb.filename}:{tb.lineno}')
    try:
        print('   translate_statement:', Sql.translate_statement(compile(s), 'mariadb', orders).as_statement())
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__)[-1]
        print('   translate_statement THROW', type(e).__name__, getattr(e, 'code', ''), e, f'at {tb.filename}:{tb.lineno}')
```

**`$SCRATCH/verify-U/cpp.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main() {
  const Bindings b({{"ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)}})}});
  const char* srcs[] = {
    "R[1] = 5; ORDERS .> FILTER(_[\"id\"] > COUNT(R))",
    "R[1] = 5; R",
    "R[1] = 5; ORDERS .> TAKE(COUNT(R))",
    "R[\"a\"] = 1; R[\"b\"] = 2; ORDERS .> FILTER(_[\"id\"] > COUNT(R))",
    "R[1] = 3; ORDERS .> DROP(MAX(R))",
    "R[1] = 3; ORDERS .> FILTER(_[\"id\"] > MAX(R))",
    "R[1] = 3; ORDERS .> FILTER(_[\"id\"] > R[1])",
  };
  for (const char* s : srcs) {
    std::cout << "[mariadb] " << s << "\n";
    try {
      auto p = Sql::plan_hybrid(sel::compile(s), "mariadb", b);
      std::cout << "   plan: " << (p.pure_sql ? "pure_sql" : p.pure_memory ? "pure_memory" : "hybrid");
      if (p.sql_statement) std::cout << " sql=" << p.sql_statement->as_statement();
      std::cout << "\n";
    } catch (const sel::sql::SqlError& e) { std::cout << "   plan THROW SqlError " << e.code << " " << e.what() << "\n"; }
      catch (const std::exception& e) { std::cout << "   plan THROW " << e.what() << "\n"; }
    try {
      std::cout << "   translate_statement: " << Sql::translate_statement(sel::compile(s), "mariadb", b).as_statement() << "\n";
    } catch (const sel::sql::SqlError& e) { std::cout << "   translate_statement THROW SqlError " << e.code << " " << e.what() << "\n"; }
      catch (const std::exception& e) { std::cout << "   translate_statement THROW " << e.what() << "\n"; }
    try {
      std::cout << "   translate: " << Sql::translate(sel::compile(s), "mariadb", b).as_statement() << "\n";
    } catch (const sel::sql::SqlError& e) { std::cout << "   translate THROW SqlError " << e.code << " " << e.what() << "\n"; }
      catch (const std::exception& e) { std::cout << "   translate THROW " << e.what() << "\n"; }
  }
}
```

**`$SCRATCH/verify-U/lisp.lisp`**

```lisp
(defun probe ()
  (let* ((orders (sel.sql:binding-relation "orders" "o"
                   (list (cons "ID" (sel.sql:binding-column "id" "o" :num)))))
         (bindings (list (cons "ORDERS" orders))))
    (dolist (s '("R[1] = 5; ORDERS .> FILTER(_[\"id\"] > COUNT(R))"
                 "R[1] = 5; R"
                 "R[1] = 5; ORDERS .> TAKE(COUNT(R))"
                 "R[\"a\"] = 1; R[\"b\"] = 2; ORDERS .> FILTER(_[\"id\"] > COUNT(R))"
                 "R[1] = 3; ORDERS .> DROP(MAX(R))"
                 "R[1] = 3; ORDERS .> FILTER(_[\"id\"] > MAX(R))"
                 "R[1] = 3; ORDERS .> FILTER(_[\"id\"] > R[1])"))
      (format t "[mariadb] ~a~%" s)
      (handler-case
          (let* ((p (sel:compile-source s))
                 (plan (sel.sql:plan-hybrid p "mariadb" bindings)))
            (format t "   plan: ~a~@[ sql=~a~]~%"
                    (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                          ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                          (t "hybrid"))
                    (and (sel.sql:hybrid-plan-sql-statement plan)
                         (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan)))))
        (sel.sql:sql-error (e) (format t "   plan THROW SqlError ~a ~a~%" (sel.sql:sql-error-code e) e))
        (error (e) (format t "   plan THROW ~a: ~a~%" (type-of e) e)))
      (handler-case
          (format t "   translate-statement: ~a~%"
                  (sel.sql:as-statement (sel.sql:translate-statement (sel:compile-source s) "mariadb" bindings)))
        (sel.sql:sql-error (e) (format t "   translate-statement THROW SqlError ~a ~a~%" (sel.sql:sql-error-code e) e))
        (error (e) (format t "   translate-statement THROW ~a: ~a~%" (type-of e) e))))))
(probe)
```

**`js2.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
for (const s of ['ORDERS .> TAKE(1/0)', 'ORDERS .> TAKE("a" + 1)', 'ORDERS .> TAKE(COUNT((1,2)))', 'R = (1,2); ORDERS .> TAKE(COUNT(R))']) {
  for (const f of ['planHybrid', 'translateStatement']) {
    try { const r = Sql[f](compile(s), 'mariadb', orders); console.log(s, f, r.asStatement ? r.asStatement() : (r.pureSql ? 'pure_sql ' + r.sqlStatement.asStatement() : r.pureMemory ? 'pure_memory' : 'hybrid')); }
    catch (e) { console.log(s, f, 'THROW', e.constructor.name, e.code || '', e.message); }
  }
}
```

**`php2.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$orders = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
foreach (['ORDERS .> TAKE(1/0)'] as $s) {
  try { $p = Sql::planHybrid(Sel::compile($s), 'mariadb', $orders); echo "$s plan: " . ($p->pureSql ? 'pure_sql' : ($p->pureMemory ? 'pure_memory' : 'hybrid')) . "\n"; }
  catch (\Throwable $e) { echo "$s plan THROW " . get_class($e) . " " . ($e->code ?? '') . " " . $e->getMessage() . "\n"; }
  try { echo "$s translateStatement: " . Sql::translateStatement(Sel::compile($s), 'mariadb', $orders)->asStatement() . "\n"; }
  catch (\Throwable $e) { echo "$s translateStatement THROW " . get_class($e) . " " . ($e->code ?? '') . " " . $e->getMessage() . "\n"; }
}
```

**`py2.py`**

```python
from sel import compile
from sel.sql import Sql, Binding
orders = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}
s = 'ORDERS .> TAKE(1/0)'
for f in ('plan_hybrid', 'translate_statement'):
    try:
        r = getattr(Sql, f)(compile(s), 'mariadb', orders)
        print(s, f, 'pure_sql' if getattr(r, 'pure_sql', False) else 'pure_memory' if getattr(r, 'pure_memory', False) else getattr(r, 'as_statement', lambda: 'hybrid')())
    except Exception as e:
        print(s, f, 'THROW', type(e).__name__, getattr(e, 'code', ''), e)
```

**`cpp2.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <iostream>
using namespace sel::sql;
int main() {
  const Bindings b({{"ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", "o", SqlKind::Num)}})}});
  const char* s = "ORDERS .> TAKE(1/0)";
  try { auto p = Sql::plan_hybrid(sel::compile(s), "mariadb", b); std::cout << s << " plan: " << (p.pure_sql ? "pure_sql" : p.pure_memory ? "pure_memory" : "hybrid") << "\n"; }
  catch (const SqlError& e) { std::cout << s << " plan THROW SqlError " << e.code() << " " << e.what() << "\n"; }
  try { std::cout << s << " translate_statement: " << Sql::translate_statement(sel::compile(s), "mariadb", b).as_statement() << "\n"; }
  catch (const SqlError& e) { std::cout << s << " translate_statement THROW SqlError " << e.code() << " " << e.what() << "\n"; }
}
```

**`lisp2.lisp`**

```lisp
(let* ((bindings (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o" (list (cons "ID" (sel.sql:binding-column "id" "o" :num)))))))
       (s "ORDERS .> TAKE(1/0)"))
  (handler-case (let ((plan (sel.sql:plan-hybrid (sel:compile-source s) "mariadb" bindings)))
                  (format t "~a plan: ~a~%" s (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql") ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory") (t "hybrid"))))
    (sel.sql:sql-error (e) (format t "~a plan THROW SqlError ~a ~a~%" s (sel.sql:sql-error-code e) e))
    (error (e) (format t "~a plan THROW ~a~%" s (type-of e))))
  (handler-case (format t "~a translate-statement: ~a~%" s (sel.sql:as-statement (sel.sql:translate-statement (sel:compile-source s) "mariadb" bindings)))
    (sel.sql:sql-error (e) (format t "~a translate-statement THROW SqlError ~a ~a~%" s (sel.sql:sql-error-code e) e))
    (error (e) (format t "~a translate-statement THROW ~a~%" s (type-of e)))))
```

### Commands run and their output

Run JS reproduction of the planner with clist programs

```bash
mkdir -p $SCRATCH/verify-U && cd $SCRATCH/verify-U && # (file written, see "Reproduction scripts" above)
node js.mjs
```

```
[mariadb] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_sql sql=undefined
   translateStatement: undefined
   translate: undefined
[mariadb] R[1] = 5; R
   plan: pure_memory
   translateStatement THROW SqlError E_SQL_SHAPE expected a relational query or pipeline
   translate THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
[mariadb] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan: THROW TypeError  constants.refuseAsSel is not a function
   translateStatement THROW TypeError  constants.refuseAsSel is not a function
   translate THROW TypeError  constants.refuseAsSel is not a function
[mariadb] R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > SUM(R))
   plan: THROW SelError E_ARITY SUM takes 2 to 3 arguments, got 1
   translateStatement THROW SelError E_ARITY SUM takes 2 to 3 arguments, got 1
   translate THROW SelError E_ARITY SUM takes 2 to 3 arguments, got 1
[mariadb] R[1] = 3; ORDERS .> DROP(MAX(R))
   plan: THROW TypeError  constants.refuseAsSel is not a function
   translateStatement THROW TypeError  constants.refuseAsSel is not a function
   translate THROW TypeError  constants.refuseAsSel is not a function
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > MAX(R))
   plan: pure_memory
   translateStatement THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
   translate THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > R[1])
   plan: pure_memory
   translateStatement THROW SqlError E_SQL_SHAPE only a bound name can be indexed here; SQL has no way to index into the result of an expression
   translate THROW SqlError E_SQL_SHAPE only a bound name can be indexed here; SQL has no way to index into the result of an expression
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > COUNT(R)) .> TAKE(COUNT(R))
   plan: THROW TypeError  constants.refuseAsSel is not a function
   translateStatement THROW TypeError  constants.refuseAsSel is not a function
   translate THROW TypeError  constants.refuseAsSel is not a function
[postgresql] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_sql sql=undefined
   translateStatement: undefined
   translate: undefined
[postgresql] R[1] = 5; R
   plan: pure_memory
   translateStatement THROW SqlError E_SQL_SHAPE expected a relational query or pipeline
   translate THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
[postgresql] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan: THROW TypeError  constants.refuseAsSel is not a function
   translateStatement THROW TypeError  constants.refuseAsSel is not a function
   translate THROW TypeError  constants.refuseAsSel is not a function
[postgresql] R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > SUM(R))
   plan: THROW SelError E_ARITY SUM takes 2 to 3 arguments, got 1
   translateStatement THROW SelError E_ARITY SUM takes 2 to 3 arguments, got 1
   translate THROW SelError E_ARITY SUM takes 2 to 3 arguments, got 1
[postgresql] R[1] = 3; ORDERS .> DROP(MAX(R))
   plan: THROW TypeError  constants.refuseAsSel is not a function
   translateStatement THROW TypeError  constants.refuseAsSel is not a function
   translate THROW TypeError  constants.refuseAsSel is not a function
[postgresql] R[1] = 3; ORDERS .> FILTER(_["id"] > MAX(R))
   plan: pure_memory
   translateStatement THROW SqlError E_SQL_S
… (truncated)
```

Rerun JS repro with statement text, and in-memory REPL

```bash
cd $SCRATCH/verify-U && sed -i 's/p.sqlStatement.sql/p.sqlStatement.asStatement()/; s/f.sql)/f.asStatement())/g' js.mjs && sed -i "s/'R\[\"a\"\] = 1; R\[\"b\"\] = 2; ORDERS .> FILTER(_\[\"id\"\] > SUM(R))'/'R[\"a\"] = 1; R[\"b\"] = 2; ORDERS .> FILTER(_[\"id\"] > COUNT(R))'/" js.mjs && node js.mjs 2>&1 | grep -v "^\[postgresql" | head -40; echo; echo "== in-memory =="; cd /home/nathan/workspaces/nth-share/sel; for s in 'R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))' 'R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))' 'R[1] = 5; ORDERS .> TAKE(COUNT(R))'; do echo "$s"; node js/bin/sel.mjs -e "$s" 2>&1 | head -3; done
```

```
[mariadb] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_sql sql=SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
   translateStatement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
   translate: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
[mariadb] R[1] = 5; R
   plan: pure_memory
   translateStatement THROW SqlError E_SQL_SHAPE expected a relational query or pipeline
   translate THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
[mariadb] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan: THROW TypeError  constants.refuseAsSel is not a function
   translateStatement THROW TypeError  constants.refuseAsSel is not a function
   translate THROW TypeError  constants.refuseAsSel is not a function
[mariadb] R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_sql sql=SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 2)
   translateStatement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 2)
   translate: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 2)
[mariadb] R[1] = 3; ORDERS .> DROP(MAX(R))
   plan: THROW TypeError  constants.refuseAsSel is not a function
   translateStatement THROW TypeError  constants.refuseAsSel is not a function
   translate THROW TypeError  constants.refuseAsSel is not a function
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > MAX(R))
   plan: pure_memory
   translateStatement THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
   translate THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > R[1])
   plan: pure_memory
   translateStatement THROW SqlError E_SQL_SHAPE only a bound name can be indexed here; SQL has no way to index into the result of an expression
   translate THROW SqlError E_SQL_SHAPE only a bound name can be indexed here; SQL has no way to index into the result of an expression
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > COUNT(R)) .> TAKE(COUNT(R))
   plan: THROW TypeError  constants.refuseAsSel is not a function
   translateStatement THROW TypeError  constants.refuseAsSel is not a function
   translate THROW TypeError  constants.refuseAsSel is not a function
   plan: pure_sql sql=SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1)
   translate: SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1)
   plan: pure_memory
   translateStatement THROW SqlError E_SQL_SHAPE expected a relational query or pipeline
   translate THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
   plan: THROW TypeError  constants.refuseAsSel is not a function
   translateStatement THROW TypeError  constants.refuseAsSel is not a function

== in-memory ==
R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
E_UNDEF_VAR at line 1 column 11: undefined variable ORDERS
R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))
E_UNDEF_VAR at line 1 column 25: undefined variable ORDERS
R[1] = 5; ORDERS .> TAKE(COUNT(R))
E_UNDEF_VAR at line 1 column 11: undefined variable ORDERS
```

Run PHP reproduction

```bash
cd $SCRATCH/verify-U && # (file written, see "Reproduction scripts" above)
php php.php 2>&1
```

```
[mariadb] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_sql sql=SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
   translateStatement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
[mariadb] R[1] = 5; R
   plan: pure_memory
   translateStatement THROW Sel\Sql\SqlError E_SQL_SHAPE expected a relational query or pipeline
[mariadb] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan THROW Error  Call to private method Sel\Sql\Constants::refuseAsSel() from scope Sel\Sql\Translator
   translateStatement THROW Error  Call to private method Sel\Sql\Constants::refuseAsSel() from scope Sel\Sql\Translator
[mariadb] R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_sql sql=SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 2)
   translateStatement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 2)
[mariadb] R[1] = 3; ORDERS .> DROP(MAX(R))
   plan THROW Error  Call to private method Sel\Sql\Constants::refuseAsSel() from scope Sel\Sql\Translator
   translateStatement THROW Error  Call to private method Sel\Sql\Constants::refuseAsSel() from scope Sel\Sql\Translator
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > MAX(R))
   plan: pure_memory
   translateStatement THROW Sel\Sql\SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > R[1])
   plan: pure_memory
   translateStatement THROW Sel\Sql\SqlError E_SQL_SHAPE only a bound name can be indexed here; SQL has no way to index into the result of an expression
```

Run Python reproduction

```bash
cd $SCRATCH/verify-U && # (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel && PYTHONPATH=$PWD/python python3 $SCRATCH/verify-U/py.py 2>&1
```

```
[mariadb] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan THROW AttributeError  'CList' object has no attribute 'args' at /home/nathan/workspaces/nth-share/sel/python/sel/optimizer.py:31
   translate_statement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
[mariadb] R[1] = 5; R
   plan THROW AttributeError  'CList' object has no attribute 'args' at /home/nathan/workspaces/nth-share/sel/python/sel/optimizer.py:31
   translate_statement THROW SqlError E_SQL_SHAPE E_SQL_SHAPE at 0:0: expected a relational query or pipeline at /home/nathan/workspaces/nth-share/sel/python/sel/sql/errors.py:36
[mariadb] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan THROW AttributeError  'CList' object has no attribute 'args' at /home/nathan/workspaces/nth-share/sel/python/sel/optimizer.py:31
   translate_statement THROW AttributeError  module 'sel.sql.constants' has no attribute 'refuse_as_sel' at /home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py:1707
[mariadb] R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan THROW AttributeError  'CList' object has no attribute 'args' at /home/nathan/workspaces/nth-share/sel/python/sel/optimizer.py:31
   translate_statement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 2)
[mariadb] R[1] = 3; ORDERS .> DROP(MAX(R))
   plan THROW AttributeError  'CList' object has no attribute 'args' at /home/nathan/workspaces/nth-share/sel/python/sel/optimizer.py:31
   translate_statement THROW AttributeError  module 'sel.sql.constants' has no attribute 'refuse_as_sel' at /home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py:1707
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > MAX(R))
   plan THROW AttributeError  'CList' object has no attribute 'args' at /home/nathan/workspaces/nth-share/sel/python/sel/optimizer.py:31
   translate_statement THROW SqlError E_SQL_SHAPE E_SQL_SHAPE at 1:2: a list is not a SQL value; a list can only be the thing an aggregate iterates at /home/nathan/workspaces/nth-share/sel/python/sel/sql/errors.py:36
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > R[1])
   plan THROW AttributeError  'CList' object has no attribute 'args' at /home/nathan/workspaces/nth-share/sel/python/sel/optimizer.py:31
   translate_statement THROW SqlError E_SQL_SHAPE E_SQL_SHAPE at 1:39: only a bound name can be indexed here; SQL has no way to index into the result of an expression at /home/nathan/workspaces/nth-share/sel/python/sel/sql/errors.py:36
```

Run the Python repro against the pre-commit tree

```bash
mkdir -p $SCRATCH/verify-U/old && git archive 8fe0e3a python js php lisp | tar -x -C $SCRATCH/verify-U/old && cd $SCRATCH/verify-U && PYTHONPATH=$PWD/old/python python3 py.py 2>&1 | head -12
```

```
[mariadb] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan THROW AttributeError  'CList' object has no attribute 'args' at $SCRATCH/verify-U/old/python/sel/optimizer.py:31
   translate_statement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
[mariadb] R[1] = 5; R
   plan THROW AttributeError  'CList' object has no attribute 'args' at $SCRATCH/verify-U/old/python/sel/optimizer.py:31
   translate_statement THROW SqlError E_SQL_SHAPE E_SQL_SHAPE at 0:0: expected a relational query or pipeline at $SCRATCH/verify-U/old/python/sel/sql/errors.py:36
[mariadb] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan THROW AttributeError  'CList' object has no attribute 'args' at $SCRATCH/verify-U/old/python/sel/optimizer.py:31
   translate_statement THROW AttributeError  module 'sel.sql.constants' has no attribute 'refuse_as_sel' at $SCRATCH/verify-U/old/python/sel/sql/translator.py:1917
[mariadb] R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan THROW AttributeError  'CList' object has no attribute 'args' at $SCRATCH/verify-U/old/python/sel/optimizer.py:31
   translate_statement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 2)
```

Run JS and PHP repros against the pre-commit tree

```bash
cd $SCRATCH/verify-U && sed "s#/home/nathan/workspaces/nth-share/sel/js#$PWD/old/js#g" js.mjs > js-old.mjs && node js-old.mjs 2>&1 | grep -v "^\[postgresql" | sed -n 1,12p; echo =====; sed "s#/home/nathan/workspaces/nth-share/sel/php#$PWD/old/php#" php.php > php-old.php && php php-old.php 2>&1 | head -8
```

```
[mariadb] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_memory
   translateStatement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
   translate: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
[mariadb] R[1] = 5; R
   plan: pure_memory
   translateStatement THROW SqlError E_SQL_SHAPE expected a relational query or pipeline
   translate THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
[mariadb] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan: pure_memory
   translateStatement THROW TypeError  constants.refuseAsSel is not a function
   translate THROW TypeError  constants.refuseAsSel is not a function
=====
[mariadb] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_sql sql=SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
   translateStatement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
[mariadb] R[1] = 5; R
   plan: pure_memory
   translateStatement THROW Sel\Sql\SqlError E_SQL_SHAPE expected a relational query or pipeline
[mariadb] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan THROW Error  Call to private method Sel\Sql\Constants::refuseAsSel() from scope Sel\Sql\Translator
```

Compile and run C++ reproduction against built objects

```bash
make -C cpp -q build/sel.o build/sel_sql_hybrid.o 2>/dev/null; echo "make -q status: $?"; # (file written, see "Reproduction scripts" above)
cd $SCRATCH/verify-U && c++ -std=c++23 -O0 -o cpp_probe cpp.cpp /home/nathan/workspaces/nth-share/sel/cpp/build/sel_sql*.o /home/nathan/workspaces/nth-share/sel/cpp/build/sel.o 2>&1 | head -20 && ./cpp_probe
```

```
Exit code 127
make -q status: 0
cpp.cpp: In function ‘int main()’:
cpp.cpp:23:84: error: invalid use of non-static member function ‘const std::string& sel::sql::SqlError::code() const’
   23 |     } catch (const sel::sql::SqlError& e) { std::cout << "   plan THROW SqlError " << e.code << " " << e.what() << "\n"; }
      |                                             ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^~~~~~~~~
In file included from cpp.cpp:2:
/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp:43:22: note: declared here
   43 |   const std::string& code() const noexcept { return code_; }
      |                      ^~~~
cpp.cpp:27:99: error: invalid use of non-static member function ‘const std::string& sel::sql::SqlError::code() const’
   27 |     } catch (const sel::sql::SqlError& e) { std::cout << "   translate_statement THROW SqlError " << e.code << " " << e.what() << "\n"; }
      |                                             ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^~~~~~~~~
/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp:43:22: note: declared here
   43 |   const std::string& code() const noexcept { return code_; }
      |                      ^~~~
cpp.cpp:31:89: error: invalid use of non-static member function ‘const std::string& sel::sql::SqlError::code() const’
   31 |     } catch (const sel::sql::SqlError& e) { std::cout << "   translate THROW SqlError " << e.code << " " << e.what() << "\n"; }
      |                                             ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^~~~~~~~~
/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp:43:22: note: declared here
   43 |   const std::string& code() const noexcept { return code_; }
      |                      ^~~~
/bin/bash: line 37: ./cpp_probe: No such file or directory
```

Compile and run C++ reproduction

```bash
cd $SCRATCH/verify-U && sed -i 's/e\.code /e.code() /g' cpp.cpp && c++ -std=c++23 -O0 -o cpp_probe cpp.cpp /home/nathan/workspaces/nth-share/sel/cpp/build/sel_sql*.o /home/nathan/workspaces/nth-share/sel/cpp/build/sel.o 2>&1 | head -20 && ./cpp_probe
```

```
[mariadb] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_memory
   translate_statement: SELECT `o`.* FROM `orders` `o` WHERE (`id` > 1)
   translate: SELECT `o`.* FROM `orders` `o` WHERE (`id` > 1)
[mariadb] R[1] = 5; R
   plan: pure_memory
   translate_statement:    translate_statement THROW SqlError E_SQL_SHAPE expected a relational query or pipeline
   translate:    translate THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
[mariadb] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan: pure_memory
   translate_statement:    translate_statement THROW SqlError E_SQL_SHAPE TAKE count cannot contain dynamic lists
   translate:    translate THROW SqlError E_SQL_SHAPE TAKE count cannot contain dynamic lists
[mariadb] R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_memory
   translate_statement: SELECT `o`.* FROM `orders` `o` WHERE (`id` > 2)
   translate: SELECT `o`.* FROM `orders` `o` WHERE (`id` > 2)
[mariadb] R[1] = 3; ORDERS .> DROP(MAX(R))
   plan: pure_memory
   translate_statement:    translate_statement THROW SqlError E_SQL_SHAPE DROP count cannot contain dynamic lists
   translate:    translate THROW SqlError E_SQL_SHAPE DROP count cannot contain dynamic lists
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > MAX(R))
   plan: pure_memory
   translate_statement:    translate_statement THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
   translate:    translate THROW SqlError E_SQL_SHAPE a list is not a SQL value; a list can only be the thing an aggregate iterates
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > R[1])
   plan: pure_memory
   translate_statement:    translate_statement THROW SqlError E_SQL_SHAPE only a bound name can be indexed here; SQL has no way to index into the result of an expression
   translate:    translate THROW SqlError E_SQL_SHAPE only a bound name can be indexed here; SQL has no way to index into the result of an expression
```

Run Lisp reproduction

```bash
sed -n 354,362p lisp/tests/unit.lisp; # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $SCRATCH/verify-U/lisp.lisp 2>&1 | grep -v "^;" | head -40
```

```
;; Ambiguous column rejection
    (let ((p (sel:compile-source "ORDERS .> LINK(CUSTOMERS, _['c_id'] == _2['id']) .> FILTER(_['id'] == 1)")))
      (handler-case (sel.sql:translate-statement p "postgresql" bindings)
        (sel.sql:sql-error (e)
          (is (string= "E_SQL_SHAPE" (sel.sql:sql-error-code e))))
        (:no-error (val)
          (declare (ignore val))
          (fail "Expected E_SQL_SHAPE for ambiguous column"))))

Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=809591 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 2, Column: 42, File-Position: 57

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-U/lisp.lisp" {1201ED65B3}>

Backtrace for: #<SB-THREAD:THREAD tid=809591 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201ED77E3}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201ED77E3}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201ED77E3}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201ED77E3}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201ED7793}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-U/lisp.lisp" {1201ED65B3}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {717F843DF4CB}> #<SB-C::SOURCE-INFO {1201E77813}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-U/lisp.lisp" {1201ED65B3}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-U/lisp.lisp" {1201ED65B3}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {717F843DF88B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-U/lisp.lisp" {1201ED65B3}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-U/lisp.lisp" {1201ED65B3}>)
9: (LOAD #P"$SCRATCH/verify-U/lisp.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-U/lisp.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

See how Lisp SQL package is loaded

```bash
cat lisp/bin/boot.lisp | head -30; sed -n 1,20p lisp/bin/sqlt.lisp; grep -n "binding-column" lisp/src/sql/binding.lisp | head -3
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
;;;; The SEL->SQL conformance suite for the Common Lisp host.
;;;;
;;;;   lisp/bin/sqlt                 every case
;;;;   lisp/bin/sqlt bind. agg.      only cases whose name contains one of these
;;;;   lisp/bin/sqlt --names         what this host loaded, and stop
;;;;   lisp/bin/sqlt --print-base 16 every case, with the printer set hostile
;;;;
;;;; --print-base is this host's alone and pins something no case file can
;;;; state. *PRINT-BASE* belongs to the calling application, and this layer may
;;;; not read it: every number it decides at translation time is a SEL number,
;;;; and SEL numbers are decimal by specification (spec/SPEC.md 4). A single
;;;; PRINC-TO-STRING anywhere in the translator renders a twelve-element list's
;;;; count as "C" under a caller that had rebound it, MAKE-NUM raises E_NOT_NUM,
;;;; and that SEL-ERROR escapes TRY-TRANSLATE, which catches SQL-ERROR alone.
;;;; Running the whole corpus this way says so for every such site at once, and
;;;; keeps saying it for sites not written yet.
;;;;
;;;; The cases live in sql/cases/*.sqlt and reach here through
;;;; tools/gen-sql-cases.mjs, which is the only thing that reads them. Nothing
;;;; in this file parses anything: a case's bindings and registrations arrive as
89:(defun binding-column (column &optional table (type :unknown) &key exact sargable guard collation prefilter split-sargable)
143:(defun binding-columns (&rest items)
```

Run Lisp reproduction with SQL system loaded

```bash
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $SCRATCH/verify-U/lisp.lisp 2>&1 | grep -v "^;" | head -40
```

```
[mariadb] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_sql sql=SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
   translate-statement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
[mariadb] R[1] = 5; R
   plan: pure_memory
   translate-statement THROW SqlError E_SQL_SHAPE E_SQL_SHAPE at 0:0: expected a relational query or pipeline
[mariadb] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan THROW TYPE-ERROR: The value
                            #S(SEL.SQL::CLIST
                               :POS #S(SEL::POS :LINE 1 :COL 2 :OFFSET 1)
                               :ENTRIES (("1"
                                          . #S(SEL::NODE
                                               :KIND :NUM
                                               :POS #S(SEL::POS
                                                       :LINE 1
                                                       :COL 8
                                                       :OFFSET 7)
                                               :S "5"
                                               :B NIL
                                               :GROUPED NIL
                                               :L NIL
                                               :R NIL
                                               :ITEMS NIL
                                               :SPEC NIL))))

                          is not of type
                            SEL::NODE
   translate-statement THROW TYPE-ERROR: The value
                                           #S(SEL.SQL::CLIST
                                              :POS #S(SEL::POS
                                                      :LINE 1
                                                      :COL 2
                                                      :OFFSET 1)
                                              :ENTRIES (("1"
                                                         . #S(SEL::NODE
                                                              :KIND :NUM
                                                              :POS #S(SEL::POS
                                                                      :LINE 1
                                                                      :COL 8
                                                                      :OFFSET 7)
```

Show remaining Lisp reproduction lines

```bash
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $SCRATCH/verify-U/lisp.lisp 2>&1 | grep -v "^;" | grep -E "^\[|^   (plan|translate)" | sed -n 8,30p
```

```
plan THROW TYPE-ERROR: The value
   translate-statement THROW TYPE-ERROR: The value
[mariadb] R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan: pure_sql sql=SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 2)
   translate-statement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 2)
[mariadb] R[1] = 3; ORDERS .> DROP(MAX(R))
   plan THROW TYPE-ERROR: The value
   translate-statement THROW TYPE-ERROR: The value
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > MAX(R))
   plan: pure_memory
   translate-statement THROW SqlError E_SQL_SHAPE E_SQL_SHAPE at 1:2: a list is not a SQL value; a list can only be the thing an ~
[mariadb] R[1] = 3; ORDERS .> FILTER(_["id"] > R[1])
   plan: pure_memory
   translate-statement THROW SqlError E_SQL_SHAPE E_SQL_SHAPE at 1:39: only a bound name can be indexed here; SQL has no way to index ~
```

Run Lisp repro against the pre-commit tree

```bash
cd $SCRATCH/verify-U && sbcl --noinform --disable-debugger --non-interactive --load old/lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --eval '(format t "loaded from: ~a~%" (asdf:system-source-directory :sel-lang))' --load lisp.lisp 2>&1 | grep -v "^;" | grep -E "^loaded|^\[|^   (plan|translate)" | head -12
```

```
loaded from: $SCRATCH/verify-U/old/lisp/
[mariadb] R[1] = 5; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan THROW TYPE-ERROR: The value
   translate-statement: SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
[mariadb] R[1] = 5; R
   plan THROW TYPE-ERROR: The value
   translate-statement THROW SqlError E_SQL_SHAPE E_SQL_SHAPE at 0:0: expected a relational query or pipeline
[mariadb] R[1] = 5; ORDERS .> TAKE(COUNT(R))
   plan THROW TYPE-ERROR: The value
   translate-statement THROW TYPE-ERROR: The value
[mariadb] R["a"] = 1; R["b"] = 2; ORDERS .> FILTER(_["id"] > COUNT(R))
   plan THROW TYPE-ERROR: The value
```

Confirm the probe programs are valid SEL in memory on all hosts

```bash
for s in 'R[1] = 5; (1,2,3) .> FILTER(_ > COUNT(R))' 'R[1] = 5; (1,2,3) .> TAKE(COUNT(R))' 'R[1] = 3; (1,2,3) .> FILTER(_ > MAX(R))'; do echo "== $s"; node js/bin/sel.mjs -e "$s"; php php/bin/sel -e "$s"; cpp/build/sel -e "$s"; lisp/bin/sel -e "$s"; PYTHONPATH=$PWD/python python3 -m sel -e "$s"; done 2>&1
```

```
== R[1] = 5; (1,2,3) .> FILTER(_ > COUNT(R))
-{"2"=t"2", "3"=t"3"}
-{"2"=t"2", "3"=t"3"}
-{"2"=t"2", "3"=t"3"}
-{"2"=t"2", "3"=t"3"}
-{"2"=t"2", "3"=t"3"}
== R[1] = 5; (1,2,3) .> TAKE(COUNT(R))
-{"1"=t"1"}
-{"1"=t"1"}
-{"1"=t"1"}
-{"1"=t"1"}
-{"1"=t"1"}
== R[1] = 3; (1,2,3) .> FILTER(_ > MAX(R))
-
-
-
-
-
```

Probe whether the JS refuseAsSel path is reachable without a clist

```bash
cd $SCRATCH/verify-U && # (file written, see "Reproduction scripts" above)
node js2.mjs
```

```
ORDERS .> TAKE(1/0) planHybrid THROW TypeError  constants.refuseAsSel is not a function
ORDERS .> TAKE(1/0) translateStatement THROW TypeError  constants.refuseAsSel is not a function
ORDERS .> TAKE("a" + 1) planHybrid THROW TypeError  constants.refuseAsSel is not a function
ORDERS .> TAKE("a" + 1) translateStatement THROW TypeError  constants.refuseAsSel is not a function
ORDERS .> TAKE(COUNT((1,2))) planHybrid pure_sql SELECT `o`.* FROM `orders` `o` LIMIT 2
ORDERS .> TAKE(COUNT((1,2))) translateStatement SELECT `o`.* FROM `orders` `o` LIMIT 2
R = (1,2); ORDERS .> TAKE(COUNT(R)) planHybrid pure_sql SELECT `o`.* FROM `orders` `o` LIMIT 2
R = (1,2); ORDERS .> TAKE(COUNT(R)) translateStatement SELECT `o`.* FROM `orders` `o` LIMIT 2
```

Probe TAKE(1/0) across the other four hosts in both lanes

```bash
cd $SCRATCH/verify-U && # (file written, see "Reproduction scripts" above)
php php2.php
# (file written, see "Reproduction scripts" above)
PYTHONPATH=/home/nathan/workspaces/nth-share/sel/python python3 py2.py
# (file written, see "Reproduction scripts" above)
c++ -std=c++23 -O0 -o cpp2 cpp2.cpp /home/nathan/workspaces/nth-share/sel/cpp/build/sel_sql*.o /home/nathan/workspaces/nth-share/sel/cpp/build/sel.o && ./cpp2
# (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $SCRATCH/verify-U/lisp2.lisp 2>&1 | grep -v "^;"
```

```
ORDERS .> TAKE(1/0) plan THROW Error  Call to private method Sel\Sql\Constants::refuseAsSel() from scope Sel\Sql\Translator
ORDERS .> TAKE(1/0) translateStatement THROW Error  Call to private method Sel\Sql\Constants::refuseAsSel() from scope Sel\Sql\Translator
ORDERS .> TAKE(1/0) plan_hybrid THROW AttributeError  module 'sel.sql.constants' has no attribute 'refuse_as_sel'
ORDERS .> TAKE(1/0) translate_statement THROW AttributeError  module 'sel.sql.constants' has no attribute 'refuse_as_sel'
ORDERS .> TAKE(1/0) plan: pure_memory
ORDERS .> TAKE(1/0) translate_statement: ORDERS .> TAKE(1/0) translate_statement THROW SqlError E_SQL_INVALID SEL rejects this expression (E_DIV_ZERO: division by zero), so there is nothing to translate; a database would answer something rather than fail
ORDERS .> TAKE(1/0) plan: pure_memory
ORDERS .> TAKE(1/0) translate-statement THROW SqlError E_SQL_INVALID E_SQL_INVALID at 1:17: SEL rejects this expression (E_DIV_ZERO: division by zero), so there is nothing to translate; a database would answer something rather than fail
```

Confirm TAKE(1/0) crash pre-exists and check fixture coverage

```bash
cd $SCRATCH/verify-U && sed "s#/home/nathan/workspaces/nth-share/sel/js#$PWD/old/js#g" js2.mjs > js2-old.mjs && node js2-old.mjs | head -2; sed "s#/home/nathan/workspaces/nth-share/sel/php#$PWD/old/php#" php2.php > php2-old.php && php php2-old.php; PYTHONPATH=$PWD/old/python python3 py2.py; cd /home/nathan/workspaces/nth-share/sel && grep -rn "TAKE(1/0)\|TAKE(1 / 0)\|refuse_as_sel\|refuseAsSel" sql/cases/*.sqlt | head -5; grep -c "" sql/cases/25-hybrid-plans.sqlt; grep -n "R\[1\]\|clist\|R\[\"" sql/cases/25-hybrid-plans.sqlt | head
```

```
ORDERS .> TAKE(1/0) planHybrid THROW TypeError  constants.refuseAsSel is not a function
ORDERS .> TAKE(1/0) translateStatement THROW TypeError  constants.refuseAsSel is not a function
ORDERS .> TAKE(1/0) plan THROW Error  Call to private method Sel\Sql\Constants::refuseAsSel() from scope Sel\Sql\Translator
ORDERS .> TAKE(1/0) translateStatement THROW Error  Call to private method Sel\Sql\Constants::refuseAsSel() from scope Sel\Sql\Translator
ORDERS .> TAKE(1/0) plan_hybrid THROW AttributeError  module 'sel.sql.constants' has no attribute 'refuse_as_sel'
ORDERS .> TAKE(1/0) translate_statement THROW AttributeError  module 'sel.sql.constants' has no attribute 'refuse_as_sel'
602
```

