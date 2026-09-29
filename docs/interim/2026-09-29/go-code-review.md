# Go host code review (correctness and performance) - 2026-09-29

## Scope and method

- **Reviewed:** the Go host `go/` (front end, decimal/value core, evaluator/optimizer/join planning, text/regex/binary/control builtins, structure/aggregate/relational builtins, SQL translator core, SQL rendering/hybrid, `go/bin/*` tools, `go/Makefile`, and the Go wiring in `tools/`). Rust is ignored.
- **Tree state:** `go/` is uncommitted work in progress. The review was against the **on-disk tree as of 2026-09-29** (HEAD `faff480` plus the working-tree changes), not a commit. Line numbers refer to that state and will drift.
- **How:** seven slice reviewers ran in parallel (slice reports `go-1` ... `go-7`), each in a scratchpad copy of `go/` (repo untouched). Slice map:

  | Slice | Area |
  | --- | --- |
  | go-1 | lexer, parser, ast, errors, utf8, manifest/limits consumers |
  | go-2 | decimal core, `value.go`, `shape.go`, `builtins_number.go` |
  | go-3 | evaluator, registry, program/host API, optimizer, join planning |
  | go-4 | text / regex / binary / control builtins |
  | go-5 | structure / aggregate builtins, in-memory relational pipeline |
  | go-6 | SEL to SQL translator core (`go/sel/sql/translator.go`, `statement.go`, normalise, bindings) |
  | go-7 | SQL rendering (`emit`, `map`, `constants`), hybrid planner, `bin/sqlt`, `sqlapi`, `sqlfuzz`, gate wiring |

  Methods used: differential testing against JS/PHP/C++/Lisp/Python (230k parse programs, ~12k regex programs, ~330 aggregate expressions, ~172k plain-vs-optimised programs, ~150k SQL mutants, ~24k hybrid pipelines, 390k decimal cases), race-detector runs, micro-benchmarks and pprof.
- **Synthesizer verification:** the top correctness findings were re-run by the synthesizer in a fresh scratchpad build of the on-disk tree (Go 1.26.8). Entries say "re-verified by synthesizer" where that happened; everything else carries the reviewer's own label (confirmed / unconfirmed, measured / reasoned) unchanged. Two reviewers noted that others overwrote some scratch files; each reviewer's findings file was treated as authoritative.
- **Ids:** `GO-C<n>` correctness (by severity, then confidence), `GO-P<n>` performance (by impact). "found by" lists every slice that reported the issue independently.

## Executive summary

- **Health:** the core is in good shape. The decimal arithmetic is exact (0 mismatches in 390k differential cases plus 200k oracle cases), the lexer/parser match JS on 230k programs apart from one bug, 1091/1091 conformance and 1065/1065 SQL cases pass, and all six findings of the earlier Go review are fixed on disk. The remaining problems are in the corners the suites do not reach: 47 correctness findings (5 high, 19 medium, 23 low), most of them with no conformance case.
- **Worst things:** (1) a regex quantifier above 1000, or a nested-repeat product above 1000, silently becomes "never matches" (RE2 limit mapped to `FALSE`); (2) three SQL-translator defects that emit wrong or crashing SQL (non-name binder nil-deref panic; `SUM(group, body)` inside `BUCKET` drops every literal and emits `* )`; stage-1 aliasing of indexed-assignment lists); (3) `ExecuteHybrid` throws away the caller's whole context; (4) read-only evaluation writes lazy caches into shared input Values (data race even with no caller mutation); (5) TAKE/DROP alias the source list's backing array.
- **Process-killers:** several inputs end in an unrecoverable Go `fatal error: out of memory` or a host panic that escapes `Program.Run` (`REPEAT`/`PADL`/`PADR`/`JOIN` sizes, `A = (A, A)` doubling, quadratic-to-exponential planners). Go turns these into process death rather than a catchable error, which is worse than in JS.
- **Cross-host:** at least 14 findings are shared with other hosts (SQL aliasing and literal loss, UNKNOWN-kind unify, exponential inlining, SORT comparator, capture retention, physical-plan observability, huge-size arguments, ...). Per CLAUDE.md "The one rule" those need spec first, then conformance cases, then every host.
- **Biggest performance wins:** LINK with `equality AND residual` (30x, currently O(n*m)); sort key re-classification (4x, 839 to 193 ms) and full-sort TOP; non-equi LINK rebuilding the right alias per pair (4x); regex `RMATCH` computing every match plus three subject copies (34x vs JS on a 3.5M-char subject); lexer `matchOperator` (261 to 48 ms after three small fixes); the SQL `Emit.Fill` byte-at-a-time concatenation (about 38% of translate time). About 55-60% of CPU in data workloads is GC, so allocation cuts pay double.
- **Go-specific risk:** RE2 caps repeat counts at 1000, has different `FindAll` empty-match semantics and a program-size limit (GO-C1, GO-C10, GO-C32); it is linear-time, which is a real advantage over JS (no ReDoS). Goroutines and shared Programs are supported, but lazy caches inside `Value` break the "read-only context is shareable" expectation (GO-C6). Go map iteration is randomised, which leaked into SQL column order (GO-C18). `fatal error` on allocation failure cannot be recovered. The decimal core is hand-written on top of `math/big.Int` (no floats), so it inherits `big.Int`'s quadratic decimal `SetString` and per-operation allocations (GO-P9, GO-P10).

## Status of the earlier review's findings

Earlier review: `docs/interim/2026-09-29/go-implementation-review.md` (six findings). Every slice reviewer re-ran the repros against the on-disk tree; the synthesizer re-ran all of them again.

| # | Earlier finding | Status | Evidence / residual |
| --- | --- | --- | --- |
| 1 | Record shape cache key collision (NUL in key) | **fixed** | `A = RECORD("a",1,"b",2); RECORD("a\u{0}b",3)["b"]` now `E_NO_KEY at 1:49` (re-run by synthesizer); `conformance/23-runtime-regressions.selt` covers it |
| 2 | Decimal to int64 wraps before range check | **fixed** (residual) | `POWER(2,18446744073709551616)` now `E_RANGE`; `LEFT("abc",18446744073709551617)` is `abc`; `TAKE((1,2,3),18446744073709551617)` returns all three (all re-run). Cosmetic: the message prints the saturated value (`exponent 9223372036854775807`). Residual: saturation now feeds `PADL`/`PADR`/`REPEAT` sizes, which panic or OOM (GO-C11) |
| 3 | `Pow10` cache race | **fixed** | RWMutex added; `go test -race -run TestConcurrent ./sel` passes (re-run); go-3 also ran 879 conformance sources plus ~6000 generated programs on shared Programs under `-race`: no reports. Residual, different class: GO-C6 |
| 4 | Shared Program `SlotCache` race | **fixed** | `atomic.Pointer` to an immutable pair. GO-P7 notes a perf caveat only |
| 5 | Caught errors leak evaluation depth | **fixed** | `MAP` over 200 items with `MISSING ?? 0` returns 200 zeros (re-run); `EvalNode` restores `Depth`/`Frames` in a `defer`. That defer costs 5-25% (GO-P11) |
| 6 | Aggregate frames survive recovered errors | **fixed** | `(SORT((1,2),X,MISSING) ?? 0); X` and `(BUCKET((1,2),MISSING) ?? 0); _` are both `E_UNDEF_VAR` (re-run). Residual hygiene: `JoinPrefilterReport` is not reset on the same path (GO-C46, unconfirmed) |

Also: `conformance/23-runtime-regressions.selt` passes 18/18 and the full suite 1091/1091 (go-3). Follow-up the earlier review asked for is partly missing: no race test with a shared read-only context (GO-C6) and no conformance case for the PADL/PADR residual.

---

# Correctness findings

## High

### GO-C1 [high] [confirmed] Regex counted repeats above 1000 (and `{00001}`) silently become "never matches"
- **Found by:** go-4 C1. **Re-verified by synthesizer** (`a{1001}`, `a{0,2000}`, `a{00001}` all `FALSE`; JS gives `TRUE` for `a{1001}`).
- **Where:** `go/sel/builtins_regex.go:339-351` (`compileRegex`: `strings.Contains(err.Error(), "repeat count")` sets `unmatchable: true`), consumed at `:387` (`findMatches` returns nil); `minLen: 1001` is dead code.
- **What:** SPEC 7.8 / 6.4 allow quantifier bounds up to 65 535 (`a{65535}` is a conformance case). RE2 refuses repeat counts above 1000, nested repeats whose product exceeds 1000, and leading zeros in a count. The code maps that compile error to "valid pattern that never matches", so the result is a confident wrong answer, and `(?:a{1001})?` or `a{0,2000}` (which should match everything) match nothing. The only conformance case at the cap (`RMATCH('a{65535}', "a")` -> `FALSE`) passes for the wrong reason.
- **Repro** (raw strings, because `{}` in `"..."` is interpolation):
  - `RMATCH('a{1001}', REPEAT('a',1001))` -> Go `FALSE`; js/py/cpp/php/lisp `TRUE`
  - `RMATCH('a{0,2000}','zzz')` -> Go `FALSE`; js/py `TRUE`
  - `RMATCH('^a{1001,}$', REPEAT('a',1001))` -> Go `FALSE`; js/py `TRUE`
  - `RMATCH('^(?:a{1000}){2}$', REPEAT('a',2000))` -> Go `FALSE`; js/py `TRUE`
  - `RMATCH('^(a{300}){300}$', REPEAT('a',90000))` -> Go `FALSE`; js/py `TRUE`
  - `RMATCH('a{00001}','a')` -> Go `FALSE`; js/cpp/php/lisp `TRUE`
  - `RFIND('b{2,5000}','abb')` -> Go `0`; js/py `2`
- **Fix sketch:** RE2's limit cannot be raised. Either expand counted repeats above 1000 into concatenated/nested repeats in the lowering pass (`a{1001}` -> `a{1000}a`; strip leading zeros from bounds), or use a different matcher for the SEL subset. Never map an unknown compile error to "no match": surface it as `E_REGEX_SYNTAX` or an explicit limit error.
- **Conformance gap:** none. Add to `09-regex.selt`: `RMATCH('^a{1001}$', REPEAT('a',1001))` TRUE; `RMATCH('^(?:a{1000}){2}$', REPEAT('a',2000))` TRUE; `RMATCH('a{0,2000}','')` TRUE; `RMATCH('a{00001}','a')` TRUE; `RMATCH('^a{65535}$', REPEAT('a',65535))` TRUE.

### GO-C2 [high] [confirmed] SQL: a non-name binder argument becomes a child-less node and nil-derefs the translator
- **Found by:** go-6 C1. **Re-verified by synthesizer** (`sql.Translate` of `BUCKET("x", A[1], "y", 1)`, mariadb, `A` a NUM column: `PANIC: runtime error: invalid memory address or nil pointer dereference`).
- **Where:** `go/sel/sql/normalise.go:167-168` (`Leaf(arg)` for `manifest.ScopeBinder`), `go/sel/sql/node.go:43-56` (`Leaf` copies no children); crashes at `translator.go:389` (`index`: `obj := n.L()` nil) and `translator.go:2235` (`count`: `n.Kids[0]`).
- **What:** stage 1 keeps the binder-position argument "as written" via `Leaf(arg)`. For a bare name that is right; for a compound node (index, bin, call) it yields an SNode with T=index but zero Kids. SEL rejects such programs only at run time (`E_EXPECT_SYMBOL`), so they compile and reach the translator. JS keeps the whole node and refuses with `E_SQL_SHAPE`. `sql.go` re-panics non-`SqlError`, so `TryTranslate` (documented to swallow only refusals) crashes the process. The mutation fuzzer found 5 crash sites from this one cause.
- **Repro:** `BUCKET("x", A[1], "y", 1)` (above). JS: `ERR E_SQL_SHAPE A is bound as a column, which has no parts to index`. Statement forms: `ITEMS .> MAP(BUCKET("x", _["NAME"], "x", _["QTY"]))`; `ITEMS .> BUCKET(_["NAME"]) .> MAP(BUCKET("entity", COUNT(_), "latest", TOP_BY(_, _["PRICE"], "DESC", 1)))` -> `index out of range [0] with length 0` at `count`.
- **Fix sketch:** return a full copy of the argument in binder scope (or refuse non-name binder args in `call()`), and convert stray runtime panics at stage boundaries into `E_SQL_SHAPE`.
- **Conformance gap:** none. Add sqlt case `shape.binder.non-name-binder-is-refused` for the expression and both statement forms.

### GO-C3 [high] [confirmed] SQL: `SUM(<group>, body)` inside a BUCKET projection drops every literal and emits invalid SQL
- **Found by:** go-6 C2. **Re-verified by synthesizer** (mariadb statement for `ITEMS .> BUCKET(_["NAME"], RECORD("n", _K, "t", SUM(_, _["PRICE"] * 2)))`: `COALESCE(SUM((`price` * )), 0) AS `t``).
- **Where:** `go/sel/sql/translator.go:1711-1732` (the `name == "SUM"` group branch of `call`).
- **What:** the fragment is rebuilt with `for _, pt := range inner.Parts { sqlBuilder.WriteString(pt.Sql) }`. Literals are `Part{IsSlot: true, Sql: ""}`, so all NUM/TEXT/BOOL literals vanish from the text and the new Fragment has nil Params. JS has the same defect in a different costume: `inner.parts.join('')` prints the slot number, so `_["PRICE"] * 2` becomes `price * 1`, a silently WRONG expression.
- **Repro:** the case above -> Go `... COALESCE(SUM((`i`.`price` * )), 0) AS `t` ...`; JS `... SUM((`i`.`price` * 1)) ...`. `SUM(_, IF(_["NAME"] $== "x", 1, 2))` -> Go `CASE WHEN (CAST(`i`.`name` AS CHAR) COLLATE ... = CAST( AS CHAR) ...) THEN  ELSE  END`. Same on PostgreSQL and SQLite.
- **Fix sketch:** splice the parts (slots included): `parts := [{Sql:"COALESCE(SUM("}, inner.Parts..., {Sql:"), 0)"}]` and return with the translator's params. Fix all hosts together.
- **Conformance gap:** none (only `SUM(_, _["col"])` is in `24-bucket.sqlt`). Add `bucket.sum.literal-in-body` with a NUM literal and a TEXT literal (`IF(_["dept"] $== "x", 1, 2)`), in inline and params modes.

### GO-C4 [high] [confirmed] SQL stage 1 aliases indexed-assignment lists: a later write to the source changes an earlier copy
- **Found by:** go-6 C3. **Re-verified by synthesizer** (Go translator `COUNT(X)` -> `2`; the SEL evaluator gives `1`). JS has the identical bug.
- **Where:** `go/sel/sql/normalise.go:68-93` (`defs[name] = value` stores the same `*SNode`; `clist.Append` mutates it in place), `node.go:78-82`.
- **What:** SEL assignment copies (spec 5.7/5.9). In stage 1, `X = R` stores R's CList pointer; a later `R[2] = ...` (or `X[2] = ...`) appends into the shared CList, so both names see the extra key. Aggregates then fold over the wrong element set and return a definite wrong answer, the one outcome sql-kinds.md section 2 says is unacceptable.
- **Repro:** `R[1] = 5; X = R; R[2] = 6; COUNT(X)` -> SEL `1`, Go translator `2`, JS translator `2`. `R[1] = 5; X = R; X[2] = 7; COUNT(R)` -> SEL `1`, translators `2`. `R[1] = A; X = R; R[2] = B; SUM(X, _ + 1)` -> SEL `E_UNDEF_VAR` (A unbound); with columns SEL sums one element, translator two.
- **Fix sketch:** copy-on-write: replace a CList def with a fresh copy plus the new entry, and copy on `X = R`.
- **Conformance gap:** none. Add `assign.indexed.copy-is-not-alias` with the two programs.

### GO-C5 [high] [confirmed] `ExecuteHybrid` throws away the caller's whole context (`IsNone()` is true for every list/record)
- **Found by:** go-7 C1. **Re-verified by synthesizer** (plan `ITEMS .> DROP(1) .> DEDUPE() .> TAKE(4) .> DROP(K)` for mariadb, hybrid; context record `{K: 3}`; runner returns five rows: `ExecuteHybrid` -> `E_UNDEF_VAR at 1:49: undefined variable K`).
- **Where:** `go/sel/sql/hybrid.go:983` (`if context == nil || context.IsNone()`); root cause `go/sel/value.go:154` (`IsNone` is `Kind == KindNone`, which is also the kind of lists and records; the "no value" test is `IsNull()`).
- **What:** JS/PHP/Python clone the caller's context and add `_INPUT`. Go replaces any record context with an empty record, so every name the continuation reads from the caller (thresholds, other tables for a LINK/ANY, helper inputs) is `E_UNDEF_VAR`. This defeats the main purpose of a hybrid plan (sql-translation.md 12.1). Nothing in Go calls `ExecuteHybrid` (no test, not in sqlapi or e2e), which is why it went unnoticed.
- **Fix sketch:** `context == nil || context.IsNull()`. go-7 measured that this one-line change makes ~24k random pipelines agree with the pure in-memory run (apart from GO-C22).
- **Conformance gap:** none (.sqlt cases never execute a plan). Add a Go unit test in `go/sel/sql` and a cross-host `execute` probe in `tools/sqlapi.*` (JS already has one in `check-js-optimizer.mjs`).

## Medium

