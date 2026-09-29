# CPP performance

[Worklist](../README.md) · [Performance protocol](../07-performance.md)

Report measurements below are historical evidence, not verified targets for this machine. Recreate each workload in the repository; scratchpad paths mentioned by reviewers are not dependencies. The shared performance protocol applies to every task, including reasoned/guessed opportunities and subitems bundled in a finding.

<a id="cpp-p1"></a>

## CPP-P1: Big-by-big decimal division is O(n*m*9) with a string allocation per step, and division falls off a cliff at about 28 digits

- [ ] **P-CPP-P1 — Measure and address this finding.**

Source: [CPP-P1](../../cpp-code-review.md). Report labels: [impact: high (DoS)] [measured].

**Source target:** `cpp/sel.cpp:649-660` (`divmod_abs` generic branch), reached from `dec_div` (1129-1132) and `dec_mod` (1172-1177); small path 1096-1128.

**Benchmark seed / reported evidence:** measured; cpp-2 box at load about 16, so absolutes are inflated 2-3x): `LEN(POWER(9, 100000) / POWER(7, 50000))` (95k / 42k digits): C++ 116.8 s, JS 0.23 s. `POWER(3,40000)/POWER(7,20000)`: 2.09 s vs 0.18 s. A quotient of 20k/10k digits takes 0.44 s (2x size -> 4.7x time). A 40-character program can pin a core for many minutes (caps allow 1M-digit operands). Realistic data: `dec_div` 30-digit/20-digit 58 us, 43-digit/20-digit 86 us, versus 210 ns for small operands. 200,000 `A / B` with those operands: C++ 16.8 s, JS 1.2 s; 200,000 `%` with a 61-digit dividend: C++ 19.4 s, JS 0.78 s.

**Implementation experiment:** Knuth algorithm D on the existing base-1e9 limbs (O(n*m/81), no allocation); keep the single-limb and power-of-ten fast paths; use it whenever the int128 path overflows, and for `dec_mod` above 38 digits. Byte-identical (exact integer division).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p2"></a>

## CPP-P2: Schoolbook multiplication with no early E_RANGE rejection

- [ ] **P-CPP-P2 — Measure and address this finding.**

Source: [CPP-P2](../../cpp-code-review.md). Report labels: [impact: high (DoS) / medium otherwise] [measured].

**Source target:** `cpp/sel.cpp:1057-1063` (`dec_mul` limb path: `dec_guard(dec_from_limbs(..., mul_limbs(la, lb), ...))`), 422-486 (`mul_limbs`), 488-555 (`sqr_limbs`), 1261-1271 (`dec_power`).

**Benchmark seed / reported evidence:** measured): `POWER(9999999999999999999999999999, 100000)` (28-digit base, doomed to E_RANGE): C++ 18.6 s, JS 1.1 s, Python 4.8 s. `A = POWER(99999999, 100000); A * A` (E_RANGE): 15.4 s vs 1.1 s. A legal 800k-digit `POWER(99999999, 100000)` takes 5.7 s vs 1.3 s in JS. `REPEAT("9",500000) * itself` 3.4 s vs 1.8 s.

**Implementation experiment:** in the limb path compute `min_digits = digits(a) + digits(b) - 1` and `fail("E_RANGE")` before multiplying if `min_digits - (sa + sb) > MAX_INT_DIGITS` or `sa + sb > MAX_FRAC_DIGITS`; do the same in `dec_power` (estimate `digits(base)*n`); add a Karatsuba threshold (about 64 limbs). Error code and position unchanged.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p3"></a>

## CPP-P3: SQL `IN` / `ANY` / `JOIN` literal lists translate in O(N^2) (`fold_pairwise` re-splices the accumulator through `Emit::fill`)

- [ ] **P-CPP-P3 — Measure and address this finding.**

Source: [CPP-P3](../../cpp-code-review.md). Report labels: [impact: high] [measured].

