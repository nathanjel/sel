# I. A pipeline that ends in a bare or sealed bucket is classified pure_sql: the full-pushdown probe skips bucketRowsAreKeys

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "Hybrid planner: the shape guard, completed".

**Verdict:** PARTIAL · **severity:** high · **introduced:** mixed · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

The headline claim is CONFIRMED in all five hosts: plan_hybrid tries the whole pipeline with try_statement before ever consulting bucketRowsAreKeys, so any pipeline that ends in an open or sealed bucket (bare BUCKET, +TAKE/DROP/FILTER(HAVING)/SORT_BY/LINK, bare-then-projected BUCKET, and now also `X = ORDERS .> BUCKET(k); X`) is classified pure_sql with `SELECT key … GROUP BY key`; executed, it answers one key record per group where run() answers a map of member lists (or E_NO_KEY for SORT_BY/LINK/second BUCKET). The one facet refuted is member 3's MAP(_K)/MAP(COUNT(_)) clause: those buckets are closed, the guard is not involved, and the one-column-record-vs-scalar difference is the general SQL scalar-projection shape (`ORDERS .> MAP(_["amount"])` behaves identically, no bucket). The observable behaviour is pre-existing (identical plans from the 8fe0e3a JS tree), but the promise (§12.1, EXTENDING "Shape", CHANGELOG "never splits inside a bucket") and the half-applied guard are introduced by this commit.

## Suggested fix — case first

Smallest fix, in each host's plan_hybrid: guard the whole-pipeline probe the same way the prefix loop is guarded — `if (!bucketRowsAreKeys(steps)) { fullSql = tryStatement(fullAst…) … }` (js/src/sql/hybrid.mjs:315, php/src/Sql/Hybrid.php:134, python/sel/sql/hybrid.py:353, cpp/sel_sql_hybrid.cpp:337, lisp/src/sql/hybrid.lisp:97); the existing prefix loop then backs up to the step before the bucket or lands on pure_memory. Case to add first, in sql/cases/25-hybrid-plans.sqlt (regenerate with `node tools/gen-sql-cases.mjs`): `plan.bucket.open-pipeline-is-not-pure-sql` — source `ORDERS .> FILTER(_["amount"] > 1) .> BUCKET(_["customer_id"])`, plan `hybrid`, expect `SELECT `o`.* FROM `orders` `o` WHERE (`o`.`amount` > 1)`; and a sibling `ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)` with plan `pure_memory`. Then add the bare, +TAKE, +FILTER(COUNT(_) > 1) and FILTER-then-BUCKET shapes to test_bucket_plans_answer_what_the_evaluator_answers_on_sqlite (python/tests/test_unit.py:504) so the executed comparison covers open/sealed endings. Separately decide whether translate_statement should keep rendering a bare bucket as keys (stmt.bucket.basic) and accepting BUCKET(k) .> SORT_BY/LINK/BUCKET that run() refuses with E_NO_KEY — if kept, §12.1 should say pure_sql is only ever offered for pipelines whose SQL rows are SEL's value, and translate's key rendering is the translate lane's own decision.

## Verifier reasoning

Code reading, every host: the whole-pipeline probe runs first and unguarded — js/src/sql/hybrid.mjs:315-319 (guard bucketRowsAreKeys at :124-137 is applied only at :211 fall-through and :329 prefix loop); php/src/Sql/Hybrid.php:133-142 (guard :254-269, applied at :150 and :349); python/sel/sql/hybrid.py:352-357 (guard :153, applied at :251 and :365); cpp/sel_sql_hybrid.cpp:335-344 (guard :71, applied at :197 and :357); lisp/src/sql/hybrid.lisp:95-104 (guard :137, applied at :117 and :217). All five behave identically, so it is a lane divergence (run() vs execute_hybrid / translate_statement), not a cross-host one. Is it a promise? docs/SQL-TRANSLATION.md:2279-2287 says a prefix ending in an unprojected bucket "or in anything that followed one — is never a split point: the planner backs up to the step before the bucket, or stays in memory"; docs/EXTENDING.md:600-604 states the general rule "A prefix is a split point only if its SQL rows are the value the evaluator would have produced for it. A bare BUCKET breaks that (keys, not groups)"; the Python test docstring (python/tests/test_unit.py:485-491) gives the same rationale and asserts execute_hybrid == run(), but lists only closed shapes. The whole pipeline is the maximal prefix and pure_sql is the trivial split with an empty continuation, so the stated rule covers it; the code does not. Counterpoint worth stating: §12.1 also documents that a bare bucket "renders as the group keys" and sql/cases/24-bucket.sqlt:9-20 (stmt.bucket.basic, pre-existing as stmt.group-by.basic) pins that for translate_statement, so a maintainer could resolve this by documenting that pure_sql may answer SQL's shape rather than SEL's — but as written, the planner contract, the EXTENDING rule and the differential test all say otherwise, and the result really does differ between lanes (including E_NO_KEY vs rows for SORT_BY/LINK/BUCKET after a bare bucket). Severity high by the rubric (differing results/errors between lanes; promise unmet). Introduced: mixed — the pre-commit JS tree produces the same pure_sql plans for every open/sealed shape, so behaviour is pre-existing; the commit added the promise and the partial guard, and its new normalise-first planning makes `X = ORDERS .> BUCKET(k); X` newly pure_sql (it was pure_memory before). The Lisp SNODE-R crash on the double bucket is a separate finding. The LINK facet is pure_sql on mariadb (with either binder spelling) and pure_memory on sqlite in my probe, so it is dialect-dependent but real.

## Verifier evidence

