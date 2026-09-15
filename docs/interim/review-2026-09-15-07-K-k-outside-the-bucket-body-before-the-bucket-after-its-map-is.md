# K. _K outside the bucket body (before the BUCKET, after its MAP) is rendered as the group key where SEL binds the list index

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "The bucket body's scope is the evaluator's".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** mixed · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

In every host, `withRow` binds `_K` to the single GROUP BY key whenever the statement plan has one group key, regardless of which step is being rendered, so a FILTER(_K …) placed before the BUCKET (rendered as WHERE) or after the closing projection/MAP (rendered as HAVING) compares the group key where SEL compares the list index. Reproduced end-to-end on SQLite: `ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")` gives `-{"2"=-{"k"=t"B","n"=t"1"}}` from run() and no rows from the pure_sql plan; `ITEMS .> FILTER(_K $== "2") .> BUCKET(...)` likewise. The misbinding pre-exists the commit (the `BUCKET(k, proj) .> FILTER(_K)` and `FILTER(_K) .> BUCKET` shapes behave identically at 8fe0e3a); the commit's new MAP-fold routes the `BUCKET(k) .> MAP(proj) .> FILTER(_K)` spelling into it as a silent pure_sql plan where before it was refused by translate() and mis-split by the planner. All five hosts agree with each other, so this is a lane divergence (run vs translate/plan), not a host divergence.

## Suggested fix — case first

Bind `_K` to the group key only while the bucket's own body is being rendered (the projection and a HAVING that sits between a bare BUCKET and its MAP), and keep the Binder.none refusal everywhere else: e.g. record on the plan the step index at which grouping closed (or push a dedicated frame from bucketProjection / the group-having renderer) and in withRow use the group key only when rendering those nodes; a FILTER before the BUCKET (WHERE) and a FILTER after the projection (post-projection HAVING) must refuse `_K` with E_SQL_SHAPE at the `_K` node, since SQL has neither a row key nor SEL's renumbered index. Add first, to sql/cases/24-bucket.sqlt: `ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))` expecting E_SQL_SHAPE at the `_K` (1:17), and `ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")` expecting E_SQL_SHAPE at its `_K`; mirror both in 25-hybrid-plans.sqlt as pure_memory plans and add the first shape to python/tests/test_unit.py::test_bucket_plans_answer_what_the_evaluator_answers_on_sqlite. Then regenerate with node tools/gen-sql-cases.mjs and fix all five withRow sites.

## Verifier reasoning

SEL semantics (spec §7.3 + conformance/16-bucket.selt): BUCKET(src,key) yields a map keyed by group key; BUCKET(src,key,proj) and BUCKET(src,key) .> MAP(proj) yield a list renumbered from "1"; FILTER preserves keys. Hence `_K` is the group key only inside the projection body and inside a FILTER between a bare BUCKET and its MAP; before the BUCKET and after the projection `_K` is the list index. The translator's row frame (js/src/sql/translator.mjs:1246-1247, php/src/Sql/Translator.php:1546-1547, python/sel/sql/translator.py:1198-1199, cpp/sel_sql_translator.cpp:1920-1921, lisp/src/sql/translator.lisp:1382-1384) tests only `statementPlan.groupBy.length === 1` and not which step is rendering, and the FILTER routing (js translator.mjs:1875-1878: `if (plan.groupBy !== null) plan.having.push(...) else plan.filters.push(...)`) sends any FILTER before the bucket to WHERE and any FILTER after the projection to HAVING within the same statement, so both render `_K` as the key column. The documented rule (docs/SQL-TRANSLATION.md:1155-1176, sql/errors.md:39) is that `_K` on a relation row is E_SQL_SHAPE; `ITEMS .> FILTER(_K $== "2")` alone is indeed refused at 1:17 in all five hosts, but adding a single-key BUCKET after it silently lifts the refusal and yields wrong rows. The middle shape `BUCKET(k) .> FILTER(_K $== "B") .> MAP(...)` is correct in all hosts (that is the one this commit fixed). Pre-commit JS and Python sources (extracted with git archive into the scratchpad) show shapes 2 and 4 producing the same wrong SQL, so the binding bug is pre-existing; shape 1 was previously E_SQL_SHAPE from translate() and a hybrid plan whose continuation answered `k="2",n="1"` (the split-after-bucket bug the commit fixed), so the commit swapped one wrong answer for another rather than introducing the misbinding. Severity high: results differ between the in-memory lane and both SQL lanes with a pure_sql classification and no refusal, which is the invariant the product is about; not covered by any pinned case (sql/cases/24-bucket.sqlt and 25-hybrid-plans.sqlt have no `_K` FILTER before a bucket or after a projection).

## Verifier evidence

