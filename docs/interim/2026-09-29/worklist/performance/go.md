# GO performance

[Worklist](../README.md) · [Performance protocol](../07-performance.md)

Report measurements below are historical evidence, not verified targets for this machine. Recreate each workload in the repository; scratchpad paths mentioned by reviewers are not dependencies. The shared performance protocol applies to every task, including reasoned/guessed opportunities and subitems bundled in a finding.

<a id="go-p1"></a>

## GO-P1: LINK / LINK_LEFT with `equality AND residual` falls back to the O(n*m) nested loop

- [x] **P-GO-P1 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P1](../../go-code-review.md). Report labels: [high] [measured].

**Source target:** `go/sel/builtins_aggregate.go:673-693` `extractJoinEqui` (requires the whole predicate to be a single `==`/`$==`); consumer `doLink` 987-1296 vs 1298-1358.

**Benchmark seed / reported evidence:** 1500 x 1500 rows, 10 distinct keys, `COUNT(LINK(L, L, l, r, ...))`: `l["a"] == r["a"]` alone 0.18 s (225000 rows); `... AND l["c"] < r["c"]` 5.83 s; `... AND l["c"] == r["c"]` 5.63 s. JS: 4.05 s / 9.59 s / 8.43 s. HEAD Go: 20.7 s for a 3000 x 3000 variant.

**Implementation experiment:** peel a *leading* equality conjunct off a left-nested AND chain as the hash key and evaluate the remaining conjuncts per bucket pair. Semantics are identical because AND short-circuits left to right; keep the `checkJoinPair` bad-key/E_NOT_NUM handling. A non-leading equality cannot be hoisted.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p2"></a>

## GO-P2: Sorting re-classifies both keys on every comparison (LooksNumeric re-parses non-numeric text each time; reflection-based sort)

- [x] **P-GO-P2 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P2](../../go-code-review.md). Report labels: [high] [measured].

**Source target:** `builtins_aggregate.go:83-151` (`compareValues`), `:233-242` and `:327-336` (`sort.SliceStable`); `value.go:448-470` (`LooksNumeric`: `defer recover`, and a failed `decimal.Parse` is never cached).

**Benchmark seed / reported evidence:** 100k rows, best of 3): `SORT(NUMS)` (ints) 839 ms; `SORT_BY(L,_["s"])` (non-numeric text) 813 ms with 3.5M allocations; `SORT_BY(L,_["s"],"DESC")` 814 ms. Prototype: classify each key ONCE into a small struct (null flag, `*Dec` + int64 fast path when scale 0 and digits fit, bool, byte slice, rank), then `slices.SortStableFunc` (same insertion-block + symMerge algorithm, so identical output even for the intransitive comparator of GO-C23): ints 839 -> 193 ms, non-numeric text 813 -> 211 ms with 200k allocations. Byte-identical on 300 random mixed-type lists. `slices.SortFunc` (pdqsort + original-index tie-break) gave 101 / 164 ms but is only equivalent for a consistent comparator: do it after GO-C23 is settled.

**Implementation experiment:** as prototyped; keep `LooksNumeric` semantics; memoise the negative result.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p3"></a>

## GO-P3: TOP / TOP_BY / TOP_DESC do a full O(n log n) stable sort to return k rows

- [x] **P-GO-P3 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P3](../../go-code-review.md). Report labels: [high] [reasoned + measured baseline].

**Source target:** `builtins_aggregate.go:327-345`.

**Benchmark seed / reported evidence:** `TOP(NUMS,10)` over 100k costs 975 ms vs 839 ms for SORT (measured baseline). Expected gain with pre-classified keys and a size-k selection (heap on (key, original index), so ties resolve to input order) is order 10-50x for k << n; not prototyped, so the speedup is reasoned.

**Implementation experiment:** bounded max-heap (or select then sort the k winners) when `limit < len(items)`; keep the full stable sort when `limit` is large (say > n/8). Same comparator caveat as GO-P2.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p4"></a>

## GO-P4: Non-equi LINK/LINK_LEFT rebuilds the right-side alias record for every (left,right) pair

- [x] **P-GO-P4 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P4](../../go-code-review.md). Report labels: [high] [measured].

**Source target:** `builtins_aggregate.go:1339-1345` (`ensureRowTableAlias(rEntry.Val, b2)` inside the inner loop; `strings.ToLower(b2)` per pair).

**Benchmark seed / reported evidence:** `LINK(TAKE(L,1000), TAKE(R,1000), X, Y, X["k"]<Y["k"])`: 1.75 s, 9.07M allocations, 518 MB. With right aliases hoisted out of the left loop (and ToLower once): 0.43 s, 1.07M allocations, 160 MB (4x). `LINK(...,X,Y,TRUE)` 2.09 s -> 0.57 s. Output identical on 90 LINK cases.

