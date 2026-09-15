# S. Lisp MAP fall-through emits the dependency columns in reverse order

**Status:** FIXED 2026-09-15 with the I/AI/P rework: the Lisp fall-through was rewritten to the shared structure and emits dependency columns in first-seen order (see CHANGELOG "Hybrid planner: the shape guard, completed").

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

The Lisp MAP fall-through emits dependency columns in a different order from JS/PHP/Python/C++ whenever two or more custom (non-pushable) RECORD pairs contribute dependencies: the other four append in first-seen order, Lisp accumulates with PUSHNEW (prepend) and never reverses, so the plan's sql_statement differs byte-for-byte across hosts. Reproduced with the reported program and four of my own; the final execute_hybrid rows are unaffected (columns are read by name), but the SQL prefix — the field 25-hybrid-plans.sqlt and §12.1 hold every host to — disagrees. The bug existed before ed16df2 (same PUSHNEW code in 8fe0e3a:lisp/src/sql/hybrid.lisp:153-166).

## Suggested fix — case first

In lisp/src/sql/hybrid.lisp make both accumulators first-seen ordered so they match the other four hosts: return `(nreverse refs)` from collect-field-references (line 178) and build all-deps in order (e.g. `(setf all-deps (nreverse all-deps))` right after the collection loop at line 260, or append instead of pushnew) — fix both together, since fixing only one flips program 3. Add first to sql/cases/25-hybrid-plans.sqlt a plan case with bindings ID/NAME/TOTAL (mariadb): `ORDERS .> MAP(RECORD("id", _["id"], "a", ABORT(_["name"]), "b", ABORT(_["total"])))` expecting `SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total` FROM `orders` `o``, and a second with a shared dependency across pairs (`"a", ABORT(_["name"] & _["total"]), "b", ABORT(_["customer_id"] & _["name"])`) expecting name, total, customer_id; then `node tools/gen-sql-cases.mjs`.

## Verifier reasoning

Code reading: lisp/src/sql/hybrid.lisp:256-260 builds `all-deps` with `(pushnew d all-deps ...)` and lisp/src/sql/hybrid.lisp:269-292 iterates `(dolist (d all-deps) ...)` with no reversal; additionally `collect-field-references` (lisp/src/sql/hybrid.lisp:156-178) itself returns refs in reverse-of-first-seen order (PUSHNEW, no nreverse). The two reversals cancel for a single custom pair with several deps (my program 3 agrees across hosts), but with several custom pairs the per-pair lists are prepended, so the order is pair-reversed (programs 1, 2, 5) or interleaved oddly when pairs share a dep (program 4). JS (js/src/sql/hybrid.mjs:230-238, `dependencies.push(field)`), PHP (php/src/Sql/Hybrid.php:366-372, `$dependencies[] = $field`), Python (python/sel/sql/hybrid.py:272-279, `dependencies.append(field)`) and C++ (cpp/sel_sql_hybrid.cpp:225-240, `dependencies.push_back(ref)`) all keep first-seen order, and all four agree on every probe. The existing fixtures in sql/cases/25-hybrid-plans.sqlt (lines 96-130) use a single pushable `id` with `ABORT("x")`, i.e. zero dependency columns, so the suite cannot see this. tools/fuzz-sql.sh does not fuzz plan_hybrid. Introduced: `git show 8fe0e3a:lisp/src/sql/hybrid.lisp` has the identical pushnew/dolist at lines 153-166, and JS already had the ordered `dependencies` array at line 172 — pre-existing, but inside this commit's promise (docs/SQL-TRANSLATION.md §12.1 "what sql/cases/25-hybrid-plans.sqlt holds every host to" — the SQL prefix). Severity high per the rubric: a plan field (sql_statement) that the contract requires to agree differs between hosts on ordinary input; the runtime rows do not differ, so it is a plan-text divergence rather than a wrong answer, but the hybrid fixtures assert exactly that text.

## Verifier evidence

```
Probe scripts under /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-S/ (probe.mjs, probe.py, probe.php, probe.cpp, probe.lisp; programs.txt), bindings ORDERS -> orders o with ID/NAME/TOTAL/CUSTOMER_ID, dialect mariadb, plan_hybrid then sql_statement.as_statement('inline').

