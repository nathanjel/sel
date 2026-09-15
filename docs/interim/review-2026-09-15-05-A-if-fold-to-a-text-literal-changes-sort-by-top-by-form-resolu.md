# A. IF fold to a text literal changes SORT_BY/TOP_BY form resolution

**Verdict:** CONFIRMED · **severity:** high · **introduced:** mixed · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

Every evaluator resolves the 3-argument SORT_BY/TOP_BY form by AST shape (text-literal at arg 2 => it is the direction; otherwise a bare name at arg 1 is the binder), and the in-memory optimiser's IF fold turns `IF(TRUE, "DESC", "ASC")` into a text literal, so `run()` and the hybrid continuation resolve a different form than the unoptimised tree: `SORT_BY((3,1,2), X, IF(TRUE,"DESC","ASC"))` is (3,1,2) unoptimised but E_UNDEF_VAR@1:20 via run() in all five hosts, and the `_` form flips from (3,1,2) to (3,2,1). In the pipeline lanes, translate_statement in JS/PHP/Python/C++ emits `ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC` (binder form) while run()/execute_hybrid raise E_UNDEF_VAR@1:42 in all five; Lisp's translate-statement additionally differs from the other four because it folds constants and refuses with E_SQL_UNBOUND@1:42. The docs/EXTENDING.md promise "the optimiser is invisible" is not met; the defect was pre-existing in JS/PHP/Python/Lisp and is newly introduced in C++ by this commit (the old C++ IF arm never fired), which also removes the fuzzer's ability to see it.

## Suggested fix — case first

Make the fold form-safe rather than removing it: in each host's IF arm (js/src/optimizer.mjs:123-127, php/src/Optimizer.php ~258, python/sel/optimizer.py ~164, cpp/sel.cpp:5346-5350, lisp/src/optimizer.lisp ~143) do not hoist a text literal into argument position 2 of a 3-argument SORT_BY/TOP_BY (or, equivalently, have the pipeline walker skip folding that argument slot when args[1] is a bare name), so the resolved form cannot change; the simplest alternative is to resolve the sort form structurally before folding and rewrite the call into the explicit 4-argument `(list, binder, key, dir)` shape. Add first the conformance case `agg.sort_by.binder-with-folded-constant-key` pinning `SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))` => (3,1,2) (and a TOP_BY twin), plus a hybrid/planner unit probe per host comparing execute_hybrid with unoptimised evaluation for `ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))`. Separately consider aligning Lisp's translate-statement with the other four by passing fold-constants off (lisp/src/sql/translator.lisp:2382), and guarding js/src/sql/translator.mjs:2235-2238 so a non-text direction refuses with E_SQL_SHAPE/E_BAD_ARG instead of a TypeError.

## Verifier reasoning

Shape-based resolution verified by reading: js/src/builtins/aggregate.mjs:214-226 and 283-293 (`args.node(2).t === 'text'` then `args.isSymbol(1)`), php/src/Builtins/Core.php:254-262, python/sel/builtins/aggregate.py:206-214, cpp/sel.cpp:3725-3740, lisp/src/builtins/aggregate.lisp:249-262; translators use the same rule (js/src/sql/translator.mjs:2226-2239, lisp/src/sql/translator.lisp:1754-1768). IF fold to a hoisted text literal: js/src/optimizer.mjs:123-127, cpp/sel.cpp:5346-5350 (and the equivalents in php/python/lisp cited by the finding). Spec §7.3 (spec/SPEC.md:656,662-666) defines `SORT_BY(list, [binder,] key [, dir])` with the binder a bare identifier node; `SORT_BY(list, X, IF(...))` is unambiguously (binder X, key IF(...)) — a constant key, legal, keeps table order — so the unoptimised answer is the spec'd one and the optimiser changes program meaning, contradicting docs/EXTENDING.md:611-613 ("The optimiser is invisible: an error a program raises after optimisation carries the same code and position as before it") and CHANGELOG.md:33-35. Lanes: js/src/sql/translator.mjs:149-150,173-174 and python/sel/sql/translator.py:165,189 run the logical optimiser with foldConstants:false; PHP (Translator.php:82,106) and C++ (sel_sql_translator.cpp:163) do not optimise at all; hybrid planners fold (js/src/sql/hybrid.mjs:309, python/sel/sql/hybrid.py:345, php/src/Sql/Hybrid.php:122, cpp/sel_sql_hybrid.cpp:327, lisp/src/sql/hybrid.lisp:84); Lisp's translate-statement (lisp/src/sql/translator.lisp:2380-2382) calls optimize-ast-logical with folding on (pre-existing, same at 8fe0e3a:2347), which is why its translate lane refuses instead of rendering CASE. Introduced status: 8fe0e3a's JS/PHP/Python/Lisp IF arms already returned the branch node (git show 8fe0e3a:js/src/optimizer.mjs:111-113 etc.) and every run() already optimised, so the misresolution is pre-existing there; 8fe0e3a:cpp/sel.cpp:5329 tested `items.size() == 4` and never fired, so C++ gave the unoptimised answer — verified by building the old C++ REPL. Severity high: a valid program's value/error changes between run() and unoptimised evaluation and between the translate and hybrid lanes, in all five hosts, and a stated promise of the commit is unmet. Side observation (not part of the cluster): JS translateStatement on `SORT_BY(_["id"], IF(TRUE,"DESC","ASC"))` throws a raw TypeError ("s is not iterable") from translator.mjs:2238 (else-branch calls asciiUpper on a non-text node) rather than a SqlError; pre-existing (8fe0e3a:2205).

## Verifier evidence

