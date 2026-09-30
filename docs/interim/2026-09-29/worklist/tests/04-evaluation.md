# T04: Evaluator order, optimizer and recovery

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [x] **T04 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

  Closed 2026-09-30 — conformance/26-evaluation-order; tools/check-eval-equivalence.{mjs,py,php} + C++/Go/Lisp plain-vs-optimised unit hooks; all six hosts agree at commit 3bc54e6 (conformance 2134/2134, sqlt 1309) and every finding row of the family is closed in coverage.csv.

**Destination:** `shared conformance fixtures plus plain/logical/physical/math-plan test adapters and host runtime recovery tests`.

**Required cases and assertions:** For every scenario compare the value, ordered dump, error code/position, side-effect trace and final context between unoptimized and optimized execution. Include a bad left operand with a missing/divide-by-zero right operand; mutation by a later operand; dead deep branches; FILTER predicates that fail on a later row; SORT_BY followed by TAKE(0), invalid/side-effecting counts, scalar and empty inputs. Inject nested math plans, more than 32768 variadic arguments in bounded processes, caught failures in repeated loops, and recovery followed by another run. Assert scratchpad/frame/depth/prefilter restoration and program reuse after failure. Test registered builtins and renamed ASTs without stale plans.

**Contract prerequisite:** Record argument evaluation/coercion order and logical depth ownership. Optimizer success must preserve failure behavior and aliasing, not only successful scalar results.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c9"></a>

### JS-C9: The math plan coerces operands at load time; the tree evaluator evaluates both operands first and coerces afterwards

Source: [JS-C9](../../js-code-review.md). Report labels: [medium] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

re-verified by synthesizer where marked):
  - `node js/bin/sel.mjs -e '"abc" + 1/0'` gives `E_NOT_NUM at line 1 column 1` (re-verified); the plain tree gives `E_DIV_ZERO@1:10` (js-3 `diff.mjs`).
  - `A = "x"; A + B` gives `E_NOT_NUM at 1:10` (re-verified); the unplanned twin `A = "x"; A + (C = B)` gives `E_UNDEF_VAR at 1:19` (re-verified).
  - `(NULL - MISSING) ?? 7` gives `E_NULL` (re-verified); `(NULL == MISSING) ?? 7` gives `7` (re-verified).
  - `ROUND("x", Y)` gives `E_NOT_NUM`; `ROUND("x", (C=Y))` gives `E_UNDEF_VAR`. `MIN(1, "x", Y)` / `MAX(1, "x", Y)` give `E_NOT_NUM` at col 8; `MIN(1, "x", (C=Y))` gives `E_UNDEF_VAR`.
  - Mutation: `A = (1, 2); A + LEN((A[1] = 10; "ab"))` gives `3`, while `A = (1, 2); A + IF(TRUE, LEN((A[1] = 10; "ab")), 0)` gives `12`, and `A = (1, 2); A + (A[1] = 10)` gives `20`. Same program shape, three answers. Python, PHP, C++, Lisp print the same as JS.
  - Fuzz (js-3): 223-230 of 20,000 random arithmetic programs give a different error code/position between the two models; no OK-versus-OK value differences unless a leaf mutates state.

**Additional fixture requirements from the report:** ; the suite passes with either model (1091/1091 through both trees, js-3). Cases `plan.order.binary-left-notnum-right-undef` (`A = "x"; A + B` expects E_UNDEF_VAR at B), `plan.order.strict-args-min`, `plan.order.round-arg-order`, `plan.alias.list-var-mutated-by-leaf` (expects `12`), `"abc" + 1/0`, `(NULL - MISSING) ?? 7`, `A = (1,2); A + LEN(A[1] = "xyz")`, and one program with a dead >200-deep branch beside a live `"abc" + 1/0`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C9.

<a id="js-c12"></a>

### JS-C12: Uncounted recursion over the AST in aggregate/join pre-analysis helpers

