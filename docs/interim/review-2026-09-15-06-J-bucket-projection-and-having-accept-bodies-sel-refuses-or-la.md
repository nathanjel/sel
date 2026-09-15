# J. Bucket projection and HAVING accept bodies SEL refuses or lacks (member fields via the group binder, later-MAP aliases, MIN/MAX(g, body), two-argument SUM with _)

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "The bucket body's scope is the evaluator's".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** mixed · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

All three facets reproduce. (1)+(2): in every host, `BUCKET(k) .> MAP(g, RECORD(..., g["amount"]))`, a HAVING that reads an alias the later MAP defines or a member field, and `MAX(_, _["amount"])` in a bucket projection all translate and plan `pure_sql` (SQLite returns rows) while `run()` raises `E_NO_KEY` at the same column on all five hosts; for the pipeline spelling this lane divergence is introduced by this commit (at 8fe0e3a `translate_statement` refused these with `E_SQL_SHAPE` and the old key-split hybrid plan raised the same `E_NO_KEY` as `run()`), while the `BUCKET(src, key, proj)` spelling and the AVG/MIN/MAX arm were already accepting them before. (3): Lisp's `with-row` always binds `_`/`_1` to the row whereas the other four bind `_` only when the plan has joins, so `SUM(g, _["amount"])` inside an explicit-binder MAP is `pure_sql` on Lisp and refused `E_SQL_UNBOUND` (hybrid/pure_memory) on the others — pre-existing, and broader than reported: `ORDERS .> FILTER(g, _["amount"] > 6)` is pure_sql on Lisp and answers rows where SEL raises `E_UNDEF_VAR`.

## Suggested fix — case first

