# Y. In-memory LINK builds rows with duplicate keys in JS and Lisp but not PHP/Python/C++

**Status:** FIXED 2026-09-16 — facets 1/2 (the in-memory row) in the first batch; facet 3 (the lanes) with W2: a joined row in SQL is now its promoted fields, in the statement and in a derived table, and an unprojected join is not a split point (`joinRowsLackBinders`); `sql/cases/26-links.sqlt`. Spec §7.4 "Joined rows" now specifies the row: each key once, in order of first occurrence, a binder key holding the row this LINK bound; `make_joined_row` in all five hosts builds it that way (JS and Lisp dedupe; every host's uncompiled path now binds `_1`/`X` like its compiled projector did). `rel.link.row-shape-named-left`, `rel.link.row-shape-literal-left`, `rel.link.chained-binders-hold-this-links-rows`, `rel.link-left.unmatched-row`.

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** js, lisp, php, python, cpp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

All three facets reproduce. In memory, JS and Lisp build a LINK row with duplicate relation-name keys (no dedupe in makeJoinedRow / make-joined-row) while PHP, Python and C++ dedupe first-wins, so COUNT/INDEXES/dump differ and even plain key access diverges: `J[1]["X"]["x"]["id"]` is `1` on php/cpp/py and `E_NO_KEY` on node/lisp. Across lanes, a LINK prefix renders as `SELECT o.*` (left columns only) in every host, so a hybrid LINK-then-BUCKET plan's continuation raises E_NO_KEY@1:69 where run() answers, and `LINK .> TAKE(2) .> MAP(_["name"])` is classified pure_sql yet its SQL is rejected by SQLite ("no such column: _sub1.name"). LINK appears in no conformance file, spec, or README; everything is pre-existing at 8fe0e3a, but the commit's new fixture `plan.tables.first-use-order` pins the `SELECT o.* … INNER JOIN … LIMIT 2` shape as a correct pure_sql plan.

## Suggested fix — case first

Smallest fix for the host divergence: make JS and Lisp dedupe first-wins like the other three — in js/src/builtins/structure.mjs makeJoinedRow replace `put` with a Set-guarded version (`if (seen.has(key)) return; seen.add(key); entries.push(...)`), and in lisp/src/builtins/structure.lisp make-joined-row skip a push when `(assoc k out-children :test #'string=)` already holds the key (and mirror it in the pre-compiled join projector at structure.lisp ~394 / structure.mjs makeJoinProjector, which copies the same slot list). Add first a conformance case, e.g. in conformance/15-relational.selt: `### name: rel.link.row-shape-named-left` with `X = LIST(RECORD("id", 1, "cid", 7)); Y = LIST(RECORD("id", 7, "name", "ann")); J = X .> LINK(Y, _1["cid"] == _2["id"]); LIST(COUNT(J[1]), INDEXES(J[1]), COUNT(J[1]["X"]))` expecting the PHP/Python/C++ answer (8 keys, X/x/_1/Y/y/_2/cid/name, 4), plus a literal-left twin — and decide in spec whether "X" holds the raw or alias-injected row, since the two host groups also disagree on that. For the lane defect (separate, larger): the join statement must project the joined columns (o.*, c.* or the promoted column list) whenever a LINK ends the SQL prefix or feeds a derived table, or the planner must refuse LINK as a split point / pure_sql tail; first case to add is an executed one in python/tests/test_unit.py next to test_bucket_plans_answer_what_the_evaluator_answers_on_sqlite with `ORDERS .> LINK(CUSTOMERS, ...) .> BUCKET(_["country"]) .> MAP(RECORD("co", _K, "tag", JOIN(LIST(_K), "-")))` compared against run().

## Verifier reasoning

Facet 1/2 (in-memory row shape): js/src/builtins/structure.mjs:246 `const put = (key, value) => { entries.push([key, value]); }` and lisp/src/builtins/structure.lisp:321-353 (`push (cons b1 r1)` after copying nested children) never check for an existing key, and the left row handed in has already been alias-injected by ensureRowTableAlias (structure.mjs:203-226, structure.lisp:285), so the nested-record loop copies "X"/"x" once and put(b1)/put(low1) add them again. python/sel/builtins/structure.py:247-251 (`if key not in present`), php/src/Builtins/Structure.php:266-268 (`if (!isset($present[$key]))`) and cpp/sel.cpp:3010-3012 (`if (!out.has(key)) out.set(key, value)`) dedupe first-wins. The dedupe also changes which value "X" holds: JS/Lisp's first "X" is the raw row (2 keys), the other three keep the alias-injected row (4 keys) — a value difference reachable by ordinary indexing, not only by COUNT/INDEXES. A literal-list left side (b1 = "_1") agrees in all five, as claimed. `git diff --stat 8fe0e3a ed16df2` touches none of structure.mjs/structure.lisp/structure.py/Structure.php, and the cpp diff hunks (3909+, 4090+, 5098+…) are outside make_joined_row at 3005; `git show 8fe0e3a:python/sel/builtins/structure.py` line 249 already has the `present` guard and the old JS put (line 246) already pushes blindly — pre-existing. `grep -rn LINK conformance/ spec/ docs/LANGUAGE.md README.md` finds nothing: the row shape is unspecified and untested. Facet 3 (lanes): js/src/sql/translator.mjs:2317 (`sourceAlias + '.*'`, present at 8fe0e3a line 2284) renders a joined pipeline's row as the left alias's columns only, so the continuation over a LINK prefix cannot see right-table or promoted columns; the planner's "back up to the step before the bucket" (docs/SQL-TRANSLATION.md §12.1) lands on that LINK and the continuation fails with E_NO_KEY in JS, PHP and Python (Python executed against a real SQLite). The same projection makes the pure_sql derived-table statement for `LINK .> TAKE(2) .> MAP(RECORD("n", _["name"]))` invalid SQL on SQLite. §12.1 promises "the database answers the prefix, the evaluator runs the rest over its rows" and CHANGELOG promises the bucket split lands where the continuation can still answer; for LINK prefixes neither holds, and the new fixture 25-hybrid-plans.sqlt:224-239 pins the o.* shape as expected. The commit did not cause this, but its planner contract and fixtures rest on it, and the observable results differ between hosts and between lanes, which is the product's core promise — severity high.

## Verifier evidence

```
In-memory, all five REPLs, P='X = LIST(RECORD("id", 1, "cid", 7)); Y = LIST(RECORD("id", 7, "name", "ann")); X .> LINK(Y, _1["cid"] == _2["id"]) .> MAP(INDEXES(_))':
  node js/bin/sel.mjs / lisp/bin/sel: -{"1"=-{"1"=t"X","2"=t"x","3"=t"X","4"=t"x","5"=t"_1","6"=t"Y","7"=t"y","8"=t"_2","9"=t"cid","10"=t"name"}}
  php php/bin/sel / cpp/build/sel / python -m sel: -{"1"=-{"1"=t"X","2"=t"x","3"=t"_1","4"=t"Y","5"=t"y","6"=t"_2","7"=t"cid","8"=t"name"}}
'...; J = X .> LINK(...); LIST(COUNT(J[1]), COUNT(J[1]["X"]), COUNT(J[1]["_1"]), J[1]["X"]["cid"])': js/lisp -{"1"=t"10","2"=t"2","3"=t"4","4"=t"7"}; php/cpp/py -{"1"=t"8","2"=t"4","3"=t"4","4"=t"7"}
'...; J[1]["X"]["x"]["id"]': js/lisp "E_NO_KEY at line 1 column 130: no key \"x\""; php/cpp/py "1"
'...; J[1]["X"]' dump: js -{"id"=t"1","cid"=t"7"}; py/cpp -{"id"=t"1","cid"=t"7","X"=-{...},"x"=-{...}}
Literal left side 'LIST(RECORD("id",1,"cid",7)) .> LINK(LIST(RECORD("id",7,"name","ann")), ...) .> MAP(INDEXES(_))': all five -{"1"=-{"1"=t"_1","2"=t"_2","3"=t"cid","4"=t"name"}}
Code: js/src/builtins/structure.mjs:246 (put pushes blindly), :203-226 ensureRowTableAlias; lisp/src/builtins/structure.lisp:285 ensure-row-table-alias, :321-353 make-joined-row (push without has-check); python/sel/builtins/structure.py:243-251 (`present` set); php/src/Builtins/Structure.php:253-268 (`$present`); cpp/sel.cpp:3005-3012 (`if (!out.has(key))`).
Provenance: `git diff --stat 8fe0e3a ed16df2 -- js/src/builtins/structure.mjs lisp/src/builtins/structure.lisp python/sel/builtins/structure.py php/src/Builtins/Structure.php cpp/sel.cpp` -> only cpp/sel.cpp (hunks at 3909,4090,5098,5186,5230,5277,5326,5863 — not make_joined_row); `git show 8fe0e3a:js/src/builtins/structure.mjs | grep -n "const put"` -> 246 same blind push; `git show 8fe0e3a:python/sel/builtins/structure.py | grep -n "if key not in present"` -> 249. `grep -rn LINK conformance/ spec/ docs/LANGUAGE.md README.md` -> nothing; `git show 8fe0e3a:sql/cases/25-hybrid-plans.sqlt` -> did not exist (new in this commit); it is the only sql/cases file mentioning LINK.
Lanes, Python with real sqlite3 (scratchpad/verify-Y/probe.py, PYTHONPATH=$PWD/python python3 probe.py):
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> TAKE(2) .> MAP(RECORD("n", _["name"]))': run() -{"1"=-{"n"=t"Ann"},"2"=-{"n"=t"Bob"}}; plan pure_sql; SQL `SELECT "_sub1"."name" AS "n" FROM (SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON (...) LIMIT 2) "_sub1"`; execute_hybrid -> OperationalError: no such column: _sub1.name
  'ORDERS .> LINK(...) .> BUCKET(_["country"]) .> MAP(RECORD("co", _K, "tag", JOIN(LIST(_K), "-")))': run() -{"1"=-{"co"=t"PL","tag"=t"PL"},"2"=-{"co"=t"DE","tag"=t"DE"}}; plan hybrid; prefix `SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON (...)`; execute_hybrid -> SelError E_NO_KEY@1:69 no key "country"
  control 'ORDERS .> FILTER(_["total"] > 1) .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "tag", JOIN(LIST(_K), "-")))': hybrid, execute_hybrid == run()
  JS (probe2.mjs, runner returning the o.* rows a DB would): run() same as above; plan hybrid; same SQL; executeHybrid E_NO_KEY@1:69
  PHP (probe.php): run() same; plan hybrid; same SQL; executeHybrid "E_NO_KEY no key \"country\""
Rendering site: js/src/sql/translator.mjs:2316-2317 `parts.push(this.emit.ident(plan.sourceAlias) + '.*')`; present at `git show 8fe0e3a:js/src/sql/translator.mjs` line 2284.
Fixture: sql/cases/25-hybrid-plans.sqlt:224-239 plan.tables.first-use-order expects pure_sql `SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (...) LIMIT 2` for `ORDERS .> LINK(CUSTOMERS, ...) .> TAKE(2)`, whose rows are 3 left columns while run() answers 12-key joined rows. docs/SQL-TRANSLATION.md:2227-2305 §12.1 quoted for the promises.
```

## Original review reports (deduplicated into this finding)

### [hybrid-planner] In-memory LINK builds rows with duplicate keys in JS and Lisp (9 keys vs 7), and no conformance case exercises LINK; hybrid continuations run it

*test-gap · medium · hosts: js, lisp*

Locations: `js/src/builtins/aggregate.mjs:1`; `lisp/src/builtins/aggregate.lisp:1`; `conformance/16-bucket.selt:1`

Pre-existing and outside the diff's own lines, but the planner's continuation lane (a prefix followed by LINK in memory) and every pure_memory plan over a LINK depend on it: the joined record carries each relation name upper/lower plus _1/_2, and JS and Lisp insert the name-keyed entries twice, producing a record with repeated keys (COUNT of the row differs). `grep -c 'name:.*LINK' conformance/*.selt` is 0, so nothing pins LINK's row shape across hosts.

Reported repro:

```
`A = LIST(RECORD("id", 1)); B = LIST(RECORD("id", 1, "x", 2)); J = A .> LINK(B, _1["id"] == _2["id"]); COUNT(J[1])` -> node/lisp: 9; php/python/cpp: 7. The dump from node shows keys A, a, A, a, _1, B, b, _2, x with A/a repeated.
```

### [probe-lanes-fold] LINK over named relations yields records with duplicate keys in JS and Lisp but not in PHP, Python or C++ (in-memory lane)

*correctness · high · hosts: js, lisp, php, python, cpp*

Locations: `js/src/builtins/structure.mjs:244-256 (makeJoinedRow: copies nested-record keys of the alias-injected left row, then put(b1)/put(low1) again with no dedupe)`; `js/src/builtins/structure.mjs:204-215,419 (ensureRowTableAlias injects b1/lower(b1) into the row; tableLeft then contains those aliases)`; `lisp/src/builtins/structure.lisp:285,321,483-484`; `python/sel/builtins/structure.py:243-256 (put() dedupes via `present`, first wins)`; `php/src/Builtins/Structure.php:199,458`

Outside the diff (structure.* is untouched) and pre-existing, but found by the LINK probes this lens was asked to run and it is a cross-host in-memory result disagreement — the product's core promise. When the left side is a named variable (context binding or helper), JS and Lisp emit a record whose key list is ORDERS, orders, ORDERS, orders, _1, ... (duplicate keys, which the Value model should not be able to represent), while PHP/Python/C++ emit ORDERS, orders, _1, ... with the first entries holding the alias-injected copy. A literal-list left side (b1 = '_1') agrees in all five. Any rule that reads a LINK row by key, dumps it, or DEDUPEs it can answer differently per host.

Reported repro:

```
REPL (-e) on all five, no context needed:
X = LIST(RECORD("id", 1, "cid", 7)); Y = LIST(RECORD("id", 7, "name", "ann")); X .> LINK(Y, _1["cid"] == _2["id"])
JS and Lisp: -{"1"=-{"X"=-{"id"=t"1", "cid"=t"7"}, "x"=-{"id"=t"1", "cid"=t"7"}, "X"=-{"id"=t"1", "cid"=t"7", "X"=-{...}, "x"=-{...}}, "x"=-{...}, "_1"=-{...}, "Y"=..., "y"=..., "_2"=..., "cid"=t"7", "name"=t"ann"}}
PHP, Python, C++: -{"1"=-{"X"=-{"id"=t"1", "cid"=t"7", "X"=-{...}, "x"=-{...}}, "x"=-{...}, "_1"=-{...}, "Y"=..., "y"=..., "_2"=..., "cid"=t"7", "name"=t"ann"}}
The same split appears for ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> TAKE(1) with ORDERS/CUSTOMERS from the context (out3-*.txt line 90, out5-*.txt lines 2/10/18/122), in both run() and the pure_memory execute_hybrid lane. LIST(RECORD("id", 1, "cid", 7)) .> LINK(LIST(RECORD("id", 7, "name", "ann")), _1["cid"] == _2["id"]) agrees in all five.
```

### [probe-lanes-bucket-hybrid] In-memory LINK rows differ between hosts (JS/Lisp emit duplicate alias keys), and no lane agrees with the SQL projection of a LINK prefix

*coherence · high · hosts: js, lisp, python, php, cpp*

Locations: `js/src/builtins/structure.mjs:400`; `lisp/src/builtins/aggregate.lisp:1`; `python/sel/builtins/aggregate.py:1`; `sql/cases/25-hybrid-plans.sqlt:224`; `sql/cases/25-hybrid-plans.sqlt:555`

LINK's output row is not byte-identical across hosts: JS and Lisp produce a row with 11 keys including "ORDERS" and "orders" twice (a flat copy and a nested one), Python/PHP/C++ produce 9 keys. There is no LINK or LINK_LEFT case anywhere in conformance/, so the suite cannot see it. Independently, the SQL side renders a LINK prefix as SELECT o.* (left table only) and the in-memory row is the nested/promoted shape above, so the diff's promise that a split lands "before the bucket" with the database answering the prefix fails for every LINK-then-BUCKET shape: the continuation cannot find the joined column. The fixture plan.tables.first-use-order pins a pure_sql SELECT o.* ... INNER JOIN ... LIMIT 2 plan whose rows are not what run() answers in any host. The translator also emits SQL that no database accepts when a step after LINK .> TAKE references a right-table column, because the derived table's field catalogue lists the join's columns but the subquery projects o.* only. All pre-existing at 8fe0e3a; reported because the diff's planner contract and fixtures rest on LINK prefixes.

Reported repro:

```
In-memory: ORDERS = LIST(RECORD("customer_id", 7)); CUSTOMERS = LIST(RECORD("id", 7, "name", "Ann")); ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(INDEXES(_))  => node js/bin/sel.mjs and lisp/bin/sel: 11 keys (ORDERS, orders, ORDERS, orders, _1, CUSTOMERS, customers, _2, customer_id, id, name); cpp/build/sel (and php, python): 9 keys (ORDERS, orders, _1, CUSTOMERS, customers, _2, customer_id, id, name).
Hybrid lane, all five hosts, b57: ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> BUCKET(_["country"]) .> MAP(RECORD("co", _K, "tag", JOIN(LIST(_K), "-")))  run() => -{"1"=-{"co"=t"PL","tag"=t"PL"}, "2"=-{"co"=t"DE","tag"=t"DE"}}; plan=hybrid, prefix SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON (...); execute_hybrid => E_NO_KEY@1:69 in py, js, php, cpp, lisp.
Translator, c09/inline probe: ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> TAKE(2) .> MAP(RECORD("n", _["name"]))  translate_statement(postgresql) => SELECT "_sub1"."name" AS "n" FROM (SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON (...) LIMIT 2) "_sub1"; on sqlite: no such column: _sub1.name.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$SCRATCH/verify-Y/probe.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';

const bindings = {
  ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM'), CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'), TOTAL: Binding.column('total','o','NUM') }),
  CUSTOMERS: Binding.relation('customers', 'c', { ID: Binding.column('id', 'c', 'NUM'), NAME: Binding.column('name', 'c', 'TEXT'), COUNTRY: Binding.column('country','c','TEXT') }),
};
const orders = [{ id: '1', customer_id: '7', total: '10' }, { id: '2', customer_id: '8', total: '20' }];
const customers = [{ id: '7', name: 'Ann', country: 'PL' }, { id: '8', name: 'Bob', country: 'DE' }];
const ctx = { ORDERS: orders, CUSTOMERS: customers };
function failure(fn) { try { return fn(); } catch (e) { return `${e.code}@${e.line}:${e.col}`; } }

for (const src of [
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(INDEXES(_))',
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> BUCKET(_["country"]) .> MAP(RECORD("co", _K, "n", COUNT(_)))',
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> TAKE(2) .> MAP(RECORD("n", _["name"]))',
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> TAKE(2) .> MAP(RECORD("n", _["name"], "keys", INDEXES(_)))',
]) {
  const program = compile(src);
  console.log('###', src);
  console.log('run():', failure(() => program.run(ctx).dump()));
  const plan = Sql.planHybrid(program, 'postgresql', bindings);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  console.log('plan:', kind);
  if (!plan.pureMemory) {
    const frag = plan.sqlFragment ?? plan.fragment ?? plan.sql;
    console.log('sql:', typeof frag === 'string' ? frag : JSON.stringify(frag && (frag.sql ?? frag.text ?? frag), null, 0));
  }
  // runner: emulate SELECT o.* by returning left rows (what SQL would give for "o".*)
  // We hand the runner the joined row projection to be generous: left columns only.
  const runner = (sql) => { console.log('runner got:', sql.sql ?? sql); return orders.map(r => ({ ...r })); };
  console.log('executeHybrid:', failure(() => { const v = Sql.executeHybrid(plan, runner, ctx); return v.dump ? v.dump() : JSON.stringify(v); }));
  try { console.log('translate_statement:', Sql.translateStatement ? Sql.translateStatement(program, 'postgresql', bindings).sql : '(no translateStatement)'); } catch (e) { console.log('translate_statement:', `${e.code}@${e.line}:${e.col} ${e.message}`); }
}
```

**`$SCRATCH/verify-Y/probe.py`**

```python
import sqlite3
from sel import compile as sel_compile
from sel.sql import Binding, Sql
from sel.value import Value

bindings = {
  'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id','o','NUM'), 'CUSTOMER_ID': Binding.column('customer_id','o','NUM'), 'TOTAL': Binding.column('total','o','NUM')}),
  'CUSTOMERS': Binding.relation('customers', 'c', {'ID': Binding.column('id','c','NUM'), 'NAME': Binding.column('name','c','TEXT'), 'COUNTRY': Binding.column('country','c','TEXT')}),
}
orders = [{'id':'1','customer_id':'7','total':'10'},{'id':'2','customer_id':'8','total':'20'}]
customers = [{'id':'7','name':'Ann','country':'PL'},{'id':'8','name':'Bob','country':'DE'}]
ctx = {'ORDERS': orders, 'CUSTOMERS': customers}
db = sqlite3.connect(':memory:')
db.execute('create table orders(id, customer_id, total)'); db.executemany('insert into orders values (?,?,?)', [(r['id'],r['customer_id'],r['total']) for r in orders])
db.execute('create table customers(id, name, country)'); db.executemany('insert into customers values (?,?,?)', [(r['id'],r['name'],r['country']) for r in customers])
def runner(sql, params):
    print('  runner SQL:', sql)
    cur = db.execute(sql, [p.as_text() for p in params]); cols=[c[0] for c in cur.description]
    return [dict(zip(cols,[str(v) for v in row])) for row in cur.fetchall()]
def failure(fn):
    try: return fn()
    except Exception as e: return f'{type(e).__name__}: {getattr(e,"code",None)}@{getattr(e,"line",None)}:{getattr(e,"col",None)} {e}'
for src in [
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> TAKE(2) .> MAP(RECORD("n", _["name"]))',
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> BUCKET(_["country"]) .> MAP(RECORD("co", _K, "tag", JOIN(LIST(_K), "-")))',
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("n", _["name"], "tag", JOIN(LIST(_["name"]), "-")))',
  'ORDERS .> FILTER(_["total"] > 1) .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "tag", JOIN(LIST(_K), "-")))',
]:
    print('###', src)
    program = sel_compile(src)
    print('  run():', failure(lambda: program.run(ctx).dump()))
    plan = Sql.plan_hybrid(program, 'sqlite', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    print('  plan:', kind)
    def ex():
        got = Sql.execute_hybrid(plan, runner, ctx)
        got = got if isinstance(got, Value) else Value.from_native(got)
        return got.dump()
    print('  execute_hybrid:', failure(ex))
```

**`$SCRATCH/verify-Y/probe2.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const bindings = {
  ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM'), CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'), TOTAL: Binding.column('total','o','NUM') }),
  CUSTOMERS: Binding.relation('customers', 'c', { ID: Binding.column('id', 'c', 'NUM'), NAME: Binding.column('name', 'c', 'TEXT'), COUNTRY: Binding.column('country','c','TEXT') }),
};
const orders = [{ id: '1', customer_id: '7', total: '10' }, { id: '2', customer_id: '8', total: '20' }];
const customers = [{ id: '7', name: 'Ann', country: 'PL' }, { id: '8', name: 'Bob', country: 'DE' }];
const ctx = { ORDERS: orders, CUSTOMERS: customers };
function failure(fn) { try { return fn(); } catch (e) { return `${e.code}@${e.line}:${e.col}`; } }
const src = 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> BUCKET(_["country"]) .> MAP(RECORD("co", _K, "tag", JOIN(LIST(_K), "-")))';
const program = compile(src);
console.log('run():', failure(() => program.run(ctx).dump()));
const plan = Sql.planHybrid(program, 'postgresql', bindings);
console.log('plan:', plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid');
// Emulate what "SELECT o.* ... INNER JOIN customers" returns: the orders columns of matching rows.
const runner = (sql) => { console.log('runner SQL:', sql); return orders.filter(o => customers.some(c => c.id === o.customer_id)).map(r => ({ ...r })); };
console.log('executeHybrid:', failure(() => Sql.executeHybrid(plan, runner, ctx).dump()));
```

**`$SCRATCH/verify-Y/probe.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$bindings = [
  'ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id','o','NUM'), 'CUSTOMER_ID' => Binding::column('customer_id','o','NUM'), 'TOTAL' => Binding::column('total','o','NUM')]),
  'CUSTOMERS' => Binding::relation('customers', 'c', ['ID' => Binding::column('id','c','NUM'), 'NAME' => Binding::column('name','c','TEXT'), 'COUNTRY' => Binding::column('country','c','TEXT')]),
];
$orders = [['id'=>'1','customer_id'=>'7','total'=>'10'],['id'=>'2','customer_id'=>'8','total'=>'20']];
$customers = [['id'=>'7','name'=>'Ann','country'=>'PL'],['id'=>'8','name'=>'Bob','country'=>'DE']];
$ctx = ['ORDERS'=>$orders, 'CUSTOMERS'=>$customers];
$src = 'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> BUCKET(_["country"]) .> MAP(RECORD("co", _K, "tag", JOIN(LIST(_K), "-")))';
$program = Sel::compile($src);
echo "run(): ", $program->run($ctx)->dump(), "\n";
$plan = Sql::planHybrid($program, 'postgresql', $bindings);
echo "plan: ", ($plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid')), "\n";
$runner = function ($sql) use ($orders) { echo "runner SQL: ", is_string($sql) ? $sql : $sql->sql, "\n"; return $orders; };
try { echo "executeHybrid: ", Sql::executeHybrid($plan, $runner, $ctx)->dump(), "\n"; } catch (Throwable $e) { echo "executeHybrid: ", $e->getCode() ?: get_class($e), " ", $e->getMessage(), "\n"; }
```

### Commands run and their output

Run LINK INDEXES probe on all five hosts

```bash
P='X = LIST(RECORD("id", 1, "cid", 7)); Y = LIST(RECORD("id", 7, "name", "ann")); X .> LINK(Y, _1["cid"] == _2["id"]) .> MAP(INDEXES(_))'; for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel"; do echo "== $h"; $h -e "$P"; done; echo "== py"; PYTHONPATH=$PWD/python python3 -m sel -e "$P"
```

```
== node js/bin/sel.mjs
-{"1"=-{"1"=t"X", "2"=t"x", "3"=t"X", "4"=t"x", "5"=t"_1", "6"=t"Y", "7"=t"y", "8"=t"_2", "9"=t"cid", "10"=t"name"}}
== php php/bin/sel
-{"1"=-{"1"=t"X", "2"=t"x", "3"=t"_1", "4"=t"Y", "5"=t"y", "6"=t"_2", "7"=t"cid", "8"=t"name"}}
== cpp/build/sel
-{"1"=-{"1"=t"X", "2"=t"x", "3"=t"_1", "4"=t"Y", "5"=t"y", "6"=t"_2", "7"=t"cid", "8"=t"name"}}
== lisp/bin/sel
-{"1"=-{"1"=t"X", "2"=t"x", "3"=t"X", "4"=t"x", "5"=t"_1", "6"=t"Y", "7"=t"y", "8"=t"_2", "9"=t"cid", "10"=t"name"}}
== py
-{"1"=-{"1"=t"X", "2"=t"x", "3"=t"_1", "4"=t"Y", "5"=t"y", "6"=t"_2", "7"=t"cid", "8"=t"name"}}
```

Probe JS hybrid lane with LINK prefixes

```bash
mkdir -p $SCRATCH/verify-Y && cd /home/nathan/workspaces/nth-share/sel && # (file written, see "Reproduction scripts" above)
node $SCRATCH/verify-Y/probe.mjs 2>&1 | head -60
```

```
### ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(INDEXES(_))
run(): -{"1"=-{"1"=t"ORDERS", "2"=t"orders", "3"=t"ORDERS", "4"=t"orders", "5"=t"_1", "6"=t"CUSTOMERS", "7"=t"customers", "8"=t"_2", "9"=t"customer_id", "10"=t"total", "11"=t"name", "12"=t"country"}, "2"=-{"1"=t"ORDERS", "2"=t"orders", "3"=t"ORDERS", "4"=t"orders", "5"=t"_1", "6"=t"CUSTOMERS", "7"=t"customers", "8"=t"_2", "9"=t"customer_id", "10"=t"total", "11"=t"name", "12"=t"country"}}
plan: hybrid
sql: undefined
runner got: SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON ("o"."customer_id" = "c"."id")
executeHybrid: -{"1"=-{"1"=t"id", "2"=t"customer_id", "3"=t"total"}, "2"=-{"1"=t"id", "2"=t"customer_id", "3"=t"total"}}
translate_statement: E_SQL_SHAPE@1:65 INDEXES yields a list of keys, and a SQL expression is a scalar
### ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> BUCKET(_["country"]) .> MAP(RECORD("co", _K, "n", COUNT(_)))
run(): -{"1"=-{"co"=t"PL", "n"=t"1"}, "2"=-{"co"=t"DE", "n"=t"1"}}
plan: pure_sql
sql: undefined
runner got: SELECT "c"."country" AS "co", COUNT(*) AS "n" FROM "orders" "o" INNER JOIN "customers" "c" ON ("o"."customer_id" = "c"."id") GROUP BY "c"."country"
executeHybrid: [{"id":"1","customer_id":"7","total":"10"},{"id":"2","customer_id":"8","total":"20"}]
translate_statement: undefined
### ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> TAKE(2) .> MAP(RECORD("n", _["name"]))
run(): -{"1"=-{"n"=t"Ann"}, "2"=-{"n"=t"Bob"}}
plan: pure_sql
sql: undefined
runner got: SELECT "_sub1"."name" AS "n" FROM (SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON ("o"."customer_id" = "c"."id") LIMIT 2) "_sub1"
executeHybrid: [{"id":"1","customer_id":"7","total":"10"},{"id":"2","customer_id":"8","total":"20"}]
translate_statement: undefined
### ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> TAKE(2) .> MAP(RECORD("n", _["name"], "keys", INDEXES(_)))
run(): -{"1"=-{"n"=t"Ann", "keys"=-{"1"=t"ORDERS", "2"=t"orders", "3"=t"ORDERS", "4"=t"orders", "5"=t"_1", "6"=t"CUSTOMERS", "7"=t"customers", "8"=t"_2", "9"=t"customer_id", "10"=t"total", "11"=t"name", "12"=t"country"}}, "2"=-{"n"=t"Bob", "keys"=-{"1"=t"ORDERS", "2"=t"orders", "3"=t"ORDERS", "4"=t"orders", "5"=t"_1", "6"=t"CUSTOMERS", "7"=t"customers", "8"=t"_2", "9"=t"customer_id", "10"=t"total", "11"=t"name", "12"=t"country"}}}
plan: hybrid
sql: undefined
runner got: SELECT "_sub1"."name" AS "n" FROM (SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON ("o"."customer_id" = "c"."id") LIMIT 2) "_sub1"
executeHybrid: E_NO_KEY@1:89
translate_statement: E_SQL_SHAPE@1:107 INDEXES yields a list of keys, and a SQL expression is a scalar
```

Check sqlite availability and read the cited fixtures

```bash
which sqlite3; python3 -c "import sqlite3; print(sqlite3.sqlite_version)"; cd /home/nathan/workspaces/nth-share/sel && sed -n 200,240p sql/cases/25-hybrid-plans.sqlt; echo ......; sed -n 540,575p sql/cases/25-hybrid-plans.sqlt
```

```
/usr/bin/which: no sqlite3 in (/home/nathan/.opencode/bin:/home/nathan/.local/bin:/home/nathan/.kimi-code/bin:/home/nathan/.local/bin:/home/nathan/.opencode/bin:/home/nathan/.local/bin:/home/nathan/.kimi-code/bin:/home/nathan/.cargo/bin:/home/nathan/.nvm/versions/node/v22.12.0/bin:/home/nathan/.local/bin:/home/nathan/bin:/usr/lib64/ccache:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/usr/lib/wsl/lib:/mnt/c/Windows/system32:/mnt/c/Windows:/mnt/c/Windows/System32/Wbem:/mnt/c/Windows/System32/WindowsPowerShell/v1.0/:/mnt/c/Windows/System32/OpenSSH/:/mnt/c/Program Files/dotnet/:/mnt/c/Program Files (x86)/NVIDIA Corporation/PhysX/Common:/mnt/c/Program Files/Antelope Audio/Antelope Launcher/:/mnt/c/Program Files/Git/cmd:/mnt/c/Program Files/CMake/bin:/mnt/c/Program Files (x86)/Windows Kits/10/Windows Performance Toolkit/:/mnt/c/Program Files/WSL/:/mnt/c/Users/marci/AppData/Local/agy/bin:/mnt/c/Users/marci/.local/bin:/mnt/c/Users/marci/AppData/Local/Microsoft/WindowsApps:/mnt/c/Users/marci/AppData/Local/Programs/Microsoft VS Code/bin:/mnt/c/Users/marci/AppData/Local/nvm:/mnt/c/nvm4w/nodejs:/mnt/c/Users/marci/AppData/Local/Microsoft/WinGet/Links:/home/nathan/.composer/vendor/bin)
3.51.2
--- tables
sales_orders
--- expect
SELECT `s`.* FROM `sales_orders` `s` LIMIT 1
===

### name: plan.tables.binding-name-is-case-insensitive
--- note
Identifiers are case-insensitive (spec §2.3), and a binding is looked up the
same way. The physical name is reported exactly as the binding spelled it.
--- dialect
mariadb
--- bindings
{"ORDERS": {"kind": "relation", "from": "Orders", "alias": "o", "fields": {"ID": {"table": "o", "column": "id", "type": "NUM"}}}}
--- source
orders .> take(1)
--- plan
pure_sql
--- tables
Orders
--- expect
SELECT `o`.* FROM `Orders` `o` LIMIT 1
===

### name: plan.tables.first-use-order
--- dialect
mariadb
--- bindings
{"ORDERS": {"kind": "relation", "from": "orders", "alias": "o", "fields": {"ID": {"table": "o", "column": "id", "type": "NUM"}, "CUSTOMER_ID": {"table": "o", "column": "customer_id", "type": "NUM"}}},
 "CUSTOMERS": {"kind": "relation", "from": "customers", "alias": "c", "fields": {"ID": {"table": "c", "column": "id", "type": "NUM"}}}}
--- source
ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> TAKE(2)
--- plan
pure_sql
--- tables
orders
customers
--- expect
SELECT `o`.* FROM `orders` `o` INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`) LIMIT 2
===

