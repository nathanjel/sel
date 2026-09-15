# AI. A second BUCKET after a bare bucket re-opens the sealed plan: BUCKET .> BUCKET .> MAP is translated instead of refused

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "Hybrid planner: the shape guard, completed".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** mixed · **hosts:** js, python, php, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

In all four transcribed hosts a BUCKET that follows a bare BUCKET discards the 'sealed' state (the BUCKET arm unconditionally sets plan.bucket = open/null after the guard and the derived-table wrap had set 'sealed'), so `BUCKET(k) .> BUCKET(COUNT(_)) .> MAP(...)` is translated to `... GROUP BY COUNT(*)` (rejected by SQLite: "aggregate functions are not allowed in the GROUP BY clause") and classified pure_sql, while run() answers a value; `BUCKET(k) .> BUCKET(k) .> MAP(...)` executes in SQL where run() is E_NO_KEY. This breaks the commit's own CHANGELOG promise ("A MAP after anything else has followed a bare bucket is refused (E_SQL_SHAPE)"). Lisp does not get that far: wrapping any plan whose projection has a nil alias and an index node calls the undefined SEL.SQL::SNODE-R (translator.lisp:1797-1800), an UNDEFINED-FUNCTION that escapes try-translate-statement and plan-hybrid — pre-existing (introduced in e4b0432, identical on 8fe0e3a) and reachable without buckets too.

## Suggested fix — case first

In every host's BUCKET arm, refuse before ensureDerived when the incoming plan is an open or sealed bucket (e.g. JS: `if (plan.bucket !== null) refuse('E_SQL_SHAPE', 'a BUCKET over buckets: SQL keeps a bucket's members only for the projection that ends the grouping', step.pos)`), or at minimum do not overwrite a 'sealed' state; and make the planner's full-pushdown probe consult bucketRowsAreKeys(steps) before accepting a pure_sql plan. First case to add to sql/cases/24-bucket.sqlt: `ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))` → refused E_SQL_SHAPE at the second BUCKET's position (and the 3-arg spelling `BUCKET(k) .> BUCKET(COUNT(_), RECORD(...))`, plus a 25-hybrid-plans.sqlt case pinning pure_memory / a split before the first bucket). Separately, fix the pre-existing Lisp bug by replacing `snode-r` with `sel::node-r` in plan-output-fields (lisp/src/sql/translator.lisp:1797-1800) and add a case that wraps a nil-alias index projection, e.g. `ORDERS .> MAP(_["customer_id"]) .> FILTER(_ > 1)` (E_SQL_SHAPE in the other hosts today), so Lisp answers an sql-error instead of UNDEFINED-FUNCTION.

## Verifier reasoning

Mechanism verified by reading all five hosts: the pre-switch guard (js/src/sql/translator.mjs:1853, python/sel/sql/translator.py:1791-1792, php/src/Sql/Translator.php:2565-2567, cpp/sel_sql_translator.cpp:2368-2370, lisp/src/sql/translator.lisp:1920-1922) turns an open bucket 'sealed' on a second BUCKET; ensureDerived/wrapPlanAsDerivedTable carries it (js:1743 `if (plan.bucket !== null) derived.bucket = 'sealed'`); then the BUCKET arm overwrites it unconditionally (js:1943, py:1843, php:2661, cpp:2442, lisp:1988 `plan.bucket = aggNode === null ? 'open' : null`), so the following MAP sees 'open' and folds into the SECOND bucket. The planner's full-pushdown probe (js/src/sql/hybrid.mjs:31-35) never consults bucketRowsAreKeys (hybrid.mjs:124-137, which returns true for [BUCKET,BUCKET,...]); only the prefix search (line 329) does. Reproduced end-to-end in every host with my own binding set and programs (see evidence): JS/Python/PHP/C++ all emit `GROUP BY COUNT(*)` and plan pure_sql; Python execute_hybrid on SQLite raises OperationalError while run() gives -{"1"=-{"size"=t"2","n"=t"1"},"2"=-{"size"=t"1","n"=t"1"}}; the same-key variant returns rows from SQL where run() is E_NO_KEY@1:47. Lisp throws UNDEFINED-FUNCTION SNODE-R for every double-bucket shape. Introduced-status is mixed: (a) the underlying mistranslation of a second BUCKET as a nested GROUP BY over key rows is pre-existing — on 8fe0e3a the 3-arg spelling `BUCKET(k) .> BUCKET(COUNT(_), RECORD(...))` already rendered `GROUP BY COUNT(*)` as pure_sql, and base execute_hybrid for the MAP spelling already failed with the same SQLite error because the hybrid split pushed the BUCKET .> BUCKET prefix; (b) what this commit changed is that the MAP spelling is now accepted by translate_statement and planned pure_sql (base refused it only incidentally — E_SQL_SHAPE@1:78 "a row of a relation has no key" from `_K` in a row-MAP — and planned hybrid), and the promise that such a MAP is refused is new to this commit and unmet. The 'sealed' mechanism is new and its overwrite is by-this-commit. (c) The Lisp SNODE-R crash is pre-existing: base extract crashes identically, even for the single `BUCKET(k) .> MAP(...)` shape, and HEAD still crashes on `ORDERS .> MAP(_["customer_id"]) .> FILTER(_ > 1)` with no bucket at all (JS refuses E_SQL_SHAPE for that). No sql/cases or conformance file contains a double BUCKET. Severity high: run() vs execute_hybrid diverge (value vs DB error; error vs value), invalid SQL is classified pure_sql in four hosts, and Lisp diverges from the other four with a non-SqlError crash that plan-hybrid cannot fall back from.

## Verifier evidence

```
Probes in /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-AI/ (probe.mjs, probe.py, probe.php, probe.cpp, probe.lisp, probe2.lisp); base commit extracted with `git archive 8fe0e3a js python php lisp | tar -x -C verify-AI/base` (working tree untouched). Bindings: ORDERS→orders o {ID, CUSTOMER_ID, AMOUNT: NUM}; rows id/customer_id/amount = (1,1,5),(2,1,3),(3,2,7).

JS HEAD (`node probe.mjs /home/nathan/workspaces/nth-share/sel`):
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY COUNT(*)
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): ERR E_NO_KEY@1:47 ; translate_statement: SELECT "_sub1"."customer_id" AS "k", COUNT(*) AS "n" FROM (...GROUP BY "o"."customer_id") "_sub1" GROUP BY "_sub1"."customer_id" ; plan: pure_sql
### ...BUCKET(_K) .> MAP(...): translate ERR E_SQL_DEPTH@1:46, plan pure_memory
### control BUCKET(k) .> TAKE(1) .> MAP(...): ERR E_SQL_SHAPE@1:50 "a MAP over buckets must follow the BUCKET..." (sealed works when the intervening step is not BUCKET)
JS BASE (`node probe.mjs verify-AI/base`): double-bucket MAP spelling → ERR E_SQL_SHAPE@1:78 "a row of a relation has no key", plan hybrid; 3-arg spelling `BUCKET(k) .> BUCKET(COUNT(_), RECORD(...))` → already `... GROUP BY COUNT(*)`, plan pure_sql.

Python HEAD (`PYTHONPATH=$PWD/python python3 probe.py`, SQLite in-memory runner):
  double-bucket MAP spelling: translate = same GROUP BY COUNT(*) SQL; plan: pure_sql; execute_hybrid(sqlite): ERR OperationalError aggregate functions are not allowed in the GROUP BY clause; run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  same-key variant: run() E_NO_KEY@1:47 vs execute_hybrid -{"1"=-{"k"=t"1", "n"=t"1"}, "2"=-{"k"=t"2", "n"=t"1"}}
  with FILTER(COUNT(_) > 1) between: also pure_sql, `... HAVING (...) ) "_sub1" GROUP BY COUNT(*)`, OperationalError.
Python BASE (`PYTHONPATH=verify-AI/base/python`): translate ERR E_SQL_SHAPE@1:78, plan hybrid, but execute_hybrid ALSO ERR OperationalError (prefix BUCKET .> BUCKET was pushed down).