Smallest fix, all five hosts: in bucketProjection/HAVING rendering bind the group binder (and `_`) to a GROUP-shaped binder rather than Binder.ROW so that indexing it (`g["amount"]`, `_["total"]` in a HAVING that precedes the MAP) refuses E_SQL_SHAPE at the index node, and let only the aggregate arms (COUNT(g), SUM(g, x, body), SUM(g, body)) open the row; delete the AVG/MIN/MAX per-group arm (SEL has no such form — every program reaching it raises). Resolve HAVING aliases through aggregateAliases only for a FILTER that follows a *projected* bucket (3-arg spelling or after the MAP). For Lisp, make `with-row` bind `_`/`_1`/source alias only when the plan has joins, as the other four do. Cases to add first (sql/cases/24-bucket.sqlt, `--- as statement`): `stmt.bucket.refuse-member-field-through-the-group`: `ITEMS .> BUCKET(_["dept"]) .> MAP(g, RECORD("dept", _K, "amt", g["amount"]))` => `--- error E_SQL_SHAPE 1:60` (same column SEL's E_NO_KEY names, 1:72 in the ORDERS probe); `stmt.bucket.refuse-having-alias-before-the-map`: `... BUCKET(_["dept"]) .> FILTER(_["total"] > 1) .> MAP(RECORD("dept", _K, "total", SUM(_, _["amount"])))` => E_SQL_SHAPE at the `_["total"]` index; `stmt.bucket.refuse-max-over-the-group`: `... BUCKET(_["dept"], RECORD("dept", _K, "mx", MAX(_, _["amount"])))` => refusal; and `stmt.map.explicit-binder-leaves-underscore-unbound`: `ITEMS .> MAP(g, RECORD("x", _["amount"]))` => `E_SQL_UNBOUND 1:30`, which pins the Lisp `with-row` divergence. Extend python/tests/test_unit.py::test_bucket_plans_answer_what_the_evaluator_answers_on_sqlite with the refused shapes asserting that plan_hybrid is pure_memory and execute_hybrid raises the same code and position as run().

## Verifier reasoning

Facets 1 and 2 are one mechanism: `bucketProjection` (js/src/sql/translator.mjs:1758-1830, registering aliases at :1789; php/src/Sql/Translator.php:2490; python/sel/sql/translator.py:1752; cpp/sel_sql_translator.cpp:2309; lisp/src/sql/translator.lisp:1879) renders every projection value under `withRow(src, binder, ...)`, i.e. the group binder is a ROW binder, so `g["amount"]` becomes a bare column under GROUP BY, and the pre-existing per-group AVG/MIN/MAX arm (js:696, php:672, py:689, cpp:1621, lisp:1085) turns `MAX(_, body)` into `MAX(col)` although docs/LANGUAGE.md:644 defines MIN/MAX as variadic scalars (AVG is E_UNKNOWN_FUNC at compile time, so unreachable through Program). The HAVING pushed at js:1876 / php:2589 / cpp:2394 / lisp:1942 / py:1807 is rendered after the MAP has filled `aggregateAliases`, so `indexBinder` (js:966-973, php:1150-1154, py:944-946) resolves `_["total"]` to the aggregate that does not yet exist when SEL runs the FILTER. Every host agrees byte-for-byte, so this is a lane divergence (SQL rows vs evaluator error), not a host divergence. docs/SQL-TRANSLATION.md §11.4 states the core promise: "a translation that passes is supposed to mean the contract holds", and the CHANGELOG line for this commit claims the fold removed acceptance "for bodies SEL refuses"; the fold instead made the pipeline spelling reach the 3-arg spelling's acceptance. The commit's diff to js/src/sql/translator.mjs shows the projection code was moved verbatim from the BUCKET arm into `bucketProjection` and the MAP arm now calls it (js:2004-2008). Facet 3: lisp/src/sql/translator.lisp:1362-1366 (`with-row`) binds binder, `_`, `_1` and the source alias unconditionally (unchanged since 8fe0e3a), while js:1258-1262, py:1208-1213, php:1557-1558, cpp:1935-1938 bind `_`/`_1` only when `joins` is non-empty. That yields different classification and SQL between hosts today (c21 repro reproduced exactly) and, for `FILTER(g, _[...])` / `MAP(g, _[...])`, Lisp answers rows where every evaluator raises E_UNDEF_VAR 1:21 / 1:30. Severity high: differing results between lanes in all five hosts (facets 1-2) and between hosts (facet 3), and the CHANGELOG/§11.4 promise is unmet. Introduced: mixed — pipeline-spelling acceptance is new to this commit; the 3-arg spelling, the AVG/MIN/MAX arm and the Lisp `_` binding pre-date it.

## Verifier evidence

```
Probe scripts in /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-J/ (probe.py, probe.mjs, probe.php, probe.cpp -> probe_cpp, probe.lisp; old/ holds `git archive 8fe0e3a js`). Bindings: ORDERS=orders o {CUSTOMER_ID NUM, AMOUNT NUM}; rows (7,10)(7,5)(9,7)(9,12)(3,1); dialect sqlite.

Identical output on JS, PHP, Python, C++ (commands: `node probe.mjs`, `php probe.php`, `PYTHONPATH=$PWD/python python3 probe.py`, `c++ -std=c++23 -I. probe.cpp build/sel_sql*.o build/sel.o && ./probe_cpp`) and Lisp for the first seven shapes:
a-pipe  ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))  run: ERR E_NO_KEY 1:72 | stmt: SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id" | plan: pure_sql | executed on sqlite (python): -{"1"=-{"cid"=t"3","amt"=t"1"},"2"=-{"cid"=t"7","amt"=t"10"},"3"=-{"cid"=t"9","amt"=t"7"}}
a-3arg  ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))  run: ERR E_NO_KEY 1:62 | same SQL, pure_sql, rows
b-pipe  ... BUCKET(k) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))  run: ERR E_NO_KEY 1:47 | stmt: ... GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC)) | pure_sql | executed: -{"1"=-{"cid"=t"7","total"=t"15"},"2"=-{"cid"=t"9","total"=t"19"}}
c-pipe  ... BUCKET(k) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("cid", _K, "n", COUNT(_)))  run: ERR E_NO_KEY 1:47 | stmt: ... HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC)) | pure_sql | executed: -{"1"=-{"cid"=t"7","n"=t"2"}}
c-pipe2 ... FILTER(_["amount"] > 6) ...  run: ERR E_NO_KEY 1:47 | HAVING (CAST("o"."amount" ...) > ...) | pure_sql | rows
max-pipe ... BUCKET(k) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))  run: ERR E_NO_KEY 1:75 | stmt: SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id" | pure_sql | executed: -{"1"=-{"cid"=t"3","mx"=t"1"},"2"=-{"cid"=t"7","mx"=t"5"},"3"=-{"cid"=t"9","mx"=t"7"}}
max-3arg  run: ERR E_NO_KEY 1:68 | same SQL, pure_sql

Host divergence (facet 3):
sum2-pipe ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "t", SUM(g, _["amount"])))  run (all 5): -{"1"=-{"cid"=t"7","t"=t"15"},"2"=-{"cid"=t"9","t"=t"19"},"3"=-{"cid"=t"3","t"=t"1"}} | js/php/py/cpp: stmt REFUSED E_SQL_UNBOUND 1:76, plan pure_memory | lisp: stmt SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "t" FROM "orders" "o" GROUP BY "o"."customer_id", plan pure_sql
sum2-take (reported c21): js/php/py/cpp REFUSED E_SQL_UNBOUND 1:102, plan hybrid `SELECT "o".* FROM "orders" "o" LIMIT 4`; lisp pure_sql `SELECT "_sub1"."customer_id" AS "cid", COUNT(*) AS "n", COALESCE(SUM("_sub1"."amount"), 0) AS "t" FROM (SELECT "o".* FROM "orders" "o" LIMIT 4) "_sub1" GROUP BY "_sub1"."customer_id"`
sum2-3arg BUCKET(g, g["customer_id"], RECORD(..., SUM(g, _["amount"]))): four hosts REFUSED E_SQL_UNBOUND 1:69 / pure_memory; lisp pure_sql (same at 8fe0e3a for JS: REFUSED E_SQL_UNBOUND 1:69)
map-underscore ORDERS .> MAP(g, RECORD("x", _["amount"]))  run (all 5): ERR E_UNDEF_VAR 1:30 | four hosts REFUSED E_SQL_UNBOUND 1:30 pure_memory | lisp: SELECT "o"."amount" AS "x" FROM "orders" "o", pure_sql
filter-underscore ORDERS .> FILTER(g, _["amount"] > 6)  run: ERR E_UNDEF_VAR 1:21 | four hosts REFUSED E_SQL_UNBOUND 1:21 | lisp: SELECT "o".* FROM "orders" "o" WHERE (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)), pure_sql

Reported REPL repros rerun on all five (`node js/bin/sel.mjs -e`, `php php/bin/sel -e`, `cpp/build/sel -e`, `lisp/bin/sel -e`, `python3 -m sel -e`): `LIST(...) .> BUCKET(_["cat"]) .> MAP(g, RECORD("cat", _K, "amt", g["v"]))` => E_NO_KEY 1:112 everywhere; `BUCKET(_["cat"], RECORD("cat", _K, "mx", MAX(_, 5)))` => E_NOT_NUM 1:104 everywhere; the FILTER(_["total"]) form => E_NO_KEY 1:87 everywhere.

Pre-commit (old JS tree from `git archive 8fe0e3a js`, `node old/probe-old.mjs`, `node old/exec-old.mjs`): a-pipe stmt REFUSED E_SQL_SHAPE 1:60, plan hybrid `SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"`, executing that plan => ERR E_NO_KEY 1:72 (same as run); b-pipe REFUSED E_SQL_SHAPE 1:83, executed => E_NO_KEY 1:47; max-pipe REFUSED E_SQL_SHAPE 1:57, executed => E_NO_KEY 1:75; a-3arg and max-3arg translated pure_sql exactly as now. `git show 8fe0e3a:<translator>` shows the AVG/MIN/MAX arm at js:696, py:689, lisp:1085, cpp:1621, php:672 and Lisp `with-row` binding "_" at lisp:1363 unchanged.

Code read: js/src/sql/translator.mjs:696 (AVG/MIN/MAX arm), :1758-1830 bucketProjection (:1789 aggregateAliases), :1876 having.push, :2004-2008 MAP folds into bucketProjection, :1244-1262 withRow binds `_` only under joins, :966-973 indexBinder alias resolution; php/src/Sql/Translator.php:672, :2490, :2589, :1150-1154, :1557-1558; python/sel/sql/translator.py:689, :1752, :1807, :944-946, :1208-1213; cpp/sel_sql_translator.cpp:1621, :2309, :2394, :1935-1938; lisp/src/sql/translator.lisp:1085, :1362-1366, :1879, :1942, :2278. docs/LANGUAGE.md:644 (MIN/MAX variadic). docs/SQL-TRANSLATION.md:1996-2001 ("a translation that passes is supposed to mean the contract holds"). CHANGELOG.md:29. `grep -n "MAX(\|MIN(" sql/cases/24-bucket.sqlt sql/cases/25-hybrid-plans.sqlt` => no matches.
```

## Original review reports (deduplicated into this finding)

### [bucket-translator] The folded projection and the HAVING still accept bodies SEL refuses: member fields through the group binder and aliases defined only by the later MAP

*correctness · medium · hosts: js, php, python, cpp, lisp*

Locations: `js/src/sql/translator.mjs:1783`; `js/src/sql/translator.mjs:1873`; `php/src/Sql/Translator.php:2484`; `php/src/Sql/Translator.php:2588`; `python/sel/sql/translator.py:1747`; `python/sel/sql/translator.py:1806`; `cpp/sel_sql_translator.cpp:2303`; `cpp/sel_sql_translator.cpp:2392`; `lisp/src/sql/translator.lisp:1877`; `lisp/src/sql/translator.lisp:1938`

The CHANGELOG presents the fold as closing 'accepted for bodies SEL refuses'. Three shapes still translate although run() raises E_NO_KEY on every host: (a) BUCKET(k) .> MAP(g, RECORD("amt", g["amount"])) — the group is a list, but the projection emits a bare non-aggregated column under GROUP BY (MariaDB returns an arbitrary member's value; MySQL with ONLY_FULL_GROUP_BY errors); (b) BUCKET(k) .> FILTER(_["total"] > 1) .> MAP(RECORD(..., "total", SUM(...))) — the HAVING is rendered after the MAP has filled aggregateAliases, so it resolves a name that does not exist when the FILTER runs in SEL; (c) BUCKET(k) .> FILTER(_["dept"] $== "x") .> MAP(...) — a member field inside the HAVING. Same output in all five hosts; the lane divergence is SQL rows vs an evaluator error.

Reported repro:

```
Scratch 99-probe.sqlt p25: ITEMS .> BUCKET(_["dept"]) .> MAP(g, RECORD("dept", _K, "amt", g["amount"])) => SELECT `dept` AS `dept`, `amount` AS `amt` FROM `items` GROUP BY `dept` (all five); REPL: LIST(RECORD("cat","A","v",1), RECORD("cat","B","v",2)) .> BUCKET(_["cat"]) .> MAP(g, RECORD("cat", _K, "amt", g["v"])) => E_NO_KEY at 1:118 (all five). p24: ... .> FILTER(_["total"] > 1) .> MAP(RECORD("dept", _K, "total", SUM(_, _["amount"]))) => ... HAVING (COALESCE(SUM(`amount`), 0) > 1); REPL equivalent => E_NO_KEY at 1:93. probe_sqlite.py runs all three: run() ERR E_NO_KEY, SQLite returns rows.
```

### [probe-lanes-bucket-hybrid] MIN/MAX(group, body) in a bucket projection is translated as a SQL aggregate, but SEL has no such form: pure_sql answers where run() raises

*correctness · medium · hosts: js, python, php, cpp, lisp*

Locations: `js/src/sql/translator.mjs:696`; `python/sel/sql/translator.py:1717`; `docs/LANGUAGE.md:644`

The statement translator's call() treats AVG/MIN/MAX with a ROW-shaped first argument as per-group aggregates (`MAX(o.amount)`), but docs/LANGUAGE.md defines MIN/MAX as variadic scalars and AVG does not exist (E_UNKNOWN_FUNC). The evaluator therefore raises for MAX(_, _["amount"]) inside a bucket while the planner classifies the pipeline pure_sql and the database returns a per-group maximum. Through the projection form this was already reachable at 8fe0e3a; the diff's folding of `BUCKET(k) .> MAP(proj)` into the projection makes the pipeline spelling reach it too, and the CHANGELOG says such bodies are "accepted for bodies SEL refuses" no longer. No 24-bucket.sqlt case mentions MIN or MAX.

Reported repro:

```
b73: ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))  run() => E_NO_KEY@1:75 in all five hosts; translate_statement (all dialects, all hosts) => SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"; plan_hybrid => pure_sql; executed on sqlite => -{"1"=-{"cid"=t"3","mx"=t"1"}, "2"=-{"cid"=t"7","mx"=t"5"}, "3"=-{"cid"=t"9","mx"=t"7"}}. b24 (MAX and MIN over status groups) identical pattern. node js/bin/sel.mjs -e 'AVG((1,2,3))' => E_UNKNOWN_FUNC.
```

### [probe-lanes-bucket-hybrid] Two-argument SUM with `_` inside an explicit-binder MAP: Lisp translates, four hosts refuse E_SQL_UNBOUND, so classification differs

*coherence · medium · hosts: lisp, python, js, php, cpp*

Locations: `lisp/src/sql/translator.lisp:1988`; `python/sel/sql/translator.py:1717`; `js/src/sql/translator.mjs:678`

SUM(g, body) in SEL binds `_` to each element of g (2-arg form), and the evaluator answers 15/19 for the program below. Inside MAP(g, RECORD(...)) closing a bucket, the Lisp translator resolves `_` in the SUM body to the group row and renders COALESCE(SUM(_sub1.amount), 0); the other four hosts look `_` up in the MAP's frame (which binds only g) and refuse E_SQL_UNBOUND@1:102. The plan is pure_sql on Lisp and hybrid (prefix LIMIT 4, bucket in memory) on the others, with different SQL. Both answers are correct today; the divergence surfaces as soon as a fixture or the SQL fuzzer generates an explicit MAP binder with a two-argument aggregate, and tools/fuzz-sql.sh compares codes.

Reported repro:

```
c21: ORDERS .> TAKE(4) .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(g), "t", SUM(g, _["amount"])))  run() => -{"1"=-{"cid"=t"7","n"=t"2","t"=t"15"}, "2"=-{"cid"=t"9","n"=t"2","t"=t"19"}}
  py/js/php/cpp translate_statement => REFUSED E_SQL_UNBOUND@1:102; plan => hybrid, SQL SELECT `o`.* FROM `orders` `o` LIMIT 4
  lisp translate_statement => SELECT `_sub1`.`customer_id` AS `cid`, COUNT(*) AS `n`, COALESCE(SUM(`_sub1`.`amount`), 0) AS `t` FROM (SELECT `o`.* FROM `orders` `o` LIMIT 4) `_sub1` GROUP BY `_sub1`.`customer_id`; plan => pure_sql.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$SCRATCH/verify-J/probe.py`**

```python
import sqlite3, sys
from sel import compile as sel_compile
from sel.value import Value
from sel.sql import Binding, Sql
from sel.errors import SelError
from sel.sql.errors import SqlError

