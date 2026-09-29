# GO correctness

[Worklist](README.md) · [Shared tests](00-tests.md) · [Performance](07-performance.md)

47 findings; 35 implementation tasks. Report severity/confidence is preserved below. Work through one source area at a time; a grouped checkbox closes only when every listed finding is resolved.

Each task starts by reproducing the report against the current tree, then lands the linked shared regression cases before the fix. Fix sketches are starting points, not approved spec changes. Prefer one implementation source per task; touching spec, fixtures or an unavoidable API boundary is allowed. Do not edit generated artifacts without their generator. High-severity tasks take precedence within each area.

## Front end, depth and source positions

<a id="c-go-c9"></a>

- [x] **C-GO-C9 — Resolve GO-C9.** Pipeline placeholder `_` replaces only the FIRST occurrence

  **[GO-C9](../go-code-review.md) — [medium] [confirmed] Pipeline placeholder `_` replaces only the FIRST occurrence**

  Source target: `go/sel/parser.go:357-366` (`parsePipeStep`; the `break` at line 363).

  Implementation starting point: remove `break`; pin the spec text ("every top-level bare `_`").

  Regression prerequisite: [T01 / GO-C9](tests/01-frontend.md#go-c9).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — Go break removed; all six hosts agree

<a id="c-go-c13"></a>

- [x] **C-GO-C13 — Resolve GO-C13.** Lexer is quadratic in interpolation nesting depth and its recursion is unbounded (fatal stack overflow)

  **[GO-C13](../go-code-review.md) — [medium] [confirmed] Lexer is quadratic in interpolation nesting depth and its recursion is unbounded (fatal stack overflow)**

  Source target: `go/sel/lexer.go:225-261` (`lexQuoted`), `302-361` (`matchBrace`/`skipQuoted`), `380-403` (`emitParts` -> `lexRange`).

  Implementation starting point: (a) memoise `matchBrace` by start offset: a scratchpad prototype turned d=64,000 from 88 s to 0.67 s and left the 230k-program differential identical; (b) make `matchBrace`/`skipQuoted` iterative and bound `lexQuoted` recursion, raising `E_DEPTH` at the position the parser would report so error precedence is unchanged.

  Regression prerequisite: [T01 / GO-C13](tests/01-frontend.md#go-c13).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — old-vs-new lexer differential 0 diffs per host (100k-300k random strings); 100k-level nest in <2s on every host

## Exact decimal arithmetic and limits

<a id="c-go-c12"></a>

- [x] **C-GO-C12 — Resolve GO-C12, GO-C42.** Coordinate the related changes below at their shared implementation surface.

  **[GO-C12](../go-code-review.md) — [medium] [confirmed] CEIL/FLOOR of a maximum-size number build a 1,000,001-digit value with no E_RANGE**

  Source target: `go/internal/decimal/dec.go:432-458` (`Floor`, `Ceil`, never call `Guard`); used by `builtins_number.go:33,42` and the `evalMathPlan` CEIL/FLOOR cases.

  Implementation starting point: wrap the two returns in `Guard(..., pos, fail)` or guard only when the increment fires.

  Regression prerequisite: [T02 / GO-C12](tests/02-decimal.md#go-c12).

  **[GO-C42](../go-code-review.md) — [low] [confirmed, latent] `FromInt(math.MinInt64)` builds a corrupt Dec**

  Source target: `go/internal/decimal/dec.go:219-226`. `absVal = -n` overflows, so `Digits` is negative with `Neg` true; `Format` returns `--9223372036854775808`. No current caller passes a user-derived int64 (every `NewInt`/`FromInt` call site checked), but `ToSafeInt` can now return MinInt64 and the pair looks meant to round-trip.

  Implementation starting point: `new(big.Int).Abs(big.NewInt(n))` (or `SetUint64(uint64(-(n+1))+1)`). Not reachable from SEL source today.

  Regression prerequisite: [T02 / GO-C42](tests/02-decimal.md#go-c42).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — go test -race green

## Value ownership, mutation and host conversion

<a id="c-go-c7"></a>

- [x] **C-GO-C7 — Resolve GO-C7.** TAKE/DROP results share the source list's backing array

  **[GO-C7](../go-code-review.md) — [medium] [confirmed] TAKE/DROP results share the source list's backing array**

  Source target: `go/sel/builtins_structure.go:99` (`NewListOwned(val.storage[:count])`) and `:127` (`val.storage[count:]`); the mechanism is `Value.Set` writing `v.storage[idx] = val` in place (`value.go:265-268`).

  Implementation starting point: copy the sub-slice (`append([]*Value(nil), val.storage[:count]...)`); O(count), same order as callers that already copy.

  Regression prerequisite: [T03 / GO-C7](tests/03-values.md#go-c7).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — go test -race green

<a id="c-go-c27"></a>

- [x] **C-GO-C27 — Resolve GO-C27.** `docs/contributing.md`'s claim that aggregate aliasing "cannot be observed" is false; SORT and BUCKET disagree across hosts

  **[GO-C27](../go-code-review.md) — [low] [confirmed, cross-host] `docs/contributing.md`'s claim that aggregate aliasing "cannot be observed" is false; SORT and BUCKET disagree across hosts**

  Source target: `go/sel/builtins_aggregate.go:244-248` (SORT/TOP output element pointers), `:429-439` (2-arg BUCKET clones).

  Implementation starting point: decide the intended rule; if 3.4 is right, MAP/FILTER/SORT/BUCKET must clone what they return (a real cost); otherwise fix the spec/contributing text and make SORT and BUCKET agree.

  Regression prerequisite: [T03 / GO-C27](tests/03-values.md#go-c27).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — go test -race green

## Evaluator order, optimizer and recovery

<a id="c-go-c25"></a>

- [x] **C-GO-C25 — Resolve GO-C25.** ROUND and POWER coerce (and type-check) argument 2 before argument 1

  **[GO-C25](../go-code-review.md) — [low] [confirmed] ROUND and POWER coerce (and type-check) argument 2 before argument 1**

  Source target: `go/sel/builtins_number.go:69-70` and `79-80` (`CheckSizedInt(args.Dec(1), ...)` before `args.Dec(0)`). The math-plan path is in the right order, so it shows only when the call is not plan-compilable (any non-leaf argument such as `IF(...)`).

  Implementation starting point: `x := args.Dec(0)` before the `CheckSizedInt` line in both builtins.

  Regression prerequisite: [T04 / GO-C25](tests/04-evaluation.md#go-c25).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts 1600/1600; plain-vs-optimised 0 diffs

<a id="c-go-c33"></a>

- [x] **C-GO-C33 — Resolve GO-C33.** Optimised tree reports enclosing-operator errors at the wrong node position (spec 6.3)

  **[GO-C33](../go-code-review.md) — [low] [confirmed, all hosts agree] Optimised tree reports enclosing-operator errors at the wrong node position (spec 6.3)**

  Source target: `go/sel/optimizer.go:601-616` (TAKE+TAKE: `merged := copyNode(first)` carries the inner Pos), `619-631` (DROP+DROP), `634-650` (SORT+TAKE `fused := copyNode(first)`), `768-775` (DISTINCT/DEDUPE keep `first`), `777-785` (FILTER(TRUE) dropped).

  Implementation starting point: copy the *outer* step and re-attach `Items[0]` (or set `merged.Pos = second.Pos`); carry the outer Pos onto the surviving node for the dropped FILTER(TRUE). Every host at once.

  Regression prerequisite: [T04 / GO-C33](tests/04-evaluation.md#go-c33).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts green

<a id="c-go-c34"></a>

- [x] **C-GO-C34 — Resolve GO-C34.** The physical plan is observable: SORT+TAKE(0) skips sort-key errors, and fusion evaluates `n` before the sort keys

  **[GO-C34](../go-code-review.md) — [low] [confirmed, all hosts agree] The physical plan is observable: SORT+TAKE(0) skips sort-key errors, and fusion evaluates `n` before the sort keys**

  Source target: `go/sel/optimizer.go:633-650`.

  Implementation starting point: spec decision (document that fusion may elide key errors at n=0 and reorder n vs keys) or keep the SORT and add a trailing slice.

  Regression prerequisite: [T04 / GO-C34](tests/04-evaluation.md#go-c34).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts green

<a id="c-go-c35"></a>

- [x] **C-GO-C35 — Resolve GO-C35.** Error precedence differs between the math plan and ordinary binary evaluation

  **[GO-C35](../go-code-review.md) — [low] [confirmed, all hosts agree] Error precedence differs between the math plan and ordinary binary evaluation**

  Source target: `go/sel/eval.go:494-506` (LOAD_VAR / LOAD_LEAF coerce with `AsDecimal` immediately) vs `217-219 + 250` (`evalBinary` evaluates both operands, then coerces left, then right).

  Implementation starting point: decide in the spec (coerce-after-both would require the plan to defer `AsDecimal`; coerce-as-loaded is what all five hosts do) and add a case either way.

  Regression prerequisite: [T04 / GO-C35](tests/04-evaluation.md#go-c35).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts 1600/1600; plain-vs-optimised 0 diffs

<a id="c-go-c44"></a>

- [x] **C-GO-C44 — Resolve GO-C44, GO-C45, GO-C46, GO-C47.** Coordinate the related changes below at their shared implementation surface.

  **[GO-C44](../go-code-review.md) — [low] [unconfirmed, latent] A panic inside the optimizer poisons `physOnce` for every later Run**

  Source target: `go/sel/program.go:56-61` and `63-76`. A panic inside `physOnce.Do` counts the Once as done; every later `Run` evaluates a nil `physicalAst`. ~170k programs through compile+optimise+run produced zero optimizer panics; only a synthetic test reproduces the second-call nil-pointer panic.

  Implementation starting point: compute into a local and fall back to `p.ast` if the optimizer panics (recover inside the Once body), or optimise in `NewProgram`.

  Regression prerequisite: [T04 / GO-C44](tests/04-evaluation.md#go-c44).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  **[GO-C45](../go-code-review.md) — [low] [unconfirmed, latent] `optRenameVar` copies nodes with stale `MathPlan` pointers**

  Source target: `go/sel/optimizer.go:515-533` (`copyNode` keeps `MathPlan`; `joinReadSelf` in `join_prefilter.go:204-206` correctly nils it).

  Implementation starting point: `cp.MathPlan = nil` in `optRenameVar`.

  Regression prerequisite: [T04 / GO-C45](tests/04-evaluation.md#go-c45).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  **[GO-C46](../go-code-review.md) — [low] [unconfirmed] Join prefilter report can be left stale when a panic is recovered by `??`**

  Source target: `go/sel/builtins_aggregate.go:945-946, 1276, 1301-1303` (`ctx.JoinPrefilterReport` set at the end of `doLink`, consumed by the parent).

  Implementation starting point: reset both fields in the EvalNode defer or the `??`/`???` recovery path.

  Regression prerequisite: [T04 / GO-C46](tests/04-evaluation.md#go-c46).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  **[GO-C47](../go-code-review.md) — [low] [reasoned] Blanket `recover()` can mask genuine Go runtime panics as "error kept"**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: narrow the recovers to `*SelError` / `*SqlError` and re-panic anything else.

  Regression prerequisite: [T04 / GO-C47](tests/04-evaluation.md#go-c47).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

## Aggregates, ordering, grouping and joins

<a id="c-go-c8"></a>

- [x] **C-GO-C8 — Resolve GO-C8.** TOP / TOP_BY / TOP_DESC / BUCKET treat a scalar source as empty; the SORT+TAKE fusion changes results

  **[GO-C8](../go-code-review.md) — [medium] [confirmed] TOP / TOP_BY / TOP_DESC / BUCKET treat a scalar source as empty; the SORT+TAKE fusion changes results**

  Source target: `go/sel/builtins_aggregate.go` `doTop` (`val.IsNull() || val.Size() == 0`, ~line 229/254) and `doBucket` (`:366`); optimizer fusion at `optimizer.go:633-650`.

  Implementation starting point: in `doTop`/`doBucket` use the `IsNull()` test then `len(val.Elements()) == 0`.

  Regression prerequisite: [T05 / GO-C8](tests/05-relational.md#go-c8).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-go-c23"></a>

- [x] **C-GO-C23 — Resolve GO-C23.** SORT / SORT_BY / TOP comparator is not a consistent order, so results depend on the sort algorithm

  **[GO-C23](../go-code-review.md) — [medium] [confirmed, spec gap, cross-host] SORT / SORT_BY / TOP comparator is not a consistent order, so results depend on the sort algorithm**

  Source target: `go/sel/builtins_aggregate.go:83-151` (`compareValues`), used by `sort.SliceStable` at `:233` and `:327`.

  Implementation starting point: spec decision, e.g. classify every key into a rank first (null, bool, number-like, other text, bin) and compare within a rank only, so the order is total; implement in all hosts. Any perf change (GO-P2) must keep the same stable-sort algorithm until this is settled.

  Regression prerequisite: [T05 / GO-C23](tests/05-relational.md#go-c23).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-go-c26"></a>

- [x] **C-GO-C26 — Resolve GO-C26.** Aggregate iteration is a snapshot for shaped records/lists but live for entries-backed records; other hosts iterate live

  **[GO-C26](../go-code-review.md) — [low] [confirmed] Aggregate iteration is a snapshot for shaped records/lists but live for entries-backed records; other hosts iterate live**

  Source target: `go/sel/value.go:680-708` (`Elements`: shaped and list branches build a fresh `[]Entry`, the entries branch returns `v.entries` itself); consumers `aggregateWalk` (`builtins_aggregate.go:70`), doSort, doTop, doBucket.

  Implementation starting point: pick one rule in the spec (iterate a snapshot of pairs is simplest) and make `Elements()` always return a copy for map-backed records; or read `v.storage[i]` at each step (also removes the `[]Entry` allocation, GO-P12).

  Regression prerequisite: [T05 / GO-C26](tests/05-relational.md#go-c26).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Portable regex semantics and resource failures

<a id="c-go-c1"></a>

- [x] **C-GO-C1 — Resolve GO-C1, GO-C10, GO-C28, GO-C30, GO-C31, GO-C32.** Coordinate the related changes below at their shared implementation surface.

  **[GO-C1](../go-code-review.md) — [high] [confirmed] Regex counted repeats above 1000 (and `{00001}`) silently become "never matches"**

  Source target: `go/sel/builtins_regex.go:339-351` (`compileRegex`: `strings.Contains(err.Error(), "repeat count")` sets `unmatchable: true`), consumed at `:387` (`findMatches` returns nil); `minLen: 1001` is dead code.

  Implementation starting point: RE2's limit cannot be raised. Either expand counted repeats above 1000 into concatenated/nested repeats in the lowering pass (`a{1001}` -> `a{1000}a`; strip leading zeros from bounds), or use a different matcher for the SEL subset. Never map an unknown compile error to "no match": surface it as `E_REGEX_SYNTAX` or an explicit limit error.

  Regression prerequisite: [T06 / GO-C1](tests/06-regex.md#go-c1).

  **[GO-C10](../go-code-review.md) — [medium] [confirmed] RREPLACE drops an empty match that abuts the previous match**

  Source target: `go/sel/builtins_regex.go:391` (`FindAllStringSubmatchIndex`), `:407-413` (the `lastEnd` guard is dead code because FindAll already dropped those matches).

  Implementation starting point: hand-write the iteration loop: after a non-empty match ending at e, re-run a second compiled variant (with `^` lowered to a never-matching class) on `s[e:]`; if it returns an empty match at 0, emit it, then advance one rune.

  Regression prerequisite: [T06 / GO-C10](tests/06-regex.md#go-c10).

  **[GO-C28](../go-code-review.md) — [low] [confirmed] Quantifier on an anchor (`^*`, `$*`, `^+`) is accepted; four hosts reject it**

  Source target: `go/sel/builtins_regex.go:125-130` (validator never checks what precedes a quantifier) and `:270-279` (anchor lowering to `\A`/`\z`, which RE2 repeats happily).

  Implementation starting point: in `ValidatePattern` track "previous atom was `^`/`$`" (also start of pattern / after `(` / after `|`) and raise `E_REGEX_SYNTAX` on `* + ? {n}`. This would also give `*a` a Go-independent error message.

  Regression prerequisite: [T06 / GO-C28](tests/06-regex.md#go-c28).

  **[GO-C30](../go-code-review.md) — [low] [confirmed, cross-host split, not Go-specific] RGROUPS capture retention across repeated groups differs by engine**

  Source target: SPEC 7.8 is silent; `builtins_regex.go` relies on RE2/Perl behaviour.

  Implementation starting point: spec decision: ban captures inside repeated groups from the subset, or normalise one way in the two ECMAScript-family hosts.

  Regression prerequisite: [T06 / GO-C30](tests/06-regex.md#go-c30).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[GO-C31](../go-code-review.md) — [low] [confirmed, spec-level, all six hosts agree] `\d \w \s` rewriting inside a class next to `-` makes accidental ranges**

  Source target: SPEC 7.8 rewrite table; `builtins_regex.go:21-23, 217-221` (`expandInside`).

  Implementation starting point: reject `-` directly before or after a shorthand inside a class in the validator ("write the `-` first or last, or escape it"), in all six hosts together; spec first.

  Regression prerequisite: [T06 / GO-C31](tests/06-regex.md#go-c31).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[GO-C32](../go-code-review.md) — [low] [confirmed] RE2's program-size limit surfaces as E_REGEX_SYNTAX for valid patterns**

  Source target: `go/sel/builtins_regex.go:350`.

  Implementation starting point: document a pattern-size limit in `limits.json`, or map this error to a dedicated limit code.

  Regression prerequisite: [T06 / GO-C32](tests/06-regex.md#go-c32).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Text, binary and output budgets

<a id="c-go-c11"></a>

- [x] **C-GO-C11 — Resolve GO-C11.** Huge REPEAT / PADL / PADR / JOIN sizes crash the process (uncatchable) instead of raising a SEL error

  **[GO-C11](../go-code-review.md) — [medium] [confirmed] Huge REPEAT / PADL / PADR / JOIN sizes crash the process (uncatchable) instead of raising a SEL error**

  Source target: `go/sel/builtins_text.go:35,44` (`pad`: `make([]rune, need)`), `:254-255` (`REPEAT`: `strings.Repeat(s, int(n))`), `builtins_aggregate.go:1568-1580` (JOIN `strings.Join`); fed by `Args.NonNegInt` -> `decimal.ToSafeInt`, which now saturates to MaxInt64; `Program.Run` re-panics non-SelError panics (`program.go:65-71`). For the translator: `go/sel/sql/constants.go` `Validate` runs constants through the evaluator (`translator.go:258`), so the panic escapes `TryTranslate`.

  Implementation starting point: spec-first: a text-size cap (analogous to 6.4's digit caps) raising `E_RANGE` before allocating in REPEAT, PADL/PADR, JOIN (precompute length with overflow check); until then check `len(s)*n` overflow and turn the two panics into `E_RANGE`; in the translator treat non-SelError panics from `Validate` as `E_SQL_INVALID`.

  Regression prerequisite: [T07 / GO-C11](tests/07-text-binary.md#go-c11).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-go-c14"></a>

- [x] **C-GO-C14 — Resolve GO-C14.** Exponential value growth from a 300-byte program ends in an unrecoverable `fatal error: out of memory`

  **[GO-C14](../go-code-review.md) — [medium] [confirmed] Exponential value growth from a 300-byte program ends in an unrecoverable `fatal error: out of memory`**

  Source target: `go/sel/eval.go:150-169` (`evalList` clones each operand) and `400-439` (assignment clones); no total-size limit anywhere in the evaluator (6.4 caps depth and digits only).

  Implementation starting point: a spec-level element/allocation budget per Run (new E_RANGE-class error) counted in `evalList`/`CloneAt`/assignment. Without it a Go embedder must sandbox untrusted programs in a child process or memory-limited container.

  Regression prerequisite: [T07 / GO-C14](tests/07-text-binary.md#go-c14).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-go-c29"></a>

- [x] **C-GO-C29 — Resolve GO-C29.** LTB accepts integral-but-scaled numbers (`1.0`, `"1.0"`); every other host raises E_RANGE

  **[GO-C29](../go-code-review.md) — [low] [confirmed] LTB accepts integral-but-scaled numbers (`1.0`, `"1.0"`); every other host raises E_RANGE**

  Source target: `go/sel/builtins_binary.go:207-215` (`decimal.IsInteger(d)` is a value test, not a scale test).

  Implementation starting point: reject `d.Scale != 0` in LTB (four hosts' behaviour), or decide otherwise in the spec and change the four hosts.

  Regression prerequisite: [T07 / GO-C29](tests/07-text-binary.md#go-c29).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## SQL lexical scope, normalization and expansion

<a id="c-go-c2"></a>

- [x] **C-GO-C2 — Resolve GO-C2.** SQL: a non-name binder argument becomes a child-less node and nil-derefs the translator

  **[GO-C2](../go-code-review.md) — [high] [confirmed] SQL: a non-name binder argument becomes a child-less node and nil-derefs the translator**

  Source target: `go/sel/sql/normalise.go:167-168` (`Leaf(arg)` for `manifest.ScopeBinder`), `go/sel/sql/node.go:43-56` (`Leaf` copies no children); crashes at `translator.go:389` (`index`: `obj := n.L()` nil) and `translator.go:2235` (`count`: `n.Kids[0]`).

  Implementation starting point: return a full copy of the argument in binder scope (or refuse non-name binder args in `call()`), and convert stray runtime panics at stage boundaries into `E_SQL_SHAPE`.

  Regression prerequisite: [T08 / GO-C2](tests/08-sql-scope.md#go-c2).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-go-c3"></a>

- [x] **C-GO-C3 — Resolve GO-C3.** SQL: `SUM(<group>, body)` inside a BUCKET projection drops every literal and emits invalid SQL

  **[GO-C3](../go-code-review.md) — [high] [confirmed] SQL: `SUM(<group>, body)` inside a BUCKET projection drops every literal and emits invalid SQL**

  Source target: `go/sel/sql/translator.go:1711-1732` (the `name == "SUM"` group branch of `call`).

  Implementation starting point: splice the parts (slots included): `parts := [{Sql:"COALESCE(SUM("}, inner.Parts..., {Sql:"), 0)"}]` and return with the translator's params. Fix all hosts together.

  Regression prerequisite: [T08 / GO-C3](tests/08-sql-scope.md#go-c3).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-go-c4"></a>

- [x] **C-GO-C4 — Resolve GO-C4.** SQL stage 1 aliases indexed-assignment lists: a later write to the source changes an earlier copy

  **[GO-C4](../go-code-review.md) — [high] [confirmed] SQL stage 1 aliases indexed-assignment lists: a later write to the source changes an earlier copy**

  Source target: `go/sel/sql/normalise.go:68-93` (`defs[name] = value` stores the same `*SNode`; `clist.Append` mutates it in place), `node.go:78-82`.

  Implementation starting point: copy-on-write: replace a CList def with a fresh copy plus the new entry, and copy on `X = R`.

  Regression prerequisite: [T08 / GO-C4](tests/08-sql-scope.md#go-c4).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-go-c20"></a>

- [x] **C-GO-C20 — Resolve GO-C20.** SQL: assignment inlining is exponential in program size (documented DoS; SEL evaluates the same program linearly)

  **[GO-C20](../go-code-review.md) — [medium] [confirmed] SQL: assignment inlining is exponential in program size (documented DoS; SEL evaluates the same program linearly)**

  Source target: `go/sel/sql/normalise.go:117-119` (a use of `X` returns the shared definition pointer), walked as a tree by `Translator.node`; no size bound, only the 200-depth bound. `IsConstant`/`Validate` also re-walk the expanded tree.

  Implementation starting point: compute expanded size in stage 1 (memoised, linear) and refuse above a documented cap; or translate shared defs once (CTE/derived binding). A cap keeps output identical and is spec-cheap.

  Regression prerequisite: [T08 / GO-C20](tests/08-sql-scope.md#go-c20).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## SQL kinds, numeric fidelity and server limits

<a id="c-go-c15"></a>

- [x] **C-GO-C15 — Resolve GO-C15, GO-C16, GO-C17.** Coordinate the related changes below at their shared implementation surface.

  **[GO-C15](../go-code-review.md) — [medium] [confirmed] SQL: UNKNOWN "unifies with anything", so an undeclared column routed through IF/COND/`??`/`???` skips the numeric guard**

  Source target: `go/sel/sql/translator.go:842-863` (`unify`), used by `retKind`, `conditional` (`:1562`), `caseWhen`.

  Implementation starting point: make `unify` return UNKNOWN when any branch is UNKNOWN, keeping the refusal for two known differing kinds.

  Regression prerequisite: [T09 / GO-C15](tests/09-sql-kinds.md#go-c15).

  **[GO-C16](../go-code-review.md) — [medium] [confirmed for SQLite, reasoned for MariaDB/MySQL] SQL: SUM bodies accept UNKNOWN without a numeric guard**

  Source target: `go/sel/sql/translator.go:911-919` (`requireNum` returns UNKNOWN unchanged), `2143-2148` (`aggBody`).

  Implementation starting point: in `aggBody` for SUM run an UNKNOWN body through `guardNumeric` (wrap/refuse) as `unary`/`binary` do.

  Regression prerequisite: [T09 / GO-C16](tests/09-sql-kinds.md#go-c16).

  **[GO-C17](../go-code-review.md) — [medium] [confirmed] SQLite MIN/MAX over a numeric column and a quoted numeric literal compare storage classes, not numbers**

  Source target: `sql/dialects/sqlite.json:217` (`MIN`, and `MAX`), exercised via `translator.go:1769-1771` (guardNumeric does not cast for a NUM-kind operand).

  Implementation starting point: the sqlite MIN/MAX template should cast every operand (`min(CAST({0} AS NUMERIC), ...)`), or refuse when any operand is not a cast expression.

  Regression prerequisite: [T09 / GO-C17](tests/09-sql-kinds.md#go-c17).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-go-c38"></a>

- [x] **C-GO-C38 — Resolve GO-C38.** SQL: TAKE/DROP counts - Go refuses beyond int64 with a non-registry code; JS silently loses precision beyond 2^53

  **[GO-C38](../go-code-review.md) — [low] [confirmed] SQL: TAKE/DROP counts - Go refuses beyond int64 with a non-registry code; JS silently loses precision beyond 2^53**

  Source target: `go/sel/sql/statement.go:691-720` (`evalIntParam`), raises `E_RANGE`/`E_NOT_INT`/`E_NOT_NUM`/`E_ARITY`/`E_BAD_ARG` from `Refuse` though `sql/errors.md` lists only `E_SQL_*`.

  Implementation starting point: pick one rule (refuse above int64/2^53) with an `E_SQL_*` code, add the case, fix JS.

  Regression prerequisite: [T09 / GO-C38](tests/09-sql-kinds.md#go-c38).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## SQL bindings, dialects, fragments and rendering

<a id="c-go-c18"></a>

- [x] **C-GO-C18 — Resolve GO-C18.** SQL: derived-table column order is nondeterministic between runs (Go map iteration)

  **[GO-C18](../go-code-review.md) — [medium] [confirmed] SQL: derived-table column order is nondeterministic between runs (Go map iteration)**

  Source target: `go/sel/sql/statement.go:90-93` (`outputFieldNames` ranges over `Relation.Fields`, a Go map), feeding `ensureDerived` (`:199`) -> `FieldEntry` order -> `FieldOrder` -> `joinedRowFields`/`scalarFields`.

  Implementation starting point: iterate `plan.SourceRelation.Relation.FieldOrder` (fallback: sorted keys) in `outputFieldNames`.

  Regression prerequisite: [T10 / GO-C18](tests/10-sql-rendering.md#go-c18).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-go-c19"></a>

- [x] **C-GO-C19 — Resolve GO-C19.** SQL: `ColumnBinding`/`RawBinding` ignore `collation`, `splitSargable`, and do not validate `prefilter`

  **[GO-C19](../go-code-review.md) — [medium] [confirmed] SQL: `ColumnBinding`/`RawBinding` ignore `collation`, `splitSargable`, and do not validate `prefilter`**

  Source target: `go/sel/sql/binding.go:113-152` (fields stored raw), `translator.go:371` (`c.Prefilter == "separate"` only).

  Implementation starting point: port `checkCollation`/`checkPrefilter`/`columnFlags` (and the `makeRelation` equivalent) verbatim; add unit tests for the typed constructors.

  Regression prerequisite: [T10 / GO-C19](tests/10-sql-rendering.md#go-c19).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-go-c24"></a>

- [x] **C-GO-C24 — Resolve GO-C24, GO-C39.** Coordinate the related changes below at their shared implementation surface.

  **[GO-C24](../go-code-review.md) — [medium/low] [confirmed] `Emit.Fill` mangles non-ASCII bytes in templates (`string(tpl[i])`)**

  Source target: `go/sel/sql/emit.go:291-293` (`push(string(tpl[i]))`; `string(byte)` yields the UTF-8 encoding of U+0000..U+00FF, so `é` becomes `Ã©`). `fillNamed` (`translator.go:1326`) correctly slices `tpl[i:i+1]`.

  Implementation starting point: push the run up to the next `{`/`}` (`push(tpl[i:j])`); this is also the GO-P16 fix.

  Regression prerequisite: [T10 / GO-C24](tests/10-sql-rendering.md#go-c24).

  **[GO-C39](../go-code-review.md) — [low] [confirmed] `Emit.Fill` edge cases differ from JS/Python/PHP (registered templates only)**

  Source target: `go/sel/sql/emit.go:309-315` and `:334-338`.

  Implementation starting point: `join(args[min(frm,len):])`; refuse only `!ok` (non-string) at `:337`.

  Regression prerequisite: [T10 / GO-C39](tests/10-sql-rendering.md#go-c39).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-go-c37"></a>

- [x] **C-GO-C37 — Resolve GO-C37.** SQL: aliases derived from the program (RECORD keys, SELECT_COLS names) bypass the empty/NUL identifier checks that bindings get

  **[GO-C37](../go-code-review.md) — [low] [confirmed] SQL: aliases derived from the program (RECORD keys, SELECT_COLS names) bypass the empty/NUL identifier checks that bindings get**

  Source target: `go/sel/sql/statement.go:944-946` (`AS ` + `Ident(*proj.Alias)`), `968-993`; `binding.go:76-83` guards binding names only.

  Implementation starting point: run the `checkName` equivalent (`E_SQL_BINDING` or `E_SQL_SHAPE`) on projection/group aliases.

  Regression prerequisite: [T10 / GO-C37](tests/10-sql-rendering.md#go-c37).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-go-c40"></a>

- [ ] **C-GO-C40 — Resolve GO-C40.** SQL: small Go/JS divergences and nondeterminism

  **[GO-C40](../go-code-review.md) — [low] [confirmed] SQL: small Go/JS divergences and nondeterminism**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: sort names / dedupe case-folded field names; fix or delete the dead comparison.

  Regression prerequisite: [T10 / GO-C40](tests/10-sql-rendering.md#go-c40).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-go-c43"></a>

- [x] **C-GO-C43 — Resolve GO-C43.** SQL: NUL in an inline text literal is emitted raw

  **[GO-C43](../go-code-review.md) — [low] [unconfirmed effect] SQL: NUL in an inline text literal is emitted raw**

  Source target: `emit.go` `TextLiteral` (`textEscape` maps only `'` and, for the MySQL family, `\`).

  Implementation starting point: refuse (`E_SQL_UNSUPPORTED` with a caveat) or emit the dialect's NUL spelling, in every host at once.

  Regression prerequisite: [T10 / GO-C43](tests/10-sql-rendering.md#go-c43).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## Hybrid execution, keys, order and context

<a id="c-go-c5"></a>

- [x] **C-GO-C5 — Resolve GO-C5.** `ExecuteHybrid` throws away the caller's whole context (`IsNone()` is true for every list/record)

  **[GO-C5](../go-code-review.md) — [high] [confirmed] `ExecuteHybrid` throws away the caller's whole context (`IsNone()` is true for every list/record)**

  Source target: `go/sel/sql/hybrid.go:983` (`if context == nil || context.IsNone()`); root cause `go/sel/value.go:154` (`IsNone` is `Kind == KindNone`, which is also the kind of lists and records; the "no value" test is `IsNull()`).

  Implementation starting point: `context == nil || context.IsNull()`. go-7 measured that this one-line change makes ~24k random pipelines agree with the pure in-memory run (apart from GO-C22).

  Regression prerequisite: [T11 / GO-C5](tests/11-hybrid.md#go-c5).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-go-c21"></a>

- [x] **C-GO-C21 — Resolve GO-C21.** `containsUnsupportedSql` visits every call's arguments twice: hybrid planner time is exponential in call nesting

  **[GO-C21](../go-code-review.md) — [medium] [confirmed] `containsUnsupportedSql` visits every call's arguments twice: hybrid planner time is exponential in call nesting**

  Source target: `go/sel/sql/hybrid.go:87-116` (the `NodeCall` branch loops `node.Items`, then falls through to the generic loop at `:113`). JS returns straight after `node.args.some(...)` (`hybrid.mjs:184`).

  Implementation starting point: return after the call branch's items loop (delete the fall-through) and memoise the helper-expansion verdict per name.

  Regression prerequisite: [T11 / GO-C21](tests/11-hybrid.md#go-c21).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-go-c22"></a>

- [x] **C-GO-C22 — Resolve GO-C22.** A hybrid split before a LINK changes the joined row (binder names), so hybrid != pure memory

  **[GO-C22](../go-code-review.md) — [medium] [confirmed, cross-host: JS behaves identically] A hybrid split before a LINK changes the joined row (binder names), so hybrid != pure memory**

  Source target: `go/sel/sql/hybrid.go:943` and `:558` (continuation source is `varNode("_INPUT")`); design also in `js/src/sql/hybrid.mjs:705`.

  Implementation starting point: treat a split whose next step is LINK/LINK_LEFT (or any continuation step reading the left argument's name) as not a split point, or bind the continuation source under the original relation's name as well as `_INPUT`.

  Regression prerequisite: [T11 / GO-C22](tests/11-hybrid.md#go-c22).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## Host integration, concurrency and tooling

<a id="c-go-c6"></a>

- [ ] **C-GO-C6 — Resolve GO-C6.** Read-only evaluation writes lazy caches into shared input Values (data race, possible torn string read)

  **[GO-C6](../go-code-review.md) — [medium] [confirmed] Read-only evaluation writes lazy caches into shared input Values (data race, possible torn string read)**

  Source target: `go/sel/value.go:62-67` (`Scalar` writes `strVal`), `:437-446` (`AsDecimal` writes `decVal`), `:448-470` (`LooksNumeric` writes `decVal`); reached from every arithmetic/comparison/`&`, `evalMathPlan` LOAD_VAR/LOAD_LEAF, `compareValues`, `StructuralHash`, `SUM`.

  Implementation starting point: publish caches through `atomic.Pointer` (decVal; text derived from decVal is deterministic, so a duplicate compute is benign) and drop or atomically publish `strVal`; or document "a Value graph is not safe for concurrent use, even read-only". Add a shared-context `-race` test.

  Regression prerequisite: [T12 / GO-C6](tests/12-integration.md#go-c6).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-go-c36"></a>

- [ ] **C-GO-C36 — Resolve GO-C36.** `RegisterFunction` accepts a nil function

  **[GO-C36](../go-code-review.md) — [low] [confirmed] `RegisterFunction` accepts a nil function**

  Source target: `go/sel/registry.go:138-168`. SPEC 8.1: a non-callable `fn` must be refused at registration.

  Implementation starting point: `if fn == nil { panic(...) }` next to the arity check.

  Regression prerequisite: [T12 / GO-C36](tests/12-integration.md#go-c36).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-go-c41"></a>

- [ ] **C-GO-C41 — Resolve GO-C41.** "Manifest names never defined" is checked only under `go test`, not at load

  **[GO-C41](../go-code-review.md) — [low] [confirmed] "Manifest names never defined" is checked only under `go test`, not at load**

  Source target: `go/sel/registry.go:20-60` (`Define`); the check lives in `go/sel/parser_test.go:65` (`TestAllManifestBuiltinsRegistered`).

  Implementation starting point: a package-level check after all `init`s (e.g. `sync.Once` in `Compile`/`Lookup`) panicking on unmatched manifest names.

  Regression prerequisite: [T12 / GO-C41](tests/12-integration.md#go-c41).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.