```
Probe scripts in /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-K/ (probe.mjs, probe.py, probe.php, probe.cpp, probe.lisp, probe-old.mjs, old/ = git archive 8fe0e3a js python). Rows: dept A/B/A, v 1/2/3; binding ITEMS -> items i {DEPT text, V num}.

Python, current tree, executing on SQLite (`PYTHONPATH=$PWD/python python3 probe.py`):
SRC ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run      -{"2"=-{"k"=t"B", "n"=t"1"}}
  sql      SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
  executed pure_sql -> -    <-- DIVERGES
SRC ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run -{"2"=-{"k"=t"B", "n"=t"1"}} / executed pure_sql -> -   <-- DIVERGES
SRC ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "B") .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run -{"1"=-{"k"=t"B", "n"=t"1"}} / executed pure_sql -> -{"1"=-{"k"=t"B", "n"=t"1"}}   (correct)
SRC ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))
  run -{"1"=-{"k"=t"B", "n"=t"1"}} / sql ... WHERE (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT)) GROUP BY "i"."dept" / executed pure_sql -> -   <-- DIVERGES
SRC ITEMS .> FILTER(_K $== "2")
  run -{"2"=-{"dept"=t"B", "v"=t"2"}} / sql E_SQL_SHAPE@1:17 / plan pure_memory (correct refusal)

JS (node probe.mjs), PHP (php probe.php), C++ (g++ -std=c++23 probe.cpp against cpp/build/*.o), Lisp (sbcl --load lisp/bin/boot.lisp --eval quickload :sel-lang/sql --load probe.lisp): identical run() dumps, identical translateStatement text and identical pure_sql/pure_memory classification for all five programs (C++ differs only in column qualification because my probe passed nullopt as the column table).

Pre-commit JS (node probe-old.mjs, sources from `git archive 8fe0e3a js`): shape 1 -> translate E_SQL_SHAPE@1:47, plan hybrid `SELECT "i"."dept" FROM "items" "i" GROUP BY "i"."dept"`; shapes 2 and 4 -> the same HAVING/WHERE dept='2' SQL as now; shape 5 -> E_SQL_SHAPE@1:17. Pre-commit Python executed on SQLite: shape 1 hybrid -> -{"2"=-{"k"=t"2","n"=t"1"}} (wrong differently), shapes 2 and 4 -> no rows (same as now).

Code: js/src/sql/translator.mjs:1246-1249 (kBinder = groupBy[0].node when length===1, else Binder.none refusal), :1875-1878 (FILTER -> having when plan.groupBy !== null else filters); php/src/Sql/Translator.php:1546-1547; python/sel/sql/translator.py:1198-1199; cpp/sel_sql_translator.cpp:1920-1924; lisp/src/sql/translator.lisp:1382-1387. Docs: docs/SQL-TRANSLATION.md:1155-1176 and sql/errors.md:39 say `_K` on a relation row is E_SQL_SHAPE. `git diff 8fe0e3a ed16df2 -- js/src/sql/translator.mjs` shows the groupBy[0] `_K` binding lines are unchanged by the commit; the MAP fold (translator.mjs:2004-2009, `plan.bucket === 'open'` -> bucketProjection) is new.
```

## Original review reports (deduplicated into this finding)

### [bucket-translator] _K after the bucket's MAP (or before the BUCKET) is rendered as the group key, while SEL binds it to the list index there

*correctness · medium · hosts: js, php, python, cpp, lisp*

Locations: `js/src/sql/translator.mjs:1245`; `php/src/Sql/Translator.php:1553`; `python/sel/sql/translator.py:1205`; `cpp/sel_sql_translator.cpp:1921`; `lisp/src/sql/translator.lisp:1382`

The row frame binds _K to groupBy[0].node whenever the statement plan has a single group key, regardless of which step is being rendered. Inside the bucket's projection or the HAVING between BUCKET and MAP that is right. In a FILTER after the closing MAP, or a FILTER before the BUCKET, SEL's _K is the element's list index ('1','2',...), so `BUCKET(k) .> MAP(RECORD("cid", _K, ...)) .> FILTER(_K $== "1")` keeps the first projected row in SEL but the group whose key is '1' in SQL. Pre-existing for BUCKET(k, proj) .> FILTER(_K ...); the fold now routes the MAP spelling the same way. Identical in all five hosts.

Reported repro:

