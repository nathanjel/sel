# C++ performance — summary (all 25 tasks, CPP-P1 … CPP-P25, plus CPP-REG-1)

Decisions across the three rounds: **22 implemented** (20 in full; 2 partly: CPP-P24, CPP-P25), **1 already
addressed** (CPP-P3, by the SQL balanced fold), **1 deferred** (CPP-P8, numeric `Value` footprint: high risk,
≤20–30% estimated) and **1 rejected** (CPP-P10, sort comparator: no gain measured). Nothing is wholly
deferred that has a contained plan. Open follow-up: **CPP-REG-1** (scale-bench Scenario 1 still about +73% over
0.9.2) needs copy-on-write `Value` nodes — a design task under advisory review, not a tuning step; see the
attribution in the round-2 section. Round 3 (CPP-P21 … CPP-P25, the two round-2 leads) is at the end.

# C++ performance results — round 1 (CPP-P1 … CPP-P10)

Revision `223885e` + working tree (uncommitted), g++ 16.2 `-O2`, 16-thread Linux box **under heavy
load from other jobs** (load average 5–24 during the runs), so every headline number is the **min of 5
interleaved A/B runs** (`tools/perf/cpp/bench.py --ab`), CPU time (user+sys). A = the binary built from the
tree before any of these edits; B = the current binary. Output checksums were compared on every run and are
identical (`checksums equal = yes`). Benchmarks: `tools/perf/cpp/bench.py` (programs, n/2n/4n growth,
checksums), `tools/perf/cpp/sqlbench.cpp` (SQL layer), `tools/benchmark-cpp-value.cpp` (Value footprint).

Verification after the changes: `cpp/build/conformance` 2128/2128, `cpp/build/sqlt` 1304/1304, `cpp/build/unit`
645/645 (new tests listed below), decimal oracle `SEL_IMPLS=cpp tools/check-decimal.sh` 94,040 cases / 0
mismatches, a 7,500-program text-builtin differential against the old binary (0 diffs), `make -C cpp asan`
exits 0: unit-asan 627/627, conformance-asan 2128/2128, sqlunit-asan, sqlt-asan 1304/1304, no sanitizer reports.

