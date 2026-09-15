# X. COUNT translation: COUNT(_) in a non-grouped MAP becomes COUNT(*); COUNT over a BUCKET/LIST/RECORD/DISTINCT/SORT_BY/TAKE result folds to literal 0

**Status:** PARTIALLY FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "The bucket body's scope is the evaluator's". First facet only (COUNT of a row is not COUNT(*)); the second facet, `COUNT(LIST(…))` and the other structure-yielding calls folding to 0 through source()'s scalar rule, stays open.

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

Both facets reproduce in all five hosts, byte-identically, so this is a lane divergence (run() vs translate_statement/plan_hybrid) rather than a host divergence. (1) In the statement lane every host special-cases COUNT(<row binder>) to COUNT(*) without checking that the plan is grouped, so `ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))` is a pure_sql plan that returns one row with n=3 where run() returns three rows with n=4, and `ORDERS .> FILTER(COUNT(_) > 2) .> …` is a pure_sql plan whose SQL (`WHERE (COUNT(*) > 2)`) the database rejects. (2) source()'s scalar fallback treats any call not in YIELDS_LIST as one value, so COUNT(LIST/RECORD/DISTINCT/TAKE/SORT_BY/BUCKET(...)) folds to literal 0 in both the expression lane and inside a MAP/bucket projection (pure_sql, answers 0 where run() answers 2/3/…); the same calls are refused everywhere else (value position, SUM/ALL source). Both mechanisms exist unchanged at 8fe0e3a; the commit's BUCKET-folding only changes which wrong plan the b67 shape gets (hybrid before, pure_sql-with-0 now).

## Suggested fix — case first

Two small changes, cases first. (1) Gate the statement-lane shortcut: in each host's call() COUNT/SUM branch (js translator.mjs:667, python:667, php:650, cpp:1578, lisp:1057) require `statementPlan.groupBy` to be non-null (and the binder to be the bucket's group binder); otherwise fall through to count(), whose existing multi-field-row refusal (E_SQL_SHAPE) makes plan_hybrid keep the MAP in memory. Add to sql/cases/25-hybrid-plans.sqlt: `ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))` --- plan hybrid (or `--- error E_SQL_SHAPE` for the statement case), and `ORDERS .> FILTER(COUNT(_) > 2) .> MAP(...)` likewise. (2) In source()'s final fallback, refuse any `call` that is not a scalar function in the dialect map (or extend YIELDS_LIST with LIST, RECORD, DISTINCT, DEDUPE, SORT, SORT_BY, TAKE, DROP, BUCKET, SELECT_COLS and the pipe/MAP/FILTER shapes) before applying the scalar rule; add `agg.count.structure-yielding-call-is-not-a-scalar` next to sql/cases/12-aggregates.sqlt:579 with `COUNT(LIST(1,2,3))` --- error E_SQL_SHAPE, and one bucket case `… MAP(RECORD("cid", _K, "n", COUNT(BUCKET(_, _["status"]))))` expecting refusal/hybrid, then add both shapes to the SQLite evaluator-comparison test in python/tests/test_unit.py.

## Verifier reasoning

Facet 1 code: js/src/sql/translator.mjs:667-673, python/sel/sql/translator.py:667-672, php/src/Sql/Translator.php:650, cpp/sel_sql_translator.cpp:1578-1588, lisp/src/sql/translator.lisp:1057-1066 — all test only `statement_plan != null` and `binder(arg0).shape == ROW`, never `groupBy`. Outside the statement lane the same input hits the multi-field-row refusal (js translator.mjs:1064-1071 "is a row of a multi-field relation … SQL has no way to iterate or count that"), which is the documented rule (docs/SQL-TRANSLATION.md:1178-1186); the statement branch bypasses it. Facet 2 code: count() at js:1330-1337 / php:1657-1665 / cpp:2080-2088 / lisp:1470-1478 / python (count) returns literal 0 when `src.scalarRule` is set; source()'s last fallback (js:1099-1105, python:1063, php:762-767, cpp:1712/1860, lisp:949/1274) only refuses the four names in YIELDS_LIST and marks every other call as a scalar. docs/SQL-TRANSLATION.md §7.6 says COUNT of a *scalar* is 0 "a scalar has no children"; LIST/RECORD/BUCKET/… are not scalars (they are refused as "a list is not a SQL value" in every other position), so the 0 is a misclassification, not a documented decision. The fixture agg.count.list-yielding-call-is-not-a-scalar (sql/cases/12-aggregates.sqlt:579) pins the identical failure mode for SPLIT only. The bucket "compare with the evaluator" test (python/tests/test_unit.py:510-517) does not include a nested COUNT(BUCKET) or an ungrouped COUNT(_). All five hosts emit identical SQL and identical plan classes, so it is not a cross-host disagreement; it is a lane disagreement that violates the pure_sql promise ("the database answers", docs/SQL-TRANSLATION.md §12.1) silently. Pre-existing: `git show 8fe0e3a:js/src/sql/translator.mjs` has the same COUNT(*) branch at 660-673 (added in cd06049) and the same YIELDS_LIST/fallback; running the 8fe0e3a Python tree from a scratch extraction gives the same pure_sql/collapsed and 0 answers for every case except b67, which was `hybrid` there (also wrong per the CHANGELOG) and is pure_sql-with-0 now. Severity high: silently different results between lanes for a shape rule authors will write, plus a pure_sql plan the DB refuses at execution.

## Verifier evidence