```
Scratch 97-probe2.sqlt r06: ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> FILTER(_K $== "1") => ... HAVING (CAST(`dept` AS CHAR) COLLATE utf8mb4_bin = CAST('1' AS CHAR) COLLATE utf8mb4_bin) in all five. probe_sqlite.py: same program, run() = -{"1"=-{"cid"=t"A","n"=t"2"}}, execute_hybrid = - (no rows); with FILTER(_K $== "A"): run() = - , SQL = one row. ORDERS .> FILTER(_K $== "1") .> BUCKET(...) .> MAP(...): run() one row, SQL none.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$SCRATCH/verify-K/probe.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';

const bindings = { ITEMS: Binding.relation('items', 'i', { DEPT: Binding.column('dept', 'i', 'TEXT'), V: Binding.column('v', 'i', 'NUM') }) };
const rows = [{ dept: 'A', v: '1' }, { dept: '1', v: '2' }, { dept: 'A', v: '3' }];
const programs = [
  // my own: key whose value collides with a list index "2"
  'ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")',
  'ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")',
  'ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "1") .> MAP(RECORD("k", _K, "n", COUNT(_)))',
  'ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))',
  'ITEMS .> FILTER(_K $== "2")',
];
for (const src of programs) {
  const p = compile(src);
  let mem; try { mem = p.run({ ITEMS: rows }).dump(); } catch (e) { mem = `${e.code}@${e.line}:${e.col}`; }
  let sql; try { sql = Sql.translate(p, 'sqlite', bindings).sql; } catch (e) { sql = `${e.code}@${e.line}:${e.col} ${e.message}`; }
  let plan; try { const pl = Sql.planHybrid(p, 'sqlite', bindings); plan = (pl.pureSql ? 'pure_sql' : pl.pureMemory ? 'pure_memory' : 'hybrid') + ' | ' + (pl.sqlStatement ? pl.sqlStatement.sql : '-'); } catch (e) { plan = `${e.code}@${e.line}:${e.col} ${e.message}`; }
  console.log('SRC  ', src); console.log('  run ', mem); console.log('  sql ', sql); console.log('  plan', plan);
}
```

**`$S/probe.py`**

```python
import sqlite3, sys
from sel import compile as sel_compile
from sel.value import Value
from sel.sql import Binding, Sql

bindings = {'ITEMS': Binding.relation('items', 'i', {
    'DEPT': Binding.column('dept', 'i', 'TEXT'), 'V': Binding.column('v', 'i', 'NUM')})}
rows = [{'dept': 'A', 'v': '1'}, {'dept': 'B', 'v': '2'}, {'dept': 'A', 'v': '3'}]
db = sqlite3.connect(':memory:')
db.execute('create table items(dept, v)')
db.executemany('insert into items values (?, ?)', [(r['dept'], r['v']) for r in rows])
def runner(sql, params):
    cur = db.execute(sql, [p.as_text() for p in params])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]

programs = [
  'ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")',
  'ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")',
  'ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "B") .> MAP(RECORD("k", _K, "n", COUNT(_)))',
  'ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))',
  'ITEMS .> FILTER(_K $== "2")',
]
for src in programs:
    p = sel_compile(src)
    try: mem = p.run({'ITEMS': rows}).dump()
    except Exception as e: mem = f'{type(e).__name__} {getattr(e,"code",e)}'
    try: sql = Sql.translate_statement(p, 'sqlite', bindings).as_statement()
    except Exception as e: sql = f'{getattr(e,"code",type(e).__name__)}@{getattr(e,"line","?")}:{getattr(e,"col","?")}'
    try:
        plan = Sql.plan_hybrid(p, 'sqlite', bindings)
        kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
        got = Sql.execute_hybrid(plan, runner, {'ITEMS': rows})
        got = got if isinstance(got, Value) else Value.from_native(got)
        ex = f'{kind} -> {got.dump()}'
    except Exception as e: ex = f'{getattr(e,"code",type(e).__name__)} {e}'
    print('SRC ', src); print('  run     ', mem); print('  sql     ', sql); print('  executed', ex, '   <-- DIVERGES' if not ex.endswith(mem) else '')
```

**`$S/probe.php`**

```php
<?php
require_once '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$bindings = ['ITEMS' => Binding::relation('items', 'i', ['DEPT' => Binding::column('dept', 'i', 'TEXT'), 'V' => Binding::column('v', 'i', 'NUM')])];
$rows = [['dept'=>'A','v'=>'1'],['dept'=>'B','v'=>'2'],['dept'=>'A','v'=>'3']];
$programs = [
  'ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")',
  'ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")',
  'ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "B") .> MAP(RECORD("k", _K, "n", COUNT(_)))',
  'ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))',
  'ITEMS .> FILTER(_K $== "2")',
];
foreach ($programs as $src) {
  $p = Sel::compile($src);
  try { $mem = $p->run(['ITEMS' => $rows])->dump(); } catch (\Throwable $e) { $mem = get_class($e).' '.($e->code ?? $e->getMessage()); }
  try { $sql = Sql::translateStatement($p, 'sqlite', $bindings)->asStatement(); } catch (\Throwable $e) { $sql = ($e->code ?? get_class($e))."@{$e->line}:{$e->col}"; }
  try { $pl = Sql::planHybrid($p, 'sqlite', $bindings); $kind = $pl->pureSql ? 'pure_sql' : ($pl->pureMemory ? 'pure_memory' : 'hybrid'); $plan = $kind.' | '.($pl->sqlStatement ? $pl->sqlStatement->asStatement() : '-'); } catch (\Throwable $e) { $plan = get_class($e).' '.$e->getMessage(); }
  echo "SRC  $src\n  run  $mem\n  sql  $sql\n  plan $plan\n";
}
```

