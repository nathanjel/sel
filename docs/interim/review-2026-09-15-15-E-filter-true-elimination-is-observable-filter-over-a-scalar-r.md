# E. FILTER(TRUE) elimination is observable: FILTER over a scalar returns the scalar; a pipeline reduced to its bare source plans as pure_memory

**Verdict:** CONFIRMED · **severity:** medium · **introduced:** pre-existing · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing, but contradicting a promise ed16df2 wrote.

## Summary (verifier)

Both facets reproduce identically in all five hosts. (1) The optimiser's "drop a FILTER whose predicate is literal TRUE" pass fires on a scalar source, so `FILTER(5, TRUE)`, `FILTER(5, 1 > 0)`, `FILTER(5, NOT FALSE)`, `X = 1; FILTER(5, TRUE OR X)` all yield the scalar `5` (and `COUNT(FILTER(5, TRUE))` is 0, `FILTER("a", TRUE)["1"]` raises E_NO_KEY) while spec §7.3 says a scalar first argument is a one-element list and `X = TRUE; FILTER(5, X)` correctly gives `-{"1"=t"5"}` / COUNT 1. (2) Every planner unwinds the folded tree and treats "zero steps" as pure_memory, so `ORDERS .> FILTER(TRUE|NOT FALSE|1 > 0)` is classified pure_memory in JS/PHP/Python/C++/Lisp and execute_hybrid raises E_UNDEF_VAR@1:1 for ORDERS when the relation is DB-backed, while translate_statement renders the same programs as a full SELECT in all five. Neither behaviour was changed by ed16df2: the constant folds, the FILTER(TRUE) elimination, and the "optimise before unwind" planner order all exist at 8fe0e3a (verified by running the archived pre-commit JS/Python/PHP trees), and the same pure_memory answer is given for a bare `ORDERS` with no FILTER at all, which no fixture pins.

## Suggested fix — case first

