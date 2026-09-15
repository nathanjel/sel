# AJ. Hybrid continuation reports an inlined helper at its definition-site column while run() reports the use site

**Status:** FIXED 2026-09-15. The planner no longer plans stage 1's tree: a helper that is a literal (after folding) is inlined at its reads stamped with the read's position, a helper read as the pipeline's source is unwound through, and any other helper is kept as an assignment in front of the SQL prefix (for the translator's own stage 1) and of the continuation (evaluated once, as `run()` does). All five hosts; `plan.helper.*` in sql/cases/25-hybrid-plans.sqlt; the per-host unit probes now execute the shapes below and compare positions with `run()`. See docs/SQL-TRANSLATION.md §12.1 and CHANGELOG.

**Verdict:** CONFIRMED · **severity:** high · **introduced:** mixed · **hosts:** js, python, php, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

Reproduced in all five hosts: when a helper assignment is inlined by stage 1 and the planner cuts a hybrid split, the in-memory continuation reports E_NOT_NUM at the helper's definition-site column (1:5) while Program.run() reports the var-read at the use site (1:45); the same happens for a folded `(FALSE AND TRUE)` helper (1:35 vs 1:55) and for a compound helper `Y = "a" & "b"` (1:9 vs 1:45). This contradicts the new §12.1 bullet "The continuation reports errors where run would". Hosts agree with each other, so cross-host parity is intact; the divergence is between lanes within every host, and each host's unit test only covers IF/AND shapes that the fold re-stamps anyway.

## Suggested fix — case first

Smallest fix: in every host's stage-1 substitute, when the planner (not translate()) is the caller and the substituted definition is a leaf literal (num/text/bool), return a copy stamped with the var read's pos — the same rule the IF/AND fold already applies — so the continuation's operand carries the use-site column; translate() keeps definition-site positions for its refusal messages. A compound helper (`Y = "a" & "b"`) still diverges under that rule, so the complete fix is for the continuation program to keep the helper assignments it references as a prefix (`Y = …; _INPUT .> MAP(…)`) instead of inlining them, or to document in §12.1 that inlined helpers report their definition site. First case to add (per host unit probe and, for the SQL side, a `plan.*` case in sql/cases/25-hybrid-plans.sqlt): `Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)` → hybrid, run() and execute_hybrid both E_NOT_NUM@1:45; second: `X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])` → both @1:55.

## Verifier reasoning

Stage 1 substitutes a var read by the assigned node with positions untouched — deliberately, and documented as right for translate() refusal messages (js/src/sql/normalise.mjs:120-123 comment, :144-146 the var branch; python/sel/sql/normalise.py:155-158; php/src/Sql/Normalise.php:165-169; cpp/sel_sql_stage1.cpp:84-93; lisp/src/sql/stage1.lisp:205-230). The planner now runs that same stage 1 before unwinding and building the continuation (js/src/sql/hybrid.mjs:300-308, and the continuation is built from the normalised steps at :330-343), so the memory half evaluates the inlined literal where run() evaluates a var read; spec §6.3 (spec/SPEC.md:467-472, "the position of the node that actually failed") plus the commit's own reasoning in CHANGELOG.md:35 ("the operand the operator was handed") make the var-read position the run()-correct one. The pure_memory path is unaffected (it runs the original program) and the MAP fall-through shapes I tried planned as pure_memory, so only genuine hybrid splits show it. Introduced status is mixed: the §12.1 promise itself is new in this commit (git show 8fe0e3a:docs/SQL-TRANSLATION.md has no "continuation reports errors" bullet; the diff adds it at docs/SQL-TRANSLATION.md:2287-2296); PHP, Python and Lisp planners already normalised before splitting at 8fe0e3a (php/src/Sql/Hybrid.php:105 Normalise::run; python/sel/sql/hybrid.py:281; lisp/src/sql/hybrid.lisp:32), so the observable behaviour pre-existed there; JS and C++ planners at 8fe0e3a did not normalise (js/src/sql/hybrid.mjs:227 optimizeAstLogical(program.ast) on the seq → source.t !== 'var' → pure memory; cpp/sel_sql_hybrid.cpp:301 likewise), so in those two hosts the divergence became reachable only with this commit's "planner normalises before the prefix search" change. Severity high per the rubric: error positions differ between the in-memory and hybrid lanes, and a §12.1 promise added by this commit is unmet; mitigating: codes agree, hosts agree, the translate() lane's 1:5 is its documented behaviour and not part of the finding.