| task | decision | baseline (A) | after (B) | growth check | evidence / notes |
|---|---|---|---|---|---|
| CPP-P1 big decimal division | **implemented** (Knuth algorithm D on the base-1e9 limbs, `divmod_limbs`; power-of-ten and single-limb fast paths kept) | `LEN(POWER(9,n)/POWER(7,n/2))` n=10000: 0.58 s (x4.2 per 2x n); `%` by a 20-digit divisor, 61-digit dividend, 40k ops: 1.93 s; 30/20-digit `/` 40k ops: 0.132 s | 0.006 s; 0.073 s; 0.092 s | n 2500→5000→10000: 0.002→0.003→0.005 s (was 0.046→0.151→0.636) | the review's 116 s case `LEN(POWER(9,100000)/POWER(7,50000))` now 0.33 s. Bit-identical results (oracle 94,040 cases; `test_knuth_division`: random + add-back-triggering shapes vs an independent digit-by-digit reference). |
| CPP-P2 multiplication / early E_RANGE | **implemented** (early refusal in `dec_mul` and `dec_power` from operand sizes; Karatsuba above 48 limbs, `KARATSUBA_LIMBS`, for product and square) | `LEN(POWER(99999999,50000))` 0.90 s; doomed `A=POWER(99999999,96000); A*A` 9.5 s | 0.277 s; 0.885 s | 12500→25000→50000: 0.032→0.095→0.279 (x2.94 per 2x, was x3.9) | same E_RANGE code and position as before (checked on the review's repros). Threshold 48 was chosen, not tuned (one candidate measured). `test_karatsuba_and_early_range`: Karatsuba == schoolbook on 120 random pairs (+squares), early refusal positions. |
| CPP-P3 SQL fold O(N²) | **already addressed** by the correctness wave (balanced fold above 256 operands, SPEC/sql-translation §7.1) | review: `ANY` n=16000 34 s, exponent ≈1.9 | `sqlbench fold`: n=500 / 2000 / 8000 / 16000 → 0.012 / 0.050 / 0.202 / 0.410 s (any); in-list 0.435 s, JOIN 0.353 s | linear (x4.0–4.2 per 4x n) | no further change: output bytes are pinned by sql/cases (balanced-fold cases at 256/257). Reconsider only if profiling shows `Emit::fill` dominating below 256 operands. |
| CPP-P4 `??` on a miss | **implemented for the direct case** (a name or literal-key index chain is probed without evaluating it; skipped when the depth budget could run out inside the chain, so E_DEPTH stays the evaluator's); **deferred for nested operands** | `MAP(L, _["k"] ?? 0)` 100k: 1.39 s (14 µs/miss) | 0.11 s (12.6×) | 50k→100k→200k linear | `(_["k"] & "x") ?? 0` (miss *inside* an expression) still throws an exception: 1.63 → 1.09 s in the same runs (other changes only). A non-throwing evaluation protocol is the fix and is too invasive for this round; reconsider if nested-operand misses are common in real rules. `test_coalesce_probe` pins present/miss/NULL/vacuous/scalar-index/undefined cases. |
| CPP-P5 operator dispatch | **implemented the dispatch half** (operator resolved once at parse time to `BinOp`, `Node::opc`, `switch` in `eval_binary`; `cmp_holds` replaces `compare_result`/`is_compare_op`); **literal sharing rejected** | `COUNT(FILTER(L,_>5 AND _<900000))` 1M: 1.94 s | 1.56 s (−20%) | linear | Sharing an immutable literal `Value` across evaluations would let a host mutate a returned literal and poison the program (the PHP-C1 lesson), or force a clone of every returned value at the boundary; the measured allocation share did not justify that. Reconsider with a proper immutable-Value flag. |
| CPP-P6 128-bit divisions by 10 | **implemented** (`u128_digits_rev`: 19-digit chunks, then 64-bit; `trim_trailing_zeros` in 64-bit when the mantissa fits) | `(_ / 7) & "x"` 400k: 0.71 s; `ROUND(_*1.37/3,2)` 400k: 0.63 s | 0.60 s (−15%); 0.59 s (−7%, within noise) | linear | `dec_round` and `dec_to_int` use the POW10 table, not a division loop, so they were left alone (measured: no change). |
| CPP-P7 LIST/RECORD deep clone | **implemented** (`adopt_or_clone`: a value whose every node has exactly one owner and whose depth fits the cap is kept, anything shared is cloned; used by `,`, `LIST`, `RECORD`, assignment and MAP results; `Args::take_val`) | `COUNT(LIST(MAP(S,RECORD(3 fields))))` 300k: 0.90 s; MAP+RECORD only 0.68 s | 0.60 s (−33%); 0.58 s (−14%) | linear | aliasing is exactly as before: `test_adopt_or_clone` (variables, TAKE-aliased elements, the same value twice, nested temporaries) + conformance 25-value-ownership 75/75. |
| CPP-P8 numeric Value footprint | **deferred** | `tools/benchmark-cpp-value.cpp`: `sizeof(Value)=8, Impl=72, Dec=96`; 200k integers retained 16.0 MB (same as text in that harness); `SUM(XS,_)` 1M elements 0.81 s, peak 214 MB | unchanged | — | Shrinking `Dec` to a union of inline mantissa / heap bignum touches every numeric path (limbs/digits caches are `mutable` and shared by the SQL translator). The earlier Impl free-list work already removed the dominant allocation; the remaining gain is estimated ≤20–30% on numeric-heavy loops against a high regression risk. Reconsider if numeric loops dominate a real workload. |
| CPP-P9 text builtins decode to `char32_t` | **implemented** (byte-level `LEN LEFT RIGHT SUBSTR FIND REPLACE SPLIT TRIM/LTRIM/RTRIM UPPER LOWER BACKWARDS CODE`; UTF-8 is self-synchronising, valid text is an invariant) | 16 MB text: `UPPER` 0.209 s, `LEN` 0.130, `LEFT` 0.123 (includes ~0.05 s building the text); 400k short strings `LEN(_)==1` 0.39 s | 0.078, 0.063, 0.056 s (2.2–2.6×); 0.32 s (−19%) | linear | `test_text_byte_paths` (29 cases with 2/3/4-byte characters and combining marks, expectations from the decoding implementation) and a 7,500-program differential against the old binary: 0 differences. `slice_cp`/`index_of_cp` had no remaining users and were removed. |
| CPP-P10 sort comparator | **rejected** | `SORT` of 200k numeric-text keys ≈ 0.07 s over the list-building baseline (0.306 vs 0.238 s total) — the review's 620 ms figure no longer reproduces | decorated keys (built and measured): total 0.309 vs 0.306 s; sort-only delta within noise | linear | The earlier correctness work (cached numeric parse, single total order) already removed the cost; a decorated-key implementation gave no reproducible gain, so it was reverted. Reconsider if a profile shows `compare_values` hot again. |

## Notes

* **CPP-P14 observation for the next round:** `sqlbench bindings` translating one small rule 5000× against
  2 / 51 / 501 bindings: 11.6 / 25.6 / 210 µs per call (18× slower for an identical rule).
* **Noise:** the machine was shared with up to 20 other jobs; absolute timings are not comparable with the
  review's. Ratios from interleaved A/B min-of-5 runs are the evidence; the raw tables are in
  `$CLAUDE_JOB_DIR/tmp/cppperf/` (not kept in the repo) and reproduced by `bench.py --ab`.
* **New/changed tests** (`cpp/tests/unit.cpp`): `test_knuth_division`, `test_karatsuba_and_early_range`,
  `test_coalesce_probe`, `test_text_byte_paths`, `test_adopt_or_clone`.

---

# C++ performance results — round 2 (CPP-P11 … CPP-P20, CPP-REG-1)

Revision `090cd61` + working tree (uncommitted), g++ 16.2 `-O2` only, same shared box (load average 5–11
during the final runs). Headline numbers are **min of 7 interleaved A/B runs** (`bench.py --ab`), CPU time;
A = the binary built before any round-2 edit (`sel-base`), B = current. Checksums equal on every row.
Growth runs: `bench.py --reps 3` (n/2n/4n). Raw tables: `$CLAUDE_JOB_DIR/tmp/cppperf2/` (not in the repo).

Verification after the round: `make -C cpp` clean, `cpp/build/conformance` 2128/2128, `cpp/build/sqlt` 1305/1305
(one case more than round 1: added by other work), `cpp/build/unit` 720/720, `sqlunit` green, decimal oracle
94,040 cases / 0 mismatches, **`make -C cpp asan` exits 0** (unit, conformance, sqlunit, sqlt under
address+leak+UB), `make -C cpp tsan`, `tsan-registry` and the new `tsan-regex` clean.

| task | decision | baseline (A) | after (B) | growth check | evidence / notes |
|---|---|---|---|---|---|
| CPP-P11 `TOP` full sort | **implemented** (when `k*2 >= n` the admitted entries are sorted once with `std::stable_sort` and truncated, instead of heap maintenance; `k*4` was measured and regressed the 25% case, so the threshold is `k*2`; direction hoisted out of the comparator) | `TOP(X, n/10)` 0.212 s, `TOP(X, n/2)` 0.371 s (n=100k text keys), `TOP_DESC` 1000 of 200k ascending 0.459 s; `TOP(X,10)` 0.275 s | 0.190 s (0.90), 0.310 s (0.84), 0.359 s (0.78); `TOP(X,10)` 0.268 s (0.97, must not regress) | linear (x2.0–2.2 per 2x) | `test_round2_fast_paths` compares TOP against SORT+take for asc/desc, ties (stability), k = 0, 1, n−1, n, n+1. A decorated-key comparator was not retried (CPP-P10 lesson). |
| CPP-P12 bare `BUCKET` | **implemented** (group keys moved into the result, rows built with `Value::list(std::move(rows))` and assembled with `Internals::with_children`; rows kept from pipeline temporaries — see REG-1 — instead of cloned) | `BUCKET(X,_)` unique keys n=200k 0.616 s; 6 groups n=400k 0.458 s | 0.461 s (0.75); 0.461 s (1.01, nothing to gain: 6 groups) | linear | The 3-argument form (per-group aggregate) is unchanged by design; the gain is only the bare form's per-row copying. |
| CPP-P13 nested parentheses | **implemented** (a parenthesised group is marked `grouped` in place when the inner node is exclusively owned, instead of wrapping/copying it per level) | 90 parens around a 200k-element list 0.989 s (one pair: same list ≈0.31 s → depth was multiplying the work) | 0.306 s (0.31, 3.2×) | x2.0 per 2x n | A grouped list is still one argument and a grouped list inside a list still flattens as before (`test_round2_fast_paths`, conformance). |
| CPP-P14 SQL translate with many bindings | **implemented** (`Bindings` precomputes `value_names()` and the alias-clash verdict once in the constructor; the translator holds `const Bindings&` instead of copying the map per call; `const_scope` iterates the cached names) | `sqlbench bindings` 5000× 2 / 51 / 501 bindings: 11.5 / 24.3 / 204 µs per call | 11.6 / 10.9 / 11.2 µs (**18×** at 501) | flat in the binding count (was linear) | Emitted SQL byte-identical (checksum `6a4ca5d85f64ae11` both). `sql_unit` P14 block: alias-clash errors (same exception, same position) are still raised lazily, at translate time, exactly as before. |
| CPP-P15 `evaluate()` overhead | **implemented** (`evaluate()` runs a rule with no iterating call — `has_iterating_call` — through plain `eval_node` on the tree as parsed, skipping the extra plan/frame machinery) | `evalbench` (per call): boolean 4.59 µs, arithmetic 13.05 µs, aggregate 15.9 µs | 2.70 µs (−41%), 7.04 µs (−46%), 16.0 µs (neutral: has a loop) | — | Results checksummed by the bench (equal); `test_round2_fast_paths` pins that a loop-free rule still writes the caller's context. Numbers above are same-run pairs; the box's frequency wanders ±2× between runs, so absolute µs are not comparable across runs. |
| CPP-P16 regex call overhead | **implemented** (regex cache holds `shared_ptr<const Regex>`; a `thread_local` last-used slot skips the mutex, key string and map when the same pattern+flags repeat; the subject is widened to UTF-32 in one step — byte-for-byte for ASCII — instead of via a code-point vector plus a copy) | `FILTER(L, RMATCH("^a",_))` 1M 0.854 s; with `"i"` 0.957 s; `RMATCH("^abc", 16 MB text)` 0.173 s | 0.716 s (0.84); 0.777 s (0.81); 0.111 s (0.64) | linear | **Found by TSan while testing this:** the pre-existing bounded cache evicted a `Regex` another thread was still matching with (use-after-free window). The `shared_ptr` entries fix it; `cpp/tests/regex_race.cpp` + `make tsan-regex` (4 threads × 600 rounds × 300 patterns, cache bound forced) pin it, plus a 4-thread churn in `test_round2_fast_paths` and cache-churn/flag/non-ASCII cases. |
| CPP-P17 loop-binder name test | **implemented** (`node_contains_var` compares the `_K`/binder name case-insensitively in place; it built lowercase copies of the name per node visited) | `MAP(L, SUM(LIST(1), _+…))` 100k 0.484 s; tiny inner `ANY` 200k 0.212 s | 0.438 s (0.90); 0.194 s (0.92) | linear | Semantics unchanged (`_k` any case, named binder with `_K` pinned in `test_round2_fast_paths`; conformance 2128). |
| CPP-P18 `RREPLACE` | **implemented** (replacement template pre-split into pieces once, only for subjects ≥ 256 bytes — pre-splitting short subjects cost +8%; `u32_subject` decoded once) | 2M matches in a 2 MB text 0.295 s; per short string 400k 0.385 s | 0.158 s (0.54); 0.366 s (0.95, neutral) | x2.0 per 2x | `test_round2_fast_paths`: `$0`, `$1`, `$$`, empty matches, multibyte subjects on both sides of the 256-byte gate. |
| CPP-P19 base64 | **implemented** (static `B64_TABLE` lookup, reserved output strings) | decode 8 MB 0.189 s; encode 0.088 s | 0.131 s (0.69); 0.079 s (0.90) | linear | Rejections (`E_BAD_ARG` on a foreign character / misplaced padding) unchanged: round-trip + malformed-input cases in `test_round2_fast_paths` and conformance. |
| CPP-P20 `FILTER` element copying | **implemented** (`keep_element`: elements owned only by a pipeline temporary are kept, not cloned; a packed-list source yields a packed list) | `FILTER` keep-all 400k 0.502 s; keep-half 0.373 s | 0.463 s (0.92); 0.356 s (0.95) | linear | Aliasing is exactly as SPEC §3.4 (`test_pipeline_temporaries_are_kept`, `test_filter_packed_result`; mutation-checked: forcing adoption of variable-owned elements fails the TAKE/variable cases). |
| **CPP-REG-1** scale-bench Scenario 1 vs 0.9.2 | **partially recovered** (≈7%); the rest is structural — see the attribution below | `faff480` (0.9.2): **595 ms** (min of 7; median 607) | before this fork's REG-1 work 1,105 ms; **now 1,031 ms** (−6.7%) → still **+73%** vs 0.9.2 | — | `scale-bench --only scenario1 --runs 3 --warmups 1`, `program_run_ms`, three binaries interleaved 7×. Scenarios 2–4 (run-time a few ms): 6→7, 186→205 (+10%), 8→10 ms. |

## CPP-REG-1 attribution

gprof (`-O2 -pg`, Scenario 1, one run, same dataset) of the 0.9.2 tree vs now — the program does the same
logical work (identical row counts, identical result), so call counts attribute the difference directly:

| counter | 0.9.2 | now | Δ |
|---|---:|---:|---|
| `Value::destroy` element lambda (objects freed) | 4.52 M | 10.58 M | **+6.06 M** (≈ +6 M node allocations/frees) |
| `Value::destroy` top-level | 1.20 M | 2.74 M | +1.54 M |
| `Dec::Dec(const Dec&)` (numeric leaf copies) | 2.32 M | 3.57 M | +1.25 M |
| `Value::clone_at` calls | 290 k | 338 k | +48 k (each clones a subtree) |
| `eval_dispatch` | 3.57 M | 2.41 M | −1.16 M (P5/P15-style savings, offsetting) |
| `take_snapshot` | — | 228 calls | the §7.3 snapshot handles (cheap per call, but every iteration pays) |
| self time in `destroy` lambda | 0.39 s (13%) | 0.74 s (20%) | **+0.35 s** — the largest single term |
| `structural_hash`, `get`, `push_back` | 0.35 / 0.19 / 0.11 s | 0.27 / 0.23 / 0.14 s | flat |

Reading: the regression is **allocation + destruction of copied row trees**, not evaluation. It is the cost of
SPEC §3.4 (aggregates, `,`, `LIST`/`RECORD`, LINK/join projection copy what they collect) and §7.3 (iteration
over a snapshot), both introduced by the remediation commits; 0.9.2 aliased those values. A semantics-preserving
recovery has to avoid the copy when nobody else can observe it:

* **Done** — `keep_element`/`Internals::exclusively_held`: a `FILTER`/`BUCKET` row/`SORT`/`TOP` element that is
  owned only by a pipeline temporary (use_count 1 all the way down) is moved, not cloned; `Value::clone_at` has a
  scalar-leaf fast path; bare `BUCKET` moves keys and rows; packed-list results stay packed. 1,105 → 1,031 ms.
* **Not done (would need a semantics or representation change)** — the remaining ~6 M extra nodes come from
  projections (`LINK`/`JoinProjector::build`, 232 k rows) and record construction whose sources are *dataset
  variables* (shared, so adoption is correctly refused). Removing them needs copy-on-write `Value` nodes (clone
  becomes a refcount bump, the first write copies). That touches every mutation site (`Internals`, assignment
  store, `,`), interacts with the TSan-checked lazily-filled `Dec` caches, and is the same work the Rust/Go hosts
  already do structurally; it is a design task, not a tuning one. A partial variant (adopting children of
  partially-owned nodes) was built and measured: no gain on this scenario, and its nested path was untestable — reverted.
* Tried and rejected: a thread-local deferred-free list in `Value::destroy` (no gain), sharing `Dec` between
  clones (the mutable `digits`/`limbs` caches make that a data race under TSan).

## Notes

* **Noise:** the machine frequency and load wander by up to 2× between runs (see the `evalbench` remark); only
  same-run A/B pairs are evidence. Scale-bench 0.9.2 produced one 1.28 s outlier in a set of 595–611.
* **Leads for round 3:** (1) copy-on-write `Value` nodes (REG-1 remainder, design first, needs an advisor pass);
  (2) `opt_pipeline_op` classifies a `string_view` 7.3 M times in Scenario 1 (~1.4% of time) — precompute once
  per `Node`; (3) a non-throwing `??` protocol for nested operands (round-1 CPP-P4 leftover).
* **New/changed tests:** `cpp/tests/unit.cpp`: `test_pipeline_temporaries_are_kept`, `test_filter_packed_result`,
  `test_round2_fast_paths` (TOP, parens/depth, RREPLACE gate, base64, regex cache, evaluate fast path);
  `cpp/tests/sql_unit.cpp`: P14 block (`value_names`, lazy alias-clash); `cpp/tests/regex_race.cpp` (new) via
  `make -C cpp tsan-regex`.
* **Tools:** `tools/perf/cpp/bench.py` (scenarios p11–p20), `evalbench.cpp` (new, CPP-P15), `sqlbench.cpp bindings`.


# C++ performance results — round 3 (CPP-P21 … CPP-P25, round-2 leads)

Revision `090cd61` + working tree, g++ 16.2 `-O2`, box loaded by other jobs (load average 10–12): ratios are
the **min of 7–11 interleaved A/B runs** (`tools/perf/cpp/bench.py --ab`, A = the binary at the start of the
round) and every checksum matched. Growth: every scenario is linear (×2.0 per 2× n).

| task | decision | baseline (A) | after (B) | notes |
|---|---|---|---|---|
| CPP-P21 `RECORD` literal keys | **implemented** (values go straight into the shaped storage; the shape is checked against the key NODES as they are now — the hybrid planner rewrites argument lists and a stale shape must fall back) | `MAP(S, RECORD("id",_K))` 400k: 0.485 s; three keys 1.42 s | 0.404 s (**0.83**); 1.09 s (**0.76**); `LIST(_K,_K)` reference 1.01 | first version skipped the node check and broke a `sqlunit` parity case — the check is the price of the planner rewriting nodes |
| CPP-P22 substring search, pad, repeat | **implemented** (`byte_find` = glibc `memmem`, two-way, under FIND/REPLACE/SPLIT; `pad` byte-level; `REPEAT` moves its result and assigns single-byte fills) | `FIND(a^n & "b", a^2n)` n=400k: **6.66 s** (×4 per 2×); `PADL("x",16M,"ab")` 0.445 s / 187 MB; `REPEAT("z",16M)` 0.091 s | 0.021 s (linear); 0.073 s / 35 MB (**6×**, 5× less memory); 0.020 s (4.5×) | differentials: 3,000 PAD and 4,000 FIND/REPLACE/SPLIT programs against a Python model over a multi-byte alphabet, 0 diffs; non-glibc builds fall back to `std::string::find` |
| CPP-P23 `DISTINCT`/`DEDUPE` | **implemented** (open addressing over `uint32` indices into the output list, full hashes kept in a parallel vector; load ≤ 1/2) | 200k unique numeric texts 0.340 s | 0.227 s (**0.67**); with 1000 distinct values (little to gain) 0.96 | 1,500-program differential against first-occurrence order, 0 diffs |
| CPP-P24 small overheads | **partly implemented**: (a) `Args` keeps up to 4 arguments in the object — **0.96–0.98** on strict-call-heavy loops (modest); (b) `execute_hybrid` copies only the variables the continuation assigns to (`assigned_roots`, iterative) — **26,255 µs → 2.1 µs** per call with a 50k-row context (12,000×), caller never written (tested); (c) math scratchpad sized to the plan: **no measurable gain, reverted**; (d) `plan_hybrid` re-normalisation: **not reproducible** — planning is linear (≈8 µs/step, 1.2 ms for 160 steps) | see cells | | the context copy keeps the whole-value clone for a scalar/list context |
| CPP-P25 front-end allocation | **partly implemented**: operator lookup by first byte (was a scan of 31), ASCII token slices without the encoder, parser tokens by `const&` — **0.94–0.95** on token-heavy sources (400k numbers 0.660 → 0.627 s; 800k-term chain 0.918 → 0.870 s). **Rejected:** reserving the literal buffer (unsafe: the closing quote is unknown, `to - start` would over-allocate per literal). **Deferred:** `string_view` tokens / dropping the 4-byte-per-byte `chars_` (memory 78 MB per 100k numbers) — needs a front-end redesign for a memory, not time, gain | | | throughput ≈ 0.4–0.6 µs per token |
| round-2 lead: `opt_pipeline_op` | **rejected** | 7.3 M calls ≈ 1.4% of Scenario 1 | | a ceiling below the 2% noise floor; a spec pointer compare would save ≤1% |
| round-2 lead: non-throwing nested `??` | **implemented** (`probe_eval`: a small tree of operators over names and literal-key indexes reports a miss instead of throwing it; everything else runs in `eval_node` inside it, so the outer catch still sees what they throw; depth is shifted by the skipped levels so `E_DEPTH` lands on the same node; `eval_binary`/`eval_unary` split into `apply_binary`/`apply_unary` so the coercions are the same code) | `MAP(L, (_["k"] & "x") ?? 0)` 100k rows: 1.044 s (≈10 µs a row, one exception each) | 0.072 s (**0.07**, 14×) | 6,000 random `??`/`???` programs (values, coercion errors, misses, effects, calls, `AND`, `IF`) and 392 depth-boundary programs against the previous binary: 0 diffs |

**Tests added:** `cpp/tests/unit.cpp` `test_round3_fast_paths` (RECORD literal/duplicate/dynamic keys and error order; FIND/REPLACE/SPLIT incl. multi-byte and a linear-time bound skipped under ASan; PAD/REPEAT cases; DISTINCT/DEDUPE incl. growth past the initial table; >4-argument calls; 15 nested-`??` cases incl. coercion errors, effects and the "a miss never runs the right operand" rule) and `cpp/tests/sql_unit.cpp` (execute_hybrid isolation: helper assignment, indexed write, copy of a variable, unassigned variable read, scalar context). **Tools:** `tools/perf/cpp/bench.py` scenarios p21–p25, `sqlbench.cpp` modes `plan` and `hybrid`.

**Verification (final tree):** decimal oracle 94,040 cases / 0 mismatches; `sqlunit` green; `sqlt` green (1309 under ASan); `make -C cpp tsan`, `tsan-registry` and `tsan-regex` green. **ASan:** `unit-asan` 751/751, `sqlunit-asan` and `sqlt-asan` green, and `conformance-asan` run per file (8 files at a time) passes every file, except that `conformance/31-audit-findings.selt` was run under ASan without its three 8M-code-point cap cases (`text.replace.cap-*`, `text.rreplace.cap-*`: they did not finish in 25 minutes under ASan on this loaded box; the other 19 cases, including the three `join.order.*`, pass under ASan in 0.06 s, and all 22 pass in the plain build in 1.4 s). **Join-prefilter error order (coordinator item, peer finding; a correctness fix, not a speed-up):** on the deep prefilter path of `do_link` (FILTER handed down through a LINK whose left is itself a LINK/FILTER, a MAP after it) the right source is evaluated first as an optimisation. If it raises a SEL error, the left source (pure) is now evaluated as written and ITS error wins; only when that evaluates cleanly does the right source's error stand (eval depth is restored by `eval_node`'s RAII while the exception unwinds). The three `conformance/31-audit-findings.selt` `join.order.*` cases that reported 1:76/1:84/1:89 now report 1:38. Checked: conformance 2134/2134, unit 771/771 (plain-vs-optimised clean), join-filter oracle (mixed seed 54001 / uniform 54002, `SEL_IMPLS="js cpp"`, 1500 pairs each: 0 cross-host and 0 as-written disagreements), join-rows oracle 2000 programs 0 wrong, TSan lanes clean. **Test-lane note:** `unit` now skips `28b-regex-ambiguity` in its plain-vs-optimised pass (it ran the validator at its caps dozens of times; over an hour under ASan; conformance covers it) and skips `31-audit-findings` under ASan only (three cases build 8M-code-point texts, ~100× slower there); the serial `make asan` conformance step takes tens of minutes under load because of those cases, so run it per file in parallel as above.