```
Own probes under /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-I/ (shapes.txt = 12 programs; probe.py, probe.mjs, probe.php, probe.cpp, probe.lisp, link.py, probe-old.mjs).

Python, executed against in-process SQLite (`PYTHONPATH=$PWD/python python3 verify-I/probe.py`):
- `ORDERS .> BUCKET(_["customer_id"])` → plan pure_sql `SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"`; run() = -{"7"=-{"1"=-{customer_id=7,amount=10},"2"=-{…}},"9"=-{…}}; execute_hybrid = -{"1"=-{"customer_id"=t"7"},"2"=-{"customer_id"=t"9"}} → DIFFERENT
- `… .> TAKE(1)` → pure_sql `… GROUP BY … LIMIT 1`; run() one group of two members; hybrid -{"1"=-{"customer_id"=t"7"}} → DIFFERENT
- `… .> FILTER(COUNT(_) > 1)` → pure_sql `… HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC))`; run() group "7" with 2 members; hybrid one key row → DIFFERENT
- `… .> FILTER(COUNT(_) > 1) .> TAKE(1)` → pure_sql → DIFFERENT
- `… .> BUCKET(_["customer_id"], RECORD("d", _K, "n", COUNT(_)))` → pure_sql derived-table GROUP BY; run() ERR E_NO_KEY; hybrid two rows n=1 → DIFFERENT
- own: `ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["amount"])` → pure_sql `SELECT "o"."amount" … WHERE … GROUP BY "o"."amount"`; run() map of 2 groups; hybrid 2 key rows → DIFFERENT
- own: `… .> SORT_BY(_["customer_id"])` → pure_sql `… ORDER BY …`; run() ERR E_NO_KEY; hybrid 2 rows → DIFFERENT
- own: `… .> DROP(1)` → pure_sql `… LIMIT -1 OFFSET 1`; run() one group; hybrid one key row → DIFFERENT
- own: `X = ORDERS .> BUCKET(_["amount"]); X` → pure_sql; DIFFERENT (pre-commit JS tree: pure_memory)
- `… .> MAP(_K)` / `… .> MAP(COUNT(_))` → pure_sql `SELECT "o"."customer_id" … GROUP BY` / `SELECT COUNT(*) … GROUP BY`; run() scalars, hybrid one-column records — but `ORDERS .> MAP(_["amount"])` (no bucket) → pure_sql `SELECT "o"."amount" FROM "orders" "o"` with the same scalar-vs-record difference, so this is not bucket-specific.
- `verify-I/link.py` on mariadb: `ORDERS .> BUCKET(_["customer_id"]) .> LINK(CUSTOMERS, o, c, o["customer_id"] == c["id"])` → pure_sql `SELECT `_sub1`.* FROM (SELECT `o`.`customer_id` … GROUP BY …) `_sub1` INNER JOIN `customers` …`; run() ERR E_NO_KEY.

JS (`node verify-I/probe.mjs verify-I/shapes.txt`), PHP (`php verify-I/probe.php …`), C++ (`g++ -std=c++23 -I. -o verify-I/probe_cpp verify-I/probe.cpp build/sel_sql*.o build/sel.o`), Lisp (`sbcl … --load lisp/bin/boot.lisp --eval '(ql:quickload :sel-lang/sql)' --load verify-I/probe.lisp …`): byte-identical classification and SQL to Python for all 12 shapes (Lisp: `CRASH The function SEL.SQL::SNODE-R is undefined.` on the double-bucket shape — the separate SNODE-R finding).

Pre-commit check: `git archive 8fe0e3a js package.json | tar -x -C verify-I/old`; `node verify-I/probe-old.mjs` → the bare bucket, +TAKE, +FILTER, +FILTER+TAKE, +SORT_BY, +DROP, double bucket, FILTER-then-BUCKET are all pure_sql with the same SQL at 8fe0e3a; `X = …; X` was pure_memory; MAP(_K) was hybrid. `git show 8fe0e3a:sql/cases/24-group-by.sqlt` lines 9-20: stmt.group-by.basic already pinned `ITEMS .> GROUP_BY(_["dept"])` → `SELECT `dept` FROM `items` GROUP BY `dept``.