......
SQL rows are keys, not groups: no continuation could recover the members, so
it is not a split point and the MAP fall-through does not fire either. Pure
memory is the only right answer.
--- dialect
mariadb
--- bindings
{"ORDERS": {"kind": "relation", "from": "orders", "alias": "o", "fields": {"CUSTOMER_ID": {"table": "o", "column": "customer_id", "type": "NUM"}}}}
--- source
ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "note", ABORT("x")))
--- plan
pure_memory
--- tables
orders
===

### name: plan.bucket.split-before-the-bucket
--- note
The split lands before the bucket instead: the FILTER goes to the database,
the grouping and its untranslatable projection stay in memory.
--- dialect
mariadb
--- bindings
{"ORDERS": {"kind": "relation", "from": "orders", "alias": "o", "fields": {"CUSTOMER_ID": {"table": "o", "column": "customer_id", "type": "NUM"}, "AMOUNT": {"table": "o", "column": "amount", "type": "NUM"}}}}
--- source
ORDERS .> FILTER(_["amount"] > 1) .> BUCKET(_["customer_id"]) .> MAP
… (truncated)
```

Run Python probe executing LINK plans against SQLite

```bash
# (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $SCRATCH/verify-Y/probe.py
```

```
### ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> TAKE(2) .> MAP(RECORD("n", _["name"]))
  run(): -{"1"=-{"n"=t"Ann"}, "2"=-{"n"=t"Bob"}}
  plan: pure_sql
  runner SQL: SELECT "_sub1"."name" AS "n" FROM (SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON (CAST("o"."customer_id" AS NUMERIC) = CAST("c"."id" AS NUMERIC)) LIMIT 2) "_sub1"
  execute_hybrid: OperationalError: None@None:None no such column: _sub1.name
### ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> BUCKET(_["country"]) .> MAP(RECORD("co", _K, "tag", JOIN(LIST(_K), "-")))
  run(): -{"1"=-{"co"=t"PL", "tag"=t"PL"}, "2"=-{"co"=t"DE", "tag"=t"DE"}}
  plan: hybrid
  runner SQL: SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON (CAST("o"."customer_id" AS NUMERIC) = CAST("c"."id" AS NUMERIC))
  execute_hybrid: SelError: E_NO_KEY@1:69 E_NO_KEY at 1:69: no key "country"
### ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("n", _["name"], "tag", JOIN(LIST(_["name"]), "-")))
  run(): -{"1"=-{"n"=t"Ann", "tag"=t"Ann"}, "2"=-{"n"=t"Bob", "tag"=t"Bob"}}
  plan: hybrid
  runner SQL: SELECT "c"."name" AS "n", "c"."name" AS "name" FROM "orders" "o" INNER JOIN "customers" "c" ON (CAST("o"."customer_id" AS NUMERIC) = CAST("c"."id" AS NUMERIC))
  execute_hybrid: -{"1"=-{"n"=t"Ann", "tag"=t"Ann"}, "2"=-{"n"=t"Bob", "tag"=t"Bob"}}
### ORDERS .> FILTER(_["total"] > 1) .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "tag", JOIN(LIST(_K), "-")))
  run(): -{"1"=-{"cid"=t"7", "tag"=t"7"}, "2"=-{"cid"=t"8", "tag"=t"8"}}
  plan: hybrid
  runner SQL: SELECT "o".* FROM "orders" "o" WHERE (CAST("o"."total" AS NUMERIC) > CAST('1' AS NUMERIC))
  execute_hybrid: -{"1"=-{"cid"=t"7", "tag"=t"7"}, "2"=-{"cid"=t"8", "tag"=t"8"}}