**Source target:** `cpp/sel_sql_translator.cpp:1389-1402` (`fold_pairwise`), 1808 (`IN` list), 2680 (`aggregate` unroll), 2802 (`JOIN`); helpers `apply` (:1333), `Emit::fill` (`cpp/sel_sql_emit.cpp:244-351`, `splice` lambda copying every part), `Fragment` copy semantics.

**Benchmark seed / reported evidence:** measured): cpp-6 (driver, -O1): `ANY((0..n-1), _ == X)` n=1000 103 ms, 4000 1.59 s, 16000 34.4 s (357 KB output); `X IN (0..n-1)` n=1000 133 ms, 4000 2.5 s, 16000 33.7 s (1.6 MB); exponent about 1.9. cpp-7 (-O2, mariadb): `ID IN (0..n-1)` n=1000 78 ms, 5000 2.06 s, 20000 43.4 s (text list: 91 ms / 2.06 s / 46.5 s). gprof at n=4000: 49% in the `push` lambda of `fill` (16M calls), 14% `vector<Part>::push_back`, 13% `fold_pairwise`, 8% `Fragment` copy ctor (48k copies). Compile is 8-17 ms at n=20000, so translation is the whole cost. JS shows the same curve (16,000 items: 8.7 s).

**Implementation experiment:** byte-identical linear build: resolve the operator template once; if it has the shape `pre{0}mid{1}post` (all shipped AND/OR/+/& entries do), emit all `pre`s first in reverse order, then item0, then `mid_i item_i post_i` (a single vector appended in place); fall back to the current loop otherwise. At the very least `std::move` `acc` into the pair and use a span over the two operands. Note the unrolled output nests N levels deep and `fold_pairwise` is outside the `node()` depth guard, so servers with parser recursion limits may reject long IN lists (reasoned, not tested against a DB); a documented cap is worth considering.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p4"></a>

## CPP-P4: `??` / `???` on a missing key cost about 20 microseconds each (implemented with a C++ exception)

- [ ] **P-CPP-P4 — Measure and address this finding.**

Source: [CPP-P4](../../cpp-code-review.md). Report labels: [impact: medium-high] [measured].

**Source target:** `cpp/sel.cpp:3476-3484` (`eval_binary`, `try { eval_node(l) } catch (SelError&)` around E_NO_KEY / E_UNDEF_VAR).

**Benchmark seed / reported evidence:** measured): `L = SPLIT(REPEAT("a,",300000),","); COUNT(MAP(L, (_["k"] & "x") ?? 0))` = 6.09 s CPU (20 us per miss) vs 0.27 s for the non-throwing `HAS(_, "k")` equivalent; JS is 8.5 s, so this is not C++-only, but C++ has the most to gain.

**Implementation experiment:** when the left operand is a `Var` or an `Index` chain over `Var`/`Text` keys (no calls), evaluate it with a non-throwing probe (walk the chain, return "missing" instead of `fail`) and go straight to the right side; keep try/catch for anything else. Results and positions identical, since a pure chain can only fail with those two codes.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p5"></a>

## CPP-P5: Operator dispatch by string comparison and per-evaluation literal allocation dominate small-predicate loops

- [ ] **P-CPP-P5 — Measure and address this finding.**