**`$S/probe.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <iostream>
using namespace sel::sql;
int main() {
  const Bindings bindings({{"ITEMS", Binding::relation("items", "i",
      {{"DEPT", Binding::column("dept", std::nullopt, SqlKind::Text)},
       {"V", Binding::column("v", std::nullopt, SqlKind::Num)}})}});
  sel::Value rows = sel::Value::list({
      sel::Value::record({"dept","v"}, {sel::Value::text("A"), sel::Value::text("1")}),
      sel::Value::record({"dept","v"}, {sel::Value::text("B"), sel::Value::text("2")}),
      sel::Value::record({"dept","v"}, {sel::Value::text("A"), sel::Value::text("3")})});
  const char* programs[] = {
    "ITEMS .> BUCKET(_[\"dept\"]) .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_))) .> FILTER(_K $== \"2\")",
    "ITEMS .> BUCKET(_[\"dept\"], RECORD(\"k\", _K, \"n\", COUNT(_))) .> FILTER(_K $== \"2\")",
    "ITEMS .> BUCKET(_[\"dept\"]) .> FILTER(_K $== \"B\") .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_)))",
    "ITEMS .> FILTER(_K $== \"2\") .> BUCKET(_[\"dept\"], RECORD(\"k\", _K, \"n\", COUNT(_)))",
    "ITEMS .> FILTER(_K $== \"2\")",
  };
  for (const char* src : programs) {
    sel::Program p = sel::compile(src);
    std::string mem, sql, plan;
    try { sel::Value ctx = sel::Value::none(); ctx.set("ITEMS", rows.clone()); mem = p.run(ctx).dump(); }
    catch (const sel::SelError& e) { mem = e.code(); }
    try { sql = Sql::translate_statement(p, "sqlite", bindings).as_statement(); }
    catch (const sel::SelError& e) { sql = e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
    try { HybridPlan pl = Sql::plan_hybrid(p, "sqlite", bindings);
      plan = std::string(pl.pure_sql ? "pure_sql" : pl.pure_memory ? "pure_memory" : "hybrid") + " | " + (pl.sql_statement ? pl.sql_statement->as_statement() : "-"); }
    catch (const sel::SelError& e) { plan = e.code(); }
    std::cout << "SRC  " << src << "\n  run  " << mem << "\n  sql  " << sql << "\n  plan " << plan << "\n";
  }
}
```

**`$S/probe.lisp`**

```lisp
(let* ((bindings (list (cons "ITEMS" (sel.sql:binding-relation "items" "i"
                   (list (cons "DEPT" (sel.sql:binding-column "dept" "i" :text))
                         (cons "V" (sel.sql:binding-column "v" "i" :num)))))))
       (rows-src "LIST(RECORD('dept','A','v','1'), RECORD('dept','B','v','2'), RECORD('dept','A','v','3'))"))
  (dolist (src '("ITEMS .> BUCKET(_[\"dept\"]) .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_))) .> FILTER(_K $== \"2\")"
                 "ITEMS .> BUCKET(_[\"dept\"], RECORD(\"k\", _K, \"n\", COUNT(_))) .> FILTER(_K $== \"2\")"
                 "ITEMS .> BUCKET(_[\"dept\"]) .> FILTER(_K $== \"B\") .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_)))"
                 "ITEMS .> FILTER(_K $== \"2\") .> BUCKET(_[\"dept\"], RECORD(\"k\", _K, \"n\", COUNT(_)))"
                 "ITEMS .> FILTER(_K $== \"2\")"))
    (let* ((p (sel:compile-source src))
           (ctx (sel:make-none))
           (mem (progn (sel:value-set ctx "ITEMS" (sel:evaluate rows-src))
                       (handler-case (sel:value-dump (sel:run p ctx))
                         (sel:sel-error (e) (sel:sel-error-code e)))))
           (sql (handler-case (sel.sql:as-statement (sel.sql:translate-statement p "sqlite" bindings))
                  (sel.sql:sql-error (e) (format nil "~a@~a:~a" (sel.sql:sql-error-code e) (sel.sql:sql-error-line e) (sel.sql:sql-error-col e)))))
           (plan (handler-case
                     (let ((pl (sel.sql:plan-hybrid p "sqlite" bindings)))
                       (format nil "~a | ~a"
                               (cond ((sel.sql:hybrid-plan-pure-sql-p pl) "pure_sql")
                                     ((sel.sql:hybrid-plan-pure-memory-p pl) "pure_memory")
                                     (t "hybrid"))
                               (if (sel.sql:hybrid-plan-sql-statement pl)
                                   (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement pl)) "-")))
                   (sel.sql:sql-error (e) (sel.sql:sql-error-code e)))))
      (format t "SRC  ~a~%  run  ~a~%  sql  ~a~%  plan ~a~%" src mem sql plan))))
```