## Verifier evidence

```
Scripts under /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-AJ/ (repro.mjs, repro.py, repro.php, repro.cpp → repro_cpp, repro.lisp, repro2.mjs, repro3.mjs). Identical output from `node repro.mjs`, `PYTHONPATH=$PWD/python python3 repro.py`, `php repro.php`, `./repro_cpp` (compiled with c++ -std=c++23 against cpp/build/*.o), and `sbcl --load lisp/bin/boot.lisp --load repro.lisp`, bindings {ORDERS: relation('orders','o')}, postgresql, rows [{id:'1'},{id:'2'}]:
Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y) → run=E_NOT_NUM@1:45 plan=hybrid execute_hybrid=E_NOT_NUM@1:5 (sql=SELECT "o".* FROM "orders" "o" LIMIT 2)
X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"]) → run=E_NOT_NUM@1:55 execute_hybrid=E_NOT_NUM@1:35
Y = "x"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _["id"]) → run=1:35 execute_hybrid=1:35 (the tested shape agrees)
own shape: Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z) → run=E_NOT_NUM@1:50 execute_hybrid=E_NOT_NUM@1:5
control: ORDERS .> TAKE(1) .> MAP(_["id"] * "q") → run=1:36 execute_hybrid=1:36
JS-only extra (repro3.mjs): Y = "a" & "b"; ORDERS .> TAKE(2) .> MAP(1 + Y) → run=E_NOT_NUM@1:45 execute_hybrid=E_NOT_NUM@1:9; repro2.mjs: Y = "x"; ORDERS .> MAP(_["id"] + Y) → pure_memory, both 1:34; Y = "x"; (ORDERS .> TAKE(2)) .> MAP(_["id"] + Y) → hybrid, run 1:47 vs execute_hybrid 1:5.
Code read: js/src/sql/normalise.mjs:120-123,144-146; python/sel/sql/normalise.py:155-158; php/src/Sql/Normalise.php:165-169; cpp/sel_sql_stage1.cpp:84-93; lisp/src/sql/stage1.lisp:205-230; js/src/sql/hybrid.mjs:286-347; docs/SQL-TRANSLATION.md:2287-2296; spec/SPEC.md:467-472; CHANGELOG.md:35-37; unit probes covering only IF/AND shapes at python/tests/test_unit.py:311-333, tools/check-php-optimizer.php:247-260, cpp/tests/sql_unit.cpp:135-156, lisp/tests/unit.lisp:1017-1049. History: `git show 8fe0e3a:docs/SQL-TRANSLATION.md | grep "continuation reports errors"` → no match; `git show 8fe0e3a:js/src/sql/hybrid.mjs` line 227 optimizeAstLogical(program.ast) with no normalise; `git show 8fe0e3a:cpp/sel_sql_hybrid.cpp` line 301 same; `git show 8fe0e3a:php/src/Sql/Hybrid.php` line 105 Normalise::run; python hybrid.py:281 and lisp hybrid.lisp:32 normalised already; `git diff 8fe0e3a ed16df2 -- lisp/src/sql/stage1.lisp python/sel/sql/normalise.py | grep -c '^[+-]'` → 0.
```

## Original review reports (deduplicated into this finding)

### [probe-lanes-fold] Hybrid continuation reports an inlined helper's definition-site column while run() reports the use site — in all five hosts, contradicting §12.1 'the continuation reports errors where run would'

*doc-claim · high · hosts: js, python, php, cpp, lisp*

Locations: `js/src/sql/normalise.mjs:120-123,144-145 (var read replaced by the assigned node, which keeps its own pos)`; `python/sel/sql/normalise.py:155-158`; `php/src/Sql/Normalise.php:165-169`; `cpp/sel_sql_stage1.cpp:88-93`; `lisp/src/sql/stage1.lisp:205-230`; `docs/SQL-TRANSLATION.md:2287-2297 (the promise)`

Stage 1 substitutes a helper variable by the node it was assigned, positions untouched (documented at normalise.mjs:120 as the right thing for a refusal message). The planner then folds and cuts that tree, so the continuation's operand is the literal at the assignment (1:5) whereas run() evaluates the var read and reports the operand it was actually handed (1:45). The commit's own §6.3 reasoning — the operator names the node it was handed — says the inlined leaf should be stamped with the var's position the way a hoisted IF branch is; the unit tests in every host only cover IF(TRUE, Y, 1), where the IF re-stamps anyway, so the shape was never observed. The hosts agree with each other, so the shared fixtures cannot see it; every host-local 'continuation reports errors where run does' test passes.

