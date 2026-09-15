# R. LAZY_RECORD: C++ MAP clones rows and forces unread lazy fields; the other four never evaluate an unread MAP field

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** cpp, js, php, python, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing, but contradicting a promise ed16df2 wrote.

## Summary (verifier)

Every host's in-memory optimiser rewrites `MAP(RECORD(>=2 pairs))` to `LAZY_RECORD`, whose non-literal fields are thunks; C++'s MAP clones each row (`r.clone()`, and `clone_at` starts with `force()`), so every field is evaluated at MAP time, while JS/Python/Lisp never evaluate a field nothing reads and PHP only evaluates it when a later FILTER copies the row (`$item->copy()` forces). So `COUNT(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))` is 1 in JS/PHP/Python/Lisp and E_DIV_ZERO@1:35 in C++, and `... .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()` splits three ways (JS/Py/Lisp: 1; PHP and C++: E_ABORT). The spec (strict RECORD, §6.2/6.3 "stops at the first failure") and the commit's own new promise in docs/EXTENDING.md ("The optimiser is invisible: an error a program raises after optimisation carries the same code and position as before it") make C++'s answer the correct one; the behaviour itself predates the commit (identical on a build of 8fe0e3a), but this commit writes the promise it violates and documents "five copy sites incl. MAP result and FILTER element" that only C++ actually implements.

## Suggested fix — case first

Add the .selt case first: `COUNT(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))` => E_DIV_ZERO@1:35, plus a FILTER-after-MAP twin (`LIST(RECORD("id", 1), RECORD("id", 2)) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()` => E_ABORT@1:81) so the PHP-only forcing is pinned too, with a `--- note` that the physical rewrite must not change which errors a program raises. Smallest fix that satisfies the spec and the "optimiser is invisible" promise: make the rewrite unobservable by forcing every field of a LAZY_RECORD row when MAP collects it (i.e. JS/PHP/Python/Lisp MAP do what C++'s clone already does, or C++'s MAP forces without cloning) — which admits the rewrite buys nothing under the current spec and it can simply be dropped; the alternative of keeping the laziness requires a spec change saying MAP projections are evaluated on demand, and then C++ MAP and PHP FILTER must stop forcing. Either way, correct docs/EXTENDING.md's five-copy-sites paragraph (MAP/FILTER copy only in C++, FILTER also in PHP).

## Verifier reasoning

Both members are facets of one defect and both reproduce exactly as reported. Reading: the LAZY_RECORD rewrite fires unconditionally on any MAP whose body is RECORD with >=4 args in all five physical optimisers (js/src/optimizer.mjs:592-600, php/src/Optimizer.php:93-94, python/sel/optimizer.py:663-664, lisp/src/optimizer.lisp:852-867, cpp/sel.cpp:5831-5832). LAZY_RECORD builds thunks for non-literal fields (js/src/builtins/structure.mjs:75-93, cpp/sel.cpp:3479-3510). MAP collects the row without copying in JS (js/src/builtins/aggregate.mjs:113-119 `out.push(r)`), PHP (php/src/Builtins/Core.php:447-454 `$out[] = $r`), Python (python/sel/builtins/aggregate.py:89-96 `out.append(r)`), Lisp (lisp/src/builtins/aggregate.lisp:94-103 `(push r out)`), but C++ does `out.push_back(r.clone())` (cpp/sel.cpp:4025-4033) and `Value::clone_at` calls `force()` first and recurses into every child (cpp/sel.cpp:804-835), so every thunk is evaluated at MAP time. FILTER adds a second axis: C++ `item.clone()` (cpp/sel.cpp:4037-4046) and PHP `$item->copy()` (php/src/Builtins/Core.php:460-469; `copyAt` calls `$this->force()` and recurses, php/src/Value.php:679-700) force, while JS (aggregate.mjs:125-133 `out.set(key,item)`), Python (aggregate.py:99-111) and Lisp (aggregate.lisp:107-119) alias. Hence three observable classes: C++ (eager at MAP), PHP (eager only if a FILTER follows), JS/Py/Lisp (never unless the value is dumped/read). The Lisp optimiser's own header (lisp/src/optimizer.lisp:7) says the rewrite exists "to avoid computing unused fields on non-selected rows", i.e. it was meant to be unobservable, which it is not. Spec: RECORD is strict (§6 "A strict function's arguments are all evaluated, left to right"), MAP is "list of each body result", and §6.3 says evaluation stops at the first failure — so the unoptimised program raises, and C++ is the host matching the spec. Pre-existing: `git show 8fe0e3a` has the same MAP/clone code and the same LAZY_RECORD rewrite (it first arrived in 8fe0e3a for JS), and a `git archive 8fe0e3a` build reproduces the same split. What is new in ed16df2 is docs/EXTENDING.md §"Changing the optimiser or the planner" (the "optimiser is invisible" bullet did not exist at 8fe0e3a), so the commit's promise is unmet by four hosts. Hybrid lane: the same builtins run in the continuation, so per host the hybrid lane agrees with run() for these shapes (verified JS/Py/PHP/C++). Incidental, not part of R: `ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> MAP(_["v"])` plans as SQL `SELECT v FROM (SELECT id AS v ...)` plus a continuation that re-runs the MAP(RECORD) over those rows; against SQLite the hybrid lane gives E_NO_KEY@1:39 while run() gives the values — a separate planner bug worth its own finding.