Source: [CPP-P5](../../cpp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `cpp/sel.cpp:3463-3534` (`eval_binary`: up to about 15-17 `std::string == const char*` tests, then `is_compare_op(Token{Tok::Op, op, {}})` which constructs a Token and does a `std::set<std::string>` lookup, then up to 6 more compares in `compare_result`), 2714-2716, 3364-3375, 3702-3711 (`Num`: `Internals::from_dec(*node.dec)` copies the `Dec` and heap-allocates a new one per evaluation; `Text`: `make_text(node.s)` copies per evaluation). `as_dec` returns a 96-byte `Dec` by value, twice per operator.

**Benchmark seed / reported evidence:** measured): gprof of `COUNT(FILTER(L, _ > 5 AND _ < 900000))` over 1M elements: 39,000,100 calls to `operator==(string const&, char const*)` for 8,000,004 node evaluations (about 9% of self time), 2M `std::set::find` and 6M `Dec` copy constructions. Experiment (scratchpad `cpp3/exp`, not in repo; -O2, best of 5 CPU seconds, box shared so +/-15%): cache an opcode on the Node and cache literal `Value`s on the Node: `COUNT(FILTER(L, _K > 5))` 1.66 s to 1.04 s; `COUNT(FILTER(L, _ > 5 AND _ < 900000))` 1.54 to 1.22 s; `SUM(L, _*2+1)` 1.29 to 1.09 s (list construction of about 0.55-0.8 s is included in each figure). cpp-2: `MAP(XS, _ > _)` about 700-870 ns/element vs about 430-510 for `_ + 1` (compiled plan); the dispatch share was not isolated (guess).

