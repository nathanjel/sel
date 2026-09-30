# LISP performance

[Worklist](../README.md) · [Performance protocol](../07-performance.md)

Report measurements below are historical evidence, not verified targets for this machine. Recreate each workload in the repository; scratchpad paths mentioned by reviewers are not dependencies. The shared performance protocol applies to every task, including reasoned/guessed opportunities and subitems bundled in a finding.

<a id="lisp-p1"></a>

## LISP-P1: `emit-parts` calls `(length acc)` per interpolation: quadratic in the number of interpolations

- [x] **P-LISP-P1 — Measure and address this finding.**

  Closed 2026-09-30 — already-addressed (T01 iterative/linear lexer rewrite); evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P1](../../lisp-code-review.md). Report labels: [impact: high] [measured].

**Source target:** `lexer.lisp:292` and :295 (`(mark (length acc))` and `(= (length acc) (1+ mark))`); `acc` is the whole reversed token list so far.

**Benchmark seed / reported evidence:** one literal `"{1}{1}...{1}"`: N=4,000: 0.20 s; 16,000: 3.6 s; 64,000 (192 KB): 71 s. Other hosts compile the 16,000 case in under 1 s (whole process). Replacing both `length` calls with an identity check (`(eq acc mark)` where `mark` is the cons just after pushing the opening paren, i.e. "no token was added by `lex-range`") gives 4,000: 0.003 s; 16,000: 0.012 s; 64,000: 0.106 s. Semantics identical (`{ }` and comment-only bodies still raise E_SYNTAX at the same position); the reviewer ran the patched function in-session only.

**Implementation experiment:** the empty-body test becomes `(eq acc mark-after-open-paren)`. DoS-class quadratic on tens of KB of input; the single best fix in slice 1.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p2"></a>

## LISP-P2: Legal 1,000,000-digit numbers cost 5 to 150 s per operation (schoolbook conversion and multiplication plus redundant work)

- [x] **P-LISP-P2 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P2](../../lisp-code-review.md). Report labels: [impact: high] [measured].

**Source target:** `decimal.lisp:60-66` (`num-digits`), :82-93 (`dec-guard`), :117-124 (`parse-bignum-string`), :211-232 (`dec-trim-scale`), :303-307 (`%dec-mul-general`), :41-58 (`pow10`); `parser.lisp:394-398`; `optimizer.lisp:14-26,66,108,130`.

**Benchmark seed / reported evidence:** measured, uncontended unless noted): dec-parse 999,999 digits 6.5 s; dec-format 7.3 s; `+1` 22.3 s; `==` 39.4 s; CANON 49.0 s; `POWER(9999999999, 99999)` 6.9 s vs JS 0.77 s / PHP 0.53 s / Py 1.05 s (C++ 5.7 s); `POWER(3.14159, 100000)` 1.7 s vs 0.3 to 0.5 s. In a scratchpad copy with `dec-val` copied in `copy-node-shallow` (plus C1 fixed), fold/plan using `node-dec-val`, `parse-primary` keeping the token when canonical, `dec-trim-scale` using `parse-bignum-string`, pre-multiply digit-count bound and bit-length guard: `+1` 22.3 -> 7.6 s, `==` 39.4 -> 5.6 s, CANON 49 -> 8.1 s, `*` E_RANGE 37 -> 5.9 s. Adding a 12-line Karatsuba (`kmul`, threshold 6000 bits) for parse, mul and `pow10`: `==` 1.2 s, `*` E_RANGE 1.3 s, `POWER(9999999999,99999)` 5.3 s (now bounded by `dec-format` printing), `+1` 4.5 s. All 1091 conformance cases and the 6,000-case fuzz stayed green.