**Implementation experiment:** `rightAliased := make([]*Value, len(rightEnts))` before the left loop. The equi path already does this once per right row (line 1006).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p5"></a>

## GO-P5: RMATCH / RFIND / RGROUPS compute every match, a per-byte offset table and three copies of the subject

- [x] **P-GO-P5 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P5](../../go-code-review.md). Report labels: [high] [measured].

**Source target:** `go/sel/builtins_regex.go:386-433` (`findMatches`) called from `:495, :505, :519`; `regexArgs` at `:435-451`; `foldSubject` `:366`.

**Benchmark seed / reported evidence:** `RMATCH('.', REPEAT('123-45,', 500000))` (3.5M chars): Go 4.30 s vs JS 0.126 s (34x). Micro-benchmark on 700 KB: 759 ms and 306 MB / 1.4M allocations per call. Short subject: `findMatches` 2.8 us / 400 B / 6 allocs for `"123-45"` vs 0.53 us / 0 allocs for `regexp.MatchString`.

**Implementation experiment:** RMATCH -> `re.MatchString(s)` on the original string. RFIND/RGROUPS -> `FindStringSubmatchIndex`, converting only the first match's byte offsets with `utf8.RuneCountInString(s[:off])` (ASCII fast path). RREPLACE -> iterate byte offsets and splice from the original string. `foldSubject` is unnecessary: Go's `(?i)` already folds U+212A to `k` and U+017F to `s` (verified: `(?i)k` matches "K", `(?i)s` matches "ſ", `(?i)[^k]` does not match "K").

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p6"></a>

## GO-P6: Front-end throughput: `matchOperator` allocates, the token slice is unsized, `posAt` binary-searches

- [x] **P-GO-P6 — Measure and address this finding.**

  Closed 2026-09-30 — implemented (partly) (remaining sub-items measured and deferred in the results row); evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P6](../../go-code-review.md). Report labels: [high for compile time of large rules] [measured].

**Source target:** `go/sel/lexer.go:176-194` and `:168` (`[]rune(op)` per probe over a 34-entry table); `:104` (`make([]Token, 0, 32)`, Token is 56 B); `:86-101` (`posAt`, called per token at `:127` and eagerly before `fail(...)` at `:264, 303, 342, 364, 197, 226`); `go/internal/utf8/utf8.go:132-140` (`AsciiUpper`: two allocations even when already upper case; used at `lexer.go:152` per ident and `registry.go:78` per `Lookup`).

**Benchmark seed / reported evidence:** 436 KB / ~135k-token generated program, `go test -bench Tokenize`): 261 ms/op (1.7 MB/s), `stringtoslicerune` 18% flat, `matchOperator` 27% cumulative. Precomputing operator runes (or byte compare; all operators are ASCII): 129 ms. Then presizing `out` to `l.n/3+32`: 129 -> 57 ms and 55.8 -> 22.9 MB/op. A monotone `posAt` cursor: 57 -> 48 ms. Lazy failure positions and the `AsciiUpper` scan-first fix are reasoned, not measured. Overall parse throughput is ~0.6 us/token (78 ms for the 135k-token program), GC-dominated.

**Implementation experiment:** `var operatorRunes` at init (or dispatch on the first rune); presize tokens (and/or shrink Token: `TokenType` as uint8, `Pos` as an int32 offset resolved on error); forward-only `posAt` cursor and failure-only `Pos`; `AsciiUpper` returns `s` unchanged if no `a-z`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p7"></a>

## GO-P7: `??` / `???` on a missing field pays panic + recover + Sprintf (~6x slower than a hit)

- [x] **P-GO-P7 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P7](../../go-code-review.md). Report labels: [medium] [measured].

**Source target:** `go/sel/eval.go:193-215`; `fail()` in `errors.go` builds `fmt.Sprintf("no key %q")` before panicking (`eval.go:107,112,71,81`).

**Benchmark seed / reported evidence:** 200k records: `COUNT(MAP(L, _["zz"] ?? 1))` 277-350 ms vs `_["a"] ?? 1` (present) 46-60 ms, ~1.2 us extra per miss (profile: Fail 25%, gorecover/unwinder ~20%). No HEAD baseline (HEAD errors with E_DEPTH).

**Implementation experiment:** when `node.L` is a NodeVar/NodeIndex chain, resolve it with a non-panicking "try" walk and fall back to the recover path for other node kinds; make the error message lazy. Codes/positions unchanged.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p8"></a>

## GO-P8: Per-node `defer` closure in `EvalNode` (added by the depth/frame fix)

- [x] **P-GO-P8 — Measure and address this finding.**

  Closed 2026-09-30 — rejected (no gain under CPU-time A/B (P8 per-node defer x1.00; P22 x1.03)); evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P8](../../go-code-review.md). Report labels: [medium] [measured, noisy].

