# C2. MAP/SORT swap in the logical optimiser changes the program value; runs inside translate() in JS/Python only

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "The optimiser keeps the program's value". The translate-lane difference (which hosts optimise inside `translate`) is finding C and stays open; the `_K`-after-the-projection SQL the suggested statement case would have pinned is finding K and stays open, so the SQL-side pin is `stmt.map.computed-field-then-keyless-sort` instead.

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

Both facets reproduce. (1) translate()/translateStatement() run the logical optimiser in JS and Python only (PHP and C++ never; Lisp only in translate-statement), so `ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)` is E_SQL_SHAPE 1:31 in JS/Python/Lisp-translate-statement and `SELECT ... GROUP BY dept ORDER BY dept ASC` in PHP/C++/Lisp-translate. (2) The MAP/SORT swap rule, identical in all five optimisers, fires whenever the sort key has no `_["field"]` reference and is not value-preserving: `LIST(3, 1, 2) .> MAP(0 - _) .> SORT()` answers -1,-2,-3 (not even sorted) from run() in every host while the unoptimised evaluator answers -3,-2,-1, and the bucket shape answers cat="1","2" instead of the group keys; the hybrid lane inherits the same wrong value. Everything involved pre-dates the commit, but it breaks the commit's own written promise that the optimiser is invisible and that a program answers the same whichever lane runs it.

## Suggested fix — case first

Smallest fix: in all five optimisers drop the "no field references" branch of the MAP/SORT swap — swap only when the sort key's field set is non-empty and every field is a MAP pass-through (and the key does not read _K or the bare binder); a keyless SORT()/SORT_DESC()/TOP(n) and SORT_BY(_K) must stay after the MAP. Then make translate()/translate_statement() agree: either none of the five run the logical optimiser inside translate (PHP/C++ today) or all do with the same options — currently JS/Python/Lisp-translate-statement do and PHP/C++/Lisp-translate do not, and the option is undocumented. Cases to add first: conformance `LIST(3, 1, 2) .> MAP(0 - _) .> SORT()` => -{"1"=t"-3", "2"=t"-2", "3"=t"-1"} and `LIST(RECORD("cat","B"), RECORD("cat","A"), RECORD("cat","B")) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT_BY(_K)` => cat A/B; then a .sqlt statement case for `ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)` expecting `SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC` (currently refused by JS/Python), plus a 25-hybrid-plans case pinning its classification as pure_sql.

## Verifier reasoning

Facet 1 (translate lanes differ across hosts): js/src/sql/translator.mjs:149-150 and :173-174 call `optimizeAstLogical(ast, {foldConstants:false, fuseFilters:false})` before normalise; python/sel/sql/translator.py:165-167 and :189-191 do the same; php/src/Sql/Translator.php:70-91 and :94-109 go straight from Normalise::run to analyzePipeline; cpp/sel_sql_translator.cpp:115-146 and :148-166 likewise never optimise; lisp/src/sql/translator.lisp:2340-2360 (`translate`) does not optimise but :2372-2388 (`translate-statement`) runs the full `sel:optimize-ast-logical` unless `:no-optimize`. Nothing in docs/SQL-TRANSLATION.md documents that translate() optimises (only `plan_hybrid` is documented to, §12.1). The swap seals the bucket (SORT lands between BUCKET and MAP), so the hosts that optimise refuse at the MAP and the others emit SQL. The `noOptimize` option confirms the mechanism: with it, JS/Python/Lisp emit the same string as PHP/C++.

Facet 2 (the swap changes the value): the rule at js/src/optimizer.mjs:338-347, php/src/Optimizer.php:376-384, python/sel/optimizer.py:392-400, cpp/sel.cpp:5594-5604, lisp/src/optimizer.lisp:639-652 swaps `MAP(computed) .> SORT*` when `refs` (the `_["field"]` names in the key) is EMPTY or all pass-through. The empty branch is unsound: a keyless SORT()/SORT_DESC()/TOP(n) compares the MAP's outputs, and SORT_BY(_K) keys on the MAP input's keys, which SORT renumbers to "1","2",... before MAP reads `_K`. docs/interim/sel_porting_worklist_and_checklist.md:159 describes the intended rule as "when the key uses only pass-through fields" — the no-key case was never meant to be included. All five run() lanes evaluate the optimised tree (Program.physicalAst / physical_ast / optimize_ast) so all five agree on the wrong value; a scratch .selt with the unoptimised expectations fails identically on all five conformance runners. The hybrid planner (which optimises before unwinding in every host) classifies the bucket shape as pure_memory and executes the same wrong value; PHP/C++/Lisp-translate's SQL (`ORDER BY dept`) would return the correct group keys — so those hosts disagree with themselves across lanes.

Pre-existing: `git show 8fe0e3a:` shows the rule (mapHasComputedFields / map_has_computed_fields / opt_map_has_computed / map-has-computed-fields-p) in all five optimisers, run() already optimising in all five, JS/Python translate already calling optimizeAstLogical (:150/:174, :165/:189), and Lisp translate-statement already optimising. Severity high: differing results across hosts (translate refuse vs SQL), across lanes (SQL vs memory in PHP/C++/Lisp), and a wrong in-memory value in every host — contradicting docs/EXTENDING.md "The optimiser is invisible" and §12.1 "run it, plan it, run it again, and it answers the same" (the latter concerns reuse, but the value itself is wrong relative to spec SORT semantics and the unoptimised evaluator).

## Verifier evidence

```
Probe dir: /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-C2/

[translate lanes] P='ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)', bindings ITEMS=relation items {DEPT: column dept TEXT}, dialect mariadb:
 node tr.mjs "$P" ->
  JS translate null: E_SQL_SHAPE 1:31 a MAP over buckets must follow the BUCKET...
  JS translate {"noOptimize":true}: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
  JS translateStatement null: E_SQL_SHAPE 1:31 ...
  JS plan: pure_memory
 PYTHONPATH=$PWD/python python3 tr.py "$P" -> PY translate None: E_SQL_SHAPE 1:31 ...; with noOptimize: same SELECT as above
 php tr.php "$P" -> PHP translate []: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC (translateStatement same); PHP plan: pure_memory
 g++ -std=c++23 -I cpp tr.cpp cpp/build/sel_sql_*.o cpp/build/sel.o; ./tr "$P" -> CPP translate: SELECT ... ORDER BY `dept` ASC; CPP translate_statement: same; CPP plan: pure_memory
 sbcl --load lisp/bin/boot.lisp --load tr.lisp "$P" -> LISP TRANSLATE NIL: SELECT ... ORDER BY `dept` ASC; LISP TRANSLATE-STATEMENT NIL: E_SQL_SHAPE 1:31 ...; LISP TRANSLATE-STATEMENT (:NO-OPTIMIZE T): SELECT ...

[value change, reported repro] all five REPLs (-e) on 'LIST(RECORD("cat","B","v",1), RECORD("cat","A","v",2), RECORD("cat","B","v",3)) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT()' -> -{"1"=-{"cat"=t"1", "n"=t"2"}, "2"=-{"cat"=t"2", "n"=t"1"}} (JS, PHP, CPP, LISP, PY identical). node probe_opt.mjs same program -> raw evalNode: -{"1"=-{"cat"=t"B", "n"=t"2"}, "2"=-{"cat"=t"A", "n"=t"1"}}; steps raw [BUCKET,MAP,SORT], logical [BUCKET,SORT,MAP].

[value change, my own non-bucket repro] '(3, 1, 2) .> MAP(0 - _) .> SORT()' -> JS/PHP/CPP/LISP/PY all: -{"1"=t"-1", "2"=t"-2", "3"=t"-3"}; raw evalNode (JS): -{"1"=t"-3", "2"=t"-2", "3"=t"-1"}. '(3, 1, 2) .> MAP(0 - _) .> TOP(1)' -> all five: -{"1"=t"-1"}; raw: -{"1"=t"-3"}.
 Scratch 99-probe.selt (cases probe.map-then-sort.negation expecting -3,-2,-1 and probe.bucket-map-sort-by-key expecting cat=A/B) run through node js/bin/conformance.mjs, php php/bin/conformance, cpp/build/conformance, lisp/bin/conformance, python/bin/conformance.py -> every host: "0 passed, 2 failed", got tree -{"1"=t"-1", "2"=t"-2", "3"=t"-3"} and -{"1"=-{"cat"=t"1",...},"2"=-{"cat"=t"2",...}}.

[three lanes, Python over sqlite] lanes.py 'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> SORT_BY(_K)' -> raw eval cid=7/9; run() cid=1/2; plan pure_memory; hybrid cid=1/2; translate_statement E_SQL_SHAPE at 1:39. 'ORDERS .> MAP(0 - _["amount"]) .> SORT()' -> raw -10,-7,-5; run() -10,-5,-7; hybrid -10,-5,-7.

[code] js/src/sql/translator.mjs:149-150,173-174; python/sel/sql/translator.py:165-167,189-191; php/src/Sql/Translator.php:70-109 (no optimiser call); cpp/sel_sql_translator.cpp:115-166 (none); lisp/src/sql/translator.lisp:2340-2360 vs 2372-2388. Swap rule: js/src/optimizer.mjs:338-347 (`refs.length === 0 || ...`), php/src/Optimizer.php:376-384 (`$refs === [] || ...`), python/sel/optimizer.py:392-400 (`not refs or ...`), cpp/sel.cpp:5594-5604 (`refs.empty() || ...`), lisp/src/optimizer.lisp:639-652 (`(or (null s-fields) ...)`). Intended rule: docs/interim/sel_porting_worklist_and_checklist.md:159 "when the key uses only pass-through fields".

[pre-existing] git show 8fe0e3a:js/src/optimizer.mjs lines 343-347 has the same rule; 8fe0e3a:php/src/Optimizer.php:355, python/sel/optimizer.py:395, lisp/src/optimizer.lisp:619, cpp/sel.cpp:5584 all have it; 8fe0e3a:js/src/sel.mjs:21 `evalNode(optimizeAst(this.ast))`, php/src/Sel.php:48, python/sel/__init__.py:50, cpp/sel.cpp:5871, lisp/src/sel.lisp:21 all ran the optimiser in run(); 8fe0e3a:js/src/sql/translator.mjs:150,174 and python/sel/sql/translator.py:165,189 already optimised in translate; 8fe0e3a lisp translate-statement already called optimize-ast-logical.
```

