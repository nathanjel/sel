# JS performance

[Worklist](../README.md) · [Performance protocol](../07-performance.md)

Report measurements below are historical evidence, not verified targets for this machine. Recreate each workload in the repository; scratchpad paths mentioned by reviewers are not dependencies. The shared performance protocol applies to every task, including reasoned/guessed opportunities and subitems bundled in a finding.

<a id="js-p1"></a>

## JS-P1: `Value.text` scans every result string for surrogates, which flattens V8 ropes: repeated `&`/`&=` is ~50x slower than necessary and superlinear

- [x] **P-JS-P1 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p01-text-concat.mjs

Source: [JS-P1](../../js-code-review.md). Report labels: [high] [measured].

**Source target:** `value.mjs:14-18` (`checkText`), `:186-190` (`Value.text`); called from `eval.mjs:375` (`concat`), plus 16 more sites in `builtins/text.mjs` and aggregate/regex/binary/control.

**Benchmark seed / reported evidence:** node v24.16): program `L = SPLIT(REPEAT("a,", n), ","); S = ""; ALL(L, IS_NULL(S &= "abcdefghij") OR TRUE)`: n=10000 116 ms; n=20000 1347 ms; n=40000 7951 ms (68x for 4x the work). With `Value.text` monkeypatched to `new Value(TEXT, s)`: 32 / 74 / 151 ms (52x faster at n=40000, linear). Pure micro: 40000 appends 7.7 s with the check versus 0.5 ms raw. A rule folding untrusted data into a string is a practical DoS.

**Implementation experiment:** internal `Value.textOwned(s)` (no check) for `concat` and builtins whose output derives from validated Values; keep `Value.text` checking for host input. Byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p2"></a>

## JS-P2: The lexer builds a `fromCodePoints([c])` string per character and dominates compile time

- [x] **P-JS-P2 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p02-lexer.mjs

Source: [JS-P2](../../js-code-review.md). Report labels: [high] [measured] [c].

**Source target:** `lexer.mjs:39` (`.map((c) => fromCodePoints([c]))`), `:47-54` (`posAt` binary search per token), `:77-110` (`{ ..., ...pos }` spread per token), `:114-124` (`matchOperator` scans the 35-entry OPERATORS list per operator character).

**Benchmark seed / reported evidence:** 2 MB source, 720k tokens, `tokenize` best of 7: 1,050 ms as is; about 600-660 ms with `String.fromCodePoint(c)`; 530-580 ms with a first-character operator table on top; 420-470 ms after also building token objects literally (no spread) and caching the last line index in `posAt`. Overall 2.3-2.5x (loaded box). The optimised copy (`scratchpad/js1/lx/lexer3.mjs`) produces byte-identical JSON token streams on a 2,000-line mixed sample. `parse(src)` in full is 1,790 ms, so lexing is ~60% of compile.

**Implementation experiment:** (1) `String.fromCodePoint(c)` for the chars array (or keep numeric code points and slice the source for token values); (2) an `OPS_BY_FIRST[c]` table; (3) token literals `{ type, value, line, col, offset }` with incremental line/col instead of `posAt` plus spread.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p3"></a>

## JS-P3: Cliff for rows with more than ~254 fields: shape interning stops, so every join pair builds its own plan (36x slower, 240 MB extra)

- [x] **P-JS-P3 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p03-wide-join.mjs

Source: [JS-P3](../../js-code-review.md). Report labels: [high] [measured].

**Source target:** `structure.mjs:264` (`keys.length <= 256 && … <= 16384` in `ensureRowTableAlias`), `value.mjs` `SHAPE_CACHE_MAX_KEYS = 256` / `SHAPE_CACHE_MAX_CHARS = 16384` (`recordShape`), consumed by `makeJoinProjector` (:503-533: `plans.get(left.shape)`) and `rowPlan`/`internRecordShape` (:397).

**Benchmark seed / reported evidence:** `bench5.mjs`, equi join 3,000 x 3,000 rows, C columns each): C=100 67 ms; C=250 90 ms; C=260 3311 ms (+243 MB heap); C=300 4442 ms; C=500 6034 ms. With both caps raised (keys 4096, chars 262144, patched copy): C=260 91 ms, C=300 75 ms, C=500 188 ms (36x). Falling back to `makeJoinedRow` for wide rows was not better (1.9 s at C=250).