bindings = {'ORDERS': Binding.relation('orders', 'o', {
    'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM'),
    'AMOUNT': Binding.column('amount', 'o', 'NUM')})}
rows = [{'customer_id': '7', 'amount': '10'}, {'customer_id': '7', 'amount': '5'},
        {'customer_id': '9', 'amount': '7'}, {'customer_id': '9', 'amount': '12'},
        {'customer_id': '3', 'amount': '1'}]
db = sqlite3.connect(':memory:')
db.execute('create table orders(customer_id, amount)')
db.executemany('insert into orders values (?, ?)', [(r['customer_id'], r['amount']) for r in rows])

def runner(sql, params):
    cur = db.execute(sql, [p.as_text() for p in params])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]

shapes = [
 ('a-pipe',  'ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))'),
 ('a-3arg',  'ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))'),
 ('b-pipe',  'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))'),
 ('b-3arg',  'ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "total", SUM(_, _["amount"]))) .> FILTER(_["total"] > 6)'),
 ('c-pipe',  'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("cid", _K, "n", COUNT(_)))'),
 ('c-pipe2', 'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["amount"] > 6) .> MAP(RECORD("cid", _K, "n", COUNT(_)))'),
 ('max-pipe','ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))'),
 ('max-3arg','ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "mx", MAX(_, _["amount"])))'),
 ('avg-3arg','ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "av", AVG(_, _["amount"])))'),
 ('sum2-pipe','ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "t", SUM(g, _["amount"])))'),
 ('sum2-take','ORDERS .> TAKE(4) .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(g), "t", SUM(g, _["amount"])))'),
 ('sum2-3arg','ORDERS .> BUCKET(g, g["customer_id"], RECORD("cid", _K, "t", SUM(g, _["amount"])))'),
 ('sum2-x-3arg','ORDERS .> BUCKET(g, g["customer_id"], RECORD("cid", _K, "t", SUM(g, x, x["amount"])))'),
]
for tag, src in shapes:
    print('==', tag, '::', src)
    prog = sel_compile(src)
    try:
        print('  run      :', prog.run({'ORDERS': rows}).dump())
    except SelError as e:
        print('  run      : ERR', e.code, f'{e.line}:{e.col}')
    try:
        frag = Sql.translate_statement(prog, 'sqlite', bindings)
        print('  stmt     :', frag.as_statement('params'))
    except SqlError as e:
        print('  stmt     : REFUSED', e.code, f'{e.line}:{e.col}')
    plan = Sql.plan_hybrid(prog, 'sqlite', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    print('  plan     :', kind, plan.sql_statement.as_statement('params') if plan.sql_statement else None)
    try:
        got = Sql.execute_hybrid(plan, runner, {'ORDERS': rows})
        got = got if isinstance(got, Value) else Value.from_native(got)
        print('  executed :', got.dump())
    except SelError as e:
        print('  executed : ERR', e.code, f'{e.line}:{e.col}')
    except Exception as e:
        print('  executed : EXC', type(e).__name__, e)
```

**`$SCRATCH/verify-J/probe.lisp`**

```lisp
(defparameter *bindings*
  (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o"
                        (list (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "o" :num))
                              (cons "AMOUNT" (sel.sql:binding-column "amount" "o" :num)))))))
(defparameter *rows*
  (list (list (cons "customer_id" "7") (cons "amount" "10"))
        (list (cons "customer_id" "7") (cons "amount" "5"))
        (list (cons "customer_id" "9") (cons "amount" "7"))
        (list (cons "customer_id" "9") (cons "amount" "12"))
        (list (cons "customer_id" "3") (cons "amount" "1"))))
(defparameter *shapes*
  '(("a-pipe" "ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(g, RECORD(\"cid\", _K, \"amt\", g[\"amount\"]))")
    ("a-3arg" "ORDERS .> BUCKET(_[\"customer_id\"], RECORD(\"cid\", _K, \"amt\", _[\"amount\"]))")
    ("b-pipe" "ORDERS .> BUCKET(_[\"customer_id\"]) .> FILTER(_[\"total\"] > 6) .> MAP(RECORD(\"cid\", _K, \"total\", SUM(_, _[\"amount\"])))")
    ("c-pipe" "ORDERS .> BUCKET(_[\"customer_id\"]) .> FILTER(_[\"customer_id\"] == 7) .> MAP(RECORD(\"cid\", _K, \"n\", COUNT(_)))")
    ("c-pipe2" "ORDERS .> BUCKET(_[\"customer_id\"]) .> FILTER(_[\"amount\"] > 6) .> MAP(RECORD(\"cid\", _K, \"n\", COUNT(_)))")
    ("max-pipe" "ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(RECORD(\"cid\", _K, \"mx\", MAX(_, _[\"amount\"])))")
    ("max-3arg" "ORDERS .> BUCKET(_[\"customer_id\"], RECORD(\"cid\", _K, \"mx\", MAX(_, _[\"amount\"])))")
    ("sum2-pipe" "ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(g, RECORD(\"cid\", _K, \"t\", SUM(g, _[\"amount\"])))")
    ("sum2-take" "ORDERS .> TAKE(4) .> BUCKET(_[\"customer_id\"]) .> MAP(g, RECORD(\"cid\", _K, \"n\", COUNT(g), \"t\", SUM(g, _[\"amount\"])))")
    ("sum2-3arg" "ORDERS .> BUCKET(g, g[\"customer_id\"], RECORD(\"cid\", _K, \"t\", SUM(g, _[\"amount\"])))")
    ("map-underscore" "ORDERS .> MAP(g, RECORD(\"x\", _[\"amount\"]))")
    ("filter-underscore" "ORDERS .> FILTER(g, _[\"amount\"] > 6)")
    ("map-alias" "ORDERS .> MAP(g, RECORD(\"x\", o[\"amount\"]))")))
