# SEL JS host: code review (correctness and performance)

Date: 2026-09-29
Scope: the JS host, `js/` (`js/src/*.mjs`, `js/src/builtins/*`, `js/src/sql/*`, `js/bin/*`). Rust is ignored. Generated files (`_map.mjs`, `_limits.mjs`, `_math_ops.mjs`, `_builtin_manifest.mjs`, `case-data.mjs`) are out of scope except for generator problems.

How the review was done: seven slice reviewers read their files in full, cross-checked against `spec/` and `conformance/`, and tried to confirm each suspected bug by running the host and comparing it with php, python, cpp, lisp and go where available. Performance claims were micro-benchmarked in a scratchpad (repo read-only). A synthesizer then merged the seven reports, removed duplicates and re-ran the top findings.

| Slice | Area | Files |
|---|---|---|
| js-1 | front end | `lexer.mjs`, `parser.mjs`, `errors.mjs`, `utf8.mjs`, limits consumer |
| js-2 | numeric core and values | `decimal.mjs`, `value.mjs`, `builtins/number.mjs`, `math_plan.mjs` |
| js-3 | evaluator, registry, host API, optimizer | `eval.mjs`, `registry.mjs`, `sel.mjs`, `optimizer.mjs`, `js/bin/*` |
| js-4 | text, regex, binary, null and control builtins | `builtins/{text,regex,binary,null,control}.mjs` |
| js-5 | structure and aggregate builtins, relational pipeline | `builtins/{structure,aggregate}.mjs` |
| js-6 | SEL to SQL translator core | `sql/{translator,normalise,binding,bindings,binder,fragment,errors,index}.mjs` |
| js-7 | SQL rendering and hybrid | `sql/{hybrid,map,emit,constants,relational-plan}.mjs` |

Confidence labels are kept as the reviewers gave them: **confirmed** means the reviewer ran a repro; **unconfirmed** means reasoned only. For performance: **measured** > **reasoned** > **guessed**. Entries the synthesizer re-ran say "re-verified by synthesizer". Where an entry merges several slices, the slice ids are listed.

Not covered by any reviewer: SQL behaviour on real MariaDB/PostgreSQL servers (no Docker; only SQLite via `node:sqlite` in js-7, everything else reasoned from emitted text), `tools/check.sh` and the fuzzers, the FILTER-over-LINK pre-filter soundness proofs (`structure.mjs` ~551-1125, read for crash paths only), and `map.mjs` beyond `checkLexical`/`chain`/`entry`.

## Executive summary

- Overall health: the core (decimal arithmetic, parser precedence, escapes, UTF-8 decoding, regex rewriting, dump/eql, LINK key logic) is sound. Reviewers ran hundreds of thousands of differential cases (257k decimal cases, ~100k pipelines, 40k SQL translations) with no divergence in those areas. The defects cluster in resource limits, host exceptions leaking out as non-`SelError`, unspecified corners that the five hosts resolve differently, and the SQL layer's scoping and budget behaviour.
- 60 correctness findings: 8 high, 20 medium, 32 low. 28 performance findings.
- Worst correctness items:
  - an aggregate whose body mutates the collection it iterates crashes with an uncaught `TypeError`, or exhausts the heap (re-verified);
  - SORT/TOP order is not a total order for numeric-looking versus non-numeric text, and five hosts give five different answers (re-verified);
  - the `??` chain and interpolation nesting are not depth-counted, so hostile input gives a raw `RangeError` (re-verified);
  - REPLACE, REPEAT and PAD leak host exceptions (re-verified);
  - regex catastrophic backtracking has no bound (re-verified);
  - in the SQL layer, aggregate binders are dynamically scoped (silently wrong SQL, re-verified) and a 300-character rule can produce exponential output.
- Cross-cutting theme: the gate is blind to two evaluation models. The JS conformance suite passes 1091/1091 with the optimiser and with the plain tree (js-3), so the math-plan operand ordering and the SORT+TAKE fusion divergences are invisible to it.
- Biggest performance wins, all measured:
  - `Value.text` flattening V8 ropes: repeated `&`/`&=` is 52x slower than needed at n=40000, and quadratic-plus, so a practical DoS;
  - the lexer, at about 60% of compile time, is 2.3-2.5x faster with three local changes;
  - rows with more than about 254 fields hit a shape-interning cliff that makes joins 36x slower;
  - the SQL translator re-evaluates constant sub-trees at every level, 150x amplification at 160 terms;
  - text builtins round-trip through code point arrays (5-17x on short strings);
  - UTF-8, hex and base64 codecs are 8-20x slower than a straight loop.
- Most fixes here are spec-first and cross-host (see "Cross-host findings"). The one rule in `CLAUDE.md` applies: spec, then `.selt`/`.sqlt` cases, then every host, then `tools/check.sh`.

---

# Correctness findings

Ordered by severity, then by confidence. Every finding below was confirmed by a reviewer unless the heading says unconfirmed.

## High

### JS-C1 [high] [confirmed, re-verified by synthesizer] An aggregate body that grows the collection it iterates crashes with an uncaught TypeError or runs out of heap
Slices: js-3 C1, js-5 C2.
- Where: `js/src/builtins/aggregate.mjs:69,75` (`walk`) and `:468-474` (`consume` loop of TOP/TOP_BY); `structure.mjs:32-46` (`forEachCollectionItem`). Root cause: `Args.val(0)` returns the variable's own Value (eval.mjs:75-78), `A[..] = ..` mutates that same object, and the loops re-read `collection.storage.length` or iterate the live `children` Map.
- What: Spec §7.3 says the body runs once per child, and §3.4 says the collection is a live value. A body that appends a non-positional key or appends past the end flips the packed list into Map mode (`storage = null`), and the next loop test dereferences null: a host `TypeError`, not a `SelError`. If the collection is already in Map mode, `for (const [k, v] of collection.children)` visits keys added during iteration and never ends (heap exhaustion). php/python/cpp all answer a snapshot value.
- Repro:
  - `node js/bin/sel.mjs -e 'A = (1,2); COUNT(MAP(A, A[COUNT(A) + 1] = 0))'` gives `TypeError: Cannot read properties of null (reading 'length')` at aggregate.mjs:69. Expected `2` (php prints 2). Same with FILTER, SUM, ALL and `COUNT(TOP_BY(A, (A[COUNT(A)+1] = 0; _), 5))` (line 469).
  - `node js/bin/sel.mjs -e 'A = LIST(1,2,3); COUNT(MAP(A, (A["k"]=1; _)))'` gives the same TypeError; php/lisp/python/cpp/go give `3`. The record variant `A = RECORD("a",1); COUNT(MAP(A,(A["b"]=2;_)))` crashes at :75. BUCKET is not affected (it snapshots via `entries()`).
  - `node --max-old-space-size=300 js/bin/sel.mjs -e 'R = RECORD("a",1); R["b"] = 2; R["c"]=3; COUNT(MAP(R, R[_K & "x"] = 1))'` gives `FATAL ERROR: ... JavaScript heap out of memory` (re-run by synthesizer: fatal within 7 s wall on a loaded box; js-3 measured rc 134 in ~2 s; php/cpp print 3).
  - Side note (js-5, spec gap, not JS-only): in-range mutation is live in js/python/cpp and a snapshot in php/lisp/go: `A = LIST(1,2,3); MAP(A,(A[3]=100;_)) .> JOIN(",")` gives `1,2,100` versus `1,2,3`. The spec does not say which.
- Fix sketch: iterate a snapshot. Only a body that contains an `assign` node (static, cacheable on the physical node) can mutate anything, so snapshot (`Array.from(children)`, captured `storage` array and length) only in that case and keep the zero-copy path otherwise. Capture `length` once in TOP's `consume` loops. Snapshot is also the semantics php/lisp/go have.
- Conformance gap: none. Cases: `A = (1,2); COUNT(MAP(A, A[COUNT(A)+1] = 0))` => 2 (and FILTER, SUM, ALL, SORT_BY key, TOP_BY key, BUCKET key), `A = LIST(1,2,3); COUNT(MAP(A, (A["k"]=1; _)))` => 3, the record variant => 3, and the Map-mode record variant. Needs the spec to say snapshot versus live for in-range mutation first.

### JS-C2 [high] [confirmed, re-verified by synthesizer for js and php] SORT / SORT_BY / TOP comparator is not a total order for numeric-looking versus non-numeric text
Slice: js-5 C1.
- Where: `js/src/builtins/aggregate.mjs:269-301` (`compareValues`), used by `doSort` (:365) and `doTop` (:413).
- What: two numeric-looking values compare as numbers, but a numeric-looking TEXT against a non-numeric TEXT falls into the `TEXT/BIN` byte-compare branch (:288) before the `rank()` fallback, so the intended NULL < BOOL < numbers < text order (pinned by `rel.sort.mixed`) never applies to text/text pairs. This is a cycle: "9" < "10" (numeric), "10" < "1a" (bytes), "1a" < "9" (bytes). V8's TimSort, Lisp/C++/Go merge sorts and PHP's sort each produce a different permutation, and within JS the TOP heap and the SORT disagree. docs/functions.md and spec §7.4 do not define the mixed case. Realistic data (IDs, SKUs, versions: "10","9","1a") hits it.
- Repro:
  - `LIST("10","9","1a") .> SORT() .> JOIN(",")` gives `1a,9,10` in js and php (re-run by synthesizer), python `1a,9,10`; lisp/cpp/go `9,10,1a`.
  - `LIST("1a","x","100","1b","9","10","2","5","30","3b","4","33") .> SORT() .> JOIN(",")` gives js `10,100,1a,1b,2,4,5,9,30,33,3b,x`; php `100,1a,1b,2,4,5,9,10,30,33,3b,x`; lisp `2,3b,4,5,30,33,100,1a,1b,9,10,x`; cpp `2,5,9,10,30,100,1a,1b,3b,4,33,x`; go `100,1a,1b,2,5,9,10,30,3b,4,33,x` (five different answers).
  - JS internal: with L = that 12-element list, `S=SORT(L); JOIN(TAKE(S,4),",")` gives `10,100,1a,1b` but `JOIN(TOP(L,4),",")` gives `1a,1b,2,33`. `SORT(SORT(L))` differs from `SORT(L)` for L = ("10","9","1a").
- Fix sketch: make the order total in the spec first: classify each key (null 0 < bool 1 < numeric-looking 2 < other TEXT/BIN 3; decimal cmp within class 2, bytes within class 3) and compare classes before values. In `compareValues` test "both numeric" and "both non-numeric text" explicitly, then byte-compare. Add the rule to SPEC §7.3/7.4 and docs/functions.md.
- Conformance gap: none (`rel.sort.mixed` has one value per class). Cases: `LIST("10","9","1a") .> SORT()`, permutations of it, `SORT(SORT(x))` idempotence, `TOP(L,n)` equals `TAKE(SORT(L),n)`.

### JS-C3 [high] [confirmed, re-verified by synthesizer] Right-associative `??` / `???` recursion is not depth-counted: hostile input gives a host RangeError instead of E_DEPTH
Slice: js-1 C1.
- Where: `js/src/parser.mjs:209-213` (the `assoc === 'R'` branch of `parseTerm`: `const right = this.parseTerm(bp);` with no `enter`/`leave`).
- What: SPEC §6.4 and the contributing trap "Watch E_DEPTH" require every nesting construct to be counted. Prefix operators and assignment were fixed; `??` and `???`, the only other right-recursive rule, were missed. `compile()` throws a raw `RangeError` at roughly 4,000 to 8,000 operators (about 20 KB). Evaluation is fine only because `??` short-circuits. `dependencies()` does raise E_DEPTH from n=200. Nothing raises E_DEPTH at parse time for any n, so hosts also disagree below the crash threshold.
- Repro: `node -e "import('/home/nathan/workspaces/nth/sel/js/src/sel.mjs').then(m=>m.compile('1 ?? '.repeat(20000)+'1'))"` throws `RangeError: Maximum call stack size exceeded` (re-run by synthesizer). At n=250 every host parses and runs it (result 1). js-1's script: n=199 ok, n>=200 E_DEPTH from `dependencies`, n=8000 RangeError from `compile`. Other hosts at n=20000: cpp segfaults (exit 139), Python RecursionError, Lisp control stack exhausted; PHP and Go are fine. First RangeError in JS at n=4003 for `??` and n=7816 for `???` (varies with JIT state).
- Fix sketch: spec first: state what `??` costs (one level per operator, like assignment). Wrap the recursion in `this.enter(t); try {...} finally { this.leave(); }` in every host, as the assignment branch does. Alternative: a loop that collects operands and folds right-to-left, but the cost must still be defined by the spec.
- Conformance gap: none (`grep '??' conformance/10-limits.selt` is empty). Cases `lim.coalesce-depth` (`1 ?? ` repeated 200 and 201 times, E_DEPTH at a pinned offset) and `lim.coalesce-depth-just-under`, pattern `lim.assign-depth`.

### JS-C4 [high] [confirmed, re-verified by synthesizer] REPLACE throws an uncaught RangeError when the replacement is longer than about 125k code points
Slice: js-4 C2.
- Where: `js/src/builtins/text.mjs:78` (`out.push(...repl)`).
- What: the spread passes every code point as a call argument. V8 refuses beyond roughly 120k arguments with `RangeError: Maximum call stack size exceeded`, a host exception that escapes `Program.run`. A correct result exists (php, python, cpp, lisp have no such limit).
- Repro: `LEN(REPLACE("a", REPEAT("b", 100000), "a"))` gives `t"100000"`. `node js/bin/sel.mjs -e 'LEN(REPLACE("a", REPEAT("b", 150000), "a"))'` gives an uncaught `RangeError` (re-run by synthesizer; crash at text.mjs:78).
- Fix sketch: `for (const c of repl) out.push(c)`, or build the result from UTF-16 chunks and never spread (see JS-P5, JS-P6).
- Conformance gap: none. Case `text.replace.long-replacement` with a 200,000-code-point replacement; the same for SPLIT with a long separator (fine today, unpinned).

### JS-C5 [high] [confirmed, re-verified by synthesizer] REPEAT, PADL and PADR have no size cap; the JS host throws host exceptions or exhausts memory
Slices: js-4 C3, js-2 N1 (cross-slice note).
- Where: `js/src/builtins/text.mjs:140` (REPEAT), `:143-155` (pad). Enabling primitive: `Args.int/nonNegInt` to `D.toSafeInt` (decimal.mjs:174), named "safe" but returning imprecise or `Infinity` values above 2^53 (`Number(digits)`).
- What: spec §6.4 caps three size arguments (ROUND, POWER, regex quantifier) but not REPEAT count or PADL/PADR width. In JS: `String.prototype.repeat` throws a bare `RangeError: Invalid string length` above ~2^29 units; `''.repeat(Infinity)` throws `RangeError: Invalid count value`, so `REPEAT("", <400-digit number>)` crashes even though the answer is `""`; PADL/PADR build a JS array one element at a time (width 20M took 4.7 s and multiple GB, then `Invalid array length`). Every other host fails in its own way (PHP: memory fatal, Lisp: heap exhaustion).
- Repro: `node js/bin/sel.mjs -e "REPEAT('a', 1000000000000)"` gives `RangeError: Invalid string length` (re-run by synthesizer). `REPEAT('', 1$(printf '%0400d' 0))` gives `RangeError: Invalid count value`. `PADL("abc", 99999999999999999999999, "x")` gives `RangeError: Invalid array length`. `REPEAT("", 99999999999999999999999)` correctly gives `""` (finite count). js-2 N1: `REPEAT("ab", 9999999999)` and `PADL("a", 99999999999, "x")` escape as raw `RangeError` from `program.run`.
- Fix sketch: add a text-length cap to spec §6.4 (for example 1,000,000 code points, or the 2,000,000 the limits tests already assume), enforced in REPEAT/PADL/PADR/REPLACE/`&` as `E_RANGE` before allocating; compute `count * len` in decimal-safe arithmetic; handle empty string and empty result first.
- Conformance gap: none. Cases `text.repeat.huge-count` and `text.padl.huge-width` (both `E_RANGE`), `text.repeat.empty-huge-count` (returns `""`).