### Commands run and their output

Read bucket conformance cases

```bash
cat conformance/16-bucket.selt
```

```
% In-memory relational operator: BUCKET

### name: rel.bucket.empty
--- source
LIST() .> BUCKET(_["cat"])
--- expect
none
===

### name: rel.bucket.null
--- source
BUCKET(NULL, _["cat"])
--- expect
none
===

### name: rel.bucket.map-keys
--- source
LIST(RECORD("cat", "A", "v", 1), RECORD("cat", "B", "v", 2), RECORD("cat", "A", "v", 3)) .> BUCKET(_["cat"])
--- expect
tree -{"A"=-{"1"=-{"cat"=t"A", "v"=t"1"}, "2"=-{"cat"=t"A", "v"=t"3"}}, "B"=-{"1"=-{"cat"=t"B", "v"=t"2"}}}
===

### name: rel.bucket.count
--- source
LIST(RECORD("cat", "A", "v", 1), RECORD("cat", "B", "v", 2), RECORD("cat", "A", "v", 3)) .> BUCKET(_["cat"], RECORD("cat", _K, "count", COUNT(_)))
--- expect
tree -{"1"=-{"cat"=t"A", "count"=t"2"}, "2"=-{"cat"=t"B", "count"=t"1"}}
===

### name: rel.bucket.sum
--- source
LIST(RECORD("cat", "A", "v", 10), RECORD("cat", "B", "v", 20), RECORD("cat", "A", "v", 30)) .> BUCKET(_["cat"], RECORD("cat", _K, "total", SUM(_, _["v"])))
--- expect
tree -{"1"=-{"cat"=t"A", "total"=t"40"}, "2"=-{"cat"=t"B", "total"=t"20"}}
===

### name: rel.bucket.custom-binder
--- source
LIST(RECORD("cat", "A", "v", 10), RECORD("cat", "B", "v", 20)) .> BUCKET(R, R["cat"], RECORD("c", _K, "t", SUM(R, x, x["v"])))
--- expect
tree -{"1"=-{"c"=t"A", "t"=t"10"}, "2"=-{"c"=t"B", "t"=t"20"}}
===

### name: rel.bucket.pipeline.filter-having
--- source
LIST(RECORD("cat", "A", "v", 10), RECORD("cat", "B", "v", 50), RECORD("cat", "A", "v", 30), RECORD("cat", "C", "v", 5)) .> FILTER(_["v"] >= 10) .> BUCKET(_["cat"], RECORD("cat", _K, "total", SUM(_, _["v"]))) .> FILTER(_["total"] >= 40) .> SORT_BY(_["total"], "DESC")
--- expect
tree -{"1"=-{"cat"=t"B", "total"=t"50"}, "2"=-{"cat"=t"A", "total"=t"40"}}
===

### name: rel.bucket.numeric-key
--- source
LIST(RECORD("dept", 10, "v", "x"), RECORD("dept", 20, "v", "y"), RECORD("dept", 10, "v", "z")) .> BUCKET(_["dept"], RECORD("dept", _K, "count", COUNT(_)))
--- expect
tree -{"1"=-{"dept"=t"10", "count"=t"2"}, "2"=-{"dept"=t"20", "count"=t"1"}}
===
```

See how hybrid plans are executed in JS and the Sql API names

```bash
mkdir -p $SCRATCH/verify-K && cd /home/nathan/workspaces/nth-share/sel && sed -n 200,260p tools/check-js-optimizer.mjs; grep -n "executeHybrid\|static translate\|static translateStatement\|static planHybrid" js/src/sql/*.mjs | head
```

```
const plan = Sql.planHybrid(program, 'postgresql', orders);
  const got = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  check(got === kind, `${source}: expected a ${kind} plan, got ${got}`);
  const inMemory = failure(() => program.run({ ORDERS: rows }));
  const executed = failure(() => Sql.executeHybrid(plan, runner, { ORDERS: rows }));
  check(inMemory === want, `${source}: run() reports ${inMemory}, want ${want}`);
  check(executed === want, `${source}: the executed plan reports ${executed}, want ${want}`);
}

console.log(`JS optimizer checks: ${checks} passed`);
js/src/sql/index.mjs:20:import { HybridPlan, executeHybrid, planHybrid } from './hybrid.mjs';
js/src/sql/index.mjs:23:export { executeHybrid, planHybrid };
js/src/sql/index.mjs:32:  static translate(program, dialect, bindings = null, options = null) {
js/src/sql/index.mjs:53:  static translateStatement(program, dialect, bindings = null, options = null) {
js/src/sql/index.mjs:68:  static planHybrid(program, dialect, bindings = null, options = null) {
js/src/sql/index.mjs:72:  static executeHybrid(plan, dbRunner, context = null) {
js/src/sql/index.mjs:73:    return executeHybrid(plan, dbRunner, context);
js/src/sql/hybrid.mjs:350:export function executeHybrid(plan, dbRunner, context = null) {
js/src/sql/hybrid.mjs:351:  if (!(plan instanceof HybridPlan)) throw new TypeError('executeHybrid expects a HybridPlan');
```