## Verifier evidence

```
Reported repros, all five REPLs (HEAD ed16df2):
`O = LIST(RECORD("id", 1), RECORD("id", 2)); O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> COUNT()` -> js 2, php 2, lisp 2, py 2, cpp `E_ABORT at line 1 column 90: no`.
`COUNT(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))` -> js/php/lisp/py 1, cpp `E_DIV_ZERO at line 1 column 35`.
`(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))[0]["b"]` -> four hosts `E_NO_KEY at line 1 column 53`, cpp `E_DIV_ZERO at line 1 column 30`.
Bare `LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3))` -> all five `E_DIV_ZERO at line 1 column 29` (dump forces, which is why the suite never sees it).
`O = ...; O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> FILTER(_["NOTE"] == "x")` -> four hosts `E_NO_KEY at 1:109`, cpp `E_ABORT at 1:90`.
My own programs, all five REPLs:
`ORDERS = LIST(RECORD("id", 1), RECORD("id", 2)); ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> SUM(_["v"])` -> js 3, php 3, lisp 3, py 3, cpp `E_ABORT at line 1 column 121: x`.
`... .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()` -> js 1, lisp 1, py 1, php `E_ABORT at 1:121`, cpp `E_ABORT at 1:121` (PHP's FILTER copy forces; three-way split).
Both lanes via plan_hybrid/execute_hybrid (probe scripts in scratchpad/verify-R/probe.{mjs,py,php,cpp}, bindings ORDERS(id NUM), fake runner returning [{id:1},{id:2}]): SUM shape -> js/py/php `pure_memory run=t"3" hybrid=t"3"`, cpp `run=E_ABORT@1:72 hybrid=E_ABORT@1:72`; FILTER+COUNT shape -> js/py `run=t"1" hybrid=t"1"`, php `run=E_ABORT@1:72 hybrid=E_ABORT@1:72`, cpp same as php.
Physical trees identical in JS and PHP for the FILTER shape (both carry LAZY_RECORD; scratchpad/verify-R/phys.{mjs,php}), so the split is in the builtins, not the rewrite.
Pre-existing: `git archive 8fe0e3a` into scratchpad/verify-R/old, `make build/sel` there; `COUNT(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))` -> js 1, php 1, py 1, cpp `E_DIV_ZERO at line 1 column 35`; FILTER+COUNT shape -> js 1, php E_ABORT@1:81, cpp E_ABORT@1:81, py 1. `git log -S'LAZY_RECORD' -- js/src/optimizer.mjs` -> first appears in 8fe0e3a. `git show 8fe0e3a:docs/EXTENDING.md | grep -c 'optimiser is invisible'` -> 0; the bullet is added by this commit (docs/EXTENDING.md:610-620).
Code: cpp/sel.cpp:4025-4033 (MAP `r.clone()`), cpp/sel.cpp:804-835 (`clone_at` begins with `force()`), cpp/sel.cpp:4037-4046 (FILTER `item.clone()`), cpp/sel.cpp:3479-3510 (LAZY_RECORD thunks), cpp/sel.cpp:5831-5832; js/src/builtins/aggregate.mjs:113-119 and 125-133; js/src/builtins/structure.mjs:75-93; js/src/optimizer.mjs:592-600; php/src/Builtins/Core.php:447-454 and 460-469; php/src/Value.php:673-700 (`copyAt` forces); php/src/Optimizer.php:93-94; python/sel/builtins/aggregate.py:89-96 and 99-111; python/sel/optimizer.py:663-664; lisp/src/builtins/aggregate.lisp:94-103 and 107-119; lisp/src/optimizer.lisp:7, 852-867; spec/SPEC.md:461-475 (§6.2/6.3), 605-608 (strict/lazy), 650 (MAP); docs/EXTENDING.md:480-484 ("copies at exactly five places ... MAP collecting a result, and FILTER collecting an element" — true only of C++).
```

## Original review reports (deduplicated into this finding)

### [hybrid-planner] LAZY_RECORD physical rewrite changes observable results: JS/PHP/Python/Lisp never evaluate an unread MAP field, C++ evaluates it on materialisation

*correctness · high · hosts: js, php, python, lisp, cpp*

Locations: `js/src/optimizer.mjs:587-597`; `cpp/sel.cpp:5822-5834`; `js/src/builtins/structure.mjs:75`; `cpp/sel.cpp:3479`; `lisp/src/optimizer.lisp:852-867`