## Original review reports (deduplicated into this finding)

### [bucket-translator] translate() disagrees across hosts on BUCKET(k) .> MAP(RECORD(.., _K, ..)) .> SORT_BY(_K): the logical optimiser's MAP/SORT swap runs inside translate() in JS/Python only, and the swap itself changes the program's value

*coherence · high · hosts: js, php, python, cpp, lisp*

Locations: `js/src/sql/translator.mjs:149`; `js/src/sql/translator.mjs:173`; `python/sel/sql/translator.py:165`; `python/sel/sql/translator.py:189`; `lisp/src/sql/translator.lisp:2347`; `lisp/src/sql/translator.lisp:2382`; `js/src/optimizer.mjs:338`; `php/src/Optimizer.php:377`; `python/sel/optimizer.py:393`; `cpp/sel.cpp:5594`; `lisp/src/optimizer.lisp:633`

Two divergences in one shape. (1) JS and Python run optimizeAstLogical inside translate()/translateStatement(); PHP and C++ never do; Lisp's translate does not but translate-statement runs the full optimiser (with constant folding and filter fusion that JS/Python disable). The MAP/SORT swap rule (identical in all five) fires when the sort key has no field references — SORT() or SORT_BY(_K) — and moves the SORT before the bucket's MAP, which seals the bucket, so JS/Python refuse E_SQL_SHAPE at the MAP while PHP/C++/Lisp emit ORDER BY dept. (2) The swap is not value-preserving: the MAP body reads _K, and the SORT step turns the keyed tree into a renumbered list, so after the swap _K is '1','2' instead of the group keys — run() answers the wrong value in every host (they agree with each other, and disagree with the unoptimised evaluator). The fold's premise ('one value in the evaluator') is therefore broken by the optimiser the planner runs first.

Reported repro:

```
Scratch case 95-probe4.sqlt e12 / 97-probe2.sqlt r02: ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K) — JS, Python: E_SQL_SHAPE 1:31 (a MAP over buckets must follow the BUCKET...); PHP, C++, Lisp: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC. node probe_opt.mjs 'LIST(RECORD("cat","B","v",1), RECORD("cat","A","v",2), RECORD("cat","B","v",3)) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT()': raw evalNode -{"1"=-{"cat"=t"B","n"=t"2"}, "2"=-{"cat"=t"A","n"=t"1"}}; optimizeAstLogical (steps BUCKET > SORT > MAP) and Program.run in all five REPLs: -{"1"=-{"cat"=t"1","n"=t"2"}, "2"=-{"cat"=t"2","n"=t"1"}}.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$D/probe_opt.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile, optimizeAstLogical, optimizeAst, Value } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { evalNode, Context } from '/home/nathan/workspaces/nth-share/sel/js/src/eval.mjs';
import { format } from '/home/nathan/workspaces/nth-share/sel/js/src/value.mjs';
const src = process.argv[2];
const p = compile(src);
const raw = evalNode(p.ast, new Context(Value.fromNative({})));
const opt = p.run({});
const steps = (ast) => { const out=[]; let n=ast; while (n && n.t==='call' && n.name!==undefined) { out.unshift(n.name); n = n.args && n.args[0]; } return out; };
console.log('raw  :', raw.toString ? raw.toString() : JSON.stringify(raw));
console.log('run  :', opt.toString ? opt.toString() : JSON.stringify(opt));
console.log('steps raw:', JSON.stringify(steps(p.ast)), ' logical:', JSON.stringify(steps(optimizeAstLogical(p.ast))));
```

**`$D/tr.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const src = process.argv[2];
const b = { ITEMS: Binding.relation('items', null, { DEPT: Binding.column('dept', null, 'TEXT') }, null, null) };
for (const [label, fn] of [['translate', 'translate'], ['translateStatement', 'translateStatement']]) {
  for (const opts of [null, { noOptimize: true }]) {
    try { const f = Sql[fn](compile(src), 'mariadb', b, opts); console.log(`JS ${label} ${JSON.stringify(opts)}: ${f.sql()}`); }
    catch (e) { console.log(`JS ${label} ${JSON.stringify(opts)}: ${e.code} ${e.line ?? ''}:${e.col ?? ''} ${e.message}`); }
  }
}
```

**`$D/tr.py`**

```python
import sys
sys.path.insert(0, '/home/nathan/workspaces/nth-share/sel/python')
from sel import compile
from sel.sql import Sql, Binding
src = sys.argv[1]
b = {"ITEMS": Binding.relation("items", None, {"DEPT": Binding.column("dept", None, "TEXT")}, None, None)}
for label in ('translate', 'translate_statement'):
    for opts in (None, {'noOptimize': True}):
        try:
            f = getattr(Sql, label)(compile(src), 'mariadb', b, opts)
            print(f"PY {label} {opts}: {f.sql()}")
        except Exception as e:
            print(f"PY {label} {opts}: {getattr(e,'code',type(e).__name__)} {getattr(e,'line','')}:{getattr(e,'col','')} {e}")
```

**`$D/tr.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$src = $argv[1];
$b = ['ITEMS' => Binding::relation('items', null, ['DEPT' => Binding::column('dept', null, 'TEXT')], null, null)];
foreach (['translate', 'translateStatement'] as $fn) {
  foreach ([null, ['noOptimize' => true]] as $opts) {
    try { $f = Sql::$fn(Sel::compile($src), 'mariadb', $b, $opts); echo "PHP $fn " . json_encode($opts) . ": " . $f->sql() . "\n"; }
    catch (\Throwable $e) { echo "PHP $fn " . json_encode($opts) . ": " . ($e->code ?? get_class($e)) . " " . ($e->line ?? '') . ":" . ($e->col ?? '') . " " . $e->getMessage() . "\n"; }
  }
}
```

**`$D/tr.lisp`**

```lisp
(let ((*standard-output* (make-broadcast-stream)))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
(defun probe (src)
  (let ((b (list (cons "ITEMS" (sel.sql:binding-relation "items" nil (list (cons "DEPT" (sel.sql:binding-column "dept" nil :text))) nil nil)))))
    (dolist (fn '(sel.sql:translate sel.sql:translate-statement))
      (dolist (opts '(nil (:no-optimize t)))
        (handler-case
            (format t "LISP ~a ~s: ~a~%" fn opts (sel.sql:as-statement (funcall fn (sel:compile src) "mariadb" b opts)))
          (sel.sql:sql-error (e) (format t "LISP ~a ~s: ~a ~a:~a ~a~%" fn opts (sel.sql:sql-error-code e) (sel.sql:sql-error-line e) (sel.sql:sql-error-col e) (sel.sql:sql-error-message e))))))))
(probe (second sb-ext:*posix-argv*))
```

**`$D/tr.cpp`**

```cpp
#include "sel.hpp"
#include "sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main(int argc, char** argv) {
  const Bindings b({{"ITEMS", Binding::relation("items", std::nullopt, {{"DEPT", Binding::column("dept", std::nullopt, SqlKind::Text)}})}});
  const std::string src = argv[1];
  try { std::cout << "CPP translate: " << Sql::translate(sel::compile(src), "mariadb", b).as_statement() << "\n"; }
  catch (const sel::sql::SqlError& e) { std::cout << "CPP translate: " << e.code() << " " << e.line() << ":" << e.col() << " " << e.message() << "\n"; }
  try { std::cout << "CPP translate_statement: " << Sql::translate_statement(sel::compile(src), "mariadb", b).as_statement() << "\n"; }
  catch (const sel::sql::SqlError& e) { std::cout << "CPP translate_statement: " << e.code() << " " << e.line() << ":" << e.col() << " " << e.message() << "\n"; }
}
```

