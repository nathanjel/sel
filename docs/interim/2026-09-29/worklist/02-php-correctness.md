# PHP correctness

[Worklist](README.md) · [Shared tests](00-tests.md) · [Performance](07-performance.md)

58 findings; 45 implementation tasks. Report severity/confidence is preserved below. Work through one source area at a time; a grouped checkbox closes only when every listed finding is resolved.

Each task starts by reproducing the report against the current tree, then lands the linked shared regression cases before the fix. Fix sketches are starting points, not approved spec changes. Prefer one implementation source per task; touching spec, fixtures or an unavoidable API boundary is allowed. Do not edit generated artifacts without their generator. High-severity tasks take precedence within each area.

## Front end, depth and source positions

<a id="c-php-c5"></a>

- [x] **C-PHP-C5 — Resolve PHP-C5.** Nested string interpolation makes the lexer quadratic or worse: a 12.8 KB source costs 9 s, 40 KB does not finish in 300 s

  **[PHP-C5](../php-code-review.md) — [high] [confirmed; re-verified by synthesizer as super-linear] Nested string interpolation makes the lexer quadratic or worse: a 12.8 KB source costs 9 s, 40 KB does not finish in 300 s**

  Source target: `php/src/Lexer.php:231-267` (`lexQuoted`), `314-351` (`matchBrace`), `353-373` (`skipQuoted`), `401-425` (`emitParts` -> `lexRange`).

  Implementation starting point: memoise `matchBrace($i)` and `skipQuoted($j)` results by start index in two per-Lexer arrays (a successful scan of a start offset is independent of the caller; errors throw and are not cached). Prototype by the reviewer (LexerM.php): d=3200 9.4 s -> 0.08 s, d=12800 0.39 s, token stream byte-identical (serialize+md5 equal at d=400/1600/3200). Counting interpolation nesting in the lexer would change which error wins, so memoisation is the byte-identical fix.

  Regression prerequisite: [T01 / PHP-C5](tests/01-frontend.md#php-c5).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — old-vs-new lexer differential 0 diffs per host (100k-300k random strings); 100k-level nest in <2s on every host

<a id="c-php-c14"></a>

- [ ] **C-PHP-C14 — Resolve PHP-C14.** Assignment-target chains cost O(n^2): `array_unshift` in a loop, 120 KB source takes ~10 s

  **[PHP-C14](../php-code-review.md) — [medium] [confirmed; re-verified by synthesizer] Assignment-target chains cost O(n^2): `array_unshift` in a loop, 120 KB source takes ~10 s**

  Source target: `php/src/Evaluator.php:497` (`resolveTarget`: `array_unshift($chain, $n['idx'])`); the E_DEPTH check is at `:515`, after the loop.

  Implementation starting point: `$chain[] = $n['idx']; ... $chain = array_reverse($chain);`; better, check chain length against MAX_DEPTH while walking and stop. Reviewer measured with that patch on a scratch copy: 40000 goes 9.6 s -> 1.5 s (the rest is the parser).

  Regression prerequisite: [T01 / PHP-C14](tests/01-frontend.md#php-c14).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c15"></a>

- [x] **C-PHP-C15 — Resolve PHP-C15.** A failed parse of a very long flat chain still segfaults PHP: several throw sites do not dismantle the partial tree

  **[PHP-C15](../php-code-review.md) — [medium] [confirmed; not re-verified] A failed parse of a very long flat chain still segfaults PHP: several throw sites do not dismantle the partial tree**

  Source target: `php/src/Parser.php:274-282` (parseProgram trailing-token check), `419-425` (comparison-chain error; `$right` local), `495-505` (parsePostfix: `$idx` when `expectOp(']')` fails), `525-538` and `617-632` (parseCall/parsePipeStep: `$inner`/`$args` when expectOp(')'), E_UNKNOWN_FUNC or E_ARITY fires), `596-606` (parenthesised primary: `$inner`).

  Implementation starting point: wrap each site in the same try/catch-dismantle idiom, or a small helper that dismantles a list of local nodes: `$node` in parseProgram, `$right`/`$left` in the N branch, `$idx`/`$node` in parsePostfix, `$inner`/`$args` in parseCall/parsePipeStep/parsePrimary, `$args` (+ `$left` for pipe steps) in finishCall.

  Regression prerequisite: [T01 / PHP-C15](tests/01-frontend.md#php-c15).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

<a id="c-php-c16"></a>

- [x] **C-PHP-C16 — Resolve PHP-C16.** Right-associative `??` / `???` chains are not depth-counted (spec 6.4)

  **[PHP-C16](../php-code-review.md) — [medium] [confirmed; re-verified by synthesizer] Right-associative `??` / `???` chains are not depth-counted (spec 6.4)**

  Source target: `php/src/Parser.php:409-413` (`if ($assoc === 'R') { $right = $this->parseTerm($bp); ... }`).

  Implementation starting point: in the `'R'` (non-assignment) branch wrap the recursive parseTerm in `enter($t)`/try/finally `leave()` as the assignment branch does, in all hosts; pin the cost in spec 6.4 plus a `lim.coalesce-depth` case (existing pins are unaffected).

  Regression prerequisite: [T01 / PHP-C16](tests/01-frontend.md#php-c16).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts enter()/leave() in the R-assoc branch; conformance 1120/1120 on js, js-bundle-min, php, cpp, lisp, go, python

<a id="c-php-c42"></a>

- [x] **C-PHP-C42 — Resolve PHP-C42.** E_UTF8 for invalid source carries no position

  **[PHP-C42](../php-code-review.md) — [low] [confirmed] E_UTF8 for invalid source carries no position**

  Source target: `php/src/Lexer.php:46` (`Utf8::validate($source)` with no `$pos`), `Utf8.php:21-65`.

  Implementation starting point: `validate()` throws with (or returns) the byte offset; convert by counting code points in the valid prefix and line/col by counting `"\n"`; decide the spec question (offset of the first bad byte in code points of the valid prefix) and pin it. Needs a host-API test, not a `.selt`.

  Regression prerequisite: [T01 / PHP-C42](tests/01-frontend.md#php-c42).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — check-cli-source.sh passes on all six hosts

<a id="c-php-c43"></a>

- [x] **C-PHP-C43 — Resolve PHP-C43.** `strtoupper` / `strtolower` / `strcasecmp` are locale-dependent on the PHP versions `composer.json` still allows

  **[PHP-C43](../php-code-review.md) — [low] [unconfirmed - no PHP 8.1] `strtoupper` / `strtolower` / `strcasecmp` are locale-dependent on the PHP versions `composer.json` still allows**

  Source target: `Lexer.php:154`; `Registry.php:26,100,160,198,207`; `Optimizer.php:539,540,660,679,683`; `Sql/Bindings.php:38-53`, `Sql/Binding.php:254-260`, `Hybrid.php` (many), `Map.php:164,549,677`; `Regex.php:311`; `Core.php:268,276,281,411` (`strcasecmp`); `Value.php` (`RecordShape::alias`). `Hybrid.php:242` and `Constants.php:150` already use the explicit `strtr` alphabet.

  Implementation starting point: a shared ASCII-upper helper `strtr($s, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')`, or raise the floor to 8.2 in `composer.json` and the docs.

  Regression prerequisite: [T01 / PHP-C43](tests/01-frontend.md#php-c43).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — check-cli-source.sh passes on all six hosts

<a id="c-php-c44"></a>

- [x] **C-PHP-C44 — Resolve PHP-C44.** Error catalogue says E_RANGE and E_UTF8 are run-time-only, but the PHP front end raises both at compile time

  **[PHP-C44](../php-code-review.md) — [low] [confirmed] Error catalogue says E_RANGE and E_UTF8 are run-time-only, but the PHP front end raises both at compile time**

  Source target: `php/src/Limits.php:41-42` (generated from `spec/limits.json:30-31` `"E_UTF8"/"E_RANGE": {"phase": "run"}`); raised at `Lexer.php:46` (E_UTF8) and `:302` (E_RANGE for `\u{110000}` / surrogates); number literals past the digit cap raise E_RANGE from `Dec::parse` during compile.

  Implementation starting point: set phase to `both` for E_RANGE and E_UTF8 in `spec/limits.json` and list them in `errors.md`'s compile-time table.

  Regression prerequisite: [T01 / PHP-C44](tests/01-frontend.md#php-c44).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — node tools/gen-limits.mjs regenerated every host rendering

## Exact decimal arithmetic and limits

<a id="c-php-c17"></a>

- [x] **C-PHP-C17 — Resolve PHP-C17.** `mulAbs` limb accumulators overflow to float -> uncaught `TypeError`

  **[PHP-C17](../php-code-review.md) — [medium] [confirmed, no-GMP only] `mulAbs` limb accumulators overflow to float -> uncaught `TypeError`**

  Source target: `php/src/Dec.php:210-241` (7-digit limbs, `$acc[$i+$j] += $ai * $limbsB[$j]`, `intdiv($t, 10000000)` at `:225`).

  Implementation starting point: carry-normalise every ~9000 rows of `i` (or 6-digit limbs), or add Karatsuba (PHP-P5) whose base case has no such bound; wrap the fallback core so no host exception escapes.

  Regression prerequisite: [T02 / PHP-C17](tests/02-decimal.md#php-c17).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — php lanes green; tests fail on HEAD archive (27)

<a id="c-php-c40"></a>

- [x] **C-PHP-C40 — Resolve PHP-C40, PHP-C41.** Coordinate the related changes below at their shared implementation surface.

  **[PHP-C40](../php-code-review.md) — [low] [confirmed] `Dec::cmp` fast path ignores `nativeNeg`; `Dec::checked` trusts a supplied native cache**

  Source target: `php/src/Dec.php:686-691` (cmp), `:455-457` (checked); contrast `intMantissa` `:340-345` which validates both.

  Implementation starting point: `cmp` uses `intMantissa()` (already validates); `checked` always rebuilds via `make()` (~0.3 us) or compares `native` with `parseMantissa`. (The cost interacts with PHP-P12.)

  Regression prerequisite: [T02 / PHP-C40](tests/02-decimal.md#php-c40).

  **[PHP-C41](../php-code-review.md) — [low] [confirmed for un-normalised descriptors] ext-gmp paths parse digit strings with base auto-detection (octal)**

  Source target: `php/src/Dec.php:69, 116, 162, 282` (`gmp_add($a,$b)` etc. with plain strings).

  Implementation starting point: `gmp_init($s, 10)` or `ltrim` in the public entry points.

  Regression prerequisite: [T02 / PHP-C41](tests/02-decimal.md#php-c41).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — php lanes green; tests fail on HEAD archive (27)

## Value ownership, mutation and host conversion

<a id="c-php-c1"></a>

- [x] **C-PHP-C1 — Resolve PHP-C1, PHP-C10, PHP-C39.** Coordinate the related changes below at their shared implementation surface.

  **[PHP-C1](../php-code-review.md) — [high] [confirmed; re-verified by synthesizer] Shared `Value::bool()` flyweights are mutable; one program can poison `TRUE`/`FALSE` process-wide**

  Source target: `php/src/Value.php:265-273` (`Value::bool`, static `$valTrue/$valFalse`), `:684` (`set`), `:1091` (`fromNativeAt` -> `self::bool`); reached from `Evaluator.php` (`case 'bool'`, `evalUnary`, `evalBinary`).

  Implementation starting point: make the singletons immutable or copy-on-write (`set()` and the evaluator's assignment walk replace a flyweight root/intermediate with a copy before writing); simplest safe alternative: `fromNativeAt`, `Program::run` context intake and the public `Value::bool()` allocate fresh objects, and the flyweight becomes a private `sharedBool()` used only for internal results that are never stored (anything that can become a mutation target - the context root and children, the value returned from `run()` - must be fresh). A `LogicException` on `set()` of a flyweight is a further option. The same audit applies to plan item 1.7 (null/none flyweights).

  Regression prerequisite: [T03 / PHP-C1](tests/03-values.md#php-c1).

  **[PHP-C10](../php-code-review.md) — [medium] [confirmed; re-verified by synthesizer] `Value::num("007")` keeps the raw text (0.9.2 regression); the API parity probe fails for PHP on HEAD**

  Source target: `php/src/Value.php:290-296` (`new self(self::TEXT, $d)`; was `null` in 0.9.1; docblock at `:277` still says "007 becomes 7").

  Implementation starting point: `new self(self::TEXT, null)` (getScalar formats lazily from `decVal`, which `parse` canonicalised); or store `Dec::format($parsed)` instead of `$d`.

  Regression prerequisite: [T03 / PHP-C10](tests/03-values.md#php-c10).

  **[PHP-C39](../php-code-review.md) — [low] [confirmed] Malformed `Value` constructor calls: silent truncation or a host `TypeError` instead of `E_BAD_ARG`**

  Source target: `php/src/Value.php:299` (`int(int $n)`), `:236` (`text(string)`), `:260` (`bin(string)`), `:308` (`list(array)`), `:379` (`record`).

  Implementation starting point: drop scalar type declarations on public constructors (keep `mixed`), check `is_int/is_string/...` by hand and `fail('E_BAD_ARG', ..., null)`; give `bin` an `array` branch.

  Regression prerequisite: [T03 / PHP-C39](tests/03-values.md#php-c39).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — php lanes green; tests fail on HEAD archive (27)

<a id="c-php-c2"></a>

- [x] **C-PHP-C2 — Resolve PHP-C2.** `,` no longer copies what it collects; PHP disagrees with spec 3.4 and every other host

  **[PHP-C2](../php-code-review.md) — [high] [confirmed; re-verified by synthesizer] `,` no longer copies what it collects; PHP disagrees with spec 3.4 and every other host**

  Source target: `php/src/Evaluator.php:248-262` (`evalList`).

  Implementation starting point: restore `->copy($node['pos'])` for both branches (with its E_DEPTH position). If too costly, copy only when the item list has more than one item and a later item is not a pure literal or variable; copy first, optimise later.

  Regression prerequisite: [T03 / PHP-C2](tests/03-values.md#php-c2).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — php lanes green; tests fail on HEAD archive (27)

## Evaluator order, optimizer and recovery

<a id="c-php-c11"></a>

- [x] **C-PHP-C11 — Resolve PHP-C11.** FILTER+FILTER fusion treats a bare variable or literal predicate as "cannot raise", so the optimised program reports a different error

  **[PHP-C11](../php-code-review.md) — [medium] [confirmed; re-verified by synthesizer] FILTER+FILTER fusion treats a bare variable or literal predicate as "cannot raise", so the optimised program reports a different error**

  Source target: `php/src/Optimizer.php:463-484` (fusion) using `cannotRaise` at `:532-554` (`case 'var'`, literal cases return true).

  Implementation starting point: in the non-logical case only a literal `bool` counts as cannot-raise; `var` counts only when its static kind is known bool (never here). Simplest: `cannotRaise` returns true for `bool` literals only when `!$logical`.

  Regression prerequisite: [T04 / PHP-C11](tests/04-evaluation.md#php-c11).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts green

<a id="c-php-c12"></a>

- [x] **C-PHP-C12 — Resolve PHP-C12.** SORT/SORT_BY + TAKE fusion into TOP/TOP_BY drops key errors when the count is 0 and evaluates the count before the keys

  **[PHP-C12](../php-code-review.md) — [medium] [confirmed] SORT/SORT_BY + TAKE fusion into TOP/TOP_BY drops key errors when the count is 0 and evaluates the count before the keys**

  Source target: `php/src/Optimizer.php:391-404`.

  Implementation starting point: fuse only when the TAKE count is a literal integer > 0 (`numericLiteral() > 0`); or make TOP* with n=0 still evaluate every key and evaluate n after the keys (host-wide: spec plus five hosts).

  Regression prerequisite: [T04 / PHP-C12](tests/04-evaluation.md#php-c12).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts green

<a id="c-php-c13"></a>

- [x] **C-PHP-C13 — Resolve PHP-C13.** Math plan coerces each leaf operand at load time, so a coercion error on an earlier operand masks an error on a later one (the plain tree gives the opposite)

  **[PHP-C13](../php-code-review.md) — [medium] [confirmed; re-verified by synthesizer] Math plan coerces each leaf operand at load time, so a coercion error on an earlier operand masks an error on a later one (the plain tree gives the opposite)**

  Source target: `php/src/Evaluator.php:53-66` (`LOAD_VAR`/`LOAD_LEAF` call `asDecimal` immediately) vs `evalBinary` `:308-323` (evaluate both, then coerce).

  Implementation starting point: (a) `LOAD_*` store the raw `Value` and coerce at the consuming step in operand order (matches 6.2; near-free); or (b) declare the current order the spec and change `evalBinary`. Needs a spec sentence, a `.selt` case and a change in every host's plan compiler.

  Regression prerequisite: [T04 / PHP-C13](tests/04-evaluation.md#php-c13).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts 1600/1600; plain-vs-optimised 0 diffs

## Aggregates, ordering, grouping and joins

<a id="c-php-c22"></a>

- [x] **C-PHP-C22 — Resolve PHP-C22.** SORT/SORT_BY comparator is not a total order for mixed number-shaped and other text; hosts return different orders

  **[PHP-C22](../php-code-review.md) — [medium] [confirmed] SORT/SORT_BY comparator is not a total order for mixed number-shaped and other text; hosts return different orders**

  Source target: `php/src/Builtins/Core.php:207-239` (`compareValues`).

  Implementation starting point: define a total order in the spec and every host (rank first: NULL < BOOL < number-shaped < other TEXT < BIN, then compare within rank), or refuse the mix. A rank-first comparator also makes the decorated sort of PHP-P3 a single-key sort.

  Regression prerequisite: [T05 / PHP-C22](tests/05-relational.md#php-c22).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-php-c24"></a>

- [x] **C-PHP-C24 — Resolve PHP-C24, PHP-C25, PHP-C46.** Coordinate the related changes below at their shared implementation surface.

  **[PHP-C24](../php-code-review.md) — [medium] [confirmed; re-verified by synthesizer] BUCKET reports `_K` as an invented counter for an element whose record key is `""`**

  Source target: `php/src/Builtins/Structure.php:1756` (`Value::text($key === '' ? (string) (++$index) : $key)`).

  Implementation starting point: bind `Value::text($key)` unconditionally and drop `$index`; `forEachElement` already yields the true key.

  Regression prerequisite: [T05 / PHP-C24](tests/05-relational.md#php-c24).

  **[PHP-C25](../php-code-review.md) — [medium] [confirmed; re-verified by synthesizer] BUCKET of a scalar returns empty instead of grouping the one-element list the scalar stands for**

  Source target: `php/src/Builtins/Structure.php:1724` (`$value->isNull() || $value->size() === 0`).

  Implementation starting point: test `$value->kind === Value::NONE && $value->size() === 0` instead of `size() === 0`; the rest already handles scalars through `forEachElement`. Fix other hosts in the same change.

  Regression prerequisite: [T05 / PHP-C25](tests/05-relational.md#php-c25).

  **[PHP-C46](../php-code-review.md) — [low] [confirmed, cross-host] Bare BUCKET silently drops a group when two EQL-distinct keys spell the same text**

  Source target: `php/src/Builtins/Structure.php:1765-1791`.

  Implementation starting point: merge groups by `keyString` in the bare spelling, or refuse a key that has children with E_NOT_TEXT. Spec first.

  Regression prerequisite: [T05 / PHP-C46](tests/05-relational.md#php-c46).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-php-c26"></a>

- [ ] **C-PHP-C26 — Resolve PHP-C26.** Joined rows hold each binder row under three keys, so deep copy / hash / EQL / dump blow up exponentially with join depth

  **[PHP-C26](../php-code-review.md) — [medium] [confirmed, cross-host design] Joined rows hold each binder row under three keys, so deep copy / hash / EQL / dump blow up exponentially with join depth**

  Source target: `makeJoinedRow` (`Structure.php:505-547`), `rowPlan` (`:557-594`): left row under `A`, `a`, `_1`; right under `B`, `b`, `_2`; each row itself carries alias entries. `copy()`, `structuralHash()`, `eqlAt()`, `dump()` walk the shared PHP objects as a tree.

  Implementation starting point: spec decision (e.g. do not store binder rows under three keys, or cap size/work). Host-only mitigation: memoise `copyAt` / `updateStructuralHash` per shared object within one traversal - safe for hashing and equality, not for copy (must yield unshared results).

  Regression prerequisite: [T05 / PHP-C26](tests/05-relational.md#php-c26).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c47"></a>

- [ ] **C-PHP-C47 — Resolve PHP-C47.** The BUCKET table row `BUCKET(list, [binder,] key)` implies a 3-argument binder+key form that no host has

  **[PHP-C47](../php-code-review.md) — [low] [confirmed; spec/doc issue, all hosts agree] The BUCKET table row `BUCKET(list, [binder,] key)` implies a 3-argument binder+key form that no host has**

  Source target: `spec/SPEC.md` line 673 vs `Structure.php:1729-1737` (3 arguments = key + proj; binder only with 4).

  Implementation starting point: fix the spec row and `docs/functions.md` (or add a bare-identifier disambiguation on all hosts). A bare bucket with a custom binder cannot be written today.

  Regression prerequisite: [T05 / PHP-C47](tests/05-relational.md#php-c47).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## Portable regex semantics and resource failures

<a id="c-php-c3"></a>

- [x] **C-PHP-C3 — Resolve PHP-C3.** PCRE resource errors (backtrack / recursion / JIT stack limit) become silently wrong results

  **[PHP-C3](../php-code-review.md) — [high] [confirmed; re-verified by synthesizer] PCRE resource errors (backtrack / recursion / JIT stack limit) become silently wrong results**

  Source target: `php/src/Builtins/Regex.php:403, 410, 419, 442` (every `preg_*` call; none checks `preg_last_error()`).

  Implementation starting point: after each `preg_*` call test `preg_last_error() !== PREG_NO_ERROR` (or `=== false`) and raise a SEL error. That needs a catalogued code (`spec/errors.md`, `spec/limits.json`, `tools/check-error-codes.sh`), e.g. a run-time `E_REGEX_LIMIT`; a spec decision. Optionally retry once with different limits. `RREPLACE` must distinguish `false` from "zero matches".

  Regression prerequisite: [T06 / PHP-C3](tests/06-regex.md#php-c3).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-php-c4"></a>

- [x] **C-PHP-C4 — Resolve PHP-C4.** PCRE backtracking verbs `(*FAIL)`, `(*ACCEPT)`, `(*COMMIT)`, `(*UTF8)` are accepted and change matching

  **[PHP-C4](../php-code-review.md) — [high] [confirmed; re-verified by synthesizer] PCRE backtracking verbs `(*FAIL)`, `(*ACCEPT)`, `(*COMMIT)`, `(*UTF8)` are accepted and change matching**

  Source target: `php/src/Builtins/Regex.php:132-148` (`(` handling) then `:156` (`*` treated as a quantifier).

  Implementation starting point: in the `(` branch reject when the next code point is `*` (and `+`), with the "this group type is not portable" message; more generally reject any `(` followed by a quantifier character.

  Regression prerequisite: [T06 / PHP-C4](tests/06-regex.md#php-c4).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-php-c18"></a>

- [ ] **C-PHP-C18 — Resolve PHP-C18, PHP-C19, PHP-C20, PHP-C21, PHP-C45.** Coordinate the related changes below at their shared implementation surface.

  **[PHP-C18](../php-code-review.md) — [medium] [confirmed] Class escapes used as range endpoints are expanded into a wrong range (`[+-\d]`, `[\s-x]`, `[\w-a]`)**

  Source target: `php/src/Builtins/Regex.php:271-275` (EXPAND_INSIDE substitution in `validateClass`).

  Implementation starting point: in `validateClass` track whether the previous item was a class escape and whether the next non-`]` char is an unescaped `-` (or the escape follows a `-`); reject with E_REGEX_SYNTAX ("class escape cannot be a range endpoint") as ES u-mode does. All hosts, spec and case first.

  Regression prerequisite: [T06 / PHP-C18](tests/06-regex.md#php-c18).

  **[PHP-C19](../php-code-review.md) — [medium] [confirmed] `[a[:alpha:]`, `[a[.b.]`, `[a[=b=]`: PHP raises E_REGEX_SYNTAX where every other host matches**

  Source target: `php/src/Builtins/Regex.php:250-252` (POSIX-class check runs only at the very start of a class).

  Implementation starting point: reject an unescaped `[` inside a class when followed by `:`, `.` or `=` (not only at class start), or emit `\[` for every literal `[` in a class (valid in both engines).

  Regression prerequisite: [T06 / PHP-C19](tests/06-regex.md#php-c19).

  **[PHP-C20](../php-code-review.md) — [medium] [confirmed; re-verified by synthesizer] RREPLACE with a pattern that can match empty: PHP (and C++) retry non-empty at the same position, JS/Python/Lisp advance by one**

  Source target: `php/src/Builtins/Regex.php:442-447` (`preg_match_all`).

  Implementation starting point: spec picks one (ES/Python is the majority; "advance one code point after an empty match" is simple). In PHP replace `preg_match_all` with a loop of `preg_match($re, $s, $m, PREG_OFFSET_CAPTURE|PREG_UNMATCHED_AS_NULL, $offset)`, copying one code point after an empty match at byte p, keeping full-subject calls so `^`/lookbehind context is right. Also fixes PHP-C23 (RREPLACE memory) and PHP-P18.

  Regression prerequisite: [T06 / PHP-C20](tests/06-regex.md#php-c20).

  **[PHP-C21](../php-code-review.md) — [medium] [confirmed] Capture groups inside a quantified group: PCRE keeps the last participating value, ECMAScript resets each iteration**

  Source target: `php/src/Builtins/Regex.php:416-429, 433-464` (engine behaviour; no rewrite possible except by refusal).

  Implementation starting point: spec decision: refuse capturing groups under a quantifier (portable-subset rule, E_REGEX_SYNTAX; cheapest, matches how `\b` was handled) or specify ES semantics and emulate (hard).

  Regression prerequisite: [T06 / PHP-C21](tests/06-regex.md#php-c21).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[PHP-C45](../php-code-review.md) — [low] [confirmed] PCRE structural limits make valid portable patterns E_REGEX_SYNTAX in PHP only**

  Source target: `php/src/Builtins/Regex.php:346-349` ("PCRE rejected").

  Implementation starting point: add nesting depth (and maybe total repeat size) to the spec's portable limits so every host rejects the same patterns; count group nesting in `validate()`.

  Regression prerequisite: [T06 / PHP-C45](tests/06-regex.md#php-c45).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## Text, binary and output budgets

<a id="c-php-c6"></a>

- [x] **C-PHP-C6 — Resolve PHP-C6.** REPEAT / PADL / PADR (and exponential value growth) have no size cap: PHP dies with an uncatchable "Allowed memory size exhausted" fatal

  **[PHP-C6](../php-code-review.md) — [high] [confirmed] REPEAT / PADL / PADR (and exponential value growth) have no size cap: PHP dies with an uncatchable "Allowed memory size exhausted" fatal**

  Source target: `php/src/Builtins/Text.php:156-159` (`str_repeat`), `230-249` (`pad()` builds a PHP array with one element per pad character); `Evaluator::evalAssign` `->copy()`, `evalList`.

  Implementation starting point: spec adds a result-length cap for REPEAT/PADL/PADR (E_RANGE above N code points, stated in `spec/limits.json`), ideally also an element/byte cap for `,`/copy. In PHP build padding with `str_repeat` plus a code-point-aware slice (see PHP-P1). Until then document that `memory_limit` is the guard.

  Regression prerequisite: [T07 / PHP-C6](tests/07-text-binary.md#php-c6).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-php-c23"></a>

- [x] **C-PHP-C23 — Resolve PHP-C23.** Memory-limit walls at ordinary sizes: per-code-point arrays, RREPLACE match arrays, hash-array tokens

  **[PHP-C23](../php-code-review.md) — [medium] [confirmed] Memory-limit walls at ordinary sizes: per-code-point arrays, RREPLACE match arrays, hash-array tokens**

  Source target: `php/src/Builtins/Text.php` (LEN 51, LEFT 55, RIGHT 60, SUBSTR 67, FIND 81-82, REPLACE 99-101, SPLIT 120-121, TRIM 188, BACKWARDS 153, pad 232, CODE 177, asciiCase 213) all via `Utf8::chars()`; `Regex::escapeDelimiter/validate`; `Regex.php:442-447`; `Lexer.php:107,144,154,170` and Parser node arrays.

  Implementation starting point: (a) byte-level counting/slicing (PHP-P1); (b) streaming `preg_match` loop (PHP-C20) or a constant-memory `preg_replace_callback`; (c) token as small final class or packed list (PHP-P11) and a source-size/token-count guard turning "fatal" into a SelError.

  Regression prerequisite: [T07 / PHP-C23](tests/07-text-binary.md#php-c23).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## SQL lexical scope, normalization and expansion

<a id="c-php-c7"></a>

- [ ] **C-PHP-C7 — Resolve PHP-C7.** `SUM(g, body)` inside a BUCKET projection emits parameter slot numbers instead of literals

  **[PHP-C7](../php-code-review.md) — [high] [confirmed; not re-verified] `SUM(g, body)` inside a BUCKET projection emits parameter slot numbers instead of literals**

  Source target: `php/src/Sql/Translator.php:708` (`implode('', $inner->parts)`).

  Implementation starting point: splice: `new Fragment(array_merge(['COALESCE(SUM('], $inner->parts, ['), 0)']), 'NUM', $this->dialect, ...)`, carrying `$inner`'s caveats. Same fix in every host.

  Regression prerequisite: [T08 / PHP-C7](tests/08-sql-scope.md#php-c7).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c29"></a>

- [ ] **C-PHP-C29 — Resolve PHP-C29.** A static-list element that mentions `_K` is evaluated in the inner aggregate's scope

  **[PHP-C29](../php-code-review.md) — [medium] [confirmed] A static-list element that mentions `_K` is evaluated in the inner aggregate's scope**

  Source target: `php/src/Sql/Translator.php:1965-1967` with `:2021-2035` (`withElement` always binds `_K`), `:1544-1548` (`fromBinder` NODE renders the element node lazily in the current frame).

  Implementation starting point: resolve the element's free names in the frame stack as it was when the list was written (capture depth in `Binder::node`, render with frames truncated to it), or substitute `_K`/binder name before pushing the frame; failing that, refuse such elements.

  Regression prerequisite: [T08 / PHP-C29](tests/08-sql-scope.md#php-c29).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c30"></a>

- [ ] **C-PHP-C30 — Resolve PHP-C30.** A FILTER's binder name is bound in the aggregate body's scope

  **[PHP-C30](../php-code-review.md) — [medium] [confirmed] A FILTER's binder name is bound in the aggregate body's scope**

  Source target: `php/src/Sql/Translator.php:2026-2028` (`withElement`: `$frame[$filter['binder']] = $elem`) and `:2087-2089` (`withRow`).

  Implementation starting point: render each filter predicate in a frame holding only its own binder (and `_K`), and the body in a frame holding only the body binder.

  Regression prerequisite: [T08 / PHP-C30](tests/08-sql-scope.md#php-c30).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c32"></a>

- [ ] **C-PHP-C32 — Resolve PHP-C32.** No bound on generated-SQL size: 20 short assignments produce 8 MB / 17 s (2^n blow-up), also in hybrid planning

  **[PHP-C32](../php-code-review.md) — [medium] [confirmed] No bound on generated-SQL size: 20 short assignments produce 8 MB / 17 s (2^n blow-up), also in hybrid planning**

  Source target: `php/src/Sql/Normalise.php:25-33, 154-160` and `:158-232` (`substitute` inlines every read of a defined name and returns `$defs[name]` verbatim, no size accounting); `Translator` renders every use. No budget anywhere.

  Implementation starting point: charge every rendered node against a budget and refuse past it (new `E_SQL_SIZE` / `E_SQL_TOO_LARGE` / E_SQL_UNSUPPORTED, added to `spec/limits.json`), or in stage 1 refuse / leave local a non-leaf helper read more than once when the expanded node count exceeds a limit; or share subtrees by emitting a reused variable once.

  Regression prerequisite: [T08 / PHP-C32](tests/08-sql-scope.md#php-c32).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c34"></a>

- [ ] **C-PHP-C34 — Resolve PHP-C34.** Helper that rebinds a relation name is applied twice (`ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)`)

  **[PHP-C34](../php-code-review.md) — [medium] [confirmed] Helper that rebinds a relation name is applied twice (`ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)`)**

  Source target: `php/src/Sql/Hybrid.php:136` (`unwindThroughHelpers`, 845-858) with `917-922` (`withHelpers`) / `889-907`.

  Implementation starting point: after unwinding through `defs[N]` where N is also read by the result, drop that assignment from `$leading`, or refuse to unwind when a def's source is its own name.

  Regression prerequisite: [T08 / PHP-C34](tests/08-sql-scope.md#php-c34).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## SQL kinds, numeric fidelity and server limits

<a id="c-php-c27"></a>

- [ ] **C-PHP-C27 — Resolve PHP-C27, PHP-C28, PHP-C31.** Coordinate the related changes below at their shared implementation surface.

  **[PHP-C27](../php-code-review.md) — [medium] [confirmed] Group-path `SUM(g, body)` skips every kind check the relation path applies**

  Source target: `php/src/Sql/Translator.php:702-709` (vs `aggBody`, `:1993-2011`, which calls `requireNum`).

  Implementation starting point: route the body through `requireNum()` and the same constant check (`Constants::requireNumeric`), i.e. reuse `aggBody` instead of duplicating.

  Regression prerequisite: [T09 / PHP-C27](tests/09-sql-kinds.md#php-c27).

  **[PHP-C28](../php-code-review.md) — [medium] [confirmed; cross-host design] Kind unification launders UNKNOWN into NUM/BOOL, so numeric and boolean guards are skipped**

  Source target: `php/src/Sql/Translator.php:2576-2593` (`unify`), used by `conditional()` (`:1435`) and `@unify` in `retKind` (`:2544`) for `??`, `???`, COALESCE.

  Implementation starting point: `unify` returns UNKNOWN whenever any branch is UNKNOWN (still may refuse two conflicting known kinds); typed branches stay typed.

  Regression prerequisite: [T09 / PHP-C28](tests/09-sql-kinds.md#php-c28).

  **[PHP-C31](../php-code-review.md) — [medium] [confirmed] JOIN over a static list or columns binding bypasses the `&` BOOL guard**

  Source target: `php/src/Sql/Translator.php:2279-2291` (`joinAggregate`, `foldPairwise('&')`); guard lives only in `binary()` `:482-485`.

  Implementation starting point: in `joinAggregate` run `requireNotBoolOperand` on each element fragment and on the separator (and the relation-branch `sep`).

  Regression prerequisite: [T09 / PHP-C31](tests/09-sql-kinds.md#php-c31).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c33"></a>

- [ ] **C-PHP-C33 — Resolve PHP-C33.** Text-literal operands of arithmetic are emitted as bare quoted strings, which MariaDB/MySQL evaluate as DOUBLE

  **[PHP-C33](../php-code-review.md) — [medium] [unconfirmed: needs a MariaDB/MySQL server] Text-literal operands of arithmetic are emitted as bare quoted strings, which MariaDB/MySQL evaluate as DOUBLE**

  Source target: `php/src/Sql/Translator.php:465-479` and `:2636-2649` (`guardNumeric` returns constants unchanged); template `({0} + {1})` in `sql/dialects/mysql-family.json`.

  Implementation starting point: render a constant text operand in a numeric position through `Emit::numericLiteral` (exact, canonical) or `numericCast`.

  Regression prerequisite: [T09 / PHP-C33](tests/09-sql-kinds.md#php-c33).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c50"></a>

- [ ] **C-PHP-C50 — Resolve PHP-C50.** TAKE/DROP counts written as whole numbers with a scale are refused with E_NOT_INT by the translator

  **[PHP-C50](../php-code-review.md) — [low] [confirmed, all hosts] TAKE/DROP counts written as whole numbers with a scale are refused with E_NOT_INT by the translator**

  Source target: `php/src/Sql/Translator.php:3639-3641` (`evalIntParam`).

  Implementation starting point: accept a value whose fraction is zero; all hosts.

  Regression prerequisite: [T09 / PHP-C50](tests/09-sql-kinds.md#php-c50).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c51"></a>

- [ ] **C-PHP-C51 — Resolve PHP-C51.** Over-refusals from stale "measured" argument-kind tables

  **[PHP-C51](../php-code-review.md) — [low] [confirmed] Over-refusals from stale "measured" argument-kind tables**

  Source target: `php/src/Sql/Translator.php:1228-1231, 1278-1290` (`BIN_ARGUMENT_OK`, `BOOL_ARGUMENT_OK`).

  Implementation starting point: re-measure with the loop the comment describes and regenerate both sets.

  Regression prerequisite: [T09 / PHP-C51](tests/09-sql-kinds.md#php-c51).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c52"></a>

- [ ] **C-PHP-C52 — Resolve PHP-C52.** Numeric literals of more than 65 digits are emitted bare on MariaDB/MySQL with no caveat

  **[PHP-C52](../php-code-review.md) — [low] [unconfirmed] Numeric literals of more than 65 digits are emitted bare on MariaDB/MySQL with no caveat**

  Source target: `Emit::numericLiteral` (`php/src/Sql/Emit.php:105-138`), reached from `literal()`.

  Implementation starting point: add a `wide-literal` caveat or refuse over 65 digits for mysql-family.

  Regression prerequisite: [T09 / PHP-C52](tests/09-sql-kinds.md#php-c52).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c58"></a>

- [ ] **C-PHP-C58 — Resolve PHP-C58.** An IN list or unrolled aggregate of about 1000 or more elements translates but fails at run time

  **[PHP-C58](../php-code-review.md) — [low] [confirmed on SQLite] An IN list or unrolled aggregate of about 1000 or more elements translates but fails at run time**

  Source target: `php/src/Sql/Translator.php:635-663` (`inOperator`), `:674-682` (`foldPairwise`), documented choice in `sql-translation.md` 7.1 ("pairwise-left").

  Implementation starting point: beyond a per-dialect element count fold as a balanced tree (through the operator template), or refuse so the planner keeps it in memory. Byte-identity with other hosts affected only above the threshold. (Interacts with PHP-P8.)

  Regression prerequisite: [T09 / PHP-C58](tests/09-sql-kinds.md#php-c58).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## SQL bindings, dialects, fragments and rendering

<a id="c-php-c37"></a>

- [ ] **C-PHP-C37 — Resolve PHP-C37, PHP-C49.** Coordinate the related changes below at their shared implementation surface.

  **[PHP-C37](../php-code-review.md) — [medium] [confirmed] `Map::defineDialect` accepts quote/escape pairs that make text literals injectable**

  Source target: `php/src/Sql/Map.php:129-131, 491-537` (`checkLexical`) and `Emit.php:140-153` (`textLiteral`), `262-267` (`ident`).

  Implementation starting point: after resolving the effective lexical set require: `textEscape` has a key equal to `textQuote`; `identEscape` contains `identQuote`; and if any escape value introduces `\` then `\` itself is a key. Refuse with LogicException like the other checks.

  Regression prerequisite: [T10 / PHP-C37](tests/10-sql-rendering.md#php-c37).

  **[PHP-C49](../php-code-review.md) — [low] [confirmed] `Map::checkNumericGuard` marks the dialect as checked before validating it**

  Source target: `php/src/Sql/Map.php:331-337` (`self::$guardChecked[$dialect] = true;` before the checks that throw `LogicException`).

  Implementation starting point: set the flag only after all checks pass.

  Regression prerequisite: [T10 / PHP-C49](tests/10-sql-rendering.md#php-c49).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c53"></a>

- [ ] **C-PHP-C53 — Resolve PHP-C53, PHP-C54.** Coordinate the related changes below at their shared implementation surface.

  **[PHP-C53](../php-code-review.md) — [low] [confirmed] NUL characters reach the server (RECORD-key aliases, inline TEXT literals); small binding-validation gaps**

  Source target: `php/src/Sql/Emit.php:262-267` (`ident`), `:140-153` (`textLiteral`); `Binding::checkName` (`Binding.php:314`) guards binding names only; `Binding::column(..., $type)` `checkType`; `Translator::$inWhere` (set `:3914, :3922`, never read).

  Implementation starting point: `refuse('E_SQL_UNSUPPORTED')` for NUL in `ident()` and in inline `textLiteral`; restrict column types to NUM, TEXT, BOOL, BIN, UNKNOWN; remove `$inWhere`.

  Regression prerequisite: [T10 / PHP-C53](tests/10-sql-rendering.md#php-c53).

  **[PHP-C54](../php-code-review.md) — [low] [unconfirmed on server] RECORD-key aliases are not checked against server identifier rules**

  Source target: `php/src/Sql/Emit.php:262` (`ident`) fed by projection aliases.

  Implementation starting point: refuse (-> local plan) empty aliases and, for postgresql, aliases longer than 63 bytes or colliding after truncation.

  Regression prerequisite: [T10 / PHP-C54](tests/10-sql-rendering.md#php-c54).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c56"></a>

- [ ] **C-PHP-C56 — Resolve PHP-C56.** PostgreSQL text literals assume `standard_conforming_strings=on`

  **[PHP-C56](../php-code-review.md) — [low] [unconfirmed] PostgreSQL text literals assume `standard_conforming_strings=on`**

  Source target: MapData postgresql `textEscape` (`'` only); `Emit::textLiteral`.

  Implementation starting point: document it, or emit `E'...'` with backslash doubling for postgresql.

  Regression prerequisite: [T10 / PHP-C56](tests/10-sql-rendering.md#php-c56).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c57"></a>

- [ ] **C-PHP-C57 — Resolve PHP-C57.** Small API/validation inconsistencies in the SQL layer

  **[PHP-C57](../php-code-review.md) — [low] [confirmed] Small API/validation inconsistencies in the SQL layer**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: Found by: slice 7 C12, C15. - `Binding::column('a','t',['NUM'])` interpolates an array into the message ("Array to string conversion" warning; an ErrorException under a strict handler that `tryTranslate*` does not catch) - `Binding.php:329-332`; use `get_debug_type`. - `Hybrid::execute` returns whatever the runner returned for a pure_sql plan (native array stays an array) but a `Value` for hybrid and pure_memory (`Hybrid.php:944`). `Fragment::asValue('bogus')` is accepted when the fragment has no slots (`Fragment.php:228-238`). `new Bindings(['x'=>..., 'X'=>...])` and RELATION fields `a`/`A` silently collapse (last wins). `checkAliases` compares aliases case-sensitively although SQLite/MySQL identifiers are case-insensitive (unconfirmed).

  Regression prerequisite: [T10 / PHP-C57](tests/10-sql-rendering.md#php-c57).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## Hybrid execution, keys, order and context

<a id="c-php-c8"></a>

- [ ] **C-PHP-C8 — Resolve PHP-C8.** Hybrid split on a key-retaining FILTER loses SEL's row keys: continuation `_K` / result keys differ from pure memory

  **[PHP-C8](../php-code-review.md) — [high] [confirmed] Hybrid split on a key-retaining FILTER loses SEL's row keys: continuation `_K` / result keys differ from pure memory**

  Source target: `php/src/Sql/Hybrid.php:177-201` (prefix loop), `550-675` (`tryPlanFallthrough`), `readsWholeRow` at `491` ignores `_K`.

  Implementation starting point: track "last step kept the input keys" over the prefix and refuse the split unless the continuation begins with a renumbering step that does not read `_K`; treat `_K` var reads like a whole-row read in `readsWholeRow`.

  Regression prerequisite: [T11 / PHP-C8](tests/11-hybrid.md#php-c8).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c9"></a>

- [ ] **C-PHP-C9 — Resolve PHP-C9.** LINK in the hybrid continuation renames the left binder to `_INPUT` (spec 7.4 violation)

  **[PHP-C9](../php-code-review.md) — [high] [confirmed] LINK in the hybrid continuation renames the left binder to `_INPUT` (spec 7.4 violation)**

  Source target: `php/src/Sql/Hybrid.php:191, 617, 649` (`'name' => '_INPUT'`).

  Implementation starting point: do not split before a 3-argument LINK whose left has no earlier LINK, or bind the continuation source under the original relation name, or rewrite to the 5-argument form.

  Regression prerequisite: [T11 / PHP-C9](tests/11-hybrid.md#php-c9).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c35"></a>

- [ ] **C-PHP-C35 — Resolve PHP-C35.** Pure-SQL plans do not preserve SEL's order through SORT_BY .> LINK (and BUCKET group order)

  **[PHP-C35](../php-code-review.md) — [medium] [confirmed on SQLite; unconfirmed on MariaDB] Pure-SQL plans do not preserve SEL's order through SORT_BY .> LINK (and BUCKET group order)**

  Source target: classification `php/src/Sql/Hybrid.php:160-170`; root cause in `Translator.php` join/derived-table rendering (`:3561` `ensureDerived($plan, planHasRowsAbove)` for LINK, comment `:2947-2952`).

  Implementation starting point: carry the left ORDER BY to the outer statement of a join (or classify sort+LINK as non-pushable unless the outer statement re-sorts); for BUCKET add an ORDER BY reproducing first appearance (needs a row rank) or declare group order engine-defined; or refuse/keep in memory a LINK after an un-LIMITed sort.

  Regression prerequisite: [T11 / PHP-C35](tests/11-hybrid.md#php-c35).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c36"></a>

- [ ] **C-PHP-C36 — Resolve PHP-C36.** Hybrid/optimiser reorders and pushes steps ahead of local work, suppressing errors SEL would raise

  **[PHP-C36](../php-code-review.md) — [medium] [confirmed] Hybrid/optimiser reorders and pushes steps ahead of local work, suppressing errors SEL would raise**

  Source target: `php/src/Sql/Hybrid.php:482` (`FALLTHROUGH_DOWNSTREAM`), `583-595`, `641-643`; `Hybrid.php:140-143` (`Optimizer::optimize(..., false, $options)`, root cause in the Optimizer).

  Implementation starting point: restrict downstream steps to those that never drop rows (SORT_BY) or require the custom half to be provably total; otherwise fall to the ordinary prefix split. Keep the swap only when the sort key cannot fail (known column), or accept and document.

  Regression prerequisite: [T11 / PHP-C36](tests/11-hybrid.md#php-c36).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c55"></a>

- [ ] **C-PHP-C55 — Resolve PHP-C55.** `sourceTables` over-reports (binders and assignment targets named like a relation)

  **[PHP-C55](../php-code-review.md) — [low] [confirmed] `sourceTables` over-reports (binders and assignment targets named like a relation)**

  Source target: `php/src/Sql/Hybrid.php:314-338`.

  Implementation starting point: skip `target` of assignments and respect binder scope like `Normalise::substitute`.

  Regression prerequisite: [T11 / PHP-C55](tests/11-hybrid.md#php-c55).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## Host integration, concurrency and tooling

<a id="c-php-c38"></a>

- [ ] **C-PHP-C38 — Resolve PHP-C38.** `Program::dependencies()` is order-insensitive: a variable read before it is assigned is not reported

  **[PHP-C38](../php-code-review.md) — [low] [confirmed; re-verified by synthesizer] `Program::dependencies()` is order-insensitive: a variable read before it is assigned is not reported**

  Source target: `php/src/Sel.php:121-129` (`array_diff($reads, $assigned)`).

  Implementation starting point: track assignment order in `collect`, or change the comment/spec to "never assigned anywhere"; all hosts together.

  Regression prerequisite: [T12 / PHP-C38](tests/12-integration.md#php-c38).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-php-c48"></a>

- [ ] **C-PHP-C48 — Resolve PHP-C48.** Cosmetic: error message text differs from other hosts

  **[PHP-C48](../php-code-review.md) — [low] [confirmed] Cosmetic: error message text differs from other hosts**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: Found by: slice 2 C7, slice 5 C5. Codes and positions agree (messages are not contract). - `ROUND(1, 99999999999999999999)` reports "scale 9223372036854775807 exceeds the maximum" (JS/Python print the real number): `Dec.php:592-597` `toInt` saturates via `(int)"digits"` (re-verified). The saturation is correct for every in-range comparison tried; a future caller doing arithmetic on the clamp would turn it into a float. - `LINK_LEFT(LIST(RECORD("k","१२")), LIST(RECORD("k",1)), A,B,A["k"]==B["k"])` prints `not a number: "१२"` (PHP, `Value.php:292` `json_encode($d)`) vs literal characters (js, cpp). Fix: `JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES` if parity is wanted.

  Regression prerequisite: [T12 / PHP-C48](tests/12-integration.md#php-c48).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.