Code: js/src/sql/hybrid.mjs:315-319, :124-137, :211, :329; php/src/Sql/Hybrid.php:133-142, :150, :254-269, :349; python/sel/sql/hybrid.py:352-357, :153, :251, :365; cpp/sel_sql_hybrid.cpp:335-344, :71, :197, :357; lisp/src/sql/hybrid.lisp:95-104, :117, :137, :217. Promises: docs/SQL-TRANSLATION.md:2279-2287; docs/EXTENDING.md:600-604; CHANGELOG.md "[Unreleased]" "The hybrid planner never splits inside a bucket"; python/tests/test_unit.py:485-522 (closed shapes only); sql/cases/25-hybrid-plans.sqlt:518-602 (no case ends in an open/sealed bucket).
```

## Original review reports (deduplicated into this finding)

### [bucket-translator] A pipeline that ENDS in a bare or sealed bucket is classified pure_sql, so execute_hybrid answers key rows where run() answers a map of member lists

*correctness · high · hosts: js, php, python, cpp, lisp*

Locations: `js/src/sql/hybrid.mjs:317`; `php/src/Sql/Hybrid.php:134`; `python/sel/sql/hybrid.py:352`; `cpp/sel_sql_hybrid.cpp:335`; `lisp/src/sql/hybrid.lisp:97`

bucketRowsAreKeys is applied to the fall-through and to every prefix in the longest-prefix loop, but not to the whole-pipeline pushdown that runs first. The translator happily renders a bare bucket as its keys ('the most SQL can say'), so ORDERS .> BUCKET(k), ... .> TAKE(1), ... .> FILTER(COUNT(_) > 1), ... .> LINK(...), and BUCKET(k) .> BUCKET(k2, proj) are all pure_sql — a plan whose rows are keys, the exact situation §12.1 says is never a split point. The lanes disagree: run() returns a tree keyed by the group key holding member records (or E_NO_KEY for the double bucket), execute_hybrid returns one flat record per key. Consistent across the five hosts, so only an executed comparison sees it; the SQLite unit test contains no such shape.

Reported repro:

```
Planner probes 98-plan-probe.sqlt q01/q08/q09/q12/q07 (bare bucket alone, + TAKE(2), + FILTER(COUNT(_) > 1), + LINK, bare then projected bucket): all five hosts answer pure_sql (Lisp crashes on q07/q12, see the SNODE-R finding). probe_sqlite.py: ORDERS .> BUCKET(_["customer_id"]) — plan pure_sql `SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"`; run() = -{"A"=-{"1"=-{...}, "2"=-{...}}, "B"=..., ""=...}; execute_hybrid = -{"1"=-{"customer_id"=-}, "2"=-{"customer_id"=t"A"}, "3"=-{"customer_id"=t"B"}}. ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"], RECORD("d", _K, "n", COUNT(_))): run() E_NO_KEY at 1:47, SQL returns three rows with n=1.
```

### [promises-vs-code] Full-pipeline probe skips bucketRowsAreKeys: a bare bucket followed by TAKE/FILTER/BUCKET is pure_sql and answers keys where run() answers groups

*correctness · high · hosts: js, python, php, cpp, lisp*

Locations: `js/src/sql/hybrid.mjs:316`; `python/sel/sql/hybrid.py:352`; `php/src/Sql/Hybrid.php:133`; `cpp/sel_sql_hybrid.cpp:335`; `lisp/src/sql/hybrid.lisp:95`

CHANGELOG says 'The hybrid planner never splits inside a bucket. A prefix whose SQL rows would be keys rather than groups is not a split point', and §12.1 says 'a prefix that ends in a bucket nobody has projected — or in anything that followed one — is never a split point: the planner backs up to the step before the bucket, or stays in memory'. The guard is applied only in the longest-prefix loop and the fall-through; step 1 (try the whole pipeline) calls tryStatement(fullAst) without consulting bucketRowsAreKeys in any host. So any pipeline whose LAST step leaves the bucket open or sealed and which the translator happens to accept becomes pure_sql, and the caller gets GROUP BY key rows where run() gives a map of member rows. The fixture plan.bucket.sealed-prefix-is-not-a-split-point only passes because that particular MAP is refused by the translator, not because the planner declined the prefix.

Reported repro:

```
PYTHONPATH=python python3 (sqlite executor in scratchpad): source 'ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)' -> plan pure_sql `SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT 1`; run() = -{"1"=-{"1"=-{customer_id=7, amount=10}, "2"=-{customer_id=7, amount=5}}}; execute_hybrid = -{"1"=-{"customer_id"=t"7"}}. Same for '... .> FILTER(COUNT(_) > 1) .> TAKE(1)'. ./run.sh cases6.txt shows all five hosts classify both as pure_sql with identical SQL, so every host disagrees with its own run() the same way.
```

### [probe-lanes-bucket-hybrid] A pipeline ending in an open bucket (or MAP(_K)/MAP(COUNT(_))/FILTER over one) is classified pure_sql though the database's rows are keys, not what run() answers

*doc-claim · low · hosts: js, python, php, cpp, lisp*

Locations: `docs/SQL-TRANSLATION.md:2277`; `python/tests/test_unit.py:485`; `js/src/sql/hybrid.mjs:315`

§12.1 says BUCKET(src, key) on its own renders as the group keys, and the planner's bucket_rows_are_keys guard exists because such rows are not SEL's value — yet the full-pushdown probe never consults that guard, so a pipeline whose LAST step leaves the bucket open is a pure_sql plan ("the database answers") whose answer is a list of key records where run() answers a map of member lists; MAP(_K) and MAP(COUNT(_)) over the bucket likewise come back as one-column records where run() answers a list of scalars, and FILTER(COUNT(_) > 1) alone returns key rows where run() returns the surviving groups. The Python sqlite test that the CHANGELOG cites as comparing bucket plans with the evaluator runs only closed shapes, so this family is unpinned in every host. Either the contract should state that an open-ended bucket pipeline is not a pure_sql plan (fall to pure_memory, as the split logic already does for prefixes), or the docs should say pure_sql may answer a different shape here.

Reported repro:

```
b01: ORDERS .> BUCKET(_["customer_id"])  run() => -{"7"=-{"1"=-{"customer_id"=t"7",...}, "2"=...}, "9"=..., "3"=...}; plan_hybrid (all hosts, all dialects) => pure_sql SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"; executed => -{"1"=-{"customer_id"=t"3"}, "2"=-{"customer_id"=t"7"}, "3"=-{"customer_id"=t"9"}}.
c17: ... .> BUCKET(_["customer_id"]) .> MAP(_K)  run() => -{"1"=t"7", "2"=t"9", "3"=t"3"}; pure_sql, executed => records {customer_id}. c18: .> MAP(COUNT(_)) run() => t"2",t"2",t"1"; pure_sql SELECT COUNT(*) ... GROUP BY, executed => records {"COUNT(*)"}. c19: .> FILTER(COUNT(_) > 1) run() => map of two groups; pure_sql => two key rows.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$S/probe.py`**

```python
import sqlite3, sys
from sel import compile as sel_compile
from sel.value import Value
from sel.sql import Binding, Sql

bindings = {'ORDERS': Binding.relation('orders', 'o', {
    'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM'),
    'AMOUNT': Binding.column('amount', 'o', 'NUM')}),
    'CUSTOMERS': Binding.relation('customers', 'c', {
    'ID': Binding.column('id', 'c', 'NUM'),
    'NAME': Binding.column('name', 'c', 'TEXT')})}
