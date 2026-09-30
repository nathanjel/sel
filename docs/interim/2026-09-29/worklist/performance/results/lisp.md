# Lisp performance — summary (LISP-P1 … LISP-P27)

| Disposition | Count | Tasks |
|---|---:|---|
| implemented | 24 | P2–P12, P14–P25, P27 (P2 without the printer; P9 part (b); P15 in two steps; P27 in five sub-items, see below) |
| already addressed by an earlier correctness change | 2 | P1, P13 (the T01 iterative lexer) |
| rejected on measurement | 1 | P26 (shape-cache key hashing: 0.8 µs per lookup at the cap, nothing to win) |
| deferred | 0 | — |

Sub-item dispositions that are *not* "implemented": P2's own Newton-reciprocal printer (rejected: 1.25× at 1M digits, slower
below), P9 part (a) precomputed aliased right rows (rejected: §7.3 requires later pairs to see mutations), P27 `looks-numeric`
caching into `value-dec-val` (rejected: no reproducible gain, ISNUM+compare 98 vs 98 ms), P27 `make-args` allocation /
literal-value sharing (deferred: `make-args` is 8.5% of a strict-call-heavy profile, dynamic-extent is unsafe for the lazy builtins
that keep the struct, literal sharing is not proven safe for mutable value handles; reconsider with an escape analysis of the
builtins' use of `args`).

# Lisp performance results — round 1 (LISP-P1 … LISP-P10)

Revision: `63cda0c` + uncommitted working tree. SBCL 2.6.8-1.fc44, 16-thread box **shared with other agents** (load average 5–23
during the runs): every before/after pair below was taken back to back, baseline first (a copy of `lisp/` made before the first
edit, selected with `SEL_LISP_BENCH_ROOT`), and medians of 3–7 repetitions after a warm-up are reported; absolute numbers are
noisy, ratios held across repeats. Harness: `tools/perf/lisp/harness.lisp` (fixed seeds, semantic checksum per point, n / 2n / 4n
growth); workloads `tools/perf/lisp/tasks-r1.lisp` (run: `SEL_LISP_BENCH_ROOT=lisp/ BENCH_TASKS=LISP-P4 sbcl --script
tools/perf/lisp/tasks-r1.lisp`), `p2-million.lisp` (the review's 999,999-digit operations), `link-diff.lisp` (8,000-program
differential of the LINK change against the baseline tree: 0 differences), `bignum-check.lisp` (Karatsuba / 10^k / digit count
against the built-ins). Checksums in every row are identical before and after.

| Task | Decision | Baseline | After | Growth / evidence |
|---|---|---|---|---|
| LISP-P1 `emit-parts` `(length acc)` | **already addressed** (by the T01 lexer rewrite: a running token count replaces `(length acc)`) | review: 4k 0.20 s, 16k 3.6 s, 64k 71 s | 4k 24 ms, 16k ~110 ms, 64k ~0.6 s | x4 n → ~x4 time (linear); `perf-p1-…` unit test keeps it (64,000 interpolations) |
| LISP-P2 legal 1,000,000-digit numbers | **implemented** (Karatsuba `kmul` for parse/`10^k`/product, pre-multiply digit-count bound, cheap `num-digits`, canonical numeral kept as text in the parser and as a cache on long `dec`s, `parse-bignum-string` in `dec-trim-scale`); **rejected**: a divide-and-conquer/Newton printer | 999,999 digits: `S == S` 21.4 s, `CANON` 14.9 s, `S * S` (E_RANGE) 55.6 s, `S + 1` 15.1 s, `POWER(9999999999, 99999)` 7.1 s, `POWER(3.14159, 100000)` 3.7 s | `==` 1.2 s, `CANON` 0.6 s, `*` E_RANGE 1.2 s, `+1` 5.6 s, `POWER(9999999999,99999)` 5.2 s, `POWER(3.14159,100000)` 2.9 s | 100k/200k/400k digits: parse 78/293/1879 ms → 19/57/150 ms; `==` 404/1803/2690 → 43/118/281 ms. What is left in `+1` and the powers is printing the result (SBCL's printer, 4.8 s at 1M digits). My Newton-reciprocal printer was exact (0 differences on 120 numbers of 18k–280k digits) but only 1.25x faster at 1M digits and slower below it (99 vs 63 ms at 100k, 1047 vs 994 at 400k, 3.8 vs 4.8 s at 1M): rejected, reconsider with a subquadratic divide in the runtime. Unit tests: `perf-p2-…` (3) |
| LISP-P3 `eval-binary` `string=` chain | **implemented** (operator interned once per node into `node-opc`, `case` dispatch, no `subseq`) | FILTER 3 conjuncts: 50k rows 151 ms, 100k 295, 200k 582; arithmetic predicate 200k 546 ms | 76 / 152 / 320 ms; 409 ms | 1.3–2.0x, consing −10%; includes the P5/P6 effects it was measured with (the review measured 1.5–1.9x for this alone). `perf-p3-…` covers all 28 operator spellings |
| LISP-P4 text-key sorts | **implemented** (keys decorated once: rank + DEC/octets/BOOL flag; `compare-values` kept as the reference) | `SORT_BY` text 50k: 937 ms, 199.6 MB; `TOP 3` 917 ms | 215 ms, 26.7 MB; 244 ms | 12.5k/25k/50k: 236/401/937 → 43/93/215 ms (4.4x), consing −87%. `perf-p4-…` checks order equality on 400 random mixed-kind pairs |
| LISP-P5 `copy-node-shallow` drops `dec-val` | **implemented** (copied; folded results carry their decimal; `node-dec` helper) | optimised `A > 1`, 100k runs: 100 ms (plain tree 81) — optimised was slower; `A == 5 AND B != 3`: 99 (79) | 34 ms (plain 33); 46 (45) | optimised now equals the plain tree. `perf-p5-…` |
| LISP-P6 `dec-parse`/`dec-format` short numbers | **implemented** (`dec-parse-short`, `dec-format-small`) | `12345.67`: parse 520 ns, format 354 ns; `12345`: 264 / 262 | 99 / 65; 78 / 57 | 5.3x / 5.4x. 2.5M random strings: parse and format identical to the general paths, 0 differences; also removes the `*print-radix*` dependency (`perf-p6-…`) |
| LISP-P7 DEDUPE on BIN quadratic | **implemented** (FNV-1a over the octets in `value-hash`) | 2k/4k/8k distinct 4-byte BINs: 422 / 1391 / 5788 ms | 1 / 1 / 2 ms | linear; `perf-p7-…` |
| LISP-P8 unshaped rows in LINK | **implemented** (a shaped twin of each unshaped row, built once per join; binder keys still hold the original rows) | 25k x 12.5k unshaped equi-join: 129 ms (min 118), 33.5 MB; 6.25k: 46 ms | 73 ms (min 68), 19.5 MB; 15 ms | 2.9x at 6.25k, 1.7x at 25k; `perf-p8-…` compares shaped vs unshaped results |
| LISP-P9 nested-loop LINK | (a) **rejected**, (b) **implemented** | (b) `key == key AND residual`, 250/500/1000 rows per side: 97 / 447 / 1288 ms, 337 MB | 2 / 5 / 17 ms, 5.5 MB | (a) precomputing the aliased right rows would make a predicate that mutates a right row through another variable invisible to later pairs — §7.3 says mutation inside an element is still seen (measured upper bound of the gain: `ensure-row-table-alias` 13% of a 700x700 non-equi LINK). (b) only pairs with equal leading keys run the predicate; used only when the predicate has no assignment, calls only shipped built-ins, and every key of both sides is live and good — otherwise the nested loop runs unchanged (errors where they always were). 8,000 random programs (LINK/LINK_LEFT, bad keys, NULLs, raising residuals): 0 differences vs the baseline tree; `perf-p9-…` |
| LISP-P10 `execute-hybrid` copies the caller's context | **implemented** (a continuation with no assignment and only shipped built-ins runs on the caller's variables through a fresh root; a writer still gets the full copy) | 300k-element context, 20 calls: 4735 ms (75k: 207, 150k: 458) | 8 ms (2–3 ms) | the caller's root never gains the plan's input variable and is never written (`perf-p10-…`, hybrid parity lane 68/68) |

Verification after the changes: `lisp/bin/conformance` 2128/2128, `lisp/bin/sqlt` 1304/1304 (with reuse twin), `lisp/bin/test` 1596/1596 (+ the
`perf-p*` tests; P4/P7 and the Karatsuba/number tests fail on the baseline tree by construction — they use the new functions or the
old hash collision), decimal oracle 94,040 cases 0 mismatches, `tools/check-budgets.sh` (lisp) green, Lisp hybrid parity 68/68,
`tools/fuzz.sh 3000` (lisp vs js) 0 disagreements, `tools/check-cli-source.sh` green.

# Lisp performance results — round 2 (LISP-P11 … LISP-P20)

Revision: `63cda0c` + uncommitted working tree (round 1 changes included). SBCL 2.6.8-1.fc44, 16-thread box shared with other agents
(load average 7–11 during the runs). Baseline = a copy of `lisp/` taken before the first round-2 edit (`SEL_LISP_BENCH_ROOT`), run back to
back with the working tree; the **min** of 3–7 repetitions is quoted (the median moves ~2× with load), ratios held across repeats.
Workloads: `tools/perf/lisp/tasks-r2.lisp` (`SEL_LISP_BENCH_ROOT=… BENCH_TASKS=LISP-P11 sbcl --script tools/perf/lisp/tasks-r2.lisp`,
harness `tools/perf/lisp/harness.lisp`). Every checksum is identical before and after.

| Task | Decision | Baseline (min ms) | After (min ms) | Growth / evidence |
|---|---|---|---|---|
| LISP-P11 n-ary SQL folds | **implemented** (two causes, neither the `splice` the review blamed first: the slot id `(length params)` in `make-literal` was 38% of the profile; then per-node dialect lookups) — see P12 and P15; plus `emit-fill` copies a run of template characters at once instead of one `push-str` per character | `X IN L` (value binding) n=1600/3200/6400: 110 / 320 / 1173; `ANY(L, _ == X)` 6400: 1032; `X IN (n text literals)` n=4000: 392; COALESCE of 4000 literals: 191 | 37 / 73 / 154; ANY 6400: 203; literals 4000: 97; COALESCE 56 | growth per doubling ×3.6 → ×2.1 (linear); consing 82.6 → 60 MB at n=6400 |
| LISP-P12 O(P²) in parameters | **implemented** (`param-count` slot in the translator instead of `(length params)`; `frag-join` and `bindings` read the kind/value lists through one vector per render) | n=12,800 params: translate 1296, `as-value :params` 564, `bindings` 261, `:inline` 300 | translate 270, `:params` 11, `bindings` 1, `:inline` 26 | 4.8×, 51×, 260×, 11.5×; `:params`/`bindings` now flat in n; `perf-p11-p12-…`, `perf-p12-slot-ids-…` |
| LISP-P13 nested interpolation re-scan | **already addressed** (by the T01 iterative lexer with memoised brace matching) | review: 1000 levels 0.12 s, 4000 levels 2.3 s; baseline tree of this round: 1 / 4 / 11 ms (1000/2000/4000) | 1 / 4 / 8 ms | linear; the eager-`pos` share the review guessed is not measurable at this size — no change |
| LISP-P14 RECORD key duplicate check | **implemented** (`keys-distinct-p`: pairwise below 24 keys, `equal` hash table above; used by the parser, `from-native` and the RECORD builtin) | compile n=2000/4000/8000/16000: 50 / 198 / 838 / 3352 | 9 / 17 / 36 / 73 | 46× at 16,000, linear; `perf-p14-…` compares against `remove-duplicates` on random key lists incl. duplicates across the threshold |
| LISP-P15 dialect lookups | **implemented** (per-dialect `view`: chain, entry table per section, lexical table; dropped whole by `map-changed` on every registration and reset; synchronized tables) **plus an unlisted hot spot found by the profile**: `check-binder-position` and `binding-form` filtered the whole builtin-form table with `string=` per call node — now an index keyed on the table (`binding-forms-named`) | typical 20-node rule ×2000, mariadb: 236–253; postgresql 237; profile: `string=*` 19%, `remove-if-not` 21%, `dialect-chain` 16% | 144–152; postgresql ~150; consing 77.8 → 48.5 MB | **1.65×**. Template pre-parsing (the review's second half) **deferred**: after the cache and the index `emit-fill` is ~20% of the remaining time and its semantics (lexical expansion, cycle refusal, slot errors) make a pre-parsed form a larger change for a bounded gain; reconsider if translation of tiny rules becomes a bottleneck |
| LISP-P16 stage-1 statements | **implemented** (`defs` is a struct with an `equal` hash table; alist still accepted by readers) | n independent statements, 2000/4000/8000: 285 / 999 / 3793 (quadratic, consing 34 → 504 MB); chain with a depth refusal 13 / 20 / 34 (already linear: the E_SQL_DEPTH early exit from the correctness wave) | 14 / 29 / 60 (consing 4 → 17 MB) | 63× at 8000, linear |
| LISP-P17 two scanners per pattern | **implemented** (tail scanner built on the first RREPLACE continuation, kept in the cache entry) | 5000 unique patterns through RMATCH: 149 (1000: 27, 2500: 73) | 114 (20 / 55) | −23%; RREPLACE results pinned by `perf-p17-…` (empty matches, `^`) |
| LISP-P18 `bytes-to-hex` | **implemented** (preallocated string filled from a digit table) | 2M bytes: 458; TO_HEX incl. FROM_HEX 570 | 52; 215 | 8.8× (the rest of the TO_HEX run is FROM_HEX and UTF-8 work); `perf-p18-…` against the old `format` spelling |
| LISP-P19 join-key canonicalisation | **implemented** (canonical string bound once; fraction zeros trimmed from the formatted text instead of a bignum division per zero; buckets put in row order once after the build instead of `(reverse matches)` per left row; position keys from the shared index-string table) | two-row equi LINK with z fraction zeros, z=5000/10000/20000: 77 / 295 / 1116 (quadratic) | 0 / 1 / 2 | ~500× at 20,000; `perf-p19-…` checks the new canonical form against the old algorithm on 4000 random numerals and a 100,000-zero key against its short spelling. 8000 left rows × 50 buckets: 17 → 19 ms (no change within noise — the per-row `reverse` is not visible at bucket size 160) |
| LISP-P20 `(format nil "~d" i)` for list keys | **implemented** (the 1..10,000 key-string table moved from `aggregate.lisp` into `value.lisp` and used by `ensure-list-children`, `value-keys`, `value-hash`, `to-native`, the comma flatten and the join position) | `value-hash` of a 1000-int list ×200: 30; MAP with a 6-item comma list per row, 100k rows: 486 | 8; 419 | `value-hash` 3.7× (and 6 MB → 0 consed); the MAP workload −14% (the review measured ~10%); `perf-p20-…` checks the hash of a list equals the hash of its record spelling |

Verification after the changes (final tree): `lisp/bin/conformance` 2128/2128, `lisp/bin/sqlt` 1304/1304 (with reuse twin), `lisp/bin/test`
5403 checks / 0 failures (new `perf-p11-p12-…`, `perf-p12-…`, `perf-p14-…`, `perf-p15-…`, `perf-p17-…`, `perf-p18-…`, `perf-p19-…`,
`perf-p20-…`), decimal oracle 94,040 cases 0 mismatches, `tools/check-sql-budgets.sh` and `tools/check-budgets.sh` (lisp) green,
`tools/check-cli-source.sh` green, `tools/fuzz.sh 3000 4242` (js vs lisp) 0 disagreements.

Not attributable to this round: `tools/fuzz-sql.sh 1500 4243` (js vs lisp) shows one disagreement (program 414, a refused hybrid
statement whose SQL prefix JS now renders with an extra outer `ORDER BY` sort). The **baseline** Lisp tree of this round produces the
identical output to the working tree on that corpus, so the difference comes from the JS side of the tree (under edit by another fork),
not from these changes.

# Lisp performance results — round 3 (LISP-P21 … LISP-P27)

Revision: `63cda0c` + uncommitted working tree (rounds 1–2 included). SBCL 2.6.8-1.fc44, 16-thread box shared with other agents
(load average 7–10 during the final A/B run). Baseline = a copy of `lisp/` taken before the first round-3 edit
(`SEL_LISP_BENCH_ROOT`), run back to back with the working tree; 5 repetitions, **median** quoted (min in
`tmp/r3-final.tsv` style output; ratios held across repeats). Workloads: `tools/perf/lisp/tasks-r3.lisp`
(`SEL_LISP_BENCH_ROOT=… BENCH_TASKS=LISP-P21 sbcl --script tools/perf/lisp/tasks-r3.lisp`). Every checksum is identical before and after.

| Task | Decision | Baseline (median ms; MB consed) | After | Growth / evidence |
|---|---|---|---|---|
| LISP-P21 REPEAT / PADL / PADR | **implemented** (PAD fills one preallocated result, single-character fills with `fill`; REPEAT doubles the filled prefix with `replace` instead of one call per copy; REPEAT was already not stream-built since the T07 cap rewrite) | 5M chars: REPEAT 66, PADL 186 (69 MB), PADR 190 (69 MB) | REPEAT 15, PADL 53 (19 MB), PADR 12 (19 MB) | linear, n/2n/4n = 1.25M/2.5M/5M; 4.4× / 3.5× / 15.8×; memory 3.6× lower; `perf-p21-…` compares both against the slow obvious definition for 4 strings × 7 widths × 4 fills and 13 repeat counts |
| LISP-P22 operator matching | **implemented** (first-character table `+operators-by-first-char+`, candidates in the original longest-first order) | 100 KB op-heavy compile 45; 200 KB 95; tiny rule ×2000 51 | 29; 59; 28 | −36% / −38% / −45%; `perf-p22-…` checks every position of two operator-dense strings against the linear scan; 9,500-program differential against the baseline tree (lexer adversarial + division/modulo grid): 0 differences |
| LISP-P23 `evaluate` | **implemented** (runs the tree as written; the optimiser is held transparent by the plain-vs-optimised probe) | `evaluate` ×5000: 192 (57.7 MB) | 97 (40.0 MB) | 2.0×; includes the lexer gain: compile + run (the same work through `run`) went 192 → 140; `run` of a compiled program unchanged (13) |
| LISP-P24 SQL text literals | **implemented** (per-dialect `escape-plan` cached by the identity of the rule list: rules sorted once, single-character rules as a 128-entry vector + table, runs between special characters copied with one `write-string`) | `emit-text-literal` ×100k, 44 chars: mariadb 685, postgresql 428, sqlite 427; one 1 MB literal 150 | 235 / 213 / 203; 60 | 2.9× / 2.0× / 2.1×; 2.5× on the 1 MB literal; end-to-end render of 12,500 / 25,000 / 50,000 literals 349 / 650 / 1472 → 248 / 470 / 1177 (−29% … −20%: the rest of a translation is `emit-fill`, see P15) |
| LISP-P25 hybrid helper bookkeeping | **implemented** (`referenced-assignments` is a worklist over names — one `read-names` per statement, hash tables; `read-names` dedups with a table) | chain of n non-literal helpers: n=100 36, 150 97, 200 523, 250 1080, 300 1878 | 11, 23, 6, 8, 11 | the super-quadratic bookkeeping is gone (n=300: 170×); the n≤150 time that remains is the inherent quadratic OUTPUT size (helper i inlines helpers 1..i−1); from n=200 the chain is past the evaluation depth and the plan is pure-memory; `perf-p25-…` compares the new closure with the fixpoint loop it replaced on four programs |
| LISP-P26 shape-cache key hashing | **rejected** | 64/128/256 shapes with a 6-key common prefix, 20 passes: 1 / 1 / 3 ms (0.8 µs per lookup) | unchanged | the claimed collision needs more than 256 distinct similar-prefix shapes; the cache is capped at 256 entries and keys, so the worst bucket walk is bounded and costs under 1 µs here. Reconsider if the cap is raised |
| LISP-P27 smaller items | see the sub-items below | | | |

LISP-P27 sub-items (each measured against the baseline tree):

| Sub-item | Decision | Baseline → after |
|---|---|---|
| `registry-lookup` upcases on every call node | **implemented** (`registry-lookup-canonical`, inline `gethash`, for the parser's already-canonical tokens) | compile of 12,000 call nodes 49 → 25 ms (together with the P22 lexer gain: the two were not separated; compile of the same rule with no calls moves only with P22) |
| `looks-numeric` parses and discards | **rejected** (implemented, measured, reverted) | ISNUM(X) AND X > 5 over 100k: 100 → 96 ms, no reproducible gain (saves one 99 ns parse per distinct value, below the loop's noise) |
| `value-copy-at` copies BIN octets | **implemented** (octets shared, as TEXT scalars already are; nothing in a program writes into a built BIN, the boundary still copies in `make-bin` and out in `to-native`) | 500 KB BIN assigned 10×, 20 runs: 49 ms / 105 MB → 7 ms / 9.7 MB (7×, 11× less garbage); `perf-p27-bin-copies-…` checks BAND/BOR/BXOR/BTL leave the source octets unchanged and `to-native` still copies |
| `dec-div` fixnum fast path | **implemented** (word-sized operands use the machine divide; the trailing-zero strip of a word-sized quotient uses word arithmetic) | 2M divisions 500 → 345 ms (250 → 172 ns each), 1.4×; the review's "roughly halve" did not hold: the rest is `dec-make` and the generic multiplies; `perf-p27-division-…` compares 90 operand pairs with rational arithmetic; decimal oracle 94,040 cases 0 mismatches |
| `make-args` allocation per strict call / literal values shared | **deferred** | `make-args` + `%make-args` are 8.5% of a strict-call-heavy profile (500k strict text calls: 222 ms); dynamic-extent allocation is unsafe for lazy builtins that keep the struct, and sharing an immutable literal value is not proven safe for mutable handles. Reconsider after an audit that no builtin retains `args` |

**LISP-P15 revisited (was deferred in round 2: template pre-parsing).** Implemented: `template-segments` parses a mapping template once (literal strings, `(:splice k)`, `(:join k)`, `(:lexical slot)`), cached by the template string's identity in a weak synchronized table; a lexical reference's per-argument expansion (`{key:one}` → value with `{0}` replaced) is cached per (value, argument) and parsed once; recursion inside an expansion never enters the identity cache. `emit-fill` = `fill-segments` over those segments, same order of effects and refusals. `emit-fill` per call, `mariadb`: `({0} = {1})` 1132 → 738 ns, `COALESCE({0}, {1})` 1060 → 556, `CAST({0} AS {textCast:0})` 2868 → 1558, `CASE WHEN {0} IS NULL …` 1758 → 832 (1.5–1.9×). Byte-identical SQL: `lisp/bin/sqlt` 1304/1304 with the reuse twin, 34 Lisp mutations caught / 0 survived / 0 suite errors, Lisp hybrid parity 68/68, SQL API probes agree with JS on all 60, `perf-p15-template-segments-…` exercises `{{`, `}}`, `{*}`, `{k:}`, unterminated `{`, a missing argument, an unknown lexical name, `{textCast:*}`, `{binaryCast:…}` with a BIN operand, each twice (second time from the cache).

Verification after the changes (final tree): `lisp/bin/conformance` 2128/2128, `lisp/bin/sqlt` 1304/1304 (with reuse twin), `lisp/bin/test` 5974 checks / 0 failures (new `perf-p21-…`, `perf-p22-…`, `perf-p23-…`, `perf-p24-…`, `perf-p25-…`, `perf-p27-…` ×2, `perf-p15-template-segments-…`; none was run against the old tree — the pairs above and the differentials are the before/after evidence), decimal oracle 94,040 cases 0 mismatches, `tools/check-budgets.sh`, `tools/check-sql-budgets.sh` and `tools/check-cli-source.sh` (lisp) green, 9,500-program old-vs-new `batch` differential 0 differences.

## LISP-REG-1 — regression check against 0.9.2 (faff480), with recovery

Peer lead: the C++ host is ~+75% on tools/scale-test Scenario 1 since 0.9.2. Suspects from the correctness waves: clone-on-collect (SPEC 3.4), snapshot iteration, evaluate-then-coerce plan loads, caps/budget counters. Method: baseline = `git archive faff480` into a scratch tree (working tree never touched by git), SBCL 2.6.8, same current harness and dataset (`tools/scale-test/sel_benchmarks.lisp`, `dataset-10x.json`) for both, three interleaved baseline/current pairs of 5 samples (+2 warm-ups) per scenario; current = working tree on top of 63cda0c (dirty). Box load 5–10 (other agents), so minimums and medians of alternating runs are quoted.

**Decision: implemented (recovery) — fresh-result adoption; scale scenarios were not regressed.**

Scale scenarios (program_run_ms, min / median over 15 samples, ratio = current / 0.9.2):

| scenario | 0.9.2 min | current min | ratio | 0.9.2 med | current med | ratio |
|---|---:|---:|---:|---:|---:|---:|
| 1 deep 14-stage pipeline | 507 | 488 | 0.96 | 614 | 580 | 0.94 |
| 2 | 11 | 8 | 0.73 | 14 | 8 | 0.57 |
| 3 | 233 | 210 | 0.90 | 245 | 223 | 0.91 |
| 4 | 15 | 7 | 0.47 | 16 | 13 | 0.81 |
| 5 | 218 (45 samples) | 197 | 0.90 | 246 | 227 | 0.92 |
| 6 | 198 | 198 | 1.00 | 227 | 213 | 0.94 |

(Scenario 5 showed a one-off 1.23 median in the 5-sample pass; 45 samples settle it at 0.92.) Parity checks passed on both trees. `tools/benchmark-lisp-traversal.lisp` (args construction, coalesce, assignment, indexed sweep, walk-create; 24 size points): all within ±4% of 0.9.2, several 8–20% faster; identical bytes consed. `tools/lisp-runtime/benchmark.lisp`: alias/args/node samples +2–12% (sub-microsecond samples, bytes identical — noise band), compile-and-run 0.70; `projection-1000` 970 → 1100 µs with +40% bytes: the only real signal, attributed below.

Attribution (micro-benchmarks, 1000-element list of texts, min CPU µs over 9×, before = 0.9.2): the cost is the per-element copy §3.4 requires (MAP/FILTER/SORT/TOP/BUCKET copy what they collect; RECORD/LIST copy their arguments; `=` copies the stored value) — one 104-byte value allocation per element, ~80–100 ns. `MAP(XS, RECORD(...))` copied the RECORD twice (once in RECORD, once in MAP); a pipeline stage copied the elements its producer had just built; `B = MAP(...)` copied the fresh result again.

Recovery (no semantic change): a result built fresh and referenced by nothing else (calls to MAP, FILTER, SORT, SORT_DESC, SORT_BY, TOP, TOP_DESC, TOP_BY, BUCKET, LIST, RECORD — `node-fresh-p` in `eval.lisp`; not TAKE/DROP/DISTINCT/DEDUPE/LINK/IF/variables/index, whose results alias or may return an operand) is adopted by its consumer instead of being copied again: MAP when its body is fresh, FILTER/SORT*/TOP*/BUCKET when their source is fresh, BUCKET's projected result, and `X = <fresh call>` (whose depth is still checked against path+value depth by a new walk-only `value-depth-check`, the copy's walk without the copy). A first version tested freshness per element (a string-list scan per element: +15% slower than the copy it removed); it is now computed once per call.

| micro case | 0.9.2 µs | before recovery µs | after µs | after / 0.9.2 | bytes 0.9.2 → before → after |
|---|---:|---:|---:|---:|---|
| MAP(XS, RECORD(..)) | 882 | 953 | 864 | 0.98 | 824K → 1152K → 832K |
| MAP(XS, LIST(_, _)) | 567 | 637 | 528 | 0.93 | 472K → 800K → 480K |
| XS .> MAP .> FILTER .> SORT_BY | 2447 | 1947 | 1645 | 0.67 | 1.22M → 2.22M → 1.26M |
| XS .> MAP .> TOP_BY | 1253 | 1361 | 1272 | 1.02 | 857K → 1228K → 905K |
| XS .> MAP .> BUCKET | 1306 | 1459 | 1402 | 1.07 | 856K → 1226K → 904K |
| B = FILTER(XS, TRUE) | 378 | 396 | 329 | 0.87 | 369K → 473K → 289K |
| B = MAP(XS, RECORD(..)) | 547 | 626 | 545 | 1.00 | 688K → 920K → 464K |
| FILTER(XS, TRUE) (variable source) | 233 | 298 | 298 | 1.28 | 185K → 289K → 289K |
| SORT(XS) (variable source) | 154 | 248 | 249 | 1.62 | 72K → 208K → 208K |

**Deferred remainder:** aggregates over a *variable* source (FILTER/SORT over `XS`) stay +28% / +62% over 0.9.2 on this micro-benchmark, because the §3.4 copy is required there (the elements are shared with the variable). Making them cheaper needs copy-on-write or immutable leaf values (a value-representation change; `value-set` on a leaf mutates in place today). Reconsideration condition: a measured real workload dominated by FILTER/SORT over a large variable list; the scale scenarios (which are pipelines of fresh stages) are at or below 0.9.2.

Verification: `lisp/bin/conformance` 2128/2128, `lisp/bin/sqlt` 1305/1305, `lisp/bin/test` 5984 checks / 0 failures (new `reg-fresh-results-are-adopted-without-aliasing`, `reg-adopted-assignment-still-counts-its-depth-from-the-target`: writing through an adopted result never reaches the source, TAKE/FILTER-over-variable/SORT/IF results are still copied, and a 150-index path plus a 70-deep fresh value still raises E_DEPTH), `tools/fuzz.sh 3000` on 3 seeds (js vs lisp) and `tools/fuzz-sql.sh 1000 4243`: 0 disagreements, 0 crashes.