**Source target:** `go/sel/eval.go:31-52`.

**Benchmark seed / reported evidence:** 3 alternating runs of 200k-row loops, current vs a copy with the defer removed: filter-and 182/177/138 ms vs 127/135/130; map-math 229/235/244 vs 204/217/229; map-if 170/175/155 vs 142/150/187; map-coalesce-ok 59/67/61 vs 48/50/47. Roughly 5-25% on a shared box (upper bound). Against HEAD the current tree was not slower on filter-and/map-math (noise; other work changed too).

**Implementation experiment:** restore `ctx.Depth`/`ctx.Frames` at the five `recover()` sites (`??`, `evalBoolSafely`, `canonicalJoinKey`, ...) from values saved before the protected call: same semantics, zero per-node cost.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p9"></a>

## GO-P9: Small-number arithmetic is allocation-bound: 4-6 allocations per op; `Make` copies every result

- [x] **P-GO-P9 — Measure and address this finding.**

  Closed 2026-09-30 — implemented (partly) (remaining sub-items measured and deferred in the results row); evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P9](../../go-code-review.md). Report labels: [medium] [measured].

**Source target:** `go/internal/decimal/dec.go:49-60` (`Make` does `new(big.Int).Set(digits)`), `Negate`/`Abs` (`232-238`) allocate two big.Ints for a sign flip, `Parse` (`123`), `go/sel/value.go:44-60` (`Value` is 152 bytes).

**Benchmark seed / reported evidence:** 300k-500k iterations): AddSmall ~600-1100 ns / 6 allocs / 192 B; MulSmall 640 ns / 4 allocs; Negate 363 ns / 3 allocs; Parse("12345.67") 1034 ns / 6 allocs; Div 1268 ns / 10 allocs; TrimScale 1515 ns / 7 allocs; CmpDiffScale 521 ns / 2 allocs; `NewInt` 396 ns / 3 allocs / 232 B. End to end `SUM(L,_)` over 200k ints 750 ns/element; `SUM(L,_*2+1)` 2.2 us/element. A non-copying `makeOwned` removed one alloc per op: Mul 480-640 -> 360-380 ns, Add within noise (20-40% on Mul; not the whole story).

**Implementation experiment:** (1) `makeOwned` for fresh ints; make Negate/Abs share `Digits` (Dec is immutable; no in-place mutation of `.Digits` outside dec.go); (2) a compact representation (magnitude fits uint64 and scale <= 18: keep `mag uint64`, allocate a big.Int only on overflow; ~10 ns and allocation-free for Add/Sub/Mul/Cmp/Round); (3) `ParseUint` fast path for <= 19 digits; (4) shrink `Value` by moving rarely used fields behind one pointer. Re-run the 390k differential.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p10"></a>

## GO-P10: Parsing a large numeral is quadratic (`big.Int.SetString`)

- [x] **P-GO-P10 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P10](../../go-code-review.md). Report labels: [medium] [measured].

**Source target:** `go/internal/decimal/dec.go:175-176` (`digits.SetString(stripped, 10)`), reached from `Parse`, hence every text-to-number coercion and every literal.

**Benchmark seed / reported evidence:** microbench `SetString` of 999,999 digits 2.5 s (7 s in one noisy run) vs a divide-and-conquer parse (split in halves, combine with `Pow10(len(lo))`, 2000-digit leaves) 0.44 s, byte-identical; at 100,000 digits 63 ms vs 28 ms. End to end `X = REPEAT("7", 999999); LEN(X + 1)`: Go 3.35 s, JS 1.18 s, PHP 0.42 s, C++ 0.03 s. `Format` on the same number is 0.5-0.7 s (already sub-quadratic). go-1: a 1,000,000-digit literal compiles in 2.7 s in Go vs 0.87 s JS and 0.03 s C++.

**Implementation experiment:** recursive halving above ~2-4k digits using the existing `Pow10` cache.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p11"></a>

## GO-P11: Keyed lists (the result of FILTER when any element is dropped, and join results) have O(n) `Get`/`Has`/`Set`

- [x] **P-GO-P11 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P11](../../go-code-review.md). Report labels: [medium] [measured].

**Source target:** `go/sel/value.go:184-193, 214-222, 257-263` (linear scans over `listKeys`); produced by `builtins_aggregate.go:1546` (FILTER) and `:1287` (join result).

**Benchmark seed / reported evidence:** 20,000-element keyed list, `SUM(INDEXES(K), K[_])`: 1.08 s vs 30 ms on a dense list (36x); ~27 s extrapolated at 100k.

**Implementation experiment:** lazily build a `map[string]int` on first `Get`/`Has`/`Set` at `len(listKeys) >= 16`, as `rebuildIndex` does for entries; clone/drop consistently in `CloneAt`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p12"></a>