Reported repro:

```
Context {ORDERS: rows, ...}, bindings as in the harness, postgresql (identical for mariadb); identical output in JS, Python, PHP, C++ and Lisp:
Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)  →  run(): E_NOT_NUM@1:45 | plan_hybrid: hybrid, prefix SELECT "o".* FROM "orders" "o" LIMIT 2 | execute_hybrid: E_NOT_NUM@1:5 | translate(): E_SQL_INVALID@1:5
X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])  →  run(): E_NOT_NUM@1:55 | execute_hybrid: E_NOT_NUM@1:35 (the AND fold, stamped at the AND inside the assignment)
For contrast the shapes the tests cover agree: Y = "x"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _["id"]) → run 1:35, execute_hybrid 1:35; ORDERS .> TAKE(2) .> MAP(_["id"] + LABEL) (value binding) → run 1:36, execute_hybrid 1:36.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`repro.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const rows = [{id:'1'},{id:'2'}];
const bindings = { ORDERS: Binding.relation('orders', 'o') };
const sources = [
  'Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)',
  'X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])',
  'Y = "x"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _["id"])',
  // my own: helper used in FILTER, and a var read in a MAP with a different lexical shape
  'Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z)',
  'ORDERS .> TAKE(1) .> MAP(_["id"] * "q")',
];
function fail(fn){ try { const v = fn(); return 'ok ' + JSON.stringify(v && v.toNative ? v.toNative() : v);} catch(e){ return `${e.code}@${e.line}:${e.col}`; } }
for (const src of sources) {
  const p = compile(src);
  const run = fail(() => p.run({ORDERS: rows}));
  let plan; try { plan = Sql.planHybrid(p, 'postgresql', bindings); } catch(e){ console.log(src, 'PLAN ERR', e.code, e.message); continue; }
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  const ex = fail(() => Sql.executeHybrid(plan, () => rows, {ORDERS: rows}));
  console.log(`${src}\n  run=${run}  plan=${kind}  execute_hybrid=${ex}${plan.sqlStatement? '  sql='+plan.sqlStatement.asStatement('params'):''}`);
}
```

**`repro.py`**

```python
from sel import compile as sel_compile
from sel.errors import SelError
from sel.sql import Sql, Binding
rows = [{'id': '1'}, {'id': '2'}]
bindings = {'ORDERS': Binding.relation('orders', 'o')}
sources = [
  'Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)',
  'X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])',
  'Y = "x"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _["id"])',
  'Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z)',
  'ORDERS .> TAKE(1) .> MAP(_["id"] * "q")',
]
def fail(fn):
    try: fn(); return 'ok'
    except SelError as e: return f'{e.code}@{e.line}:{e.col}'
for src in sources:
    p = sel_compile(src)
    run = fail(lambda: p.run({'ORDERS': rows}))
    plan = Sql.plan_hybrid(p, 'postgresql', bindings)
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    ex = fail(lambda: Sql.execute_hybrid(plan, lambda sql, params: rows, {'ORDERS': rows}))
    print(f'{src}\n  run={run}  plan={kind}  execute_hybrid={ex}')