```
REPLs (all five) on `SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))`: js/php/cpp/lisp/py all print `E_UNDEF_VAR at line 1 column 20`; on the `_` form all print `-{"1"=t"3", "2"=t"2", "3"=t"1"}`; `TOP_BY((3, 1, 2), _, IF(TRUE, "DESC", "ASC"), 2)` all print `-{"1"=t"3", "2"=t"2"}`.
Own JS probe (scratchpad/verify-A/diff.mjs; evalNode(parse) vs evalNode(optimizeAst(parse)) vs compile().run()):
  SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))  raw=ok {"1":"3","2":"1","3":"2"}  opt=error E_UNDEF_VAR  run=error E_UNDEF_VAR
  SORT_BY(LIST(RECORD("n", 2), RECORD("n", 1)), R, IF(FALSE, "x", "ASC")) .> MAP(_["n"])  raw=ok {"1":"2","2":"1"}  opt/run=error E_UNDEF_VAR
  SORT_BY(("b","c","a"), _, IF(1 > 0, "DESC", "ASC"))  raw=ok {b,c,a}  opt/run=ok {c,b,a}
  TOP_BY(("b","c","a"), Q, IF(FALSE, "DESC", "ASC"), 1)  raw=ok {"1":"b"}  opt/run=error E_UNDEF_VAR
  SORT_BY((3, 1, 2), _, "DE" & "SC")  raw=opt=run=ok (3,1,2)  (no other text-producing fold exists; only the IF arm)
Lanes, my own second program `ORDERS .> FILTER(_["id"] > 0) .> TOP_BY(ROW, IF(FALSE, "x", "DESC"), 2) .> MAP(_["id"])` plus the reported one, rows id=3,1,2:
  JS (verify-A/lanes.mjs): run() E_UNDEF_VAR@1:42 / @1:41; planHybrid kind=hybrid; executeHybrid E_UNDEF_VAR@1:42 / @1:41; translateStatement ok `... ORDER BY CASE WHEN ? THEN ? ELSE ? END ASC` (params TRUE,'DESC','ASC').
  Python (verify-A/lanes.py): identical: run E_UNDEF_VAR@1:42/@1:41, hybrid, execute_hybrid same, translate_statement ok `SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY CASE WHEN 2 THEN 3 ELSE 4 END ASC`.
  PHP (verify-A/lanes.php): identical (E_UNDEF_VAR@1:42/@1:41; translateStmt ok with ORDER BY CASE ... ASC).
  C++ (verify-A/lanes_cpp.cpp linked against cpp/build/sel_sql*.o build/sel.o): run E_UNDEF_VAR@1:42/@1:41; hybrid; execute_hybrid same; translate_statement ok `... ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC` / `CASE WHEN FALSE THEN 'x' ELSE 'DESC' END ASC LIMIT 2`.
  Lisp (verify-A/lanes2.lisp, tr.lisp): run E_UNDEF_VAR@1:42; execute-hybrid E_UNDEF_VAR@1:42; translate-statement `error E_SQL_UNBOUND@1:42` (O unbound — Lisp folds in the translate lane); with `(:no-optimize t)` it renders `... ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC` like the other four. Also Lisp translate-statement folds `ORDERS .> FILTER(_["id"] > IF(TRUE, 1, 2))` to `... > 1` while JS renders `> CASE WHEN ? THEN ? ELSE ? END`.
Pre-commit: built 8fe0e3a's cpp/sel.cpp + bin/sel.cpp (verify-A/old/sel_old): `SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))` -> `-{"1"=t"3", "2"=t"1", "3"=t"2"}` and the `_` form -> `-{"1"=t"3", "2"=t"1", "3"=t"2"}` (unoptimised answers). 8fe0e3a's JS (git archive into verify-A/oldjs): `E_UNDEF_VAR at line 1 column 20` and `-{"1"=t"3", "2"=t"2", "3"=t"1"}` (already folded). git show 8fe0e3a:cpp/sel.cpp:5329-5332 shows the dead `items.size() == 4` arm; 8fe0e3a js/src/optimizer.mjs:111-113, python/sel/optimizer.py:146-148, php/src/Optimizer.php:235-239, lisp/src/optimizer.lisp:138-144 already returned the branch node.
```

## Original review reports (deduplicated into this finding)

### [fold-positions] IF fold to a text literal changes SORT_BY/TOP_BY form resolution: run() and the hybrid continuation differ from unoptimised evaluation and from translate_statement

*correctness · high · hosts: js, php, python, cpp, lisp*

Locations: `js/src/optimizer.mjs:121`; `php/src/Optimizer.php:258`; `python/sel/optimizer.py:164`; `cpp/sel.cpp:5346`; `lisp/src/optimizer.lisp:143`; `js/src/builtins/aggregate.mjs:218`; `js/src/builtins/aggregate.mjs:283`; `php/src/Builtins/Core.php:254`; `php/src/Builtins/Structure.php:610`; `python/sel/builtins/aggregate.py:206`; `python/sel/builtins/aggregate.py:272`; `cpp/sel.cpp:3730`; `cpp/sel.cpp:3815`; `lisp/src/builtins/aggregate.lisp:249`; `lisp/src/builtins/aggregate.lisp:359`; `js/src/sql/translator.mjs:2227`; `php/src/Sql/Translator.php:2948`

The three-argument SORT_BY/TOP_BY form is disambiguated by AST shape in every evaluator: if args[2] is a *text literal node* it is the direction and args[1] the key; otherwise a bare-name args[1] is the binder and args[2] the key. The IF fold turns `IF(TRUE, "DESC", "ASC")` into a text literal node, so after optimisation the same source resolves to a different form. `SORT_BY((3,1,2), X, IF(TRUE, "DESC", "ASC"))` evaluates unoptimised to (3,1,2) with X as binder and a constant key, but run() raises E_UNDEF_VAR at 1:20 in all five hosts; `SORT_BY((3,1,2), _, IF(TRUE, "DESC", "ASC"))` yields (3,1,2) unoptimised and (3,2,1) after optimisation. This directly contradicts the commit's promise (CHANGELOG 'Constant folding keeps error positions', docs/EXTENDING.md 'The optimiser is invisible: an error a program raises after optimisation carries the same code and position as before it') — here optimisation invents an error, and elsewhere changes the value. It also splits the lanes: for `ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))` translate_statement emits `... ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC` (binder O, constant key: rows in table order) in all five hosts, while plan_hybrid folds first, cannot push the SORT_BY, and execute_hybrid's continuation raises E_UNDEF_VAR@1:42 (JS and C++ verified; the same fold runs in the other three). Before this commit C++'s IF arm never fired, so C++ gave the unoptimised answer and the other four the folded one — the fuzzer could see it; making C++ fold too made all five agree on the wrong answer, so the differential fuzzer can no longer detect it. Any fold that produces a text literal from a non-literal node has this hazard; today only the IF arm does. The fix belongs with the fold (do not hoist a text literal into argument position 2 of a 3-argument SORT_BY/TOP_BY, or resolve the sort form before folding), and a conformance case should pin the unoptimised semantics.

Reported repro:

```
JS unoptimised vs optimised (scratchpad diff.mjs, evalNode(parse(src)) vs evalNode(optimizeAst(parse(src)))):
  SORT_BY((3, 1, 2), _, IF(TRUE, "DESC", "ASC"))  raw=ok {"1":"3","2":"1","3":"2"}  opt=ok {"1":"3","2":"2","3":"1"}
  SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))  raw=ok {"1":"3","2":"1","3":"2"}  opt=error E_UNDEF_VAR at 1:20
  TOP_BY((3, 1, 2), _, IF(TRUE, "DESC", "ASC"), 2) raw=ok {"1":"3","2":"1"}  opt=ok {"1":"3","2":"2"}
All five REPLs (node js/bin/sel.mjs -e / php php/bin/sel -e / cpp/build/sel -e / lisp/bin/sel -e / python -m sel -e) on `SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))` print `E_UNDEF_VAR at line 1 column 20`; on the `_` form all print -{"1"=t"3", "2"=t"2", "3"=t"1"}.
JS lanes (scratchpad exec.mjs) for `ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))` with rows id=3,1,2: unoptimised eval ok [3,1,2]; run() E_UNDEF_VAR@1:42; plan=hybrid `SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 0)`; executeHybrid E_UNDEF_VAR@1:42; translateStatement ok `... ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC`. C++ probe (scratchpad lanes_cpp) gives the same split: translate_statement renders ORDER BY CASE ..., execute_hybrid and run raise E_UNDEF_VAR 1:42.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$D/diff.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { parse } from '/home/nathan/workspaces/nth-share/sel/js/src/parser.mjs';
import { Context, evalNode } from '/home/nathan/workspaces/nth-share/sel/js/src/eval.mjs';
import { Value } from '/home/nathan/workspaces/nth-share/sel/js/src/value.mjs';
import { optimizeAst } from '/home/nathan/workspaces/nth-share/sel/js/src/optimizer.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
const srcs = [
  'SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))',
  'SORT_BY((3, 1, 2), _, IF(TRUE, "DESC", "ASC"))',
  'TOP_BY((3, 1, 2), _, IF(TRUE, "DESC", "ASC"), 2)',
  // my own: binder named R with a different constant-key branch, plus FALSE condition
  'SORT_BY(LIST(RECORD("n", 2), RECORD("n", 1)), R, IF(FALSE, "x", "ASC")) .> MAP(_["n"])',
  'SORT_BY(("b","c","a"), _, IF(1 > 0, "DESC", "ASC"))',
  'TOP_BY(("b","c","a"), Q, IF(FALSE, "DESC", "ASC"), 1)',
  // sanity: non-IF text-producing folds? there are none, e.g. "a" & "b" is not folded
  'SORT_BY((3, 1, 2), _, "DE" & "SC")',
];
const show = (r) => r.ok ? 'ok ' + JSON.stringify(r.v.toNative()) : `error ${r.e.code} at ${r.e.pos?.line}:${r.e.pos?.col}`;
const tryEval = (ast) => { try { return { ok: true, v: evalNode(ast, new Context(Value.fromNative({}))) }; } catch (e) { return { ok: false, e }; } };
for (const src of srcs) {
  const ast = parse(src);
  const raw = tryEval(ast);
  const opt = tryEval(optimizeAst(ast));
  let run; try { run = { ok: true, v: compile(src).run({}) }; } catch (e) { run = { ok: false, e }; }
  console.log(src + '\n  raw=' + show(raw) + '\n  opt=' + show(opt) + '\n  run=' + show(run));
}
```

**`$D/lanes.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
const rows = [{ id: '3' }, { id: '1' }, { id: '2' }];
const runner = () => rows;
const f = (fn) => { try { const v = fn(); return 'ok ' + JSON.stringify(v && v.toNative ? v.toNative() : v); } catch (e) { return `error ${e.code}@${e.line}:${e.col}`; } };
for (const source of [
  'ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))',
  // my own: TOP_BY with a FALSE condition and a different binder
  'ORDERS .> FILTER(_["id"] > 0) .> TOP_BY(ROW, IF(FALSE, "x", "DESC"), 2) .> MAP(_["id"])',
]) {
  const program = compile(source);
  console.log(source);
  console.log('  run():           ', f(() => program.run({ ORDERS: rows })));
  const plan = Sql.planHybrid(program, 'postgresql', orders);
  console.log('  plan kind:       ', plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid', '| sql:', plan.sql && (plan.sql.sql ?? plan.sql));
  console.log('  executeHybrid:   ', f(() => Sql.executeHybrid(plan, runner, { ORDERS: rows })));
  console.log('  translateStmt:   ', f(() => { const s = Sql.translateStatement(program, 'postgresql', orders); return s.sql ?? s; }));
}
```

**`$D/lanes.py`**

```python
from sel import compile as sel_compile
from sel.sql import Sql, Binding
from sel.errors import SelError
orders = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}
rows = [{'id': '3'}, {'id': '1'}, {'id': '2'}]
def f(fn):
    try:
        v = fn(); return 'ok ' + repr(v.to_native() if hasattr(v, 'to_native') else v)
    except SelError as e:
        return f'error {e.code}@{e.line}:{e.col}'
for source in ['ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))',
               'ORDERS .> FILTER(_["id"] > 0) .> TOP_BY(ROW, IF(FALSE, "x", "DESC"), 2) .> MAP(_["id"])']:
    p = sel_compile(source)
    print(source)
    print('  run():        ', f(lambda: p.run({'ORDERS': rows})))
    plan = Sql.plan_hybrid(p, 'postgresql', orders)
    print('  plan kind:    ', 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid')
    print('  execute_hybrid', f(lambda: Sql.execute_hybrid(plan, lambda sql, params: rows, {'ORDERS': rows})))
    s = Sql.translate_statement(p, 'postgresql', orders)
    print('  translate_stmt', f(lambda: ''.join(str(x) for x in s.parts) if hasattr(s, 'parts') else s.sql))
```

**`$D/lanes.php`**

```php
<?php
require_once '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$orders = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
$rows = [['id' => '3'], ['id' => '1'], ['id' => '2']];
$runner = static fn () => $rows;
$f = static function ($fn) { try { $v = $fn(); return 'ok ' . json_encode(is_object($v) && method_exists($v, 'toNative') ? $v->toNative() : $v); } catch (\Sel\SelError $e) { return "error {$e->code}@{$e->line}:{$e->col}"; } };
foreach (['ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))',
          'ORDERS .> FILTER(_["id"] > 0) .> TOP_BY(ROW, IF(FALSE, "x", "DESC"), 2) .> MAP(_["id"])'] as $source) {
    $program = Sel::compile($source);
    echo $source, "\n";
    echo "  run():         ", $f(static fn () => $program->run(['ORDERS' => $rows])), "\n";
    $plan = Sql::planHybrid($program, 'postgresql', $orders);
    echo "  plan kind:     ", $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid'), "\n";
    echo "  executeHybrid: ", $f(static fn () => Sql::executeHybrid($plan, $runner, ['ORDERS' => $rows])), "\n";
    echo "  translateStmt: ", $f(static function () use ($program, $orders) { $s = Sql::translateStatement($program, 'postgresql', $orders); return method_exists($s, 'toSql') ? $s->toSql() : (property_exists($s, 'sql') ? $s->sql : implode('', array_map('strval', $s->parts))); }), "\n";
}
```