## GO-P12: Per-node and per-walk allocation: literals, Args, scratchpad, and `Elements()`/`Entries()` key strings

- [x] **P-GO-P12 — Measure and address this finding.**

  Closed 2026-09-30 — implemented (partly) (remaining sub-items measured and deferred in the results row); evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P12](../../go-code-review.md). Report labels: [medium] [measured allocs, reasoned savings].

**Source target:** `eval.go:56-66` (each NodeNum/NodeText/NodeBool evaluation allocates a Value), `:136` (`NewArgs` allocates the struct and `vals` slice per call, lazy or not), `:491` (`make([]*Dec, ScratchpadSize)` per math evaluation); `go/sel/value.go:346-370, 680-708, 646-678` (`Entries`/`Elements`/`structuralHashAt` build a `[]Entry` and a `strconv.Itoa(i+1)` string per element past 99); `builtins_aggregate.go:72` (`frame["_K"]` map lookup per element), FILTER appends with no capacity hint then `NewListWithKeys` copies again, `:1339-1352` per-pair frame map assignments.

**Benchmark seed / reported evidence:** allocations/element over 1000 records: `COUNT(MAP(L, 1))` 1.93; `_["a"]` 0.93; `_["a"] > 4` 2.93; `_["a"] * 2` 5.73; `_["a"]*2 + _["c"]` 10.7; `IF(_["a"] > 4, 1, 2)` 5.93; `RECORD("x", 1)` 9.9. CPU profiles of filter/map loops are 45-50% mallocgc + GC scan (go-5: 55-60% GC in every data scenario). `Entries()` on a 100-element list 4.8 us / 2.7 KB / 2 allocs; `COUNT(MAP(L,_))` over 100k = 99,943 allocations; `COUNT(SORT(NUMS))` shows the same 99,917 before the sort starts. `_K` creation goes through `NewText`, which re-validates UTF-8 (`:73`).

**Implementation experiment:** byte-identical, each needs care): Args with a small embedded `[4]*Value` array or per-ctx free list; scratchpad on the stack for `ScratchpadSize <= 16`; iterate `storage` directly and materialise the key text only when `_K` is used or FILTER needs custom keys; hoist the `_K` presence test to a bool; skip key compare on `v.shape == other.shape` in `EqlAt`; hash BIN without `string(binVal)`; `NewTextOwned` for `_K`. Sharing immutable TRUE/FALSE/literal Values needs a clone-on-return or read-only flag because `Program.Run` hands results to a host that may `Set` into them (guessed 10-20% on scalar-heavy loops, not measured). The bigger lever inside math plans is GO-P9.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p13"></a>

## GO-P13: Assignment deep-copies a fresh right-hand side

- [x] **P-GO-P13 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P13](../../go-code-review.md). Report labels: [medium] [measured cost of the copy, reasoned fix].

**Source target:** `go/sel/eval.go:406` (`EvalNode(node.R).CloneAt(1, node.Pos)`).

**Benchmark seed / reported evidence:** `X = MAP(L, RECORD("x", _["a"], "y", _["c"]))` over 200k rows: 292-302 ms vs 203 ms for the MAP alone (the clone is ~1/3 of the statement); `X = L` alone 74 ms (pure clone).

**Implementation experiment:** skip the clone when the RHS provably has no other owner (constructor results that already cloned their inputs, not a variable read, IF branch, `_` etc.): a "fresh" bit on Value or a static provenance walk. Spec 3.4 requires only the observable copy.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p14"></a>

## GO-P14: Record construction: uniqueness map, signature string and `reflect.DeepEqual` per call

- [x] **P-GO-P14 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P14](../../go-code-review.md). Report labels: [medium] [measured].

**Source target:** `go/sel/shape.go:40-83` (`UniqueRecordShape` allocates a `seen` map per call; `InternRecordShape` builds a signature with `strconv.Itoa` + Builder), called from `NewRecordFromEntries` (`value.go:134`); `builtins_aggregate.go:471-503` (`ensureRowTableAlias` recomputes the target shape for every row of every LINK: two allocations for the keys slice, one map, one Builder, the cache lookup); `builtins_structure.go:59-83` (RECORD with literal keys still evaluates the key nodes, builds a keys slice and calls `reflect.DeepEqual(shape.Keys, keys)` per row, although `parser.go:81-93` already precomputes `node.Shape` when every key is a literal).