```
Probe scripts under /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-X/ (probe.py, probe.mjs, probe.php, probe.cpp, probe.lisp).

Python + SQLite (PYTHONPATH=$PWD/python python3 probe.py), orders = 3 rows (id, customer_id, status, amount):
  ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))
    run(): 3 rows, n=4 each; plan: pure_sql; SQL: SELECT "o"."id" AS "id", COUNT(*) AS "n" FROM "orders" "o"; exec: 1 row {id=1,n=3}  DIFFERENT
  ORDERS .> MAP(r, RECORD("id", r["id"], "n", COUNT(r)))  same (explicit binder)
  ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"]))
    run(): 3 rows; plan: pure_sql; SQL: … WHERE (CAST(COUNT(*) AS NUMERIC) > …); exec: sqlite3 "misuse of aggregate function COUNT()"
  CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"]))))
    run(): n=2,1,0; plan pure_sql; SQL: SELECT "c"."id" AS "id", '0' AS "n" FROM "customers" "c"; exec n=0,0,0  DIFFERENT
  ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"]))))
    run(): 2,1; plan pure_sql; SQL: … '0' AS "by_status" … GROUP BY …; exec 0,0  DIFFERENT
  COUNT(LIST(1,2,3)) / COUNT(RECORD("a",1,"b",2)) / COUNT(DISTINCT((1,1,2))) / COUNT(TAKE((1,2,3),2)) inside MAP: run() 3/2/2/2, SQL '0' AS "n", exec 0  DIFFERENT
  Expression lane Sql.translate(...,'postgresql',{}).as_value(): COUNT(LIST(1,2,3))=0 (run 3), COUNT(RECORD("a",1))=0 (run 1), COUNT((1,2,3))=3, COUNT(DISTINCT((1,1,2)))=0, COUNT(SORT_BY((1,2),_))=0, COUNT(TAKE((1,2,3),2))=0, COUNT(BUCKET((1,2),_))=0 (run 2 each).

JS (node probe.mjs), PHP (php probe.php), C++ (c++ -std=c++23 -I cpp probe.cpp cpp/build/sel_sql*.o cpp/build/sel.o), Lisp (sbcl --load lisp/bin/boot.lisp --eval '(ql:quickload :sel-lang/sql)' --load probe.lisp) — mariadb translate_statement, all four identical:
  MAP(... COUNT(_))            -> SELECT `o`.`id` AS `id`, COUNT(*) AS `n` FROM `orders` `o`; plan pure_sql
  FILTER(COUNT(_) > 2) .> MAP  -> SELECT `o`.`id` AS `id` FROM `orders` `o` WHERE (COUNT(*) > 2); plan pure_sql
  COUNT(BUCKET(FILTER(ORDERS…))) in MAP -> SELECT `c`.`id` AS `id`, 0 AS `n` FROM `customers` `c`; pure_sql
  BUCKET .> MAP(COUNT(BUCKET(_,…))) -> … 0 AS `by_status` … GROUP BY …; pure_sql
  COUNT(LIST(1,2,3)) / COUNT(TAKE(...)) in MAP -> 0 AS `n`; pure_sql
  expr: COUNT(LIST(1,2,3))=0, COUNT(RECORD("a",1))=0, COUNT((1,2,3))=3, COUNT(BUCKET((1,2),_))=0 in js, php, cpp, lisp.
  (C++ output shows unqualified `id` only because the probe passed std::nullopt as the column alias.)

Other positions refuse the same calls (JS, postgresql): LIST(1,2) => E_SQL_UNSUPPORTED "LIST has no mapping"; DISTINCT/TAKE/BUCKET/SORT_BY((…)) => E_SQL_SHAPE "a list is not a SQL value"; SUM(LIST(1,2), _) => E_SQL_UNSUPPORTED; ALL(RECORD("a",1), _ > 0) => E_SQL_UNSUPPORTED; only COUNT (0) and HAS (FALSE) fold.
sql/dialects/ansi.json funcs: LIST, RECORD, DISTINCT, DEDUPE, TAKE, DROP, SORT, SORT_BY, BUCKET absent; SPLIT/BTL/RGROUPS carry the "yields a list" refusal string.

Pre-existing check: `git show 8fe0e3a:js/src/sql/translator.mjs | sed -n 660,680p` shows the identical COUNT(*) branch; `git log -S'COUNT(*)' -- js/src/sql/translator.mjs` -> cd06049. `git archive 8fe0e3a python | tar -x` into scratchpad and rerunning probe.py: same pure_sql/collapsed and 0 answers for every case; b67 was `hybrid` (SQL = SELECT customer_id … GROUP BY) at 8fe0e3a and is pure_sql-with-0 at HEAD.

Code read: js/src/sql/translator.mjs:667-673 (COUNT(*) branch), :1064-1071 (multi-field row refusal bypassed by it), :1099-1105 (YIELDS_LIST fallback), :1330-1337 (count folds scalarRule to 0); python/sel/sql/translator.py:71, :667-672, :1063; php/src/Sql/Translator.php:650, :762-767, :1657-1665; cpp/sel_sql_translator.cpp:1578-1588, :1712, :1860, :2080-2088; lisp/src/sql/translator.lisp:949, :1057-1066, :1274, :1470-1478. docs/SQL-TRANSLATION.md:1178-1186 (bare `_` over a multi-field relation is refused), :1296-1299 (§7.6 COUNT table: 0 only for a *scalar*), :2227-2300 (§12.1 pure_sql = "the database answers"). python/tests/test_unit.py:510-517 (bucket evaluator-comparison shapes; none nests COUNT over a structure or uses ungrouped COUNT(_)).
```

## Original review reports (deduplicated into this finding)

### [hybrid-planner] COUNT(_) inside a non-grouped MAP is pushed down as COUNT(*), collapsing the statement to one row

*correctness · medium · hosts: js, php, python, cpp, lisp*

Locations: `js/src/sql/hybrid.mjs:139-141`; `js/src/sql/translator.mjs:1984`; `cpp/sel_sql_translator.cpp:2487`; `lisp/src/sql/translator.lisp:2076`

COUNT is in every host's special-call set, so `COUNT(_)` in a MAP projection is treated as translatable; the translators render it as COUNT(*) without a GROUP BY. In the evaluator `_` is the row record and COUNT(_) is its field count, one value per row; in SQL the aggregate reduces the whole table to one row. The plan is pure_sql in all five hosts, so the hybrid lane silently returns one row where run() returns one per source row.

Reported repro:

```
Python + SQLite: `ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_), "note", GET(_["name"], 1)))` -> plan hybrid, sql=SELECT "o"."id" AS "id", COUNT(*) AS "n", "o"."name" AS "name" FROM "orders" "o"; run() = three rows with n=3; execute_hybrid = one row. translate_statement (mariadb) of `ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))` gives SELECT `o`.`id` AS `id`, COUNT(*) AS `n` FROM `orders` `o` in js, cpp and lisp.
```

### [probe-lanes-bucket-hybrid] COUNT over a BUCKET (or LIST/RECORD/DISTINCT/SORT_BY/TAKE) result folds to literal 0 in every translator

*correctness · medium · hosts: js, python, php, cpp, lisp*

Locations: `js/src/sql/translator.mjs:1336`; `python/sel/sql/translator.py:1717`; `sql/cases/12-aggregates.sqlt:579`

count() asks source() for its argument and, when the argument is not a relation and the scalar rule applies, emits the literal 0. source() reaches that fallback for any call that yields a structure — BUCKET, LIST, RECORD, DISTINCT, DEDUPE, SORT_BY, TAKE, a nested pipeline — so COUNT(BUCKET(...)) inside a MAP body or a bucket projection is rendered as `0 AS n` and the plan is pure_sql, answering 0 where the evaluator counts the groups. The fixture agg.count.list-yielding-call-is-not-a-scalar pins exactly this failure mode for SPLIT/BTL/RGROUPS/INDEXES only. Pre-existing at 8fe0e3a; the diff's bucket work makes BUCKET inside a projection a shape rule authors will write, and the bucket plans' "answer what the evaluator answers" test does not include it.

Reported repro:

```
b44: CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"]))))  run() => n = 2, 1, 0; translate_statement (all hosts) => SELECT "c"."id" AS "id", '0' AS "n" FROM "customers" "c" (sqlite) / 0 AS `n` (mariadb); pure_sql; executed => n = 0, 0, 0.
b67: ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"]))))  run() => by_status 2,1,1; SQL => ... '0' AS "by_status" ... GROUP BY; executed => 0,0,0.
Expression lane: Sql.translate(compile('COUNT(LIST(1,2,3))'), 'postgresql', {}).as_value() => 0 (run() => 3); COUNT(RECORD("a", 1)) => 0 (run() => 1); COUNT((1,2,3)) => 3.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$SCRATCH/verify-X/probe.py`**

