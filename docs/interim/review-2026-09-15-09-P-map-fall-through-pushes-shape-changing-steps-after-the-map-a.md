# P. MAP fall-through pushes shape-changing steps after the MAP and re-evaluates the original RECORD over projected rows

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "Hybrid planner: the shape guard, completed".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** mixed · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

The MAP fall-through (tryPlanFallthrough) is identical in all five hosts and wrong in the same three ways: (1) every step after the MAP is pushed into SQL while the continuation is the ORIGINAL RECORD body re-applied to the final rows, so a downstream BUCKET / MAP / SELECT_COLS / LINK (mariadb/postgresql) changes the row shape under the continuation and the hybrid lane answers E_NO_KEY or a different shape than run(); the new `bucketRowsAreKeys` guard only looks at steps BEFORE the MAP, so a bucket after it is still split — contradicting §12.1's "never a split point ... or in anything that followed one"; (2) a pushable pair whose key differs from its source column (`"cid", _["customer_id"]`) or is an expression (`_["amount"] + 1`) is re-evaluated in the continuation against alias-keyed rows: E_NO_KEY or silently doubled arithmetic; (3) whole-row binder reads (`GET(_, "name")`) are not collected as dependencies, so the custom half sees a projected row and answers none. I reproduced every facet with a real SQLite (Python, PHP natively; JS/C++/Lisp via a SQLite oracle helper) and all five hosts diverge from run() identically on the same programs, so it is a lane divergence, not a host divergence. The fall-through logic is pre-existing at 8fe0e3a in all five hosts; this commit added the before-the-MAP bucket guard, the §12.1 contract and the CHANGELOG sentence "the MAP fall-through does not fire over one", and its three fall-through fixtures all spell the pushable pair as `"id", _["id"]`, which is the only spelling that works.

## Suggested fix — case first

Smallest fix, in tryPlanFallthrough of all five hosts: (1) refuse the fall-through unless every step after the MAP is shape-preserving (FILTER/SORT*/TAKE/DROP/DEDUPE...) — equivalently run bucketRowsAreKeys over the whole rewritten step list and additionally return null when any downstream step is MAP/SELECT_COLS/LINK/LINK_LEFT/BUCKET; (2) build the continuation body as `RECORD(k1, _[k1], ..., <custom pairs verbatim>)` — pass the pushable pairs through BY KEY instead of re-evaluating their expressions — and treat a bare binder var (`_` not under an index, incl. `GET(_, ...)`, `COUNT(_)`) inside a custom pair as "whole row needed" -> refuse. First cases to add: `sql/cases/25-hybrid-plans.sqlt` planner cases `ORDERS .> MAP(RECORD("cid", _["customer_id"], "tag", ABORT("x"))) .> TAKE(3)` (hybrid; continuation must not read customer_id) and `ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "tag", ABORT("x"))) .> BUCKET(_["customer_id"])` (must be pure_memory, or split before the MAP), then extend python/tests/test_unit.py::test_bucket_plans_answer_what_the_evaluator_answers_on_sqlite (and the JS/PHP/C++/Lisp optimizer checks) with the aliased pair, the `_["amount"] + 1` pair, `GET(_, "name")`, and the MAP-then-BUCKET shapes compared against run().

## Verifier reasoning

Code (all five hosts, same structure): the fall-through finds the first MAP, guards only `steps[:map_index]` with bucketRowsAreKeys (js/src/sql/hybrid.mjs:211, php/src/Sql/Hybrid.php:349, python/sel/sql/hybrid.py:251, cpp/sel_sql_hybrid.cpp:197, lisp/src/sql/hybrid.lisp:217), appends every downstream step to the SQL prefix unchanged (hybrid.mjs:257, Hybrid.php:390, hybrid.py:296, sel_sql_hybrid.cpp:211-212 plus the rewritten steps, hybrid.lisp:306), and builds the continuation as `MAP(_INPUT, <original body>)` (hybrid.mjs:264-267, Hybrid.php:395-398, hybrid.py:302-306, sel_sql_hybrid.cpp:275, hybrid.lisp:315-318). collectFieldReferences (hybrid.mjs:163, hybrid.lisp:156, sel_sql_hybrid.cpp:106, PHP/Python equivalents) only recognises `binder["text"]` index nodes, never a bare binder var, so `GET(_, "name")` and `COUNT(_)` contribute no dependency columns. The only downstream guard is "no downstream step indexes a custom key", which does not detect shape changes. Consequences follow directly: the original body reads `_["customer_id"]` on rows that carry `cid` (E_NO_KEY), re-applies `_["amount"] + 1` to an already-incremented column (12 instead of 11), and reads `_["name"]` after a BUCKET/MAP/SELECT_COLS dropped it (E_NO_KEY), or when there are no dependency columns lays the flat MAP over SQL's group-key rows (a list of key records where run() returns a map of groups; a `.> BUCKET .> MAP(_K, COUNT(_))` loses `n` and gains the custom field). LINK: on sqlite the shape plans pure_memory (join over a derived table with an untyped comparison is refused there), but on mariadb/postgresql it plans hybrid with the INNER JOIN in SQL and the original two-field MAP as continuation, so the output has only {id, note} where run() has the LINK record keys — divergent by construction. Promises: §12.1 says a hybrid plan's continuation "runs the rest over its rows" and "a prefix that ends in a bucket nobody has projected — or in anything that followed one — is never a split point"; CHANGELOG says "the MAP fall-through does not fire over one". Both are unmet for a bucket AFTER the fall-through MAP. Provenance: `git show 8fe0e3a:` for all five hybrid files contains the same tryPlanFallthrough with the same continuation construction (e.g. old js had no bucket guard at all; old cpp line 269 `continuation_map->items.push_back(details->body)`; old lisp `(list cont-root rec-node)`), so the defect is pre-existing; the promise and the partial guard are new — "mixed". Fixtures: sql/cases/25-hybrid-plans.sqlt lines 92-150 (plan.hybrid.unsupported-suffix, fallthrough-keeps-downstream-steps, longest-prefix) all use `"id", _["id"]` and only shape-preserving downstream steps; python/tests/test_unit.py:485-528 (the SQLite bucket test) never puts a bucket after a fall-through MAP nor an aliased pushable pair. Severity high: differing results/errors between the in-memory and hybrid lanes in every host, plus a promise unmet. Two asides seen while probing, outside this cluster: (a) lisp/src/sql/hybrid.lisp:335/341 calls the runner with `as-statement` (inline) AND `fragment-params`, whereas the other four pass `asStatement('params')` + `bindings()` (hybrid.mjs:355, hybrid.py:395, Hybrid.php:428, sel_sql_hybrid.cpp:395) — pre-existing (8fe0e3a lisp lines 277/283); a real driver rejects inline SQL with surplus params, as sqlite did in my first Lisp run. (b) `ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))` is pure_sql `COUNT(*)` (1 row, n=3) while run() gives 3 rows with n=4 (record key count) in all hosts — a translator-level divergence, not the fall-through.

## Verifier evidence