**Benchmark seed / reported evidence:** 3-key `UniqueRecordShape` 400 ns; `NewRecordFromEntries` 3 keys 2.7 us vs `NewShapedRecord` with a cached shape 0.76 us. `COUNT(LINK(L,R,_1["k"]==_2["k"]))` (100k x 100k, 500k output rows): 870 ms / 2.88M allocs; with a per-(shape,name) memo (sync.Map keyed `{*RecordShape, name}`) 728 ms / 1.88M allocs (-16% time, -35% allocs; the prototype even called `os.Getenv` per row). scale-bench scenario1: `ensureRowTableAlias` 15% of CPU (1.36 s of 9.1 s), `UniqueRecordShape` 8%. RECORD fast path: `COUNT(MAP(L,RECORD("a",_["v"],"b",_["k"])))` over 100k: 107 ms / 1.0M allocs -> 84-94 ms / 0.7M allocs (20% faster, 30% fewer allocs).

**Implementation experiment:** look the signature up first (a hit proves uniqueness), linear duplicate scan for <= 8 keys, `strconv.AppendInt` into a stack buffer; memoise per (source shape pointer, table name); `if sh := args.RecordShape(); sh != nil { evaluate values left to right; return NewShapedRecord(sh, values) }` (key literals cannot fail or have effects).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p15"></a>

## GO-P15: FIND is naive O(n*m) on `[]rune` copies

- [x] **P-GO-P15 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P15](../../go-code-review.md). Report labels: [medium] [measured] [].

**Source target:** `go/sel/builtins_text.go:123-154`.

**Benchmark seed / reported evidence:** `FIND(REPEAT("a",100000) & "b", REPEAT("a",200000))` = 15.0 s in Go (JS 25.5 s: a cross-host DoS shape, cheap to remove in Go).

**Implementation experiment:** convert the 1-based rune `from` to a byte offset (ASCII fast path), `strings.Index`, then `utf8.RuneCountInString(hay[:idx])+1`. Identical for valid UTF-8.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p16"></a>

## GO-P16: SQL `Emit.Fill` builds templates one byte at a time by string concatenation; `Lexical()`/`Entry()` rebuild the dialect chain per lookup

- [x] **P-GO-P16 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P16](../../go-code-review.md). Report labels: [medium] [measured].

**Source target:** `go/sel/sql/emit.go:246-255` and `:291-293` (`push(string(tpl[i]))` then `parts[len-1].Sql += s`); `go/sel/sql/map.go:362-379` (`Chain`), `:392-405` (`Lexical`), `:407-444` (`Entry`), each allocating a `seen` map and slice and locking `mapMu` twice; callers include Ident, TextLiteral (two Lexical calls per literal), Placeholder, every `{key}` slot in Fill, FormatLiteral, translator `apply`.

**Benchmark seed / reported evidence:** benchmark (60 x `(AMT + i > 3 AND NAME $== "it's i" AND LEN(NAME) < j)`, mariadb, 200 iterations): 3.6-4.2 ms per translate+render, output 10,648 bytes; pprof of a 180-predicate rule: `Emit.Fill` 35% cumulative, concatstrings 11%, growslice 22%, GC ~25%, Chain 14%, Lexical 10%. With both fixes: 2.25-2.45 ms (about 38% faster; the Chain cache alone about half of that); `sqlt` still 1065/1065 (byte-identical). PlanHybrid on a 3-step pipeline 46-69 us -> 31-34 us.

**Implementation experiment:** push the run of plain bytes up to the next `{`/`}` at once (also the GO-C24 fix); cache the chain per dialect (invalidate in DefineDialect/Reset/Define, never cache "unknown dialect").

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p17"></a>

## GO-P17: SQL pairwise folds are quadratic: `x IN (<n literals>)`, aggregate unrolls over n elements

- [x] **P-GO-P17 — Measure and address this finding.**

  Closed 2026-09-30 — already-addressed (linear since the T09 balanced fold); evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P17](../../go-code-review.md). Report labels: [medium] [measured].

**Source target:** `go/sel/sql/translator.go:1150-1157` (`foldPairwise`) -> `apply` -> `Emit.Fill` (splice at `emit.go:244-258`) copying `acc.Parts` at each step; used by `inOperator` (`:1525`), `aggregate` (`:2231`), `count`, `joinAggregate`.

**Benchmark seed / reported evidence:** `sql.Translate` mariadb, compile excluded): `A IN (0..n-1)`: n=1000 0.09 s, n=4000 0.88 s, n=8000 3.5 s, n=16000 15 s (about 4x per doubling); output only 0.8 MB at n=8000. pprof: 33% in `Emit.Fill` splice closures, the rest GC scan/memclr. JS quadratic too but ~2x faster (4000: 0.41 s).

**Implementation experiment:** obtain the operator template shape once (fill it with two sentinel fragments, split into prefix/infix/suffix) and emit `prefix*(n-1) + p0 + (infix + p_k + suffix)...` in one pass; or build the accumulated fragment as a rope and flatten once. Byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p18"></a>