```python
import sqlite3, sys
from sel import compile as sel_compile
from sel.value import Value
from sel.sql import Binding, Sql

bindings = {
 'ORDERS': Binding.relation('orders', 'o', {
    'ID': Binding.column('id', 'o', 'NUM'),
    'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM'),
    'STATUS': Binding.column('status', 'o', 'TEXT'),
    'AMOUNT': Binding.column('amount', 'o', 'NUM')}),
 'CUSTOMERS': Binding.relation('customers', 'c', {
    'ID': Binding.column('id', 'c', 'NUM'),
    'NAME': Binding.column('name', 'c', 'TEXT')}),
}
orders = [{'id':'1','customer_id':'7','status':'A','amount':'10'},
          {'id':'2','customer_id':'7','status':'B','amount':'5'},
          {'id':'3','customer_id':'9','status':'A','amount':'7'}]
customers = [{'id':'7','name':'ann'},{'id':'9','name':'bob'},{'id':'11','name':'cy'}]
db = sqlite3.connect(':memory:')
db.execute('create table orders(id, customer_id, status, amount)')
db.executemany('insert into orders values (?,?,?,?)', [(r['id'],r['customer_id'],r['status'],r['amount']) for r in orders])
db.execute('create table customers(id, name)')
db.executemany('insert into customers values (?,?)', [(r['id'],r['name']) for r in customers])
def runner(sql, params):
    print('   SQL:', sql, [p.as_text() for p in params])
    cur = db.execute(sql, [p.as_text() for p in params])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]

progs = [
 # facet 1
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))',
 'ORDERS .> MAP(r, RECORD("id", r["id"], "n", COUNT(r)))',
 'ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"]))',
 # facet 2
 'CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"]))))',
 'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"]))))',
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3))))',
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(RECORD("a", 1, "b", 2))))',
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(DISTINCT((1,1,2)))))',
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2))))',
]
for src in progs:
    print('==', src)
    p = sel_compile(src)
    try:
        want = p.run({'ORDERS': orders, 'CUSTOMERS': customers}).dump()
    except Exception as e:
        want = 'ERR ' + getattr(e, 'code', str(e))
    print('   run():', want)
    try:
        plan = Sql.plan_hybrid(p, 'sqlite', bindings)
        kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
        print('   plan:', kind)
        got = Sql.execute_hybrid(plan, runner, {'ORDERS': orders, 'CUSTOMERS': customers})
        got = got if isinstance(got, Value) else Value.from_native(got)
        print('   exec:', got.dump(), '  <-- %s' % ('SAME' if got.dump()==want else 'DIFFERENT'))
    except Exception as e:
        print('   plan/exec ERR:', getattr(e,'code',None), e)
for src in ['COUNT(LIST(1,2,3))','COUNT(RECORD("a",1))','COUNT((1,2,3))','COUNT(DISTINCT((1,1,2)))','COUNT(SORT_BY((1,2), _))','COUNT(TAKE((1,2,3),2))','COUNT(BUCKET((1,2),_))']:
    p = sel_compile(src)
    try: run = p.run().dump()
    except Exception as e: run = 'ERR '+getattr(e,'code',str(e))
    try:
        f = Sql.translate(p, 'postgresql', {})
        t = f.as_value().dump() if hasattr(f,'as_value') else str(f)
    except Exception as e: t = 'ERR '+str(getattr(e,'code',e))
    print('expr', src, 'run=', run, 'translate=', t)
```

**`$SCRATCH/verify-X/expr.py`**

```python
from sel import compile as sel_compile
from sel.sql import Sql
for src in ['COUNT(LIST(1,2,3))','COUNT(RECORD("a",1))','COUNT((1,2,3))','COUNT(DISTINCT((1,1,2)))','COUNT(SORT_BY((1,2), _))','COUNT(TAKE((1,2,3),2))','COUNT(BUCKET((1,2),_))']:
    p = sel_compile(src)
    try: run = p.run().dump()
    except Exception as e: run = 'ERR '+getattr(e,'code',str(e))
    try:
        f = Sql.translate(p, 'postgresql', {}); t = f.as_value()
    except Exception as e: t = 'ERR '+str(getattr(e,'code',e))
    print('expr', src, 'run=', run, 'translate=', t)
```

**`$SCRATCH/verify-X/probe.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const bindings = {
 ORDERS: Binding.relation('orders','o',{ ID: Binding.column('id','o','NUM'), CUSTOMER_ID: Binding.column('customer_id','o','NUM'), STATUS: Binding.column('status','o','TEXT'), AMOUNT: Binding.column('amount','o','NUM')}),
 CUSTOMERS: Binding.relation('customers','c',{ ID: Binding.column('id','c','NUM'), NAME: Binding.column('name','c','TEXT')}),
};
const progs = [
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))',
 'ORDERS .> MAP(r, RECORD("id", r["id"], "n", COUNT(r)))',
 'ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"]))',
 'CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"]))))',
 'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"]))))',
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3))))',
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2))))',
];
const orders = [{id:'1',customer_id:'7',status:'A',amount:'10'},{id:'2',customer_id:'7',status:'B',amount:'5'},{id:'3',customer_id:'9',status:'A',amount:'7'}];
for (const src of progs) {
  const p = compile(src);
  let run; try { run = p.run({ORDERS: orders, CUSTOMERS: [{id:'7',name:'ann'},{id:'9',name:'bob'},{id:'11',name:'cy'}]}).dump(); } catch (e) { run = 'ERR ' + e.code; }
  let sql; try { const f = Sql.translateStatement(p, 'mariadb', bindings); sql = f.sql ?? f.toString(); } catch (e) { sql = 'ERR ' + e.code + ' ' + e.message; }
  let kind; try { const plan = Sql.planHybrid(p, 'mariadb', bindings); kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid'; } catch (e) { kind = 'ERR ' + e.code; }
  console.log('==', src, '\n   run():', run, '\n   sql:', sql, '\n   plan:', kind);
}
for (const src of ['COUNT(LIST(1,2,3))','COUNT(RECORD("a",1))','COUNT((1,2,3))','COUNT(BUCKET((1,2),_))']) {
  const p = compile(src);
  let t; try { t = Sql.translate(p, 'postgresql', {}).asValue(); } catch (e) { t = 'ERR ' + e.code; }
  console.log('expr', src, 'run=', p.run().dump(), 'translate=', t);
}
```

**`$S/probe.php`**

```php
<?php
declare(strict_types=1);
require_once '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Binding; use Sel\Sql\Sql;
$b = [
 'ORDERS' => Binding::relation('orders','o',['ID'=>Binding::column('id','o','NUM'),'CUSTOMER_ID'=>Binding::column('customer_id','o','NUM'),'STATUS'=>Binding::column('status','o','TEXT'),'AMOUNT'=>Binding::column('amount','o','NUM')]),
 'CUSTOMERS' => Binding::relation('customers','c',['ID'=>Binding::column('id','c','NUM'),'NAME'=>Binding::column('name','c','TEXT')]),
];
$progs = [
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))',
 'ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"]))',
 'CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"]))))',
 'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"]))))',
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3))))',
 'ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2))))',
];
foreach ($progs as $src) {
  $p = Sel::compile($src);
  try { $sql = Sql::translateStatement($p, 'mariadb', $b)->asStatement(); } catch (\Throwable $e) { $sql = 'ERR '.($e->code ?? get_class($e)).' '.$e->getMessage(); }
  try { $plan = Sql::planHybrid($p, 'mariadb', $b); $kind = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid'); } catch (\Throwable $e) { $kind = 'ERR '.$e->getMessage(); }
  echo "== $src\n   sql: $sql\n   plan: $kind\n";
}
foreach (['COUNT(LIST(1,2,3))','COUNT(RECORD("a",1))','COUNT((1,2,3))','COUNT(BUCKET((1,2),_))'] as $src) {
  $p = Sel::compile($src);
  try { $t = Sql::translate($p, 'postgresql', [])->asValue(); } catch (\Throwable $e) { $t = 'ERR '.$e->getMessage(); }
  echo "expr $src run=".$p->run()->dump()." translate=$t\n";
}
```

**`$S/probe.cpp`**

```cpp
#include "sel.hpp"
#include "sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main() {
  const Bindings b({
   {"ORDERS", Binding::relation("orders","o",{{"ID",Binding::column("id",std::nullopt,SqlKind::Num)},{"CUSTOMER_ID",Binding::column("customer_id",std::nullopt,SqlKind::Num)},{"STATUS",Binding::column("status",std::nullopt,SqlKind::Text)},{"AMOUNT",Binding::column("amount",std::nullopt,SqlKind::Num)}})},
   {"CUSTOMERS", Binding::relation("customers","c",{{"ID",Binding::column("id",std::nullopt,SqlKind::Num)},{"NAME",Binding::column("name",std::nullopt,SqlKind::Text)}})}});
  const char* progs[] = {
   "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(_)))",
   "ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD(\"id\", _[\"id\"]))",
   "CUSTOMERS .> MAP(c, RECORD(\"id\", c[\"id\"], \"n\", COUNT(BUCKET(FILTER(ORDERS, o, o[\"customer_id\"] == c[\"id\"]), _[\"status\"]))))",
   "ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(RECORD(\"cid\", _K, \"by_status\", COUNT(BUCKET(_, _[\"status\"]))))",
   "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(LIST(1,2,3))))",
   "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(TAKE((1,2,3), 2))))",
  };
  for (auto src : progs) {
    std::cout << "== " << src << "\n";
    const sel::Program p = sel::compile(src);
    try { std::cout << "   sql: " << Sql::translate_statement(p, "mariadb", b).as_statement() << "\n"; }
    catch (const sel::SelError& e) { std::cout << "   sql: ERR " << e.code() << " " << e.what() << "\n"; }
    try { auto plan = Sql::plan_hybrid(p, "mariadb", b); std::cout << "   plan: " << (plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid") << "\n"; }
    catch (const sel::SelError& e) { std::cout << "   plan: ERR " << e.code() << "\n"; }
  }
  for (auto src : {"COUNT(LIST(1,2,3))","COUNT(RECORD(\"a\",1))","COUNT((1,2,3))","COUNT(BUCKET((1,2),_))"}) {
    const sel::Program p = sel::compile(src);
    std::string t; try { t = Sql::translate(p, "postgresql", Bindings({})).as_value(); } catch (const sel::SelError& e) { t = std::string("ERR ") + e.code(); }
    std::cout << "expr " << src << " translate=" << t << "\n";
  }
}
```

