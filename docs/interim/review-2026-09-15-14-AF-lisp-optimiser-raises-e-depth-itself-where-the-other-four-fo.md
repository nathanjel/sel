# AF. Lisp optimiser raises E_DEPTH itself where the other four fold past the boundary the evaluator refuses

**Status:** FIXED 2026-09-16. Spec §6.4 now says the evaluator is the depth authority; every optimiser returns a tree that reaches the cap as written (a bounded `exceedsDepth` walk at the entry point, counting as the evaluator counts) and none raises — Lisp's `fail` is gone, and the four folding hosts no longer erase the E_DEPTH of a 201-term chain. `lim.eval-depth-foldable-chain`, `-just-under`, `-is-not-raised-on-an-unvisited-branch`, `-on-a-visited-branch`, `-through-an-assignment`, `-through-an-assignment-just-under`. The fuzzer suggestion (boundary-length chains in `gen-programs`) is left for finding #31.

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** lisp, js, php, python, cpp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing, but contradicting a promise ed16df2 wrote.

## Summary (verifier)

Reproduced: on a 201-term foldable chain (and on `X = <199 ones>; X`, `LIST(1,2,3) .> TAKE(<200 ones>)`, a 201-term `*` chain, a 201-term `TRUE AND` chain) Program.run() returns a value in JS/PHP/Python/C++ and raises E_DEPTH at 1:1 (or the equivalent leaf column) in Lisp, while dependencies() raises E_DEPTH at 1:1 in all five; plan_hybrid is pure_memory in all hosts but execute_hybrid then calls run(), so the hybrid lane inherits the split. One analytical facet of the report is wrong in a way that matters for the fix: the unoptimised evaluator (JS evalNode on program.ast) answers E_DEPTH@1:1 on the 201-chain, so on that program Lisp is the host that matches the evaluator and the four folding hosts are the ones breaking the new EXTENDING.md "the optimiser is invisible" contract — but Lisp breaks it in the other direction: `IF(TRUE, 7, <201-term chain>)` evaluates to 7 in four hosts (branch never evaluated) and to E_DEPTH@1:17 in Lisp, whose optimiser guard fires on subtrees the evaluator never visits. Both guard styles are pre-existing at 8fe0e3a; the commit added the contract that neither side satisfies and rewrote the Lisp walk keeping the fail.

## Suggested fix — case first

