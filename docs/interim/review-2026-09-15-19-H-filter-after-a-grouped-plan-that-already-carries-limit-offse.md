# H. FILTER after a grouped plan that already carries LIMIT/OFFSET/ORDER BY: Lisp wraps (WHERE), the other four append HAVING before LIMIT

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

The FILTER step's derived-table predicate really is `groupBy === null && (...)` in JS/PHP/Python/C++ and `(or limit offset (and (null group-by) projections) order-by)` in Lisp, and the difference is observable today: for `BUCKET(...) .> TAKE(2) .> FILTER(_["n"] > 1)` the four hosts emit `... GROUP BY cid HAVING (COUNT(*) > 1) LIMIT 2` while Lisp emits a `_sub1` derived table with the LIMIT inside and a WHERE outside (and on the sqlite dialect Lisp's translate() refuses E_SQL_UNSUPPORTED while the four translate, and plan_hybrid classifies it hybrid in Lisp vs pure_sql in the four). The four hosts' SQL is also wrong against the evaluator: Python's in-memory lane answers one row `[{A,2}]` and its pure_sql plan executed on SQLite answers two rows `[{A,2},{C,2}]` (DROP(1) likewise: memory `[C]`, SQL keeps only rows after the HAVING). All five predicates are byte-identical at 8fe0e3a, so the defect is pre-existing; the commit added the "a FILTER between them is the HAVING" promise and the stmt.bucket.pagination case without pinning the combined shape.

## Suggested fix — case first