**`$S/probe.lisp`**

```lisp
(defun kind-of (plan)
  (cond ((sel.sql:hybrid-plan-pure-sql plan) "pure_sql")
        ((sel.sql:hybrid-plan-pure-memory plan) "pure_memory")
        (t "hybrid")))
(let* ((b (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o"
                   (list (cons "ID" (sel.sql:binding-column "id" "o" "NUM"))
                         (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "o" "NUM"))
                         (cons "STATUS" (sel.sql:binding-column "status" "o" "TEXT"))
                         (cons "AMOUNT" (sel.sql:binding-column "amount" "o" "NUM")))))
                (cons "CUSTOMERS" (sel.sql:binding-relation "customers" "c"
                   (list (cons "ID" (sel.sql:binding-column "id" "c" "NUM"))
                         (cons "NAME" (sel.sql:binding-column "name" "c" "TEXT"))))))))
  (dolist (src '("ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(_)))"
                 "ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD(\"id\", _[\"id\"]))"
                 "CUSTOMERS .> MAP(c, RECORD(\"id\", c[\"id\"], \"n\", COUNT(BUCKET(FILTER(ORDERS, o, o[\"customer_id\"] == c[\"id\"]), _[\"status\"]))))"
                 "ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(RECORD(\"cid\", _K, \"by_status\", COUNT(BUCKET(_, _[\"status\"]))))"
                 "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(LIST(1,2,3))))"
                 "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(TAKE((1,2,3), 2))))"))
    (format t "== ~a~%" src)
    (let ((p (sel:compile-source src)))
      (format t "   sql: ~a~%" (handler-case (sel.sql:as-statement (sel.sql:translate-statement p "mariadb" b))
                                 (error (e) (format nil "ERR ~a" e))))
      (format t "   plan: ~a~%" (handler-case (kind-of (sel.sql:plan-hybrid p "mariadb" b))
                                  (error (e) (format nil "ERR ~a" e))))))
  (dolist (src '("COUNT(LIST(1,2,3))" "COUNT(RECORD(\"a\",1))" "COUNT((1,2,3))" "COUNT(BUCKET((1,2),_))"))
    (let ((p (sel:compile-source src)))
      (format t "expr ~a translate=~a~%" src
              (handler-case (sel.sql:as-value (sel.sql:translate p "postgresql" nil))
                (error (e) (format nil "ERR ~a" e)))))))
```

### Commands run and their output

Run Python probe for both COUNT facets against SQLite

```bash
mkdir -p $SCRATCH/verify-X && cd /home/nathan/workspaces/nth-share/sel && # (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $SCRATCH/verify-X/probe.py
```

```
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))
   run(): -{"1"=-{"id"=t"1", "n"=t"4"}, "2"=-{"id"=t"2", "n"=t"4"}, "3"=-{"id"=t"3", "n"=t"4"}}
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", COUNT(*) AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"3"}}   <-- DIFFERENT
== ORDERS .> MAP(r, RECORD("id", r["id"], "n", COUNT(r)))
   run(): -{"1"=-{"id"=t"1", "n"=t"4"}, "2"=-{"id"=t"2", "n"=t"4"}, "3"=-{"id"=t"3", "n"=t"4"}}
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", COUNT(*) AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"3"}}   <-- DIFFERENT
== ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"]))
   run(): -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id" FROM "orders" "o" WHERE (CAST(COUNT(*) AS NUMERIC) > CAST('2' AS NUMERIC)) []
   plan/exec ERR: None misuse of aggregate function COUNT()
== CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"]))))
   run(): -{"1"=-{"id"=t"7", "n"=t"2"}, "2"=-{"id"=t"9", "n"=t"1"}, "3"=-{"id"=t"11", "n"=t"0"}}
   plan: pure_sql
   SQL: SELECT "c"."id" AS "id", '0' AS "n" FROM "customers" "c" []
   exec: -{"1"=-{"id"=t"7", "n"=t"0"}, "2"=-{"id"=t"9", "n"=t"0"}, "3"=-{"id"=t"11", "n"=t"0"}}   <-- DIFFERENT
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"]))))
   run(): -{"1"=-{"cid"=t"7", "by_status"=t"2"}, "2"=-{"cid"=t"9", "by_status"=t"1"}}
   plan: pure_sql
   SQL: SELECT "o"."customer_id" AS "cid", '0' AS "by_status" FROM "orders" "o" GROUP BY "o"."customer_id" []
   exec: -{"1"=-{"cid"=t"7", "by_status"=t"0"}, "2"=-{"cid"=t"9", "by_status"=t"0"}}   <-- DIFFERENT
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3))))
   run(): -{"1"=-{"id"=t"1", "n"=t"3"}, "2"=-{"id"=t"2", "n"=t"3"}, "3"=-{"id"=t"3", "n"=t"3"}}
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", '0' AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"0"}, "2"=-{"id"=t"2", "n"=t"0"}, "3"=-{"id"=t"3", "n"=t"0"}}   <-- DIFFERENT
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(RECORD("a", 1, "b", 2))))
   run(): -{"1"=-{"id"=t"1", "n"=t"2"}, "2"=-{"id"=t"2", "n"=t"2"}, "3"=-{"id"=t"3", "n"=t"2"}}
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", '0' AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"0"}, "2"=-{"id"=t"2", "n"=t"0"}, "3"=-{"id"=t"3", "n"=t"0"}}   <-- DIFFERENT
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(DISTINCT((1,1,2)))))
   run(): -{"1"=-{"id"=t"1", "n"=t"2"}, "2"=-{"id"=t"2", "n"=t"2"}, "3"=-{"id"=t"3", "n"=t"2"}}
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", '0' AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"0"}, "2"=-{"id"=t"2", "n"=t"0"}, "3"=-{"id"=t"3", "n"=t"0"}}   <-- DIFFERENT
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2))))
   run(): -{"1"=-{"id"=t"1", "n"=t"2"}, "2"=-{"id"=t"2", "n"=t"2"}, "3"=-{"id"=t"3", "n"=t"2"}}
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", '0' AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"0"}, "2"=-{"id"=t"2", "n"=t"0"}, "3"=-{"id"=t"3", "n"=t"0"}}   <-- DIFFERENT
expr COUNT(LIST(1,2,3)) run= t"3" translate= ERR 'str' object has no attribute 'dump'
expr COUNT(RECORD("a",1)) run= t"1" translate= ERR 'str' object has no attribute 'dump'
expr COUNT((1,2,3)) run= t"3" translate= ERR 'str' object has no attribute 'dump'
expr COUNT(DISTINCT((1,1,2))) run= t"2" translat
… (truncated)
```

Run the expression-lane probe and locate translate_statement entry points

```bash
# (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $SCRATCH/verify-X/expr.py; grep -n "translateStatement\|translate_statement\|translate-statement" js/src/sql/index.mjs python/sel/sql/__init__.py php/src/Sql/Sql.php lisp/src/sql/*.lisp cpp/sel_sql.hpp 2>/dev/null | head
```