(dolist (s *shapes*)
  (destructuring-bind (tag src) s
    (format t "== ~a :: ~a~%" tag src)
    (let ((p (sel:compile-source src)))
      (handler-case (format t "  run      : ~a~%" (sel:value-dump (sel:run p (list (cons "ORDERS" *rows*)))))
        (sel:sel-error (e) (format t "  run      : ERR ~a ~a:~a~%" (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e))))
      (handler-case (format t "  stmt     : ~a~%" (sel.sql:as-statement (sel.sql:translate-statement p "sqlite" *bindings*) :params))
        (sel.sql:sql-error (e) (format t "  stmt     : REFUSED ~a ~a:~a~%" (sel.sql:sql-error-code e) (sel.sql:sql-error-line e) (sel.sql:sql-error-col e))))
      (let ((plan (sel.sql:plan-hybrid p "sqlite" *bindings*)))
        (format t "  plan     : ~a ~a~%"
                (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                      ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                      (t "hybrid"))
                (let ((f (sel.sql:hybrid-plan-sql-statement plan))) (and f (sel.sql:as-statement f :params))))))))
```

**`$SCRATCH/verify-J/probe.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const bindings = { ORDERS: Binding.relation('orders', 'o', {
  CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'),
  AMOUNT: Binding.column('amount', 'o', 'NUM') }) };
const rows = [{customer_id:'7',amount:'10'},{customer_id:'7',amount:'5'},{customer_id:'9',amount:'7'},{customer_id:'9',amount:'12'},{customer_id:'3',amount:'1'}];
const shapes = [
 ['a-pipe',  'ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))'],
 ['a-3arg',  'ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))'],
 ['b-pipe',  'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))'],
 ['c-pipe',  'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("cid", _K, "n", COUNT(_)))'],
 ['c-pipe2', 'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["amount"] > 6) .> MAP(RECORD("cid", _K, "n", COUNT(_)))'],
 ['max-pipe','ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))'],
 ['max-3arg','ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "mx", MAX(_, _["amount"])))'],
 ['sum2-pipe','ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "t", SUM(g, _["amount"])))'],
 ['sum2-take','ORDERS .> TAKE(4) .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(g), "t", SUM(g, _["amount"])))'],
 ['sum2-3arg','ORDERS .> BUCKET(g, g["customer_id"], RECORD("cid", _K, "t", SUM(g, _["amount"])))'],
 ['map-underscore', 'ORDERS .> MAP(g, RECORD("x", _["amount"]))'],
 ['filter-underscore', 'ORDERS .> FILTER(g, _["amount"] > 6)'],
];
for (const [tag, src] of shapes) {
  console.log('==', tag, '::', src);
  const prog = compile(src);
  try { console.log('  run      :', prog.run({ ORDERS: rows }).dump()); }
  catch (e) { console.log('  run      : ERR', e.code, `${e.line}:${e.col}`); }
  try { console.log('  stmt     :', Sql.translateStatement(prog, 'sqlite', bindings).asStatement('params')); }
  catch (e) { console.log('  stmt     : REFUSED', e.code, `${e.line}:${e.col}`); }
  const plan = Sql.planHybrid(prog, 'sqlite', bindings);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  console.log('  plan     :', kind, plan.sqlStatement ? plan.sqlStatement.asStatement('params') : null);
}
```

**`$SCRATCH/verify-J/probe.php`**

```php
<?php
declare(strict_types=1);
require_once '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Binding; use Sel\Sql\Sql;
$bindings = ['ORDERS' => Binding::relation('orders', 'o', [
  'CUSTOMER_ID' => Binding::column('customer_id', 'o', 'NUM'),
  'AMOUNT' => Binding::column('amount', 'o', 'NUM')])];
