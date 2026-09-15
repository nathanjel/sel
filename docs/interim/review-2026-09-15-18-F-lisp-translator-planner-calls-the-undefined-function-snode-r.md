# F. Lisp translator/planner calls the undefined function SNODE-R on sealed-bucket shapes

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "Grouped and sorted TEXT keys are collated".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

lisp/src/sql/translator.lisp:1797-1800 (plan-output-fields) calls SEL.SQL::SNODE-R, which is defined nowhere (stage1.lisp:41-42 defines only snode-kind/snode-pos); it is reached whenever wrap-plan-as-derived-table wraps a plan whose projections carry a nil alias with an :index node — every bare BUCKET(key) followed by LINK/LINK_LEFT/BUCKET/SELECT_COLS/TAKE+FILTER, an explicit-binder bucket followed by MAP, a multi-key bucket followed by a non-RECORD MAP, and even a non-bucket `MAP(_["col"]) .> SELECT_COLS(...)`. Because the condition is UNDEFINED-FUNCTION and not sql-error, try-translate-statement (translator.lisp:2389-2392) does not catch it and both translate-statement and plan-hybrid throw where JS/PHP/Python/C++ agree byte-for-byte on pure_sql / pure_memory / E_SQL_SHAPE / E_SQL_DEPTH. The site was introduced by e4b0432 and reproduces identically against the parent commit's Lisp tree, so it is pre-existing, but it directly breaks the §12.1/EXTENDING promise the commit restates ("never an exception out of plan_hybrid") on the sealed-bucket shapes the commit adds. One facet of member 2 is refuted: `BUCKET(_["k"]) .> MAP(G, RECORD(..., COUNT(G)))` with `_` as the bucket binder does NOT crash — all five hosts, Lisp included, produce the same pure_sql statement; only the explicit-bucket-binder form `BUCKET(O, O["k"]) .> MAP(G, ...)` crashes.

## Suggested fix — case first