```
expr COUNT(LIST(1,2,3)) run= t"3" translate= 0
expr COUNT(RECORD("a",1)) run= t"1" translate= 0
expr COUNT((1,2,3)) run= t"3" translate= 3
expr COUNT(DISTINCT((1,1,2))) run= t"2" translate= 0
expr COUNT(SORT_BY((1,2), _)) run= t"2" translate= 0
expr COUNT(TAKE((1,2,3),2)) run= t"2" translate= 0
expr COUNT(BUCKET((1,2),_)) run= t"2" translate= 0
php/src/Sql/Sql.php:57:    public static function translateStatement(Program $program, string $dialect,
php/src/Sql/Sql.php:62:        return $t->translateStatement($program->ast);
php/src/Sql/Sql.php:75:            return self::translateStatement($program, $dialect, $bindings, $options);
js/src/sql/index.mjs:53:  static translateStatement(program, dialect, bindings = null, options = null) {
js/src/sql/index.mjs:55:    return t.translateStatement(program.ast);
js/src/sql/index.mjs:61:      return Sql.translateStatement(program, dialect, bindings, options);
lisp/src/sql/hybrid.lisp:97:                   (full-frag (try-translate-statement full-prog dialect bindings options)))
lisp/src/sql/hybrid.lisp:118:                                (try-translate-statement prefix-prog dialect bindings options))))
lisp/src/sql/hybrid.lisp:309:                           (sql-frag (try-translate-statement rewritten-prog dialect bindings options)))
python/sel/sql/__init__.py:65:    def translate_statement(program, dialect: str, bindings: dict[str, Any] | None = None,
```

Run JS probe for both COUNT facets

```bash
# (file written, see "Reproduction scripts" above)
node $SCRATCH/verify-X/probe.mjs
```

```
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_))) 
   run(): -{"1"=-{"id"=t"1", "n"=t"4"}, "2"=-{"id"=t"2", "n"=t"4"}, "3"=-{"id"=t"3", "n"=t"4"}} 
   sql: [object Object] 
   plan: pure_sql
== ORDERS .> MAP(r, RECORD("id", r["id"], "n", COUNT(r))) 
   run(): -{"1"=-{"id"=t"1", "n"=t"4"}, "2"=-{"id"=t"2", "n"=t"4"}, "3"=-{"id"=t"3", "n"=t"4"}} 
   sql: [object Object] 
   plan: pure_sql
== ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"])) 
   run(): -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}} 
   sql: [object Object] 
   plan: pure_sql
== CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"])))) 
   run(): -{"1"=-{"id"=t"7", "n"=t"2"}, "2"=-{"id"=t"9", "n"=t"1"}, "3"=-{"id"=t"11", "n"=t"0"}} 
   sql: [object Object] 
   plan: pure_sql
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"])))) 
   run(): -{"1"=-{"cid"=t"7", "by_status"=t"2"}, "2"=-{"cid"=t"9", "by_status"=t"1"}} 
   sql: [object Object] 
   plan: pure_sql
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3)))) 
   run(): -{"1"=-{"id"=t"1", "n"=t"3"}, "2"=-{"id"=t"2", "n"=t"3"}, "3"=-{"id"=t"3", "n"=t"3"}} 
   sql: [object Object] 
   plan: pure_sql
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2)))) 
   run(): -{"1"=-{"id"=t"1", "n"=t"2"}, "2"=-{"id"=t"2", "n"=t"2"}, "3"=-{"id"=t"3", "n"=t"2"}} 
   sql: [object Object] 
   plan: pure_sql
expr COUNT(LIST(1,2,3)) run= t"3" translate= 0
expr COUNT(RECORD("a",1)) run= t"1" translate= 0
expr COUNT((1,2,3)) run= t"3" translate= 3
expr COUNT(BUCKET((1,2),_)) run= t"2" translate= 0
```

Rerun JS probe printing SQL text

```bash
sed -i 's/sql = f.sql ?? f.toString();/sql = f.asValue();/' $SCRATCH/verify-X/probe.mjs && node $SCRATCH/verify-X/probe.mjs 2>&1 | grep -E "^==|sql:"
```

```
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_))) 
   sql: ERR E_SQL_SHAPE this expression yields a statement, and a SQL expression is a scalar; use asStatement() 
== ORDERS .> MAP(r, RECORD("id", r["id"], "n", COUNT(r))) 
   sql: ERR E_SQL_SHAPE this expression yields a statement, and a SQL expression is a scalar; use asStatement() 
== ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"])) 
   sql: ERR E_SQL_SHAPE this expression yields a statement, and a SQL expression is a scalar; use asStatement() 
== CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"])))) 
   sql: ERR E_SQL_SHAPE this expression yields a statement, and a SQL expression is a scalar; use asStatement() 
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"])))) 
   sql: ERR E_SQL_SHAPE this expression yields a statement, and a SQL expression is a scalar; use asStatement() 
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3)))) 
   sql: ERR E_SQL_SHAPE this expression yields a statement, and a SQL expression is a scalar; use asStatement() 
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2)))) 
   sql: ERR E_SQL_SHAPE this expression yields a statement, and a SQL expression is a scalar; use asStatement()
```

Rerun JS probe using asStatement

```bash
sed -i 's/sql = f.asValue();/sql = f.asStatement().sql;/' $SCRATCH/verify-X/probe.mjs && grep -n "asStatement" -A12 js/src/sql/fragment.mjs | head -30 && node $SCRATCH/verify-X/probe.mjs 2>&1 | grep -E "^==|sql:"
```

```
44:        'this expression yields a statement, and a SQL expression is a scalar; use asStatement()');
45-    }
46-    return this.#join(mode);
47-  }
48-
49-  // Usable as a top-level SQL query statement.
50:  asStatement(mode = 'inline') {
51-    if (this.kind !== 'STATEMENT') {
52-      refuse('E_SQL_SHAPE',
53-        `expected STATEMENT fragment, got ${this.kind}; use asValue() or asCondition()`);
54-    }
55-    return this.#join(mode);
56-  }
57-
58-  // Usable as a condition. BOOL as it stands, and nothing else.
59-  //
60-  // A NUM or TEXT fragment is refused rather than accepted. Silently allowing
61-  // `WHERE o.total` is how a database turns a validation rule into the
62-  // truthiness test SEL spent its whole design avoiding.
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_))) 
   sql: undefined 
== ORDERS .> MAP(r, RECORD("id", r["id"], "n", COUNT(r))) 
   sql: undefined 
== ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"])) 
   sql: undefined 
== CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"])))) 
   sql: undefined 
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"])))) 
   sql: undefined 
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3)))) 
   sql: undefined 
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2)))) 
   sql: undefined
```

Rerun JS probe printing statement SQL

```bash
sed -i 's/sql = f.asStatement().sql;/sql = f.asStatement();/' $SCRATCH/verify-X/probe.mjs && node $SCRATCH/verify-X/probe.mjs 2>&1 | grep -E "^==|sql:|plan:"
```

```
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_))) 
   sql: SELECT `o`.`id` AS `id`, COUNT(*) AS `n` FROM `orders` `o` 
   plan: pure_sql
== ORDERS .> MAP(r, RECORD("id", r["id"], "n", COUNT(r))) 
   sql: SELECT `o`.`id` AS `id`, COUNT(*) AS `n` FROM `orders` `o` 
   plan: pure_sql
== ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"])) 
   sql: SELECT `o`.`id` AS `id` FROM `orders` `o` WHERE (COUNT(*) > 2) 
   plan: pure_sql
== CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"])))) 
   sql: SELECT `c`.`id` AS `id`, 0 AS `n` FROM `customers` `c` 
   plan: pure_sql
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"])))) 
   sql: SELECT `o`.`customer_id` AS `cid`, 0 AS `by_status` FROM `orders` `o` GROUP BY `o`.`customer_id` 
   plan: pure_sql
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3)))) 
   sql: SELECT `o`.`id` AS `id`, 0 AS `n` FROM `orders` `o` 
   plan: pure_sql
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2)))) 
   sql: SELECT `o`.`id` AS `id`, 0 AS `n` FROM `orders` `o` 
   plan: pure_sql
```