Decide first, in spec §6.4 / EXTENDING.md: the evaluator (and dependencies(), which the spec ties to it) is the depth authority, so the optimiser must neither raise nor erase E_DEPTH. Smallest coherent fix: in all five optimisers, when `depth > MAX_DEPTH` return the node unrewritten (drop Lisp's `fail`), AND make fold-node refuse to fold a node whose subtree depth reaches the limit — simplest is to have optimizeTree return the child unrewritten (no fold) once `depth + 1 > MAX_DEPTH`, i.e. stop folding one level earlier so the leaf at depth 201 is still evaluated and raises. Then add to conformance/10-limits.selt, before touching any host: (1) `1 + 1 + ... (201 terms)` => `error E_DEPTH at 1:1` (the foldable chain the four hosts currently answer), (2) the same chain with `&` on text (already agrees, pins the pair), (3) `IF(TRUE, 7, <201-term + chain>)` => `num 7` (pins that the optimiser must not raise on a branch the evaluator never visits), (4) the 200-term chain => `num 200` as the just-under guard. Also bump tools/gen-programs.mjs to occasionally emit chains of length MAX_DEPTH-1..MAX_DEPTH+1 so the fuzzer can see the boundary.

## Verifier reasoning

Code reading confirms the cited divergence: lisp/src/optimizer.lisp:906-907 `((> depth +max-depth+) (fail "E_DEPTH" ... (node-pos node)))` vs js/src/optimizer.mjs:569 `if (depth > MAX_DEPTH) return node;`, php/src/Optimizer.php:57-59 (`return $node`), python/sel/optimizer.py:634-635 (`return node`), cpp/sel.cpp:5845 (`return node`). Every host's evaluator counts every node including leaves (lisp/src/eval.lisp:101-105 `incf context-depth` then check; js/src/eval.mjs:118-121), so a 201-term left chain has its leftmost leaf at depth 201 and the raw evaluator raises E_DEPTH@1:1 — verified by calling evalNode(p.ast) directly in JS. In the four hosts the optimiser folds the whole chain into one literal before evaluation, so run() answers 201: the optimiser changed the outcome, which is what EXTENDING.md:611 (added by this commit, `git diff 8fe0e3a ed16df2 -- docs/EXTENDING.md` line 47) says must not happen. Whether the four hosts refuse a 201-deep tree therefore depends on whether the operator folds: a 201-term `"a" & ...` chain is E_DEPTH@1:1 in all five, a 201-term `+`/`*`/`AND` chain succeeds in four. Lisp's optimiser is conversely a second depth authority: for `IF(TRUE, 7, <201-term chain>)` the evaluator never visits the deep branch (four hosts answer 7) but Lisp raises E_DEPTH@1:17/1:15. So the report's sub-claim that Lisp reports "the leaf's position, not the node the evaluator would name" is refuted for the 201-chain (same node, same position) and the sql/cases/18 note "the evaluator answers E_DEPTH" remains literally true of every evaluator — it is run() that diverges from the evaluator in four hosts. dependencies() raises E_DEPTH@1:1 in all five on the 201-chain (spec §6.4: shares the evaluation depth), so JS/PHP/Python/C++ run() and dependencies() disagree on the same program. plan_hybrid: pure_memory in JS and Lisp for `ORDERS .> TAKE(<200 ones>)`; js/src/sql/hybrid.mjs:352 and lisp/src/sql/hybrid.lisp:336-337 route pure-memory plans to run(), so the hybrid lane carries the same divergence. Pre-existing: `git show 8fe0e3a:lisp/src/optimizer.lisp` lines 850 and 888 have the same `fail "E_DEPTH"` in optimize-ast-logical/optimize-ast-in-memory, `git show 8fe0e3a:lisp/src/sel.lisp:21` already evaluated `(optimize-ast ...)`, and `git show 8fe0e3a:js/src/optimizer.mjs:574` already had `return node`. Fuzzer cannot see it: tools/gen-programs.mjs:241 `expr(int(1, 4))` never approaches depth 200. Severity high: cross-host run() results differ and a documented promise of this commit is unmet on both sides.

## Verifier evidence

```
Reported repro rerun, P = 201 ones joined by ' + ': `node js/bin/sel.mjs -e "$P"` => 201; `php php/bin/sel -e "$P"` => 201; `cpp/build/sel -e "$P"` => 201; `PYTHONPATH=$PWD/python python3 -m sel -e "$P"` => 201; `lisp/bin/sel -e "$P"` => "E_DEPTH at line 1 column 1: evaluation nested too deeply". 202 terms => E_DEPTH at 1:3 in all five. `<host> --deps -e "$P"` (201 terms) => "E_DEPTH at line 1 column 1: expression nested too deeply" in all five. Own repros (all five hosts, same order js/php/cpp/lisp/py): 201-term `2 * 2 * ...` => 3213876088517980551083924184682325205044405987565585670602752 in four, E_DEPTH@1:1 in Lisp; 201-term `TRUE AND ...` => TRUE in four, E_DEPTH@1:1 Lisp; `X = <199 ones>; X` => 199 in four, E_DEPTH@1:5 Lisp; `LIST(1,2,3) .> TAKE(<200 ones>)` => -{"1"=t"1","2"=t"2","3"=t"3"} in four, E_DEPTH@1:21 Lisp; 201-term `"a" & "a" & ...` => E_DEPTH@1:1 in ALL five; 200-term `+` chain => 200 in all five. `IF(TRUE, 7, <201-term & chain>)` => 7 in js/php/cpp/py, "E_DEPTH at line 1 column 17" in Lisp; `IF(TRUE, 7, <201-term + chain>)` => 7 in four, E_DEPTH@1:15 in Lisp; --deps on the IF program => E_DEPTH@1:17 in JS and Lisp. Raw evaluator probe (/tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-AF/probe.mjs, evalNode(p.ast, new Context(...)) vs p.run() vs p.dependencies()): chain201 raw=E_DEPTH@1:1 run=t"201" deps=E_DEPTH@1:1; assign199 raw=E_DEPTH@1:5 run=t"199" deps=E_DEPTH@1:5; take200 raw=E_DEPTH@1:21 run=list deps=E_DEPTH@1:21; concat201 raw=E_DEPTH@1:1 run=E_DEPTH@1:1 deps=E_DEPTH@1:1. plan_hybrid on `ORDERS .> TAKE(<200 ones>)`: JS (verify-AF/plan.mjs) pureMemory=true pureSql=false; Lisp (verify-AF/plan.lisp loaded after quickload :sel-lang/sql) pure-memory=T pure-sql=NIL. Files: lisp/src/optimizer.lisp:906-907; js/src/optimizer.mjs:569; php/src/Optimizer.php:57-59; python/sel/optimizer.py:634-635; cpp/sel.cpp:5845; lisp/src/eval.lisp:101-105; js/src/eval.mjs:118-121; js/src/sel.mjs:33-43 (run -> physicalAst -> optimizeAst); lisp/src/sel.lisp:26-32; js/src/sql/hybrid.mjs:352; lisp/src/sql/hybrid.lisp:336-337; docs/EXTENDING.md:611-613; spec/SPEC.md:501-508; sql/cases/18-host-neutrality.sqlt:164-170; tools/gen-programs.mjs:241. Pre-existing: `git show 8fe0e3a:lisp/src/optimizer.lisp | grep -n E_DEPTH` => 850, 888; `git show 8fe0e3a:lisp/src/sel.lisp` line 21 `(eval-node (optimize-ast (program-ast program)) ...)`; `git show 8fe0e3a:js/src/optimizer.mjs` line 574 `if (depth > MAX_DEPTH) return node;`.
```

## Original review reports (deduplicated into this finding)

### [program-cache-immutability] Lisp optimiser raises E_DEPTH itself where the other four stop rewriting: run() disagrees at the depth boundary

*correctness · high · hosts: lisp, js, php, python, cpp*

Locations: `lisp/src/optimizer.lisp:906`; `js/src/optimizer.mjs:569`; `php/src/Optimizer.php:57`; `python/sel/optimizer.py:634`; `cpp/sel.cpp:5845`; `docs/EXTENDING.md:611`; `sql/cases/18-host-neutrality.sqlt:164`

All five optimisers count depth on the way down, but at depth > 200 four of them return the node unrewritten and let the evaluator decide (JS/PHP/Python/C++), while Lisp's optimize-tree calls (fail "E_DEPTH" ...) at the node it reached. Program.run() goes through this walk in every host (it is what the new physical-AST cache builds), so a program whose deepest node is a leaf literal at depth 201 is constant-folded to a value by four hosts and refused by Lisp; the position Lisp reports is the leaf's, not the node the evaluator would name. This directly contradicts the commit's own contract in docs/EXTENDING.md ('The optimiser is invisible: an error a program raises after optimisation carries the same code and position as before it') and it is a cross-host run() result difference. It also exposes a second incoherence inside the four folding hosts: dependencies() raises E_DEPTH@1:1 on the same program in all five hosts (spec §6.4: dependencies() raises exactly when the program could not have been evaluated) while run() answers 201, and the note on sql/cases/18-host-neutrality.sqlt neutral.depth.one-past-the-limit-is-refused states '201 terms: the evaluator answers E_DEPTH' — which is only true of Lisp now. The guard is pre-existing in both directions (the old optimize-ast-logical/in-memory had the same fail, the old JS the same return), but the remediation rewrote the Lisp walk and kept it, and the fuzzer never generates boundary-depth programs. Whichever side is right needs a decision, a conformance case in 10-limits.selt, and then one behaviour in five hosts; note that plan_hybrid is coherent (stage 1 refuses first, pure_memory everywhere) so the disagreement is confined to run().

Reported repro:

```
P=$(python3 -c "print(' + '.join(['1']*201))"); for each host `<repl> -e "$P"`: js/php/cpp/python => t"201"; lisp => E_DEPTH at line 1 column 1. `X = <199 ones joined by +>; X` => 199 in js/php/cpp/python, E_DEPTH at 1:5 in lisp. `LIST(1,2,3) .> TAKE(<200 ones>)` => list in four hosts, E_DEPTH at 1:21 in lisp. 202 terms => E_DEPTH at 1:3 in all five (the bin at depth 201 is where both the optimiser guard and the evaluator land). `<repl> --deps -e "$P"` (201 terms) => E_DEPTH at 1:1 in all five hosts, while run() answers 201 in four. plan_hybrid on the 201-term chain => pure_memory in all five (stage 1's E_SQL_DEPTH fires before the optimiser). Code paths: lisp/src/optimizer.lisp:906-907 `((> depth +max-depth+) (fail "E_DEPTH" ...))` vs js/src/optimizer.mjs:569 `if (depth > MAX_DEPTH) return node;`, php/src/Optimizer.php:57-59, python/sel/optimizer.py:634-635, cpp/sel.cpp:5845.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$D/probe.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Context, evalNode } from '/home/nathan/workspaces/nth-share/sel/js/src/eval.mjs';
import { Value } from '/home/nathan/workspaces/nth-share/sel/js/src/value.mjs';
const progs = {
  chain201: Array(201).fill('1').join(' + '),
  assign199: 'X = ' + Array(199).fill('1').join(' + ') + '; X',
  take200: 'LIST(1,2,3) .> TAKE(' + Array(200).fill('1').join(' + ') + ')',
  neg200: '-'.repeat(199) + '1',      // 199 unary minus nodes + leaf at depth 200
  neg200b: '-'.repeat(200) + '1',     // wait: parser caps prefix at 200 -> check
  paren: '('.repeat(99) + '1' + ')'.repeat(99),
  concat: Array(201).fill('"a"').join(' & '),
};
for (const [k, src] of Object.entries(progs)) {
  let p; try { p = compile(src); } catch (e) { console.log(k, 'compile:', e.code, e.line+':'+e.column); continue; }
  let raw; try { raw = 'raw=' + evalNode(p.ast, new Context(Value.fromNative({}))).toString().slice(0,20); } catch (e) { raw = 'raw=' + e.code + '@' + e.line + ':' + e.column; }
  let opt; try { opt = 'run=' + p.run({}).toString().slice(0,20); } catch (e) { opt = 'run=' + e.code + '@' + e.line + ':' + e.column; }
  let deps; try { deps = 'deps=' + JSON.stringify(p.dependencies()); } catch (e) { deps = 'deps=' + e.code + '@' + e.line + ':' + e.column; }
  console.log(k, raw, opt, deps);
}
```

**`$D/plan.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
const src = 'ORDERS .> TAKE(' + Array(200).fill('1').join(' + ') + ')';
const plan = Sql.planHybrid(compile(src), 'postgresql', orders);
console.log('js pureMemory=', plan.pureMemory, 'pureSql=', plan.pureSql);
```

**`$D/plan.lisp`**

```lisp
(let* ((orders (sel.sql:binding-relation "orders" "o" (list (cons "ID" (sel.sql:binding-column "id" "o")))))
       (bindings (list (cons "ORDERS" orders)))
       (src (format nil "ORDERS .> TAKE(~{~a~^ + ~})" (make-list 200 :initial-element 1)))
       (plan (sel.sql:plan-hybrid (sel:compile-source src) "postgresql" bindings)))
  (format t "lisp pure-memory=~a pure-sql=~a~%" (sel.sql:hybrid-plan-pure-memory-p plan) (sel.sql:hybrid-plan-pure-sql-p plan)))
```

### Commands run and their output

Run 202-term chain and --deps on 201 terms in all hosts

```bash
P=$(python3 -c "print(' + '.join(['1']*202))"); for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel" "env PYTHONPATH=$PWD/python python3 -m sel"; do echo "== $h 202"; $h -e "$P" 2>&1 | head -2; done; P=$(python3 -c "print(' + '.join(['1']*201))"); for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel" "env PYTHONPATH=$PWD/python python3 -m sel"; do echo "== $h deps 201"; $h --deps -e "$P" 2>&1 | head -2; done
```

```
== node js/bin/sel.mjs 202
E_DEPTH at line 1 column 3: evaluation nested too deeply
== php php/bin/sel 202
E_DEPTH at line 1 column 3: evaluation nested too deeply
== cpp/build/sel 202
E_DEPTH at line 1 column 3: evaluation nested too deeply
== lisp/bin/sel 202
E_DEPTH at line 1 column 3: evaluation nested too deeply
== env PYTHONPATH=/home/nathan/workspaces/nth-share/sel/python python3 -m sel 202
E_DEPTH at line 1 column 3: evaluation nested too deeply
== node js/bin/sel.mjs deps 201
E_DEPTH at line 1 column 1: expression nested too deeply
== php php/bin/sel deps 201
E_DEPTH at line 1 column 1: expression nested too deeply
== cpp/build/sel deps 201
E_DEPTH at line 1 column 1: expression nested too deeply
== lisp/bin/sel deps 201
E_DEPTH at line 1 column 1: expression nested too deeply
== env PYTHONPATH=/home/nathan/workspaces/nth-share/sel/python python3 -m sel deps 201
E_DEPTH at line 1 column 1: expression nested too deeply
```

Inspect Lisp REPL loader and evaluator depth checks

```bash
cat lisp/bin/sel | head -30; grep -n "max-depth" lisp/src/evaluator.lisp | head; grep -n "MAX_DEPTH" js/src/evaluator.mjs | head
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
ugrep: warning: lisp/src/evaluator.lisp: No such file or directory
ugrep: warning: js/src/evaluator.mjs: No such file or directory
```

Compare raw evaluator vs run() vs dependencies() in JS on boundary programs

```bash
D=$SCRATCH/verify-AF && mkdir -p $D && # (file written, see "Reproduction scripts" above)
node $D/probe.mjs
```

```
chain201 raw=E_DEPTH@1:undefined run=[object Object] deps=E_DEPTH@1:undefined
assign199 raw=E_DEPTH@1:undefined run=[object Object] deps=E_DEPTH@1:undefined
take200 raw=E_DEPTH@1:undefined run=[object Object] deps=E_DEPTH@1:undefined
neg200 compile: E_DEPTH 1:undefined
neg200b compile: E_DEPTH 1:undefined
paren raw=[object Object] run=[object Object] deps=[]
concat raw=E_DEPTH@1:undefined run=E_DEPTH@1:undefined deps=E_DEPTH@1:undefined
```

Rerun JS probe with corrected error and dump fields

```bash
D=$SCRATCH/verify-AF && sed -i 's/e.column/e.col/g; s/\.toString()\.slice(0,20)/.dump().slice(0,20)/g' $D/probe.mjs && node $D/probe.mjs
```

```
chain201 raw=E_DEPTH@1:1 run=t"201" deps=E_DEPTH@1:1
assign199 raw=E_DEPTH@1:5 run=t"199" deps=E_DEPTH@1:5
take200 raw=E_DEPTH@1:21 run=-{"1"=t"1", "2"=t"2" deps=E_DEPTH@1:21
neg200 compile: E_DEPTH 1:200
neg200b compile: E_DEPTH 1:200
paren raw=t"1" run=t"1" deps=[]
concat raw=E_DEPTH@1:1 run=E_DEPTH@1:1 deps=E_DEPTH@1:1
```

Run independent boundary-depth programs in all five hosts

```bash
run() { for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel" "env PYTHONPATH=$PWD/python python3 -m sel"; do printf "%-28s " "${h##*/}"; $h -e "$1" 2>&1 | head -1 | cut -c1-70; done; }; 
echo "### 201 x 2 chain (*)"; run "$(python3 -c "print(' * '.join(['2']*201))")"
echo "### 201 text & chain"; run "$(python3 -c "print(' & '.join(['\"a\"']*201))")"
echo "### 201 TRUE AND chain"; run "$(python3 -c "print(' AND '.join(['TRUE']*201))")"
echo "### X = 199 ones; X"; run "$(python3 -c "print('X = ' + ' + '.join(['1']*199) + '; X')")"
echo "### LIST .> TAKE(200 ones)"; run "$(python3 -c "print('LIST(1,2,3) .> TAKE(' + ' + '.join(['1']*200) + ')')")"
echo "### 200-term chain (at limit)"; run "$(python3 -c "print(' + '.join(['1']*200))")"
```

```
### 201 x 2 chain (*)
sel.mjs                      3213876088517980551083924184682325205044405987565585670602752
sel                          3213876088517980551083924184682325205044405987565585670602752
sel                          3213876088517980551083924184682325205044405987565585670602752
sel                          E_DEPTH at line 1 column 1: evaluation nested too deeply
python python3 -m sel        3213876088517980551083924184682325205044405987565585670602752
### 201 text & chain
sel.mjs                      E_DEPTH at line 1 column 1: evaluation nested too deeply
sel                          E_DEPTH at line 1 column 1: evaluation nested too deeply
sel                          E_DEPTH at line 1 column 1: evaluation nested too deeply
sel                          E_DEPTH at line 1 column 1: evaluation nested too deeply
python python3 -m sel        E_DEPTH at line 1 column 1: evaluation nested too deeply
### 201 TRUE AND chain
sel.mjs                      TRUE
sel                          TRUE
sel                          TRUE
sel                          E_DEPTH at line 1 column 1: evaluation nested too deeply
python python3 -m sel        TRUE
### X = 199 ones; X
sel.mjs                      199
sel                          199
sel                          199
sel                          E_DEPTH at line 1 column 5: evaluation nested too deeply
python python3 -m sel        199
### LIST .> TAKE(200 ones)
sel.mjs                      -{"1"=t"1", "2"=t"2", "3"=t"3"}
sel                          -{"1"=t"1", "2"=t"2", "3"=t"3"}
sel                          -{"1"=t"1", "2"=t"2", "3"=t"3"}
sel                          E_DEPTH at line 1 column 21: evaluation nested too deeply
python python3 -m sel        -{"1"=t"1", "2"=t"2", "3"=t"3"}
### 200-term chain (at limit)
sel.mjs                      200
sel                          200
sel                          200
sel                          200
python python3 -m sel        200
```

Check plan_hybrid on a boundary-depth TAKE in JS and Lisp

```bash
D=$SCRATCH/verify-AF && # (file written, see "Reproduction scripts" above)
node $D/plan.mjs; # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/plan.lisp 2>&1 | tail -3
```

```
js pureMemory= true pureSql= false
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Show Lisp probe error and binding constructor signatures

```bash
D=$SCRATCH/verify-AF && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/plan.lisp 2>&1 | grep -v "^[0-9]*:" | head -8; grep -n "binding-column\|binding-relation" lisp/src/sql/*.lisp | grep defun | head
```

```
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=840023 "main thread" RUNNING
                                                 {1204028083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 1, Column: 40, File-Position: 39

lisp/src/sql/binding.lisp:89:(defun binding-column (column &optional table (type :unknown) &key exact sargable guard collation prefilter split-sargable)
lisp/src/sql/binding.lisp:143:(defun binding-columns (&rest items)
lisp/src/sql/binding.lisp:183:(defun binding-relation (from &optional alias fields scalar correlate &key prefilter split-sargable)
lisp/src/sql/binding.lisp:195:(defun binding-relation-query (query &optional alias fields scalar correlate &key prefilter split-sargable)
```

Load the Lisp SQL system then run the plan probe

```bash
D=$SCRATCH/verify-AF && grep -n "quickload\|QUICKLOAD" lisp/bin/sqlt.lisp lisp/bin/sel.lisp | head -3; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $D/plan.lisp 2>&1 | grep -v "^[0-9]*:" | tail -4
```

```
lisp/bin/sqlt.lisp:26:  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
lisp pure-memory=T pure-sql=NIL
```

Test whether an unevaluated deep branch trips Lisp's optimiser guard

```bash
run() { for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel" "env PYTHONPATH=$PWD/python python3 -m sel"; do printf "%-28s " "${h##*/}"; $h -e "$1" 2>&1 | head -1 | cut -c1-70; done; }; 
echo "### IF(TRUE, 7, <201-term & chain>) - unevaluated deep branch, unfoldable"; run "$(python3 -c "print('IF(TRUE, 7, ' + ' & '.join(['\"a\"']*201) + ')')")"
echo "### IF(TRUE, 7, <201-term + chain>) - unevaluated deep branch, foldable"; run "$(python3 -c "print('IF(TRUE, 7, ' + ' + '.join(['1']*201) + ')')")"
```

```
### IF(TRUE, 7, <201-term & chain>) - unevaluated deep branch, unfoldable
sel.mjs                      7
sel                          7
sel                          7
sel                          E_DEPTH at line 1 column 17: evaluation nested too deeply
python python3 -m sel        7
### IF(TRUE, 7, <201-term + chain>) - unevaluated deep branch, foldable
sel.mjs                      7
sel                          7
sel                          7
sel                          E_DEPTH at line 1 column 15: evaluation nested too deeply
python python3 -m sel        7
```

Check dependencies() on the unevaluated deep branch in JS and Lisp

```bash
P="$(python3 -c "print('IF(TRUE, 7, ' + ' & '.join(['\"a\"']*201) + ')')")"; for h in "node js/bin/sel.mjs" "lisp/bin/sel"; do printf "%-20s " "${h##*/}"; $h --deps -e "$P" 2>&1 | head -1 | cut -c1-70; done
```

```
sel.mjs              E_DEPTH at line 1 column 17: expression nested too deeply
sel                  E_DEPTH at line 1 column 17: expression nested too deeply
```