**Implementation experiment:** (a) replace `parse-integer` in `dec-trim-scale`; (b) bound-check before multiplying and decide `dec-guard` from `integer-length` (lo = 1+floor((bits-1)*30102/100000), hi = 1+floor(bits*30103/100000)); (c) stop re-parsing/re-formatting literals (P5); (d) build 10^k via Karatsuba or 5^k<<k and short-circuit TRUNC/FLOOR/CEIL/ROUND-down/`dec-integerp` when `integer-length(digits)` proves `digits < 10^scale`; (e) optional Karatsuba for parse/mul and a D&C formatter. All keep results byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p3"></a>

## LISP-P3: `eval-binary` dispatches on the operator with chains of `string=`

- [x] **P-LISP-P3 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P3](../../lisp-code-review.md). Report labels: [impact: high] [measured].

**Source target:** `eval.lisp:365-430` (`eval-binary`), :327-334 (`compare-result`), :420-423 (`(subseq op 1)` allocation for `$` operators).

**Benchmark seed / reported evidence:** sb-sprof flat profile of `X .> FILTER(_["a"] > 10 AND _["b"] < 5 AND _["a"] != 77) .> COUNT` over 200k rows: `STRING=*` 34.4% self, `EVAL-BINARY` 11.7%, `%MEMBER-TEST` 1.5%, `COMPARE-RESULT` 1.4%. A prototype (`binary-op-code` maps the operator string to a keyword by length/char, then `case`) is byte-identical on 23k+ random programs and took the same three filters from 330/681/519 ms to 170/385/326 ms (min of 5 reps, repeated twice): 1.5 to 1.9x on its own.

**Implementation experiment:** intern the operator once per node (an `op` keyword/fixnum slot on `node`, filled in `bin-node`, or computed by char-dispatch) and `case` on it in `eval-binary`, `compare-result` and `eval-assign`; drop the `(subseq op 1)` allocation.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p4"></a>

## LISP-P4: SORT/SORT_BY/TOP on text keys re-encode UTF-8 and re-parse a decimal on every comparison

- [x] **P-LISP-P4 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P4](../../lisp-code-review.md). Report labels: [impact: high for text sorts] [measured].

**Source target:** `aggregate.lisp:243-274` (`compare-values`), called from comparator lambdas at :415, :474-478, :530.

**Benchmark seed / reported evidence:** 50k records, `SORT_BY(R, _["name"])`: 1.2 to 1.8 s and 180 MB consed, versus 0.2 s for numeric keys and 0.04 s for integer keys. Profile: ENCODE-UTF8 42%, DEC-PARSE 16%. A prototype decorating each key once (dec or NIL, bytes or NIL, kind) and comparing with the same rules: 0.35 s including decoration, about 5x faster, identical order. TOP pays the same (`TOP 3 text` 50k: 36 ms, 13.7 MB for a 3-row result).

**Implementation experiment:** extend `sort-item` with precomputed `dec`, `bytes`, `null-p`, `kind` filled once per item; a comparator mirroring `compare-values` on those fields (keep `compare-values` as the reference for the mixed-kind fallback and for whatever C17 decides).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p5"></a>

## LISP-P5: `copy-node-shallow` drops `node-dec-val`, so every optimised numeric literal loses its parsed decimal