**Implementation experiment:** resolve the operator to an enum once in the parser/optimiser (stored in `Node`) and switch on it; hold a shared immutable `Value` for Num/Text/Bool/Null literal nodes (safe: `=`/`,`/aggregates clone what they keep). In `Context` rather than the shared AST if a non-atomic `ref_count` would be shared across threads (cpp-2's caution). Route comparison through a `dec_cmp`-returning plan op or the math plan. Byte-identical output.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p6"></a>

## CPP-P6: 128-bit divisions by 10 in hot formatting and rounding paths

- [ ] **P-CPP-P6 — Measure and address this finding.**

Source: [CPP-P6](../../cpp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `cpp/sel.cpp:576-585` (`dec_digits_from_magnitude`), 866-902 (`dec_format_buf`), 927-938 (`dec_trim_scale`), 1112-1120 (`dec_div` exact case), 1192-1199 (`dec_round`), 1275-1284 (`dec_to_int`), 4530-4533 (join key trim loop).

**Benchmark seed / reported evidence:** measured): `dec_format` of `12345.67` 284 ns, `dec_format_buf` 127 ns, `dec_add` 52 ns, `dec_mul` 47 ns, `dec_div` small 210 ns.

**Implementation experiment:** when the magnitude fits in 64 bits (almost always) use `uint64_t` division or a two-digit table; use `dec_format_buf`-style output in `dec_format` too; count trailing zeros in 64 bits for the exact-division trim.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p7"></a>

## CPP-P7: `LIST` / `RECORD` (and `,`, `=`, MAP collection) deep-clone freshly built temporaries

- [ ] **P-CPP-P7 — Measure and address this finding.**

Source: [CPP-P7](../../cpp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `cpp/sel.cpp:5155-5168` (`LIST`: `a.val(i).clone()`, `RECORD`: `a.val(i + 1).clone()`), 3423ff (`eval_list` clones every collected element), 1522-1554 (`clone_at`).

**Benchmark seed / reported evidence:** measured, cpp-5): 300k-row `MAP(S, RECORD("id",_K,"g",_K,"v",_K))` takes 1.2-1.5 s; wrapping it in one `LIST(...)` adds 0.5-0.65 s, and each further wrapper the same (`LIST(LIST(LIST(LIST(MAP(...)))))` 2.7 s). Typical programs do `RECORD("rows", FILTER(...), ...)`. cpp-2: `MAP(XS, 5000)` costs 300-400 ns per element more than `MAP(XS, _)` (literal materialisation + clone + free).

**Implementation experiment:** give `Args` a `take_val(i)` that moves the value out of `vals_[i]` and, if the Impl's refcount is 1 and it is scalar-only or uniquely owned, returns it without clone; clone only when shared. The same test applies to the assignment store in `eval_assign` (`eval_node(rhs).clone()`) and to `,`. Byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p8"></a>

## CPP-P8: Every numeric Value costs two allocations and 192 bytes

- [ ] **P-CPP-P8 — Measure and address this finding.**

Source: [CPP-P8](../../cpp-code-review.md). Report labels: [impact: medium] [measured for the pieces, reasoned for the total].

**Source target:** `cpp/sel.hpp:133-140` (`Dec` is 96 bytes: string + vector + int128, all always present), `cpp/sel.cpp:1407-1413` (`from_dec` = `Impl` + `make_unique<Dec>`).

**Benchmark seed / reported evidence:** `tools/benchmark-cpp-value.cpp`: an integer Value retains 192 bytes vs 80 for text; 200k integers build in 39 ms; `Value::integer` 110 ns (2 mallocs). `SUM(XS, _)` 130 ns/element, `MAP(XS, _ + 1)` 430 ns, `MAP(XS, _ > 5000)` 930 ns/element (loaded box).

**Implementation experiment:** shrink `Dec` for the small case (union of mantissa / heap bignum) so the number payload lives inside `Impl`; keep the compact scalar header the prior work protected.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p9"></a>

## CPP-P9: Text builtins decode the whole string to `vector<char32_t>` even when a byte scan would do

- [ ] **P-CPP-P9 — Measure and address this finding.**

Source: [CPP-P9](../../cpp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `cpp/sel.cpp:3883` (`cps_of`) used by `LEN`, `LEFT`, `RIGHT`, `SUBSTR`, `CODE`, `TRIM`/`LTRIM`/`RTRIM`, `UPPER`/`LOWER` (`ascii_case`, 6162), `BACKWARDS`, `FIND`, `REPLACE`, `SPLIT`.

**Benchmark seed / reported evidence:** measured, 40 MB text, `BLEN(f(S))` minus base 277 ms, noisy): `UPPER` +1320 ms, `BACKWARDS` +1200, `TRIM` +870, `LEFT(S,3)` +500, `LEN` +425 (vs `BLEN` +35). For short strings about 0.5 us per call (`FILTER(L, LEN(_)==4)` over 1M strings 1.0-1.7 s vs `BLEN` 1.0-1.5 s).

**Implementation experiment:** byte fast paths as above; byte-identical since input is already valid UTF-8.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p10"></a>

## CPP-P10: Sort comparator re-classifies both keys on every comparison

- [ ] **P-CPP-P10 — Measure and address this finding.**

Source: [CPP-P10](../../cpp-code-review.md). Report labels: [impact: medium] [measured baseline, reasoned cause].

**Source target:** `cpp/sel.cpp:5570-5610` (`compare_values`), 5745 (`stable_sort`).

**Benchmark seed / reported evidence:** 200k numeric-text keys, `SORT(X)` costs about 620 ms, roughly 3 us/element, about 175 ns per comparison (17.6 comparisons/element). Text-only keys are cheaper (about 2 us/element).

**Implementation experiment:** decorate once per key (O(n)) into `SortEntry {class, const Dec*/string_view}` and compare the decorated keys. Same total order as today; combine with the C43 fix so the order is defined once.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p11"></a>

## CPP-P11: `TOP*` with a large N is 2-3x slower than the full sort it replaces

- [ ] **P-CPP-P11 — Measure and address this finding.**

Source: [CPP-P11](../../cpp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `cpp/sel.cpp:5790-5865` (`do_top`, heap + a `compare` that string-compares `direction == "DESC"` on every call).

**Benchmark seed / reported evidence:** measured; 200k pseudo-random numeric texts; data build 388 ms): `SORT(X)` +620 ms; `TOP(X,10)` +60; `TOP(X,1000)` +96; `TOP(X,20000)` +570 (about a full sort); `TOP(X,100000)` +1220 (2x a full sort). On ascending data with DESC (every element displaces the root) `TOP_DESC(X,1000)` +630 vs `SORT_DESC` +250.

**Implementation experiment:** hoist `const bool desc` out of the lambda; when `k * 8 >= source_size` fall back to `stable_sort` + truncate (same output, as idx breaks ties); use a hole-based sift instead of swaps.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p12"></a>

## CPP-P12: Bare `BUCKET(list, key)` is allocation-heavy per group (about 3.5 us/group)

- [ ] **P-CPP-P12 — Measure and address this finding.**

Source: [CPP-P12](../../cpp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `cpp/sel.cpp:5895-5960` (`do_bucket`).

**Benchmark seed / reported evidence:** measured): 200k unique numeric-text keys: `BUCKET(X, _)` 1168 ms vs `BUCKET(X,_,COUNT(_))` 888 ms vs `DISTINCT(X)` 483 ms, generation baseline 273 ms; 6 groups: 402 ms (cost is per group, not per row).

**Implementation experiment:** for the bare form group on `key_str` with one `unordered_map<string,size_t>` (this is the C39 fix); build the result record with `Internals::with_children` in one shot (keys unique by construction); `reserve` `groups`; build the record `index` lazily.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p13"></a>

## CPP-P13: Each parenthesised group shallow-copies its node, copying the whole `items` vector

- [ ] **P-CPP-P13 — Measure and address this finding.**

Source: [CPP-P13](../../cpp-code-review.md). Report labels: [impact: medium, only on wide nested lists] [measured].

**Source target:** `cpp/sel.cpp:3117-3119` (`auto copy = std::make_shared<Node>(*inner); copy->grouped = true;`).

**Benchmark seed / reported evidence:** measured): `(` x90 + a 200,000-element list + `)` x90 (1.29 MB) compiles in 1.05 s; the same list under one paren pair in 0.14 s (parse stage 1307 ms vs about 90 ms in the instrumented copy). About 7x on that shape, negligible on ordinary rules.