Add conformance case first: `FILTER(5, TRUE)` => `-{"1"=t"5"}` (and `COUNT(FILTER(5, 1 > 0))` => 1), then in the FILTER(TRUE) elimination of all five optimisers only drop the step when its input is guaranteed a list — i.e. it is not the first step (a previous step's output) or the pipeline source is a list-producing node (`,` sequence / LIST / a pipeline call); a first-step FILTER over an arbitrary source must stay. For the planner, add `plan.pure-sql.constant-true-filter` (`ORDERS .> FILTER(TRUE)` -> pure_sql, expect `SELECT "o".* FROM "orders" "o"`) and, if the bare-source case is meant to push down, `ORDERS` -> pure_sql; implement by letting the no-steps branch try tryStatement on the bare bound relation source before returning pure_memory (js/src/sql/hybrid.mjs:311 and the four counterparts). Optionally add a translate case for `ORDERS .> FILTER(TRUE) .> TAKE(1)` to pin the currently divergent SQL text (WHERE TRUE in PHP/C++ vs none in JS/Python/Lisp).

## Verifier reasoning

Facet (1) is a spec breach that the optimiser makes uniform across hosts: the elimination site (js/src/optimizer.mjs:381-384, php/src/Optimizer.php:425-430, python/sel/optimizer.py:433-437, cpp/sel.cpp:5642, lisp/src/optimizer.lisp:735 "Pass 10") never checks whether the FILTER is the first step over a non-list source. Program.run always evaluates the physical (optimised) tree, so the unoptimised (spec) answer is only reachable by hiding the constant behind a variable. The finding's wording "now all become a bool literal" is inaccurate as to provenance — the old js optimizer at 8fe0e3a already folded NOT/AND/OR/comparisons/IF (git show 8fe0e3a:js/src/optimizer.mjs lines 63-108) and already had the elimination (line 387); the commit only changed which position a hoisted literal carries. Facet (2): every planner's no-steps branch (js/src/sql/hybrid.mjs:311, php/src/Sql/Hybrid.php:127, python/sel/sql/hybrid.py:347, cpp/sel_sql_hybrid.cpp:329, lisp/src/sql/hybrid.lisp:86) returns pure_memory when unwinding yields no steps, and the planner folds constants (translators in JS/Python explicitly pass foldConstants:false; PHP/C++ do not optimise at all; Lisp folds fully) so a fully translatable pipeline is classified as "nothing pushes down", contradicting §12.1's table. The same branch makes a bare `ORDERS` pure_memory too, so the root cause is the no-steps rule, not the fold; the fold merely widens the set of programs reaching it. Pre-commit JS and Python planners (archived 8fe0e3a trees) give the identical pure_memory classification and E_UNDEF_VAR. Severity is medium rather than high: all five hosts agree in every lane, the defect predates the commit, the lane difference needs a degenerate program (a FILTER that filters nothing) and a context without the relation rows, and the bare-source rule is an unpinned design decision rather than a stated promise. Adjacent observation (outside this cluster, reported for completeness): translate_statement SQL text for constant filters differs by host — `ORDERS .> FILTER(TRUE) .> TAKE(1)` renders `... LIMIT 1` in JS/Python/Lisp but `... WHERE TRUE LIMIT 1` in PHP/C++, and Lisp alone folds `FILTER(1 > 0)` away — no .sqlt case covers a constant predicate.

## Verifier evidence

```
REPLs (HEAD ed16df2), identical in js/php/py/cpp/lisp: `FILTER(5, TRUE)` -> 5; `FILTER(5, 1 > 0)` -> 5; `5 .> FILTER(IF(TRUE, TRUE, FALSE))` -> 5; `X = 1; FILTER(5, TRUE OR X)` -> 5; `COUNT(FILTER(5, TRUE))` -> 0; `COUNT(FILTER(5, NOT FALSE))` -> 0; `FILTER("a", TRUE)["1"]` -> E_NO_KEY at 1:18; controls: `X = TRUE; FILTER(5, X)` -> -{"1"=t"5"}; `X = TRUE; COUNT(FILTER(5, X))` -> 1; `X = TRUE; FILTER("a", X)["1"]` -> a; `LIST(5) .> FILTER(TRUE)` -> -{"1"=t"5"}. Pre-commit (git archive 8fe0e3a js python php into scratchpad/verify-E/old): old js/py/php give the same 5 / 0 / E_NO_KEY. Planner probes (scratchpad/verify-E/lanes.mjs, lanes.py, lanes.php, lanes.lisp, lanes_cpp.cpp; postgresql, ORDERS=relation orders/o {ID: column id NUM}, empty context): for `ORDERS .> FILTER(TRUE)`, `ORDERS .> FILTER(NOT FALSE)`, `ORDERS .> FILTER(1 > 0)` and bare `ORDERS` every host prints plan: pure_memory tables=["orders"], executeHybrid: ERR E_UNDEF_VAR@1:1 undefined variable ORDERS; translateStatement succeeds in every host (JS/PY: `SELECT "o".* FROM "orders" "o"` / `... WHERE (NOT FALSE)` / `... WHERE (1 > 0)`; PHP/C++: `... WHERE TRUE` / `WHERE (NOT FALSE)` / `WHERE (1 > 0)`; Lisp: `SELECT "o".* FROM "orders" "o"` for all three). `ORDERS .> FILTER(TRUE) .> TAKE(1)` plans pure_sql everywhere (SQL `SELECT "o".* FROM "orders" "o" LIMIT 1`). Old JS/Python planners (lanes_old.mjs, lanes.py with PYTHONPATH=old/python): same pure_memory + E_UNDEF_VAR (only source_tables differs: "ORDERS" then, "orders" now). Code: js/src/optimizer.mjs:381-384; php/src/Optimizer.php:425-430; python/sel/optimizer.py:433-437; cpp/sel.cpp:5642; lisp/src/optimizer.lisp:735; js/src/sql/hybrid.mjs:311-314; php/src/Sql/Hybrid.php:127-131; python/sel/sql/hybrid.py:347-350; cpp/sel_sql_hybrid.cpp:329-333; lisp/src/sql/hybrid.lisp:86-91. Pre-commit: git show 8fe0e3a:js/src/optimizer.mjs lines 63,76,98,107 (folds) and 387 (elimination); git show 8fe0e3a:js/src/sql/hybrid.mjs:227-232 (optimizeAstLogical then `!steps.length` -> pureMemory); same in 8fe0e3a python/sel/sql/hybrid.py:282, php/src/Sql/Hybrid.php:106-116, cpp/sel_sql_hybrid.cpp:301-306, lisp/src/sql/hybrid.lisp:32-53. Spec: spec/SPEC.md:671-673 (scalar first argument is a one-element list). Fixtures: sql/cases/25-hybrid-plans.sqlt has no case for a bare relation or a constant-TRUE filter; conformance has no `FILTER(<scalar>, TRUE)` case.
```

## Original review reports (deduplicated into this finding)

### [fold-positions] Folded-to-TRUE filter predicates make the optimiser visible: FILTER over a scalar returns the scalar, and a relation pipeline reduced to its bare source plans as pure_memory

*correctness · medium · hosts: js, php, python, cpp, lisp*

Locations: `js/src/optimizer.mjs:382`; `php/src/Optimizer.php:426`; `python/sel/optimizer.py:435`; `cpp/sel.cpp:5642`; `lisp/src/optimizer.lisp:735`; `js/src/sql/hybrid.mjs:311`; `python/sel/sql/hybrid.py:347`; `php/src/Sql/Hybrid.php:127`; `lisp/src/sql/hybrid.lisp:86`; `cpp/sel_sql_hybrid.cpp:329`

The trivial `FILTER(TRUE)` elimination is pre-existing, but the fold feeds it: `1 > 0`, `IF(TRUE, TRUE, FALSE)`, `NOT FALSE`, `TRUE OR x` now all become a bool literal and the step is dropped. Two consequences contradict 'the optimiser is invisible'. (1) FILTER wraps a scalar source into a one-element list, so `FILTER(5, TRUE)` / `5 .> FILTER(1 > 0)` evaluate unoptimised to (5) and after optimisation to the scalar 5 — while `X = TRUE; FILTER(5, X)` still gives (5). All five hosts agree on the optimised answer, so the fuzzer is blind to it. (2) In the planner, `ORDERS .> FILTER(1 > 0)` unwinds to zero steps after folding, and every host's plan_hybrid then classifies a fully-translatable pipeline (translate_statement emits `SELECT "o".* FROM "orders" "o" WHERE (1 > 0)`) as pure_memory; execute_hybrid then evaluates the original program in memory and raises E_UNDEF_VAR at 1:1 for ORDERS, because a DB-backed relation is not in the context. This holds in JS, PHP, Python, Lisp and C++ alike (all report pure_memory), so it is coherent but wrong against §12.1's 'it plans the tree the translator will see' — the translator would have pushed it down. A guard in the no-steps branch (a bare relation source is a pure-SQL `SELECT *`, or refuse to drop the last step) fixes (2); (1) needs the elimination to keep a FILTER over a non-pipeline source or to be spec'd.

Reported repro:

```
JS unoptimised vs optimised (scratchpad diff.mjs):
  5 .> FILTER(TRUE)               raw=ok {"1":"5"}  opt=ok "5"
  FILTER(5, 1 > 0)                raw=ok {"1":"5"}  opt=ok "5"
  5 .> FILTER(IF(TRUE, TRUE, FALSE)) raw=ok {"1":"5"} opt=ok "5"
All five REPLs: `FILTER(5, 1 > 0)` -> 5 ; `X = TRUE; FILTER(5, X)` -> -{"1"=t"5"}.
Planner, `ORDERS .> FILTER(1 > 0)` with ORDERS relation binding: JS plan=pure_memory, executeHybrid -> E_UNDEF_VAR@1:1 undefined variable ORDERS (context without ORDERS); translateStatement -> SELECT "o".* FROM "orders" "o" WHERE (1 > 0). PHP/PY/LISP/CPP plan: pure_memory for `ORDERS .> FILTER(IF(TRUE, TRUE, FALSE))` and `ORDERS .> FILTER(1 > 0)` (lanes.php / lanes.py / lanes.lisp / lanes_cpp).
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$S/lanes.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const bindings = {"ORDERS": {"kind": "relation", "from": "orders", "alias": "o", "fields": {"ID": {"table": "o", "column": "id", "type": "NUM"}}}};
const runner = (sql) => { console.log('   db got:', sql.sql ?? sql); return [{ID: '1'}, {ID: '2'}]; };
for (const src of ['ORDERS .> FILTER(TRUE)', 'ORDERS .> FILTER(NOT FALSE)', 'ORDERS .> FILTER(1 > 0)', 'ORDERS .> FILTER(TRUE) .> TAKE(1)', 'ORDERS .> TAKE(1)', 'ORDERS', 'ORDERS .> FILTER(_["ID"] > 1) .> FILTER(TRUE)']) {
  const p = compile(src);
  let ts; try { ts = Sql.translateStatement(p, 'postgresql', bindings).sql; } catch (e) { ts = 'ERR ' + (e.code ?? e.message); }
  const plan = Sql.planHybrid(p, 'postgresql', bindings);
  const cls = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  let ex; try { const r = Sql.executeHybrid(plan, runner, {}); ex = JSON.stringify(r.toNative ? r.toNative() : r); } catch (e) { ex = 'ERR ' + e.code + '@' + e.line + ':' + e.column + ' ' + e.message; }
  console.log(`${src}\n   translateStatement: ${ts}\n   plan: ${cls}  tables=${JSON.stringify(plan.sourceTables)}\n   executeHybrid: ${ex}`);
}
```

**`$S/lanes.py`**

```python
from sel import compile as sel_compile
from sel.sql import Sql, Binding
from sel.errors import SelError
b = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}
rows = [{'id': '1'}, {'id': '2'}]
def runner(sql, params):
    print('   db got:', sql); return rows
for src in ['ORDERS .> FILTER(TRUE)', 'ORDERS .> FILTER(NOT FALSE)', 'ORDERS .> FILTER(1 > 0)', 'ORDERS .> FILTER(TRUE) .> TAKE(1)', 'ORDERS']:
    p = sel_compile(src)
    try: ts = Sql.translate_statement(p, 'postgresql', b).as_statement()
    except Exception as e: ts = 'ERR ' + repr(e)
    plan = Sql.plan_hybrid(p, 'postgresql', b)
    cls = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    try: ex = Sql.execute_hybrid(plan, runner, {}).to_native()
    except SelError as e: ex = f'ERR {e.code}@{e.line}:{e.col} {e}'
    print(f'{src}\n   translate_statement: {ts}\n   plan: {cls} tables={plan.source_tables}\n   execute_hybrid: {ex}')
```

**`$S/lanes.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/bootstrap.php';
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$b = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
$rows = [['id' => '1'], ['id' => '2']];
$runner = function ($sql) use ($rows) { echo "   db got: ", (is_object($sql) ? $sql->asStatement() : $sql), "\n"; return $rows; };
foreach (['ORDERS .> FILTER(TRUE)', 'ORDERS .> FILTER(NOT FALSE)', 'ORDERS .> FILTER(1 > 0)', 'ORDERS .> FILTER(TRUE) .> TAKE(1)', 'ORDERS'] as $src) {
  $p = Sel::compile($src);
  try { $ts = Sql::translateStatement($p, 'postgresql', $b)->asStatement(); } catch (\Throwable $e) { $ts = 'ERR ' . $e->getMessage(); }
  $plan = Sql::planHybrid($p, 'postgresql', $b);
  $cls = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
  try { $ex = json_encode(Sql::executeHybrid($plan, $runner, [])); } catch (\Sel\SelError $e) { $ex = 'ERR ' . $e->code . '@' . $e->line . ':' . $e->col . ' ' . $e->getMessage(); } catch (\Throwable $e) { $ex = 'ERR ' . get_class($e) . ' ' . $e->getMessage(); }
  echo "$src\n   translateStatement: $ts\n   plan: $cls tables=" . json_encode($plan->sourceTables) . "\n   executeHybrid: $ex\n";
}
```

**`$S/lanes.lisp`**

```lisp
(load "/home/nathan/workspaces/nth-share/sel/lisp/bin/boot.lisp")
(let* ((orders (sel.sql:binding-relation "orders" "o" (list (cons "ID" (sel.sql:binding-column "id" "o" "NUM")))))
       (bindings (list (cons "ORDERS" orders)))
       (runner (lambda (sql params) (declare (ignore params)) (format t "   db got: ~a~%" sql)
                 (sel:evaluate "LIST(RECORD('id', 1), RECORD('id', 2))"))))
  (dolist (src '("ORDERS .> FILTER(TRUE)" "ORDERS .> FILTER(NOT FALSE)" "ORDERS .> FILTER(1 > 0)" "ORDERS .> FILTER(TRUE) .> TAKE(1)" "ORDERS"))
    (let* ((p (sel:compile-source src))
           (ts (handler-case (sel.sql:as-statement (sel.sql:translate-statement p "postgresql" bindings))
                 (error (e) (format nil "ERR ~a" e))))
           (plan (sel.sql:plan-hybrid p "postgresql" bindings))
           (cls (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql") ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory") (t "hybrid")))
           (ex (handler-case (sel:to-native (sel.sql:execute-hybrid plan runner))
                 (sel:sel-error (e) (format nil "ERR ~a@~a:~a" (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e)))
                 (error (e) (format nil "ERR ~a" e)))))
      (format t "~a~%   translate-statement: ~a~%   plan: ~a tables=~s~%   execute-hybrid: ~s~%" src ts cls (sel.sql:hybrid-plan-source-tables plan) ex))))
```

**`$S/lanes_cpp.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main() {
  Bindings bindings;
  bindings.set("ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)}}));
  const sel::Value rows = sel::Value::list({sel::Value::record({"id"}, {sel::Value::text("1")}), sel::Value::record({"id"}, {sel::Value::text("2")})});
  for (const char* src : {"ORDERS .> FILTER(TRUE)", "ORDERS .> FILTER(NOT FALSE)", "ORDERS .> FILTER(1 > 0)", "ORDERS .> FILTER(TRUE) .> TAKE(1)", "ORDERS"}) {
    sel::Program p = sel::compile(src);
    std::string ts; try { ts = Sql::translate_statement(p, "postgresql", bindings).as_statement(); } catch (const std::exception& e) { ts = std::string("ERR ") + e.what(); }
    const sel::sql::HybridPlan plan = Sql::plan_hybrid(p, "postgresql", bindings);
    const std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    std::string ex;
    try {
      sel::Value ctx = sel::Value::none();
      ex = Sql::execute_hybrid(plan, [&](const std::string& sql, const std::vector<sel::Value>&) { std::cout << "   db got: " << sql << "\n"; return rows; }, ctx).dump();
    } catch (const sel::SelError& e) { ex = "ERR " + e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
    std::cout << src << "\n   translate_statement: " << ts << "\n   plan: " << kind << " tables=";
    for (auto& t : plan.source_tables) std::cout << t << ",";
    std::cout << "\n   execute_hybrid: " << ex << "\n";
  }
}
```

### Commands run and their output

Run FILTER-over-scalar probes across all five REPLs

```bash
for e in 'FILTER(5, TRUE)' 'FILTER(5, 1 > 0)' '5 .> FILTER(IF(TRUE, TRUE, FALSE))' 'X = TRUE; FILTER(5, X)' 'COUNT(FILTER(5, TRUE))' 'COUNT(FILTER(5, NOT FALSE))' 'FILTER(NULL, TRUE)' 'FILTER("a", TRUE)["1"]' ; do echo "== $e"; echo -n "js:   "; node js/bin/sel.mjs -e "$e" 2>&1; echo -n "php:  "; php php/bin/sel -e "$e" 2>&1; echo -n "py:   "; PYTHONPATH=$PWD/python python3 -m sel -e "$e" 2>&1; echo -n "cpp:  "; cpp/build/sel -e "$e" 2>&1; echo -n "lisp: "; lisp/bin/sel -e "$e" 2>&1; done
```

```
Exit code 1
== FILTER(5, TRUE)
js:   5
php:  5
py:   5
cpp:  5
lisp: 5
== FILTER(5, 1 > 0)
js:   5
php:  5
py:   5
cpp:  5
lisp: 5
== 5 .> FILTER(IF(TRUE, TRUE, FALSE))
js:   5
php:  5
py:   5
cpp:  5
lisp: 5
== X = TRUE; FILTER(5, X)
js:   -{"1"=t"5"}
php:  -{"1"=t"5"}
py:   -{"1"=t"5"}
cpp:  -{"1"=t"5"}
lisp: -{"1"=t"5"}
== COUNT(FILTER(5, TRUE))
js:   0
php:  0
py:   0
cpp:  0
lisp: 0
== COUNT(FILTER(5, NOT FALSE))
js:   0
php:  0
py:   0
cpp:  0
lisp: 0
== FILTER(NULL, TRUE)
js:   -
php:  -
py:   -
cpp:  -
lisp: -
== FILTER("a", TRUE)["1"]
js:   E_NO_KEY at line 1 column 18: no key "1"
php:  E_NO_KEY at line 1 column 18: no key "1"
py:   E_NO_KEY at line 1 column 18: no key "1"
cpp:  E_NO_KEY at line 1 column 18: no key "1"
lisp: E_NO_KEY at line 1 column 18: no key "1"
```

Run unoptimised control probes and find optimiser options in JS host API

```bash
for e in 'X = TRUE; COUNT(FILTER(5, X))' 'X = TRUE; FILTER("a", X)["1"]' 'FILTER(NULL, TRUE) EQL NULL' 'X = TRUE; FILTER(NULL, X) EQL NULL' 'COUNT(FILTER(NULL, TRUE))'; do echo "== $e"; echo -n "js:   "; node js/bin/sel.mjs -e "$e" 2>&1; echo -n "php:  "; php php/bin/sel -e "$e" 2>&1; echo -n "py:   "; PYTHONPATH=$PWD/python python3 -m sel -e "$e" 2>&1; echo -n "cpp:  "; cpp/build/sel -e "$e" 2>&1; echo -n "lisp: "; lisp/bin/sel -e "$e" 2>&1; done; grep -n "optimi\|fold\|fuse" js/src/sel.mjs | head -20
```

```
== X = TRUE; COUNT(FILTER(5, X))
js:   1
php:  1
py:   1
cpp:  1
lisp: 1
== X = TRUE; FILTER("a", X)["1"]
js:   a
php:  a
py:   a
cpp:  a
lisp: a
== FILTER(NULL, TRUE) EQL NULL
js:   TRUE
php:  TRUE
py:   TRUE
cpp:  TRUE
lisp: TRUE
== X = TRUE; FILTER(NULL, X) EQL NULL
js:   TRUE
php:  TRUE
py:   TRUE
cpp:  TRUE
lisp: TRUE
== COUNT(FILTER(NULL, TRUE))
js:   0
php:  0
py:   0
cpp:  0
lisp: 0
9:import { optimizeAst, optimizeAstLogical, optimizeAstInMemory } from './optimizer.mjs';
16:    // optimiser and the planner copy on the way down and never write into it
21:    // The physical tree run() evaluates: `ast` after the in-memory optimiser,
38:  // The optimised tree run() evaluates, built once per `ast`.
41:      this._physical = optimizeAst(this.ast);
225:  optimizeAst, optimizeAstLogical, optimizeAstInMemory,
```

Run the same probes against the pre-commit JS, Python and PHP hosts

```bash
S=$SCRATCH/verify-E && mkdir -p $S/old && git archive 8fe0e3a js python php | tar -x -C $S/old && for e in 'FILTER(5, TRUE)' 'FILTER(5, 1 > 0)' 'COUNT(FILTER(5, TRUE))' 'X = TRUE; COUNT(FILTER(5, X))' 'FILTER("a", TRUE)["1"]'; do echo "== $e"; echo -n "old js:  "; node $S/old/js/bin/sel.mjs -e "$e" 2>&1; echo -n "old py:  "; PYTHONPATH=$S/old/python python3 -m sel -e "$e" 2>&1; echo -n "old php: "; php $S/old/php/bin/sel -e "$e" 2>&1; done
```

```
Exit code 1
== FILTER(5, TRUE)
old js:  5
old py:  5
old php: 5
== FILTER(5, 1 > 0)
old js:  5
old py:  5
old php: 5
== COUNT(FILTER(5, TRUE))
old js:  0
old py:  0
old php: 0
== X = TRUE; COUNT(FILTER(5, X))
old js:  1
old py:  1
old php: 1
== FILTER("a", TRUE)["1"]
old js:  E_NO_KEY at line 1 column 18: no key "1"
old py:  E_NO_KEY at line 1 column 18: no key "1"
old php: E_NO_KEY at line 1 column 18: no key "1"
```

Probe JS lanes for FILTER(TRUE) pipelines over a relation binding

```bash
S=$SCRATCH/verify-E && # (file written, see "Reproduction scripts" above)
node $S/lanes.mjs
```

```
Exit code 1
file:///home/nathan/workspaces/nth-share/sel/js/src/sql/bindings.mjs:27
        throw new SqlError('E_SQL_BINDING',
              ^

SqlError: the binding for ORDERS is a object; build one with Binding.column(), .columns(), .relation(), .relationQuery(), .raw() or .value()
    at new Bindings (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/bindings.mjs:27:15)
    at planHybrid (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/hybrid.mjs:287:61)
    at Sql.planHybrid (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs:69:12)
    at file://$SCRATCH/verify-E/lanes.mjs:8:20
    at ModuleJob.run (node:internal/modules/esm/module_job:271:25)
    at async onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:547:26)
    at async asyncRunEntryPointWithESMLoader (node:internal/modules/run_main:116:5) {
  code: 'E_SQL_BINDING',
  line: 0,
  col: 0,
  offset: 0
}

Node.js v22.12.0
```

Rerun JS lane probe with proper Binding.relation

```bash
S=$SCRATCH/verify-E && sed -i 's|^const bindings = .*|const bindings = { ORDERS: Binding.relation("orders", "o", { ID: Binding.column("id", "o", "NUM") }, null, null) };|; s|import { Sql }|import { Sql, Binding }|' $S/lanes.mjs && node $S/lanes.mjs
```

```
ORDERS .> FILTER(TRUE)
   translateStatement: undefined
   plan: pure_memory  tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
ORDERS .> FILTER(NOT FALSE)
   translateStatement: undefined
   plan: pure_memory  tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
ORDERS .> FILTER(1 > 0)
   translateStatement: undefined
   plan: pure_memory  tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
   db got: SELECT "o".* FROM "orders" "o" LIMIT 1
ORDERS .> FILTER(TRUE) .> TAKE(1)
   translateStatement: undefined
   plan: pure_sql  tables=["orders"]
   executeHybrid: [{"ID":"1"},{"ID":"2"}]
   db got: SELECT "o".* FROM "orders" "o" LIMIT 1
ORDERS .> TAKE(1)
   translateStatement: undefined
   plan: pure_sql  tables=["orders"]
   executeHybrid: [{"ID":"1"},{"ID":"2"}]
ORDERS
   translateStatement: undefined
   plan: pure_memory  tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
   db got: SELECT "o".* FROM "orders" "o" WHERE (("o"."id" > 1) AND TRUE)
ORDERS .> FILTER(_["ID"] > 1) .> FILTER(TRUE)
   translateStatement: undefined
   plan: pure_sql  tables=["orders"]
   executeHybrid: [{"ID":"1"},{"ID":"2"}]
```

Rerun JS lane probe printing SQL text

```bash
S=$SCRATCH/verify-E && sed -i 's|Sql.translateStatement(p, .postgresql., bindings).sql|Sql.translateStatement(p, "postgresql", bindings).asStatement()|; s|console.log(.   db got:., sql.sql ?? sql)|console.log("   db got:", sql.asStatement ? sql.asStatement() : sql)|' $S/lanes.mjs && node $S/lanes.mjs
```

```
ORDERS .> FILTER(TRUE)
   translateStatement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory  tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
ORDERS .> FILTER(NOT FALSE)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE (NOT FALSE)
   plan: pure_memory  tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
ORDERS .> FILTER(1 > 0)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE (1 > 0)
   plan: pure_memory  tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
   db got: SELECT "o".* FROM "orders" "o" LIMIT 1
ORDERS .> FILTER(TRUE) .> TAKE(1)
   translateStatement: SELECT "o".* FROM "orders" "o" LIMIT 1
   plan: pure_sql  tables=["orders"]
   executeHybrid: [{"ID":"1"},{"ID":"2"}]
   db got: SELECT "o".* FROM "orders" "o" LIMIT 1
ORDERS .> TAKE(1)
   translateStatement: SELECT "o".* FROM "orders" "o" LIMIT 1
   plan: pure_sql  tables=["orders"]
   executeHybrid: [{"ID":"1"},{"ID":"2"}]
ORDERS
   translateStatement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory  tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
   db got: SELECT "o".* FROM "orders" "o" WHERE (("o"."id" > 1) AND TRUE)
ORDERS .> FILTER(_["ID"] > 1) .> FILTER(TRUE)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1)
   plan: pure_sql  tables=["orders"]
   executeHybrid: [{"ID":"1"},{"ID":"2"}]
```

Probe Python lanes for FILTER(TRUE) pipelines

```bash
S=$SCRATCH/verify-E && # (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $S/lanes.py
```

```
Exit code 1
ORDERS .> FILTER(TRUE)
   translate_statement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory tables=['orders']
   execute_hybrid: ERR E_UNDEF_VAR@1:1 E_UNDEF_VAR at 1:1: undefined variable ORDERS
ORDERS .> FILTER(NOT FALSE)
   translate_statement: SELECT "o".* FROM "orders" "o" WHERE (NOT FALSE)
   plan: pure_memory tables=['orders']
   execute_hybrid: ERR E_UNDEF_VAR@1:1 E_UNDEF_VAR at 1:1: undefined variable ORDERS
ORDERS .> FILTER(1 > 0)
   translate_statement: SELECT "o".* FROM "orders" "o" WHERE (1 > 0)
   plan: pure_memory tables=['orders']
   execute_hybrid: ERR E_UNDEF_VAR@1:1 E_UNDEF_VAR at 1:1: undefined variable ORDERS
   db got: SELECT "o".* FROM "orders" "o" LIMIT 1
Traceback (most recent call last):
  File "$SCRATCH/verify-E/lanes.py", line 14, in <module>
    try: ex = Sql.execute_hybrid(plan, runner, {}).to_native()
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AttributeError: 'list' object has no attribute 'to_native'
```

Rerun Python probe

```bash
S=$SCRATCH/verify-E && sed -i 's|Sql.execute_hybrid(plan, runner, {}).to_native()|Sql.execute_hybrid(plan, runner, {})|' $S/lanes.py && PYTHONPATH=$PWD/python python3 $S/lanes.py 2>&1 | tail -8
```

```
ORDERS .> FILTER(TRUE) .> TAKE(1)
   translate_statement: SELECT "o".* FROM "orders" "o" LIMIT 1
   plan: pure_sql tables=['orders']
   execute_hybrid: [{'id': '1'}, {'id': '2'}]
ORDERS
   translate_statement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory tables=['orders']
   execute_hybrid: ERR E_UNDEF_VAR@1:1 E_UNDEF_VAR at 1:1: undefined variable ORDERS
```

Probe PHP lanes for FILTER(TRUE) pipelines

```bash
S=$SCRATCH/verify-E && sed -n 1,20p tools/check-php-optimizer.php | grep -n "require\|use " ; # (file written, see "Reproduction scripts" above)
php $S/lanes.php
```

```
7:require_once __DIR__ . '/../php/src/Sql/bootstrap.php';
9:use Sel\Optimizer;
10:use Sel\Sel;
11:use Sel\Dec;
12:use Sel\Sql\Binding;
13:use Sel\Sql\Sql;
ORDERS .> FILTER(TRUE)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE TRUE
   plan: pure_memory tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:1 undefined variable ORDERS
ORDERS .> FILTER(NOT FALSE)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE (NOT FALSE)
   plan: pure_memory tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:1 undefined variable ORDERS
ORDERS .> FILTER(1 > 0)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE (1 > 0)
   plan: pure_memory tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:1 undefined variable ORDERS
   db got: SELECT "o".* FROM "orders" "o" LIMIT 1
ORDERS .> FILTER(TRUE) .> TAKE(1)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE TRUE LIMIT 1
   plan: pure_sql tables=["orders"]
   executeHybrid: [{"id":"1"},{"id":"2"}]
ORDERS
   translateStatement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory tables=["orders"]
   executeHybrid: ERR E_UNDEF_VAR@1:1 undefined variable ORDERS
```

Find Lisp error accessors and exported symbols

```bash
S=$SCRATCH/verify-E && grep -n "sel-error-code\|error-line\|error-col\|define-condition sel-error" lisp/src/errors.lisp | head -5; grep -n "hybrid-plan-source-tables\|execute-hybrid" lisp/src/sql/package.lisp | head
```

```
18:(define-condition sel-error (error)
19:  ((code :initarg :code :reader sel-error-code)
21:   (line :initarg :line :initform 0 :reader sel-error-line)
22:   (col :initarg :col :initform 0 :reader sel-error-col)
26:                     (sel-error-code c) (sel-error-line c) (sel-error-col c)
105:   #:hybrid-plan-source-tables
107:   #:execute-hybrid))
```

Probe Lisp lanes for FILTER(TRUE) pipelines

```bash
S=$SCRATCH/verify-E && # (file written, see "Reproduction scripts" above)
sbcl --non-interactive --load $S/lanes.lisp 2>&1 | grep -v "^;" | tail -25
```

```
Package SEL.SQL does not exist.

      Line: 2, Column: 40, File-Position: 105

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-E/lanes.lisp" {1200C321D3}>

Backtrace for: #<SB-THREAD:THREAD tid=753262 "main thread" RUNNING {1204020103}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201EB5743}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201EB5743}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201EB5743}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201EB5743}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201EB56F3}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-E/lanes.lisp" {1200C321D3}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {7BC9076E74CB}> #<SB-C::SOURCE-INFO {1200C3E583}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-E/lanes.lisp" {1200C321D3}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-E/lanes.lisp" {1200C321D3}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {7BC9076E788B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-E/lanes.lisp" {1200C321D3}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-E/lanes.lisp" {1200C321D3}>)
9: (LOAD #P"$SCRATCH/verify-E/lanes.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "$SCRATCH/verify-E/lanes.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Run Lisp lane probe after loading the SQL system

```bash
S=$SCRATCH/verify-E && sed -i '1d' $S/lanes.lisp && grep -n "quickload\|sel-lang" lisp/bin/sqlt.lisp | head -3; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(ql:quickload :sel-lang/sql :silent t)' --load $S/lanes.lisp 2>&1 | grep -v "^;" | tail -25
```

```
23:;;; boot.lisp brings in :sel-lang; the SQL layer is its own system, for the
26:  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
2: (INVOKE-DEBUGGER #<SEL.SQL:SQL-ERROR {1202445C93}>)
3: (ERROR SEL.SQL:SQL-ERROR :CODE "E_SQL_BINDING" :MESSAGE "a binding has type \"NUM\"; use one of NUM, TEXT, BOOL, BIN, UNKNOWN, LIST, STATEMENT" :LINE 0 :COL 0 :OFFSET 0)
4: (SEL.SQL::REFUSE "E_SQL_BINDING" "a binding has type \"NUM\"; use one of NUM, TEXT, BOOL, BIN, UNKNOWN, LIST, STATEMENT" NIL)
5: (SEL.SQL:BINDING-COLUMN "id" "o" "NUM" :EXACT NIL :SARGABLE NIL :GUARD NIL :COLLATION NIL :PREFILTER NIL :SPLIT-SARGABLE NIL)
6: ("top level form") [toplevel]
7: ((FLET "G" :IN SB-C::%COMPILE-IN-LEXENV))
8: (SB-C::%COMPILE-IN-LEXENV (LET* ((ORDERS (SEL.SQL:BINDING-RELATION "orders" "o" (LIST #))) (BINDINGS (LIST (CONS "ORDERS" ORDERS))) (RUNNER (LAMBDA (SQL PARAMS) (DECLARE #) (FORMAT T "   db got: ~a~%" SQL) (SEL:EVALUATE "LIST(RECORD('id', 1), RECORD('id', 2))")))) (DOLIST (SRC (QUOTE ("ORDERS .> FILTER(TRUE)" "ORDERS .> FILTER(NOT FALSE)" "ORDERS .> FILTER(1 > 0)" "ORDERS .> FILTER(TRUE) .> TAKE(1)" "ORDERS"))) (LET* ((P #) (TS #) (PLAN #) (CLS #) (EX #)) (FORMAT T "~a~%   translate-statement: ~a~%   plan: ~a tables=~s~%   execute-hybrid: ~s~%" SRC TS CLS (SEL.SQL:HYBRID-PLAN-SOURCE-TABLES PLAN) EX)))) #<NULL-LEXENV> NIL #<SB-C::SOURCE-INFO {12022842D3}> 0 NIL T T)
9: (SB-C:EVAL-WITH-COMPILE-IN-LEXENV (LET* ((ORDERS (SEL.SQL:BINDING-RELATION "orders" "o" (LIST #))) (BINDINGS (LIST (CONS "ORDERS" ORDERS))) (RUNNER (LAMBDA (SQL PARAMS) (DECLARE #) (FORMAT T "   db got: ~a~%" SQL) (SEL:EVALUATE "LIST(RECORD('id', 1), RECORD('id', 2))")))) (DOLIST (SRC (QUOTE ("ORDERS .> FILTER(TRUE)" "ORDERS .> FILTER(NOT FALSE)" "ORDERS .> FILTER(1 > 0)" "ORDERS .> FILTER(TRUE) .> TAKE(1)" "ORDERS"))) (LET* ((P #) (TS #) (PLAN #) (CLS #) (EX #)) (FORMAT T "~a~%   translate-statement: ~a~%   plan: ~a tables=~s~%   execute-hybrid: ~s~%" SRC TS CLS (SEL.SQL:HYBRID-PLAN-SOURCE-TABLES PLAN) EX)))) #<NULL-LEXENV> #<SB-C::SOURCE-INFO {12022842D3}> 0 NIL)
10: (SB-IMPL::%SIMPLE-EVAL (LET* ((ORDERS (SEL.SQL:BINDING-RELATION "orders" "o" (LIST #))) (BINDINGS (LIST (CONS "ORDERS" ORDERS))) (RUNNER (LAMBDA (SQL PARAMS) (DECLARE #) (FORMAT T "   db got: ~a~%" SQL) (SEL:EVALUATE "LIST(RECORD('id', 1), RECORD('id', 2))")))) (DOLIST (SRC (QUOTE ("ORDERS .> FILTER(TRUE)" "ORDERS .> FILTER(NOT FALSE)" "ORDERS .> FILTER(1 > 0)" "ORDERS .> FILTER(TRUE) .> TAKE(1)" "ORDERS"))) (LET* ((P #) (TS #) (PLAN #) (CLS #) (EX #)) (FORMAT T "~a~%   translate-statement: ~a~%   plan: ~a tables=~s~%   execute-hybrid: ~s~%" SRC TS CLS (SEL.SQL:HYBRID-PLAN-SOURCE-TABLES PLAN) EX)))) #<NULL-LEXENV>)
11: (SB-INT:SIMPLE-EVAL-IN-LEXENV (LET* ((ORDERS (SEL.SQL:BINDING-RELATION "orders" "o" (LIST #))) (BINDINGS (LIST (CONS "ORDERS" ORDERS))) (RUNNER (LAMBDA (SQL PARAMS) (DECLARE #) (FORMAT T "   db got: ~a~%" SQL) (SEL:EVALUATE "LIST(RECORD('id', 1), RECORD('id', 2))")))) (DOLIST (SRC (QUOTE ("ORDERS .> FILTER(TRUE)" "ORDERS .> FILTER(NOT FALSE)" "ORDERS .> FILTER(1 > 0)" "ORDERS .> FILTER(TRUE) .> TAKE(1)" "ORDERS"))) (LET* ((P #) (TS #) (PLAN #) (CLS #) (EX #)) (FORMAT T "~a~%   translate-statement: ~a~%   plan: ~a tables=~s~%   execute-hybrid: ~s~%" SRC TS CLS (SEL.SQL:HYBRID-PLAN-SOURCE-TABLES PLAN) EX)))) #<NULL-LEXENV>)
12: (EVAL-TLF (LET* ((ORDERS (SEL.SQL:BINDING-RELATION "orders" "o" (LIST #))) (BINDINGS (LIST (CONS "ORDERS" ORDERS))) (RUNNER (LAMBDA (SQL PARAMS) (DECLARE #) (FORMAT T "   db got
… (truncated)
```

Rerun Lisp probe with keyword column type

```bash
S=$SCRATCH/verify-E && sed -i 's|(sel.sql:binding-column "id" "o" "NUM")|(sel.sql:binding-column "id" "o" :num)|' $S/lanes.lisp && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(ql:quickload :sel-lang/sql :silent t)' --load $S/lanes.lisp 2>&1 | grep -v "^;" | tail -25
```

```
ORDERS .> FILTER(TRUE)
   translate-statement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory tables=("orders")
   execute-hybrid: "ERR E_UNDEF_VAR@1:1"
ORDERS .> FILTER(NOT FALSE)
   translate-statement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory tables=("orders")
   execute-hybrid: "ERR E_UNDEF_VAR@1:1"
ORDERS .> FILTER(1 > 0)
   translate-statement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory tables=("orders")
   execute-hybrid: "ERR E_UNDEF_VAR@1:1"
   db got: SELECT "o".* FROM "orders" "o" LIMIT 1
ORDERS .> FILTER(TRUE) .> TAKE(1)
   translate-statement: SELECT "o".* FROM "orders" "o" LIMIT 1
   plan: pure_sql tables=("orders")
   execute-hybrid: (("1" ("id" . "1")) ("2" ("id" . "2")))
ORDERS
   translate-statement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory tables=("orders")
   execute-hybrid: "ERR E_UNDEF_VAR@1:1"
```

Write C++ lane probe and check the Bindings API

```bash
S=$SCRATCH/verify-E && # (file written, see "Reproduction scripts" above)
grep -n "set(\|void set\|Bindings(" cpp/sel_sql.hpp | head -5; grep -n "source_tables" cpp/sel_sql.hpp | head -2
```

```
47:  int offset() const noexcept { return pos_.offset; }
143:  void set_exact(bool v) { exact_ = v; }
144:  void set_sargable(bool v) { sargable_ = v; }
145:  void set_guard(bool v) { guard_ = v; }
147:  void set_prefilter(std::shared_ptr<Fragment> p) { prefilter_ = std::move(p); }
431:  std::vector<std::string> source_tables;
```

Compile and run the C++ lane probe

```bash
S=$SCRATCH/verify-E && sed -i 's|  Bindings bindings;\n||' $S/lanes_cpp.cpp && python3 - <<EOF
import re
p='$S/lanes_cpp.cpp'; s=open(p).read()
s=s.replace('  Bindings bindings;\n  bindings.set("ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)}}));','  const Bindings bindings({{"ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)}})}});')
open(p,'w').write(s)
EOF
cd cpp && g++ -std=c++23 -O1 -o $S/lanes_cpp $S/lanes_cpp.cpp build/sel_sql*.o build/sel.o 2>&1 | head -20 && $S/lanes_cpp
```

```
ORDERS .> FILTER(TRUE)
   translate_statement: SELECT "o".* FROM "orders" "o" WHERE TRUE
   plan: pure_memory tables=orders,
   execute_hybrid: ERR E_UNDEF_VAR@1:1
ORDERS .> FILTER(NOT FALSE)
   translate_statement: SELECT "o".* FROM "orders" "o" WHERE (NOT FALSE)
   plan: pure_memory tables=orders,
   execute_hybrid: ERR E_UNDEF_VAR@1:1
ORDERS .> FILTER(1 > 0)
   translate_statement: SELECT "o".* FROM "orders" "o" WHERE (1 > 0)
   plan: pure_memory tables=orders,
   execute_hybrid: ERR E_UNDEF_VAR@1:1
   db got: SELECT "o".* FROM "orders" "o" LIMIT 1
ORDERS .> FILTER(TRUE) .> TAKE(1)
   translate_statement: SELECT "o".* FROM "orders" "o" WHERE TRUE LIMIT 1
   plan: pure_sql tables=orders,
   execute_hybrid: -{"1"=-{"id"=t"1"}, "2"=-{"id"=t"2"}}
ORDERS
   translate_statement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory tables=orders,
   execute_hybrid: ERR E_UNDEF_VAR@1:1
```

Run the lane probe against the pre-commit JS and Python planners

```bash
S=$SCRATCH/verify-E && sed "s|/home/nathan/workspaces/nth-share/sel/js|$S/old/js|g" $S/lanes.mjs > $S/lanes_old.mjs && echo "== OLD JS (8fe0e3a)" && node $S/lanes_old.mjs 2>&1 | grep -v "db got"; sed "s|^from sel|from sel|" $S/lanes.py > /dev/null; echo "== OLD PY (8fe0e3a)"; PYTHONPATH=$S/old/python python3 $S/lanes.py 2>&1 | grep -v "db got"
```

```
== OLD JS (8fe0e3a)
ORDERS .> FILTER(TRUE)
   translateStatement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory  tables=["ORDERS"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
ORDERS .> FILTER(NOT FALSE)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE (NOT FALSE)
   plan: pure_memory  tables=["ORDERS"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
ORDERS .> FILTER(1 > 0)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE (1 > 0)
   plan: pure_memory  tables=["ORDERS"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
ORDERS .> FILTER(TRUE) .> TAKE(1)
   translateStatement: SELECT "o".* FROM "orders" "o" LIMIT 1
   plan: pure_sql  tables=["ORDERS"]
   executeHybrid: [{"ID":"1"},{"ID":"2"}]
ORDERS .> TAKE(1)
   translateStatement: SELECT "o".* FROM "orders" "o" LIMIT 1
   plan: pure_sql  tables=["ORDERS"]
   executeHybrid: [{"ID":"1"},{"ID":"2"}]
ORDERS
   translateStatement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory  tables=["ORDERS"]
   executeHybrid: ERR E_UNDEF_VAR@1:undefined undefined variable ORDERS
ORDERS .> FILTER(_["ID"] > 1) .> FILTER(TRUE)
   translateStatement: SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1)
   plan: pure_sql  tables=["ORDERS"]
   executeHybrid: [{"ID":"1"},{"ID":"2"}]
== OLD PY (8fe0e3a)
ORDERS .> FILTER(TRUE)
   translate_statement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory tables=['ORDERS']
   execute_hybrid: ERR E_UNDEF_VAR@1:1 E_UNDEF_VAR at 1:1: undefined variable ORDERS
ORDERS .> FILTER(NOT FALSE)
   translate_statement: SELECT "o".* FROM "orders" "o" WHERE (NOT FALSE)
   plan: pure_memory tables=['ORDERS']
   execute_hybrid: ERR E_UNDEF_VAR@1:1 E_UNDEF_VAR at 1:1: undefined variable ORDERS
ORDERS .> FILTER(1 > 0)
   translate_statement: SELECT "o".* FROM "orders" "o" WHERE (1 > 0)
   plan: pure_memory tables=['ORDERS']
   execute_hybrid: ERR E_UNDEF_VAR@1:1 E_UNDEF_VAR at 1:1: undefined variable ORDERS
ORDERS .> FILTER(TRUE) .> TAKE(1)
   translate_statement: SELECT "o".* FROM "orders" "o" LIMIT 1
   plan: pure_sql tables=['ORDERS']
   execute_hybrid: [{'id': '1'}, {'id': '2'}]
ORDERS
   translate_statement: SELECT "o".* FROM "orders" "o"
   plan: pure_memory tables=['ORDERS']
   execute_hybrid: ERR E_UNDEF_VAR@1:1 E_UNDEF_VAR at 1:1: undefined variable ORDERS
```

Probe more folded predicates and quote the elimination sites in Python, Lisp and PHP

```bash
for e in 'X = 1; FILTER(5, TRUE OR X)' 'LIST(5) .> FILTER(TRUE)' 'FILTER(5, FALSE OR TRUE)'; do echo "== $e"; echo -n "js: "; node js/bin/sel.mjs -e "$e"; echo -n "py: "; PYTHONPATH=$PWD/python python3 -m sel -e "$e"; echo -n "cpp: "; cpp/build/sel -e "$e"; echo -n "lisp: "; lisp/bin/sel -e "$e"; echo -n "php: "; php php/bin/sel -e "$e"; done; sed -n 425,440p python/sel/optimizer.py; sed -n 730,740p lisp/src/optimizer.lisp; sed -n 422,430p php/src/Optimizer.php
```

```
== X = 1; FILTER(5, TRUE OR X)
js: 5
py: 5
cpp: 5
lisp: 5
php: 5
== LIST(5) .> FILTER(TRUE)
js: -{"1"=t"5"}
py: -{"1"=t"5"}
cpp: -{"1"=t"5"}
lisp: -{"1"=t"5"}
php: -{"1"=t"5"}
== FILTER(5, FALSE OR TRUE)
js: 5
py: 5
cpp: 5
lisp: 5
php: 5
                changed = True
                continue
            if (second is not None and first.name in ('DISTINCT', 'DEDUPE')
                    and second.name in ('DISTINCT', 'DEDUPE')):
                next_steps.append(first)
                i += 2
                changed = True
                continue
            first_filter = filter_details(first) if first.name == 'FILTER' else None
            if (first_filter is not None and first_filter['valid']
                    and first_filter['predicate'].t == 'bool' and first_filter['predicate'].v):
                changed = True
                i += 1
                continue
            next_steps.append(first)
            i += 1
                (progn
                  (push s1 new-steps)
                  (incf i 1)))))
        (setf curr-steps (nreverse new-steps)))

      ;; Pass 10: Eliminate trivial FILTER(TRUE)
      (let ((new-steps '())
            (i 0)
            (len (length curr-steps)))
        (loop while (< i len) do
          (let* ((s (nth i curr-steps))
                    continue;
                }
                $filter = $firstName === 'FILTER' ? self::filterDetails($first) : null;
                if ($filter !== null && $filter['valid']
                    && ($filter['predicate']['t'] ?? null) === 'bool'
                    && $filter['predicate']['v'] === true) {
                    $changed = true;
                    continue;
                }
```