**Implementation experiment:** keep bounding the number of cached shapes (256 entries) but not each shape's width (or bound by total characters at a much larger value); key the alias-plan cache on the shape in a WeakMap; stop `plans` growing per row when shapes are not interned. Shapes are internal, so byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p4"></a>

## JS-P4: SQL translator re-evaluates constant sub-trees at every nesting level (quadratic amplification, ~150x at 160 terms)

- [x] **P-JS-P4 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p04-sql-constants.mjs

Source: [JS-P4](../../js-code-review.md). Report labels: [high as a DoS] [measured].

**Source target:** `translator.mjs:243-256` (`node()`: `isConstant` then `dispatch` then `constants.validate`) calling `constants.mjs:169-241` (`isConstant`/`validate`/`requireNumeric`/`constantScale`); also `translator.mjs:1924, 1961-1979` (`guardNumeric`, `requireNumericConstant`, `coerceScaleLimits`).

**Benchmark seed / reported evidence:** js-7 `cv.mjs`: `T .> FILTER(_["n"] > LEN(REPEAT("x",20000)) + ... k terms)`: k=10 translate 250 ms (one eval 24 ms); k=40 1368 ms (33 ms); k=80 4341 ms (68 ms); k=160 17.3 s (111 ms). Rule text ~5 KB. `isConstant` is 6% of self time on a 30-clause translate. js-6: a 190-term constant chain `1 + 1 + ... > 0` takes 11.2 ms per translate versus 2.1 ms over a column (50-run average), i.e. O(depth^2) evaluator runs, bounded by depth 200.

**Implementation experiment:** memoise per node (WeakMap node -> {isConstant, value-or-SelError}) for the duration of a translation, evaluate a maximal constant subtree once, and have children inherit; error position/code stay the innermost node's, so byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p5"></a>

## JS-P5: Every text builtin round-trips the whole string through a code point array

- [x] **P-JS-P5 — Measure and address this finding.**

  Closed 2026-09-30 — implemented (partly) (remaining sub-items dispositioned in the results row); evidence: performance/results/js.md, tools/perf/js/p05-text-builtins.mjs

Source: [JS-P5](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `text.mjs:10` (`cps`), LEN, LEFT, RIGHT, SUBSTR, FIND, REPLACE, SPLIT, TRIM/LTRIM/RTRIM, UPPER/LOWER, BACKWARDS, pad, CODE (:21-176); `utf8.mjs:15-40` (`toCodePoints`/`fromCodePoints` with `cps.slice(i, i+4096)` + `apply`).

**Benchmark seed / reported evidence:** node 24): 1M-character string, `LEFT(S, 5)` ~65 ms per call, `LEN` ~61 ms per call. On a 30-character row: TRIM 851 ns versus 51 ns with a `charCodeAt` scan and one `slice` (17x); LEN 244 ns versus 44 ns with a no-surrogate fast path (5.5x); UPPER 809 ns. A 300k-row pipeline with `TRIM(name)` spends ~1 us per call against 0.85 us per-row evaluator baseline. js-2 N2: 2500 iterations of `LEN(S &= "abcdefghij") > 0` over a growing S spent 545 of 750 ms in `toCodePoints`.

**Implementation experiment:** one helper `hasSurrogates(s)` (regex test, or tracked once per Value); when false use the native path (`LEN` to `s.length`, `LEFT/RIGHT/SUBSTR` to `slice`, `TRIM` via `charCodeAt` over the four ASCII whitespace units, `FIND` to `indexOf`). Keep the code point path for strings with surrogates. Byte-identical because SEL whitespace is ASCII.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p6"></a>

## JS-P6: FIND, REPLACE and SPLIT are naive O(n*m) over number arrays

- [x] **P-JS-P6 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p05-text-builtins.mjs