## GO-P18: `ExecuteHybrid` deep-clones the whole caller context on every execution

- [x] **P-GO-P18 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P18](../../go-code-review.md). Report labels: [medium, only with big contexts] [measured].

**Source target:** `go/sel/sql/hybrid.go:986` (`context.Clone()`).

**Benchmark seed / reported evidence:** a context with a 50,000-record table (3 fields each) plus small ORDERS: `Clone()` 40 ms; 5 `ExecuteHybrid` calls with a trivial continuation 168 ms (~34 ms each), measured with the GO-C5 fix applied (before it the clone never happens). JS does the same.

**Implementation experiment:** shallow-copy the root (same child handles plus `_INPUT`) when the continuation has no assignment targets rooted in caller names (`Dependencies()` separates reads from assignments); else Clone.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p19"></a>

## GO-P19: Regex per-call overhead: `fmt.Sprintf` cache key + global RWMutex; and the cache is unbounded

- [x] **P-GO-P19 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P19](../../go-code-review.md). Report labels: [medium] [measured].

**Source target:** `go/sel/builtins_regex.go:324-330` (key `fmt.Sprintf("%v:%s", ignoreCase, pattern)`, then a shared RWMutex), `:293-296, 359-361` (process-global map, never evicted).

**Benchmark seed / reported evidence:** `compileRegex` cache hit 1.04 us / 40 B / 2 allocs vs ~30 ns for a raw map lookup keyed by a two-field struct. `MAP` over 200,000 items with `RMATCH('^\d{3}-\d{2}$', _)` 0.76 s total (JS 0.89 s; an empty `FILTER` of the same list 0.17 s), so the regex path costs ~3 us/item, ~6x the engine. Unbounded: 100,000 distinct small patterns -> 100,000 entries and 238 MB retained heap (~2.4 KB/entry) after GC, a slow leak / DoS lever for runtime-computed patterns.

**Implementation experiment:** key `struct{ic bool; p string}`; resolve literal patterns once per AST node; bounded LRU (512-4096 entries) or cache only literal patterns.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p20"></a>

## GO-P20: `[]rune` round-trips in TRIM / LEFT / RIGHT / SUBSTR / CODE / BACKWARDS / PADL

- [x] **P-GO-P20 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P20](../../go-code-review.md). Report labels: [medium] [measured] [].

**Source target:** `go/sel/builtins_text.go:16-31` (trimText), `69, 83, 97, 240, 299, 34-51`.

**Benchmark seed / reported evidence:** `LEN(TRIM(<80 MB ASCII>))` 2.19 s vs 0.31 s without TRIM (~1.9 s for scanning a few edge bytes; JS 14.5 s). `LEN` itself is fine. `CODE(s)` converts the whole string to read `runes[0]`.

**Implementation experiment:** TRIM* by byte scan (all four trimmed chars are ASCII); CODE via `utf8.DecodeRuneInString`; LEFT/RIGHT/SUBSTR by walking to the k-th rune boundary with an ASCII fast path; PADL/PADR with `strings.Builder` and known rune counts.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p21"></a>

## GO-P21: `Guard` recomputes 10^N to count digits on maximum-size values; `TrimScale` and `Cmp` round-trip through strings / aligned copies

- [x] **P-GO-P21 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P21](../../go-code-review.md). Report labels: [low] [measured].

**Source target:** `dec.go:99-121` (`numDigits` calls `Pow10(d-1)` up to twice; values above 1,000,000 digits are never cached; the cache is flushed when weight passes 1,048,576); `dec.go:198-217, 331-352, 250-256` (TrimScale, Cmp, IsInteger).

**Benchmark seed / reported evidence:** a legal 2,000,000-digit / scale-1,000,000 value costs ~200 ms per `Guard` (i.e. per Add/Mul/Round) vs ~1 ms for the addition (`Add d+d` 195 ms, `Add d+1` 390 ms). TrimScale of a 1M-digit value 760 ms (String then SetString), 1.5 us for a 10-digit one; `Cmp` with different scales allocates a scaled copy (521 ns / 2 allocs small, ~100 ms with a 1M-digit scale before the Pow10 cache warms).

**Implementation experiment:** bound digit count from `BitLen` (`lo = floor((bitLen-1)*log10 2)+1`, `hi = floor(bitLen*log10 2)+1`) and call `Pow10` only when `[lo,hi]` straddles the cap; strip trailing zeros by chunked `QuoRem`; compare different-scale operands by (BitLen/numDigits - scale) first.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p22"></a>

## GO-P22: `nodeContainsVar` walks the whole body AST (and allocates via `AsciiUpper` per Var node) on each aggregate call

- [x] **P-GO-P22 — Measure and address this finding.**

  Closed 2026-09-30 — rejected (no gain under CPU-time A/B (P8 per-node defer x1.00; P22 x1.03)); evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P22](../../go-code-review.md). Report labels: [low] [reasoned].