Run JS probe comparing run() and translate() for _K after and before BUCKET

```bash
# (file written, see "Reproduction scripts" above)
node $SCRATCH/verify-K/probe.mjs
```

```
SRC   ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run  -{"2"=-{"k"=t"1", "n"=t"1"}}
  sql  undefined
  plan pure_sql | undefined
SRC   ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run  -{"2"=-{"k"=t"1", "n"=t"1"}}
  sql  undefined
  plan pure_sql | undefined
SRC   ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "1") .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run  -{"1"=-{"k"=t"1", "n"=t"1"}}
  sql  undefined
  plan pure_sql | undefined
SRC   ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))
  run  -{"1"=-{"k"=t"1", "n"=t"1"}}
  sql  undefined
  plan pure_sql | undefined
SRC   ITEMS .> FILTER(_K $== "2")
  run  -{"2"=-{"dept"=t"1", "v"=t"2"}}
  sql  E_SQL_SHAPE@1:17 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan pure_memory | -
```

Rerun JS probe with the statement text

```bash
cd $SCRATCH/verify-K && sed -i 's/Sql.translate(p, .sqlite., bindings).sql/Sql.translateStatement(p, "sqlite", bindings).asStatement()/; s/pl.sqlStatement.sql/pl.sqlStatement.asStatement()/' probe.mjs && node probe.mjs
```

```
SRC   ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run  -{"2"=-{"k"=t"1", "n"=t"1"}}
  sql  SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
  plan pure_sql | SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
SRC   ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run  -{"2"=-{"k"=t"1", "n"=t"1"}}
  sql  SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
  plan pure_sql | SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
SRC   ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "1") .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run  -{"1"=-{"k"=t"1", "n"=t"1"}}
  sql  SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('1' AS TEXT))
  plan pure_sql | SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('1' AS TEXT))
SRC   ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))
  run  -{"1"=-{"k"=t"1", "n"=t"1"}}
  sql  SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" WHERE (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT)) GROUP BY "i"."dept"
  plan pure_sql | SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" WHERE (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT)) GROUP BY "i"."dept"
SRC   ITEMS .> FILTER(_K $== "2")
  run  -{"2"=-{"dept"=t"1", "v"=t"2"}}
  sql  E_SQL_SHAPE@1:17 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan pure_memory | -
```

Run the same probe against the pre-commit JS sources extracted to the scratchpad

```bash
S=$SCRATCH/verify-K && mkdir -p $S/old && git archive 8fe0e3a js python | tar -x -C $S/old && sed "s#/home/nathan/workspaces/nth-share/sel/js#$S/old/js#g" $S/probe.mjs > $S/probe-old.mjs && node $S/probe-old.mjs
```

```
SRC   ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run  -{"2"=-{"k"=t"1", "n"=t"1"}}
  sql  E_SQL_SHAPE@1:47 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan hybrid | SELECT "i"."dept" FROM "items" "i" GROUP BY "i"."dept"
SRC   ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run  -{"2"=-{"k"=t"1", "n"=t"1"}}
  sql  SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
  plan pure_sql | SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
SRC   ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "1") .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run  -{"1"=-{"k"=t"1", "n"=t"1"}}
  sql  E_SQL_SHAPE@1:69 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan hybrid | SELECT "i"."dept" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('1' AS TEXT))
SRC   ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))
  run  -{"1"=-{"k"=t"1", "n"=t"1"}}
  sql  SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" WHERE (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT)) GROUP BY "i"."dept"
  plan pure_sql | SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" WHERE (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT)) GROUP BY "i"."dept"
SRC   ITEMS .> FILTER(_K $== "2")
  run  -{"2"=-{"dept"=t"1", "v"=t"2"}}
  sql  E_SQL_SHAPE@1:17 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan pure_memory | -
```

Run the Python probe executing each plan on SQLite and comparing with run()

```bash
S=$SCRATCH/verify-K && # (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $S/probe.py
```

```
SRC  ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run      -{"2"=-{"k"=t"B", "n"=t"1"}}
  sql      SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
  executed pure_sql -> -    <-- DIVERGES
SRC  ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run      -{"2"=-{"k"=t"B", "n"=t"1"}}
  sql      SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
  executed pure_sql -> -    <-- DIVERGES
SRC  ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "B") .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run      -{"1"=-{"k"=t"B", "n"=t"1"}}
  sql      SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('B' AS TEXT))
  executed pure_sql -> -{"1"=-{"k"=t"B", "n"=t"1"}} 
SRC  ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))
  run      -{"1"=-{"k"=t"B", "n"=t"1"}}
  sql      SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" WHERE (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT)) GROUP BY "i"."dept"
  executed pure_sql -> -    <-- DIVERGES
SRC  ITEMS .> FILTER(_K $== "2")
  run      -{"2"=-{"dept"=t"B", "v"=t"2"}}
  sql      E_SQL_SHAPE@1:17
  executed pure_memory -> -{"2"=-{"dept"=t"B", "v"=t"2"}}
```