Run PHP probe for both COUNT facets

```bash
S=$SCRATCH/verify-X && # (file written, see "Reproduction scripts" above)
php $S/probe.php
```

```
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))
   sql: SELECT `o`.`id` AS `id`, COUNT(*) AS `n` FROM `orders` `o`
   plan: pure_sql
== ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"]))
   sql: SELECT `o`.`id` AS `id` FROM `orders` `o` WHERE (COUNT(*) > 2)
   plan: pure_sql
== CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"]))))
   sql: SELECT `c`.`id` AS `id`, 0 AS `n` FROM `customers` `c`
   plan: pure_sql
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"]))))
   sql: SELECT `o`.`customer_id` AS `cid`, 0 AS `by_status` FROM `orders` `o` GROUP BY `o`.`customer_id`
   plan: pure_sql
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3))))
   sql: SELECT `o`.`id` AS `id`, 0 AS `n` FROM `orders` `o`
   plan: pure_sql
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2))))
   sql: SELECT `o`.`id` AS `id`, 0 AS `n` FROM `orders` `o`
   plan: pure_sql
expr COUNT(LIST(1,2,3)) run=t"3" translate=0
expr COUNT(RECORD("a",1)) run=t"1" translate=0
expr COUNT((1,2,3)) run=t"3" translate=3
expr COUNT(BUCKET((1,2),_)) run=t"2" translate=0
```

List C++ SQL object files and error type

```bash
S=$SCRATCH/verify-X && grep -n "^SQL_SRCS" -A3 cpp/Makefile; ls cpp/build/*.o; grep -n "struct SelError\|class SelError\|std::string code" cpp/sel.hpp | head -3
```

```
32:SQL_SRCS := sel_sql.cpp sel_sql_binding.cpp sel_sql_emit.cpp sel_sql_map.cpp \
33-            sel_sql_map_data.cpp sel_sql_node.cpp sel_sql_stage1.cpp \
34-            sel_sql_translator.cpp sel_sql_hybrid.cpp
35-SQL_OBJS := $(addprefix $(BUILD)/,$(SQL_SRCS:.cpp=.o))
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
47:class SelError : public std::exception {
49:  SelError(std::string code, std::string message, Pos pos);
63:  std::string code_;
```

Compile and run C++ probe for both COUNT facets

```bash
S=$SCRATCH/verify-X && sed -n 47,70p cpp/sel.hpp; # (file written, see "Reproduction scripts" above)
c++ -std=c++23 -O1 -I cpp -o $S/probe $S/probe.cpp cpp/build/sel_sql*.o cpp/build/sel.o 2>&1 | head -20 && $S/probe
```

```
Exit code 127
class SelError : public std::exception {
 public:
  SelError(std::string code, std::string message, Pos pos);

  const std::string& code() const noexcept { return code_; }
  const std::string& message() const noexcept { return message_; }
  int line() const noexcept { return pos_.line; }
  int col() const noexcept { return pos_.col; }
  int offset() const noexcept { return pos_.offset; }
  Pos pos() const noexcept { return pos_; }

  // "CODE at line:col: message"
  std::string str() const;
  const char* what() const noexcept override { return message_.c_str(); }

 private:
  std::string code_;
  std::string message_;
  Pos pos_;
};

// --- limits -----------------------------------------------------------------

// spec/SPEC.md §6.4's three caps, which are one number: the parser's nesting,
$SCRATCH/verify-X/probe.cpp: In function ‘int main()’:
$SCRATCH/verify-X/probe.cpp:27:73: error: call of overloaded ‘Bindings(<brace-enclosed initializer list>)’ is ambiguous
   27 |     std::string t; try { t = Sql::translate(p, "postgresql", Bindings({})).as_value(); } catch (const sel::SelError& e) { t = std::string("ERR ") + e.code(); }
      |                                                                         ^
  • there are 3 candidates
In file included from $SCRATCH/verify-X/probe.cpp:2:
    • candidate 1: ‘sel::sql::Bindings::Bindings(std::vector<std::pair<std::__cxx11::basic_string<char>, sel::sql::Binding> >)’
      cpp/sel_sql.hpp:330:12:
        330 |   explicit Bindings(std::vector<std::pair<std::string, Binding>> bindings);
            |            ^~~~~~~~
    • candidate 2: ‘sel::sql::Bindings::Bindings(const sel::sql::Bindings&)’
      cpp/sel_sql.hpp:327:7:
        327 | class Bindings {
            |       ^~~~~~~~
    • candidate 3: ‘sel::sql::Bindings::Bindings(sel::sql::Bindings&&)’
/bin/bash: line 33: $SCRATCH/verify-X/probe: No such file or directory
```

Compile and run C++ probe

```bash
S=$SCRATCH/verify-X && sed -i 's/Bindings({})/Bindings(std::vector<std::pair<std::string, Binding>>{})/' $S/probe.cpp && c++ -std=c++23 -O1 -I cpp -o $S/probe $S/probe.cpp cpp/build/sel_sql*.o cpp/build/sel.o 2>&1 | head -20 && $S/probe
```

```
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))
   sql: SELECT `id` AS `id`, COUNT(*) AS `n` FROM `orders` `o`
   plan: pure_sql
== ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"]))
   sql: SELECT `id` AS `id` FROM `orders` `o` WHERE (COUNT(*) > 2)
   plan: pure_sql
== CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"]))))
   sql: SELECT `id` AS `id`, 0 AS `n` FROM `customers` `c`
   plan: pure_sql
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"]))))
   sql: SELECT `customer_id` AS `cid`, 0 AS `by_status` FROM `orders` `o` GROUP BY `customer_id`
   plan: pure_sql
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3))))
   sql: SELECT `id` AS `id`, 0 AS `n` FROM `orders` `o`
   plan: pure_sql
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2))))
   sql: SELECT `id` AS `id`, 0 AS `n` FROM `orders` `o`
   plan: pure_sql
expr COUNT(LIST(1,2,3)) translate=0
expr COUNT(RECORD("a",1)) translate=0
expr COUNT((1,2,3)) translate=3
expr COUNT(BUCKET((1,2),_)) translate=0
```

Run Lisp probe for both COUNT facets

```bash
S=$SCRATCH/verify-X && grep -n "plan-hybrid\|pure-sql\|hybrid-plan-" lisp/tests/unit.lisp | head -6; grep -n "sel-error-code\|error-code" lisp/src/errors.lisp | head -3; # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp 2>&1 | tail -30
```

```
414:           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
415:      (is-true (sel.sql:hybrid-plan-pure-sql-p plan))
416:      (is-false (sel.sql:hybrid-plan-pure-memory-p plan))
417:      (is (null (sel.sql:hybrid-plan-continuation-program plan)))
430:           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
431:      (is-false (sel.sql:hybrid-plan-pure-sql-p plan))
19:  ((code :initarg :code :reader sel-error-code)
26:                     (sel-error-code c) (sel-error-line c) (sel-error-col c)
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=823569 "main thread" RUNNING
                                                 {1204028083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 2, Column: 38, File-Position: 59

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {1201EB74F3}>

Backtrace for: #<SB-THREAD:THREAD tid=823569 "main thread" RUNNING {1204028083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201EB87E3}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201EB87E3}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201EB87E3}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201EB87E3}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201EB8793}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {1201EB74F3}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {734F2004F4CB}> #<SB-C::SOURCE-INFO {1201E577E3}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {1201EB74F3}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {1201EB74F3}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {734F2004F88B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {1201EB74F3}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {1201EB74F3}>)
9: (LOAD #P"$SCRATCH/verify-X/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-X/probe.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Read how the Lisp SQL package is loaded

```bash
cat lisp/bin/boot.lisp | head -30; head -20 lisp/bin/sqlt.lisp; head -12 lisp/bin/test
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
#!/usr/bin/env bash
# The Common Lisp unit tests. The conformance suite is what proves the
# implementation correct; these cover the layers underneath it.
here="$(cd "$(dirname "$0")" && pwd)"
exec sbcl --noinform --disable-debugger --non-interactive \
  --load "$here/boot.lisp" \
  --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/tests))' \
  --eval '(unless (funcall (find-symbol "RUN!" "FIVEAM") (find-symbol "SEL" "SEL-TESTS")) (sb-ext:exit :code 1))'