Smallest fix: in js/src/sql/translator.mjs:1856, php/src/Sql/Translator.php:2571, python/sel/sql/translator.py:1794 and cpp/sel_sql_translator.cpp:2372 make the FILTER wrap predicate `limit !== null || offset !== null || (groupBy === null && (projections || selectCols || orderBy.length || distinct))` — i.e. a LIMIT/OFFSET already on the plan always forces the derived table, grouped or not, so the FILTER lands as a WHERE over the paginated groups (Lisp's shape, which matches the evaluator); then bring lisp/src/sql/translator.lisp:1925 to exactly the same predicate (drop the unconditional `order-by` wrap, since HAVING+ORDER BY is equivalent and the Lisp planner already emits that form; add select-cols/distinct under the ungrouped branch). Cases to add first, in sql/cases/24-bucket.sqlt then `node tools/gen-sql-cases.mjs`: `stmt.bucket.filter-after-pagination` — `ITEMS .> BUCKET(_["dept"], RECORD("dept", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)` (as statement, mariadb) expecting the `_sub1` derived-table string, a DROP twin, a `stmt.bucket.filter-after-sort` pinning the HAVING ... ORDER BY string, and a `--- plan` case for the same TAKE shape on sqlite pinning one classification for all five; plus extend python/tests/test_unit.py::test_bucket_plans_answer_what_the_evaluator_answers_on_sqlite with the TAKE(2) .> FILTER and DROP(1) .> FILTER shapes so the executed result is compared with the evaluator.

## Verifier reasoning

Facet 1 (cross-host string divergence under translate): CONFIRMED for all three shapes (TAKE, DROP, SORT_BY before the FILTER) — four hosts append HAVING to the grouped statement, Lisp wraps in `_sub1`. Facet 2 (divergence under plan_hybrid): CONFIRMED for TAKE and DROP (four: pure_sql with HAVING-before-LIMIT; Lisp on mariadb: pure_sql with the wrapped statement; Lisp on sqlite: hybrid with prefix `GROUP BY LIMIT 2` and the FILTER in memory). For the SORT_BY shape all five planners agree (Lisp's logical optimiser moves the FILTER ahead of the SORT_BY, so the plan is the HAVING form), so that facet is a translate()-only string difference and is not semantically wrong (HAVING then ORDER BY equals SORT then FILTER), which matches the finding's own scoping of the semantic claim to TAKE/DROP. Facet 3 (four hosts' SQL disagrees with the evaluator): CONFIRMED by running Program.run vs Sql.execute_hybrid on SQLite in Python and by executing both SQL forms directly in sqlite3; the in-memory lane in all five hosts agrees on one row. Lisp's wrapped form (LIMIT inside, filter outside) is the semantically right one, i.e. the `groupBy === null &&` guard in the four hosts over-reaches: it was meant to keep a FILTER directly after a BUCKET as HAVING, but it also suppresses the wrap once a LIMIT/OFFSET has landed on the grouped plan. Introduced: pre-existing — `git show 8fe0e3a:` shows identical predicates in every host (lisp 1867-1873, js 1781-1785, php 2486-2491, python 2041-2046, cpp 2313-2319). No sql/cases case covers FILTER after TAKE/DROP/SORT_BY on a bucket (24-bucket.sqlt has FILTER-before-SORT_BY and TAKE.>DROP without a FILTER; 25-hybrid-plans.sqlt pins TAKE.>FILTER only on an ungrouped plan, where every host correctly wraps), which is why the suite is green. Severity high: different SQL bytes between hosts, different plan classification/refusal between hosts, and different results between the in-memory and SQL lanes. Side observation outside this cluster: the SQL lane renumbers list keys from 1 whereas in-memory FILTER preserves them (`-{"3"=...}` vs `-{"1"=...}`), a general pre-existing lane property not asserted by this finding.

## Verifier evidence

```
Code (HEAD): js/src/sql/translator.mjs:1856-1859 `candidate.groupBy === null && Boolean(projections || selectCols || limit !== null || offset !== null || orderBy.length || distinct)`; php/src/Sql/Translator.php:2571-2575 same; python/sel/sql/translator.py:1794-1798 same; cpp/sel_sql_translator.cpp:2372-2377 `!plan.group_by.has_value() && (...)`; lisp/src/sql/translator.lisp:1925-1930 `(or limit offset (and (null group-by) projections) order-by)`. Parent commit: `git show 8fe0e3a:<file> | grep -A6 FILTER` shows the identical predicates at lisp 1867-1873, js 1781-1785, php 2486-2491, python 2041-2046, cpp 2313-2319.

Own repro (scratchpad verify-H/probe.{mjs,py,php,lisp,cpp}; bindings ORDERS=relation orders {CID text, AMT num}; dialect mariadb):
SRC ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)
 js/php/python/cpp translate AND planHybrid(pure_sql): SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2
 lisp translate AND plan-hybrid(pure_sql): SELECT `_sub1`.* FROM (SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`n` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`n` AS DECIMAL(65,10)) ELSE NULL END > 1)
SRC ... .> DROP(1) .> FILTER(_["n"] > 1): four hosts `... HAVING (COUNT(*) > 1) LIMIT 18446744073709551615 OFFSET 1`; lisp wraps `(... GROUP BY `cid` LIMIT 18446744073709551615 OFFSET 1) `_sub1` WHERE ...`.
SRC ... RECORD("cid", _K, "s", SUM(_, _["amt"]))) .> SORT_BY(_["s"], "DESC") .> FILTER(_["s"] > 5): four hosts translate+plan `... GROUP BY `cid` HAVING (COALESCE(SUM(`amt`), 0) > 5) ORDER BY ... DESC`; lisp translate wraps (`(... ORDER BY ... DESC) `_sub1` WHERE ...`) but lisp plan-hybrid gives the same HAVING string as the four.
Bare bucket variant (JS): ORDERS .> BUCKET(_["cid"]) .> TAKE(2) .> FILTER(COUNT(_) > 1) -> pure_sql `SELECT `cid` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2`.
sqlite dialect: js/py translate -> `... HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 2`, plan pure_sql; lisp translate -> `ERR E_SQL_UNSUPPORTED at 1:84: dialect sqlite has no way to ask whether a value is a number...`, lisp plan-hybrid -> `hybrid :: SELECT "cid" AS "cid", COUNT(*) AS "n" FROM "orders" GROUP BY "cid" LIMIT 2`.

In-memory lane, all five REPLs, P='LIST(RECORD("cid","A"), RECORD("cid","A"), RECORD("cid","B"), RECORD("cid","C"), RECORD("cid","C")) .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)': js/php/cpp/lisp/py all print -{"1"=-{"cid"=t"A", "n"=t"2"}}.
Lane comparison (verify-H/lanes.py, Python, sqlite3 in-memory table orders with rows A,A,B,C,C):
 TAKE(2) .> FILTER: memory -{"1"=-{"cid"=t"A", "n"=t"2"}} ; execute_hybrid(pure_sql) -{"1"=-{"cid"=t"A", "n"=t"2"}, "2"=-{"cid"=t"C", "n"=t"2"}}
 DROP(1) .> FILTER: memory -{"2"=-{"cid"=t"C", "n"=t"2"}} ; execute_hybrid(pure_sql) -{"1"=-{"cid"=t"C", "n"=t"2"}}
Direct sqlite3: four-host SQL -> [('A', 2), ('C', 2)]; Lisp prefix `GROUP BY "cid" LIMIT 2` -> [('A', 2), ('B', 1)] then in-memory n>1 -> [('A', 2)] (matches the evaluator).
Suite coverage: grep of sql/cases/24-bucket.sqlt and 25-hybrid-plans.sqlt finds no FILTER after TAKE/DROP/SORT_BY on a bucket; 25-hybrid-plans.sqlt:367 pins ORDERS .> TAKE(2) .> FILTER(...) (ungrouped) as the wrapped `_sub1` form in every host.
```

## Original review reports (deduplicated into this finding)

### [bucket-translator] FILTER after a grouped plan that already carries LIMIT/OFFSET/ORDER BY: Lisp wraps and emits WHERE, the other four append HAVING before LIMIT (and SEL applies TAKE before the FILTER)

*coherence · high · hosts: js, php, python, cpp, lisp*

Locations: `lisp/src/sql/translator.lisp:1925`; `js/src/sql/translator.mjs:1856`; `php/src/Sql/Translator.php:2571`; `python/sel/sql/translator.py:1794`; `cpp/sel_sql_translator.cpp:2372`

The FILTER step's derived-table predicate is `groupBy === null && (projections || selectCols || limit || offset || orderBy || distinct)` in JS/PHP/Python/C++ — a grouped plan is never wrapped, the predicate always becomes HAVING — but `(or limit offset (and (null group-by) projections) order-by)` in Lisp, which wraps a grouped plan once a TAKE/DROP/SORT has landed. The SQL strings differ, and for TAKE/DROP the four hosts' SQL is also semantically wrong against the evaluator (HAVING filters before LIMIT; SEL takes first). The conditions predate the commit, but the diff's 'a FILTER between them is the HAVING' promise and stmt.bucket.pagination make FILTER-after-TAKE-after-BUCKET a natural shape and nothing pins it.

Reported repro:

```
Scratch case 99-probe.sqlt p13 / 97-probe2.sqlt r07: ITEMS .> BUCKET(_["dept"], RECORD("dept", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1) — JS/PHP/Py/C++: ... GROUP BY `dept` HAVING (COUNT(*) > 1) LIMIT 2; Lisp: SELECT `_sub1`.* FROM (... GROUP BY `dept` LIMIT 2) `_sub1` WHERE (... `_sub1`.`n` ... > 1). 94-probe5.sqlt t01 (SORT_BY then FILTER) and t02 (DROP then FILTER after the MAP spelling) split the same way. probe_sqlite.py, Python: the TAKE(2) .> FILTER shape plans pure_sql; run() = [{cid:A,n:2}], SQLite = [{cid:NULL,n:2},{cid:A,n:2}].
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`probe.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const bindings = { ORDERS: Binding.relation('orders', null, { CID: Binding.column('cid', null, 'TEXT'), AMT: Binding.column('amt', null, 'NUM') }, null, null) };
const progs = [
  'ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)',
  'ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)',
  'ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "s", SUM(_, _["amt"]))) .> SORT_BY(_["s"], "DESC") .> FILTER(_["s"] > 5)',
  'ORDERS .> BUCKET(_["cid"]) .> TAKE(2) .> FILTER(_["cid"] != "A")',
];
for (const src of progs) {
  const p = compile(src);
  let s;
  try { s = Sql.translate(p, 'mariadb', bindings, {}).asStatement('inline'); } catch (e) { s = `${e.code} ${e.message}`; }
  let cls;
  try { const pl = Sql.planHybrid(p, 'mariadb', bindings, {}); cls = pl.pureSql ? 'pure_sql' : pl.pureMemory ? 'pure_memory' : 'hybrid'; cls += ' :: ' + (pl.sqlStatement ? pl.sqlStatement.asStatement('inline') : '-'); } catch (e) { cls = `${e.code} ${e.message}`; }
  console.log('SRC  ' + src); console.log('SQL  ' + s); console.log('PLAN ' + cls); console.log();
}
```

**`probe.py`**

```python
from sel import compile
from sel.sql import Sql, Binding
bindings = {'ORDERS': Binding.relation('orders', None, {'CID': Binding.column('cid', None, 'TEXT'), 'AMT': Binding.column('amt', None, 'NUM')}, None, None)}
progs = [
  'ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)',
  'ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)',
  'ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "s", SUM(_, _["amt"]))) .> SORT_BY(_["s"], "DESC") .> FILTER(_["s"] > 5)',
]
for src in progs:
    p = compile(src)
    try: s = Sql.translate(p, 'mariadb', bindings, {}).as_statement('inline')
    except Exception as e: s = f'{getattr(e,"code",type(e).__name__)} {e}'
    try:
        pl = Sql.plan_hybrid(p, 'mariadb', bindings, {})
        cls = 'pure_sql' if pl.pure_sql else 'pure_memory' if pl.pure_memory else 'hybrid'
        cls += ' :: ' + (pl.sql_statement.as_statement('inline') if pl.sql_statement else '-')
    except Exception as e: cls = f'{getattr(e,"code",type(e).__name__)} {e}'
    print('SRC ', src); print('SQL ', s); print('PLAN', cls); print()
```

**`probe.php`**

```php
<?php
require_once '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$bindings = ['ORDERS' => Binding::relation('orders', null, ['CID' => Binding::column('cid', null, 'TEXT'), 'AMT' => Binding::column('amt', null, 'NUM')])];
$progs = [
  'ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)',
  'ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)',
  'ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "s", SUM(_, _["amt"]))) .> SORT_BY(_["s"], "DESC") .> FILTER(_["s"] > 5)',
];
foreach ($progs as $src) {
  $p = Sel::compile($src);
  try { $s = Sql::translate($p, 'mariadb', $bindings, [])->asStatement('inline'); } catch (\Throwable $e) { $s = get_class($e) . ' ' . $e->getMessage(); }
  try { $pl = Sql::planHybrid($p, 'mariadb', $bindings, []); $cls = $pl->pureSql ? 'pure_sql' : ($pl->pureMemory ? 'pure_memory' : 'hybrid'); $cls .= ' :: ' . ($pl->sqlStatement ? $pl->sqlStatement->asStatement('inline') : '-'); } catch (\Throwable $e) { $cls = get_class($e) . ' ' . $e->getMessage(); }
  echo "SRC  $src\nSQL  $s\nPLAN $cls\n\n";
}
```

**`probe.lisp`**

```lisp
(let ((*standard-output* (make-broadcast-stream)))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
(defpackage #:probe-h (:use #:common-lisp #:sel.sql))
(in-package #:probe-h)
(defun bnd () (list (cons "ORDERS" (binding-relation "orders" nil (list (cons "CID" (binding-column "cid" nil :text)) (cons "AMT" (binding-column "amt" nil :num))) nil nil))))
(dolist (src '("ORDERS .> BUCKET(_[\"cid\"], RECORD(\"cid\", _K, \"n\", COUNT(_))) .> TAKE(2) .> FILTER(_[\"n\"] > 1)"
               "ORDERS .> BUCKET(_[\"cid\"], RECORD(\"cid\", _K, \"n\", COUNT(_))) .> DROP(1) .> FILTER(_[\"n\"] > 1)"
               "ORDERS .> BUCKET(_[\"cid\"], RECORD(\"cid\", _K, \"s\", SUM(_, _[\"amt\"]))) .> SORT_BY(_[\"s\"], \"DESC\") .> FILTER(_[\"s\"] > 5)"))
  (format t "SRC  ~a~%" src)
  (format t "SQL  ~a~%"
          (handler-case (as-statement (translate (sel:compile-source src) "mariadb" (bnd) nil) :inline)
            (error (e) (format nil "ERR ~a" e))))
  (format t "PLAN ~a~%~%"
          (handler-case
              (let ((plan (plan-hybrid (sel:compile-source src) "mariadb" (bnd) nil)))
                (format nil "~a :: ~a"
                        (cond ((hybrid-plan-pure-sql-p plan) "pure_sql") ((hybrid-plan-pure-memory-p plan) "pure_memory") (t "hybrid"))
                        (if (hybrid-plan-sql-statement plan) (as-statement (hybrid-plan-sql-statement plan) :inline) "-")))
            (error (e) (format nil "ERR ~a" e)))))
```

**`probe.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main() {
  const Bindings bindings({{"ORDERS", Binding::relation("orders", std::nullopt,
      {{"CID", Binding::column("cid", std::nullopt, SqlKind::Text)}, {"AMT", Binding::column("amt", std::nullopt, SqlKind::Num)}})}});
  const char* progs[] = {
    "ORDERS .> BUCKET(_[\"cid\"], RECORD(\"cid\", _K, \"n\", COUNT(_))) .> TAKE(2) .> FILTER(_[\"n\"] > 1)",
    "ORDERS .> BUCKET(_[\"cid\"], RECORD(\"cid\", _K, \"n\", COUNT(_))) .> DROP(1) .> FILTER(_[\"n\"] > 1)",
    "ORDERS .> BUCKET(_[\"cid\"], RECORD(\"cid\", _K, \"s\", SUM(_, _[\"amt\"]))) .> SORT_BY(_[\"s\"], \"DESC\") .> FILTER(_[\"s\"] > 5)",
  };
  for (const char* src : progs) {
    std::cout << "SRC  " << src << "\n";
    const sel::Program p = sel::compile(src);
    try { std::cout << "SQL  " << Sql::translate_statement(p, "mariadb", bindings).as_statement() << "\n"; }
    catch (const std::exception& e) { std::cout << "SQL  ERR " << e.what() << "\n"; }
    try {
      auto plan = Sql::plan_hybrid(p, "mariadb", bindings);
      std::cout << "PLAN " << (plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid") << " :: "
                << (plan.sql_statement ? plan.sql_statement->as_statement() : std::string("-")) << "\n\n";
    } catch (const std::exception& e) { std::cout << "PLAN ERR " << e.what() << "\n\n"; }
  }
}
```

**`lanes.py`**

```python
import sqlite3
from sel import compile as sel_compile
from sel.value import Value
from sel.sql import Binding, Sql
bindings = {'ORDERS': Binding.relation('orders', None, {'CID': Binding.column('cid', None, 'TEXT'), 'AMT': Binding.column('amt', None, 'NUM')}, None, None)}
rows = [{'cid': 'A', 'amt': '1'}, {'cid': 'A', 'amt': '2'}, {'cid': 'B', 'amt': '3'}, {'cid': 'C', 'amt': '4'}, {'cid': 'C', 'amt': '5'}]
db = sqlite3.connect(':memory:')
db.execute('create table orders(cid, amt)')
db.executemany('insert into orders values (?, ?)', [(r['cid'], r['amt']) for r in rows])
def runner(sql, params):
    cur = db.execute(sql, [p.as_text() for p in params])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]
for src in ['ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)',
            'ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)']:
    program = sel_compile(src)
    print('SRC     ', src)
    print('memory  ', program.run({'ORDERS': rows}).dump())
    plan = Sql.plan_hybrid(program, 'sqlite', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    got = Sql.execute_hybrid(plan, runner, {'ORDERS': rows})
    got = got if isinstance(got, Value) else Value.from_native(got)
    print('hybrid  ', kind, got.dump())
    print()
```

**`$SCRATCH/verify-H/probe2.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const bindings = { ORDERS: Binding.relation('orders', null, { CID: Binding.column('cid', null, 'TEXT'), AMT: Binding.column('amt', null, 'NUM') }, null, null) };
const src = 'ORDERS .> BUCKET(_["cid"]) .> TAKE(2) .> FILTER(COUNT(_) > 1)';
const p = compile(src);
let s; try { s = Sql.translate(p, 'mariadb', bindings, {}).asStatement('inline'); } catch (e) { s = `${e.code} ${e.message}`; }
let cls; try { const pl = Sql.planHybrid(p, 'mariadb', bindings, {}); cls = (pl.pureSql ? 'pure_sql' : pl.pureMemory ? 'pure_memory' : 'hybrid') + ' :: ' + (pl.sqlStatement ? pl.sqlStatement.asStatement('inline') : '-'); } catch (e) { cls = `${e.code} ${e.message}`; }
console.log('SQL  ' + s); console.log('PLAN ' + cls);
```

**`$SCRATCH/verify-H/lanes2.py`**

```python
import sqlite3
from sel import compile as sel_compile
from sel.value import Value
from sel.sql import Binding, Sql
bindings = {'ORDERS': Binding.relation('orders', None, {'CID': Binding.column('cid', None, 'TEXT'), 'AMT': Binding.column('amt', None, 'NUM')}, None, None)}
rows = [{'cid': 'A', 'amt': '1'}, {'cid': 'A', 'amt': '2'}, {'cid': 'B', 'amt': '3'}]
db = sqlite3.connect(':memory:'); db.execute('create table orders(cid, amt)')
db.executemany('insert into orders values (?, ?)', [(r['cid'], r['amt']) for r in rows])
def runner(sql, params):
    cur = db.execute(sql, [p.as_text() for p in params]); cols = [c[0] for c in cur.description]
    return [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]
src = 'ORDERS .> FILTER(_["cid"] $== "B")'
program = sel_compile(src)
print('memory  ', program.run({'ORDERS': rows}).dump())
plan = Sql.plan_hybrid(program, 'sqlite', bindings)
got = Sql.execute_hybrid(plan, runner, {'ORDERS': rows}); got = got if isinstance(got, Value) else Value.from_native(got)
print('hybrid  ', 'pure_sql' if plan.pure_sql else 'hybrid', got.dump())
```

### Commands run and their output

Show FILTER-step wrap predicate at parent commit in each host

```bash
for f in lisp/src/sql/translator.lisp js/src/sql/translator.mjs php/src/Sql/Translator.php python/sel/sql/translator.py cpp/sel_sql_translator.cpp; do echo "== $f"; git show 8fe0e3a:$f | grep -n -A6 -E '\(\(equal sname "FILTER"\)|case .FILTER.:|if name == .FILTER.|name == "FILTER"\) \{' | head -30; done
```

```
== lisp/src/sql/translator.lisp
1867:                ((equal sname "FILTER")
1868-                 (when (or (relational-plan-limit plan)
1869-                           (relational-plan-offset plan)
1870-                           (and (null (relational-plan-group-by plan))
1871-                                (relational-plan-projections plan))
1872-                           (relational-plan-order-by plan))
1873-                   (setf plan (wrap-plan-as-derived-table plan)))
== js/src/sql/translator.mjs
1781:        case 'FILTER': {
1782-          plan = this.ensureDerived(plan, (candidate) =>
1783-            candidate.groupBy === null && Boolean(candidate.projections || candidate.selectCols
1784-              || candidate.limit !== null || candidate.offset !== null
1785-              || candidate.orderBy.length || candidate.distinct));
1786-          let binder;
1787-          let pred;
== php/src/Sql/Translator.php
2486:                case 'FILTER':
2487-                    $plan = $this->ensureDerived($plan,
2488-                        $plan->groupBy === null
2489-                        && ($plan->projections !== null || $plan->selectCols !== null
2490-                            || $plan->limit !== null || $plan->offset !== null
2491-                            || $plan->orderBy !== [] || $plan->distinct));
2492-                    if (count($args) === 2) {
== python/sel/sql/translator.py
1733:            if name == 'FILTER':
1734-                if len(args) == 2:
1735-                    binder = '_'
1736-                    pred = args[1]
1737-                elif len(args) == 3:
1738-                    if not _constants.is_binder_name(args[1]):
1739-                        refuse('E_SQL_SHAPE', 'the binder of FILTER must be a bare name', args[1].pos)
--
2041:            if name == 'FILTER':
2042-                plan = self._ensure_derived(plan, lambda candidate:
2043-                    candidate.group_by is None and bool(
2044-                        candidate.projections is not None or candidate.select_cols is not None
2045-                        or candidate.limit is not None or candidate.offset is not None
2046-                        or candidate.order_by or candidate.distinct))
2047-                if len(args) == 2:
== cpp/sel_sql_translator.cpp
1782:    if (name == "FILTER") {
1783-      // Refuses FIRST if the FILTER's binder is not a bare name, then recurses
1784-      // so nested FILTERs conjoin -- innermost first.
1785-      const AggShape f = agg_shape(*src);
1786-      Source inner = classify(src->kids()[0]);
1787-      inner.filters.push_back({f.binder, f.body});
1788-      return inner;
--
2042:  if (name == "MAP" || name == "FILTER") {
2043-    refuse("E_SQL_SHAPE",
2044-           name + " yields a list, and a SQL expression is a scalar; it can only "
2045-                  "be the thing another aggregate iterates",
2046-           n.pos());
2047-  }
2048-  if (name == "JOIN") return join_aggregate(n);
--
2313:    if (name == "FILTER") {
2314-      const bool need_derived =
2315-          !plan.group_by.has_value() &&
2316-          (plan.projections.has_value() || plan.select_cols.has_value() ||
2317-           plan.distinct || plan.limit.has_value() || plan.offset.has_value() ||
2318-           !plan.order_by.empty());
2319-      plan = ensure_derived(std::move(plan), need_derived);
```

Run JS probe for TAKE/DROP/SORT then FILTER after BUCKET

```bash
mkdir -p $SCRATCH/verify-H && cd $SCRATCH/verify-H && # (file written, see "Reproduction scripts" above)
node probe.mjs
```

```
SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)
SQL  SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2
PLAN pure_sql :: SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2

SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)
SQL  SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 18446744073709551615 OFFSET 1
PLAN pure_sql :: SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 18446744073709551615 OFFSET 1

SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "s", SUM(_, _["amt"]))) .> SORT_BY(_["s"], "DESC") .> FILTER(_["s"] > 5)
SQL  SELECT `cid` AS `cid`, COALESCE(SUM(`amt`), 0) AS `s` FROM `orders` GROUP BY `cid` HAVING (COALESCE(SUM(`amt`), 0) > 5) ORDER BY COALESCE(SUM(`amt`), 0) DESC
PLAN pure_sql :: SELECT `cid` AS `cid`, COALESCE(SUM(`amt`), 0) AS `s` FROM `orders` GROUP BY `cid` HAVING (COALESCE(SUM(`amt`), 0) > 5) ORDER BY COALESCE(SUM(`amt`), 0) DESC

SRC  ORDERS .> BUCKET(_["cid"]) .> TAKE(2) .> FILTER(_["cid"] != "A")
SQL  E_SQL_INVALID SEL rejects this expression (E_NOT_NUM: not a number: "A"), so there is nothing to translate; a database would answer something rather than fail
PLAN pure_memory :: -
```

Run Python probe

```bash
cd $SCRATCH/verify-H && # (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel && PYTHONPATH=$PWD/python python3 $SCRATCH/verify-H/probe.py
```

```
SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)
SQL  SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2
PLAN pure_sql :: SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2

SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)
SQL  SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 18446744073709551615 OFFSET 1
PLAN pure_sql :: SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 18446744073709551615 OFFSET 1

SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "s", SUM(_, _["amt"]))) .> SORT_BY(_["s"], "DESC") .> FILTER(_["s"] > 5)
SQL  SELECT `cid` AS `cid`, COALESCE(SUM(`amt`), 0) AS `s` FROM `orders` GROUP BY `cid` HAVING (COALESCE(SUM(`amt`), 0) > 5) ORDER BY COALESCE(SUM(`amt`), 0) DESC
PLAN pure_sql :: SELECT `cid` AS `cid`, COALESCE(SUM(`amt`), 0) AS `s` FROM `orders` GROUP BY `cid` HAVING (COALESCE(SUM(`amt`), 0) > 5) ORDER BY COALESCE(SUM(`amt`), 0) DESC
```

Run PHP probe

```bash
cd $SCRATCH/verify-H && # (file written, see "Reproduction scripts" above)
php probe.php
```

```
SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)
SQL  SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2
PLAN pure_sql :: SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2

SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)
SQL  SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 18446744073709551615 OFFSET 1
PLAN pure_sql :: SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 18446744073709551615 OFFSET 1

SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "s", SUM(_, _["amt"]))) .> SORT_BY(_["s"], "DESC") .> FILTER(_["s"] > 5)
SQL  SELECT `cid` AS `cid`, COALESCE(SUM(`amt`), 0) AS `s` FROM `orders` GROUP BY `cid` HAVING (COALESCE(SUM(`amt`), 0) > 5) ORDER BY COALESCE(SUM(`amt`), 0) DESC
PLAN pure_sql :: SELECT `cid` AS `cid`, COALESCE(SUM(`amt`), 0) AS `s` FROM `orders` GROUP BY `cid` HAVING (COALESCE(SUM(`amt`), 0) > 5) ORDER BY COALESCE(SUM(`amt`), 0) DESC
```

Inspect Lisp runner API usage

```bash
cat lisp/bin/sqlt | head -20; grep -n "translate-statement\|plan-hybrid\|make-relation-binding\|binding-relation\|relation-binding\|pure-sql\|sql-statement\|as-statement" lisp/bin/sqlt.lisp | head -20; grep -n -m2 "relation" lisp/bin/case-data.lisp
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
98:  (cond ((hybrid-plan-pure-sql-p plan) "pure_sql")
123:                  plan (plan-hybrid program dialect binds (list :strict (getf c :strict))))))
160:       (when (hybrid-plan-sql-statement plan)
168:       (unless (hybrid-plan-sql-statement plan)
172:       (let ((sql (as-statement (hybrid-plan-sql-statement plan) mode)))
182:                        (hybrid-plan-hybrid-p plan))
216:                            ((equal as "statement") (as-statement frag mode))
270:    (let ((debug-render (cond ((equal as "statement") (as-statement frag :debug))
```

Grep Lisp case data as text

```bash
file lisp/bin/case-data.lisp; grep -a -c "BUCKET" lisp/bin/case-data.lisp; grep -a -n -m1 -A12 'DROP(5)' lisp/bin/case-data.lisp
```

```
lisp/bin/case-data.lisp: data
23
8219:   :source "ITEMS .> DROP(5)"
8220-   :expect "SELECT * FROM `items` LIMIT 18446744073709551615 OFFSET 5"
8221-   :error nil
8222-   :throws nil
8223-   :params nil
8224-   :as "statement"
8225-   :mode nil
8226-   :strict nil
8227-   :plan nil
8228-   :tables :none
8229-   :register nil
8230-   :bindings (lambda () (list (cons "ITEMS" (binding-relation "items" nil (list ) nil nil)))))
8231-  (list
```

Run Lisp probe

```bash
cd $SCRATCH/verify-H && # (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $SCRATCH/verify-H/probe.lisp 2>&1 | grep -v "^;"
```

```
SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)
SQL  SELECT `_sub1`.* FROM (SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`n` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`n` AS DECIMAL(65,10)) ELSE NULL END > 1)
PLAN pure_sql :: SELECT `_sub1`.* FROM (SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` LIMIT 2) `_sub1` WHERE (CASE WHEN (`_sub1`.`n` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`n` AS DECIMAL(65,10)) ELSE NULL END > 1)

SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)
SQL  SELECT `_sub1`.* FROM (SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` LIMIT 18446744073709551615 OFFSET 1) `_sub1` WHERE (CASE WHEN (`_sub1`.`n` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`n` AS DECIMAL(65,10)) ELSE NULL END > 1)
PLAN pure_sql :: SELECT `_sub1`.* FROM (SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` LIMIT 18446744073709551615 OFFSET 1) `_sub1` WHERE (CASE WHEN (`_sub1`.`n` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`n` AS DECIMAL(65,10)) ELSE NULL END > 1)

SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "s", SUM(_, _["amt"]))) .> SORT_BY(_["s"], "DESC") .> FILTER(_["s"] > 5)
SQL  SELECT `_sub1`.* FROM (SELECT `cid` AS `cid`, COALESCE(SUM(`amt`), 0) AS `s` FROM `orders` GROUP BY `cid` ORDER BY COALESCE(SUM(`amt`), 0) DESC) `_sub1` WHERE (CASE WHEN (`_sub1`.`s` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`s` AS DECIMAL(65,10)) ELSE NULL END > 5)
PLAN pure_sql :: SELECT `cid` AS `cid`, COALESCE(SUM(`amt`), 0) AS `s` FROM `orders` GROUP BY `cid` HAVING (COALESCE(SUM(`amt`), 0) > 5) ORDER BY COALESCE(SUM(`amt`), 0) DESC
```

Compile and run C++ probe

```bash
cd $SCRATCH/verify-H && # (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel/cpp && g++ -std=c++23 -O1 -w -o $SCRATCH/verify-H/probe $SCRATCH/verify-H/probe.cpp build/sel_sql*.o build/sel.o && $SCRATCH/verify-H/probe
```

```
SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)
SQL  SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2
PLAN pure_sql :: SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2

SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)
SQL  SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 18446744073709551615 OFFSET 1
PLAN pure_sql :: SELECT `cid` AS `cid`, COUNT(*) AS `n` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 18446744073709551615 OFFSET 1

SRC  ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "s", SUM(_, _["amt"]))) .> SORT_BY(_["s"], "DESC") .> FILTER(_["s"] > 5)
SQL  SELECT `cid` AS `cid`, COALESCE(SUM(`amt`), 0) AS `s` FROM `orders` GROUP BY `cid` HAVING (COALESCE(SUM(`amt`), 0) > 5) ORDER BY COALESCE(SUM(`amt`), 0) DESC
PLAN pure_sql :: SELECT `cid` AS `cid`, COALESCE(SUM(`amt`), 0) AS `s` FROM `orders` GROUP BY `cid` HAVING (COALESCE(SUM(`amt`), 0) > 5) ORDER BY COALESCE(SUM(`amt`), 0) DESC
```

Evaluate the TAKE-then-FILTER program in memory in all five hosts

```bash
P='ORDERS = [RECORD("cid","A"), RECORD("cid","A"), RECORD("cid","B"), RECORD("cid","C"), RECORD("cid","C")], ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)'; echo "js:  $(node js/bin/sel.mjs -e "$P")"; echo "php: $(php php/bin/sel -e "$P")"; echo "cpp: $(cpp/build/sel -e "$P")"; echo "lsp: $(lisp/bin/sel -e "$P" 2>&1 | grep -v '^;')"; echo "py:  $(PYTHONPATH=$PWD/python python3 -m sel -e "$P")"
```

```
E_SYNTAX at line 1 column 10: unexpected "["
js:  
E_SYNTAX at line 1 column 10: unexpected "["
php: 
E_SYNTAX at line 1 column 10: unexpected "["
cpp: 
lsp: E_SYNTAX at line 1 column 10: unexpected "["
E_SYNTAX at line 1 column 10: unexpected "["
py:
```

Evaluate the TAKE-then-FILTER program in memory in all five hosts

```bash
P='LIST(RECORD("cid","A"), RECORD("cid","A"), RECORD("cid","B"), RECORD("cid","C"), RECORD("cid","C")) .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)'; echo "js:  $(node js/bin/sel.mjs -e "$P")"; echo "php: $(php php/bin/sel -e "$P")"; echo "cpp: $(cpp/build/sel -e "$P")"; echo "lsp: $(lisp/bin/sel -e "$P" 2>&1 | grep -v '^;')"; echo "py:  $(PYTHONPATH=$PWD/python python3 -m sel -e "$P")"
```

```
js:  -{"1"=-{"cid"=t"A", "n"=t"2"}}
php: -{"1"=-{"cid"=t"A", "n"=t"2"}}
cpp: -{"1"=-{"cid"=t"A", "n"=t"2"}}
lsp: -{"1"=-{"cid"=t"A", "n"=t"2"}}
py:  -{"1"=-{"cid"=t"A", "n"=t"2"}}
```

Get sqlite-dialect SQL from JS and Lisp

```bash
sed -i "s/'mariadb'/'sqlite'/g" $SCRATCH/verify-H/probe.mjs && node $SCRATCH/verify-H/probe.mjs 2>&1 | grep '^SQL' | head -3; sed -i 's/"mariadb"/"sqlite"/g' $SCRATCH/verify-H/probe.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $SCRATCH/verify-H/probe.lisp 2>&1 | grep '^SQL' | head -3
```

```
SQL  SELECT "cid" AS "cid", COUNT(*) AS "n" FROM "orders" GROUP BY "cid" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 2
SQL  SELECT "cid" AS "cid", COUNT(*) AS "n" FROM "orders" GROUP BY "cid" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT -1 OFFSET 1
SQL  SELECT "cid" AS "cid", COALESCE(SUM("amt"), 0) AS "s" FROM "orders" GROUP BY "cid" HAVING (CAST(COALESCE(SUM("amt"), 0) AS NUMERIC) > CAST('5' AS NUMERIC)) ORDER BY COALESCE(SUM("amt"), 0) DESC
SQL  ERR E_SQL_UNSUPPORTED at 1:84: dialect sqlite has no way to ask whether a value is a number, so an operand it has not been told is one cannot be read as one here; declare the binding NUM if the column really is numeric
SQL  ERR E_SQL_UNSUPPORTED at 1:84: dialect sqlite has no way to ask whether a value is a number, so an operand it has not been told is one cannot be read as one here; declare the binding NUM if the column really is numeric
SQL  ERR E_SQL_UNSUPPORTED at 1:108: dialect sqlite has no way to ask whether a value is a number, so an operand it has not been told is one cannot be read as one here; declare the binding NUM if the column really is numeric
```

Compare sqlite-dialect plan classification in Lisp vs Python

```bash
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $SCRATCH/verify-H/probe.lisp 2>&1 | grep '^PLAN'; sed -i "s/'mariadb'/'sqlite'/g" $SCRATCH/verify-H/probe.py; PYTHONPATH=$PWD/python python3 $SCRATCH/verify-H/probe.py | grep '^PLAN'
```

```
PLAN hybrid :: SELECT "cid" AS "cid", COUNT(*) AS "n" FROM "orders" GROUP BY "cid" LIMIT 2
PLAN hybrid :: SELECT "cid" AS "cid", COUNT(*) AS "n" FROM "orders" GROUP BY "cid" LIMIT -1 OFFSET 1
PLAN pure_sql :: SELECT "cid" AS "cid", COALESCE(SUM("amt"), 0) AS "s" FROM "orders" GROUP BY "cid" HAVING (CAST(COALESCE(SUM("amt"), 0) AS NUMERIC) > CAST('5' AS NUMERIC)) ORDER BY COALESCE(SUM("amt"), 0) DESC
PLAN pure_sql :: SELECT "cid" AS "cid", COUNT(*) AS "n" FROM "orders" GROUP BY "cid" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 2
PLAN pure_sql :: SELECT "cid" AS "cid", COUNT(*) AS "n" FROM "orders" GROUP BY "cid" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT -1 OFFSET 1
PLAN pure_sql :: SELECT "cid" AS "cid", COALESCE(SUM("amt"), 0) AS "s" FROM "orders" GROUP BY "cid" HAVING (CAST(COALESCE(SUM("amt"), 0) AS NUMERIC) > CAST('5' AS NUMERIC)) ORDER BY COALESCE(SUM("amt"), 0) DESC
```

Execute both SQL shapes against SQLite and compare with SEL's in-memory answer

```bash
cd $SCRATCH/verify-H && python3 - <<'EOF'
import sqlite3
c = sqlite3.connect(':memory:')
c.execute('create table orders(cid text, amt numeric)')
c.executemany('insert into orders values (?,?)', [('A',1),('A',2),('B',3),('C',4),('C',5)])
four = 'SELECT "cid" AS "cid", COUNT(*) AS "n" FROM "orders" GROUP BY "cid" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST(\'1\' AS NUMERIC)) LIMIT 2'
lisp_prefix = 'SELECT "cid" AS "cid", COUNT(*) AS "n" FROM "orders" GROUP BY "cid" LIMIT 2'
print('four hosts pure_sql :', c.execute(four).fetchall())
rows = c.execute(lisp_prefix).fetchall()
print('lisp hybrid prefix  :', rows, '-> after in-memory FILTER n>1:', [r for r in rows if r[1] > 1])
EOF
```

```
four hosts pure_sql : [('A', 2), ('C', 2)]
lisp hybrid prefix  : [('A', 2), ('B', 1)] -> after in-memory FILTER n>1: [('A', 2)]
```

Compare Python in-memory lane vs executed plan lane on SQLite

```bash
cd $SCRATCH/verify-H && # (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel && PYTHONPATH=$PWD/python python3 $SCRATCH/verify-H/lanes.py
```

```
SRC      ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(2) .> FILTER(_["n"] > 1)
memory   -{"1"=-{"cid"=t"A", "n"=t"2"}}
hybrid   pure_sql -{"1"=-{"cid"=t"A", "n"=t"2"}, "2"=-{"cid"=t"C", "n"=t"2"}}

SRC      ORDERS .> BUCKET(_["cid"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)
memory   -{"2"=-{"cid"=t"C", "n"=t"2"}}
hybrid   pure_sql -{"1"=-{"cid"=t"C", "n"=t"2"}}
```

Probe the bare-bucket variant in JS

```bash
# (file written, see "Reproduction scripts" above)
node $SCRATCH/verify-H/probe2.mjs
P='LIST(RECORD("cid","A"), RECORD("cid","A"), RECORD("cid","B"), RECORD("cid","C"), RECORD("cid","C")) .> BUCKET(_["cid"]) .> TAKE(2) .> FILTER(COUNT(_) > 1)'; echo "mem js: $(node js/bin/sel.mjs -e "$P")"
```

```
SQL  SELECT `cid` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2
PLAN pure_sql :: SELECT `cid` FROM `orders` GROUP BY `cid` HAVING (COUNT(*) > 1) LIMIT 2
mem js: -{"1"=-{"1"=-{"cid"=t"A"}, "2"=-{"cid"=t"A"}}}
```

Check DROP key numbering in memory

```bash
node js/bin/sel.mjs -e 'LIST(RECORD("cid","A"), RECORD("cid","C")) .> DROP(1)'; node js/bin/sel.mjs -e '(5,6,7) .> DROP(1)'; grep -n -A6 "DROP(" conformance/*.selt | grep -A6 "expect\|source" | head -20
```

```
-{"1"=-{"cid"=t"C"}}
-{"1"=t"6", "2"=t"7"}
conformance/15-relational.selt-121---- expect
conformance/15-relational.selt-122-tree -{"1"=t"3", "2"=t"4"}
conformance/15-relational.selt-123-===
conformance/15-relational.selt-124-
conformance/15-relational.selt-125-### name: rel.drop.exact
conformance/15-relational.selt-126---- source
conformance/15-relational.selt:127:LIST(1, 2) .> DROP(2)
conformance/15-relational.selt-128---- expect
conformance/15-relational.selt-129-none
conformance/15-relational.selt-130-===
conformance/15-relational.selt-131-
conformance/15-relational.selt-132-### name: rel.drop.excess
conformance/15-relational.selt-133---- source
conformance/15-relational.selt:134:LIST(1, 2) .> DROP(5)
conformance/15-relational.selt-135---- expect
conformance/15-relational.selt-136-none
conformance/15-relational.selt-137-===
conformance/15-relational.selt-138-
conformance/15-relational.selt-139-### name: rel.drop.zero
conformance/15-relational.selt-140---- source
```

Check FILTER key numbering across lanes (side observation)

```bash
# (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $SCRATCH/verify-H/lanes2.py; grep -n -i "renumber\|keys are\|key.*preserv" docs/SQL-TRANSLATION.md spec/SPEC.md | head -5
```

```
memory   -{"3"=-{"cid"=t"B", "amt"=t"3"}}
hybrid   pure_sql -{"1"=-{"cid"=t"B", "amt"=t"3"}}
docs/SQL-TRANSLATION.md:681:Indexing works positionally: `VALUES[2]` is `` `x`.`b` `` (keys are `"1"`, `"2"`,
docs/SQL-TRANSLATION.md:873:   `R["a"] = 1; R["b"] = 2` gives `_K` of `"a"` and `"b"`; a renumbered `list`
docs/SQL-TRANSLATION.md:897:   contributes its children, and keys are renumbered from 1, so that stage 2
docs/SQL-TRANSLATION.md:1280:   renumbering (§9), but it is an invariant worth pinning with a case before
docs/SQL-TRANSLATION.md:1306:keys are `"1"`, `"2"`, … and never a field name — which is how the row oracle's
```