**Source target:** `builtins_aggregate.go:17-46`, called from `:64, :215, :301, :390`.

**Benchmark seed / reported evidence:** the answer depends only on the node; nested aggregates (`MAP(orders, SUM(lines, ...))`) redo the walk per outer row. Identifiers are already upper-case at lex time (spec 2.3), so `node.S == "_K"` suffices. Not measured.

**Implementation experiment:** compare `node.S == "_K"`; cache the walk result on the body Node (compile-time or atomic bool).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p23"></a>

## GO-P23: Redundant UTF-8 re-validation, binary helpers, `AsciiUpper`/`AsciiLower` double copy

- [x] **P-GO-P23 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P23](../../go-code-review.md). Report labels: [low] [reasoned/measured mix].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Implementation experiment:** - **Found by:** go-4 P6, P7, P8. - `builtins_text.go:167 (REPLACE), :184 (SPLIT), :255 (REPEAT)` call `NewText`, which runs `utf8.ValidateText` (a per-rune `range` loop, ~2-3x slower than `unicode/utf8.ValidString`) on results built from valid inputs. Reasoned only (REPLACE of 50M chars measured 2.1 s total). Use `NewTextOwned`. - Binary (measured): ENCODE+DECODE of a 16 MB buffer 0.94 s (JS 6.0 s, acceptable); `BTL` of a 4 MB BIN 1.7 s (0.4 us/byte, one decimal allocation per element; a shared cache of the 256 byte-valued Values needs the aliasing rule checked first). `builtins_binary.go:105-118` ENCODE_BASE64 unsized `append`; `:122-165` DECODE_BASE64 `map[byte]int` lookup per char and unsized `append`; `:185-189` BTL; keep the hand-written strictness (Go's `encoding/base64` Strict mode would change acceptance of non-canonical trailing bits such as `YR==`, which all hosts accept). Fix: `[256]int8` table, `make([]byte, 0, n)`. - `AsciiUpper`/`AsciiLower` (`utf8.go`): `[]byte(s)` then `string(b)`, no early exit; negligible except on very large text.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p24"></a>

## GO-P24: One-shot `Eval(src)` pays for optimisation it cannot amortise

- [x] **P-GO-P24 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P24](../../go-code-review.md). Report labels: [low] [measured].

**Source target:** `go/sel/program.go:170-176` (`Eval` = Compile + Run, with Run calling `OptimizeAST`).

**Benchmark seed / reported evidence:** go test -bench, 20000x): Parse `X + 1` 2.8 us, Optimize 1.1 us, run 0.62 us (plain unoptimised evaluation 0.55 us); medium expression: parse 15.3 us, optimize 4.0 us; pipeline: parse 22.9 us, optimize 5.9 us. Optimisation is ~25% of a run-once total. For repeated runs the plan pays off: `A*2 + B%7 - C` 1.32 us plan vs 2.45 us plain; the 3-op-per-side expression 4.5 vs 5.8 us; the 1-op `A + 1` is break-even (518 vs 515 ns).

**Implementation experiment:** `Eval`/`MustEval` evaluate `p.AST()` directly (spec 8: the physical tree is a function of the AST only), or skip planning when there are < 2 operators.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p25"></a>

## GO-P25: SlotCache miss path allocates on every polymorphic site

- [x] **P-GO-P25 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P25](../../go-code-review.md). Report labels: [low] [reasoned].

**Source target:** `go/sel/eval.go:100-104` (`node.SlotCache.Store(&SlotCache{...})` on every shape miss).

**Benchmark seed / reported evidence:** records with alternating shapes reallocate a 16-byte cache entry and hammer one atomic store per evaluation; concurrent users of one Program bounce the cache line. Not measured.

**Implementation experiment:** count misses and stop storing after a few (megamorphic), or store only when the old entry is nil.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p26"></a>

## GO-P26: DISTINCT/BUCKET hashing; structural hash quality

- [x] **P-GO-P26 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P26](../../go-code-review.md). Report labels: [low] [measured].

**Source target:** `value.go` `structuralHashAt` (`v.Entries()` per container, `Scalar()` formats decVal on first use); `builtins_structure.go:210-235`; `builtins_aggregate.go:410`.

**Benchmark seed / reported evidence:** `DISTINCT(L)` 100k records 78 ms / 300k allocs; `DISTINCT(NUMS)` 65 ms / 200k allocs; 2-arg `BUCKET(L,_["k"])` 126 ms / 612k allocs (mostly the mandatory Clone of each row). Adequate. The hash is a homegrown FNV/multiply-xor combine with a fixed seed; equal-hash records degrade a bucket to a linear Eql scan. Not exploited (no hash-flooding finding).