```

Run JS hybrid LINK-then-BUCKET probe

```bash
# (file written, see "Reproduction scripts" above)
node $SCRATCH/verify-Y/probe2.mjs
```

```
run(): -{"1"=-{"co"=t"PL", "tag"=t"PL"}, "2"=-{"co"=t"DE", "tag"=t"DE"}}
plan: hybrid
runner SQL: SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON ("o"."customer_id" = "c"."id")
executeHybrid: E_NO_KEY@1:69
```

Compare COUNT of a LINK row across hosts

```bash
P='X = LIST(RECORD("id", 1, "cid", 7)); Y = LIST(RECORD("id", 7, "name", "ann")); J = X .> LINK(Y, _1["cid"] == _2["id"]); LIST(COUNT(J[1]), COUNT(J[1]["X"]), COUNT(J[1]["_1"]), J[1]["X"]["cid"])'; for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel"; do echo "== $h"; $h -e "$P"; done; echo "== py"; PYTHONPATH=$PWD/python python3 -m sel -e "$P"
```

```
== node js/bin/sel.mjs
-{"1"=t"10", "2"=t"2", "3"=t"4", "4"=t"7"}
== php php/bin/sel
-{"1"=t"8", "2"=t"4", "3"=t"4", "4"=t"7"}
== cpp/build/sel
-{"1"=t"8", "2"=t"4", "3"=t"4", "4"=t"7"}
== lisp/bin/sel
-{"1"=t"10", "2"=t"2", "3"=t"4", "4"=t"7"}
== py
-{"1"=t"8", "2"=t"4", "3"=t"4", "4"=t"7"}
```

Dump the X sub-record on JS, Python, C++

```bash
P='X = LIST(RECORD("id", 1, "cid", 7)); Y = LIST(RECORD("id", 7, "name", "ann")); J = X .> LINK(Y, _1["cid"] == _2["id"]); J[1]["X"]'; echo "== js"; node js/bin/sel.mjs -e "$P"; echo "== py"; PYTHONPATH=$PWD/python python3 -m sel -e "$P"; echo "== cpp"; cpp/build/sel -e "$P"
```

```
== js
-{"id"=t"1", "cid"=t"7"}
== py
-{"id"=t"1", "cid"=t"7", "X"=-{"id"=t"1", "cid"=t"7"}, "x"=-{"id"=t"1", "cid"=t"7"}}
== cpp
-{"id"=t"1", "cid"=t"7", "X"=-{"id"=t"1", "cid"=t"7"}, "x"=-{"id"=t"1", "cid"=t"7"}}
```

Probe nested alias access across hosts

```bash
P='X = LIST(RECORD("id", 1, "cid", 7)); Y = LIST(RECORD("id", 7, "name", "ann")); J = X .> LINK(Y, _1["cid"] == _2["id"]); J[1]["X"]["x"]["id"]'; for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel"; do echo "== $h"; $h -e "$P" 2>&1 | head -2; done; echo "== py"; PYTHONPATH=$PWD/python python3 -m sel -e "$P" 2>&1 | head -2
```

```
== node js/bin/sel.mjs
E_NO_KEY at line 1 column 130: no key "x"
== php php/bin/sel
1
== cpp/build/sel
1
== lisp/bin/sel
E_NO_KEY at line 1 column 130: no key "x"
== py
1
```

Check literal-left LINK agreement and old Python dedupe

```bash
P='LIST(RECORD("id", 1, "cid", 7)) .> LINK(LIST(RECORD("id", 7, "name", "ann")), _1["cid"] == _2["id"]) .> MAP(INDEXES(_))'; for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel"; do echo "== $h"; $h -e "$P" 2>&1 | head -2; done; echo "== py"; PYTHONPATH=$PWD/python python3 -m sel -e "$P" 2>&1 | head -2; git show 8fe0e3a:python/sel/builtins/structure.py | grep -n "if key not in present"
```

```
== node js/bin/sel.mjs
-{"1"=-{"1"=t"_1", "2"=t"_2", "3"=t"cid", "4"=t"name"}}
== php php/bin/sel
-{"1"=-{"1"=t"_1", "2"=t"_2", "3"=t"cid", "4"=t"name"}}
== cpp/build/sel
-{"1"=-{"1"=t"_1", "2"=t"_2", "3"=t"cid", "4"=t"name"}}
== lisp/bin/sel
-{"1"=-{"1"=t"_1", "2"=t"_2", "3"=t"cid", "4"=t"name"}}
== py
-{"1"=-{"1"=t"_1", "2"=t"_2", "3"=t"cid", "4"=t"name"}}
249:        if key not in present:
```

Run PHP hybrid LINK-then-BUCKET probe

```bash
grep -n "planHybrid\|executeHybrid\|Binding::relation" tools/check-php-optimizer.php | head -5; # (file written, see "Reproduction scripts" above)
php $SCRATCH/verify-Y/probe.php
```

```
166:    'CUSTOMERS' => Binding::relation('customers', 'customers', [
170:$hybrid = Sql::planHybrid(
200:$orders = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
202:$helper = Sql::planHybrid(Sel::compile('X = ORDERS; X .> TAKE(1)'), 'postgresql', $orders);
207:$fused = Sql::planHybrid(Sel::compile($twoFilters), 'postgresql', $orders);
run(): -{"1"=-{"co"=t"PL", "tag"=t"PL"}, "2"=-{"co"=t"DE", "tag"=t"DE"}}
plan: hybrid
executeHybrid: runner SQL: SELECT "o".* FROM "orders" "o" INNER JOIN "customers" "c" ON ("o"."customer_id" = "c"."id")
executeHybrid: E_NO_KEY no key "country"
```