**`$D/lanes.py`**

```python
import sys, sqlite3
sys.path.insert(0, '/home/nathan/workspaces/nth-share/sel/python')
from sel import compile as sel_compile, Value
from sel.sql import Binding, Sql
from sel.evaluator import eval_node, Context
bindings = {'ORDERS': Binding.relation('orders', 'o', {
    'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM'),
    'AMOUNT': Binding.column('amount', 'o', 'NUM')})}
rows = [{'customer_id': '7', 'amount': '10'}, {'customer_id': '7', 'amount': '5'},
        {'customer_id': '9', 'amount': '7'}]
db = sqlite3.connect(':memory:')
db.execute('create table orders(customer_id, amount)')
db.executemany('insert into orders values (?, ?)', [(r['customer_id'], r['amount']) for r in rows])
def runner(sql, params):
    print('   SQL:', sql)
    cur = db.execute(sql, [p.as_text() for p in params])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]
for source in sys.argv[1:]:
    print('==', source)
    program = sel_compile(source)
    raw = eval_node(program.ast, Context(Value.from_native({'ORDERS': rows})))
    print('  raw eval :', raw.dump())
    print('  run()    :', program.run({'ORDERS': rows}).dump())
    plan = Sql.plan_hybrid(program, 'sqlite', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    print('  plan     :', kind)
    got = Sql.execute_hybrid(plan, runner, {'ORDERS': rows})
    got = got if isinstance(got, Value) else Value.from_native(got)
    print('  hybrid   :', got.dump())
    try:
        print('  translate:', Sql.translate_statement(program, 'sqlite', bindings).as_statement())
    except Exception as e:
        print('  translate:', e)
```

**`$D/99-probe.selt`**

```
### name: probe.map-then-sort.negation
--- source
LIST(3, 1, 2) .> MAP(0 - _) .> SORT()
--- expect
tree -{"1"=t"-3", "2"=t"-2", "3"=t"-1"}
===

### name: probe.bucket-map-sort-by-key
--- source
LIST(RECORD("cat","B"), RECORD("cat","A"), RECORD("cat","B")) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT_BY(_K)
--- expect
tree -{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"B", "n"=t"2"}}
===
```

### Commands run and their output

Check whether the optimiser-in-translate existed before the commit

```bash
for f in js/src/sql/translator.mjs python/sel/sql/translator.py php/src/Sql/Translator.php cpp/sel_sql_translator.cpp lisp/src/sql/translator.lisp; do echo "== $f"; git show 8fe0e3a:$f | grep -n "optimize_ast_logical\|optimizeAstLogical\|optimize-ast-logical\|Optimizer::optimize" ; done; echo; git diff 8fe0e3a ed16df2 --stat | tail -5
```

```
== js/src/sql/translator.mjs
26:import { optimizeAstLogical } from '../optimizer.mjs';
150:      ? optimizeAstLogical(ast, { foldConstants: false, fuseFilters: false }) : ast;
174:      ? optimizeAstLogical(ast, { foldConstants: false, fuseFilters: false }) : ast;
== python/sel/sql/translator.py
33:from ..optimizer import optimize_ast_logical
165:        source_ast = (optimize_ast_logical(ast, {'foldConstants': False,
189:        source_ast = (optimize_ast_logical(ast, {'foldConstants': False,
== php/src/Sql/Translator.php
== cpp/sel_sql_translator.cpp
== lisp/src/sql/translator.lisp
2347:                      (sel:optimize-ast-logical (sel:program-ast program))))

 tools/check-js-optimizer.mjs                       |   93 +
 tools/check-php-optimizer.php                      |  104 +
 tools/gen-sql-cases.mjs                            |   47 +-
 tools/impls.sh                                     |    6 +-
 72 files changed, 14697 insertions(+), 2466 deletions(-)
```

Run the reported SORT() repro in all five REPLs

```bash
mkdir -p $SCRATCH/verify-C2 && ls cpp/build/ | head -20; P='LIST(RECORD("cat","B","v",1), RECORD("cat","A","v",2), RECORD("cat","B","v",3)) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT()'; echo "JS:"; node js/bin/sel.mjs -e "$P"; echo "PHP:"; php php/bin/sel -e "$P"; echo "CPP:"; cpp/build/sel -e "$P"; echo "LISP:"; lisp/bin/sel -e "$P"; echo "PY:"; PYTHONPATH=$PWD/python python3 -m sel -e "$P"
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
JS:
-{"1"=-{"cat"=t"1", "n"=t"2"}, "2"=-{"cat"=t"2", "n"=t"1"}}
PHP:
-{"1"=-{"cat"=t"1", "n"=t"2"}, "2"=-{"cat"=t"2", "n"=t"1"}}
CPP:
-{"1"=-{"cat"=t"1", "n"=t"2"}, "2"=-{"cat"=t"2", "n"=t"1"}}
LISP:
-{"1"=-{"cat"=t"1", "n"=t"2"}, "2"=-{"cat"=t"2", "n"=t"1"}}
PY:
-{"1"=-{"cat"=t"1", "n"=t"2"}, "2"=-{"cat"=t"2", "n"=t"1"}}
```

Compare raw evalNode vs run() in JS

```bash
D=$SCRATCH/verify-C2 && # (file written, see "Reproduction scripts" above)
grep -n "^export" js/src/value.mjs | head; node $D/probe_opt.mjs 'LIST(RECORD("cat","B","v",1), RECORD("cat","A","v",2), RECORD("cat","B","v",3)) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT()'
```

```
Exit code 1
8:export const NONE = 'NONE';
9:export const TEXT = 'TEXT';
10:export const BIN = 'BIN';
11:export const BOOL = 'BOOL';
18:export class RecordShape {
49:export class Value {
550:export function structuralHash(value, depth = 1) {
612:export { quoteDump, toCodePoints, decodeUtf8 };
file://$SCRATCH/verify-C2/probe_opt.mjs:4
import { format } from '/home/nathan/workspaces/nth-share/sel/js/src/value.mjs';
         ^^^^^^
SyntaxError: The requested module '/home/nathan/workspaces/nth-share/sel/js/src/value.mjs' does not provide an export named 'format'
    at ModuleJob._instantiate (node:internal/modules/esm/module_job:180:21)
    at async ModuleJob.run (node:internal/modules/esm/module_job:263:5)
    at async onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:547:26)
    at async asyncRunEntryPointWithESMLoader (node:internal/modules/run_main:116:5)

Node.js v22.12.0
```

Fix the probe and rerun

```bash
D=$SCRATCH/verify-C2 && grep -n "dump\|toString" js/bin/sel.mjs | head -5; sed -i "s#import { format } from.*#import { quoteDump } from '/home/nathan/workspaces/nth-share/sel/js/src/value.mjs';#" $D/probe_opt.mjs; sed -i "s/raw.toString ? raw.toString() : JSON.stringify(raw)/raw.dump()/; s/opt.toString ? opt.toString() : JSON.stringify(opt)/opt.dump()/" $D/probe_opt.mjs; node $D/probe_opt.mjs 'LIST(RECORD("cat","B","v",1), RECORD("cat","A","v",2), RECORD("cat","B","v",3)) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT()'
```

```
17:    if (v.kind === 'BIN') return `bin:${v.dump().slice(1)}`;
19:  return v.dump();
raw  : -{"1"=-{"cat"=t"B", "n"=t"2"}, "2"=-{"cat"=t"A", "n"=t"1"}}
run  : -{"1"=-{"cat"=t"1", "n"=t"2"}, "2"=-{"cat"=t"2", "n"=t"1"}}
steps raw: ["RECORD","LIST","BUCKET","MAP","SORT"]  logical: ["RECORD","LIST","BUCKET","SORT","MAP"]
```

Recount BUCKET occurrences with plain grep

```bash
which grep; grep -c 'BUCKET' php/bin/CaseData.php; grep -c 'BUCKET' lisp/bin/case-data.lisp; grep -c 'BUCKET' js/bin/case-data.mjs; /usr/bin/grep -c 'bucket.basic' php/bin/CaseData.php lisp/bin/case-data.lisp
```

```
grep ()
{ 
    local _cc_a;
    for _cc_a in ${1+"$@"};
    do
        case "$_cc_a" in 
            -*-filter* | -*-pager* | -*-view* | -*-format-open* | -*-config* | ---* | -@* | -*-save-config* | -[Zz]* | -[!-]*[Zz]* | --null | --null-data)
                command grep ${1+"$@"};
                return
            ;;
        esac;
    done;
    local _cc_bin="${CLAUDE_CODE_EXECPATH:-}";
    [[ -x $_cc_bin ]] || _cc_bin=/home/nathan/.local/bin/claude;
    if [[ ! -x $_cc_bin ]]; then
        command grep ${1+"$@"};
        return;
    fi;
    if [[ -n ${ZSH_VERSION:-} ]]; then
        ARGV0=ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg --exclude-dir=.bzr --exclude-dir=.jj --exclude-dir=.sl ${1+"$@"};
    else
        if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "win32" ]]; then
            ARGV0=ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg --exclude-dir=.bzr --exclude-dir=.jj --exclude-dir=.sl ${1+"$@"};
        else
            ( exec -a ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg --exclude-dir=.bzr --exclude-dir=.jj --exclude-dir=.sl ${1+"$@"} );
        fi;
    fi
}
23
php/bin/CaseData.php:1
lisp/bin/case-data.lisp:1
```