Run the PHP probe

```bash
S=$SCRATCH/verify-K && # (file written, see "Reproduction scripts" above)
php $S/probe.php
```

```
SRC  ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run  -{"2"=-{"k"=t"B", "n"=t"1"}}
  sql  SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
  plan pure_sql | SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
SRC  ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run  -{"2"=-{"k"=t"B", "n"=t"1"}}
  sql  SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
  plan pure_sql | SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT))
SRC  ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "B") .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run  -{"1"=-{"k"=t"B", "n"=t"1"}}
  sql  SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('B' AS TEXT))
  plan pure_sql | SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "i"."dept" HAVING (CAST("i"."dept" AS TEXT) = CAST('B' AS TEXT))
SRC  ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))
  run  -{"1"=-{"k"=t"B", "n"=t"1"}}
  sql  SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" WHERE (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT)) GROUP BY "i"."dept"
  plan pure_sql | SELECT "i"."dept" AS "k", COUNT(*) AS "n" FROM "items" "i" WHERE (CAST("i"."dept" AS TEXT) = CAST('2' AS TEXT)) GROUP BY "i"."dept"
SRC  ITEMS .> FILTER(_K $== "2")
  run  -{"2"=-{"dept"=t"B", "v"=t"2"}}
  sql  E_SQL_SHAPE@1:17
  plan pure_memory | -
```

Compile and run the C++ probe against the built objects

```bash
S=$SCRATCH/verify-K && # (file written, see "Reproduction scripts" above)
cd cpp && g++ -std=c++23 -O1 -o $S/probe-cpp $S/probe.cpp build/sel_sql.o build/sel_sql_binding.o build/sel_sql_emit.o build/sel_sql_map.o build/sel_sql_map_data.o build/sel_sql_node.o build/sel_sql_stage1.o build/sel_sql_translator.o build/sel_sql_hybrid.o build/sel.o 2>&1 | head -20 && $S/probe-cpp
```

```
Exit code 134
terminate called after throwing an instance of 'sel::sql::SqlError'
  what():  a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
/bin/bash: line 35: 781605 Aborted                    $S/probe-cpp
```

Check SqlError's base class

```bash
S=$SCRATCH/verify-K && grep -n "class SqlError" -A6 cpp/sel_sql.hpp | head -12
```

```
39:class SqlError : public std::exception {
40- public:
41-  SqlError(std::string code, std::string message, Pos pos = {});
42-
43-  const std::string& code() const noexcept { return code_; }
44-  const std::string& message() const noexcept { return message_; }
45-  int line() const noexcept { return pos_.line; }
```

Rebuild and run the C++ probe catching SqlError

```bash
S=$SCRATCH/verify-K && sed -i 's/catch (const sel::SelError& e) { sql = /catch (const SqlError\& e) { sql = /; s/catch (const sel::SelError& e) { plan = e.code(); }/catch (const SqlError\& e) { plan = e.code(); }/' $S/probe.cpp && cd cpp && g++ -std=c++23 -O1 -o $S/probe-cpp $S/probe.cpp build/sel_sql.o build/sel_sql_binding.o build/sel_sql_emit.o build/sel_sql_map.o build/sel_sql_map_data.o build/sel_sql_node.o build/sel_sql_stage1.o build/sel_sql_translator.o build/sel_sql_hybrid.o build/sel.o 2>&1 | head && $S/probe-cpp
```

```
SRC  ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run  -{"2"=-{"k"=t"B", "n"=t"1"}}
  sql  SELECT "dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "dept" HAVING (CAST("dept" AS TEXT) = CAST('2' AS TEXT))
  plan pure_sql | SELECT "dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "dept" HAVING (CAST("dept" AS TEXT) = CAST('2' AS TEXT))
SRC  ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run  -{"2"=-{"k"=t"B", "n"=t"1"}}
  sql  SELECT "dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "dept" HAVING (CAST("dept" AS TEXT) = CAST('2' AS TEXT))
  plan pure_sql | SELECT "dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "dept" HAVING (CAST("dept" AS TEXT) = CAST('2' AS TEXT))
SRC  ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "B") .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run  -{"1"=-{"k"=t"B", "n"=t"1"}}
  sql  SELECT "dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "dept" HAVING (CAST("dept" AS TEXT) = CAST('B' AS TEXT))
  plan pure_sql | SELECT "dept" AS "k", COUNT(*) AS "n" FROM "items" "i" GROUP BY "dept" HAVING (CAST("dept" AS TEXT) = CAST('B' AS TEXT))
SRC  ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))
  run  -{"1"=-{"k"=t"B", "n"=t"1"}}
  sql  SELECT "dept" AS "k", COUNT(*) AS "n" FROM "items" "i" WHERE (CAST("dept" AS TEXT) = CAST('2' AS TEXT)) GROUP BY "dept"
  plan pure_sql | SELECT "dept" AS "k", COUNT(*) AS "n" FROM "items" "i" WHERE (CAST("dept" AS TEXT) = CAST('2' AS TEXT)) GROUP BY "dept"
SRC  ITEMS .> FILTER(_K $== "2")
  run  -{"2"=-{"dept"=t"B", "v"=t"2"}}
  sql  E_SQL_SHAPE@1:17
  plan pure_memory | -
```