```

Run Lisp probe after loading the SQL system

```bash
S=$SCRATCH/verify-X && grep -n "sel-lang/sql\|:sel-lang" lisp/*.asd | head; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $S/probe.lisp 2>&1 | grep -v "^;" | head -40
```

```
3:;;;;   (ql:quickload :sel-lang)
53:;;; evaluator should not carry the dialect map. `(ql:quickload :sel-lang/sql)`
55:(defsystem "sel-lang/sql"
85:  :depends-on ("sel-lang" "sel-lang/sql" "fiveam")
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=823892 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Symbol "HYBRID-PLAN-PURE-SQL" not found in the SEL.SQL package.

      Line: 2, Column: 38, File-Position: 59

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {12022A98E3}>

Backtrace for: #<SB-THREAD:THREAD tid=823892 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {12022AAB83}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {12022AAB83}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {12022AAB83}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {12022AAB83}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Symbol ~S not found in the ~A package." {12022AAB33}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {12022A98E3}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {7F3A6C72F4CB}> #<SB-C::SOURCE-INFO {12022B21E3}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {12022A98E3}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {12022A98E3}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {7F3A6C72F88B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {12022A98E3}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-X/probe.lisp" {12022A98E3}>)
9: (LOAD #P"$SCRATCH/verify-X/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:EVAL . "(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol \"QUICKLOAD\" \"QL\") :sel-lang/sql))") (:LOAD . "$SCRATCH/verify-X/probe.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Rerun Lisp probe with correct accessor names

```bash
S=$SCRATCH/verify-X && sed -i 's/hybrid-plan-pure-sql plan/hybrid-plan-pure-sql-p plan/; s/hybrid-plan-pure-memory plan/hybrid-plan-pure-memory-p plan/' $S/probe.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $S/probe.lisp 2>&1 | grep -v "^;" | head -40
```

```
While evaluating the form starting at line 5, column 0
  of #P"$SCRATCH/verify-X/probe.lisp":
Unhandled SEL.SQL:SQL-ERROR in thread #<SB-THREAD:THREAD tid=824146 "main thread" RUNNING
                                         {1204030083}>:
  E_SQL_BINDING at 0:0: a binding has type "NUM"; use one of NUM, TEXT, BOOL, BIN, UNKNOWN, LIST, STATEMENT

Backtrace for: #<SB-THREAD:THREAD tid=824146 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SEL.SQL:SQL-ERROR {12024ADC73}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SEL.SQL:SQL-ERROR {12024ADC73}>)
2: (INVOKE-DEBUGGER #<SEL.SQL:SQL-ERROR {12024ADC73}>)
3: (ERROR SEL.SQL:SQL-ERROR :CODE "E_SQL_BINDING" :MESSAGE "a binding has type \"NUM\"; use one of NUM, TEXT, BOOL, BIN, UNKNOWN, LIST, STATEMENT" :LINE 0 :COL 0 :OFFSET 0)
4: (SEL.SQL::REFUSE "E_SQL_BINDING" "a binding has type \"NUM\"; use one of NUM, TEXT, BOOL, BIN, UNKNOWN, LIST, STATEMENT" NIL)
5: (SEL.SQL:BINDING-COLUMN "id" "o" "NUM" :EXACT NIL :SARGABLE NIL :GUARD NIL :COLLATION NIL :PREFILTER NIL :SPLIT-SARGABLE NIL)
6: ("top level form") [toplevel]
7: ((FLET "G" :IN SB-C::%COMPILE-IN-LEXENV))
8: (SB-C::%COMPILE-IN-LEXENV (LET* ((B (LIST (CONS "ORDERS" #) (CONS "CUSTOMERS" #)))) (DOLIST (SRC (QUOTE ("ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(_)))" "ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD(\"id\", _[\"id\"]))" "CUSTOMERS .> MAP(c, RECORD(\"id\", c[\"id\"], \"n\", COUNT(BUCKET(FILTER(ORDERS, o, o[\"customer_id\"] == c[\"id\"]), _[\"status\"]))))" "ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(RECORD(\"cid\", _K, \"by_status\", COUNT(BUCKET(_, _[\"status\"]))))" "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(LIST(1,2,3))))" "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(TAKE((1,2,3), 2))))"))) (FORMAT T "== ~a~%" SRC) (LET ((P #)) (FORMAT T "   sql: ~a~%" (HANDLER-CASE # #)) (FORMAT T "   plan: ~a~%" (HANDLER-CASE # #)))) (DOLIST (SRC (QUOTE ("COUNT(LIST(1,2,3))" "COUNT(RECORD(\"a\",1))" "COUNT((1,2,3))" "COUNT(BUCKET((1,2),_))"))) (LET ((P #)) (FORMAT T "expr ~a translate=~a~%" SRC (HANDLER-CASE # #))))) #<NULL-LEXENV> NIL #<SB-C::SOURCE-INFO {12022B21E3}> 1 NIL T T)
9: (SB-C:EVAL-WITH-COMPILE-IN-LEXENV (LET* ((B (LIST (CONS "ORDERS" #) (CONS "CUSTOMERS" #)))) (DOLIST (SRC (QUOTE ("ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(_)))" "ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD(\"id\", _[\"id\"]))" "CUSTOMERS .> MAP(c, RECORD(\"id\", c[\"id\"], \"n\", COUNT(BUCKET(FILTER(ORDERS, o, o[\"customer_id\"] == c[\"id\"]), _[\"status\"]))))" "ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(RECORD(\"cid\", _K, \"by_status\", COUNT(BUCKET(_, _[\"status\"]))))" "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(LIST(1,2,3))))" "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(TAKE((1,2,3), 2))))"))) (FORMAT T "== ~a~%" SRC) (LET ((P #)) (FORMAT T "   sql: ~a~%" (HANDLER-CASE # #)) (FORMAT T "   plan: ~a~%" (HANDLER-CASE # #)))) (DOLIST (SRC (QUOTE ("COUNT(LIST(1,2,3))" "COUNT(RECORD(\"a\",1))" "COUNT((1,2,3))" "COUNT(BUCKET((1,2),_))"))) (LET ((P #)) (FORMAT T "expr ~a translate=~a~%" SRC (HANDLER-CASE # #))))) #<NULL-LEXENV> #<SB-C::SOURCE-INFO {12022B21E3}> 1 NIL)
10: (SB-IMPL::%SIMPLE-EVAL (LET* ((B (LIST (CONS "ORDERS" #) (CONS "CUSTOMERS" #)))) (DOLIST (SRC (QUOTE ("ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"n\", COUNT(_)))" "ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD(\"id\", _[\"id\"]))" "CUSTOMERS .> MAP(c, RECORD(\"id\", c[\"id\"], \"n\", COU
… (truncated)
```

Rerun Lisp probe with keyword kinds

```bash
S=$SCRATCH/verify-X && sed -i 's/"NUM"/:num/g; s/"TEXT"/:text/g' $S/probe.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $S/probe.lisp 2>&1 | grep -v "^;" | head -30
```

```
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))
   sql: SELECT `o`.`id` AS `id`, COUNT(*) AS `n` FROM `orders` `o`
   plan: pure_sql
== ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"]))
   sql: SELECT `o`.`id` AS `id` FROM `orders` `o` WHERE (COUNT(*) > 2)
   plan: pure_sql
== CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"]))))
   sql: SELECT `c`.`id` AS `id`, 0 AS `n` FROM `customers` `c`
   plan: pure_sql
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"]))))
   sql: SELECT `o`.`customer_id` AS `cid`, 0 AS `by_status` FROM `orders` `o` GROUP BY `o`.`customer_id`
   plan: pure_sql
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3))))
   sql: SELECT `o`.`id` AS `id`, 0 AS `n` FROM `orders` `o`
   plan: pure_sql
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2))))
   sql: SELECT `o`.`id` AS `id`, 0 AS `n` FROM `orders` `o`
   plan: pure_sql
expr COUNT(LIST(1,2,3)) translate=0
expr COUNT(RECORD("a",1)) translate=0
expr COUNT((1,2,3)) translate=3
expr COUNT(BUCKET((1,2),_)) translate=0
```

Run the Python probe against the parent commit's sources extracted to scratchpad

```bash
S=$SCRATCH/verify-X && mkdir -p $S/old && git archive 8fe0e3a python js | tar -x -C $S/old && cd $S/old && PYTHONPATH=$S/old/python python3 $S/probe.py 2>&1 | grep -E "^==|plan:|SQL:|exec:"
```

```
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", COUNT(*) AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"3"}}   <-- DIFFERENT
== ORDERS .> MAP(r, RECORD("id", r["id"], "n", COUNT(r)))
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", COUNT(*) AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"3"}}   <-- DIFFERENT
== ORDERS .> FILTER(COUNT(_) > 2) .> MAP(RECORD("id", _["id"]))
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id" FROM "orders" "o" WHERE (CAST(COUNT(*) AS NUMERIC) > CAST('2' AS NUMERIC)) []
== CUSTOMERS .> MAP(c, RECORD("id", c["id"], "n", COUNT(BUCKET(FILTER(ORDERS, o, o["customer_id"] == c["id"]), _["status"]))))
   plan: pure_sql
   SQL: SELECT "c"."id" AS "id", '0' AS "n" FROM "customers" "c" []
   exec: -{"1"=-{"id"=t"7", "n"=t"0"}, "2"=-{"id"=t"9", "n"=t"0"}, "3"=-{"id"=t"11", "n"=t"0"}}   <-- DIFFERENT
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "by_status", COUNT(BUCKET(_, _["status"]))))
   plan: hybrid
   SQL: SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" []
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(LIST(1,2,3))))
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", '0' AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"0"}, "2"=-{"id"=t"2", "n"=t"0"}, "3"=-{"id"=t"3", "n"=t"0"}}   <-- DIFFERENT
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(RECORD("a", 1, "b", 2))))
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", '0' AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"0"}, "2"=-{"id"=t"2", "n"=t"0"}, "3"=-{"id"=t"3", "n"=t"0"}}   <-- DIFFERENT
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(DISTINCT((1,1,2)))))
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", '0' AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"0"}, "2"=-{"id"=t"2", "n"=t"0"}, "3"=-{"id"=t"3", "n"=t"0"}}   <-- DIFFERENT
== ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(TAKE((1,2,3), 2))))
   plan: pure_sql
   SQL: SELECT "o"."id" AS "id", '0' AS "n" FROM "orders" "o" []
   exec: -{"1"=-{"id"=t"1", "n"=t"0"}, "2"=-{"id"=t"2", "n"=t"0"}, "3"=-{"id"=t"3", "n"=t"0"}}   <-- DIFFERENT
```

Check how the ansi dialect map treats list-yielding functions

```bash
python3 - <<'EOF'
import json
d=json.load(open('sql/dialects/ansi.json'))
print(list(d.keys()))
fn=d.get('fn',{})
for k in ['LIST','RECORD','DISTINCT','DEDUPE','TAKE','DROP','SORT_BY','SORT','BUCKET','SPLIT','MAP','FILTER','COUNT','INDEXES','BTL','RGROUPS','GET','LEN']:
    print(k, repr(fn.get(k,'<absent>'))[:100])
EOF
```

```
['dialect', 'version', 'target', 'lexical', 'ops', 'funcs', 'skel', 'notes']
LIST '<absent>'
RECORD '<absent>'
DISTINCT '<absent>'
DEDUPE '<absent>'
TAKE '<absent>'
DROP '<absent>'
SORT_BY '<absent>'
SORT '<absent>'
BUCKET '<absent>'
SPLIT '<absent>'
MAP '<absent>'
FILTER '<absent>'
COUNT '<absent>'
INDEXES '<absent>'
BTL '<absent>'
RGROUPS '<absent>'
GET '<absent>'
LEN '<absent>'
```

Check how LIST/RECORD/etc. are treated in value position and as other aggregates' sources

```bash
python3 - <<'EOF'
import json
d=json.load(open('sql/dialects/ansi.json'))
fn=d['funcs']
for k in ['LIST','RECORD','DISTINCT','DEDUPE','TAKE','DROP','SORT_BY','SORT','BUCKET','SPLIT','MAP','FILTER','COUNT','INDEXES','BTL','RGROUPS','GET','LEN','KEYS','VALUES','MERGE']:
    print(k, repr(fn.get(k,'<absent>'))[:120])
EOF
node -e "
import('/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs').then(async () => {
const { compile } = await import('/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs');
const { Sql } = await import('/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs');
for (const src of ['LIST(1,2)', 'RECORD(\"a\",1)', 'DISTINCT((1,2))', 'TAKE((1,2),1)', 'BUCKET((1,2),_)', 'SORT_BY((1,2),_)', 'SUM(LIST(1,2), _)', 'ALL(RECORD(\"a\",1), _ > 0)', 'HAS(LIST(1,2), 1)', 'LIST(1,2)[1]']) {
  let t; try { t = Sql.translate(compile(src), 'postgresql', {}).asValue(); } catch (e) { t = 'ERR ' + e.code + ': ' + e.message; }
  console.log(src, '=>', t);
}});"
```

```
LIST '<absent>'
RECORD '<absent>'
DISTINCT '<absent>'
DEDUPE '<absent>'
TAKE '<absent>'
DROP '<absent>'
SORT_BY '<absent>'
SORT '<absent>'
BUCKET '<absent>'
SPLIT 'yields a list, and a SQL expression is a scalar'
MAP '<absent>'
FILTER '<absent>'
COUNT '<absent>'
INDEXES '<absent>'
BTL 'yields a list, and a SQL expression is a scalar'
RGROUPS 'yields a list, and a SQL expression is a scalar'
GET 'takes a container, and a SQL expression is a scalar'
LEN {'tpl': 'CHAR_LENGTH({textCast:0})', 'ret': 'NUM'}
KEYS '<absent>'
VALUES '<absent>'
MERGE '<absent>'
LIST(1,2) => ERR E_SQL_UNSUPPORTED: LIST has no mapping in dialect postgresql
RECORD("a",1) => ERR E_SQL_UNSUPPORTED: RECORD has no mapping in dialect postgresql
DISTINCT((1,2)) => ERR E_SQL_SHAPE: a list is not a SQL value; a list can only be the thing an aggregate iterates
TAKE((1,2),1) => ERR E_SQL_SHAPE: a list is not a SQL value; a list can only be the thing an aggregate iterates
BUCKET((1,2),_) => ERR E_SQL_SHAPE: a list is not a SQL value; a list can only be the thing an aggregate iterates
SORT_BY((1,2),_) => ERR E_SQL_SHAPE: a list is not a SQL value; a list can only be the thing an aggregate iterates
SUM(LIST(1,2), _) => ERR E_SQL_UNSUPPORTED: LIST has no mapping in dialect postgresql
ALL(RECORD("a",1), _ > 0) => ERR E_SQL_UNSUPPORTED: RECORD has no mapping in dialect postgresql
HAS(LIST(1,2), 1) => FALSE
LIST(1,2)[1] => ERR E_SQL_SHAPE: only a bound name can be indexed here; SQL has no way to index into the result of an expression
```