```
Probe scripts under /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-P/ (probe.py, probe.mjs, probe.php, probe.cpp/probe_cpp, probe.lisp, cases.txt, cases2.txt, sqlrun.py / sqlrun_tsv.py = in-memory SQLite oracle with orders(id,customer_id,amount,name)=(1,7,10,ab),(2,7,5,cd),(3,9,7,ef) and customers(id,city)). Each probe compiles a program, runs it in memory, plans with dialect sqlite, executes the plan (Python: sqlite3; PHP: PDO sqlite; JS/C++/Lisp: runner shells to sqlrun*.py), and diffs dumps.

Commands: `PYTHONPATH=$PWD/python python3 $S/probe.py $S/cases.txt`; `node $S/probe.mjs $S/cases.txt`; `php $S/probe.php $S/cases.txt`; `cd cpp && c++ -std=c++23 -O1 -o $S/probe_cpp $S/probe.cpp build/sel_sql*.o build/map_replay.o build/sel.o && $S/probe_cpp $S/cases.txt`; `sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --eval '(sb-ext:exit :code (sel-cli::main))' --end-toplevel-options $S/cases.txt`.

Results, identical in all five hosts (my own programs, not the reporter's):
- `ORDERS .> MAP(RECORD("cid", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> SORT_BY(_["cid"]) .> TAKE(2)` plan=hybrid, SQL `SELECT "_sub1".* FROM (SELECT "o"."customer_id" AS "cid", "o"."name" AS "name" FROM "orders" "o") "_sub1" ORDER BY "_sub1"."cid" ASC LIMIT 2`; run: `-{"1"=-{"cid"=t"7","shout"=t"abab"},"2"=-{"cid"=t"7","shout"=t"cdcd"}}`; hybrid: `ERR E_NO_KEY@1:30`.
- `ORDERS .> MAP(RECORD("amount", _["amount"] + 1, "shout", REPEAT(_["name"], 2)))` hybrid; run amount=11,6,8; executed plan amount=12,7,9 (no error).
- `... MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"])` hybrid, SQL `SELECT "_sub1"."customer_id" FROM (...) "_sub1" GROUP BY "_sub1"."customer_id"`; run = map of two groups; hybrid = `ERR E_NO_KEY@1:72`.
- `... .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))` hybrid; run `-{"1"=-{"cid"=t"7","n"=t"2"},"2"=-{"cid"=t"9","n"=t"1"}}`; hybrid `ERR E_NO_KEY`.
- `ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> MAP(RECORD("id", _["id"]))` and `... .> SELECT_COLS("id")`: run = 3 {id} rows; hybrid = `ERR E_NO_KEY@1:50`.
- `ORDERS .> MAP(RECORD("id", _["id"], "whole", GET(_, "name")))` hybrid, SQL `SELECT "o"."id" AS "id" FROM "orders" "o"`; run whole=ab/cd/ef; hybrid whole=none (`"whole"=-`).
- Control `ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> FILTER(_["id"] > 1) .> TAKE(1)`: SAME in all five (alias==column, shape-preserving suffix).
- cases2.txt (Python): `ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "note", REPEAT("x", 2))) .> BUCKET(_["customer_id"])` run = `-{"7"=-{...2 rows},"9"=-{...1 row}}`, hybrid = `-{"1"=-{"customer_id"=t"7","note"=t"xx"},"2"=-{"customer_id"=t"9","note"=t"xx"}}` (flat key rows); `... .> BUCKET(_["customer_id"]) .> MAP(RECORD("customer_id", _K, "n", COUNT(_)))` run has n=2/1, hybrid has `tag`/`note` and no `n`; `... .> BUCKET .> FILTER(COUNT(_) > 1) .> MAP(RECORD("customer_id", _K))` run `{customer_id=7}`, hybrid `{customer_id=7, note=xx}`.
- Reporter's repros rerun (cases.txt tail): `"cid", _["customer_id"] ... TAKE(2)` -> E_NO_KEY@1:30; `"amount", _["amount"] * 2 ...` -> 40/20/28 vs 20/10/14; `GET(_, "name")` -> row=none; `.> MAP(RECORD("id", _["id"]))` / `.> BUCKET(_["id"])` -> E_NO_KEY@1:50 — all five hosts.
- LINK facet (Python plan only): `ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> LINK(CUSTOMERS, _1["id"] == _2["id"])` -> sqlite: pure_memory; mariadb: hybrid `SELECT `_sub1`.* FROM (SELECT `o`.`id` AS `id`, `o`.`name` AS `name` FROM `orders` `o`) `_sub1` INNER JOIN `customers` `c` ON (...)`; postgresql: hybrid with the same join — the continuation is the two-field MAP, so its output cannot carry the LINK record keys run() produces.
- Provenance: `git show 8fe0e3a:js/src/sql/hybrid.mjs` shows tryPlanFallthrough (line 150) with the same downstream append and `[input, details.body]` continuation and NO bucket guard; `git show 8fe0e3a:cpp/sel_sql_hybrid.cpp` line 269 `continuation_map->items.push_back(details->body)`; `git show 8fe0e3a:lisp/src/sql/hybrid.lisp` `(list cont-root rec-node)`; PHP line 310 `$details['body']`; Python `_try_plan_fallthrough` at line 202.
- Fixtures: sql/cases/25-hybrid-plans.sqlt:92-150 — all three fall-through cases are `"id", _["id"]` with ABORT as the custom pair; `grep 'MAP(RECORD' sql/cases/25-hybrid-plans.sqlt | grep -v '"id", _\["id"\]\|_K'` finds only single-custom-pair records (no fall-through). python/tests/test_unit.py:485-528 shapes contain no MAP-before-BUCKET.
- docs/SQL-TRANSLATION.md:2227-2300 (§12.1 table row for `hybrid`, and the "A bucket is split only where SQL still has its members" bullet); CHANGELOG.md [Unreleased] "The hybrid planner never splits inside a bucket ... the MAP fall-through does not fire over one".
```

## Original review reports (deduplicated into this finding)

### [hybrid-planner] MAP fall-through re-applies the custom half after downstream steps that change the row shape (MAP, BUCKET, SELECT_COLS, LINK) and ignores whole-row binder reads; hybrid lane answers differently from run() in every host

*correctness · high · hosts: js, php, python, cpp, lisp*

Locations: `js/src/sql/hybrid.mjs:224-228`; `js/src/sql/hybrid.mjs:256-267`; `php/src/Sql/Hybrid.php:360-364`; `php/src/Sql/Hybrid.php:390-398`; `python/sel/sql/hybrid.py:263-268`; `python/sel/sql/hybrid.py:296-306`; `cpp/sel_sql_hybrid.cpp:210-221`; `cpp/sel_sql_hybrid.cpp:261-275`; `lisp/src/sql/hybrid.lisp:248-253`; `lisp/src/sql/hybrid.lisp:304-318`

tryPlanFallthrough pushes every step AFTER the first MAP into the SQL statement and makes the continuation `MAP(_INPUT, <original body>)`, i.e. the full original record body is evaluated over the rows that come back from the whole pushed pipeline. The only guard is that no downstream step references a custom key by `_["key"]`. That is sound only when every downstream step preserves row shape (FILTER/SORT/TAKE/DROP/DEDUPE). A downstream MAP or SELECT_COLS that drops the dependency columns, a downstream BUCKET (whose SQL rows are keys, exactly the case §12.1 says is never a split point -- bucketRowsAreKeys is only run on the steps BEFORE the map, hybrid.mjs:211, so a bucket after the map is not seen), or a downstream LINK (mariadb: `ORDERS .> MAP(RECORD("id",_["id"],"note",GET(_["name"],1))) .> LINK(CUSTOMERS, _1["id"]==_2["id"])` plans hybrid in all five hosts with the join in SQL and the first MAP body as continuation) produces a result whose shape and values differ from Program.run. Separately, a binder used as a whole row (`GET(_, "name")`) or `COUNT(_)` is not collected as a dependency (collectFieldReferences only sees `binder["text"]`), so the custom half runs over the projected row and answers none/other values. None of the 25 planner fixtures has a shape-changing step after the fall-through MAP; the SQLite unit test only covers bucket shapes.

Reported repro:

```
PYTHONPATH=python python3 probe (sqlite in-memory table orders(id, customer_id, name) with rows (1,7,ab),(2,7,cd),(3,9,ef); dialect sqlite; ORDERS bound with ID/CUSTOMER_ID/NAME):
1) `ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> MAP(RECORD("id", _["id"]))` -> plan=hybrid, sql=SELECT "_sub1"."id" AS "id" FROM (SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o") "_sub1"; run() = -{"1"=-{"id"=t"1"}, ...}; execute_hybrid = ERR E_NO_KEY@1:50.
2) `... .> BUCKET(_["id"])` -> hybrid, sql=SELECT "_sub1"."id" FROM (...) "_sub1" GROUP BY "_sub1"."id"; run() = keyed map of groups; execute_hybrid = ERR E_NO_KEY@1:50.
3) `... .> BUCKET(_["id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))` -> hybrid; run() = -{"1"=-{"k"=t"1","n"=t"1"},...}; execute_hybrid = ERR E_NO_KEY@1:29.
4) `... .> SELECT_COLS("id")` -> hybrid; run() = {id} rows; execute_hybrid = ERR E_NO_KEY@1:50.
5) `ORDERS .> MAP(RECORD("id", _["id"], "row", GET(_, "name")))` -> hybrid, sql=SELECT "o"."id" AS "id" FROM "orders" "o"; run() gives row="ab"/"cd"/"ef", execute_hybrid gives row=none.
The same classifications and SQL (mariadb dialect) come out of all five hosts' plan_hybrid (probe batch 3, no diff between hosts on these lines), so the hybrid lane is wrong identically everywhere.
```

### [promises-vs-code] MAP fall-through pushes a downstream BUCKET into SQL and re-runs the MAP over key rows; and its continuation re-evaluates pushable pairs by source field on alias-keyed rows (E_NO_KEY)

*correctness · high · hosts: js, python, php, cpp, lisp*

Locations: `js/src/sql/hybrid.mjs:211`; `js/src/sql/hybrid.mjs:264`; `python/sel/sql/hybrid.py:251`; `python/sel/sql/hybrid.py:303`; `php/src/Sql/Hybrid.php:349`; `php/src/Sql/Hybrid.php:395`; `cpp/sel_sql_hybrid.cpp:197`; `cpp/sel_sql_hybrid.cpp:271`; `lisp/src/sql/hybrid.lisp:217`; `lisp/src/sql/hybrid.lisp:315`

The new guard `bucketRowsAreKeys(steps.slice(0, mapIndex))` only looks at steps BEFORE the MAP. Every step AFTER the MAP (which may be a bare BUCKET, or a BUCKET .> MAP) is appended to the SQL prefix, so the grouping runs in the database over the pushable half and the continuation MAP is then evaluated over the group-key rows — exactly the 'one row per group' bug the CHANGELOG says is closed, in the other direction. Separately (pre-existing, but it makes the pinned fall-through shape fragile), the continuation is the ORIGINAL RECORD body (`details.body`), so a pushable pair whose alias differs from its source field (`"cid", _["customer_id"]`) is re-evaluated against rows that only carry `cid` and raises E_NO_KEY; plan.hybrid.fallthrough-keeps-downstream-steps only works because alias == column there, and the sqlite unit test never exercises a differing alias.

Reported repro:

```
Sqlite executor, Python: 'ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "note", REPEAT("x", 2))) .> BUCKET(_["customer_id"])' -> hybrid, SQL `SELECT "_sub1"."customer_id" FROM (SELECT ...) "_sub1" GROUP BY ...`; run() = map of two groups with member rows; execute_hybrid = flat list of 2 key records with note. '... .> BUCKET(_["customer_id"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))' -> run() = [{k=7,n=2},{k=9,n=1}] vs plan ERROR E_NO_KEY. Alias case: 'ORDERS .> MAP(RECORD("cid", _["customer_id"], "note", REPEAT("x", 2))) .> TAKE(2)' -> run() = two records, execute_hybrid = ERROR E_NO_KEY. ./run.sh cases6.txt sqlite: all five hosts classify these identically (hybrid), so the lane disagreement exists in every host.
```

### [probe-lanes-bucket-hybrid] MAP fall-through continuation re-evaluates the original RECORD over projected rows: E_NO_KEY or silently wrong values

*correctness · high · hosts: js, python, php, cpp, lisp*

Locations: `js/src/sql/hybrid.mjs:264`; `python/sel/sql/hybrid.py:303`; `php/src/Sql/Hybrid.php:395`; `cpp/sel_sql_hybrid.cpp:271`; `lisp/src/sql/hybrid.lisp:315`

The MAP fall-through pushes the translatable pairs of a RECORD into SQL under their RECORD keys (plus the columns the custom pairs reference) and then builds the continuation as MAP(_INPUT, <original body>) in all five hosts. The continuation therefore re-evaluates the pushable pairs' expressions over rows that carry the pair's KEY, not the source column. Every fixture (plan.hybrid.unsupported-suffix, plan.hybrid.fallthrough-keeps-downstream-steps, plan.hybrid.longest-prefix) spells the pushable pair as "id", _["id"], so key == column and the bug is invisible. Any other spelling either raises E_NO_KEY in the continuation where run() answers rows, or — when the key is a column name used in an expression — silently returns wrong values. Worse, every step AFTER the MAP is pushed into the SQL while the continuation MAP is re-applied to the final rows, so a downstream BUCKET is silently dropped. This contradicts §12.1 ("the database answers the prefix, the evaluator runs the rest") and the CHANGELOG claim that a Python unit test compares bucket plans with the evaluator. Pre-existing at 8fe0e3a, but the diff writes the contract and adds fixtures without covering it.

Reported repro:

```
All five hosts, sqlite dialect, executed plan vs run() (scratchpad probe rows b37/b38/b41/b52/b84/b87/b89 agree host-for-host):
(1) ORDERS .> MAP(RECORD("cid", _["customer_id"], "tag", JOIN(LIST(_["status"]), "-"))) .> TAKE(3)  run() => -{"1"=-{"cid"=t"7","tag"=t"paid"}, ...3 rows}; plan=hybrid, SQL: SELECT "o"."customer_id" AS "cid", "o"."status" AS "status" FROM "orders" "o" LIMIT 3; execute_hybrid => E_NO_KEY@1:30 (in py, js, php, cpp, lisp).
(2) ORDERS .> MAP(RECORD("amount", _["amount"] * 2, "tag", JOIN(LIST(_["status"]), "-")))  run() => amount 20,10,14,24,2; SQL: SELECT ("o"."amount" * '2') AS "amount", "o"."status" AS "status" FROM "orders" "o"; executed plan => amount 40,20,28,48,4 (doubled twice) in all five hosts, no error.
(3) ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "tag", JOIN(LIST("x"), "-"))) .> BUCKET(_["customer_id"]) .> MAP(RECORD("customer_id", _K, "n", COUNT(_)))  run() => -{"1"=-{"customer_id"=t"7","n"=t"2"}, "2"=-{"customer_id"=t"9","n"=t"1"}}; plan=hybrid, SQL: SELECT "_sub1"."customer_id" AS "customer_id", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" AS "customer_id" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"; executed (Python, real sqlite) => -{"1"=-{"customer_id"=t"7","tag"=t"x"}, "2"=-{"customer_id"=t"9","tag"=t"x"}} — the count is gone.
Command: cd repo && PYTHONPATH=python .venv/bin/python <scratchpad>/probe-lanes/probe_py.py (b37, b89) and the inline script in the transcript for (3).
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$SCRATCH/verify-P/probe.py`**

```python
import sqlite3, sys
from sel import compile as sel_compile
from sel.sql import Binding, Sql
from sel.value import Value

