# G. BUCKET(k) .> MAP(proj) is not BUCKET(k, proj) in the evaluator for NULL/BOOL/LIST/RECORD keys; the bare BUCKET stringifies keys differently per host

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "Bucket keys, and a FILTER after pagination".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** mixed · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

Every facet reproduces. In the evaluator `BUCKET(k) .> MAP(proj)` is not `BUCKET(k, proj)`: the bare BUCKET returns a tree keyed by a string rendering of the key, so the MAP spelling sees `_K` as TEXT ("" for NULL, "TRUE"/"FALSE" for booleans) and collapses every LIST/RECORD key onto one string entry (rows lost), while the 3-arg spelling binds the real key Value and keeps every group; the collapsed key string is "null" in JS, "" in PHP/C++, "None" in Python and NIL in Lisp (TYPE-ERROR crash). The commit's translator fold renders the MAP spelling with 3-arg semantics, so a pure_sql plan returns different rows from run() for NULL/BOOL/RECORD keys (reproduced on SQLite: 3 groups vs 2, NULL vs ""), and the Lisp `value-eql-at` parallel-`let` bug crashes the 3-arg spelling with list keys (and `LIST(LIST(1,2), LIST(1,2)) .> DEDUPE()`).

## Suggested fix — case first

The fold is only sound if the evaluator makes the two spellings agree, so fix the language core first, spec-first: (a) add to conformance/16-bucket.selt the bare-bucket case `LIST(RECORD("d", LIST(1,2)), RECORD("d", LIST(3,4))) .> BUCKET(_["d"])` pinning one answer for a non-scalar key (the least surprising is a refusal — e.g. E_BAD_ARG at the key node for a NULL/LIST/RECORD key in the 2-arg spelling — or, if structured keys are wanted, a key string that is the key's dump so distinct keys stay distinct), plus cases for a NULL key and a BOOL key in both spellings; (b) make the four stringifiers and Lisp's `key-str` produce that one answer (lisp/src/builtins/aggregate.lisp:518-525 currently passes NIL into a `:type string` slot); (c) fix lisp/src/value.lisp:486-488 by turning the parallel `let` into `let*` (add `LIST(LIST(1,2), LIST(1,2)) .> DEDUPE()` to the conformance suite — four hosts already answer it). Then, in the translator, keep the fold only where it is faithful: refuse (E_SQL_SHAPE) `BUCKET(k) .> MAP(proj)` when the key's inferred kind is BOOL or when `_K` is projected from a nullable column and the evaluator's answer stays TEXT — or make the evaluator's MAP-spelling `_K` be the key Value, which is the simplest way to make the CHANGELOG sentence true. Add a NULL customer_id and a RECORD key to python/tests/test_unit.py::test_bucket_plans_answer_what_the_evaluator_answers_on_sqlite so the executed comparison covers the kinds the string fixtures cannot.

## Verifier reasoning

The evaluator code is byte-for-byte what 8fe0e3a had (only `doGroupBy`→`doBucket` renames in the diff; PHP unchanged), so the key-stringification divergence and both Lisp crashes are PRE-EXISTING. What this commit INTRODUCES is the translator fold (`plan.bucket === 'open'` → `bucketProjection(plan, binder, expr)` at js/src/sql/translator.mjs:2006-2009, php/src/Sql/Translator.php:2721-2723, python/sel/sql/translator.py:1892-1894, cpp/sel_sql_translator.cpp:2511-2513, lisp/src/sql/translator.lisp:2094-2096; absent in 8fe0e3a's translator, whose MAP case at old line 1963 always went through ensureDerived) together with the written promise that the two spellings are "one value in the evaluator" (CHANGELOG.md [Unreleased] bucket bullet; sql/cases/24-bucket.sqlt:191-197 note; the bucketProjection doc comment js/src/sql/translator.mjs:1750-1755). That promise is false for four key kinds, so the fold is a lane divergence and the promise is unmet. All hosts run the fold identically (confirmed JS and Python emit the same SQL; the sqlt fixture is green in all five), and all five evaluators agree with each other except for the collapsed-key string and the Lisp crashes, so the cross-host part is confined to structured keys whose first scalar looks numeric. Key-string logic: js/src/builtins/aggregate.mjs:436-440,459 (`looksNumeric()` descends into the first child, then `String(groupKey.scalar)` of a NONE value is "null"); python/sel/builtins/aggregate.py:410-417,432 (`str(None)` → "None"); php/src/Builtins/Structure.php:741-747,760 (`(string) null` → ""); cpp/sel.cpp:3968-3976,3983 (`eval_key.scalar()` of a NONE value is ""); lisp/src/builtins/aggregate.lisp:518-525 (`(value-scalar eval-key)` is NIL, passed to `make-group-entry` whose `key-str` slot is `:type string`, aggregate.lisp:472-475 → TYPE-ERROR). Lisp list equality: lisp/src/value.lisp:486-488 binds `(n (length sa))` in a parallel `let` alongside `sa` → UNBOUND-VARIABLE SEL::SA. BUCKET is not in spec/SPEC.md at all and conformance/16-bucket.selt only buckets on TEXT and numeric keys, which is why the suite, tools/fuzz.sh and the SQLite unit test (numeric text customer_id only) never see any of this. The SQLite probe's bool 0/1 and NULL-first ordering also differ for the 3-arg spelling, so those two are noise for this finding; the RECORD-key group count and the NULL-vs-"" `_K` are the clean differential.

## Verifier evidence

```
Runner /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/run5.sh runs one expression through all five REPLs (cpp/build up to date: `make -q` exit 0).

(1) RECORD key, MAP spelling vs 3-arg — all five hosts:
`LIST(RECORD("cat","A","v",1), RECORD("cat","B","v",2), RECORD("cat","A","v",3)) .> BUCKET(RECORD("d", _["cat"])) .> MAP(RECORD("n", COUNT(_)))` → `-{"1"=-{"n"=t"1"}}` (js, php, cpp, lisp, py); with `BUCKET(RECORD("d", _["cat"]), RECORD("n", COUNT(_)))` → `-{"1"=-{"n"=t"2"}, "2"=-{"n"=t"1"}}` (all five). One group vs two.

(2) LIST key, MAP spelling with _K: `LIST(RECORD("d", LIST(1,2), "a", 2), RECORD("d", LIST(3,4), "a", 3)) .> BUCKET(_["d"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))` → js `-{"1"=-{"k"=t"null", "n"=t"1"}}`; php and cpp `-{"1"=-{"k"=t"", "n"=t"1"}}`; py `-{"1"=-{"k"=t"None", "n"=t"1"}}`; lisp `Unhandled TYPE-ERROR ... The value NIL is not of type ...` (MAKE-GROUP-ENTRY). 3-arg spelling of the same source: js/php/cpp/py `-{"1"=-{"k"=-{"1"=t"1","2"=t"2"}, "n"=t"1"}, "2"=-{"k"=-{"1"=t"3","2"=t"4"}, "n"=t"1"}}`; lisp `Unhandled UNBOUND-VARIABLE ... The variable SEL::SA is unbound.`

(3) Bare bucket: `LIST(RECORD("d", LIST(1,2)), RECORD("d", LIST(3,4))) .> BUCKET(_["d"])` → js `-{"null"=-{"1"=-{"d"=-{"1"=t"3","2"=t"4"}}}}`, php/cpp `-{""=...}`, py `-{"None"=...}`, lisp TYPE-ERROR; the [1,2] record is dropped in every host.

(4) NULL key: bare → `-{""=-{...d=-...}, "x"=...}` (all five); MAP spelling `_K` → `t""`; 3-arg `_K` → `-` (NULL). BOOL key: MAP spelling `_K` → `t"TRUE"`/`t"FALSE"`; 3-arg → `TRUE`/`FALSE`. All five hosts agree on these.

(5) DEDUPE: `LIST(LIST(1,2), LIST(1,2)) .> DEDUPE()` → js/php/cpp/py `-{"1"=-{"1"=t"1","2"=t"2"}}`; lisp `The variable SEL::SA is unbound.`

(6) Lane divergence, Python + SQLite (verify-G/probe_sqlite.py, rows customer_id 7 / NULL / 9):
`ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_)))` → plan pure_sql, SQL `SELECT COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id"`; run() `-{"1"=-{"n"=t"1"}, "2"=-{"n"=t"1"}}` (2 groups); execute_hybrid 3 groups → DIFFER. The 3-arg spelling gives 3 groups in both lanes → SAME.
`ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))` → pure_sql; run() has `"cid"=t""` for the NULL group, execute_hybrid has `"cid"=-`. JS (verify-G/tr.mjs, planHybrid postgresql) emits the identical SQL for both programs.