**Implementation experiment:** make `parse_sequence()`/`parse_list` return `std::shared_ptr<Node>` and set `grouped = true` in place, or use the same `const_cast` justification `~Node` documents (valid for a fresh `make_shared<Node>`). The copy preserves `pos`, so positions do not change.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p14"></a>

## CPP-P14: Every SQL `translate()` copies the whole `Bindings` and scans all of them

- [ ] **P-CPP-P14 — Measure and address this finding.**

Source: [CPP-P14](../../cpp-code-review.md). Report labels: [impact: medium] [measured].

**Source target:** `cpp/sel_sql.cpp:180` (`Translator t(dialect, bindings, options)` takes `Bindings` by value), `cpp/sel_sql_translator.cpp:300-304, :323` and `cpp/sel_sql_stage1.cpp:466-478` (`const_scope` calls `names()` then `get()`, each allocating an upper-cased key, for every binding to find the scalar `value` ones).

**Benchmark seed / reported evidence:** measured, -O2): translating `COL0 > 5 AND COL0 < 10` 20,000 times: 2 bindings 17.9 us/call, 51 bindings 34.9 us, 501 bindings 278.7 us (15x slower for an identical rule).

**Implementation experiment:** hold `const Bindings&` (or `shared_ptr<const Bindings>`) in the Translator; build the const scope lazily, only for the names in the program's `dependencies()`, or cache the value-binding list inside `Bindings`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p15"></a>

## CPP-P15: `evaluate(source, ctx)` pays the optimiser on every call and it costs about as much as parsing

- [ ] **P-CPP-P15 — Measure and address this finding.**

Source: [CPP-P15](../../cpp-code-review.md). Report labels: [impact: medium for one-shot use] [measured].

**Source target:** `cpp/sel.cpp:8114-8117` (`physical_ast` built on first `run`), 8137 (`evaluate` = `compile(source).run(...)`).