bindings = {'ORDERS': Binding.relation('orders', 'o', {
    'ID': Binding.column('id', 'o', 'NUM'),
    'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM'),
    'AMOUNT': Binding.column('amount', 'o', 'NUM'),
    'NAME': Binding.column('name', 'o', 'TEXT')}),
  'CUSTOMERS': Binding.relation('customers', 'c', {
    'ID': Binding.column('id', 'c', 'NUM'),
    'CITY': Binding.column('city', 'c', 'TEXT')})}
orders = [{'id': '1', 'customer_id': '7', 'amount': '10', 'name': 'ab'},
          {'id': '2', 'customer_id': '7', 'amount': '5', 'name': 'cd'},
          {'id': '3', 'customer_id': '9', 'amount': '7', 'name': 'ef'}]
customers = [{'id': '7', 'city': 'Oslo'}, {'id': '9', 'city': 'Rome'}]
db = sqlite3.connect(':memory:')
db.execute('create table orders(id, customer_id, amount, name)')
db.executemany('insert into orders values (?,?,?,?)', [tuple(r.values()) for r in orders])
db.execute('create table customers(id, city)')
db.executemany('insert into customers values (?,?)', [tuple(r.values()) for r in customers])

def runner(sql, params):
    cur = db.execute(sql, [p.as_text() for p in params])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]