See how Lisp scripts load and the Lisp SQL API names

```bash
cat lisp/bin/sqlt | head -30; grep -n "plan-hybrid\|translate-statement\|make-relation\|relation-binding\|column-binding\|as-statement\|hybrid-plan-sql" lisp/tests/unit.lisp | head -20
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
342:           (frag (sel.sql:translate-statement p "postgresql" bindings))
343:           (sql (sel.sql:as-statement frag)))
350:           (frag (sel.sql:translate-statement p "postgresql" bindings))
351:           (sql (sel.sql:as-statement frag)))
356:      (handler-case (sel.sql:translate-statement p "postgresql" bindings)
365:           (frag (sel.sql:translate-statement p "postgresql" bindings))
366:           (sql (sel.sql:as-statement frag)))
371:           (frag (sel.sql:translate-statement p "postgresql" bindings))
372:           (sql (sel.sql:as-statement frag)))
384:           (frag (sel.sql:translate-statement p "postgresql" bindings))
385:           (sql (sel.sql:as-statement frag)))
392:           (frag (sel.sql:translate-statement p "postgresql" bindings))
393:           (sql (sel.sql:as-statement frag)))
399:           (frag (sel.sql:translate-statement p "postgresql" bindings))
400:           (sql (sel.sql:as-statement frag)))
414:           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
430:           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
433:      (is (not (null (sel.sql:hybrid-plan-sql-statement plan))))
435:      (let* ((sql (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan)))
447:           (plan (sel.sql:plan-hybrid p "postgresql" nil))
```

Run the Lisp probe

```bash
S=$SCRATCH/verify-K && # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp 2>&1 | grep -v "^;" | tail -25
```

```
Package SEL.SQL does not exist.

      Line: 1, Column: 62, File-Position: 61

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-K/probe.lisp" {1201EB73D3}>

Backtrace for: #<SB-THREAD:THREAD tid=783120 "main thread" RUNNING {1204028083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201EB8723}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201EB8723}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201EB8723}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201EB8723}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201EB86D3}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-K/probe.lisp" {1201EB73D3}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {75E44D10F4CB}> #<SB-C::SOURCE-INFO {1201E57753}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-K/probe.lisp" {1201EB73D3}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-K/probe.lisp" {1201EB73D3}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {75E44D10F88B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-K/probe.lisp" {1201EB73D3}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-K/probe.lisp" {1201EB73D3}>)
9: (LOAD #P"$SCRATCH/verify-K/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-K/probe.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

See how the SQL package is loaded in Lisp

```bash
cat lisp/bin/boot.lisp; head -20 lisp/bin/sqlt.lisp; cat lisp/bin/test 2>/dev/null | head
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

Run the Python probe against the pre-commit Python sources

```bash
S=$SCRATCH/verify-K && PYTHONPATH=$S/old/python python3 $S/probe.py 2>&1 | grep -v "^  sql"
```

```
SRC  ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run      -{"2"=-{"k"=t"B", "n"=t"1"}}
  executed hybrid -> -{"2"=-{"k"=t"2", "n"=t"1"}}    <-- DIVERGES
SRC  ITEMS .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_))) .> FILTER(_K $== "2")
  run      -{"2"=-{"k"=t"B", "n"=t"1"}}
  executed pure_sql -> -    <-- DIVERGES
SRC  ITEMS .> BUCKET(_["dept"]) .> FILTER(_K $== "B") .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run      -{"1"=-{"k"=t"B", "n"=t"1"}}
  executed hybrid -> -{"1"=-{"k"=t"1", "n"=t"1"}}    <-- DIVERGES
SRC  ITEMS .> FILTER(_K $== "2") .> BUCKET(_["dept"], RECORD("k", _K, "n", COUNT(_)))
  run      -{"1"=-{"k"=t"B", "n"=t"1"}}
  executed pure_sql -> -    <-- DIVERGES
SRC  ITEMS .> FILTER(_K $== "2")
  run      -{"2"=-{"dept"=t"B", "v"=t"2"}}
  executed pure_memory -> -{"2"=-{"dept"=t"B", "v"=t"2"}}
```