- [x] **P-LISP-P5 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P5](../../lisp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `optimizer.lisp:14-26` (copies every slot except `dec-val` and `argument-plan`; the struct comment at `parser.lisp:25` acknowledges only the latter). Folded `:num` nodes at :66/:119 carry no `dec-val`. Consumers: :66/108-109/130-131 (`dec-parse (node-s ...)`), `math-plan.lisp:82-83`, `eval.lisp:262`.

**Benchmark seed / reported evidence:** `A > 1`, 300k runs, min of 7: optimised 278 to 341 ms vs plain-tree 239 to 283 ms (optimised is slower; three separate processes); with `dec-val` copied and folded literals given theirs: 257 to 282 ms vs 254 to 280 ms. `A == 5 AND B != 3`: 560 to 692 ms optimised vs 418 to 546 plain, patched 426 to 503 vs 422 to 498. Combined with P3 the 200k-row FILTER benchmark goes 681 -> about 290 ms. s2: measured with the P2 patch, which needs C1 fixed first, otherwise `hash.bucket.numeric-cache` and `rel.bucket.map-spelling-equals-projected-spelling` fail because `eval-key-hash` reads dec-val presence as "numeric".

**Implementation experiment:** add `(node-dec-val copy) (node-dec-val n)` to `copy-node-shallow`, use `node-dec-val` in fold, and set it on folded results (`dres` is already a DEC). Order matters: fix C1 first.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p6"></a>

## LISP-P6: `dec-parse` and `dec-format` are slow for ordinary short numbers

- [x] **P-LISP-P6 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P6](../../lisp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `decimal.lisp:105-163` (`dec-number-string-p` + `dec-parse`), :165-180 (`dec-format`).

**Benchmark seed / reported evidence:** 2M iterations): `dec-parse "12345.67"` 472 ns, `"12345"` 162 ns; `dec-format` of 12345.67 286 ns. A single-pass fixnum-accumulating parser for at most 19 chars (falls back otherwise; scratchpad `p6.lisp`) takes 64.5 ns / 48.5 ns (7.3x / 3.3x) and agrees on 18 edge strings; a hand-written digit-buffer formatter for `digits < 2^62`, `scale < 20` takes 65 ns (4.4x) and also removes the `*print-radix*` dependency of C22.

**Implementation experiment:** as above; every text->number and number->text conversion in a rule pays this.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p7"></a>

## LISP-P7: DEDUPE/DISTINCT on BIN values is quadratic (`value-hash` hashes a BIN only by its length)

- [x] **P-LISP-P7 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P7](../../lisp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `value.lisp:554-557` (`(:bin ... (sxhash (length b)))`); use `builtins/structure.lisp:159` (`do-dedupe`).

**Benchmark seed / reported evidence:** `COUNT(DEDUPE(B))` over N distinct 4-byte BINs: N=2,000 0.41 s, 4,000 1.33 s, 8,000 5.98 s (x4 per doubling), vs 1 ms for 8,000 distinct texts.

**Implementation experiment:** fold the octets into the hash (FNV over the vector, or `sxhash` of a length-prefixed form). Results unaffected; only bucket placement changes.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p8"></a>

## LISP-P8: Rows without a record shape take the slow `make-joined-row` path in LINK (about 6x slower)

- [x] **P-LISP-P8 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P8](../../lisp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `structure.lisp:403-446` (`make-joined-row`), selected at :617-619 when a row has no shape/storage.

**Benchmark seed / reported evidence:** 50k x 25k equi-join on 4-field rows: 0.93 s / 143 MB unshaped vs 0.18 s / 26 MB shaped (profile: MAKE-JOINED-ROW 72%, STRING=* 15%).

**Implementation experiment:** shape unshaped rows once before the join via `get-record-shape`, or cache per key-list plans for children-based rows as the plans do for shapes.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p9"></a>

## LISP-P9: Nested-loop LINK: per-pair row aliasing, and no hash join for AND-conjunctions or predicates mentioning an outer variable

- [x] **P-LISP-P9 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P9](../../lisp-code-review.md). Report labels: [impact: medium for non-trivial predicates] [measured + reasoned].

**Source target:** `structure.lisp:1336-1349` (fallback: `(ensure-row-table-alias item2 b2)` inside the inner loop) and :197-217 / :172-195 (only a bare `==`/`$==` between a b1-only and a b2-only expression qualifies; a free variable such as `OFFSET` counts as "depends on something else").

**Benchmark seed / reported evidence:** (a) the aliased right row is rebuilt for every (left,right) pair though it depends only on the right row: n*m allocations. Profile of a 700x700 non-equi LINK: ENSURE-ROW-TABLE-ALIAS 13%, %MAKE-VALUE-RAW 9.5%. (b) `A["k"] == B["k"] AND A["d"] < B["d"]` is O(n*m) with the full evaluator per pair: 1000x1000 = 4.4 s, 450 MB; the same-size equi-join takes ms. Extracting an equi conjunct with a residual predicate must keep error ordering, so this is a design task.