Every host rewrites `MAP(RECORD(...>=2 pairs))` to LAZY_RECORD in the physical tree (the tree §12.1 says run() evaluates). In four hosts a lazy field that nothing reads is never evaluated, even when the record is the program's final value or is counted; C++ forces the fields, so an erroring field (or a side-effecting one) raises in C++ and not elsewhere. The unoptimised semantics (MAP builds the record) say E_ABORT at the ABORT. This is visible in the pure_memory lane and in hybrid continuations (`ORDERS .> MAP(RECORD("id",_["id"],"note",ABORT("no"))) .> FILTER(_["NOTE"] == "x")` executes as E_NO_KEY@1:70 in four hosts and E_ABORT@1:51 in C++, probe batch 2 line 264).

Reported repro:

```
`O = LIST(RECORD("id", 1), RECORD("id", 2)); O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> COUNT()` -> node/php/py/lisp: 2; cpp/build/sel: E_ABORT at 1:90.  `... .> FILTER(_["id"] > 1) .> MAP(_["id"])` -> four hosts -{"1"=t"2"}; C++ E_ABORT at 1:90.  `... .> FILTER(_["NOTE"] == "x")` -> four hosts E_NO_KEY at 1:109, C++ E_ABORT at 1:90.
```

### [program-cache-immutability] C++ MAP clones each mapped row, forcing LAZY_RECORD fields that the other four hosts leave lazy: run() diverges on any unread failing field

*correctness · high · hosts: cpp, js, php, python, lisp*

Locations: `cpp/sel.cpp:4029`; `cpp/sel.cpp:804-835`; `js/src/builtins/aggregate.mjs:117`; `php/src/Builtins/Core.php:451`; `python/sel/builtins/aggregate.py:93`; `lisp/src/builtins/aggregate.lisp:100`

The physical tree that Program.run() now caches rewrites MAP(RECORD(...)) with four or more arguments into LAZY_RECORD in every host, whose non-literal fields are thunks forced on first read. C++'s MAP pushes `r.clone()` for every row (cpp/sel.cpp:4029) and Value::clone_at begins with force() (cpp/sel.cpp:805), so every lazy field is evaluated at MAP time; JS, PHP, Python and Lisp push the row itself, so a field that is never read is never evaluated. A field whose evaluation raises therefore raises in C++ and not elsewhere — a run() result difference that the conformance corpus does not carry (my 1260-program corpus is identical across hosts; only this hand-written shape differs). This is pre-existing — neither MAP nor clone is in the diff — and outside the hybrid/fold groups the commit promises, but it sits exactly on the physical rewrite this commit makes the cached, canonical thing run() evaluates, and CLAUDE.md's 'deep copies happen at exactly five sites (... MAP result ...)' is only true of C++. Minimal .selt case first, then decide whether MAP copies (four hosts change) or does not (C++ changes and the clone-forces-thunks rule needs a second look).

Reported repro:

```
`COUNT(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))` => 1 on js/php/lisp/python; E_DIV_ZERO at line 1 column 35 on cpp. `(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))[0]["b"]` => E_NO_KEY at 1:53 on four hosts (the lazy field was never forced), E_DIV_ZERO at 1:30 on cpp. Dumping the result (`LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3))` bare) forces everywhere and all five raise E_DIV_ZERO at 1:29, which is why the suite never sees it.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$D/probe.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const bindings = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
const rows = [{ id: '1' }, { id: '2' }];
const fail = (fn) => { try { const v = fn(); return v.dump ? v.dump() : JSON.stringify(v); } catch (e) { return e.code ? `${e.code}@${e.line}:${e.col}` : String(e); } };
for (const src of process.argv.slice(2)) {
  const p = compile(src);
  const plan = Sql.planHybrid(p, 'postgresql', bindings);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  console.log(`js   ${kind}  run=${fail(() => p.run({ ORDERS: rows }))}  hybrid=${fail(() => Sql.executeHybrid(plan, () => rows, { ORDERS: rows }))}`);
}
```

**`$D/probe.py`**

```python
import sys
sys.path.insert(0, '/home/nathan/workspaces/nth-share/sel/python')
from sel import compile as sel_compile, SelError
from sel.sql import Sql, Binding
bindings = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}
rows = [{'id': '1'}, {'id': '2'}]
def fail(fn):
    try:
        v = fn(); return v.dump() if hasattr(v, 'dump') else repr(v)
    except SelError as e:
        return f'{e.code}@{e.line}:{e.col}'
for src in sys.argv[1:]:
    p = sel_compile(src)
    plan = Sql.plan_hybrid(p, 'postgresql', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    print(f'py   {kind}  run={fail(lambda: p.run({"ORDERS": rows}))}  hybrid={fail(lambda: Sql.execute_hybrid(plan, lambda s, a: rows, {"ORDERS": rows}))}')
```