$rows = [['customer_id'=>'7','amount'=>'10'],['customer_id'=>'7','amount'=>'5'],['customer_id'=>'9','amount'=>'7'],['customer_id'=>'9','amount'=>'12'],['customer_id'=>'3','amount'=>'1']];
$shapes = [
 ['a-pipe',  'ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))'],
 ['a-3arg',  'ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))'],
 ['b-pipe',  'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))'],
 ['c-pipe',  'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("cid", _K, "n", COUNT(_)))'],
 ['c-pipe2', 'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["amount"] > 6) .> MAP(RECORD("cid", _K, "n", COUNT(_)))'],
 ['max-pipe','ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))'],
 ['max-3arg','ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "mx", MAX(_, _["amount"])))'],
 ['sum2-pipe','ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "t", SUM(g, _["amount"])))'],
 ['sum2-take','ORDERS .> TAKE(4) .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(g), "t", SUM(g, _["amount"])))'],
 ['sum2-3arg','ORDERS .> BUCKET(g, g["customer_id"], RECORD("cid", _K, "t", SUM(g, _["amount"])))'],
 ['map-underscore', 'ORDERS .> MAP(g, RECORD("x", _["amount"]))'],
 ['filter-underscore', 'ORDERS .> FILTER(g, _["amount"] > 6)'],
];
foreach ($shapes as [$tag, $src]) {
  echo "== $tag :: $src\n";
  $prog = Sel::compile($src);
  try { echo "  run      : " . $prog->run(['ORDERS' => $rows])->dump() . "\n"; }
  catch (\Sel\SelError $e) { echo "  run      : ERR {$e->code} {$e->line}:{$e->col}\n"; }
  try { echo "  stmt     : " . Sql::translateStatement($prog, 'sqlite', $bindings)->asStatement('params') . "\n"; }
  catch (\Sel\Sql\SqlError $e) { echo "  stmt     : REFUSED {$e->code} {$e->line}:{$e->col}\n"; }
  $plan = Sql::planHybrid($prog, 'sqlite', $bindings);
  $kind = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
  echo "  plan     : $kind " . ($plan->sqlStatement ? $plan->sqlStatement->asStatement('params') : 'null') . "\n";
}
```

**`$SCRATCH/verify-J/probe.cpp`**

```cpp
#include "sel.hpp"
#include "sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main() {
  Bindings bindings({{"ORDERS", Binding::relation("orders", "o",
      {{"CUSTOMER_ID", Binding::column("customer_id", "o", SqlKind::Num)},
       {"AMOUNT", Binding::column("amount", "o", SqlKind::Num)}})}});
  auto row = [](const char* c, const char* a) {
    return sel::Value::record({"customer_id", "amount"}, {sel::Value::text(c), sel::Value::text(a)}); };
  sel::Value rows = sel::Value::list({row("7","10"), row("7","5"), row("9","7"), row("9","12"), row("3","1")});
  const char* shapes[][2] = {
   {"a-pipe",  "ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(g, RECORD(\"cid\", _K, \"amt\", g[\"amount\"]))"},
   {"a-3arg",  "ORDERS .> BUCKET(_[\"customer_id\"], RECORD(\"cid\", _K, \"amt\", _[\"amount\"]))"},
   {"b-pipe",  "ORDERS .> BUCKET(_[\"customer_id\"]) .> FILTER(_[\"total\"] > 6) .> MAP(RECORD(\"cid\", _K, \"total\", SUM(_, _[\"amount\"])))"},
   {"c-pipe",  "ORDERS .> BUCKET(_[\"customer_id\"]) .> FILTER(_[\"customer_id\"] == 7) .> MAP(RECORD(\"cid\", _K, \"n\", COUNT(_)))"},
   {"c-pipe2", "ORDERS .> BUCKET(_[\"customer_id\"]) .> FILTER(_[\"amount\"] > 6) .> MAP(RECORD(\"cid\", _K, \"n\", COUNT(_)))"},
   {"max-pipe","ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(RECORD(\"cid\", _K, \"mx\", MAX(_, _[\"amount\"])))"},
   {"max-3arg","ORDERS .> BUCKET(_[\"customer_id\"], RECORD(\"cid\", _K, \"mx\", MAX(_, _[\"amount\"])))"},
   {"sum2-pipe","ORDERS .> BUCKET(_[\"customer_id\"]) .> MAP(g, RECORD(\"cid\", _K, \"t\", SUM(g, _[\"amount\"])))"},
   {"sum2-take","ORDERS .> TAKE(4) .> BUCKET(_[\"customer_id\"]) .> MAP(g, RECORD(\"cid\", _K, \"n\", COUNT(g), \"t\", SUM(g, _[\"amount\"])))"},
   {"sum2-3arg","ORDERS .> BUCKET(g, g[\"customer_id\"], RECORD(\"cid\", _K, \"t\", SUM(g, _[\"amount\"])))"},
   {"map-underscore", "ORDERS .> MAP(g, RECORD(\"x\", _[\"amount\"]))"},
   {"filter-underscore", "ORDERS .> FILTER(g, _[\"amount\"] > 6)"},
  };
  for (auto& s : shapes) {
    std::cout << "== " << s[0] << " :: " << s[1] << "\n";
    sel::Program prog = sel::compile(s[1]);
    try { sel::Value ctx = sel::Value::none(); ctx.set("ORDERS", rows.clone());
          std::cout << "  run      : " << prog.run(ctx).dump() << "\n"; }
    catch (const sel::SelError& e) { std::cout << "  run      : ERR " << e.code() << " " << e.line() << ":" << e.col() << "\n"; }
    try { std::cout << "  stmt     : " << Sql::translate_statement(prog, "sqlite", bindings).as_statement(sel::sql::Fragment::Mode::Params) << "\n"; }
    catch (const sel::sql::SqlError& e) { std::cout << "  stmt     : REFUSED " << e.code() << " " << e.line() << ":" << e.col() << "\n"; }
    auto plan = Sql::plan_hybrid(prog, "sqlite", bindings);
    std::cout << "  plan     : " << (plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid") << " "
              << (plan.sql_statement ? plan.sql_statement->as_statement(sel::sql::Fragment::Mode::Params) : std::string("null")) << "\n";
  }
}
```

**`$OLD/exec-old.mjs`**

```js
import { compile } from '$OLD/js/src/sel.mjs';
import { Sql, Binding } from '$OLD/js/src/sql/index.mjs';
const bindings = { ORDERS: Binding.relation('orders', 'o', { CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'), AMOUNT: Binding.column('amount', 'o', 'NUM') }) };
const keyRows = [{customer_id:'3'},{customer_id:'7'},{customer_id:'9'}];
for (const src of [
 'ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))',
 'ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))',
 'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))',
]) {
  const plan = Sql.planHybrid(compile(src), 'sqlite', bindings);
  const runner = (sql) => { console.log('  old prefix SQL:', sql); return keyRows; };
  try { console.log(src, '\n  old hybrid executed:', Sql.executeHybrid(plan, runner, {}).dump()); }
  catch (e) { console.log(src, '\n  old hybrid executed: ERR', e.code, e.line + ':' + e.col); }
}
```

### Commands run and their output

Read bucket conformance cases

```bash
cat conformance/16-bucket.selt | head -150
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

Read bucket SQL cases

```bash
cat sql/cases/24-bucket.sqlt
```

```
# Statements: BUCKET and HAVING in relational pipelines.
#
# Covers BUCKET key forms (single, LIST, RECORD),
# aggregate projections (COUNT(*), SUM with COALESCE),
# pre-filtering (WHERE) vs post-filtering (HAVING),
# sorting by aggregate aliases in ORDER BY,
# pagination with BUCKET, and dialect parity.

### name: stmt.bucket.basic
--- dialect
mariadb
--- bindings
{"ITEMS": {"kind": "relation", "from": "items", "fields": {"DEPT": {"kind": "column", "column": "dept", "type": "TEXT"}}}}
--- as
statement
--- source
ITEMS .> BUCKET(_["dept"])
--- expect
SELECT `dept` FROM `items` GROUP BY `dept`
===

### name: stmt.bucket.multi-list
--- dialect
mariadb
--- bindings
{"ITEMS": {"kind": "relation", "from": "items", "fields": {"DEPT": {"kind": "column", "column": "dept", "type": "TEXT"}, "CATEGORY": {"kind": "column", "column": "category", "type": "TEXT"}}}}
--- as
statement
--- source
ITEMS .> BUCKET((_["dept"], _["category"]))
--- expect
SELECT `dept`, `category` FROM `items` GROUP BY `dept`, `category`
===

### name: stmt.bucket.multi-record
--- dialect
mariadb
--- bindings
{"ITEMS": {"kind": "relation", "from": "items", "fields": {"DEPT": {"kind": "column", "column": "dept", "type": "TEXT"}, "CATEGORY": {"kind": "column", "column": "category", "type": "TEXT"}}}}
--- as
statement
--- source
ITEMS .> BUCKET(RECORD("dept", _["dept"], "category", _["category"]))
--- expect
SELECT `dept` AS `dept`, `category` AS `category` FROM `items` GROUP BY `dept`, `category`
===

### name: stmt.bucket.aggregates
--- dialect
mariadb
--- bindings
{"ITEMS": {"kind": "relation", "from": "items", "fields": {"DEPT": {"kind": "column", "column": "dept", "type": "TEXT"}, "AMOUNT": {"kind": "column", "column": "amount", "type": "NUM"}}}}
--- as
statement
--- source
ITEMS .> BUCKET(_["dept"], RECORD("dept", _K, "cnt", COUNT(_), "total", SUM(_, _["amount"])))
--- expect
SELECT `dept` AS `dept`, COUNT(*) AS `cnt`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` GROUP BY `dept`
===

### name: stmt.bucket.custom-binder
--- dialect
mariadb
--- bindings
{"ITEMS": {"kind": "relation", "from": "items", "fields": {"DEPT": {"kind": "column", "column": "dept", "type": "TEXT"}, "AMOUNT": {"kind": "column", "column": "amount", "type": "NUM"}}}}
--- as
statement
--- source
ITEMS .> BUCKET(r, r["dept"], RECORD("dept", _K, "total", SUM(r, x, x["amount"])))
--- expect
SELECT `dept` AS `dept`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` GROUP BY `dept`
===

### name: stmt.bucket.where-and-having
--- dialect
mariadb
--- bindings
{"ITEMS": {"kind": "relation", "from": "items", "fields": {"DEPT": {"kind": "column", "column": "dept", "type": "TEXT"}, "AMOUNT": {"kind": "column", "column": "amount", "type": "NUM"}, "ACTIVE": {"kind": "column", "column": "active", "type": "NUM"}}}}
--- as
statement
--- source
ITEMS .> FILTER(_["active"] == 1) .> BUCKET(_["dept"], RECORD("dept", _K, "total", SUM(_, _["amount"]))) .> FILTER(_["total"] > 100)
--- expect
SELECT `dept` AS `dept`, COALESCE(SUM(`amount`), 0) AS `total` FROM `items` WHERE (`active` = 1) GROUP BY `dept` HAVING (COALESCE(SUM(`amount`), 0) > 100)
===

### name: stmt.bucket.order-by-agg
--- dialect
mariadb
--- bindings
{"ITEMS": {"kind": "relation", "from": "items", "fields": {"DEPT": {"kind": "column", "column": "dept", "type": "TEXT"}, "AMOUNT": {"kind": "column", "column": "amount", "type": "NUM"}}}}
--- as
statement
--- source
ITEMS .> BUCKET(_["dept"], RECORD("dept", _K, "total", SUM(_, _["amount"]))) .> SORT_BY(_["tota
… (truncated)
```

Locate Python API and error classes

```bash
mkdir -p $SCRATCH/verify-J && cd /home/nathan/workspaces/nth-share/sel && grep -n "def translate_statement\|def plan_hybrid\|def execute_hybrid\|def translate\b" python/sel/sql/*.py | head; grep -n "class SelError\|class SqlError" -A6 python/sel/errors.py python/sel/sql/errors.py | head -30
```

```
python/sel/sql/hybrid.py:326:def plan_hybrid(program: Program, dialect: str,
python/sel/sql/hybrid.py:383:def execute_hybrid(plan: HybridPlan, db_runner: Callable[[str, list[Value]], Any],
python/sel/sql/__init__.py:37:    def translate(program, dialect: str, bindings: dict[str, Any] | None = None,
python/sel/sql/__init__.py:65:    def translate_statement(program, dialect: str, bindings: dict[str, Any] | None = None,
python/sel/sql/__init__.py:82:    def plan_hybrid(program, dialect: str,
python/sel/sql/__init__.py:89:    def execute_hybrid(plan: HybridPlan, db_runner, context: Any = None):
python/sel/sql/translator.py:153:    def translate(self, ast: Node) -> Fragment:
python/sel/sql/translator.py:177:    def translate_statement(self, ast: Node) -> Fragment:
python/sel/errors.py:18:class SelError(Exception):
python/sel/errors.py-19-    """`code` is a stable identifier from spec/errors.md and part of the
python/sel/errors.py-20-    language's contract; `message` is human text, free to change and to be
python/sel/errors.py-21-    translated. Conformance tests assert on the code and the position only.
python/sel/errors.py-22-    """
python/sel/errors.py-23-
python/sel/errors.py-24-    __slots__ = ('code', 'message', 'line', 'col', 'offset')
python/sel/sql/errors.py:15:class SqlError(Exception):
python/sel/sql/errors.py-16-    """One class, one message. No diagnostics object, no `explain()` API."""
python/sel/sql/errors.py-17-
python/sel/sql/errors.py-18-    __slots__ = ('code', 'message', 'line', 'col', 'offset')
python/sel/sql/errors.py-19-
python/sel/sql/errors.py-20-    def __init__(self, code: str, message: str, pos: Pos | None = None) -> None:
python/sel/sql/errors.py-21-        super().__init__(message)
```

Run Python probe over the reported shapes with SQLite

```bash
# (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $SCRATCH/verify-J/probe.py
```

```
Exit code 1
== a-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))
  run      : ERR E_NO_KEY 1:72
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  executed : -{"1"=-{"cid"=t"3", "amt"=t"1"}, "2"=-{"cid"=t"7", "amt"=t"10"}, "3"=-{"cid"=t"9", "amt"=t"7"}}
== a-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))
  run      : ERR E_NO_KEY 1:62
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  executed : -{"1"=-{"cid"=t"3", "amt"=t"1"}, "2"=-{"cid"=t"7", "amt"=t"10"}, "3"=-{"cid"=t"9", "amt"=t"7"}}
== b-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
  executed : -{"1"=-{"cid"=t"7", "total"=t"15"}, "2"=-{"cid"=t"9", "total"=t"19"}}
== b-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "total", SUM(_, _["amount"]))) .> FILTER(_["total"] > 6)
  run      : -{"1"=-{"cid"=t"7", "total"=t"15"}, "2"=-{"cid"=t"9", "total"=t"19"}}
  stmt     : SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
  executed : -{"1"=-{"cid"=t"7", "total"=t"15"}, "2"=-{"cid"=t"9", "total"=t"19"}}
== c-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
  executed : -{"1"=-{"cid"=t"7", "n"=t"2"}}
== c-pipe2 :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["amount"] > 6) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
  executed : -{"1"=-{"cid"=t"7", "n"=t"2"}, "2"=-{"cid"=t"9", "n"=t"2"}}
== ma
… (truncated)
```

Run the remaining SUM two-argument shapes in Python

```bash
sed -i "/'avg-3arg'/d" $SCRATCH/verify-J/probe.py && PYTHONPATH=$PWD/python python3 $SCRATCH/verify-J/probe.py 2>&1 | sed -n '/== sum2-pipe/,$p'
```

```
== sum2-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "t", SUM(g, _["amount"])))
  run      : -{"1"=-{"cid"=t"7", "t"=t"15"}, "2"=-{"cid"=t"9", "t"=t"19"}, "3"=-{"cid"=t"3", "t"=t"1"}}
  stmt     : REFUSED E_SQL_UNBOUND 1:76
  plan     : pure_memory None
  executed : -{"1"=-{"cid"=t"7", "t"=t"15"}, "2"=-{"cid"=t"9", "t"=t"19"}, "3"=-{"cid"=t"3", "t"=t"1"}}
== sum2-take :: ORDERS .> TAKE(4) .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(g), "t", SUM(g, _["amount"])))
  run      : -{"1"=-{"cid"=t"7", "n"=t"2", "t"=t"15"}, "2"=-{"cid"=t"9", "n"=t"2", "t"=t"19"}}
  stmt     : REFUSED E_SQL_UNBOUND 1:102
  plan     : hybrid SELECT "o".* FROM "orders" "o" LIMIT 4
  executed : -{"1"=-{"cid"=t"7", "n"=t"2", "t"=t"15"}, "2"=-{"cid"=t"9", "n"=t"2", "t"=t"19"}}
== sum2-3arg :: ORDERS .> BUCKET(g, g["customer_id"], RECORD("cid", _K, "t", SUM(g, _["amount"])))
  run      : -{"1"=-{"cid"=t"7", "t"=t"15"}, "2"=-{"cid"=t"9", "t"=t"19"}, "3"=-{"cid"=t"3", "t"=t"1"}}
  stmt     : REFUSED E_SQL_UNBOUND 1:69
  plan     : pure_memory None
  executed : -{"1"=-{"cid"=t"7", "t"=t"15"}, "2"=-{"cid"=t"9", "t"=t"19"}, "3"=-{"cid"=t"3", "t"=t"1"}}
== sum2-x-3arg :: ORDERS .> BUCKET(g, g["customer_id"], RECORD("cid", _K, "t", SUM(g, x, x["amount"])))
  run      : -{"1"=-{"cid"=t"7", "t"=t"15"}, "2"=-{"cid"=t"9", "t"=t"19"}, "3"=-{"cid"=t"3", "t"=t"1"}}
  stmt     : SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "t" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "t" FROM "orders" "o" GROUP BY "o"."customer_id"
  executed : -{"1"=-{"cid"=t"3", "t"=t"1"}, "2"=-{"cid"=t"7", "t"=t"15"}, "3"=-{"cid"=t"9", "t"=t"19"}}
```

Run the Lisp probe over the same shapes

```bash
# (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $SCRATCH/verify-J/probe.lisp 2>&1 | grep -v "^;"
```

```
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=774013 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 2, Column: 48, File-Position: 72

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-J/probe.lisp" {1201ED65C3}>

Backtrace for: #<SB-THREAD:THREAD tid=774013 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201ED7833}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201ED77E3}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-J/probe.lisp" {1201ED65C3}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {76A3E50474CB}> #<SB-C::SOURCE-INFO {1201E77813}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-J/probe.lisp" {1201ED65C3}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-J/probe.lisp" {1201ED65C3}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {76A3E504788B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-J/probe.lisp" {1201ED65C3}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-J/probe.lisp" {1201ED65C3}>)
9: (LOAD #P"$SCRATCH/verify-J/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-J/probe.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Run the Lisp probe with the SQL system loaded

```bash
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $SCRATCH/verify-J/probe.lisp 2>&1 | grep -v "^;"
```

```
== a-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))
  run      : ERR E_NO_KEY 1:72
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
== a-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))
  run      : ERR E_NO_KEY 1:62
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
== b-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
== c-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
== c-pipe2 :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["amount"] > 6) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
== max-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))
  run      : ERR E_NO_KEY 1:75
  stmt     : SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