**Benchmark seed / reported evidence:** measured, `scratchpad/cpp3/src/bench1.cpp`, 100k iterations; compile / run / compile+run): `TOTAL > 10 AND STATUS $== "open"`: 2.15 / 0.93 / 5.19 us (optimiser about 2.1 us). `ROUND(PRICE*QTY*(1+TAX/100)-DISC,2) > 100`: 6.18 / 1.37 / 14.14 us (about 6.6 us). `COUNT(FILTER(ITEMS, ...)) >= 1`: 5.23 / 3.95 / 18.99 us (about 10 us). The header's rationale ("the copy costs more than evaluating a small rule") is right, but the convenience `evaluate()` ends up 2-3x slower than parse+run.

**Implementation experiment:** have `evaluate()` run the un-optimised `ast_` (or optimise lazily from the second `run` on a Program). Results identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p16"></a>

## CPP-P16: Per-call regex overhead for literal patterns, and the subject is converted twice at 4-8x size

- [ ] **P-CPP-P16 — Measure and address this finding.**

Source: [CPP-P16](../../cpp-code-review.md). Report labels: [impact: medium] [reasoned + measured deltas].

**Source target:** `cpp/sel.cpp:6831-6888` (`compile_regex`, flag decode, key string, global mutex + `std::map` lookup), 6890-6900 (`cps_of(...)` -> `vector<char32_t>` -> `std::u32string(subject.begin(), subject.end())`), 6962-6970 (same in RREPLACE).

**Benchmark seed / reported evidence:** 1M `RMATCH` calls about 0.9 us each; the `i` flag adds about 0.5 us for the pattern decode (measured, noisy). `RMATCH("^abc", S)` on 40 MB takes 1170 ms vs `LEN(S)` +425 ms; the difference is the second copy plus SRELL trying the `^` anchor at every start offset (the second part is a guess, not measured separately).

**Implementation experiment:** literal patterns are known at compile time (C28): validate and compile once when the `Program` is built and keep a `shared_ptr<const Regex>` on the call node. Decode the subject straight into a `u32string` (`decode_utf8_to(std::u32string&)`).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p17"></a>

## CPP-P17: `node_contains_var` re-scans and allocates on every aggregate call

- [ ] **P-CPP-P17 — Measure and address this finding.**

Source: [CPP-P17](../../cpp-code-review.md). Report labels: [impact: low-medium] [measured by call count].

**Source target:** `cpp/sel.cpp:4430` (called at 5553, 5701, 5822, 5903, 5968). Each visited `Var` node builds two upper-cased `std::string`s (`upper_name(node.s)` and `upper_name(std::string(wanted))`).

**Benchmark seed / reported evidence:** gprof of `COUNT(MAP(L, SUM(LIST(1), _+_+...(40 terms))))` over 100k rows: `upper_name` called 8,000,160 times (80 per inner aggregate call), the top self-time entry (15%), and with the string constructors/destructors about 25% of the run. Matters for small inner lists (nested aggregates); negligible for big lists. cpp-5 measured an inner `ANY(LIST(1), TRUE)` at about 0.4 us per call in total.

**Implementation experiment:** compute "reads `_K`" once per body node at parse/optimise time (a bool on Node) or compare case-insensitively without allocating; the same change fixes C6.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p18"></a>

## CPP-P18: RREPLACE re-parses the replacement and makes four temporaries per match

- [ ] **P-CPP-P18 — Measure and address this finding.**

Source: [CPP-P18](../../cpp-code-review.md). Report labels: [impact: low-medium] [measured].

**Source target:** `cpp/sel.cpp:6902-6930` (`expand_replacement`), 6980-6990.

**Benchmark seed / reported evidence:** measured): `RREPLACE("a","bb",S)` with 5M matches in 40 MB: 2.5 s (about 0.4 us/match); per element on 1M short strings RREPLACE about 2.4 us vs RMATCH about 0.9 us.

**Implementation experiment:** pre-parse `repl` once into segments (literal u32 chunks and group numbers) and append to the u32 output directly. Do NOT validate `$n` against `mark_count()` up front: the error must fire only when a match happens.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p19"></a>

## CPP-P19: Base64 decode uses `strchr` per input character; encode appends char by char