**Implementation experiment:** hash shaped records via `shape.Keys` + `storage` without building Entries; precompute the key hash once per shape.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p27"></a>

## GO-P27: Parser hot loop details; numeric literal cost (null result)

- [x] **P-GO-P27 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P27](../../go-code-review.md). Report labels: [low] [measured].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Implementation experiment:** - **Found by:** go-1 P5, P6. - `infixEntry` does a map lookup (`mapaccess2_faststr`, ~5% of parse) on every loop iteration of `parseTerm`; a byte/enum set by the lexer would remove it. `defer p.leave()` is open-coded and cheap. 1M-term `1+1+...+1` compiles in 2.3-3.6 s (2.6 s with GO-P6 applied); nothing algorithmic wrong. - Numeric literals cost ~10 allocations each (`1 + 12.5 + ...` x 100k: ~200 ms, ~1.0M allocs). Replacing `decimal.Format(parsed)` with a text canonicaliser (verified equal on 200k random literals) removed 200k allocs but wall time was within noise (best-of-5: 154 vs 158 ms): **not recommended**. GC/allocation of the node tree dominates (Node ~176 B; `NewNode` 40% of parser alloc bytes; each NodeIndex allocates an `atomic.Pointer`).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p28"></a>

## GO-P28: Smaller SQL costs

- [x] **P-GO-P28 — Measure and address this finding.**

  Closed 2026-09-30 — implemented (partly) (remaining sub-items measured and deferred in the results row); evidence: performance/results/go.md, tools/perf/go/

Source: [GO-P28](../../go-code-review.md). Report labels: [low] [measured / reasoned].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Implementation experiment:** - **Found by:** go-6 P2, P3; go-7 P4, P5. - **P2 (measured):** `translator.go:252-259` validates `IsConstant(n)` then `Validate(n)` for every compound constant node, and `ToNode` (`node.go:135-181`) deep-copies each time: a depth-195 chain `1+1+...+1` costs 38 ms per translate vs ~3 ms with a column; `SUM((n literals), _ + 1) > 0` at n=1000 113 ms, n=4000 2.0 s. Bounded by MAX_DEPTH for chains. Fix: validate only the maximal constant subtree. - **P3 (reasoned):** `Translator.binder()` (`translator.go:310-320`) heap-copies the ~200 B `Binder` per variable lookup; `RelationSpec.Field` (`binding.go:59`) uppercases and returns a pointer to a heap copy per call; `BuildJoinRows` (`row_model.go:206`) is rebuilt per `withRow`/`withJoinBinders`/`joinedRowFields` call; `constScope` re-sorts `Bindings.Names()` on every Begin. Not visible at realistic sizes (the whole 1065-case suite runs in 0.33 s). - **`TextLiteral` (measured):** sorts its escape keys and scans byte-by-byte with `HasPrefix` per key on every call: 100,000 short literals ('hello', mariadb) 193 ms = 1.9 us each; a 1.1 MB text 40 ms (mariadb), 25 ms (postgresql), ~36 ns/byte. Fix: a `strings.Replacer` (or byte table, all keys are one byte in shipped dialects) cached per dialect. - **Hybrid (reasoned, unmeasured):** `referencedAssignments` re-runs `readNames` per kept assignment on every fixpoint pass (O(k^2 * size)); `helpers.wrap` is rebuilt for every candidate split; `PlanHybrid` retries `TryTranslateStatement` for each prefix length from the longest down (s failed full translations for an s-step pipeline whose only translatable prefix is short). PlanHybrid measured 30-70 us for small pipelines.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="go-p29"></a>

## GO-P29: Go GC configuration dominates large-context workloads

- [ ] **P-GO-P29 — Measure and address this finding.**

  Deferred 2026-09-30 — documentation-only GC tuning advice (GOGC=200: -21% CPU, +45% memory on scenario 1); recommended text is in the round-3 row, no library-wide setting; reconsider when the Go usage docs are next revised — place the recommended text there. Evidence: performance/results/go.md

Source: [GO-P29](../../go-code-review.md). Report labels: [low] [reasoned].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** every scale-bench scenario spends 55-62% of CPU in `gcDrain`/`scanObjectsSmall` (pprof). Embedders with a large resident context can raise `debug.SetGCPercent`; the library could document this (compare Python's `_gc.py` note in contributing.md). No code change; it is why the allocation cuts above pay off twice.

**Implementation experiment:** - **Found by:** go-5 P10. - **Evidence:** every scale-bench scenario spends 55-62% of CPU in `gcDrain`/`scanObjectsSmall` (pprof). Embedders with a large resident context can raise `debug.SetGCPercent`; the library could document this (compare Python's `_gc.py` note in contributing.md). No code change; it is why the allocation cuts above pay off twice.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.