In lisp/src/sql/translator.lisp:1797-1800 replace the four `(snode-r (third p))` calls with `(sel::node-r (third p))` (the :index node's right child, as stage1.lisp:319 and the old is-same-field code already use); the `(not (clist-p (third p)))` guard on line 1796 already makes that safe. Optionally align the fallback name "col" (translator.lisp:1801) with the other hosts' `expr<N>` (js/src/sql/translator.mjs:1705). Add first, to sql/cases/24-bucket.sqlt, `ITEMS .> BUCKET(_["dept"]) .> LINK(DEPTS, i, d, i["dept"] == d["name"])` (mariadb, ITEMS{DEPT}, DEPTS{NAME}, as statement, expect `SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (...)` as the four agreeing hosts render it), plus a `--- plan` case in 25-hybrid-plans.sqlt for `ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept")` (pure_sql) and `ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))` (pure_memory); then run `node tools/gen-sql-cases.mjs`. After the crash is gone, expect Lisp to still diverge on `BUCKET(k) .> TAKE(n) .> FILTER(COUNT(_) > c)` (Lisp wraps and emits an aggregate in WHERE; others emit HAVING before LIMIT) — that is a second, separate case to pin.

## Verifier reasoning

Read the code: translator.lisp:1789-1800 plan-output-fields tests `(snode-r (third p))` for nil-alias projections; `grep -n "snode-r\b" lisp/src/sql/*.lisp` finds only the four call sites and no defun; stage1.lisp:41-42 has snode-kind/snode-pos only. Nil-alias :index projections are produced at translator.lisp:1882 (single-expression bucket projection), 1976/1986 (bucket key group-by entries copied into projections) and 2115 (non-RECORD MAP). wrap-plan-as-derived-table (translator.lisp:1825-1836) always calls plan-output-fields, and the commit extends it with `:bucket (and ... :sealed)` making these paths the sealed-bucket lane. try-translate-statement (2389-2392) catches sql-error only, so plan-hybrid (hybrid.lisp:97,118) lets UNDEFINED-FUNCTION escape. Ran the same 15-program corpus through translate_statement and plan_hybrid in all five hosts with identical bindings (mariadb): JS, PHP, Python and C++ outputs are byte-identical to each other; Lisp throws UNDEFINED-FUNCTION on 11 of 15. Own reproductions beyond the reported ones: `ITEMS .> BUCKET(_["dept"]) .> LINK_LEFT(...)`, `.> SORT_BY(_K) .> LINK(...)`, `.> SELECT_COLS("dept")`, and non-bucket `ITEMS .> MAP(_["dept"]) .> SELECT_COLS("dept")` (others: pure_sql `SELECT _sub1.dept FROM (SELECT dept FROM items) _sub1`; Lisp: throw). Pre-existence: `git archive 8fe0e3a lisp` into scratchpad and loaded via its own boot.lisp; identical UNDEFINED-FUNCTION on the same programs (and additionally on `BUCKET(_["dept"]) .> MAP(g, RECORD(...SUM(g,x,...)))`, which the commit fixed by routing it through bucket-projection). Suite blindness confirmed: `lisp/bin/sqlt bucket plan` is 51 passed / 0 failed; no .sqlt case wraps a bare or explicit-binder bucket with LINK/SELECT_COLS/BUCKET. Facet check on member 2: with both unaliased and aliased ("o") relation bindings, `ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))` is pure_sql `SELECT dept AS d, COUNT(*) AS n FROM items GROUP BY dept` in all five hosts including Lisp, so that specific repro (and its "others answer pure_memory" claim) does not reproduce; the explicit-bucket-binder form does crash as claimed. Defining snode-r at runtime (monkey-patch, no tracked file touched) removes every crash; 11 of 15 programs then match the other hosts exactly, and the remaining diffs are two separate pre-existing Lisp divergences unmasked by the fix: (a) `BUCKET .> TAKE(2) .> FILTER(COUNT(_) > 1)` — Lisp wraps and emits `WHERE (COUNT(*) > 1)` on the outer query (invalid SQL) where the other four emit `HAVING ... LIMIT 2`; (b) LINK against an unaliased relation binding renders the joined column unqualified (`name` vs `D`.`name`) — reproduced without any bucket (`ITEMS .> LINK(DEPTS, i, d, i["dept"] == d["name"])`), so it is not this cluster. Severity high: Lisp throws an internal error where four hosts return a result, and plan_hybrid violates the documented never-throws promise.

## Verifier evidence

```
Code: lisp/src/sql/translator.lisp:1789-1811 plan-output-fields, :1797-1800 the four `(snode-r (third p))` calls; lisp/src/sql/stage1.lisp:41-42 only snode-kind/snode-pos; translator.lisp:1825-1836 wrap-plan-as-derived-table (line 1836 `:bucket (and (relational-plan-bucket plan) :sealed)` added by this commit); translator.lisp:1882, 1976, 1986, 2115 create nil-alias projections; translator.lisp:2389-2392 try-translate-statement catches sql-error only; hybrid.lisp:97 and :118 call it from plan-hybrid. `git show 8fe0e3a:lisp/src/sql/translator.lisp | grep -n snode-r` -> lines 1797-1800 present at parent; `git log -S"snode-r (third p)"` -> e4b0432. JS analog js/src/sql/translator.mjs:1696-1707 outputFieldNames uses `projection.node.idx.v` for the same case (works). Probes in /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-F/ (probe.lisp, probe.mjs, probe.py, probe.php, probe.cpp, programs.txt, programs2.txt); commands: `sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --end-toplevel-options $S/programs.txt`, `node $S/probe.mjs $S/programs.txt`, `PYTHONPATH=$PWD/python python3 $S/probe.py ...`, `php $S/probe.php ...`, `g++ -std=c++23 -O1 -o $S/probe_cpp $S/probe.cpp cpp/build/sel_sql*.o cpp/build/sel.o && $S/probe_cpp ...`. Sample outputs — `ITEMS .> BUCKET(_["dept"]) .> LINK(DEPTS, i, d, i["dept"] == d["name"])`: JS/PHP/Py/C++ translate = `SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (...)`, plan = pure_sql; Lisp translate and plan = `THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.` `ITEMS .> BUCKET(_["dept"]) .> BUCKET(_K)`: others E_SQL_DEPTH 1:38 / pure_memory; Lisp throw. `ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept")`: others pure_sql `SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1``; Lisp throw. `ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))`: others E_SQL_SHAPE 1:53 / pure_memory; Lisp throw. `ITEMS .> BUCKET(g, g["dept"]) .> MAP(RECORD("d", _K, "n", COUNT(_)))`: others E_SQL_SHAPE 1:50 / pure_memory; Lisp throw. `ITEMS .> BUCKET(_["dept"], _["amount"]) .> MAP(RECORD("x", _["amount"]))`: others pure_sql `SELECT `_sub1`.`amount` AS `x` FROM (SELECT `amount` FROM `items` GROUP BY `dept`) `_sub1``; Lisp throw. `ITEMS .> MAP(_["dept"]) .> SELECT_COLS("dept")` (non-bucket): others pure_sql; Lisp throw. Refuted facet: `ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))` -> all five hosts pure_sql `SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`` (also with alias "o"). Parent tree: `git archive 8fe0e3a lisp | tar -x -C $S/old` then same probe via `$S/old/lisp/bin/boot.lisp` -> same UNDEFINED-FUNCTION on all these programs. Suite: `lisp/bin/sqlt bucket plan` -> `51 passed ..., 0 failed, 0 suite errors`. Patched run (`(defun sel.sql::snode-r (n) (if (sel.sql::clist-p n) nil (sel::node-r n)))` prepended to the probe) -> no throws; diff vs JS leaves only the TAKE-then-FILTER `WHERE (COUNT(*) > 1)` shape and the unqualified `name` join column, the latter reproduced without BUCKET on `ITEMS .> LINK(DEPTS, i, d, i["dept"] == d["name"])` (Lisp: `CASE WHEN (`dept` REGEXP ...` / `(`name` REGEXP`; JS: `items`.`dept` / `D`.`name`).
```

## Original review reports (deduplicated into this finding)

### [bucket-translator] Lisp translator/planner crash (UNDEFINED-FUNCTION SNODE-R) on every sealed-bucket shape that wraps a derived table

*correctness · high · hosts: lisp*

Locations: `lisp/src/sql/translator.lisp:1797`; `lisp/src/sql/translator.lisp:1836`; `lisp/src/sql/hybrid.lisp:97`; `lisp/src/sql/hybrid.lisp:118`

plan-output-fields calls (snode-r ...) which is not defined in SEL.SQL (only snode-kind/snode-pos exist, stage1.lisp:41-42); it is reached whenever a plan whose projections carry no alias — exactly a bare bucket's key projection — is wrapped by wrap-plan-as-derived-table, i.e. the very function the diff extends with `:bucket (and ... :sealed)`. So BUCKET(k) followed by LINK/LINK_LEFT, a second BUCKET, or SELECT_COLS (and, because of Lisp's own FILTER wrap rule, BUCKET(k) .> TAKE(n) .> FILTER(...)) raises an internal error instead of SQL or E_SQL_SHAPE. Both translate and plan-hybrid propagate it (try-translate only catches sql-error), so plan_hybrid throws where the other four hosts answer pure_sql or pure_memory. The function is pre-existing but the sealed-bucket promise routes these shapes through it and no fixture covers them.

Reported repro:

```
Scratch case 99-probe.sqlt p10: ITEMS .> BUCKET(_["dept"]) .> LINK(DEPTS, i, d, i["dept"] == d["name"]) — JS/PHP/Py/C++: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN ...; Lisp: SUITE ERROR unexpected UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined. Same for p06 (BUCKET .> BUCKET), p09 (BUCKET .> LINK .> MAP: others E_SQL_SHAPE 1:76), p23 (BUCKET .> SELECT_COLS .> MAP), r08 (BUCKET .> TAKE(2) .> FILTER(COUNT(_) > 1)). Planner probes 98-plan-probe.sqlt q02/q06/q07/q12: four hosts classify pure_memory/pure_sql, Lisp plan-hybrid raises the same UNDEFINED-FUNCTION.
```

### [hybrid-planner] Lisp translator calls the undefined function SNODE-R when a bucket's MAP has an explicit binder, and the error escapes plan_hybrid

*correctness · high · hosts: lisp*

Locations: `lisp/src/sql/translator.lisp:1797-1800`; `lisp/src/sql/hybrid.lisp:97`; `lisp/src/sql/stage1.lisp:41-42`

plan-output-fields references `snode-r`, which is never defined (stage1.lisp defines only snode-kind and snode-pos). It is reached when a projection carries no alias and its node is an :index, which the new bucket-projection path produces for `BUCKET(k) .> MAP(G, RECORD(... COUNT(G)))`. Because the condition is an UNDEFINED-FUNCTION error rather than a sql-error, try-translate-statement does not catch it and plan-hybrid aborts, where the other four hosts answer pure_memory (JS/PHP/Python) or refuse cleanly. The fixtures only use `_` as the MAP binder after a bucket.

Reported repro:

```
mariadb, `ORDERS .> BUCKET(_["customer_id"]) .> MAP(G, RECORD("cid", _K, "n", COUNT(G)))`: js/php/py/cpp plan_hybrid -> class=pure_memory (tables [orders]); lisp -> THROW The function SEL.SQL::SNODE-R is undefined. Same for `ORDERS .> BUCKET(O, O["customer_id"]) .> MAP(G, RECORD("cid", _K, "n", COUNT(G)))` where the other hosts refuse E_SQL_SHAPE@1:63 via translate_statement.
```

### [promises-vs-code] Lisp translator calls the undefined function SNODE-R: a non-SqlError escapes translate-statement AND plan-hybrid for bucket shapes the other hosts plan or refuse

*correctness · high · hosts: lisp*

Locations: `lisp/src/sql/translator.lisp:1797`; `lisp/src/sql/hybrid.lisp:97`; `lisp/src/sql/hybrid.lisp:118`

§12.1/EXTENDING promise 'Anything stage 1 or the translator refuses is a shorter prefix or a pure-memory plan, never an exception out of plan_hybrid'. plan-output-fields (reached whenever a plan with a nil-alias :index projection is wrapped as a derived table) calls SNODE-R, which does not exist (stage1.lisp defines only snode-kind and snode-pos). try-translate-statement catches sql-error only, so UNDEFINED-FUNCTION escapes plan-hybrid. Reachable from the very shapes this commit's bucket work makes common: a projected bucket with a non-RECORD projection followed by any wrapping step, and an open bucket followed by BUCKET or SELECT_COLS. The site predates the commit (e4b0432) but the commit's fixtures never wrap such a projection. The other four hosts return pure_sql / pure_memory / E_SQL_SHAPE for the same programs.

Reported repro:

```
TR=1 ./run.sh cases2.txt: 'ORDERS .> BUCKET(_["customer_id"], _["amount"]) .> MAP(RECORD("x", _["amount"]))' -> js/py/php/cpp pure_sql `SELECT _sub1.amount AS x FROM (SELECT o.amount FROM orders o GROUP BY o.customer_id) _sub1`; lisp: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined (both plan-hybrid and translate-statement). Same for 'ORDERS .> BUCKET(g, g["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))' (others: pure_memory / E_SQL_SHAPE@1:60), 'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K)' (others: E_SQL_DEPTH@1:46) and 'ORDERS .> BUCKET(_["customer_id"]) .> SELECT_COLS("customer_id") .> MAP(RECORD("cid", _K))' (others: pure_memory).
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$S/programs.txt`**

```
ITEMS .> BUCKET(_["dept"]) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
ITEMS .> BUCKET(_["dept"]) .> LINK_LEFT(DEPTS, i, d, i["dept"] == d["name"])
ITEMS .> BUCKET(_["dept"]) .> BUCKET(_K)
ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept")
ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept") .> MAP(RECORD("d", _K))
ITEMS .> BUCKET(_["dept"]) .> TAKE(2) .> FILTER(COUNT(_) > 1)
ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
ITEMS .> BUCKET(g, g["dept"]) .> MAP(RECORD("d", _K, "n", COUNT(_)))
ITEMS .> BUCKET(_["dept"], _["amount"]) .> MAP(RECORD("x", _["amount"]))
ITEMS .> BUCKET(_["dept"]) .> MAP(g, RECORD("dept", _K, "total", SUM(g, x, x["amount"])))
ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "cnt", COUNT(_)))
ITEMS .> BUCKET(_["dept"]) .> TAKE(2) .> MAP(RECORD("dept", _K))
ITEMS .> BUCKET(_["dept"]) .> SORT_BY(_K) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
ITEMS .> BUCKET(_["dept"], _["amount"]) .> LINK(DEPTS, i, d, i["x"] == d["name"])
```

**`$S/probe.lisp`**

```lisp
(let ((*standard-output* (make-broadcast-stream)))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
(in-package :sel.sql)
(defun binds ()
  (list (cons "ITEMS" (binding-relation "items" nil
                        (list (cons "DEPT" (binding-column "dept" nil :text))
                              (cons "AMOUNT" (binding-column "amount" nil :num)))))
        (cons "DEPTS" (binding-relation "depts" nil
                        (list (cons "NAME" (binding-column "name" nil :text))
                              (cons "ID" (binding-column "id" nil :num)))))))
(defun classify (plan)
  (cond ((hybrid-plan-pure-sql-p plan) "pure_sql")
        ((hybrid-plan-pure-memory-p plan) "pure_memory")
        (t "hybrid")))
(defun probe (src)
  (format t "~&== ~a~%" src)
  (let ((p (sel:compile-source src)))
    (format t "  translate: ~a~%"
      (handler-case (as-statement (translate-statement p "mariadb" (binds)))
        (sql-error (e) (format nil "~a ~a:~a" (sql-error-code e) (sql-error-line e) (sql-error-col e)))
        (error (e) (format nil "THROW ~a: ~a" (type-of e) e))))
    (format t "  plan:      ~a~%"
      (handler-case (let ((plan (plan-hybrid p "mariadb" (binds))))
                      (format nil "~a ~a" (classify plan)
                              (if (hybrid-plan-sql-statement plan) (as-statement (hybrid-plan-sql-statement plan)) "")))
        (sql-error (e) (format nil "SQL-ERROR ~a ~a:~a" (sql-error-code e) (sql-error-line e) (sql-error-col e)))
        (error (e) (format nil "THROW ~a: ~a" (type-of e) e))))))
(with-open-file (in (second (member "--end-toplevel-options" sb-ext:*posix-argv* :test #'string=)))
  (loop for line = (read-line in nil) while line
        unless (zerop (length line)) do (probe line)))
```

**`$S/probe.mjs`**

```js
import fs from 'node:fs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding, SqlError } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const binds = () => ({
  ITEMS: Binding.relation('items', null, { DEPT: Binding.column('dept', null, 'TEXT'), AMOUNT: Binding.column('amount', null, 'NUM') }),
  DEPTS: Binding.relation('depts', null, { NAME: Binding.column('name', null, 'TEXT'), ID: Binding.column('id', null, 'NUM') }),
});
const fmt = (e) => e instanceof SqlError ? `${e.code} ${e.line}:${e.col}` : `THROW ${e.constructor.name}: ${e.message}`;
for (const src of fs.readFileSync(process.argv[2], 'utf8').split('\n').filter(Boolean)) {
  console.log('== ' + src);
  const p = compile(src);
  let t; try { t = Sql.translateStatement(p, 'mariadb', binds()).asStatement(); } catch (e) { t = fmt(e); }
  console.log('  translate: ' + t);
  let pl; try { const plan = Sql.planHybrid(p, 'mariadb', binds()); pl = (plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid') + ' ' + (plan.sqlStatement ? plan.sqlStatement.asStatement() : ''); } catch (e) { pl = fmt(e); }
  console.log('  plan:      ' + pl);
}
```

**`$S/probe.py`**

```python
import sys
from sel import compile
from sel.sql import Sql, Binding
from sel.sql.errors import SqlError
def binds():
    return {
      'ITEMS': Binding.relation('items', None, {'DEPT': Binding.column('dept', None, 'TEXT'), 'AMOUNT': Binding.column('amount', None, 'NUM')}),
      'DEPTS': Binding.relation('depts', None, {'NAME': Binding.column('name', None, 'TEXT'), 'ID': Binding.column('id', None, 'NUM')}),
    }
def fmt(e):
    return f'{e.code} {e.line}:{e.col}' if isinstance(e, SqlError) else f'THROW {type(e).__name__}: {e}'
for src in open(sys.argv[1]).read().split('\n'):
    if not src: continue
    print('== ' + src)
    p = compile(src)
    try: t = Sql.translate_statement(p, 'mariadb', binds()).as_statement()
    except Exception as e: t = fmt(e)
    print('  translate: ' + t)
    try:
        plan = Sql.plan_hybrid(p, 'mariadb', binds())
        pl = ('pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid') + ' ' + (plan.sql_statement.as_statement() if plan.sql_statement else '')
    except Exception as e: pl = fmt(e)
    print('  plan:      ' + pl)
```

**`$S/probe.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding; use Sel\Sql\SqlError;
function binds() { return [
  'ITEMS' => Binding::relation('items', null, ['DEPT' => Binding::column('dept', null, 'TEXT'), 'AMOUNT' => Binding::column('amount', null, 'NUM')]),
  'DEPTS' => Binding::relation('depts', null, ['NAME' => Binding::column('name', null, 'TEXT'), 'ID' => Binding::column('id', null, 'NUM')]),
]; }
function fmt($e) { return $e instanceof SqlError ? "{$e->code} {$e->line}:{$e->col}" : 'THROW ' . get_class($e) . ': ' . $e->getMessage(); }
foreach (array_filter(explode("\n", file_get_contents($argv[1]))) as $src) {
  echo "== $src\n";
  $p = Sel::compile($src);
  try { $t = Sql::translateStatement($p, 'mariadb', binds())->asStatement(); } catch (\Throwable $e) { $t = fmt($e); }
  echo "  translate: $t\n";
  try { $plan = Sql::planHybrid($p, 'mariadb', binds()); $pl = ($plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid')) . ' ' . ($plan->sqlStatement ? $plan->sqlStatement->asStatement() : ''); } catch (\Throwable $e) { $pl = fmt($e); }
  echo "  plan:      $pl\n";
}
```

**`$S/probe.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <fstream>
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind; using sel::sql::SqlError;
static Bindings binds() {
  return Bindings({
    {"ITEMS", Binding::relation("items", std::nullopt, {{"DEPT", Binding::column("dept", std::nullopt, SqlKind::Text)}, {"AMOUNT", Binding::column("amount", std::nullopt, SqlKind::Num)}})},
    {"DEPTS", Binding::relation("depts", std::nullopt, {{"NAME", Binding::column("name", std::nullopt, SqlKind::Text)}, {"ID", Binding::column("id", std::nullopt, SqlKind::Num)}})}});
}
int main(int argc, char** argv) {
  std::ifstream in(argv[1]); std::string src;
  while (std::getline(in, src)) { if (src.empty()) continue;
    std::cout << "== " << src << "\n";
    const sel::Program p = sel::compile(src);
    std::string t;
    try { t = Sql::translate_statement(p, "mariadb", binds()).as_statement(); }
    catch (const SqlError& e) { t = e.code() + " " + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
    catch (const std::exception& e) { t = std::string("THROW ") + e.what(); }
    std::cout << "  translate: " << t << "\n";
    std::string pl;
    try { auto plan = Sql::plan_hybrid(p, "mariadb", binds());
      pl = std::string(plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid") + " " + (plan.sql_statement ? plan.sql_statement->as_statement() : ""); }
    catch (const SqlError& e) { pl = e.code() + " " + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
    catch (const std::exception& e) { pl = std::string("THROW ") + e.what(); }
    std::cout << "  plan:      " << pl << "\n";
  }
}
```

**`$S/programs2.txt`**

```
ITEMS .> MAP(_["dept"]) .> LINK(DEPTS, i, d, i == d["name"])
ITEMS .> MAP(_["dept"]) .> SELECT_COLS("dept")
ITEMS .> MAP(_["dept"]) .> DISTINCT() .> TAKE(1) .> FILTER(_ > 1)
ITEMS .> MAP(UPPER(_["dept"])) .> LINK(DEPTS, i, d, i == d["name"])
```

**`$S/programs4.txt`**

```
ITEMS .> LINK(DEPTS, i, d, i["dept"] == d["name"])
ITEMS .> TAKE(5) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
ITEMS .> MAP(RECORD("dept", _["dept"])) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
```

**`$S/programs5.txt`**

```
ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
```

### Commands run and their output

Check Lisp binding constructor signatures and exports

```bash
mkdir -p $SCRATCH/verify-F && cd /home/nathan/workspaces/nth-share/sel && grep -n "defun binding-relation\|defun binding-column" -A 6 lisp/src/sql/*.lisp | head -30; grep -n "export" -A 40 lisp/src/sql/package.lisp | grep -i "hybrid\|translate\|as-statement\|sql-error" | head
```

```
lisp/src/sql/binding.lisp:89:(defun binding-column (column &optional table (type :unknown) &key exact sargable guard collation prefilter split-sargable)
lisp/src/sql/binding.lisp-90-  "One column, optionally qualified by a table, optionally typed.
lisp/src/sql/binding.lisp-91-
lisp/src/sql/binding.lisp-92-TYPE is what the kind guards read, and leaving it :UNKNOWN is honest rather than
lisp/src/sql/binding.lisp-93-free. An UNKNOWN operand still passes the guards that only rule kinds out --
lisp/src/sql/binding.lisp-94-REQUIRE-NOT-BOOL, REQUIRE-NOT-BOOL-OPERAND, REQUIRE-NUM and EQL-CLASS -- and
lisp/src/sql/binding.lisp-95-GUARD-NUMERIC wraps it where a number is read. It is REFUSED wherever a BOOL is
--
lisp/src/sql/binding.lisp:143:(defun binding-columns (&rest items)
lisp/src/sql/binding.lisp-144-  "An ordered set of columns, iterated by an aggregate and indexed by position:
lisp/src/sql/binding.lisp-145-the first is V[1]."
lisp/src/sql/binding.lisp-146-  (unless items
lisp/src/sql/binding.lisp-147-    (refuse "E_SQL_BINDING" "a columns binding needs at least one column"))
lisp/src/sql/binding.lisp-148-  (let ((out '()))
lisp/src/sql/binding.lisp-149-    (loop for item in items
--
lisp/src/sql/binding.lisp:183:(defun binding-relation (from &optional alias fields scalar correlate &key prefilter split-sargable)
lisp/src/sql/binding.lisp-184-  "A set of rows, rendered as a correlated subquery.
lisp/src/sql/binding.lisp-185-
lisp/src/sql/binding.lisp-186-FIELDS maps a SEL key to a column binding, as an ALIST -- the order is the
lisp/src/sql/binding.lisp-187-order they were given. SCALAR names the field a bare reference means, and only
lisp/src/sql/binding.lisp-188-a ONE-field relation may declare it: a wider row is a map in SEL, and a map is
lisp/src/sql/binding.lisp-189-not the value of one of its fields. CORRELATE is SQL, like RAW, and joins the
--
lisp/src/sql/binding.lisp:195:(defun binding-relation-query (query &optional alias fields scalar correlate &key prefilter split-sargable)
lisp/src/sql/binding.lisp-196-  "The same, over a query the application writes rather than a table."
lisp/src/sql/binding.lisp-197-  (check-string "a relation query" query)
lisp/src/sql/binding.lisp-198-  (when (zerop (length query))
lisp/src/sql/binding.lisp-199-    (refuse "E_SQL_BINDING" "a relation query cannot be empty"))
lisp/src/sql/binding.lisp-200-  (when alias (check-name "alias" alias))
16-   #:sql-error
17-   #:sql-error-code
18-   #:sql-error-message
19-   #:sql-error-line
20-   #:sql-error-col
21-   #:sql-error-offset
39-   #:as-statement
```

Run Lisp probe over bucket shapes via translate-statement and plan-hybrid

```bash
S=$SCRATCH/verify-F && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --end-toplevel-options $S/programs.txt 2>&1 | grep -v "^;"
```

```
WARNING: redefining SEL.SQL::CLASSIFY in DEFUN
While evaluating the form starting at line 28, column 0
  of #P"$SCRATCH/verify-F/probe.lisp":
Unhandled TYPE-ERROR in thread #<SB-THREAD:THREAD tid=751762 "main thread" RUNNING
                                  {1204030083}>:
  The value
    NIL
  is not of type
    (OR STRING PATHNAME SYNONYM-STREAM FILE-STREAM)
  when binding SB-KERNEL:FILENAME

Backtrace for: #<SB-THREAD:THREAD tid=751762 "main thread" RUNNING {1204030083}>
0: (OPEN NIL) [external]
1: ("top level form") [toplevel]
2: ((FLET "G" :IN SB-C::%COMPILE-IN-LEXENV))
3: (SB-C::%COMPILE-IN-LEXENV (LET ((IN (OPEN (SECOND #) :AUTO-CLOSE . #1=(NIL))) (#2=#:G588 T)) (UNWIND-PROTECT (MULTIPLE-VALUE-PROG1 (PROGN (LOOP FOR LINE = # WHILE LINE UNLESS # DO #)) (SETQ #2# . #1#)) (WHEN IN (CLOSE IN :ABORT #2#)))) #<NULL-LEXENV> NIL #<SB-C::SOURCE-INFO {1201E77633}> 5 NIL T T)
4: (SB-C:EVAL-WITH-COMPILE-IN-LEXENV (LET ((IN (OPEN (SECOND #) :AUTO-CLOSE . #1=(NIL))) (#2=#:G588 T)) (UNWIND-PROTECT (MULTIPLE-VALUE-PROG1 (PROGN (LOOP FOR LINE = # WHILE LINE UNLESS # DO #)) (SETQ #2# . #1#)) (WHEN IN (CLOSE IN :ABORT #2#)))) #<NULL-LEXENV> #<SB-C::SOURCE-INFO {1201E77633}> 5 NIL)
5: (SB-IMPL::%SIMPLE-EVAL (LET ((IN (OPEN (SECOND #) :AUTO-CLOSE . #1=(NIL))) (#2=#:G588 T)) (UNWIND-PROTECT (MULTIPLE-VALUE-PROG1 (PROGN (LOOP FOR LINE = # WHILE LINE UNLESS # DO #)) (SETQ #2# . #1#)) (WHEN IN (CLOSE IN :ABORT #2#)))) #<NULL-LEXENV>)
6: (SB-INT:SIMPLE-EVAL-IN-LEXENV (WITH-OPEN-FILE (IN (SECOND (MEMBER "--end-toplevel-options" SB-EXT:*POSIX-ARGV* :TEST (FUNCTION STRING=)))) (LOOP FOR LINE = (READ-LINE IN NIL) WHILE LINE UNLESS (ZEROP (LENGTH LINE)) DO (PROBE LINE))) #<NULL-LEXENV>)
7: (SB-EXT:EVAL-TLF (WITH-OPEN-FILE (IN (SECOND (MEMBER "--end-toplevel-options" SB-EXT:*POSIX-ARGV* :TEST (FUNCTION STRING=)))) (LOOP FOR LINE = (READ-LINE IN NIL) WHILE LINE UNLESS (ZEROP (LENGTH LINE)) DO (PROBE LINE))) 5 NIL)
8: ((LABELS SB-FASL::EVAL-FORM :IN SB-INT:LOAD-AS-SOURCE) (WITH-OPEN-FILE (IN (SECOND (MEMBER "--end-toplevel-options" SB-EXT:*POSIX-ARGV* :TEST (FUNCTION STRING=)))) (LOOP FOR LINE = (READ-LINE IN NIL) WHILE LINE UNLESS (ZEROP (LENGTH LINE)) DO (PROBE LINE))) 5)
9: ((LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) (WITH-OPEN-FILE (IN (SECOND (MEMBER "--end-toplevel-options" SB-EXT:*POSIX-ARGV* :TEST (FUNCTION STRING=)))) (LOOP FOR LINE = (READ-LINE IN NIL) WHILE LINE UNLESS (ZEROP (LENGTH LINE)) DO (PROBE LINE))) :CURRENT-INDEX 5)
10: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {71C579AA74CB}> #<SB-C::SOURCE-INFO {1201E77633}> SB-C::INPUT-ERROR-IN-LOAD)
11: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-F/probe.lisp" {1201ECF393}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
12: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-F/probe.lisp" {1201ECF393}> NIL)
13: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {71C579AA788B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-F/probe.lisp" {1201ECF393}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-F/probe.lisp" {1201ECF393}>)
14: (LOAD #P"$SCRATCH/verify-F/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
15: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-F/p
… (truncated)
```

Rerun Lisp probe with fixed argv handling

```bash
S=$SCRATCH/verify-F && sed -i 's|(with-open-file (in (second (member "--end-toplevel-options" sb-ext:\*posix-argv\* :test #.string=)))|(with-open-file (in (car (last sb-ext:*posix-argv*)))|' $S/probe.lisp && sed -i 's/defun classify/defun my-classify/; s/(classify plan)/(my-classify plan)/' $S/probe.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --end-toplevel-options $S/programs.txt 2>&1 | grep -v "^;"
```

```
== ITEMS .> BUCKET(_["dept"]) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"]) .> LINK_LEFT(DEPTS, i, d, i["dept"] == d["name"])
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"]) .> BUCKET(_K)
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept")
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept") .> MAP(RECORD("d", _K))
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"]) .> TAKE(2) .> FILTER(COUNT(_) > 1)
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`
== ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(g, g["dept"]) .> MAP(RECORD("d", _K, "n", COUNT(_)))
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"], _["amount"]) .> MAP(RECORD("x", _["amount"]))
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"]) .> MAP(g, RECORD("dept", _K, "total", SUM(g, x, x["amount"])))
  translate: SELECT `dept` AS `dept`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `dept`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` GROUP BY `dept`
== ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "cnt", COUNT(_)))
  translate: SELECT `dept` AS `dept`, COUNT(*) AS `cnt` FROM `items` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `dept`, COUNT(*) AS `cnt` FROM `items` GROUP BY `dept`
== ITEMS .> BUCKET(_["dept"]) .> TAKE(2) .> MAP(RECORD("dept", _K))
  translate: E_SQL_SHAPE 1:42
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"]) .> SORT_BY(_K) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"], _["amount"]) .> LINK(DEPTS, i, d, i["x"] == d["name"])
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTI
… (truncated)
```

Run JS probe over the same programs

```bash
S=$SCRATCH/verify-F && # (file written, see "Reproduction scripts" above)
node $S/probe.mjs $S/programs.txt
```

```
== ITEMS .> BUCKET(_["dept"]) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
  translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
  plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
== ITEMS .> BUCKET(_["dept"]) .> LINK_LEFT(DEPTS, i, d, i["dept"] == d["name"])
  translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
  plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
== ITEMS .> BUCKET(_["dept"]) .> BUCKET(_K)
  translate: E_SQL_DEPTH 1:38
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept")
  translate: SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1`
  plan:      pure_sql SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1`
== ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept") .> MAP(RECORD("d", _K))
  translate: E_SQL_SHAPE 1:54
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"]) .> TAKE(2) .> FILTER(COUNT(_) > 1)
  translate: SELECT `dept` FROM `items` GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2
  plan:      pure_sql SELECT `dept` FROM `items` GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2
== ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`
== ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: E_SQL_SHAPE 1:53
  plan:      pure_memory 
== ITEMS .> BUCKET(g, g["dept"]) .> MAP(RECORD("d", _K, "n", COUNT(_)))
  translate: E_SQL_SHAPE 1:50
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"], _["amount"]) .> MAP(RECORD("x", _["amount"]))
  translate: SELECT `_sub1`.`amount` AS `x` FROM (SELECT `amount` FROM `items` GROUP BY `dept`) `_sub1`
  plan:      pure_sql SELECT `_sub1`.`amount` AS `x` FROM (SELECT `amount` FROM `items` GROUP BY `dept`) `_sub1`
== ITEMS .> BUCKET(_["dept"]) .> MAP(g, RECORD("dept", _K, "total", SUM(g, x, x["amount"])))
  translate: SELECT `dept` AS `dept`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `dept`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` GROUP BY `dept`
== ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "cnt", COUNT(_)))
  translate: SE
… (truncated)
```

Run Python probe over the same programs

```bash
grep -n "def translate_statement\|def plan_hybrid\|def as_statement" python/sel/sql/__init__.py python/sel/sql/fragment.py | head; S=$SCRATCH/verify-F && # (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $S/probe.py $S/programs.txt
```

```
python/sel/sql/fragment.py:64:    def as_statement(self, mode: str = 'inline') -> str:
python/sel/sql/__init__.py:65:    def translate_statement(program, dialect: str, bindings: dict[str, Any] | None = None,
python/sel/sql/__init__.py:82:    def plan_hybrid(program, dialect: str,
== ITEMS .> BUCKET(_["dept"]) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
  translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
  plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
== ITEMS .> BUCKET(_["dept"]) .> LINK_LEFT(DEPTS, i, d, i["dept"] == d["name"])
  translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
  plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
== ITEMS .> BUCKET(_["dept"]) .> BUCKET(_K)
  translate: E_SQL_DEPTH 1:38
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept")
  translate: SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1`
  plan:      pure_sql SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1`
== ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept") .> MAP(RECORD("d", _K))
  translate: E_SQL_SHAPE 1:54
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"]) .> TAKE(2) .> FILTER(COUNT(_) > 1)
  translate: SELECT `dept` FROM `items` GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2
  plan:      pure_sql SELECT `dept` FROM `items` GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2
== ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`
== ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: E_SQL_SHAPE 1:53
  plan:      pure_memory 
== ITEMS .> BUCKET(g, g["dept"]) .> MAP(RECORD("d", _K, "n", COUNT(_)))
  translate: E_SQL_SHAPE 1:50
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"], _["amount"]) .> MAP(RECORD("x", _["amount"]))
  translate: SELECT `_sub1`.`amount` AS `x` FROM (SELECT `amount` FROM `items` GROUP BY `dept`) `_sub1`
  plan:      pure_sql SELECT `_sub1`.`amount` AS `x` FROM (SELECT `amount` FROM `items` GROUP BY `dept`) `_sub1`
== ITEMS .> BUCKET(_["dept"]) .> MAP(g, RECORD("dept", _K, "total", SUM(g, x, x["amount"])))
  translate: SELECT `dept` 
… (truncated)
```

Run PHP probe over the same programs

```bash
S=$SCRATCH/verify-F && # (file written, see "Reproduction scripts" above)
php $S/probe.php $S/programs.txt
```

```
== ITEMS .> BUCKET(_["dept"]) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
  translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
  plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
== ITEMS .> BUCKET(_["dept"]) .> LINK_LEFT(DEPTS, i, d, i["dept"] == d["name"])
  translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
  plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
== ITEMS .> BUCKET(_["dept"]) .> BUCKET(_K)
  translate: E_SQL_DEPTH 1:38
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept")
  translate: SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1`
  plan:      pure_sql SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1`
== ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept") .> MAP(RECORD("d", _K))
  translate: E_SQL_SHAPE 1:54
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"]) .> TAKE(2) .> FILTER(COUNT(_) > 1)
  translate: SELECT `dept` FROM `items` GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2
  plan:      pure_sql SELECT `dept` FROM `items` GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2
== ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`
== ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: E_SQL_SHAPE 1:53
  plan:      pure_memory 
== ITEMS .> BUCKET(g, g["dept"]) .> MAP(RECORD("d", _K, "n", COUNT(_)))
  translate: E_SQL_SHAPE 1:50
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"], _["amount"]) .> MAP(RECORD("x", _["amount"]))
  translate: SELECT `_sub1`.`amount` AS `x` FROM (SELECT `amount` FROM `items` GROUP BY `dept`) `_sub1`
  plan:      pure_sql SELECT `_sub1`.`amount` AS `x` FROM (SELECT `amount` FROM `items` GROUP BY `dept`) `_sub1`
== ITEMS .> BUCKET(_["dept"]) .> MAP(g, RECORD("dept", _K, "total", SUM(g, x, x["amount"])))
  translate: SELECT `dept` AS `dept`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `dept`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` GROUP BY `dept`
== ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "cnt", COUNT(_)))
  translate: SE
… (truncated)
```

Build and run C++ probe over the same programs

```bash
S=$SCRATCH/verify-F && # (file written, see "Reproduction scripts" above)
cd cpp && g++ -std=c++23 -O1 -o $S/probe_cpp $S/probe.cpp build/sel_sql*.o build/sel.o 2>&1 | tail -5 && $S/probe_cpp $S/programs.txt
```

```
== ITEMS .> BUCKET(_["dept"]) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
  translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
  plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
== ITEMS .> BUCKET(_["dept"]) .> LINK_LEFT(DEPTS, i, d, i["dept"] == d["name"])
  translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
  plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
== ITEMS .> BUCKET(_["dept"]) .> BUCKET(_K)
  translate: E_SQL_DEPTH 1:38
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept")
  translate: SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1`
  plan:      pure_sql SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1`
== ITEMS .> BUCKET(_["dept"]) .> SELECT_COLS("dept") .> MAP(RECORD("d", _K))
  translate: E_SQL_SHAPE 1:54
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"]) .> TAKE(2) .> FILTER(COUNT(_) > 1)
  translate: SELECT `dept` FROM `items` GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2
  plan:      pure_sql SELECT `dept` FROM `items` GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2
== ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` GROUP BY `dept`
== ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: E_SQL_SHAPE 1:53
  plan:      pure_memory 
== ITEMS .> BUCKET(g, g["dept"]) .> MAP(RECORD("d", _K, "n", COUNT(_)))
  translate: E_SQL_SHAPE 1:50
  plan:      pure_memory 
== ITEMS .> BUCKET(_["dept"], _["amount"]) .> MAP(RECORD("x", _["amount"]))
  translate: SELECT `_sub1`.`amount` AS `x` FROM (SELECT `amount` FROM `items` GROUP BY `dept`) `_sub1`
  plan:      pure_sql SELECT `_sub1`.`amount` AS `x` FROM (SELECT `amount` FROM `items` GROUP BY `dept`) `_sub1`
== ITEMS .> BUCKET(_["dept"]) .> MAP(g, RECORD("dept", _K, "total", SUM(g, x, x["amount"])))
  translate: SELECT `dept` AS `dept`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `dept`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` GROUP BY `dept`
== ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "cnt", COUNT(_)))
  translate: SE
… (truncated)
```

Read old bucket/MAP handling to see whether nil-alias projection could be wrapped pre-commit

```bash
O=$SCRATCH/verify-F/old-translator.lisp; sed -n 1930,1975p $O; echo ======; sed -n 2070,2085p $O
```

```
(setf (relational-plan-group-by plan) (nreverse group-by)))

                   (if agg-node
                       (if (and (not (clist-p agg-node)) (eq (snode-kind agg-node) :call) (equal (sel::node-s agg-node) "RECORD"))
                           (let ((rec-args (sel::node-items agg-node))
                                 (projs '()))
                             (unless (evenp (length rec-args))
                               (refuse "E_ARITY" "RECORD takes an even number of arguments" (snode-pos agg-node)))
                             (loop for (k-node v-node) on rec-args by #'cddr do
                               (unless (eq (snode-kind k-node) :text)
                                 (refuse "E_BAD_ARG" "RECORD field names must be string literals" (snode-pos k-node)))
                               (let* ((alias (sel::node-s k-node))
                                      (actual-node (if (and (not (clist-p v-node))
                                                            (eq (snode-kind v-node) :var)
                                                            (equal (sel::node-s v-node) "_K")
                                                            (= (length (relational-plan-group-by plan)) 1))
                                                       (third (first (relational-plan-group-by plan)))
                                                       v-node))
                                      (is-same-field (and (not (clist-p actual-node))
                                                          (eq (snode-kind actual-node) :index)
                                                          (let ((l (sel::node-l actual-node))
                                                                (r (sel::node-r actual-node)))
                                                            (and l (not (clist-p l)) (eq (snode-kind l) :var)
                                                                 (or (equal (sel::node-s l) "_") (equal (sel::node-s l) binder))
                                                                 r (not (clist-p r)) (eq (snode-kind r) :text)
                                                                 (string-equal (sel::node-s r) alias))))))
                                 (unless is-same-field
                                   (push (cons alias actual-node) (relational-plan-aggregate-aliases plan)))
                                 (push (list alias binder actual-node) projs)))
                             (setf (relational-plan-projections plan) (nreverse projs)))
                           (setf (relational-plan-projections plan) (list (list nil binder agg-node))))
                       (let ((projs '()))
                         (dolist (gb (relational-plan-group-by plan))
                           (push (list (first gb) (second gb) (third gb)) projs))
                         (setf (relational-plan-projections plan) (nreverse projs))))
                   (setf (relational-plan-select-cols plan) nil)))

                ((or (equal sname "LINK") (equal sname "LINK_LEFT"))
                 (when (or (relational-plan-group-by plan)
                           (relational-plan-projections plan)
                           (relational-plan-select-cols plan)
                           (relational-plan-limit plan)
                           (relational-plan-offset plan)
                           (relational-plan-distinct plan))
                   (setf plan (wrap-plan-as-derived-table plan)))

… (truncated)
```

Probe non-bucket nil-alias projection shapes in JS and Lisp

```bash
S=$SCRATCH/verify-F && # (file written, see "Reproduction scripts" above)
echo "### JS"; node $S/probe.mjs $S/programs2.txt; echo "### LISP"; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --end-toplevel-options $S/programs2.txt 2>&1 | grep -v "^;\|WARNING"
```

```
### JS
== ITEMS .> MAP(_["dept"]) .> LINK(DEPTS, i, d, i == d["name"])
  translate: E_SQL_SHAPE 1:46
  plan:      hybrid SELECT `dept` FROM `items`
== ITEMS .> MAP(_["dept"]) .> SELECT_COLS("dept")
  translate: SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items`) `_sub1`
  plan:      pure_sql SELECT `_sub1`.`dept` FROM (SELECT `dept` FROM `items`) `_sub1`
== ITEMS .> MAP(_["dept"]) .> DISTINCT() .> TAKE(1) .> FILTER(_ > 1)
  translate: E_SQL_SHAPE 1:60
  plan:      hybrid SELECT DISTINCT `dept` FROM `items` LIMIT 1
== ITEMS .> MAP(UPPER(_["dept"])) .> LINK(DEPTS, i, d, i == d["name"])
  translate: E_SQL_SHAPE 1:53
  plan:      hybrid SELECT UPPER(`dept`) FROM `items`
### LISP
== ITEMS .> MAP(_["dept"]) .> LINK(DEPTS, i, d, i == d["name"])
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> MAP(_["dept"]) .> SELECT_COLS("dept")
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> MAP(_["dept"]) .> DISTINCT() .> TAKE(1) .> FILTER(_ > 1)
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> MAP(UPPER(_["dept"])) .> LINK(DEPTS, i, d, i == d["name"])
  translate: E_SQL_SHAPE 1:53
  plan:      hybrid SELECT UPPER(`dept`) FROM `items`
```

Run the same probe against the parent commit's Lisp tree extracted to scratchpad

```bash
S=$SCRATCH/verify-F && mkdir -p $S/old && git archive 8fe0e3a lisp | tar -x -C $S/old && ls $S/old/lisp | head; head -3 $S/programs.txt > $S/programs3.txt; sed -n 10,11p $S/programs.txt >> $S/programs3.txt; cat $S/programs2.txt >> $S/programs3.txt; sbcl --noinform --disable-debugger --non-interactive --load $S/old/lisp/bin/boot.lisp --load $S/probe.lisp --end-toplevel-options $S/programs3.txt 2>&1 | grep -v "^;\|WARNING"
```

```
bin
sel-lang.asd
src
tests
== ITEMS .> BUCKET(_["dept"]) .> LINK(DEPTS, i, d, i["dept"] == d["name"])
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"]) .> LINK_LEFT(DEPTS, i, d, i["dept"] == d["name"])
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"]) .> BUCKET(_K)
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"], _["amount"]) .> MAP(RECORD("x", _["amount"]))
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> BUCKET(_["dept"]) .> MAP(g, RECORD("dept", _K, "total", SUM(g, x, x["amount"])))
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> MAP(_["dept"]) .> LINK(DEPTS, i, d, i == d["name"])
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> MAP(_["dept"]) .> SELECT_COLS("dept")
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> MAP(_["dept"]) .> DISTINCT() .> TAKE(1) .> FILTER(_ > 1)
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
== ITEMS .> MAP(UPPER(_["dept"])) .> LINK(DEPTS, i, d, i == d["name"])
  translate: E_SQL_SHAPE 1:53
  plan:      hybrid SELECT UPPER(`dept`) FROM `items`
```

Verify that defining snode-r at runtime makes Lisp output match JS byte-for-byte

```bash
S=$SCRATCH/verify-F && { echo '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))'; echo '(defun sel.sql::snode-r (n) (if (sel.sql::clist-p n) nil (sel::node-r n)))'; cat $S/probe.lisp; } > $S/probe-patched.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe-patched.lisp --end-toplevel-options $S/programs.txt 2>&1 | grep -v "^;\|WARNING" > $S/lisp-patched.out; node $S/probe.mjs $S/programs.txt > $S/js.out; diff $S/js.out $S/lisp-patched.out && echo "IDENTICAL to JS after defining snode-r"
```

```
Exit code 1
2,3c2,3
<   translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
<   plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
---
>   translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`name` AS DECIMAL(65,10)) ELSE NULL END)
>   plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`name` AS DECIMAL(65,10)) ELSE NULL END)
5,6c5,6
<   translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
<   plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
---
>   translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`name` AS DECIMAL(65,10)) ELSE NULL END)
>   plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept`) `_sub1` LEFT JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`name` AS DECIMAL(65,10)) ELSE NULL END)
17,18c17,18
<   translate: SELECT `dept` FROM `items` GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2
<   plan:      pure_sql SELECT `dept` FROM `items` GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2
---
>   translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept` LIMIT 2) `_sub1` WHERE (COUNT(*) > 1)
>   plan:      pure_sql SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept` LIMIT 2) `_sub1` WHERE (COUNT(*) > 1)
41,42c41,42
<   translate: SELECT `_sub1`.* FROM (SELECT `dept` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\
… (truncated)
```

Check whether the join-column qualification divergence exists independent of the crash

```bash
S=$SCRATCH/verify-F && # (file written, see "Reproduction scripts" above)
echo "### JS"; node $S/probe.mjs $S/programs4.txt | grep translate; echo "### LISP(HEAD, unpatched)"; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --end-toplevel-options $S/programs4.txt 2>&1 | grep translate
```

```
### JS
  translate: SELECT * FROM `items` INNER JOIN `depts` `D` ON (CASE WHEN (`items`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`items`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
  translate: SELECT `_sub1`.* FROM (SELECT * FROM `items` LIMIT 5) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
  translate: SELECT `_sub1`.* FROM (SELECT `dept` AS `dept` FROM `items`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`D`.`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`D`.`name` AS DECIMAL(65,10)) ELSE NULL END)
### LISP(HEAD, unpatched)
  translate: SELECT * FROM `items` INNER JOIN `depts` `D` ON (CASE WHEN (`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`name` AS DECIMAL(65,10)) ELSE NULL END)
  translate: SELECT `_sub1`.* FROM (SELECT * FROM `items` LIMIT 5) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`name` AS DECIMAL(65,10)) ELSE NULL END)
  translate: SELECT `_sub1`.* FROM (SELECT `dept` AS `dept` FROM `items`) `_sub1` INNER JOIN `depts` `D` ON (CASE WHEN (`_sub1`.`dept` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`dept` AS DECIMAL(65,10)) ELSE NULL END = CASE WHEN (`name` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`name` AS DECIMAL(65,10)) ELSE NULL END)
```

Retest the explicit-binder MAP claim with an aliased relation binding

```bash
S=$SCRATCH/verify-F && sed 's/(binding-relation "items" nil/(binding-relation "items" "o"/' $S/probe.lisp > $S/probe-alias.lisp && sed "s/Binding.relation('items', null/Binding.relation('items', 'o'/" $S/probe.mjs > $S/probe-alias.mjs && # (file written, see "Reproduction scripts" above)
echo "### JS aliased"; node $S/probe-alias.mjs $S/programs5.txt; echo "### LISP aliased"; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe-alias.lisp --end-toplevel-options $S/programs5.txt 2>&1 | grep -v "^;\|WARNING"
```

```
### JS aliased
== ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` `o` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` `o` GROUP BY `dept`
== ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: E_SQL_SHAPE 1:53
  plan:      pure_memory 
### LISP aliased
== ITEMS .> BUCKET(_["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` `o` GROUP BY `dept`
  plan:      pure_sql SELECT `dept` AS `d`, COUNT(*) AS `n` FROM `items` `o` GROUP BY `dept`
== ITEMS .> BUCKET(O, O["dept"]) .> MAP(G, RECORD("d", _K, "n", COUNT(G)))
  translate: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan:      THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
```

Confirm the committed suite is green for Lisp and holds no wrapping-bucket fixture

```bash
lisp/bin/sqlt bucket plan 2>&1 | tail -3; grep -c "BUCKET" sql/cases/25-hybrid-plans.sqlt; grep -n "BUCKET.*\.> \(LINK\|SELECT_COLS\|BUCKET\|SORT\|TAKE\|DISTINCT\)" sql/cases/*.sqlt | head
```

```
51 passed (43 also checked against a mirrored dialect), 0 failed, 0 suite errors
5
sql/cases/24-bucket.sqlt:95:ITEMS .> BUCKET(_["dept"], RECORD("dept", _K, "total", SUM(_, _["amount"]))) .> SORT_BY(_["total"], "DESC")
sql/cases/24-bucket.sqlt:108:ITEMS .> BUCKET(_["dept"]) .> TAKE(10) .> DROP(5)
sql/cases/24-bucket.sqlt:121:ITEMS .> FILTER(_["active"] == 1) .> BUCKET(_["dept"], RECORD("dept", _K, "total", SUM(_, _["amount"]))) .> FILTER(_["total"] > 100) .> SORT_BY(_["total"], "DESC")
sql/cases/24-bucket.sqlt:254:ITEMS .> BUCKET(_["dept"]) .> TAKE(2) .> MAP(RECORD("dept", _K))
sql/cases/25-hybrid-plans.sqlt:597:ORDERS .> BUCKET(_["customer_id"]) .> TAKE(2) .> MAP(RECORD("cid", _K))
```