Programs:
1. ORDERS .> MAP(RECORD("id", _["id"], "x", GET(_["name"], 1), "y", GET(_["total"], 1), "z", GET(_["customer_id"], 1)))  (reported repro)
2. ORDERS .> MAP(RECORD("id", _["id"], "a", ABORT(_["name"]), "b", ABORT(_["total"])))
3. ORDERS .> MAP(RECORD("id", _["id"], "a", ABORT(_["name"] & _["total"] & _["customer_id"])))
4. ORDERS .> MAP(RECORD("id", _["id"], "a", ABORT(_["name"] & _["total"]), "b", ABORT(_["customer_id"] & _["name"])))
5. ORDERS .> FILTER(_["total"] > 1) .> MAP(RECORD("id", _["id"], "a", ABORT(_["customer_id"]), "b", ABORT(_["name"]))) .> TAKE(2)

Commands: `node $D/probe.mjs $D/programs.txt`; `PYTHONPATH=$PWD/python python3 $D/probe.py ...`; `php $D/probe.php ...`; `g++ -std=c++23 -O1 -o $D/probe $D/probe.cpp cpp/build/sel_sql_*.o cpp/build/sel.o && $D/probe ...`; `sbcl --non-interactive --load lisp/bin/boot.lisp --eval '(ql:quickload :sel-lang/sql)' --load $D/probe.lisp --eval '(probe-main ...)'`.

JS, Python, PHP, C++ (identical output):
1: hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
2: hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total` FROM `orders` `o`
3: hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
4: hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
5: hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`name` AS `name` FROM `orders` `o` WHERE (`o`.`total` > 1) LIMIT 2

Lisp:
1: hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`total` AS `total`, `o`.`name` AS `name` FROM `orders` `o`
2: hybrid | SELECT `o`.`id` AS `id`, `o`.`total` AS `total`, `o`.`name` AS `name` FROM `orders` `o`
3: hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`   (agrees — single pair, double reversal cancels)
4: hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`name` AS `name`, `o`.`total` AS `total` FROM `orders` `o`
5: hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` WHERE (`o`.`total` > 1) LIMIT 2

Code: lisp/src/sql/hybrid.lisp:156-178 (collect-field-references, pushnew without nreverse), :256-260 (all-deps pushnew), :269-292 (dolist all-deps -> new-items); js/src/sql/hybrid.mjs:230-238,240-245; php/src/Sql/Hybrid.php:366-380; python/sel/sql/hybrid.py:271-287; cpp/sel_sql_hybrid.cpp:225-247. `git show 8fe0e3a:lisp/src/sql/hybrid.lisp | grep -n pushnew` -> lines 69, 83, 157 (same logic pre-commit). `node tools/gen-sql-cases.mjs --check` -> "sql cases are current — 568 case(s)"; fixtures 25-hybrid-plans.sqlt:96-130 carry zero dependency columns.
```

## Original review reports (deduplicated into this finding)

### [hybrid-planner] Lisp fall-through emits the dependency columns in reverse order, so the SQL prefix differs from the other four hosts

*coherence · high · hosts: lisp*

Locations: `lisp/src/sql/hybrid.lisp:256-260`; `lisp/src/sql/hybrid.lisp:269-292`; `js/src/sql/hybrid.mjs:230-249`; `cpp/sel_sql_hybrid.cpp:225-253`

JS/PHP/Python/C++ append dependencies in first-seen order. Lisp builds `all-deps` with PUSHNEW (prepends) and never reverses it before building `new-items`, so with two or more dependency columns the SELECT list is in reverse order. The plan's sql_statement therefore differs byte-for-byte across hosts; every fixture in 25-hybrid-plans.sqlt has at most one dependency column, which is why the suite is green.

Reported repro:

```
mariadb, `ORDERS .> MAP(RECORD("id", _["id"], "x", GET(_["name"], 1), "y", GET(_["total"], 1), "z", GET(_["customer_id"], 1)))`: js/php/py/cpp sql=SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`; lisp sql=SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`total` AS `total`, `o`.`name` AS `name` FROM `orders` `o`. Same with two ABORT pairs (probe batch 1, line 61).
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`programs.txt`**