### JS-C6 [high] [confirmed, re-verified by synthesizer] RMATCH / RFIND / RGROUPS / RREPLACE have no protection against catastrophic backtracking
Slice: js-4 C1.
- Where: `js/src/builtins/regex.mjs:204-244` (compile), `:286`, `:295`, `:305`, `:327`. The validator (:61-118) allows nested quantifiers.
- What: the portable subset allows `(a+)+`, `(a|a)*` and similar, and the JS host hands them to V8's backtracking engine with no step limit. A 45-character program can pin the evaluator for hours. Spec §6.4 gives the reason for caps ("a hostile rule becomes a stack overflow") but names no regex-time bound. Hosts differ: PHP returned FALSE in 117 ms at both sizes, C++ died with an uncaught `srell::regex_error error_complexity` (core dump, exit 134), Python is also exponential (0.68 s at n=22, 7.1 s at n=26).
- Repro (js-4 measured): `RMATCH('^(a+)+$', REPEAT('a', N) & 'b')` gives FALSE after 74 ms at N=20, 188 ms at N=24, 700 ms at N=26, 3.5 s at N=28, 14 s at N=30 (about 4x per 2 characters). Re-run by synthesizer at N=26: FALSE, 8.1 s wall including startup on a heavily loaded box (exponential growth confirmed; the absolute number is noisier than js-4's).
- Fix sketch: needs a spec decision first: (a) forbid nested unbounded quantifiers (cheap, but rejects legitimate patterns and does not cover `(a|aa)+`, `(.*)*`), (b) implement a small Thompson/Pike matcher in every host (the subset has no backreferences or lookaround, so it is regular) for linear time and identical semantics, or (c) define a step budget and a new error code. The C++ uncaught exception is a separate bug in that host.
- Conformance gap: none. Cases `re.dos.nested-plus` and `re.dos.alt-star` once a bound exists.

### JS-C7 [high] [confirmed, repro 1 re-verified by synthesizer] SQL translator: aggregate binders are dynamically scoped
Slice: js-6 C1. Shared with the Python host (identical output on repros 1-3).
- Where: `js/src/sql/translator.mjs:1205-1206` (`fromBinder`, NODE case), `:1198-1203` (`binder`), `:1384` (`source`, NODE case), `:1547-1559` (`withElement`), `:1567-1616`.
- What: `Binder.node(elem)` stores an AST node that is re-rendered later inside whatever frames are on the stack at the point of use. A free variable or `_K` in the element resolves against binders opened after the element was written. SEL captures values at collection time (spec §7.3). Consequences: (a) a silently wrong constant SQL expression; (b) a false E_SQL_DEPTH refusal for the natural nested-`_` shape, with a message that claims the evaluator would answer E_DEPTH; (c) an uncaught RangeError from `tryTranslate` via unbounded recursion in `source()` (not depth-counted).
- Repro (js-6 helper `tr(src, bindings, dialect)`; `A` = `Binding.column('a','t','NUM')`, `C` = `Binding.columns(...)`):
  1. `ANY((0,0), ALL((_K, 5), I, I > 1))` (no bindings), mariadb and postgresql: SEL evaluates to TRUE (outer key "2" gives (2,5)). The translation uses the inner key '1' in both iterations, so the SQL is FALSE. Re-verified by synthesizer on mariadb: SEL gives `TRUE`; the translation is `(((CASE WHEN ('1' REGEXP ...) THEN CAST('1' AS DECIMAL(65,10)) ... END > 1) AND (5 > 1)) OR ((CASE WHEN ('1' REGEXP ...) ... > 1) AND (5 > 1)))`, i.e. `_K` is '1' in both halves.
  2. `ALL((A, 1), I, ALL((5, 6), A, I > 0))` with A a column: SEL with A=-1 gives FALSE; the translation is `((5 > 0) AND (6 > 0)) AND ((1 > 0) AND (1 > 0))`, constant TRUE.
  3. `ANY((A, 2), ALL((_, 5), _ > 0))`: SEL answers a value; translator says E_SQL_DEPTH "the evaluator answers E_DEPTH for it". Same for `ANY(C, ALL((_, 5), _ > 0))` and `ANY(C, X, ALL((X, 5), X, X > 0))`.
  4. `Sql.tryTranslate(compile('ANY((Y, 2), Y, ALL(Y, Z, Z > 0))'), 'mariadb', {Y: Binding.column('y','t','NUM')})` throws `RangeError: Maximum call stack size exceeded` (same for COUNT(Y), HAS(Y, 1), JOIN(Y, ",")); SEL gives TRUE.
- Fix sketch: make binders lexical. Store a copy of `this.frames` in `Binder.node` and render/resolve a NODE payload with `this.frames` temporarily replaced by that snapshot (both in `fromBinder` and in `source()`, where recursion must also count depth). The synthesized `_K` text node is only safe today because it has no free variables.
- Conformance gap: none. Cases `agg.scope.inner-binder-does-not-capture-outer-element-key` (repro 1), `agg.scope.same-binder-name-nested` (repro 3), `agg.scope.element-named-like-inner-binder` (repro 2), and a refusal-or-translation (not a throw) case for repro 4.

### JS-C8 [high] [confirmed] SQL translator: exponential output and time from small programs (no size or work budget)
Slice: js-6 C3 and P2 (also related js-7 P1 and JS-P4 here). Shared with Python by design.
- Where: `js/src/sql/normalise.mjs:143-208` (a definition is inlined as a shared pointer, so a DAG becomes a tree in the render walk), `translator.mjs:1504-1514` (`aggregate` unrolls every element and re-renders element nodes per use), `:858-864`, and `constants.validate` at normalise.mjs:90.
- What: the only bound is depth (200). Neither the inlined node count nor the output size is bounded, while the evaluator is linear. `X0 = A; X1 = X0 + X0; ...; Xn > 0` is a ~10*n byte program whose translation has 2^n leaves. Constants are worse because `record()` calls `validate` (evalNode) on the expanded tree and `node()` validates at every level. A public rule engine accepting user-authored rules can be made to burn CPU and RAM by a 300-character rule.
- Repro (measured, mariadb): column base, n=18: 1.0 s / 3.1 MB SQL; n=20: 3.6 s / 12.6 MB; n=22: 15 s / 50 MB. Constant base `X0 = 1`: n=16: 0.8 s, n=20: 14 s / 6.3 MB, n=22: 66 s / 25 MB. SEL evaluates the n=200 version in 23 ms. Via binders with no assignments: `ALL((A,A), V1, ALL((V1,V1), V2, ... V10 > 0))` grows 4x per level (n=10: 20 KB, n=15: 655 KB, a 306-char program; n=25 is hundreds of GB).
- Fix sketch: count rendered nodes (or fragment parts) in the Translator and in `substitute` (budget of about 1e5 nodes, refused as E_SQL_UNSUPPORTED / E_SQL_DEPTH "expands to more than N nodes"). `substitute` can memoise each definition's size and add it at each use.
- Conformance gap: none. Cases `norm.inline.expansion-budget` (n=30 doubling chain gives a refusal) and an aggregate-nesting equivalent.

## Medium

### JS-C9 [medium] [confirmed, re-verified by synthesizer] The math plan coerces operands at load time; the tree evaluator evaluates both operands first and coerces afterwards
Slices: js-2 C1, js-3 C2. All five hosts agree on the planned answer (their plan executors were transcribed), so the divergence is the plan versus the spec/tree evaluator, not host versus host.
- Where: `js/src/eval.mjs:145-158` (`LOAD_VAR`/`LOAD_LEAF` call `asDecimal` immediately) versus `eval.mjs:317-326` (`evalBinary` evaluates `l` and `r` before any `asDecimal`); `js/src/math_plan.mjs:57-61,177-179`; MIN/MAX fold at :158-170 (coerces `MIN(a,b)` before `c` is evaluated).
- What: spec §7.1 says a strict function's arguments are all evaluated left to right before the body runs, §3.4 says a pending reference sees later mutations, and §6.2/§6.3 do not say whether operand N is type-checked before operand N+1 is evaluated. The same program gives a different answer depending on whether the optimiser could plan it. It is observable in values because `??`/`???` swallow only E_NO_KEY/E_UNDEF_VAR: `(NULL - MISSING) ?? 7` gives E_NULL in all five hosts but `(NULL == MISSING) ?? 7` gives 7 (comparisons go through `evalBinary`, arithmetic through the plan). It also breaks §3.4: `A = (1,2); A + LEN(A[1] = "xyz")` is 4 through the plan and E_NOT_NUM@1:12 through the plain tree. The plain tree is what runs whenever `exceedsDepth` (optimizer.mjs:566-587) sees a >200-deep node anywhere in the program, even in a dead branch: `IF(TRUE, "abc" + 1/0, <300-term 1+1+... chain>)` reports E_DIV_ZERO@1:19 while `IF(TRUE, "abc" + 1/0, 1)` reports E_NOT_NUM@1:10 (identical in php, cpp, python).
- Repro (re-verified by synthesizer where marked):
  - `node js/bin/sel.mjs -e '"abc" + 1/0'` gives `E_NOT_NUM at line 1 column 1` (re-verified); the plain tree gives `E_DIV_ZERO@1:10` (js-3 `diff.mjs`).
  - `A = "x"; A + B` gives `E_NOT_NUM at 1:10` (re-verified); the unplanned twin `A = "x"; A + (C = B)` gives `E_UNDEF_VAR at 1:19` (re-verified).
  - `(NULL - MISSING) ?? 7` gives `E_NULL` (re-verified); `(NULL == MISSING) ?? 7` gives `7` (re-verified).
  - `ROUND("x", Y)` gives `E_NOT_NUM`; `ROUND("x", (C=Y))` gives `E_UNDEF_VAR`. `MIN(1, "x", Y)` / `MAX(1, "x", Y)` give `E_NOT_NUM` at col 8; `MIN(1, "x", (C=Y))` gives `E_UNDEF_VAR`.
  - Mutation: `A = (1, 2); A + LEN((A[1] = 10; "ab"))` gives `3`, while `A = (1, 2); A + IF(TRUE, LEN((A[1] = 10; "ab")), 0)` gives `12`, and `A = (1, 2); A + (A[1] = 10)` gives `20`. Same program shape, three answers. Python, PHP, C++, Lisp print the same as JS.
  - Fuzz (js-3): 223-230 of 20,000 random arithmetic programs give a different error code/position between the two models; no OK-versus-OK value differences unless a leaf mutates state.
- Fix sketch: choose one model in the spec (js-3 favours "evaluate both operands, then coerce left then right"). In the plan, let LOAD_VAR/LOAD_LEAF store the raw Value in the scratchpad and let each arithmetic step coerce `src1` then `src2` (Value caches `_decimal`); for MIN/MAX emit all operands first, then chain. That is a five-host change. If the current plan behaviour is the decision, `evalBinary` must interleave for `+ - * / %` and the deep-tree fallback stops being a different language.
- Conformance gap: none; the suite passes with either model (1091/1091 through both trees, js-3). Cases `plan.order.binary-left-notnum-right-undef` (`A = "x"; A + B` expects E_UNDEF_VAR at B), `plan.order.strict-args-min`, `plan.order.round-arg-order`, `plan.alias.list-var-mutated-by-leaf` (expects `12`), `"abc" + 1/0`, `(NULL - MISSING) ?? 7`, `A = (1,2); A + LEN(A[1] = "xyz")`, and one program with a dead >200-deep branch beside a live `"abc" + 1/0`.

### JS-C10 [medium] [confirmed] Lexer recursion for nested interpolation is not depth-bounded; cost grows faster than linearly
Slice: js-1 C2.
- Where: `js/src/lexer.mjs:147-182` (`lexQuoted`), `:212-254` (`matchBrace`/`skipQuoted`), `:259-280` (`emitParts` calls `lexRange` calls `lexQuoted`).
- What: `"{"{"{...1...}"}"}"` recurses three ways and none counts against MAX_DEPTH. The parser's E_DEPTH (pinned at 1:101 for this shape) is reached only after the whole source is lexed, so JS gives `RangeError` at about 1,600 levels (6 KB source). Each level rescans its whole interior (outer `matchBrace` skips the inner literal, then `lexRange` lexes it and its own `matchBrace` skips it again), so lexing is O(n * depth). Measured lexing only: depth 500 about 9 ms, 1000 about 31 ms, 1500 about 81 ms. PHP was measured at depth 50,000 (about 200 KB): aborted after more than 110 s CPU; JS is protected only because its stack overflows first.
- Repro: js-1 `interp.mjs` prints for depth 1000 `E_DEPTH 1:101`, for 3000 and 5000 a `RangeError`. A file of 5,000 nested levels: php, cpp, go and lisp print `E_DEPTH at line 1 column 101`; js and Python print RangeError / RecursionError.
- Fix sketch: give the lexer its own nesting counter and raise E_DEPTH once interpolation nesting could no longer parse anyway (each interpolation costs four parse levels, so a cap of MAX_DEPTH/4 nested literals cannot change a result that would otherwise have parsed); make it match the parser's reported position, or lex iteratively with an explicit stack and record each literal's matching `}` once to remove the rescans.
- Conformance gap: `lim.parse-depth` covers 100 nested parens only. Add `lim.interp-depth` with a few thousand nested levels expecting `E_DEPTH 1:101` (also catches quadratic blowups by timeout).

### JS-C11 [medium] [confirmed, all five hosts agree] An interpolation body is spliced as raw tokens without checking that its parentheses balance
Slice: js-1 C3.
- Where: `js/src/lexer.mjs:259-280` (`emitParts`). Same design in the other hosts.
- What: SPEC §2.6 and grammar.md say the braces contain "a full SEL expression" and that `{}`-less literals always go through `&`. `1) + (2` is not an expression, but the token splice accepts it: a stray `)` closes the synthetic `(` early and a stray `(` reopens one that the synthetic `)` later closes. The literal can then evaluate as something other than a TEXT `&` chain (a sequence, or a list, which §2.6 is designed to prevent), and the surrounding call's argument count can change.
- Repro (js, php, cpp, python, go print identical results): `"{1) + (2}"` gives `3` (expected E_SYNTAX); `COUNT("{1), (2}")` gives `2` (expected E_SYNTAX); `"a{1);(2}"` gives `2`, discarding the literal text; `A=(1,2); COUNT("{A) ; (A}")` gives `0`. Re-verified by synthesizer: `"{1) + (2}"` gives `3` and `COUNT("{1), (2}")` gives `2`.
- Fix sketch: spec first. In `emitParts` track paren depth over the emitted interpolation tokens and raise E_SYNTAX at the first unmatched `)` (or at the `{` if a `(` is left open); or parse the body with its own parser instance and splice the subtree. All hosts.
- Conformance gap: none (`lex.interp.*` uses balanced bodies). Cases `lex.interp.unbalanced-close` (`"{1) + (2}"` E_SYNTAX), `lex.interp.unbalanced-open` (`"{(1}"`, errors today only by accident at end of input), and `COUNT("{1), (2}")` E_SYNTAX.

### JS-C12 [medium] [confirmed] Uncounted recursion over the AST in aggregate/join pre-analysis helpers
Slice: js-5 C6.
- Where: `aggregate.mjs:30-43` (`nodeContainsVar`, called by `walk`, `doTop`, `doBucket`), `structure.mjs:159-172` (`exprDependsOnlyOn` via `tryExtractEquiKeys`), also `structure.mjs` `pureSource` :564, `readSelf` :623, `aggregate.mjs` `readsOnlyFields` :157.
- What: spec §6.4 says a tree walk needs its own count because `1+1+1+…` builds a tree as deep as it is long. The evaluator and parser are protected (100,000 terms gives E_DEPTH cleanly); these pre-analysis walks run before the evaluator can raise E_DEPTH and are not.
- Repro: `COUNT(MAP(LIST(1,2), _+_+…(30,000 terms)…+1))` gives `RangeError: Maximum call stack size exceeded` at aggregate.mjs:30; threshold between 8,000 terms (proper E_DEPTH) and 12,000. Same with `TOP(LIST(1,2), _+_+…, 1)` and `LINK(.., _1+_1+…+0 == _2)` (structure.mjs:159). SORT is fine. php/lisp also fail on 100k terms in their own ways; cpp printed nothing.
- Fix sketch: give the walks a depth argument and raise E_DEPTH at 200 (matching the evaluator) or return the conservative answer past the cap; or cache one boolean on the node (JS-P14) and compute it iteratively.
- Conformance gap: 10-limits.selt covers eval/parse depth only. Add an aggregate-body variant with a 5,000-term chain (E_DEPTH at the same column on every host).

### JS-C13 [medium] [confirmed] SORT/SORT_DESC/SORT_BY + TAKE fusion into TOP/TOP_BY changes evaluation order of the count, keys and side effects
Slice: js-3 C3. All five hosts implement the fused behaviour, so this is a shared, undocumented deviation from §6.2.
- Where: `js/src/optimizer.mjs:384-392` (rule `SORT* then TAKE -> TOP*`).
- What: spec §7.4 defines TOP as "the first n of SORT, as one step" and requires the count-zero list evaluation to be kept. The fusion loses three other orderings: (a) with `n = 0` the sort keys are never evaluated, so a key that would raise E_NO_KEY/E_NULL yields the empty list; (b) `n` is validated before the keys, so `... .> SORT_BY(_["zz"]) .> TAKE(0.5)` gives E_NOT_INT where the plain tree gives E_NO_KEY; (c) `n` is evaluated before the key expressions, so side effects in `n` are visible to the keys: `X = 1; (3,1,2) .> SORT_BY(_ * X) .> TAKE((X = -1; 2))` sorts with X = -1 giving (3,2); the plain tree gives (1,2).
- Repro: `node js/bin/sel.mjs -e 'LIST(RECORD("a",1)) .> SORT_BY(_["zz"]) .> TAKE(0)'` gives `-` (re-verified by synthesizer); the plain tree gives E_NO_KEY@1:33; `TAKE(1)` does raise E_NO_KEY. Fuzz: 55 diffs in 3000 programs, all n=0 / invalid n.
- Fix sketch: either document that TOP's contract is "keys of the taken rows only" and pin it, or restrict the fusion to a literal non-negative-integer `n` >= 1 whose keys cannot raise or have effects (`cannotRaise` test on the key expression).
- Conformance gap: `rel.take.zero-after-a-sort-still-evaluates-the-list` pins only the list. Add cases for a key that raises with TAKE(0), and for an invalid `n` after a raising key.

### JS-C14 [medium] [confirmed] DEDUPE/DISTINCT and bare/projection BUCKET go quadratic on hash-colliding keys (unseeded 32-bit FNV-1a)
Slice: js-5 C3.
- Where: `value.mjs:662-703` (`structuralHash`/`stringHash`/`mixHash`) as used by `structure.mjs:137-152` (`doDedupe`: `bucket.some(existing => item.eql(existing))`) and `aggregate.mjs:551-553` (`doBucket`: `bucket.find(...)`).
- What: the hash is deterministic and weak. FNV-1a is invertible per byte, so strings can be chained with birthday collisions (~2^16 work per block) to produce 2^k distinct strings with one identical hash. All land in one bucket and every insert scans it with `eql`. About 0.5 MB of input buys seconds and 3 MB buys minutes.
- Repro (generator `scratchpad/js5/flood.mjs`; bench `bench1.mjs`): n distinct 48-char strings, all same hash: DEDUPE n=1024 145 ms, n=4096 519 ms, n=8192 2186 ms; BUCKET(L,_,_) n=8192 3327 ms. Non-colliding: DEDUPE 7.5 ms, BUCKET 13 ms. 4x n gives 4-6x time; n=65536 extrapolates to ~2.5-3.5 minutes.
- Fix sketch: for scalar-only keys (TEXT/BOOL/BIN with no children) key a Map on an exact string (`"t"+scalar`, `"b"+hex`, ...); use `structuralHash` only for compound values, with a per-process random seed. Order of first appearance is unchanged.
- Conformance gap: none (21-structural-hash-identity checks identity, not cost). Better a `tools/` benchmark scenario than a `.selt`.

### JS-C15 [medium] [confirmed] Numeric equi-join key canonicalisation is quadratic in the number of trailing fractional zeros
Slice: js-5 C4.
- Where: `structure.mjs:211-215` (`canonicalJoinKey`: `while (scale > 0 && digits % 10n === 0n) { digits /= 10n; scale--; }`).
- What: strips trailing zeros one BigInt division at a time; each division is O(size of digits); `"1."` followed by n zeros parses to digits=10^n, scale=n, so n divisions of an n-digit BigInt. Fractional digits are legal up to 1,000,000 (§6.4), so one 100 KB numeric text used as a `==` join key stalls the process; it is per row and bypasses the E_RANGE caps.
- Repro (`bench2.mjs`): `A=[{k:"1."+"0"*n}]; B=[{k:"1"}]; COUNT(LINK(A,B,_1["k"]==_2["k"]))`: n=5,000 60 ms, 20,000 1.0 s, 50,000 6.0 s, 100,000 19 s, 200,000 83 s. Plain `_1["k"]==_2["k"]` on the same values takes 2-34 ms.
- Fix sketch: format first, then trim trailing `0` characters (and a dangling `.`) from the string, which is linear; or count trailing zeros once from the decimal string.
- Conformance gap: `rel.link.numeric-key-*` pin key equivalence but not cost.

### JS-C16 [medium] [confirmed] `exprDependsOnlyOn` ignores `list` AST nodes: a comma-list operand is mis-classified as one-sided and the join raises a spurious E_UNDEF_VAR (JS and Lisp; php/python/cpp/go answer)
Slice: js-5 C5.
- Where: `structure.mjs:159-172` (`default: return true`; no case for `t: 'list'`; `pureSource`, `nodeContainsVar` and `readSelf` do handle `list`/`items`).
- What: in `LINK(.., (_1["k"], _2["k"]) == _1["k"])` the left operand reads both binders, but `exprDependsOnlyOn(list, leftNames)` returns true. With the swapped orientation the "right key" expression is evaluated in the right-only frame where `_1` is unbound. §7.4 says an equi-join is used only when the two sides "read one binder each"; anything else is evaluated per pair.
- Repro: `LINK(LIST(RECORD("k",1)), LIST(RECORD("k",1)), (_1["k"], _2["k"]) == _1["k"]) .> COUNT` gives `E_UNDEF_VAR ... undefined variable _1` in js and lisp; `1` in php/python/cpp/go. `... _1["k"] == (_2["k"], _1["k"]) AND TRUE` (forces the generic path) gives `0` in all hosts, so the plain form disagrees with itself.
- Fix sketch: add `case 'list': return node.items.every(...)`, or better default to `false`/refuse and enumerate the leaf node types (`num`, `text`, `bool`, `null`) so a new node type fails safe.
- Conformance gap: none. Add the two expressions to 15-relational.selt.

### JS-C17 [medium] [confirmed] V8 regexp backtrack-stack overflow escapes as an uncaught RangeError from a regex builtin
Slice: js-4 C4.
- Where: `regex.mjs:286-288` (`re.test`), `:295`, `:305`, `:261` (`re.exec` inside `matches`). Only `new RegExp` is wrapped in try/catch (:235-239).
- What: on long subjects a capturing loop such as `(a|b)*` overflows V8's irregexp backtrack stack and `re.test`/`re.exec` throw `RangeError: Maximum call stack size exceeded`, not a SelError. Threshold about 4M characters for `^(a|b)*$`, 5-20M for non-capturing forms. No other host produces an exception here.
- Repro: `RMATCH('^(a|b)*$', REPEAT('a', 5000000))` throws the RangeError; n=3,500,000 returns TRUE. `LEN(RREPLACE('(a|b)*', 'x', REPEAT('a', 5000000)))` throws the same.
- Fix sketch: catch `RangeError` around `test`/`exec` and convert to a SelError; needs an errors.md entry (`E_RANGE` or a new code). Goes away with a non-backtracking engine (JS-C6).
- Conformance gap: none, and a test needs a multi-megabyte subject, so use host unit tests.

### JS-C18 [medium] [confirmed] The regex class validator rejects POSIX classes only at the start of a class: `[a[:digit:]` is accepted here and rejected by PCRE
Slice: js-4 C5. The validator is transcribed per host; the JS copy has the hole.
- Where: `regex.mjs:163-165` (checks `[:` only right after the opening `[`/`[^`), loop :170-196.
- What: spec §7.8 rejects POSIX classes. Inside the class body, `[` followed by `:`, `.` or `=` is a literal `[` in ECMAScript and three other hosts but starts a POSIX class / collating element in PCRE, so the same pattern matches in JS, Python, C++, Lisp and is a compile error in PHP.
- Repro: `RMATCH('[a[:digit:]', ':')` gives TRUE in js/python/cpp/lisp and `E_REGEX_SYNTAX ... PCRE rejected /[a[:digit:]/` in php. `[a[.x.]` and `[a[=x=]` split the same way.
- Fix sketch: in `validateClass` reject `[` followed by `:`, `.` or `=` anywhere in the class (simplest: reject any unescaped `[` inside a class and say to write `\[`), in all five hosts.
- Conformance gap: `re.posix-class` cases cover the leading form only. Add `re.class.posix-not-leading` and `re.class.collating-element`.

### JS-C19 [medium] [confirmed] Group nesting depth is not bounded by the regex validator; PCRE and SRELL reject at 250, JS/Python/Lisp accept
Slice: js-4 C6.
- Where: `regex.mjs:85-97` (no depth counter).
- What: PHP fails at 251 nested groups (PCRE2 limit 250), C++ (SRELL) fails at a similar depth, JS accepts thousands. At 100,000 nesting JS fails with a V8 message mapped to `E_REGEX_SYNTAX` (no crash); compile time at 10,000 is 297 ms. Spec §6.4 already states the principle for the quantifier bound.
- Repro: pattern `'(' * 300 + 'a' + ')' * 300` with `RMATCH(<pat>, 'a')`: js TRUE, python TRUE, lisp TRUE, php `E_REGEX_SYNTAX ... PCRE rejected`, cpp `E_REGEX_SYNTAX ... error_complexity`. PHP boundary: 250 nested groups TRUE, 251 rejected.
- Fix sketch: add a group-nesting cap (250) to spec §6.4 / limits.json, count `(`/`)` in `validate`, raise `E_REGEX_SYNTAX`. PCRE also caps total groups at 65535 (untested).
- Conformance gap: none. Cases `re.limits.group-depth-250-ok` and `re.limits.group-depth-251-rejected`.

### JS-C20 [medium] [confirmed] `Value.num` accepts a decimal record with a non-boolean `neg`, producing wrong arithmetic
Slice: js-2 C2.
- Where: `value.mjs:47-54` (`checkDecimal`), consumed by decimal.mjs `add` (`a.neg === b.neg`), `mul`, `cmp`, `div`.
- What: spec §8 says a constructor given "not a decimal at all" must raise E_BAD_ARG. `checkDecimal` validates `digits` and `scale` but not `neg`; the arithmetic compares signs with `===`/`!==`, so any non-boolean `neg` silently corrupts results. The natural mistake is a caller building `{ digits: 5n, scale: 0 }`.
- Repro: `Value.num({digits:5n, scale:0})` then `A + 5` gives `0` (expected 10). `Value.num({neg:1, digits:5n, scale:0})` then `A * -1` gives `-5` (expected 5). `Value.num({neg:'yes',digits:5n,scale:0}).scalar` is `-5`.
- Fix sketch: require `typeof d.neg === 'boolean'` in `checkDecimal` and store a normalised copy (see JS-C33).
- Conformance gap: none. API probe: `Value.num` with a missing/non-boolean sign is E_BAD_ARG.

### JS-C21 [medium] [confirmed] `Value.fromNative` on a sparse array builds a corrupt list; later use throws raw TypeError
Slice: js-2 C4.
- Where: `value.mjs:625` (`x.map(...)` preserves holes).
- What: `storage` contains empty slots. `dump()` prints `"1"=t"1", , "3"=t"3"`, `COUNT` reports 3, and any walk dereferences `undefined`. Spec §8: never the host's own exception.
- Repro: `compile('L, 5').run({L:[1,,3]})` throws `TypeError: Cannot read properties of undefined (reading 'clone')`; `DEDUPE(L)` and `JOIN(L, ",")` throw `TypeError: .for is not iterable`; `SORT(L)` returns a dump with a dangling comma. `undefined` inside an ordinary array is fine (NULL); only holes break.
- Fix sketch: `Array.from(x, (e) => Value.fromNativeAt(e, depth + 1))` (visits holes as undefined, so NULL), or reject holes with E_BAD_ARG.
- Conformance gap: none (api probes do not use a sparse array).

### JS-C22 [medium] [confirmed] Hybrid planner: a SQL prefix ending in a FILTER loses FILTER's retained keys; the continuation sees SQL row ordinals
Slice: js-7 C1. Python's planner classifies the same programs as `hybrid` with the same statement, so it is cross-host (only JS was executed, on SQLite).
- Where: `js/src/sql/hybrid.mjs:690-716` (prefix search loop; the only key guard is inside `tryPlanFallthrough` via `FALLTHROUGH_DOWNSTREAM`, line 237); `executeHybrid:721-734` binds DB rows as a fresh 1..n list.
- What: spec §7.3 says FILTER keeps its input's keys, and a `_K` read after a step sees that step's keys; an implementation "must not let that show". The planner splits `ORDERS .> FILTER(sql-able) .> <step needing memory>` as SQL prefix + continuation over `_INPUT`, whose rows are renumbered 1..n. So (a) a continuation FILTER answers keys "1.." where `run()` answers the retained keys, and (b) any `_K` in the continuation sees the wrong number. The same defect exists in the fallthrough path for a custom MAP pair reading `_K` (line 312 rejects only whole-row reads). The project knows this hazard (`plan.fallthrough.filter-under-its-own-binder-reads-a-pushable-key`, optimiser `keysRenumberedBy`); it is not applied to the general split.
- Repro (js7/h.mjs, SQLite, rows id 1..6, `BACKWARDS` has no SQL spelling on sqlite):
  - `ORDERS .> FILTER(_["id"]>2) .> FILTER(BACKWARDS(_["name"]) $== "ahpla")`: `run()` gives `{"3"={id=3,...}}`; `executeHybrid` gives `{"1"={id=3,...}}`.
  - `ORDERS .> FILTER(_["id"]>2) .> MAP(RECORD("k", BACKWARDS(_["name"]) & _K))`: `run()` gives k = ahpla3, ammag4, ïnü5, ahplA6; hybrid gives ahpla1, ammag2, ïnü3, ahplA4.
  - `ORDERS .> FILTER(_["id"]>2) .> MAP(RECORD("a", _["id"], "b", BACKWARDS(_["name"]) & _K))` (fallthrough path): same wrong `_K`.
  - Fuzz: ~1.5% of random 1-4 step pipelines hit this ("KEYS" class).
- Fix sketch: when choosing a split point, back off to before the FILTER if the prefix keeps FILTER-retained keys after its last renumbering step and the continuation is key-visible (its steps include a FILTER, or any body reads `_K`). In the fallthrough, reject custom pairs that read `_K` when any FILTER precedes the MAP.
- Conformance gap: none. In 25-hybrid-plans.sqlt: `ORDERS .> FILTER(...) .> FILTER(<no-sql fn>)` = pure_memory (or hybrid with prefix stopping before the FILTER), and `... .> FILTER(...) .> MAP(RECORD("k", <no-sql fn> & _K))`. The `--- plan` runner checks only classification/SQL text, so an executing case against SQLite is needed.

### JS-C23 [medium] [confirmed] Hybrid fallthrough (partially local MAP) with downstream TAKE/DROP hides errors the custom half raises on rows the LIMIT cuts off
Slice: js-7 C2.
- Where: `hybrid.mjs:237` (`FALLTHROUGH_DOWNSTREAM = SORT_BY, TOP_BY, TAKE, DROP`) and `:314-319`.
- What: SEL evaluates the whole MAP and only then takes the first n. The plan pushes `LIMIT n` into SQL and applies the custom half to just those n rows, so an error on row n+1 never happens. The optimiser has the concept (`cannotRaise`/`mapCannotRaise`, optimizer.mjs:239-267, SEM-07); it is not consulted here.
- Repro: `ORDERS .> MAP(RECORD("a", _["id"], "b", LEN(BACKWARDS(_["name"])) + IF(_["id"] > 4, "x", 1))) .> TAKE(2)`: `run()` gives `E_NOT_NUM`; `executeHybrid` (plan `hybrid`, SQL `SELECT id AS a, name, id ... LIMIT 2`) gives `{"1"={a=1,b=6},"2"={a=2,b=5}}`. Same with `.> DROP(1)`.
- Fix sketch: allow TAKE/DROP downstream only when the custom half provably cannot raise (reuse `cannotRaise`), otherwise put the split before the MAP (the general prefix loop already does that correctly).
- Conformance gap: none. Case `plan.fallthrough.take-after-a-map-that-can-raise-stays-local`.

### JS-C24 [medium] [confirmed] `checkNumericGuard` memoises the dialect before it checks, so a failing check is skipped on every later call
Slice: js-7 C3. PHP (`Map.php:334`) has the same shape; Python's docstring is the same text; likely all hosts.
- Where: `js/src/sql/map.mjs:227-252` (`guardChecked.add(dialect)` at 229 precedes the `throw`s at 235-250).
- What: sql/MAP.md §7 rule 10 exists because a numericGuard that disagrees with ISNUM "fails silently: it emits SQL that answers where SEL would not". The first translation needing the guard throws (a plain Error); the second finds the dialect in `guardChecked`, returns, and emits the wrong guard.
- Repro (js7/guard.mjs): `map.defineDialect('pg-bad',{extends:'postgresql',lexical:{numericGuard:"CASE WHEN ({textCast:0} ~ '^.*$') THEN CAST({0} AS NUMERIC) ELSE NULL END"}})`, then translate `T .> FILTER(_["s"] > 0)` three times: call 0 throws "declares a numericGuard that does not carry '^-?[0-9]+(\.[0-9]+)?$'"; calls 1 and 2 return `... WHERE (CASE WHEN (... ~ '^.*$') THEN CAST(... AS NUMERIC) ELSE NULL END > 0)`.
- Fix sketch: `guardChecked.add(dialect)` only after all three checks pass (or delete it in a catch).
- Conformance gap: none (runtime-registration checks are not in `.sqlt`); a unit test calling translate twice on a bad dialect.

### JS-C25 [medium] [confirmed] A registered dialect whose `textEscape` map does not escape the quote is accepted and produces injectable inline literals
Slice: js-7 C4.
- Where: `map.mjs:372-397` (`checkLexical`, `types[key] === 'map'` branch) with `emit.mjs:132-159` (`textLiteral`).
- What: `checkLexical` rejects `textEscape` given as a string (comment at 383-385 records that this "is an injection"), but a MAP with no entry for the text quote is equally accepted. Overriding a single key of an inherited map REPLACES it (`lexical()` returns the first hit), so the natural "just change the backslash rule" registration drops the `'`->`''` rule. `identEscape` has no check at all (e.g. `identEscape: '"'` makes `a"b` end the identifier).
- Repro (js7/esc.mjs): `map.defineDialect('my-nobs',{extends:'mariadb',lexical:{textEscape:{'\\':'\\\\'}}})`; translate `T .> FILTER(_["s"] $== "x' OR '1'='1")` gives `... = CAST('x' OR '1'='1' AS CHAR) COLLATE ...` (params mode is safe; inline mode and `LIKE`/CASE literals are not).
- Fix sketch: after resolving the effective lexical set (in `defineDialect` and when a runtime `lexical` is read), require that `textLiteral` of the quote character round-trips to a string that cannot terminate the literal, and that `identEscape` contains `identQuote` twice or is a recognised escape.
- Conformance gap: none.

### JS-C26 [medium] [confirmed] SQL stage-1 inlining captures a definition's free names into aggregate binders
Slice: js-6 C2. Python identical.
- Where: `normalise.mjs:143-208` (`substitute`), with `translator.mjs:1198`.
- What: `defs.get(name)` is spliced in unchanged. If the definition's right-hand side mentions a name later used as an aggregate binder at the use site, the translator resolves it to the binder. Spec §5.7 and docs/internals/sql-translation.md §5.4 step 4 ("Capture happens at the assignment, not at the use") say the opposite. `normalise` protects only the definition name (`bound.includes`), not the free variables of the inlined value.
- Repro: `X2 = A; ALL((5,6), A, X2 > 0)` with A a column. SEL with A=-1: FALSE. Translation: `((5 > 0) AND (6 > 0))`.
- Fix sketch: alpha-rename binders that clash with free names of any definition reaching their scope, or refuse with E_SQL_ASSIGN in that case. With JS-C7 fixed, the snapshot idea covers this if definitions are wrapped as closures.
- Conformance gap: none. Case `norm.inline.capture-not-by-binder`.

### JS-C27 [medium] [confirmed] SQL `unify()` promotes an undeclared (UNKNOWN) column to a certain kind, bypassing the kind warrant
Slice: js-6 C4. Python identical.
- Where: `translator.mjs:3145-3158` (`unify`), used at 1188 (IF/COND) and by `retKind` (3121-3129) for `@unify:` entries (`??`, `COALESCE`, MIN/MAX-style).
- What: `unify([UNKNOWN, BOOL])` is BOOL and `unify([UNKNOWN, NUM])` is NUM, and the unified kind is then treated as declared. That defeats docs/internals/sql-kinds.md §4-5 (an undeclared column is refused in boolean position and guarded in numeric position). The doc calls that false-positive path the worse cell (an undeclared column holding 1 matches on MariaDB; SEL raises E_NOT_BOOL). Reasoned from emitted text; no server run.
- Repro (mariadb, F = `Binding.column('f','t')` undeclared, A likewise): `F AND TRUE` gives E_SQL_SHAPE (correct), but `IF(TRUE, F, TRUE) AND TRUE` and `(F ?? TRUE) AND TRUE` translate to `(CASE WHEN TRUE THEN f ELSE TRUE END AND TRUE)` / `(COALESCE(f, TRUE) AND TRUE)`. SEL: `F = 1; IF(TRUE, F, TRUE) AND TRUE` is E_NOT_BOOL. `A + 1 > 0` is guarded, but `IF(N > 0, A, 0) + 1 > 0` and `(A ?? 0) + 1 > 0` are `CASE ... END + 1` unguarded. PostgreSQL turns the boolean case into a run-time type error, so the exposure is the MySQL family and SQLite.
- Fix sketch: `unify` returns UNKNOWN whenever any branch is UNKNOWN (certain only if all are certain); check the effect on the sqlt corpus.
- Conformance gap: none. Cases `warrant.bool.unknown-through-coalesce` and `warrant.numeric.unknown-through-if` in 19-kind-warrant.sqlt.

### JS-C28 [medium] [confirmed] A `Binding.raw` field is dropped by SELECT_COLS and by derived-table wrapping, giving SQL that names a nonexistent column
Slice: js-6 C5. Python identical.
- Where: `translator.mjs:2855-2856` (`const column = fSpec?.column ?? col`), `:2168-2170` (`wrapPlanAsDerivedTable`: `column: sourceField?.column ?? name`), `:2840-2863`.
- What: a raw field has `raw` but no `column`. `SELECT_COLS("total")` falls back to the rule's own text and quotes it, which is a different column (or none). After a wrapping step (`TAKE`, `SORT`, `MAP` before a FILTER) the derived table is `SELECT i.* ...`, which has no computed column, but the outer query refers to `_sub1`.`TOTAL`. The expression form correctly emits the raw SQL, so this is inconsistent, and the translator reports success for a statement that cannot run, violating whole-expression refusal.
- Repro (js6/m.mjs; relation items/i with `TOTAL: Binding.raw('i.price * i.qty','NUM')`): `ITEMS .> SELECT_COLS("total")` gives ``SELECT `i`.`total` FROM `items` `i` `` (raw expression ignored). `ITEMS .> SORT_BY(_["ID"]) .> TAKE(2) .> FILTER(_["TOTAL"] > 5)` gives `... FROM (SELECT `i`.* ... LIMIT 2) `_sub1` WHERE (... `_sub1`.`TOTAL` ...)`. No sql/cases fixture combines `raw` with a statement.
- Fix sketch: refuse (E_SQL_UNSUPPORTED) when a raw field is selected or would be read across a wrap, or project `<raw> AS <key>` in the inner select.
- Conformance gap: none. Cases `stmt.refusal.raw-field-select-cols` and `stmt.refusal.raw-field-across-derived-table`.

## Low

Entries in this tier are shorter; each keeps Where / What / Repro / Fix / Gap.

### JS-C29 [low] [confirmed] Public entry points leak host exceptions for non-string input
Slices: js-1 C5, js-3 C6.
- Where: `js/src/lexer.mjs:36-39`, `utf8.mjs:15-17`, `js/src/sel.mjs:140-142` (`compile`), `js/bin/sel.mjs:36-42`.
- What: `toCodePoints` uses only `str.length`/`charCodeAt`. `compile(12)` parses as an empty program and raises a misleading `E_SYNTAX unexpected end of input`; `compile(undefined)`/`compile(null)` throw a host `TypeError`; `compile(['1'])` and array-likes of numbers are accepted as source. `node js/bin/sel.mjs -e` with no expression dies with an uncaught `TypeError` stack trace. Spec §8 asks for E_BAD_ARG on malformed boundary calls.
- Repro: `node -e "import('/home/nathan/workspaces/nth/sel/js/src/sel.mjs').then(m=>{try{m.compile(12)}catch(e){console.log(e.code,e.message)}})"` prints `E_SYNTAX unexpected end of input`; `node js/bin/sel.mjs -e`.
- Fix sketch: `if (typeof source !== 'string') badArg(...)` in `compile`/`Lexer`; print usage in the CLI when `-e` has no operand.
- Conformance gap: none (host API probe).

### JS-C30 [low] [confirmed] Source with an unpaired surrogate raises E_UTF8 at position 0:0:0, not at the offending character
Slice: js-1 C4. php, cpp and go also print `line 0 column 0` for invalid source bytes, so this is a cross-host convention.
- Where: `lexer.mjs:39` (`toCodePoints(source, null)`), `errors.mjs:8-10` (`pos ? ... : 0`).
- What: SPEC §2 says invalid UTF-8 in source is E_UTF8 "at the offending byte"; errors.md says every error carries line/col/offset.
- Repro: `compile('1 +\n \ud800')` throws E_UTF8 with line 0, col 0, offset 0. Expected line 2, col 2, offset 5 (code point index).
- Fix sketch: compute the position in the lexer path; needs a spec decision on byte versus code point offset first.
- Conformance gap: none; a `.selt` cannot carry invalid bytes, so an API-level case.

### JS-C31 [low] [confirmed] Pipeline call arguments cost one nesting level, not two; multi-placeholder pipes diverge in Go
Slice: js-1 C6.
- Where: `parser.mjs:303-339` (`parsePipeStep` calls `parseSequence` outside any `parsePrimary`), `:326-333` (placeholder replacement loop).
- What: (a) spec §6.4 says a call's parentheses cost two levels; a `.>` step with parentheses costs one, so `1 .> ABS(1 .> ABS(...` nests about 200 deep before E_DEPTH versus 100 for `ABS(ABS(...`. All hosts agree (E_DEPTH at column 1792 for n=200 and 201 in js/php/cpp/go; Python raises RecursionError at n=200), so it is a spec gap. (b) `N=0; (N+=1) .> MAX(_, _); N` gives 2 in js/php/cpp/py (every top-level `_` replaced, same `left` node evaluated twice) but Go prints `E_UNDEF_VAR ... undefined variable _`. The spec says "one of the top-level arguments is `_`".
- Repro: (b) above; (a) `python3 -c "print('1 .> ABS('*200+'1'+')'*200)"` piped into `-e` on each host.
- Fix sketch: decide in the spec whether the pipe step costs two levels and whether multiple `_` all bind (and whether the operand is evaluated once or once per `_`); then add cases.
- Conformance gap: `pipe.placeholder.first`/`.second` test single placeholders only. No multi-`_` or pipe-depth case.

### JS-C32 [low] [confirmed] Error catalogue phases are wrong for two codes the front end raises at compile time
Slice: js-1 C7.
- Where: `spec/errors.md` compile-time table and `spec/limits.json` (rendered into `_limits.mjs:22-35`).
- What: `E_RANGE` (lexer for `\u{110000}`/`\u{D800}`, parser via `D.parse` for a numeric literal past the digit caps) and `E_UTF8` (source validation) are listed as `run` only, but JS raises both while compiling. Only `tools/check-error-codes.sh` consumes the phase; documentation/generator-data problem only.
- Repro: `compile('"\\u{110000}"')` throws E_RANGE with no evaluation.
- Fix sketch: mark them `both` in `limits.json` and add rows to the compile-time table.
- Conformance gap: n/a.

### JS-C33 [low] [confirmed] `Value.num(decimal)` keeps the caller's object, so mutating it afterwards changes the Value
Slice: js-2 C3.
- Where: `value.mjs:47-54` returns `d` itself; `Value.num` stores it in `_decimal` (287-291).
- What: spec §8 "The boundary copies": changing the host's data afterwards never changes a Value.
- Repro: `const d={neg:false,digits:5n,scale:0}; const v=Value.num(d); d.digits=99999n; d.scale=2; v.scalar` gives `999.99` (expected `5`).
- Fix sketch: `return { neg: d.neg, digits: d.digits, scale: d.scale }`; internal callers could use an unchecked `Value.numOwned` (JS-P25).
- Conformance gap: none.

### JS-C34 [low] [confirmed] `fromNative` silently turns unsupported objects into NULL/records instead of E_BAD_ARG
Slice: js-2 C5.
- Where: `value.mjs:627-630` (`typeof x === 'object'` catch-all using `Object.keys`).
- What: spec §8: a native value with no conversion raises E_BAD_ARG. `Date`, `Map`, `Set`, `ArrayBuffer` have no own enumerable keys, so become the empty record (NULL). Typed arrays other than `Uint8Array` become records keyed "0","1",...
- Repro: `Value.fromNative(new Date()).dump()` gives `-`; `Value.fromNative(new Map([[1,2]])).dump()` gives `-`; `Value.fromNative(new Int8Array([1,2])).dump()` gives a record `-{"0"=t"1", "1"=t"2"}`.
- Fix sketch: accept only `Object.getPrototypeOf(x)` of `Object.prototype` or `null` (maybe class instances); everything else `badArg`; reject `ArrayBuffer.isView(x)` other than Uint8Array.
- Conformance gap: none.

### JS-C35 [low] [confirmed] JS `fromNative` accepts fractional numbers; PHP and Python refuse floats
Slice: js-2 C6.
- Where: `value.mjs:616-619, 710-715` (`nativeNumberToDecimal`).
- What: spec §8 lists "a float" as not taken (E_BAD_ARG); docs/usage/README.md:189 says the dynamic hosts refuse one. JS turns any double with a shortest round-trip form into a decimal, so `0.1 + 0.2` becomes `0.30000000000000004`. A comment at 706-709 argues this is intended, but nothing in spec/docs records JS as the exception. Integer-valued doubles must be accepted (JS cannot tell `3` from `3.0`). Numbers whose `String()` has an exponent are correctly refused.
- Repro: `Value.fromNative(0.1+0.2).scalar` gives `0.30000000000000004` (JS); PHP `Value::fromNative(0.5)` and Python `Value.from_native(0.5)` give E_BAD_ARG.
- Fix sketch: refuse non-integers in JS (parity, breaking) or record the JS exception in spec §8 and the docs. Needs a decision.
- Conformance gap: tools/api probes do not cover floats; add one on all hosts.

### JS-C36 [low] [confirmed] `toNative` then `fromNative` is not an inverse for records whose keys look like array indices
Slice: js-2 C7.
- Where: `value.mjs:634-656` (`toNativeAt` builds a plain object).
- What: spec §8 says `fromNative(toNative(v))` dumps exactly as `v`; key order is normative (§3.3). A JS object enumerates integer-like keys first in ascending order. PHP has a stated exception in §8; JS has none.
- Repro: `v = RECORD("b", 1, "2", 2, "a", 3)`; `v.dump()` is `-{"b"=t"1", "2"=t"2", "a"=t"3"}`, `Value.fromNative(v.toNative()).dump()` is `-{"2"=t"2", "b"=t"1", "a"=t"3"}`.
- Fix sketch: unfixable with plain objects; document the exception like PHP's, or offer an ordered `toNative` (entries array / Map).
- Conformance gap: none.

### JS-C37 [low] [confirmed] `Value.scalar` setter leaves a stale cached decimal
Slice: js-2 C8.
- Where: `value.mjs:148-150` (setter), `:138` (`_decimal` cache); `scalar` is public (`sel.d.ts:43`).
- What: setting it on a numeric Value keeps `_decimal`, so `ISNUM`/`asDecimal` and `eqlAt` use the old number. No in-tree code assigns `.scalar`.
- Repro: `w = Value.num('12'); w.scalar = 'abc'; w.looksNumeric()` gives `true` (expected false).
- Fix sketch: setter also does `this._decimal = null`.
- Conformance gap: none.

### JS-C38 [low] [unconfirmed as a defect; latent] `_MAX_INT_BITS = 3321929` is a hand-typed copy of a generated limit
Slice: js-2 C9.
- Where: `js/src/decimal.mjs:26-27`.
- What: the `guard()` prefilter `(digits >> 3321928) !== 0n` is sound only for MAX_INT_DIGITS = 1,000,000. `MAX_INT_DIGITS` is generated (`_limits.mjs`), this constant does not follow it. If the limit changes and is regenerated, a value could pass the prefilter and skip the exact test. The current constant was verified correct at the boundary; nothing ties the two.
- Fix sketch: derive the shift from the limit at load time with integer arithmetic (a rational bound; `10n ** BigInt(MAX_INT_DIGITS)` costs 87 ms at 1e6), or add a one-line gate assertion.
- Conformance gap: boundary cases exist in 10-limits.selt; nothing ties the constant.

### JS-C39 [low] [confirmed] `optimizeAstLogical` fuses two FILTERs into a deeper predicate and can raise E_DEPTH for a program the evaluator accepts
Slice: js-3 C4.
- Where: `optimizer.mjs:444-463` (fusion wraps both predicates in an extra `AND`, +1 level), with `optimizeRoot`'s check only on the unrewritten tree (585-587).
- What: spec §6.4: "An optimiser must neither raise E_DEPTH itself ... nor make it disappear." On the logical path `cannotRaise` admits comparison/AND/OR chains over binder fields, so a 197-conjunct predicate qualifies. The physical path is unaffected.
- Repro (js3/depth.mjs): `FILTER(FILTER(T, _["a"] > 0), <197 terms of _["a"] == 1 joined by AND>)`: plain is ok; `optimizeAstLogical(...)` output evaluated gives `E_DEPTH@1:31`. 198 terms: both E_DEPTH.
- Fix sketch: skip the fusion when the fused predicate's depth would exceed the cap (reuse `exceedsDepth` on the candidate), or run the `exceedsDepth` guard on the optimiser output.
- Conformance gap: none (10-limits pins folding only). An sqlt/hybrid case at the boundary.

### JS-C40 [low] [confirmed] `Program.dependencies()` is order-insensitive and drops variables read before, or only conditionally after, an assignment
Slice: js-3 C5. php, python and cpp print the same answers, so shared design that contradicts the spec sentence.
- Where: `js/src/sel.mjs:50-55` (`reads` minus `assigned`, whole-program sets), comment at :47-49; spec §8 line 1088.
- What: spec §8 says `dependencies()` returns "every variable the program reads"; the code comment says "reads without having assigned it first"; the implementation subtracts every name assigned anywhere. A frontend using it to decide which inputs re-trigger a rule misses those.
- Repro: `node js/bin/sel.mjs --deps -e 'A + 1; A = 2'` prints empty (expected A). `--deps -e 'IF(X, A = 1, 0); A'` prints X only.
- Fix sketch: track definite assignment in source order (an assignment counts only if it dominates the read); or correct the spec sentence and document "may under-report".
- Conformance gap: none (only tools/check-api.sh probes it). API probes for both programs.

### JS-C41 [low] [confirmed] Nothing bounds the size of a value, the work of `,`, or the size of a join result
Slices: js-3 C7, js-5 C12. All hosts share this; spec §6.4 caps depth and digits only.
- Where: `eval.mjs:271-282` (`evalList`); `structure.mjs:1127-1151` (per-pair path pushes every joined row into `output`; also equi with one shared key).
- What: `A=(1,2); A=(A,A); ...` 30 times doubles A each time; with a 500 MB heap the process aborts, with the default heap it takes minutes and several GB. `LINK(A, A, TRUE)` over two 3,000-row lists is 9 M joined rows; under `--max-old-space-size=256` it dies with a V8 fatal OOM in about 3.5 s; at the default heap ~30,000 x 30,000 rows.
- Repro: `S='A=(1,2);'$(printf 'A=(A,A);%.0s' $(seq 30))'COUNT(A)'; node --max-old-space-size=500 js/bin/sel.mjs -e "$S"` (256-byte program, `FATAL ERROR: heap out of memory`, rc 134); js-5 `oom.mjs` for the join.
- Fix sketch: a spec decision: an element-count cap for `evalList`/`clone` and a join-result cap (E_RANGE), or document that hosts must run untrusted rules under a resource limit.
- Conformance gap: n/a.

### JS-C42 [low] [confirmed] The public `register`/`registerBuiltin` can replace a builtin, and the optimiser/math plan ignores the replacement
Slice: js-3 C8.
- Where: `registry.mjs:90-100` (no builtin/reserved protection, no manifest reconcile), `math_plan.mjs:43,134` and `optimizer.mjs:119` (classify by `node.name`, not `node.spec`), typed in `sel.d.ts:152-160`.
- What: `registerFunction` refuses builtin names (§8.1), but the exported `register` overwrites `ABS`; the math plan still compiles `ABS(...)` to the ABS opcode, so the override runs in some contexts and not others.
- Repro: `register("ABS",1,1,()=>Value.text("overridden")); compile('ABS("x")').run({})` gives `E_NOT_NUM` from the plan instead of "overridden".
- Fix sketch: stop exporting `register`/`registerBuiltin` from the package entry, or make it refuse names in `BUILTIN_MANIFEST` unless called from `define`.
- Conformance gap: none (api probe).

### JS-C43 [low] [confirmed] `\d \w \s` expansion inside a regex class turns an adjacent `-` into a range (hosts agree; the result is neither engine's meaning)
Slice: js-4 C7.
- Where: `regex.mjs:29` (EXPAND_INSIDE), `:182`.
- What: `[\s-z]` is rewritten to `[ \t\n\r\f\x0b-z]`, so the hyphen forms the range `\x0b`-`z`. ECMAScript in `u` mode rejects `[\s-z]`; PCRE reads `-` as a literal. The whitelist should reject the construct; instead the rewrite hides it and all five hosts agree on the wrong meaning.
- Repro: `RMATCH('[\s-z]', 'a')` gives TRUE in all five hosts (PCRE semantics: FALSE). `RMATCH('[+-\d]', '5')` gives FALSE and `RMATCH('[+-\d]', ',')` gives TRUE. `RMATCH('[\w-.]', 'a')` gives `E_REGEX_SYNTAX ... Range out of order` with the rewritten pattern in the message.
- Fix sketch: in `validateClass` reject a `-` directly before or after a `\d \w \s` escape ("write the hyphen first or last") and say so in §7.8.
- Conformance gap: none. Case `re.class.escape-adjacent-hyphen`.

### JS-C44 [low] [confirmed] Regex flag letters are matched case-insensitively using the host's `toLowerCase`
Slice: js-4 C8.
- Where: `regex.mjs:206-208`.
- What: spec §7.8: "Flags: `i` and nothing else". `I` is accepted as `i`. No other code point lower-cases to `i`, `m` or `s` (U+0130, U+212A, U+017F checked); every host does the same, so no divergence, but it breaches the "no host case mapping" corollary.
- Repro: `RMATCH('a', 'A', 'I')` gives TRUE in all hosts.
- Fix sketch: compare `ch === 'i'` exactly; decide in the spec whether `I` is allowed; change all hosts together.
- Conformance gap: `re.flag.unknown` exists; add `re.flag.uppercase-I`.

### JS-C45 [low] [confirmed] The regex validator does not reject quantified anchors (`^*`, `$+`, `^{2}`); JS relies on V8, Lisp accepts them
Slice: js-4 C9.
- Where: `regex.mjs:99-110`.
- What: V8 (`u` mode), PCRE, Python and SRELL reject a quantified anchor; cl-ppcre accepts. JS is correct only because V8 refuses.
- Repro: `RMATCH('^*', 'aaa')` gives `E_REGEX_SYNTAX` in js/php/python/cpp and TRUE in lisp; `$+` and `^{2}` split the same way.
- Fix sketch: reject a quantifier directly after `^`, `$`, `(` or `|` in `validate`, in all hosts.
- Conformance gap: none. Case `re.quant.on-anchor`.

### JS-C46 [low] [confirmed, consistent across hosts] `LTB` of an empty list fails, so `LTB(BTL(x))` does not round-trip for empty input
Slice: js-4 C10.
- Where: `binary.mjs:120-121` (`v.size() > 0 ? v.values() : [v]`).
- What: an empty list has no scalar and no children, so `asDecimal` raises `E_NO_SCALAR`. Spec §7.7 lists only `E_RANGE`. `FROM_HEX("")` and `DECODE_BASE64("")` do produce an empty BIN, so the language can make but not rebuild it. Probably a spec omission.
- Repro: `BLEN(LTB(BTL("")))` gives `E_NO_SCALAR at line 1 column 10: value has no scalar and no children` (js, php, python, cpp, lisp).
- Fix sketch: decide in the spec (empty list gives empty BIN is the natural reading) and change all hosts.
- Conformance gap: none. Case `bin.ltb.empty`.

### JS-C47 [low] [confirmed] Behaviour of `PATH(x, "")` is unspecified and untested
Slice: js-4 C11.
- Where: `null.mjs:42` (`if (pathStr === '') return target;`).
- What: the empty path returns the target itself, not the value under key `""`. All hosts agree (`PATH(RECORD("", 5), "")` returns the record), but §7.9 ("walks dot-separated child keys") could equally read it as one segment `""`. Keys containing `.` cannot be addressed (`PATH(RECORD("a.b",5), "a.b")` is NULL).
- Fix sketch: pin the decision in the spec.
- Conformance gap: none. Case `null.path.empty-path`.

### JS-C48 [low] [confirmed] SORT clones its elements but TOP/TAKE do not, so the SORT+TAKE-to-TOP fusion changes an observable result in JS (and C++)
Slice: js-5 C7.
- Where: `aggregate.mjs:371` (`indexed.map((x) => x.item.clone())`) versus `:476` (`heap.map((entry) => entry.item)`).
- What: the physical tree must not change meaning (contributing.md traps). SORT/SORT_BY return deep copies, TOP/TAKE return the elements themselves, and the optimiser turns `TAKE(SORT(A),1)` into TOP, so a pending read through the result sees a later mutation only when the fusion happened. php/python/lisp/go alias everywhere, so are self-consistent; C++ has the same JS split. `LIST(…)` copies in JS (`structure.mjs:62`) though §3.4 lists only `,`, assignment and the aggregates as copying.
- Repro: `A = LIST(LIST("a")); TAKE(SORT(A),1)["1"][(A["1"]["1"]="z"; "1")]` gives `z` in all hosts (fused). `... TAKE(IF(TRUE, SORT(A), 0),1)["1"][...]` (not fusable) gives `a` in js and cpp, `z` in php/python/lisp/go. `LIST(A)["1"]["1"][...]`: js/lisp/cpp clone (`a`), php/python alias (`z`).
- Fix sketch: decide in the spec whether SORT and LIST copy. Aliasing (drop the clone) is the majority behaviour and removes the cost in JS-P13.
- Conformance gap: none.

### JS-C49 [low] [confirmed] BUCKET over a scalar returns an empty list instead of the one-element list §7.3 prescribes
Slice: js-5 C8. js/php/lisp/python/go agree; only C++ follows the spec.
- Where: `aggregate.mjs:520` (`if (value.isNull() || value.size() === 0) return Value.list([])`).
- What: §7.3 says an aggregate whose first argument has no children but a scalar treats it as a one-element list; MAP/SUM/ALL do (via `walk`), BUCKET does not.
- Repro: `BUCKET("abc", _)` gives `-` in js/php/lisp/python/go; cpp gives `-{"abc"=-{"1"=t"abc"}}`. `COUNT(BUCKET("abc", _, _))` gives 0 versus cpp 1.
- Fix sketch: return early only for `kind === NONE && size() === 0` (as `doTop` does); needs the suite to fix the rule first (five hosts currently agree on the other answer).
- Conformance gap: none. Case `BUCKET("abc", _)`.

### JS-C50 [low] [confirmed, all hosts] Bare `BUCKET(list, key)` silently drops a whole group when two distinct group keys have the same index-key text
Slice: js-5 C9.
- Where: `aggregate.mjs:550-561` (group identity = EQL of the whole key) versus `:575` (`out.set(group.keyString, …)` overwrites).
- What: §7.3 says the two-argument spelling groups by the key's scalar verbatim and that `.> MAP(proj)` over it equals the 3-argument spelling. A TEXT value that also carries children (`A="x"; A[1]=2`) is not EQL to plain "x" so forms a second group with keyString "x", which replaces the first group's entry.
- Repro: `A="x"; A[1]=2; L=LIST("x",A); BUCKET(L,_) .> INDEXES .> JOIN(",")` gives `x` (one key), `BUCKET(L,_) .> MAP(COUNT(_))` gives `1`, while `BUCKET(L,_,COUNT(_))` gives `1,1`. Every host prints the same.
- Fix sketch: in the bare spelling identify groups by keyString (the scalar), not by structural EQL.
- Conformance gap: none. Add the repro to 16-bucket.selt.

### JS-C51 [low] [confirmed, all hosts] Same-name binders (`LINK(T, T, T["id"] == T["mgr"])`) evaluate differently on the equi and the per-pair path
Slice: js-5 C10. A spec-level ambiguity that optimisation makes visible.
- Where: `structure.mjs:174-185` (`tryExtractEquiKeys`: both name sets contain the shared relation name, so both orientations match) and `:1127-1151` (per-pair path binds `T` to the right row last).
- Repro: `T = LIST(RECORD("id",1,"mgr",2), RECORD("id",2,"mgr",1), RECORD("id",3,"mgr",9)); LINK(T,T,T["id"]==T["mgr"]) .> COUNT` gives 2 on all hosts; the same with `AND TRUE` gives 0 on all hosts.
- Fix sketch: pin it in §7.4 (with equal left and right binder names only `_1`/`_2` are unambiguous; refuse equi extraction when the names collide, or bind both on the per-pair path); add cases.
- Conformance gap: none.

### JS-C52 [low] [confirmed, all hosts] Spec signature `BUCKET(list, binder, key)` is declared in spec/builtins.json (form 2) but not implemented
Slice: js-5 C11.
- Where: `aggregate.mjs:526-535` (`doBucket` picks by count only).
- Repro: `BUCKET(LIST(RECORD("cat","A")), R, R["cat"])` gives `E_UNDEF_VAR ... R` on every host (the manifest says R is a binder).
- Fix sketch: drop the form from the manifest/§7.3, or dispatch on `args.isSymbol(1)` like `doSort` does (the spec must resolve the `BUCKET(L, KEY, COUNT(_))` ambiguity where KEY is a variable).
- Conformance gap: none.

### JS-C53 [low] [confirmed] SQL: a binder that reuses the name of a scalar `value` binding is treated as that constant
Slice: js-6 C6. Python identical.
- Where: `translator.mjs:247-254` (`node`: `constants.isConstant(n, this.constNames)`), `:1923-1933` (`guardNumeric`), `:1977-1981`; `constants.mjs` `isConstant` (`var` gives `b.has(name)`).
- What: `constNames` is built from bindings only; `this.frames` binders are not excluded. Effects: the numeric guard is skipped for the element (kind-warrant hole), and `validate()` evaluates the binding's value instead of the element, giving false E_SQL_INVALID refusals.
- Repro: bindings `V = Value.text('5')`, `A` undeclared column. `ALL((A, A), V, V + 1 > 0)` gives `((a + 1) > 0) AND ((a + 1) > 0)` unguarded, while binder `I` gets the CASE/REGEXP guard. With `V = "abc"`, `ALL((A, 2), V, V + 1 > 0)` gives E_SQL_INVALID (E_NOT_NUM); SEL accepts it. Relation form: `ALL(R, V, V["QTY"] + 1 > 0)` gives E_SQL_INVALID (E_NO_KEY).
- Fix sketch: `isConstant` must be told the names currently bound by translator frames (treat any var found by `this.binder(name)` as non-constant).
- Conformance gap: none. Case `const.binder-shadows-value-binding`.

### JS-C54 [low] [confirmed] SQL layer: non-SqlError host exceptions escape `tryTranslate` / `planHybrid`, and definition chains are refused with a wrong message
Slices: js-6 C7, C8; js-7 C5.
- Where: (a) `constants.mjs` `isConstant` recursion (called from `normalise.mjs:90`); (b) `translator.mjs:1483` (`valueNode`: `v.asText(pos)` on a NONE element), `:297-316` / `emit.mjs:80-86`; (c) `hybrid.mjs:91-118` (`sourceTables`), `:174` (`containsUnsupportedSql`), `:202` (`collectFieldReferences`), `:242` (`readsWholeRow`), `:465` (`inlineLiterals`), `:524` (`readNames`); (d) `normalise.mjs:53-100`, `:90`.
- What: (a) inlining makes the tree as deep as the number of definitions and stage 1 walks it recursively before the depth guard can act. (b) A list `value` binding with a null element raises a SelError from `asText`. (c) A flat 20,000-step chain parses, `translate` answers E_SQL_DEPTH and `run` E_DEPTH, but `planHybrid` falls back to `pureMemoryPlan` then `sourceTables` and overflows the stack (spec §6.4; `dependencies()` was fixed for exactly this). (d) The E_DEPTH accounting inlines a chain of assignments into one deep tree, so the refusal message claims the evaluator raises E_DEPTH; it does not (each RHS is depth 2 in SEL). Safe direction.
- Repro: (a) `X0 = N; X1 = X0 + 1; ...; X20000 > 0` with N a column: `RangeError: Maximum call stack size exceeded` thrown by `Sql.translate` (js-6 `u.mjs`, 20000 and 100000 definitions; 3000 gives E_SQL_DEPTH; SEL evaluates 3000 defs fine). (b) `Sql.tryTranslate(compile('COUNT(V)'), 'mariadb', {V: Binding.value(Value.list([Value.num('1'), Value.null()]))})` throws `SelError E_NULL` (also ANY/JOIN over V); `V[2] == 1` on the same binding translates, then `asValue('inline')` throws SqlError E_SQL_BINDING after `tryTranslate` returned a Fragment. (c) js-7 `deep.mjs`: `ORDERS` + `' .> TAKE(1)'` x 20000 gives `RangeError` at hybrid.mjs:95; x250..5000 gives `pure_memory` (fine); x150 gives pure_sql. (d) 250 definitions over a constant give E_SQL_INVALID "SEL rejects this expression (E_DEPTH: ...)"; over a column, E_SQL_DEPTH. SEL prints TRUE for 3000.
- Fix sketch: charge each definition's depth at its use in `substitute` (store `depth(def)` next to it) and refuse before the tree exists; reject NONE children in `Binding.value` (or in `valueNode`) with E_SQL_BINDING; pass a depth to the hybrid walks and raise at MAX_DEPTH, or catch in `pureMemoryPlan`; fix the wording or document the translator limit.
- Conformance gap: none. A plan case with a >MAX_DEPTH chain expecting the same code in every host.

### JS-C55 [low] [confirmed] SQL LIMIT/OFFSET/TAKE/DROP counts go through a JS `Number`: exponent notation and rounding in the SQL text
Slice: js-6 C9. Python keeps the exact integer; JS/PHP-style host divergence.
- Where: `translator.mjs:2670` (`return Number(d.digits)`), `:2535`, `:2545-2551`, `:2991-3003` (template literals).
- Repro (js-6 `d.mjs`): `ITEMS .> TAKE(99999999999999999999999)` gives `LIMIT 1e+23` (Python: `LIMIT 99999999999999999999999`); `TAKE(9007199254740993)` gives `LIMIT 9007199254740992`; `DROP(99999999999999999999999)` gives `OFFSET 1e+23`.
- Fix sketch: keep `d.digits` as BigInt/string for the count and use it in arithmetic only under `MAX_SAFE_INTEGER`, or refuse counts above a fixed cap in all hosts.
- Conformance gap: 28-slice-overflow.sqlt covers only the DROP sum. Case `stmt.take.over-2^53`.

### JS-C56 [low] [confirmed] SQL TAKE/DROP counts with a written scale are refused although SEL accepts them
Slice: js-6 C10.
- Where: `translator.mjs:2664-2666` (`d.scale !== 0`).
- Repro: `ITEMS .> TAKE(2.0)` gives E_NOT_INT; `node js/bin/sel.mjs -e '(1,2,3) .> TAKE(2.0)'` gives `{"1"=t"1", "2"=t"2"}`. Also `1.0 * 2` and a `value` binding "2.0". Safe direction, wrong code.
- Fix sketch: strip trailing fractional zeros (or reuse the evaluator's `nonNegInt`) before testing integrality.
- Conformance gap: `stmt.refusal.take-float` pins only 1.5. Add `stmt.take.whole-number-with-scale`.

### JS-C57 [low] [confirmed] SQL: rule-supplied identifiers are not validated like binding-supplied ones; SELECT_COLS bypasses the field allow-list on a relation with no declared fields
Slice: js-6 C11. Quoting is correct everywhere reviewed, so this is not an injection.
- Where: `translator.mjs:2832` (`AS ident(proj.alias)`), `:2451-2468`, `:2840-2856`; `binding.mjs:213` (`checkName` applies to bindings only).
- What: `RECORD("", ...)` on PostgreSQL emits `AS ""` (rejected by the server) and `RECORD("a\u{0}b", ...)` emits a NUL in an identifier, both cases `checkName` prevents for bindings. `SELECT_COLS("password")` on a relation declared without fields emits `i`.`password` from the rule text while `_["password"]` on the same relation is refused ("it declares none").
- Repro (js-6 `cc.mjs`): ``SELECT `i`.`password`, `i`.`x``; DROP TABLE t; --` FROM `items` `i` `` (quoted, inert); ``SELECT 1 AS `a``b^@` FROM `items` `i` `` (NUL); `ITEMS .> FILTER(_["password"] $== "x")` gives E_SQL_BINDING.
- Fix sketch: run rule-supplied names through the same empty/NUL check (E_SQL_SHAPE); decide whether an empty `fields` map means "anything" or "nothing", consistently for `_[...]` too.
- Conformance gap: none.

### JS-C58 [low] [confirmed] SQL: an assigned keyed list cannot be indexed directly (missing feature, safe direction)
Slice: js-6 C12.
- Where: `translator.mjs:427-431` (`index`), cf. `childOf` at :3074.
- Repro: `R[1] = N; R[2] = 2; R[1] + R[2] > 0` is refused ("only a bound name can be indexed"), while `SUM(R, I, I)` translates.
- Fix sketch: in `index()`, when `obj.t` is `clist`/`list`, resolve with `childOf` and render the child.
- Conformance gap: none.

### JS-C59 [low] [confirmed, SQLite only] Pure-SQL bucket drops an explicit SORT_BY that fixes the group order
Slice: js-7 C6 (belongs to the translator; planner classifies it `pure_sql`, hybrid.mjs:677-682).
- What: SEL's BUCKET yields groups in first-appearance order of its input; after an explicit SORT_BY that order is defined. The statement sorts in a derived table and GROUPs outside it with no ORDER BY, so group order is the database's. Without a SORT_BY the order is documented "undefined"; this is only the explicit-sort case. PG/MariaDB hash aggregation is free to reorder too (reasoned).
- Repro: `ORDERS .> SORT_BY(_["customer_id"], "DESC") .> BUCKET(_["customer_id"], RECORD("c", _K, "n", COUNT(_)))` on SQLite: `run()` gives c = 12, 11, 10; `executeHybrid` (pure_sql) gives 10, 11, 12. Statement: `SELECT MIN(customer_id) AS c, COUNT(*) AS n FROM (SELECT o.* FROM orders o ORDER BY o.customer_id DESC) _sub1 GROUP BY CAST(_sub1.customer_id AS TEXT) COLLATE BINARY`.
- Fix sketch: order groups by MIN of a row sequence derived from the sort (as the latest-member strategy does) or classify sort-then-bucket as not pushable.
- Conformance gap: none.

### JS-C60 [low] [confirmed] `executeHybrid` treats the context differently for hybrid versus pure-memory plans
Slice: js-7 C7.
- Where: `hybrid.mjs:723` versus `:730`.
- What: `Program.run` documents "the context is mutated in place by any assignments"; the pure-memory branch passes the caller's Value through, the hybrid branch `clone()`s it, so a hybrid plan silently stops mutating the caller's context (and pays a deep copy, JS-P16). A falsy non-Value context becomes `{}` (consistent with `run()`); `[1,2]` gets `_INPUT` set on a list.
- Repro: js7/c.mjs (13 context shapes; no crashes, only the mutation/clone difference).
- Fix sketch: document, or build a shallow root (top-level entries aliased) and copy only what the continuation assigns.
- Conformance gap: none.

### Noted, not counted as findings
- js-3 unexplored hunch, no repro: `ctx.joinPrefilterReport` is left set when an error propagates out of a LINK's right-hand source and is swallowed by `??`; a later FILTER(LINK) could read it.
- js-4 cross-host note (PHP, not JS): `LEFT('abc', <400-digit number>)` prints an empty result (others: `abc`); `FIND('c','abc', <400-digit number>)` raises `E_RANGE` (others: `0`).
- js-4: spec §7.8's prose says RGROUPS with no match returns "empty list"; conformance `re.groups.no-match` pins `none` (a NULL-kind value). A prose/case mismatch for the spec owner.
- js-6: bare aggregate body over an undeclared column (`SUM((A,B), I, I)` gives `(a + b)`) is a hole `docs/internals/sql-kinds.md` §4.1 already records ("notaddressed").
- js-7: `containsUnsupportedSql` treats ALL/ANY/HAS/INDEXES as unsupported, so a MAP pair using them is evaluated locally when it could be pushed (missed pushdown, never wrong).

---

# Performance findings

Ordered by impact. "Likely matters" is the reviewers' own assessment.

### JS-P1 [high] [measured] `Value.text` scans every result string for surrogates, which flattens V8 ropes: repeated `&`/`&=` is ~50x slower than necessary and superlinear
Slice: js-2 P1.
- Where: `value.mjs:14-18` (`checkText`), `:186-190` (`Value.text`); called from `eval.mjs:375` (`concat`), plus 16 more sites in `builtins/text.mjs` and aggregate/regex/binary/control.
- Why it is slow: `concat` does `Value.text(lv.scalar + rv.scalar)`. JS builds a cons-string in O(1), but `ANY_SURROGATE.test(s)` forces V8 to flatten it, copying the whole accumulated string every time. Both operands are already-validated TEXT, so the concatenation cannot contain an unpaired surrogate; the same holds for results of builtins that slice by code point. The check is needed only at the host boundary.
- Evidence (node v24.16): program `L = SPLIT(REPEAT("a,", n), ","); S = ""; ALL(L, IS_NULL(S &= "abcdefghij") OR TRUE)`: n=10000 116 ms; n=20000 1347 ms; n=40000 7951 ms (68x for 4x the work). With `Value.text` monkeypatched to `new Value(TEXT, s)`: 32 / 74 / 151 ms (52x faster at n=40000, linear). Pure micro: 40000 appends 7.7 s with the check versus 0.5 ms raw. A rule folding untrusted data into a string is a practical DoS.
- Fix sketch: internal `Value.textOwned(s)` (no check) for `concat` and builtins whose output derives from validated Values; keep `Value.text` checking for host input. Byte-identical.

### JS-P2 [high] [measured] The lexer builds a `fromCodePoints([c])` string per character and dominates compile time
Slice: js-1 P1.
- Where: `lexer.mjs:39` (`.map((c) => fromCodePoints([c]))`), `:47-54` (`posAt` binary search per token), `:77-110` (`{ ..., ...pos }` spread per token), `:114-124` (`matchOperator` scans the 35-entry OPERATORS list per operator character).
- Why: `fromCodePoints` allocates a slice and calls `String.fromCodePoint.apply` for every character, then the one-character strings are joined back with `slice().join()` per token. A CPU profile of `parse()` over a 2 MB, 20,000-line rule file shows lexRange, the `Lexer` constructor, `fromCodePoints`, GC and `slice` at ~85% of samples; the parser proper ~5%.
- Evidence: 2 MB source, 720k tokens, `tokenize` best of 7: 1,050 ms as is; about 600-660 ms with `String.fromCodePoint(c)`; 530-580 ms with a first-character operator table on top; 420-470 ms after also building token objects literally (no spread) and caching the last line index in `posAt`. Overall 2.3-2.5x (loaded box). The optimised copy (`scratchpad/js1/lx/lexer3.mjs`) produces byte-identical JSON token streams on a 2,000-line mixed sample. `parse(src)` in full is 1,790 ms, so lexing is ~60% of compile.
- Fix sketch, in order of value: (1) `String.fromCodePoint(c)` for the chars array (or keep numeric code points and slice the source for token values); (2) an `OPS_BY_FIRST[c]` table; (3) token literals `{ type, value, line, col, offset }` with incremental line/col instead of `posAt` plus spread.

### JS-P3 [high] [measured] Cliff for rows with more than ~254 fields: shape interning stops, so every join pair builds its own plan (36x slower, 240 MB extra)
Slice: js-5 P1.
- Where: `structure.mjs:264` (`keys.length <= 256 && … <= 16384` in `ensureRowTableAlias`), `value.mjs` `SHAPE_CACHE_MAX_KEYS = 256` / `SHAPE_CACHE_MAX_CHARS = 16384` (`recordShape`), consumed by `makeJoinProjector` (:503-533: `plans.get(left.shape)`) and `rowPlan`/`internRecordShape` (:397).
- Why: wide rows exceed the key cap, so each row gets its own non-interned shape; the projector's plan cache is keyed on shape identity, so every pair misses, compiles a fresh rowPlan (JSON.stringify of ~500 keys, Sets, Maps, typed arrays) and adds one more never-evicted entry to `plans`.
- Evidence (`bench5.mjs`, equi join 3,000 x 3,000 rows, C columns each): C=100 67 ms; C=250 90 ms; C=260 3311 ms (+243 MB heap); C=300 4442 ms; C=500 6034 ms. With both caps raised (keys 4096, chars 262144, patched copy): C=260 91 ms, C=300 75 ms, C=500 188 ms (36x). Falling back to `makeJoinedRow` for wide rows was not better (1.9 s at C=250).
- Fix sketch: keep bounding the number of cached shapes (256 entries) but not each shape's width (or bound by total characters at a much larger value); key the alias-plan cache on the shape in a WeakMap; stop `plans` growing per row when shapes are not interned. Shapes are internal, so byte-identical.

### JS-P4 [high as a DoS] [measured] SQL translator re-evaluates constant sub-trees at every nesting level (quadratic amplification, ~150x at 160 terms)
Slices: js-7 P1, js-6 P3 (and the exponential aspect in JS-C8).
- Where: `translator.mjs:243-256` (`node()`: `isConstant` then `dispatch` then `constants.validate`) calling `constants.mjs:169-241` (`isConstant`/`validate`/`requireNumeric`/`constantScale`); also `translator.mjs:1924, 1961-1979` (`guardNumeric`, `requireNumericConstant`, `coerceScaleLimits`).
- Why: every constant `bin`/`un`/`call` node is translated and then handed whole to `evalNode`, and each numeric operand is evaluated again by `requireNumeric`/`constantScale`. For a left-nested chain of constants that is O(depth x subtree cost). `planHybrid` runs the translator once per candidate prefix on top.
- Evidence: js-7 `cv.mjs`: `T .> FILTER(_["n"] > LEN(REPEAT("x",20000)) + ... k terms)`: k=10 translate 250 ms (one eval 24 ms); k=40 1368 ms (33 ms); k=80 4341 ms (68 ms); k=160 17.3 s (111 ms). Rule text ~5 KB. `isConstant` is 6% of self time on a 30-clause translate. js-6: a 190-term constant chain `1 + 1 + ... > 0` takes 11.2 ms per translate versus 2.1 ms over a column (50-run average), i.e. O(depth^2) evaluator runs, bounded by depth 200.
- Fix sketch: memoise per node (WeakMap node -> {isConstant, value-or-SelError}) for the duration of a translation, evaluate a maximal constant subtree once, and have children inherit; error position/code stay the innermost node's, so byte-identical.

### JS-P5 [medium] [measured] Every text builtin round-trips the whole string through a code point array
Slices: js-4 P1, js-2 N2.
- Where: `text.mjs:10` (`cps`), LEN, LEFT, RIGHT, SUBSTR, FIND, REPLACE, SPLIT, TRIM/LTRIM/RTRIM, UPPER/LOWER, BACKWARDS, pad, CODE (:21-176); `utf8.mjs:15-40` (`toCodePoints`/`fromCodePoints` with `cps.slice(i, i+4096)` + `apply`).
- Why: `toCodePoints` allocates a JS array of numbers for the full string and `fromCodePoints` slices and applies in 4096 chunks: O(n) work and allocation even when the result is O(1) (`LEFT(s, 5)`, `CODE(s)`, `TRIM` with no spaces), and 5-17x more than needed on short strings. Strings are well-formed (`Value.text` enforces it), and any string with no surrogates has code point index equal to UTF-16 index, so native `length`/`slice`/`indexOf` are exact.
- Evidence (node 24): 1M-character string, `LEFT(S, 5)` ~65 ms per call, `LEN` ~61 ms per call. On a 30-character row: TRIM 851 ns versus 51 ns with a `charCodeAt` scan and one `slice` (17x); LEN 244 ns versus 44 ns with a no-surrogate fast path (5.5x); UPPER 809 ns. A 300k-row pipeline with `TRIM(name)` spends ~1 us per call against 0.85 us per-row evaluator baseline. js-2 N2: 2500 iterations of `LEN(S &= "abcdefghij") > 0` over a growing S spent 545 of 750 ms in `toCodePoints`.
- Fix sketch: one helper `hasSurrogates(s)` (regex test, or tracked once per Value); when false use the native path (`LEN` to `s.length`, `LEFT/RIGHT/SUBSTR` to `slice`, `TRIM` via `charCodeAt` over the four ASCII whitespace units, `FIND` to `indexOf`). Keep the code point path for strings with surrogates. Byte-identical because SEL whitespace is ASCII.

### JS-P6 [medium] [measured] FIND, REPLACE and SPLIT are naive O(n*m) over number arrays
Slice: js-4 P2.
- Where: `text.mjs:12-19` (`indexOfCp`), used at :61, :75, :95; also feeds JS-C4.
- Why: the textbook double loop over arrays of numbers, restarted per match; REPLACE/SPLIT call it once per occurrence and rebuild `out` one element at a time (:77, :81).
- Evidence: `FIND(REPEAT("a",20000)&"b", REPEAT("a",40000))` takes 1.5 s; doubling both to 40k/80k takes 5.7 s (quadratic). Native `hay.indexOf(needle)` on the same inputs takes under 1 ms. A well-formed needle can only match at a code point boundary of a well-formed haystack.
- Fix sketch: `indexOf` on the UTF-16 strings and convert the resulting offset with a single pass (needed only for `FIND`); REPLACE and SPLIT work purely on UTF-16 with `slice`/`join`, or `split`/`replaceAll` with string arguments (literal in JS). Keep the array path only for the FIND `from` conversion.

### JS-P7 [medium] [measured] UTF-8, hex and base64 codecs go through intermediate arrays and per-byte strings
Slices: js-1 P2, P6; js-4 P3, P4.
- Where: `utf8.mjs:44-64` (`encodeUtf8`: `toCodePoints`, then `out.push` per byte, then `Uint8Array.from`), `:117-123` (`bytesToHex`: `toString(16).padStart(2,'0')` per byte), `:68-115` (`decodeUtf8`); `binary.mjs:22-28` (FROM_HEX: slice, regex `.test`, `parseInt` per pair), `:62-84` (DECODE_BASE64: `Map` lookup per char, plain array, then `Uint8Array.from`).
- Evidence: js-1 (`enc.mjs`, identical output verified with `Buffer.compare`): 1.6M-char mixed text `encodeUtf8` 210 ms versus 28 ms with a one-pass loop into a preallocated `Uint8Array(n*3)`; 2M ASCII 209 versus 26 ms; `bytesToHex` on 2 MB 454 ms versus 21 ms with a 256-entry table; 11-character strings, 1e6 calls: 318 versus 162 ms. js-4 (1 MB): `bytesToHex` 355 ms versus 57-64 ms (6x); FROM_HEX 131 ms versus 14 ms (9x); `encodeUtf8` 97 ms versus 5 ms `TextEncoder`; `decodeUtf8` 51 ms versus 2 ms `fatal: true` `TextDecoder`; `CRC32(REPEAT("x",1000000))` 219 ms and `BASE64` encode 355 ms although the encode loop is 16 ms; 300k-row `DECODE_BASE64("QUItMTAwMA==")` ~2.4 us per call (966 ms). js-1 P6 (guessed, not prototyped): `decodeUtf8` measured 83 ms (mixed) and 139 ms (ASCII) for 2 MB; an ASCII run fast path would likely help several-fold.
- Fix sketch: single-pass encoder into a preallocated buffer with the same surrogate validation and messages; 256-entry hex lookup; `charCodeAt` tables for hex/base64 decoding into a preallocated `Uint8Array`; ASCII fast path for decode. No host codec is used unless `TextDecoder` is given `{ fatal: true, ignoreBOM: true }` (else a leading U+FEFF is dropped; `FROM_UTF8(FROM_HEX("efbbbf41"))` must keep 2 code points). Error paths keep the offending character for the message.

### JS-P8 [medium] [measured] Text comparison encodes both operands to UTF-8 on every comparison; the sort/TOP comparator re-derives each key's class every time
Slices: js-1 P3, js-5 P3.
- Where: `eval.mjs:335`, `aggregate.mjs:289` via `asBytes` (`value.mjs:464`, `encodeUtf8` then `bytesCompare` `utf8.mjs:133`); `aggregate.mjs:269-301` (`compareValues`: `isNull`, `looksNumeric` x2, `asDecimal` x2, `asBytes` per compare), called from :365 and :413/:418 (TOP heap).
- Why: `looksNumeric` caches only a successful parse, so a non-numeric text key runs `D.parse` on each of the ~n log n comparisons; TEXT versus TEXT allocates two byte arrays per compare. `doSort` also builds a new `Map` frame and `Value.text(k)` per element even when the key expression does not use `_K` (`doTop` gates this with `nodeContainsVar`).
- Evidence: js-1: 400k comparisons of 10-20 character strings: 756 ms (encode + `bytesCompare`) versus 308 ms with a direct UTF-16 code-unit loop mapping units above D800 the way UTF-8 order requires; agreement on 20,000 sampled pairs and the edge set. Caveat: `encodeUtf8` is where a host string with an unpaired surrogate becomes E_UTF8, so a fast path must keep that check (take it only when a `/[\ud800-\udfff]/` test fails on both strings). js-5 (`bench4.mjs`, 200,000 keys): SORT of "k123" text 6007 ms; with keys decorated once 2036 ms (2.9x). SORT of numeric text 1907 ms; remaining cost is `D.cmp`'s BigInt scale alignment (`decimal.mjs:206`) and GC (comparator 549 ms, cmp 404 ms, GC 327 ms).
- Fix sketch: `compareText(a, b)` in `utf8.mjs` with the guarded fast path; decorate each sorted element once (isNull, kind, decimal-or-null, bytes) and compare decorated records (any fix for JS-C2 goes in the same place); gate `_K` on `nodeContainsVar` in `doSort`; fast path in `D.cmp` for equal scale / small digits.

### JS-P9 [medium] [measured] `Value.fromEntriesOwned` re-derives the record shape from scratch for every row (~4x slower than a shape check)
Slice: js-2 P2.
- Where: `value.mjs:115-127` (`shapedFromUniqueEntries`), `:85-99` (`recordShape`: `JSON.stringify(keys)` plus a Map lookup on a fresh string), `:254-262`.
- Why: every row built with `fromEntriesOwned` (SELECT_COLS, LINK joined rows, RECORD without a compile-time shape; `structure.mjs:80/89/131/293/354`) allocates an entry array, a `keys` array, a `values` array, a `Set` and a JSON string that must be hashed. `recordWithShape` (`structure.mjs:75-79`) already has the cheap form.
- Evidence (6 keys, 1e6 iterations): `fromEntriesOwned` 2428 ns/row (entry-building alone 204 ns, `JSON.stringify` alone 669 ns); via `shapedFromShape` when the shape is known 106 ns. A prototype comparing entry keys against a "last shape used" (6 pointer compares) with fallback: 577 ns/row (4.2x faster) including entry building.
- Fix sketch: keep a one-entry "last shape" cache in `shapedFromUniqueEntries` (check length and `entries[i][0] === shape.keys[i]`; uniqueness is guaranteed by the shape); fall back to Set + JSON. Let `recordShape` compare a candidate key list against a cached shape instead of building a JSON signature every call.

### JS-P10 [medium] [measured] `D.parse` is ~4x slower than needed and is ~17% of a numeric-data workload
Slice: js-2 P3.
- Where: `decimal.mjs:104-119` (regexp test, `slice`, `indexOf`, `slice` x2, concatenation, `replace(/^0+/)`, then `BigInt(string)`).
- Evidence: `parse("12.50")` 689 ns, `"3"` 650 ns, `"-0.05"` 868 ns versus a single-pass `charCodeAt` prototype (strings up to 16 chars, accumulate a Number, one `BigInt(v)`) at 162, 161, 194 ns; 300k random short strings fuzzed against `D.parse`: 0 mismatches. Profile of `ALL(L, _ * 2 + 1 > 0)` over 200k numeric strings: `parse` was 286 ms of 1673 ms total self time (17%); `add`/`cmp` are 35-70 ns each.
- Fix sketch: fast path for `length <= 15` digits (exact in a double; pure integer accumulation, still ASCII-checked before any conversion), otherwise the existing path (16 chars cannot reach a cap).

### JS-P11 [medium] [measured (noisy) + reasoned] One non-plannable operand (IF, COND, `,`, `;`, assignment) disables planning for the whole arithmetic expression
Slice: js-3 P1.
- Where: `math_plan.mjs:173-175` (returns null for `assign`/`seq`/`list`/IF/COND), `optimizer.mjs:535-557` (`inMath` is propagated to every child, so a child never gets its own plan once its parent is math).
- Why: `_["a"] * _["b"] * 2 * 3 + IF(_["c"] $== "k1", 1, 0)` compiles no plan for anything; the same expression with `LEN(_["c"])` in place of the IF gets a full plan.
- Evidence (`bench_if.mjs`, 100k rows, cpu-ms min/median over 9 runs): planned 109-204 / 125-211 versus unplanned 250-256 / 272-338 (ratio ~1.3-2.3x, indicative only at high load).
- Fix sketch: when `compileMathPlan(folded)` returns null, retry on each child with `inMath=false` (bottom-up), or let IF/COND be LOAD_LEAF (laziness preserved because a leaf is evaluated by `evalNode`). Result bytes unchanged provided JS-C9 is decided consistently.

### JS-P12 [medium] [measured] The append idiom `A = (A, x)` is O(n^2) with ~300 ns per copied element, 64% GC
Slice: js-3 P2.
- Where: `eval.mjs:271-282` (`evalList` clones every child) and `:410` (`evalAssign` clones the whole list a second time), `value.mjs` `cloneAt` (one 9-field `Value` per scalar).
- Evidence (`append2.mjs`): 2000 appends 1.05-1.23 s CPU, 4000 appends 3.6-3.9 s (20000 killed after minutes); profile 63.7% GC, 19% `cloneAt`, 7% `evalList`. Removing the redundant second copy (skip `.clone()` when `node.value.t === 'list'`) measured only ~15% (1234 to 1042 ms at 2000). The redundant copy also matters for the depth boundary (a fresh list of 200-deep children is 201 deep; the assignment clone raises E_DEPTH), so it is not free to drop.
- Fix sketch: structural (share immutable scalar leaves / copy-on-write, or in-place `A = (A, e)` append when the old value is unreferenced), which is `value.mjs` territory and must preserve §3.4. Partial win: return the same object from `cloneAt` for childless, decimal-cached scalars only if Value gains an immutability guarantee (it does not: `A[1][2] = 3` can add children to a scalar).

### JS-P13 [medium] [measured] `doSort` deep-clones every result element
Slice: js-5 P2.
- Where: `aggregate.mjs:371`.
- Evidence: SORT_BY over 200,000 records with a nested record and list: 3043 ms, 1629 ms without the clone (clone ~46% of the time plus GC).
- Fix sketch: drop the clone; needs the spec decision in JS-C48. Aliasing is the majority behaviour.

### JS-P14 [medium] [measured] `nodeContainsVar` walks the aggregate body on every aggregate call
Slices: js-5 P4, js-3 P4 (reasoned).
- Where: `aggregate.mjs:30-43` (called at :53/54, :443, :537), with two `toUpperCase()` allocations per `var` node.
- Why: a nested aggregate (`MAP(R, SUM(_["b"], …))`) re-walks the inner body for every outer element; the answer depends only on the immutable AST.
- Evidence: js-5 profile of `MAP(R, SUM(_["b"], <6-term arithmetic body>))` over 200,000 outer rows: `nodeContainsVar` 314 ms of 1638 ms total (19%). js-3 rated it low (reasoned, unmeasured).
- Fix sketch: memoise the boolean on the physical node (`node.usesK` slot, or WeakMap; the physical tree is a function of the AST) and compare names with the already upper-cased form; compute iteratively while doing it (JS-C12), next to `keysUnobserved`.

### JS-P15 [medium] [measured] `foldPairwise` is quadratic and produces a left-deep chain as deep as the list
Slice: js-6 P1.
- Where: `translator.mjs:858-864` (`foldPairwise`), `emit.mjs` `fill` (splice of the accumulated parts); callers `inOperator` (852), unrolled `aggregate` (1514), JOIN, COUNT.
- Evidence: `X IN L` (TEXT column, value-binding list of N strings, mariadb, translate only): N=2000 0.24 s; 4000 0.86 s; 8000 2.3 s; 16000 10.2 s (4x per doubling). `--cpu-prof` at N=8000: `fill` 690 ms self, `apply` 251 ms, GC 113 ms. The output has parenthesis depth N (N=16000 gives 16001 nested parens); MySQL/MariaDB/PostgreSQL parsers have stack limits on nested expressions (reasoned, not run).
- Fix sketch: build the left-deep chain iteratively without recopying (a rope of nested part arrays flattened once in `Fragment.#join`, or outer parentheses as prefix/suffix counts). Output must stay byte-identical, which rules out a balanced tree unless the sqlt expectations for 3+ element lists change.

### JS-P16 [medium] [measured] `executeHybrid` deep-clones the entire caller context on every hybrid run
Slice: js-7 P3.
- Where: `hybrid.mjs:730`.
- Evidence (js7/perf1.mjs): context holding a 200k-row unrelated table: 149 ms per run of a 3-row hybrid plan versus 0.09 ms with a small context.
- Fix sketch: shallow root (alias top-level entries, add `_INPUT`), copying only if the continuation assigns to a context name; behaviour changes only in the JS-C60 sense.

### JS-P17 [low-medium] [measured] BTL allocates a BigInt-backed decimal per byte; `Value.int(bigint)` first-call cost
Slices: js-4 P7, js-2 P6.
- Where: `binary.mjs:114` (`Value.int(b)`), `value.mjs` `Value.int` to `D.fromInt` (BigInt); `value.mjs:56-57, 299`.
- Evidence: building 1M byte Values 666 ms with `Value.int`, 211 ms with 256 pre-built decimals plus `Value.num`. `BTL` over 300k 7-byte rows took 2.46 s (~7 us per row). LTB itself is fast (46 ms per 1M); `Value.list` re-scanning an array the builtin just built is 49 ms per 1M. js-2: `Value.int(bigint)` forces `10n ** 1000000n` (87 ms, ~415 KB retained) on the first BigInt ever passed; a `n < 10n**18n` shortcut avoids it (first-call latency only, reasoned).
- Fix sketch: a module-level table of 256 decimals; `Value.listOwned(out)` in BTL, SPLIT and RGROUPS. Values must stay distinct objects; only decimal payloads shared (confirm Decimal objects are immutable in this host).

### JS-P18 [low-medium] [measured] Over-cap rejection in `guard()` costs up to ~0.5 s per rejected operation because `numDigits` builds an uncached 10^k
Slice: js-2 P4.
- Where: `decimal.mjs:61-67` (`numDigits` to `pow10(d - 1)`), `:80-92`; `mul`/`round`/`add` compute the full oversize result before `guard`.
- Evidence: `guard` on a 1M x 1M digit product 461 ms (`10n**1000000n` alone 87 ms, 2M ~350 ms); the multiply itself 128 ms. `POWER(POWER(3,99999),21)` E_RANGE takes 0.57 s, `POWER(999999999999, 100000)` 0.67 s. One-shot (E_RANGE ends the run; `??` does not catch it), so a bounded latency spike.
- Fix sketch: bracket the digit count from the bit length with integer arithmetic (lower bound `floor((b-1)*30102/100000)+1`, upper `floor(b*30103/100000)+1`), reject when the lower bound minus scale exceeds the cap, accept when the upper bound does not, and call the exact count only in the ambiguous band. `numDigits` was verified exact on 1.2M values, so it stays as tie-break. Optionally pre-check operand bit lengths in `mul`.

### JS-P19 [low-medium] [measured] `SelError` captures a stack trace, and errors are used as control flow in several probes
Slice: js-1 P4.
- Where: `errors.mjs:3-11`; consumers such as `builtins/structure.mjs:208, 218, 676` (`canonicalJoinKey`, join-key checks: `try { asDecimal } catch (SelError)`) and `value.mjs:500`.
- Evidence (`err.mjs`): `throw new SelError` 7.9 us at depth 5 and 14 us at depth 60 versus 2.2 and 7.8 us for a plain throw, and ~2.7 and 8.1 us with `Error.stackTraceLimit = 0`. A numeric join over a text column full of non-numbers pays this per row.
- Fix sketch: non-throwing probes (`tryAsDecimal`; `D.parse` already returns null) in the hot paths, rather than changing `SelError`.

### JS-P20 [low-medium] [measured] Regex `compile()` pays ~0.45 us fixed overhead per call, plus an uncached ASCII scan for the `i` flag
Slice: js-4 P5.
- Where: `regex.mjs:204-244`.
- Why: every call iterates the flags string with `for..of`/`toLowerCase`, concatenates a fresh cache key (`'\0' + pattern`), and for `i` calls `toCodePoints(pattern)` before consulting the cache.
- Evidence: raw `re.test` loop 0.16 us per call; with key concatenation and Map lookup 0.61 us; the `i`-flag ASCII check adds ~0.35 us. A 300k-row filter `RMATCH('^[a-z0-9]+@[a-z.]+$', email)` costs ~2.2 us per row against ~0.85 us for an empty FILTER.
- Fix sketch: remember the last `(pattern, flags)` string pair and its RegExp (`===` on both), or cache on the AST node when the pattern is a literal; move the non-ASCII check inside the cache-miss branch (the error still fires on the first call).

### JS-P21 [low] [measured] The regex compile cache is unbounded and keyed by data-derived patterns
Slice: js-4 P6.
- Where: `regex.mjs:202, :231-241`.
- Evidence: 300,000 distinct patterns retained 187 MB (~620 bytes each) and took 8.1 s (~27 us per miss). A rule taking its pattern from data (`RMATCH(_["pat"], text)`) leaks one RegExp per distinct pattern in a long-lived server; a hostile input can drive it.
- Fix sketch: cap the Map (a few thousand entries; drop the oldest on insert, easy with Map iteration order).

### JS-P22 [low-medium] [measured, noisy] Non-equi LINK re-aliases the right row for every pair
Slice: js-5 P5.
- Where: `structure.mjs:1140-1149` (`ensureRowTableAlias(rightItem, b2)` inside the inner loop).
- Evidence: 600 x 600 non-equi (3,300 matches): 646 to 461 ms, 733 to 558 ms, 692 to 558 ms in three paired runs (~15-25%, loaded box). `ALIAS_PLANS` stores one tableName per shape (`structure.mjs:257-259`), so relations sharing a shape but differing in name rebuild a RecordShape twice per left row here.
- Fix sketch: alias the right rows once before the outer loop; key `ALIAS_PLANS` on (shape, tableName).

### JS-P23 [low] [measured] Numeric/text literals allocate a fresh 9-field Value and re-run the surrogate regex on every evaluation
Slices: js-3 P3, js-2 P6.
- Where: `eval.mjs:223-228` (`case 'num'`, `case 'text'`) to `Value.text` to `checkText`.
- Evidence: ~75 ns per `Value.text('50')+_decimal` evaluation (5M iterations, `lit.mjs`). In the 200k-row FILTER profile the regex shows 1.9% and GC 20%.
- Fix sketch: a trusted constructor that skips `checkText` (the parser validated the literal), create literal Values once (assignment clones, so literals are not mutated), and/or a fast path in `evalBinary` comparisons that reads `node.r.dec` directly for a numeric literal operand. Byte-identical.

### JS-P24 [low] [measured] Double validation/allocation on every arithmetic result
Slice: js-2 P5.
- Where: `value.mjs:47-54` via `Value.num` called from `eval.mjs:218` and `evalBinary`; `decimal.mjs` `add` etc. already call `guard`.
- Evidence: `D.add` 68 ns; `Value.num(D.add(...))` 180 ns; a raw `new Value(TEXT,null)` with `_decimal` set 52 ns. ~60 ns per result goes to `checkDecimal` (typeof checks, `guard` a second time). ~22% of the numeric benchmark is GC (guessed, not measured, that trimming Value's fields helps).
- Fix sketch: internal `Value.numOwned(decimal)` for the evaluator/plan/number builtins; keep the checked `Value.num` for host input (which fixes JS-C20/JS-C33 in one place).

### JS-P25 [low] [measured] `textLiteral` is 20-25x slower than a split/join and allocates per character
Slice: js-7 P2.
- Where: `emit.mjs:132-159`.
- Evidence: 2.8 MB of text: mariadb 1206 ms, postgresql 728 ms; `split("'").join("''")` takes 50 ms. Only inline mode with large literals matters (params mode binds text).
- Fix sketch: precompute (per dialect, cached) a sorted key list and use one `String.replace` with an alternation regex built from escaped keys and a lookup object (single pass, longest-first).

### JS-P26 [low] [measured] Dialect chain is rebuilt on every lexical/entry lookup
Slice: js-7 P4.
- Where: `map.mjs:291-299` (`chain`), `:309-320` (`lexical`), `:326-344` (`entry`).
- Evidence: 1e6 `lexical()` calls 1.5 s, `entry()` 1.0 s, `Emit.ident` 2.0 s; ~10-12% of self time in a 30-clause translate.
- Fix sketch: cache the chain array per dialect, invalidated in `defineDialect`/reset.

### JS-P27 [low] [measured] `fromCodePoints` slices even short arrays
Slice: js-1 P5.
- Where: `utf8.mjs:33-40`.
- Evidence: 10-element array 246 ms versus 192 ms per 1e6 calls; single code point 101 versus 55 ms, with `String.fromCodePoint.apply(null, cps)` directly for `length <= 4096`.
- Fix sketch: fast path for short arrays. Superseded by JS-P5 where that lands.

### JS-P28 [low] [reasoned] Smaller items
- `structuralHash` (`value.mjs:679-683`) allocates `String(i + 1)` and hashes it per list element per call (needed so a packed list hashes like its keyed Map twin); a precomputed table of key hashes for 1..N would remove it. `for (const byte of value.scalar)` on a Uint8Array uses the iterator protocol; an indexed loop is faster. Low impact (DEDUPE/BUCKET on lists of lists only). (js-2)
- `pow10` cache (`decimal.mjs:38-53`) is cleared wholesale when total exponent weight passes ~1M digits, so operands with scales 600k and 500k alternately recompute a 10^k each op; contrived. (js-2)
- `D.cmp`/`aligned` multiply to align scales on every comparison of differing scales (32 ns same-scale versus 40 ns different); matters only at huge scales (1M-digit cmp 0.6 ms). (js-2)
- `elements(value)` in TAKE/DROP/SELECT_COLS/DEDUPE/BUCKET/JOIN/`doSort` allocates a `[key, value]` pair per element even for flat lists (the relational lane has `forEachCollectionItem`); `String(i + 1)` keys are only needed for `_K`; `makeJoinedRow` allocates two Sets and an entries array per pair; `doBucket` bare form clones every member (spec-mandated; 2540 ms versus 615 ms for the projection form on 200,000 nested records). (js-5)
- `TO_UTF8`: `Value.bin(a.bytes(0))` copies an array `encodeUtf8` just created (`binOwned` would do for TEXT); pad returns `fromCodePoints(c)` for the no-op case instead of the original string; pad builds `padding` element by element then `concat`s instead of `repeat` + slice; PADL of 1M is 181 ms today; worth doing only with JS-P5. (js-4)

---

# Cross-host findings

These are shared with other hosts (or are spec gaps in which the hosts currently disagree or all agree on a doubtful answer). Per `CLAUDE.md` "The one rule": spec first, then conformance cases, then every host, then `tools/check.sh`. None of these should be fixed in JS alone.

| Finding | Cross-host situation | Spec / conformance action |
|---|---|---|
| JS-C1 aggregate body mutating its collection | JS crashes; php/py/cpp/lisp/go answer; in-range mutation is live in js/py/cpp and snapshot in php/lisp/go | Spec §3.4/§7.3: snapshot versus live. Cases in JS-C1 |
| JS-C2 SORT order on numeric-looking versus non-numeric text | 5 hosts, 5 answers | Spec §7.3/7.4 total order; cases in JS-C2 |
| JS-C3 `??` chain depth | cpp segfault, Python RecursionError, Lisp stack exhaustion; PHP/Go fine | Spec §6.4: `??` costs one level; `lim.coalesce-depth`, `-just-under` |
| JS-C10 interpolation depth | js and Python fail with host errors; php/cpp/go/lisp give E_DEPTH 1:101; PHP is quadratic (>110 s at depth 50,000) | `lim.interp-depth` |
| JS-C11 interpolation body paren balance | all five hosts accept `"{1) + (2}"` | `lex.interp.unbalanced-close`, `-open`, `COUNT("{1), (2}")` |
| JS-C5 REPEAT / PADL / PADR / text size cap | PHP memory fatal, Lisp heap exhaustion | Spec §6.4 text-length cap with E_RANGE; `text.repeat.huge-count`, `text.padl.huge-width`, `text.repeat.empty-huge-count` |
| JS-C6 regex catastrophic backtracking | cpp uncaught `srell::regex_error`, Python exponential, PHP bounded by PCRE limits | Spec: linear matcher or step budget/new code; `re.dos.nested-plus`, `re.dos.alt-star` |
| JS-C18 POSIX class mid-class | php rejects; js/py/cpp/lisp accept | `re.class.posix-not-leading`, `re.class.collating-element` |
| JS-C19 regex group nesting cap | php/cpp reject at ~250; js/py/lisp accept | Spec §6.4 group cap 250; `re.limits.group-depth-250-ok`, `-251-rejected` |
| JS-C43 `[\s-z]` etc. | all five agree on a meaning neither engine has | `re.class.escape-adjacent-hyphen` |
| JS-C44 flag `I` | all hosts accept | `re.flag.uppercase-I` |
| JS-C45 quantified anchors | lisp accepts | `re.quant.on-anchor` |
| JS-C9 math-plan versus tree operand coercion | all five hosts share the plan answer | Spec §6.2/§7.1 operand type-check timing; cases in JS-C9 |
| JS-C13 SORT/TAKE to TOP fusion | all five hosts fused | Spec §7.4 TOP contract; cases in JS-C13 |
| JS-C16 `exprDependsOnlyOn` `list` node | JS and Lisp raise spurious E_UNDEF_VAR; php/py/cpp/go answer | two expressions in 15-relational.selt |
| JS-C14 hash flooding of DEDUPE/BUCKET | JS measured; other hosts' hashes not reviewed here | benchmark scenario in `tools/`, not `.selt` |
| JS-C48 SORT clone versus alias; `LIST` copying | js/lisp/cpp clone; php/python alias; C++ splits like JS | Spec §3.4 copy list |
| JS-C49 BUCKET over a scalar | only C++ follows §7.3 | `BUCKET("abc", _)` |
| JS-C50 bare BUCKET drops a group | all hosts | repro in 16-bucket.selt |
| JS-C51 same-name binders in LINK | all hosts, equi versus per-pair | §7.4 pin + cases |
| JS-C52 `BUCKET(list, binder, key)` form | manifest declares it; no host implements it | drop from manifest or implement |
| JS-C31 pipe step depth and multi-`_` | all agree on depth; Go diverges on multi-`_` | Spec §6.4 pipe cost; multi-`_` semantics; cases |
| JS-C30 E_UTF8 position | php/cpp/go also report 0:0 | Spec decision on byte versus code-point offset; API-level case |
| JS-C32 error catalogue phases | `spec/limits.json` data | mark E_RANGE, E_UTF8 `both` |
| JS-C40 `dependencies()` | php/python/cpp print the same under-report | spec sentence or implementation; API probes |
| JS-C41 no size bound for values/joins | all hosts | Spec §6.4 element-count / join-row cap |
| JS-C46 `LTB(empty list)` | all five agree on E_NO_SCALAR | Spec §7.7; `bin.ltb.empty` |
| JS-C47 `PATH(x, "")` | all agree | `null.path.empty-path` |
| JS-C35 fromNative floats | JS accepts; PHP/Python refuse | Spec §8 / docs exception, or refuse in JS; API probe on all hosts |
| JS-C7, C26, C27, C28, C53, C57 SQL translator | Python identical on the pairs checked (40,000-case differential: 0 differences) | `.sqlt` cases listed per entry |
| JS-C22 hybrid planner | Python classifies the same programs as hybrid with the same statement | executing 25-hybrid-plans case on SQLite |
| JS-C24 `checkNumericGuard` memoise-before-check | PHP has the same shape; Python's docstring likewise | unit test: translate twice on a bad dialect |
| JS-C55 LIMIT via Number | JS/PHP style; Python and Lisp bignums correct | `stmt.take.over-2^53`, or a cap in all hosts |
| Regex note: RGROUPS no-match | Spec §7.8 prose says "empty list"; case pins `none` | spec prose or case |
| PHP-only (js-4 note) | `LEFT`/`FIND` with a 400-digit argument differ from the other four hosts | tell the PHP reviewer |

---

# Suggested conformance cases

`.selt` unless noted. "Decision" means the spec must choose the answer first.

| Case name (idea) | Expression | Expected |
|---|---|---|
| `lim.coalesce-depth` / `-just-under` | `1 ?? ` x200 / x201 then `1` | E_DEPTH at a pinned column (JS-C3) |
| `lim.interp-depth` | a few thousand nested `"{"{...}"}"` levels | E_DEPTH 1:101 (JS-C10) |
| `lim.agg-body-chain` | `COUNT(MAP(LIST(1,2), _+_+…(5,000 terms)…))` | E_DEPTH, same column all hosts (JS-C12) |
| `lex.interp.unbalanced-close` | `"{1) + (2}"` | E_SYNTAX (JS-C11) |
| `lex.interp.unbalanced-open` | `"{(1}"` | E_SYNTAX (JS-C11) |
| `lex.interp.count-splice` | `COUNT("{1), (2}")` | E_SYNTAX (JS-C11) |
| `pipe.placeholder.multi` / `pipe.depth` | `N=0; (N+=1) .> MAX(_, _); N`; `1 .> ABS(` x200 | decision (JS-C31) |
| `agg.mutate.append-list` | `A = (1,2); COUNT(MAP(A, A[COUNT(A)+1] = 0))` | `2` (JS-C1; also FILTER, SUM, ALL, SORT_BY, TOP_BY, BUCKET) |
| `agg.mutate.add-key` | `A = LIST(1,2,3); COUNT(MAP(A, (A["k"]=1; _)))` | `3` (JS-C1) |
| `agg.mutate.record` | `R = RECORD("a",1); R["b"]=2; R["c"]=3; COUNT(MAP(R, R[_K & "x"] = 1))` | `3` (JS-C1) |
| `rel.sort.mixed-text` | `LIST("10","9","1a") .> SORT() .> JOIN(",")` and permutations | decision, one answer (JS-C2) |
| `rel.sort.idempotent` | `SORT(SORT(L))` equals `SORT(L)` for the mixed list | equal (JS-C2) |
| `rel.top.equals-take-sort` | `TOP(L,4)` equals `TAKE(SORT(L),4)` on the 12-element list | equal (JS-C2) |
| `plan.order.binary-left-notnum-right-undef` | `A = "x"; A + B` | decision (E_UNDEF_VAR at B if evaluate-then-coerce) (JS-C9) |
| `plan.order.div-after-notnum` | `"abc" + 1/0` | decision (JS-C9) |
| `plan.order.null-minus-missing` | `(NULL - MISSING) ?? 7` | decision; must equal the `==` form's behaviour (JS-C9) |
| `plan.order.strict-args-min` / `round-arg-order` | `MIN(1, "x", Y)`; `ROUND("x", Y)` | E_UNDEF_VAR at Y if strict-args rule applies (JS-C9) |
| `plan.alias.list-var-mutated-by-leaf` | `A = (1, 2); A + LEN((A[1] = 10; "ab"))` | `12` if the reference is not a snapshot (JS-C9) |
| `plan.alias.mutation-in-arg` | `A = (1,2); A + LEN(A[1] = "xyz")` | decision (JS-C9) |
| `plan.order.dead-deep-branch` | `IF(TRUE, "abc" + 1/0, <300-term chain>)` | same error as without the dead branch (JS-C9) |
| `rel.top.zero-raising-key` | `LIST(RECORD("a",1)) .> SORT_BY(_["zz"]) .> TAKE(0)` | E_NO_KEY or documented TOP contract (JS-C13) |
| `rel.top.invalid-n-after-raising-key` | `... .> SORT_BY(_["zz"]) .> TAKE(0.5)` | decision: E_NO_KEY versus E_NOT_INT (JS-C13) |
| `rel.top.count-side-effect-order` | `X = 1; (3,1,2) .> SORT_BY(_ * X) .> TAKE((X = -1; 2))` | decision: (1,2) versus (3,2) (JS-C13) |
| `rel.link.list-operand-one-sided` | `LINK(LIST(RECORD("k",1)), LIST(RECORD("k",1)), (_1["k"], _2["k"]) == _1["k"]) .> COUNT` | `1` (JS-C16) |
| `rel.link.list-operand-generic` | `... _1["k"] == (_2["k"], _1["k"]) AND TRUE` | `0` (JS-C16) |
| `rel.link.same-name-binders` | `LINK(T,T,T["id"]==T["mgr"]) .> COUNT` versus `... AND TRUE` | one answer for both (JS-C51) |
| `rel.bucket.scalar` | `BUCKET("abc", _)` | one-element grouping per §7.3 (JS-C49) |
| `rel.bucket.bare-same-index-text` | `A="x"; A[1]=2; L=LIST("x",A); BUCKET(L,_) .> MAP(COUNT(_))` | consistent with `BUCKET(L,_,COUNT(_))` (JS-C50) |
| `rel.alias.sort-take-fused` | `A = LIST(LIST("a")); TAKE(SORT(A),1)["1"][(A["1"]["1"]="z"; "1")]` versus the `IF(TRUE, SORT(A), 0)` form | equal (JS-C48) |
| `text.replace.long-replacement` | `LEN(REPLACE("a", REPEAT("b", 200000), "a"))` | `200000` (JS-C4) |
| `text.split.long-separator` | SPLIT with a 200,000-code-point separator | no host exception (JS-C4) |
| `text.repeat.huge-count` / `text.padl.huge-width` | `REPEAT('a', 1000000000000)`; `PADL("abc", 99999999999999999999999, "x")` | E_RANGE (JS-C5) |
| `text.repeat.empty-huge-count` | `REPEAT("", 99999999999999999999999)` | `""` (JS-C5) |
| `re.dos.nested-plus` / `re.dos.alt-star` | `RMATCH('^(a+)+$', REPEAT('a',N) & 'b')` | fast result once a bound exists (JS-C6) |
| `re.class.posix-not-leading` | `RMATCH('[a[:digit:]', ':')` | E_REGEX_SYNTAX (JS-C18) |
| `re.class.collating-element` | `RMATCH('[a[.x.]', 'x')`, `[a[=x=]` | E_REGEX_SYNTAX (JS-C18) |
| `re.limits.group-depth-250-ok` / `-251-rejected` | `'(' * 250 + 'a' + ')' * 250` / 251 | TRUE / E_REGEX_SYNTAX (JS-C19) |
| `re.class.escape-adjacent-hyphen` | `RMATCH('[\s-z]', 'a')`, `[+-\d]`, `[\w-.]` | E_REGEX_SYNTAX (JS-C43) |
| `re.flag.uppercase-I` | `RMATCH('a', 'A', 'I')` | decision (JS-C44) |
| `re.quant.on-anchor` | `RMATCH('^*', 'aaa')`, `$+`, `^{2}` | E_REGEX_SYNTAX everywhere (JS-C45) |
| `bin.ltb.empty` | `BLEN(LTB(BTL("")))` | `0` (decision) (JS-C46) |
| `null.path.empty-path` | `PATH(RECORD("", 5), "")` | pin current behaviour (JS-C47) |
| API probe | `compile(12)`, `compile(undefined)`, CLI `-e` with no operand | E_BAD_ARG / usage (JS-C29) |
| API probe | `Value.num({digits:5n, scale:0})` | E_BAD_ARG (JS-C20) |
| API probe | `Value.fromNative([1,,3])`; `Value.fromNative(new Date())`; `fromNative(0.5)` | E_BAD_ARG (JS-C21, C34, C35) |
| API probe | `dependencies()` of `A + 1; A = 2` and `IF(X, A = 1, 0); A` | A; X, A (or documented under-report) (JS-C40) |
| `sqlt` `agg.scope.*` | repros 1-4 of JS-C7 | outer keys '1' and '2' preserved; no RangeError |
| `sqlt` `norm.inline.expansion-budget` | n=30 doubling chain | refusal (JS-C8) |
| `sqlt` `norm.inline.capture-not-by-binder` | `X2 = A; ALL((5,6), A, X2 > 0)` | A is the column, not the binder (JS-C26) |
| `sqlt` `warrant.bool.unknown-through-coalesce` / `warrant.numeric.unknown-through-if` | `(F ?? TRUE) AND TRUE`; `IF(N > 0, A, 0) + 1 > 0` | refused / guarded (JS-C27) |
| `sqlt` `stmt.refusal.raw-field-select-cols` / `-across-derived-table` | `ITEMS .> SELECT_COLS("total")` with a raw field | refusal (JS-C28) |
| `sqlt` `const.binder-shadows-value-binding` | `ALL((A, A), V, V + 1 > 0)` with a scalar value binding `V` | guarded like binder `I` (JS-C53) |
| `sqlt` `stmt.take.over-2^53` | `ITEMS .> TAKE(99999999999999999999999)` | exact digits or refusal (JS-C55) |
| `sqlt` `stmt.take.whole-number-with-scale` | `ITEMS .> TAKE(2.0)` | accepted, same as SEL (JS-C56) |
| `sqlt` `plan.deep-chain` | `ORDERS` + 20,000 x `.> TAKE(1)` | same error code in every host, no host exception (JS-C54) |
| `sqlt` (25-hybrid-plans, executing on SQLite) | `ORDERS .> FILTER(...) .> FILTER(<no-sql fn>)`; `... .> MAP(RECORD("k", <no-sql fn> & _K))` | pure_memory or prefix before the FILTER (JS-C22) |
| `sqlt` `plan.fallthrough.take-after-a-map-that-can-raise-stays-local` | MAP that raises on row 3, then `TAKE(2)` | E_NOT_NUM (JS-C23) |
| unit test | translate twice on a bad registered dialect (numericGuard), and a `textEscape` without the quote rule | throws both times (JS-C24, C25) |

---

# Suggested fix order

Group A: quick, low-risk, JS-local (a day or less), no spec change needed.
1. JS-P1 (`Value.textOwned` for `concat` and derived builtins): biggest win, linear instead of superlinear `&=`.
2. JS-C4 (`out.push(...repl)` to a loop): one-line crash fix.
3. JS-C21, JS-C20, JS-C33, JS-C37, JS-C29 (host-boundary validation: sparse arrays, `neg` type, copy, setter, non-string source).
4. JS-C15 (linear trailing-zero strip in `canonicalJoinKey`).
5. JS-C24 (move `guardChecked.add` after the checks), JS-C25 (validate `textEscape`/`identEscape` at `defineDialect`).
6. JS-P2, JS-P7, JS-P27 (lexer and codecs: byte-identical, well benchmarked, prototypes already exist in the reviewers' scratchpads).
7. JS-P9, JS-P10, JS-P14, JS-P20, JS-P21, JS-P23 (small local caches and fast paths).
8. JS-P3 (raise the shape-cache width caps; bound entry count only).

Group B: needs a small spec decision, then all five hosts (do together, with the cases from the table above).
1. JS-C1 (aggregate iteration snapshot versus live) and JS-C2 (total order for SORT/TOP): both are high, both are visible to users, both make hosts disagree today.
2. JS-C3, JS-C10, JS-C12, JS-C54 (uncounted recursion: `??`, interpolation, pre-analysis walks, SQL/hybrid walks): one theme, one depth-accounting rule per construct, so fix in one sweep and add limit cases.
3. JS-C5 (text size cap) together with JS-C4's case and JS-C41 (collection/join size cap): one "resource caps" spec section.
4. JS-C11 (interpolation paren balance).
5. JS-C9 and JS-C13 (math plan and TOP fusion evaluation order): pick the model, then make the plan and the optimiser fusion follow it; add cases that run the suite through both trees. The JS gate should run the conformance suite through the plain tree as well (js-3 did this in a patched copy) so that plan-versus-tree divergences show up.
6. JS-C16, JS-C48/JS-P13, JS-C49, JS-C50, JS-C51, JS-C52 (relational corners the hosts already disagree on).
7. Regex batch: JS-C18, C19, C43, C44, C45 (validator tightening, all hosts), JS-C17.

Group C: larger design work.
1. JS-C6 (regex time bound): decide between a Thompson/Pike matcher in every host, a step budget, or restricting nested quantifiers; the C++ uncaught exception should be fixed regardless.
2. JS-C7, JS-C8, JS-C26, JS-C53, JS-P4 (SQL translator): lexical binder capture (`frames` snapshot) and a node budget; add the memoisation of constant evaluation at the same time. Python shares all of these.
3. JS-C22, JS-C23, JS-C59 (hybrid planner key retention and error-hiding limits), with an executing SQLite case in the gate.
4. JS-P5, JS-P6, JS-P8, JS-P12 (text builtins without code point arrays; comparator decoration; append idiom): value-model work that needs care with §3.4 aliasing.
5. JS-C14 (hash flooding): scalar-key Maps plus a seeded hash for compound values.

Group D: low-priority cleanups and documentation decisions: JS-C30, C31, C32, C35, C36, C38, C40, C42, C46, C47, C55-C58, C60, JS-P15-P19, JS-P22, JS-P24-P26, JS-P28.

---

# Checked and found sound

Merged from the seven reports; the next reviewer need not redo these.

Front end (js-1)
- Operator tokenisation order matches grammar.md (longest first); `1.`, `.5`, `1..2`, `1.5.2` rejected; `1.>ABS` lexes as `1` `.>` `ABS`. Non-ASCII digits and letters are E_SYNTAX in all hosts; identifiers use ASCII checks.
- Whitespace is exactly space/tab/CR/LF; `#` comments end at LF only, including inside interpolation.
- Escapes `\\ \" \n \t \r \{ \}` and `\u{1-6 hex}`: E_ESCAPE, E_RANGE, E_UNTERMINATED codes and positions matched php/cpp/py/go for about 30 shapes. Interpolation: empty segments kept, `{}`/`{ }`/`{# c\n}` are E_SYNTAX at the body start, braces inside nested literals and `\{` do not close the body.
- Lines/columns/offsets count code points; `posAt` correct at line boundaries and EOF.
- Parser precedence table matches SPEC §5 and grammar.md; assignment targets validated after parsing; `(A)[1] = 5` accepted by every host.
- Depth pins for parens, calls, index brackets, prefix chains and assignment chains reach E_DEPTH at identical columns in js/php/cpp/go. Left-associative chains of 200,000 terms parse in under 1 s and are refused with E_DEPTH by the evaluator and `dependencies()`.
- `.>` desugaring (placeholder rules, `binds` exclusion, grouped `(_)`, empty parens, postfix chaining, error ordering), call-argument flattening and `RecordShape` preparation agree with the other hosts.
- `decodeUtf8` accept/reject table (overlongs, surrogates, `f4 90+`, `f5+`, truncation, lone continuation) identical in js/php/cpp/py/go for 50 byte sequences; `toCodePoints` rejects lone surrogates; `bytesCompare` is byte order. `asciiUpper` moves only a-z. `errors.mjs` `MAX_DEPTH` from `_limits.mjs` (200); `RESERVED` matches SPEC §2.3.

Numeric core and values (js-2)
- decimal arithmetic vs an independent model: 257k cases (`+ - * / % cmp round CANON floor ceil trunc POWER isInteger`, up to 40 int digits, scale 35), 0 mismatches; the repo oracle `tools/decimal-oracle.py 6000 777` gives 59,998 cases, 0 mismatches (its gaps: no POWER, CANON, `isInteger`, no operand above ~13 digits or scale above 6, no cap boundary).
- `guard()` boundaries at exactly 1,000,000 int/frac digits and scales 0..1e6; `numDigits` exact on 1.2M values; negative zero never appears.
- `parse`: ASCII-only, no trimming, JS `$` does not match before a trailing newline, linear on 5M-char adversarial inputs; leading-zero stripping and cap tests on the stripped size.
- POWER (32-bit truncation gone, intermediate guard stops runaway squaring, `POWER(0,0)`=1, error ordering agrees with php/py/cpp: 63 probe expressions); division scale rules; `number.mjs` argument coercion order and positions, MIN/MAX first-wins on ties, `CANON`.
- `math_plan.mjs`: manifest consumer refuses to load on a missing opcode; copy-propagation rules 1-5 are result-identical; `optimizeRoot` skips optimisation past the evaluator cap so plan depth cannot hide E_DEPTH.
- `value.mjs`: `clone/eqlAt/dumpAt/fromNativeAt/toNativeAt` depth-counted; shared-shape/list fast paths agree with the entry path; `eql` and `structuralHash` consistent; dump escaping identical on five hosts; `Value.bin`/`Value.int` range checks; `__proto__` keys survive `toNative`.

Evaluator, registry, host API, optimizer (js-3)
- Plan versus plain tree agree on values: 20,000 random arithmetic programs, zero OK-versus-error or value differences (all differences were the JS-C9 operand-order class). Copy-propagation keeps scale and drops the sign of zero like `D.add/mul`.
- Pipeline rewrites: ~100k random pipelines through the physical optimiser identical to the plain tree except JS-C9 and JS-C13; FILTER swap/fusion, TAKE/TAKE, DROP/DROP, DISTINCT/DISTINCT, FILTER(TRUE) removal, MAP/sort swap show no divergence (the in-memory `cannotRaise` treats index reads as unsafe, keeping swap rules nearly inert).
- Constant folding reproduces the evaluator including positions; folds that throw are left unfolded; DROP+DROP sums up to 2^54 saturate identically.
- Depth: `exceedsDepth` matches the evaluator's counting; `evalNode` restores `ctx.depth` in `finally`; frames are popped in `finally` in `walk` and the sort/TOP/BUCKET loops; assignment chain depth is checked before any evaluation.
- Assignment semantics (path resolution, index expressions evaluated once left to right, compound forms, `=` clones, E_BAD_ASSIGN for binders incl. `_K`/`_`); non-arithmetic `evalBinary` operators evaluate both then coerce left then right; `AND`/`OR` short-circuit; `??`/`???` catch only E_NO_KEY/E_UNDEF_VAR.
- Registry: `define` checks against the manifest at load; `registerFunction` refuses builtins/reserved words; a compiled program keeps its spec. `dependencies()` depth-capped with the evaluator's E_DEPTH position; `Program.physicalAst()` cached per `ast` identity and never mutates `ast`.
- Perf experiments with no measurable gain (do not redo): a reusable scratchpad array per math plan; removing the per-node `try/finally` in `evalNode`. Optimiser compile cost is fine: 90-step alternating pipelines optimise in 1-35 ms (logical) and 1-7 ms (physical).

Text, regex, binary, null and control builtins (js-4)
- Regex rewrite: `\d \D \w \W \s \S` outside and inside classes expand to explicit ASCII classes; `\b \B \v \p \A \z \Z \G \K`, digit backreferences, lookaround, named/atomic groups and possessive quantifiers rejected with `E_REGEX_SYNTAX`. `^`/`$` have no `m` flag and no `$`-before-trailing-newline behaviour; dotall on; all five hosts agree. The `i` flag: non-ASCII pattern is `E_BAD_ARG` before syntax errors; U+212A and U+017F folding consistent across JS/PHP/Python.
- Regex offsets, empty-match advance, non-participating groups, `$0`-`$9`/`$$` splicing, `re.lastIndex` reset, quantifier bounds (65535 cap, `{2,1}`, leading zeros; `(a{65535}){65535}` compiles lazily in 94 ms).
- `Args` and `define` conventions: strict builtins evaluate all arguments left to right; COALESCE, GET, PATH, IF, COND are lazy as the manifest says; `IF(NULL,...)` is `E_NULL`, `IF("",...)` is `E_NOT_BOOL`.
- Text builtins count code points on astral inputs and agree with all hosts; UPPER/LOWER touch only A-Z; TRIM strips only space/tab/CR/LF; error order and codes for 23- and 400-digit integer arguments agree on JS/Python/C++/Lisp.
- Binary: `FROM_UTF8` validation, U+FEFF kept, `CRC32` check value `cbf43926`, padded base64 encode and strict decode, `LTB` range and whole-number rules identical to the other hosts.
- Null/control: `IS_NULL`, `IS_NOT_NULL`, `IS_BLANK`/`IS_PRESENT`, `GET`, `PATH`, `ABORT`; `index.mjs` import order and `assertManifestCovered()`; no aliasing problems.

Structure and aggregate builtins (js-5)
- Strict-builtin argument order for JOIN, HAS, TAKE, DROP, SELECT_COLS, RECORD, BUCKET, TOP_BY; RECORD arity, duplicate keys, zero-arg forms, value cloning; TAKE/DROP with 1e20 and 2^53+1; SELECT_COLS argument-order semantics; DEDUPE/DISTINCT identity (1, "1", 1.0, "1.0" separate per EQL, NULLs merge).
- Sort stability and DESC tie order, TOP heap agreement with the stable sort whenever the comparator is a total order, empty/NULL inputs.
- LINK/LINK_LEFT: canonical numeric key is one key per number (`1.5` = `"1.50"`, `-0` = `0`); byte key for `$==`; NULL keys match nothing; JOIN_BAD ordering matches §7.4; right-first evaluation when the left source is a join; joined-row key order; conformance 06-aggregates 49/49, 15-relational 144/144, 16-bucket 21/21, 16-joined-rows 30/30, 21-structural-hash-identity 13/13 on JS. The LINK bucket lookup is an exact-string Map, immune to JS-C14.
- FILTER preserves keys; frames always popped in `finally`; `ctx.joinPrefilter` reset around nested evaluation; `_K` bound only when mentioned. TOP/SORT on 200,000 rows: `TOP_BY(...,10)` 205 ms, `SORT_BY(...) .> TAKE(10)` fused into the heap path (135 ms).

SQL translator and rendering (js-6, js-7)
- Identifier quoting: `Emit.ident` doubles the quote character (or `identEscape`) for every table/column/alias; bindings validated by `checkName`; `fillSlot` uses split/join so `$&`-style patterns cannot reinterpret an identifier; the literal path never concatenates a value into SQL. `textLiteral` correct for MySQL-family, PG, SQLite on `a\b'c` + NUL + U+001A + U+10FFFF.
- NUM literal path re-emits `D.format(D.parse(text))`; `Binding.value(..., 'NUM')` rejects `" 5"`, `"+5"`, `"1e3"`, `"0x10"`, Arabic-Indic digits, `"007"`, `"-0"`, `"5\n"`; `"1 OR 1=1"` refused with E_SQL_BINDING; BOOL/NUM/BIN never bound as parameters.
- `Bindings` uses a Map; `makeRelation` uses `Object.create(null)`; `__proto__`/`constructor` names safe, including the `defs` Map; `Object.hasOwn` used for dialect keys.
- Whole-expression refusal: no state survives a thrown refusal; `tryTranslate` catches only SqlError; the source AST is not mutated by translation; the `E_SQL_DEPTH` counter is balanced on every exit path.
- Params mode: 30,000 cases, `bindings().length` equals the debug marker count. JS and Python agree on code and position for 40,000 random translations (all four dialects, inline/params/debug); 59k builtin sweep and 715k random sweep had no non-SqlError host exception beyond those listed.
- `_K` and `_` as assigned names protected from stage-1 substitution; undeclared-column handling refuses/guards except via the `unify` route (JS-C27).
- Dialect inheritance flattened tables checked for ansi/mysql-family/mariadb/mysql/postgresql/sqlite; runtime `defineDialect` validation and lookup order behave as documented; `Emit.fill` escapes and cyclic references handled; hybrid: helper handling, fallthrough dependency handling, `bucketRowsAreKeys`/`joinRowsLackBinders`, LINK not a split point until projected, `tryLatestMember` equals `run()` with an explicit sort; ~4000-program SQLite fuzz found no value or error-code disagreement between `executeHybrid` and `run` other than JS-C22, JS-C23, JS-C59 (and group-order/unsorted-TAKE noise treated as "relations have no order").
- `relational-plan.mjs` is plain data classes.
- `tools/benchmark-js-decimal-guard.mjs` times only `add(9...9, 1)` (cheap `guard` fast paths), never the near-cap `numDigits` path or a rejection (JS-P18); its default import path resolves relative to the tool file.