**Implementation experiment:** (a) precompute an `aliased-val2` vector once (safe, same for every left row); (b) optional: accept AND-chains whose first conjunct is an equi key, evaluating the rest per candidate pair in order.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p10"></a>

## LISP-P10: `execute-hybrid` deep-copies the entire caller context on every call

- [x] **P-LISP-P10 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P10](../../lisp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `hybrid.lisp:743-746` (`sel:value-copy` of the caller's context).

**Benchmark seed / reported evidence:** with a 300,000-element list in the context, 20 executions of a trivial hybrid plan take 2.53 s (about 127 ms each) versus about 0 with an empty context.

**Implementation experiment:** skip the copy when the continuation contains no `:assign` node, or copy only the top level and copy-on-assign the touched key. Behaviour identical because nothing can write through an alias without an assignment.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p11"></a>

## LISP-P11: SQL: `emit-fill` splices whole part lists at every nesting level; left-folded n-ary translations are super-linear

- [x] **P-LISP-P11 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P11](../../lisp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `translator.lisp:1009-1019` (`fold-pairwise` -> `apply-entry` -> `emit-fill`), `emit.lisp:220-317` (`splice`, `push-str`; :238-249).

**Benchmark seed / reported evidence:** `X IN L` with a value-binding of n elements: n=1,600 115 ms, 3,200 478 ms, 6,400 1.46 s (4x per doubling); `ANY(L, _ > 5)` n=1,600 149 ms, 3,200 585 ms; sb-sprof of the n=3,200 ANY: `splice` 55% total, `push-str` 25%, `list-nreverse` 12%. `ANY` nested 4 deep x 12 elements (3.6 MB output) took 46 s. s7: IN list of n text literals: n=250 0.010 s, 1000 0.081 s, 4000 0.66 s (6000 about 1 s; profile of the 6000 case: SPLICE 34% self, PUSH-STR 17%, MAKE-LITERAL 12%, LIST-NREVERSE 11%); COALESCE of n literals: 4000 -> 0.15 s. Replacing `(nth i args)` in `join-from` with a list walk did NOT help (s7), so the cost is the part-list copying, not `nth`.

**Implementation experiment:** build the disjunction as one n-ary template fill (`{*}` join with " OR ") or a balanced tree, or make fragments a rope/vector that `emit-fill` nconc-appends. Output must stay byte-identical (left-associative shape is pinned by cases; parenthesisation is the risk).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p12"></a>

## LISP-P12: SQL: `make-literal` and fragment rendering are O(P^2) in the number of parameters

- [x] **P-LISP-P12 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P12](../../lisp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `translator.lisp:80-86` (slot id from `(length (translator-params tr))`, 23% self time in the n=3,200 ANY profile); `fragment.lisp:49-64, 66-94, 133-144` (`frag-join`, `bindings`, `slot-inline-p` use `nth` per slot).

**Benchmark seed / reported evidence:** 12,800 params: `as-value :params` 0.5 s, `bindings` 0.38 s, `:inline` 0.42 s (1,600 params: 5 to 12 ms).

**Implementation experiment:** keep a counter in the translator struct (the list is already reversed for O(1) push); convert `params`/`param-kinds` to vectors once per render.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p13"></a>

## LISP-P13: Nested interpolation re-scans the remaining literal at every level and allocates positions eagerly

- [x] **P-LISP-P13 — Measure and address this finding.**

  Closed 2026-09-30 — already-addressed (T01 iterative/linear lexer rewrite); evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P13](../../lisp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `lexer.lisp:188` (`match-brace` pre-scan per `{`), :233-264 (`match-brace` / `skip-quoted` each call `lexer-pos-at` and allocate a `pos` even when no error results).

**Benchmark seed / reported evidence:** O(n * depth): 1000 levels 0.12 s, 4000 levels 2.3 s. The eager-`pos` share is guessed small and was not separately measured.

**Implementation experiment:** single-pass lexing (C4 (a)); compute `pos` lazily, only on the failure path.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p14"></a>

## LISP-P14: RECORD key duplicate check is quadratic (`remove-duplicates :test #'string=` on a list)

- [x] **P-LISP-P14 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P14](../../lisp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `parser.lisp:157` (`prepare-record-shape`), `value.lisp:673` (`from-native`), `structure.lisp:42` (first evaluation of RECORD).

**Benchmark seed / reported evidence:** compiling `RECORD("k0",0,"k1",1,...)`: 1,000 keys 0.03 s; 4,000 0.55 s; 8,000 1.6 s; 16,000 7.7 s. Isolated `remove-duplicates` over 16,000 strings 6.7 s; a hash-set check 0.002 s; `get-record-shape` itself 0.003 s. Thousands of literal keys are unusual, but generated rule text is not.

**Implementation experiment:** `get-record-shape` already builds an `equal` hash table of key->index; detect duplicates in that pass, or use a local `equal` hash table in `prepare-record-shape`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p15"></a>

## LISP-P15: SQL: dialect lookups are unindexed and re-derive the inheritance chain every call

- [x] **P-LISP-P15 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P15](../../lisp-code-review.md). Report labels: [impact: low-medium] [measured].

**Source target:** `map.lisp:58-60` (assoc over the dialect table), :89-101 (`dialect-chain` allocates and does `member` string compares each call), :108-119 (`dialect-lexical`), :183-205 (`dialect-entry`); used by `apply-entry`, `emit-fill`, `emit-text-literal`, `lex-text`.

**Benchmark seed / reported evidence:** a typical 20-node rule translates in 0.22 ms; sb-sprof shows `string=*`/`%assoc-test`/`%member-test`/`dialect-chain` about 45% and `push-str`/`concatenate` (emit-fill builds every template char by char via `(concatenate 'string ...)`) about 18%. 1M lookups: hit near the front of funcs 0.63 s; a MISS (unsupported function, walked by `contains-unsupported-sql-p` for every call node of every custom pair) 5.9 s (about 6 us each); lexical lookup 0.75 to 1.3 s per million. Negligible per translation, noticeable only in fall-through planning over big RECORDs.

**Implementation experiment:** cache a flattened per-(dialect, section) hash table (invalidate in `define-dialect`/`define-entry`/`map-reset`, with a generation counter), and pre-parse each template into a part vector once.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p16"></a>

## LISP-P16: SQL stage 1 is O(n^2) in the number of statements

- [x] **P-LISP-P16 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P16](../../lisp-code-review.md). Report labels: [impact: low-medium] [measured].