rows = [{'customer_id': '7', 'amount': '10'}, {'customer_id': '7', 'amount': '5'},
        {'customer_id': '9', 'amount': '7'}]
custs = [{'id': '7', 'name': 'ann'}, {'id': '9', 'name': 'bob'}]
db = sqlite3.connect(':memory:')
db.execute('create table orders(customer_id, amount)')
db.executemany('insert into orders values (?, ?)', [(r['customer_id'], r['amount']) for r in rows])
db.execute('create table customers(id, name)')
db.executemany('insert into customers values (?, ?)', [(r['id'], r['name']) for r in custs])

def runner(sql, params):
    cur = db.execute(sql, [p.as_text() for p in params])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]

shapes = [
    # reported
    'ORDERS .> BUCKET(_["customer_id"])',
    'ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)',
    'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1)',
    'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> TAKE(1)',
    'ORDERS .> BUCKET(_["customer_id"]) .> MAP(_K)',
    'ORDERS .> BUCKET(_["customer_id"]) .> MAP(COUNT(_))',
    'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"], RECORD("d", _K, "n", COUNT(_)))',
    # my own
    'ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["amount"])',
    'ORDERS .> BUCKET(_["customer_id"]) .> SORT_BY(_["customer_id"])',
    'ORDERS .> BUCKET(_["customer_id"]) .> DROP(1)',
    'ORDERS .> BUCKET(_["customer_id"]) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])',
    'X = ORDERS .> BUCKET(_["amount"]); X',
]
ctx = {'ORDERS': rows, 'CUSTOMERS': custs}
for src in shapes:
    program = sel_compile(src)
    try:
        want = program.run(ctx).dump()
    except Exception as e:
        want = 'ERR ' + getattr(e, 'code', type(e).__name__) + ' ' + str(getattr(e, 'pos', ''))
    plan = Sql.plan_hybrid(program, 'sqlite', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    sql = plan.sql_statement.as_statement('params') if plan.sql_statement else None
    try:
        got = Sql.execute_hybrid(plan, runner, ctx)
        got = got if isinstance(got, Value) else Value.from_native(got)
        got = got.dump()
    except Exception as e:
        got = 'ERR ' + getattr(e, 'code', type(e).__name__) + ' ' + str(getattr(e, 'pos', ''))
    print('###', src)
    print('  plan   :', kind, '|', sql)
    print('  run()  :', want)
    print('  hybrid :', got)
    print('  SAME' if got == want else '  DIFFERENT')
```

**`$S/shapes.txt`**

```
ORDERS .> BUCKET(_["customer_id"])
ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)
ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1)
ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> TAKE(1)
ORDERS .> BUCKET(_["customer_id"]) .> MAP(_K)
ORDERS .> BUCKET(_["customer_id"]) .> MAP(COUNT(_))
ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"], RECORD("d", _K, "n", COUNT(_)))
ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["amount"])
ORDERS .> BUCKET(_["customer_id"]) .> SORT_BY(_["customer_id"])
ORDERS .> BUCKET(_["customer_id"]) .> DROP(1)
X = ORDERS .> BUCKET(_["amount"]); X
ORDERS .> MAP(_["amount"])
```

**`$S/probe.mjs`**

```js
import fs from 'node:fs';
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile, Value } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const bindings = { ORDERS: Binding.relation('orders', 'o', {
  CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'),
  AMOUNT: Binding.column('amount', 'o', 'NUM') }) };
const rows = [{customer_id:'7',amount:'10'},{customer_id:'7',amount:'5'},{customer_id:'9',amount:'7'}];
const keyRows = { // what sqlite answered for these statements (from probe.py)
};
for (const src of fs.readFileSync(process.argv[2], 'utf8').split('\n').filter(Boolean)) {
  const program = compile(src);
  let want; try { want = program.run({ORDERS: rows}).dump(); } catch (e) { want = 'ERR ' + e.code + ' ' + e.pos; }
  const plan = Sql.planHybrid(program, 'sqlite', bindings);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  const sql = plan.sqlStatement ? plan.sqlStatement.asStatement('params') : null;
  console.log('###', src); console.log('  plan   :', kind, '|', sql); console.log('  run()  :', want);
}
```

**`$S/probe.php`**

```php
<?php
require_once '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Binding; use Sel\Sql\Sql;
$bindings = ['ORDERS' => Binding::relation('orders', 'o', fields: [
  'CUSTOMER_ID' => Binding::column('customer_id', 'o', 'NUM'),
  'AMOUNT' => Binding::column('amount', 'o', 'NUM')])];