- [ ] **P-CPP-P19 — Measure and address this finding.**

Source: [CPP-P19](../../cpp-code-review.md). Report labels: [impact: low-medium] [measured].

**Source target:** `cpp/sel.cpp:6420-6423` (`b64_index`), 6483-6498, 6500-6538.

**Benchmark seed / reported evidence:** 40 MB decode about +1.1 s vs encode +0.6 s. A 256-entry constexpr table plus `reserve` should be several times faster (estimate, not measured).

**Implementation experiment:** 256-entry lookup table; `reserve` the output.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p20"></a>

## CPP-P20: `FILTER` result always materialises string keys

- [ ] **P-CPP-P20 — Measure and address this finding.**

Source: [CPP-P20](../../cpp-code-review.md). Report labels: [impact: low-medium] [measured].

**Source target:** `cpp/sel.cpp:5735-5749`.

**Benchmark seed / reported evidence:** `FILTER(A, cond)` over 200k rows adds about 150 ms (about 0.75 us/kept row) vs `MAP(A,_)` about free.

**Implementation experiment:** detect "kept keys are exactly 1..k in order" while walking (source packed and no gap) and return `Value::list(items)`; fall back otherwise. Byte-identical dump.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p21"></a>

## CPP-P21: `RECORD` with literal keys builds an intermediate record, then a second shaped Value

- [ ] **P-CPP-P21 — Measure and address this finding.**

Source: [CPP-P21](../../cpp-code-review.md). Report labels: [impact: low-medium] [measured; reasoned split].

**Source target:** `cpp/sel.cpp:5170-5186`.

**Benchmark seed / reported evidence:** `MAP(S, RECORD("id",_K))` +0.83 us/row over `MAP(S,_K)`; three fields about 3 us/row vs `LIST(_K,_K)` 0.75 us/row (200k rows). Reasoned that about half is the intermediate structure.

**Implementation experiment:** if `a.record_shape()`: `storage.reserve(n)`; for each pair evaluate the (literal) key, then `storage.push_back(a.val(2i+1).clone())`; then `Internals::shaped(shape, storage)`. Keep the general path for dynamic keys. Same evaluation order.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p22"></a>

## CPP-P22: Naive O(n*m) substring search, pad and repeat loops

- [ ] **P-CPP-P22 — Measure and address this finding.**

Source: [CPP-P22](../../cpp-code-review.md). Report labels: [impact: low] [measured / reasoned].

**Source target:** `cpp/sel.cpp:3885-3898` (`index_of_cp`), 6183-6203 (`pad`), 6314-6320 (`REPEAT`).

**Benchmark seed / reported evidence:** measured): `FIND(REPEAT("a",20000) & "b", REPEAT("a",200000))` takes 4.2 s (4e9 compares); `REPLACE`/`SPLIT` share it. `PADL("x",20000000,"ab")` 1.2 s; `REPEAT` appends without `reserve` (40 MB in about 280 ms).

**Implementation experiment:** byte-level `memmem` / `std::string::find` (glibc two-way); `reserve` in `pad` and `REPEAT`. See C8 for the caps.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p23"></a>

## CPP-P23: `DISTINCT` uses `unordered_map<uint64_t, vector<Value>>` (one node and one vector block per distinct value)

- [ ] **P-CPP-P23 — Measure and address this finding.**

Source: [CPP-P23](../../cpp-code-review.md). Report labels: [impact: low] [reasoned].

**Source target:** `cpp/sel.cpp:5335-5352`. 200k unique numeric texts: about 1.1 us/element.

**Implementation experiment:** an open-addressing table of `uint32_t` indices into `out` (compare with `eql` against `out[idx]`) removes two mallocs per distinct value.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p24"></a>

## CPP-P24: Small per-call and per-run allocation and hybrid-planner overheads

- [ ] **P-CPP-P24 — Measure and address this finding.**