**Source target:** `stage1.lisp:297-364` (`defs` is an alist: `assoc` + `(append defs (list ...))` per statement, `assoc` per variable read in `substitute-node`), plus `is-constant` re-walking the growing value.

**Benchmark seed / reported evidence:** chain `A0 = X; A1 = A0 + 1; ...; An > 0`: n=4,000 1.4 s, 8,000 5.3 s, 16,000 about 21 s (all refused at the end with E_SQL_DEPTH). A 350 KB script costs 20 s before any refusal.

**Implementation experiment:** hash table for `defs`, and stop early on the E_SQL_DEPTH condition (already deeper than 200 by statement 200).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p17"></a>

## LISP-P17: Every regex pattern compiles two scanners, only RREPLACE uses the second

- [x] **P-LISP-P17 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P17](../../lisp-code-review.md). Report labels: [impact: low-medium] [measured].

**Source target:** `regex.lisp:288-303` (`(cons head (build nil))`), :318-320 (RMATCH/RFIND/RGROUPS discard the tail scanner).

**Benchmark seed / reported evidence:** 5,000 unique patterns: `compile-regex` 128 ms vs 58 ms for one scanner (about 25 us vs 12 us); about 1 KB retained per cache entry. Irrelevant for fixed literal patterns; matters for patterns built from data.