**`$D/lanes.lisp`**

```lisp
(let* ((orders (sel.sql:binding-relation "orders" "o" (list (cons "ID" (sel.sql:binding-column "id" "o")))))
       (bindings (list (cons "ORDERS" orders))))
  (dolist (src '("ORDERS .> FILTER(_[\"id\"] > 0) .> SORT_BY(O, IF(TRUE, \"DESC\", \"ASC\"))"
                 "ORDERS .> FILTER(_[\"id\"] > 0) .> TOP_BY(ROW, IF(FALSE, \"x\", \"DESC\"), 2) .> MAP(_[\"id\"])"))
    (format t "~a~%" src)
    (let ((p (sel:compile-source src))
          (rows (sel:evaluate "LIST(RECORD('id', 3), RECORD('id', 1), RECORD('id', 2))")))
      (format t "  run():          ~a~%"
              (handler-case (sel:value-size (sel:run p (sel:evaluate "RECORD('ORDERS', LIST(RECORD('id', 3), RECORD('id', 1), RECORD('id', 2)))")))
                (sel:sel-error (e) (format nil "error ~a@~a:~a" (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e)))))
      (let ((plan (sel.sql:plan-hybrid p "postgresql" bindings)))
        (format t "  plan kind:      ~a~%" (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql") ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory") (t "hybrid")))
        (format t "  execute-hybrid: ~a~%"
                (handler-case (sel:value-size (sel.sql:execute-hybrid plan (lambda (sql params) (declare (ignore sql params)) rows)))
                  (sel:sel-error (e) (format nil "error ~a@~a:~a" (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e))))))
      (format t "  translate-stmt: ~a~%"
              (handler-case (sel.sql:as-statement (sel.sql:translate-statement p "postgresql" bindings))
                (sel:sel-error (e) (format nil "error ~a@~a:~a" (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e))))))))
```

**`$D/tr.lisp`**

```lisp
(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
(let* ((orders (sel.sql:binding-relation "orders" "o" (list (cons "ID" (sel.sql:binding-column "id" "o")))))
       (bindings (list (cons "ORDERS" orders))))
  (dolist (src '("ORDERS .> FILTER(_[\"id\"] > IF(TRUE, 1, 2))"
                 "ORDERS .> FILTER(_[\"id\"] > 0) .> SORT_BY(O, IF(TRUE, \"DESC\", \"ASC\"))"
                 "ORDERS .> FILTER(_[\"id\"] > 0) .> SORT_BY(_[\"id\"], IF(TRUE, \"DESC\", \"ASC\"))"))
    (format t "~a~%  translate-stmt: ~a~%" src
            (handler-case (sel.sql:as-statement (sel.sql:translate-statement (sel:compile-source src) "postgresql" bindings))
              (sel.sql:sql-error (e) (format nil "error ~a@~a:~a" (sel.sql:sql-error-code e) (sel.sql:sql-error-line e) (sel.sql:sql-error-col e)))))
    (format t "  no-optimize:    ~a~%"
            (handler-case (sel.sql:as-statement (sel.sql:translate-statement (sel:compile-source src) "postgresql" bindings '(:no-optimize t)))
              (sel.sql:sql-error (e) (format nil "error ~a@~a:~a" (sel.sql:sql-error-code e) (sel.sql:sql-error-line e) (sel.sql:sql-error-col e)))))))
```

**`$D/tr.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
for (const src of ['ORDERS .> FILTER(_["id"] > IF(TRUE, 1, 2))', 'ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC"))']) {
  try { const s = Sql.translateStatement(compile(src), 'postgresql', orders); console.log(src, '\n  ', s.parts.map(p => typeof p === 'string' ? p : '?').join('')); }
  catch (e) { console.log(src, '\n   error', e.code, e.line + ':' + e.col); }
}
```

**`$D/tr2.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
const src = 'ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC"))';
try { Sql.translateStatement(compile(src), 'postgresql', orders); } catch (e) { console.log(e.stack); }
```

**`$D/lanes_cpp.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main() {
  const Bindings bindings({{"ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)}})}});
  const sel::Value rows = sel::Value::list({sel::Value::record({"id"}, {sel::Value::text("3")}),
                                            sel::Value::record({"id"}, {sel::Value::text("1")}),
                                            sel::Value::record({"id"}, {sel::Value::text("2")})});
  const auto f = [](const auto& fn) -> std::string {
    try { return "ok " + fn(); }
    catch (const sel::SelError& e) { return "error " + e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
    catch (const sel::sql::SqlError& e) { return "sqlerror " + e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
  };
  for (const char* src : {"ORDERS .> FILTER(_[\"id\"] > 0) .> SORT_BY(O, IF(TRUE, \"DESC\", \"ASC\"))",
                          "ORDERS .> FILTER(_[\"id\"] > 0) .> TOP_BY(ROW, IF(FALSE, \"x\", \"DESC\"), 2) .> MAP(_[\"id\"])"}) {
    const sel::Program program = sel::compile(src);
    std::cout << src << "\n";
    sel::Value context = sel::Value::none(); context.set("ORDERS", rows);
    std::cout << "  run():            " << f([&] { sel::Value c = context.clone(); return program.run(c).dump(); }) << "\n";
    const sel::sql::HybridPlan plan = Sql::plan_hybrid(program, "postgresql", bindings);
    std::cout << "  plan kind:        " << (plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid") << "\n";
    std::cout << "  execute_hybrid:   " << f([&] { return Sql::execute_hybrid(plan, [&](const std::string&, const std::vector<sel::Value>&) { return rows; }, context).dump(); }) << "\n";
    std::cout << "  translate_stmt:   " << f([&] { return Sql::translate_statement(program, "postgresql", bindings).as_statement(); }) << "\n";
  }
}
```

### Commands run and their output

Read JS optimizer head and Program API

```bash
D=$SCRATCH/verify-A && mkdir -p $D && sed -n 1,60p js/src/optimizer.mjs && sed -n 1,60p js/src/sel.mjs
```