== max-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "mx", MAX(_, _["amount"])))
  run      : ERR E_NO_KEY 1:68
  stmt     : SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
== sum2-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "t", SUM(g, _["amount"])))
  run      : -{"1"=-{"cid"=t"7", "t"=t"15"}, "2"=-{"cid"=t"9", "t"=t"19"}, "3"=-{"cid"=t"3", "t"=t"1"}}
  stmt     : SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "t" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COALESCE(S
… (truncated)
```

Run the extra shapes in Python

```bash
cat >> $SCRATCH/verify-J/probe.py <<'EOF'

print("#### extras")
for tag, src in [
 ('map-underscore', 'ORDERS .> MAP(g, RECORD("x", _["amount"]))'),
 ('filter-underscore', 'ORDERS .> FILTER(g, _["amount"] > 6)'),
 ('map-alias', 'ORDERS .> MAP(g, RECORD("x", o["amount"]))'),
]:
    print('==', tag, '::', src)
    prog = sel_compile(src)
    try:
        print('  run      :', prog.run({'ORDERS': rows}).dump())
    except SelError as e:
        print('  run      : ERR', e.code, f'{e.line}:{e.col}')
    try:
        frag = Sql.translate_statement(prog, 'sqlite', bindings)
        print('  stmt     :', frag.as_statement('params'))
    except SqlError as e:
        print('  stmt     : REFUSED', e.code, f'{e.line}:{e.col}')
    plan = Sql.plan_hybrid(prog, 'sqlite', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    print('  plan     :', kind, plan.sql_statement.as_statement('params') if plan.sql_statement else None)
EOF
PYTHONPATH=$PWD/python python3 $SCRATCH/verify-J/probe.py 2>&1 | sed -n '/#### extras/,$p'
```

```
#### extras
== map-underscore :: ORDERS .> MAP(g, RECORD("x", _["amount"]))
  run      : ERR E_UNDEF_VAR 1:30
  stmt     : REFUSED E_SQL_UNBOUND 1:30
  plan     : pure_memory None
== filter-underscore :: ORDERS .> FILTER(g, _["amount"] > 6)
  run      : ERR E_UNDEF_VAR 1:21
  stmt     : REFUSED E_SQL_UNBOUND 1:21
  plan     : pure_memory None
== map-alias :: ORDERS .> MAP(g, RECORD("x", o["amount"]))
  run      : ERR E_UNDEF_VAR 1:30
  stmt     : REFUSED E_SQL_UNBOUND 1:30
  plan     : pure_memory None
```

Run the JS probe

```bash
# (file written, see "Reproduction scripts" above)
node $SCRATCH/verify-J/probe.mjs
```

```
== a-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))
  run      : ERR E_NO_KEY 1:72
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
== a-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))
  run      : ERR E_NO_KEY 1:62
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
== b-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
== c-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
== c-pipe2 :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["amount"] > 6) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
== max-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))
  run      : ERR E_NO_KEY 1:75
  stmt     : SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