```

**`repro.php`**

```php
<?php
declare(strict_types=1);
require_once '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Binding; use Sel\Sql\Sql;
$rows = [['id' => '1'], ['id' => '2']];
$bindings = ['ORDERS' => Binding::relation('orders', 'o')];
$runner = static fn (string $sql, array $params): array => $rows;
$failure = static function (callable $fn): string {
    try { $fn(); return 'ok'; } catch (\Sel\SelError $e) { return "{$e->code}@{$e->line}:{$e->col}"; }
};
foreach ([
  'Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)',
  'X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])',
  'Y = "x"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _["id"])',
  'Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z)',
  'ORDERS .> TAKE(1) .> MAP(_["id"] * "q")',
] as $src) {
    $p = Sel::compile($src);
    $run = $failure(static fn () => $p->run(['ORDERS' => $rows]));
    $plan = Sql::planHybrid($p, 'postgresql', $bindings);
    $kind = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
    $ex = $failure(static fn () => Sql::executeHybrid($plan, $runner, ['ORDERS' => $rows]));
    echo "$src\n  run=$run  plan=$kind  execute_hybrid=$ex\n";
}
```

**`repro.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql;
int main() {
  Bindings bindings; bindings.add("ORDERS", Binding::relation("orders", "o", {}));
  const sel::Value rows = sel::Value::list(
      {sel::Value::record({"id"}, {sel::Value::text("1")}),
       sel::Value::record({"id"}, {sel::Value::text("2")})});
  auto failure = [](const auto& fn) -> std::string {
    try { fn(); return "ok"; } catch (const sel::SelError& e) { return e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
  };
  const char* sources[] = {
    "Y = \"x\"; ORDERS .> TAKE(2) .> MAP(_[\"id\"] + Y)",
    "X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _[\"id\"])",
    "Y = \"x\"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _[\"id\"])",
    "Z = \"abc\"; ORDERS .> TAKE(1) .> FILTER(_[\"id\"] > Z)",
    "ORDERS .> TAKE(1) .> MAP(_[\"id\"] * \"q\")",
  };
  for (const char* src : sources) {
    const sel::Program program = sel::compile(src);
    const sel::sql::HybridPlan plan = Sql::plan_hybrid(program, "postgresql", bindings);
    std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    sel::Value context = sel::Value::none(); context.set("ORDERS", rows);
    std::string run = failure([&] { sel::Value c = context.clone(); program.run(c); });
    std::string ex = failure([&] { Sql::execute_hybrid(plan, [&](const std::string&, const std::vector<sel::Value>&) { return rows; }, context); });
    std::cout << src << "\n  run=" << run << "  plan=" << kind << "  execute_hybrid=" << ex << "\n";
  }
}
```

**`$SCRATCH/verify-AJ/repro.lisp`**

```lisp
(let ((*standard-output* (make-broadcast-stream)))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
(defun failure (thunk)
  (handler-case (progn (funcall thunk) "ok")
    (sel:sel-error (e)
      (format nil "~a@~d:~d" (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e)))))
(let ((orders (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o"))))
      (rows (sel:evaluate "LIST(RECORD('id', '1'), RECORD('id', '2'))")))
  (loop for source in
        '("Y = \"x\"; ORDERS .> TAKE(2) .> MAP(_[\"id\"] + Y)"
          "X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _[\"id\"])"
          "Y = \"x\"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _[\"id\"])"
          "Z = \"abc\"; ORDERS .> TAKE(1) .> FILTER(_[\"id\"] > Z)"
          "ORDERS .> TAKE(1) .> MAP(_[\"id\"] * \"q\")")
        do (let* ((program (sel:compile-source source))
                  (plan (sel.sql:plan-hybrid program "postgresql" orders))
                  (context (sel:make-none)))
             (sel:value-set context "ORDERS" rows)
             (format t "~a~%  run=~a  plan=~a  execute_hybrid=~a~%" source
                     (failure (lambda () (sel:run program context)))
                     (cond ((sel.sql:hybrid-plan-pure-sql-p plan) :pure-sql)
                           ((sel.sql:hybrid-plan-pure-memory-p plan) :pure-memory)
                           (t :hybrid))
                     (failure (lambda () (sel.sql:execute-hybrid plan (lambda (sql params) (declare (ignore sql params)) rows) context)))))))
```

**`repro2.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const rows = [{id:'1'},{id:'2'}];
const bindings = { ORDERS: Binding.relation('orders', 'o') };
const sources = [
  'Y = "x"; ORDERS .> MAP(_["id"] + Y)',            // MAP fall-through
  'Y = "x"; ORDERS .> FILTER(_["id"] > Y)',          // pure memory? or prefix-less
  'Y = "x"; (ORDERS .> TAKE(2)) .> MAP(_["id"] + Y)',
];
function fail(fn){ try { const v = fn(); return 'ok';} catch(e){ return `${e.code}@${e.line}:${e.col}`; } }
for (const src of sources) {
  const p = compile(src);
  const run = fail(() => p.run({ORDERS: rows}));
  const plan = Sql.planHybrid(p, 'postgresql', bindings);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  const ex = fail(() => Sql.executeHybrid(plan, () => rows, {ORDERS: rows}));
  console.log(`${src}\n  run=${run}  plan=${kind}  execute_hybrid=${ex}${plan.sqlStatement? '  sql='+plan.sqlStatement.asStatement('params'):''}`);
}
```

**`repro3.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const rows = [{id:'1'},{id:'2'}];
const bindings = { ORDERS: Binding.relation('orders', 'o') };
const sources = [
  'Y = "a" & "b"; ORDERS .> TAKE(2) .> MAP(1 + Y)',   // compound helper, fails only at the use
];
function fail(fn){ try { const v = fn(); return 'ok';} catch(e){ return `${e.code}@${e.line}:${e.col}`; } }
for (const src of sources) {
  const p = compile(src);
  const run = fail(() => p.run({ORDERS: rows}));
  const plan = Sql.planHybrid(p, 'postgresql', bindings);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  const ex = fail(() => Sql.executeHybrid(plan, () => rows, {ORDERS: rows}));
  console.log(`${src}\n  run=${run}  plan=${kind}  execute_hybrid=${ex}`);
}
```

### Commands run and their output

Run the JS reproduction of the reported and own shapes

```bash
mkdir -p $SCRATCH/verify-AJ && cd $SCRATCH/verify-AJ && # (file written, see "Reproduction scripts" above)
node repro.mjs
```

```
Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)
  run=E_NOT_NUM@1:45  plan=hybrid  execute_hybrid=E_NOT_NUM@1:5  sql=SELECT "o".* FROM "orders" "o" LIMIT 2
X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])
  run=E_NOT_NUM@1:55  plan=hybrid  execute_hybrid=E_NOT_NUM@1:35  sql=SELECT "o".* FROM "orders" "o" LIMIT 2