**Implementation experiment:** build the tail scanner lazily on the first RREPLACE continuation; keep the validation on the head build. (Cache boundedness is a separate matter, see C12.)

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p18"></a>

## LISP-P18: `bytes-to-hex` formats each byte with `format`

- [x] **P-LISP-P18 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P18](../../lisp-code-review.md). Report labels: [impact: medium (s4) / low (s1)] [measured].

**Source target:** `utf8.lisp:86-89` (used by TO_HEX, `binary.lisp:19-20`).

**Benchmark seed / reported evidence:** 0.48 s per MB (s1); 2 M bytes: 428 ms vs 28 ms with a 16-char lookup table filling a preallocated string (s4, 15x). encode-utf8 of the same 2 M ASCII chars: 27 ms, decode 56 ms, CRC32 32 ms, so hex is the outlier in the binary group. `format ~2,'0x` per byte through a string stream, then `string-downcase` over the whole result.

**Implementation experiment:** fill a preallocated `(make-string (* 2 n))` from `"0123456789abcdef"`; output is already lower-case so `string-downcase` (the wrong tool anyway) goes away.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p19"></a>

## LISP-P19: Join-key canonicalisation does redundant work

- [x] **P-LISP-P19 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P19](../../lisp-code-review.md). Report labels: [impact: low-medium] [measured for the zero-stripping; reasoned for the rest].

**Source target:** `structure.lisp:222-240` (`canonical-numeric-string`), :252-271 (`extract-join-key`, calls it twice at :257), :1310 (`(reverse matches)` per left row), :1229 (`format nil "~d"`).

**Benchmark seed / reported evidence:** (1) a non-integer numeric text key is parsed twice; (2) trailing-zero stripping is `(floor digits 10)` in a loop, O(z) bignum divisions, quadratic in the number of trailing fraction zeros: isolated, 20,000 zeros 0.8 s, 50,000 4.7 s, 100,000 20 s; a two-row equi LINK on keys with 100,000 fraction zeros took 75 s in Lisp (36 s JS, 0.6 s PHP), about 27% in this loop and the rest in generic decimal parse/format (see P2); (3) each left row does `(reverse matches)` though buckets could be reversed once after the build; (4) `emit` formats the position with `format` per joined row when `numbered`.

**Implementation experiment:** bind the canonical string once; strip trailing zeros by string (or divide by 10^k in chunks); `nreverse` buckets after the build phase.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p20"></a>

## LISP-P20: Per-element `(format nil "~d" i)` for list keys

- [x] **P-LISP-P20 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P20](../../lisp-code-review.md). Report labels: [impact: low] [measured].

**Source target:** `value.lisp:110, :299-300, :572-577` (`value-hash`), :718; `eval.lisp:310-311` (`eval-list`); aggregate walk for rows beyond 10,000.

**Benchmark seed / reported evidence:** `(format nil "~d" 12345)` 133 ns; `value-hash` of a 1000-int list 154 us, `value-keys` 106 us, `value-eql` 65 us, `value-copy` 56 us per 1000 elements. `aggregate.lisp` keeps `*index-string-cache*` (1..10000) and `format-index-string`, but `eval.lisp` loads first and does not use it. A prototype using the cache: a 6-item comma list inside a 100k-row MAP 444 -> 399 ms (about 10%, noisy). DEDUPE of 50k two-element lists takes 0.1 s, so it is not a bottleneck today. Profile with 200k rows: `%OUTPUT-INTEGER-IN-BASE` 4.8% + `GET-OUTPUT-STREAM-STRING` 1.2%.

**Implementation experiment:** move the cache into `value.lisp` (or `eval.lisp`) so both use it; for `value-hash` hash the integer directly (the mix must match the children-based branch, which hashes the key string, since equal lists in the two representations must hash equal).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p21"></a>