(7) Provenance: `git diff 8fe0e3a ed16df2 -- js/src/builtins/aggregate.mjs python/sel/builtins/aggregate.py lisp/src/builtins/aggregate.lisp` shows only renames; `git show 8fe0e3a:js/src/builtins/aggregate.mjs | grep keyString` shows the same lines 436-459; `git show 8fe0e3a:lisp/src/value.lisp | grep "(n (length sa))"` → line 488 present. `git show 8fe0e3a:js/src/sql/translator.mjs | grep bucketProjection` → nothing (fold is new).
```

## Original review reports (deduplicated into this finding)

### [bucket-translator] The fold BUCKET(k) .> MAP(proj) == BUCKET(k, proj) is false in the evaluator for NULL, BOOL, LIST and RECORD keys, and the SQL follows the wrong spelling

*correctness · high · hosts: js, php, python, cpp, lisp*

Locations: `js/src/sql/translator.mjs:2006`; `php/src/Sql/Translator.php:2721`; `python/sel/sql/translator.py:1892`; `cpp/sel_sql_translator.cpp:2511`; `lisp/src/sql/translator.lisp:2094`; `js/src/builtins/aggregate.mjs:436`; `php/src/Builtins/Structure.php:741`; `python/sel/builtins/aggregate.py:415`; `cpp/sel.cpp:3968`; `lisp/src/builtins/aggregate.lisp:518`

The CHANGELOG, the fixture note (stmt.bucket.pipeline-map-is-the-projection) and every bucketProjection comment assert the two spellings are one value in the evaluator. They are not: a bare BUCKET returns a tree keyed by a text rendering of the key — '' for NULL and for LIST/RECORD keys, 'TRUE'/'FALSE' for booleans — and every group with the same key string overwrites the previous one (out.set(keyString, ...)), so MAP over it sees _K as that text and sees only one group for non-scalar keys. BUCKET(k, proj) binds _K to the actual key value and keeps every group. The translator folds the MAP into the projection, i.e. it emits SQL for the 3-argument semantics, so a pure_sql plan for the MAP spelling returns different rows from run() whenever a key is NULL/BOOL/LIST/RECORD. All five hosts agree with each other (the evaluators are byte-identical), so the fixtures and the SQLite unit test — numeric text keys only — cannot see it.

Reported repro:

```
REPL, all five hosts: LIST(RECORD("cat", "A", "v", 1), RECORD("cat", "B", "v", 2), RECORD("cat", "A", "v", 3)) .> BUCKET(RECORD("d", _["cat"])) .> MAP(RECORD("n", COUNT(_))) => -{"1"=-{"n"=t"1"}} while ... .> BUCKET(RECORD("d", _["cat"]), RECORD("n", COUNT(_))) => -{"1"=-{"n"=t"2"}, "2"=-{"n"=t"1"}}; with a NULL key the MAP spelling gives "cat"=t"" and the 3-arg spelling "cat"=- ; with TRUE/FALSE keys t"TRUE" vs TRUE. probe_sqlite.py (Python, SQLite): ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_))) plans pure_sql `SELECT COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id"`; run() = one row {n:2}, execute_hybrid = three rows; ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) with a NULL customer_id: run() cid="" vs SQL cid=NULL.
```

### [promises-vs-code] The evaluator's two bucket spellings are not 'one value': a bare BUCKET stringifies and collapses non-scalar keys differently in every host ("null" / "" / "None" / Lisp crash), so folding BUCKET(k) .> MAP(proj) into GROUP BY k changes results

*correctness · high · hosts: js, python, php, cpp, lisp*

Locations: `js/src/builtins/aggregate.mjs:439`; `python/sel/builtins/aggregate.py:411`; `cpp/sel.cpp:3968`; `php/src/Builtins/Structure.php:76`; `lisp/src/builtins/aggregate.lisp:518`; `lisp/src/value.lisp:486`; `sql/cases/24-bucket.sqlt:340`; `CHANGELOG.md:29`

CHANGELOG ('it is BUCKET(k, proj) in the evaluator') and the note on stmt.bucket.pipeline-map-is-the-projection ('one value in the evaluator -- the MAP's body runs once per group with _ the group and _K its key') are the justification for translating the MAP spelling as one grouped statement. In the evaluator the bare BUCKET builds a MAP keyed by a key STRING: TEXT/BOOL/numeric keys are stringified (so `_K` in the MAP spelling is always TEXT, while in the projection spelling it is the key Value), and every other key gets one shared string — computed as `String(scalar)`/`str(scalar)` after `looksNumeric()` in JS/Python ("null"/"None"), '' in PHP/C++, and NIL in Lisp, which then violates the `:type string` slot of group-entry and crashes. Because the map is keyed by that string, groups with distinct non-scalar keys overwrite each other (rows are LOST), whereas BUCKET(src, k, proj) keeps them apart. The projection spelling with list keys also crashes Lisp (value-eql-at binds `n (length sa)` inside a parallel `let`, so SA is unbound — the same crash is reachable via `LIST(LIST(1,2), LIST(1,2)) .> DEDUPE()`). This evaluator code predates the commit (e4b0432), but the commit's bucket promise and the SQL fold rest on an equivalence that does not hold, and the cross-host disagreement is in the language core.

Reported repro:

```
REPLs (node js/bin/sel.mjs -e / php php/bin/sel -e / cpp/build/sel -e / lisp/bin/sel -e / python -m sel -e): 'LIST(RECORD("d", LIST(1,2), "a", 2), RECORD("d", LIST(3,4), "a", 3)) .> BUCKET(_["d"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))' -> js -{"1"=-{"k"=t"null", "n"=t"1"}}; php and cpp -{"1"=-{"k"=t"", "n"=t"1"}}; python -{"1"=-{"k"=t"None", "n"=t"1"}}; lisp: Unhandled TYPE-ERROR NIL is not of type STRING in MAKE-GROUP-ENTRY. The projection spelling of the same source gives two groups with list keys in js/php/cpp/python and 'Unhandled UNBOUND-VARIABLE SEL::SA' in lisp. Bare 'LIST(RECORD("d", LIST(1,2)), RECORD("d", LIST(3,4))) .> BUCKET(_["d"])' -> js -{"null"=-{... d=[3,4] only}}, php/cpp -{""=...}, python -{"None"=...}, lisp crash: first record dropped in every host. tools/fuzz.sh 4000 20260813 reports 0 disagreements because its corpus never buckets on a structured key.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$SCRATCH/run5.sh`**

```bash
#!/bin/bash
# usage: run5.sh 'expr'
cd /home/nathan/workspaces/nth-share/sel
E="$1"
echo "== js";   node js/bin/sel.mjs -e "$E" 2>&1 | head -5
echo "== php";  php php/bin/sel -e "$E" 2>&1 | head -5
echo "== cpp";  cpp/build/sel -e "$E" 2>&1 | head -5
echo "== lisp"; lisp/bin/sel -e "$E" 2>&1 | head -5
echo "== py";   PYTHONPATH=$PWD/python python3 -m sel -e "$E" 2>&1 | head -5
```

**`$D/probe_sqlite.py`**

```python
import sqlite3, sys
sys.path.insert(0, '/home/nathan/workspaces/nth-share/sel/python')
from sel import compile as sel_compile
from sel.sql import Binding, Sql
from sel.value import Value