```
// Engine-independent pipeline rewrites shared by the in-memory and SQL lanes.
// The parser AST is treated as immutable by callers: every rewrite starts from
// shallow copies, and nested nodes are copied as they are visited.

import * as D from './decimal.mjs';
import { lookup } from './registry.mjs';
import { MAX_DEPTH } from './errors.mjs';

const PIPELINE_OPS = new Set([
  'FILTER', 'BUCKET', 'SELECT_COLS', 'MAP', 'DISTINCT', 'DEDUPE',
  'TAKE', 'DROP', 'SORT', 'SORT_DESC', 'SORT_BY', 'TOP', 'TOP_DESC', 'TOP_BY',
  'LINK', 'LINK_LEFT',
]);

function copyNode(node) {
  if (!node || typeof node !== 'object') return node;
  const copy = { ...node };
  if (node.args) copy.args = [...node.args];
  if (node.items) copy.items = [...node.items];
  return copy;
}

function call(name, args, pos) {
  const spec = lookup(name);
  return { t: 'call', name, spec, args, pos };
}

function unwindPipeline(node) {
  const steps = [];
  let current = node;
  while (current && current.t === 'call' && PIPELINE_OPS.has(current.name)
         && current.args.length > 0) {
    steps.unshift(current);
    current = current.args[0];
  }
  return { source: current, steps };
}

function buildPipeline(source, steps) {
  let current = source;
  for (const step of steps) {
    const next = copyNode(step);
    next.args = [current, ...step.args.slice(1)];
    current = next;
  }
  return current;
}

function literalBool(value, pos) { return { t: 'bool', v: value, pos }; }
function literalNum(value, pos) { return { t: 'num', v: value, pos }; }

// A fold that replaces a node by one of its children must not move the error
// position an operator over the result reports: spec §6.3 names the node that
// actually failed, and to the operator the operand IS the folded node, not the
// literal inside it (ctl.if.constant-condition-result-keeps-the-if-position).
// So a hoisted child is re-stamped with the folded node's position -- which is
// only exact for a leaf literal, the one shape that carries no positions of
// its own and cannot fail by itself. A variable is a leaf that can (E_UNDEF_VAR
// at its own column), so it is not a literal here.
const LITERAL_TYPES = new Set(['num', 'text', 'bool', 'null']);
// Public host interface. See spec/SPEC.md §8.

import './builtins/index.mjs';
import { parse } from './parser.mjs';
import { Context, evalNode, MAX_DEPTH } from './eval.mjs';
import { RecordShape, Value, NONE, TEXT, BIN, BOOL } from './value.mjs';
import { SelError, fail } from './errors.mjs';
import { names, register, registerBuiltin } from './registry.mjs';
import { optimizeAst, optimizeAstLogical, optimizeAstInMemory } from './optimizer.mjs';

export class Program {
  constructor(source, ast) {
    this.source = source;
    // The parse tree, and the tree every other consumer reads: dependencies(),
    // the SQL translator, the hybrid planner. It is IMMUTABLE once here -- the
    // optimiser and the planner copy on the way down and never write into it
    // (sql/cases/25-hybrid-plans.sqlt asserts so) -- and a caller who builds a
    // Program from an AST of their own is held to the same rule. Reassigning
    // `ast` is fine and drops the cache below; writing into its nodes is not.
    this.ast = ast;
    // The physical tree run() evaluates: `ast` after the in-memory optimiser,
    // built on the first run and kept, because the rewrite and the copy it
    // makes cost more than evaluating a small rule does. Keyed by the identity
    // of `ast` so a reassi
… (truncated)
```

Compare unoptimised vs optimised evaluation in JS on my own programs

```bash
D=$SCRATCH/verify-A && # (file written, see "Reproduction scripts" above)
node $D/diff.mjs
```

```
SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))
  raw=ok {"1":"3","2":"1","3":"2"}
  opt=error E_UNDEF_VAR at undefined:undefined
  run=error E_UNDEF_VAR at undefined:undefined
SORT_BY((3, 1, 2), _, IF(TRUE, "DESC", "ASC"))
  raw=ok {"1":"3","2":"1","3":"2"}
  opt=ok {"1":"3","2":"2","3":"1"}
  run=ok {"1":"3","2":"2","3":"1"}
TOP_BY((3, 1, 2), _, IF(TRUE, "DESC", "ASC"), 2)
  raw=ok {"1":"3","2":"1"}
  opt=ok {"1":"3","2":"2"}
  run=ok {"1":"3","2":"2"}
SORT_BY(LIST(RECORD("n", 2), RECORD("n", 1)), R, IF(FALSE, "x", "ASC")) .> MAP(_["n"])
  raw=ok {"1":"2","2":"1"}
  opt=error E_UNDEF_VAR at undefined:undefined
  run=error E_UNDEF_VAR at undefined:undefined
SORT_BY(("b","c","a"), _, IF(1 > 0, "DESC", "ASC"))
  raw=ok {"1":"b","2":"c","3":"a"}
  opt=ok {"1":"c","2":"b","3":"a"}
  run=ok {"1":"c","2":"b","3":"a"}
TOP_BY(("b","c","a"), Q, IF(FALSE, "DESC", "ASC"), 1)
  raw=ok {"1":"b"}
  opt=error E_UNDEF_VAR at undefined:undefined
  run=error E_UNDEF_VAR at undefined:undefined
SORT_BY((3, 1, 2), _, "DE" & "SC")
  raw=ok {"1":"3","2":"1","3":"2"}
  opt=ok {"1":"3","2":"1","3":"2"}
  run=ok {"1":"3","2":"1","3":"2"}
```

Run the three lanes in JS for the reported and my own pipeline

```bash
D=$SCRATCH/verify-A && # (file written, see "Reproduction scripts" above)
node $D/lanes.mjs
```

```
ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))
  run():            error E_UNDEF_VAR@1:42
  plan kind:        hybrid | sql: undefined
  executeHybrid:    error E_UNDEF_VAR@1:42
  translateStmt:    ok {"parts":["SELECT ","\"o\".*"," FROM ","\"orders\" \"o\""," WHERE ","(\"o\".\"id\" > ",1,")"," ORDER BY ","CASE WHEN ",2," THEN ",3," ELSE ",4," END"," ASC"],"kind":"STATEMENT","dialect":"postgresql","params":[{"kind":"TEXT","scalar":"0","children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":{"neg":false,"digits":"0","scale":0,"intVal":0}},{"kind":"BOOL","scalar":true,"children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":null},{"kind":"TEXT","scalar":"DESC","children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":null},{"kind":"TEXT","scalar":"ASC","children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":null}],"paramKinds":["NUM","BOOL","TEXT","TEXT"],"caveats":[],"exact":false,"sargable":false,"guard":false,"prefilter":null,"separatePrefilter":false}
ORDERS .> FILTER(_["id"] > 0) .> TOP_BY(ROW, IF(FALSE, "x", "DESC"), 2) .> MAP(_["id"])
  run():            error E_UNDEF_VAR@1:41
  plan kind:        hybrid | sql: undefined
  executeHybrid:    error E_UNDEF_VAR@1:41
  translateStmt:    ok {"parts":["SELECT ","\"_sub1\".\"id\""," FROM ","(","SELECT ","\"o\".*"," FROM ","\"orders\" \"o\""," WHERE ","(\"o\".\"id\" > ",1,")"," ORDER BY ","CASE WHEN ",2," THEN ",3," ELSE ",4," END"," ASC"," LIMIT 2",") \"_sub1\""],"kind":"STATEMENT","dialect":"postgresql","params":[{"kind":"TEXT","scalar":"0","children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":{"neg":false,"digits":"0","scale":0,"intVal":0}},{"kind":"BOOL","scalar":false,"children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":null},{"kind":"TEXT","scalar":"x","children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":null},{"kind":"TEXT","scalar":"DESC","children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":null}],"paramKinds":["NUM","BOOL","TEXT","TEXT"],"caveats":[],"exact":false,"sargable":false,"guard":false,"prefilter":null,"separatePrefilter":false}
```