**`$D/probe.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/bootstrap.php';
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding; use Sel\SelError;
$bindings = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
$rows = [['id' => '1'], ['id' => '2']];
$fail = function ($fn) { try { $v = $fn(); return is_object($v) && method_exists($v, 'dump') ? $v->dump() : json_encode($v); } catch (SelError $e) { return $e->code . '@' . $e->line . ':' . $e->col; } };
foreach (array_slice($argv, 1) as $src) {
  $p = Sel::compile($src);
  $plan = Sql::planHybrid($p, 'postgresql', $bindings);
  $kind = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
  echo "php  $kind  run=" . $fail(fn() => $p->run(['ORDERS' => $rows])) . "  hybrid=" . $fail(fn() => Sql::executeHybrid($plan, fn($s, $a) => $rows, ['ORDERS' => $rows])) . "\n";
}
```

**`$D/phys.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
const strip = (n) => { if (Array.isArray(n)) return n.map(strip); if (n && typeof n === 'object') { const o = {}; for (const [k, v] of Object.entries(n)) { if (k === 'pos' || k === 'spec') continue; o[k] = strip(v); } return o; } return n; };
console.log(JSON.stringify(strip(compile(process.argv[2]).physicalAst())));
```

**`$D/phys.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/bootstrap.php';
$p = Sel\Sel::compile($argv[1]);
function strip($n) { if (is_array($n)) { $o = []; foreach ($n as $k => $v) { if ($k === 'pos' || $k === 'spec') continue; $o[$k] = strip($v); } return $o; } return $n; }
echo json_encode(strip($p->physicalAst())), "\n";
```

**`$D/plan.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const bindings = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
const strip = (n) => { if (Array.isArray(n)) return n.map(strip); if (n && typeof n === 'object') { const o = {}; for (const [k, v] of Object.entries(n)) { if (k === 'pos' || k === 'spec') continue; o[k] = strip(v); } return o; } return n; };
const plan = Sql.planHybrid(compile(process.argv[2]), 'postgresql', bindings);
console.log('SQL:', plan.sqlStatement && plan.sqlStatement.asStatement('params'));
console.log('CONT:', JSON.stringify(strip(plan.continuationAst)));
console.log('PHYS:', JSON.stringify(strip(plan.continuationProgram.physicalAst())));
```

**`$D/sqlite_probe.py`**

```python
import sys, sqlite3
sys.path.insert(0, '/home/nathan/workspaces/nth-share/sel/python')
from sel import compile as sel_compile, SelError
from sel.sql import Sql, Binding
bindings = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}
rows = [{'id': '1'}, {'id': '2'}]
db = sqlite3.connect(':memory:'); db.execute('create table orders(id)'); db.executemany('insert into orders values (?)', [(r['id'],) for r in rows])
def runner(sql, params):
    print('  SQL:', sql)
    cur = db.execute(sql, [p.as_text() for p in params]); cols = [c[0] for c in cur.description]
    out = [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]; print('  ROWS:', out); return out
def fail(fn):
    try:
        v = fn(); return v.dump() if hasattr(v, 'dump') else repr(v)
    except SelError as e:
        return f'{e.code}@{e.line}:{e.col}'
for src in sys.argv[1:]:
    p = sel_compile(src); plan = Sql.plan_hybrid(p, 'sqlite', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    print(src); print('  kind', kind, ' run=', fail(lambda: p.run({'ORDERS': rows})))
    print('  hybrid=', fail(lambda: Sql.execute_hybrid(plan, runner, {'ORDERS': rows})))
```

**`$D/probe.cpp`**

```cpp
#include "sel.hpp"
#include "sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main(int argc, char** argv) {
  const Bindings bindings({{"ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)}})}});
  const sel::Value rows = sel::Value::list({sel::Value::record({"id"}, {sel::Value::text("1")}), sel::Value::record({"id"}, {sel::Value::text("2")})});
  auto failure = [](const auto& fn) -> std::string { try { return fn().dump(); } catch (const sel::SelError& e) { return e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); } };
  for (int i = 1; i < argc; i++) {
    const sel::Program program = sel::compile(argv[i]);
    const auto plan = Sql::plan_hybrid(program, "postgresql", bindings);
    const std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    sel::Value context = sel::Value::none(); context.set("ORDERS", rows);
    std::cout << "cpp  " << kind << "  run=" << failure([&] { sel::Value c = context.clone(); return program.run(c); })
              << "  hybrid=" << failure([&] { return Sql::execute_hybrid(plan, [&](const std::string&, const std::vector<sel::Value>&) { return rows; }, context); }) << "\n";
  }
}
```

### Commands run and their output

Run the reported repros in all five hosts