### GO-C6 [medium] [confirmed] Read-only evaluation writes lazy caches into shared input Values (data race, possible torn string read)
- **Found by:** go-2 C4, go-3 C1, go-5 C5 (rated low there). **Re-verified by synthesizer** (`NewNone().Set("P", NewText("12.50"))`, 8 goroutines x 200 `MustCompile("P + 1").Run(ctx)` under `-race`: `WARNING: DATA RACE` at `value.go:437/440/444`).
- **Where:** `go/sel/value.go:62-67` (`Scalar` writes `strVal`), `:437-446` (`AsDecimal` writes `decVal`), `:448-470` (`LooksNumeric` writes `decVal`); reached from every arithmetic/comparison/`&`, `evalMathPlan` LOAD_VAR/LOAD_LEAF, `compareValues`, `StructuralHash`, `SUM`.
- **What:** reading a text value as a number caches the parsed Dec (and formatting a number caches its text) inside the shared Value. Two goroutines running programs over the same context race even though nobody assigns anything. `strVal` is a two-word string header, so a torn read is possible in principle. The earlier review's fixes 3 and 4 cover global caches and the per-node slot cache but assume each worker owns its values; `concurrency_test.go` gives every worker its own context. "One loaded dataset, many concurrent requests" is a normal embedding and no doc states that contexts must not be shared.
- **Repro:** the case above. go-3 also raced `R["x"] + 1`, `SUM(L, _["v"])`, `SORT_BY(L, _["v"])`, `R["name"] & R["x"]` over one root, and a root holding `N` from `X = 1.5; X * 3` then `N & "x"` (race at `value.go:63/64`). go-5: 200 `NewText("<n>")` items, 4 goroutines alternating `SORT(L) .> COUNT()` and `SUM(L, _ + 1)`: dozens of races. Results were still correct; the race report is the failure.
- **Fix sketch:** publish caches through `atomic.Pointer` (decVal; text derived from decVal is deterministic, so a duplicate compute is benign) and drop or atomically publish `strVal`; or document "a Value graph is not safe for concurrent use, even read-only". Add a shared-context `-race` test.
- **Conformance gap:** not expressible in `.selt`; needs a Go race test in `concurrency_test.go`.

### GO-C7 [medium] [confirmed] TAKE/DROP results share the source list's backing array
- **Found by:** go-2 C2, go-5 C1. **Re-verified by synthesizer** (`A = (1,2,3); TAKE(A,2)[(A[1] = 9; "1")]` -> Go `9`, JS `1`; `A = LIST(1,2,3); LIST(TAKE(A,2), A[1] = 9)` -> Go `{"1"={"1"=9,"2"=2},"2"=9}`).
- **Where:** `go/sel/builtins_structure.go:99` (`NewListOwned(val.storage[:count])`) and `:127` (`val.storage[count:]`); the mechanism is `Value.Set` writing `v.storage[idx] = val` in place (`value.go:265-268`).
- **What:** SPEC 3.4: only shared *elements* are visible through aliases; TAKE builds its own list. Here both Values point at one array, so `A[1] = 9` rewrites the result's slot. The returned slice also keeps the source's capacity, so any future `append` on `storage` would overwrite the source's tail (none does today).
- **Repro:** above; also `A = (1,2,3); DROP(A,1)[(A[2] = 9; "1")]` -> Go `9`, JS/PHP `2`; `A = LIST(1,2,3); LIST(DROP(A,1), A[2] = 9)` -> Go shows 9,3, the others 2,3. JS, PHP, Python, C++, Lisp all give the detached result.
- **Fix sketch:** copy the sub-slice (`append([]*Value(nil), val.storage[:count]...)`); O(count), same order as callers that already copy.
- **Conformance gap:** none. Add `struct.take.detached-from-source` (`A = LIST(1,2,3); LIST(TAKE(A,2), A[1] = 9)` expecting `-{"1"=-{"1"=t"1","2"=t"2"},"2"=t"9"}`) plus the DROP twin.

### GO-C8 [medium] [confirmed] TOP / TOP_BY / TOP_DESC / BUCKET treat a scalar source as empty; the SORT+TAKE fusion changes results
- **Found by:** go-3 C2, go-5 C2. **Re-verified by synthesizer** (`TOP(5,3)` -> `-` (empty), `TAKE(SORT(5), 1)` -> `-`, `SORT(5)` -> `{"1"=t"5"}`, `BUCKET(5,_,_)` empty; JS `TOP(5,3)` -> `{"1"=t"5"}`).
- **Where:** `go/sel/builtins_aggregate.go` `doTop` (`val.IsNull() || val.Size() == 0`, ~line 229/254) and `doBucket` (`:366`); optimizer fusion at `optimizer.go:633-650`.
- **What:** SPEC 7.3: a first argument with no children "is treated as a one-element list containing itself when it has a scalar". `Value.Size()` counts children only, so a scalar has size 0. SORT, MAP, FILTER, ALL, SUM use `Elements()` and are right. Because the optimizer fuses `SORT... .> TAKE(n)` into `TOP...`, Go's *physical* result for `TAKE(SORT(5), 1)` is `()` while the unoptimised evaluation (`EvalNode(p.AST())`) is `(5)`.
- **Repro:** `TOP(5,3)`, `TOP_BY(5,X,X,3)`, `TOP_DESC(5,3)`: Go and Lisp empty; JS/PHP/Python/C++ `{"1"=5}`. `COUNT(TOP(5,1))` 0 vs 1. `BUCKET(5,_,_)`: Go/JS/PHP/Python/Lisp empty, C++ `{"1"={"1"=5}}`; `BUCKET(5,_)`: C++ `{"5"={"1"=5}}`, the others empty. `X = 5; TAKE(SORT_BY(X,_),1)` diverges the same way.
- **Fix sketch:** in `doTop`/`doBucket` use the `IsNull()` test then `len(val.Elements()) == 0`.
- **Conformance gap:** `agg.scalar-is-one-element-list` covers only ALL and FILTER. Add `agg.top.scalar-source` (`TOP(5,3)` -> `-{"1"=t"5"}`), `rel.top-by.scalar-source`, `agg.bucket.scalar-source` (`BUCKET(5,_,COUNT(_))` -> `-{"1"=t"1"}`) and `rel.sort-take.scalar-source-fusion`. Lisp also diverges on TOP; BUCKET diverges in four hosts (see Cross-host).

### GO-C9 [medium] [confirmed] Pipeline placeholder `_` replaces only the FIRST occurrence
- **Found by:** go-1 C1. **Re-verified by synthesizer** (`LIST(1,2) .> LIST(_, _)` -> Go `E_UNDEF_VAR at line 1 column 22`; JS `{"1"={"1"=1,"2"=2},"2"={"1"=1,"2"=2}}`; `5 .> MAX(_, _, 3)` -> Go `E_UNDEF_VAR` col 13).
- **Where:** `go/sel/parser.go:357-366` (`parsePipeStep`; the `break` at line 363).
- **What:** SPEC.md:449 and grammar.md say "the bare placeholder `_` is replaced by x" and are silent on repeats; JS, Python, PHP, C++ and Lisp all substitute every top-level bare `_`. Go stops after the first, so the second stays a variable read. Removing the `break` makes Go agree with JS on all 80k parse-corpus programs (98 differed before).
- **Fix sketch:** remove `break`; pin the spec text ("every top-level bare `_`").
- **Conformance gap:** `pipe.placeholder.first`/`.second` (`14-pipeline.selt:47,54`) use a single `_`. Add `pipe.placeholder.repeated` (`LIST(1,2) .> LIST(_, _)`) and `pipe.placeholder.repeated-below-min-arity` (`5 .> MAX(_, _)`).

### GO-C10 [medium] [confirmed] RREPLACE drops an empty match that abuts the previous match
- **Found by:** go-4 C2. **Re-verified by synthesizer** (`RREPLACE('a*','-','baac')` -> Go `-b-c-`; JS `-b--c-`).
- **Where:** `go/sel/builtins_regex.go:391` (`FindAllStringSubmatchIndex`), `:407-413` (the `lastEnd` guard is dead code because FindAll already dropped those matches).
- **What:** Go's `FindAll*` "ignore[s] empty matches abutting a preceding match"; PCRE, ECMAScript, Python 3.7+, cl-ppcre and SRELL report them, and the spec asks for "all matches replaced". RMATCH/RFIND/RGROUPS use only the first match. In 12,000 fuzz programs every Go-vs-majority difference (31 of ~350 empty-capable RREPLACE programs) was this one.
- **Repro:** `RREPLACE('a*','-','baac')` (above); `RREPLACE('\s*','_','a b')` -> Go `_a_b_`, others `_a__b_`; `RREPLACE('(b*|[é-ü])','<$0|$1>','baac')` -> Go `<b|b>a<|>a<|>c<|>` vs others `<b|b><|>a<|>a<|>c<|>`.
- **Fix sketch:** hand-write the iteration loop: after a non-empty match ending at e, re-run a second compiled variant (with `^` lowered to a never-matching class) on `s[e:]`; if it returns an empty match at 0, emit it, then advance one rune.
- **Conformance gap:** none. `re.replace.zero-width` (`'x*'` on "ab") does not hit the abutting case. Add `RREPLACE('a*', "-", "baac")` -> `"-b--c-"` and one with a capture group.