Run the three lanes in Python

```bash
D=$SCRATCH/verify-A && # (file written, see "Reproduction scripts" above)
PYTHONPATH=$PWD/python python3 $D/lanes.py 2>&1 | head -30
```

```
ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))
  run():         error E_UNDEF_VAR@1:42
  plan kind:     hybrid
  execute_hybrid error E_UNDEF_VAR@1:42
  translate_stmt ok 'SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY CASE WHEN 2 THEN 3 ELSE 4 END ASC'
ORDERS .> FILTER(_["id"] > 0) .> TOP_BY(ROW, IF(FALSE, "x", "DESC"), 2) .> MAP(_["id"])
  run():         error E_UNDEF_VAR@1:41
  plan kind:     hybrid
  execute_hybrid error E_UNDEF_VAR@1:41
  translate_stmt ok 'SELECT "_sub1"."id" FROM (SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1) ORDER BY CASE WHEN 2 THEN 3 ELSE 4 END ASC LIMIT 2) "_sub1"'
```

Run the three lanes in PHP

```bash
D=$SCRATCH/verify-A && # (file written, see "Reproduction scripts" above)
php $D/lanes.php 2>&1 | head -20
```

```
ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))
  run():         error E_UNDEF_VAR@1:42
  plan kind:     hybrid
  executeHybrid: error E_UNDEF_VAR@1:42
  translateStmt: ok "SELECT \"o\".* FROM \"orders\" \"o\" WHERE (\"o\".\"id\" > 1) ORDER BY CASE WHEN 2 THEN 3 ELSE 4 END ASC"
ORDERS .> FILTER(_["id"] > 0) .> TOP_BY(ROW, IF(FALSE, "x", "DESC"), 2) .> MAP(_["id"])
  run():         error E_UNDEF_VAR@1:41
  plan kind:     hybrid
  executeHybrid: error E_UNDEF_VAR@1:41
  translateStmt: ok "SELECT \"_sub1\".\"id\" FROM (SELECT \"o\".* FROM \"orders\" \"o\" WHERE (\"o\".\"id\" > 1) ORDER BY CASE WHEN 2 THEN 3 ELSE 4 END ASC LIMIT 2) \"_sub1\""
```

Run the three lanes in Lisp

```bash
D=$SCRATCH/verify-A && # (file written, see "Reproduction scripts" above)
grep -n "sel-error-code\|sel-error-line\|sel-error-col\|:export" lisp/src/errors.lisp lisp/src/package.lisp 2>/dev/null | head; grep -rn "defun run\b\|(defun run " lisp/src/sel.lisp | head -3
sbcl --non-interactive --load lisp/bin/boot.lisp --load $D/lanes.lisp 2>&1 | grep -v "^;" | tail -15
```