## LISP-P21: REPEAT / PADL / PADR build strings one write at a time

- [x] **P-LISP-P21 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P21](../../lisp-code-review.md). Report labels: [impact: low] [measured].

**Source target:** `text.lisp:135-136, 147-149`.

**Benchmark seed / reported evidence:** `with-output-to-string` + `dotimes` writes with a final copy out of the stream, so a large result needs about 2x memory (drives the C20 heap exhaustion). REPEAT 5 M chars 287 ms; 50 M chars about 5 s (js 3 s, cpp 0.8 s); PADL 5 M 179 ms.

**Implementation experiment:** `(make-string (* n len))` then `replace` per copy (or doubling `replace`); PAD fills directly into a preallocated result.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p22"></a>

## LISP-P22: `match-operator` scans all 33 operator strings with `string=` for every operator token

- [x] **P-LISP-P22 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P22](../../lisp-code-review.md). Report labels: [impact: low] [measured].

**Source target:** `lexer.lisp:140-145`.

**Benchmark seed / reported evidence:** tokenizing a 204 KB / 66k-token program takes 0.074 s. A first-character guard before the `string=` loop cut tokenize from 0.219 s to 0.119 s (-45%, 5 passes). Tiny rules (34 chars, `compile-source` about 13 us) spend about 7 us in the lexer.

**Implementation experiment:** `case` on the first character to pick candidates. Longest-match order must stay (`???` before `??`, `$<=` before `$<`).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p23"></a>

## LISP-P23: `evaluate` (compile + run once) pays the full optimizer for nothing

- [x] **P-LISP-P23 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P23](../../lisp-code-review.md). Report labels: [impact: low] [measured].

**Source target:** `sel.lisp:48-49` and :26-32 (physical tree built on first `run`).

**Benchmark seed / reported evidence:** `IF(A > 1 AND B < 5, A * 2 + B, ROUND(A / 3, 2))`: parse 29.9 us, optimize 7.5 us, run 2.3 us (plain tree also 2.3 us); `evaluate` 35.4 us vs parse + plain-run 25.9 us (+37%). The struct comment already says so.

**Implementation experiment:** have `evaluate` run the un-optimised AST (same results modulo C9/C13 to C15), or optimise on the second run of a program.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p24"></a>

## LISP-P24: SQL: `emit-text-literal` pays per-call sorting and per-character rule search

- [x] **P-LISP-P24 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P24](../../lisp-code-review.md). Report labels: [impact: low] [measured].

**Source target:** `emit.lisp:85-108`.

**Benchmark seed / reported evidence:** 100k literals of 44 chars: 1.1 to 1.2 s (about 11 us each; a 1 MB literal 0.24 s, so per-call overhead plus about 250 ns/char). Each call does two dialect-lexical chain walks, `copy-list` + `stable-sort` of the rules, and a `find-if` with a fresh closure per character.

**Implementation experiment:** cache the sorted rule list per (dialect, overlay generation); for shipped dialects scan with a small char->replacement table (rules are single-character).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p25"></a>

## LISP-P25: Hybrid helper-assignment bookkeeping is super-quadratic in the number of non-literal helpers

- [x] **P-LISP-P25 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P25](../../lisp-code-review.md). Report labels: [impact: low] [measured].

**Source target:** `hybrid.lisp:200-236` (`read-names` recomputed on every pass of `referenced-assignments`; `wrap` called once per candidate split k).

**Benchmark seed / reported evidence:** chain of n non-literal helpers `H_i = H_{i-1} + COUNT(ORDERS)` before a 3-step pipeline: n=25 9 ms, 50 42 ms, 100 261 ms, 200 799 ms. Literal helpers cheap (n=100: 9 ms).

**Implementation experiment:** compute `read-names` once per statement and close the dependency set in a single reverse pass.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p26"></a>

## LISP-P26: Shape-cache key hashing: `sxhash` of a list of strings looks at only the first few elements

