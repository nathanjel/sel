# AA. PHP physicalAst() is not keyed by the identity of $ast: reassigning $program->ast is not noticed

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "Five quick wins".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** by-this-commit · **hosts:** php, js, python, lisp, cpp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

PHP's Program::physicalAst() (php/src/Sel.php:84-90) caches on `$this->physical === null` and never re-keys on `$ast`, so after `$p->ast = ...` run() and executeHybrid()'s pure-memory continuation keep evaluating the old tree while dependencies() and Sql::translate() read the new one; JS, Python and Lisp key the cache on the identity of `ast` and notice the reassignment (same sequence gives `2 4` / `run2=20` there and `2 2` / `run2=2` in PHP). This contradicts CHANGELOG.md:22 ("keyed by the identity of the public AST") and docs/SQL-TRANSLATION.md:2297 ("reassigning the whole tree is fine, and noticed"), and PHP's own docblock (Sel.php:17-19) documents the opposite contract. C++ has no writable ast (cpp/sel.hpp:316, private `ast_`), so it is not reachable there; the JS .d.ts `readonly ast` (js/src/sel.d.ts:113) is a minor documentation contradiction with the JS runtime comment and behaviour.

## Suggested fix — case first

Smallest fix (PHP >=8.1, no hooks): add `private ?array $physicalOf = null;` to Program and make physicalAst() `if ($this->physicalOf === null || $this->physicalOf !== $this->ast) { $this->physical = Optimizer::optimize($this->ast, true); $this->physicalOf = $this->ast; }` — the copy-on-write share makes `===` an O(1) identity check until `$ast` is reassigned, and dismantle `$physicalOf` alongside `$physical` in __destruct only if it was separated (or simply drop the reference before dismantling `$ast`). Rewrite the Sel.php:11-22 docblock to match §12.1 ("reassigning the whole tree is fine, and noticed"). Case to add first, in tools/check-php-optimizer.php after line 229 mirroring check-js-optimizer.mjs:178-181: `$physical = $reusable->physicalAst(); $reusable->ast = Sel::compile('1 + 1')->ast; check($reusable->physicalAst() !== $physical && $reusable->run()->asText() === '2', 'reassigning ast drops the cache');` — it fails on HEAD (run() answers the pipeline's old result). Also flip js/src/sel.d.ts:113 from `readonly ast: any` to `ast: any` (or state in §12.1 that TypeScript consumers are held to readonly) so the declaration matches the documented contract. Alternative if the team prefers PHP's current contract: change CHANGELOG.md:22 / SQL-TRANSLATION.md:2297 to say PHP does not notice a reassignment — but that leaves a cross-host public-API divergence, so the code fix is preferable.

## Verifier reasoning

Every facet of the cluster checks out. (1) Code: php/src/Sel.php:84-90 `if ($this->physical === null) { $this->physical = Optimizer::optimize($this->ast, true); }` — no key. js/src/sel.mjs:39-45 `if (this._physicalOf !== this.ast)`, python/sel/__init__.py:66-72 `if self._physical_of is not self.ast`, lisp/src/sel.lisp:25-31 `(unless (eq (program-%physical-of program) ast)` — all keyed. cpp/sel.hpp:316/329-336 + cpp/sel.cpp:5896-5899: `ast()` is a getter over a private `ast_`, `physical_ast()` under `call_once`; no reassignment is expressible, so C++ is consistent by construction. (2) Behaviour: reproduced the reported `1 + 1` → reassign → `2 + 2` sequence and my own `A + 1` → `B * 10` sequence (with dependencies, translate and the hybrid lane) in PHP, JS, Python and Lisp; PHP alone answers with the stale tree, and within PHP run()/executeHybrid disagree with dependencies()/translate(). (3) Tests: tools/check-php-optimizer.php:229 asserts only `physicalAst() === physicalAst()` ('built once'); tools/check-js-optimizer.mjs:178-181, python/tests/test_unit.py:528-534 and lisp/tests/unit.lisp:985-989 all additionally assert that reassigning ast drops the cache — which is why the PHP gap is green (`php tools/check-php-optimizer.php` → 46 passed). (4) Introduced: at 8fe0e3a php/src/Sel.php:48 ran `Optimizer::optimize($this->ast, true)` on every run() (JS likewise, js/src/sel.mjs:21 at 8fe0e3a), so before this commit a reassignment was always seen in every host; the cache and the divergence both arrive with ed16df2. Severity high under the rubric: differing results between hosts for one public API sequence, differing results between lanes inside PHP, and a stated CHANGELOG/§12.1 promise unmet. The .d.ts `readonly` facet is low on its own (TypeScript consumers are told the field is immutable while the runtime comment says reassignment is supported and noticed).

## Verifier evidence

```
Reported repro, PHP: `php -r 'require "php/src/Sql/bootstrap.php"; $p = Sel\Sel::compile("1 + 1"); echo $p->run()->asText(); $p->ast = Sel\Sel::compile("2 + 2")->ast; echo " ", $p->run()->asText(), "\n";'` → `2 2`. JS (scratchpad/verify-AA/js.mjs, imports js/src/sel.mjs): `reported: 2 4`. Python (verify-AA/py.py): `reported: 2 4`. Lisp (verify-AA/lisp.lisp via lisp/bin/boot.lisp + `(ql:quickload :sel-lang/sql)`, `(setf (sel:program-ast p) ...)`): `reported: 2 4`.
Own repro (compile "A + 1", run {A:1}, deps; reassign ast to compile("B * 10").ast; run {A:1,B:2}, deps, translate postgresql with B bound to column b NUM): PHP → `run1=2 deps=A | run2=2 deps=B sql=(CAST("b" AS NUMERIC) * CAST(10 AS NUMERIC))`; JS → `run1=2 deps=A | run2=20 deps=B sql=(CAST("b" AS NUMERIC) * CAST(10 AS NUMERIC))`; Python → same as JS; Lisp → `run1=2 deps=(A) | run2=20 deps=(B) sql=(CAST("b" AS NUMERIC) * CAST(10 AS NUMERIC))`.
Hybrid lane after reassignment (planHybrid over ORDERS relation binding, pure-memory plan whose continuationProgram === the program, executeHybrid with {A:1,B:2}): PHP → `pureMemory=true same=true exec=2`; JS (verify-AA/js2.mjs) → `pureMemory= true same= true exec= 20`.
Code read: php/src/Sel.php:11-22 (docblock: "leaves run() evaluating the tree it was constructed with"), :84-90 (physicalAst, null-keyed); js/src/sel.mjs:21-27,39-45; python/sel/__init__.py:50-72; lisp/src/sel.lisp:15-31; cpp/sel.hpp:308-336, cpp/sel.cpp:5892-5904; js/src/sel.d.ts:109-113 (`readonly ast`). Promises: CHANGELOG.md:22, docs/SQL-TRANSLATION.md:2288-2297. Tests: tools/check-php-optimizer.php:229 (only 'built once'); tools/check-js-optimizer.mjs:178-181, python/tests/test_unit.py:528-534, lisp/tests/unit.lisp:985-989 (all assert reassign drops the cache). `php tools/check-php-optimizer.php` → `PHP optimizer checks: 46 passed`. Pre-commit: `git show 8fe0e3a:php/src/Sel.php` line 48 `$ast = Optimizer::optimize($this->ast, true);` inside run() (no cache); `git show 8fe0e3a:js/src/sel.mjs` line 21 `evalNode(optimizeAst(this.ast), ...)`.
Fix-feasibility probe: PHP `===` on two variables sharing one zend_array is O(1) (20k-node AST: 0.037 ms per 1000 compares), deep compare of a separately parsed equal tree 7 ms each — so a retained `$physicalOf` compared with `===` gives identity-keyed behaviour in the common case on PHP >=8.1 (composer.json:26; property hooks are unavailable at that floor).
```

## Original review reports (deduplicated into this finding)

### [runners-generator-tests] PHP physicalAst() cache is not keyed by the AST: reassigning $program->ast leaves run() evaluating the old tree, unlike JS, Python and Lisp; PHP unit check omits the assertion the other three carry

*coherence · high · hosts: php, js, python, lisp*

Locations: `php/src/Sel.php:84`; `js/src/sel.mjs:40`; `python/sel/__init__.py:68`; `lisp/src/sel.lisp:28`; `docs/SQL-TRANSLATION.md:2297`; `CHANGELOG.md:22`; `tools/check-php-optimizer.php:210`

§12.1 says 'reassigning the whole tree is fine, and noticed' and the CHANGELOG says the physical tree is 'keyed by the identity of the public AST'. JS (`_physicalOf !== this.ast`), Python (`_physical_of is not self.ast`) and Lisp (`eq %physical-of ast`) do that; PHP's physicalAst() (Sel.php:84-90) caches unconditionally in `$physical` and never compares against `$ast`, and its own docblock (Sel.php:11-22) documents the opposite contract ('leaves run() evaluating the tree it was constructed with'). tools/check-js-optimizer.mjs, python/tests/test_unit.py (test_program_caches_the_physical_ast_per_source_tree) and lisp/tests/unit.lisp planner-contract all assert 'reassigning ast drops the cache'; tools/check-php-optimizer.php stops at 'the physical AST is built once', which is why the gap in the matrix is not a failing test. Observable difference in a public API sequence.

Reported repro:

```
php -r 'require "php/src/Sql/bootstrap.php"; $p = Sel\Sel::compile("1 + 1"); echo $p->run()->asText(); $p->ast = Sel\Sel::compile("2 + 2")->ast; echo $p->run()->asText();' => 2 2. node (same sequence with compile().ast) => 2 4. PYTHONPATH=python python3 (same) => 2 4. Lisp unit test asserts the 4-style behaviour and passes.
```

### [program-cache-immutability] PHP physicalAst() is keyed on 'built once', not on the identity of $ast: reassigning $program->ast is noticed by three hosts and ignored by PHP

*coherence · medium · hosts: php, js, python, lisp, cpp*

Locations: `php/src/Sel.php:84-90`; `js/src/sel.mjs:39-45`; `python/sel/__init__.py:66-72`; `lisp/src/sel.lisp:25-31`; `cpp/sel.hpp:329-336`; `CHANGELOG.md:22`; `js/src/sel.d.ts:113`

CHANGELOG says the physical tree is 'keyed by the identity of the public AST' in every host, and the JS/Python/Lisp source comments say 'Reassigning ast is fine and drops the cache'. JS (`this._physicalOf !== this.ast`), Python (`self._physical_of is not self.ast`) and Lisp (`(eq (program-%physical-of program) ast)`) implement that; PHP caches on `$this->physical === null` and never looks at `$ast` again, and its own docblock admits a write to `$ast` 'leaves run() evaluating the tree it was constructed with'. C++ has no writable ast. So the same public field, writable in four hosts, has three documented contracts: JS/Python/Lisp notice a reassignment; PHP silently runs the old tree; the JS .d.ts declares `readonly ast` contradicting the JS runtime comment. Within PHP the lanes then disagree: after a reassignment dependencies() and the SQL translator read the new tree while run() evaluates the old one. In-place writes into nodes are equally unnoticed in all four dynamic hosts (documented as forbidden), so only the reassignment lane diverges. Either PHP should key on the array (e.g. compare a retained reference/hash, or make ast a private-with-getter so a write is refused) or the other three should stop promising invalidation and the CHANGELOG/comments should say so.

Reported repro:

```
php -r 'require "php/src/bootstrap.php"; $p=Sel\Sel::compile("1 + 1"); $p->run(); $p->ast=Sel\Sel::compile("2 + 2")->ast; echo $p->run()->dump();' => t"2" (JS `p.ast = compile('2 + 2').ast; p.run()` => t"4"; Python `p.ast = compile('2 + 2').ast; p.run()` => t"4"; Lisp `(setf (sel:program-ast p) ...)` then run => 4). PHP lanes: `$p=compile("A + 1"); $p->run(["A"=>1])` => 2, deps A; `$p->ast=compile("B * 10")->ast;` then run(["A"=>1,"B"=>2]) => t"2" (still A + 1) while `$p->dependencies()` => B. tools/check-php-optimizer.php:229 asserts only 'built once' and (unlike check-js-optimizer.mjs:178-181, test_unit.py:528-534, unit.lisp:985-989) has no 'reassigning ast drops the cache' assertion, which is how the divergence stays green.
```

### [promises-vs-code] PHP physicalAst() is not keyed by the AST: reassigning $program->ast leaves run() evaluating the old tree, contradicting §12.1 'reassigning the whole tree is fine, and noticed'

*coherence · medium · hosts: php, js, python, lisp, cpp*

Locations: `php/src/Sel.php:84`; `js/src/sel.mjs:37`; `python/sel/__init__.py:70`; `lisp/src/sel.lisp:40`; `docs/SQL-TRANSLATION.md:2296`; `CHANGELOG.md:20`

CHANGELOG: 'Program.run() builds its physical tree once ... keyed by the identity of the public AST'; §12.1: 'a caller who constructs a Program from an AST of their own must not write into its nodes afterwards (reassigning the whole tree is fine, and noticed)'. JS, Python and Lisp compare `_physicalOf`/`_physical_of`/`%physical-of` against the current ast; PHP's physicalAst() (Sel.php:84) caches on first call with no key, and its own docblock admits a later write 'leaves run() evaluating the tree it was constructed with'. Same public API usage, different answer per host.

Reported repro:

```
php -r 'require "php/src/bootstrap.php"; $p = Sel\Sel::compile("1 + 1"); echo $p->run()->asText(); $p->ast = Sel\Sel::compile("2 + 2")->ast; echo " ", $p->run()->asText();' -> `2 2`. Same sequence in node (p.ast = compile('2 + 2').ast) -> `2 4`; Python -> `2 4`.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$D/js.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const p = compile('1 + 1'); let out = p.run().asText(); p.ast = compile('2 + 2').ast; out += ' ' + p.run().asText(); console.log('reported:', out);
const q = compile('A + 1'); let s = 'run1=' + q.run({A:1}).asText() + ' deps=' + q.dependencies();
q.ast = compile('B * 10').ast; s += ' | run2=' + q.run({A:1,B:2}).asText() + ' deps=' + q.dependencies() + ' sql=' + Sql.translate(q, 'postgresql', {B: Binding.column('b', null, 'NUM')}).sql;
console.log('own:', s);
```

**`$D/py.py`**

```python
from sel import compile
from sel.sql import Sql, Binding
p = compile('1 + 1'); out = p.run().as_text(); p.ast = compile('2 + 2').ast; out += ' ' + p.run().as_text(); print('reported:', out)
q = compile('A + 1'); s = 'run1=' + q.run({'A':1}).as_text() + ' deps=' + ','.join(q.dependencies())
q.ast = compile('B * 10').ast; s += ' | run2=' + q.run({'A':1,'B':2}).as_text() + ' deps=' + ','.join(q.dependencies()) + ' sql=' + Sql.translate(q, 'postgresql', {'B': Binding.column('b', None, 'NUM')}).sql
print('own:', s)
```

**`$D/lisp.lisp`**

```lisp
(let ((p (sel:compile-source "1 + 1")))
  (format t "reported: ~a" (sel:as-text (sel:run p)))
  (setf (sel:program-ast p) (sel:program-ast (sel:compile-source "2 + 2")))
  (format t " ~a~%" (sel:as-text (sel:run p))))
(let ((q (sel:compile-source "A + 1")))
  (format t "own: run1=~a deps=~a" (sel:as-text (sel:run q (list (cons "A" 1)))) (sel:dependencies q))
  (setf (sel:program-ast q) (sel:program-ast (sel:compile-source "B * 10")))
  (format t " | run2=~a deps=~a sql=~a~%"
          (sel:as-text (sel:run q (list (cons "A" 1) (cons "B" 2))))
          (sel:dependencies q)
          (sel.sql:as-value (sel.sql:translate q "postgresql" (list (cons "B" (sel.sql:binding-column "b" nil :num)))))))
```

**`$D/js2.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const b = {ORDERS: Binding.relation('orders','o',{ID: Binding.column('id','o','NUM')})};
const p = compile('A + 1'); p.run({A:1}); p.ast = compile('B * 10').ast;
const plan = Sql.planHybrid(p, 'postgresql', b);
console.log('pureMemory=', plan.pureMemory, 'same=', plan.continuationProgram === p, 'exec=', Sql.executeHybrid(plan, () => [], {A:1,B:2}).asText());
```

### Commands run and their output

Run reported and own repro in PHP, JS, Python

```bash
mkdir -p $SCRATCH/verify-AA && D=$SCRATCH/verify-AA
echo "== PHP reported repro"; php -r 'require "php/src/Sql/bootstrap.php"; $p = Sel\Sel::compile("1 + 1"); echo $p->run()->asText(); $p->ast = Sel\Sel::compile("2 + 2")->ast; echo " ", $p->run()->asText(), "\n";'
echo "== PHP own repro (lanes)"; php -r 'require "php/src/Sql/bootstrap.php"; $p = Sel\Sel::compile("A + 1"); echo "run1=", $p->run(["A"=>1])->asText(), " deps=", implode(",", $p->dependencies()); $p->ast = Sel\Sel::compile("B * 10")->ast; echo " | run2=", $p->run(["A"=>1,"B"=>2])->asText(), " deps=", implode(",", $p->dependencies()), " sql=", Sel\Sql\Sql::translate($p, "postgresql", ["B"=>Sel\Sql\Binding::column("b", null, "NUM")])->sql, "\n";'
# (file written, see "Reproduction scripts" above)
echo "== JS"; node $D/js.mjs
# (file written, see "Reproduction scripts" above)
echo "== Python"; PYTHONPATH=$PWD/python python3 $D/py.py
```

```
Exit code 1
== PHP reported repro
2 2
== PHP own repro (lanes)
run1=2 deps=A | run2=2 deps=B sql=PHP Warning:  Undefined property: Sel\Sql\Fragment::$sql in Command line code on line 1

== JS
reported: 2 4
own: run1=2 deps=A | run2=20 deps=B sql=undefined
== Python
reported: 2 4
Traceback (most recent call last):
  File "$SCRATCH/verify-AA/py.py", line 5, in <module>
    q.ast = compile('B * 10').ast; s += ' | run2=' + q.run({'A':1,'B':2}).as_text() + ' deps=' + ','.join(q.dependencies()) + ' sql=' + Sql.translate(q, 'postgresql', {'B': Binding.column('b', None, 'NUM')}).sql
                                                                                                                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AttributeError: 'Fragment' object has no attribute 'sql'
```

Rerun own repro using asValue().sql

```bash
D=$SCRATCH/verify-AA
sed -i 's/\.text();$/.asValue().sql;/; s/\.text()$/.as_value().sql/' $D/js.mjs $D/py.py; sed -n 37,50p js/src/sql/fragment.mjs
echo "== PHP own"; php -r 'require "php/src/Sql/bootstrap.php"; $p = Sel\Sel::compile("A + 1"); echo "run1=", $p->run(["A"=>1])->asText(), " deps=", implode(",", $p->dependencies()); $p->ast = Sel\Sel::compile("B * 10")->ast; echo " | run2=", $p->run(["A"=>1,"B"=>2])->asText(), " deps=", implode(",", $p->dependencies()), " sql=", Sel\Sql\Sql::translate($p, "postgresql", ["B"=>Sel\Sql\Binding::column("b", null, "NUM")])->asValue()->sql, "\n";'
echo "== JS"; node $D/js.mjs; echo "== Python"; PYTHONPATH=$PWD/python python3 $D/py.py
```

```
Exit code 1
  asValue(mode = 'inline') {
    if (this.kind === 'LIST') {
      refuse('E_SQL_SHAPE',
        'this expression yields a list, and a SQL expression is a scalar');
    }
    if (this.kind === 'STATEMENT') {
      refuse('E_SQL_SHAPE',
        'this expression yields a statement, and a SQL expression is a scalar; use asStatement()');
    }
    return this.#join(mode);
  }

  // Usable as a top-level SQL query statement.
  asStatement(mode = 'inline') {
== PHP own
run1=2 deps=A | run2=2 deps=B sql=PHP Warning:  Attempt to read property "sql" on string in Command line code on line 1

== JS
reported: 2 4
own: run1=2 deps=A | run2=20 deps=B sql=undefined
== Python
reported: 2 4
Traceback (most recent call last):
  File "$SCRATCH/verify-AA/py.py", line 5, in <module>
    q.ast = compile('B * 10').ast; s += ' | run2=' + q.run({'A':1,'B':2}).as_text() + ' deps=' + ','.join(q.dependencies()) + ' sql=' + Sql.translate(q, 'postgresql', {'B': Binding.column('b', None, 'NUM')}).as_value().sql
                                                                                                                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AttributeError: 'str' object has no attribute 'sql'
```

Rerun own repro with correct Fragment API

```bash
D=$SCRATCH/verify-AA
sed -i 's/\.asValue()\.sql;$/.asValue();/; s/\.as_value()\.sql$/.as_value()/' $D/js.mjs $D/py.py
echo "== PHP own"; php -r 'require "php/src/Sql/bootstrap.php"; $p = Sel\Sel::compile("A + 1"); echo "run1=", $p->run(["A"=>1])->asText(), " deps=", implode(",", $p->dependencies()); $p->ast = Sel\Sel::compile("B * 10")->ast; echo " | run2=", $p->run(["A"=>1,"B"=>2])->asText(), " deps=", implode(",", $p->dependencies()), " sql=", Sel\Sql\Sql::translate($p, "postgresql", ["B"=>Sel\Sql\Binding::column("b", null, "NUM")])->asValue(), "\n";'
echo "== JS"; node $D/js.mjs; echo "== Python"; PYTHONPATH=$PWD/python python3 $D/py.py
```

```
== PHP own
run1=2 deps=A | run2=2 deps=B sql=(CAST("b" AS NUMERIC) * CAST(10 AS NUMERIC))
== JS
reported: 2 4
own: run1=2 deps=A | run2=20 deps=B sql=(CAST("b" AS NUMERIC) * CAST(10 AS NUMERIC))
== Python
reported: 2 4
own: run1=2 deps=A | run2=20 deps=B sql=(CAST("b" AS NUMERIC) * CAST(10 AS NUMERIC))
```

See how Lisp scripts load the system

```bash
cat lisp/bin/sel | head -30; ls lisp/bin
```

```
#!/usr/bin/env bash
# See sel.lisp beside this wrapper. Run from the repository root.
here="$(cd "$(dirname "$0")" && pwd)"
exec sbcl --noinform --disable-debugger --non-interactive \
  --load "$here/boot.lisp" \
  --load "$here/sel.lisp" \
  --eval '(sel-cli:main)' \
  --end-toplevel-options "$@"
api
api.lisp
batch
batch.lisp
boot.lisp
case-data.lisp
check-decimal
check-decimal.lisp
conformance
conformance.lisp
e2e
e2e.lisp
map-replay.lisp
sel
sel.lisp
sqlfuzz
sqlfuzz.lisp
sqlreplay
sqlreplay.lisp
sqlt
sqlt.lisp
test
```

Check Lisp boot and translate API names

```bash
cat lisp/bin/boot.lisp | head -30; grep -n "translate\b\|defun translate\|fragment-sql\|as-value" lisp/src/sql/*.lisp | head
```

```
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
lisp/src/sql/package.lisp:37:   #:as-value
lisp/src/sql/package.lisp:84:   #:translate
lisp/src/sql/package.lisp:85:   #:try-translate
lisp/src/sql/package.lisp:86:   #:translate-statement
lisp/src/sql/package.lisp:87:   #:try-translate-statement
lisp/src/sql/fragment.lisp:90:(defun as-value (f &optional (mode :inline))
lisp/src/sql/map.lisp:70:anyone runs; translate to one of ~{~a~^, ~}" name (dialect-targets))
lisp/src/sql/hybrid.lisp:97:                   (full-frag (try-translate-statement full-prog dialect bindings options)))
lisp/src/sql/hybrid.lisp:118:                                (try-translate-statement prefix-prog dialect bindings options))))
lisp/src/sql/hybrid.lisp:309:                           (sql-frag (try-translate-statement rewritten-prog dialect bindings options)))
```

Look up Lisp translate and binding constructors

```bash
D=$SCRATCH/verify-AA; grep -n "defun translate\b\|defun translate " -A6 lisp/src/sql/*.lisp | head -20; grep -n "column-binding\|defun column\b\|make-column" lisp/src/sql/package.lisp lisp/src/sql/binding.lisp 2>/dev/null | head -5
```

```
lisp/src/sql/translator.lisp:161:(defun translate-variable (tr n)
lisp/src/sql/translator.lisp-162-  (let ((name (sel::node-s n)))
lisp/src/sql/translator.lisp-163-    ;; A binder wins over the bindings map, without ever consulting it.
lisp/src/sql/translator.lisp-164-    (let ((bound (find-binder tr name)))
lisp/src/sql/translator.lisp-165-      (when bound (return-from translate-variable (from-binder tr bound n))))
lisp/src/sql/translator.lisp-166-    (let* ((b (bindings-get (translator-bindings tr) name (snode-pos n)))
lisp/src/sql/translator.lisp-167-           (spec (binding-spec b)))
--
lisp/src/sql/translator.lisp:204:(defun translate-index (tr n)
lisp/src/sql/translator.lisp-205-  (let ((obj (sel::node-l n)))
lisp/src/sql/translator.lisp-206-    ;; Check for nested index: e.g. _["o"]["id"]
lisp/src/sql/translator.lisp-207-    (when (and (not (clist-p obj))
lisp/src/sql/translator.lisp-208-               (eq (snode-kind obj) :index)
lisp/src/sql/translator.lisp-209-               (let ((sub-l (sel::node-l obj)))
lisp/src/sql/translator.lisp-210-                 (and sub-l (not (clist-p sub-l)) (eq (snode-kind sub-l) :var))))
--
lisp/src/sql/translator.lisp:662:(defun translate-unary (tr n)
lisp/src/sql/translator.lisp-663-  (let ((x (walk-node tr (sel::node-l n)))
lisp/src/sql/translator.lisp-664-        (op (sel::node-s n)))
lisp/src/sql/translator.lisp-665-    (if (equal op "NOT")
```

See Lisp bindings shape

```bash
D=$SCRATCH/verify-AA; sed -n 236,245p lisp/tests/unit.lisp; grep -n "defun make-bindings" -A12 lisp/src/sql/binding.lisp | head -20
```

```
;; binding below, 12 comes out "C", MAKE-NUM raises E_NOT_NUM, and that
  ;; SEL-ERROR escapes TRY-TRANSLATE.
  (let* ((cols (apply #'sel.sql:binding-columns
                      (loop for i from 1 to 12
                            collect (sel.sql:binding-column (format nil "c~D" i)))))
         (bindings (list (cons "COLS" cols)))
         (program (sel:compile-source "COUNT(COLS)")))
    (is (string= "12" (sel.sql:as-value
                       (sel.sql:translate program "mariadb" bindings))))
    (let ((*print-base* 16))
230:(defun make-bindings (alist)
231-  (let ((sorted '()) (order '()))
232-    (dolist (cell alist)
233-      (let* ((key (sel::ascii-upcase (car cell)))
234-             (existing (assoc key sorted :test #'equal)))
235-        (unless (binding-p (cdr cell))
236-          (refuse "E_SQL_BINDING"
237-                  (format nil "the binding for ~a is not a binding; build one ~
238-with BINDING-COLUMN, -COLUMNS, -RELATION, -RELATION-QUERY, -RAW or -VALUE"
239-                          (car cell))))
240-        ;; A duplicate name keeps its FIRST position and takes the LAST value,
241-        ;; which is what assigning into a dict twice does.
242-        (if existing
```

Run repro in Lisp

```bash
D=$SCRATCH/verify-AA; # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/lisp.lisp 2>&1 | tail -5
```

```
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

See the Lisp error and exported names

```bash
D=$SCRATCH/verify-AA; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/lisp.lisp 2>&1 | grep -v "^[0-9]*:" | head -12; grep -n "#:dependencies\|#:program-ast\|#:as-text\|#:run\b" lisp/src/package.lisp
```

```
reported: 2 4
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=834123 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 11, Column: 27, File-Position: 606

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-AA/lisp.lisp" {1201ED6A73}>

Backtrace for: #<SB-THREAD:THREAD tid=834123 "main thread" RUNNING {1204030083}>
42:   #:as-text
55:   #:program-ast
58:   #:run
59:   #:dependencies
```

Load the Lisp SQL system and rerun

```bash
D=$SCRATCH/verify-AA; head -12 lisp/bin/sqlt.lisp | grep -n "quickload\|require\|sql"; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (ql:quickload :sel-lang/sql))' --load $D/lisp.lisp 2>&1 | grep -v "^[0-9]*:" | head -6
```

```
3:;;;;   lisp/bin/sqlt                 every case
4:;;;;   lisp/bin/sqlt bind. agg.      only cases whose name contains one of these
5:;;;;   lisp/bin/sqlt --names         what this host loaded, and stop
6:;;;;   lisp/bin/sqlt --print-base 16 every case, with the printer set hostile
reported: 2 4
own: run1=2 deps=(A) | run2=20 deps=(B) sql=(CAST("b" AS NUMERIC) * CAST(10 AS NUMERIC))
```

Check the hybrid lane after reassignment in PHP and JS

```bash
D=$SCRATCH/verify-AA
echo "== PHP hybrid lane"; php -r 'require "php/src/Sql/bootstrap.php"; use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding; $b=["ORDERS"=>Binding::relation("orders","o",["ID"=>Binding::column("id","o","NUM")])]; $p = Sel::compile("A + 1"); $p->run(["A"=>1]); $p->ast = Sel::compile("B * 10")->ast; $plan = Sql::planHybrid($p, "postgresql", $b); echo "pureMemory=", var_export($plan->pureMemory, true), " same=", var_export($plan->continuationProgram === $p, true), " exec=", Sql::executeHybrid($plan, fn($s,$q)=>[], ["A"=>1,"B"=>2])->asText(), "\n";'
# (file written, see "Reproduction scripts" above)
echo "== JS hybrid lane"; node $D/js2.mjs
```

```
== PHP hybrid lane
pureMemory=true same=true exec=2
== JS hybrid lane
pureMemory= true same= true exec= 20
```

Check PHP version floor and that the PHP optimiser check passes as-is

```bash
php --version | head -1; grep -n '"php"' composer.json; php tools/check-php-optimizer.php 2>&1 | tail -3
```

```
PHP 8.5.10 (cli) (built: Aug 25 2026 21:09:32) (NTS gcc x86_64)
26:    "php": ">=8.1"
PHP optimizer checks: 46 passed
```

Time PHP array === on a shared vs separate copy of a 20k-node AST

```bash
php -r '$src = str_repeat("1 + ", 20000) . "1"; require "php/src/bootstrap.php"; $p = Sel\Sel::compile($src); $a = $p->ast; $t=hrtime(true); for($i=0;$i<1000;$i++){ $r = ($a === $p->ast); } echo "shared identical: ", (hrtime(true)-$t)/1e6, " ms/1000\n"; $b = Sel\Sel::compile($src)->ast; $t=hrtime(true); for($i=0;$i<10;$i++){ $r = ($b === $p->ast); } echo "deep compare: ", (hrtime(true)-$t)/1e6, " ms/10 -> ", var_export($r,true), "\n";'
```

```
shared identical: 0.037023 ms/1000
deep compare: 71.187599 ms/10 -> true
```