$rows = [['customer_id'=>'7','amount'=>'10'],['customer_id'=>'7','amount'=>'5'],['customer_id'=>'9','amount'=>'7']];
foreach (array_filter(explode("\n", file_get_contents($argv[1]))) as $src) {
  $program = Sel::compile($src);
  try { $want = $program->run(['ORDERS' => $rows])->dump(); } catch (\Throwable $e) { $want = 'ERR ' . ($e->code ?? get_class($e)); }
  $plan = Sql::planHybrid($program, 'sqlite', $bindings);
  $kind = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
  $sql = $plan->sqlStatement ? $plan->sqlStatement->asStatement('params') : null;
  echo "### $src\n  plan   : $kind | $sql\n  run()  : $want\n";
}
```

**`$S/probe.cpp`**

```cpp
#include "sel.hpp"
#include "sel_sql.hpp"
#include <fstream>
#include <iostream>
using namespace sel; using namespace sel::sql;
int main(int, char** argv) {
  Bindings bindings({{"ORDERS", Binding::relation("orders", "o",
     {{"CUSTOMER_ID", Binding::column("customer_id", std::string("o"), SqlKind::Num)},
      {"AMOUNT", Binding::column("amount", std::string("o"), SqlKind::Num)}})}});
  auto rec = [](const char* c, const char* a) { return Value::record({"customer_id","amount"}, {Value::text(c), Value::text(a)}); };
  Value rows = Value::list({rec("7","10"), rec("7","5"), rec("9","7")});
  std::ifstream in(argv[1]); std::string src;
  while (std::getline(in, src)) {
    if (src.empty()) continue;
    Program p = compile(src);
    std::string want;
    try { Value ctx = Value::record({"ORDERS"}, {rows}); want = p.run(ctx).dump(); }
    catch (const SelError& e) { want = std::string("ERR ") + e.code(); }
    HybridPlan plan = Sql::plan_hybrid(p, "sqlite", bindings);
    std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    std::string sql = plan.sql_statement ? plan.sql_statement->as_statement(Fragment::Mode::Params) : "null";
    std::cout << "### " << src << "\n  plan   : " << kind << " | " << sql << "\n  run()  : " << want << "\n";
  }
}
```

**`$S/probe.lisp`**

```lisp
(defun probe-main (file)
  (let* ((bindings (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o"
                     (list (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "o" :num))
                           (cons "AMOUNT" (sel.sql:binding-column "amount" "o" :num)))))))
         (rows (list (list (cons "customer_id" "7") (cons "amount" "10"))
                     (list (cons "customer_id" "7") (cons "amount" "5"))
                     (list (cons "customer_id" "9") (cons "amount" "7")))))
    (with-open-file (in file)
      (loop for src = (read-line in nil) while src do
        (when (plusp (length src))
          (let* ((p (sel:compile-source src))
                 (want (handler-case (sel:value-dump (sel:run p (list (cons "ORDERS" rows))))
                         (sel:sel-error (e) (format nil "ERR ~a" (sel:sel-error-code e)))))
                 (plan (handler-case (sel.sql:plan-hybrid p "sqlite" bindings)
                         (error (e) (format nil "CRASH ~a" e)))))
            (format t "### ~a~%" src)
            (if (stringp plan)
                (format t "  plan   : ~a~%" plan)
                (let ((kind (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                                  ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                                  (t "hybrid")))
                      (frag (sel.sql:hybrid-plan-sql-statement plan)))
                  (format t "  plan   : ~a | ~a~%" kind (if frag (sel.sql:as-statement frag :params) "null"))))
            (format t "  run()  : ~a~%" want)))))))
```

**`$S/link.py`**

```python
from sel import compile as sel_compile
from sel.sql import Binding, Sql
b = {'ORDERS': Binding.relation('orders', 'o', {'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM')}),
     'CUSTOMERS': Binding.relation('customers', 'c', {'ID': Binding.column('id', 'c', 'NUM'), 'NAME': Binding.column('name', 'c', 'TEXT')})}
for src in ['ORDERS .> BUCKET(_["customer_id"]) .> LINK(CUSTOMERS, o, c, o["customer_id"] == c["id"])',
            'ORDERS .> BUCKET(_["customer_id"]) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])']:
    p = sel_compile(src); plan = Sql.plan_hybrid(p, 'mariadb', b)
    print(src, '->', 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid', plan.sql_statement.as_statement() if plan.sql_statement else None)
    try: print('  run:', p.run({'ORDERS': [{'customer_id': '7'}], 'CUSTOMERS': [{'id': '7', 'name': 'a'}]}).dump())
    except Exception as e: print('  run: ERR', e.code)
```

### Commands run and their output

Check builds and see C++ SQL unit API

```bash
mkdir -p $SCRATCH/verify-I && ls cpp/build/ | head -30; ls lisp/bin; sed -n 1,80p cpp/tests/sql_unit.cpp | head -100
```

```
api
ast
batch
case_data.o
check-decimal
CMakeCache.txt
CMakeFiles
cmake_install.cmake
conformance
conformance-asan
e2e
example-complex
example-dialect
example-plain
example-sql
libsel-lang.a
Makefile
map_replay.o
scale-bench
sel
sel-langConfig.cmake
sel-langConfigVersion.cmake
sel.o
sel_sql_binding.o
sel_sql_emit.o
sel_sql_hybrid.o
sel_sql_map_data.o
sel_sql_map.o
sel_sql_node.o
sel_sql.o
api
api.lisp
batch
batch.lisp
boot.lisp
case-data.lisp
check-decimal
check-decimal.lisp
conformance
conformance.lisp
e2e
e2e.lisp
map-replay.lisp
sel
sel.lisp
sqlfuzz
sqlfuzz.lisp
sqlreplay
sqlreplay.lisp
sqlt
sqlt.lisp
test
// Focused C++ SQL tests for the advanced relational-plan paths.

#include "../sel.hpp"
#include "../sel_sql.hpp"

#include <iostream>
#include <string>
#include <vector>

namespace {

using sel::sql::Binding;
using sel::sql::Bindings;
using sel::sql::Sql;
using sel::sql::SqlKind;

Binding orders() {
  return Binding::relation(
      "orders", "o",
      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
       {"CUSTOMER_ID", Binding::column("customer_id", std::nullopt, SqlKind::Num)}});
}

Binding customers() {
  return Binding::relation(
      "customers", "c",
      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
       {"NAME", Binding::column("name", std::nullopt, SqlKind::Text)}});
}

}  // namespace