- [x] **P-LISP-P26 — Measure and address this finding.**

  Closed 2026-09-30 — rejected (shape-cache key hashing: <1 us per lookup at the 256-entry cap); evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P26](../../lisp-code-review.md). Report labels: [impact: low] [measured].

**Source target:** `value.lisp:36, :42` (`(gethash keys *shape-cache*)`, `equal` table keyed on a list).

**Benchmark seed / reported evidence:** 200 key lists sharing a 6-key prefix all had the same `sxhash`, so they collide in one bucket and each lookup compares up to 256 lists with `equal` (cache capped at 256 entries / 256 keys). Only matters for many similar-prefix shapes; unmeasured end to end.

**Implementation experiment:** key the cache on a length-prefixed string join of the keys, or a hand-rolled hash of all keys.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="lisp-p27"></a>

## LISP-P27: Smaller items

- [x] **P-LISP-P27 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/lisp.md, tools/perf/lisp/

Source: [LISP-P27](../../lisp-code-review.md). Report labels: [impact: low] [mostly reasoned or guessed].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Implementation experiment:** - `registry-lookup` upcases (allocates) on every call node: `registry.lisp:85-86`, called from `parser.lisp:370, :460` with a token value the lexer already upper-cased (s1-P7, guessed, not measured). Fix: `registry-lookup-canonical`. - `looks-numeric` (`value.lisp:428-437`, ISNUM) parses and discards the decimal; `ISNUM(x) AND x > 5` parses x twice (1327 ns vs 817 ns for `a*b+c/3-2`). Cache into `value-dec-val` after C1 (s2-P6, reasoned). - `value-copy-at` copies BIN octets on every assignment/`,` (`value.lisp:461, :486`); a BIN cannot be mutated in place, so this is O(len) per copy for nothing (s2-P6, reasoned). - `dec-div` (`decimal.lisp:373`) always builds a bignum numerator: 88 to 110 ns for ordinary operands; a fixnum fast path would roughly halve it. `dec-add/mul/cmp` are 24 to 62 ns already; declaring `int-val` as `(signed-byte 62)` would give < 2x (s2-P6, reasoned). - `make-args` allocates a 7-slot struct and a cache vector per strict call (`eval.lisp:61-72`); `eval-dispatch :num` and `:text` allocate a fresh value per evaluation (:262-263). Sharing an immutable literal value was not proven safe (values are mutable handles). Prior lisp-runtime work already covered `args` (s3-P5, reasoned). - `ascii-case` (`text.lisp:113-120`) uses `map 'string` with a closure (UPPER of 5 M chars 331 ms) and allocates even when nothing changes; `trim-text` uses `member` on a list per char; `FIND/REPLACE/SPLIT` use generic `search` (5000-vs-20000-char worst case 1.1 s, O(nm)); `BTL`/`LTB` allocate one value per byte (1 M bytes 444 / 617 ms) (s4-P5, measured, not hot enough to matter). Cached RMATCH call overhead is about 1 us (200,000 calls 194 ms) and fine. - cl-ppcre backtracking is about 5x slower than V8 on quadratic patterns: `RMATCH('a*[b]', REPEAT('a', 40000))` lisp 11.3 s, js 2.2 s, cpp 4 ms (SRELL prefilters); n=20000: 2.9 s / 0.6 s / 4 ms. Not cheaply fixable; a first-set/required-class precheck for patterns starting with `x*` would help (cl-ppcre only checks required literal strings, which is why `a*b` is fast and `a*[b]` is not) (s4-P4). - SQL: `translate-in` branch D and `translate-conditional` use `nth` in loops (`translator.lisp:1246-1258, 2128-2148`); a 3,000-element IN also yields 3,000-deep parenthesised OR nesting that servers with a parser stack limit (MariaDB about thousands) may reject; worth a limit or a flat `OR` chain if bytes may change (s6-P6, reasoned).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.