```
lisp/src/package.lisp:5:  (:export
lisp/src/package.lisp:8:   #:sel-error-code
lisp/src/package.lisp:10:   #:sel-error-line
lisp/src/package.lisp:11:   #:sel-error-col
lisp/src/errors.lisp:19:  ((code :initarg :code :reader sel-error-code)
lisp/src/errors.lisp:21:   (line :initarg :line :initform 0 :reader sel-error-line)
lisp/src/errors.lisp:22:   (col :initarg :col :initform 0 :reader sel-error-col)
lisp/src/errors.lisp:26:                     (sel-error-code c) (sel-error-line c) (sel-error-col c)
lisp/src/sel.lisp:38:(defun run (program &optional context)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201EC03E3}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201EC03E3}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201EC0393}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-A/lanes.lisp" {1201EBF0C3}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {78D980EC74CB}> #<SB-C::SOURCE-INFO {1201E67663}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-A/lanes.lisp" {1201EBF0C3}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-A/lanes.lisp" {1201EBF0C3}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {78D980EC788B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-A/lanes.lisp" {1201EBF0C3}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-A/lanes.lisp" {1201EBF0C3}>)
9: (LOAD #P"$SCRATCH/verify-A/lanes.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-A/lanes.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Run the three lanes in Lisp with the SQL system loaded

```bash
D=$SCRATCH/verify-A && { echo '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))'; cat $D/lanes.lisp; } > $D/lanes2.lisp && sbcl --non-interactive --load lisp/bin/boot.lisp --load $D/lanes2.lisp 2>&1 | grep -v "^;" | tail -15
```

```
17: (EVAL-TLF (LET* ((ORDERS (SEL.SQL:BINDING-RELATION "orders" "o" (LIST #))) (BINDINGS (LIST (CONS "ORDERS" ORDERS)))) (DOLIST (SRC (QUOTE ("ORDERS .> FILTER(_[\"id\"] > 0) .> SORT_BY(O, IF(TRUE, \"DESC\", \"ASC\"))" "ORDERS .> FILTER(_[\"id\"] > 0) .> TOP_BY(ROW, IF(FALSE, \"x\", \"DESC\"), 2) .> MAP(_[\"id\"])"))) (FORMAT T "~a~%" SRC) (LET ((P #) (ROWS #)) (FORMAT T "  run():          ~a~%" (HANDLER-CASE # #)) (LET (#) (FORMAT T "  plan kind:      ~a~%" #) (FORMAT T "  execute-hybrid: ~a~%" #)) (FORMAT T "  translate-stmt: ~a~%" (HANDLER-CASE # #))))) 1 NIL)
18: ((LABELS SB-FASL::EVAL-FORM :IN SB-INT:LOAD-AS-SOURCE) (LET* ((ORDERS (SEL.SQL:BINDING-RELATION "orders" "o" (LIST #))) (BINDINGS (LIST (CONS "ORDERS" ORDERS)))) (DOLIST (SRC (QUOTE ("ORDERS .> FILTER(_[\"id\"] > 0) .> SORT_BY(O, IF(TRUE, \"DESC\", \"ASC\"))" "ORDERS .> FILTER(_[\"id\"] > 0) .> TOP_BY(ROW, IF(FALSE, \"x\", \"DESC\"), 2) .> MAP(_[\"id\"])"))) (FORMAT T "~a~%" SRC) (LET ((P #) (ROWS #)) (FORMAT T "  run():          ~a~%" (HANDLER-CASE # #)) (LET (#) (FORMAT T "  plan kind:      ~a~%" #) (FORMAT T "  execute-hybrid: ~a~%" #)) (FORMAT T "  translate-stmt: ~a~%" (HANDLER-CASE # #))))) 1)
19: ((LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) (LET* ((ORDERS (SEL.SQL:BINDING-RELATION "orders" "o" (LIST #))) (BINDINGS (LIST (CONS "ORDERS" ORDERS)))) (DOLIST (SRC (QUOTE ("ORDERS .> FILTER(_[\"id\"] > 0) .> SORT_BY(O, IF(TRUE, \"DESC\", \"ASC\"))" "ORDERS .> FILTER(_[\"id\"] > 0) .> TOP_BY(ROW, IF(FALSE, \"x\", \"DESC\"), 2) .> MAP(_[\"id\"])"))) (FORMAT T "~a~%" SRC) (LET ((P #) (ROWS #)) (FORMAT T "  run():          ~a~%" (HANDLER-CASE # #)) (LET (#) (FORMAT T "  plan kind:      ~a~%" #) (FORMAT T "  execute-hybrid: ~a~%" #)) (FORMAT T "  translate-stmt: ~a~%" (HANDLER-CASE # #))))) :CURRENT-INDEX 1)
20: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {7A19C312F4CB}> #<SB-C::SOURCE-INFO {1201E77813}> SB-C::INPUT-ERROR-IN-LOAD)
21: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-A/lanes2.lisp" {1201ED6603}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
22: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-A/lanes2.lisp" {1201ED6603}> NIL)
23: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {7A19C312F88B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-A/lanes2.lisp" {1201ED6603}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-A/lanes2.lisp" {1201ED6603}>)
24: (LOAD #P"$SCRATCH/verify-A/lanes2.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
25: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-A/lanes2.lisp") (:QUIT)))
26: (SB-IMPL::TOPLEVEL-INIT)
27: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
28: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
29: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Find the Lisp error and the exported symbols

```bash
D=$SCRATCH/verify-A && sbcl --non-interactive --load lisp/bin/boot.lisp --load $D/lanes2.lisp 2>&1 | grep -v "^;" | grep -m3 -i "error\|does not exist\|unbound\|undefined"; grep -n "#:run\|#:evaluate\|#:value-size\|#:sel-error\b\|#:compile-source" lisp/src/package.lisp; grep -n "#:plan-hybrid\|#:execute-hybrid\|#:hybrid-plan-pure\|#:as-statement\|#:translate-statement\|#:binding-" lisp/src/sql/package.lisp
```

```
run():          error E_UNDEF_VAR@1:42
  execute-hybrid: error E_UNDEF_VAR@1:42
Unhandled SEL.SQL:SQL-ERROR in thread #<SB-THREAD:THREAD tid=735987 "main thread" RUNNING
7:   #:sel-error
8:   #:sel-error-code
9:   #:sel-error-message
10:   #:sel-error-line
11:   #:sel-error-col
12:   #:sel-error-offset
32:   #:value-size
57:   #:compile-source
58:   #:run
60:   #:evaluate
39:   #:as-statement
63:   #:binding-p
64:   #:binding-column
65:   #:binding-raw
66:   #:binding-columns
67:   #:binding-relation
68:   #:binding-relation-query
69:   #:binding-value
86:   #:translate-statement
102:   #:hybrid-plan-pure-sql-p
103:   #:hybrid-plan-pure-memory-p
106:   #:plan-hybrid
107:   #:execute-hybrid))
```

Show the Lisp SQL error details

```bash
D=$SCRATCH/verify-A && sbcl --non-interactive --load lisp/bin/boot.lisp --load $D/lanes2.lisp 2>&1 | grep -v "^;" | grep -B2 -A6 "Unhandled SEL.SQL" | head -20; grep -n "#:sql-error" lisp/src/sql/package.lisp
```

```
While evaluating the form starting at line 2, column 0
  of #P"$SCRATCH/verify-A/lanes2.lisp":
Unhandled SEL.SQL:SQL-ERROR in thread #<SB-THREAD:THREAD tid=736203 "main thread" RUNNING
                                         {1204030083}>:
  E_SQL_UNBOUND at 1:42: O is read by this rule but no binding says where it lives; bound names are ORDERS

Backtrace for: #<SB-THREAD:THREAD tid=736203 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SEL.SQL:SQL-ERROR {12024C9693}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SEL.SQL:SQL-ERROR {12024C9693}>)
16:   #:sql-error
17:   #:sql-error-code
18:   #:sql-error-message
19:   #:sql-error-line
20:   #:sql-error-col
21:   #:sql-error-offset
```

Compare Lisp vs JS translate_statement folding of a leaf IF

```bash
D=$SCRATCH/verify-A && # (file written, see "Reproduction scripts" above)
sbcl --non-interactive --load lisp/bin/boot.lisp --load $D/tr.lisp 2>&1 | grep -v "^;" | tail -8
echo ==== JS
# (file written, see "Reproduction scripts" above)
node $D/tr.mjs
```

```
translate-stmt: SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN (CAST("o"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("o"."id" AS NUMERIC) ELSE NULL END > 1)
  no-optimize:    SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN (CAST("o"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("o"."id" AS NUMERIC) ELSE NULL END > CASE WHEN TRUE THEN 1 ELSE 2 END)
ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))
  translate-stmt: error E_SQL_UNBOUND@1:42
  no-optimize:    SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN (CAST("o"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("o"."id" AS NUMERIC) ELSE NULL END > 0) ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC"))
  translate-stmt: SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN (CAST("o"."id" AS TEXT) ~ '^-?[0-9]+(\.[0-9]+)?$') THEN CAST("o"."id" AS NUMERIC) ELSE NULL END > 0) ORDER BY "o"."id" DESC
  no-optimize:    error E_SQL_SHAPE@1:43
==== JS
ORDERS .> FILTER(_["id"] > IF(TRUE, 1, 2)) 
   SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > CASE WHEN ? THEN ? ELSE ? END)
ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) 
   error undefined undefined:undefined
```

Show the JS error class thrown for the binder-less SORT_BY

```bash
D=$SCRATCH/verify-A && sed -i 's/console.log(src, .\\n   error., e.code, e.line + .:. + e.col)/console.log(src, "\\n   error", e.code, e.line, e.col, e.constructor.name, e.message)/' $D/tr.mjs && node $D/tr.mjs 2>&1 | tail -3; grep -n "class SqlError" -A10 js/src/sql/errors.mjs | head -14
```

```
SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > CASE WHEN ? THEN ? ELSE ? END)
ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) 
   error undefined undefined undefined TypeError s is not iterable
7:export class SqlError extends Error {
8-  // One class, one message. No diagnostics object, no `explain()` API.
9-  constructor(code, message, pos) {
10-    super(message);
11-    this.name = 'SqlError';
12-    this.code = code;
13-    this.line = pos ? pos.line : 0;
14-    this.col = pos ? pos.col : 0;
15-    this.offset = pos ? pos.offset : 0;
16-  }
17-
```

Locate the JS TypeError source for a non-text direction node

```bash
D=$SCRATCH/verify-A && # (file written, see "Reproduction scripts" above)
node $D/tr2.mjs 2>&1 | head -8; sed -n 2240,2260p js/src/sql/translator.mjs; grep -n "function asciiUpper" -A5 js/src/sql/*.mjs | head
```

```
TypeError: s is not iterable
    at asciiUpper (file:///home/nathan/workspaces/nth-share/sel/js/src/lexer.mjs:292:20)
    at Translator.analyzeSortStep (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/translator.mjs:2238:15)
    at Translator.analyzePipeline (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/translator.mjs:2080:16)
    at Translator.translateStatement (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/translator.mjs:176:23)
    at Sql.translateStatement (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs:55:14)
    at file://$SCRATCH/verify-A/tr2.mjs:5:11
    at ModuleJob.run (node:internal/modules/esm/module_job:271:25)
    } else if (count === 4) {
      if (!constants.isBinderName(args[1])) {
        refuse('E_SQL_SHAPE', 'the binder of SORT_BY must be a bare name', args[1].pos);
      }
      binder = args[1].name;
      key = args[2];
      if (args[3].t !== 'text') {
        refuse('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", args[3].pos);
      }
      dir = asciiUpper(args[3].v);
    } else {
      refuse('E_ARITY', 'SORT_BY takes 2 to 4 arguments', step.pos);
    }

    if (dir !== 'ASC' && dir !== 'DESC') {
      const dirPos = count === 4 ? args[3].pos : args[2].pos;
      refuse('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", dirPos);
    }

    plan.orderBy.push({
      binder,
```

Compile and run the three lanes in C++

```bash
D=$SCRATCH/verify-A && # (file written, see "Reproduction scripts" above)
cd cpp && g++ -std=c++23 -O1 -o $D/lanes_cpp $D/lanes_cpp.cpp build/sel_sql*.o build/sel.o 2>&1 | head -20; $D/lanes_cpp
```

```
ORDERS .> FILTER(_["id"] > 0) .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))
  run():            error E_UNDEF_VAR@1:42
  plan kind:        hybrid
  execute_hybrid:   error E_UNDEF_VAR@1:42
  translate_stmt:   ok SELECT "o".* FROM "orders" "o" WHERE ("id" > 0) ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
ORDERS .> FILTER(_["id"] > 0) .> TOP_BY(ROW, IF(FALSE, "x", "DESC"), 2) .> MAP(_["id"])
  run():            error E_UNDEF_VAR@1:41
  plan kind:        hybrid
  execute_hybrid:   error E_UNDEF_VAR@1:41
  translate_stmt:   ok SELECT "_sub1"."id" FROM (SELECT "o".* FROM "orders" "o" WHERE ("id" > 0) ORDER BY CASE WHEN FALSE THEN 'x' ELSE 'DESC' END ASC LIMIT 2) "_sub1"
```

Build the pre-commit C++ REPL and run the repro on it

```bash
D=$SCRATCH/verify-A/old && mkdir -p $D/bin && for f in sel.cpp sel.hpp sel_ast.hpp sel_optimizer.cpp bin/sel.cpp; do git show 8fe0e3a:cpp/$f > $D/$f; done && cd $D && g++ -std=c++23 -O1 -c -o sel.o sel.cpp 2>&1 | head -5 && g++ -std=c++23 -O1 -o sel_old bin/sel.cpp sel.o 2>&1 | head -5 && ./sel_old -e 'SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))'; ./sel_old -e 'SORT_BY((3, 1, 2), _, IF(TRUE, "DESC", "ASC"))'
```

```
Exit code 127
sel.cpp:30:10: fatal error: third_party/srell/srell.hpp: No such file or directory
   30 | #include "third_party/srell/srell.hpp"
      |          ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~
compilation terminated.
/usr/bin/ld.bfd: cannot find sel.o: No such file or directory
collect2: error: ld returned 1 exit status
/bin/bash: line 1: ./sel_old: No such file or directory
/bin/bash: line 1: ./sel_old: No such file or directory
```

Build the pre-commit C++ REPL with the srell header and run the repro

```bash
D=$SCRATCH/verify-A/old && ln -sfn /home/nathan/workspaces/nth-share/sel/cpp/third_party $D/third_party && cd $D && g++ -std=c++23 -O1 -c -o sel.o sel.cpp 2>&1 | head -5 && g++ -std=c++23 -O1 -o sel_old bin/sel.cpp sel.o 2>&1 | head -5 && ./sel_old -e 'SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))'; ./sel_old -e 'SORT_BY((3, 1, 2), _, IF(TRUE, "DESC", "ASC"))'
```

```
-{"1"=t"3", "2"=t"1", "3"=t"2"}
-{"1"=t"3", "2"=t"1", "3"=t"2"}
```

Run the pre-commit JS REPL on the repro

```bash
D=$SCRATCH/verify-A/oldjs && mkdir -p $D && git archive 8fe0e3a js | tar -x -C $D && node $D/js/bin/sel.mjs -e 'SORT_BY((3, 1, 2), X, IF(TRUE, "DESC", "ASC"))'; node $D/js/bin/sel.mjs -e 'SORT_BY((3, 1, 2), _, IF(TRUE, "DESC", "ASC"))'
```

```
E_UNDEF_VAR at line 1 column 20: undefined variable X
-{"1"=t"3", "2"=t"2", "3"=t"1"}
```