int main() {
  const Bindings bindings({{"ORDERS", orders()}, {"CUSTOMERS", customers()}});
  const sel::Program inner = sel::compile(
      "MAP(LINK(ORDERS, CUSTOMERS, O, C, O[\"CUSTOMER_ID\"] == C[\"ID\"]), "
      "X, RECORD(\"ORDER_ID\", _1[\"ID\"], \"NAME\", _2[\"NAME\"]))");
  const std::string inner_sql =
      Sql::translate_statement(inner, "mariadb", bindings).as_statement();
  const std::string wanted_inner =
      "SELECT `o`.`id` AS `ORDER_ID`, `c`.`name` AS `NAME` FROM `orders` `o` "
      "INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)";
  if (inner_sql != wanted_inner) {
    std::cerr << "join SQL mismatch\n got:  " << inner_sql << "\n want: "
              << wanted_inner << "\n";
    return 1;
  }

  const sel::Program derived = sel::compile(
      "FILTER(MAP(ORDERS, O, RECORD(\"ID\", O[\"ID\"], \"CID\", O[\"CUSTOMER_ID\"])), "
      "R, R[\"ID\"] > 1)");
  const std::string derived_sql =
      Sql::translate_statement(derived, "mariadb", bindings).as_statement();
  const std::string wanted_derived =
      "SELECT `_sub1`.* FROM (SELECT `id` AS `ID`, `customer_id` AS `CID` "
      "FROM `orders` `o`) `_sub1` WHERE (CASE WHEN (`_sub1`.`ID` REGEXP "
      "'\\\\A-?[0-9]+(\\\\.[0-9]+)?\\\\z') THEN CAST(`_sub1`.`ID` AS DECIMAL(65,10)) "
      "ELSE NULL END > 1)";
  if (derived_sql != wanted_derived) {
    std::cerr << "derived SQL mismatch\n got:  " << derived_sql << "\n want: "
              << wanted_derived << "\n";
    return 1;
  }

  const sel::Program hybrid_program =
      sel::compile("MAP(TAKE(ORDERS, 1), O, ABORT(\"not pushed\"))");
  const sel::sql::HybridPlan hybrid =
      Sql::plan_hybrid(hybrid_program, "mariadb", bindings);
  if (!hybrid.is_hybrid || hybrid.pure_sql || hybrid.pure_memory ||
      !hybrid.sql_statement || !hybrid.continuation_program ||
      hybrid.source_tables.size() != 1 || hybrid.source_tables[0] != "orders") {
    std::cerr << "hybrid plan did not split at the unsupported suffix\n";
    return 1;
  }

  // The hybrid planner's contract, the parts a host-local check can see.
  // sql/cases/25-hybrid-plans.sqlt holds the language-neutral ve
… (truncated)
```

Run Python sqlite probe: run() vs execute_hybrid for bare-bucket-ending pipelines

```bash
S=$SCRATCH/verify-I && # (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $S/probe.py
```

```
### ORDERS .> BUCKET(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}, "9"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
  hybrid : -{"1"=-{"customer_id"=t"7"}, "2"=-{"customer_id"=t"9"}}
  DIFFERENT
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
  hybrid : -{"1"=-{"customer_id"=t"7"}}
  DIFFERENT
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC))
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
  hybrid : -{"1"=-{"customer_id"=t"7"}}
  DIFFERENT
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
  hybrid : -{"1"=-{"customer_id"=t"7"}}
  DIFFERENT
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(_K)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"7", "2"=t"9"}
  hybrid : -{"1"=-{"customer_id"=t"7"}, "2"=-{"customer_id"=t"9"}}
  DIFFERENT
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(COUNT(_))
  plan   : pure_sql | SELECT COUNT(*) FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"2", "2"=t"1"}
  hybrid : -{"1"=-{"COUNT(*)"=t"2"}, "2"=-{"COUNT(*)"=t"1"}}
  DIFFERENT
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"], RECORD("d", _K, "n", COUNT(_)))
  plan   : pure_sql | SELECT "_sub1"."customer_id" AS "d", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY "_sub1"."customer_id"
  run()  : ERR E_NO_KEY 
  hybrid : -{"1"=-{"d"=t"7", "n"=t"1"}, "2"=-{"d"=t"9", "n"=t"1"}}
  DIFFERENT
### ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["amount"])
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o" WHERE (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)) GROUP BY "o"."amount"
  run()  : -{"10"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}}, "7"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
  hybrid : -{"1"=-{"amount"=t"10"}, "2"=-{"amount"=t"7"}}
  DIFFERENT
### ORDERS .> BUCKET(_["customer_id"]) .> SORT_BY(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" ORDER BY "o"."customer_id" ASC
  run()  : ERR E_NO_KEY 
  hybrid : -{"1"=-{"customer_id"=t"7"}, "2"=-{"customer_id"=t"9"}}
  DIFFERENT
### ORDERS .> BUCKET(_["customer_id"]) .> DROP(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT -1 OFFSET 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
  hybrid : -{"1"=-{"customer_id"=t"9"}}
  DIFFERENT
### ORDERS .> BUCKET(_["customer_id"]) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])
  plan   : pure_memory | None
  run()  : ERR E_NO_KEY 
  hybrid : ERR E_NO_KEY 
  SAME
### X = ORDERS .> BUCKET(_["amount"]); X
  pl
… (truncated)
```

Run JS planner probe on the same shapes

```bash
S=$SCRATCH/verify-I && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
node $S/probe.mjs $S/shapes.txt
```

```
### ORDERS .> BUCKET(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}, "9"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC))
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(_K)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"7", "2"=t"9"}
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(COUNT(_))
  plan   : pure_sql | SELECT COUNT(*) FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"2", "2"=t"1"}
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"], RECORD("d", _K, "n", COUNT(_)))
  plan   : pure_sql | SELECT "_sub1"."customer_id" AS "d", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY "_sub1"."customer_id"
  run()  : ERR E_NO_KEY undefined
### ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["amount"])
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o" WHERE (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)) GROUP BY "o"."amount"
  run()  : -{"10"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}}, "7"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> SORT_BY(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" ORDER BY "o"."customer_id" ASC
  run()  : ERR E_NO_KEY undefined
### ORDERS .> BUCKET(_["customer_id"]) .> DROP(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT -1 OFFSET 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### X = ORDERS .> BUCKET(_["amount"]); X
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o" GROUP BY "o"."amount"
  run()  : -{"10"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}}, "5"=-{"1"=-{"customer_id"=t"7", "amount"=t"5"}}, "7"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> MAP(_["amount"])
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o"
  run()  : -{"1"=t"10", "2"=t"5", "3"=t"7"}
```

Run PHP planner probe

```bash
S=$SCRATCH/verify-I && sed -n 85,110p php/src/Sql/Sql.php; grep -n "function pureSql\|pureSql\b" php/src/Sql/Hybrid.php | head -5; # (file written, see "Reproduction scripts" above)
php $S/probe.php $S/shapes.txt
```

```
}

    /**
     * Plan the maximal SQL prefix and, when needed, an in-memory continuation.
     *
     * @param array<string, Binding>|Bindings $bindings
     * @param array<string,mixed> $options
     */
    public static function planHybrid(Program $program, string $dialect,
                                      array|Bindings $bindings = [], array $options = []): HybridPlan
    {
        return Hybrid::plan($program, $dialect, $bindings, $options);
    }

    /** @param callable(string,list<Value>,Fragment):mixed $dbRunner */
    public static function executeHybrid(HybridPlan $plan, callable $dbRunner, $context = null): mixed
    {
        return Hybrid::execute($plan, $dbRunner, $context);
    }
}
29:    public bool $pureSql;
59:        $this->pureSql = (bool) ($spec['pureSql'] ?? false);
67:        $this->pure_sql = $this->pureSql;
69:        $this->is_hybrid = !$this->pureSql && !$this->pureMemory;
75:        return !$this->pureSql && !$this->pureMemory;
### ORDERS .> BUCKET(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}, "9"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC))
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(_K)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"7", "2"=t"9"}
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(COUNT(_))
  plan   : pure_sql | SELECT COUNT(*) FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"2", "2"=t"1"}
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"], RECORD("d", _K, "n", COUNT(_)))
  plan   : pure_sql | SELECT "_sub1"."customer_id" AS "d", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY "_sub1"."customer_id"
  run()  : ERR E_NO_KEY
### ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["amount"])
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o" WHERE (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)) GROUP BY "o"."amount"
  run()  : -{"10"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}}, "7"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> SORT_BY(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" ORDER BY "o"."customer_id" ASC
  run()  : ERR E_NO_KEY
### ORDERS .> BUCKET(_["customer_id"]) .> DROP(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."custome
… (truncated)
```

Build and run C++ planner probe

```bash
S=$SCRATCH/verify-I && grep -n "SQL_SRCS :=" -A2 cpp/Makefile; grep -n "class SelError\|struct SelError\|code()\|pos()" cpp/sel.hpp | head -8; # (file written, see "Reproduction scripts" above)
cd cpp && g++ -std=c++23 -O0 -I. -o $S/probe_cpp $S/probe.cpp build/sel_sql*.o build/sel.o 2>&1 | head -20; $S/probe_cpp $S/shapes.txt
```

```
Exit code 127
32:SQL_SRCS := sel_sql.cpp sel_sql_binding.cpp sel_sql_emit.cpp sel_sql_map.cpp \
33-            sel_sql_map_data.cpp sel_sql_node.cpp sel_sql_stage1.cpp \
34-            sel_sql_translator.cpp sel_sql_hybrid.cpp
47:class SelError : public std::exception {
51:  const std::string& code() const noexcept { return code_; }
56:  Pos pos() const noexcept { return pos_; }
$SCRATCH/verify-I/probe.cpp: In function ‘int main(int, char**)’:
$SCRATCH/verify-I/probe.cpp:21:87: error: ‘sel::sql::Fragment::Mode’ has not been declared
   21 |     std::string sql = plan.sql_statement ? plan.sql_statement->as_statement(Fragment::Mode::Params) : "null";
      |                                                                                       ^~~~
/bin/bash: line 27: $SCRATCH/verify-I/probe_cpp: No such file or directory
```

Fix Mode enum and rerun C++ probe

```bash
S=$SCRATCH/verify-I && grep -n "enum class Mode" cpp/sel_sql.hpp; sed -i 's/Fragment::Mode::Params/Mode::Params/' $S/probe.cpp && cd cpp && g++ -std=c++23 -O0 -I. -o $S/probe_cpp $S/probe.cpp build/sel_sql*.o build/sel.o 2>&1 | head -20; $S/probe_cpp $S/shapes.txt
```

```
81:enum class Mode { Inline, Params, Debug };
### ORDERS .> BUCKET(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}, "9"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC))
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(_K)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"7", "2"=t"9"}
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(COUNT(_))
  plan   : pure_sql | SELECT COUNT(*) FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"2", "2"=t"1"}
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"], RECORD("d", _K, "n", COUNT(_)))
  plan   : pure_sql | SELECT "_sub1"."customer_id" AS "d", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY "_sub1"."customer_id"
  run()  : ERR E_NO_KEY
### ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["amount"])
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o" WHERE (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)) GROUP BY "o"."amount"
  run()  : -{"10"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}}, "7"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> SORT_BY(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" ORDER BY "o"."customer_id" ASC
  run()  : ERR E_NO_KEY
### ORDERS .> BUCKET(_["customer_id"]) .> DROP(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT -1 OFFSET 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### X = ORDERS .> BUCKET(_["amount"]); X
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o" GROUP BY "o"."amount"
  run()  : -{"10"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}}, "5"=-{"1"=-{"customer_id"=t"7", "amount"=t"5"}}, "7"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> MAP(_["amount"])
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o"
  run()  : -{"1"=t"10", "2"=t"5", "3"=t"7"}