Find PHP and Lisp binding constructor forms

```bash
/usr/bin/grep -n "bucket.basic" -A20 php/bin/CaseData.php | /usr/bin/grep "relation" | cut -c1-300; /usr/bin/grep -n "bucket.basic" -A20 lisp/bin/case-data.lisp | /usr/bin/grep -i "relation" | cut -c1-300
```

```
/usr/bin/grep: php/bin/CaseData.php: binary file matches
/usr/bin/grep: lisp/bin/case-data.lisp: binary file matches
```

Find PHP and Lisp binding constructor forms

```bash
/usr/bin/grep -a -n "bucket.basic" -A20 php/bin/CaseData.php | /usr/bin/grep -a "relation" | cut -c1-300; /usr/bin/grep -a -n "bucket.basic" -A20 lisp/bin/case-data.lisp | /usr/bin/grep -a -i "relation" | cut -c1-300
```

```
8847-            'bindings' => static fn (): array => ['ITEMS' => Binding::relation('items', null, ['DEPT' => Binding::column('dept', null, 'TEXT')], null, null)],
8390-   :bindings (lambda () (list (cons "ITEMS" (binding-relation "items" nil (list (cons "DEPT" (binding-column "dept" nil :text))) nil nil)))))
```

Write translate probes for JS, Python, PHP

```bash
D=$SCRATCH/verify-C2
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
/usr/bin/grep -n "function sql\|public function\|->code\|\$code" php/src/Sql/Fragment.php php/src/Sql/SqlError.php 2>/dev/null | head; /usr/bin/grep -n "class SqlError" -A12 php/src/Sql/*.php | head -20
```

```
php/src/Sql/Fragment.php:53:    public function __construct(
php/src/Sql/Fragment.php:79:    public function asValue(string $mode = 'inline'): string
php/src/Sql/Fragment.php:93:    public function asStatement(string $mode = 'inline'): string
php/src/Sql/Fragment.php:112:    public function asCondition(string $mode = 'inline'): string
php/src/Sql/Fragment.php:143:    public function bindings(): array
php/src/Sql/Fragment.php:197:    public function isExact(): bool
php/src/Sql/SqlError.php:12:    // Untyped so it can override Exception::$code, whose type must be omitted.
php/src/Sql/SqlError.php:14:    public $code;
php/src/Sql/SqlError.php:20:    public function __construct(string $code, string $message, ?array $pos = null)
php/src/Sql/SqlError.php:23:        $this->code = $code;
php/src/Sql/SqlError.php:10:final class SqlError extends \Exception
php/src/Sql/SqlError.php-11-{
php/src/Sql/SqlError.php-12-    // Untyped so it can override Exception::$code, whose type must be omitted.
php/src/Sql/SqlError.php-13-    /** @var string */
php/src/Sql/SqlError.php-14-    public $code;
php/src/Sql/SqlError.php-15-    public int $line;
php/src/Sql/SqlError.php-16-    public int $col;
php/src/Sql/SqlError.php-17-    public int $offset;
php/src/Sql/SqlError.php-18-
php/src/Sql/SqlError.php-19-    /** @param array{line:int,col:int,offset:int}|null $pos */
php/src/Sql/SqlError.php-20-    public function __construct(string $code, string $message, ?array $pos = null)
php/src/Sql/SqlError.php-21-    {
php/src/Sql/SqlError.php-22-        parent::__construct($message);
```

Run translate probes in JS, Python, PHP

```bash
D=$SCRATCH/verify-C2
sed -i 's/\$f->sql()/$f->asStatement()/' $D/tr.php
/usr/bin/grep -n "def sql\|def as_statement\|def as_value" python/sel/sql/fragment.py | head -3; /usr/bin/grep -n "  sql()\|  asStatement(\|  asValue(" js/src/sql/fragment.mjs | head
sed -i 's/f.sql()/f.asStatement()/' $D/tr.mjs; sed -i 's/f.sql()/f.as_statement()/' $D/tr.py
P='ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)'
node $D/tr.mjs "$P"; PYTHONPATH=$PWD/python python3 $D/tr.py "$P"; php $D/tr.php "$P"
```

```
52:    def as_value(self, mode: str = 'inline') -> str:
64:    def as_statement(self, mode: str = 'inline') -> str:
37:  asValue(mode = 'inline') {
50:  asStatement(mode = 'inline') {
JS translate null: E_SQL_SHAPE 1:31 a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
JS translate {"noOptimize":true}: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
JS translateStatement null: E_SQL_SHAPE 1:31 a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
JS translateStatement {"noOptimize":true}: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
PY translate None: E_SQL_SHAPE 1:31 E_SQL_SHAPE at 1:31: a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
PY translate {'noOptimize': True}: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
PY translate_statement None: E_SQL_SHAPE 1:31 E_SQL_SHAPE at 1:31: a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
PY translate_statement {'noOptimize': True}: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
PHP translate null: TypeError : Sel\Sql\Sql::translate(): Argument #4 ($options) must be of type array, null given, called in $SCRATCH/verify-C2/tr.php on line 8
PHP translate {"noOptimize":true}: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
PHP translateStatement null: TypeError : Sel\Sql\Sql::translateStatement(): Argument #4 ($options) must be of type array, null given, called in $SCRATCH/verify-C2/tr.php on line 8
PHP translateStatement {"noOptimize":true}: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
```

Rerun PHP probe and inspect Lisp loading

```bash
D=$SCRATCH/verify-C2
sed -i "s/foreach (\[null, \['noOptimize' => true\]\]/foreach ([[], ['noOptimize' => true]]/" $D/tr.php
P='ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)'
php $D/tr.php "$P"
# Lisp probe
sed -n 1,40p lisp/bin/sqlt.lisp | head -40; /usr/bin/grep -n "fragment-as-statement\|as-statement" lisp/src/sql/*.lisp | head -3
```

```
PHP translate []: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
PHP translate {"noOptimize":true}: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
PHP translateStatement []: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
PHP translateStatement {"noOptimize":true}: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
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
;;;; constructor calls.

;;; boot.lisp brings in :sel-lang; the SQL layer is its own system, for the
;;; reason the JS host keeps its translator behind a separate entry point.
(let ((*standard-output* (make-broadcast-stream)))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))

(defpackage #:sel-sqlt
  (:use #:common-lisp #:sel.sql)
  (:export #:main))

(in-package #:sel-sqlt)

(defvar *corpus-print-base* 10
  "What *PRINT-BASE* is bound to while a case translates -- see --print-base.
Bound around the translation alone and not around this file's own reporting, so
the counts in the summary stay readable whatever it is set to.")

(load (merge-pathnames "case-data.lisp" (directory-namestring *load-truename*)))

lisp/src/sql/fragment.lisp:101:(defun as-statement (f &optional (mode :inline))
lisp/src/sql/hybrid.lisp:335:       (funcall db-runner (as-statement frag) (fragment-params frag))))
lisp/src/sql/hybrid.lisp:341:            (db-rows (funcall db-runner (as-statement frag) (fragment-params frag)))
```

Learn how to boot Lisp and read SQL errors