progs = [l.rstrip('\n') for l in open(sys.argv[1]) if l.strip() and not l.startswith('#')]
for src in progs:
    program = sel_compile(src)
    ctx = {'ORDERS': orders, 'CUSTOMERS': customers}
    try:
        want = program.run(ctx).dump()
    except Exception as e:
        want = 'ERR ' + getattr(e, 'code', type(e).__name__) + '@' + str(getattr(e, 'pos', ''))
    plan = Sql.plan_hybrid(program, 'sqlite', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    sql = plan.sql_statement.sql if plan.sql_statement else ''
    try:
        got = Sql.execute_hybrid(plan, runner, ctx)
        got = got if isinstance(got, Value) else Value.from_native(got)
        got = got.dump()
    except Exception as e:
        got = 'ERR ' + getattr(e, 'code', type(e).__name__) + '@' + str(getattr(e, 'pos', ''))
    print('###', src)
    print('  plan:', kind)
    print('  sql :', sql)
    print('  run :', want)
    print('  hyb :', got)
    print('  ' + ('SAME' if want == got else '*** DIFFER ***'))
```

**`$SCRATCH/verify-P/cases.txt`**

```
# --- my own shapes ---
ORDERS .> MAP(RECORD("cid", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> SORT_BY(_["cid"]) .> TAKE(2)
ORDERS .> MAP(RECORD("amount", _["amount"] + 1, "shout", REPEAT(_["name"], 2)))
ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"])
ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> MAP(RECORD("id", _["id"]))
ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> SELECT_COLS("id")
ORDERS .> MAP(RECORD("id", _["id"], "customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])
ORDERS .> MAP(RECORD("id", _["id"], "whole", GET(_, "name")))
ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))
# --- control: alias == column, shape-preserving downstream (should be SAME) ---
ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> FILTER(_["id"] > 1) .> TAKE(1)
# --- reporter's repros ---
ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> MAP(RECORD("id", _["id"]))
ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> BUCKET(_["id"])
ORDERS .> MAP(RECORD("id", _["id"], "row", GET(_, "name")))
ORDERS .> MAP(RECORD("cid", _["customer_id"], "note", REPEAT("x", 2))) .> TAKE(2)
ORDERS .> MAP(RECORD("amount", _["amount"] * 2, "tag", JOIN(LIST(_["name"]), "-")))
```

**`$S/sqlrun.py`**

```python
import sqlite3, sys, json
req = json.load(sys.stdin)
db = sqlite3.connect(':memory:')
db.execute('create table orders(id, customer_id, amount, name)')
db.executemany('insert into orders values (?,?,?,?)', [('1','7','10','ab'),('2','7','5','cd'),('3','9','7','ef')])
db.execute('create table customers(id, city)')
db.executemany('insert into customers values (?,?)', [('7','Oslo'),('9','Rome')])
cur = db.execute(req['sql'], req.get('params', []))
cols = [c[0] for c in cur.description]
print(json.dumps([dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]))
```

**`$S/probe.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
import { Value } from '/home/nathan/workspaces/nth-share/sel/js/src/value.mjs';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
const S = '/tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-P';
const bindings = {
  ORDERS: Binding.relation('orders', 'o', {
    ID: Binding.column('id', 'o', 'NUM'), CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'),
    AMOUNT: Binding.column('amount', 'o', 'NUM'), NAME: Binding.column('name', 'o', 'TEXT') }),
  CUSTOMERS: Binding.relation('customers', 'c', {
    ID: Binding.column('id', 'c', 'NUM'), CITY: Binding.column('city', 'c', 'TEXT') }) };
const orders = [{ id: '1', customer_id: '7', amount: '10', name: 'ab' }, { id: '2', customer_id: '7', amount: '5', name: 'cd' }, { id: '3', customer_id: '9', amount: '7', name: 'ef' }];
const customers = [{ id: '7', city: 'Oslo' }, { id: '9', city: 'Rome' }];
const runner = (sql, params) => JSON.parse(execFileSync('python3', [S + '/sqlrun.py'], { input: JSON.stringify({ sql, params: params.map((p) => p.asText()) }) }).toString());
const err = (e) => 'ERR ' + (e.code ?? e.message) + '@';
const dump = (v) => (v instanceof Value ? v : Value.fromNative(v)).dump();
for (const src of readFileSync(process.argv[2], 'utf8').split('\n').filter((l) => l.trim() && !l.startsWith('#'))) {
  const program = compile(src);
  const ctx = { ORDERS: orders, CUSTOMERS: customers };
  let want, got;
  try { want = dump(program.run(ctx)); } catch (e) { want = err(e); }
  const plan = Sql.planHybrid(program, 'sqlite', bindings);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  const sql = plan.sqlStatement ? plan.sqlStatement.asStatement('inline') : '';
  try { got = dump(Sql.executeHybrid(plan, runner, ctx)); } catch (e) { got = err(e); }
  console.log('###', src); console.log('  plan:', kind); console.log('  sql :', sql);
  console.log('  run :', want); console.log('  hyb :', got); console.log('  ' + (want === got ? 'SAME' : '*** DIFFER ***'));
}
```

**`$S/link.txt`**

```
ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> LINK(CUSTOMERS, _1["id"] == _2["id"])
ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> LINK_LEFT(CUSTOMERS, _1["id"] == _2["id"])
```

**`$S/probe.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding; use Sel\Value;
$bindings = ['ORDERS' => Binding::relation('orders', 'o', [
    'ID' => Binding::column('id', 'o', 'NUM'), 'CUSTOMER_ID' => Binding::column('customer_id', 'o', 'NUM'),
    'AMOUNT' => Binding::column('amount', 'o', 'NUM'), 'NAME' => Binding::column('name', 'o', 'TEXT')]),
  'CUSTOMERS' => Binding::relation('customers', 'c', ['ID' => Binding::column('id', 'c', 'NUM'), 'CITY' => Binding::column('city', 'c', 'TEXT')])];
$orders = [['id'=>'1','customer_id'=>'7','amount'=>'10','name'=>'ab'],['id'=>'2','customer_id'=>'7','amount'=>'5','name'=>'cd'],['id'=>'3','customer_id'=>'9','amount'=>'7','name'=>'ef']];
$customers = [['id'=>'7','city'=>'Oslo'],['id'=>'9','city'=>'Rome']];
$db = new PDO('sqlite::memory:');
$db->exec('create table orders(id, customer_id, amount, name)');
foreach ($orders as $r) $db->prepare('insert into orders values (?,?,?,?)')->execute(array_values($r));
$db->exec('create table customers(id, city)');
foreach ($customers as $r) $db->prepare('insert into customers values (?,?)')->execute(array_values($r));
$runner = function (string $sql, array $params) use ($db) {
    $st = $db->prepare($sql);
    $st->execute(array_map(fn ($p) => $p->asText(), $params));
    $rows = [];
    foreach ($st->fetchAll(PDO::FETCH_ASSOC) as $row) $rows[] = array_map(fn ($v) => (string) $v, $row);
    return $rows;
};
$dump = fn ($v) => ($v instanceof Value ? $v : Value::fromNative($v))->dump();
$err = fn ($e) => 'ERR ' . ($e->code ?? get_class($e)) . '@';
foreach (file($argv[1], FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $src) {
    if ($src[0] === '#') continue;
    $program = Sel::compile($src);
    $ctx = ['ORDERS' => $orders, 'CUSTOMERS' => $customers];
    try { $want = $dump($program->run($ctx)); } catch (\Throwable $e) { $want = $err($e); }
    $plan = Sql::planHybrid($program, 'sqlite', $bindings);
    $kind = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
    $sql = $plan->sqlStatement ? $plan->sqlStatement->asStatement('inline') : '';
    try { $got = $dump(Sql::executeHybrid($plan, $runner, $ctx)); } catch (\Throwable $e) { $got = $err($e); }
    echo "### $src\n  plan: $kind\n  sql : $sql\n  run : $want\n  hyb : $got\n  " . ($want === $got ? 'SAME' : '*** DIFFER ***') . "\n";
}
```

**`$S/sqlrun_tsv.py`**

```python
# request file: line 1 = SQL, remaining lines = params. output: one row per line, key\tvalue\tkey\tvalue...
import sqlite3, sys
lines = open(sys.argv[1]).read().split('\n')
sql, params = lines[0], [p for p in lines[1:] if p != '']
db = sqlite3.connect(':memory:')
db.execute('create table orders(id, customer_id, amount, name)')
db.executemany('insert into orders values (?,?,?,?)', [('1','7','10','ab'),('2','7','5','cd'),('3','9','7','ef')])
db.execute('create table customers(id, city)')
db.executemany('insert into customers values (?,?)', [('7','Oslo'),('9','Rome')])
cur = db.execute(sql, params)
cols = [c[0] for c in cur.description]
for row in cur.fetchall():
    print('\t'.join(x for c, v in zip(cols, row) for x in (c, str(v))))
```

**`$S/probe.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
using sel::sql::Binding; using sel::sql::Sql; using sel::sql::SqlKind;
static const char* S = "/tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-P";
static sel::Value rows_of(const std::vector<std::vector<std::pair<std::string,std::string>>>& rs) {
  std::vector<sel::Value> out;
  for (auto& r : rs) { std::vector<std::string> k; std::vector<sel::Value> v; for (auto& [a,b] : r) { k.push_back(a); v.push_back(sel::Value::text(b)); } out.push_back(sel::Value::record(k, v)); }
  return sel::Value::list(out);
}
int main(int argc, char** argv) {
  sel::sql::Bindings bindings;
  bindings.emplace("ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)}, {"CUSTOMER_ID", Binding::column("customer_id", std::nullopt, SqlKind::Num)}, {"AMOUNT", Binding::column("amount", std::nullopt, SqlKind::Num)}, {"NAME", Binding::column("name", std::nullopt, SqlKind::Text)}}));
  bindings.emplace("CUSTOMERS", Binding::relation("customers", "c", {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)}, {"CITY", Binding::column("city", std::nullopt, SqlKind::Text)}}));
  sel::Value orders = rows_of({{{"id","1"},{"customer_id","7"},{"amount","10"},{"name","ab"}},{{"id","2"},{"customer_id","7"},{"amount","5"},{"name","cd"}},{{"id","3"},{"customer_id","9"},{"amount","7"},{"name","ef"}}});
  sel::Value customers = rows_of({{{"id","7"},{"city","Oslo"}},{{"id","9"},{"city","Rome"}}});
  auto runner = [&](const std::string& sql, const std::vector<sel::Value>& params) {
    std::string req = std::string(S) + "/req.txt";
    { std::ofstream f(req); f << sql << "\n"; for (auto& p : params) f << p.as_text() << "\n"; }
    std::string cmd = std::string("python3 ") + S + "/sqlrun_tsv.py " + req;
    FILE* pipe = popen(cmd.c_str(), "r"); std::string out; char buf[4096];
    while (fgets(buf, sizeof buf, pipe)) out += buf; pclose(pipe);
    std::vector<sel::Value> rows; std::istringstream in(out); std::string line;
    while (std::getline(in, line)) { std::vector<std::string> k; std::vector<sel::Value> v; std::istringstream ls(line); std::string a, b;
      while (std::getline(ls, a, '\t') && std::getline(ls, b, '\t')) { k.push_back(a); v.push_back(sel::Value::text(b)); }
      rows.push_back(sel::Value::record(k, v)); }
    return sel::Value::list(rows);
  };
  std::ifstream cases(argv[1]); std::string src;
  while (std::getline(cases, src)) {
    if (src.empty() || src[0] == '#') continue;
    sel::Program program = sel::compile(src);
    std::string want, got;
    try { sel::Value c = sel::Value::none(); c.set("ORDERS", orders); c.set("CUSTOMERS", customers); want = program.run(c).dump(); }
    catch (const sel::SelError& e) { want = "ERR " + e.code() + "@"; }
    sel::sql::HybridPlan plan = Sql::plan_hybrid(program, "sqlite", bindings);
    std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    std::string sql = plan.sql_statement ? plan.sql_statement->as_statement() : "";
    try { sel::Value c = sel::Value::none(); c.set("ORDERS", orders); c.set("CUSTOMERS", customers); got = Sql::execute_hybrid(plan, runner, c).dump(); }
    catch (const sel::SelError& e) { got = "ERR " + e.code() + "@"; }
    std::cout << "### " << src << "\n  plan: " << kind << "\n  sql : " << sql << "\n  run : " << want << "\n  hyb : " << got << "\n  " << (want == got ? "SAME" : "*** DIFFER ***") << "\n";
  }
}
```

**`$S/probe.lisp`**

```lisp
(in-package #:sel-cli)
(defparameter *s* "/tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-P")
(defun rows-of (specs)
  (sel:from-native (mapcar (lambda (r) (let ((h (make-hash-table :test 'equal))) (loop for (k . v) in r do (setf (gethash k h) v)) h)) specs)))
(defun split-tabs (line)
  (loop with start = 0 for pos = (position #\Tab line :start start)
        collect (subseq line start pos) while pos do (setf start (1+ pos))))
(defun runner (sql params)
  (let ((req (concatenate 'string *s* "/req.txt")))
    (with-open-file (f req :direction :output :if-exists :supersede)
      (write-line sql f) (dolist (p params) (write-line (sel:as-text p) f)))
    (let* ((out (with-output-to-string (o)
                  (sb-ext:run-program "python3" (list (concatenate 'string *s* "/sqlrun_tsv.py") req) :search t :output o)))
           (rows (sel:evaluate "LIST()"))
           (i 0))
      (dolist (line (split-lines out))
        (unless (zerop (length line))
          (let ((rec (sel:make-none)) (parts (split-tabs line)))
            (loop while parts do (sel:value-set rec (pop parts) (sel:from-native (pop parts))))
            (incf i)
            (sel:value-set rows (princ-to-string i) rec))))
      rows)))
(defun main ()
  (let* ((bindings (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o"
                     (list (cons "ID" (sel.sql:binding-column "id" "o" :num)) (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "o" :num))
                           (cons "AMOUNT" (sel.sql:binding-column "amount" "o" :num)) (cons "NAME" (sel.sql:binding-column "name" "o" :text)))))
                         (cons "CUSTOMERS" (sel.sql:binding-relation "customers" "c"
                     (list (cons "ID" (sel.sql:binding-column "id" "c" :num)) (cons "CITY" (sel.sql:binding-column "city" "c" :text)))))))
         (orders (rows-of '((("id" . "1") ("customer_id" . "7") ("amount" . "10") ("name" . "ab"))
                            (("id" . "2") ("customer_id" . "7") ("amount" . "5") ("name" . "cd"))
                            (("id" . "3") ("customer_id" . "9") ("amount" . "7") ("name" . "ef")))))
         (customers (rows-of '((("id" . "7") ("city" . "Oslo")) (("id" . "9") ("city" . "Rome"))))))
    (dolist (src (split-lines (read-text-file (first (script-args)))))
      (when (and (plusp (length src)) (char/= (char src 0) #\#))
        (let* ((program (sel:compile-source src))
               (ctx (lambda () (let ((c (sel:make-none))) (sel:value-set c "ORDERS" orders) (sel:value-set c "CUSTOMERS" customers) c)))
               (want (handler-case (sel:value-dump (sel:run program (funcall ctx)))
                       (sel:sel-error (e) (format nil "ERR ~a@" (sel:sel-error-code e)))))
               (plan (sel.sql:plan-hybrid program "sqlite" bindings))
               (kind (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql") ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory") (t "hybrid")))
               (sql (if (sel.sql:hybrid-plan-sql-statement plan) (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan)) ""))
               (got (handler-case (let ((r (sel.sql:execute-hybrid plan #'runner (funcall ctx)))) (sel:value-dump (if (sel:value-p r) r (sel:from-native r))))
                      (sel:sel-error (e) (format nil "ERR ~a@" (sel:sel-error-code e))))))
          (format t "### ~a~%  plan: ~a~%  sql : ~a~%  run : ~a~%  hyb : ~a~%  ~a~%" src kind sql want got (if (string= want got) "SAME" "*** DIFFER ***")))))
    0))
```

**`$S/dbg.lisp`**

```lisp
(in-package #:sel-cli)
(defun dbg ()
  (let* ((rows (runner "SELECT \"o\".\"id\" AS \"id\", \"o\".\"name\" AS \"name\" FROM \"orders\" \"o\" LIMIT 1" nil)))
    (format t "rows: ~a~%" (sel:value-dump rows))
    (let ((ctx (sel:make-none)))
      (sel:value-set ctx "_INPUT" rows)
      (format t "cont: ~a~%" (sel:value-dump (sel:run (sel:compile-source "_INPUT .> MAP(RECORD(\"id\", _[\"id\"], \"shout\", REPEAT(_[\"name\"], 2)))") ctx))))
    (let ((ctx (sel:make-none)))
      (sel:value-set ctx "_INPUT" (sel:evaluate "LIST(RECORD(\"id\", \"2\", \"name\", \"cd\"))"))
      (format t "cont2: ~a~%" (sel:value-dump (sel:run (sel:compile-source "_INPUT .> MAP(RECORD(\"id\", _[\"id\"], \"shout\", REPEAT(_[\"name\"], 2)))") ctx)))))
  0)
```

**`$S/dbg2.lisp`**

```lisp
(in-package #:sel-cli)
(defun dbg2 ()
  (let* ((bindings (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o"
                     (list (cons "ID" (sel.sql:binding-column "id" "o" :num)) (cons "NAME" (sel.sql:binding-column "name" "o" :text)))))))
         (program (sel:compile-source "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"shout\", REPEAT(_[\"name\"], 2))) .> FILTER(_[\"id\"] > 1) .> TAKE(1)"))
         (plan (sel.sql:plan-hybrid program "sqlite" bindings)))
    (format t "cont ast: ~s~%" (sel.sql:hybrid-plan-continuation-ast plan))
    (handler-case (format t "res: ~a~%" (sel:value-dump (sel.sql:execute-hybrid plan #'runner)))
      (sel:sel-error (e) (format t "ERR ~a ~a~%" (sel:sel-error-code e) e)))
    (handler-case (format t "res-no-ctx-mock: ~a~%" (sel:value-dump (sel.sql:execute-hybrid plan (lambda (q p) (declare (ignore q p)) (sel:evaluate "LIST(RECORD(\"id\", \"2\", \"name\", \"cd\"))")))))
      (sel:sel-error (e) (format t "ERR ~a ~a~%" (sel:sel-error-code e) e))))
  0)
```

**`$S/dbg3.lisp`**

```lisp
(in-package #:sel-cli)
(defun dbg3 ()
  (let* ((bindings (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o"
                     (list (cons "ID" (sel.sql:binding-column "id" "o" :num)) (cons "NAME" (sel.sql:binding-column "name" "o" :text)))))))
         (program (sel:compile-source "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"shout\", REPEAT(_[\"name\"], 2))) .> FILTER(_[\"id\"] > 1) .> TAKE(1)"))
         (plan (sel.sql:plan-hybrid program "sqlite" bindings)))
    (handler-case (format t "res: ~a~%" (sel:value-dump (sel.sql:execute-hybrid plan (lambda (q p) (format t "SQL: ~s~%PARAMS: ~s~%" q p) (let ((r (runner q p))) (format t "ROWS: ~a~%" (sel:value-dump r)) r)))))
      (sel:sel-error (e) (format t "ERR ~a ~a~%" (sel:sel-error-code e) e))))
  0)
```

**`$S/cases2.txt`**

```
ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "note", REPEAT("x", 2))) .> BUCKET(_["customer_id"])
ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "tag", JOIN(LIST("x"), "-"))) .> BUCKET(_["customer_id"]) .> MAP(RECORD("customer_id", _K, "n", COUNT(_)))
ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "note", REPEAT("x", 2))) .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> MAP(RECORD("customer_id", _K))
```

### Commands run and their output

Run Python sqlite probe of run() vs execute_hybrid

```bash
mkdir -p $SCRATCH/verify-P && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel && PYTHONPATH=$PWD/python python3 $SCRATCH/verify-P/probe.py $SCRATCH/verify-P/cases.txt
```

```
Exit code 1
Traceback (most recent call last):
  File "$SCRATCH/verify-P/probe.py", line 39, in <module>
    sql = plan.sql_statement.sql if plan.sql_statement else ''
          ^^^^^^^^^^^^^^^^^^^^^^
AttributeError: 'Fragment' object has no attribute 'sql'
```

Run Python sqlite probe of run() vs execute_hybrid

```bash
S=$SCRATCH/verify-P && sed -i "s/plan.sql_statement.sql if/plan.sql_statement.as_statement('inline') if/" $S/probe.py && PYTHONPATH=$PWD/python python3 $S/probe.py $S/cases.txt
```

```
### ORDERS .> MAP(RECORD("cid", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> SORT_BY(_["cid"]) .> TAKE(2)
  plan: hybrid
  sql : SELECT "_sub1".* FROM (SELECT "o"."customer_id" AS "cid", "o"."name" AS "name" FROM "orders" "o") "_sub1" ORDER BY "_sub1"."cid" ASC LIMIT 2
  run : -{"1"=-{"cid"=t"7", "shout"=t"abab"}, "2"=-{"cid"=t"7", "shout"=t"cdcd"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("amount", _["amount"] + 1, "shout", REPEAT(_["name"], 2)))
  plan: hybrid
  sql : SELECT ("o"."amount" + '1') AS "amount", "o"."name" AS "name" FROM "orders" "o"
  run : -{"1"=-{"amount"=t"11", "shout"=t"abab"}, "2"=-{"amount"=t"6", "shout"=t"cdcd"}, "3"=-{"amount"=t"8", "shout"=t"efef"}}
  hyb : -{"1"=-{"amount"=t"12", "shout"=t"abab"}, "2"=-{"amount"=t"7", "shout"=t"cdcd"}, "3"=-{"amount"=t"9", "shout"=t"efef"}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"])
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" FROM (SELECT "o"."customer_id" AS "customer_id", "o"."name" AS "name" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"7"=-{"1"=-{"customer_id"=t"7", "shout"=t"abab"}, "2"=-{"customer_id"=t"7", "shout"=t"cdcd"}}, "9"=-{"1"=-{"customer_id"=t"9", "shout"=t"efef"}}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" AS "cid", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" AS "customer_id", "o"."name" AS "name" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"1"=-{"cid"=t"7", "n"=t"2"}, "2"=-{"cid"=t"9", "n"=t"1"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> MAP(RECORD("id", _["id"]))
  plan: hybrid
  sql : SELECT "_sub1"."id" AS "id" FROM (SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o") "_sub1"
  run : -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> SELECT_COLS("id")
  plan: hybrid
  sql : SELECT "_sub1"."id" FROM (SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o") "_sub1"
  run : -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])
  plan: pure_memory
  sql : 
  run : -{"1"=-{"ORDERS"=-{"id"=t"1", "customer_id"=t"7", "shout"=t"abab", "ORDERS"=-{"id"=t"1", "customer_id"=t"7", "shout"=t"abab"}, "orders"=-{"id"=t"1", "customer_id"=t"7", "shout"=t"abab"}}, "orders"=-{"id"=t"1", "customer_id"=t"7", "shout"=t"abab", "ORDERS"=-{"id"=t"1", "customer_id"=t"7", "shout"=t"abab"}, "orders"=-{"id"=t"1", "customer_id"=t"7", "shout"=t"abab"}}, "_1"=-{"id"=t"1", "customer_id"=t"7", "shout"=t"abab", "ORDERS"=-{"id"=t"1", "customer_id"=t"7", "shout"=t"abab"}, "orders"=-{"id"=t"1", "customer_id"=t"7", "shout"=t"abab"}}, "CUSTOMERS"=-{"id"=t"7", "city"=t"Oslo", "CUSTOMERS"=-{"id"=t"7", "city"=t"Oslo"}, "customers"=-{"id"=t"7", "city"=t"Oslo"}}, "customers"=-{"id"=t"7", "city"=t"Oslo", "CUSTOMERS"=-{"id"=t"7", "city"=t"Oslo"}, "customers"=-{"id"=t"7", "city"=t"Oslo"}}, "_2"=-{"id"=t"7", "city"=t"Oslo", "
… (truncated)
```

Check sqlite availability per host

```bash
which sqlite3; php -m | grep -i sqlite; node -e "try{require('node:sqlite');console.log('node:sqlite ok')}catch(e){console.log('no node:sqlite', e.message)}"; node --version; ls /home/nathan/workspaces/nth-share/sel/cpp/build | head -30
```

```
/usr/bin/which: no sqlite3 in (/home/nathan/.opencode/bin:/home/nathan/.local/bin:/home/nathan/.kimi-code/bin:/home/nathan/.local/bin:/home/nathan/.opencode/bin:/home/nathan/.local/bin:/home/nathan/.kimi-code/bin:/home/nathan/.cargo/bin:/home/nathan/.nvm/versions/node/v22.12.0/bin:/home/nathan/.local/bin:/home/nathan/bin:/usr/lib64/ccache:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/usr/lib/wsl/lib:/mnt/c/Windows/system32:/mnt/c/Windows:/mnt/c/Windows/System32/Wbem:/mnt/c/Windows/System32/WindowsPowerShell/v1.0/:/mnt/c/Windows/System32/OpenSSH/:/mnt/c/Program Files/dotnet/:/mnt/c/Program Files (x86)/NVIDIA Corporation/PhysX/Common:/mnt/c/Program Files/Antelope Audio/Antelope Launcher/:/mnt/c/Program Files/Git/cmd:/mnt/c/Program Files/CMake/bin:/mnt/c/Program Files (x86)/Windows Kits/10/Windows Performance Toolkit/:/mnt/c/Program Files/WSL/:/mnt/c/Users/marci/AppData/Local/agy/bin:/mnt/c/Users/marci/.local/bin:/mnt/c/Users/marci/AppData/Local/Microsoft/WindowsApps:/mnt/c/Users/marci/AppData/Local/Programs/Microsoft VS Code/bin:/mnt/c/Users/marci/AppData/Local/nvm:/mnt/c/nvm4w/nodejs:/mnt/c/Users/marci/AppData/Local/Microsoft/WinGet/Links:/home/nathan/.composer/vendor/bin)
pdo_sqlite
sqlite3
no node:sqlite No such built-in module: node:sqlite
v22.12.0
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
```

Create shared SQLite oracle helper and test it

```bash
S=$SCRATCH/verify-P && # (file written, see "Reproduction scripts" above)
echo '{"sql":"SELECT \"o\".\"id\" AS \"id\" FROM \"orders\" \"o\" LIMIT ?","params":["2"]}' | python3 $S/sqlrun.py
```

```
[{"id": "1"}, {"id": "2"}]
```

Run JS probe against sqlite oracle

```bash
grep -n "asStatement\|bindings()\|as_statement" js/src/sql/fragment.mjs | head -5; grep -n "dump\|fromNative" js/src/value.mjs | head -8; S=$SCRATCH/verify-P; # (file written, see "Reproduction scripts" above)
node $S/probe.mjs $S/cases.txt 2>&1 | grep -v '^  run :.*ORDERS"=' | head -120
```

```
44:        'this expression yields a statement, and a SQL expression is a scalar; use asStatement()');
50:  asStatement(mode = 'inline') {
87:  bindings() {
121:  // bindings() hands back Values, and the cast wrapping the placeholder is what
137:    let nth = 0;                       // position in bindings(), not slot id
414:  // and the evaluator's, and it was the last one left uncounted. clone, eql, dump
488:  // --- canonical dump (conformance/README.md) -------------------------------
490:  dump() { return this.dumpAt(1); }
492:  dumpAt(depth) {
503:    const parts = this.entries().map(([k, v]) => `${quoteDump(k)}=${v.dumpAt(depth + 1)}`);
509:  static fromNative(x) { return Value.fromNativeAt(x, 1); }
511:  static fromNativeAt(x, depth) {
522:    if (Array.isArray(x)) return Value.listOwned(x.map((e) => Value.fromNativeAt(e, depth + 1)));
### ORDERS .> MAP(RECORD("cid", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> SORT_BY(_["cid"]) .> TAKE(2)
  plan: hybrid
  sql : SELECT "_sub1".* FROM (SELECT "o"."customer_id" AS "cid", "o"."name" AS "name" FROM "orders" "o") "_sub1" ORDER BY "_sub1"."cid" ASC LIMIT 2
  run : -{"1"=-{"cid"=t"7", "shout"=t"abab"}, "2"=-{"cid"=t"7", "shout"=t"cdcd"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("amount", _["amount"] + 1, "shout", REPEAT(_["name"], 2)))
  plan: hybrid
  sql : SELECT ("o"."amount" + '1') AS "amount", "o"."name" AS "name" FROM "orders" "o"
  run : -{"1"=-{"amount"=t"11", "shout"=t"abab"}, "2"=-{"amount"=t"6", "shout"=t"cdcd"}, "3"=-{"amount"=t"8", "shout"=t"efef"}}
  hyb : -{"1"=-{"amount"=t"12", "shout"=t"abab"}, "2"=-{"amount"=t"7", "shout"=t"cdcd"}, "3"=-{"amount"=t"9", "shout"=t"efef"}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"])
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" FROM (SELECT "o"."customer_id" AS "customer_id", "o"."name" AS "name" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"7"=-{"1"=-{"customer_id"=t"7", "shout"=t"abab"}, "2"=-{"customer_id"=t"7", "shout"=t"cdcd"}}, "9"=-{"1"=-{"customer_id"=t"9", "shout"=t"efef"}}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" AS "cid", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" AS "customer_id", "o"."name" AS "name" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"1"=-{"cid"=t"7", "n"=t"2"}, "2"=-{"cid"=t"9", "n"=t"1"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> MAP(RECORD("id", _["id"]))
  plan: hybrid
  sql : SELECT "_sub1"."id" AS "id" FROM (SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o") "_sub1"
  run : -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> SELECT_COLS("id")
  plan: hybrid
  sql : SELECT "_sub1"."id" FROM (SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o") "_sub1"
  run : -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])
  plan: pur
… (truncated)
```

Probe LINK after fall-through MAP in Python

```bash
S=$SCRATCH/verify-P && # (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $S/probe.py $S/link.txt 2>&1 | cut -c1-400
```

```
### ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> LINK(CUSTOMERS, _1["id"] == _2["id"])
  plan: pure_memory
  sql : 
  run : -
  hyb : -
  SAME
### ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> LINK_LEFT(CUSTOMERS, _1["id"] == _2["id"])
  plan: pure_memory
  sql : 
  run : -{"1"=-{"ORDERS"=-{"id"=t"1", "note"=-}, "orders"=-{"id"=t"1", "note"=-}, "_1"=-{"id"=t"1", "note"=-, "ORDERS"=-{"id"=t"1", "note"=-}, "orders"=-{"id"=t"1", "note"=-}}, "CUSTOMERS"=-{"id"=-, "city"=-, "CUSTOMERS"=-, "customers"=-}, "customers"=-{"id"=-, "city"=-, "CUSTOMERS"=-, "customers"=-}, "_2"=-{"id"=-, "city"=-, "CUSTOMERS"=-, "customers"=-}, "note"=-}, "2"=-{"ORDERS"=-{"id"=t"2", "n
  hyb : -{"1"=-{"ORDERS"=-{"id"=t"1", "note"=-}, "orders"=-{"id"=t"1", "note"=-}, "_1"=-{"id"=t"1", "note"=-, "ORDERS"=-{"id"=t"1", "note"=-}, "orders"=-{"id"=t"1", "note"=-}}, "CUSTOMERS"=-{"id"=-, "city"=-, "CUSTOMERS"=-, "customers"=-}, "customers"=-{"id"=-, "city"=-, "CUSTOMERS"=-, "customers"=-}, "_2"=-{"id"=-, "city"=-, "CUSTOMERS"=-, "customers"=-}, "note"=-}, "2"=-{"ORDERS"=-{"id"=t"2", "n
  SAME
```

Check LINK-after-MAP classification across dialects

```bash
PYTHONPATH=$PWD/python python3 - <<'EOF'
from sel import compile as c
from sel.sql import Binding, Sql
b = {'ORDERS': Binding.relation('orders','o',{'ID': Binding.column('id','o','NUM'),'NAME': Binding.column('name','o','TEXT')}),
     'CUSTOMERS': Binding.relation('customers','c',{'ID': Binding.column('id','c','NUM'),'CITY': Binding.column('city','c','TEXT')})}
for d in ['sqlite','mariadb','postgresql']:
    for src in ['ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> LINK(CUSTOMERS, _1["id"] == _2["id"])',
                'ORDERS .> MAP(RECORD("id", _["id"])) .> LINK(CUSTOMERS, _1["id"] == _2["id"])',
                'ORDERS .> LINK(CUSTOMERS, _1["id"] == _2["id"])']:
        p = Sql.plan_hybrid(c(src), d, b)
        k = 'pure_sql' if p.pure_sql else 'pure_memory' if p.pure_memory else 'hybrid'
        print(d, k, p.sql_statement.as_statement('inline') if p.sql_statement else '', '|', src)
    try:
        print(d, 'stmt:', Sql.translate_statement(c('ORDERS .> MAP(RECORD("id", _["id"])) .> LINK(CUSTOMERS, _1["id"] == _2["id"])'), d, b).as_statement('inline'))
    except Exception as e: print(d, 'stmt refused', getattr(e,'code',e), e)
EOF
```

```
sqlite pure_memory  | ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> LINK(CUSTOMERS, _1["id"] == _2["id"])
sqlite hybrid SELECT "o"."id" AS "id" FROM "orders" "o" | ORDERS .> MAP(RECORD("id", _["id"])) .> LINK(CUSTOMERS, _1["id"] == _2["id"])
sqlite pure_sql SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON (CAST("o"."id" AS NUMERIC) = CAST("c"."id" AS NUMERIC)) | ORDERS .> LINK(CUSTOMERS, _1["id"] == _2["id"])
sqlite stmt refused E_SQL_UNSUPPORTED E_SQL_UNSUPPORTED at 1:59: dialect sqlite has no way to ask whether a value is a number, so an operand it has not been told is one cannot be read as one here; declare the binding NUM if the column really is numeric
mariadb hybrid SELECT `_sub1`.* FROM (SELECT `o`.`id` AS `id`, `o`.`name` AS `name` FROM `orders` `o`) `_sub1` INNER JOIN `customers` `c` ON (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END = `c`.`id`) | ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> LINK(CUSTOMERS, _1["id"] == _2["id"])
mariadb pure_sql SELECT `_sub1`.* FROM (SELECT `o`.`id` AS `id` FROM `orders` `o`) `_sub1` INNER JOIN `customers` `c` ON (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END = `c`.`id`) | ORDERS .> MAP(RECORD("id", _["id"])) .> LINK(CUSTOMERS, _1["id"] == _2["id"])
mariadb pure_sql SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`id` = `c`.`id`) | ORDERS .> LINK(CUSTOMERS, _1["id"] == _2["id"])
mariadb stmt: SELECT `_sub1`.* FROM (SELECT `o`.`id` AS `id` FROM `orders` `o`) `_sub1` INNER JOIN `customers` `c` ON (CASE WHEN (`_sub1`.`id` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`_sub1`.`id` AS DECIMAL(65,10)) ELSE NULL END = `c`.`id`)
postgresql hybrid SELECT "_sub1".* FROM (SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o") "_sub1" INNER JOIN "customers" "c" ON (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END = "c"."id") | ORDERS .> MAP(RECORD("id", _["id"], "note", GET(_["name"], 1))) .> LINK(CUSTOMERS, _1["id"] == _2["id"])
postgresql pure_sql SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" INNER JOIN "customers" "c" ON (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END = "c"."id") | ORDERS .> MAP(RECORD("id", _["id"])) .> LINK(CUSTOMERS, _1["id"] == _2["id"])
postgresql pure_sql SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON ("o"."id" = "c"."id") | ORDERS .> LINK(CUSTOMERS, _1["id"] == _2["id"])
postgresql stmt: SELECT "_sub1".* FROM (SELECT "o"."id" AS "id" FROM "orders" "o") "_sub1" INNER JOIN "customers" "c" ON (CASE WHEN (CAST("_sub1"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub1"."id" AS NUMERIC) ELSE NULL END = "c"."id")
```

Run PHP probe with native PDO sqlite

```bash
S=$SCRATCH/verify-P && # (file written, see "Reproduction scripts" above)
php $S/probe.php $S/cases.txt 2>&1 | grep -v 'ORDERS"='
```

```
### ORDERS .> MAP(RECORD("cid", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> SORT_BY(_["cid"]) .> TAKE(2)
  plan: hybrid
  sql : SELECT "_sub1".* FROM (SELECT "o"."customer_id" AS "cid", "o"."name" AS "name" FROM "orders" "o") "_sub1" ORDER BY "_sub1"."cid" ASC LIMIT 2
  run : -{"1"=-{"cid"=t"7", "shout"=t"abab"}, "2"=-{"cid"=t"7", "shout"=t"cdcd"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("amount", _["amount"] + 1, "shout", REPEAT(_["name"], 2)))
  plan: hybrid
  sql : SELECT ("o"."amount" + '1') AS "amount", "o"."name" AS "name" FROM "orders" "o"
  run : -{"1"=-{"amount"=t"11", "shout"=t"abab"}, "2"=-{"amount"=t"6", "shout"=t"cdcd"}, "3"=-{"amount"=t"8", "shout"=t"efef"}}
  hyb : -{"1"=-{"amount"=t"12", "shout"=t"abab"}, "2"=-{"amount"=t"7", "shout"=t"cdcd"}, "3"=-{"amount"=t"9", "shout"=t"efef"}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"])
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" FROM (SELECT "o"."customer_id" AS "customer_id", "o"."name" AS "name" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"7"=-{"1"=-{"customer_id"=t"7", "shout"=t"abab"}, "2"=-{"customer_id"=t"7", "shout"=t"cdcd"}}, "9"=-{"1"=-{"customer_id"=t"9", "shout"=t"efef"}}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" AS "cid", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" AS "customer_id", "o"."name" AS "name" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"1"=-{"cid"=t"7", "n"=t"2"}, "2"=-{"cid"=t"9", "n"=t"1"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> MAP(RECORD("id", _["id"]))
  plan: hybrid
  sql : SELECT "_sub1"."id" AS "id" FROM (SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o") "_sub1"
  run : -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> SELECT_COLS("id")
  plan: hybrid
  sql : SELECT "_sub1"."id" FROM (SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o") "_sub1"
  run : -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])
  plan: pure_memory
  sql : 
  SAME
### ORDERS .> MAP(RECORD("id", _["id"], "whole", GET(_, "name")))
  plan: hybrid
  sql : SELECT "o"."id" AS "id" FROM "orders" "o"
  run : -{"1"=-{"id"=t"1", "whole"=t"ab"}, "2"=-{"id"=t"2", "whole"=t"cd"}, "3"=-{"id"=t"3", "whole"=t"ef"}}
  hyb : -{"1"=-{"id"=t"1", "whole"=-}, "2"=-{"id"=t"2", "whole"=-}, "3"=-{"id"=t"3", "whole"=-}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))
  plan: pure_sql
  sql : SELECT "o"."id" AS "id", COUNT(*) AS "n" FROM "orders" "o"
  run : -{"1"=-{"id"=t"1", "n"=t"4"}, "2"=-{"id"=t"2", "n"=t"4"}, "3"=-{"id"=t"3", "n"=t"4"}}
  hyb : -{"1"=-{"id"=t"1", "n"=t"3"}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> FILTER(_["id"] > 1) .> TAKE(1)
  plan: hybrid
  sql : SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" 
… (truncated)
```

Build the C++ probe against the built objects

```bash
S=$SCRATCH/verify-P && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
cd cpp && c++ -std=c++23 -O1 -o $S/probe_cpp $S/probe.cpp build/sel_sql*.o build/map_replay.o build/sel.o 2>&1 | head -20; ls -la $S/probe_cpp
```

```
Exit code 2
$SCRATCH/verify-P/probe.cpp: In function ‘int main(int, char**)’:
$SCRATCH/verify-P/probe.cpp:18:12: error: ‘class sel::sql::Bindings’ has no member named ‘emplace’
   18 |   bindings.emplace("ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)}, {"CUSTOMER_ID", Binding::column("customer_id", std::nullopt, SqlKind::Num)}, {"AMOUNT", Binding::column("amount", std::nullopt, SqlKind::Num)}, {"NAME", Binding::column("name", std::nullopt, SqlKind::Text)}}));
      |            ^~~~~~~
$SCRATCH/verify-P/probe.cpp:19:12: error: ‘class sel::sql::Bindings’ has no member named ‘emplace’
   19 |   bindings.emplace("CUSTOMERS", Binding::relation("customers", "c", {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)}, {"CITY", Binding::column("city", std::nullopt, SqlKind::Text)}}));
      |            ^~~~~~~
ls: cannot access '$SCRATCH/verify-P/probe_cpp': No such file or directory
```

Fix bindings construction, build and run the C++ probe

```bash
S=$SCRATCH/verify-P && python3 - <<'EOF'
import re
S='$SCRATCH/verify-P/probe.cpp'
t=open(S).read()
t=t.replace('''  sel::sql::Bindings bindings;
  bindings.emplace("ORDERS", ''','''  const sel::sql::Bindings bindings({{"ORDERS", ''')
t=t.replace('''}}));
  bindings.emplace("CUSTOMERS", ''','''}})},
  {"CUSTOMERS", ''')
t=t.replace('''SqlKind::Text)}}));
  sel::Value orders''','''SqlKind::Text)}})}});
  sel::Value orders''')
open(S,'w').write(t)
EOF
cd cpp && c++ -std=c++23 -O1 -o $S/probe_cpp $S/probe.cpp build/sel_sql*.o build/map_replay.o build/sel.o 2>&1 | head -20 && $S/probe_cpp $S/cases.txt 2>&1 | grep -v 'ORDERS"='
```

```
### ORDERS .> MAP(RECORD("cid", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> SORT_BY(_["cid"]) .> TAKE(2)
  plan: hybrid
  sql : SELECT "_sub1".* FROM (SELECT "customer_id" AS "cid", "name" AS "name" FROM "orders" "o") "_sub1" ORDER BY "_sub1"."cid" ASC LIMIT 2
  run : -{"1"=-{"cid"=t"7", "shout"=t"abab"}, "2"=-{"cid"=t"7", "shout"=t"cdcd"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("amount", _["amount"] + 1, "shout", REPEAT(_["name"], 2)))
  plan: hybrid
  sql : SELECT ("amount" + '1') AS "amount", "name" AS "name" FROM "orders" "o"
  run : -{"1"=-{"amount"=t"11", "shout"=t"abab"}, "2"=-{"amount"=t"6", "shout"=t"cdcd"}, "3"=-{"amount"=t"8", "shout"=t"efef"}}
  hyb : -{"1"=-{"amount"=t"12", "shout"=t"abab"}, "2"=-{"amount"=t"7", "shout"=t"cdcd"}, "3"=-{"amount"=t"9", "shout"=t"efef"}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"])
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" FROM (SELECT "customer_id" AS "customer_id", "name" AS "name" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"7"=-{"1"=-{"customer_id"=t"7", "shout"=t"abab"}, "2"=-{"customer_id"=t"7", "shout"=t"cdcd"}}, "9"=-{"1"=-{"customer_id"=t"9", "shout"=t"efef"}}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" AS "cid", COUNT(*) AS "n" FROM (SELECT "customer_id" AS "customer_id", "name" AS "name" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"1"=-{"cid"=t"7", "n"=t"2"}, "2"=-{"cid"=t"9", "n"=t"1"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> MAP(RECORD("id", _["id"]))
  plan: hybrid
  sql : SELECT "_sub1"."id" AS "id" FROM (SELECT "id" AS "id", "name" AS "name" FROM "orders" "o") "_sub1"
  run : -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> SELECT_COLS("id")
  plan: hybrid
  sql : SELECT "_sub1"."id" FROM (SELECT "id" AS "id", "name" AS "name" FROM "orders" "o") "_sub1"
  run : -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])
  plan: pure_memory
  sql : 
  SAME
### ORDERS .> MAP(RECORD("id", _["id"], "whole", GET(_, "name")))
  plan: hybrid
  sql : SELECT "id" AS "id" FROM "orders" "o"
  run : -{"1"=-{"id"=t"1", "whole"=t"ab"}, "2"=-{"id"=t"2", "whole"=t"cd"}, "3"=-{"id"=t"3", "whole"=t"ef"}}
  hyb : -{"1"=-{"id"=t"1", "whole"=-}, "2"=-{"id"=t"2", "whole"=-}, "3"=-{"id"=t"3", "whole"=-}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))
  plan: pure_sql
  sql : SELECT "id" AS "id", COUNT(*) AS "n" FROM "orders" "o"
  run : -{"1"=-{"id"=t"1", "n"=t"4"}, "2"=-{"id"=t"2", "n"=t"4"}, "3"=-{"id"=t"3", "n"=t"4"}}
  hyb : -{"1"=-{"id"=t"1", "n"=t"3"}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> FILTER(_["id"] > 1) .> TAKE(1)
  plan: hybrid
  sql : SELECT "id" AS "id", "name" AS "name" FROM "orders" "o" WHERE (CAST("id" AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 1
… (truncated)
```

Look at Lisp loading and API

```bash
cat lisp/bin/sqlt | head -30; sed -n 400,430p lisp/tests/unit.lisp; grep -n "defun make-relation-binding\|defun make-column-binding\|defun relation\b\|(defun column\b\|export" lisp/src/sql/binding.lisp lisp/src/sql/package.lisp 2>/dev/null | head -20; ls lisp/src/sql
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
           (sql (sel.sql:as-statement frag)))
      (is (not (null (search "\"_sub1\"" sql))))
      (is (not (null (search "\"_sub2\"" sql))))
      (is (not (null (search "GROUP BY \"orders\".\"c_id\"" sql))))
      (is (not (null (search "\"_sub2\".\"tax\"" sql)))))))

(test hybrid-execution-planner
  (let* ((orders (sel.sql:binding-relation "orders" "orders"
                   (list (cons "ID" (sel.sql:binding-column "id" "orders"))
                         (cons "C_ID" (sel.sql:binding-column "c_id" "orders"))
                         (cons "AMOUNT" (sel.sql:binding-column "amount" "orders")))))
         (bindings (list (cons "ORDERS" orders))))
    ;; Pure SQL plan
    (let* ((p (sel:compile-source "ORDERS .> FILTER(_['amount'] > 100) .> SORT_BY(_['id'])"))
           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
      (is-true (sel.sql:hybrid-plan-pure-sql-p plan))
      (is-false (sel.sql:hybrid-plan-pure-memory-p plan))
      (is (null (sel.sql:hybrid-plan-continuation-program plan)))
      (let* ((runner-called nil)
             (mock-runner (lambda (sql params)
                            (declare (ignore params))
                            (setf runner-called t)
                            (is (not (null (search "WHERE" sql))))
                            (sel:evaluate "LIST(RECORD('id', 1, 'amount', 120))"))))
        (let ((res (sel.sql:execute-hybrid plan mock-runner)))
          (is-true runner-called)
          (is (= 1 (sel:value-size res))))))

    ;; Split hybrid plan (pushdown prefix + in-memory continuation)
    (let* ((p (sel:compile-source "ORDERS .> FILTER(_['amount'] > 100) .> MAP(RECORD('id', _['id'], 'groups', RGROUPS('([0-9]+)', _['id'])))"))
           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
lisp/src/sql/package.lisp:14:  (:export
lisp/src/sql/binding.lisp:265:(defun relation-alias (rel)
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
```

Run the Lisp probe

```bash
S=$SCRATCH/verify-P && # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --eval '(sb-ext:exit :code (sel-cli::main))' --end-toplevel-options $S/cases.txt 2>&1 | grep -v 'ORDERS"=' | head -120
```

```
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=795865 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 24, Column: 65, File-Position: 1358

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-P/probe.lisp" {1201ECF393}>

Backtrace for: #<SB-THREAD:THREAD tid=795865 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {12020AC983}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK SB-EXT:*INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {12020AC983}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {12020AC983}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {12020AC983}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {12020AC933}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-P/probe.lisp" {1201ECF393}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {72303FC5F4CB}> #<SB-C::SOURCE-INFO {1201E77633}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-P/probe.lisp" {1201ECF393}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-P/probe.lisp" {1201ECF393}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {72303FC5F88B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-P/probe.lisp" {1201ECF393}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-P/probe.lisp" {1201ECF393}>)
9: (LOAD #P"$SCRATCH/verify-P/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-P/probe.lisp") (:EVAL . "(sb-ext:exit :code (sel-cli::main))") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Run the Lisp probe with the SQL system loaded

```bash
S=$SCRATCH/verify-P && sed -i '1i (let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' $S/probe.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --eval '(sb-ext:exit :code (sel-cli::main))' --end-toplevel-options $S/cases.txt 2>&1 | grep -v 'ORDERS"=' | head -120
```

```
Unhandled SB-KERNEL:CASE-FAILURE in thread #<SB-THREAD:THREAD tid=796370 "main thread" RUNNING
                                              {1204030083}>:
  #<HASH-TABLE :TEST EQUAL :COUNT 4 {12026A7F53}> fell through ETYPECASE expression.
  Wanted one of (NULL SEL:VALUE (MEMBER T) STRING INTEGER RATIO FLOAT
                 (VECTOR (UNSIGNED-BYTE 8)) CONS).

Backtrace for: #<SB-THREAD:THREAD tid=796370 "main thread" RUNNING {1204030083}>
0: (SEL::FROM-NATIVE-AT #<HASH-TABLE :TEST EQUAL :COUNT 4 {12026A7F53}> 2)
1: (SEL::FROM-NATIVE-AT (#<HASH-TABLE :TEST EQUAL :COUNT 4 {12026A7F53}> #<HASH-TABLE :TEST EQUAL :COUNT 4 {12026C0003}> #<HASH-TABLE :TEST EQUAL :COUNT 4 {12026C0083}>) 1)
2: (SEL-CLI:MAIN)
3: (SB-INT:SIMPLE-EVAL-IN-LEXENV (SEL-CLI:MAIN) #<NULL-LEXENV>)
4: (SB-INT:SIMPLE-EVAL-IN-LEXENV (EXIT :CODE (SEL-CLI:MAIN)) #<NULL-LEXENV>)
5: (EVAL (EXIT :CODE (SEL-CLI:MAIN)))
6: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-P/probe.lisp") (:EVAL . "(sb-ext:exit :code (sel-cli::main))") (:QUIT)))
7: (SB-IMPL::TOPLEVEL-INIT)
8: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
9: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
10: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Fix row construction and run the Lisp probe

```bash
S=$SCRATCH/verify-P && python3 - <<'EOF'
S='$SCRATCH/verify-P/probe.lisp'
t=open(S).read()
t=t.replace('''(defun rows-of (specs)
  (sel:from-native (mapcar (lambda (r) (let ((h (make-hash-table :test 'equal))) (loop for (k . v) in r do (setf (gethash k h) v)) h)) specs)))''',
'''(defun rows-of (specs)
  (let ((rows (sel:evaluate "LIST()")) (i 0))
    (dolist (r specs) (let ((rec (sel:make-none))) (loop for (k . v) in r do (sel:value-set rec k (sel:from-native v))) (incf i) (sel:value-set rows (princ-to-string i) rec)))
    rows))''')
open(S,'w').write(t)
EOF
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --eval '(sb-ext:exit :code (sel-cli::main))' --end-toplevel-options $S/cases.txt 2>&1 | grep -v 'ORDERS"=' | head -120
```

```
### ORDERS .> MAP(RECORD("cid", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> SORT_BY(_["cid"]) .> TAKE(2)
  plan: hybrid
  sql : SELECT "_sub1".* FROM (SELECT "o"."customer_id" AS "cid", "o"."name" AS "name" FROM "orders" "o") "_sub1" ORDER BY "_sub1"."cid" ASC LIMIT 2
  run : -{"1"=-{"cid"=t"7", "shout"=t"abab"}, "2"=-{"cid"=t"7", "shout"=t"cdcd"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("amount", _["amount"] + 1, "shout", REPEAT(_["name"], 2)))
  plan: hybrid
  sql : SELECT ("o"."amount" + '1') AS "amount", "o"."name" AS "name" FROM "orders" "o"
  run : -{"1"=-{"amount"=t"11", "shout"=t"abab"}, "2"=-{"amount"=t"6", "shout"=t"cdcd"}, "3"=-{"amount"=t"8", "shout"=t"efef"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"])
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" FROM (SELECT "o"."customer_id" AS "customer_id", "o"."name" AS "name" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"7"=-{"1"=-{"customer_id"=t"7", "shout"=t"abab"}, "2"=-{"customer_id"=t"7", "shout"=t"cdcd"}}, "9"=-{"1"=-{"customer_id"=t"9", "shout"=t"efef"}}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" AS "cid", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" AS "customer_id", "o"."name" AS "name" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"1"=-{"cid"=t"7", "n"=t"2"}, "2"=-{"cid"=t"9", "n"=t"1"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> MAP(RECORD("id", _["id"]))
  plan: hybrid
  sql : SELECT "_sub1"."id" AS "id" FROM (SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o") "_sub1"
  run : -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> SELECT_COLS("id")
  plan: hybrid
  sql : SELECT "_sub1"."id" FROM (SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o") "_sub1"
  run : -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}, "3"=-{"id"=t"3"}}
  hyb : ERR E_NO_KEY@
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])
  plan: pure_memory
  sql : 
  SAME
### ORDERS .> MAP(RECORD("id", _["id"], "whole", GET(_, "name")))
  plan: hybrid
  sql : SELECT "o"."id" AS "id" FROM "orders" "o"
  run : -{"1"=-{"id"=t"1", "whole"=t"ab"}, "2"=-{"id"=t"2", "whole"=t"cd"}, "3"=-{"id"=t"3", "whole"=t"ef"}}
  hyb : -{"1"=-{"id"=t"1", "whole"=-}, "2"=-{"id"=t"2", "whole"=-}, "3"=-{"id"=t"3", "whole"=-}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "n", COUNT(_)))
  plan: pure_sql
  sql : SELECT "o"."id" AS "id", COUNT(*) AS "n" FROM "orders" "o"
  run : -{"1"=-{"id"=t"1", "n"=t"4"}, "2"=-{"id"=t"2", "n"=t"4"}, "3"=-{"id"=t"3", "n"=t"4"}}
  hyb : -{"1"=-{"id"=t"1", "n"=t"3"}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> FILTER(_["id"] > 1) .> TAKE(1)
  plan: hybrid
  sql : SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o" WHERE (CAST("o"."id" AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 1
  run : -{"1"=-{"id"=t"2", "shout"=t"
… (truncated)
```

Debug the Lisp runner rows

```bash
S=$SCRATCH/verify-P && # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --load $S/dbg.lisp --eval '(sb-ext:exit :code (sel-cli::dbg))' 2>&1 | head -20
```

```
rows: -{"1"=-{"id"=t"1", "name"=t"ab"}}
cont: -{"1"=-{"id"=t"1", "shout"=t"abab"}}
cont2: -{"1"=-{"id"=t"2", "shout"=t"cdcd"}}
```

Debug the Lisp control case

```bash
S=$SCRATCH/verify-P && # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --load $S/dbg2.lisp --eval '(sb-ext:exit :code (sel-cli::dbg2))' 2>&1 | head -20
```

```
cont ast: #S(SEL::NODE
             :KIND :CALL
             :POS #S(SEL::POS :LINE 1 :COL 11 :OFFSET 10)
             :S "MAP"
             :B NIL
             :GROUPED NIL
             :L NIL
             :R NIL
             :ITEMS (#S(SEL::NODE
                        :KIND :VAR
                        :POS #S(SEL::POS :LINE 1 :COL 11 :OFFSET 10)
                        :S "_INPUT"
                        :B NIL
                        :GROUPED NIL
                        :L NIL
                        :R NIL
                        :ITEMS NIL
                        :SPEC NIL)
                     #S(SEL::NODE
                        :KIND :CALL
```

Show only the result lines of the Lisp debug

```bash
S=$SCRATCH/verify-P && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --load $S/dbg2.lisp --eval '(sb-ext:exit :code (sel-cli::dbg2))' 2>&1 | grep -v "^ " | grep -v "^cont ast"
```

```
ERR E_NO_KEY E_NO_KEY at 1:29: no key "id"
res-no-ctx-mock: -{"1"=-{"id"=t"2", "shout"=t"cdcd"}}
```

Trace SQL, params and rows in the Lisp runner

```bash
S=$SCRATCH/verify-P && # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/probe.lisp --load $S/dbg3.lisp --eval '(sb-ext:exit :code (sel-cli::dbg3))' 2>&1 | grep -v "^ " | head; cat $S/req.txt
```

```
SQL: "SELECT \"o\".\"id\" AS \"id\", \"o\".\"name\" AS \"name\" FROM \"orders\" \"o\" WHERE (CAST(\"o\".\"id\" AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 1"
PARAMS: (#S(SEL:VALUE
ROWS: -{"1"=-{"Traceback (most recent call last):"=-}, "2"=-{"  File \"$SCRATCH/verify-P/sqlrun_tsv.py\", line 10, in <module>"=-}, "3"=-{"    cur = db.execute(sql, params)"=-}, "4"=-{"sqlite3.ProgrammingError: Incorrect number of bindings supplied. The current statement uses 0, and there are 1 supplied."=-}}
ERR E_NO_KEY E_NO_KEY at 1:29: no key "id"
SELECT "o"."id" AS "id", "o"."name" AS "name" FROM "orders" "o" WHERE (CAST("o"."id" AS NUMERIC) > CAST('1' AS NUMERIC)) LIMIT 1
1
```

Run bucket-after-MAP shapes and collect error positions in Python

```bash
S=$SCRATCH/verify-P && # (file written, see "Reproduction scripts" above)
sed -i "s/'ERR ' + getattr(e, 'code', type(e).__name__) + '@' + str(getattr(e, 'pos', ''))/'ERR ' + getattr(e, 'code', type(e).__name__) + '@' + str(getattr(e, 'line', '')) + ':' + str(getattr(e, 'col', ''))/g" $S/probe.py; PYTHONPATH=$PWD/python python3 $S/probe.py $S/cases2.txt; PYTHONPATH=$PWD/python python3 $S/probe.py $S/cases.txt | grep "ERR" | sort | uniq -c
```

```
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "note", REPEAT("x", 2))) .> BUCKET(_["customer_id"])
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" FROM (SELECT "o"."customer_id" AS "customer_id" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"7"=-{"1"=-{"customer_id"=t"7", "note"=t"xx"}, "2"=-{"customer_id"=t"7", "note"=t"xx"}}, "9"=-{"1"=-{"customer_id"=t"9", "note"=t"xx"}}}
  hyb : -{"1"=-{"customer_id"=t"7", "note"=t"xx"}, "2"=-{"customer_id"=t"9", "note"=t"xx"}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "tag", JOIN(LIST("x"), "-"))) .> BUCKET(_["customer_id"]) .> MAP(RECORD("customer_id", _K, "n", COUNT(_)))
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" AS "customer_id", COUNT(*) AS "n" FROM (SELECT "o"."customer_id" AS "customer_id" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id"
  run : -{"1"=-{"customer_id"=t"7", "n"=t"2"}, "2"=-{"customer_id"=t"9", "n"=t"1"}}
  hyb : -{"1"=-{"customer_id"=t"7", "tag"=t"x"}, "2"=-{"customer_id"=t"9", "tag"=t"x"}}
  *** DIFFER ***
### ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "note", REPEAT("x", 2))) .> BUCKET(_["customer_id"]) .> FILTER(COUNT(_) > 1) .> MAP(RECORD("customer_id", _K))
  plan: hybrid
  sql : SELECT "_sub1"."customer_id" AS "customer_id" FROM (SELECT "o"."customer_id" AS "customer_id" FROM "orders" "o") "_sub1" GROUP BY "_sub1"."customer_id" HAVING (CAST(COUNT(*) AS NUMERIC) > CAST('1' AS NUMERIC))
  run : -{"1"=-{"customer_id"=t"7"}}
  hyb : -{"1"=-{"customer_id"=t"7", "note"=t"xx"}}
  *** DIFFER ***
      2   hyb : ERR E_NO_KEY@1:30
      1   hyb : ERR E_NO_KEY@1:38
      2   hyb : ERR E_NO_KEY@1:50
      2   hyb : ERR E_NO_KEY@1:54
      1   hyb : ERR E_NO_KEY@1:72
```