```bash
for p in 'O = LIST(RECORD("id", 1), RECORD("id", 2)); O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> COUNT()' 'COUNT(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))' '(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))[0]["b"]' 'LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3))'; do echo "=== $p"; echo -n "js:   "; node js/bin/sel.mjs -e "$p" 2>&1; echo -n "php:  "; php php/bin/sel -e "$p" 2>&1; echo -n "cpp:  "; cpp/build/sel -e "$p" 2>&1; echo -n "lisp: "; lisp/bin/sel -e "$p" 2>&1; echo -n "py:   "; PYTHONPATH=$PWD/python python3 -m sel -e "$p" 2>&1; done
```

```
Exit code 1
=== O = LIST(RECORD("id", 1), RECORD("id", 2)); O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> COUNT()
js:   2
php:  2
cpp:  E_ABORT at line 1 column 90: no
lisp: 2
py:   2
=== COUNT(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))
js:   1
php:  1
cpp:  E_DIV_ZERO at line 1 column 35: division by zero
lisp: 1
py:   1
=== (LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))[0]["b"]
js:   E_NO_KEY at line 1 column 53: no key "0"
php:  E_NO_KEY at line 1 column 53: no key "0"
cpp:  E_DIV_ZERO at line 1 column 30: division by zero
lisp: E_NO_KEY at line 1 column 53: no key "0"
py:   E_NO_KEY at line 1 column 53: no key "0"
=== LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3))
js:   E_DIV_ZERO at line 1 column 29: division by zero
php:  E_DIV_ZERO at line 1 column 29: division by zero
cpp:  E_DIV_ZERO at line 1 column 29: division by zero
lisp: E_DIV_ZERO at line 1 column 29: division by zero
py:   E_DIV_ZERO at line 1 column 29: division by zero
```

Export the pre-commit tree into the scratchpad

```bash
S=$SCRATCH/verify-R/old && mkdir -p $S && git archive 8fe0e3a | tar -x -C $S && ls $S && grep -n '^sel\b\|^build/sel:' $S/cpp/Makefile | head
```

```
CHANGELOG.md
composer.json
conformance
cpp
docs
examples
js
LICENSE
lisp
package.json
PACKAGING.md
php
pyproject.toml
python
README.md
spec
sql
tools
```

Run repros against the pre-commit tree

```bash
cd $SCRATCH/verify-R/old && for p in 'COUNT(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))' 'O = LIST(RECORD("id", 1), RECORD("id", 2)); O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> COUNT()'; do echo "=== OLD 8fe0e3a: $p"; echo -n "js:  "; node js/bin/sel.mjs -e "$p" 2>&1; echo -n "php: "; php php/bin/sel -e "$p" 2>&1; echo -n "cpp: "; cpp/build/sel -e "$p" 2>&1; echo -n "py:  "; PYTHONPATH=$PWD/python python3 -m sel -e "$p" 2>&1; done
```

```
=== OLD 8fe0e3a: COUNT(LIST(1) .> MAP(RECORD("a", 1/0, "b", "k", "c", 3)))
js:  1
php: 1
cpp: E_DIV_ZERO at line 1 column 35: division by zero
py:  1
=== OLD 8fe0e3a: O = LIST(RECORD("id", 1), RECORD("id", 2)); O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> COUNT()
js:  2
php: 2
cpp: E_ABORT at line 1 column 90: no
py:  2
```

Write JS, Python and PHP probe scripts and check PHP API names

```bash
D=$SCRATCH/verify-R && mkdir -p $D && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
grep -n 'public.*\$code\|public.*\$line\|public.*\$col\|function getCode\|readonly' php/src/SelError.php | head; grep -n 'pureSql\|pureMemory' php/src/Sql/HybridPlan.php | head -3; grep -n 'static function planHybrid\|static function executeHybrid' php/src/Sql/Sql.php
```

```
22:    public $code;
23:    public int $line;
24:    public int $col;
28:    public function __construct(string $code, string $message, ?array $pos = null)
ugrep: warning: php/src/Sql/HybridPlan.php: No such file or directory
93:    public static function planHybrid(Program $program, string $dialect,
100:    public static function executeHybrid(HybridPlan $plan, callable $dbRunner, $context = null): mixed
```

Run own probes in JS, Python and PHP across both lanes

```bash
D=$SCRATCH/verify-R && P1='ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"] * 2, "bad", _["missing"])) .> SUM(_["v"])' && P2='ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", UPPER(1))) .> FILTER(_["v"] > 1) .> MAP(_["v"])' && P3='ORDERS .> MAP(RECORD("v", _["id"], "bad", 1/0)) .> COUNT()' && for p in "$P1" "$P2" "$P3"; do echo "=== $p"; node $D/probe.mjs "$p"; PYTHONPATH=$PWD/python python3 $D/probe.py "$p"; php $D/probe.php "$p"; done
```