```bash
cat lisp/bin/sqlt | head -20; cat lisp/bin/boot.lisp | head -30; /usr/bin/grep -n "sql-error-code\|define-condition sql-error" -A6 lisp/src/sql/errors.lisp 2>/dev/null | head -20; ls lisp/src/sql/
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
9:(define-condition sql-error (error)
10:  ((code :initarg :code :reader sql-error-code)
11-   (message :initarg :message :reader sql-error-message)
12-   (line :initarg :line :initform 0 :reader sql-error-line)
13-   (col :initarg :col :initform 0 :reader sql-error-col)
14-   (offset :initarg :offset :initform 0 :reader sql-error-offset))
15-  (:report (lambda (c s)
16:             (format s "~a at ~a:~a: ~a" (sql-error-code c) (sql-error-line c)
17-                     (sql-error-col c) (sql-error-message c))))
18-  (:documentation "One class, one message. No diagnostics object, no EXPLAIN."))
19-
20-(defun refuse (code message &optional pos)
21-  "Signal at the point of failure. Nothing wraps this on the way out, the same
22-rule spec/errors.md sets for the evaluator."
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

Run the Lisp translate probe

```bash
D=$SCRATCH/verify-C2
# (file written, see "Reproduction scripts" above)
P='ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)'
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/tr.lisp --end-toplevel-options "$P" 2>&1 | tail -8
```

```
9: (LOAD #P"$SCRATCH/verify-C2/tr.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-C2/tr.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

See the Lisp error and exported symbols

```bash
D=$SCRATCH/verify-C2
P='ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)'
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/tr.lisp --end-toplevel-options "$P" 2>&1 | /usr/bin/grep -v "^[0-9]*: " | head -12; /usr/bin/grep -n "export" -A40 lisp/src/sql/package.lisp | /usr/bin/grep -i "binding\|as-statement\|translate" | head
```

```
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=746461 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    The symbol "COMPILE" is not external in the SEL package.

      Line: 8, Column: 95, File-Position: 500

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-C2/tr.lisp" {1201ECF353}>

Backtrace for: #<SB-THREAD:THREAD tid=746461 "main thread" RUNNING {1204030083}>

39-   #:as-statement
40-   #:bindings
```

Fix the Lisp compile symbol and rerun

```bash
D=$SCRATCH/verify-C2
/usr/bin/grep -n "compile" lisp/tests/unit.lisp | head -3; sed -i 's/(sel:compile src)/(sel:compile-program src)/' $D/tr.lisp
P='ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)'
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/tr.lisp --end-toplevel-options "$P" 2>&1 | /usr/bin/grep -v "^[0-9]*: " | head -12
```

```
149:  (let ((deps (sel:dependencies (sel:compile-source "IF(A > B, A, C)"))))
151:  (is (equal '("Y") (sel:dependencies (sel:compile-source "X = 1; X + Y")))
153:  (is (equal '("ITEMS") (sel:dependencies (sel:compile-source "ALL(ITEMS, ITEM, ITEM > 0)")))
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=746709 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Symbol "COMPILE-PROGRAM" not found in the SEL package.

      Line: 8, Column: 103, File-Position: 508

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-C2/tr.lisp" {1201ECF353}>

Backtrace for: #<SB-THREAD:THREAD tid=746709 "main thread" RUNNING {1204030083}>
```

Rerun the Lisp probe

```bash
D=$SCRATCH/verify-C2
sed -i 's/(sel:compile-program src)/(sel:compile-source src)/' $D/tr.lisp
P='ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)'
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/tr.lisp --end-toplevel-options "$P" 2>&1 | /usr/bin/grep -v "^[0-9]*: " | head -12
```

```
LISP TRANSLATE NIL: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
LISP TRANSLATE (:NO-OPTIMIZE T): SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
LISP TRANSLATE-STATEMENT NIL: E_SQL_SHAPE 1:31 a MAP over buckets must follow the BUCKET, with at most a ~
FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
LISP TRANSLATE-STATEMENT (:NO-OPTIMIZE T): SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
```

Get C++ SQL objects list and error type

```bash
/usr/bin/grep -n "SQL_OBJS\s*=\|SQL_OBJS :=" -A3 cpp/Makefile | head; /usr/bin/grep -n "class SqlError\|struct SqlError" -A8 cpp/sel_sql.hpp | head -14; /usr/bin/grep -n "static Fragment translate\|struct Options\|struct TranslateOptions" -A3 cpp/sel_sql.hpp | head -20
```

```
35:SQL_OBJS := $(addprefix $(BUILD)/,$(SQL_SRCS:.cpp=.o))
36-SQL_HDRS := sel_sql.hpp sel_sql_emit.hpp sel_sql_map.hpp sel_sql_node.hpp \
37-            sel_sql_stage1.hpp sel_sql_translator.hpp
38-# Tools over that layer.
39:class SqlError : public std::exception {
40- public:
41-  SqlError(std::string code, std::string message, Pos pos = {});
42-
43-  const std::string& code() const noexcept { return code_; }
44-  const std::string& message() const noexcept { return message_; }
45-  int line() const noexcept { return pos_.line; }
46-  int col() const noexcept { return pos_.col; }
47-  int offset() const noexcept { return pos_.offset; }
361:struct Options {
362-  // Refuse anything the dialect can only translate approximately -- an entry
363-  // carrying a caveat. An application that would rather evaluate a rule in the
364-  // host than push down a near-equivalent turns this on.
--
376:  static Fragment translate(const Program& program, const std::string& dialect,
377-                            const Bindings& bindings = {},
378-                            const Options& options = {});
379-
--
393:  static Fragment translate_statement(const Program& program,
394-                                      const std::string& dialect,
395-                                      const Bindings& bindings = {},
396-                                      const Options& options = {});
```

Build and run the C++ translate probe

```bash
D=$SCRATCH/verify-C2
# (file written, see "Reproduction scripts" above)
cd cpp && g++ -std=c++23 -O1 -I. -o $D/tr $D/tr.cpp build/sel_sql_binding.o build/sel_sql_emit.o build/sel_sql_hybrid.o build/sel_sql_map_data.o build/sel_sql_map.o build/sel_sql_node.o build/sel_sql.o build/sel_sql_stage1.o build/sel_sql_translator.o build/sel.o 2>&1 | tail -5 && $D/tr 'ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)'
```

```
CPP translate: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
CPP translate_statement: SELECT `dept` AS `dept`, COUNT(*) AS `n` FROM `items` GROUP BY `dept` ORDER BY `dept` ASC
```

Probe MAP/SORT swap on non-bucket programs (raw vs run)

```bash
D=$SCRATCH/verify-C2
for P in '(3, 1, 2) .> MAP(0 - _) .> SORT()' '(3, 1, 2) .> MAP(0 - _) .> TOP(1)' '(3, 1, 2) .> MAP(10 - _) .> SORT_DESC()' '(1, 2, 3) .> MAP(_K) .> SORT_DESC()' '(RECORD("x", 3), RECORD("x", 1)) .> MAP(RECORD("y", 0 - _["x"])) .> SORT_BY(_["y"])'; do echo "== $P"; node $D/probe_opt.mjs "$P"; done
```

```
Exit code 1
== (3, 1, 2) .> MAP(0 - _) .> SORT()
raw  : -{"1"=t"-3", "2"=t"-2", "3"=t"-1"}
run  : -{"1"=t"-1", "2"=t"-2", "3"=t"-3"}
steps raw: ["MAP","SORT"]  logical: ["SORT","MAP"]
== (3, 1, 2) .> MAP(0 - _) .> TOP(1)
raw  : -{"1"=t"-3"}
run  : -{"1"=t"-1"}
steps raw: ["MAP","TOP"]  logical: ["TOP","MAP"]
== (3, 1, 2) .> MAP(10 - _) .> SORT_DESC()
raw  : -{"1"=t"9", "2"=t"8", "3"=t"7"}
run  : -{"1"=t"7", "2"=t"8", "3"=t"9"}
steps raw: ["MAP","SORT_DESC"]  logical: ["SORT_DESC","MAP"]
== (1, 2, 3) .> MAP(_K) .> SORT_DESC()
raw  : -{"1"=t"3", "2"=t"2", "3"=t"1"}
run  : -{"1"=t"1", "2"=t"2", "3"=t"3"}
steps raw: ["MAP","SORT_DESC"]  logical: ["SORT_DESC","MAP"]
== (RECORD("x", 3), RECORD("x", 1)) .> MAP(RECORD("y", 0 - _["x"])) .> SORT_BY(_["y"])
file:///home/nathan/workspaces/nth-share/sel/js/src/errors.mjs:20
  throw new SelError(code, message, pos);
        ^

SelError: no key "x"
    at fail (file:///home/nathan/workspaces/nth-share/sel/js/src/errors.mjs:20:9)
    at evalDispatch (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:147:32)
    at evalNode (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:124:12)
    at evalBinary (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:236:13)
    at evalDispatch (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:159:24)
    at evalNode (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:124:12)
    at Args.val (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:71:54)
    at evalDispatch (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:166:57)
    at evalNode (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:124:12)
    at Args.evalNode (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:76:47) {
  code: 'E_NO_KEY',
  line: 1,
  col: 58,
  offset: 57
}

Node.js v22.12.0
```

Run the non-bucket MAP/SORT repro in all five REPLs

```bash
for P in '(3, 1, 2) .> MAP(0 - _) .> SORT()' '(3, 1, 2) .> MAP(0 - _) .> TOP(1)'; do echo "== $P"; echo -n "JS  : "; node js/bin/sel.mjs -e "$P"; echo -n "PHP : "; php php/bin/sel -e "$P"; echo -n "CPP : "; cpp/build/sel -e "$P"; echo -n "LISP: "; lisp/bin/sel -e "$P"; echo -n "PY  : "; PYTHONPATH=$PWD/python python3 -m sel -e "$P"; done
```

```
== (3, 1, 2) .> MAP(0 - _) .> SORT()
JS  : -{"1"=t"-1", "2"=t"-2", "3"=t"-3"}
PHP : -{"1"=t"-1", "2"=t"-2", "3"=t"-3"}
CPP : -{"1"=t"-1", "2"=t"-2", "3"=t"-3"}
LISP: -{"1"=t"-1", "2"=t"-2", "3"=t"-3"}
PY  : -{"1"=t"-1", "2"=t"-2", "3"=t"-3"}
== (3, 1, 2) .> MAP(0 - _) .> TOP(1)
JS  : -{"1"=t"-1"}
PHP : -{"1"=t"-1"}
CPP : -{"1"=t"-1"}
LISP: -{"1"=t"-1"}
PY  : -{"1"=t"-1"}
```

Check whether the rule and optimised run() pre-date the commit in each host

```bash
for f in php/src/Optimizer.php python/sel/optimizer.py lisp/src/optimizer.lisp; do echo "== $f @8fe0e3a"; git show 8fe0e3a:$f 2>&1 | /usr/bin/grep -n -i "mapHasComputedFields\|map_has_computed_fields\|map-has-computed-fields" | head -3; done; echo "== cpp"; git show 8fe0e3a:cpp/sel.cpp | /usr/bin/grep -n "map_has_computed_fields\|opt_map_has_computed" | head -3; echo "== run() pre-commit"; git show 8fe0e3a:php/src/Sel.php | /usr/bin/grep -n "optimi" | head -3; git show 8fe0e3a:python/sel/__init__.py | /usr/bin/grep -n "optimi" | head -3; git show 8fe0e3a:cpp/sel.cpp | /usr/bin/grep -n "Program::run" -A6 | head -12; git show 8fe0e3a:lisp/src/sel.lisp | /usr/bin/grep -n "optimi" | head -3
```

```
== php/src/Optimizer.php @8fe0e3a
355:                    && self::mapHasComputedFields($first)) {
466:    private static function mapHasComputedFields(array $step): bool
== python/sel/optimizer.py @8fe0e3a
222:def map_has_computed_fields(step: Node) -> bool:
395:                    and map_has_computed_fields(first)):
== lisp/src/optimizer.lisp @8fe0e3a
212:(defun map-has-computed-fields-p (map-step)
619:                     (map-has-computed-fields-p s1))
== cpp
5398:bool opt_map_has_computed(const Node& step) {
5584:          opt_map_has_computed(*first)) {
== run() pre-commit
48:        $ast = Optimizer::optimize($this->ast, true);
49:        from .optimizer import optimize_ast
50:        return eval_node(optimize_ast(self.ast), _Context(root))
5869:Value Program::run(Value& context) const {
5870-  Context ctx(context);
5871-  const NodePtr optimized = optimize_ast(ast_);
5872-  return eval_node(*optimized, ctx);
5873-}
5874-
5875:Value Program::run() const {
5876-  Value ctx = Value::none();
5877-  return run(ctx);
5878-}
5879-
5880-std::vector<std::string> Program::dependencies() const {
21:    (eval-node (optimize-ast (program-ast program)) (make-context root))))
```

Find the Python sqlite hybrid execution test

```bash
/usr/bin/grep -n "sqlite\|execute_hybrid\|plan_hybrid" python/tests/test_unit.py | head -20
```

```
328:    plan = Sql.plan_hybrid(program, 'postgresql', _orders())
332:    assert failure(lambda: Sql.execute_hybrid(plan, lambda sql, params: rows,
444:    plan = Sql.plan_hybrid(sel_compile('X = ORDERS; X .> TAKE(1)'), 'postgresql', _orders())
452:    fused = Sql.plan_hybrid(sel_compile(source), 'postgresql', _orders())
453:    unfused = Sql.plan_hybrid(sel_compile(source), 'postgresql', _orders(),
463:    plan = Sql.plan_hybrid(program, 'postgresql', _orders())
480:    Sql.plan_hybrid(program, 'postgresql', _orders())
485:def test_bucket_plans_answer_what_the_evaluator_answers_on_sqlite():
489:    executed plan can see that, and sqlite3 ships with Python, so this runs
492:    import sqlite3
499:    db = sqlite3.connect(':memory:')
520:        plan = Sql.plan_hybrid(program, 'sqlite', bindings)
523:        got = Sql.execute_hybrid(plan, runner, {'ORDERS': rows})
```

Write the Python three-lane probe; find evaluator module names

```bash
D=$SCRATCH/verify-C2
# (file written, see "Reproduction scripts" above)
/usr/bin/grep -n "^from\|^def eval_node\|^class Context\|^class _Context" python/sel/__init__.py python/sel/evaluator.py 2>/dev/null | head; ls python/sel/
```

```
python/sel/__init__.py:15:from __future__ import annotations
python/sel/__init__.py:17:from typing import Any
python/sel/__init__.py:19:from . import builtins as _builtins   # noqa: F401  registers the function table
python/sel/__init__.py:20:from .errors import Pos, SelError, fail
python/sel/__init__.py:21:from .eval import MAX_DEPTH, Context as _Context, eval_node
python/sel/__init__.py:22:from .parser import Node, parse
python/sel/__init__.py:23:from .registry import names as _names
python/sel/__init__.py:24:from .value import BIN, BOOL, NONE, TEXT, Value
builtins
_cli.py
decimal.py
errors.py
eval.py
__init__.py
lexer.py
__main__.py
optimizer.py
parser.py
__pycache__
py.typed
registry.py
sql
utf8.py
value.py
```

Run three lanes in Python over sqlite for several shapes

```bash
D=$SCRATCH/verify-C2
sed -i 's/from sel.evaluator import eval_node, Context/from sel.eval import eval_node, Context/' $D/lanes.py
PYTHONPATH=$PWD/python python3 $D/lanes.py 'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> SORT_BY(_K)' 'ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> SORT_BY(_K, "DESC")' 'ORDERS .> MAP(0 - _["amount"]) .> SORT()' 'ORDERS .> MAP(RECORD("neg", 0 - _["amount"])) .> SORT_BY(_["neg"])'
```

```
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> SORT_BY(_K)
  raw eval : -{"1"=-{"cid"=t"7", "n"=t"2"}, "2"=-{"cid"=t"9", "n"=t"1"}}
  run()    : -{"1"=-{"cid"=t"1", "n"=t"2"}, "2"=-{"cid"=t"2", "n"=t"1"}}
  plan     : pure_memory
  hybrid   : -{"1"=-{"cid"=t"1", "n"=t"2"}, "2"=-{"cid"=t"2", "n"=t"1"}}
  translate: E_SQL_SHAPE at 1:39: a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
== ORDERS .> BUCKET(_["customer_id"]) .> MAP(RECORD("cid", _K, "n", COUNT(_))) .> SORT_BY(_K, "DESC")
  raw eval : -{"1"=-{"cid"=t"9", "n"=t"1"}, "2"=-{"cid"=t"7", "n"=t"2"}}
  run()    : -{"1"=-{"cid"=t"1", "n"=t"1"}, "2"=-{"cid"=t"2", "n"=t"2"}}
  plan     : pure_memory
  hybrid   : -{"1"=-{"cid"=t"1", "n"=t"1"}, "2"=-{"cid"=t"2", "n"=t"2"}}
  translate: E_SQL_SHAPE at 1:39: a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping
== ORDERS .> MAP(0 - _["amount"]) .> SORT()
  raw eval : -{"1"=t"-10", "2"=t"-7", "3"=t"-5"}
  run()    : -{"1"=t"-10", "2"=t"-5", "3"=t"-7"}
  plan     : pure_memory
  hybrid   : -{"1"=t"-10", "2"=t"-5", "3"=t"-7"}
  translate: E_SQL_SHAPE at 1:35: SORT on a multi-field relation requires a key expression; use SORT_BY
== ORDERS .> MAP(RECORD("neg", 0 - _["amount"])) .> SORT_BY(_["neg"])
  raw eval : -{"1"=-{"neg"=t"-10"}, "2"=-{"neg"=t"-7"}, "3"=-{"neg"=t"-5"}}
  run()    : -{"1"=-{"neg"=t"-10"}, "2"=-{"neg"=t"-7"}, "3"=-{"neg"=t"-5"}}
  plan     : pure_sql
   SQL: SELECT "_sub1".* FROM (SELECT ('0' - "o"."amount") AS "neg" FROM "orders" "o") "_sub1" ORDER BY "_sub1"."neg" ASC
  hybrid   : -{"1"=-{"neg"=t"-10"}, "2"=-{"neg"=t"-7"}, "3"=-{"neg"=t"-5"}}
  translate: SELECT "_sub1".* FROM (SELECT ('0' - "o"."amount") AS "neg" FROM "orders" "o") "_sub1" ORDER BY "_sub1"."neg" ASC
```

Find PHP and C++ planner API usage

```bash
/usr/bin/grep -n "planHybrid\|pureMemory\|pure_memory\|isHybrid\|->sqlStatement" tools/check-php-optimizer.php | head -8; /usr/bin/grep -n "plan_hybrid\|pure_memory\|pure_sql\|sql_statement" cpp/tests/sql_unit.cpp | head -8
```

```
170:$hybrid = Sql::planHybrid(
175:check($hybrid->pureSql && $hybrid->sqlStatement !== null, 'hybrid normalization before planning');
202:$helper = Sql::planHybrid(Sel::compile('X = ORDERS; X .> TAKE(1)'), 'postgresql', $orders);
207:$fused = Sql::planHybrid(Sel::compile($twoFilters), 'postgresql', $orders);
208:$unfused = Sql::planHybrid(Sel::compile($twoFilters), 'postgresql', $orders, ['fuseFilters' => false]);
214:$refusedPlan = Sql::planHybrid($refused, 'postgresql', $orders);
215:check($refusedPlan->pureMemory && $refusedPlan->continuationProgram === $refused
226:Sql::planHybrid($reusable, 'postgresql', $orders);
68:      Sql::plan_hybrid(hybrid_program, "mariadb", bindings);
69:  if (!hybrid.is_hybrid || hybrid.pure_sql || hybrid.pure_memory ||
70:      !hybrid.sql_statement || !hybrid.continuation_program ||
82:  const sel::sql::HybridPlan helper_plan = Sql::plan_hybrid(helper, "mariadb", bindings);
83:  if (!helper_plan.pure_sql || helper_plan.source_tables != std::vector<std::string>{"orders"}) {
89:  const sel::sql::HybridPlan refused_plan = Sql::plan_hybrid(refused, "mariadb", bindings);
90:  if (!refused_plan.pure_memory || refused_plan.sql_statement ||
134:      {"ORDERS .> FILTER(IF(TRUE, \"x\", 1) >= _[\"id\"])", "pure_memory", "E_NOT_NUM@1:18"},
```

Check the planner classification in PHP, JS, C++

```bash
D=$SCRATCH/verify-C2
cat >> $D/tr.php <<'EOF'
$plan = Sql::planHybrid(Sel::compile($src), 'mariadb', $b);
echo "PHP plan: " . ($plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid')) . ($plan->sqlStatement ? ' ' . $plan->sqlStatement->asStatement() : '') . "\n";
EOF
cat >> $D/tr.mjs <<'EOF'
const plan = Sql.planHybrid(compile(src), 'mariadb', b);
console.log('JS plan:', plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid', plan.sqlStatement ? plan.sqlStatement.asStatement() : '');
EOF
python3 - <<'EOF'
import re
p='$SCRATCH/verify-C2/tr.cpp'
s=open(p).read()
s=s.replace('}\n', '''  { auto plan = Sql::plan_hybrid(sel::compile(src), "mariadb", b);
    std::cout << "CPP plan: " << (plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid") << (plan.sql_statement ? " " + plan.sql_statement->as_statement() : "") << "\\n"; }
}
''',1) if s.rstrip().endswith('}') else s
# simpler: replace last closing brace
idx=s.rstrip().rfind('}')
s=s.rstrip()[:idx]+'''  { auto plan = Sql::plan_hybrid(sel::compile(src), "mariadb", b);
    std::cout << "CPP plan: " << (plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid") << (plan.sql_statement ? " " + plan.sql_statement->as_statement() : "") << "\\n"; }
}
'''
open(p,'w').write(s)
EOF
P='ITEMS .> BUCKET(_["dept"]) .> MAP(RECORD("dept", _K, "n", COUNT(_))) .> SORT_BY(_K)'
php $D/tr.php "$P" | tail -1; node $D/tr.mjs "$P" | tail -1; cd cpp && g++ -std=c++23 -O1 -I. -o $D/tr $D/tr.cpp build/sel_sql_binding.o build/sel_sql_emit.o build/sel_sql_hybrid.o build/sel_sql_map_data.o build/sel_sql_map.o build/sel_sql_node.o build/sel_sql.o build/sel_sql_stage1.o build/sel_sql_translator.o build/sel.o 2>&1 | /usr/bin/grep -i error | head -5; $D/tr "$P" | tail -1
```

```
PHP plan: pure_memory
JS plan: pure_memory 
CPP plan: pure_memory
```

Search tests and docs for the MAP/SORT rule's intent

```bash
/usr/bin/grep -n "SORT\|TOP" sql/cases/25-hybrid-plans.sqlt | head; /usr/bin/grep -rn "MAP.*SORT\|MAP.*TOP" tools/check-js-optimizer.mjs tools/check-php-optimizer.php python/tests/test_unit.py lisp/tests/unit.lisp cpp/tests/sql_unit.cpp | head; /usr/bin/grep -n -i "sort.*map\|map.*sort" docs/EXTENDING.md docs/SQL-TRANSLATION.md docs/interim/*.md CHANGELOG.md | head
```

```
138:ORDERS .> SORT_DESC(_["id"]) .> TAKE(1) .> MAP(RECORD("id", _["id"], "tag", ABORT("x")))
319:ORDERS .> SORT_BY(_["id"]) .> TAKE(1)
tools/check-js-optimizer.mjs:71:same(names(late), ['SORT_BY', 'MAP'], 'SORT_BY late materialization');
tools/check-js-optimizer.mjs:78:same(names(optimizedSteps(topPrefix + 'TOP(r, r["y"], 1)', false)), ['MAP', 'TOP'],
tools/check-php-optimizer.php:96:check(step_names($topComputed) === ['MAP', 'TOP'], 'TOP explicit binder key analysis');
tools/check-php-optimizer.php:148:check(step_names($lateMaterialization) === ['SORT_BY', 'MAP'], 'SORT_BY late materialization');
python/tests/test_unit.py:276:    assert names(prefix + 'TOP(r, r["y"], 1)') == ['MAP', 'TOP']
lisp/tests/unit.lisp:617:    ;; Continuation executes MAP with custom score, FILTER, BUCKET, SORT_BY, and TAKE
lisp/tests/unit.lisp:768:  ;; 3. Late Materialization: MAP .> TOP_BY
lisp/tests/unit.lisp:769:  (let* ((prog (sel:compile-source "DATA .> MAP(RECORD('id', _['id'], 'heavy', _['x'] * 2)) .> TOP_BY(_['id'], 3)"))
lisp/tests/unit.lisp:771:    ;; Top-level should now be MAP, with child TOP_BY
lisp/tests/unit.lisp:788:      (is (equal '("MAP" "TOP") (mapcar #'sel::node-s computed-steps)))))
docs/interim/sel_comprehensive_in_memory_optimization_roadmap.md:114:* **Target**: Compile pipeline expressions (`MAP` record constructors, `FILTER` predicates, `SORT_BY` keys, `BUCKET` aggregation expressions) into native Common Lisp lambdas compiled to x86-64 machine code by SBCL.
docs/interim/sel_comprehensive_in_memory_optimization_roadmap.md:129:| **Group 1** | **Logical Pipeline Optimization** | `FILTER` fusion, Pushdown across `LINK` & `MAP`, Redundant Sort/Dedupe Pruning, Late Materialization, Zero-Copy Filtering. | **COMPLETED (100% Pass, 2.18x Suite Speedup)** |
docs/interim/sel_cpp_copy_and_materialization_optimization_plan.md:300:- nested source mutation after `TAKE`, `DROP`, `MAP`, `FILTER`, `SORT`, and
docs/interim/sel_porting_worklist_and_checklist.md:55:| **Group 1: Logical AST Optimizer** (Filter fusion, predicate pushdown across joins & maps, sort pruning) | **Complete** | Complete | Complete | Complete | Complete | **Complete** |
docs/interim/sel_porting_worklist_and_checklist.md:159:   - **Top-N Pushdown Through MAP**: move `TOP`/`TOP_DESC`/`TOP_BY` or sort steps before a computed `MAP` when the key uses only pass-through fields.
docs/interim/sel_porting_worklist_and_checklist.md:266:short-circuiting, all slicing/top-N fusions, MAP/SORT/SELECT filter pushdown, join
docs/interim/sel_porting_worklist_and_checklist.md:473:   - **Aggregates**: `ALL`, `ANY`, `MAP`, `FILTER`, `SUM`, `JOIN`, `SORT`, `SORT_DESC`, `SORT_BY`, `GROUP_BY`.
docs/interim/sel_scale_parity_benchmark_report.md:12:2. **Deep Relational Pipelines**: Constructed a **14-stage pipeline** chaining 2 subquery wraps, 3 joins, 4 filters, 2 intermediate maps, bucket aggregation, having clauses, sorting, and limits into a single executable query.
docs/interim/sel_sql_execution_engine_plan.md:31:        T1["Unified Relational Pipeline |><br/>FILTER -> WHERE / HAVING<br/>MAP / RECORD -> SELECT Projection<br/>GROUP_BY -> GROUP BY & Aggregates<br/>SORT_BY -> ORDER BY<br/>TAKE / DROP -> LIMIT / OFFSET<br/>JOIN_ON -> Relational Joins"]
docs/interim/sel_sql_execution_engine_plan.md:336:   - Every new operator (`??`, `|>`) and function (`PATH`, `FILTER`, `MAP`, `RECORD`, `SORT_BY`, `TAKE`) verified across all 5 language implementations.
```

See a SORT conformance case for the file format

```bash
/usr/bin/grep -n -B3 -A8 "SORT()" conformance/*.selt | /usr/bin/grep -A8 "### name" | head -30
```

```
conformance/15-relational.selt-216-### name: rel.sort.direct-asc
conformance/15-relational.selt-217---- source
conformance/15-relational.selt:218:LIST(3, 1, 2) .> SORT()
conformance/15-relational.selt-219---- expect
conformance/15-relational.selt-220-tree -{"1"=t"1", "2"=t"2", "3"=t"3"}
conformance/15-relational.selt-221-===
conformance/15-relational.selt-222-
conformance/15-relational.selt-223-### name: rel.sort.direct-desc
conformance/15-relational.selt-224---- source
conformance/15-relational.selt-225-LIST(3, 1, 2) .> SORT_DESC()
conformance/15-relational.selt-226---- expect
--
conformance/15-relational.selt-229-
conformance/15-relational.selt-230-### name: rel.sort.text
conformance/15-relational.selt-231---- source
conformance/15-relational.selt:232:LIST("b", "a", "c") .> SORT()
conformance/15-relational.selt-233---- expect
conformance/15-relational.selt-234-tree -{"1"=t"a", "2"=t"b", "3"=t"c"}
conformance/15-relational.selt-235-===
conformance/15-relational.selt-236-
conformance/15-relational.selt-237-### name: rel.sort.mixed
conformance/15-relational.selt-238---- source
conformance/15-relational.selt:239:LIST("text", 42, TRUE, NULL) .> SORT()
conformance/15-relational.selt-240---- expect
conformance/15-relational.selt-241-tree -{"1"=-, "2"=TRUE, "3"=t"42", "4"=t"text"}
conformance/15-relational.selt-242-===
conformance/15-relational.selt-243-
conformance/15-relational.selt-244-### name: rel.sort-by.default-asc
conformance/15-relational.selt-245---- source
conformance/15-relational.selt-246-LIST(RECORD("n", 30), RECORD("n", 10), RECORD("n", 20)) .> SORT_BY(_["n"]) .> MAP(_["n"])
```

Run a scratch conformance file with the expected unoptimised values on all hosts

```bash
D=$SCRATCH/verify-C2
# (file written, see "Reproduction scripts" above)
echo JS; node js/bin/conformance.mjs $D/99-probe.selt 2>&1 | tail -6; echo PHP; php php/bin/conformance $D/99-probe.selt 2>&1 | tail -6; echo CPP; cpp/build/conformance $D/99-probe.selt 2>&1 | tail -6; echo LISP; lisp/bin/conformance $D/99-probe.selt 2>&1 | tail -6; echo PY; PYTHONPATH=$PWD/python python3 python/bin/conformance.py $D/99-probe.selt 2>&1 | tail -6
```

```
JS
FAIL probe.bucket-map-sort-by-key  ($SCRATCH/verify-C2/99-probe.selt:8)
     source: LIST(RECORD("cat","B"), RECORD("cat","A"), RECORD("cat","B")) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT_BY(_K)
     want:   tree -{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"B", "n"=t"2"}}
     got tree -{"1"=-{"cat"=t"1", "n"=t"1"}, "2"=-{"cat"=t"2", "n"=t"2"}}

0 passed, 2 failed, 0 suite errors
PHP
FAIL probe.bucket-map-sort-by-key  (99-probe.selt:8)
     source: LIST(RECORD("cat","B"), RECORD("cat","A"), RECORD("cat","B")) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT_BY(_K)
     want:   tree -{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"B", "n"=t"2"}}
     got tree -{"1"=-{"cat"=t"1", "n"=t"1"}, "2"=-{"cat"=t"2", "n"=t"2"}}

0 passed, 2 failed, 0 suite errors
CPP
FAIL probe.bucket-map-sort-by-key  (99-probe.selt:8)
     source: LIST(RECORD("cat","B"), RECORD("cat","A"), RECORD("cat","B")) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT_BY(_K)
     want:   tree -{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"B", "n"=t"2"}}
     got tree -{"1"=-{"cat"=t"1", "n"=t"1"}, "2"=-{"cat"=t"2", "n"=t"2"}}

0 passed, 2 failed, 0 suite errors
LISP
FAIL probe.bucket-map-sort-by-key  (99-probe.selt:8)
     source: LIST(RECORD("cat","B"), RECORD("cat","A"), RECORD("cat","B")) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT_BY(_K)
     want:   tree -{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"B", "n"=t"2"}}
     got tree -{"1"=-{"cat"=t"1", "n"=t"1"}, "2"=-{"cat"=t"2", "n"=t"2"}}

0 passed, 2 failed, 0 suite errors
PY
FAIL probe.bucket-map-sort-by-key  ($SCRATCH/verify-C2/99-probe.selt:8)
     source: LIST(RECORD("cat","B"), RECORD("cat","A"), RECORD("cat","B")) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) .> SORT_BY(_K)
     want:   tree -{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"B", "n"=t"2"}}
     got tree -{"1"=-{"cat"=t"1", "n"=t"1"}, "2"=-{"cat"=t"2", "n"=t"2"}}

0 passed, 2 failed, 0 suite errors
```

Show the first failure and the swap rule at each host's cited line

```bash
D=$SCRATCH/verify-C2; node js/bin/conformance.mjs $D/99-probe.selt 2>&1 | head -5; echo; sed -n 5585,5600p cpp/sel.cpp; echo; sed -n 388,400p python/sel/optimizer.py; echo; sed -n 372,384p php/src/Optimizer.php; echo; sed -n 628,640p lisp/src/optimizer.lisp
```

```
FAIL probe.map-then-sort.negation  ($SCRATCH/verify-C2/99-probe.selt:1)
     source: LIST(3, 1, 2) .> MAP(0 - _) .> SORT()
     want:   tree -{"1"=t"-3", "2"=t"-2", "3"=t"-1"}
     got tree -{"1"=t"-1", "2"=t"-2", "3"=t"-3"}
FAIL probe.bucket-map-sort-by-key  ($SCRATCH/verify-C2/99-probe.selt:8)

              return std::find(fields.begin(), fields.end(), f) != fields.end();
            })) {
          next.push_back(*second);
          next.push_back(first);
          i += 2;
          changed = true;
          continue;
        }
      }
      if (second && first->s == "MAP" &&
          ((*second)->s == "TOP" || (*second)->s == "TOP_DESC" || (*second)->s == "TOP_BY" ||
           (*second)->s == "SORT" || (*second)->s == "SORT_DESC" || (*second)->s == "SORT_BY") &&
          opt_map_has_computed(*first)) {
        const auto refs = opt_sort_fields(**second);
        const auto passes = opt_map_passthroughs(*first);
        if (refs.empty() || std::all_of(refs.begin(), refs.end(), [&](const std::string& f) {

                if details['valid'] and refs and all(field in select_fields(first) for field in refs):
                    next_steps.extend((second, first))
                    i += 2
                    changed = True
                    continue
            if (second is not None and first.name == 'MAP'
                    and second.name in ('TOP', 'TOP_DESC', 'TOP_BY', 'SORT', 'SORT_DESC', 'SORT_BY')
                    and map_has_computed_fields(first)):
                details = sort_details(second)
                refs = field_refs(details['key'], details['binder'] or '_') if details['key'] else []
                if not refs or all(field in map_passthroughs(first) for field in refs):
                    next_steps.extend((second, first))
                    i += 2

                        $i++;
                        $changed = true;
                        continue;
                    }
                }
                if ($second !== null && $firstName === 'MAP'
                    && in_array($secondName, ['TOP', 'TOP_DESC', 'TOP_BY', 'SORT', 'SORT_DESC', 'SORT_BY'], true)
                    && self::mapHasComputedFields($first)) {
                    $details = self::sortDetails($second);
                    $refs = $details['key'] === null ? [] : self::fieldRefs($details['key'], $details['binder'] ?? '_');
                    if ($refs === [] || self::allIn($refs, self::mapPassthroughs($first))) {
                        $next[] = $second;
                        $next[] = $first;

                (progn
                  (push s1 new-steps)
                  (incf i 1)))))
        (setf curr-steps (nreverse new-steps)))

      ;; Pass 6: TOP_BY / SORT_BY pushdown through MAP (Late Materialization)
      (let ((new-steps '())
            (i 0)
            (len (length curr-steps)))
        (loop while (< i len) do
          (let ((s1 (nth i curr-steps))
                (s2 (when (< (1+ i) len) (nth (1+ i) curr-steps))))
            (if (and s2 (string= (node-s s1) "MAP")
```

Search docs for whether translate() is documented to optimise

```bash
/usr/bin/grep -n -i "optimizeSql\|logical rewrite\|pipeline rewrite\|before translat" docs/*.md README.md sql/*.md 2>/dev/null | head; /usr/bin/grep -n "translate.*sees\|sees the same\|same tree" docs/SQL-TRANSLATION.md | head -5
```

```
898:   sees the same list shape the evaluator would have built.
```