Source: [JS-P6](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `text.mjs:12-19` (`indexOfCp`), used at :61, :75, :95; also feeds JS-C4.

**Benchmark seed / reported evidence:** `FIND(REPEAT("a",20000)&"b", REPEAT("a",40000))` takes 1.5 s; doubling both to 40k/80k takes 5.7 s (quadratic). Native `hay.indexOf(needle)` on the same inputs takes under 1 ms. A well-formed needle can only match at a code point boundary of a well-formed haystack.

**Implementation experiment:** `indexOf` on the UTF-16 strings and convert the resulting offset with a single pass (needed only for `FIND`); REPLACE and SPLIT work purely on UTF-16 with `slice`/`join`, or `split`/`replaceAll` with string arguments (literal in JS). Keep the array path only for the FIND `from` conversion.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p7"></a>

## JS-P7: UTF-8, hex and base64 codecs go through intermediate arrays and per-byte strings

- [x] **P-JS-P7 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p07-codecs.mjs

Source: [JS-P7](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `utf8.mjs:44-64` (`encodeUtf8`: `toCodePoints`, then `out.push` per byte, then `Uint8Array.from`), `:117-123` (`bytesToHex`: `toString(16).padStart(2,'0')` per byte), `:68-115` (`decodeUtf8`); `binary.mjs:22-28` (FROM_HEX: slice, regex `.test`, `parseInt` per pair), `:62-84` (DECODE_BASE64: `Map` lookup per char, plain array, then `Uint8Array.from`).

**Benchmark seed / reported evidence:** js-1 (`enc.mjs`, identical output verified with `Buffer.compare`): 1.6M-char mixed text `encodeUtf8` 210 ms versus 28 ms with a one-pass loop into a preallocated `Uint8Array(n*3)`; 2M ASCII 209 versus 26 ms; `bytesToHex` on 2 MB 454 ms versus 21 ms with a 256-entry table; 11-character strings, 1e6 calls: 318 versus 162 ms. js-4 (1 MB): `bytesToHex` 355 ms versus 57-64 ms (6x); FROM_HEX 131 ms versus 14 ms (9x); `encodeUtf8` 97 ms versus 5 ms `TextEncoder`; `decodeUtf8` 51 ms versus 2 ms `fatal: true` `TextDecoder`; `CRC32(REPEAT("x",1000000))` 219 ms and `BASE64` encode 355 ms although the encode loop is 16 ms; 300k-row `DECODE_BASE64("QUItMTAwMA==")` ~2.4 us per call (966 ms). js-1 P6 (guessed, not prototyped): `decodeUtf8` measured 83 ms (mixed) and 139 ms (ASCII) for 2 MB; an ASCII run fast path would likely help several-fold.

**Implementation experiment:** single-pass encoder into a preallocated buffer with the same surrogate validation and messages; 256-entry hex lookup; `charCodeAt` tables for hex/base64 decoding into a preallocated `Uint8Array`; ASCII fast path for decode. No host codec is used unless `TextDecoder` is given `{ fatal: true, ignoreBOM: true }` (else a leading U+FEFF is dropped; `FROM_UTF8(FROM_HEX("efbbbf41"))` must keep 2 code points). Error paths keep the offending character for the message.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p8"></a>

## JS-P8: Text comparison encodes both operands to UTF-8 on every comparison; the sort/TOP comparator re-derives each key's class every time

- [x] **P-JS-P8 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p08-sort-compare.mjs

Source: [JS-P8](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `eval.mjs:335`, `aggregate.mjs:289` via `asBytes` (`value.mjs:464`, `encodeUtf8` then `bytesCompare` `utf8.mjs:133`); `aggregate.mjs:269-301` (`compareValues`: `isNull`, `looksNumeric` x2, `asDecimal` x2, `asBytes` per compare), called from :365 and :413/:418 (TOP heap).

**Benchmark seed / reported evidence:** js-1: 400k comparisons of 10-20 character strings: 756 ms (encode + `bytesCompare`) versus 308 ms with a direct UTF-16 code-unit loop mapping units above D800 the way UTF-8 order requires; agreement on 20,000 sampled pairs and the edge set. Caveat: `encodeUtf8` is where a host string with an unpaired surrogate becomes E_UTF8, so a fast path must keep that check (take it only when a `/[\ud800-\udfff]/` test fails on both strings). js-5 (`bench4.mjs`, 200,000 keys): SORT of "k123" text 6007 ms; with keys decorated once 2036 ms (2.9x). SORT of numeric text 1907 ms; remaining cost is `D.cmp`'s BigInt scale alignment (`decimal.mjs:206`) and GC (comparator 549 ms, cmp 404 ms, GC 327 ms).

**Implementation experiment:** `compareText(a, b)` in `utf8.mjs` with the guarded fast path; decorate each sorted element once (isNull, kind, decimal-or-null, bytes) and compare decorated records (any fix for JS-C2 goes in the same place); gate `_K` on `nodeContainsVar` in `doSort`; fast path in `D.cmp` for equal scale / small digits.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p9"></a>

## JS-P9: `Value.fromEntriesOwned` re-derives the record shape from scratch for every row (~4x slower than a shape check)

- [x] **P-JS-P9 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p09-entries-shape.mjs

Source: [JS-P9](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `value.mjs:115-127` (`shapedFromUniqueEntries`), `:85-99` (`recordShape`: `JSON.stringify(keys)` plus a Map lookup on a fresh string), `:254-262`.

**Benchmark seed / reported evidence:** 6 keys, 1e6 iterations): `fromEntriesOwned` 2428 ns/row (entry-building alone 204 ns, `JSON.stringify` alone 669 ns); via `shapedFromShape` when the shape is known 106 ns. A prototype comparing entry keys against a "last shape used" (6 pointer compares) with fallback: 577 ns/row (4.2x faster) including entry building.

**Implementation experiment:** keep a one-entry "last shape" cache in `shapedFromUniqueEntries` (check length and `entries[i][0] === shape.keys[i]`; uniqueness is guaranteed by the shape); fall back to Set + JSON. Let `recordShape` compare a candidate key list against a cached shape instead of building a JSON signature every call.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p10"></a>

## JS-P10: `D.parse` is ~4x slower than needed and is ~17% of a numeric-data workload

- [x] **P-JS-P10 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p10-decimal-parse.mjs

Source: [JS-P10](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `decimal.mjs:104-119` (regexp test, `slice`, `indexOf`, `slice` x2, concatenation, `replace(/^0+/)`, then `BigInt(string)`).

**Benchmark seed / reported evidence:** `parse("12.50")` 689 ns, `"3"` 650 ns, `"-0.05"` 868 ns versus a single-pass `charCodeAt` prototype (strings up to 16 chars, accumulate a Number, one `BigInt(v)`) at 162, 161, 194 ns; 300k random short strings fuzzed against `D.parse`: 0 mismatches. Profile of `ALL(L, _ * 2 + 1 > 0)` over 200k numeric strings: `parse` was 286 ms of 1673 ms total self time (17%); `add`/`cmp` are 35-70 ns each.

**Implementation experiment:** fast path for `length <= 15` digits (exact in a double; pure integer accumulation, still ASCII-checked before any conversion), otherwise the existing path (16 chars cannot reach a cap).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p11"></a>

## JS-P11: One non-plannable operand (IF, COND, `,`, `;`, assignment) disables planning for the whole arithmetic expression

- [x] **P-JS-P11 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p11-plan-with-if.mjs

Source: [JS-P11](../../js-code-review.md). Report labels: [medium] [measured (noisy) + reasoned].

**Source target:** `math_plan.mjs:173-175` (returns null for `assign`/`seq`/`list`/IF/COND), `optimizer.mjs:535-557` (`inMath` is propagated to every child, so a child never gets its own plan once its parent is math).

**Benchmark seed / reported evidence:** `bench_if.mjs`, 100k rows, cpu-ms min/median over 9 runs): planned 109-204 / 125-211 versus unplanned 250-256 / 272-338 (ratio ~1.3-2.3x, indicative only at high load).

**Implementation experiment:** when `compileMathPlan(folded)` returns null, retry on each child with `inMath=false` (bottom-up), or let IF/COND be LOAD_LEAF (laziness preserved because a leaf is evaluated by `evalNode`). Result bytes unchanged provided JS-C9 is decided consistently.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p12"></a>

## JS-P12: The append idiom `A = (A, x)` is O(n^2) with ~300 ns per copied element, 64% GC

- [x] **P-JS-P12 — Measure and address this finding.**

  Closed 2026-09-30 — implemented (partly) (remaining sub-items dispositioned in the results row); evidence: performance/results/js.md, tools/perf/js/p12-append.mjs

Source: [JS-P12](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `eval.mjs:271-282` (`evalList` clones every child) and `:410` (`evalAssign` clones the whole list a second time), `value.mjs` `cloneAt` (one 9-field `Value` per scalar).

**Benchmark seed / reported evidence:** `append2.mjs`): 2000 appends 1.05-1.23 s CPU, 4000 appends 3.6-3.9 s (20000 killed after minutes); profile 63.7% GC, 19% `cloneAt`, 7% `evalList`. Removing the redundant second copy (skip `.clone()` when `node.value.t === 'list'`) measured only ~15% (1234 to 1042 ms at 2000). The redundant copy also matters for the depth boundary (a fresh list of 200-deep children is 201 deep; the assignment clone raises E_DEPTH), so it is not free to drop.

**Implementation experiment:** structural (share immutable scalar leaves / copy-on-write, or in-place `A = (A, e)` append when the old value is unreferenced), which is `value.mjs` territory and must preserve §3.4. Partial win: return the same object from `cloneAt` for childless, decimal-cached scalars only if Value gains an immutability guarantee (it does not: `A[1][2] = 3` can add children to a scalar).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p13"></a>

## JS-P13: `doSort` deep-clones every result element

- [x] **P-JS-P13 — Measure and address this finding.**

  Closed 2026-09-30 — rejected (SORT/SORT_BY copy is the SPEC 3.4 contract); evidence: performance/results/js.md, tools/perf/js/p13-sort-clone.mjs

Source: [JS-P13](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `aggregate.mjs:371`.

**Benchmark seed / reported evidence:** SORT_BY over 200,000 records with a nested record and list: 3043 ms, 1629 ms without the clone (clone ~46% of the time plus GC).

**Implementation experiment:** drop the clone; needs the spec decision in JS-C48. Aliasing is the majority behaviour.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p14"></a>

## JS-P14: `nodeContainsVar` walks the aggregate body on every aggregate call

- [x] **P-JS-P14 — Measure and address this finding.**

  Closed 2026-09-30 — rejected (0-5% on the report workload (noise)); evidence: performance/results/js.md, tools/perf/js/p14-contains-var.mjs

Source: [JS-P14](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `aggregate.mjs:30-43` (called at :53/54, :443, :537), with two `toUpperCase()` allocations per `var` node.

**Benchmark seed / reported evidence:** js-5 profile of `MAP(R, SUM(_["b"], <6-term arithmetic body>))` over 200,000 outer rows: `nodeContainsVar` 314 ms of 1638 ms total (19%). js-3 rated it low (reasoned, unmeasured).

**Implementation experiment:** memoise the boolean on the physical node (`node.usesK` slot, or WeakMap; the physical tree is a function of the AST) and compare names with the already upper-cased form; compute iteratively while doing it (JS-C12), next to `keysUnobserved`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p15"></a>

## JS-P15: `foldPairwise` is quadratic and produces a left-deep chain as deep as the list

- [x] **P-JS-P15 — Measure and address this finding.**

  Closed 2026-09-30 — already-addressed (SQL balanced fold (T09 wave)); evidence: performance/results/js.md, tools/perf/js/p15-fold-pairwise.mjs

Source: [JS-P15](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `translator.mjs:858-864` (`foldPairwise`), `emit.mjs` `fill` (splice of the accumulated parts); callers `inOperator` (852), unrolled `aggregate` (1514), JOIN, COUNT.

**Benchmark seed / reported evidence:** `X IN L` (TEXT column, value-binding list of N strings, mariadb, translate only): N=2000 0.24 s; 4000 0.86 s; 8000 2.3 s; 16000 10.2 s (4x per doubling). `--cpu-prof` at N=8000: `fill` 690 ms self, `apply` 251 ms, GC 113 ms. The output has parenthesis depth N (N=16000 gives 16001 nested parens); MySQL/MariaDB/PostgreSQL parsers have stack limits on nested expressions (reasoned, not run).

**Implementation experiment:** build the left-deep chain iteratively without recopying (a rope of nested part arrays flattened once in `Fragment.#join`, or outer parentheses as prefix/suffix counts). Output must stay byte-identical, which rules out a balanced tree unless the sqlt expectations for 3+ element lists change.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p16"></a>

## JS-P16: `executeHybrid` deep-clones the entire caller context on every hybrid run

- [x] **P-JS-P16 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p16-hybrid-context.mjs

Source: [JS-P16](../../js-code-review.md). Report labels: [medium] [measured].

**Source target:** `hybrid.mjs:730`.

**Benchmark seed / reported evidence:** js7/perf1.mjs): context holding a 200k-row unrelated table: 149 ms per run of a 3-row hybrid plan versus 0.09 ms with a small context.

**Implementation experiment:** shallow root (alias top-level entries, add `_INPUT`), copying only if the continuation assigns to a context name; behaviour changes only in the JS-C60 sense.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p17"></a>

## JS-P17: BTL allocates a BigInt-backed decimal per byte; `Value.int(bigint)` first-call cost

- [x] **P-JS-P17 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p17-btl.mjs

Source: [JS-P17](../../js-code-review.md). Report labels: [low-medium] [measured].

**Source target:** `binary.mjs:114` (`Value.int(b)`), `value.mjs` `Value.int` to `D.fromInt` (BigInt); `value.mjs:56-57, 299`.

**Benchmark seed / reported evidence:** building 1M byte Values 666 ms with `Value.int`, 211 ms with 256 pre-built decimals plus `Value.num`. `BTL` over 300k 7-byte rows took 2.46 s (~7 us per row). LTB itself is fast (46 ms per 1M); `Value.list` re-scanning an array the builtin just built is 49 ms per 1M. js-2: `Value.int(bigint)` forces `10n  1000000n` (87 ms, ~415 KB retained) on the first BigInt ever passed; a `n < 10n18n` shortcut avoids it (first-call latency only, reasoned).

**Implementation experiment:** a module-level table of 256 decimals; `Value.listOwned(out)` in BTL, SPLIT and RGROUPS. Values must stay distinct objects; only decimal payloads shared (confirm Decimal objects are immutable in this host).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p18"></a>

## JS-P18: Over-cap rejection in `guard()` costs up to ~0.5 s per rejected operation because `numDigits` builds an uncached 10^k

- [x] **P-JS-P18 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p18-guard-reject.mjs

Source: [JS-P18](../../js-code-review.md). Report labels: [low-medium] [measured].

**Source target:** `decimal.mjs:61-67` (`numDigits` to `pow10(d - 1)`), `:80-92`; `mul`/`round`/`add` compute the full oversize result before `guard`.

**Benchmark seed / reported evidence:** `guard` on a 1M x 1M digit product 461 ms (`10n1000000n` alone 87 ms, 2M ~350 ms); the multiply itself 128 ms. `POWER(POWER(3,99999),21)` E_RANGE takes 0.57 s, `POWER(999999999999, 100000)` 0.67 s. One-shot (E_RANGE ends the run; `??` does not catch it), so a bounded latency spike.

**Implementation experiment:** bracket the digit count from the bit length with integer arithmetic (lower bound `floor((b-1)*30102/100000)+1`, upper `floor(b*30103/100000)+1`), reject when the lower bound minus scale exceeds the cap, accept when the upper bound does not, and call the exact count only in the ambiguous band. `numDigits` was verified exact on 1.2M values, so it stays as tie-break. Optionally pre-check operand bit lengths in `mul`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p19"></a>

## JS-P19: `SelError` captures a stack trace, and errors are used as control flow in several probes

- [x] **P-JS-P19 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p19-join-bad-keys.mjs

Source: [JS-P19](../../js-code-review.md). Report labels: [low-medium] [measured].

**Source target:** `errors.mjs:3-11`; consumers such as `builtins/structure.mjs:208, 218, 676` (`canonicalJoinKey`, join-key checks: `try { asDecimal } catch (SelError)`) and `value.mjs:500`.

**Benchmark seed / reported evidence:** `err.mjs`): `throw new SelError` 7.9 us at depth 5 and 14 us at depth 60 versus 2.2 and 7.8 us for a plain throw, and ~2.7 and 8.1 us with `Error.stackTraceLimit = 0`. A numeric join over a text column full of non-numbers pays this per row.

**Implementation experiment:** non-throwing probes (`tryAsDecimal`; `D.parse` already returns null) in the hot paths, rather than changing `SelError`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p20"></a>

## JS-P20: Regex `compile()` pays ~0.45 us fixed overhead per call, plus an uncached ASCII scan for the `i` flag

- [x] **P-JS-P20 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p20-regex-compile.mjs

Source: [JS-P20](../../js-code-review.md). Report labels: [low-medium] [measured].

**Source target:** `regex.mjs:204-244`.

**Benchmark seed / reported evidence:** raw `re.test` loop 0.16 us per call; with key concatenation and Map lookup 0.61 us; the `i`-flag ASCII check adds ~0.35 us. A 300k-row filter `RMATCH('^[a-z0-9]+@[a-z.]+$', email)` costs ~2.2 us per row against ~0.85 us for an empty FILTER.

**Implementation experiment:** remember the last `(pattern, flags)` string pair and its RegExp (`===` on both), or cache on the AST node when the pattern is a literal; move the non-ASCII check inside the cache-miss branch (the error still fires on the first call).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p21"></a>

## JS-P21: The regex compile cache is unbounded and keyed by data-derived patterns

- [x] **P-JS-P21 — Measure and address this finding.**

  Closed 2026-09-30 — already-addressed (regex cache bounded at 256 (T06 wave); remaining retention is V8 own regexp cache); evidence: performance/results/js.md, tools/perf/js/p21-regex-cache.mjs

Source: [JS-P21](../../js-code-review.md). Report labels: [low] [measured].

**Source target:** `regex.mjs:202, :231-241`.

**Benchmark seed / reported evidence:** 300,000 distinct patterns retained 187 MB (~620 bytes each) and took 8.1 s (~27 us per miss). A rule taking its pattern from data (`RMATCH(_["pat"], text)`) leaks one RegExp per distinct pattern in a long-lived server; a hostile input can drive it.

**Implementation experiment:** cap the Map (a few thousand entries; drop the oldest on insert, easy with Map iteration order).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p22"></a>

## JS-P22: Non-equi LINK re-aliases the right row for every pair

- [x] **P-JS-P22 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p22-link-realias.mjs

Source: [JS-P22](../../js-code-review.md). Report labels: [low-medium] [measured, noisy].

**Source target:** `structure.mjs:1140-1149` (`ensureRowTableAlias(rightItem, b2)` inside the inner loop).

**Benchmark seed / reported evidence:** 600 x 600 non-equi (3,300 matches): 646 to 461 ms, 733 to 558 ms, 692 to 558 ms in three paired runs (~15-25%, loaded box). `ALIAS_PLANS` stores one tableName per shape (`structure.mjs:257-259`), so relations sharing a shape but differing in name rebuild a RecordShape twice per left row here.

**Implementation experiment:** alias the right rows once before the outer loop; key `ALIAS_PLANS` on (shape, tableName).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p23"></a>

## JS-P23: Numeric/text literals allocate a fresh 9-field Value and re-run the surrogate regex on every evaluation

- [x] **P-JS-P23 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p23-literals.mjs

Source: [JS-P23](../../js-code-review.md). Report labels: [low] [measured].

**Source target:** `eval.mjs:223-228` (`case 'num'`, `case 'text'`) to `Value.text` to `checkText`.

**Benchmark seed / reported evidence:** ~75 ns per `Value.text('50')+_decimal` evaluation (5M iterations, `lit.mjs`). In the 200k-row FILTER profile the regex shows 1.9% and GC 20%.

**Implementation experiment:** a trusted constructor that skips `checkText` (the parser validated the literal), create literal Values once (assignment clones, so literals are not mutated), and/or a fast path in `evalBinary` comparisons that reads `node.r.dec` directly for a numeric literal operand. Byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p24"></a>

## JS-P24: Double validation/allocation on every arithmetic result

- [x] **P-JS-P24 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p24-num-result.mjs

Source: [JS-P24](../../js-code-review.md). Report labels: [low] [measured].

**Source target:** `value.mjs:47-54` via `Value.num` called from `eval.mjs:218` and `evalBinary`; `decimal.mjs` `add` etc. already call `guard`.

**Benchmark seed / reported evidence:** `D.add` 68 ns; `Value.num(D.add(...))` 180 ns; a raw `new Value(TEXT,null)` with `_decimal` set 52 ns. ~60 ns per result goes to `checkDecimal` (typeof checks, `guard` a second time). ~22% of the numeric benchmark is GC (guessed, not measured, that trimming Value's fields helps).

**Implementation experiment:** internal `Value.numOwned(decimal)` for the evaluator/plan/number builtins; keep the checked `Value.num` for host input (which fixes JS-C20/JS-C33 in one place).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p25"></a>

## JS-P25: `textLiteral` is 20-25x slower than a split/join and allocates per character

- [x] **P-JS-P25 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p25-text-literal.mjs

Source: [JS-P25](../../js-code-review.md). Report labels: [low] [measured].

**Source target:** `emit.mjs:132-159`.

**Benchmark seed / reported evidence:** 2.8 MB of text: mariadb 1206 ms, postgresql 728 ms; `split("'").join("''")` takes 50 ms. Only inline mode with large literals matters (params mode binds text).

**Implementation experiment:** precompute (per dialect, cached) a sorted key list and use one `String.replace` with an alternation regex built from escaped keys and a lookup object (single pass, longest-first).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p26"></a>

## JS-P26: Dialect chain is rebuilt on every lexical/entry lookup

- [x] **P-JS-P26 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p26-dialect-chain.mjs

Source: [JS-P26](../../js-code-review.md). Report labels: [low] [measured].

**Source target:** `map.mjs:291-299` (`chain`), `:309-320` (`lexical`), `:326-344` (`entry`).

**Benchmark seed / reported evidence:** 1e6 `lexical()` calls 1.5 s, `entry()` 1.0 s, `Emit.ident` 2.0 s; ~10-12% of self time in a 30-clause translate.

**Implementation experiment:** cache the chain array per dialect, invalidated in `defineDialect`/reset.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p27"></a>

## JS-P27: `fromCodePoints` slices even short arrays

- [x] **P-JS-P27 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/js.md, tools/perf/js/p27-from-code-points.mjs

Source: [JS-P27](../../js-code-review.md). Report labels: [low] [measured].

**Source target:** `utf8.mjs:33-40`.

**Benchmark seed / reported evidence:** 10-element array 246 ms versus 192 ms per 1e6 calls; single code point 101 versus 55 ms, with `String.fromCodePoint.apply(null, cps)` directly for `length <= 4096`.

**Implementation experiment:** fast path for short arrays. Superseded by JS-P5 where that lands.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="js-p28"></a>

## JS-P28: Smaller items

- [x] **P-JS-P28 — Measure and address this finding.**

  Closed 2026-09-30 — implemented (partly) (remaining sub-items dispositioned in the results row); evidence: performance/results/js.md, tools/perf/js/p28-hash-micro.mjs

Source: [JS-P28](../../js-code-review.md). Report labels: [low] [reasoned].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Implementation experiment:** - `structuralHash` (`value.mjs:679-683`) allocates `String(i + 1)` and hashes it per list element per call (needed so a packed list hashes like its keyed Map twin); a precomputed table of key hashes for 1..N would remove it. `for (const byte of value.scalar)` on a Uint8Array uses the iterator protocol; an indexed loop is faster. Low impact (DEDUPE/BUCKET on lists of lists only). (js-2) - `pow10` cache (`decimal.mjs:38-53`) is cleared wholesale when total exponent weight passes ~1M digits, so operands with scales 600k and 500k alternately recompute a 10^k each op; contrived. (js-2) - `D.cmp`/`aligned` multiply to align scales on every comparison of differing scales (32 ns same-scale versus 40 ns different); matters only at huge scales (1M-digit cmp 0.6 ms). (js-2) - `elements(value)` in TAKE/DROP/SELECT_COLS/DEDUPE/BUCKET/JOIN/`doSort` allocates a `[key, value]` pair per element even for flat lists (the relational lane has `forEachCollectionItem`); `String(i + 1)` keys are only needed for `_K`; `makeJoinedRow` allocates two Sets and an entries array per pair; `doBucket` bare form clones every member (spec-mandated; 2540 ms versus 615 ms for the projection form on 200,000 nested records). (js-5) - `TO_UTF8`: `Value.bin(a.bytes(0))` copies an array `encodeUtf8` just created (`binOwned` would do for TEXT); pad returns `fromCodePoints(c)` for the no-op case instead of the original string; pad builds `padding` element by element then `concat`s instead of `repeat` + slice; PADL of 1M is 181 ms today; worth doing only with JS-P5. (js-4)

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.