```
=== ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"] * 2, "bad", _["missing"])) .> SUM(_["v"])
js   pure_memory  run=t"6"  hybrid=t"6"
py   pure_memory  run=t"6"  hybrid=t"6"
php  pure_memory  run=t"6"  hybrid=t"6"
=== ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", UPPER(1))) .> FILTER(_["v"] > 1) .> MAP(_["v"])
js   pure_sql  run=-{"1"=t"2"}  hybrid=[{"id":"1"},{"id":"2"}]
py   pure_sql  run=-{"1"=t"2"}  hybrid=[{'id': '1'}, {'id': '2'}]
php  pure_sql  run=-{"1"=t"2"}  hybrid=[{"id":"1"},{"id":"2"}]
=== ORDERS .> MAP(RECORD("v", _["id"], "bad", 1/0)) .> COUNT()
js   pure_memory  run=t"2"  hybrid=t"2"
py   pure_memory  run=t"2"  hybrid=t"2"
php  pure_memory  run=t"2"  hybrid=t"2"
```

Run hybrid-shaped probes in JS, Python and PHP

```bash
D=$SCRATCH/verify-R && P4='ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> COUNT()' && P5='ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> MAP(_["v"])' && for p in "$P4" "$P5"; do echo "=== $p"; node $D/probe.mjs "$p"; PYTHONPATH=$PWD/python python3 $D/probe.py "$p"; php $D/probe.php "$p"; done
```

```
=== ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> COUNT()
js   pure_memory  run=t"2"  hybrid=t"2"
py   pure_memory  run=t"2"  hybrid=t"2"
php  pure_memory  run=t"2"  hybrid=t"2"
=== ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> MAP(_["v"])
js   hybrid  run=-{"1"=t"2"}  hybrid=E_ABORT@1:60
py   hybrid  run=-{"1"=t"2"}  hybrid=E_ABORT@1:60
php  hybrid  run=E_ABORT@1:60  hybrid=E_ABORT@1:60
```

Dump the physical tree in JS and PHP for the diverging program

```bash
D=$SCRATCH/verify-R && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
P5='ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> MAP(_["v"])'
node $D/phys.mjs "$P5"; echo; php $D/phys.php "$P5" 2>&1 | head -5; grep -n 'function physicalAst' php/src/Sel.php
```

```
{"t":"call","name":"MAP","args":[{"t":"call","name":"FILTER","args":[{"t":"call","name":"MAP","args":[{"t":"call","name":"TAKE","args":[{"t":"var","name":"ORDERS"},{"t":"num","v":"2"}]},{"t":"call","name":"LAZY_RECORD","args":[{"t":"text","v":"v"},{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"id"}},{"t":"text","v":"bad"},{"t":"call","name":"ABORT","args":[{"t":"text","v":"x"}]}]}]},{"t":"bin","op":">","l":{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"v"}},"r":{"t":"num","v":"1"}}]},{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"v"}}]}

{"t":"call","name":"MAP","args":[{"t":"call","name":"FILTER","args":[{"t":"call","name":"MAP","args":[{"t":"call","name":"TAKE","args":[{"t":"var","name":"ORDERS"},{"t":"num","v":"2"}]},{"t":"call","name":"LAZY_RECORD","args":[{"t":"text","v":"v"},{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"id"}},{"t":"text","v":"bad"},{"t":"call","name":"ABORT","args":[{"t":"text","v":"x"}]}]}]},{"t":"bin","op":">","l":{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"v"}},"r":{"t":"num","v":"1"}}]},{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"v"}}]}
84:    public function physicalAst(): array
```

Run FILTER-after-MAP shapes in all five REPLs

```bash
run5() { echo "=== $1"; echo -n "js:   "; node js/bin/sel.mjs -e "$1" 2>&1; echo -n "php:  "; php php/bin/sel -e "$1" 2>&1; echo -n "cpp:  "; cpp/build/sel -e "$1" 2>&1; echo -n "lisp: "; lisp/bin/sel -e "$1" 2>&1; echo -n "py:   "; PYTHONPATH=$PWD/python python3 -m sel -e "$1" 2>&1; }; run5 'O = LIST(RECORD("id", 1), RECORD("id", 2)); O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> FILTER(_["id"] > 1) .> MAP(_["id"])'; run5 'O = LIST(RECORD("id", 1), RECORD("id", 2)); O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> FILTER(_["NOTE"] == "x")'; run5 'LIST(RECORD("id", 1), RECORD("id", 2)) .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> MAP(_["v"])'; run5 'LIST(RECORD("id", 1), RECORD("id", 2)) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1)'; run5 'LIST(RECORD("id", 1), RECORD("id", 2)) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()'
```