```
ORDERS .> MAP(RECORD("id", _["id"], "x", GET(_["name"], 1), "y", GET(_["total"], 1), "z", GET(_["customer_id"], 1)))
ORDERS .> MAP(RECORD("id", _["id"], "a", ABORT(_["name"]), "b", ABORT(_["total"])))
ORDERS .> MAP(RECORD("id", _["id"], "a", ABORT(_["name"] & _["total"] & _["customer_id"])))
ORDERS .> MAP(RECORD("id", _["id"], "a", ABORT(_["name"] & _["total"]), "b", ABORT(_["customer_id"] & _["name"])))
ORDERS .> FILTER(_["total"] > 1) .> MAP(RECORD("id", _["id"], "a", ABORT(_["customer_id"]), "b", ABORT(_["name"]))) .> TAKE(2)
```

**`probe.mjs`**

```js
import { readFileSync } from 'node:fs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', {
  ID: Binding.column('id', 'o', 'NUM'), NAME: Binding.column('name', 'o', 'TEXT'),
  TOTAL: Binding.column('total', 'o', 'NUM'), CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM') }, null, null) };
for (const src of readFileSync(process.argv[2], 'utf8').split('\n').filter(Boolean)) {
  const plan = Sql.planHybrid(compile(src), 'mariadb', orders);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  console.log(kind + ' | ' + (plan.sqlStatement ? plan.sqlStatement.asStatement('inline') : '-'));
}
```

**`probe.py`**

```python
import sys
from sel import compile as sel_compile
from sel.sql import Sql, Binding
orders = {'ORDERS': Binding.relation('orders', 'o', {
    'ID': Binding.column('id', 'o', 'NUM'), 'NAME': Binding.column('name', 'o', 'TEXT'),
    'TOTAL': Binding.column('total', 'o', 'NUM'), 'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM')}, None, None)}
for src in [l for l in open(sys.argv[1]).read().split('\n') if l]:
    plan = Sql.plan_hybrid(sel_compile(src), 'mariadb', orders)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    print(kind + ' | ' + (plan.sql_statement.as_statement('inline') if plan.sql_statement is not None else '-'))
```

**`probe.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$orders = ['ORDERS' => Binding::relation('orders', 'o', [
  'ID' => Binding::column('id', 'o', 'NUM'), 'NAME' => Binding::column('name', 'o', 'TEXT'),
  'TOTAL' => Binding::column('total', 'o', 'NUM'), 'CUSTOMER_ID' => Binding::column('customer_id', 'o', 'NUM')], null, null)];
foreach (array_filter(explode("\n", file_get_contents($argv[1]))) as $src) {
  $plan = Sql::planHybrid(Sel::compile($src), 'mariadb', $orders);
  $kind = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
  echo $kind . ' | ' . ($plan->sqlStatement !== null ? $plan->sqlStatement->asStatement('inline') : '-') . "\n";
}
```

**`probe.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <fstream>
#include <iostream>
#include <string>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main(int, char** argv) {
  Bindings bindings({{"ORDERS", Binding::relation("orders", "o", {
    {"ID", Binding::column("id", "o", SqlKind::Num)}, {"NAME", Binding::column("name", "o", SqlKind::Text)},
    {"TOTAL", Binding::column("total", "o", SqlKind::Num)}, {"CUSTOMER_ID", Binding::column("customer_id", "o", SqlKind::Num)}}, std::nullopt, std::nullopt)}});
  std::ifstream in(argv[1]); std::string src;
  while (std::getline(in, src)) { if (src.empty()) continue;
    const sel::Program p = sel::compile(src);
    const auto plan = Sql::plan_hybrid(p, "mariadb", bindings);
    std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    std::cout << kind << " | " << (plan.sql_statement ? plan.sql_statement->as_statement(*sel::sql::mode_from_name("inline")) : "-") << "\n";
  }
}
```

**`probe.lisp`**

