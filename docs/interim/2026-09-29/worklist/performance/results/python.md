# Python performance — summary (all 31 tasks, rounds 1-3)

| Decision | Count | Tasks |
|---|---|---|
| implemented | 25 | P1 P2 P4 P5 P6 P8 P9 P10 · P11 P12 P13 P14 P16 P19 P20 · P21 P22 P23 P24 P25 P26 P28 P29 P30 P31 |
| already addressed by a correctness change | 2 | P3 (PY-C42 balanced fold), P27 (planner is linear since PY-C52 / PY-C1 site f) |
| rejected (measured) | 2 | P7 (sub-plans), P15 (2-arg BUCKET copy is now the contract) |
| deferred (profiled / effect gone) | 2 | P17, P18 — see below |

Parts of an implemented task that were measured and **not** done, with the condition to
reconsider: P24(a) lighter scalar `Value` (saves ~45 ns of ~700 ns per Value, under 1% of an
evaluated element, and every reader of the unset slots would need guarding); P25 the `walk`
per-element hoists and the inlined `keep` (A/B within ±3%, 15 rounds); P28 `chain()` memo and
`_substitute` returning the original node (no measurable share after the template cache);
P29(a) an `eval_bool` fast path (free `Value.bool` bounds the gain at 3-7% on comparison
predicates, 12% on the most boolean-heavy one — not worth a parallel evaluation path that
must repeat error-order-sensitive code), P29(b) re-walking `_resolve_target` from the root
(required by SPEC 5.7's "re-derived after the right-hand side ran"; bounded by the cap of 200).

Deferred: **P17** interpreter loop/closures — `eval_node` + `_dispatch` self time is ~14% of
profiled time, the rest is Value/decimal construction; reconsider if a change to the value
representation makes dispatch the larger share. **P18** SQL constant validation per ancestor —
after PY-P11 the worst measured case (50k-char constant nested 16 deep) translates in 12.6 ms,
so the effect is gone; reconsider if a workload shows constant validation above ~20% of a
translation.

# Python performance results — round 1 (PY-P1 … PY-P10)

Method (07-performance.md): CPU time (`time.process_time`), fixed seeds, semantic
checksum per workload (identical before/after unless stated), warm-up, 3–7
repetitions, median (min/max in the benchmark output), n / 2n / 4n growth.
Benchmarks: `tools/perf/python/bench_p*.py` (shared `harness.py`); run with
`PYTHONPATH=python python3 tools/perf/python/bench_pN_*.py`.

Environment: CPython 3.14.7, x86_64, 16 threads, revision 223885e + uncommitted working
tree. **Machine load caveat:** five other agents were running tests and benchmarks at
the same time, so absolute numbers moved by up to 2x between runs. Where it mattered
(PY-P6, PY-P7, PY-P8) the comparison was made **in one process, alternating the two
variants, taking the minimum of 3 rounds**; the other rows compare a "before" run and
an "after" run taken minutes apart and are quoted as ratios.

| Task | Decision | Baseline → after (same workload) | Growth check | Evidence | Notes |
|---|---|---|---|---|---|
| PY-P1 sort/TOP key derivation | implemented | `SORT_BY` numeric text 10k 389 → 198 ms, 40k 1.78 s → 805 ms (2.2x); text keys 10k 517 → 174 ms, 40k 2.25 s → 732 ms (3.1x); `DESC` text 40k 4.41 s → 714 ms (6.2x); mixed kinds 10k 807 → 221 ms (3.7x); `TOP_BY` 10 40k 902 → 560 ms; `TOP_BY` 1000 40k 1.13 s → 485 ms (2.3x) | 198 → 416 → 805 ms for 10k/20k/40k: linear | `bench_p1_sort.py`; `test_perf_sort.py` (107 tests: precomputed key order == pairwise comparator on random mixed lists incl. records, ties, DESC; `_DecKey` int interop) | each key is derived once (`sort_key`: rank + int / `_DecKey` / bytes) and sorted with the interpreter's stable sort (`reverse=True` keeps ties in input order = SPEC 7.3); TOP uses `heapq.nsmallest/nlargest` (stable). Remaining cost is evaluating the key body per row (the `MAP` baseline is 380 ms at 40k). |
| PY-P2 LINK `exec` per shape pair | implemented | nested LINK in MAP n=3000: 2.24 s → 350 ms (6.4x); one layout per row: 2.43 s → 209 ms (11.6x); 300 layouts: 2.34 s → 211 ms (11x); 100 layouts 163 → 49 ms (3.3x); single-LINK control unchanged (~33 ms) | 98 → 176 → 350 ms for n=750/1500/3000 | `bench_p2_link.py`; `test_perf_join_plans.py` | (a) compiled code object cached process-wide by generated source (`_PLAN_CODE`, 512 entries, bounded; the text names indices, not keys, so layouts differing only in names share one plan); (b) shape cache evicts the oldest quarter instead of `clear()`. Not done: compiling a plan only after a shape pair is seen a few times (the remaining ~6x over the control for one-layout-per-row is function creation + plan building). |
| PY-P3 SQL `fold_pairwise` quadratic | already addressed by correctness (PY-C42: balanced fold above 256 operands) | measured after that change: `T IN` literals n=500 65 ms, 2000 265 ms, 8000 974 ms; `ANY` 147 / 564 / 1970 ms; `SUM` 40 / 156 / 596 ms (historical report: 31.9 s at 8000) | 4x n → 3.7x time: linear | `bench_p3_fold.py` (rendered-SQL checksums) | Up to 256 operands the fold is still left-deep and copies the growing part list (bounded: 256² part copies); the byte-identical left-fold rewrite the task proposed is not needed below the threshold. Benchmark kept as the regression watch. |
| PY-P4 `dataclasses.replace` in `copy_node` | implemented | 40-rule set: optimiser portion (`physical_ast` − compile) 10.3 → 4.5 ms (−56%), compile+optimise 20.1 → 14.7 ms (−27%); 13-node rule compile+optimise 400 → 273 µs (−32%), one-shot `evaluate` 577 → 469 µs (−19%) | n/a (per-node constant) | `bench_p4_copy.py`; `test_perf_node_copy.py` (every declared field carried; sharing semantics == `replace`) | `Node.replaced(**changes)`: explicit constructor call, shares unchanged lists exactly as `replace` did; used by the optimiser, SQL normaliser, translator and hybrid planner. Not done: return the original node when no child changed in `_substitute`. |
| PY-P5 frozen dataclasses | implemented (Dec, Pos, Token); Node default lists deferred | micro: `Dec(...)` 604 → 251 ns (2.4x), `Pos` 573 → 184 ns (3.1x), `D.mul` 959 → 543 ns, `D.add` 1043 → 786 ns; end to end 100k rows: `MAP` arith 1.83 → 1.62 s (−11%), `FILTER` arith 1.91 → 1.87 s (−2%); tokenise rule text 51.7 → 45.7 ms (−12%) | n/a | `bench_p5_alloc.py`; whole pytest lane (equality/hash/pickle/copy of Dec and Pos unchanged) | plain `__slots__` classes, immutable by convention, `__eq__`/`__hash__`/`__repr__` kept (Dec equality is representational: 150/2 ≠ 15/1). **Deferred:** sharing one empty tuple for `Node.items/args` defaults (≈35% node memory) needs an audit that no consumer appends in place; reconsider with that audit. |
| PY-P6 `Value.num` re-validation | implemented (modest) | `Value.num(dec)` 796 ns vs `_num_owned` 377 ns (2.1x on the constructor); in-process A/B: pure plan 25.9 → 24.9 µs (−4%), tree walk 37.7 → 36.3 µs (−4%), `MAP` arith 50k 870 → 846 ms (−3%), `FILTER` 835 → 806 ms (−3.5%) | n/a | `bench_p6_num.py`; in-process A/B `ab_p6.py`; whole lane | `Value._num_owned(dec)` (no checks) used by the evaluator, math-plan result, arithmetic builtins, `SUM`; `Value.num` stays the public boundary. The review's "15% of plan time" did not reproduce (3–4%). |
| PY-P7 plan thrown away by one un-plannable operand | **rejected** (implemented, measured, reverted) | sub-plans attached to math operands of a refused plan vs none, in-process A/B: `(M) + IF(...)` 1.03x, `(M) * IF(..) − (A*B+C)` 0.95x, `MAX(M, IF, ..)` 0.98x, pure math 1.00x | n/a | `bench_p7_subplans.py`; A/B `ab_p7.py` (PY-P7 needs the reverted `attach_subplans` to re-run) | After PY-C7/PY-C8 (plans hold `Value`s and coerce per operation, SPEC 6.2) **the plan no longer beats the tree walk**: `A*B+C − A*B*C/B` plan 38.0 µs vs walk 40.2 µs. The premise of the finding (plan 37% faster) is gone, so sub-plans buy nothing. Reconsider if the plan runner regains a decimal-slot fast path; worth a separate look at the plan runner's per-step cost. |
| PY-P8 constant list operands rebuilt per row | implemented | 20k rows, `FILTER(x IN list)`: 3 literals (tuple) 353 → 210 ms (1.7x), `LIST` 3 373 → 215 ms (1.7x), 10: 628 → 253 ms (2.5x), 200: 7.02 s → 1.32 s (constant Value) → **194 ms with the membership set (36x)** | linear in rows; flat in list length | `bench_p8_inlist.py`; in-process A/B `ab_p8.py`; `test_perf_in_list.py` (139 tests: constant path == per-row walk for text/number/bool/NULL/list/record needles and mixed literal lists) | optimiser stamps `Node.const_value` (a `ConstantList`: the list's Value + a text set when every element is text/number) on a physical `IN` whose right side is a list of literals; only reached for trees within the depth cap (E_DEPTH is unaffected); a plain-text needle is a set lookup (EQL of plain texts is text equality), anything else falls back to the scan. |
| PY-P9 fold re-parses/re-formats | implemented | `A > 1 + 2` 17.6 → 15.5 µs per evaluation (now equal to `A > 3` at 15.6 µs), `A > 1.5 * 2` 18.7 → 17.4 µs; compile + first run folding two 30k-digit literals 52.4 → 42.6 ms (−19%) | n/a | `bench_p9_fold.py`; `test_perf_fold.py` | folded nodes carry the `Dec` they computed (`literal_num(text, pos, dec)`), folds read `node.dec` instead of re-parsing, `numeric_literal` too. |
| PY-P10 lexer | implemented | big rule text 79 → 51 ms (−35%); 100k identifiers 436 → 216 ms (−50%); 1 MB comment 141 ms → 0.1 ms; 1 MB whitespace 184 → 7.4 ms (25x); 12,500 lines 313 → 200 ms (−36%); `1+1+…` (200k ops) 927 → 800 ms (−14%) | linear | `bench_p10_lexer.py` (token-stream digest identical); `test_perf_lexer.py` (9,000 random sources incl. errors compared with a frozen copy of the pre-change lexer, `fixtures/lexer_before_py_p10.py`; operator-table prefix property) | (P1) operators by 3/2/1-char set lookup; (P2) whitespace, comment (`str.find`), identifier and number scanning by ASCII-class regexes with `endpos`; `.upper()` on an already-ASCII identifier; (P3) line table by `str.find` hops and `bisect` for `pos_at`; `check_source` already had the ASCII fast path from the correctness wave; `Token` is a plain slots class. Not done: quoted-literal scanning is still per character. |

Tests run after the changes (all on the final working tree): Python unit lane 1424 passed
(of which 400+ are new in `test_perf_*.py`), Python conformance 2128/2128, `python/bin/sqlt`
1304/1304 with the reuse twin, `tools/check-eval-equivalence.py` 2128 sources / 0 differ,
`tools/check-cli-source.sh` green, decimal oracle 94,040 cases / 0 mismatches, `python/bin/api.py` exit 0.


# Python performance results — round 2 (PY-P11 … PY-P20)

Same method and harness as round 1 (CPU time, fixed seeds, semantic checksum identical
before/after, warm-up, median, n/2n/4n growth). Environment: CPython 3.14.7, x86_64, revision
223885e + uncommitted working tree (round 1 + correctness work). **Load caveat:** the box ran
at load average 10–24 with five other agents, so absolute numbers moved by up to 2x between
runs; the ratios below come from back-to-back runs of the same script, and for the smaller
effects (PY-P14) the same benchmark was repeated. Each benchmark lives in
`tools/perf/python/bench_p<N>_*.py`; equivalence tests are `python/tests/test_perf_*.py`.

| Task | Decision | Baseline → after (same workload) | Growth check | Evidence | Notes |
|---|---|---|---|---|---|
| PY-P11 text builtins | implemented (PADL/PADR were already fixed by the PY-C14 pad rewrite) | 1M chars: `UPPER` ascii 126 → 3.9 ms (32x), `LOWER` 118 → 3.6 ms; mixed/non-ASCII `UPPER` 170 → 43 ms (4x), `LOWER` 171 → 41 ms; `TRIM` 127 → 10.7 ms (12x), `LTRIM` 47 → 5.6 ms, `RTRIM` 61 → 7.5 ms; `PADL`/`PADR` 1M 3.0 ms unchanged | linear (C-speed str methods) | `bench_p11_text.py` (output checked against the per-character reference in-script), `test_perf_text.py` (3000 random strings incl. ß, ﬁ, İ, ſ, K-sign, NBSP, U+0085 against the old loops) | ASCII text takes `str.upper()/lower()` (byte-identical for ASCII); anything else a fixed 26-entry `translate` table, so the Unicode mapping is still never applied. TRIM uses the explicit four-character set, never `strip()` without arguments. |
| PY-P12 binary codecs | implemented | 900k bytes: `ENCODE_BASE64` 334 → 2.5 ms (130x), `DECODE_BASE64` 410 → 8.2 ms (50x), `CRC32` 198 ms → 77 µs (2500x), `FROM_HEX` 594 → 10.4 ms (57x), `BTL` 750 → 365 ms (2x), `LTB(BTL(B))` 1.39 s → 0.71 s | linear (encode 2.04x/2.03x per doubling before; C speed after) | `bench_p12_binary.py` (checksum 33cb6461a874 before and after), `test_perf_binary.py` (hand-written originals kept as the reference: 6000 random base64 and hex strings incl. invalid padding, `=` placement, non-ASCII digits; CRC32 catalogue check value `cbf43926` and random data) | Input CHECKING stays in this file (explicit ASCII patterns, padding rule), the arithmetic moves to `base64`/`bytes.fromhex`/`zlib.crc32`; the module docstring (which used to say the stdlib is deliberately avoided) is rewritten to say so and why the accepted set is unchanged. `BTL`/`LTB` are limited by `Value` construction (two calls, nine slot writes per element); one shared `Dec` per byte value removes the rest. **Project decision made here:** `zlib.crc32` replaces the table loop (same CRC-32/ISO-HDLC, proven by the reference test). |
| PY-P13 `RREPLACE` | implemented | literal replacement, 1M matches 2.89 → 0.54 s (5.3x); with `$0` 3.45 → 1.16 s (3.0x); two groups, 10k matches 788 → 428 ms; zero-width, 300k matches 464 → 292 ms | 250k/500k/1M: 158/280/551 ms (x1.77, x1.97: linear) | `bench_p13_rreplace.py` (checksum 80ada812802b before and after), `test_perf_rreplace.py` (4000 random pattern/replacement/subject triples against the old `_expand` + generator loop, incl. errors; bad group only when a match occurs; cap) | The replacement is parsed once into literal/group parts; the match loop is inlined (was a generator plus a call per match); the `$`-free case is a constant. |
| PY-P14 nested-loop LINK alias per pair | implemented | `LINK <` 500x500 2.46 → 1.92 s (−22%); always-FALSE 1500x1500 11.1 → 7.98 s (−28%); named `AND` 500x500 2.21 → 1.69 s (−24%); `LINK_LEFT` 500x500 3.73 → 1.88 s (noisy: the baseline run overlapped a load spike) | n x n is quadratic as expected (x3.8–x4.1 per doubling at 375/750) | `bench_p14_link_loop.py` (checksum 39573888d6c7 before and after), `test_perf_link_loop.py` (40 random joins against a brute-force reference, named sides, empty sides) | The right side's alias is built once, on the first left row (so an empty left side still does no work). What remains per pair is one predicate evaluation and the frame stores. |
| PY-P15 2-arg `BUCKET` member copy | **rejected** (measured, no change) | 100k rows / 1000 groups: bare `BUCKET(R, key)` 2.36 s vs `BUCKET(R, key, COUNT(_))` 1.85 s, i.e. ~5 µs per row is the member copy | linear | `bench_p15_bucket_copy.py`; the contract test in the script (a bucket member does not follow a later write to its source) | The copy IS the contract now: SPEC §3.4 (decision of this worklist, T03) lists BUCKET among the operations that copy what they collect, and `conformance/25-value-ownership.selt` `alias.aggregate-copies.bucket-*` pins it on all six hosts. Dropping the clone would make those cases fail. Reconsider only with an ownership/copy-on-write mechanism that can prove nobody else holds the rows. |
| PY-P16 `execute_hybrid` context clone | implemented | 100,000-row context, program that only reads it: 659 ms → 88 µs | independent of context size now | `bench_p16_hybrid_ctx.py`, `test_perf_hybrid_context.py` (the caller's context is bit-identical after a new name, an index write, `op=`, a write inside an aggregate body, a write through an alias copy, a native context) | The context is copied ONE level; only the variables the continuation assigns to (root of every assignment target, found by an iterative AST walk, cached on the plan) are deep-copied. Variables only read are shared. Anything that is not a plain record of variables falls back to the full clone. The PY-C51 contract (the caller's context is never written) is unchanged and tested. |
| PY-P17 interpreter loop / closures | **deferred** (profiled, not attempted) | profile of FILTER/SUM over 20k rows: `eval_node` + `_dispatch` self time ≈ 14% of profiled time; the rest is value/decimal construction and native conversion (`_from_native_at` alone is 12%) | — | cProfile output in the session log; the round-1 prototype (fuse `eval_node`/`_dispatch`, test hot node types first) measured only 2–7% | A closure-per-node compile would touch every node kind and all three equivalence lanes for at most ~10–15% on these workloads. Reconsider when a profile shows dispatch above ~30%, or with a structural move (per-node handler slot chosen in `optimize_tree`). |
| PY-P18 SQL constant validation per ancestor | **deferred** (measured, effect gone) | after PY-P11 the workload is cheap: 50k-char constant nested 2/8/16 deep translates in 0.83/4.2/12.6 ms; `REPLACE`-nest (200k chars) depth 16/48/90 (the nesting cap): 2.5/12.7/47 ms (≈ quadratic in depth, tiny constant) | quadratic in depth but bounded by the 200-level cap at ~50 ms | `bench_p18_const_validate.py` | The reported 1.8 s came from per-character `UPPER`, fixed in PY-P11; constants are also folded at compile time. A per-translation memo would need an evaluator node type for memoised constants (JS did that for JS-P4); not worth the complexity at ≤ 50 ms. Reconsider if a non-folded, expensive constant builtin appears. |
| PY-P19 `%` on huge ints | implemented | 400k-digit % 200k-digit: 1.31 → 0.815 s (1.6x); `A % B` small operands unchanged (32 µs) | per doubling x3.5 → x3.0 (sub-quadratic path) | `bench_p19_mod.py` (checksum 90c6cde547b0 before and after), `test_perf_mod.py` (3000 random sign/scale pairs against `%`; `is_integer`) | `divmod(A, B)[1]` in `D.mod`, and in `D.is_integer`. The win grows with size (the review measured 9x at 3.3M bits). |
| PY-P20 SQL `text_literal` | implemented | 1 MB clean text: 229–329 ms → 2.9 ms (80–110x, per dialect); 1.1 MB with quotes/backslashes 276–345 → 38–50 ms (6–7x); 3-char literal 3.5 µs | linear | `bench_p20_text_literal.py` (asserts equality with the reference loop for every input/dialect), `test_perf_text_literal.py` (shipped dialects + overlapping/multi-character/empty/‘longest wins’ tables; escape-table replacement is not served from the cache) | Single-character rules → `str.translate`; multi-character rules → one alternation regex sorted longest-first (same as the scan it replaces); an escaper per (dialect, escape-dict identity). |

## Note: the math plan vs the plain tree walk (observation from round 1)

`tools/perf/python/ab_plan_vs_tree.py`, in-process alternating, minimum of 5 rounds, after the
evaluate-then-coerce change (plan = `physical_ast()`, tree = `ast`):

| workload | plan | tree walk | plan / tree |
|---|---|---|---|
| scalar `A*B+C-D/2` (x2000) | 21.4 ms | 23.5 ms | 0.91 |
| scalar `(A+B)*(C-D)+A*A` (x2000) | 19.1 ms | 24.0 ms | 0.80 |
| `SUM(R, a*b+1)` 20k rows | 226.8 ms | 225.8 ms | 1.00 |
| `COUNT(FILTER(R, a*2+b > 100))` 20k rows | 359.9 ms | 309.8 ms | 1.16 |
| `COUNT(MAP(R, (a+b)*c))` 20k rows | 271.3 ms | 248.3 ms | 1.09 |

The plan still wins on pure scalar arithmetic (9–20%), ties on `SUM`, and loses 9–16% on
per-row `FILTER`/`MAP` bodies whose operands are field reads (each field read now hands the
plan a `Value` and the plan coerces at the consuming step). The result is mixed, not a pure
cost, so nothing is simplified or disabled. Possible follow-up (not done, needs its own
measurement on more workloads): skip planning for bodies that read fields and are evaluated
per row.

## Verification (round 2, final tree)

All on the final working tree (CPython 3.14.7): `python/tests` 1456 passed (round 2 added
`test_perf_text.py`, `test_perf_binary.py`, `test_perf_rreplace.py`, `test_perf_link_loop.py`,
`test_perf_hybrid_context.py`, `test_perf_mod.py`, `test_perf_text_literal.py`); Python
conformance 2128/2128; `python/bin/sqlt` 1304/1304 with the reuse twin;
`tools/check-eval-equivalence.py` 2128 sources / 0 differ; decimal oracle 94,040 cases / 0
mismatches; `tools/check-cli-source.sh` (python) green; `python/bin/api.py` exit 0.


# Python performance results — round 3 (PY-P21 … PY-P31)

Same method and harness as rounds 1-2 (`tools/perf/python/`, CPU time, checksums, growth). Revision 223885e +
uncommitted working tree; CPython 3.14.7; box load average 10-20 from other agents, so **A/B rows
were taken in one process, alternating baseline tree and working tree, best of N rounds**
(`ab_programs.py`, `ab_translate.py`, `ab_p24_hash.py`; the baseline is a copy of `python/sel` taken at
the start of this round). Every "after" run has the same semantic checksum as its baseline.

| Task | Decision | Baseline → after | Growth check | Evidence | Notes |
|---|---|---|---|---|---|
| PY-P21 `_contains_unsupported_sql` ignores `ops` | implemented | PostgreSQL/MariaDB plan of `SORT_BY .> MAP(… BAND …)`: prefix `SELECT r.*` (all columns moved) → projected columns only; plan time 1.5 → 1.2 ms | n/a (plan shape) | `bench_p21_unsupported_ops.py`, `test_perf_unsupported_ops.py` (6 of 13 fail on the old tree) | a withdrawn operator (`ops` entry is a reason string: BAND/BOR/BXOR) is unsupported like a withdrawn function; SQLite already projected |
| PY-P22 scale gap builds `10**gap` | implemented | `cmp` / `is_integer` with a new ~1e6 exponent per call 232 / 229 ms → 1.2 / 0.7 µs; `cmp` of 200k-digit vs scale 1e6 220 ms → 2.4 µs | flat in gap | `bench_p22_scale_gap.py`, `test_perf_scale_gap.py` (exact-rational differential, power-of-ten boundaries) | bit-length bound decides when the scales differ by more than 64; inconclusive falls through to the exact alignment; ordinary `cmp` 1.3 → 1.2 µs. Additions still align (the result really has that scale). The review's 0.3 s for a *repeated* gap did not reproduce (the power cache hits: 139 µs) — the cold case is what was fixed |
| PY-P23 over-cap products computed in full | implemented | `mul` 1M-digit × 1M-digit → `E_RANGE` 1.32 s → 12 µs; `POWER(POWER(10,20),100000)` 842 → 197 ms (the legal intermediate squarings remain) | n/a | `bench_p23_overcap_mul.py`, `test_perf_overcap_mul.py` (equals `guard` with a shrunk cap, same code/message/position) | pre-check from operand bit lengths only when they sum to ≥ 2^20 bits; small `mul` +0.06 µs (0.53 → 0.60 µs, accepted: <1% of any evaluated node) |
| PY-P24 value overheads | implemented (b) (c) (d); (a) rejected | (b) `is_vacuous` of a fresh 1M-digit number 916 ms → 2.6 µs; (c) `structural_hash` of dense lists 172 → 152 ms (20k×8), 182 → 172 (2000×100), 183 → 170 (200×1000) = 6-12%; (d) `ROUND(1, 10**300000)` raised a raw `ValueError` (CPython's int→str limit) → now `E_RANGE` | (b) independent of size | `bench_p24_value_overheads.py`, `ab_p24_hash.py`, `test_perf_value_overheads.py` (ValueError case and the 916 ms case fail on the old tree) | (a) see summary. (d) was a correctness leak, not just speed. The index-hash table is replaced, never appended to (thread-safe), capped at 4096 |
| PY-P25 SUM / aggregate walk / do_sort | implemented (SUM, do_sort frame); walk hoists rejected | `SUM(R,_["n"])` 100k 202 → 160 ms (0.79), scale-2 text 212 → 167 (0.79), scalars 188 → 131 (0.70); `SORT_BY` 886 → 803 ms (0.91), text keys 960 → 877 (0.91) | linear | `bench_p25_aggregates.py`, `ab_programs.py … sums/sorts/walks`, `test_perf_aggregates.py` (SUM = chain of `add`s incl. cancellation scale, cap errors via shrunk cap) | SUM keeps the widest scale and the same cap error at the same position; `do_sort` uses one frame and builds `_K` only if the key body reads it. Walk hoists/inlined `keep`: 15-round A/B MAP 1.03, FILTER 0.99, ALL 1.01 — reverted |
| PY-P26 DISTINCT/BUCKET hash whole records | implemented | `DISTINCT` 100k text 194 → 105 ms, 100k int 192 → 77 ms; `BUCKET(T,_,COUNT(_))` 868 → 444 ms, int keys 397 → 271 ms; `BUCKET` by record field 648 → 558 ms | linear | `bench_p26_identity.py`, `test_perf_identity.py` (vs an O(n²) `eql` definition incl. 1 vs 1.0, NULL vs empty list, BIN vs TEXT, BOOL) | childless keys are identified by `(kind, scalar)` in a plain dict/set; records keep the structural path (`DISTINCT` of 100k records unchanged within noise); `first_collection_item` reads one element via `iter_values` (LINK over irregular rows 3.5 → 2.8 ms) |
| PY-P27 hybrid quadratic prefix search | already addressed | 25/50/90/180 chained MAP steps + untranslatable tail: 6.7 / 12.7 / 25.5 / 48.9 ms (linear; the review's 0.72 s at 90); helper chains 100/200/300/600: 4.9 / 7.6 / 11.0 / 25.1 ms | linear (×2 per doubling) | `bench_p27_hybrid_planner.py` | the linear planner from PY-C52 / PY-C1 site f; over 200 steps the plan is `pure_memory` (depth), by design. No code change |
| PY-P28 translator per-translation overhead | implemented (template segment cache) | 10-clause rule × 3 dialects 5.09 → 4.21 ms (0.83) | n/a | `bench_p28_translate_overhead.py`, `ab_translate.py`, `test_perf_template_segments.py` (vs the old character scan on 4000 random templates incl. unbalanced braces) | templates are tokenised once per distinct string (bounded cache 4096). `chain()` memo and returning the original node from `_substitute` were not done (no measurable share; see summary). The per-element re-render stays (slot numbering rule) |
| PY-P29 evaluator micro-costs | implemented (b) text literal, (c) bitwise; (a) and (b2) rejected | text literal per row MAP 197 → 187 ms (0.95), literal compare 0.98, concat 0.98; `BAND`/`BXOR` 1000 bytes 71 → 6.7 µs (0.09), 1 MB 70.8 → 7.8 ms (0.11); below 64 bytes unchanged | linear | `bench_p29_evaluator_micro.py`, `ab_programs.py … micro`, `test_perf_evaluator_micro.py` (byte-loop differential around the threshold; hand-built literal nodes with a lone surrogate / non-str still refused) | (a) `eval_bool`: upper bound measured with a free `Value.bool`: 3% (and-chain) … 12% (not/or) — rejected. (b2) `_resolve_target` re-derivation is required by SPEC 5.7 and bounded — rejected |
| PY-P30 regex `i` flag / `_args_for` | implemented | `RMATCH` no flag 679 → 641 ms (0.95), with `i` 888 → 815 (0.92), `RFIND` 966 → 917 (0.95), 100k rows | linear | `ab_programs.py … regex`, `test_perf_regex_overhead.py` | `isascii()` for the pattern and subject tests; no-flags call skips the flag scan; kelvin/long-s folding and every error unchanged. `tools/check-regex-ambiguity-diff.py` (6000 patterns plain + 3000 `i`): 0 mismatches |
| PY-P31 lexer literal bodies | implemented | 1 MB quoted literal 128.5 → 8.1 ms, 1 MB raw 104.7 ms → 0.51 ms, quoted with an escape per ~50 chars 166 → 35 ms, with `{1}` per ~200 chars 177 → 49 ms; 2000 short literals 40 → 37 ms | linear; nested interpolation was already linear since the T01 lexer rewrite (depth 100: 1.3 ms, 1000: 13.9 ms, 4000: 35 ms) | `bench_p31_lexer_literals.py`, `test_perf_lexer_literals.py` + the round-1 frozen-lexer differential `test_perf_lexer.py` (0 differences) | run-at-a-time scanning through two precompiled regexes; the nested-interpolation half of the finding is already addressed |

## Verification (round 3, final tree)

All on the final working tree (CPython 3.14.7): `python/tests` 1659 passed (round 3 added `test_perf_unsupported_ops.py`,
`test_perf_scale_gap.py`, `test_perf_overcap_mul.py`, `test_perf_value_overheads.py`, `test_perf_aggregates.py`,
`test_perf_identity.py`, `test_perf_template_segments.py`, `test_perf_evaluator_micro.py`,
`test_perf_regex_overhead.py`, `test_perf_lexer_literals.py`); Python conformance 2128/2128; `python/bin/sqlt`
1304/1304 with the reuse twin; `tools/check-eval-equivalence.py` 2128 sources / 0 differ; decimal oracle 94,040
cases / 0 mismatches; `tools/check-cli-source.sh` (python) green; `python/bin/api.py` exit 0.
Which new tests fail on the baseline tree: `test_perf_unsupported_ops` (6), `test_perf_overcap_mul` (timing/attr),
`test_perf_value_overheads` (3: formatting time, table attribute, ValueError); the rest are equivalence tests that
pass on both trees by design (they pin behaviour the optimisation must preserve).