```
=== O = LIST(RECORD("id", 1), RECORD("id", 2)); O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> FILTER(_["id"] > 1) .> MAP(_["id"])
js:   -{"1"=t"2"}
php:  -{"1"=t"2"}
cpp:  E_ABORT at line 1 column 90: no
lisp: -{"1"=t"2"}
py:   -{"1"=t"2"}
=== O = LIST(RECORD("id", 1), RECORD("id", 2)); O .> MAP(RECORD("id", _["id"], "note", ABORT("no"))) .> FILTER(_["NOTE"] == "x")
js:   E_NO_KEY at line 1 column 109: no key "NOTE"
php:  E_NO_KEY at line 1 column 109: no key "NOTE"
cpp:  E_ABORT at line 1 column 90: no
lisp: E_NO_KEY at line 1 column 109: no key "NOTE"
py:   E_NO_KEY at line 1 column 109: no key "NOTE"
=== LIST(RECORD("id", 1), RECORD("id", 2)) .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> MAP(_["v"])
js:   -{"1"=t"2"}
php:  E_ABORT at line 1 column 92: x
cpp:  E_ABORT at line 1 column 92: x
lisp: -{"1"=t"2"}
py:   -{"1"=t"2"}
=== LIST(RECORD("id", 1), RECORD("id", 2)) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1)
js:   E_ABORT at line 1 column 81: x
php:  E_ABORT at line 1 column 81: x
cpp:  E_ABORT at line 1 column 81: x
lisp: E_ABORT at line 1 column 81: x
py:   E_ABORT at line 1 column 81: x
=== LIST(RECORD("id", 1), RECORD("id", 2)) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()
js:   1
php:  E_ABORT at line 1 column 81: x
cpp:  E_ABORT at line 1 column 81: x
lisp: 1
py:   1
```

Dump the JS hybrid plan continuation and its physical tree

```bash
D=$SCRATCH/verify-R && # (file written, see "Reproduction scripts" above)
node $D/plan.mjs 'ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> MAP(_["v"])'
```

```
SQL: SELECT "_sub2"."v" FROM (SELECT "_sub1"."id" AS "v" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2" WHERE (CASE WHEN (CAST("_sub2"."v" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("_sub2"."v" AS NUMERIC) ELSE NULL END > 1)
CONT: {"t":"call","name":"MAP","args":[{"t":"var","name":"_INPUT"},{"t":"call","name":"RECORD","args":[{"t":"text","v":"v"},{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"id"}},{"t":"text","v":"bad"},{"t":"call","name":"ABORT","args":[{"t":"text","v":"x"}]}]}]}
PHYS: {"t":"call","name":"MAP","args":[{"t":"var","name":"_INPUT"},{"t":"call","name":"LAZY_RECORD","args":[{"t":"text","v":"v"},{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"id"}},{"t":"text","v":"bad"},{"t":"call","name":"ABORT","args":[{"t":"text","v":"x"}]}]}]}
```

Probe hybrid plans whose continuation keeps the MAP and yields a scalar

```bash
D=$SCRATCH/verify-R && for p in 'ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> SUM(_["v"])' 'ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()' 'ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> MAP(_["v"])'; do echo "=== $p"; node $D/plan.mjs "$p" | grep -v PHYS; node $D/probe.mjs "$p"; PYTHONPATH=$PWD/python python3 $D/probe.py "$p"; php $D/probe.php "$p"; done
```

```
=== ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> SUM(_["v"])
SQL: null
CONT: {"t":"call","name":"SUM","args":[{"t":"call","name":"MAP","args":[{"t":"call","name":"FILTER","args":[{"t":"var","name":"ORDERS"},{"t":"bin","op":">","l":{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"id"}},"r":{"t":"num","v":"0"}}]},{"t":"call","name":"RECORD","args":[{"t":"text","v":"v"},{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"id"}},{"t":"text","v":"bad"},{"t":"call","name":"ABORT","args":[{"t":"text","v":"x"}]}]}]},{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"v"}}]}
js   pure_memory  run=t"3"  hybrid=t"3"
py   pure_memory  run=t"3"  hybrid=t"3"
php  pure_memory  run=t"3"  hybrid=t"3"
=== ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()
SQL: null
CONT: {"t":"call","name":"COUNT","args":[{"t":"call","name":"FILTER","args":[{"t":"call","name":"MAP","args":[{"t":"call","name":"FILTER","args":[{"t":"var","name":"ORDERS"},{"t":"bin","op":">","l":{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"id"}},"r":{"t":"num","v":"0"}}]},{"t":"call","name":"RECORD","args":[{"t":"text","v":"v"},{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"id"}},{"t":"text","v":"bad"},{"t":"call","name":"ABORT","args":[{"t":"text","v":"x"}]}]}]},{"t":"bin","op":">","l":{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"v"}},"r":{"t":"num","v":"1"}}]}]}
js   pure_memory  run=t"1"  hybrid=t"1"
py   pure_memory  run=t"1"  hybrid=t"1"
php  pure_memory  run=E_ABORT@1:72  hybrid=E_ABORT@1:72
=== ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> MAP(_["v"])
SQL: SELECT "_sub2"."v" FROM (SELECT "_sub1"."id" AS "v" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2"
CONT: {"t":"call","name":"MAP","args":[{"t":"var","name":"_INPUT"},{"t":"call","name":"RECORD","args":[{"t":"text","v":"v"},{"t":"index","obj":{"t":"var","name":"_"},"idx":{"t":"text","v":"id"}},{"t":"text","v":"bad"},{"t":"call","name":"ABORT","args":[{"t":"text","v":"x"}]}]}]}
js   hybrid  run=-{"1"=t"1", "2"=t"2"}  hybrid=E_ABORT@1:60
py   hybrid  run=-{"1"=t"1", "2"=t"2"}  hybrid=E_ABORT@1:60
php  hybrid  run=-{"1"=t"1", "2"=t"2"}  hybrid=E_ABORT@1:60
```

