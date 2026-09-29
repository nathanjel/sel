# JS correctness

[Worklist](README.md) · [Shared tests](00-tests.md) · [Performance](07-performance.md)

60 findings; 44 implementation tasks. Report severity/confidence is preserved below. Work through one source area at a time; a grouped checkbox closes only when every listed finding is resolved.

Each task starts by reproducing the report against the current tree, then lands the linked shared regression cases before the fix. Fix sketches are starting points, not approved spec changes. Prefer one implementation source per task; touching spec, fixtures or an unavoidable API boundary is allowed. Do not edit generated artifacts without their generator. High-severity tasks take precedence within each area.

## Front end, depth and source positions

<a id="c-js-c3"></a>

- [x] **C-JS-C3 — Resolve JS-C3.** Right-associative `??` / `???` recursion is not depth-counted: hostile input gives a host RangeError instead of E_DEPTH

  **[JS-C3](../js-code-review.md) — [high] [confirmed, re-verified by synthesizer] Right-associative `??` / `???` recursion is not depth-counted: hostile input gives a host RangeError instead of E_DEPTH**

  Source target: `js/src/parser.mjs:209-213` (the `assoc === 'R'` branch of `parseTerm`: `const right = this.parseTerm(bp);` with no `enter`/`leave`).

  Implementation starting point: spec first: state what `??` costs (one level per operator, like assignment). Wrap the recursion in `this.enter(t); try {...} finally { this.leave(); }` in every host, as the assignment branch does. Alternative: a loop that collects operands and folds right-to-left, but the cost must still be defined by the spec.

  Regression prerequisite: [T01 / JS-C3](tests/01-frontend.md#js-c3).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts enter()/leave() in the R-assoc branch; conformance 1120/1120 on js, js-bundle-min, php, cpp, lisp, go, python

<a id="c-js-c10"></a>

- [x] **C-JS-C10 — Resolve JS-C10, JS-C11.** Coordinate the related changes below at their shared implementation surface.

  **[JS-C10](../js-code-review.md) — [medium] [confirmed] Lexer recursion for nested interpolation is not depth-bounded; cost grows faster than linearly**

  Source target: `js/src/lexer.mjs:147-182` (`lexQuoted`), `:212-254` (`matchBrace`/`skipQuoted`), `:259-280` (`emitParts` calls `lexRange` calls `lexQuoted`).

  Implementation starting point: give the lexer its own nesting counter and raise E_DEPTH once interpolation nesting could no longer parse anyway (each interpolation costs four parse levels, so a cap of MAX_DEPTH/4 nested literals cannot change a result that would otherwise have parsed); make it match the parser's reported position, or lex iteratively with an explicit stack and record each literal's matching `}` once to remove the rescans.

  Regression prerequisite: [T01 / JS-C10](tests/01-frontend.md#js-c10).

  **[JS-C11](../js-code-review.md) — [medium] [confirmed, all five hosts agree] An interpolation body is spliced as raw tokens without checking that its parentheses balance**

  Source target: `js/src/lexer.mjs:259-280` (`emitParts`). Same design in the other hosts.

  Implementation starting point: spec first. In `emitParts` track paren depth over the emitted interpolation tokens and raise E_SYNTAX at the first unmatched `)` (or at the `{` if a `(` is left open); or parse the body with its own parser instance and splice the subtree. All hosts.

  Regression prerequisite: [T01 / JS-C11](tests/01-frontend.md#js-c11).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — old-vs-new lexer differential 0 diffs per host (100k-300k random strings); 100k-level nest in <2s on every host

<a id="c-js-c30"></a>

- [x] **C-JS-C30 — Resolve JS-C30.** Source with an unpaired surrogate raises E_UTF8 at position 0:0:0, not at the offending character

  **[JS-C30](../js-code-review.md) — [low] [confirmed] Source with an unpaired surrogate raises E_UTF8 at position 0:0:0, not at the offending character**

  Source target: `lexer.mjs:39` (`toCodePoints(source, null)`), `errors.mjs:8-10` (`pos ? ... : 0`).

  Implementation starting point: compute the position in the lexer path; needs a spec decision on byte versus code point offset first.

  Regression prerequisite: [T01 / JS-C30](tests/01-frontend.md#js-c30).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — check-cli-source.sh passes on all six hosts

<a id="c-js-c31"></a>

- [x] **C-JS-C31 — Resolve JS-C31.** Pipeline call arguments cost one nesting level, not two; multi-placeholder pipes diverge in Go

  **[JS-C31](../js-code-review.md) — [low] [confirmed] Pipeline call arguments cost one nesting level, not two; multi-placeholder pipes diverge in Go**

  Source target: `parser.mjs:303-339` (`parsePipeStep` calls `parseSequence` outside any `parsePrimary`), `:326-333` (placeholder replacement loop).

  Implementation starting point: decide in the spec whether the pipe step costs two levels and whether multiple `_` all bind (and whether the operand is evaluated once or once per `_`); then add cases.

  Regression prerequisite: [T01 / JS-C31](tests/01-frontend.md#js-c31).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — Go break removed; all six hosts agree

<a id="c-js-c32"></a>

- [x] **C-JS-C32 — Resolve JS-C32.** Error catalogue phases are wrong for two codes the front end raises at compile time

  **[JS-C32](../js-code-review.md) — [low] [confirmed] Error catalogue phases are wrong for two codes the front end raises at compile time**

  Source target: `spec/errors.md` compile-time table and `spec/limits.json` (rendered into `_limits.mjs:22-35`).

  Implementation starting point: mark them `both` in `limits.json` and add rows to the compile-time table.

  Regression prerequisite: [T01 / JS-C32](tests/01-frontend.md#js-c32).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — node tools/gen-limits.mjs regenerated every host rendering

## Exact decimal arithmetic and limits

<a id="c-js-c20"></a>

- [x] **C-JS-C20 — Resolve JS-C20, JS-C33, JS-C37.** Coordinate the related changes below at their shared implementation surface.

  **[JS-C20](../js-code-review.md) — [medium] [confirmed] `Value.num` accepts a decimal record with a non-boolean `neg`, producing wrong arithmetic**

  Source target: `value.mjs:47-54` (`checkDecimal`), consumed by decimal.mjs `add` (`a.neg === b.neg`), `mul`, `cmp`, `div`.

  Implementation starting point: require `typeof d.neg === 'boolean'` in `checkDecimal` and store a normalised copy (see JS-C33).

  Regression prerequisite: [T02 / JS-C20](tests/02-decimal.md#js-c20).

  **[JS-C33](../js-code-review.md) — [low] [confirmed] `Value.num(decimal)` keeps the caller's object, so mutating it afterwards changes the Value**

  Source target: `value.mjs:47-54` returns `d` itself; `Value.num` stores it in `_decimal` (287-291).

  Implementation starting point: `return { neg: d.neg, digits: d.digits, scale: d.scale }`; internal callers could use an unchecked `Value.numOwned` (JS-P25).

  Regression prerequisite: [T02 / JS-C33](tests/02-decimal.md#js-c33).

  **[JS-C37](../js-code-review.md) — [low] [confirmed] `Value.scalar` setter leaves a stale cached decimal**

  Source target: `value.mjs:148-150` (setter), `:138` (`_decimal` cache); `scalar` is public (`sel.d.ts:43`).

  Implementation starting point: setter also does `this._decimal = null`.

  Regression prerequisite: [T03 / JS-C37](tests/03-values.md#js-c37).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — JS lane green (runtime 141 checks, optimizer 149, conformance 24/25 pass, dist/min identical)

<a id="c-js-c38"></a>

- [x] **C-JS-C38 — Resolve JS-C38.** `_MAX_INT_BITS = 3321929` is a hand-typed copy of a generated limit

  **[JS-C38](../js-code-review.md) — [low] [unconfirmed as a defect; latent] `_MAX_INT_BITS = 3321929` is a hand-typed copy of a generated limit**

  Source target: `js/src/decimal.mjs:26-27`.

  Implementation starting point: derive the shift from the limit at load time with integer arithmetic (a rational bound; `10n  BigInt(MAX_INT_DIGITS)` costs 87 ms at 1e6), or add a one-line gate assertion.

  Regression prerequisite: [T02 / JS-C38](tests/02-decimal.md#js-c38).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — JS lane green (runtime 141 checks, optimizer 149, conformance 24/25 pass, dist/min identical)

## Value ownership, mutation and host conversion

<a id="c-js-c21"></a>

- [x] **C-JS-C21 — Resolve JS-C21, JS-C34, JS-C35, JS-C36.** Coordinate the related changes below at their shared implementation surface.

  **[JS-C21](../js-code-review.md) — [medium] [confirmed] `Value.fromNative` on a sparse array builds a corrupt list; later use throws raw TypeError**

  Source target: `value.mjs:625` (`x.map(...)` preserves holes).

  Implementation starting point: `Array.from(x, (e) => Value.fromNativeAt(e, depth + 1))` (visits holes as undefined, so NULL), or reject holes with E_BAD_ARG.

  Regression prerequisite: [T03 / JS-C21](tests/03-values.md#js-c21).

  **[JS-C34](../js-code-review.md) — [low] [confirmed] `fromNative` silently turns unsupported objects into NULL/records instead of E_BAD_ARG**

  Source target: `value.mjs:627-630` (`typeof x === 'object'` catch-all using `Object.keys`).

  Implementation starting point: accept only `Object.getPrototypeOf(x)` of `Object.prototype` or `null` (maybe class instances); everything else `badArg`; reject `ArrayBuffer.isView(x)` other than Uint8Array.

  Regression prerequisite: [T03 / JS-C34](tests/03-values.md#js-c34).

  **[JS-C35](../js-code-review.md) — [low] [confirmed] JS `fromNative` accepts fractional numbers; PHP and Python refuse floats**

  Source target: `value.mjs:616-619, 710-715` (`nativeNumberToDecimal`).

  Implementation starting point: refuse non-integers in JS (parity, breaking) or record the JS exception in spec §8 and the docs. Needs a decision.

  Regression prerequisite: [T03 / JS-C35](tests/03-values.md#js-c35).

  **[JS-C36](../js-code-review.md) — [low] [confirmed] `toNative` then `fromNative` is not an inverse for records whose keys look like array indices**

  Source target: `value.mjs:634-656` (`toNativeAt` builds a plain object).

  Implementation starting point: unfixable with plain objects; document the exception like PHP's, or offer an ordered `toNative` (entries array / Map).

  Regression prerequisite: [T03 / JS-C36](tests/03-values.md#js-c36).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — JS lane green (runtime 141 checks, optimizer 149, conformance 24/25 pass, dist/min identical)

## Evaluator order, optimizer and recovery

<a id="c-js-c9"></a>

- [x] **C-JS-C9 — Resolve JS-C9.** The math plan coerces operands at load time; the tree evaluator evaluates both operands first and coerces afterwards

  **[JS-C9](../js-code-review.md) — [medium] [confirmed, re-verified by synthesizer] The math plan coerces operands at load time; the tree evaluator evaluates both operands first and coerces afterwards**

  Source target: `js/src/eval.mjs:145-158` (`LOAD_VAR`/`LOAD_LEAF` call `asDecimal` immediately) versus `eval.mjs:317-326` (`evalBinary` evaluates `l` and `r` before any `asDecimal`); `js/src/math_plan.mjs:57-61,177-179`; MIN/MAX fold at :158-170 (coerces `MIN(a,b)` before `c` is evaluated).

  Implementation starting point: choose one model in the spec (js-3 favours "evaluate both operands, then coerce left then right"). In the plan, let LOAD_VAR/LOAD_LEAF store the raw Value in the scratchpad and let each arithmetic step coerce `src1` then `src2` (Value caches `_decimal`); for MIN/MAX emit all operands first, then chain. That is a five-host change. If the current plan behaviour is the decision, `evalBinary` must interleave for `+ - * / %` and the deep-tree fallback stops being a different language.

  Regression prerequisite: [T04 / JS-C9](tests/04-evaluation.md#js-c9).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts 1600/1600; plain-vs-optimised 0 diffs

<a id="c-js-c12"></a>

- [x] **C-JS-C12 — Resolve JS-C12.** Uncounted recursion over the AST in aggregate/join pre-analysis helpers

  **[JS-C12](../js-code-review.md) — [medium] [confirmed] Uncounted recursion over the AST in aggregate/join pre-analysis helpers**

  Source target: `aggregate.mjs:30-43` (`nodeContainsVar`, called by `walk`, `doTop`, `doBucket`), `structure.mjs:159-172` (`exprDependsOnlyOn` via `tryExtractEquiKeys`), also `structure.mjs` `pureSource` :564, `readSelf` :623, `aggregate.mjs` `readsOnlyFields` :157.

  Implementation starting point: give the walks a depth argument and raise E_DEPTH at 200 (matching the evaluator) or return the conservative answer past the cap; or cache one boolean on the node (JS-P14) and compute it iteratively.

  Regression prerequisite: [T04 / JS-C12](tests/04-evaluation.md#js-c12).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

<a id="c-js-c13"></a>

- [x] **C-JS-C13 — Resolve JS-C13.** SORT/SORT_DESC/SORT_BY + TAKE fusion into TOP/TOP_BY changes evaluation order of the count, keys and side effects

  **[JS-C13](../js-code-review.md) — [medium] [confirmed] SORT/SORT_DESC/SORT_BY + TAKE fusion into TOP/TOP_BY changes evaluation order of the count, keys and side effects**

  Source target: `js/src/optimizer.mjs:384-392` (rule `SORT* then TAKE -> TOP*`).

  Implementation starting point: either document that TOP's contract is "keys of the taken rows only" and pin it, or restrict the fusion to a literal non-negative-integer `n` >= 1 whose keys cannot raise or have effects (`cannotRaise` test on the key expression).

  Regression prerequisite: [T04 / JS-C13](tests/04-evaluation.md#js-c13).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts green

<a id="c-js-c39"></a>

- [x] **C-JS-C39 — Resolve JS-C39.** `optimizeAstLogical` fuses two FILTERs into a deeper predicate and can raise E_DEPTH for a program the evaluator accepts

  **[JS-C39](../js-code-review.md) — [low] [confirmed] `optimizeAstLogical` fuses two FILTERs into a deeper predicate and can raise E_DEPTH for a program the evaluator accepts**

  Source target: `optimizer.mjs:444-463` (fusion wraps both predicates in an extra `AND`, +1 level), with `optimizeRoot`'s check only on the unrewritten tree (585-587).

  Implementation starting point: skip the fusion when the fused predicate's depth would exceed the cap (reuse `exceedsDepth` on the candidate), or run the `exceedsDepth` guard on the optimiser output.

  Regression prerequisite: [T04 / JS-C39](tests/04-evaluation.md#js-c39).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts green

<a id="c-js-c42"></a>

- [x] **C-JS-C42 — Resolve JS-C42.** The public `register`/`registerBuiltin` can replace a builtin, and the optimiser/math plan ignores the replacement

  **[JS-C42](../js-code-review.md) — [low] [confirmed] The public `register`/`registerBuiltin` can replace a builtin, and the optimiser/math plan ignores the replacement**

  Source target: `registry.mjs:90-100` (no builtin/reserved protection, no manifest reconcile), `math_plan.mjs:43,134` and `optimizer.mjs:119` (classify by `node.name`, not `node.spec`), typed in `sel.d.ts:152-160`.

  Implementation starting point: stop exporting `register`/`registerBuiltin` from the package entry, or make it refuse names in `BUILTIN_MANIFEST` unless called from `define`.

  Regression prerequisite: [T04 / JS-C42](tests/04-evaluation.md#js-c42).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

## Aggregates, ordering, grouping and joins

<a id="c-js-c1"></a>

- [x] **C-JS-C1 — Resolve JS-C1.** An aggregate body that grows the collection it iterates crashes with an uncaught TypeError or runs out of heap

  **[JS-C1](../js-code-review.md) — [high] [confirmed, re-verified by synthesizer] An aggregate body that grows the collection it iterates crashes with an uncaught TypeError or runs out of heap**

  Source target: `js/src/builtins/aggregate.mjs:69,75` (`walk`) and `:468-474` (`consume` loop of TOP/TOP_BY); `structure.mjs:32-46` (`forEachCollectionItem`). Root cause: `Args.val(0)` returns the variable's own Value (eval.mjs:75-78), `A[..] = ..` mutates that same object, and the loops re-read `collection.storage.length` or iterate the live `children` Map.

  Implementation starting point: iterate a snapshot. Only a body that contains an `assign` node (static, cacheable on the physical node) can mutate anything, so snapshot (`Array.from(children)`, captured `storage` array and length) only in that case and keep the zero-copy path otherwise. Capture `length` once in TOP's `consume` loops. Snapshot is also the semantics php/lisp/go have.

  Regression prerequisite: [T05 / JS-C1](tests/05-relational.md#js-c1).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-js-c2"></a>

- [x] **C-JS-C2 — Resolve JS-C2.** SORT / SORT_BY / TOP comparator is not a total order for numeric-looking versus non-numeric text

  **[JS-C2](../js-code-review.md) — [high] [confirmed, re-verified by synthesizer for js and php] SORT / SORT_BY / TOP comparator is not a total order for numeric-looking versus non-numeric text**

  Source target: `js/src/builtins/aggregate.mjs:269-301` (`compareValues`), used by `doSort` (:365) and `doTop` (:413).

  Implementation starting point: make the order total in the spec first: classify each key (null 0 < bool 1 < numeric-looking 2 < other TEXT/BIN 3; decimal cmp within class 2, bytes within class 3) and compare classes before values. In `compareValues` test "both numeric" and "both non-numeric text" explicitly, then byte-compare. Add the rule to SPEC §7.3/7.4 and docs/functions.md.

  Regression prerequisite: [T05 / JS-C2](tests/05-relational.md#js-c2).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-js-c14"></a>

- [x] **C-JS-C14 — Resolve JS-C14.** DEDUPE/DISTINCT and bare/projection BUCKET go quadratic on hash-colliding keys (unseeded 32-bit FNV-1a)

  **[JS-C14](../js-code-review.md) — [medium] [confirmed] DEDUPE/DISTINCT and bare/projection BUCKET go quadratic on hash-colliding keys (unseeded 32-bit FNV-1a)**

  Source target: `value.mjs:662-703` (`structuralHash`/`stringHash`/`mixHash`) as used by `structure.mjs:137-152` (`doDedupe`: `bucket.some(existing => item.eql(existing))`) and `aggregate.mjs:551-553` (`doBucket`: `bucket.find(...)`).

  Implementation starting point: for scalar-only keys (TEXT/BOOL/BIN with no children) key a Map on an exact string (`"t"+scalar`, `"b"+hex`, ...); use `structuralHash` only for compound values, with a per-process random seed. Order of first appearance is unchanged.

  Regression prerequisite: [T05 / JS-C14](tests/05-relational.md#js-c14).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-js-c15"></a>

- [x] **C-JS-C15 — Resolve JS-C15.** Numeric equi-join key canonicalisation is quadratic in the number of trailing fractional zeros

  **[JS-C15](../js-code-review.md) — [medium] [confirmed] Numeric equi-join key canonicalisation is quadratic in the number of trailing fractional zeros**

  Source target: `structure.mjs:211-215` (`canonicalJoinKey`: `while (scale > 0 && digits % 10n === 0n) { digits /= 10n; scale--; }`).

  Implementation starting point: format first, then trim trailing `0` characters (and a dangling `.`) from the string, which is linear; or count trailing zeros once from the decimal string.

  Regression prerequisite: [T05 / JS-C15](tests/05-relational.md#js-c15).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-js-c16"></a>

- [x] **C-JS-C16 — Resolve JS-C16.** `exprDependsOnlyOn` ignores `list` AST nodes: a comma-list operand is mis-classified as one-sided and the join raises a spurious E_UNDEF_VAR (JS and Lisp; php/python/cpp/go answer)

  **[JS-C16](../js-code-review.md) — [medium] [confirmed] `exprDependsOnlyOn` ignores `list` AST nodes: a comma-list operand is mis-classified as one-sided and the join raises a spurious E_UNDEF_VAR (JS and Lisp; php/python/cpp/go answer)**

  Source target: `structure.mjs:159-172` (`default: return true`; no case for `t: 'list'`; `pureSource`, `nodeContainsVar` and `readSelf` do handle `list`/`items`).

  Implementation starting point: add `case 'list': return node.items.every(...)`, or better default to `false`/refuse and enumerate the leaf node types (`num`, `text`, `bool`, `null`) so a new node type fails safe.

  Regression prerequisite: [T05 / JS-C16](tests/05-relational.md#js-c16).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-js-c48"></a>

- [ ] **C-JS-C48 — Resolve JS-C48.** SORT clones its elements but TOP/TAKE do not, so the SORT+TAKE-to-TOP fusion changes an observable result in JS (and C++)

  **[JS-C48](../js-code-review.md) — [low] [confirmed] SORT clones its elements but TOP/TAKE do not, so the SORT+TAKE-to-TOP fusion changes an observable result in JS (and C++)**

  Source target: `aggregate.mjs:371` (`indexed.map((x) => x.item.clone())`) versus `:476` (`heap.map((entry) => entry.item)`).

  Implementation starting point: decide in the spec whether SORT and LIST copy. Aliasing (drop the clone) is the majority behaviour and removes the cost in JS-P13.

  Regression prerequisite: [T05 / JS-C48](tests/05-relational.md#js-c48).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c49"></a>

- [ ] **C-JS-C49 — Resolve JS-C49, JS-C50, JS-C52.** Coordinate the related changes below at their shared implementation surface.

  **[JS-C49](../js-code-review.md) — [low] [confirmed] BUCKET over a scalar returns an empty list instead of the one-element list §7.3 prescribes**

  Source target: `aggregate.mjs:520` (`if (value.isNull() || value.size() === 0) return Value.list([])`).

  Implementation starting point: return early only for `kind === NONE && size() === 0` (as `doTop` does); needs the suite to fix the rule first (five hosts currently agree on the other answer).

  Regression prerequisite: [T05 / JS-C49](tests/05-relational.md#js-c49).

  **[JS-C50](../js-code-review.md) — [low] [confirmed, all hosts] Bare `BUCKET(list, key)` silently drops a whole group when two distinct group keys have the same index-key text**

  Source target: `aggregate.mjs:550-561` (group identity = EQL of the whole key) versus `:575` (`out.set(group.keyString, …)` overwrites).

  Implementation starting point: in the bare spelling identify groups by keyString (the scalar), not by structural EQL.

  Regression prerequisite: [T05 / JS-C50](tests/05-relational.md#js-c50).

  **[JS-C52](../js-code-review.md) — [low] [confirmed, all hosts] Spec signature `BUCKET(list, binder, key)` is declared in spec/builtins.json (form 2) but not implemented**

  Source target: `aggregate.mjs:526-535` (`doBucket` picks by count only).

  Implementation starting point: drop the form from the manifest/§7.3, or dispatch on `args.isSymbol(1)` like `doSort` does (the spec must resolve the `BUCKET(L, KEY, COUNT(_))` ambiguity where KEY is a variable).

  Regression prerequisite: [T05 / JS-C52](tests/05-relational.md#js-c52).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c51"></a>

- [x] **C-JS-C51 — Resolve JS-C51.** Same-name binders (`LINK(T, T, T["id"] == T["mgr"])`) evaluate differently on the equi and the per-pair path

  **[JS-C51](../js-code-review.md) — [low] [confirmed, all hosts] Same-name binders (`LINK(T, T, T["id"] == T["mgr"])`) evaluate differently on the equi and the per-pair path**

  Source target: `structure.mjs:174-185` (`tryExtractEquiKeys`: both name sets contain the shared relation name, so both orientations match) and `:1127-1151` (per-pair path binds `T` to the right row last).

  Implementation starting point: pin it in §7.4 (with equal left and right binder names only `_1`/`_2` are unambiguous; refuse equi extraction when the names collide, or bind both on the per-pair path); add cases.

  Regression prerequisite: [T05 / JS-C51](tests/05-relational.md#js-c51).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Portable regex semantics and resource failures

<a id="c-js-c6"></a>

- [ ] **C-JS-C6 — Resolve JS-C6.** RMATCH / RFIND / RGROUPS / RREPLACE have no protection against catastrophic backtracking

  **[JS-C6](../js-code-review.md) — [high] [confirmed, re-verified by synthesizer] RMATCH / RFIND / RGROUPS / RREPLACE have no protection against catastrophic backtracking**

  Source target: `js/src/builtins/regex.mjs:204-244` (compile), `:286`, `:295`, `:305`, `:327`. The validator (:61-118) allows nested quantifiers.

  Implementation starting point: needs a spec decision first: (a) forbid nested unbounded quantifiers (cheap, but rejects legitimate patterns and does not cover `(a|aa)+`, `(.*)*`), (b) implement a small Thompson/Pike matcher in every host (the subset has no backreferences or lookaround, so it is regular) for linear time and identical semantics, or (c) define a step budget and a new error code. The C++ uncaught exception is a separate bug in that host.

  Regression prerequisite: [T06 / JS-C6](tests/06-regex.md#js-c6).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c17"></a>

- [x] **C-JS-C17 — Resolve JS-C17.** V8 regexp backtrack-stack overflow escapes as an uncaught RangeError from a regex builtin

  **[JS-C17](../js-code-review.md) — [medium] [confirmed] V8 regexp backtrack-stack overflow escapes as an uncaught RangeError from a regex builtin**

  Source target: `regex.mjs:286-288` (`re.test`), `:295`, `:305`, `:261` (`re.exec` inside `matches`). Only `new RegExp` is wrapped in try/catch (:235-239).

  Implementation starting point: catch `RangeError` around `test`/`exec` and convert to a SelError; needs an errors.md entry (`E_RANGE` or a new code). Goes away with a non-backtracking engine (JS-C6).

  Regression prerequisite: [T06 / JS-C17](tests/06-regex.md#js-c17).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-js-c18"></a>

- [x] **C-JS-C18 — Resolve JS-C18, JS-C19, JS-C43, JS-C44, JS-C45.** Coordinate the related changes below at their shared implementation surface.

  **[JS-C18](../js-code-review.md) — [medium] [confirmed] The regex class validator rejects POSIX classes only at the start of a class: `[a[:digit:]` is accepted here and rejected by PCRE**

  Source target: `regex.mjs:163-165` (checks `[:` only right after the opening `[`/`[^`), loop :170-196.

  Implementation starting point: in `validateClass` reject `[` followed by `:`, `.` or `=` anywhere in the class (simplest: reject any unescaped `[` inside a class and say to write `\[`), in all five hosts.

  Regression prerequisite: [T06 / JS-C18](tests/06-regex.md#js-c18).

  **[JS-C19](../js-code-review.md) — [medium] [confirmed] Group nesting depth is not bounded by the regex validator; PCRE and SRELL reject at 250, JS/Python/Lisp accept**

  Source target: `regex.mjs:85-97` (no depth counter).

  Implementation starting point: add a group-nesting cap (250) to spec §6.4 / limits.json, count `(`/`)` in `validate`, raise `E_REGEX_SYNTAX`. PCRE also caps total groups at 65535 (untested).

  Regression prerequisite: [T06 / JS-C19](tests/06-regex.md#js-c19).

  **[JS-C43](../js-code-review.md) — [low] [confirmed] `\d \w \s` expansion inside a regex class turns an adjacent `-` into a range (hosts agree; the result is neither engine's meaning)**

  Source target: `regex.mjs:29` (EXPAND_INSIDE), `:182`.

  Implementation starting point: in `validateClass` reject a `-` directly before or after a `\d \w \s` escape ("write the hyphen first or last") and say so in §7.8.

  Regression prerequisite: [T06 / JS-C43](tests/06-regex.md#js-c43).

  **[JS-C44](../js-code-review.md) — [low] [confirmed] Regex flag letters are matched case-insensitively using the host's `toLowerCase`**

  Source target: `regex.mjs:206-208`.

  Implementation starting point: compare `ch === 'i'` exactly; decide in the spec whether `I` is allowed; change all hosts together.

  Regression prerequisite: [T06 / JS-C44](tests/06-regex.md#js-c44).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[JS-C45](../js-code-review.md) — [low] [confirmed] The regex validator does not reject quantified anchors (`^*`, `$+`, `^{2}`); JS relies on V8, Lisp accepts them**

  Source target: `regex.mjs:99-110`.

  Implementation starting point: reject a quantifier directly after `^`, `$`, `(` or `|` in `validate`, in all hosts.

  Regression prerequisite: [T06 / JS-C45](tests/06-regex.md#js-c45).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Text, binary and output budgets

<a id="c-js-c4"></a>

- [x] **C-JS-C4 — Resolve JS-C4.** REPLACE throws an uncaught RangeError when the replacement is longer than about 125k code points

  **[JS-C4](../js-code-review.md) — [high] [confirmed, re-verified by synthesizer] REPLACE throws an uncaught RangeError when the replacement is longer than about 125k code points**

  Source target: `js/src/builtins/text.mjs:78` (`out.push(...repl)`).

  Implementation starting point: `for (const c of repl) out.push(c)`, or build the result from UTF-16 chunks and never spread (see JS-P5, JS-P6).

  Regression prerequisite: [T07 / JS-C4](tests/07-text-binary.md#js-c4).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-js-c5"></a>

- [x] **C-JS-C5 — Resolve JS-C5.** REPEAT, PADL and PADR have no size cap; the JS host throws host exceptions or exhausts memory

  **[JS-C5](../js-code-review.md) — [high] [confirmed, re-verified by synthesizer] REPEAT, PADL and PADR have no size cap; the JS host throws host exceptions or exhausts memory**

  Source target: `js/src/builtins/text.mjs:140` (REPEAT), `:143-155` (pad). Enabling primitive: `Args.int/nonNegInt` to `D.toSafeInt` (decimal.mjs:174), named "safe" but returning imprecise or `Infinity` values above 2^53 (`Number(digits)`).

  Implementation starting point: add a text-length cap to spec §6.4 (for example 1,000,000 code points, or the 2,000,000 the limits tests already assume), enforced in REPEAT/PADL/PADR/REPLACE/`&` as `E_RANGE` before allocating; compute `count * len` in decimal-safe arithmetic; handle empty string and empty result first.

  Regression prerequisite: [T07 / JS-C5](tests/07-text-binary.md#js-c5).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-js-c41"></a>

- [x] **C-JS-C41 — Resolve JS-C41.** Nothing bounds the size of a value, the work of `,`, or the size of a join result

  **[JS-C41](../js-code-review.md) — [low] [confirmed] Nothing bounds the size of a value, the work of `,`, or the size of a join result**

  Source target: `eval.mjs:271-282` (`evalList`); `structure.mjs:1127-1151` (per-pair path pushes every joined row into `output`; also equi with one shared key).

  Implementation starting point: a spec decision: an element-count cap for `evalList`/`clone` and a join-result cap (E_RANGE), or document that hosts must run untrusted rules under a resource limit.

  Regression prerequisite: [T07 / JS-C41](tests/07-text-binary.md#js-c41).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-js-c46"></a>

- [x] **C-JS-C46 — Resolve JS-C46.** `LTB` of an empty list fails, so `LTB(BTL(x))` does not round-trip for empty input

  **[JS-C46](../js-code-review.md) — [low] [confirmed, consistent across hosts] `LTB` of an empty list fails, so `LTB(BTL(x))` does not round-trip for empty input**

  Source target: `binary.mjs:120-121` (`v.size() > 0 ? v.values() : [v]`).

  Implementation starting point: decide in the spec (empty list gives empty BIN is the natural reading) and change all hosts.

  Regression prerequisite: [T07 / JS-C46](tests/07-text-binary.md#js-c46).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-js-c47"></a>

- [x] **C-JS-C47 — Resolve JS-C47.** Behaviour of `PATH(x, "")` is unspecified and untested

  **[JS-C47](../js-code-review.md) — [low] [confirmed] Behaviour of `PATH(x, "")` is unspecified and untested**

  Source target: `null.mjs:42` (`if (pathStr === '') return target;`).

  Implementation starting point: pin the decision in the spec.

  Regression prerequisite: [T07 / JS-C47](tests/07-text-binary.md#js-c47).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## SQL lexical scope, normalization and expansion

<a id="c-js-c7"></a>

- [ ] **C-JS-C7 — Resolve JS-C7, JS-C26, JS-C53.** Coordinate the related changes below at their shared implementation surface.

  **[JS-C7](../js-code-review.md) — [high] [confirmed, repro 1 re-verified by synthesizer] SQL translator: aggregate binders are dynamically scoped**

  Source target: `js/src/sql/translator.mjs:1205-1206` (`fromBinder`, NODE case), `:1198-1203` (`binder`), `:1384` (`source`, NODE case), `:1547-1559` (`withElement`), `:1567-1616`.

  Implementation starting point: make binders lexical. Store a copy of `this.frames` in `Binder.node` and render/resolve a NODE payload with `this.frames` temporarily replaced by that snapshot (both in `fromBinder` and in `source()`, where recursion must also count depth). The synthesized `_K` text node is only safe today because it has no free variables.

  Regression prerequisite: [T08 / JS-C7](tests/08-sql-scope.md#js-c7).

  **[JS-C26](../js-code-review.md) — [medium] [confirmed] SQL stage-1 inlining captures a definition's free names into aggregate binders**

  Source target: `normalise.mjs:143-208` (`substitute`), with `translator.mjs:1198`.

  Implementation starting point: alpha-rename binders that clash with free names of any definition reaching their scope, or refuse with E_SQL_ASSIGN in that case. With JS-C7 fixed, the snapshot idea covers this if definitions are wrapped as closures.

  Regression prerequisite: [T08 / JS-C26](tests/08-sql-scope.md#js-c26).

  **[JS-C53](../js-code-review.md) — [low] [confirmed] SQL: a binder that reuses the name of a scalar `value` binding is treated as that constant**

  Source target: `translator.mjs:247-254` (`node`: `constants.isConstant(n, this.constNames)`), `:1923-1933` (`guardNumeric`), `:1977-1981`; `constants.mjs` `isConstant` (`var` gives `b.has(name)`).

  Implementation starting point: `isConstant` must be told the names currently bound by translator frames (treat any var found by `this.binder(name)` as non-constant).

  Regression prerequisite: [T08 / JS-C53](tests/08-sql-scope.md#js-c53).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c8"></a>

- [ ] **C-JS-C8 — Resolve JS-C8.** SQL translator: exponential output and time from small programs (no size or work budget)

  **[JS-C8](../js-code-review.md) — [high] [confirmed] SQL translator: exponential output and time from small programs (no size or work budget)**

  Source target: `js/src/sql/normalise.mjs:143-208` (a definition is inlined as a shared pointer, so a DAG becomes a tree in the render walk), `translator.mjs:1504-1514` (`aggregate` unrolls every element and re-renders element nodes per use), `:858-864`, and `constants.validate` at normalise.mjs:90.

  Implementation starting point: count rendered nodes (or fragment parts) in the Translator and in `substitute` (budget of about 1e5 nodes, refused as E_SQL_UNSUPPORTED / E_SQL_DEPTH "expands to more than N nodes"). `substitute` can memoise each definition's size and add it at each use.

  Regression prerequisite: [T08 / JS-C8](tests/08-sql-scope.md#js-c8).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c54"></a>

- [ ] **C-JS-C54 — Resolve JS-C54.** SQL layer: non-SqlError host exceptions escape `tryTranslate` / `planHybrid`, and definition chains are refused with a wrong message

  **[JS-C54](../js-code-review.md) — [low] [confirmed] SQL layer: non-SqlError host exceptions escape `tryTranslate` / `planHybrid`, and definition chains are refused with a wrong message**

  Source target: (a) `constants.mjs` `isConstant` recursion (called from `normalise.mjs:90`); (b) `translator.mjs:1483` (`valueNode`: `v.asText(pos)` on a NONE element), `:297-316` / `emit.mjs:80-86`; (c) `hybrid.mjs:91-118` (`sourceTables`), `:174` (`containsUnsupportedSql`), `:202` (`collectFieldReferences`), `:242` (`readsWholeRow`), `:465` (`inlineLiterals`), `:524` (`readNames`); (d) `normalise.mjs:53-100`, `:90`.

  Implementation starting point: charge each definition's depth at its use in `substitute` (store `depth(def)` next to it) and refuse before the tree exists; reject NONE children in `Binding.value` (or in `valueNode`) with E_SQL_BINDING; pass a depth to the hybrid walks and raise at MAX_DEPTH, or catch in `pureMemoryPlan`; fix the wording or document the translator limit.

  Regression prerequisite: [T08 / JS-C54](tests/08-sql-scope.md#js-c54).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c58"></a>

- [ ] **C-JS-C58 — Resolve JS-C58.** SQL: an assigned keyed list cannot be indexed directly (missing feature, safe direction)

  **[JS-C58](../js-code-review.md) — [low] [confirmed] SQL: an assigned keyed list cannot be indexed directly (missing feature, safe direction)**

  Source target: `translator.mjs:427-431` (`index`), cf. `childOf` at :3074.

  Implementation starting point: in `index()`, when `obj.t` is `clist`/`list`, resolve with `childOf` and render the child.

  Regression prerequisite: [T08 / JS-C58](tests/08-sql-scope.md#js-c58).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## SQL kinds, numeric fidelity and server limits

<a id="c-js-c27"></a>

- [ ] **C-JS-C27 — Resolve JS-C27.** SQL `unify()` promotes an undeclared (UNKNOWN) column to a certain kind, bypassing the kind warrant

  **[JS-C27](../js-code-review.md) — [medium] [confirmed] SQL `unify()` promotes an undeclared (UNKNOWN) column to a certain kind, bypassing the kind warrant**

  Source target: `translator.mjs:3145-3158` (`unify`), used at 1188 (IF/COND) and by `retKind` (3121-3129) for `@unify:` entries (`??`, `COALESCE`, MIN/MAX-style).

  Implementation starting point: `unify` returns UNKNOWN whenever any branch is UNKNOWN (certain only if all are certain); check the effect on the sqlt corpus.

  Regression prerequisite: [T09 / JS-C27](tests/09-sql-kinds.md#js-c27).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c55"></a>

- [ ] **C-JS-C55 — Resolve JS-C55, JS-C56.** Coordinate the related changes below at their shared implementation surface.

  **[JS-C55](../js-code-review.md) — [low] [confirmed] SQL LIMIT/OFFSET/TAKE/DROP counts go through a JS `Number`: exponent notation and rounding in the SQL text**

  Source target: `translator.mjs:2670` (`return Number(d.digits)`), `:2535`, `:2545-2551`, `:2991-3003` (template literals).

  Implementation starting point: keep `d.digits` as BigInt/string for the count and use it in arithmetic only under `MAX_SAFE_INTEGER`, or refuse counts above a fixed cap in all hosts.

  Regression prerequisite: [T09 / JS-C55](tests/09-sql-kinds.md#js-c55).

  **[JS-C56](../js-code-review.md) — [low] [confirmed] SQL TAKE/DROP counts with a written scale are refused although SEL accepts them**

  Source target: `translator.mjs:2664-2666` (`d.scale !== 0`).

  Implementation starting point: strip trailing fractional zeros (or reuse the evaluator's `nonNegInt`) before testing integrality.

  Regression prerequisite: [T09 / JS-C56](tests/09-sql-kinds.md#js-c56).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## SQL bindings, dialects, fragments and rendering

<a id="c-js-c24"></a>

- [ ] **C-JS-C24 — Resolve JS-C24, JS-C25.** Coordinate the related changes below at their shared implementation surface.

  **[JS-C24](../js-code-review.md) — [medium] [confirmed] `checkNumericGuard` memoises the dialect before it checks, so a failing check is skipped on every later call**

  Source target: `js/src/sql/map.mjs:227-252` (`guardChecked.add(dialect)` at 229 precedes the `throw`s at 235-250).

  Implementation starting point: `guardChecked.add(dialect)` only after all three checks pass (or delete it in a catch).

  Regression prerequisite: [T10 / JS-C24](tests/10-sql-rendering.md#js-c24).

  **[JS-C25](../js-code-review.md) — [medium] [confirmed] A registered dialect whose `textEscape` map does not escape the quote is accepted and produces injectable inline literals**

  Source target: `map.mjs:372-397` (`checkLexical`, `types[key] === 'map'` branch) with `emit.mjs:132-159` (`textLiteral`).

  Implementation starting point: after resolving the effective lexical set (in `defineDialect` and when a runtime `lexical` is read), require that `textLiteral` of the quote character round-trips to a string that cannot terminate the literal, and that `identEscape` contains `identQuote` twice or is a recognised escape.

  Regression prerequisite: [T10 / JS-C25](tests/10-sql-rendering.md#js-c25).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c28"></a>

- [ ] **C-JS-C28 — Resolve JS-C28.** A `Binding.raw` field is dropped by SELECT_COLS and by derived-table wrapping, giving SQL that names a nonexistent column

  **[JS-C28](../js-code-review.md) — [medium] [confirmed] A `Binding.raw` field is dropped by SELECT_COLS and by derived-table wrapping, giving SQL that names a nonexistent column**

  Source target: `translator.mjs:2855-2856` (`const column = fSpec?.column ?? col`), `:2168-2170` (`wrapPlanAsDerivedTable`: `column: sourceField?.column ?? name`), `:2840-2863`.

  Implementation starting point: refuse (E_SQL_UNSUPPORTED) when a raw field is selected or would be read across a wrap, or project `<raw> AS <key>` in the inner select.

  Regression prerequisite: [T10 / JS-C28](tests/10-sql-rendering.md#js-c28).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c57"></a>

- [ ] **C-JS-C57 — Resolve JS-C57.** SQL: rule-supplied identifiers are not validated like binding-supplied ones; SELECT_COLS bypasses the field allow-list on a relation with no declared fields

  **[JS-C57](../js-code-review.md) — [low] [confirmed] SQL: rule-supplied identifiers are not validated like binding-supplied ones; SELECT_COLS bypasses the field allow-list on a relation with no declared fields**

  Source target: `translator.mjs:2832` (`AS ident(proj.alias)`), `:2451-2468`, `:2840-2856`; `binding.mjs:213` (`checkName` applies to bindings only).

  Implementation starting point: run rule-supplied names through the same empty/NUL check (E_SQL_SHAPE); decide whether an empty `fields` map means "anything" or "nothing", consistently for `_[...]` too.

  Regression prerequisite: [T10 / JS-C57](tests/10-sql-rendering.md#js-c57).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## Hybrid execution, keys, order and context

<a id="c-js-c22"></a>

- [ ] **C-JS-C22 — Resolve JS-C22.** Hybrid planner: a SQL prefix ending in a FILTER loses FILTER's retained keys; the continuation sees SQL row ordinals

  **[JS-C22](../js-code-review.md) — [medium] [confirmed] Hybrid planner: a SQL prefix ending in a FILTER loses FILTER's retained keys; the continuation sees SQL row ordinals**

  Source target: `js/src/sql/hybrid.mjs:690-716` (prefix search loop; the only key guard is inside `tryPlanFallthrough` via `FALLTHROUGH_DOWNSTREAM`, line 237); `executeHybrid:721-734` binds DB rows as a fresh 1..n list.

  Implementation starting point: when choosing a split point, back off to before the FILTER if the prefix keeps FILTER-retained keys after its last renumbering step and the continuation is key-visible (its steps include a FILTER, or any body reads `_K`). In the fallthrough, reject custom pairs that read `_K` when any FILTER precedes the MAP.

  Regression prerequisite: [T11 / JS-C22](tests/11-hybrid.md#js-c22).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c23"></a>

- [ ] **C-JS-C23 — Resolve JS-C23.** Hybrid fallthrough (partially local MAP) with downstream TAKE/DROP hides errors the custom half raises on rows the LIMIT cuts off

  **[JS-C23](../js-code-review.md) — [medium] [confirmed] Hybrid fallthrough (partially local MAP) with downstream TAKE/DROP hides errors the custom half raises on rows the LIMIT cuts off**

  Source target: `hybrid.mjs:237` (`FALLTHROUGH_DOWNSTREAM = SORT_BY, TOP_BY, TAKE, DROP`) and `:314-319`.

  Implementation starting point: allow TAKE/DROP downstream only when the custom half provably cannot raise (reuse `cannotRaise`), otherwise put the split before the MAP (the general prefix loop already does that correctly).

  Regression prerequisite: [T11 / JS-C23](tests/11-hybrid.md#js-c23).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c59"></a>

- [ ] **C-JS-C59 — Resolve JS-C59.** Pure-SQL bucket drops an explicit SORT_BY that fixes the group order

  **[JS-C59](../js-code-review.md) — [low] [confirmed, SQLite only] Pure-SQL bucket drops an explicit SORT_BY that fixes the group order**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: order groups by MIN of a row sequence derived from the sort (as the latest-member strategy does) or classify sort-then-bucket as not pushable.

  Regression prerequisite: [T11 / JS-C59](tests/11-hybrid.md#js-c59).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c60"></a>

- [ ] **C-JS-C60 — Resolve JS-C60.** `executeHybrid` treats the context differently for hybrid versus pure-memory plans

  **[JS-C60](../js-code-review.md) — [low] [confirmed] `executeHybrid` treats the context differently for hybrid versus pure-memory plans**

  Source target: `hybrid.mjs:723` versus `:730`.

  Implementation starting point: document, or build a shallow root (top-level entries aliased) and copy only what the continuation assigns.

  Regression prerequisite: [T11 / JS-C60](tests/11-hybrid.md#js-c60).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## Host integration, concurrency and tooling

<a id="c-js-c29"></a>

- [ ] **C-JS-C29 — Resolve JS-C29.** Public entry points leak host exceptions for non-string input

  **[JS-C29](../js-code-review.md) — [low] [confirmed] Public entry points leak host exceptions for non-string input**

  Source target: `js/src/lexer.mjs:36-39`, `utf8.mjs:15-17`, `js/src/sel.mjs:140-142` (`compile`), `js/bin/sel.mjs:36-42`.

  Implementation starting point: `if (typeof source !== 'string') badArg(...)` in `compile`/`Lexer`; print usage in the CLI when `-e` has no operand.

  Regression prerequisite: [T12 / JS-C29](tests/12-integration.md#js-c29).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-js-c40"></a>

- [ ] **C-JS-C40 — Resolve JS-C40.** `Program.dependencies()` is order-insensitive and drops variables read before, or only conditionally after, an assignment

  **[JS-C40](../js-code-review.md) — [low] [confirmed] `Program.dependencies()` is order-insensitive and drops variables read before, or only conditionally after, an assignment**

  Source target: `js/src/sel.mjs:50-55` (`reads` minus `assigned`, whole-program sets), comment at :47-49; spec §8 line 1088.

  Implementation starting point: track definite assignment in source order (an assignment counts only if it dominates the read); or correct the spec sentence and document "may under-report".

  Regression prerequisite: [T12 / JS-C40](tests/12-integration.md#js-c40).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.