Y = "x"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _["id"])
  run=E_NOT_NUM@1:35  plan=hybrid  execute_hybrid=E_NOT_NUM@1:35  sql=SELECT "o".* FROM "orders" "o" LIMIT 2
Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z)
  run=E_NOT_NUM@1:50  plan=hybrid  execute_hybrid=E_NOT_NUM@1:5  sql=SELECT "o".* FROM "orders" "o" LIMIT 1
ORDERS .> TAKE(1) .> MAP(_["id"] * "q")
  run=E_NOT_NUM@1:36  plan=hybrid  execute_hybrid=E_NOT_NUM@1:36  sql=SELECT "o".* FROM "orders" "o" LIMIT 1
```

Run the Python reproduction

```bash
cd $SCRATCH/verify-AJ && # (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel && PYTHONPATH=$PWD/python python3 $SCRATCH/verify-AJ/repro.py
```

```
Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)
  run=E_NOT_NUM@1:45  plan=hybrid  execute_hybrid=E_NOT_NUM@1:5
X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])
  run=E_NOT_NUM@1:55  plan=hybrid  execute_hybrid=E_NOT_NUM@1:35
Y = "x"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _["id"])
  run=E_NOT_NUM@1:35  plan=hybrid  execute_hybrid=E_NOT_NUM@1:35
Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z)
  run=E_NOT_NUM@1:50  plan=hybrid  execute_hybrid=E_NOT_NUM@1:5
ORDERS .> TAKE(1) .> MAP(_["id"] * "q")
  run=E_NOT_NUM@1:36  plan=hybrid  execute_hybrid=E_NOT_NUM@1:36
```

Run the PHP reproduction

```bash
cd $SCRATCH/verify-AJ && # (file written, see "Reproduction scripts" above)
php repro.php
```

```
Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)
  run=E_NOT_NUM@1:45  plan=hybrid  execute_hybrid=E_NOT_NUM@1:5
X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])
  run=E_NOT_NUM@1:55  plan=hybrid  execute_hybrid=E_NOT_NUM@1:35
Y = "x"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _["id"])
  run=E_NOT_NUM@1:35  plan=hybrid  execute_hybrid=E_NOT_NUM@1:35
Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z)
  run=E_NOT_NUM@1:50  plan=hybrid  execute_hybrid=E_NOT_NUM@1:5
ORDERS .> TAKE(1) .> MAP(_["id"] * "q")
  run=E_NOT_NUM@1:36  plan=hybrid  execute_hybrid=E_NOT_NUM@1:36