Source: [CPP-P24](../../cpp-code-review.md). Report labels: [impact: low] [reasoned; one guessed].

**Source target:** - `Args` allocates a `std::vector<std::optional<Value>>` per call node evaluation (`cpp/sel.cpp:3279`); use an inline array for at most 4 arguments. - `Context` starts the math scratchpad at 32 default-constructed `Dec` (about 100 bytes each, about 3 KB) on the first plan of every `Program::run` (3761); size it to the plan, or pool it. - `plan_hybrid` re-normalises and re-translates the same tree up to (steps + 3) times (`cpp/sel_sql_hybrid.cpp:760-766, 789-793, 806, 815-846`, `Helpers::tables` 500-503). Measured negligible for short pipelines (160 filters after an unsupported step: 2 ms) but it doubles the cost for C17-style inputs. Cache the `normalise` result per (tree, scope); try prefixes from the first refusal position. - `execute_hybrid` deep-clones the caller's whole context per call (`cpp/sel_sql_hybrid.cpp:868`; guessed). A shallow top-level copy gives the same isolation for top-level assignments (see C54). - `is_constant`/`validate` re-run at each compound node, and `binary`/`guard_numeric`/`coerce_scale_limits` each call them again; `SNode::to_node` deep-copies per call (`cpp/sel_sql_translator.cpp:408-427`). O(depth x size) for ordinary chains (reasoned, not measured); cache `{is_constant, validated}` by `const SNode*` per translation. - `Emit::fill` appends template text one char at a time via `push(tpl.substr(i,1))` (`cpp/sel_sql_emit.cpp:244-351`); `text_operand` copies `f.parts_` before the cast overwrites it (:228); `classify` deep-copies `RelationSpec` per aggregate (translator.cpp:2390); `Map::lexical`/`chain`/`shipped` allocate per lookup (`cpp/sel_sql_map.cpp:717-777`, under 2% of the time in gprof at n=4000, so only relevant after P3 is fixed).

**Implementation experiment:** as listed per bullet; all byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="cpp-p25"></a>

## CPP-P25: Front-end allocation and copy overheads

- [ ] **P-CPP-P25 — Measure and address this finding.**

Source: [CPP-P25](../../cpp-code-review.md). Report labels: [impact: low] [reasoned; front-end measured].

**Source target:** - Token stream and AST allocate about 60-170 bytes of heap per source byte (`Lexer` `CodePoints chars_` = 4 bytes/byte, `Token` holds a `std::string`, `make_shared<Node>` about 200-230 B, a full `Dec` per number literal; `cpp/sel.cpp:2360-2400, 2798-2803, 3075-3079`). RSS delta after `compile()` (measured): 1.26 MB list of 200k numbers gives 74 MB; 0.6 MB `A[1][1]...` gives 98 MB; 0.34 MB `A0*2 + ...` gives 43 MB. The spec has no source-size limit. Throughput about 10 MB/s (7-12 us per small rule). - `Token` is copied by value at each parse step (`cpp/sel.cpp:2888, 2970, 3068`), and text literals are encoded code point by code point (`lex_raw`/`lex_quoted`, 2475-2531). A 5 MB string literal compiles in about 60 ms (measured); using `const Token&`, moving `t.value`, and appending raw byte ranges for escape-free stretches removes about half. - `match_operator` scans a 31-entry table with `std::string` compares per operator token and returns a `std::string` (2456-2469); flat `A[1][1]...` (600k tokens) lexes at about 93 ms (about 150 ns/token, measured). A switch on the first code point would avoid the allocation. - `dec_cmp` copies and scales limbs when scales differ, and `dec_round` etc. rebuild strings for huge scales (`cpp/sel.cpp:1074-1088, 1201-1207`); a digit-count pre-check settles most comparisons without allocation (reasoned).

**Implementation experiment:** only worth doing after P13 and C18: keep byte offsets into the source and build tokens as `string_view`s; drop `chars_` after computing line starts.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.