```lisp
(defun probe-main (path)
  (let* ((orders (sel.sql:binding-relation "orders" "o"
                   (list (cons "ID" (sel.sql:binding-column "id" "o" :num))
                         (cons "NAME" (sel.sql:binding-column "name" "o" :text))
                         (cons "TOTAL" (sel.sql:binding-column "total" "o" :num))
                         (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "o" :num)))
                   nil nil))
         (bindings (list (cons "ORDERS" orders))))
    (dolist (src (remove "" (sel-cli:split-lines (sel-cli:read-text-file path)) :test #'string=))
      (let* ((plan (sel.sql:plan-hybrid (sel:compile-source src) "mariadb" bindings))
             (kind (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                         ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                         (t "hybrid"))))
        (format t "~a | ~a~%" kind
                (if (sel.sql:hybrid-plan-sql-statement plan)
                    (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan))
                    "-"))))))
```

### Commands run and their output

Count hybrid cases per generated file and check staleness

```bash
for f in lisp/bin/case-data.lisp php/bin/CaseData.php python/bin/case_data.py cpp/bin/case_data.cpp js/bin/case-data.mjs; do echo "$f: $(grep -c '25-hybrid' $f)"; done; git status --short | head; node tools/gen-sql-cases.mjs --check 2>&1 | tail -5; git status --short | head
```

```
lisp/bin/case-data.lisp: 
php/bin/CaseData.php: 
python/bin/case_data.py: 33
cpp/bin/case_data.cpp: 33
js/bin/case-data.mjs: 33
sql cases are current — 568 case(s)
```

Write probe scripts for all five hosts

```bash
mkdir -p $SCRATCH/verify-S && cd $SCRATCH/verify-S && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
echo done
```

```
done
```

Run the probe in all five hosts

```bash
D=$SCRATCH/verify-S; echo "== JS"; node $D/probe.mjs $D/programs.txt; echo "== PY"; PYTHONPATH=$PWD/python python3 $D/probe.py $D/programs.txt; echo "== PHP"; php $D/probe.php $D/programs.txt; echo "== CPP"; g++ -std=c++23 -O1 -o $D/probe $D/probe.cpp cpp/build/sel_sql_binding.o cpp/build/sel_sql_emit.o cpp/build/sel_sql_hybrid.o cpp/build/sel_sql_map_data.o cpp/build/sel_sql_map.o cpp/build/sel_sql_node.o cpp/build/sel_sql.o cpp/build/sel_sql_stage1.o cpp/build/sel_sql_translator.o cpp/build/sel.o && $D/probe $D/programs.txt; echo "== LISP"; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/probe.lisp --eval "(probe-main \"$D/programs.txt\")" 2>&1 | grep -v "^;"
```

```
== JS
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`name` AS `name` FROM `orders` `o` WHERE (`o`.`total` > 1) LIMIT 2
== PY
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`name` AS `name` FROM `orders` `o` WHERE (`o`.`total` > 1) LIMIT 2
== PHP
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`name` AS `name` FROM `orders` `o` WHERE (`o`.`total` > 1) LIMIT 2
== CPP
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`name` AS `name` FROM `orders` `o` WHERE (`o`.`total` > 1) LIMIT 2
== LISP
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=798130 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 2, Column: 42, File-Position: 66

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-S/probe.lisp" {1201ED70C3}>

Backtrace for: #<SB-THREAD:THREAD tid=798130 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201ED83E3}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201ED83E3}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201ED83E3}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201ED83E3}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201ED8393}> :STREAM #<SB-
… (truncated)
```

Run the Lisp probe with the SQL system loaded

```bash
D=$SCRATCH/verify-S; echo "== LISP"; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $D/probe.lisp --eval "(probe-main \"$D/programs.txt\")" 2>&1 | grep -v "^;"
```

```
== LISP
hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`total` AS `total`, `o`.`name` AS `name` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`total` AS `total`, `o`.`name` AS `name` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`total` AS `total`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`name` AS `name`, `o`.`total` AS `total` FROM `orders` `o`
hybrid | SELECT `o`.`id` AS `id`, `o`.`name` AS `name`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` WHERE (`o`.`total` > 1) LIMIT 2
```