```

Write the C++ repro and check the Bindings API

```bash
cd $SCRATCH/verify-AJ && # (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel/cpp && grep -n "Bindings\b.*add\|void add\|class Bindings" -A3 sel_sql.hpp | head -20
```

```
327:class Bindings {
328- public:
329-  Bindings() = default;
330-  explicit Bindings(std::vector<std::pair<std::string, Binding>> bindings);
```

Build and run the C++ reproduction

```bash
cd $SCRATCH/verify-AJ && sed -i 's|Bindings bindings; bindings.add("ORDERS", Binding::relation("orders", "o", {}));|Bindings bindings({{"ORDERS", Binding::relation("orders", "o")}});|' repro.cpp && cd /home/nathan/workspaces/nth-share/sel/cpp && c++ -std=c++23 -O1 -o $SCRATCH/verify-AJ/repro_cpp $SCRATCH/verify-AJ/repro.cpp build/sel_sql_binding.o build/sel_sql_emit.o build/sel_sql_hybrid.o build/sel_sql_map_data.o build/sel_sql_map.o build/sel_sql_node.o build/sel_sql.o build/sel_sql_stage1.o build/sel_sql_translator.o build/sel.o 2>&1 | head -20 && $SCRATCH/verify-AJ/repro_cpp
```

```
Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)
  run=E_NOT_NUM@1:45  plan=hybrid  execute_hybrid=E_NOT_NUM@1:5
X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])
  run=E_NOT_NUM@1:55  plan=hybrid  execute_hybrid=E_NOT_NUM@1:35
Y = "x"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _["id"])
  run=E_NOT_NUM@1:35  plan=hybrid  execute_hybrid=E_NOT_NUM@1:35
Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z)
  run=E_NOT_NUM@1:50  plan=hybrid  execute_hybrid=E_NOT_NUM@1:5
ORDERS .> TAKE(1) .> MAP(_["id"] * "q")
  run=E_NOT_NUM@1:36  plan=hybrid  execute_hybrid=E_NOT_NUM@1:36
```

Run the Lisp reproduction

```bash
grep -n "sel-lang/sql\|quickload" lisp/bin/sqlt.lisp lisp/bin/boot.lisp | head; # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $SCRATCH/verify-AJ/repro.lisp 2>&1 | tail -15
```

```
lisp/bin/boot.lisp:17:                 (https://www.quicklisp.org) and run (ql:quickload :cl-ppcre).~%"
lisp/bin/sqlt.lisp:26:  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)
  run=E_NOT_NUM@1:45  plan=HYBRID  execute_hybrid=E_NOT_NUM@1:5
X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])
  run=E_NOT_NUM@1:55  plan=HYBRID  execute_hybrid=E_NOT_NUM@1:35
Y = "x"; ORDERS .> TAKE(2) .> MAP(IF(TRUE, Y, 1) >= _["id"])
  run=E_NOT_NUM@1:35  plan=HYBRID  execute_hybrid=E_NOT_NUM@1:35
Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z)
  run=E_NOT_NUM@1:50  plan=HYBRID  execute_hybrid=E_NOT_NUM@1:5
ORDERS .> TAKE(1) .> MAP(_["id"] * "q")
  run=E_NOT_NUM@1:36  plan=HYBRID  execute_hybrid=E_NOT_NUM@1:36
```

Probe the MAP fall-through shape and list related planner cases

```bash
cd $SCRATCH/verify-AJ && # (file written, see "Reproduction scripts" above)
node repro2.mjs; cd /home/nathan/workspaces/nth-share/sel && grep -n "^### " sql/cases/25-hybrid-plans.sqlt | grep -i "helper\|assign\|fold"
```

```
Y = "x"; ORDERS .> MAP(_["id"] + Y)
  run=E_NOT_NUM@1:34  plan=pure_memory  execute_hybrid=E_NOT_NUM@1:34
Y = "x"; ORDERS .> FILTER(_["id"] > Y)
  run=E_NOT_NUM@1:37  plan=pure_memory  execute_hybrid=E_NOT_NUM@1:37
Y = "x"; (ORDERS .> TAKE(2)) .> MAP(_["id"] + Y)
  run=E_NOT_NUM@1:47  plan=hybrid  execute_hybrid=E_NOT_NUM@1:5  sql=SELECT "o".* FROM "orders" "o" LIMIT 2
34:### name: plan.pure-sql.through-helper-assignment
397:### name: plan.fold.literal-branch-is-hoisted-into-the-prefix
416:### name: plan.fold.compound-branch-stays-an-if
440:### name: plan.fold.hoisted-literal-does-not-change-the-split
477:### name: plan.immutable.folding-does-not-write-back
499:### name: plan.immutable.folding-inside-a-kept-step
```

Probe a compound helper whose failure is only at the use site

```bash
cd $SCRATCH/verify-AJ && # (file written, see "Reproduction scripts" above)
node repro3.mjs
```

```
Y = "a" & "b"; ORDERS .> TAKE(2) .> MAP(1 + Y)
  run=E_NOT_NUM@1:45  plan=hybrid  execute_hybrid=E_NOT_NUM@1:9
```