bindings = {'ORDERS': Binding.relation('orders', 'o', {
    'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM'),
    'AMOUNT': Binding.column('amount', 'o', 'NUM')})}
rows = [{'customer_id': '7', 'amount': '10'}, {'customer_id': None, 'amount': '5'},
        {'customer_id': '9', 'amount': '7'}]
db = sqlite3.connect(':memory:')
db.execute('create table orders(customer_id, amount)')
db.executemany('insert into orders values (?, ?)', [(r['customer_id'], r['amount']) for r in rows])

def runner(sql, params):
    print('   SQL:', sql, params)
    cur = db.execute(sql, [p.as_text() for p in params])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, [None if v is None else str(v) for v in row])) for row in cur.fetchall()]

shapes = [
    'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))',
    'ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_)))',
    'ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_)))',
    'ORDERS .> BUCKET(RECORD("c", _["customer_id"]), RECORD("n", COUNT(_)))',
    'ORDERS .> BUCKET(_["amount"] > 6) .> MAP(RECORD("k", _K, "n", COUNT(_)))',
    'ORDERS .> BUCKET(_["amount"] > 6, RECORD("k", _K, "n", COUNT(_)))',
]
for source in shapes:
    print('###', source)
    program = sel_compile(source)
    want = program.run({'ORDERS': rows}).dump()
    plan = Sql.plan_hybrid(program, 'sqlite', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    print('   plan:', kind)
    got = Sql.execute_hybrid(plan, runner, {'ORDERS': rows})
    got = got if isinstance(got, Value) else Value.from_native(got)
    print('   run():          ', want)
    print('   execute_hybrid: ', got.dump())
    print('   SAME' if got.dump() == want else '   DIFFER')
```

**`$D/tr.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const bindings = { ORDERS: Binding.relation('orders', 'o', {
  CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'), AMOUNT: Binding.column('amount', 'o', 'NUM') }) };
for (const src of [
  'ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_)))',
  'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))',
]) {
  const p = compile(src);
  const plan = Sql.planHybrid(p, 'postgresql', bindings);
  console.log(src, '\n  ', plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid', plan.sql ?? plan.prefixSql ?? JSON.stringify(Object.keys(plan)));
}
```

**`$D/tr.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/bootstrap.php';
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$b = ['ORDERS' => Binding::relation('orders', 'o', ['CUSTOMER_ID' => Binding::column('customer_id', 'o', 'NUM'), 'AMOUNT' => Binding::column('amount','o','NUM')])];
foreach (['ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_)))'] as $src) {
  $p = Sel::compile($src);
  $plan = Sql::planHybrid($p, 'postgresql', $b);
  echo $src, "\n   "; print_r(array_filter((array)$plan, fn($v)=>is_scalar($v)));
}
```

### Commands run and their output



```bash
cat conformance/16-bucket.selt; echo ======; grep -n -i "bucket" docs/EXTENDING.md docs/SQL-TRANSLATION.md | head -60
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
======
docs/EXTENDING.md:602:  evaluator would have produced for it. A bare `BUCKET` breaks that (keys, not
docs/EXTENDING.md:603:  groups); `bucketRowsAreKeys` in each planner is the list of such steps, and a
docs/EXTENDING.md:605:- **Vocabulary, again.** The externally facing verb is `BUCKET`; `GROUP_BY` is
docs/SQL-TRANSLATION.md:2279:- **A bucket is split only where SQL still has its members.** `BUCKET(src,
docs/SQL-TRANSLATION.md:2281:  SEL's value is a map of member rows. So `BUCKET(src, key) .> MAP(proj)` is
docs/SQL-TRANSLATION.md:2282:  translated as `BUCKET(src, key, proj)`, one grouped statement (a `FILTER`
docs/SQL-TRANSLATION.md:2283:  between them is its `HAVING`), and a prefix that ends in a bucket nobody has
docs/SQL-TRANSLATION.md:2285:  planner backs up to the step before the bucket, or stays in memory. Before
docs/SQL-TRANSLATION.md:2286:  this the split landed after the bucket and the continuation counted one row
```



```bash
git check-ignore cpp/build && (cd cpp && make -q 2>&1; echo "make -q exit: $?")
```

```
cpp/build
make -q exit: 0
```



```bash
# (file written, see "Reproduction scripts" above)
chmod +x $SCRATCH/run5.sh
R=$SCRATCH/run5.sh
echo "#### repro 1: record key, MAP spelling"
$R 'LIST(RECORD("cat", "A", "v", 1), RECORD("cat", "B", "v", 2), RECORD("cat", "A", "v", 3)) .> BUCKET(RECORD("d", _["cat"])) .> MAP(RECORD("n", COUNT(_)))'
echo "#### repro 1b: record key, 3-arg spelling"
$R 'LIST(RECORD("cat", "A", "v", 1), RECORD("cat", "B", "v", 2), RECORD("cat", "A", "v", 3)) .> BUCKET(RECORD("d", _["cat"]), RECORD("n", COUNT(_)))'
```

```
#### repro 1: record key, MAP spelling
== js
-{"1"=-{"n"=t"1"}}
== php
-{"1"=-{"n"=t"1"}}
== cpp
-{"1"=-{"n"=t"1"}}
== lisp
-{"1"=-{"n"=t"1"}}
== py
-{"1"=-{"n"=t"1"}}
#### repro 1b: record key, 3-arg spelling
== js
-{"1"=-{"n"=t"2"}, "2"=-{"n"=t"1"}}
== php
-{"1"=-{"n"=t"2"}, "2"=-{"n"=t"1"}}
== cpp
-{"1"=-{"n"=t"2"}, "2"=-{"n"=t"1"}}
== lisp
-{"1"=-{"n"=t"2"}, "2"=-{"n"=t"1"}}
== py
-{"1"=-{"n"=t"2"}, "2"=-{"n"=t"1"}}
```



```bash
R=$SCRATCH/run5.sh
echo "#### repro 2: list key, MAP spelling with _K"
$R 'LIST(RECORD("d", LIST(1,2), "a", 2), RECORD("d", LIST(3,4), "a", 3)) .> BUCKET(_["d"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))'
echo "#### repro 2b: list key, 3-arg spelling"
$R 'LIST(RECORD("d", LIST(1,2), "a", 2), RECORD("d", LIST(3,4), "a", 3)) .> BUCKET(_["d"], RECORD("k", _K, "n", COUNT(_)))'
echo "#### repro 3: bare bucket list key"
$R 'LIST(RECORD("d", LIST(1,2)), RECORD("d", LIST(3,4))) .> BUCKET(_["d"])'
```

```
#### repro 2: list key, MAP spelling with _K
== js
-{"1"=-{"k"=t"null", "n"=t"1"}}
== php
-{"1"=-{"k"=t"", "n"=t"1"}}
== cpp
-{"1"=-{"k"=t"", "n"=t"1"}}
== lisp
Unhandled TYPE-ERROR in thread #<SB-THREAD:THREAD tid=759225 "main thread" RUNNING
                                  {1204030083}>:
  The value
    NIL
  is not of type
== py
-{"1"=-{"k"=t"None", "n"=t"1"}}
#### repro 2b: list key, 3-arg spelling
== js
-{"1"=-{"k"=-{"1"=t"1", "2"=t"2"}, "n"=t"1"}, "2"=-{"k"=-{"1"=t"3", "2"=t"4"}, "n"=t"1"}}
== php
-{"1"=-{"k"=-{"1"=t"1", "2"=t"2"}, "n"=t"1"}, "2"=-{"k"=-{"1"=t"3", "2"=t"4"}, "n"=t"1"}}
== cpp
-{"1"=-{"k"=-{"1"=t"1", "2"=t"2"}, "n"=t"1"}, "2"=-{"k"=-{"1"=t"3", "2"=t"4"}, "n"=t"1"}}
== lisp
Unhandled UNBOUND-VARIABLE in thread #<SB-THREAD:THREAD tid=759294 "main thread" RUNNING
                                        {1204028083}>:
  The variable SEL::SA is unbound.

Backtrace for: #<SB-THREAD:THREAD tid=759294 "main thread" RUNNING {1204028083}>
== py
-{"1"=-{"k"=-{"1"=t"1", "2"=t"2"}, "n"=t"1"}, "2"=-{"k"=-{"1"=t"3", "2"=t"4"}, "n"=t"1"}}
#### repro 3: bare bucket list key
== js
-{"null"=-{"1"=-{"d"=-{"1"=t"3", "2"=t"4"}}}}
== php
-{""=-{"1"=-{"d"=-{"1"=t"3", "2"=t"4"}}}}
== cpp
-{""=-{"1"=-{"d"=-{"1"=t"3", "2"=t"4"}}}}
== lisp
Unhandled TYPE-ERROR in thread #<SB-THREAD:THREAD tid=759318 "main thread" RUNNING
                                  {1204030083}>:
  The value
    NIL
  is not of type
== py
-{"None"=-{"1"=-{"d"=-{"1"=t"3", "2"=t"4"}}}}
```



```bash
R=$SCRATCH/run5.sh
echo "#### NULL key, bare"
$R 'LIST(RECORD("d", NULL, "a", 1), RECORD("d", "x", "a", 2)) .> BUCKET(_["d"])'
echo "#### NULL key, MAP spelling"
$R 'LIST(RECORD("d", NULL, "a", 1), RECORD("d", "x", "a", 2)) .> BUCKET(_["d"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))'
echo "#### NULL key, 3-arg"
$R 'LIST(RECORD("d", NULL, "a", 1), RECORD("d", "x", "a", 2)) .> BUCKET(_["d"], RECORD("k", _K, "n", COUNT(_)))'
echo "#### BOOL key, MAP spelling"
$R 'LIST(RECORD("d", TRUE, "a", 1), RECORD("d", FALSE, "a", 2), RECORD("d", TRUE, "a", 3)) .> BUCKET(_["d"]) .> MAP(RECORD("k", _K, "n", COUNT(_)))'
echo "#### BOOL key, 3-arg"
$R 'LIST(RECORD("d", TRUE, "a", 1), RECORD("d", FALSE, "a", 2), RECORD("d", TRUE, "a", 3)) .> BUCKET(_["d"], RECORD("k", _K, "n", COUNT(_)))'
```

```
#### NULL key, bare
== js
-{""=-{"1"=-{"d"=-, "a"=t"1"}}, "x"=-{"1"=-{"d"=t"x", "a"=t"2"}}}
== php
-{""=-{"1"=-{"d"=-, "a"=t"1"}}, "x"=-{"1"=-{"d"=t"x", "a"=t"2"}}}
== cpp
-{""=-{"1"=-{"d"=-, "a"=t"1"}}, "x"=-{"1"=-{"d"=t"x", "a"=t"2"}}}
== lisp
-{""=-{"1"=-{"d"=-, "a"=t"1"}}, "x"=-{"1"=-{"d"=t"x", "a"=t"2"}}}
== py
-{""=-{"1"=-{"d"=-, "a"=t"1"}}, "x"=-{"1"=-{"d"=t"x", "a"=t"2"}}}
#### NULL key, MAP spelling
== js
-{"1"=-{"k"=t"", "n"=t"1"}, "2"=-{"k"=t"x", "n"=t"1"}}
== php
-{"1"=-{"k"=t"", "n"=t"1"}, "2"=-{"k"=t"x", "n"=t"1"}}
== cpp
-{"1"=-{"k"=t"", "n"=t"1"}, "2"=-{"k"=t"x", "n"=t"1"}}
== lisp
-{"1"=-{"k"=t"", "n"=t"1"}, "2"=-{"k"=t"x", "n"=t"1"}}
== py
-{"1"=-{"k"=t"", "n"=t"1"}, "2"=-{"k"=t"x", "n"=t"1"}}
#### NULL key, 3-arg
== js
-{"1"=-{"k"=-, "n"=t"1"}, "2"=-{"k"=t"x", "n"=t"1"}}
== php
-{"1"=-{"k"=-, "n"=t"1"}, "2"=-{"k"=t"x", "n"=t"1"}}
== cpp
-{"1"=-{"k"=-, "n"=t"1"}, "2"=-{"k"=t"x", "n"=t"1"}}
== lisp
-{"1"=-{"k"=-, "n"=t"1"}, "2"=-{"k"=t"x", "n"=t"1"}}
== py
-{"1"=-{"k"=-, "n"=t"1"}, "2"=-{"k"=t"x", "n"=t"1"}}
#### BOOL key, MAP spelling
== js
-{"1"=-{"k"=t"TRUE", "n"=t"2"}, "2"=-{"k"=t"FALSE", "n"=t"1"}}
== php
-{"1"=-{"k"=t"TRUE", "n"=t"2"}, "2"=-{"k"=t"FALSE", "n"=t"1"}}
== cpp
-{"1"=-{"k"=t"TRUE", "n"=t"2"}, "2"=-{"k"=t"FALSE", "n"=t"1"}}
== lisp
-{"1"=-{"k"=t"TRUE", "n"=t"2"}, "2"=-{"k"=t"FALSE", "n"=t"1"}}
== py
-{"1"=-{"k"=t"TRUE", "n"=t"2"}, "2"=-{"k"=t"FALSE", "n"=t"1"}}
#### BOOL key, 3-arg
== js
-{"1"=-{"k"=TRUE, "n"=t"2"}, "2"=-{"k"=FALSE, "n"=t"1"}}
== php
-{"1"=-{"k"=TRUE, "n"=t"2"}, "2"=-{"k"=FALSE, "n"=t"1"}}
== cpp
-{"1"=-{"k"=TRUE, "n"=t"2"}, "2"=-{"k"=FALSE, "n"=t"1"}}
== lisp
-{"1"=-{"k"=TRUE, "n"=t"2"}, "2"=-{"k"=FALSE, "n"=t"1"}}
== py
-{"1"=-{"k"=TRUE, "n"=t"2"}, "2"=-{"k"=FALSE, "n"=t"1"}}
```



```bash
D=$SCRATCH/verify-G && mkdir -p $D && # (file written, see "Reproduction scripts" above)
python3 $D/probe_sqlite.py
```

```
### ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_)))
   plan: pure_sql
   SQL: SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" []
   run():           -{"1"=-{"cid"=t"7", "n"=t"1"}, "2"=-{"cid"=t"", "n"=t"1"}, "3"=-{"cid"=t"9", "n"=t"1"}}
   execute_hybrid:  -{"1"=-{"cid"=-, "n"=t"1"}, "2"=-{"cid"=t"7", "n"=t"1"}, "3"=-{"cid"=t"9", "n"=t"1"}}
   DIFFER
### ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_)))
   plan: pure_sql
   SQL: SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" []
   run():           -{"1"=-{"cid"=t"7", "n"=t"1"}, "2"=-{"cid"=-, "n"=t"1"}, "3"=-{"cid"=t"9", "n"=t"1"}}
   execute_hybrid:  -{"1"=-{"cid"=-, "n"=t"1"}, "2"=-{"cid"=t"7", "n"=t"1"}, "3"=-{"cid"=t"9", "n"=t"1"}}
   DIFFER
### ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_)))
   plan: pure_sql
   SQL: SELECT COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" []
   run():           -{"1"=-{"n"=t"1"}, "2"=-{"n"=t"1"}}
   execute_hybrid:  -{"1"=-{"n"=t"1"}, "2"=-{"n"=t"1"}, "3"=-{"n"=t"1"}}
   DIFFER
### ORDERS .> BUCKET(RECORD("c", _["customer_id"]), RECORD("n", COUNT(_)))
   plan: pure_sql
   SQL: SELECT COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id" []
   run():           -{"1"=-{"n"=t"1"}, "2"=-{"n"=t"1"}, "3"=-{"n"=t"1"}}
   execute_hybrid:  -{"1"=-{"n"=t"1"}, "2"=-{"n"=t"1"}, "3"=-{"n"=t"1"}}
   SAME
### ORDERS .> BUCKET(_["amount"] > 6) .> MAP(RECORD("k", _K, "n", COUNT(_)))
   plan: pure_sql
   SQL: SELECT (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)) AS "k", COUNT(*) AS "n" FROM "orders" "o" GROUP BY (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)) []
   run():           -{"1"=-{"k"=t"TRUE", "n"=t"2"}, "2"=-{"k"=t"FALSE", "n"=t"1"}}
   execute_hybrid:  -{"1"=-{"k"=t"0", "n"=t"1"}, "2"=-{"k"=t"1", "n"=t"2"}}
   DIFFER
### ORDERS .> BUCKET(_["amount"] > 6, RECORD("k", _K, "n", COUNT(_)))
   plan: pure_sql
   SQL: SELECT (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)) AS "k", COUNT(*) AS "n" FROM "orders" "o" GROUP BY (CAST("o"."amount" AS NUMERIC) > CAST('6' AS NUMERIC)) []
   run():           -{"1"=-{"k"=TRUE, "n"=t"2"}, "2"=-{"k"=FALSE, "n"=t"1"}}
   execute_hybrid:  -{"1"=-{"k"=t"0", "n"=t"1"}, "2"=-{"k"=t"1", "n"=t"2"}}
   DIFFER
```



```bash
D=$SCRATCH/verify-G
# (file written, see "Reproduction scripts" above)
node $D/tr.mjs
# (file written, see "Reproduction scripts" above)
php $D/tr.php 2>&1 | head -30
```

```
ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_))) 
   pure_sql ["dialect","sqlStatement","sqlPrefixAst","continuationAst","continuationProgram","continuationSourceVar","pureSql","pureMemory","sourceTables"]
ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) 
   pure_sql ["dialect","sqlStatement","sqlPrefixAst","continuationAst","continuationProgram","continuationSourceVar","pureSql","pureMemory","sourceTables"]
ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_)))
   Array
(
    [dialect] => postgresql
    [continuationSourceVar] => _INPUT
    [pureSql] => 1
    [pureMemory] => 
    [continuation_source_var] => _INPUT
    [pure_sql] => 1
    [pure_memory] => 
    [is_hybrid] => 
)
```



```bash
D=$SCRATCH/verify-G
grep -n "sqlStatement" js/src/sql/hybrid.mjs | head -3; grep -n "class SqlStatement\|sql()\|get sql\|\.text\b" js/src/sql/statement.mjs 2>/dev/null | head; ls js/src/sql/
```

```
33:  constructor({ dialect = null, sqlStatement = null, sqlPrefixAst = null, continuationAst = null,
37:    this.sqlStatement = sqlStatement;
47:  get sql_query() { return this.sqlStatement; }
binder.mjs
binding.mjs
bindings.mjs
constants.mjs
emit.mjs
errors.mjs
fragment.mjs
hybrid.mjs
index.d.ts
index.mjs
_map.mjs
map.mjs
normalise.mjs
relational-plan.mjs
translator.mjs
```



```bash
D=$SCRATCH/verify-G
sed -i 's/plan.sql ?? plan.prefixSql ?? JSON.stringify(Object.keys(plan))/plan.sqlStatement.sql ?? plan.sqlStatement.text ?? JSON.stringify(Object.keys(plan.sqlStatement))/' $D/tr.mjs; node $D/tr.mjs
grep -n "sqlStatement\|sql_statement" php/src/Sql/HybridPlan.php 2>/dev/null | head -3; ls php/src/Sql | head -30
```

```
ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_))) 
   pure_sql ["parts","kind","dialect","params","paramKinds","caveats","exact","sargable","guard","prefilter","separatePrefilter"]
ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) 
   pure_sql ["parts","kind","dialect","params","paramKinds","caveats","exact","sargable","guard","prefilter","separatePrefilter"]
Binder.php
Binding.php
Bindings.php
bootstrap.php
Constants.php
Emit.php
Fragment.php
Hybrid.php
MapData.php
Map.php
Normalise.php
RelationalPlan.php
SqlError.php
Sql.php
Translator.php
```



```bash
D=$SCRATCH/verify-G
grep -n "toSql\|render()\|get sql()\|toString()" js/src/sql/fragment.mjs | head -5
```

```
(Bash completed with no output)
```



```bash
D=$SCRATCH/verify-G
sed -n 135,150p js/bin/sqlt.mjs; sed -n 240,255p js/bin/sqlt.mjs
```

```
return 'a pure-memory plan must run the original program';
    }
    if (plan.continuationAst !== program.ast) {
      return 'a pure-memory plan must expose the original AST as its continuation';
    }
  } else {
    if (plan.sqlStatement === null) return `a ${c.plan} plan has no SQL statement`;
    if (plan.sqlPrefixAst === null) return `a ${c.plan} plan has no SQL prefix AST`;
    const sql = plan.sqlStatement.asStatement(mode);
    if (sql !== c.expect) return `got:  ${sql}\n     want: ${c.expect}`;
    if (c.plan === 'pure_sql') {
      if (plan.continuationProgram !== null || plan.continuationAst !== null) {
        return 'a pure-SQL plan carries a continuation';
      }
    } else if (plan.continuationProgram === null || plan.continuationAst === null) {
      return 'a hybrid plan has no continuation';
  // Checked for every case that produces a fragment, not only those asking about
  // params: every slot in the part list must have a value, and every value must
  // be emitted. A value bound but never emitted means a Fragment was rendered and
  // thrown away — invisible in `inline` mode, which is what the rest of the suite
  // asserts.
  const seen = new Set();
  for (const p of frag.parts) {
    if (typeof p === 'string') continue;
    if (p < 1 || p > frag.params.length) {
      return `parameter slot ${p} has no value in params`;
    }
    seen.add(p);
  }
  const orphans = [];
  for (let i = 1; i <= frag.params.length; i += 1) if (!seen.has(i)) orphans.push(i);
  if (orphans.length) {
```



```bash
D=$SCRATCH/verify-G
sed -i "s/plan.sqlStatement.sql ?? plan.sqlStatement.text ?? JSON.stringify(Object.keys(plan.sqlStatement))/plan.sqlStatement.asStatement('inline')/" $D/tr.mjs; node $D/tr.mjs
```

```
ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_))) 
   pure_sql SELECT COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id"
ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) 
   pure_sql SELECT "o"."customer_id" AS "cid", COUNT(*) AS "n" FROM "orders" "o" GROUP BY "o"."customer_id"
```



```bash
R=$SCRATCH/run5.sh
echo "#### DEDUPE list-of-lists"
$R 'LIST(LIST(1,2), LIST(1,2)) .> DEDUPE()'
echo "#### equality of lists"
$R 'LIST(1,2) == LIST(1,2)'
git show 8fe0e3a:lisp/src/value.lisp | grep -n "(n (length sa))" ; git show 8fe0e3a:lisp/src/builtins/aggregate.lisp | grep -n "(t \"\"))"
```

```
#### DEDUPE list-of-lists
== js
-{"1"=-{"1"=t"1", "2"=t"2"}}
== php
-{"1"=-{"1"=t"1", "2"=t"2"}}
== cpp
-{"1"=-{"1"=t"1", "2"=t"2"}}
== lisp
Unhandled UNBOUND-VARIABLE in thread #<SB-THREAD:THREAD tid=763935 "main thread" RUNNING
                                        {1204030083}>:
  The variable SEL::SA is unbound.

Backtrace for: #<SB-THREAD:THREAD tid=763935 "main thread" RUNNING {1204030083}>
== py
-{"1"=-{"1"=t"1", "2"=t"2"}}
#### equality of lists
== js
TRUE
== php
TRUE
== cpp
TRUE
== lisp
TRUE
== py
TRUE
488:                (n (length sa)))
525:                                                        (t ""))
```