### GO-C11 [medium] [confirmed] Huge REPEAT / PADL / PADR / JOIN sizes crash the process (uncatchable) instead of raising a SEL error
- **Found by:** go-2 C6, go-4 C5, go-5 C6, go-6 C10 (constant-folding route). **PADL panic re-verified by synthesizer** (`PADL("a", 318446744073709551616, "x")` -> `panic: runtime error: makeslice: len out of range [recovered, repanicked]`).
- **Where:** `go/sel/builtins_text.go:35,44` (`pad`: `make([]rune, need)`), `:254-255` (`REPEAT`: `strings.Repeat(s, int(n))`), `builtins_aggregate.go:1568-1580` (JOIN `strings.Join`); fed by `Args.NonNegInt` -> `decimal.ToSafeInt`, which now saturates to MaxInt64; `Program.Run` re-panics non-SelError panics (`program.go:65-71`). For the translator: `go/sel/sql/constants.go` `Validate` runs constants through the evaluator (`translator.go:258`), so the panic escapes `TryTranslate`.
- **What:** no spec cap exists (only number digits, ROUND scale, POWER exponent, regex quantifiers, §6.4), so behaviour is unspecified; but Go's failure mode is process death. `PADL("a", 5000000000, "x")` -> `fatal error: out of memory` (not a panic; `recover` cannot catch it); `REPEAT("ab", 9223372036854775807)` -> `panic: strings: Repeat output length overflow`; `PADL("a", 9223372036854775807, "x")` -> `panic: makeslice: len out of range`. PADL also allocates 4 bytes per code point (`[]rune`), ~5x the result string. JS gives a catchable RangeError; C++/Lisp/PHP spin or exhaust memory (their batch runners hang on `REPEAT("", 99999999999)`), so it is cross-host, but Go's mode is the worst for a server-embedded library.
- **Repro:** `bash -c 'ulimit -v 8000000; go/build/sel -e "PADL(\"a\", 5000000000, \"x\")"'` -> fatal error: out of memory. `go/build/sel -e 'REPEAT("ab", 99999999999999999999)'` -> panic trace. `LEN(JOIN(SPLIT(REPEAT("a,",2000000),","), REPEAT("b",2000000)))` under `ulimit -v 6000000` -> `runtime: out of memory: cannot allocate 4000002867200-byte block`; JS `RangeError: Invalid string length`. Translator: `PADL("7", 318446744073709551616, "0") $== NAME` -> `PANIC: makeslice: len out of range` from `sql.Translate`; `LEN(REPEAT("abc", 100000000))` = 300000000 in 0.9 s, so a constant in a translated rule can burn memory at translation time.
- **Fix sketch:** spec-first: a text-size cap (analogous to 6.4's digit caps) raising `E_RANGE` before allocating in REPEAT, PADL/PADR, JOIN (precompute length with overflow check); until then check `len(s)*n` overflow and turn the two panics into `E_RANGE`; in the translator treat non-SelError panics from `Validate` as `E_SQL_INVALID`.
- **Conformance gap:** none; needs the spec decision, then e.g. `LEN(REPEAT("ab", 99999999999999999999))` -> `error E_RANGE` and a translator case `pad.count-out-of-range`.

### GO-C12 [medium] [confirmed] CEIL/FLOOR of a maximum-size number build a 1,000,001-digit value with no E_RANGE
- **Found by:** go-2 C1. **Re-verified by synthesizer** (`LEN(CEIL(REPEAT("9", 1000000) & ".5"))` -> Go `1000001`; JS `E_RANGE number has more than 1000000 integer digits` at "line 0 column 0").
- **Where:** `go/internal/decimal/dec.go:432-458` (`Floor`, `Ceil`, never call `Guard`); used by `builtins_number.go:33,42` and the `evalMathPlan` CEIL/FLOOR cases.
- **What:** SPEC 6.4 says the 1,000,000-integer-digit cap is "checked wherever a number is built". The carry (`999...9.5` -> `10^1000000`) exceeds it. `Round` (which Guards) correctly refuses the analogous case. C++ has the same hole (1000001). Separately JS and PHP report this error at "line 0 column 0", i.e. without a node position, contrary to 6.3.
- **Repro:** `LEN(FLOOR("-" & REPEAT("9",1000000) & ".5"))` -> Go 1000002 vs E_RANGE. `ROUND(REPEAT("9",1000000) & ".5", 0)` is `E_RANGE` at column 5 in all four hosts (expected model).
- **Fix sketch:** wrap the two returns in `Guard(..., pos, fail)` or guard only when the increment fires.
- **Conformance gap:** none (`10-limits.selt` has no CEIL/FLOOR case). Add `limit.ceil.carry` = `CEIL(REPEAT("9",1000000) & ".5")` => `!E_RANGE` at the CEIL call, plus the FLOOR negative twin.

### GO-C13 [medium] [confirmed] Lexer is quadratic in interpolation nesting depth and its recursion is unbounded (fatal stack overflow)
- **Found by:** go-1 C2.
- **Where:** `go/sel/lexer.go:225-261` (`lexQuoted`), `302-361` (`matchBrace`/`skipQuoted`), `380-403` (`emitParts` -> `lexRange`).
- **What:** for `"{ "{ "{ ... 1 ... }" }" }"` every level calls `matchBrace`, which re-scans the whole remaining interior, and `emitParts` re-lexes it one level deeper: O(n * depth). Nesting is bounded only by the parser's E_DEPTH (6.4), which runs after the whole source is tokenised. The mutual recursion has no depth counter; Go's 1 GB goroutine stack limit is a `fatal error: stack overflow` (exit 2), not a recoverable SelError.
- **Repro** (measured, source `'"{'*d + '1' + '}"'*d` via `go/build/sel file`): d=1,000: 0.02 s; 4,000: 0.25 s; 16,000: 3.8 s; 32,000: 17.6 s; 64,000 (256 KB): 88 s CPU; every run ends `E_DEPTH at 1:101`. `'"{' * 8000000` (16 MB) -> `runtime: goroutine stack exceeds 1000000000-byte limit / fatal error: stack overflow`, exit 2. For context, JS dies with a RangeError trace at d=2,000 and C++ segfaults at d=16,000 (and takes 4.3 s at 16k): a cross-host lexer hole; Go is the most resilient.
- **Fix sketch:** (a) memoise `matchBrace` by start offset: a scratchpad prototype turned d=64,000 from 88 s to 0.67 s and left the 230k-program differential identical; (b) make `matchBrace`/`skipQuoted` iterative and bound `lexQuoted` recursion, raising `E_DEPTH` at the position the parser would report so error precedence is unchanged.
- **Conformance gap:** none. Pin `lim.interp-nesting-*` (100 nested interpolations -> E_DEPTH at the same position everywhere); the timing part is not expressible in `.selt`.

### GO-C14 [medium] [confirmed] Exponential value growth from a 300-byte program ends in an unrecoverable `fatal error: out of memory`
- **Found by:** go-3 C6.
- **Where:** `go/sel/eval.go:150-169` (`evalList` clones each operand) and `400-439` (assignment clones); no total-size limit anywhere in the evaluator (6.4 caps depth and digits only).
- **What:** `A = (A, A)` doubles the tree on each statement. 22 doublings (4.2M elements) take 2.9 s / ~1 GB; 30 never finish. The allocator failure is a Go `fatal error` that `recover()` cannot catch, so `Program.Run` cannot return an error. JS grows the same way (5.2 s at 22); the language-level problem is shared, the Go consequence is the hard abort.
- **Repro:** `go/build/sel -e "A = 1; $(python3 -c "print(' '.join(['A = (A, A);']*30))") COUNT(A)"` under `ulimit -v 3000000` -> `fatal error: out of memory`, exit 2. 18/20/22 doublings: 0.2/0.8/2.9 s, results 262144/1048576/4194304.
- **Fix sketch:** a spec-level element/allocation budget per Run (new E_RANGE-class error) counted in `evalList`/`CloneAt`/assignment. Without it a Go embedder must sandbox untrusted programs in a child process or memory-limited container.
- **Conformance gap:** none; needs a limits decision first.

### GO-C15 [medium] [confirmed] SQL: UNKNOWN "unifies with anything", so an undeclared column routed through IF/COND/`??`/`???` skips the numeric guard
- **Found by:** go-6 C4. JS has the same behaviour.
- **Where:** `go/sel/sql/translator.go:842-863` (`unify`), used by `retKind`, `conditional` (`:1562`), `caseWhen`.
- **What:** `IF(c, 1, U)` with U an UNKNOWN-kind column gets kind NUM, so the surrounding arithmetic/comparison emits no `CASE WHEN ISNUM ... END` wrapper. sql-kinds.md section 5 says uncertain-in-numeric is wrapped when testable and REFUSED when untestable; plain `U > 0` obeys (SQLite: `E_SQL_UNSUPPORTED`), `IF(FLAG, 1, U) > 0` and `(U ?? 1) > 0` translate. MAP.md:446 / sql-translation.md:343 say "UNKNOWN if they disagree".
- **Repro** (measured on SQLite via Python sqlite3): `IF(FLAG, 1, UNK) < 1` -> `(CAST(CASE WHEN "o"."flag" THEN '1' ELSE "o"."unk" END AS NUMERIC) < CAST('1' AS NUMERIC))`; row flag=0, unk='abc': SQLite TRUE, SEL `E_NOT_NUM: not a number: "abc"`. The oracle fuzz found 18 of 234 random expressions violating the warrant this way (e.g. `A < IF(TRUE, U, -1)`, `(U ?? -1) <= (-1 * A)`). On mariadb `IF(FLAG, 1, UNK) > 0` emits `CASE WHEN flag THEN 1 ELSE unk END > 0` with no guard (reasoned; MariaDB coerces 'abc' to 0).
- **Fix sketch:** make `unify` return UNKNOWN when any branch is UNKNOWN, keeping the refusal for two known differing kinds.
- **Conformance gap:** none. Add `warrant.unify.unknown-branch-stays-unknown` (mariadb: guard present; sqlite: E_SQL_UNSUPPORTED).

### GO-C16 [medium] [confirmed for SQLite, reasoned for MariaDB/MySQL] SQL: SUM bodies accept UNKNOWN without a numeric guard
- **Found by:** go-6 C5. JS identical.
- **Where:** `go/sel/sql/translator.go:911-919` (`requireNum` returns UNKNOWN unchanged), `2143-2148` (`aggBody`).
- **What:** an aggregate SUM whose body is an UNKNOWN column is emitted bare, whereas `U + 1` is guarded. SQL must not answer where SEL raises E_NOT_NUM.
- **Repro:** bindings U=UNKNOWN col, N=NUM col, COLS=(U,N), T=relation(U,N). sqlite `SUM(COLS, _)` -> `("t"."u" + "t"."n")`; row u='abc', n=1: SQLite gives 1, SEL `E_NOT_NUM`. `SUM(T, _["U"])` -> `COALESCE(SUM("t"."u"), 0)` gives 0.0 on 'abc' in SQLite (measured). MariaDB emits bare `SUM(`t`.`u`)` (reasoned). PostgreSQL casts for COLS (loud, acceptable), `SUM("t"."u")` is a type error (loud).
- **Fix sketch:** in `aggBody` for SUM run an UNKNOWN body through `guardNumeric` (wrap/refuse) as `unary`/`binary` do.
- **Conformance gap:** none. Add `warrant.sum.unknown-body`.

### GO-C17 [medium] [confirmed] SQLite MIN/MAX over a numeric column and a quoted numeric literal compare storage classes, not numbers
- **Found by:** go-6 C9. Shared by all hosts (generated map).
- **Where:** `sql/dialects/sqlite.json:217` (`MIN`, and `MAX`), exercised via `translator.go:1769-1771` (guardNumeric does not cast for a NUM-kind operand).
- **What:** SQLite renders literals as text (`'1'`), so `min("a", '1')` compares a numeric column value with a TEXT value; numbers sort below text, so `min` returns the column value whenever the column is numeric-typed. SEL takes the numeric minimum. The corpus and gate oracle pass presumably because their columns have TEXT affinity.
- **Repro:** `MIN(A, 1) > 1` (A NUM column, sqlite) -> `(CAST(min("o"."a", '1') AS NUMERIC) > CAST('1' AS NUMERIC))`; row a=2.5: SQLite TRUE (`select min(2.5,'0')` is 2.5), SEL `MIN(2.5, 1)` is 1 so FALSE. The oracle also flagged `MIN(LEN(U), MIN(1,1))` and `MIN(1, A) IN (A, A)`.
- **Fix sketch:** the sqlite MIN/MAX template should cast every operand (`min(CAST({0} AS NUMERIC), ...)`), or refuse when any operand is not a cast expression.
- **Conformance gap:** none for numeric-affinity columns. Add an oracle case with an INTEGER/REAL column.

### GO-C18 [medium] [confirmed] SQL: derived-table column order is nondeterministic between runs (Go map iteration)
- **Found by:** go-6 C7.
- **Where:** `go/sel/sql/statement.go:90-93` (`outputFieldNames` ranges over `Relation.Fields`, a Go map), feeding `ensureDerived` (`:199`) -> `FieldEntry` order -> `FieldOrder` -> `joinedRowFields`/`scalarFields`.
- **What:** for a plain relation source the derived relation's fields are listed in map-iteration order, so the same rule renders different SQL text on different translations, against "cross-host byte-identical agreement is the product". `RelationSpec.FieldOrder` exists to prevent this and is used elsewhere.
- **Repro** (as reported): `ITEMS .> TAKE(5) .> LINK(TAGS, a, b, a["NAME"] $== b["TAG"])` (statement, mariadb) run 6 times: the select list alternates between `_sub1`.`price`, `_sub1`.`qty`, `_sub1`.`name` and a different order. The corpus passes only because the cases use one field or get lucky. **Not re-run by the synthesizer:** the synthesizer's stand-in program (a self-join of ITEMS) was refused with `E_SQL_SHAPE`, which did not exercise this path.
- **Fix sketch:** iterate `plan.SourceRelation.Relation.FieldOrder` (fallback: sorted keys) in `outputFieldNames`.
- **Conformance gap:** the sqlt harness runs each case once; run each twice (or 5x) and require equal text, plus a two-field wrapped-then-linked case.

### GO-C19 [medium] [confirmed] SQL: `ColumnBinding`/`RawBinding` ignore `collation`, `splitSargable`, and do not validate `prefilter`
- **Found by:** go-6 C8.
- **Where:** `go/sel/sql/binding.go:113-152` (fields stored raw), `translator.go:371` (`c.Prefilter == "separate"` only).
- **What:** sql-translation.md 652-661 and JS `columnFlags` (`binding.mjs:296`) fold `collation: 'binary'|'exact'` into exact, `'sargable'|'prefilter'` into sargable, reject unknown collation/prefilter spellings (`E_SQL_BINDING`), lowercase prefilter, and make `splitSargable: true` imply prefilter `separate`. Go does none of it; only the corpus binders (which pre-resolve these in the generator) exercise the typed path, so the suite is blind. An application passing `collation="binary"` gets the slow CAST+COLLATE form; `collation="sargable"`/`splitSargable=true` get no sargable prefilter; typos are silently accepted. `RelationBinding(..., prefilter="bogus")` is accepted (no `checkPrefilter`).
- **Repro** (Go vs JS, mariadb, `ANY(T, _ $== "x")` over a TEXT column): `collation="sargable"` -> Go `... CAST(t.c AS CHAR) COLLATE ... = CAST('x' AS CHAR) ...` with no coarse `t.c = 'x'` term; JS emits `((t.c = 'x') AND (CAST...))`. `splitSargable=true`: Go single EXISTS, JS `EXISTS(coarse) AND EXISTS(exact)`. `prefilter="bogus"` / `collation="bogus"`: Go accepts, JS `E_SQL_BINDING`.
- **Fix sketch:** port `checkCollation`/`checkPrefilter`/`columnFlags` (and the `makeRelation` equivalent) verbatim; add unit tests for the typed constructors.
- **Conformance gap:** `bind.collation.*` cases exist but the Go harness binds them through pre-resolved `bindCol`; add Go unit tests calling `ColumnBinding` with string collation/prefilter values.

### GO-C20 [medium] [confirmed] SQL: assignment inlining is exponential in program size (documented DoS; SEL evaluates the same program linearly)
- **Found by:** go-6 C6 (plus go-7 C2's note on helper expansion). JS has the same behaviour.
- **Where:** `go/sel/sql/normalise.go:117-119` (a use of `X` returns the shared definition pointer), walked as a tree by `Translator.node`; no size bound, only the 200-depth bound. `IsConstant`/`Validate` also re-walk the expanded tree.
- **What:** `X0 = A + A; X1 = X0 + X0; ...; Xn` is 2^n nodes after inlining; `E_SQL_DEPTH` never triggers because depth grows by 1 per level. Output text and time double per level.
- **Repro** (Go, mariadb): n=14 0.23 s, n=18 4.0 s (6 MB SQL), n=20 19 s (25 MB); n=30 would be ~2^30 nodes. `sel -e` evaluates n=60 in 6 ms. A 1 KB rule can exhaust CPU and memory of whatever process translates rules.
- **Fix sketch:** compute expanded size in stage 1 (memoised, linear) and refuse above a documented cap; or translate shared defs once (CTE/derived binding). A cap keeps output identical and is spec-cheap.
- **Conformance gap:** none. Add `limit.inline-expansion` (n=40 doubling chain -> refusal).

### GO-C21 [medium] [confirmed] `containsUnsupportedSql` visits every call's arguments twice: hybrid planner time is exponential in call nesting
- **Found by:** go-7 C2. Cross-host part: helper-expansion blow-up also exists in JS.
- **Where:** `go/sel/sql/hybrid.go:87-116` (the `NodeCall` branch loops `node.Items`, then falls through to the generic loop at `:113`). JS returns straight after `node.args.some(...)` (`hybrid.mjs:184`).
- **What:** reached from `PlanHybrid` -> `tryPlanFallthrough` for `... .> MAP(RECORD(pushable pair, custom pair))`, i.e. any rule mixing an SQL-mappable and an unmappable column, once the full-SQL and latest-member attempts failed. Cost is 2^depth in nested function calls; the cap is only E_DEPTH (200).
- **Repro:** `ORDERS .> MAP(RECORD("plus", ABS(ABS(...ABS(_["amount"])...)), "tag", ABORT("x")))` planned for mariadb (bindings as in `sql/cases/25`): n=14 nested ABS 14 ms, n=18 217 ms, n=20 1.1 s, n=22 4.27 s (doubling per level; n=30 extrapolates to ~17 min, not run). JS on the same program: 22 ms at n=22. Cross-host: `H1 = H0 + H0; H2 = H1 + H1; ...` then a MAP reading H16: Go 1.2 s, JS 0.6 s, both 2^n.
- **Fix sketch:** return after the call branch's items loop (delete the fall-through) and memoise the helper-expansion verdict per name.
- **Conformance gap:** none. Add a plan case (or a Go unit test with a time bound) with 40-deep `ABS(...)` in a pushable pair next to an `ABORT("x")` pair.

### GO-C22 [medium] [confirmed, cross-host: JS behaves identically] A hybrid split before a LINK changes the joined row (binder names), so hybrid != pure memory
- **Found by:** go-7 C5.
- **Where:** `go/sel/sql/hybrid.go:943` and `:558` (continuation source is `varNode("_INPUT")`); design also in `js/src/sql/hybrid.mjs:705`.
- **What:** SPEC 7.4 "Joined rows": the left binders are the *name of the left argument*. When the planner puts `ORDERS .> TAKE(1)` in SQL and the LINK in the continuation, the left argument is `_INPUT`, so every joined row carries `_INPUT`/`_input` instead of `ORDERS`/`orders`, and later reads (`_["ORDERS"]["name"]`) or the E_NO_KEY the original would raise change. The planner already guards this for a LINK in the prefix (`joinRowsLackBinders`) but not for a LINK that starts the continuation.
- **Repro:** `ORDERS .> TAKE(1) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])` (plan hybrid): in-memory result keys `ORDERS`, `orders`; after hybrid execution `_INPUT`, `_input`. 67 of 4,000 random pipelines mismatched this way, all with a LINK after the split, plus a few where the original raises E_NO_KEY (`FILTER(_["customer_id"] $== "b")` on a joined row) and the hybrid returns a value/empty list. No other mismatch kind appeared.
- **Fix sketch:** treat a split whose next step is LINK/LINK_LEFT (or any continuation step reading the left argument's name) as not a split point, or bind the continuation source under the original relation's name as well as `_INPUT`.
- **Conformance gap:** none (`sql/cases/25` pins only the prefix SQL text). Add an executed-plan case for `X .> TAKE(1) .> LINK(...)`.

### GO-C23 [medium] [confirmed, spec gap, cross-host] SORT / SORT_BY / TOP comparator is not a consistent order, so results depend on the sort algorithm
- **Found by:** go-5 C3.
- **Where:** `go/sel/builtins_aggregate.go:83-151` (`compareValues`), used by `sort.SliceStable` at `:233` and `:327`.
- **What:** number-like TEXT vs number-like TEXT compares numerically, but number-like TEXT vs non-numeric TEXT compares bytewise (the text/bin branch fires before the rank branch). That is intransitive: 2 < 10 (numeric), 10 < "1a" (bytes), "1a" < 2 (bytes). SPEC defines no comparator for SORT; only `rel.sort.mixed` pins null < bool < number < text. Go agrees with JS only because both use the same insertion-block + merge scheme.
- **Repro:** `LIST("2","10","1a","3","1b","20","2a") .> SORT() .> JOIN(",")`: Go/JS/PHP/Python `2,10,1a,1b,3,20,2a`; C++ `1a,2,3,10,1b,20,2a`; Lisp `1b,2,3,10,1a,20,2a`.
- **Fix sketch:** spec decision, e.g. classify every key into a rank first (null, bool, number-like, other text, bin) and compare within a rank only, so the order is total; implement in all hosts. Any perf change (GO-P2) must keep the same stable-sort algorithm until this is settled.
- **Conformance gap:** none. Add `rel.sort.numeric-text-vs-text` with the list above once the rule is decided.

### GO-C24 [medium/low] [confirmed] `Emit.Fill` mangles non-ASCII bytes in templates (`string(tpl[i])`)
- **Found by:** go-7 C3 (rated medium), go-6 C11 (rated low). Reachable only through runtime-registered templates; shipped dialect templates are pure ASCII.
- **Where:** `go/sel/sql/emit.go:291-293` (`push(string(tpl[i]))`; `string(byte)` yields the UTF-8 encoding of U+0000..U+00FF, so `é` becomes `Ã©`). `fillNamed` (`translator.go:1326`) correctly slices `tpl[i:i+1]`.
- **Repro:** `sql.Define("mariadb","funcs","UPPER", {"tpl":"F({0}, 'é ż')","ret":"TEXT"})`, translate `UPPER(NAME)` -> Go `F(`o`.`name`, 'Ã© Å¼')`; JS `F(`o`.`name`, 'é ż')`. go-6: `MY_CRC('zażółć', {0})` -> `MY_CRC('zaÅ¼Ã³ÅÄ', 'x')`.
- **Fix sketch:** push the run up to the next `{`/`}` (`push(tpl[i:j])`); this is also the GO-P16 fix.
- **Conformance gap:** none. Add a `.sqlt` `--- register` case with a non-ASCII template (it would catch every host).

## Low

### GO-C25 [low] [confirmed] ROUND and POWER coerce (and type-check) argument 2 before argument 1
- **Found by:** go-2 C3. C++ has the same order. **Direction re-verified by synthesizer:** `X="a"; Y="1"; ROUND(X, IF(Y=="1", "x", -1))` -> Go `E_NOT_NUM` at column 24 (the `"x"`), JS column 21 (`X`).
- **Where:** `go/sel/builtins_number.go:69-70` and `79-80` (`CheckSizedInt(args.Dec(1), ...)` before `args.Dec(0)`). The math-plan path is in the right order, so it shows only when the call is not plan-compilable (any non-leaf argument such as `IF(...)`).
- **What:** 6.2/6.3: strictly left to right, first failure wins. Argument *evaluation* order is correct; only coercion order is wrong.
- **Repro** (as reported): `ROUND(X, IF(Y=="1", "x", -1))` -> Go/C++ `E_NOT_NUM` col 29, JS/PHP col 25 (the reporter's spacing differs from the synthesizer's; columns shift accordingly). `ROUND(X, IF(Y=="1", -1, 0))` -> Go/C++ `E_RANGE`, JS/PHP/Lisp `E_NOT_NUM`. `POWER(X, IF(Y=="1", 1.5, 0))` -> Go/C++ `E_NOT_INT`, others `E_NOT_NUM`. `ROUND(NULL, IF(Y=="1","z",0))` -> Go E_NOT_NUM(z) vs E_NULL.
- **Fix sketch:** `x := args.Dec(0)` before the `CheckSizedInt` line in both builtins.
- **Conformance gap:** none. Add a case with a non-plannable second argument and a bad first argument, asserting the column of the first.

### GO-C26 [low] [confirmed] Aggregate iteration is a snapshot for shaped records/lists but live for entries-backed records; other hosts iterate live
- **Found by:** go-2 C5, go-5 C4.
- **Where:** `go/sel/value.go:680-708` (`Elements`: shaped and list branches build a fresh `[]Entry`, the entries branch returns `v.entries` itself); consumers `aggregateWalk` (`builtins_aggregate.go:70`), doSort, doTop, doBucket.
- **What:** SPEC 7.3 does not say what a body that writes to the walked collection sees (undefined behaviour), but one program gives two answers depending on how the record was built: a representation leak, and Go is the odd one out.
- **Repro:** `R = RECORD("a",1,"b",2); MAP(R, (R["b"] = 99; _))` -> Go `{"1"=t"1","2"=t"2"}`; JS, PHP, Python, Lisp, C++ `"2"=t"99"`. Entries-backed (`R = NULL; R["a"]=1; R["b"]=2; MAP(R, (R["b"] = 99; _))`) Go gives `t"99"`. go-5: `R = RECORD("a",1); R["b"]=2; R["c"]=3; MAP(R, (R["c"] = 100) + _)` gives `101,102,200` (entries) vs `101,102,103` (shaped) in Go; JS/PHP/C++ give 200 for the shaped form. Lists: `A = LIST(1,2,3); MAP(A, (A[3] = 100) + _)` 103 in Go/PHP, 200 in JS/C++; `MAP(A,(A[4]=100)+_)` crashes the JS host with a TypeError (for the JS report).
- **Fix sketch:** pick one rule in the spec (iterate a snapshot of pairs is simplest) and make `Elements()` always return a copy for map-backed records; or read `v.storage[i]` at each step (also removes the `[]Entry` allocation, GO-P12).
- **Conformance gap:** none. Add `agg.map.body-mutates-later-element` once the rule is chosen.

### GO-C27 [low] [confirmed, cross-host] `docs/contributing.md`'s claim that aggregate aliasing "cannot be observed" is false; SORT and BUCKET disagree across hosts
- **Found by:** go-5 C8.
- **Where:** `go/sel/builtins_aggregate.go:244-248` (SORT/TOP output element pointers), `:429-439` (2-arg BUCKET clones).
- **What:** a variable's child can be mutated in place while an aggregate result is pending. SPEC 3.4 says aggregates copy what they collect; Go copies in 2-arg BUCKET only.
- **Repro:** `A = LIST(RECORD("x",1)); LIST(SORT(A), A[1]["x"] = 9)`: Go and PHP show x=9, JS and C++ show 1. `... LIST(BUCKET(A,1), A[1]["x"] = 9)`: Go and JS show 1, PHP and C++ show 9. MAP/FILTER/TAKE/DISTINCT/3-arg BUCKET show 9 in all four hosts.
- **Fix sketch:** decide the intended rule; if 3.4 is right, MAP/FILTER/SORT/BUCKET must clone what they return (a real cost); otherwise fix the spec/contributing text and make SORT and BUCKET agree.
- **Conformance gap:** none.

### GO-C28 [low] [confirmed] Quantifier on an anchor (`^*`, `$*`, `^+`) is accepted; four hosts reject it
- **Found by:** go-4 C3.
- **Where:** `go/sel/builtins_regex.go:125-130` (validator never checks what precedes a quantifier) and `:270-279` (anchor lowering to `\A`/`\z`, which RE2 repeats happily).
- **Repro:** `RMATCH('^*','a')` -> Go TRUE, Lisp TRUE; JS/PHP/C++/Python `E_REGEX_SYNTAX`. Same for `$*` and `^+`.
- **Fix sketch:** in `ValidatePattern` track "previous atom was `^`/`$`" (also start of pattern / after `(` / after `|`) and raise `E_REGEX_SYNTAX` on `* + ? {n}`. This would also give `*a` a Go-independent error message.
- **Conformance gap:** none. Add `RMATCH('^*','a')` -> `E_REGEX_SYNTAX` (Lisp needs the same fix).

### GO-C29 [low] [confirmed] LTB accepts integral-but-scaled numbers (`1.0`, `"1.0"`); every other host raises E_RANGE
- **Found by:** go-4 C4.
- **Where:** `go/sel/builtins_binary.go:207-215` (`decimal.IsInteger(d)` is a value test, not a scale test).
- **Repro:** `BTL(LTB(LIST(1.0)))` -> Go `1`, others `E_RANGE`; `LTB(LIST("1.0"))` -> Go `bin:01`, js/py `E_RANGE`. `LEFT("abc",1.0)` is `a` in all hosts.
- **Fix sketch:** reject `d.Scale != 0` in LTB (four hosts' behaviour), or decide otherwise in the spec and change the four hosts.
- **Conformance gap:** none. Add `LTB((65, 1.0))` with an explicit decision.

### GO-C30 [low] [confirmed, cross-host split, not Go-specific] RGROUPS capture retention across repeated groups differs by engine
- **Found by:** go-4 C7.
- **Where:** SPEC 7.8 is silent; `builtins_regex.go` relies on RE2/Perl behaviour.
- **Repro:** `RGROUPS('(?:(a)|b)+','ab')`: Go/PHP/Lisp/Python `{1:"ab",2:"a"}`; JS/C++ (ECMAScript reset) `{1:"ab",2:""}`. Also `RGROUPS('((a)|(b))+','ab')` group 3, `RGROUPS('(?:(a)|(b))*','ba')`. Go is in the 4:2 majority; the suite has no case.
- **Fix sketch:** spec decision: ban captures inside repeated groups from the subset, or normalise one way in the two ECMAScript-family hosts.
- **Conformance gap:** none; add a case once decided.

### GO-C31 [low] [confirmed, spec-level, all six hosts agree] `\d \w \s` rewriting inside a class next to `-` makes accidental ranges
- **Found by:** go-4 C8.
- **Where:** SPEC 7.8 rewrite table; `builtins_regex.go:21-23, 217-221` (`expandInside`).
- **What/repro:** the textual expansion is not neutral next to `-`: `[+-\d]` becomes `[+-0-9]` = range `+`..`0` then `-`,`9`, matching `,` `-` `.` `/` `9` but NOT `5`: `RMATCH('^[+-\d]$','5')` FALSE and `RMATCH('^[+-\d]$',',')` TRUE in Go, JS, C++, PHP, Lisp, Python. `[\w-z]` becomes `_-z` (matches the backtick): `RMATCH('^[\w-z]$','`')` TRUE everywhere. `[\w-.]` becomes the reversed range `_-.` -> `E_REGEX_SYNTAX`. In PCRE `-` after a shorthand is literal; in ECMAScript-u it is a syntax error.
- **Fix sketch:** reject `-` directly before or after a shorthand inside a class in the validator ("write the `-` first or last, or escape it"), in all six hosts together; spec first.
- **Conformance gap:** none.

### GO-C32 [low] [confirmed] RE2's program-size limit surfaces as E_REGEX_SYNTAX for valid patterns
- **Found by:** go-4 C6. JS throws a raw SyntaxError for the same input.
- **Where:** `go/sel/builtins_regex.go:350`.
- **Repro:** `RMATCH('[^a]{1000}' x 10000, 'a')` (100 KB pattern, every quantifier legal under 7.8): build with `python3 -c "print(\"RMATCH('\" + '[^a]{1000}'*10000 + \"','a')\")"`; Go -> `E_REGEX_SYNTAX ... expression too large`.
- **Fix sketch:** document a pattern-size limit in `limits.json`, or map this error to a dedicated limit code.
- **Conformance gap:** none.

### GO-C33 [low] [confirmed, all hosts agree] Optimised tree reports enclosing-operator errors at the wrong node position (spec 6.3)
- **Found by:** go-3 C3.
- **Where:** `go/sel/optimizer.go:601-616` (TAKE+TAKE: `merged := copyNode(first)` carries the inner Pos), `619-631` (DROP+DROP), `634-650` (SORT+TAKE `fused := copyNode(first)`), `768-775` (DISTINCT/DEDUPE keep `first`), `777-785` (FILTER(TRUE) dropped).
- **What:** when the outermost step of a pipeline is merged into or dropped in favour of an inner step, the node handed to the enclosing operator carries the inner call's position. Go plain and physical evaluation disagree; JS and Python agree with Go's *physical* result, so it is a cross-host convention that contradicts 6.3.
- **Repro:** `NOT TAKE(TAKE(LIST(1), 3), 2)` -> physical `E_NOT_BOOL` at 1:10 (plain AST evaluation 1:5); `NOT FILTER(LIST(1), TRUE)` -> 1:12 (plain 1:5); `NOT DROP(DROP(LIST(1),1),1)` `E_NO_SCALAR` 1:10 (plain 1:5); `X = LIST(1); NOT TAKE(SORT(X), 1)` 1:23 (plain 1:18).
- **Fix sketch:** copy the *outer* step and re-attach `Items[0]` (or set `merged.Pos = second.Pos`); carry the outer Pos onto the surviving node for the dropped FILTER(TRUE). Every host at once.
- **Conformance gap:** none. Add `opt.pos.take-take-under-operator` (`NOT TAKE(TAKE(LIST(1), 3), 2)` -> error E_NOT_BOOL at 1:5) plus DROP/DROP, FILTER(TRUE), SORT+TAKE variants.

### GO-C34 [low] [confirmed, all hosts agree] The physical plan is observable: SORT+TAKE(0) skips sort-key errors, and fusion evaluates `n` before the sort keys
- **Found by:** go-3 C4.
- **Where:** `go/sel/optimizer.go:633-650`.
- **What:** SPEC 7.4 pins only "TAKE/TOP with n=0 still evaluates the list". The sort *key* body is not evaluated at all by `TOP_BY(L, key, 0)`, whereas unfused `SORT_BY(L, key) .> TAKE(0)` evaluates it per element. `SORT_BY(L, key) .> TAKE(MISSING)` raises the `n` error first after fusion but the key error first when unfused. 94 of 60000 generated pipelines differ between Go plain and physical evaluation; 72 contain `, 0)` (not each opened) and the other 22 are GO-C35.
- **Repro:** `L = LIST(RECORD("a",1),RECORD("a",2)); L .> SORT_BY(_["z"]) .> TAKE(0)` -> physical `()`, unoptimised `E_NO_KEY` at 1:52 (JS/PHP/Python/C++/Lisp also `()`); `... .> SORT_BY(_["zz"]) .> TAKE(MISSING)` -> `E_UNDEF_VAR` (unfused `E_NO_KEY`).
- **Fix sketch:** spec decision (document that fusion may elide key errors at n=0 and reorder n vs keys) or keep the SORT and add a trailing slice.
- **Conformance gap:** `rel.take.zero-after-a-sort-still-evaluates-the-list` covers only the list; pin the chosen behaviour for the key body.

### GO-C35 [low] [confirmed, all hosts agree] Error precedence differs between the math plan and ordinary binary evaluation
- **Found by:** go-3 C5.
- **Where:** `go/sel/eval.go:494-506` (LOAD_VAR / LOAD_LEAF coerce with `AsDecimal` immediately) vs `217-219 + 250` (`evalBinary` evaluates both operands, then coerces left, then right).
- **What:** in a math plan a non-numeric left operand raises `E_NOT_NUM` before the right operand is evaluated; in the non-plan path the right operand is evaluated first, so a failing right operand wins. Whether the plan applies depends on the shape of the whole subtree. 22 of 60000 generated pipelines differ. All five hosts print the physical answers, so it is a spec gap (6.2 does not say whether coercion is part of evaluating the operand).
- **Repro:** `X = "abc"; X + MISSING` -> `E_NOT_NUM` at 1:10 (all hosts); `X = "abc"; IF(TRUE, X, 1) + MISSING` -> `E_UNDEF_VAR` at 1:25 (all hosts); `"abc" == MISSING` -> `E_UNDEF_VAR`. Data-shaped: `L = LIST(RECORD("a","q","c","y")); MAP(L, r, r["a"] / r["b"])` -> physical `E_NOT_NUM`, AST evaluation `E_NO_KEY`.
- **Fix sketch:** decide in the spec (coerce-after-both would require the plan to defer `AsDecimal`; coerce-as-loaded is what all five hosts do) and add a case either way.
- **Conformance gap:** none. Add `eval.coerce.left-before-right-operand-error` with a plan-shaped and a non-plan-shaped program.

### GO-C36 [low] [confirmed] `RegisterFunction` accepts a nil function
- **Found by:** go-3 C7.
- **Where:** `go/sel/registry.go:138-168`. SPEC 8.1: a non-callable `fn` must be refused at registration.
- **Repro:** `RegisterFunction("Zed", 0, 1, nil)` returns normally; `MustCompile("ZED()").Run(nil)` -> nil-pointer panic that escapes `Program.Run` (re-panicked, `program.go:64-70`).
- **Fix sketch:** `if fn == nil { panic(...) }` next to the arity check.
- **Conformance gap:** API probe: add `host.fn.refuse.not-callable` in `tools/api.*` for all hosts.

### GO-C37 [low] [confirmed] SQL: aliases derived from the program (RECORD keys, SELECT_COLS names) bypass the empty/NUL identifier checks that bindings get
- **Found by:** go-6 C12. Same in JS.
- **Where:** `go/sel/sql/statement.go:944-946` (`AS ` + `Ident(*proj.Alias)`), `968-993`; `binding.go:76-83` guards binding names only.
- **What:** sql/errors.md promises "an identifier that is empty or contains a NUL" is refused. `RECORD("", ...)` emits `AS ""` (PostgreSQL: zero-length delimited identifier error; the other three accept) and `RECORD("a\u{0}b", ...)` emits a NUL inside the identifier, which C-string drivers truncate. Quote doubling itself is correct.
- **Repro:** `ITEMS .> MAP(RECORD("", _["NAME"]))` -> `SELECT "i"."name" AS "" FROM ...`; `RECORD("a\u{0}b", ...)` -> alias containing byte 0.
- **Fix sketch:** run the `checkName` equivalent (`E_SQL_BINDING` or `E_SQL_SHAPE`) on projection/group aliases.
- **Conformance gap:** none.

### GO-C38 [low] [confirmed] SQL: TAKE/DROP counts - Go refuses beyond int64 with a non-registry code; JS silently loses precision beyond 2^53
- **Found by:** go-6 C13.
- **Where:** `go/sel/sql/statement.go:691-720` (`evalIntParam`), raises `E_RANGE`/`E_NOT_INT`/`E_NOT_NUM`/`E_ARITY`/`E_BAD_ARG` from `Refuse` though `sql/errors.md` lists only `E_SQL_*`.
- **Repro:** `ITEMS .> TAKE(18446744073709551616)` -> Go `E_RANGE`, JS `LIMIT 18446744073709552000`; `TAKE(9223372036854775807)` Go exact, JS `...776000`. Go is the better behaviour; no case pins it.
- **Fix sketch:** pick one rule (refuse above int64/2^53) with an `E_SQL_*` code, add the case, fix JS.
- **Conformance gap:** none.

### GO-C39 [low] [confirmed] `Emit.Fill` edge cases differ from JS/Python/PHP (registered templates only)
- **Found by:** go-7 C4.
- **Where:** `go/sel/sql/emit.go:309-315` and `:334-338`.
- **What/repro:** (a) `{n:}` with `n >= len(args)`: JS/Python/PHP join the empty sub-list and emit nothing; Go refuses ("neither an argument nor a lexical entry"). `Define(mariadb funcs UPPER {"tpl":"F({0}, {1:})"})`, `UPPER(NAME)` -> Go `E_SQL_UNSUPPORTED`, JS ``F(`o`.`name`, )``. (b) A lexical key whose value is the empty string: JS pushes it, Go refuses `valStr == ""` (`emit.go:337`); dialect extending mariadb with `lexical.textCollate = ""`, tpl `F({0}{textCollate})` -> Go `E_SQL_UNSUPPORTED`, JS ``F(`o`.`name`)``. MAP.md treats null as the withdrawal, not "".
- **Fix sketch:** `join(args[min(frm,len):])`; refuse only `!ok` (non-string) at `:337`.
- **Conformance gap:** none. Add two `--- register` cases.

### GO-C40 [low] [confirmed] SQL: small Go/JS divergences and nondeterminism
- **Found by:** go-6 C14.
- `normalise.go:96-101` `constantKey` treats a TEXT key `""` as "not constant" (`R[""] = 1; R[""]` is `E_SQL_ASSIGN` at the target) whereas JS accepts the empty key and refuses later with `E_SQL_SHAPE`. Both refuse; codes differ.
- `binding.go:277-292 / 330-353`: `NewBindings` records `order` by ranging a Go map, so `CheckAliases` reports "relations X and Y share the alias" with X/Y in random order; `makeRelation` with the `map[string]*Binding` form gives a nondeterministic winner when two field names differ only by ASCII case and adds duplicate FieldOrder entries; the `[]FieldEntry` form also appends duplicates.
- `statement.go:2076-2094` `withJoinBinders` compares `&plan.Joins[i] == &join` on a by-value parameter (never true; the fallback is what works). Dead code, harmless.
- **Fix sketch:** sort names / dedupe case-folded field names; fix or delete the dead comparison.

### GO-C41 [low] [confirmed] "Manifest names never defined" is checked only under `go test`, not at load
- **Found by:** go-1 C3.
- **Where:** `go/sel/registry.go:20-60` (`Define`); the check lives in `go/sel/parser_test.go:65` (`TestAllManifestBuiltinsRegistered`).
- **What:** `docs/contributing.md` says `define` refuses to load on a manifest name no module defined; JS does this in `assertManifestCovered()` (`registry.mjs:83`). Go detects only per-definition mismatches at init. Today the 78 manifest names equal the 78 registered names, so nothing is wrong now.
- **Fix sketch:** a package-level check after all `init`s (e.g. `sync.Once` in `Compile`/`Lookup`) panicking on unmatched manifest names.
- **Conformance gap:** `tools/check-manifest.sh` covers arity behaviour, not coverage.

### GO-C42 [low] [confirmed, latent] `FromInt(math.MinInt64)` builds a corrupt Dec
- **Found by:** go-2 C7.
- **Where:** `go/internal/decimal/dec.go:219-226`. `absVal = -n` overflows, so `Digits` is negative with `Neg` true; `Format` returns `--9223372036854775808`. No current caller passes a user-derived int64 (every `NewInt`/`FromInt` call site checked), but `ToSafeInt` can now return MinInt64 and the pair looks meant to round-trip.
- **Repro:** scratch unit test: `Format(FromInt(math.MinInt64))` -> `--9223372036854775808`.
- **Fix sketch:** `new(big.Int).Abs(big.NewInt(n))` (or `SetUint64(uint64(-(n+1))+1)`). Not reachable from SEL source today.

### GO-C43 [low] [unconfirmed effect] SQL: NUL in an inline text literal is emitted raw
- **Found by:** go-7 C8 (cross-host).
- **Where:** `emit.go` `TextLiteral` (`textEscape` maps only `'` and, for the MySQL family, `\`).
- **What:** TEXT may contain U+0000 (spec 3). Inline SQL then carries a raw NUL byte. Through a C-string client API the statement is truncated at the NUL, losing the closing quote; the reviewer expects a syntax error rather than injection but could not run a server. `params` mode is unaffected. Demonstrated only that Go emits the raw byte (`V & "x'y"` with V = "a'b\\c\x00d" on mariadb/postgresql/sqlite). JS/PHP/Python do the same.
- **Fix sketch:** refuse (`E_SQL_UNSUPPORTED` with a caveat) or emit the dialect's NUL spelling, in every host at once.

### GO-C44 [low] [unconfirmed, latent] A panic inside the optimizer poisons `physOnce` for every later Run
- **Found by:** go-3 C8.
- **Where:** `go/sel/program.go:56-61` and `63-76`. A panic inside `physOnce.Do` counts the Once as done; every later `Run` evaluates a nil `physicalAst`. ~170k programs through compile+optimise+run produced zero optimizer panics; only a synthetic test reproduces the second-call nil-pointer panic.
- **Fix sketch:** compute into a local and fall back to `p.ast` if the optimizer panics (recover inside the Once body), or optimise in `NewProgram`.

### GO-C45 [low] [unconfirmed, latent] `optRenameVar` copies nodes with stale `MathPlan` pointers
- **Found by:** go-3 C9.
- **Where:** `go/sel/optimizer.go:515-533` (`copyNode` keeps `MathPlan`; `joinReadSelf` in `join_prefilter.go:204-206` correctly nils it).
- **What:** renaming a binder inside a predicate that already carries a compiled plan would leave LOAD_VAR/LOAD_LEAF pointing at the old binder. Unreachable today (the physical FILTER+FILTER merge admits only literals, the binder variable and `_K`, none of which yield a plan). No repro found.
- **Fix sketch:** `cp.MathPlan = nil` in `optRenameVar`.

### GO-C46 [low] [unconfirmed] Join prefilter report can be left stale when a panic is recovered by `??`
- **Found by:** go-5 C7.
- **Where:** `go/sel/builtins_aggregate.go:945-946, 1276, 1301-1303` (`ctx.JoinPrefilterReport` set at the end of `doLink`, consumed by the parent).
- **What:** `EvalNode` restores Depth and Frames on panic but not `ctx.JoinPrefilter`/`JoinPrefilterReport`. If an inner LINK finishes and its parent then fails on the right side, a surrounding `??` swallows the error and the report stays for the next unrelated LINK to pick up as `below`, re-evaluating its left node (visible if the left has an assignment). The reviewer could not build a program reaching it: hygiene observation.
- **Fix sketch:** reset both fields in the EvalNode defer or the `??`/`???` recovery path.

### GO-C47 [low] [reasoned] Blanket `recover()` can mask genuine Go runtime panics as "error kept"
- **Found by:** go-5 (soundness note on `evalBoolSafely`/`canonicalJoinKey`), go-7 (`hybrid.go:923` recovers ANY panic when probing a prefix under an identity barrier where JS swallows only SqlError).
- **What:** no such panic was found in these paths, so nothing is wrong today; but a real Go bug (nil deref, index out of range) inside those probes would be silently absorbed as "skip this split" / "error kept", hiding it from tests.
- **Fix sketch:** narrow the recovers to `*SelError` / `*SqlError` and re-panic anything else.

---

# Performance findings

Ordered by impact. Labels are the reviewers': measured (numbers observed), reasoned (from code), guessed.

## High

### GO-P1 [high] [measured] LINK / LINK_LEFT with `equality AND residual` falls back to the O(n*m) nested loop
- **Found by:** go-3 P1.
- **Where:** `go/sel/builtins_aggregate.go:673-693` `extractJoinEqui` (requires the whole predicate to be a single `==`/`$==`); consumer `doLink` 987-1296 vs 1298-1358.
- **Why slow:** as soon as the predicate is `l["k"] == r["k"] AND <anything>` (the common shape), every left x right pair is evaluated through `EvalNode` (~2.6 us per pair). The hashed equi path is 30x faster on the same data. All hosts share the structure.
- **Evidence:** 1500 x 1500 rows, 10 distinct keys, `COUNT(LINK(L, L, l, r, ...))`: `l["a"] == r["a"]` alone 0.18 s (225000 rows); `... AND l["c"] < r["c"]` 5.83 s; `... AND l["c"] == r["c"]` 5.63 s. JS: 4.05 s / 9.59 s / 8.43 s. HEAD Go: 20.7 s for a 3000 x 3000 variant.
- **Fix sketch:** peel a *leading* equality conjunct off a left-nested AND chain as the hash key and evaluate the remaining conjuncts per bucket pair. Semantics are identical because AND short-circuits left to right; keep the `checkJoinPair` bad-key/E_NOT_NUM handling. A non-leading equality cannot be hoisted.

### GO-P2 [high] [measured] Sorting re-classifies both keys on every comparison (LooksNumeric re-parses non-numeric text each time; reflection-based sort)
- **Found by:** go-5 P1.
- **Where:** `builtins_aggregate.go:83-151` (`compareValues`), `:233-242` and `:327-336` (`sort.SliceStable`); `value.go:448-470` (`LooksNumeric`: `defer recover`, and a failed `decimal.Parse` is never cached).
- **Why slow:** each comparison calls IsNull, LooksNumeric, AsDecimal, then `decimal.Cmp` on `*big.Int`; `sort.SliceStable` swaps 24-byte structs through `reflect.Swapper`; the same key is reclassified O(log n) times.
- **Evidence** (100k rows, best of 3): `SORT(NUMS)` (ints) 839 ms; `SORT_BY(L,_["s"])` (non-numeric text) 813 ms with 3.5M allocations; `SORT_BY(L,_["s"],"DESC")` 814 ms. Prototype: classify each key ONCE into a small struct (null flag, `*Dec` + int64 fast path when scale 0 and digits fit, bool, byte slice, rank), then `slices.SortStableFunc` (same insertion-block + symMerge algorithm, so identical output even for the intransitive comparator of GO-C23): ints 839 -> 193 ms, non-numeric text 813 -> 211 ms with 200k allocations. Byte-identical on 300 random mixed-type lists. `slices.SortFunc` (pdqsort + original-index tie-break) gave 101 / 164 ms but is only equivalent for a consistent comparator: do it after GO-C23 is settled.
- **Fix sketch:** as prototyped; keep `LooksNumeric` semantics; memoise the negative result.

### GO-P3 [high] [reasoned + measured baseline] TOP / TOP_BY / TOP_DESC do a full O(n log n) stable sort to return k rows
- **Found by:** go-5 P2.
- **Where:** `builtins_aggregate.go:327-345`.
- **Evidence:** `TOP(NUMS,10)` over 100k costs 975 ms vs 839 ms for SORT (measured baseline). Expected gain with pre-classified keys and a size-k selection (heap on (key, original index), so ties resolve to input order) is order 10-50x for k << n; **not prototyped**, so the speedup is reasoned.
- **Fix sketch:** bounded max-heap (or select then sort the k winners) when `limit < len(items)`; keep the full stable sort when `limit` is large (say > n/8). Same comparator caveat as GO-P2.

### GO-P4 [high] [measured] Non-equi LINK/LINK_LEFT rebuilds the right-side alias record for every (left,right) pair
- **Found by:** go-5 P3.
- **Where:** `builtins_aggregate.go:1339-1345` (`ensureRowTableAlias(rEntry.Val, b2)` inside the inner loop; `strings.ToLower(b2)` per pair).
- **Evidence:** `LINK(TAKE(L,1000), TAKE(R,1000), X, Y, X["k"]<Y["k"])`: 1.75 s, 9.07M allocations, 518 MB. With right aliases hoisted out of the left loop (and ToLower once): 0.43 s, 1.07M allocations, 160 MB (4x). `LINK(...,X,Y,TRUE)` 2.09 s -> 0.57 s. Output identical on 90 LINK cases.
- **Fix sketch:** `rightAliased := make([]*Value, len(rightEnts))` before the left loop. The equi path already does this once per right row (line 1006).

### GO-P5 [high] [measured] RMATCH / RFIND / RGROUPS compute every match, a per-byte offset table and three copies of the subject
- **Found by:** go-4 P1.
- **Where:** `go/sel/builtins_regex.go:386-433` (`findMatches`) called from `:495, :505, :519`; `regexArgs` at `:435-451`; `foldSubject` `:366`.
- **Why slow:** every call converts the subject to `[]rune` (4 B/char), optionally `foldSubject` (another 4 B/char), back to `string`, allocates `byteToRune := make([]int, len(s)+1)` (8 B/byte), and runs `FindAllStringSubmatchIndex(..., -1)` even though RMATCH needs a boolean and RFIND/RGROUPS need the first match.
- **Evidence:** `RMATCH('.', REPEAT('123-45,', 500000))` (3.5M chars): Go 4.30 s vs JS 0.126 s (34x). Micro-benchmark on 700 KB: 759 ms and 306 MB / 1.4M allocations per call. Short subject: `findMatches` 2.8 us / 400 B / 6 allocs for `"123-45"` vs 0.53 us / 0 allocs for `regexp.MatchString`.
- **Fix sketch:** RMATCH -> `re.MatchString(s)` on the original string. RFIND/RGROUPS -> `FindStringSubmatchIndex`, converting only the first match's byte offsets with `utf8.RuneCountInString(s[:off])` (ASCII fast path). RREPLACE -> iterate byte offsets and splice from the original string. `foldSubject` is unnecessary: Go's `(?i)` already folds U+212A to `k` and U+017F to `s` (verified: `(?i)k` matches "K", `(?i)s` matches "ſ", `(?i)[^k]` does not match "K").

### GO-P6 [high for compile time of large rules] [measured] Front-end throughput: `matchOperator` allocates, the token slice is unsized, `posAt` binary-searches
- **Found by:** go-1 P1, P2, P3, P4 (P4 also go-4 P8, go-5 P7).
- **Where:** `go/sel/lexer.go:176-194` and `:168` (`[]rune(op)` per probe over a 34-entry table); `:104` (`make([]Token, 0, 32)`, Token is 56 B); `:86-101` (`posAt`, called per token at `:127` and eagerly before `fail(...)` at `:264, 303, 342, 364, 197, 226`); `go/internal/utf8/utf8.go:132-140` (`AsciiUpper`: two allocations even when already upper case; used at `lexer.go:152` per ident and `registry.go:78` per `Lookup`).
- **Evidence** (436 KB / ~135k-token generated program, `go test -bench Tokenize`): 261 ms/op (1.7 MB/s), `stringtoslicerune` 18% flat, `matchOperator` 27% cumulative. Precomputing operator runes (or byte compare; all operators are ASCII): 129 ms. Then presizing `out` to `l.n/3+32`: 129 -> 57 ms and 55.8 -> 22.9 MB/op. A monotone `posAt` cursor: 57 -> 48 ms. Lazy failure positions and the `AsciiUpper` scan-first fix are reasoned, not measured. Overall parse throughput is ~0.6 us/token (78 ms for the 135k-token program), GC-dominated.
- **Fix sketch:** `var operatorRunes` at init (or dispatch on the first rune); presize tokens (and/or shrink Token: `TokenType` as uint8, `Pos` as an int32 offset resolved on error); forward-only `posAt` cursor and failure-only `Pos`; `AsciiUpper` returns `s` unchanged if no `a-z`.

## Medium

### GO-P7 [medium] [measured] `??` / `???` on a missing field pays panic + recover + Sprintf (~6x slower than a hit)
- **Found by:** go-3 P2.
- **Where:** `go/sel/eval.go:193-215`; `fail()` in `errors.go` builds `fmt.Sprintf("no key %q")` before panicking (`eval.go:107,112,71,81`).
- **Evidence:** 200k records: `COUNT(MAP(L, _["zz"] ?? 1))` 277-350 ms vs `_["a"] ?? 1` (present) 46-60 ms, ~1.2 us extra per miss (profile: Fail 25%, gorecover/unwinder ~20%). No HEAD baseline (HEAD errors with E_DEPTH).
- **Fix sketch:** when `node.L` is a NodeVar/NodeIndex chain, resolve it with a non-panicking "try" walk and fall back to the recover path for other node kinds; make the error message lazy. Codes/positions unchanged.

### GO-P8 [medium] [measured, noisy] Per-node `defer` closure in `EvalNode` (added by the depth/frame fix)
- **Found by:** go-3 P3.
- **Where:** `go/sel/eval.go:31-52`.
- **Evidence:** 3 alternating runs of 200k-row loops, current vs a copy with the defer removed: filter-and 182/177/138 ms vs 127/135/130; map-math 229/235/244 vs 204/217/229; map-if 170/175/155 vs 142/150/187; map-coalesce-ok 59/67/61 vs 48/50/47. Roughly 5-25% on a shared box (upper bound). Against HEAD the current tree was not slower on filter-and/map-math (noise; other work changed too).
- **Fix sketch:** restore `ctx.Depth`/`ctx.Frames` at the five `recover()` sites (`??`, `evalBoolSafely`, `canonicalJoinKey`, ...) from values saved before the protected call: same semantics, zero per-node cost.

### GO-P9 [medium] [measured] Small-number arithmetic is allocation-bound: 4-6 allocations per op; `Make` copies every result
- **Found by:** go-2 P3.
- **Where:** `go/internal/decimal/dec.go:49-60` (`Make` does `new(big.Int).Set(digits)`), `Negate`/`Abs` (`232-238`) allocate two big.Ints for a sign flip, `Parse` (`123`), `go/sel/value.go:44-60` (`Value` is 152 bytes).
- **Evidence** (300k-500k iterations): AddSmall ~600-1100 ns / 6 allocs / 192 B; MulSmall 640 ns / 4 allocs; Negate 363 ns / 3 allocs; Parse("12345.67") 1034 ns / 6 allocs; Div 1268 ns / 10 allocs; TrimScale 1515 ns / 7 allocs; CmpDiffScale 521 ns / 2 allocs; `NewInt` 396 ns / 3 allocs / 232 B. End to end `SUM(L,_)` over 200k ints 750 ns/element; `SUM(L,_*2+1)` 2.2 us/element. A non-copying `makeOwned` removed one alloc per op: Mul 480-640 -> 360-380 ns, Add within noise (20-40% on Mul; not the whole story).
- **Fix sketch:** (1) `makeOwned` for fresh ints; make Negate/Abs share `Digits` (Dec is immutable; no in-place mutation of `.Digits` outside dec.go); (2) a compact representation (magnitude fits uint64 and scale <= 18: keep `mag uint64`, allocate a big.Int only on overflow; ~10 ns and allocation-free for Add/Sub/Mul/Cmp/Round); (3) `ParseUint` fast path for <= 19 digits; (4) shrink `Value` by moving rarely used fields behind one pointer. Re-run the 390k differential.

### GO-P10 [medium] [measured] Parsing a large numeral is quadratic (`big.Int.SetString`)
- **Found by:** go-2 P1; go-1 P5 (related).
- **Where:** `go/internal/decimal/dec.go:175-176` (`digits.SetString(stripped, 10)`), reached from `Parse`, hence every text-to-number coercion and every literal.
- **Evidence:** microbench `SetString` of 999,999 digits 2.5 s (7 s in one noisy run) vs a divide-and-conquer parse (split in halves, combine with `Pow10(len(lo))`, 2000-digit leaves) 0.44 s, byte-identical; at 100,000 digits 63 ms vs 28 ms. End to end `X = REPEAT("7", 999999); LEN(X + 1)`: Go 3.35 s, JS 1.18 s, PHP 0.42 s, C++ 0.03 s. `Format` on the same number is 0.5-0.7 s (already sub-quadratic). go-1: a 1,000,000-digit literal compiles in 2.7 s in Go vs 0.87 s JS and 0.03 s C++.
- **Fix sketch:** recursive halving above ~2-4k digits using the existing `Pow10` cache.

### GO-P11 [medium] [measured] Keyed lists (the result of FILTER when any element is dropped, and join results) have O(n) `Get`/`Has`/`Set`
- **Found by:** go-2 P2.
- **Where:** `go/sel/value.go:184-193, 214-222, 257-263` (linear scans over `listKeys`); produced by `builtins_aggregate.go:1546` (FILTER) and `:1287` (join result).
- **Evidence:** 20,000-element keyed list, `SUM(INDEXES(K), K[_])`: 1.08 s vs 30 ms on a dense list (36x); ~27 s extrapolated at 100k.
- **Fix sketch:** lazily build a `map[string]int` on first `Get`/`Has`/`Set` at `len(listKeys) >= 16`, as `rebuildIndex` does for entries; clone/drop consistently in `CloneAt`.

### GO-P12 [medium] [measured allocs, reasoned savings] Per-node and per-walk allocation: literals, Args, scratchpad, and `Elements()`/`Entries()` key strings
- **Found by:** go-3 P4, go-2 P6, go-5 P6, go-5 P9.
- **Where:** `eval.go:56-66` (each NodeNum/NodeText/NodeBool evaluation allocates a Value), `:136` (`NewArgs` allocates the struct and `vals` slice per call, lazy or not), `:491` (`make([]*Dec, ScratchpadSize)` per math evaluation); `go/sel/value.go:346-370, 680-708, 646-678` (`Entries`/`Elements`/`structuralHashAt` build a `[]Entry` and a `strconv.Itoa(i+1)` string per element past 99); `builtins_aggregate.go:72` (`frame["_K"]` map lookup per element), FILTER appends with no capacity hint then `NewListWithKeys` copies again, `:1339-1352` per-pair frame map assignments.
- **Evidence:** allocations/element over 1000 records: `COUNT(MAP(L, 1))` 1.93; `_["a"]` 0.93; `_["a"] > 4` 2.93; `_["a"] * 2` 5.73; `_["a"]*2 + _["c"]` 10.7; `IF(_["a"] > 4, 1, 2)` 5.93; `RECORD("x", 1)` 9.9. CPU profiles of filter/map loops are 45-50% mallocgc + GC scan (go-5: 55-60% GC in every data scenario). `Entries()` on a 100-element list 4.8 us / 2.7 KB / 2 allocs; `COUNT(MAP(L,_))` over 100k = 99,943 allocations; `COUNT(SORT(NUMS))` shows the same 99,917 before the sort starts. `_K` creation goes through `NewText`, which re-validates UTF-8 (`:73`).
- **Fix sketch** (byte-identical, each needs care): Args with a small embedded `[4]*Value` array or per-ctx free list; scratchpad on the stack for `ScratchpadSize <= 16`; iterate `storage` directly and materialise the key text only when `_K` is used or FILTER needs custom keys; hoist the `_K` presence test to a bool; skip key compare on `v.shape == other.shape` in `EqlAt`; hash BIN without `string(binVal)`; `NewTextOwned` for `_K`. Sharing immutable TRUE/FALSE/literal Values needs a clone-on-return or read-only flag because `Program.Run` hands results to a host that may `Set` into them (guessed 10-20% on scalar-heavy loops, not measured). The bigger lever inside math plans is GO-P9.

### GO-P13 [medium] [measured cost of the copy, reasoned fix] Assignment deep-copies a fresh right-hand side
- **Found by:** go-3 P5.
- **Where:** `go/sel/eval.go:406` (`EvalNode(node.R).CloneAt(1, node.Pos)`).
- **Evidence:** `X = MAP(L, RECORD("x", _["a"], "y", _["c"]))` over 200k rows: 292-302 ms vs 203 ms for the MAP alone (the clone is ~1/3 of the statement); `X = L` alone 74 ms (pure clone).
- **Fix sketch:** skip the clone when the RHS provably has no other owner (constructor results that already cloned their inputs, not a variable read, IF branch, `_` etc.): a "fresh" bit on Value or a static provenance walk. Spec 3.4 requires only the observable copy.

### GO-P14 [medium] [measured] Record construction: uniqueness map, signature string and `reflect.DeepEqual` per call
- **Found by:** go-2 P5, go-5 P4, go-5 P5.
- **Where:** `go/sel/shape.go:40-83` (`UniqueRecordShape` allocates a `seen` map per call; `InternRecordShape` builds a signature with `strconv.Itoa` + Builder), called from `NewRecordFromEntries` (`value.go:134`); `builtins_aggregate.go:471-503` (`ensureRowTableAlias` recomputes the target shape for every row of every LINK: two allocations for the keys slice, one map, one Builder, the cache lookup); `builtins_structure.go:59-83` (RECORD with literal keys still evaluates the key nodes, builds a keys slice and calls `reflect.DeepEqual(shape.Keys, keys)` per row, although `parser.go:81-93` already precomputes `node.Shape` when every key is a literal).
- **Evidence:** 3-key `UniqueRecordShape` 400 ns; `NewRecordFromEntries` 3 keys 2.7 us vs `NewShapedRecord` with a cached shape 0.76 us. `COUNT(LINK(L,R,_1["k"]==_2["k"]))` (100k x 100k, 500k output rows): 870 ms / 2.88M allocs; with a per-(shape,name) memo (sync.Map keyed `{*RecordShape, name}`) 728 ms / 1.88M allocs (-16% time, -35% allocs; the prototype even called `os.Getenv` per row). scale-bench scenario1: `ensureRowTableAlias` 15% of CPU (1.36 s of 9.1 s), `UniqueRecordShape` 8%. RECORD fast path: `COUNT(MAP(L,RECORD("a",_["v"],"b",_["k"])))` over 100k: 107 ms / 1.0M allocs -> 84-94 ms / 0.7M allocs (20% faster, 30% fewer allocs).
- **Fix sketch:** look the signature up first (a hit proves uniqueness), linear duplicate scan for <= 8 keys, `strconv.AppendInt` into a stack buffer; memoise per (source shape pointer, table name); `if sh := args.RecordShape(); sh != nil { evaluate values left to right; return NewShapedRecord(sh, values) }` (key literals cannot fail or have effects).

### GO-P15 [medium] [measured] FIND is naive O(n*m) on `[]rune` copies
- **Found by:** go-4 P4.
- **Where:** `go/sel/builtins_text.go:123-154`.
- **Evidence:** `FIND(REPEAT("a",100000) & "b", REPEAT("a",200000))` = 15.0 s in Go (JS 25.5 s: a cross-host DoS shape, cheap to remove in Go).
- **Fix sketch:** convert the 1-based rune `from` to a byte offset (ASCII fast path), `strings.Index`, then `utf8.RuneCountInString(hay[:idx])+1`. Identical for valid UTF-8.

### GO-P16 [medium] [measured] SQL `Emit.Fill` builds templates one byte at a time by string concatenation; `Lexical()`/`Entry()` rebuild the dialect chain per lookup
- **Found by:** go-7 P1, P2.
- **Where:** `go/sel/sql/emit.go:246-255` and `:291-293` (`push(string(tpl[i]))` then `parts[len-1].Sql += s`); `go/sel/sql/map.go:362-379` (`Chain`), `:392-405` (`Lexical`), `:407-444` (`Entry`), each allocating a `seen` map and slice and locking `mapMu` twice; callers include Ident, TextLiteral (two Lexical calls per literal), Placeholder, every `{key}` slot in Fill, FormatLiteral, translator `apply`.
- **Evidence:** benchmark (60 x `(AMT + i > 3 AND NAME $== "it's i" AND LEN(NAME) < j)`, mariadb, 200 iterations): 3.6-4.2 ms per translate+render, output 10,648 bytes; pprof of a 180-predicate rule: `Emit.Fill` 35% cumulative, concatstrings 11%, growslice 22%, GC ~25%, Chain 14%, Lexical 10%. With both fixes: 2.25-2.45 ms (about 38% faster; the Chain cache alone about half of that); `sqlt` still 1065/1065 (byte-identical). PlanHybrid on a 3-step pipeline 46-69 us -> 31-34 us.
- **Fix sketch:** push the run of plain bytes up to the next `{`/`}` at once (also the GO-C24 fix); cache the chain per dialect (invalidate in DefineDialect/Reset/Define, never cache "unknown dialect").

### GO-P17 [medium] [measured] SQL pairwise folds are quadratic: `x IN (<n literals>)`, aggregate unrolls over n elements
- **Found by:** go-6 P1.
- **Where:** `go/sel/sql/translator.go:1150-1157` (`foldPairwise`) -> `apply` -> `Emit.Fill` (splice at `emit.go:244-258`) copying `acc.Parts` at each step; used by `inOperator` (`:1525`), `aggregate` (`:2231`), `count`, `joinAggregate`.
- **Evidence** (`sql.Translate` mariadb, compile excluded): `A IN (0..n-1)`: n=1000 0.09 s, n=4000 0.88 s, n=8000 3.5 s, n=16000 15 s (about 4x per doubling); output only 0.8 MB at n=8000. pprof: 33% in `Emit.Fill` splice closures, the rest GC scan/memclr. JS quadratic too but ~2x faster (4000: 0.41 s).
- **Fix sketch:** obtain the operator template shape once (fill it with two sentinel fragments, split into prefix/infix/suffix) and emit `prefix*(n-1) + p0 + (infix + p_k + suffix)...` in one pass; or build the accumulated fragment as a rope and flatten once. Byte-identical.

### GO-P18 [medium, only with big contexts] [measured] `ExecuteHybrid` deep-clones the whole caller context on every execution
- **Found by:** go-7 P3.
- **Where:** `go/sel/sql/hybrid.go:986` (`context.Clone()`).
- **Evidence:** a context with a 50,000-record table (3 fields each) plus small ORDERS: `Clone()` 40 ms; 5 `ExecuteHybrid` calls with a trivial continuation 168 ms (~34 ms each), measured with the GO-C5 fix applied (before it the clone never happens). JS does the same.
- **Fix sketch:** shallow-copy the root (same child handles plus `_INPUT`) when the continuation has no assignment targets rooted in caller names (`Dependencies()` separates reads from assignments); else Clone.

### GO-P19 [medium] [measured] Regex per-call overhead: `fmt.Sprintf` cache key + global RWMutex; and the cache is unbounded
- **Found by:** go-4 P2, P3.
- **Where:** `go/sel/builtins_regex.go:324-330` (key `fmt.Sprintf("%v:%s", ignoreCase, pattern)`, then a shared RWMutex), `:293-296, 359-361` (process-global map, never evicted).
- **Evidence:** `compileRegex` cache hit 1.04 us / 40 B / 2 allocs vs ~30 ns for a raw map lookup keyed by a two-field struct. `MAP` over 200,000 items with `RMATCH('^\d{3}-\d{2}$', _)` 0.76 s total (JS 0.89 s; an empty `FILTER` of the same list 0.17 s), so the regex path costs ~3 us/item, ~6x the engine. Unbounded: 100,000 distinct small patterns -> 100,000 entries and 238 MB retained heap (~2.4 KB/entry) after GC, a slow leak / DoS lever for runtime-computed patterns.
- **Fix sketch:** key `struct{ic bool; p string}`; resolve literal patterns once per AST node; bounded LRU (512-4096 entries) or cache only literal patterns.

### GO-P20 [medium] [measured] `[]rune` round-trips in TRIM / LEFT / RIGHT / SUBSTR / CODE / BACKWARDS / PADL
- **Found by:** go-4 P5.
- **Where:** `go/sel/builtins_text.go:16-31` (trimText), `69, 83, 97, 240, 299, 34-51`.
- **Evidence:** `LEN(TRIM(<80 MB ASCII>))` 2.19 s vs 0.31 s without TRIM (~1.9 s for scanning a few edge bytes; JS 14.5 s). `LEN` itself is fine. `CODE(s)` converts the whole string to read `runes[0]`.
- **Fix sketch:** TRIM* by byte scan (all four trimmed chars are ASCII); CODE via `utf8.DecodeRuneInString`; LEFT/RIGHT/SUBSTR by walking to the k-th rune boundary with an ASCII fast path; PADL/PADR with `strings.Builder` and known rune counts.

## Low

### GO-P21 [low] [measured] `Guard` recomputes 10^N to count digits on maximum-size values; `TrimScale` and `Cmp` round-trip through strings / aligned copies
- **Found by:** go-2 P4, P7.
- **Where:** `dec.go:99-121` (`numDigits` calls `Pow10(d-1)` up to twice; values above 1,000,000 digits are never cached; the cache is flushed when weight passes 1,048,576); `dec.go:198-217, 331-352, 250-256` (TrimScale, Cmp, IsInteger).
- **Evidence:** a legal 2,000,000-digit / scale-1,000,000 value costs ~200 ms per `Guard` (i.e. per Add/Mul/Round) vs ~1 ms for the addition (`Add d+d` 195 ms, `Add d+1` 390 ms). TrimScale of a 1M-digit value 760 ms (String then SetString), 1.5 us for a 10-digit one; `Cmp` with different scales allocates a scaled copy (521 ns / 2 allocs small, ~100 ms with a 1M-digit scale before the Pow10 cache warms).
- **Fix sketch:** bound digit count from `BitLen` (`lo = floor((bitLen-1)*log10 2)+1`, `hi = floor(bitLen*log10 2)+1`) and call `Pow10` only when `[lo,hi]` straddles the cap; strip trailing zeros by chunked `QuoRem`; compare different-scale operands by (BitLen/numDigits - scale) first.

### GO-P22 [low] [reasoned] `nodeContainsVar` walks the whole body AST (and allocates via `AsciiUpper` per Var node) on each aggregate call
- **Found by:** go-5 P7.
- **Where:** `builtins_aggregate.go:17-46`, called from `:64, :215, :301, :390`.
- **Why:** the answer depends only on the node; nested aggregates (`MAP(orders, SUM(lines, ...))`) redo the walk per outer row. Identifiers are already upper-case at lex time (spec 2.3), so `node.S == "_K"` suffices. Not measured.
- **Fix sketch:** compare `node.S == "_K"`; cache the walk result on the body Node (compile-time or atomic bool).

### GO-P23 [low] [reasoned/measured mix] Redundant UTF-8 re-validation, binary helpers, `AsciiUpper`/`AsciiLower` double copy
- **Found by:** go-4 P6, P7, P8.
- `builtins_text.go:167 (REPLACE), :184 (SPLIT), :255 (REPEAT)` call `NewText`, which runs `utf8.ValidateText` (a per-rune `range` loop, ~2-3x slower than `unicode/utf8.ValidString`) on results built from valid inputs. Reasoned only (REPLACE of 50M chars measured 2.1 s total). Use `NewTextOwned`.
- Binary (measured): ENCODE+DECODE of a 16 MB buffer 0.94 s (JS 6.0 s, acceptable); `BTL` of a 4 MB BIN 1.7 s (0.4 us/byte, one decimal allocation per element; a shared cache of the 256 byte-valued Values needs the aliasing rule checked first). `builtins_binary.go:105-118` ENCODE_BASE64 unsized `append`; `:122-165` DECODE_BASE64 `map[byte]int` lookup per char and unsized `append`; `:185-189` BTL; keep the hand-written strictness (Go's `encoding/base64` Strict mode would change acceptance of non-canonical trailing bits such as `YR==`, which all hosts accept). Fix: `[256]int8` table, `make([]byte, 0, n)`.
- `AsciiUpper`/`AsciiLower` (`utf8.go`): `[]byte(s)` then `string(b)`, no early exit; negligible except on very large text.

### GO-P24 [low] [measured] One-shot `Eval(src)` pays for optimisation it cannot amortise
- **Found by:** go-3 P6.
- **Where:** `go/sel/program.go:170-176` (`Eval` = Compile + Run, with Run calling `OptimizeAST`).
- **Evidence** (go test -bench, 20000x): Parse `X + 1` 2.8 us, Optimize 1.1 us, run 0.62 us (plain unoptimised evaluation 0.55 us); medium expression: parse 15.3 us, optimize 4.0 us; pipeline: parse 22.9 us, optimize 5.9 us. Optimisation is ~25% of a run-once total. For repeated runs the plan pays off: `A*2 + B%7 - C` 1.32 us plan vs 2.45 us plain; the 3-op-per-side expression 4.5 vs 5.8 us; the 1-op `A + 1` is break-even (518 vs 515 ns).
- **Fix sketch:** `Eval`/`MustEval` evaluate `p.AST()` directly (spec 8: the physical tree is a function of the AST only), or skip planning when there are < 2 operators.

### GO-P25 [low] [reasoned] SlotCache miss path allocates on every polymorphic site
- **Found by:** go-3 P7.
- **Where:** `go/sel/eval.go:100-104` (`node.SlotCache.Store(&SlotCache{...})` on every shape miss).
- **What:** records with alternating shapes reallocate a 16-byte cache entry and hammer one atomic store per evaluation; concurrent users of one Program bounce the cache line. Not measured.
- **Fix sketch:** count misses and stop storing after a few (megamorphic), or store only when the old entry is nil.

### GO-P26 [low] [measured] DISTINCT/BUCKET hashing; structural hash quality
- **Found by:** go-5 P8.
- **Where:** `value.go` `structuralHashAt` (`v.Entries()` per container, `Scalar()` formats decVal on first use); `builtins_structure.go:210-235`; `builtins_aggregate.go:410`.
- **Evidence:** `DISTINCT(L)` 100k records 78 ms / 300k allocs; `DISTINCT(NUMS)` 65 ms / 200k allocs; 2-arg `BUCKET(L,_["k"])` 126 ms / 612k allocs (mostly the mandatory Clone of each row). Adequate. The hash is a homegrown FNV/multiply-xor combine with a fixed seed; equal-hash records degrade a bucket to a linear Eql scan. Not exploited (no hash-flooding finding).
- **Fix sketch:** hash shaped records via `shape.Keys` + `storage` without building Entries; precompute the key hash once per shape.

### GO-P27 [low] [measured] Parser hot loop details; numeric literal cost (null result)
- **Found by:** go-1 P5, P6.
- `infixEntry` does a map lookup (`mapaccess2_faststr`, ~5% of parse) on every loop iteration of `parseTerm`; a byte/enum set by the lexer would remove it. `defer p.leave()` is open-coded and cheap. 1M-term `1+1+...+1` compiles in 2.3-3.6 s (2.6 s with GO-P6 applied); nothing algorithmic wrong.
- Numeric literals cost ~10 allocations each (`1 + 12.5 + ...` x 100k: ~200 ms, ~1.0M allocs). Replacing `decimal.Format(parsed)` with a text canonicaliser (verified equal on 200k random literals) removed 200k allocs but wall time was within noise (best-of-5: 154 vs 158 ms): **not recommended**. GC/allocation of the node tree dominates (Node ~176 B; `NewNode` 40% of parser alloc bytes; each NodeIndex allocates an `atomic.Pointer`).

### GO-P28 [low] [measured / reasoned] Smaller SQL costs
- **Found by:** go-6 P2, P3; go-7 P4, P5.
- **P2 (measured):** `translator.go:252-259` validates `IsConstant(n)` then `Validate(n)` for every compound constant node, and `ToNode` (`node.go:135-181`) deep-copies each time: a depth-195 chain `1+1+...+1` costs 38 ms per translate vs ~3 ms with a column; `SUM((n literals), _ + 1) > 0` at n=1000 113 ms, n=4000 2.0 s. Bounded by MAX_DEPTH for chains. Fix: validate only the maximal constant subtree.
- **P3 (reasoned):** `Translator.binder()` (`translator.go:310-320`) heap-copies the ~200 B `Binder` per variable lookup; `RelationSpec.Field` (`binding.go:59`) uppercases and returns a pointer to a heap copy per call; `BuildJoinRows` (`row_model.go:206`) is rebuilt per `withRow`/`withJoinBinders`/`joinedRowFields` call; `constScope` re-sorts `Bindings.Names()` on every Begin. Not visible at realistic sizes (the whole 1065-case suite runs in 0.33 s).
- **`TextLiteral` (measured):** sorts its escape keys and scans byte-by-byte with `HasPrefix` per key on every call: 100,000 short literals ('hello', mariadb) 193 ms = 1.9 us each; a 1.1 MB text 40 ms (mariadb), 25 ms (postgresql), ~36 ns/byte. Fix: a `strings.Replacer` (or byte table, all keys are one byte in shipped dialects) cached per dialect.
- **Hybrid (reasoned, unmeasured):** `referencedAssignments` re-runs `readNames` per kept assignment on every fixpoint pass (O(k^2 * size)); `helpers.wrap` is rebuilt for every candidate split; `PlanHybrid` retries `TryTranslateStatement` for each prefix length from the longest down (s failed full translations for an s-step pipeline whose only translatable prefix is short). PlanHybrid measured 30-70 us for small pipelines.

### GO-P29 [low] [reasoned] Go GC configuration dominates large-context workloads
- **Found by:** go-5 P10.
- **Evidence:** every scale-bench scenario spends 55-62% of CPU in `gcDrain`/`scanObjectsSmall` (pprof). Embedders with a large resident context can raise `debug.SetGCPercent`; the library could document this (compare Python's `_gc.py` note in contributing.md). No code change; it is why the allocation cuts above pay off twice.

---

# Cross-host findings

Issues the reviewers report as shared with other hosts. Per CLAUDE.md "The one rule": spec first, then conformance cases, then every host, then `tools/check.sh`. When a fuzzer/reviewer finds a disagreement, add the minimal `.selt`/`.sqlt` case before fixing any host.

## Shared defect: fix in all hosts together

| Go id | Issue | Hosts affected (per reviewers) |
| --- | --- | --- |
| GO-C3 | `SUM(group, body)` in BUCKET projection loses literals (Go emits `* )`; JS silently emits `* 1`, a wrong expression) | Go, JS (others not checked by go-6) |
| GO-C4 | SQL stage-1 aliasing of indexed-assignment lists | Go, JS |
| GO-C15, GO-C16 | UNKNOWN unifies with anything; SUM over UNKNOWN unguarded | Go, JS |
| GO-C17 | SQLite MIN/MAX storage-class compare (generated map) | all hosts |
| GO-C20 | assignment inlining exponential in the SQL translator | Go, JS |
| GO-C21 | helper-expansion blow-up in the hybrid planner (the call-fall-through part is Go-only) | Go, JS |
| GO-C22 | hybrid split before LINK renames binders (`_INPUT`) | Go, JS |
| GO-C37 | program-derived aliases skip empty/NUL identifier checks | Go, JS |
| GO-C43 | raw NUL in inline text literal | Go, JS, PHP, Python |
| GO-C11 | huge REPEAT/PAD/JOIN sizes (host crash / hang) | Go (fatal/panic), JS (RangeError), C++/Lisp/PHP (hang/OOM); needs a spec cap |
| GO-C13 | lexer quadratic + unbounded recursion on nested interpolation | JS (RangeError at d=2000), C++ (segfault at d=16000), Go (88 s at d=64000, stack overflow at 16 MB) |
| GO-C14 | exponential value growth via `A = (A, A)` | all hosts (Go: fatal OOM) |
| GO-C12 | CEIL/FLOOR carry beyond 1M digits | Go and C++ lack the guard; JS/PHP raise it but at "line 0 column 0" (no node position, against 6.3) |
| GO-C23 | SORT comparator intransitive; Go, C++ and Lisp give three different orders | spec gap, all hosts |
| GO-C26, GO-C27 | live vs snapshot iteration; SORT/BUCKET aliasing vs contributing.md's "cannot be observed" | all hosts (JS also crashes with a TypeError on `MAP(A,(A[4]=100)+_)`, for the JS report) |
| GO-C30 | RGROUPS capture retention (Go/PHP/Lisp/Python vs JS/C++) | spec gap |
| GO-C31 | `\d\w\s` next to `-` in a class produces accidental ranges | all six hosts agree; spec change |
| GO-C33, GO-C34, GO-C35 | optimiser/math-plan observability: wrong Pos after fusion, SORT+TAKE(0) skips key errors, coercion-order in math plans | all hosts agree with Go's physical result; spec decisions |
| GO-C38 | SQL TAKE/DROP count beyond 2^53 | Go refuses (`E_RANGE`), JS emits wrong digits |
| GO-C25 | ROUND/POWER coerce argument 2 first | Go and C++ |
| GO-C8 | scalar source for TOP/BUCKET: TOP wrong in Go and Lisp (right in JS/PHP/Python/C++); BUCKET wrong in Go/JS/PHP/Python/Lisp (right only in C++) | spec 7.3 conformance gap |
| GO-C28 | quantifier on an anchor | Go and Lisp accept; JS/PHP/C++/Python reject |

## Go-only divergences the other hosts agree against

- GO-C1 regex counted repeats above 1000 (RE2), GO-C10 RREPLACE empty-match abutting (RE2 `FindAll`), GO-C9 placeholder `_` first only, GO-C7 TAKE/DROP aliasing, GO-C29 LTB scaled integers, GO-C5 ExecuteHybrid context, GO-C2 binder nil-deref, GO-C19 binding option validation, GO-C24/GO-C39 `Emit.Fill`, GO-C18 map-order column order, GO-C36 nil function registration, GO-C41 manifest check at load.

## Side observations for other hosts' reports

- go-3: the prebuilt `cpp/build/batch` (built 11:34 that day) answers `TAKE(TAKE(LIST(1,2,3), 1.0), 5)` with all three elements and `DROP(DROP(LIST(1,2,3,4), 1.0), 1)` with `()`, while Go/JS/Python/PHP/Lisp answer `(1)` and `(3,4)`. Possibly a stale build; worth a look by the C++ reviewer (numeric literal `1.0` in the TAKE/DROP merge).
- go-5: `MAP(A,(A[4]=100)+_)` crashes the JS host with a TypeError.
- go-1: JS's and C++'s lexers have the same nested-interpolation blow-up as GO-C13.
- go-4: the cpp/lisp/php batch runners hang on `REPEAT("", 99999999999)` and `REPEAT("ab", 1e30)`.

# Suggested conformance cases

`.selt` unless noted; expected results are the spec-derived ones, and the ones marked "spec decision" cannot be added until the rule is chosen.

| Name idea | Expression | Expected |
| --- | --- | --- |
| `re.repeat.over-1000` | `RMATCH('^a{1001}$', REPEAT('a',1001))` | `TRUE` |
| `re.repeat.nested-product` | `RMATCH('^(?:a{1000}){2}$', REPEAT('a',2000))` | `TRUE` |
| `re.repeat.range-over-1000` | `RMATCH('a{0,2000}','')` | `TRUE` |
| `re.repeat.leading-zero` | `RMATCH('a{00001}','a')` | `TRUE` |
| `re.repeat.cap` | `RMATCH('^a{65535}$', REPEAT('a',65535))` | `TRUE` |
| `re.replace.empty-abutting` | `RREPLACE('a*', "-", "baac")` | `"-b--c-"` |
| `re.replace.empty-abutting-group` | `RREPLACE('(b*|c)','<$1>','baac')` | value pinned from the majority hosts |
| `re.anchor-quantifier` | `RMATCH('^*','a')` (also `$*`, `^+`) | `E_REGEX_SYNTAX` |
| `pipe.placeholder.repeated` | `LIST(1,2) .> LIST(_, _)` | `-{"1"=-{"1"=t"1","2"=t"2"},"2"=-{"1"=t"1","2"=t"2"}}` |
| `pipe.placeholder.repeated-arity` | `5 .> MAX(_, _, 3)` | `5` |
| `struct.take.detached-from-source` | `A = LIST(1,2,3); LIST(TAKE(A,2), A[1] = 9)` | `-{"1"=-{"1"=t"1","2"=t"2"},"2"=t"9"}` |
| `struct.drop.detached-from-source` | `A = LIST(1,2,3); LIST(DROP(A,1), A[2] = 9)` | `-{"1"=-{"1"=t"2","2"=t"3"},"2"=t"9"}` |
| `agg.top.scalar-source` | `TOP(5, 3)` | `-{"1"=t"5"}` |
| `rel.top-by.scalar-source` | `COUNT(TOP_BY(5, _, 1))` | `1` |
| `rel.sort-take.scalar-fusion` | `TAKE(SORT(5), 1)` | `-{"1"=t"5"}` |
| `agg.bucket.scalar-source` | `BUCKET(5, _, COUNT(_))` | `-{"1"=t"1"}` (spec 7.3; C++ is the only host that agrees today) |
| `limit.ceil.carry` | `CEIL(REPEAT("9",1000000) & ".5")` | `E_RANGE` at the CEIL call |
| `limit.floor.carry` | `FLOOR("-" & REPEAT("9",1000000) & ".5")` | `E_RANGE` at the FLOOR call |
| `eval.round.coerce-order` | `X="a"; Y="1"; ROUND(X, IF(Y=="1", "x", -1))` | `E_NOT_NUM` at the position of `X` |
| `eval.power.coerce-order` | `X="a"; Y="1"; POWER(X, IF(Y=="1", 1.5, 0))` | `E_NOT_NUM` |
| `eval.coerce.left-before-right-operand-error` (spec decision) | plan-shaped and non-plan-shaped variants of `X = "abc"; X + MISSING` | pinned either way |
| `opt.pos.take-take-under-operator` | `NOT TAKE(TAKE(LIST(1), 3), 2)` | `E_NOT_BOOL` at 1:5 (plus DROP/DROP, FILTER(TRUE), SORT+TAKE variants) |
| `rel.take.zero-sort-key-body` (spec decision) | `L .> SORT_BY(_["z"]) .> TAKE(0)` with `L = LIST(RECORD("a",1))` | pinned (`()` or `E_NO_KEY`) |
| `rel.sort.numeric-text-vs-text` (spec decision) | `LIST("2","10","1a","3","1b","20","2a") .> SORT() .> JOIN(",")` | a total order chosen in the spec |
| `agg.map.body-mutates-later-element` (spec decision) | `R = RECORD("a",1,"b",2); MAP(R, (R["b"] = 99; _))` | pinned |
| `lim.text-size.repeat` (spec decision) | `LEN(REPEAT("ab", 99999999999999999999))` | `E_RANGE` |
| `lim.text-size.pad` (spec decision) | `PADL("7", 318446744073709551616, "0")` | `E_RANGE` |
| `binary.ltb.scaled-integer` (spec decision) | `LTB((65, 1.0))` | `E_RANGE` (four hosts) or pinned otherwise |
| `re.groups.repeated-capture` (spec decision) | `RGROUPS('(?:(a)|b)+','ab')` | pinned |
| `re.class.shorthand-adjacent-dash` (spec decision) | `RMATCH('^[+-\d]$','5')` | `E_REGEX_SYNTAX` if the validator rejects it |
| `lim.interp-nesting-100` | 100 nested `"{ ... }"` interpolations | `E_DEPTH` at the same position in every host |
| `api host.fn.refuse.not-callable` (API probe) | `RegisterFunction("Zed", 0, 1, nil)` | refused at registration |
| `sqlt shape.binder.non-name-binder-is-refused` | `BUCKET("x", A[1], "y", 1)` (and the two statement forms) | `E_SQL_SHAPE` |
| `sqlt bucket.sum.literal-in-body` | `ITEMS .> BUCKET(_["dept"], RECORD("dept", _K, "t", SUM(_, _["amount"] * 2)))`; a variant with `IF(_["dept"] $== "x", 1, 2)`; inline and params modes | literals present in the SQL |
| `sqlt assign.indexed.copy-is-not-alias` | `R[1] = 5; X = R; R[2] = 6; COUNT(X)` | `1` (and the `X[2] = 7; COUNT(R)` twin) |
| `sqlt warrant.unify.unknown-branch-stays-unknown` | `IF(FLAG, 1, UNK) < 1` over an UNKNOWN column | mariadb: guard present; sqlite: `E_SQL_UNSUPPORTED` |
| `sqlt warrant.sum.unknown-body` | `SUM(T, _["U"])` with U UNKNOWN | guarded or refused |
| `sqlt limit.inline-expansion` | 40-step doubling chain `X1 = X0 + X0; ...` | refusal (documented cap) |
| `sqlt register.template.non-ascii` | `--- register` template `F({0}, 'é ż')` | template text intact |
| `sqlt register.template.empty-tail-and-empty-lexical` | `{1:}` past the end; lexical key `""` | pinned (JS/Python/PHP behaviour) |
| `sqlt pad.count-out-of-range` | `PADL("7", 318446744073709551616, "0") $== NAME` | refusal, no panic |
| `sqlt plan.deep-call-nesting` | 40-deep `ABS(...)` in a pushable pair next to `ABORT("x")` | plans in bounded time |
| executed hybrid plan | `X .> TAKE(1) .> LINK(...)`; `... .> DROP(K)` with context `{K: 3}` | equals the pure in-memory result |
| sqlt harness change | run every case twice (or 5x) and require identical text | catches map-order nondeterminism (GO-C18) |

# Gate / tooling wiring

From go-7 (and go-1, go-3, go-5 where noted). Confirmed by reading unless marked otherwise.

| Issue | Where | Effect / fix |
| --- | --- | --- |
| Go excluded from the cross-host real-database oracle | `tools/check-sql-oracle.sh:34` (`case "$impl" in php\|js\|cpp\|lisp\|python) ;; *) continue ;;`) | Go's `sqlfuzz statement` output is only text-compared with the other hosts, never executed against a real DB. Add `go` to the case list |
| Go not mutation-tested and not in the live examples | `tools/mutate-sql.py` (PHP/JS lanes only); `tools/check-usage.sh` `HOSTS` and `example_impls` | the Go SQL layer is not mutated and has no live-DB usage examples |
| `make sqlt` (and the other bare binary names) is a no-op | `go/Makefile`: `.PHONY: ... $(BINARIES)` declares `sqlt`, `sqlfuzz`, ... phony with no recipe (confirmed with `make -n sqlt`: "Nothing to be done") | real targets are `build/<name>`; drop the bare names from `.PHONY` |
| `make test` scope | `go/Makefile` | runs unit, conformance and sqlt but not sqlapi / sqlreplay / sqlfuzz; `go test -race` needs cgo (gcc present here; a box without gcc fails the unit lane) |
| Freshness rule judges by the newest binary (reasoned; not exercised) | `tools/impls.sh` `impl_available go` (copied from the C++ rule) | in `go/Makefile` every binary depends on every `*.go` file, so the correct test is the OLDEST binary. Rebuilding one binary by hand after an edit marks Go "available" while sqlt/sqlfuzz/sqlapi are stale, and the gate grades the stale ones. `check.sh`'s "MISSING ... build them (cd cpp && make; npm run build)" message does not mention `cd go && make` |
| No AST-immutability check in the Go sqlt runner | `go/bin/sqlt/main.go:265` (`sel.OptimizeAstInMemory(prog.AST())` result discarded); `sql/cases/README.md` promises the check; JS does it (`js/bin/sqlt.mjs:89-170` `snapshotAst`) | go-7 added a snapshot compare in a scratch copy of the runner (Node fields T, Pos, S, B, Grouped, Dec, KeysUnobserved, MathPlan, Spec, recursive): all 1,065 cases pass, no mutation by PlanHybrid or OptimizeAstInMemory, so this is a test gap, not a live bug. Snapshot before PlanHybrid, after PlanHybrid, after OptimizeAstInMemory and compare |
| No test executes a hybrid plan in Go | `go/sel/sql` has no tests; `ExecuteHybrid` is not called by sqlapi or e2e | hid GO-C5. Add a Go unit test and a cross-host `execute` probe in `tools/sqlapi.*` |
| No shared-context race test | `go/sel/concurrency_test.go` gives every worker its own context | hid GO-C6. Add a shared read-only-context `-race` test |
| Manifest coverage checked only under `go test` | `go/sel/parser_test.go:65` | see GO-C41 |
| `scale-bench` does not validate record key order | `go/bin/scale-bench/main.go:51-75` (`convertJSONValue` sorts keys), `:93-108/151-160` (`sort.Strings(keys)` per table), `:166-200` (`benchmarkValue` uses `map[string]interface{}`; `canonicalJSON` = `json.Marshal`, which sorts map keys) | rows load with alphabetically sorted fields and results compare through Go maps, so a wrong field order (INDEXES, promoted-field order in joined rows; both normative, 3.3 / 7.4) can never FAIL a scenario. All six scenarios PASS, which proves values only. Fix: keep JSON key order (token decoder / ordered map) and compare an order-preserving serialisation. It also computes `dist_berlin` with host float math for the fixture only |
| `sql.Options` documentation | Go's `Options` has only `Strict`; JS/PHP/Python also forward `fuseFilters`/`foldConstants` | consistent with C++ (docs/sql.md and sql-translation.md 12.1 say "C++ and Lisp take `strict` alone") but the docs list does not mention Go |
| sqlt "Unrepresentable" counted as passed | `go/bin/sqlt/main.go` | by design (as in C++); noted only |

# Suggested fix order

**1. Small, high value (minutes to an hour each; mostly one-line or local):**
1. GO-C5 `IsNone()` -> `IsNull()` in `ExecuteHybrid` (plus a hybrid execution test).
2. GO-C9 remove the `break` in `parsePipeStep` (spec sentence + two cases).
3. GO-C7 copy the sub-slice in TAKE/DROP.
4. GO-C8 use `Elements()` emptiness in `doTop`/`doBucket` (spec 7.3; cases).
5. GO-C25 coerce argument 1 before argument 2 in ROUND/POWER.
6. GO-C24 + GO-P16 (first half): push runs of template bytes in `Emit.Fill`.
7. GO-C36 nil-fn check, GO-C42 `FromInt`, GO-C41 manifest check at load, GO-C45, GO-C44.
8. GO-C21 remove the call-branch fall-through in `containsUnsupportedSql`.
9. GO-C12 `Guard` in Floor/Ceil (all hosts).
10. Tooling: drop bare names from `.PHONY`, add `go` to the oracle case list, oldest-binary freshness rule, AST-immutability snapshot in the Go sqlt runner.

**2. Correctness that needs design or a spec decision (do spec first, then all hosts):**
1. GO-C1 regex repeats above 1000 (expand counted repeats, or change engine); never map compile errors to "no match". Then GO-C10 (RREPLACE empty-abutting loop) and GO-C28.
2. SQL translator trio GO-C2, GO-C3, GO-C4 (with JS, PHP, Python, C++, Lisp), then GO-C15/GO-C16/GO-C17, GO-C19, GO-C18, GO-C22.
3. GO-C6 shared-context race: atomics for the Value caches, or an explicit documented contract, plus a `-race` test.
4. Resource limits: GO-C11 (text-size cap), GO-C14 (allocation budget), GO-C13 (memoised, iterative lexer), GO-C20 (inline expansion cap).
5. Spec gaps found by agreeing hosts: GO-C23, GO-C26/27, GO-C30/31, GO-C33-35.

**3. Performance, ordered by payoff per effort:**
1. GO-P1 leading-equality peel for LINK (30x), GO-P4 hoist right alias (4x, trivial), GO-P2 key pre-classification (4x, byte-identical prototype exists), GO-P5 regex fast paths (34x on long subjects; drop `foldSubject`).
2. GO-P6 lexer fixes (a few lines; 261 -> 48 ms), GO-P16 emit/chain cache (38%), GO-P14 RECORD literal fast path and shape memo, GO-P15 FIND via `strings.Index`, GO-P19 regex cache key/bounded LRU.
3. GO-P3 heap-based TOP after GO-C23 is decided, GO-P7 non-panicking `??`, GO-P8 move the depth/frame restore to the recover sites, GO-P11 keyed-list index, GO-P17 linear pairwise fold.
4. Larger refactors with uncertain payoff: GO-P9 compact small decimals and shrinking `Value`, GO-P12 Args/scratchpad/Elements allocation, GO-P13 fresh-RHS provenance, GO-P10 divide-and-conquer numeral parse.

# Checked and found sound

Merged from the seven reviewers.

**Front end (go-1)**
- Lexer vs grammar: ASCII-only `isDigit/isAlpha/isSpace` (no `unicode.*` calls in the slice); number rule (dot consumed only when a digit follows); comments including `#` inside interpolation braces; raw `''` escaping; `\u{}` length 1..6 hex, surrogates and >10FFFF as `E_RANGE`, other malformed as `E_ESCAPE`; `E_UNTERMINATED` at the innermost position; empty `{}` as `E_SYNTAX`; ASCII-only identifier upcasing. `hexRegex` `^...$` in RE2 has no trailing-newline quirk.
- Source handling: `RuneError`/size-1 test separates a real U+FFFD from invalid bytes, overlong forms, encoded surrogates and >U+10FFFF (all `E_UTF8`, line/col 0, same as PHP/C++). Offsets and columns are rune indexes (the doc comment at `errors.go:19` saying "byte offset" is stale; behaviour is right). CRLF, U+2028, NUL, BOM handled like JS/C++.
- Differential parse fuzz Go vs JS on 230,000 programs (60k token soups, 20k pipeline forms, 150k character-level soups): identical except GO-C9 (98 programs) and message quoting; zero non-SelError panics. About 90 hand-picked lexical/syntactic edge cases compared with JS and C++: identical.
- Parser depth accounting equals JS exactly; `-`x100k, `NOT `x100k, `(`x100k, `A=`x100k, `A[1]`x100k, `.> ABS`x100k, `+1`x100k give the same E_DEPTH code and column and never blow the stack. Precedence, associativity errors, prefix admission, reserved words, call flattening, `;` handling, `checkTarget`/`E_BAD_ASSIGN`, `E_ARITY`, `E_UNKNOWN_FUNC`, pipe-step rejection all match.
- `prepareRecordShape` returns nil on duplicate keys; `BindingForm`/generic forms match JS; the manifest `Builtins` set (78) equals the registered set. Lexer/parser hold no shared mutable state; registry is RW-locked; `go vet ./sel ./internal/...` clean.

**Numeric core and values (go-2)**
- Decimal arithmetic, division, modulo, round, floor, ceil, trunc, POWER scale rule, CANON, IsInteger, ToSafeInt: 390,002-case differential against a Python-int model (operands to 60 digits, scale to 40, 300-digit outliers, 2^63/2^64 boundaries) and 199,958 oracle cases (`tools/decimal-oracle.py 20000 777`): 0 mismatches. Zero never carries a minus sign.
- No aliasing between result and operand in `dec.go`; `Dec` is immutable with no in-place `.Digits` mutation outside dec.go; no float64 anywhere in the four files; int32 `Scale` cannot overflow. `Guard` BitLen shortcut (MAX_INT_BITS = 3321929) is correct; Parse cap logic on leading zeros matches the rendered-size rule; E_RANGE on 1M+1 digits and on the carry case `999...9.5 + 0.5` agree with C++.
- `Pow10` cache: RWMutex double-check pattern correct; eviction resets weight with the map. `shape.go` signature is prefix-free after the length-prefix fix; cache is lock-protected; KeyMap/Keys never mutated after construction.
- `Value`: CloneAt/EqlAt/DumpAt/structuralHashAt all count depth against MAX_DEPTH; ScalarSource is iterative; the Dump escape set is byte-identical to JS on DEL, U+0000-U+001F, U+2028, U+FEFF and non-ASCII. Set/Get/Has agree across shape, dense list, keyed list, entries and index paths; list-to-record conversion keeps order; empty-list TAKE behaves as an empty list.
- `builtins_number.go`: MIN/MAX tie keeps the first argument; NULL/BOOL/BIN/non-numeric codes and columns match JS/PHP/C++/Lisp; ROUND/POWER caps (1e6 / 1e5) and `E_NOT_INT` agree. `optNumericLiteral` guards `IsInt64` and DROP+DROP guards overflow, so ToSafeInt saturation does not leak into the TAKE/DROP merge.

**Evaluator, optimizer, join planning (go-3)**
- Program-level concurrency with independent contexts: 879 conformance programs plus ~6000 generated programs on shared Programs (6 goroutines, plus concurrent Compile) under `-race`: no reports. `Lookup`/`Define`/`RegisterFunction` use RWMutex; `physOnce` protects the tree; SlotCache is an atomic pointer to an immutable pair.
- Plain vs physical evaluation: ~172k programs (16 generator seeds x 6000, join-filter-oracle mixed/uniform/pipeline, a 60k-program pipeline generator, 200k mutations, 69 hand-written rewrite-edge cases): zero panics, zero hangs, and every difference opened was GO-C8, GO-C34 or GO-C35. Rewrites TAKE+TAKE, DROP+DROP (incl. 1e20 / negative / fractional literals), SORT+TAKE, MAP+FILTER, SORT+FILTER, SELECT_COLS+FILTER, MAP+SORT, FILTER+FILTER (incl. binder rename), DISTINCT/DEDUPE folding, FILTER(TRUE) dropping, constant folding (`1/0` left for run time, `-0`, `FALSE AND (1/0>0)`) and copy-propagation (`x+0`, `x*1`, incl. "-0", "007", padded text) all agreed.
- Join pre-filter / pushdown: `join-filter-oracle` gen.py (3000 pairs x 2 seeds each) Go vs JS: 0 cross-host and 0 written-vs-helper disagreements; own well-formed join generator (24k pairs, LINK/LINK_LEFT one and two deep with FILTER/SORT/TAKE/MAP over the join, sparse rows): 0 disagreements.
- Assignment: target resolved before RHS, intermediate levels created, store lands on the post-RHS tree (`A[1] = (A = 2); A`), compound ops read the target first, copy-on-assign, partial creation surviving a caught error: 27 cases plus 19 `??` cases identical across Go/JS/Python. Evaluation order: 29 order/aliasing cases identical across Go/JS/Python/PHP. Depth counting equals JS (col 204 for 250 `+`); `optExceedsDepth` prevents rewriting an over-limit tree. `Dependencies()` for 24 binder/assign forms matches JS and Python. Registry name validation (`"ab\n"`, `"ab "`, non-ASCII, fullwidth), re-registration semantics and huge `max` arity behave. Math plan: MIN/MAX tie-keeps-first, ROUND/POWER caps and AuxPos, negative zero all match JS. `go vet ./sel` clean; conformance 1091/1091, regression file 18/18.

**Text, regex, binary, control (go-4)**
- UPPER/LOWER are ASCII-only bytewise; no `strings.ToUpper/ToLower/EqualFold/TrimSpace/Fields` in the slice. `\d \w \s` (and negations) are rewritten to explicit ASCII classes inside and outside classes; `\s` is exactly space, tab, LF, CR, FF, VT. `\b \B \v \p \A \z \Z \G \K`, backrefs, lookaround, atomic/possessive, inline groups, POSIX classes, `\D\W\S` in classes and lone `]` give identical `E_REGEX_SYNTAX` across ~115 probes.
- `^`/`$` lowered to `\A`/`\z` after validation (`lowerAnchors` skips escapes and classes); `$` never matches before a trailing newline; `(?s)` always on; no `(?m)`. Offsets are code points (RFIND with astral/multibyte, RGROUPS spans, RREPLACE splicing, empty pattern agree). TEXT is valid by construction, so RE2's replace-bad-bytes behaviour is unreachable; U+FFFD round-trips. `FROM_UTF8` rejects overlong, surrogate, >U+10FFFF, truncated and lone continuation sequences with E_UTF8 at the argument position.
- `i` flag: ASCII-only pattern check before compile with `E_BAD_ARG` at the flags position; `m`/`s` give `E_BAD_ARG`; U+212A/U+017F fold like the other hosts; `İ`/`ı` conformance cases pass. Argument evaluation and error ordering match all hosts on 11 probes.
- RE2 is linear-time, so ReDoS patterns such as `(a*)*b` cannot blow up (an advantage over JS); leftmost-first alternation, lazy quantifiers and captures agree on 12,000 random programs except GO-C10/GO-C30. `regexCache` is concurrency-safe; `b64Index` written only in `init`.
- LEN/LEFT/RIGHT/SUBSTR/FIND/REPLACE/SPLIT/TRIM/BACKWARDS/CHAR/CODE agree on ~90 edge cases including arguments above 2^63, start 0 / negative, NUL and astral code points. HEX/BASE64/CRC32/BTL/LTB/BLEN/TO_UTF8 agree on ~55 probes (odd hex, non-ASCII digits, misplaced `=`, missing padding, URL-safe rejected, non-canonical `YR==` accepted everywhere). IF/COND/COALESCE/GET/PATH/IS_NULL/IS_BLANK/IS_PRESENT agree on ~60 probes.

**Structure, aggregate, relational (go-5)**
- No Go map iteration decides an output (BUCKET order from the `groups` slice; DISTINCT keeps first-occurrence order). doSort/doTop are stable with an original-index tie-break; TOP equals the first k of the stable sort. Slice aliasing via `append` is safe everywhere except TAKE/DROP (GO-C7).
- Differential parity with JS on ~330 hand-written expressions (codes and columns): arities and binder forms of SORT/SORT_BY/TOP/TOP_BY/TOP_DESC/BUCKET/MAP/FILTER/ALL/ANY/SUM/JOIN, empty/NULL/scalar sources (except GO-C8), huge and negative counts (TAKE/DROP/TOP with 2^63, 2^64+1, 1E30, -0, "1", NULL, lists), FILTER key preservation, BUCKET key identity (1 vs 1.0 vs "1"), RECORD duplicates and NUL keys, SELECT_COLS duplicates, DISTINCT identity, 90 LINK/LINK_LEFT cases (equi with 1E5/100000, 0.10/0.1, -0/0.00, 2^63 boundary and mixed bad keys; non-equi; named and unnamed binders; upper/lower alias; nested rows; empty/NULL sides; scalar rows).
- `_K` detection covers every child-bearing node type and math-plan nodes keep their children. Value nesting at depth 198/199/200 raises E_DEPTH at JS's column. Frame/depth cleanup works for SORT, SORT_BY, TOP, TOP_BY, BUCKET and the LINK paths. Identifiers are ASCII upper-case, so `strings.ToLower` on binder/relation names is safe. Joined-row construction matches JS. All six scale-bench scenarios PASS (values only; see tooling).

**SQL translator and rendering (go-6, go-7)**
- Identifier quoting doubles the quote char per dialect (a `` a`; DROP TABLE x; -- `` alias renders as one quoted identifier on all four dialects); `checkName` rejects empty and NUL binding names; `Raw`/`Correlate`/`RelationQuery` are documented trusted SQL. `TextLiteral` escapes `'` (and `\` for the MySQL family) in one longest-first pass (UTF-8 safe, same as JS); NUM literals go through `decimal.Parse` + `Format` (a NUM binding cannot carry SQL; `"1 OR 1=1 -- "` typed NUM is refused with `E_SQL_BINDING`); BIN is hex; NUM/BOOL/BIN are inlined even in params mode by design, TEXT is always bound and numbered by emission order. No injection route found through value bindings or template splicing (`FillSlot` is a literal `ReplaceAll`). `Fragment.Bindings()` vs placeholders never disagreed in ~700k fuzzed translations.
- Whole-expression refusal (nothing becomes characters until `AsValue`/`AsStatement`); params/caveat state is per Translator; depth charging identical to JS (197..201-term chains and assignment forms give the same accept/refuse boundary).
- Differential Go vs JS over ~150k mutated inputs (4 dialects x expr/stmt x strict): no divergence in accept/refuse, code, SQL text, params or caveats except GO-C38 and JS's own empty-alias emission. SQLite constants oracle: ~19k random constant expressions, 0 mismatches among ~11k compared with no caveat, 0 cases where SQL answers a definite value that SEL refuses. 8 goroutines x 300 translations on shared Programs and Bindings under `-race`: clean; `Leaf`/`Rewritten` never mutate the AST. Statement planner LIMIT/OFFSET arithmetic, sort stacking, derived-table wrapping, LINK alias uniqueness and self-join refusal agree with JS.
- `sqlt` 1065/1065, `sqlreplay` 0 differences (shipped flattened map equals the map rebuilt through the public API; inheritance ansi -> mysql-family -> {mariadb,mysql}, postgresql, sqlite matches, 649 lookups), `sqlapi` byte-identical to `node tools/sqlapi.mjs`. `PlanHybrid`, `tryPlanFallthrough`, `tryLatestMember`, `bucketRowsAreKeys`, `joinRowsLackBinders`, `inlineLiterals`, `literalHelpers`, `unwindThroughHelpers`, `referencedAssignments`, `readsWholeRow`, `collectFieldReferences`, `isOwnFieldRead` are line-for-line equivalent to `js/src/sql/hybrid.mjs` (only GO-C21 differs). Differential Go vs JS over ~24,000 mixed programs x mariadb/sqlite plus 3 x 1,500 generator programs x mariadb/postgresql: 0 differing lines. In ~24k random relational pipelines hybrid and pure_sql plans (SQL prefix simulated in memory) gave identical values and error codes to the plain in-memory run apart from GO-C22 and GO-C5. The planner does not mutate the caller's AST. `constants.go` matches `constants.mjs`; map lookup order, null-as-withdrawal, arity/variant/tpl validation and the ISNUM agreement memo behave as MAP.md describes. sqlfuzz/sqlreplay/sqlapi are faithful ports of the JS tools. The latest-member SQL (CTE + MAX join + ORDER BY MIN) and real-DB dialect text were not executed (no Docker).

**Earlier review:** its six findings are fixed (see the status table); `go test -race ./sel` (including `concurrency_test.go`) passes.

# Caveats

- Review of an uncommitted, moving tree as of 2026-09-29; line numbers will drift.
- No real MariaDB/MySQL/PostgreSQL was used (no Docker by rule): MariaDB-specific claims are reasoned from emitted SQL, SQLite claims measured with Python `sqlite3`.
- Timings come from a shared 8-core box with ~40 concurrent reviewers; treat them as +/-30%.
- The synthesizer re-ran: GO-C1 (3 repros), GO-C2, GO-C3, GO-C4, GO-C5, GO-C6, GO-C7, GO-C8, GO-C9, GO-C10, GO-C11 (PADL), GO-C12, GO-C25 (direction), and all six earlier-review repros. Everything else carries the reviewers' own confirmation labels. GO-C18's repro was attempted with a different program and was refused by the translator before reaching the affected path, so it stands on the reviewer's evidence only.