```

Run Lisp planner probe

```bash
S=$SCRATCH/verify-I && # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --eval "(probe-main \"$S/shapes.txt\")" 2>&1 | grep -v "^;" | head -60
```

```
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=769299 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 2, Column: 65, File-Position: 89

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-I/probe.lisp" {1201ED65C3}>

Backtrace for: #<SB-THREAD:THREAD tid=769299 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201ED77E3}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-I/probe.lisp" {1201ED65C3}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {7833F450F4CB}> #<SB-C::SOURCE-INFO {1201E77813}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-I/probe.lisp" {1201ED65C3}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-I/probe.lisp" {1201ED65C3}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {7833F450F88B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-I/probe.lisp" {1201ED65C3}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-I/probe.lisp" {1201ED65C3}>)
9: (LOAD #P"$SCRATCH/verify-I/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-I/probe.lisp") (:EVAL . "(probe-main \"$SCRATCH/verify-I/shapes.txt\")") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Run Lisp planner probe with the SQL system loaded

```bash
S=$SCRATCH/verify-I && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (ql:quickload :sel-lang/sql))' --load $S/probe.lisp --eval "(probe-main \"$S/shapes.txt\")" 2>&1 | grep -v "^;" | head -60
```

```
### ORDERS .> BUCKET(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}, "9"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC))
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(_K)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"7", "2"=t"9"}
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(COUNT(_))
  plan   : pure_sql | SELECT COUNT(*) FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"2", "2"=t"1"}
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"], RECORD("d", _K, "n", COUNT(_)))
  plan   : CRASH The function SEL.SQL::SNODE-R is undefined.
  run()  : ERR E_NO_KEY
### ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["amount"])
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o" WHERE (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)) GROUP BY "o"."amount"
  run()  : -{"10"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}}, "7"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> SORT_BY(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" ORDER BY "o"."customer_id" ASC
  run()  : ERR E_NO_KEY
### ORDERS .> BUCKET(_["customer_id"]) .> DROP(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT -1 OFFSET 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### X = ORDERS .> BUCKET(_["amount"]); X
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o" GROUP BY "o"."amount"
  run()  : -{"10"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}}, "5"=-{"1"=-{"customer_id"=t"7", "amount"=t"5"}}, "7"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> MAP(_["amount"])
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o"
  run()  : -{"1"=t"10", "2"=t"5", "3"=t"7"}
```

Run the same JS probe against the pre-commit (8fe0e3a) JS tree

```bash
S=$SCRATCH/verify-I && mkdir -p $S/old && git archive 8fe0e3a js package.json | tar -x -C $S/old && sed "s#/home/nathan/workspaces/nth-share/sel/js#$S/old/js#g" $S/probe.mjs > $S/probe-old.mjs && node $S/probe-old.mjs $S/shapes.txt 2>&1 | head -60
```

```
### ORDERS .> BUCKET(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}, "9"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC))
  run()  : -{"7"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> TAKE(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}, "2"=-{"customer_id"=t"7", "amount"=t"5"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(_K)
  plan   : hybrid | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
  run()  : -{"1"=t"7", "2"=t"9"}
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(COUNT(_))
  plan   : pure_sql | SELECT COUNT(*) FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1"
  run()  : -{"1"=t"2", "2"=t"1"}
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"], RECORD("d", _K, "n", COUNT(_)))
  plan   : pure_sql | SELECT "_sub1"."customer_id" AS "d", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY "_sub1"."customer_id"
  run()  : ERR E_NO_KEY undefined
### ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["amount"])
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o" WHERE (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)) GROUP BY "o"."amount"
  run()  : -{"10"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}}, "7"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> BUCKET(_["customer_id"]) .> SORT_BY(_["customer_id"])
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" ORDER BY "o"."customer_id" ASC
  run()  : ERR E_NO_KEY undefined
### ORDERS .> BUCKET(_["customer_id"]) .> DROP(1)
  plan   : pure_sql | SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" LIMIT -1 OFFSET 1
  run()  : -{"1"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### X = ORDERS .> BUCKET(_["amount"]); X
  plan   : pure_memory | null
  run()  : -{"10"=-{"1"=-{"customer_id"=t"7", "amount"=t"10"}}, "5"=-{"1"=-{"customer_id"=t"7", "amount"=t"5"}}, "7"=-{"1"=-{"customer_id"=t"9", "amount"=t"7"}}}
### ORDERS .> MAP(_["amount"])
  plan   : pure_sql | SELECT "o"."amount" FROM "orders" "o"
  run()  : -{"1"=t"10", "2"=t"5", "3"=t"7"}
```

Probe the LINK-after-bare-bucket facet with explicit binders in Python

```bash
S=$SCRATCH/verify-I && # (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $S/link.py
```

```
ORDERS .> BUCKET(_["customer_id"]) .> LINK(CUSTOMERS, o, c, o["customer_id"] == c["id"]) -> pure_sql SELECT `_sub1`.* FROM (SELECT `o`.`customer_id` FROM `orders` `o` GROUP BY `o`.`customer_id`) `_sub1` INNER JOIN `customers` `c` ON (CASE WHEN (`_sub1`.`customer_id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`customer_id` AS DECIMAL(65,10)) ELSE NULL END = `c`.`id`)
  run: ERR E_NO_KEY
ORDERS .> BUCKET(_["customer_id"]) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) -> pure_sql SELECT `_sub1`.* FROM (SELECT `o`.`customer_id` FROM `orders` `o` GROUP BY `o`.`customer_id`) `_sub1` INNER JOIN `customers` `c` ON (CASE WHEN (`_sub1`.`customer_id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`customer_id` AS DECIMAL(65,10)) ELSE NULL END = `c`.`id`)
  run: ERR E_NO_KEY
```