PHP HEAD (`php probe.php <repo>`): identical SQL `GROUP BY COUNT(*)`, plan pure_sql; PHP BASE: E_SQL_SHAPE, hybrid.
C++ HEAD (probe.cpp linked against cpp/build/*.o, `make -n` says nothing to be done): `SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "customer_id" FROM "orders" "o" GROUP BY "customer_id") "_sub1" GROUP BY COUNT(*)`, plan pure_sql.
Lisp HEAD (`sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load probe.lisp`): every double-bucket shape → THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined (translate-statement, try-translate-statement and plan-hybrid alike); single BUCKET .> MAP translates fine; TAKE-between shape refused E_SQL_SHAPE. Lisp BASE (boot from verify-AI/base/lisp): same SNODE-R crash for all shapes including single `BUCKET(k) .> MAP(...)`. probe2.lisp: `ORDERS .> MAP(_["customer_id"]) .> FILTER(_ > 1)` → SNODE-R crash on HEAD (JS: E_SQL_SHAPE). `git log -S"snode-r" -- lisp/src/sql/translator.lisp` → e4b0432 (before 8fe0e3a). `grep -rn "defun snode-" lisp/src/` → only snode-kind, snode-pos (stage1.lisp:41-42).

Code: js/src/sql/translator.mjs:1853 (guard), :1743 (wrap carries sealed), :1943 (overwrite), :1985 (MAP refuses only if sealed); python/sel/sql/translator.py:1791-1792, :1843; php/src/Sql/Translator.php:2565-2567, :2661; cpp/sel_sql_translator.cpp:2368-2370, :2442; lisp/src/sql/translator.lisp:1920-1922, :1988, :1797-1800 (snode-r), :2076-2078 (MAP sealed check). Planner: js/src/sql/hybrid.mjs:31-35 (full probe without bucketRowsAreKeys), :124-137, :329. CHANGELOG.md:29 promise. `grep -n "BUCKET(.*) .> BUCKET" sql/cases/*.sqlt conformance/*.selt` → no double-bucket case.
```

## Original review reports (deduplicated into this finding)

### [promises-vs-code] Second BUCKET after a bare bucket re-opens the sealed plan: BUCKET .> BUCKET .> MAP is translated (invalid GROUP BY COUNT(*)) instead of refused

*correctness · high · hosts: js, python, php, cpp, lisp*

Locations: `js/src/sql/translator.mjs:1853`; `js/src/sql/translator.mjs:1943`; `python/sel/sql/translator.py:1791`; `python/sel/sql/translator.py:1843`; `php/src/Sql/Translator.php:2565`; `php/src/Sql/Translator.php:2661`; `cpp/sel_sql_translator.cpp:2368`; `cpp/sel_sql_translator.cpp:2442`; `lisp/src/sql/translator.lisp:1920`; `lisp/src/sql/translator.lisp:1988`

CHANGELOG: 'A MAP after anything else has followed a bare bucket is refused (E_SQL_SHAPE)'. The per-step guard marks the plan 'sealed' when a BUCKET follows an open bucket, ensureDerived wraps it (derived.bucket = sealed), but the BUCKET case then unconditionally sets `plan.bucket = aggNode === null ? 'open' : null`, discarding 'sealed'. The following MAP therefore closes the SECOND bucket and the statement compiles. In SEL the second bucket's `_` is a group (list) so `BUCKET(k) .> BUCKET(COUNT(_))` is a meaningful program (bucket groups by size), but the SQL groups the KEY rows of the first bucket, and `GROUP BY COUNT(*)` is not even valid SQL. The planner (finding 1) then classifies it pure_sql. Lisp cannot even get there (see the SNODE-R finding).

Reported repro:

```
TR=1 ./run.sh cases8.txt sqlite: 'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))' -> js/py/php/cpp: pure_sql AND translate_statement = `SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY COUNT(*)`; lisp: THROW UNDEFINED-FUNCTION SNODE-R. Sqlite executor: run() = [{size=2,n=1},{size=1,n=1}], execute_hybrid = sqlite error 'aggregate functions are not allowed in the GROUP BY clause'. Also 'ORDERS .> BUCKET(k) .> BUCKET(k) .> MAP(RECORD("k", _K, "n", COUNT(_)))' -> pure_sql in four hosts, run() = E_NO_KEY.
```

### [probe-lanes-bucket-hybrid] BUCKET after an open bucket is accepted again: invalid SQL (GROUP BY COUNT(*)) in four hosts, undefined-function crash in Lisp; base commit refused E_SQL_SHAPE

*correctness · high · hosts: js, python, php, cpp, lisp*

Locations: `python/sel/sql/translator.py:1843`; `js/src/sql/translator.mjs:1943`; `php/src/Sql/Translator.php:2661`; `cpp/sel_sql_translator.cpp:2442`; `lisp/src/sql/translator.lisp:1988`; `lisp/src/sql/translator.lisp:1797`

The new bucket state machine marks the plan 'sealed' when anything but FILTER/MAP follows an open bucket, and the derived-table wrap carries 'sealed' forward — but the BUCKET arm then unconditionally overwrites the state with 'open'/None (`plan.bucket = 'open' if aggregate_node is None else None`). So BUCKET(k) .> BUCKET(agg) .> MAP(proj), which SEL evaluates as grouping the groups, is translated as a second GROUP BY over the KEY rows of the first: with an aggregate key it renders `GROUP BY COUNT(*)`, which every database rejects, and the planner classifies it pure_sql (the planner's own bucket_rows_are_keys returns true for this prefix, but the full-pushdown probe never consults it). With `BUCKET(_K)` as the key the four transcribed hosts refuse with the unrelated code E_SQL_DEPTH. In Lisp, plan-output-fields calls SNODE-R, which is not defined anywhere in the SQL package, so translate-statement, try-translate-statement and plan-hybrid all abort with UNDEFINED-FUNCTION instead of an sql-error — try-translate-statement cannot swallow it and plan-hybrid does not fall back to memory. At 8fe0e3a all of these were refused E_SQL_SHAPE, so this is a regression of the bucket work; conformance/16-bucket.selt and 24-bucket.sqlt have no double-bucket case.

Reported repro:

```
b22: ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("k", _K, "n", COUNT(_)))  run() => -{"1"=-{"k"=t"2","n"=t"2"}, "2"=-{"k"=t"1","n"=t"1"}}. py/js/php/cpp translate_statement (sqlite/mariadb/postgresql) => SELECT COUNT(*) AS "k", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY COUNT(*); plan=pure_sql; executing on sqlite => OperationalError: aggregate functions are not allowed in the GROUP BY clause. Lisp: translate-statement and plan-hybrid => UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined. b72 (with FILTER(COUNT(_) > 1) between) identical. b71: ... .> BUCKET(_K) .> MAP(...) => py/js/php/cpp REFUSED E_SQL_DEPTH@1:46, Lisp same crash. b46 (ORDERS .> FILTER(o, o["amount"] > 4) .> BUCKET(g, g["customer_id"]) .> MAP(g, ...)) => four hosts E_SQL_SHAPE@1:93, Lisp the same crash. Base 8fe0e3a (git worktree): all three => REFUSED E_SQL_SHAPE. Command: sbcl --load lisp/bin/boot.lisp --load <scratchpad>/probe-lanes/probe_lisp.lisp; python3 <scratchpad>/probe-lanes/compare.py py js php cpp lisp.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$S/probe.mjs`**

```js
const root = process.argv[2];
await import(`${root}/js/src/builtins/index.mjs`);
const { compile } = await import(`${root}/js/src/sel.mjs`);
const { Sql, Binding } = await import(`${root}/js/src/sql/index.mjs`);
const bindings = { ORDERS: Binding.relation('orders', 'o', {
  ID: Binding.column('id', 'o', 'NUM'),
  CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'),
  AMOUNT: Binding.column('amount', 'o', 'NUM'),
}) };
const rows = [{ id: '1', customer_id: '1', amount: '5' }, { id: '2', customer_id: '1', amount: '3' }, { id: '3', customer_id: '2', amount: '7' }];
const progs = [
  'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))',
  'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
  'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
  // my own: explicit binders, SUM inside second key
  'ORDERS .> BUCKET(o, o["customer_id"]) .> BUCKET(g, SUM(g .> MAP(_["amount"]))) .> MAP(g, RECORD("total", _K, "groups", COUNT(g)))',
  // my own: second bucket with its own projection (no MAP)
  'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_), RECORD("size", _K, "n", COUNT(_)))',
  // control: single bucket + MAP (should translate)
  'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
  // control: bucket .> SORT .> MAP (sealed -> refused)
  'ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
];
function f(fn) { try { const r = fn(); return typeof r === 'string' ? r : JSON.stringify(r); } catch (e) { return `ERR ${e.code ?? e.name}@${e.line}:${e.col} ${e.message}`; } }
for (const src of progs) {
  console.log('###', src);
  const p = compile(src);
  console.log('  run():', f(() => p.run({ ORDERS: rows }).dump()));
  console.log('  translate_statement(sqlite):', f(() => { const r = Sql.translateStatement(p, 'sqlite', bindings); return r.sql ?? r.toString(); }));
  if (Sql.planHybrid) {
    console.log('  plan:', f(() => { const pl = Sql.planHybrid(p, 'sqlite', bindings); return pl.pureSql ? 'pure_sql' : pl.pureMemory ? 'pure_memory' : 'hybrid'; }));
  }
}
```

**`$S/probe.lisp`**

```lisp
(defparameter *bindings*
  (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o"
                        (list (cons "ID" (sel.sql:binding-column "id" "o" "NUM"))
                              (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "o" "NUM"))
                              (cons "AMOUNT" (sel.sql:binding-column "amount" "o" "NUM")))))))
(defparameter *progs*
  '("ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(COUNT(_)) .> MAP(RECORD(\"size\", _K, \"n\", COUNT(_)))"
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(_[\"customer_id\"]) .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_)))"
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(_K) .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_)))"
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(COUNT(_), RECORD(\"size\", _K, \"n\", COUNT(_)))"
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_)))"
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> TAKE(1) .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_)))"))
(defun try (thunk)
  (handler-case (funcall thunk)
    (sel.sql:sql-error (e) (format nil "ERR ~a ~a" (sel.sql:sql-error-code e) e))
    (error (e) (format nil "THROW ~a: ~a" (type-of e) e))))
(dolist (src *progs*)
  (format t "### ~a~%" src)
  (let ((p (sel:compile-source src)))
    (format t "  translate_statement(sqlite): ~a~%"
            (try (lambda () (sel.sql:as-statement (sel.sql:translate-statement p "sqlite" *bindings*)))))
    (format t "  plan: ~a~%"
            (try (lambda () (let ((pl (sel.sql:plan-hybrid p "sqlite" *bindings*)))
                              (cond ((sel.sql:hybrid-plan-pure-sql-p pl) "pure_sql")
                                    ((sel.sql:hybrid-plan-pure-memory-p pl) "pure_memory")
                                    (t "hybrid"))))))))
```

**`$S/probe.py`**

```python
import sys, sqlite3
from sel import compile as sel_compile
from sel.sql import Sql, Binding
from sel.value import Value
bindings = {'ORDERS': Binding.relation('orders', 'o', {
    'ID': Binding.column('id', 'o', 'NUM'),
    'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM'),
    'AMOUNT': Binding.column('amount', 'o', 'NUM')})}
rows = [{'id': '1', 'customer_id': '1', 'amount': '5'}, {'id': '2', 'customer_id': '1', 'amount': '3'}, {'id': '3', 'customer_id': '2', 'amount': '7'}]
db = sqlite3.connect(':memory:')
db.execute('create table orders(id, customer_id, amount)')
db.executemany('insert into orders values (?, ?, ?)', [(r['id'], r['customer_id'], r['amount']) for r in rows])
def runner(sql, params):
    cur = db.execute(sql, [p.as_text() for p in params])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]
def f(fn):
    try:
        r = fn()
        return r if isinstance(r, str) else repr(r)
    except Exception as e:
        return f'ERR {type(e).__name__} {getattr(e, "code", "")}@{getattr(e, "line", "")}:{getattr(e, "col", "")} {e}'
progs = [
 'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_), RECORD("size", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
]
for src in progs:
    print('###', src)
    p = sel_compile(src)
    print('  run():', f(lambda: p.run({'ORDERS': rows}).dump()))
    print('  translate_statement(sqlite):', f(lambda: Sql.translate_statement(p, 'sqlite', bindings).as_statement()))
    def plan_kind():
        pl = Sql.plan_hybrid(p, 'sqlite', bindings)
        return 'pure_sql' if pl.pure_sql else 'pure_memory' if pl.pure_memory else 'hybrid'
    print('  plan:', f(plan_kind))
    def exec_():
        pl = Sql.plan_hybrid(p, 'sqlite', bindings)
        got = Sql.execute_hybrid(pl, runner, {'ORDERS': rows})
        got = got if isinstance(got, Value) else Value.from_native(got)
        return got.dump()
    print('  execute_hybrid(sqlite):', f(exec_))
```

**`$S/probe.cpp`**

```cpp
#include "sel.hpp"
#include "sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
template <class F> std::string tryit(F f) {
  try { return f(); }
  catch (const sel::sql::SqlError& e) { return std::string("ERR ") + e.code() + " " + e.what(); }
  catch (const sel::SelError& e) { return std::string("SELERR ") + e.what(); }
  catch (const std::exception& e) { return std::string("THROW ") + e.what(); }
}
int main() {
  Bindings bindings;
  bindings["ORDERS"] = Binding::relation("orders", "o",
      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
       {"CUSTOMER_ID", Binding::column("customer_id", std::nullopt, SqlKind::Num)},
       {"AMOUNT", Binding::column("amount", std::nullopt, SqlKind::Num)}});
  const char* progs[] = {
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(COUNT(_)) .> MAP(RECORD(\"size\", _K, \"n\", COUNT(_)))",
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(_[\"customer_id\"]) .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_)))",
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(_K) .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_)))",
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(COUNT(_), RECORD(\"size\", _K, \"n\", COUNT(_)))",
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_)))",
    "ORDERS .> BUCKET(_[\"customer_id\"]) .> TAKE(1) .> MAP(RECORD(\"k\", _K, \"n\", COUNT(_)))",
  };
  for (const char* src : progs) {
    std::cout << "### " << src << "\n";
    const sel::Program p = sel::compile(src);
    std::cout << "  translate_statement(sqlite): " << tryit([&] { return Sql::translate_statement(p, "sqlite", bindings).as_statement(); }) << "\n";
    std::cout << "  plan: " << tryit([&] { auto pl = Sql::plan_hybrid(p, "sqlite", bindings); return std::string(pl.pure_sql ? "pure_sql" : pl.pure_memory ? "pure_memory" : "hybrid"); }) << "\n";
  }
}
```

**`$S/probe.php`**

```php
<?php
$root = $argv[1];
require_once $root . '/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$bindings = ['ORDERS' => Binding::relation('orders', 'o', [
  'ID' => Binding::column('id', 'o', 'NUM'),
  'CUSTOMER_ID' => Binding::column('customer_id', 'o', 'NUM'),
  'AMOUNT' => Binding::column('amount', 'o', 'NUM')])];
$rows = [['id'=>'1','customer_id'=>'1','amount'=>'5'],['id'=>'2','customer_id'=>'1','amount'=>'3'],['id'=>'3','customer_id'=>'2','amount'=>'7']];
$progs = [
 'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_), RECORD("size", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
 'ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
];
function f($fn) { try { $r = $fn(); return is_string($r) ? $r : json_encode($r); } catch (\Throwable $e) { $code = method_exists($e, 'getCode') ? $e->getCode() : ''; $c = property_exists($e, 'code') ? $e->code : (method_exists($e,'code') ? $e->code() : $code); return 'ERR ' . get_class($e) . ' ' . $c . ' ' . $e->getMessage(); } }
foreach ($progs as $src) {
  echo "### $src\n";
  $p = Sel::compile($src);
  echo "  run(): ", f(fn() => $p->run(['ORDERS' => $rows])->dump()), "\n";
  echo "  translate_statement(sqlite): ", f(fn() => Sql::translateStatement($p, 'sqlite', $bindings)->asStatement()), "\n";
  echo "  plan: ", f(function () use ($p, $bindings) { $pl = Sql::planHybrid($p, 'sqlite', $bindings); return $pl->pureSql ? 'pure_sql' : ($pl->pureMemory ? 'pure_memory' : 'hybrid'); }), "\n";
}
```

**`$S/probe2.lisp`**

```lisp
(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
(defparameter *bindings*
  (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o"
                        (list (cons "ID" (sel.sql:binding-column "id" "o" :num))
                              (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "o" :num))
                              (cons "AMOUNT" (sel.sql:binding-column "amount" "o" :num)))))))
(defun try (thunk)
  (handler-case (funcall thunk)
    (sel.sql:sql-error (e) (format nil "ERR ~a ~a" (sel.sql:sql-error-code e) e))
    (error (e) (format nil "THROW ~a: ~a" (type-of e) e))))
(dolist (src '("ORDERS .> FILTER(o, o[\"amount\"] > 4) .> BUCKET(g, g[\"customer_id\"]) .> MAP(g, RECORD(\"cid\", _K, \"n\", COUNT(g)))"
               "ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(COUNT(_)) .> MAP(RECORD(\"n\", COUNT(_)))"
               "ORDERS .> BUCKET(_[\"customer_id\"]) .> SORT_BY(_[\"customer_id\"])"
               "ORDERS .> BUCKET(_[\"customer_id\"]) .> LINK(ORDERS, _[\"customer_id\"] == _2[\"customer_id\"])"))
  (format t "### ~a~%" src)
  (let ((p (sel:compile-source src)))
    (format t "  translate_statement(sqlite): ~a~%"
            (try (lambda () (sel.sql:as-statement (sel.sql:translate-statement p "sqlite" *bindings*)))))
    (format t "  try-translate-statement: ~a~%"
            (try (lambda () (let ((r (sel.sql:try-translate-statement p "sqlite" *bindings*))) (if r (sel.sql:as-statement r) "NIL")))))))
```

### Commands run and their output

Read base-commit BUCKET and MAP arms

```bash
F=$SCRATCH/old-translator.mjs; sed -n 1770,1830p $F; echo ----; grep -n "case 'MAP'" $F; sed -n "$(grep -n "case 'MAP'" $F | head -1 | cut -d: -f1),+30p" $F; echo ---- ensureDerived old; grep -n "ensureDerived\|wrapPlanAsDerivedTable" $F | head
```

```
plan.correlate = b.correlate && typeof b.correlate === 'object' && b.correlate.raw
      ? String(b.correlate.raw)
      : (typeof b.correlate === 'string' ? b.correlate : null);

    steps.reverse();

    for (const step of steps) {
      const name = step.name;
      const args = step.args;

      switch (name) {
        case 'FILTER': {
          plan = this.ensureDerived(plan, (candidate) =>
            candidate.groupBy === null && Boolean(candidate.projections || candidate.selectCols
              || candidate.limit !== null || candidate.offset !== null
              || candidate.orderBy.length || candidate.distinct));
          let binder;
          let pred;
          if (args.length === 2) {
            binder = '_';
            pred = args[1];
          } else if (args.length === 3) {
            if (!constants.isBinderName(args[1])) {
              refuse('E_SQL_SHAPE', 'the binder of FILTER must be a bare name', args[1].pos);
            }
            binder = args[1].name;
            pred = args[2];
          } else {
            refuse('E_ARITY', 'FILTER takes 2 or 3 arguments', step.pos);
          }
          if (plan.groupBy !== null) {
            plan.having.push({ binder, node: pred, pos: step.pos });
          } else {
            plan.filters.push({ binder, node: pred, pos: step.pos });
          }
          break;
        }

        case 'GROUP_BY':
        case 'BUCKET': {
          plan = this.ensureDerived(plan, (candidate) => this.planHasRowsAbove(candidate));
          let binder;
          let keyNode;
          let aggNode = null;
          if (args.length === 2) {
            binder = '_';
            keyNode = args[1];
          } else if (args.length === 3) {
            binder = '_';
            keyNode = args[1];
            aggNode = args[2];
          } else if (args.length === 4) {
            if (!constants.isBinderName(args[1])) {
              refuse('E_SQL_SHAPE', 'the binder of GROUP_BY must be a bare name', args[1].pos);
            }
            binder = args[1].name;
            keyNode = args[2];
            aggNode = args[3];
          } else {
            refuse('E_ARITY', 'GROUP_BY takes 2 to 4 arguments', step.pos);
          }
----
1963:        case 'MAP': {
        case 'MAP': {
          plan = this.ensureDerived(plan, (candidate) => this.planHasRowsAbove(candidate));
          let binder;
          let expr;
          if (args.length === 2) {
            binder = '_';
            expr = args[1];
          } else if (args.length === 3) {
            if (!constants.isBinderName(args[1])) {
              refuse('E_SQL_SHAPE', 'the binder of MAP must be a bare name', args[1].pos);
            }
            binder = args[1].name;
            expr = args[2];
          } else {
            refuse('E_ARITY', 'MAP takes 2 or 3 arguments', step.pos);
          }

          if (expr.t === 'call' && (expr.name === 'RECORD' || expr.name === 'LAZY_RECORD')) {
            const recArgs = expr.args;
            if (recArgs.length % 2 !== 0) {
              refuse('E_ARITY', 'RECORD takes an even number of arguments', expr.pos);
            }
            const projections = [];
            for (let i = 0; i < recArgs.length; i += 2) {
              const kNode = recArgs[i];
              const vNode = recArgs[i + 1];
              if (kNode.t !== 'text') {
                refuse('E_BAD_ARG', 'RECORD field names must be string literals', kNode.pos);
              }
              projections.push({
 
… (truncated)
```

Find refusals in the base-commit BUCKET arm

```bash
F=$SCRATCH/old-translator.mjs; sed -n 1830,1930p $F | grep -n "refuse\|aggNode === null\|projections ="
```

```
17:              refuse('E_ARITY', 'RECORD takes an even number of arguments', keyNode.pos);
21:                refuse('E_BAD_ARG', 'RECORD field names must be string literals', rArgs[i].pos);
44:                refuse('E_ARITY', 'RECORD takes an even number of arguments', aggNode.pos);
46:              const projections = [];
51:                  refuse('E_BAD_ARG', 'RECORD field names must be string literals', kNode.pos);
74:              plan.projections = projections;
76:              plan.projections = [
85:            const projections = [];
93:            plan.projections = projections;
```

Locate old E_SQL_SHAPE refusals in the base translator

```bash
F=$SCRATCH/old-translator.mjs; sed -n 1905,1930p $F; echo; grep -n "E_SQL_SHAPE" $F | sed -n 1,60p
```

```
plan.projections = [
                {
                  alias: null,
                  binder,
                  node: aggNode,
                },
              ];
            }
          } else {
            const projections = [];
            for (const gb of plan.groupBy) {
              projections.push({
                alias: gb.alias,
                binder: gb.binder,
                node: gb.node,
              });
            }
            plan.projections = projections;
          }
          plan.selectCols = null;
          break;
        }

        case 'SELECT_COLS': {
          plan = this.ensureDerived(plan, (candidate) => this.planHasRowsAbove(candidate));
          const colArgs = args.slice(1);

178:      refuse('E_SQL_SHAPE', 'expected a relational query or pipeline');
243:      refuse('E_SQL_SHAPE',
248:    refuse('E_SQL_SHAPE', `cannot translate a ${t} node`, n.pos);
273:        refuse('E_SQL_SHAPE',
285:        refuse('E_SQL_SHAPE',
292:      refuse('E_SQL_SHAPE',
324:      refuse('E_SQL_SHAPE',
342:      refuse('E_SQL_SHAPE',
362:        refuse('E_SQL_SHAPE', `${obj.name}["${key}"] is a list, not a SQL value`, n.pos);
366:    refuse('E_SQL_SHAPE',
425:    refuse('E_SQL_SHAPE',
468:      // BOOL guard, not before: `TRUE + 1` is E_SQL_SHAPE and stays that way.
564:          refuse('E_SQL_SHAPE',
577:          refuse('E_SQL_SHAPE',
640:        refuse('E_SQL_SHAPE',
724:      refuse('E_SQL_SHAPE',
741:        refuse('E_SQL_SHAPE',
769:      refuse('E_SQL_SHAPE',
774:      refuse('E_SQL_SHAPE',
941:        refuse('E_SQL_SHAPE',
947:        refuse('E_SQL_SHAPE',
956:    refuse('E_SQL_SHAPE', String(b.reason), n.pos);
962:        refuse('E_SQL_SHAPE',
985:          refuse('E_SQL_SHAPE', `field "${key}" is ambiguous across joined relations`, n.pos);
1017:    refuse('E_SQL_SHAPE',
1058:        if (bound.shape === Binder.NONE) refuse('E_SQL_SHAPE', String(bound.reason), src.pos);
1068:          refuse('E_SQL_SHAPE',
1100:      refuse('E_SQL_SHAPE',
1135:      refuse('E_SQL_SHAPE',
1145:      refuse('E_SQL_SHAPE',
1235:          refuse('E_SQL_SHAPE',
1365:      refuse('E_SQL_SHAPE',
1372:      refuse('E_SQL_SHAPE',
1384:      refuse('E_SQL_SHAPE',
1404:        refuse('E_SQL_SHAPE',
1557:    refuse('E_SQL_SHAPE',
1568:    refuse('E_SQL_SHAPE',
1607:    refuse('E_SQL_SHAPE',
1621:    refuse('E_SQL_SHAPE',
1793:              refuse('E_SQL_SHAPE', 'the binder of FILTER must be a bare name', args[1].pos);
1823:              refuse('E_SQL_SHAPE', 'the binder of GROUP_BY must be a bare name', args[1].pos);
1947:              refuse('E_SQL_SHAPE',
1952:              refuse('E_SQL_SHAPE',
1972:              refuse('E_SQL_SHAPE', 'the binder of MAP must be a bare name', args[1].pos);
2058:            refuse('E_SQL_SHAPE', `${name} requires a bound relation as its right side`, rightNode.pos);
2062:            refuse('E_SQL_SHAPE', `${rightNode.name} is not bound as a relation`, rightNode.pos);
2072:              refuse('E_SQL_SHAPE', 'join binders must be bare names', args[2].pos);
2160:        refuse('E_SQL_SHAPE', 'SORT on a multi-field relation requires a key expression; use SORT_BY', step.pos);
2171:          refuse('E_SQL_SHAPE', 'the binder of SORT must be a bare name', args[1].pos);
2209:        refuse('E_SQL_SHAPE', 'the binder of SORT_BY must be a bare name', args[1].pos);
2427:      refuse('E_SQL_SHAPE', `the binder of ${n.name} must be a bare name`, n.args[1].pos);
2485:  refuse('E_SQL_SHAPE',
2521:      refuse('E_SQL_SHAP
… (truncated)
```

Run JS probe on HEAD and on the base commit extract

```bash
S=$SCRATCH/verify-AI && mkdir -p $S/base && git archive 8fe0e3a js python php | tar -x -C $S/base && ls $S/base && # (file written, see "Reproduction scripts" above)
echo "=== HEAD"; node $S/probe.mjs /home/nathan/workspaces/nth-share/sel; echo "=== BASE 8fe0e3a"; node $S/probe.mjs $S/base
```

```
Exit code 1
js
php
python
=== HEAD
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): [object Object]
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): ERR E_NO_KEY@1:47 no key "customer_id"
  translate_statement(sqlite): [object Object]
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"1"}, "2"=-{"k"=t"2", "n"=t"1"}}
  translate_statement(sqlite): ERR E_SQL_DEPTH@1:46 this expression nests deeper than SEL will evaluate (200), so there is nothing to translate; the evaluator answers E_DEPTH for it
  plan: pure_memory
### ORDERS .> BUCKET(o, o["customer_id"]) .> BUCKET(g, SUM(g .> MAP(_["amount"]))) .> MAP(g, RECORD("total", _K, "groups", COUNT(g)))
file:///home/nathan/workspaces/nth-share/sel/js/src/errors.mjs:20
  throw new SelError(code, message, pos);
        ^

SelError: SUM takes 2 to 3 arguments, got 1
    at fail (file:///home/nathan/workspaces/nth-share/sel/js/src/errors.mjs:20:9)
    at Parser.parseCall (file:///home/nathan/workspaces/nth-share/sel/js/src/parser.mjs:398:7)
    at Parser.parsePrimary (file:///home/nathan/workspaces/nth-share/sel/js/src/parser.mjs:358:78)
    at Parser.parsePostfix (file:///home/nathan/workspaces/nth-share/sel/js/src/parser.mjs:271:21)
    at Parser.parsePrefix (file:///home/nathan/workspaces/nth-share/sel/js/src/parser.mjs:258:17)
    at Parser.parseTerm (file:///home/nathan/workspaces/nth-share/sel/js/src/parser.mjs:165:21)
    at Parser.parseList (file:///home/nathan/workspaces/nth-share/sel/js/src/parser.mjs:157:23)
    at Parser.parseSequence (file:///home/nathan/workspaces/nth-share/sel/js/src/parser.mjs:139:21)
    at Parser.parsePipeStep (file:///home/nathan/workspaces/nth-share/sel/js/src/parser.mjs:303:28)
    at Parser.parsePostfix (file:///home/nathan/workspaces/nth-share/sel/js/src/parser.mjs:285:21) {
  code: 'E_ARITY',
  line: 1,
  col: 52,
  offset: 51
}

Node.js v22.12.0
=== BASE 8fe0e3a
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): ERR E_SQL_SHAPE@1:78 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan: hybrid
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): ERR E_NO_KEY@1:47 no key "customer_id"
  translate_statement(sqlite): ERR E_SQL_SHAPE@1:83 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan: hybrid
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"1"}, "2"=-{"k"=t"2", "n"=t"1"}}
  translate_statement(sqlite): ERR E_SQL_SHAPE@1:69 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan: hybrid
### ORDERS .> BUCKET(o, o["customer_id"]) .> BUCKET(g, SUM(g .> MAP(_["amount"]))) .> MAP(g, RECORD("
… (truncated)
```

Rerun JS probe on HEAD and base

```bash
S=$SCRATCH/verify-AI && sed -i 's/return r.sql ?? r.toString();/return r.asStatement();/; s/SUM(g .> MAP(_\["amount"\]))/SUM(g, _["amount"])/' $S/probe.mjs && echo "=== HEAD"; node $S/probe.mjs /home/nathan/workspaces/nth-share/sel; echo "=== BASE 8fe0e3a"; node $S/probe.mjs $S/base
```

```
=== HEAD
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY COUNT(*)
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): ERR E_NO_KEY@1:47 no key "customer_id"
  translate_statement(sqlite): SELECT "_sub1"."customer_id" AS "k", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY "_sub1"."customer_id"
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"1"}, "2"=-{"k"=t"2", "n"=t"1"}}
  translate_statement(sqlite): ERR E_SQL_DEPTH@1:46 this expression nests deeper than SEL will evaluate (200), so there is nothing to translate; the evaluator answers E_DEPTH for it
  plan: pure_memory
### ORDERS .> BUCKET(o, o["customer_id"]) .> BUCKET(g, SUM(g, _["amount"])) .> MAP(g, RECORD("total", _K, "groups", COUNT(g)))
  run(): ERR E_UNDEF_VAR@1:18 undefined variable O
  translate_statement(sqlite): ERR E_SQL_SHAPE@1:99 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan: pure_memory
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_), RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY COUNT(*)
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"2"}, "2"=-{"k"=t"2", "n"=t"1"}}
  translate_statement(sqlite): SELECT "o"."customer_id" AS "k", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"2"}}
  translate_statement(sqlite): ERR E_SQL_SHAPE@1:50 a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
  plan: pure_memory
=== BASE 8fe0e3a
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): ERR E_SQL_SHAPE@1:78 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan: hybrid
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): ERR E_NO_KEY@1:47 no key "customer_id"
  translate_statement(sqlite): ERR E_SQL_SHAPE@1:83 a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan: hybrid
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"1"}, "2"=-{"k"=t"2", "n"=t"1"}}
  tra
… (truncated)
```

See Lisp loading and unit-test API usage

```bash
cat lisp/bin/boot.lisp 2>/dev/null | head -30; ls lisp/bin; grep -n "plan-hybrid\|translate-statement\|make-binding\|relation-binding\|execute-hybrid" lisp/tests/unit.lisp | head -20
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
342:           (frag (sel.sql:translate-statement p "postgresql" bindings))
350:           (frag (sel.sql:translate-statement p "postgresql" bindings))
356:      (handler-case (sel.sql:translate-statement p "postgresql" bindings)
365:           (frag (sel.sql:translate-statement p "postgresql" bindings))
371:           (frag (sel.sql:translate-statement p "postgresql" bindings))
384:           (frag (sel.sql:translate-statement p "postgresql" bindings))
392:           (frag (sel.sql:translate-statement p "postgresql" bindings))
399:           (frag (sel.sql:translate-statement p "postgresql" bindings))
414:           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
424:        (let ((res (sel.sql:execute-hybrid plan mock-runner)))
430:           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
439:             (res (sel.sql:execute-hybrid plan mock-runner)))
447:           (plan (sel.sql:plan-hybrid p "postgresql" nil))
452:      (let ((res (sel.sql:execute-hybrid plan nil ctx)))
510:         (plan (sel.sql:plan-hybrid prog "postgresql" bindings)))
526:           (res (sel.sql:execute-hybrid plan mock-db)))
556:         (plan (sel.sql:plan-hybrid prog "postgresql" bindings)))
571:           (res (sel.sql:execute-hybrid plan mock-db)))
611:         (plan (sel.sql:plan-hybrid prog "postgresql" bindings)))
624:           (res (sel.sql:execute-hybrid plan mock-db)))
```

Run the Lisp probe on HEAD

```bash
S=$SCRATCH/verify-AI && # (file written, see "Reproduction scripts" above)
sbcl --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp 2>&1 | grep -v "^;" | tail -30
```

```
BSD-style licenses.  See the CREDITS and COPYING files in the
distribution for more information.
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=842817 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 2, Column: 48, File-Position: 72

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-AI/probe.lisp" {1201ED6613}>

Backtrace for: #<SB-THREAD:THREAD tid=842817 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7893}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7893}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7893}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7893}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201ED7843}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-AI/probe.lisp" {1201ED6613}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {7AE12B59F4CB}> #<SB-C::SOURCE-INFO {1201E77813}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-AI/probe.lisp" {1201ED6613}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-AI/probe.lisp" {1201ED6613}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {7AE12B59F88B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-AI/probe.lisp" {1201ED6613}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-AI/probe.lisp" {1201ED6613}>)
9: (LOAD #P"$SCRATCH/verify-AI/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-AI/probe.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

See the exact sbcl invocation for the SQL suite

```bash
cat lisp/bin/sqlt; grep -n "quickload\|asdf:load\|require" lisp/bin/sqlt.lisp | head
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
```

Run the Lisp probe with the SQL system loaded

```bash
S=$SCRATCH/verify-AI && sed -i '1i (let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' $S/probe.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp 2>&1 | grep -v "^;" | tail -30
```

```
Backtrace for: #<SB-THREAD:THREAD tid=843423 "main thread" RUNNING {1204028083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SEL.SQL:SQL-ERROR {12022884F3}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SEL.SQL:SQL-ERROR {12022884F3}>)
2: (INVOKE-DEBUGGER #<SEL.SQL:SQL-ERROR {12022884F3}>)
3: (ERROR SEL.SQL:SQL-ERROR :CODE "E_SQL_BINDING" :MESSAGE "a binding has type \"NUM\"; use one of NUM, TEXT, BOOL, BIN, UNKNOWN, LIST, STATEMENT" :LINE 0 :COL 0 :OFFSET 0)
4: (SEL.SQL::REFUSE "E_SQL_BINDING" "a binding has type \"NUM\"; use one of NUM, TEXT, BOOL, BIN, UNKNOWN, LIST, STATEMENT" NIL)
5: (SEL.SQL:BINDING-COLUMN "id" "o" "NUM" :EXACT NIL :SARGABLE NIL :GUARD NIL :COLLATION NIL :PREFILTER NIL :SPLIT-SARGABLE NIL)
6: (SB-INT:SIMPLE-EVAL-IN-LEXENV (SEL.SQL:BINDING-COLUMN "id" "o" "NUM") #<NULL-LEXENV>)
7: (SB-INT:SIMPLE-EVAL-IN-LEXENV (CONS "ID" (SEL.SQL:BINDING-COLUMN "id" "o" "NUM")) #<NULL-LEXENV>)
8: (SB-INT:SIMPLE-EVAL-IN-LEXENV (LIST (CONS "ID" (SEL.SQL:BINDING-COLUMN "id" "o" "NUM")) (CONS "CUSTOMER_ID" (SEL.SQL:BINDING-COLUMN "customer_id" "o" "NUM")) (CONS "AMOUNT" (SEL.SQL:BINDING-COLUMN "amount" "o" "NUM"))) #<NULL-LEXENV>)
9: (SB-INT:SIMPLE-EVAL-IN-LEXENV (SEL.SQL:BINDING-RELATION "orders" "o" (LIST (CONS "ID" (SEL.SQL:BINDING-COLUMN "id" "o" "NUM")) (CONS "CUSTOMER_ID" (SEL.SQL:BINDING-COLUMN "customer_id" "o" "NUM")) (CONS "AMOUNT" (SEL.SQL:BINDING-COLUMN "amount" "o" "NUM")))) #<NULL-LEXENV>)
10: (SB-INT:SIMPLE-EVAL-IN-LEXENV (CONS "ORDERS" (SEL.SQL:BINDING-RELATION "orders" "o" (LIST (CONS "ID" (SEL.SQL:BINDING-COLUMN "id" "o" "NUM")) (CONS "CUSTOMER_ID" (SEL.SQL:BINDING-COLUMN "customer_id" "o" "NUM")) (CONS "AMOUNT" (SEL.SQL:BINDING-COLUMN "amount" "o" "NUM"))))) #<NULL-LEXENV>)
11: (SB-INT:SIMPLE-EVAL-IN-LEXENV (LIST (CONS "ORDERS" (SEL.SQL:BINDING-RELATION "orders" "o" (LIST (CONS "ID" #) (CONS "CUSTOMER_ID" #) (CONS "AMOUNT" #))))) #<NULL-LEXENV>)
12: (SB-INT:SIMPLE-EVAL-IN-LEXENV (SB-IMPL::%DEFPARAMETER (QUOTE *BINDINGS*) (LIST (CONS "ORDERS" (SEL.SQL:BINDING-RELATION "orders" "o" (LIST # # #)))) (SB-C:SOURCE-LOCATION)) #<NULL-LEXENV>)
13: (SB-INT:SIMPLE-EVAL-IN-LEXENV (DEFPARAMETER *BINDINGS* (LIST (CONS "ORDERS" (SEL.SQL:BINDING-RELATION "orders" "o" (LIST # # #))))) #<NULL-LEXENV>)
14: (EVAL-TLF (DEFPARAMETER *BINDINGS* (LIST (CONS "ORDERS" (SEL.SQL:BINDING-RELATION "orders" "o" (LIST # # #))))) 1 NIL)
15: ((LABELS SB-FASL::EVAL-FORM :IN SB-INT:LOAD-AS-SOURCE) (DEFPARAMETER *BINDINGS* (LIST (CONS "ORDERS" (SEL.SQL:BINDING-RELATION "orders" "o" (LIST # # #))))) 1)
16: ((LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) (DEFPARAMETER *BINDINGS* (LIST (CONS "ORDERS" (SEL.SQL:BINDING-RELATION "orders" "o" (LIST # # #))))) :CURRENT-INDEX 1)
17: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {7618816974CB}> #<SB-C::SOURCE-INFO {1201E577E3}> SB-C::INPUT-ERROR-IN-LOAD)
18: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-AI/probe.lisp" {1201EB7543}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
19: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-AI/probe.lisp" {1201EB7543}> NIL)
20: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {76188169788B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-AI/probe.lisp" {1201EB7543}> NIL #<SB-INT:FORM-TRACKING-STREAM for 
… (truncated)
```

Rerun the Lisp probe with keyword column types

```bash
S=$SCRATCH/verify-AI && sed -i 's/ "NUM"))/ :num))/g' $S/probe.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp 2>&1 | grep -v "^;" | tail -30
```

```
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_), RECORD("size", _K, "n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): SELECT "o"."customer_id" AS "k", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): ERR E_SQL_SHAPE E_SQL_SHAPE at 1:50: a MAP over buckets must follow the BUCKET, with at most a ~
FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
  plan: pure_memory
```

Run the Lisp probe against the base-commit extract

```bash
S=$SCRATCH/verify-AI && git archive 8fe0e3a lisp | tar -x -C $S/base && sbcl --noinform --disable-debugger --non-interactive --load $S/base/lisp/bin/boot.lisp --load $S/probe.lisp 2>&1 | grep -v "^;" | tail -20
```

```
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_), RECORD("size", _K, "n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  plan: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
```

Run the Python probe with SQLite execution on HEAD and base

```bash
S=$SCRATCH/verify-AI && # (file written, see "Reproduction scripts" above)
echo "=== HEAD"; PYTHONPATH=$PWD/python python3 $S/probe.py; echo "=== BASE"; PYTHONPATH=$S/base/python python3 $S/probe.py
```

```
=== HEAD
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY COUNT(*)
  plan: pure_sql
  execute_hybrid(sqlite): ERR OperationalError @: aggregate functions are not allowed in the GROUP BY clause
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): ERR SelError E_NO_KEY@1:47 E_NO_KEY at 1:47: no key "customer_id"
  translate_statement(sqlite): SELECT "_sub1"."customer_id" AS "k", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY "_sub1"."customer_id"
  plan: pure_sql
  execute_hybrid(sqlite): -{"1"=-{"k"=t"1", "n"=t"1"}, "2"=-{"k"=t"2", "n"=t"1"}}
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"1"}, "2"=-{"k"=t"2", "n"=t"1"}}
  translate_statement(sqlite): ERR SqlError E_SQL_DEPTH@1:46 E_SQL_DEPTH at 1:46: this expression nests deeper than SEL will evaluate (200), so there is nothing to translate; the evaluator answers E_DEPTH for it
  plan: pure_memory
  execute_hybrid(sqlite): -{"1"=-{"k"=t"1", "n"=t"1"}, "2"=-{"k"=t"2", "n"=t"1"}}
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_), RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY COUNT(*)
  plan: pure_sql
  execute_hybrid(sqlite): ERR OperationalError @: aggregate functions are not allowed in the GROUP BY clause
### ORDERS .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}}
  translate_statement(sqlite): SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC))) "_sub1" GROUP BY COUNT(*)
  plan: pure_sql
  execute_hybrid(sqlite): ERR OperationalError @: aggregate functions are not allowed in the GROUP BY clause
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"2"}, "2"=-{"k"=t"2", "n"=t"1"}}
  translate_statement(sqlite): SELECT "o"."customer_id" AS "k", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan: pure_sql
  execute_hybrid(sqlite): -{"1"=-{"k"=t"1", "n"=t"2"}, "2"=-{"k"=t"2", "n"=t"1"}}
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"2"}}
  translate_statement(sqlite): ERR SqlError E_SQL_SHAPE@1:50 E_SQL_SHAPE at 1:50: a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
  plan: pure_memory
  execute_hybrid(sqlite): -{"1"=-{"k"=t"1", "n"=t"2"}}
=== BASE
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): ERR SqlError E_SQL_SHAPE@1:78 E_SQL_SHAPE at 1:78: a row 
… (truncated)
```

Check whether the C++ build is current relative to the sources

```bash
git check-ignore cpp/build && ls -la --time-style=+%H:%M cpp/sel_sql_translator.cpp cpp/sel_sql_hybrid.cpp cpp/sel.cpp cpp/build/sel_sql_translator.o cpp/build/sel_sql_hybrid.o cpp/build/sqlt | awk '{print $6,$7}'; cd cpp && make -n 2>/dev/null | head -5
```

```
cpp/build
09:04 cpp/build/sel_sql_hybrid.o
09:08 cpp/build/sel_sql_translator.o
11:45 cpp/build/sqlt
11:41 cpp/sel.cpp
09:04 cpp/sel_sql_hybrid.cpp
09:07 cpp/sel_sql_translator.cpp
make: Nothing to be done for 'all'.
```

Write the C++ probe and check SqlError accessor names

```bash
S=$SCRATCH/verify-AI && # (file written, see "Reproduction scripts" above)
grep -n "code()\|const char\* code\|std::string code" cpp/sel_sql.hpp | head -3; grep -n "^SQL_SRCS" cpp/Makefile
```

```
41:  SqlError(std::string code, std::string message, Pos pos = {});
43:  const std::string& code() const noexcept { return code_; }
55:  std::string code_;
32:SQL_SRCS := sel_sql.cpp sel_sql_binding.cpp sel_sql_emit.cpp sel_sql_map.cpp \
```

Compile and run the C++ probe against the built objects

```bash
S=$SCRATCH/verify-AI && sed -n 32,35p cpp/Makefile && c++ -std=c++23 -O1 -I cpp -o $S/probe_cpp $S/probe.cpp cpp/build/sel_sql.o cpp/build/sel_sql_binding.o cpp/build/sel_sql_emit.o cpp/build/sel_sql_map.o cpp/build/sel_sql_map_data.o cpp/build/sel_sql_node.o cpp/build/sel_sql_stage1.o cpp/build/sel_sql_translator.o cpp/build/sel_sql_hybrid.o cpp/build/sel.o 2>&1 | head -20 && $S/probe_cpp
```

```
Exit code 127
SQL_SRCS := sel_sql.cpp sel_sql_binding.cpp sel_sql_emit.cpp sel_sql_map.cpp \
            sel_sql_map_data.cpp sel_sql_node.cpp sel_sql_stage1.cpp \
            sel_sql_translator.cpp sel_sql_hybrid.cpp
SQL_OBJS := $(addprefix $(BUILD)/,$(SQL_SRCS:.cpp=.o))
$SCRATCH/verify-AI/probe.cpp: In function ‘int main()’:
$SCRATCH/verify-AI/probe.cpp:13:11: error: no match for ‘operator[]’ (operand types are ‘sel::sql::Bindings’ and ‘const char [7]’)
   13 |   bindings["ORDERS"] = Binding::relation("orders", "o",
      |           ^
/bin/bash: line 1: $SCRATCH/verify-AI/probe_cpp: No such file or directory
```

Fix the C++ probe bindings, compile and run

```bash
S=$SCRATCH/verify-AI && python3 - <<'EOF'
import re,os
p=os.environ.get('S','$SCRATCH/verify-AI')+'/probe.cpp'
s=open(p).read()
s=s.replace('''  Bindings bindings;
  bindings["ORDERS"] = Binding::relation("orders", "o",
      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
       {"CUSTOMER_ID", Binding::column("customer_id", std::nullopt, SqlKind::Num)},
       {"AMOUNT", Binding::column("amount", std::nullopt, SqlKind::Num)}});''',
'''  const Bindings bindings({{"ORDERS", Binding::relation("orders", "o",
      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
       {"CUSTOMER_ID", Binding::column("customer_id", std::nullopt, SqlKind::Num)},
       {"AMOUNT", Binding::column("amount", std::nullopt, SqlKind::Num)}})}});''')
open(p,'w').write(s)
EOF
c++ -std=c++23 -O1 -I cpp -o $S/probe_cpp $S/probe.cpp cpp/build/sel_sql.o cpp/build/sel_sql_binding.o cpp/build/sel_sql_emit.o cpp/build/sel_sql_map.o cpp/build/sel_sql_map_data.o cpp/build/sel_sql_node.o cpp/build/sel_sql_stage1.o cpp/build/sel_sql_translator.o cpp/build/sel_sql_hybrid.o cpp/build/sel.o 2>&1 | head -20 && $S/probe_cpp
```

```
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  translate_statement(sqlite): SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "customer_id" FROM "orders" "o" GROUP BY "customer_id") "_sub1" GROUP BY COUNT(*)
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): SELECT "_sub1"."customer_id" AS "k", COUNT(*) AS "n" FROM (SELECT "customer_id" FROM "orders" "o" GROUP BY "customer_id") "_sub1" GROUP BY "_sub1"."customer_id"
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): ERR E_SQL_DEPTH this expression nests deeper than SEL will evaluate (200), so there is nothing to translate; the evaluator answers E_DEPTH for it
  plan: pure_memory
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_), RECORD("size", _K, "n", COUNT(_)))
  translate_statement(sqlite): SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "customer_id" FROM "orders" "o" GROUP BY "customer_id") "_sub1" GROUP BY COUNT(*)
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): SELECT "customer_id" AS "k", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "customer_id"
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  translate_statement(sqlite): ERR E_SQL_SHAPE a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
  plan: pure_memory
```

Run the PHP probe on HEAD and base

```bash
S=$SCRATCH/verify-AI && # (file written, see "Reproduction scripts" above)
echo "=== HEAD"; php $S/probe.php /home/nathan/workspaces/nth-share/sel; echo "=== BASE"; php $S/probe.php $S/base
```

```
=== HEAD
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY COUNT(*)
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): ERR Sel\SelError E_NO_KEY no key "customer_id"
  translate_statement(sqlite): SELECT "_sub1"."customer_id" AS "k", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY "_sub1"."customer_id"
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"1"}, "2"=-{"k"=t"2", "n"=t"1"}}
  translate_statement(sqlite): ERR Sel\Sql\SqlError E_SQL_DEPTH this expression nests deeper than SEL will evaluate (200), so there is nothing to translate; the evaluator answers E_DEPTH for it
  plan: pure_memory
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_), RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): SELECT COUNT(*) AS "size", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id") "_sub1" GROUP BY COUNT(*)
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"2"}, "2"=-{"k"=t"2", "n"=t"1"}}
  translate_statement(sqlite): SELECT "o"."customer_id" AS "k", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan: pure_sql
### ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"2"}}
  translate_statement(sqlite): ERR Sel\Sql\SqlError E_SQL_SHAPE a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
  plan: pure_memory
=== BASE
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"2", "n"=t"1"}, "2"=-{"size"=t"1", "n"=t"1"}}
  translate_statement(sqlite): ERR Sel\Sql\SqlError E_SQL_SHAPE a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan: hybrid
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): ERR Sel\SelError E_NO_KEY no key "customer_id"
  translate_statement(sqlite): ERR Sel\Sql\SqlError E_SQL_SHAPE a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan: hybrid
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K) .> MAP(RECORD("k", _K, "n", COUNT(_)))
  run(): -{"1"=-{"k"=t"1", "n"=t"1"}, "2"=-{"k"=t"2", "n"=t"1"}}
  translate_statement(sqlite): ERR Sel\Sql\SqlError E_SQL_SHAPE a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does
  plan: hybrid
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_), RECORD("size", _K, "n", COUNT(_)))
  run(): -{"1"=-{"size"=t"
… (truncated)
```

Check SEL's in-memory meaning of the b46 program and 3-arg BUCKET with a binder-looking arg

```bash
node js/bin/sel.mjs -e 'ORDERS = LIST(RECORD("customer_id", 1, "amount", 5), RECORD("customer_id", 1, "amount", 3), RECORD("customer_id", 2, "amount", 7)); ORDERS .> FILTER(o, o["amount"] > 4) .> BUCKET(g, g["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(g)))'; node js/bin/sel.mjs -e 'ORDERS = LIST(RECORD("customer_id", 1, "amount", 5), RECORD("customer_id", 1, "amount", 3), RECORD("customer_id", 2, "amount", 7)); ORDERS .> BUCKET(g, g["customer_id"])'
```

```
Exit code 1
E_UNDEF_VAR at line 1 column 180: undefined variable G
E_UNDEF_VAR at line 1 column 150: undefined variable G
```

Probe more Lisp shapes for the SNODE-R crash

```bash
S=$SCRATCH/verify-AI && # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe2.lisp 2>&1 | grep -v "^;" | tail -12
```

```
### ORDERS .> FILTER(o, o["amount"] > 4) .> BUCKET(g, g["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(g)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  try-translate-statement: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("n", COUNT(_)))
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  try-translate-statement: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> BUCKET(_["customer_id"]) .> SORT_BY(_["customer_id"])
  translate_statement(sqlite): SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" ORDER BY "o"."customer_id" ASC
  try-translate-statement: SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" ORDER BY "o"."customer_id" ASC
### ORDERS .> BUCKET(_["customer_id"]) .> LINK(ORDERS, _["customer_id"] == _2["customer_id"])
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  try-translate-statement: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
```

Probe whether the Lisp SNODE-R crash is reachable without buckets

```bash
S=$SCRATCH/verify-AI && sed -i 's|(dolist (src .(".*|(dolist (src (list "ORDERS .> MAP(_[\\"customer_id\\"]) .> FILTER(_ > 1)" "ORDERS .> MAP(_[\\"customer_id\\"]) .> TAKE(1) .> MAP(_ + 1)"))|' $S/probe2.lisp && sed -n 11,12p $S/probe2.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe2.lisp 2>&1 | grep -v "^;" | tail -6; node -e '
import("/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs").then(async()=>{const {compile}=await import("/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs");const {Sql,Binding}=await import("/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs");const b={ORDERS:Binding.relation("orders","o",{CUSTOMER_ID:Binding.column("customer_id","o","NUM")})};for(const s of ["ORDERS .> MAP(_[\"customer_id\"]) .> FILTER(_ > 1)","ORDERS .> MAP(_[\"customer_id\"]) .> TAKE(1) .> MAP(_ + 1)"]){try{console.log("js:",Sql.translateStatement(compile(s),"sqlite",b).asStatement())}catch(e){console.log("js: ERR",e.code,e.message)}}})'
```

```
(dolist (src (list "ORDERS .> MAP(_[\"customer_id\"]) .> FILTER(_ > 1)" "ORDERS .> MAP(_[\"customer_id\"]) .> TAKE(1) .> MAP(_ + 1)"))
               "ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(COUNT(_)) .> MAP(RECORD(\"n\", COUNT(_)))"
19: (SB-IMPL::TOPLEVEL-INIT)
20: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
21: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
22: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
js: ERR E_SQL_SHAPE _ names a row, and the relation does not say which of its fields a bare reference means; give the binding a "scalar", or index the field you want
js: ERR E_SQL_SHAPE _ names a row, and the relation does not say which of its fields a bare reference means; give the binding a "scalar", or index the field you want
```

Rerun the non-bucket Lisp wrap probe

```bash
S=$SCRATCH/verify-AI && python3 - <<'EOF'
p='$SCRATCH/verify-AI/probe2.lisp'
lines=open(p).read().split('\n')
out=[]
skip=False
for l in lines:
    if l.startswith('(dolist'):
        out.append('(dolist (src (list "ORDERS .> MAP(_[\\"customer_id\\"]) .> FILTER(_ > 1)" "ORDERS .> MAP(_[\\"customer_id\\"]) .> TAKE(1) .> MAP(_ + 1)"))')
        skip=True; continue
    if skip:
        if l.strip().startswith('"ORDERS'): continue
        skip=False
    out.append(l)
open(p,'w').write('\n'.join(out))
EOF
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe2.lisp 2>&1 | grep -v "^;" | tail -6
```

```
### ORDERS .> MAP(_["customer_id"]) .> FILTER(_ > 1)
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  try-translate-statement: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
### ORDERS .> MAP(_["customer_id"]) .> TAKE(1) .> MAP(_ + 1)
  translate_statement(sqlite): THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
  try-translate-statement: THROW UNDEFINED-FUNCTION: The function SEL.SQL::SNODE-R is undefined.
```