Execute the suspicious hybrid plan against sqlite

```bash
D=$SCRATCH/verify-R && # (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $D/sqlite_probe.py 'ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> MAP(_["v"])' 'ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", _["id"] + 1)) .> MAP(_["v"])'
```

```
ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> MAP(_["v"])
  kind hybrid  run= -{"1"=t"1", "2"=t"2"}
  SQL: SELECT "_sub2"."v" FROM (SELECT "_sub1"."id" AS "v" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1") "_sub2"
  ROWS: [{'v': '1'}, {'v': '2'}]
  hybrid= E_NO_KEY@1:39
ORDERS .> TAKE(2) .> MAP(RECORD("v", _["id"], "bad", _["id"] + 1)) .> MAP(_["v"])
  kind hybrid  run= -{"1"=t"1", "2"=t"2"}
  SQL: SELECT "o".* FROM "orders" "o" LIMIT 2
  ROWS: [{'id': '1'}, {'id': '2'}]
  hybrid= -{"1"=t"1", "2"=t"2"}
```

Check the FILTER shape on the pre-commit tree and find when LAZY_RECORD arrived in JS

```bash
cd $SCRATCH/verify-R/old && p='LIST(RECORD("id", 1), RECORD("id", 2)) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()'; echo "=== OLD: $p"; echo -n "js:  "; node js/bin/sel.mjs -e "$p" 2>&1; echo -n "php: "; php php/bin/sel -e "$p" 2>&1; echo -n "cpp: "; cpp/build/sel -e "$p" 2>&1; echo -n "py:  "; PYTHONPATH=$PWD/python python3 -m sel -e "$p" 2>&1; cd /home/nathan/workspaces/nth-share/sel; git log --oneline -1 --format='%h %s' $(git log --format=%h -S'LAZY_RECORD' -- js/src/optimizer.mjs | tail -1)
```

```
=== OLD: LIST(RECORD("id", 1), RECORD("id", 2)) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()
js:  1
php: E_ABORT at line 1 column 81: x
cpp: E_ABORT at line 1 column 81: x
py:  1
8fe0e3a wip
```

Build and run the C++ probe in both lanes

```bash
D=$SCRATCH/verify-R && # (file written, see "Reproduction scripts" above)
g++ -std=c++23 -O1 -I cpp -o $D/probe_cpp $D/probe.cpp cpp/build/sel_sql*.o cpp/build/sel.o 2>&1 | grep -v warning | head -5; $D/probe_cpp 'ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> SUM(_["v"])' 'ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()'
```

```
cpp  pure_memory  run=E_ABORT@1:72  hybrid=E_ABORT@1:72
cpp  pure_memory  run=E_ABORT@1:72  hybrid=E_ABORT@1:72
```

Run my two probe programs in all five REPLs

```bash
run5() { echo "=== $1"; echo -n "js:   "; node js/bin/sel.mjs -e "$1" 2>&1; echo -n "php:  "; php php/bin/sel -e "$1" 2>&1; echo -n "cpp:  "; cpp/build/sel -e "$1" 2>&1; echo -n "lisp: "; lisp/bin/sel -e "$1" 2>&1; echo -n "py:   "; PYTHONPATH=$PWD/python python3 -m sel -e "$1" 2>&1; }; run5 'ORDERS = LIST(RECORD("id", 1), RECORD("id", 2)); ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> SUM(_["v"])'; run5 'ORDERS = LIST(RECORD("id", 1), RECORD("id", 2)); ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()'
```

```
=== ORDERS = LIST(RECORD("id", 1), RECORD("id", 2)); ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> SUM(_["v"])
js:   3
php:  3
cpp:  E_ABORT at line 1 column 121: x
lisp: 3
py:   3
=== ORDERS = LIST(RECORD("id", 1), RECORD("id", 2)); ORDERS .> FILTER(_["id"] > 0) .> MAP(RECORD("v", _["id"], "bad", ABORT("x"))) .> FILTER(_["v"] > 1) .> COUNT()
js:   1
php:  E_ABORT at line 1 column 121: x
cpp:  E_ABORT at line 1 column 121: x
lisp: 1
py:   1
```