== max-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "mx", MAX(_, _["amount"])))
  run      : ERR E_NO_KEY 1:68
  stmt     : SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
== sum2-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "t", SUM(g, _["amount"])))
  run      : -{"1"=-{"cid"=t"7", "t"=t"15"}, "2"=-{"cid"=t"9", "t"=t"19"}, "3"=-{"cid"=t"3", "t"=t"1"}}
  stmt     : REFUSED E_SQL_UNBOUND 1:76
  plan     : pure_memory null
== sum2-take :: ORDERS .> TAKE(4) .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(g), "t", SUM(g, _["amount"]
… (truncated)
```

Run the PHP probe

```bash
grep -n "getCode\|->code\|public.*\$line\|public.*\$col\|function line\|function col" php/src/SelError.php php/src/Sql/SqlError.php | head; # (file written, see "Reproduction scripts" above)
php $SCRATCH/verify-J/probe.php
```

```
php/src/Sql/SqlError.php:23:        $this->code = $code;
php/src/Sql/SqlError.php:31:        return "{$this->code} at {$this->line}:{$this->col}: {$this->getMessage()}";
php/src/SelError.php:31:        $this->code = $code;
php/src/SelError.php:39:        return "{$this->code} at {$this->line}:{$this->col}: {$this->getMessage()}";
== a-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))
  run      : ERR E_NO_KEY 1:72
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
== a-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))
  run      : ERR E_NO_KEY 1:62
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
== b-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
== c-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
== c-pipe2 :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["amount"] > 6) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
== max-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))
  run      : ERR E_NO_KEY 1:75
  stmt     : SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
== max-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "mx", MAX(_, _["amount"])))
  run      : ERR E_NO_KEY 1:68
  stmt     : SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
== sum2-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _
… (truncated)
```

Compile and run the C++ probe

```bash
sed -n '30,36p' cpp/Makefile; grep -n "enum class Mode" -A3 cpp/sel_sql.hpp | head -6; # (file written, see "Reproduction scripts" above)
cd cpp && c++ -std=c++23 -O1 -I. -o $SCRATCH/verify-J/probe_cpp $SCRATCH/verify-J/probe.cpp build/sel_sql*.o build/sel.o 2>&1 | head -20 && $SCRATCH/verify-J/probe_cpp
```

```
Exit code 127
# contributes .rodata and no static constructor and --gc-sections drops all of
# it from a link that never asks for it.
SQL_SRCS := sel_sql.cpp sel_sql_binding.cpp sel_sql_emit.cpp sel_sql_map.cpp \
            sel_sql_map_data.cpp sel_sql_node.cpp sel_sql_stage1.cpp \
            sel_sql_translator.cpp sel_sql_hybrid.cpp
SQL_OBJS := $(addprefix $(BUILD)/,$(SQL_SRCS:.cpp=.o))
SQL_HDRS := sel_sql.hpp sel_sql_emit.hpp sel_sql_map.hpp sel_sql_node.hpp \
81:enum class Mode { Inline, Params, Debug };
82-
83-std::string_view mode_name(Mode m);
84-std::optional<Mode> mode_from_name(std::string_view name);
$SCRATCH/verify-J/probe.cpp: In function ‘int main()’:
$SCRATCH/verify-J/probe.cpp:32:127: error: ‘sel::sql::Fragment::Mode’ has not been declared
   32 |     try { std::cout << "  stmt     : " << Sql::translate_statement(prog, "sqlite", bindings).as_statement(sel::sql::Fragment::Mode::Params) << "\n"; }
      |                                                                                                                               ^~~~
$SCRATCH/verify-J/probe.cpp:36:93: error: ‘sel::sql::Fragment::Mode’ has not been declared
   36 |               << (plan.sql_statement ? plan.sql_statement->as_statement(sel::sql::Fragment::Mode::Params) : std::string("null")) << "\n";
      |                                                                                             ^~~~
/bin/bash: line 41: $SCRATCH/verify-J/probe_cpp: No such file or directory
```

Rebuild and run the C++ probe

```bash
cd /home/nathan/workspaces/nth-share/sel/cpp && sed -i 's/sel::sql::Fragment::Mode::Params/sel::sql::Mode::Params/g' $SCRATCH/verify-J/probe.cpp && c++ -std=c++23 -O1 -I. -o $SCRATCH/verify-J/probe_cpp $SCRATCH/verify-J/probe.cpp build/sel_sql*.o build/sel.o 2>&1 | head -20 && $SCRATCH/verify-J/probe_cpp
```

```
== a-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))
  run      :   run      : ERR E_NO_KEY 1:72
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
== a-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))
  run      :   run      : ERR E_NO_KEY 1:62
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
== b-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))
  run      :   run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COALESCE(SUM("o"."amount"), 0) AS "total" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST(COALESCE(SUM("o"."amount"), 0) AS NUMERIC) > CAST('6' AS NUMERIC))
== c-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      :   run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
== c-pipe2 :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["amount"] > 6) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      :   run      : ERR E_NO_KEY 1:47
  stmt     : SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
== max-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))
  run      :   run      : ERR E_NO_KEY 1:75
  stmt     : SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