Source: [JS-C12](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`COUNT(MAP(LIST(1,2), _+_+…(30,000 terms)…+1))` gives `RangeError: Maximum call stack size exceeded` at aggregate.mjs:30; threshold between 8,000 terms (proper E_DEPTH) and 12,000. Same with `TOP(LIST(1,2), _+_+…, 1)` and `LINK(.., _1+_1+…+0 == _2)` (structure.mjs:159). SORT is fine. php/lisp also fail on 100k terms in their own ways; cpp printed nothing.

**Additional fixture requirements from the report:** 10-limits.selt covers eval/parse depth only. Add an aggregate-body variant with a 5,000-term chain (E_DEPTH at the same column on every host).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C12.

<a id="js-c13"></a>

### JS-C13: SORT/SORT_DESC/SORT_BY + TAKE fusion into TOP/TOP_BY changes evaluation order of the count, keys and side effects

Source: [JS-C13](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`node js/bin/sel.mjs -e 'LIST(RECORD("a",1)) .> SORT_BY(_["zz"]) .> TAKE(0)'` gives `-` (re-verified by synthesizer); the plain tree gives E_NO_KEY@1:33; `TAKE(1)` does raise E_NO_KEY. Fuzz: 55 diffs in 3000 programs, all n=0 / invalid n.

**Additional fixture requirements from the report:** `rel.take.zero-after-a-sort-still-evaluates-the-list` pins only the list. Add cases for a key that raises with TAKE(0), and for an invalid `n` after a raising key.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C13.

<a id="js-c39"></a>

### JS-C39: `optimizeAstLogical` fuses two FILTERs into a deeper predicate and can raise E_DEPTH for a program the evaluator accepts

Source: [JS-C39](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

js3/depth.mjs): `FILTER(FILTER(T, _["a"] > 0), <197 terms of _["a"] == 1 joined by AND>)`: plain is ok; `optimizeAstLogical(...)` output evaluated gives `E_DEPTH@1:31`. 198 terms: both E_DEPTH.

**Additional fixture requirements from the report:** (10-limits pins folding only). An sqlt/hybrid case at the boundary.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C39.

<a id="js-c42"></a>

### JS-C42: The public `register`/`registerBuiltin` can replace a builtin, and the optimiser/math plan ignores the replacement

Source: [JS-C42](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`register("ABS",1,1,()=>Value.text("overridden")); compile('ABS("x")').run({})` gives `E_NOT_NUM` from the plan instead of "overridden".

**Additional fixture requirements from the report:** (api probe).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C42.

## PHP report scenarios

<a id="php-c11"></a>

### PHP-C11: FILTER+FILTER fusion treats a bare variable or literal predicate as "cannot raise", so the optimised program reports a different error

Source: [PHP-C11](../../php-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER(_)`: plain tree E_DIV_ZERO at 1:25; optimised (what `run()` does) E_NOT_BOOL at 1:50 (re-verified: PHP and JS both E_NOT_BOOL at 1:50). Same for `.> FILTER(1)`, `FILTER("x")`, `FILTER(NULL)` (E_NULL), `FILTER(_K)`.

**Additional fixture requirements from the report:** suggest `opt.filter-fusion-keeps-first-error`: the expression above, expecting `!E_DIV_ZERO`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C11.

<a id="php-c12"></a>

### PHP-C12: SORT/SORT_BY + TAKE fusion into TOP/TOP_BY drops key errors when the count is 0 and evaluates the count before the keys

Source: [PHP-C12](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

Program::ast vs physicalAst): `LIST(RECORD("a",0),RECORD("a",1)) .> SORT_BY(1/_["a"]) .> TAKE(0)`: plain E_DIV_ZERO 1:47, `run()` `-` (empty). `... .> SORT_BY(1/_["a"]) .> TAKE("x")`: plain E_DIV_ZERO, optimised E_NOT_NUM 1:64. `... .> TAKE(nosuch)`: E_DIV_ZERO vs E_UNDEF_VAR. `... .> TAKE(-1)`: E_DIV_ZERO vs E_RANGE. `LIST(3,"x",2) .> SORT(_ + 1) .> TAKE(0)`: E_NOT_NUM vs OK. (One-row inputs do not raise in either form.)

**Additional fixture requirements from the report:** for key errors - suggest `opt.sort-take-zero-still-raises` and `opt.sort-take-count-error-order`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C12.

<a id="php-c13"></a>

### PHP-C13: Math plan coerces each leaf operand at load time, so a coercion error on an earlier operand masks an error on a later one (the plain tree gives the opposite)

Source: [PHP-C13](../../php-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`x = "abc"; x + nosuch` -> E_NOT_NUM at 1:12 in `php/bin/sel` (re-verified), plain tree (`Evaluator::evalNode($p->ast)`) E_UNDEF_VAR at 1:16. `x = "abc"; x * (1/0)`: E_NOT_NUM 1:12 vs E_DIV_ZERO 1:18. `ROUND("b", ABS(TRUE))`: E_NOT_NUM 1:7 vs 1:16. `MIN(FALSE, x)` (x undefined): E_NOT_NUM vs E_UNDEF_VAR. A 24k-expression fuzz gave ~45 diffs per 6000, all error code/position swaps of this kind, never a value difference.

**Additional fixture requirements from the report:** suggest `ord.arith-evaluates-both-then-coerces`: `"x" + nosuch` expecting `!E_UNDEF_VAR`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C13.

## PYTHON report scenarios

<a id="py-c7"></a>

### PY-C7: The math plan snapshots operands at load time, so a later sub-expression's mutation is not seen (SPEC 3.4)

Source: [PY-C7](../../python-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`A = LIST(1,2); A + LEN((A[1] = 10))` -> optimised (`Program.run`, every host's CLI) `3`; plain tree walk (`eval_node(program.ast, ...)`) `12`. Spec-consistent answer: 12 (compare `A = LIST(1,2); A == (A[1] = 10)` which is TRUE everywhere). Same with `A * LEN(A[1] = 10)` (2 vs 20) and `MAX(A, LEN(A[1] = 10))` (2 vs 10). Synthesizer: optimised path `3` confirmed; the plain-walk `12` was not re-run.

**Additional fixture requirements from the report:** Suggest `alias.arith-operand-sees-later-mutation`: `A = LIST(1,2); A + LEN((A[1] = 10))` -> `num 12`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C7.

<a id="py-c8"></a>

### PY-C8: Which error wins in `X + Y` depends on plan vs tree-walk (and on an unrelated dead branch pushing the tree past depth 200)

Source: [PY-C8](../../python-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`A="x"; A + (1/0)` -> E_NOT_NUM@1:8 in all five hosts; `X = "x"; A[X + (1/0)] = 1` -> E_DIV_ZERO@1:18 in all five hosts; `X="x"; A = X + (1/0)` -> E_NOT_NUM@1:13. Dead-code sensitivity (all five hosts): `A="x"; IF(TRUE, A + (1/0), 1)` -> E_NOT_NUM@1:17 but `A="x"; IF(TRUE, A + (1/0), <260-term 1+1+... chain>)` -> E_DIV_ZERO@1:23. Same code, different position: `Z * ((MIN(A, Z) + -0) * J)` with `Z = ""`: walker E_NOT_NUM@1:15, plan E_NOT_NUM@1:2. s2: `X="a"; IF(TRUE, X + Y, 1)` -> `E_NOT_NUM at 1:17`; with the 260-term dead chain -> `E_UNDEF_VAR at 1:21` (py, js, php, cpp, lisp identical); same for `ROUND(X, Y)`, `MAX(TRUE, U, 0)`.

**Additional fixture requirements from the report:** Suggest `A = "x"; A + (1/0)`, `A = "x"; A += (1/0)`, `X = "x"; A[X + (1/0)] = 1`, plus a shallow / dead-deep-branch pair so both paths are held to one answer.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C8.

<a id="py-c9"></a>

### PY-C9: Optimiser SORT/SORT_DESC/SORT_BY + TAKE -> TOP/TOP_DESC/TOP_BY fusion changes the value and the error (all five hosts)

Source: [PY-C9](../../python-code-review.md). Report labels: [medium] [confirmed; partly re-verified].

**Scenario / reported observation:**

value): `A = 1; L = LIST(1,2,3); L .> SORT_BY(_ * A) .> TAKE((A = -1; 2))` -> unoptimised (`eval_node(program.ast, ...)`) `[1, 2]`; optimised program and cpp/lisp/js/php give `[3, 2]` (all five agree on the fused answer). Repro (error): `T .> SORT_BY(_["z"]) .> TAKE(-1)` over records: as written E_NO_KEY@1:15 (the key), optimised E_RANGE@1:30; `TAKE("x")` -> E_NOT_NUM, `TAKE(1.5)` -> E_NOT_INT, `SORT_DESC(_["z"]) .> TAKE(NULL)` -> E_NULL, `SORT_BY(_["a"]/_["c"]) .> TAKE(1/0)` -> position 1:21 vs 1:40. Synthesizer: optimised value `[3, 2]` confirmed; `LIST(1,2) .> SORT_BY(1/0) .> TAKE(-1)` returned `E_RANGE at line 1 column 35: TOP_BY argument 3 must not be negative` (the reasoned unfused answer is E_DIV_ZERO); the unoptimised side was not re-run.

**Additional fixture requirements from the report:** Suggest in `15-relational.selt` next to `rel.optimiser.sort-error-before-a-filter-then-take`: `LIST(1,2) .> SORT_BY(1/0) .> TAKE(-1)` expecting E_DIV_ZERO, and the assignment-in-n case expecting `[1, 2]`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C9.

## CPP report scenarios

<a id="cpp-c2"></a>

### CPP-C2: Compound assignment to a plain variable writes through a dangling pointer

Source: [CPP-C2](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`cpp/build/sel -e 'A = 1; A += (B = 1); A'` prints `1`; JS prints `2` (expected). Also `A = 1; A += (B = 1, C = 2, D=3, E=4, F=5, G=6); A` gives `1`. ASan: heap-use-after-free in `Value::operator=` at `eval_assign` (sel.cpp:3646), freed by `Value::set` (vector realloc, sel.cpp:1935).

**Additional fixture requirements from the report:** Suggest `asg.compound.rhs-creates-variable`: `A = 1; A += (B = 1); A` => `t"2"`, a variant with more than 4 new variables so the vector must reallocate, and `A &= (B = "x"); A`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C2.

<a id="cpp-c3"></a>

### CPP-C3: `eval_math_plan` keeps a raw `Dec*` into `ctx.math_scratchpad` across a nested plan that may resize it

Source: [CPP-C3](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`cpp/build/sel -e 'V = 1; X = 5; X + LEN(V+V+V+V+V+V+V+V+V+V+V+V+V+V+V+V+V+V+V+V)'` segfaults (rc 139); JS prints `7` (cpp-3). cpp-2: `python3 -c "print('A=1; A + LEN(\"x\" & (%s))' % '+'.join(['A']*40))"` gives garbage (`-3` at 35 `A`, `E_RANGE ... 1000000 fractional digits` at 40; the synthesizer got `-3` at 40); expected `4`.

**Additional fixture requirements from the report:** Suggest `num.plan.nested-plan-past-initial-scratchpad` (at least 33 slots total across an outer plan and a nested plan in a leaf), plus an ASan lane over it.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C3.

<a id="cpp-c9"></a>

### CPP-C9: `MathStep` / plan slot indices are `uint16_t`: `MIN`/`MAX` with over 32,768 arguments corrupts the heap

Source: [CPP-C9](../../cpp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`python3 -c "print('A=1; MAX(%s)' % ','.join(['A']*33000))" > f.sel; cpp/build/sel f.sel` gives rc 139 (ASan: heap-buffer-overflow WRITE in `Dec::operator=` from `eval_math_plan`:3771). 30,000 arguments works; JS prints `1` for 33,000.

**Additional fixture requirements from the report:** Suggest a generated 33,000-argument `MAX` (a long conformance line, or a unit test).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C9.

<a id="cpp-c19"></a>

### CPP-C19: A caught error inside a math plan leaks the scratchpad frame (unbounded memory growth under `??`)

Source: [CPP-C19](../../cpp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

measured):
  - cpp-2: `cpp/build/sel -e 'COUNT(SPLIT(REPEAT("a,", 100000), ",") .> MAP((Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q + 1) ?? 0))'` gives max RSS 610 MB in 2.1 s; the same with `MAP(Q ?? 0)` gives 33 MB in 1.06 s; JS 125 MB in 2.0 s. With 400,000 rows and a 2-slot plan: 392 MB vs 121 MB baseline.
  - cpp-3: `L = SPLIT(REPEAT("a,", 300000), ","); COUNT(MAP(L, (1 + _["k"] + 2*_K) ?? 0))` gives peak RSS 662 MB (207 MB with `(1 + _["k"]) ?? 0`, 92 MB with the never-failing `(1 + 1) ?? 0`), 0.3 s to 5.8 s to 6.4 s across the three variants.

**Additional fixture requirements from the report:** a leak is not observable to the `.selt` harness. Add a unit test that runs the `??` plan 100k times and asserts `ctx.math_scratchpad_top == 0`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C19.

<a id="cpp-c25"></a>

### CPP-C25: ROUND/POWER validate the second argument before coercing the first (C++ only, when the call is not planned)

Source: [CPP-C25](../../cpp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`X="x"; ROUND(IF(TRUE,X,1), 2.5)` gives `E_NOT_INT at line 1 column 28` in C++; JS/PHP/Lisp/Python give `E_NOT_NUM at line 1 column 14`. Same for `ROUND(IF(TRUE,X,1), -1)` (C++ `E_RANGE` col 28) and `POWER(IF(TRUE,X,1), -1)`. Plain `X="x"; ROUND(X, 2.5)` agrees everywhere (planned).

**Additional fixture requirements from the report:** Suggest `num.round.first-arg-error-before-scale-error` and a POWER twin with an unplanned first operand (`IF(TRUE, X, 1)`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C25.

<a id="cpp-c35"></a>

### CPP-C35: The planner's logical rewrite hoists FILTER above SORT_BY, hiding the sort key's error

Source: [CPP-C35](../../cpp-code-review.md). Report labels: [medium] [confirmed; JS plans the same SQL].

**Scenario / reported observation:**

`ORDERS .> SORT_BY(_["nokey"]) .> FILTER(_["id"] > 100) .> TAKE(5)`: `run()` gives `E_NO_KEY@1:20`; plan is hybrid (SQL `WHERE id > 100`) and returns `{}`. Same with `.> MAP(RECORD("id", _["id"]))` and `SORT_BY(_["amount"] + _["nokey"])`. Position-only variant: `ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_))) .> SORT_BY(_["id"], "DESC") .> FILTER(_["id"] > 1) .> MAP(RECORD("id", _["id"], "k", _["amount"]))`: `run()` gives `E_NO_KEY@1:82` (SORT_BY), plan gives `E_NO_KEY@1:109` (FILTER).

**Additional fixture requirements from the report:** Suggest `plan.sort.filter-not-hoisted-over-raising-sort-key`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C35.

<a id="cpp-c37"></a>

### CPP-C37: Which error a program raises depends on whether the optimiser turned the arithmetic into a math plan

Source: [CPP-C37](../../cpp-code-review.md). Report labels: [medium] [confirmed, all five hosts].

**Scenario / reported observation:**

`A = "x"; A + B` gives `E_NOT_NUM` col 10 (plan: LoadVar coerces immediately), but `A = "x"; A + IF(TRUE, B, 1)` gives `E_UNDEF_VAR` col 23 (no plan: both evaluated, then coerced), and `A = "x"; A < B` (comparisons are never planned) is also `E_UNDEF_VAR`. Identical on all five hosts.

**Additional fixture requirements from the report:** Suggest `ord.arith.operand-coerced-before-next-operand-evaluated` with both planned and unplanned spellings.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C37.

<a id="cpp-c45"></a>

### CPP-C45: `SORT_BY(...) .> TAKE(n)` fused into `TOP_BY` changes which error is raised

Source: [CPP-C45](../../cpp-code-review.md). Report labels: [low] [confirmed, all five hosts].

**Scenario / reported observation:**

`L = (RECORD("a",1), RECORD("a",2)); L .> SORT_BY(_["k"]) .> TAKE("x")` gives `E_NOT_NUM col 66` on all five; the helper form (`S = SORT_BY(L, _["k"]); TAKE(S, "x")`) gives `E_NO_KEY col 53`. Same with `TAKE(1/0)` (E_DIV_ZERO vs E_NO_KEY) and `TAKE(Q)` (E_UNDEF_VAR vs E_NO_KEY).

**Additional fixture requirements from the report:** `rel.optimiser.*` covers MAP/FILTER swaps, not a raising TAKE count after a raising sort key.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C45.

<a id="cpp-c46"></a>

### CPP-C46: E_DEPTH from equality/hash paths carries no position (`line 0 column 0`)

Source: [CPP-C46](../../cpp-code-review.md). Report labels: [low] [confirmed, all hosts].

**Scenario / reported observation:**

cpp-5: `X = LIST(1); MAP(SPLIT(REPEAT("a,",197),","), (X = LIST(X); 1)); COUNT(DISTINCT(LIST(LIST(X))))` gives `E_DEPTH at line 0 column 0` in all five hosts; same for `BUCKET(LIST(LIST(X)), _, COUNT(_))`. cpp-3: `D EQL D` reports `1:3` but `D IN LIST(D)`, `DISTINCT(LIST(D,D))`, `BUCKET(LIST(D,D), _)` report `0:0` (host-built 3M-level chain, `scratchpad/cpp3/src/t2.cpp`).

**Additional fixture requirements from the report:** `10-limits` pins the codes, not the position for these builtins. Suggest `limits.depth.distinct-element-position` and `limits.depth.bucket-key-position`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C46.

## LISP report scenarios

<a id="lisp-c13"></a>

### LISP-C13: The math-plan compiler coerces each operand as it loads it, so error code and position differ from plain evaluation and from spec §7.1

Source: [LISP-C13](../../lisp-code-review.md). Report labels: [medium] [confirmed, re-verified by synthesizer; cross-host spec gap].

**Scenario / reported observation:**

`lisp/bin/sel -e 'MAX(TRUE, U)'` -> `E_NOT_NUM at line 1 column 5` (re-verified); expected E_UNDEF_VAR at column 10. `x = "abc"; x + (1/0)` -> `E_NOT_NUM @1:12` but `x = "abc"; x + IF(TRUE, 1/0, 1)` -> `E_DIV_ZERO`. `X = RECORD("a","q"); X["a"] + X["b"]` -> `E_NOT_NUM` @23 but `X["a"] > X["b"]` -> `E_NO_KEY` @32. js, php, cpp and python print exactly the same as optimised Lisp, so this is a shared design of the five math-plan compilers, not a Lisp slip; only `(eval-node (program-ast p))` differs.

**Additional fixture requirements from the report:** Suggest `math.order.left-coercion-after-right-eval` (and `num.coerce-order.plan` / `.tree`) with `MAX(TRUE, U)`, `R["a"] + R["b"]` with text left and missing right key, for `+ - * / % ABS MIN MAX ROUND POWER`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C13.

<a id="lisp-c14"></a>

### LISP-C14: `cannot-raise-p` over-approximates, so FILTER+FILTER fusion and the logical field-read rule can hide or reorder errors

Source: [LISP-C14](../../lisp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

b): `ORDERS .> MAP(RECORD("n", _["name"])) .> SORT_BY(_["r"]) .> FILTER(FALSE) .> TAKE(1)` -> `run`: E_NO_KEY; hybrid (statement `... WHERE FALSE`): empty result. Also `ORDERS .> SORT_BY(_["nosuch"]) .> FILTER(_["name"] $== "zzz") .> DROP(0)` and `ORDERS .> BUCKET(_["customer_id"], RECORD("c", _K, "n", COUNT(_))) .> SORT_BY(_["amount"]) .> FILTER(FALSE) .> SORT_BY(_["customer_id"], "DESC")`.

**Additional fixture requirements from the report:** Suggest `rel.filter.fuse.non-boolean-second-predicate-keeps-first-error` and `plan.optimizer.sort-key-missing-field-blocks-filter-swap`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C14.

<a id="lisp-c15"></a>

### LISP-C15: SORT/SORT_BY + TAKE -> TOP* fusion changes evaluation order of the count and drops key evaluation when the count is 0

Source: [LISP-C15](../../lisp-code-review.md). Report labels: [medium] [confirmed; all hosts].

**Scenario / reported observation:**

`C=0; LIST(3,1,2) .> SORT_BY(_ + (C=C+1)) .> TAKE(C)` -> unoptimised `-{"1"=t"1","2"=t"3","3"=t"2"}` (TAKE(3)); optimised `-` (TAKE(0)) (re-verified the optimised output `-`). `LIST(3,1,2) .> SORT_BY(U + 0) .> TAKE(U2)`: unoptimised E_UNDEF_VAR @24 (key), optimised @39 (count). `LIST(3,"a",2) .> SORT_BY(_ + 0) .> TAKE(-1)`: unoptimised E_NOT_NUM @26, optimised E_RANGE @41. All five hosts agree with each other (optimised).

**Additional fixture requirements from the report:** Suggest `rel.sort-take.count-evaluated-after-keys` (side-effect count) and `rel.sort-take.negative-count-after-key-error`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C15.

<a id="lisp-c34"></a>

### LISP-C34: `register-builtin` is exported, unguarded and defaults to `:overwrite t`

Source: [LISP-C34](../../lisp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`(sel:register-builtin "count" 1 1 (lambda (a c) (declare (ignore a c)) (sel:make-int 42)))` then `COUNT(LIST(1,2,3))` gives 42; `register-function "COUNT"` refuses with "is a builtin".

**Additional fixture requirements from the report:** n/a (`tools/check-api.sh` could probe "host function cannot replace builtin").

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C34.

<a id="lisp-c44"></a>

### LISP-C44: Join pre-filter state can outlive a caught error

Source: [LISP-C44](../../lisp-code-review.md). Report labels: [low] [unconfirmed].

**Scenario / reported observation:**

frames and depth are restored by `unwind-protect`, but the report slot is cleared only when consumed. The consumer keys its lookup on node identity and the reviewer could not build a program that consumes a stale report: a hardening note only.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C44.

## GO report scenarios

<a id="go-c25"></a>

### GO-C25: ROUND and POWER coerce (and type-check) argument 2 before argument 1

Source: [GO-C25](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

as reported): `ROUND(X, IF(Y=="1", "x", -1))` -> Go/C++ `E_NOT_NUM` col 29, JS/PHP col 25 (the reporter's spacing differs from the synthesizer's; columns shift accordingly). `ROUND(X, IF(Y=="1", -1, 0))` -> Go/C++ `E_RANGE`, JS/PHP/Lisp `E_NOT_NUM`. `POWER(X, IF(Y=="1", 1.5, 0))` -> Go/C++ `E_NOT_INT`, others `E_NOT_NUM`. `ROUND(NULL, IF(Y=="1","z",0))` -> Go E_NOT_NUM(z) vs E_NULL.

**Additional fixture requirements from the report:** Add a case with a non-plannable second argument and a bad first argument, asserting the column of the first.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C25.

<a id="go-c33"></a>

### GO-C33: Optimised tree reports enclosing-operator errors at the wrong node position (spec 6.3)

Source: [GO-C33](../../go-code-review.md). Report labels: [low] [confirmed, all hosts agree].

**Scenario / reported observation:**

`NOT TAKE(TAKE(LIST(1), 3), 2)` -> physical `E_NOT_BOOL` at 1:10 (plain AST evaluation 1:5); `NOT FILTER(LIST(1), TRUE)` -> 1:12 (plain 1:5); `NOT DROP(DROP(LIST(1),1),1)` `E_NO_SCALAR` 1:10 (plain 1:5); `X = LIST(1); NOT TAKE(SORT(X), 1)` 1:23 (plain 1:18).

**Additional fixture requirements from the report:** Add `opt.pos.take-take-under-operator` (`NOT TAKE(TAKE(LIST(1), 3), 2)` -> error E_NOT_BOOL at 1:5) plus DROP/DROP, FILTER(TRUE), SORT+TAKE variants.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C33.

<a id="go-c34"></a>

### GO-C34: The physical plan is observable: SORT+TAKE(0) skips sort-key errors, and fusion evaluates `n` before the sort keys

Source: [GO-C34](../../go-code-review.md). Report labels: [low] [confirmed, all hosts agree].

**Scenario / reported observation:**

`L = LIST(RECORD("a",1),RECORD("a",2)); L .> SORT_BY(_["z"]) .> TAKE(0)` -> physical `()`, unoptimised `E_NO_KEY` at 1:52 (JS/PHP/Python/C++/Lisp also `()`); `... .> SORT_BY(_["zz"]) .> TAKE(MISSING)` -> `E_UNDEF_VAR` (unfused `E_NO_KEY`).

**Additional fixture requirements from the report:** `rel.take.zero-after-a-sort-still-evaluates-the-list` covers only the list; pin the chosen behaviour for the key body.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C34.

<a id="go-c35"></a>

### GO-C35: Error precedence differs between the math plan and ordinary binary evaluation

Source: [GO-C35](../../go-code-review.md). Report labels: [low] [confirmed, all hosts agree].

**Scenario / reported observation:**

`X = "abc"; X + MISSING` -> `E_NOT_NUM` at 1:10 (all hosts); `X = "abc"; IF(TRUE, X, 1) + MISSING` -> `E_UNDEF_VAR` at 1:25 (all hosts); `"abc" == MISSING` -> `E_UNDEF_VAR`. Data-shaped: `L = LIST(RECORD("a","q","c","y")); MAP(L, r, r["a"] / r["b"])` -> physical `E_NOT_NUM`, AST evaluation `E_NO_KEY`.

**Additional fixture requirements from the report:** Add `eval.coerce.left-before-right-operand-error` with a plan-shaped and a non-plan-shaped program.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C35.

<a id="go-c44"></a>

### GO-C44: A panic inside the optimizer poisons `physOnce` for every later Run

Source: [GO-C44](../../go-code-review.md). Report labels: [low] [unconfirmed, latent].

**Scenario / reported observation:**

- **Found by:** go-3 C8.
- **Where:** `go/sel/program.go:56-61` and `63-76`. A panic inside `physOnce.Do` counts the Once as done; every later `Run` evaluates a nil `physicalAst`. ~170k programs through compile+optimise+run produced zero optimizer panics; only a synthetic test reproduces the second-call nil-pointer panic.
- **Fix sketch:** compute into a local and fall back to `p.ast` if the optimizer panics (recover inside the Once body), or optimise in `NewProgram`.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C44.

<a id="go-c45"></a>

### GO-C45: `optRenameVar` copies nodes with stale `MathPlan` pointers

Source: [GO-C45](../../go-code-review.md). Report labels: [low] [unconfirmed, latent].

**Scenario / reported observation:**

renaming a binder inside a predicate that already carries a compiled plan would leave LOAD_VAR/LOAD_LEAF pointing at the old binder. Unreachable today (the physical FILTER+FILTER merge admits only literals, the binder variable and `_K`, none of which yield a plan). No repro found.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C45.

<a id="go-c46"></a>

### GO-C46: Join prefilter report can be left stale when a panic is recovered by `??`

Source: [GO-C46](../../go-code-review.md). Report labels: [low] [unconfirmed].

**Scenario / reported observation:**

`EvalNode` restores Depth and Frames on panic but not `ctx.JoinPrefilter`/`JoinPrefilterReport`. If an inner LINK finishes and its parent then fails on the right side, a surrounding `??` swallows the error and the report stays for the next unrelated LINK to pick up as `below`, re-evaluating its left node (visible if the left has an assignment). The reviewer could not build a program reaching it: hygiene observation.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C46.

<a id="go-c47"></a>

### GO-C47: Blanket `recover()` can mask genuine Go runtime panics as "error kept"

Source: [GO-C47](../../go-code-review.md). Report labels: [low] [reasoned].

**Scenario / reported observation:**

no such panic was found in these paths, so nothing is wrong today; but a real Go bug (nil deref, index out of range) inside those probes would be silently absorbed as "skip this split" / "error kept", hiding it from tests.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C47.