== max-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "mx", MAX(_, _["amount"])))
  run      :   run      : ERR E_NO_KEY 1:68
  stmt     : SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
== sum2-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "t", SUM(g, _["amount"])))
  run      : -{"1"=-{"cid"=t"7", "t"=t"15"}, "2"=-{"cid"=t"9", "t"=t"19"}, "3"=-{"cid"=t"3", "t"=t"1"}}
  stmt     :   stmt     : REFUSED E_SQL_UNBOUND 1:76
  plan     : pure_memory null
== sum2-take :: ORDERS .
… (truncated)
```

Run the same JS probe against the pre-commit JS tree

```bash
OLD=$SCRATCH/verify-J/old && mkdir -p $OLD && git archive 8fe0e3a js | tar -x -C $OLD && sed "s#/home/nathan/workspaces/nth-share/sel/js#$OLD/js#g" $SCRATCH/verify-J/probe.mjs > $OLD/probe-old.mjs && sed -i 's/GROUP_BY/BUCKET/' $OLD/probe-old.mjs && node $OLD/probe-old.mjs
```

```
== a-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"]))
  run      : ERR E_NO_KEY 1:72
  stmt     : REFUSED E_SQL_SHAPE 1:60
  plan     : hybrid SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
== a-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "amt", _["amount"]))
  run      : ERR E_NO_KEY 1:62
  stmt     : SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", "o"."amount" AS "amt" FROM "orders" "o" GROUP BY "o"."customer_id"
== b-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"])))
  run      : ERR E_NO_KEY 1:47
  stmt     : REFUSED E_SQL_SHAPE 1:83
  plan     : hybrid SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
== c-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      : ERR E_NO_KEY 1:47
  stmt     : REFUSED E_SQL_SHAPE 1:90
  plan     : hybrid SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."customer_id" AS NUMERIC) = CAST('7' AS NUMERIC))
== c-pipe2 :: ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["amount"] > 6) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  run      : ERR E_NO_KEY 1:47
  stmt     : REFUSED E_SQL_SHAPE 1:84
  plan     : hybrid SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id" HAVING (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC))
== max-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"])))
  run      : ERR E_NO_KEY 1:75
  stmt     : REFUSED E_SQL_SHAPE 1:57
  plan     : hybrid SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
== max-3arg :: ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "mx", MAX(_, _["amount"])))
  run      : ERR E_NO_KEY 1:68
  stmt     : SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
  plan     : pure_sql SELECT "o"."customer_id" AS "cid", MAX("o"."amount") AS "mx" FROM "orders" "o" GROUP BY "o"."customer_id"
== sum2-pipe :: ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "t", SUM(g, _["amount"])))
  run      : -{"1"=-{"cid"=t"7", "t"=t"15"}, "2"=-{"cid"=t"9", "t"=t"19"}, "3"=-{"cid"=t"3", "t"=t"1"}}
  stmt     : REFUSED E_SQL_SHAPE 1:60
  plan     : hybrid SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
== sum2-take :: ORDERS .> TAKE(4) .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "n", COUNT(g), "t", SUM(g, _["amount"])))
  run      : -{"1"=-{"cid"=t"7", "n"=t"2", "t"=t"15"}, "2"=-{"cid"=t"9", "n"=t"2", "t"=t"19"}}
  stmt     : REFUSED E_SQL_SHAPE 1:71
  plan     : hybrid SELECT "_sub1"."customer_id" FROM (SELECT "o".* FROM "orders" "o" LIMIT 4) "_sub1" GROUP BY "_sub1"."customer_id"
== sum2-3arg :: ORDERS .> BUCKET(g, g["customer_id"], RECORD("cid", _K, "t", SUM(g, _["amount"])))
  run      : -{"1"=-{"cid"=t"7", "t"=t"15"}, "2"=-{"cid"=t"9", "t"=t"19"}, "3"=-{"cid"=t"3", "t"=t"1"}}
  stmt     : REFUSED E_SQL_UNBOUND 1:69
  plan     : pure_memory null
== map-underscore :: ORDERS .> MAP(g, RECORD("x", _["amount"]))
  run      : ERR E_UNDEF_VAR 1:30
  stmt     : REFUSED E_SQL_UNBOUND 1:30
  plan     : pure_memory null
== filter-underscore :: ORDERS .> FILTER(g, _["amount"] > 6)
  run      : ERR E_UNDEF_VAR 1:21
  s
… (truncated)
```

Rerun the reported REPL repros on all five hosts

```bash
for h in "node js/bin/sel.mjs -e" "php php/bin/sel -e" "cpp/build/sel -e" "lisp/bin/sel -e"; do echo "## $h"; $h 'LIST(RECORD("cat","A","v",1), RECORD("cat","B","v",2)) .> BUCKET(_["cat"]) .> MAP(g, RECORD("cat", _K, "amt", g["v"]))' 2>&1 | tail -1; $h 'LIST(RECORD("cat","A","v",1), RECORD("cat","B","v",2)) .> BUCKET(_["cat"], RECORD("cat", _K, "mx", MAX(_, 5)))' 2>&1 | tail -1; $h 'LIST(RECORD("cat","A","v",1), RECORD("cat","B","v",2)) .> BUCKET(_["cat"]) .> FILTER(_["total"] > 1) .> MAP(RECORD("cat", _K, "total", SUM(_, _["v"])))' 2>&1 | tail -1; done; echo "## python"; for e in 'LIST(RECORD("cat","A","v",1), RECORD("cat","B","v",2)) .> BUCKET(_["cat"]) .> MAP(g, RECORD("cat", _K, "amt", g["v"]))' 'LIST(RECORD("cat","A","v",1), RECORD("cat","B","v",2)) .> BUCKET(_["cat"], RECORD("cat", _K, "mx", MAX(_, 5)))' 'LIST(RECORD("cat","A","v",1), RECORD("cat","B","v",2)) .> BUCKET(_["cat"]) .> FILTER(_["total"] > 1) .> MAP(RECORD("cat", _K, "total", SUM(_, _["v"])))'; do PYTHONPATH=$PWD/python python3 -m sel -e "$e" 2>&1 | tail -1; done
```

```
## node js/bin/sel.mjs -e
E_NO_KEY at line 1 column 112: no key "v"
E_NOT_NUM at line 1 column 104: not a number: "A"
E_NO_KEY at line 1 column 87: no key "total"
## php php/bin/sel -e
E_NO_KEY at line 1 column 112: no key "v"
E_NOT_NUM at line 1 column 104: not a number: "A"
E_NO_KEY at line 1 column 87: no key "total"
## cpp/build/sel -e
E_NO_KEY at line 1 column 112: no key "v"
E_NOT_NUM at line 1 column 104: not a number: "A"
E_NO_KEY at line 1 column 87: no key "total"
## lisp/bin/sel -e
E_NO_KEY at line 1 column 112: no key "v"
E_NOT_NUM at line 1 column 104: not a number: "A"
E_NO_KEY at line 1 column 87: no key "total"
## python
E_NO_KEY at line 1 column 112: no key "v"
E_NOT_NUM at line 1 column 104: not a number: 'A'
E_NO_KEY at line 1 column 87: no key "total"
```

Execute the pre-commit JS hybrid plans for the pipeline shapes

```bash
OLD=$SCRATCH/verify-J/old && # (file written, see "Reproduction scripts" above)
node $OLD/exec-old.mjs
```

```
old prefix SQL: SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
ORDERS .> BUCKET(_["customer_id"]) .> MAP(g, RECORD("cid", _K, "amt", g["amount"])) 
  old hybrid executed: ERR E_NO_KEY 1:72
  old prefix SQL: SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
ORDERS .> BUCKET(_["customer_id"]) .> FILTER(_["total"] > 6) .> MAP(RECORD("cid", _K, "total", SUM(_, _["amount"]))) 
  old hybrid executed: ERR E_NO_KEY 1:47
  old prefix SQL: SELECT "o"."customer_id" FROM "orders" "o" GROUP BY "o"."customer_id"
ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "mx", MAX(_, _["amount"]))) 
  old hybrid executed: ERR E_NO_KEY 1:75
```

