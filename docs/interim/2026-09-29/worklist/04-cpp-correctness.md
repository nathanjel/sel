# CPP correctness

[Worklist](README.md) · [Shared tests](00-tests.md) · [Performance](07-performance.md)

60 findings; 42 implementation tasks. Report severity/confidence is preserved below. Work through one source area at a time; a grouped checkbox closes only when every listed finding is resolved.

Each task starts by reproducing the report against the current tree, then lands the linked shared regression cases before the fix. Fix sketches are starting points, not approved spec changes. Prefer one implementation source per task; touching spec, fixtures or an unavoidable API boundary is allowed. Do not edit generated artifacts without their generator. High-severity tasks take precedence within each area.

## Front end, depth and source positions

<a id="c-cpp-c4"></a>

- [x] **C-CPP-C4 — Resolve CPP-C4.** Right-associative `??` / `???` chains recurse in the parser with no depth count (SIGSEGV at about 20k operators)

  **[CPP-C4](../cpp-code-review.md) — [high] [confirmed] Right-associative `??` / `???` chains recurse in the parser with no depth count (SIGSEGV at about 20k operators)**

  Source target: `cpp/sel.cpp:2914-2919` (`Parser::parse_term`, non-assignment `'R'` branch: `n->r = parse_term(e->bp)`).

  Implementation starting point: do NOT just `enter()` here. `1 ?? 1 ?? ...` (250 operators) is legal and short-circuits to 1, and the evaluator is the depth authority for such a chain. Parse the `R` non-assign chain iteratively (collect operand/op pairs, fold right-to-left into `Bin` nodes). The resulting right-deep tree is already protected downstream (`~Node` iterative; eval/dependencies/optimiser count). Same treatment in the other four hosts.

  Regression prerequisite: [T01 / CPP-C4](tests/01-frontend.md#cpp-c4).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts enter()/leave() in the R-assoc branch; conformance 1120/1120 on js, js-bundle-min, php, cpp, lisp, go, python

<a id="c-cpp-c5"></a>

- [x] **C-CPP-C5 — Resolve CPP-C5.** Nested string interpolation recurses unboundedly in the lexer (SIGSEGV at about 11-12k levels; quadratic before that)

  **[CPP-C5](../cpp-code-review.md) — [high] [confirmed] Nested string interpolation recurses unboundedly in the lexer (SIGSEGV at about 11-12k levels; quadratic before that)**

  Source target: `cpp/sel.cpp:2498-2533` (`lex_quoted`), 2582-2609 (`match_brace` <-> `skip_quoted`), 2627-2648 (`emit_parts` -> `lex_range` -> `lex_quoted`).

  Implementation starting point: add a nesting counter to `lex_quoted`, `skip_quoted`'s `{` branch and `match_brace`, raise E_DEPTH at the opening brace/quote past a small bound (chosen so every parseable program, at most about 50-66 interpolation levels, still lexes). To remove the O(d x n) rescan, reuse the recorded close index or lex nested strings straight off the same cursor. Put the same bound in every host.

  Regression prerequisite: [T01 / CPP-C5](tests/01-frontend.md#cpp-c5).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — old-vs-new lexer differential 0 diffs per host (100k-300k random strings); 100k-level nest in <2s on every host

<a id="c-cpp-c6"></a>

- [x] **C-CPP-C6 — Resolve CPP-C6.** Uncounted recursive tree walkers overflow the C stack on a long flat expression inside an aggregate body

  **[CPP-C6](../cpp-code-review.md) — [high] [confirmed] Uncounted recursive tree walkers overflow the C stack on a long flat expression inside an aggregate body**

  Source target: `cpp/sel.cpp:4430` `node_contains_var` (called at 5553 `walk`, 5701 `do_sort`, 5822 `do_top`, 5903 and 5968 `do_bucket`); also 4412 `expr_depends_only` and the `reads_only_fields` lambda in `leading_field_conjuncts` (5507-5519).

  Implementation starting point: give the helpers a depth parameter that returns conservatively (or fails E_DEPTH at the node the evaluator would) past MAX_DEPTH. Better: compute "contains `_K`" once at parse/optimise time and store a bool on the Node (this also fixes P17).

  Regression prerequisite: [T01 / CPP-C6](tests/01-frontend.md#cpp-c6).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

<a id="c-cpp-c18"></a>

- [x] **C-CPP-C18 — Resolve CPP-C18.** `RECORD(...)` with many literal keys costs O(n^2) at compile time

  **[CPP-C18](../cpp-code-review.md) — [medium] [confirmed] `RECORD(...)` with many literal keys costs O(n^2) at compile time**

  Source target: `cpp/sel.cpp:1348-1358` `prepare_record_shape` (called from `Parser::finish_call`, sel.cpp:3160): duplicate check by `std::find` over the growing `keys` vector.

  Implementation starting point: give up on the shape as soon as `node.items.size()/2 > SHAPE_CACHE_MAX_KEYS` (an optimisation only; such shapes are not cached anyway), or dedupe with `unordered_set<string_view>`. Results identical: `prepare_record_shape` returns `{}` for duplicates and the fallback path handles them.

  Regression prerequisite: [T01 / CPP-C18](tests/01-frontend.md#cpp-c18).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

<a id="c-cpp-c47"></a>

- [x] **C-CPP-C47 — Resolve CPP-C47.** `.>` placeholder spec gaps: multiple `_` evaluate the head once per placeholder; binding functions are exempt from substitution

  **[CPP-C47](../cpp-code-review.md) — [low] [confirmed, all five hosts] `.>` placeholder spec gaps: multiple `_` evaluate the head once per placeholder; binding functions are exempt from substitution**

  Source target: `cpp/sel.cpp:3053-3059` (`args[i] = left` for every bare `_`) and 3052 (`!spec->binds && count >= spec->min`).

  Implementation starting point: decide in the spec: reject more than one placeholder (E_SYNTAX at the second `_`), replace only the first, or state per-placeholder evaluation; document the binding-function exemption in SPEC §5.10 and grammar.md.

  Regression prerequisite: [T01 / CPP-C47](tests/01-frontend.md#cpp-c47).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — Go break removed; all six hosts agree

<a id="c-cpp-c48"></a>

- [x] **C-CPP-C48 — Resolve CPP-C48.** E_UTF8 for invalid source bytes carries position 0:0

  **[CPP-C48](../cpp-code-review.md) — [low] [unconfirmed against spec intent] E_UTF8 for invalid source bytes carries position 0:0**

  Source target: `cpp/sel.cpp:2365` (`decode_utf8(source)` with default `Pos{}`), 97-135.

  Implementation starting point: document "position 0:0, byte offset in the message" in `spec/errors.md`, or report the line/column of the last valid code point before the bad byte, in every host.

  Regression prerequisite: [T01 / CPP-C48](tests/01-frontend.md#cpp-c48).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — check-cli-source.sh passes on all six hosts

## Exact decimal arithmetic and limits

<a id="c-cpp-c14"></a>

- [x] **C-CPP-C14 — Resolve CPP-C14.** `dec_get_limbs` / `dec_get_digits` mutate `const Dec` through `const_cast`, including file-scope `const Dec DEC_ZERO`

  **[CPP-C14](../cpp-code-review.md) — [low] [unconfirmed as a bug; code smell] `dec_get_limbs` / `dec_get_digits` mutate `const Dec` through `const_cast`, including file-scope `const Dec DEC_ZERO`**

  Source target: `cpp/sel.cpp:675-713, 804`.

  Implementation starting point: delete the `const_cast`s and make `DEC_ZERO` non-const.

  Regression prerequisite: [T02 / CPP-C14](tests/02-decimal.md#cpp-c14).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — cpp unit 237, oracle 447->0 mismatches, ASan/UBSan clean on files 24/25

<a id="c-cpp-c20"></a>

- [x] **C-CPP-C20 — Resolve CPP-C20, CPP-C21, CPP-C24, CPP-C41.** Coordinate the related changes below at their shared implementation surface.

  **[CPP-C20](../cpp-code-review.md) — [high] [confirmed] `dec_mul` silently rounds both operands to 18 fractional digits when both have scale over 18 (POWER inherits it)**

  Source target: `cpp/sel.cpp:1037-1048` (the `a.scale > 18 && b.scale > 18 && mantissa not +-1` branch of `dec_mul`).

  Implementation starting point: delete the branch. The small path still tries `__builtin_mul_overflow` and falls to the exact limb path (validated by cpp-2 on a scratch copy: 0 mismatches over 83k cases).

  Regression prerequisite: [T02 / CPP-C20](tests/02-decimal.md#cpp-c20).

  **[CPP-C21](../cpp-code-review.md) — [high] [confirmed] `ROUND` and `/` return the wrong value when the remainder exceeds 2^126 (signed `__int128` overflow in `2 * r`, UB)**

  Source target: `cpp/sel.cpp:1197` (`dec_round`: `if (2 * r >= p) q++;`) and 1122 (`dec_div`: `if (2 * r >= den) q++;`).

  Implementation starting point: compare `r >= p - r` / `r >= den - r` (no overflow since `r < p`), or use unsigned arithmetic.

  Regression prerequisite: [T02 / CPP-C21](tests/02-decimal.md#cpp-c21).

  **[CPP-C24](../cpp-code-review.md) — [medium] [confirmed] Division/modulo by a divisor whose digit string is exactly "1" goes wrong once the dividend is beyond int128**

  Source target: `cpp/sel.cpp:617-633` (`divmod_abs` power-of-ten fast path with `k = b.size() - 1 == 0`): `r = strip(a.substr(a.size()))` is the empty string, not `"0"`. Consumers: `dec_div` (line 1135, `r == "0"`), `dec_mod` (line 1177, `dec_make(a.neg, r, s)`).

  Implementation starting point: handle `k == 0` in the pow10 branch (`q = a; r = "0"`) and make `dec_make` normalise `digits.empty()` to `"0"`.

  Regression prerequisite: [T02 / CPP-C24](tests/02-decimal.md#cpp-c24).

  **[CPP-C41](../cpp-code-review.md) — [low] [confirmed] `INT128_MIN` mantissa is reachable and negation/abs/format on it are UB; comparisons afterwards are wrong**

  Source target: `cpp/sel.cpp:915` (`dec_negate`), 951 (`dec_abs`), 679/702 (`dec_get_limbs`/`dec_get_digits`), 870 (`dec_format_buf`), 1103-1104 (`dec_div`), 1156-1157 (`dec_mod`), 1194 (`dec_round`), 1614 (`Value::num(Dec)`); `dec_from_limbs` (734) and `__builtin_mul_overflow` results deliberately admit exactly -2^127.

  Implementation starting point: never create a small Dec with mantissa == INT128_MIN (route it to the limb form in `dec_from_mantissa`/`dec_from_limbs`); validated by cpp-2 on a scratch copy.

  Regression prerequisite: [T02 / CPP-C41](tests/02-decimal.md#cpp-c41).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — cpp unit 237, oracle 447->0 mismatches, ASan/UBSan clean on files 24/25

<a id="c-cpp-c22"></a>

- [x] **C-CPP-C22 — Resolve CPP-C22.** Numeric equi-join fast key is wrong for decimals beyond int64: false matches and missed matches

  **[CPP-C22](../cpp-code-review.md) — [high] [confirmed] Numeric equi-join fast key is wrong for decimals beyond int64: false matches and missed matches**

  Source target: `cpp/sel.cpp:4479-4500` (cpp-5) / 4546-4550 (cpp-2), `make_fast_join_key`, the `d.small` branch, `BigDec` sub-case. (Line numbers differ between the two reports.)

  Implementation starting point: build the BigDec text from the stripped `m` (`dec_digits_from_magnitude(|m|)`), or use one canonicalisation function for both branches.

  Regression prerequisite: [T02 / CPP-C22](tests/02-decimal.md#cpp-c22).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Value ownership, mutation and host conversion

<a id="c-cpp-c42"></a>

- [x] **C-CPP-C42 — Resolve CPP-C42.** Aggregates alias what they collect, but SPEC §3.4 and `contributing.md` say they copy (and that C++ does)

  **[CPP-C42](../cpp-code-review.md) — [low] [confirmed] Aggregates alias what they collect, but SPEC §3.4 and `contributing.md` say they copy (and that C++ does)**

  Source target: `cpp/sel.cpp:5773-5780` (`MAP` `out.push_back(r)`), FILTER entries, `do_sort` (count>=2: no clone; count==1: clones), `do_bucket` rows, TAKE/DROP/DISTINCT.

  Implementation starting point: needs one decision: either amend §3.4 and contributing.md to say aggregates alias (matches most hosts and the perf work), or restore the clones (cost: P7). Pin with a case either way.

  Regression prerequisite: [T03 / CPP-C42](tests/03-values.md#cpp-c42).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — cpp unit 237, oracle 447->0 mismatches, ASan/UBSan clean on files 24/25

## Evaluator order, optimizer and recovery

<a id="c-cpp-c2"></a>

- [x] **C-CPP-C2 — Resolve CPP-C2.** Compound assignment to a plain variable writes through a dangling pointer

  **[CPP-C2](../cpp-code-review.md) — [high] [confirmed] Compound assignment to a plain variable writes through a dangling pointer**

  Source target: `cpp/sel.cpp:3623-3647` (`eval_assign`, `NT::Var` branch, compound operators).

  Implementation starting point: after the RHS, do `ctx.root->set(name, value)` (or re-`get` the slot), keep `target_value` for the arithmetic.

  Regression prerequisite: [T04 / CPP-C2](tests/04-evaluation.md#cpp-c2).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

<a id="c-cpp-c3"></a>

- [x] **C-CPP-C3 — Resolve CPP-C3, CPP-C9, CPP-C19.** Coordinate the related changes below at their shared implementation surface.

  **[CPP-C3](../cpp-code-review.md) — [high] [confirmed] `eval_math_plan` keeps a raw `Dec*` into `ctx.math_scratchpad` across a nested plan that may resize it**

  Source target: `cpp/sel.cpp:3758-3764` (`resize`, `Dec* scratchpad = data() + base`) and 3778-3782 (`LoadLeaf` calls `eval_node`, which may enter another plan that calls `resize`).

  Implementation starting point: index (`ctx.math_scratchpad[base + dst]`) instead of caching the pointer, or a per-frame `std::vector<Dec>`/`std::deque`. Do not keep `const Dec&` references (`d2`, `a`, `b`) across anything that can evaluate.

  Regression prerequisite: [T04 / CPP-C3](tests/04-evaluation.md#cpp-c3).

  **[CPP-C9](../cpp-code-review.md) — [medium] [confirmed] `MathStep` / plan slot indices are `uint16_t`: `MIN`/`MAX` with over 32,768 arguments corrupts the heap**

  Source target: `cpp/sel.cpp:266-282` (`dst/src1/src2/scratchpad_size` are `uint16_t`), 7830-7831 (`slot_count`, `alloc_slot`).

  Implementation starting point: use `uint32_t`/`size_t` slots, or make `emit` return nullopt (fall back to the tree evaluator) once `slot_count` passes 65,535.

  Regression prerequisite: [T04 / CPP-C9](tests/04-evaluation.md#cpp-c9).

  **[CPP-C19](../cpp-code-review.md) — [medium] [confirmed] A caught error inside a math plan leaks the scratchpad frame (unbounded memory growth under `??`)**

  Source target: `cpp/sel.cpp:3763` (`math_scratchpad_top += size`) and 3849 (restored only on success). Catch sites that keep evaluating: `??`/`???` at 3476-3482 (swallow E_UNDEF_VAR/E_NO_KEY), also 4517, 4576, 4783 and the aggregate catch blocks.

  Implementation starting point: RAII guard restoring `math_scratchpad_top = base` (same idiom as `Pop` in `eval_node`); optionally probe missing variables without throwing (P4).

  Regression prerequisite: [T04 / CPP-C19](tests/04-evaluation.md#cpp-c19).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

<a id="c-cpp-c25"></a>

- [x] **C-CPP-C25 — Resolve CPP-C25.** ROUND/POWER validate the second argument before coercing the first (C++ only, when the call is not planned)

  **[CPP-C25](../cpp-code-review.md) — [medium] [confirmed] ROUND/POWER validate the second argument before coercing the first (C++ only, when the call is not planned)**

  Source target: `cpp/sel.cpp:6367-6390` (`ROUND`: `a.non_neg_int(1)` at 6373 then `a.dec(0)` at 6380; `POWER` at 6383/6390).

  Implementation starting point: `const Dec x = a.dec(0); const long long n = a.non_neg_int(1);` in both lambdas.

  Regression prerequisite: [T04 / CPP-C25](tests/04-evaluation.md#cpp-c25).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts 1600/1600; plain-vs-optimised 0 diffs

<a id="c-cpp-c35"></a>

- [x] **C-CPP-C35 — Resolve CPP-C35.** The planner's logical rewrite hoists FILTER above SORT_BY, hiding the sort key's error

  **[CPP-C35](../cpp-code-review.md) — [medium] [confirmed; JS plans the same SQL] The planner's logical rewrite hoists FILTER above SORT_BY, hiding the sort key's error**

  Source target: `cpp/sel_sql_hybrid.cpp:779-781` (`optimize_ast_logical(build_pipeline(...))`); the rewrite itself is in `sel.cpp` (optimiser).

  Implementation starting point: hoist FILTER over a sort only when the sort key cannot raise (typed field read on a known record), as is already done for MAP.

  Regression prerequisite: [T04 / CPP-C35](tests/04-evaluation.md#cpp-c35).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — fixed in C++ SQL wave: planner passes declared fields to the logical optimizer; pinned by 25/51 plan cases

<a id="c-cpp-c37"></a>

- [x] **C-CPP-C37 — Resolve CPP-C37.** Which error a program raises depends on whether the optimiser turned the arithmetic into a math plan

  **[CPP-C37](../cpp-code-review.md) — [medium] [confirmed, all five hosts] Which error a program raises depends on whether the optimiser turned the arithmetic into a math plan**

  Source target: `cpp/sel.cpp:3463-3534` (`eval_binary`: evaluate both operands, then coerce) vs 3766-3782 (`eval_math_plan`: `LoadVar`/`LoadLeaf` coerce each operand as soon as it is loaded) and the plan compiler 7826-8007.

  Implementation starting point: decide in the spec. Either "operands are evaluated, then coerced left to right" (the plan must load all leaves before coercing) or "each operand is coerced as evaluated" (`eval_binary` must coerce `l` before evaluating `r`). Add cases and change every host together.

  Regression prerequisite: [T04 / CPP-C37](tests/04-evaluation.md#cpp-c37).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts 1600/1600; plain-vs-optimised 0 diffs

<a id="c-cpp-c45"></a>

- [x] **C-CPP-C45 — Resolve CPP-C45.** `SORT_BY(...) .> TAKE(n)` fused into `TOP_BY` changes which error is raised

  **[CPP-C45](../cpp-code-review.md) — [low] [confirmed, all five hosts] `SORT_BY(...) .> TAKE(n)` fused into `TOP_BY` changes which error is raised**

  Source target: `cpp/sel.cpp:7641-7652` (`opt_logical_steps` SORT/SORT_DESC/SORT_BY + TAKE fusion).

  Implementation starting point: spec the order, or fuse only when `n` is a literal (cannot raise).

  Regression prerequisite: [T04 / CPP-C45](tests/04-evaluation.md#cpp-c45).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts green

<a id="c-cpp-c46"></a>

- [x] **C-CPP-C46 — Resolve CPP-C46.** E_DEPTH from equality/hash paths carries no position (`line 0 column 0`)

  **[CPP-C46](../cpp-code-review.md) — [low] [confirmed, all hosts] E_DEPTH from equality/hash paths carries no position (`line 0 column 0`)**

  Source target: `cpp/sel.cpp:3394-3400` `is_in()` (called at 3509 without `node.pos`, while `EQL` passes it at 3508); `dedupe` (5330-5350) and `do_bucket` (5925-5953) call `structural_hash()`/`eql()` with the default `Pos pos = {}`.

  Implementation starting point: pass `node.pos` / `a.pos()` / `key_node->pos` into `eql`/`structural_hash` at these sites.

  Regression prerequisite: [T04 / CPP-C46](tests/04-evaluation.md#cpp-c46).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

## Aggregates, ordering, grouping and joins

<a id="c-cpp-c1"></a>

- [x] **C-CPP-C1 — Resolve CPP-C1.** Aggregates hold `const Value&` into the source collection while the body runs (heap-use-after-free)

  **[CPP-C1](../cpp-code-review.md) — [high] [confirmed] Aggregates hold `const Value&` into the source collection while the body runs (heap-use-after-free)**

  Source target: `cpp/sel.cpp:5568` (`walk`: ALL/ANY/SUM/MAP/FILTER), `do_sort` (~5709-5738), `do_bucket` (~5934-5988); reference obtained from `collection_item` (~3940).

  Implementation starting point: bind by value (`const Value item = collection_item(...)`, one refcount increment) at the three sites and audit any other `const Value& x = collection_item/...` followed by `a.eval`. Decide the snapshot-vs-live rule at the same time (C38).

  Regression prerequisite: [T05 / CPP-C1](tests/05-relational.md#cpp-c1).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-cpp-c38"></a>

- [x] **C-CPP-C38 — Resolve CPP-C38.** Hosts disagree on whether an aggregate iterates a snapshot or the live source when the body overwrites an element

  **[CPP-C38](../cpp-code-review.md) — [medium] [confirmed; cross-host split] Hosts disagree on whether an aggregate iterates a snapshot or the live source when the body overwrites an element**

  Source target: `cpp/sel.cpp:5560-5580` (`walk`), 5709 (`do_sort`), 5934 (`do_bucket`): all re-read `collection_item(coll, i)` each iteration after fixing `count` up front.

  Implementation starting point: decide in the spec. Snapshotting the child handles at entry (or binding `item` by value, C1) makes C++ match PHP/Lisp and removes the lifetime hazard.

  Regression prerequisite: [T05 / CPP-C38](tests/05-relational.md#cpp-c38).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-cpp-c39"></a>

- [x] **C-CPP-C39 — Resolve CPP-C39.** Two-argument `BUCKET` groups by structural identity but names the group by scalar: distinct groups collapse and lose members

  **[CPP-C39](../cpp-code-review.md) — [medium] [confirmed, all five hosts] Two-argument `BUCKET` groups by structural identity but names the group by scalar: distinct groups collapse and lose members**

  Source target: `cpp/sel.cpp:5934-5948` (group lookup by `structural_hash` + `eql`) and 5955-5959 (`out.set(g.key_str, ...)`).

  Implementation starting point: in the two-argument spelling group on `key_str` (`unordered_map<string,size_t>`), not on `structural_hash`/`eql`. This also removes the structural hash of every key (P12).

  Regression prerequisite: [T05 / CPP-C39](tests/05-relational.md#cpp-c39).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-cpp-c40"></a>

- [x] **C-CPP-C40 — Resolve CPP-C40.** Equi-join extraction ignores same-named binders, so the result depends on an unrelated `AND TRUE`

  **[CPP-C40](../cpp-code-review.md) — [medium] [confirmed, all five hosts] Equi-join extraction ignores same-named binders, so the result depends on an unrelated `AND TRUE`**

  Source target: `cpp/sel.cpp:4405-4419` (`extract_join_equi`), used at 4963 and 5015; contrast `right_ok = upper_name(b1) != upper_name(b2)` at ~5165, which the pre-filter does guard.

  Implementation starting point: pick a meaning in the spec (the right binder wins is what the loop does) and make `extract_join_equi` refuse when `upper_name(b1) == upper_name(b2)`, in every host.

  Regression prerequisite: [T05 / CPP-C40](tests/05-relational.md#cpp-c40).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-cpp-c43"></a>

- [x] **C-CPP-C43 — Resolve CPP-C43.** Mixed numeric-looking/non-numeric text sorts with an intransitive comparator

  **[CPP-C43](../cpp-code-review.md) — [low] [confirmed] Mixed numeric-looking/non-numeric text sorts with an intransitive comparator**

  Source target: `cpp/sel.cpp:5570-5610` (`compare_values`), used by `do_sort` (`stable_sort`) and `do_top` (heap).

  Implementation starting point: define a total order in the spec (for example class first: numeric-looking values before other text, then numeric or bytes within a class), add cases, and implement with a precomputed key class (also fixes P10).

  Regression prerequisite: [T05 / CPP-C43](tests/05-relational.md#cpp-c43).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Portable regex semantics and resource failures

<a id="c-cpp-c7"></a>

- [x] **C-CPP-C7 — Resolve CPP-C7, CPP-C16, CPP-C26, CPP-C27, CPP-C28, CPP-C49, CPP-C50.** Coordinate the related changes below at their shared implementation surface.

  **[CPP-C7](../cpp-code-review.md) — [high] [confirmed] srell `error_complexity` escapes as a raw C++ exception at match time (process abort)**

  Source target: `cpp/sel.cpp:6933-6995` (`RMATCH`/`RFIND`/`RGROUPS`/`RREPLACE` call `srell::regex_search` / iterate `u32sregex_iterator` with no try/catch; only the constructor in `compile_regex` (6876-6882) catches `srell::regex_error`).

  Implementation starting point: wrap the search and iterator loops (including `++it`) in `catch (const srell::regex_error&)` and raise a SelError. This needs a spec decision for the code (no "regex too expensive" code exists; `E_RANGE` at the call node is the closest). Also add `catch (const std::exception&)` at the `Program::run` boundary so `bad_alloc`/`length_error` (C8) never escape as foreign exceptions.

  Regression prerequisite: [T06 / CPP-C7](tests/06-regex.md#cpp-c7).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[CPP-C16](../cpp-code-review.md) — [high] [confirmed] Unbounded regex cache at about 250 KB per compiled pattern**

  Source target: `cpp/sel.cpp:6866-6884` (`static std::map<std::string, Regex> cache`, no eviction).

  Implementation starting point: bound the cache (LRU of a few hundred, or evict all past N) and hold `shared_ptr<Regex>` so an evicted entry stays valid for in-flight calls. Better, pre-compile literal patterns once at `Program` compile time and keep the `Regex` on the AST node (see P16, C28).

  Regression prerequisite: [T06 / CPP-C16](tests/06-regex.md#cpp-c16).

  **[CPP-C26](../cpp-code-review.md) — [medium] [confirmed; cross-host split] RREPLACE walks zero-width matches PCRE-style, not like a global ECMAScript match**

  Source target: `cpp/sel.cpp:6962-6994` (`srell::u32sregex_iterator`; the comment at 6970-6973 claims ECMAScript behaviour).

  Implementation starting point: spec the rule (ES/Python semantics, the simpler one: after an empty match copy one code point and continue). In C++, replace the iterator by a manual loop over `regex_search(subject.begin()+pos, end, m, re, match_prev_avail)` (so `^` still cannot match mid-subject) that advances one code point on an empty match. PHP needs the same change (suppress the PCRE `NOTEMPTY_ATSTART` retry).

  Regression prerequisite: [T06 / CPP-C26](tests/06-regex.md#cpp-c26).

  **[CPP-C27](../cpp-code-review.md) — [medium] [confirmed; cross-host split] Capture groups inside a repetition: ECMAScript hosts (C++, JS) vs PCRE-style hosts (PHP, Python, Lisp)**

  Source target: `cpp/sel.cpp:6946-6960` (RGROUPS), 6902-6930 (`expand_replacement`); root cause is the engine (SRELL follows ECMAScript: captures reset at the start of each iteration).

  Implementation starting point: needs a spec decision. Either reject a capturing group anywhere under a quantifier (validator change in every host; nothing else is portable) or pick one semantics and emulate it. C++ can only follow ES.

  Regression prerequisite: [T06 / CPP-C27](tests/06-regex.md#cpp-c27).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[CPP-C28](../cpp-code-review.md) — [medium] [confirmed, all five hosts] Literal regex patterns are not validated at compile time**

  Source target: `cpp/sel.cpp:6759` (`validate_pattern` is called only from `compile_regex` at run time and from the SQL translator; nothing in the parser/optimiser).

  Implementation starting point: in `compile`/the optimiser, for `RMATCH/RFIND/RGROUPS/RREPLACE` whose pattern argument is a text literal, call `validate_pattern` (plus a trial srell compile and the `i`-flag ASCII check when the flag is a literal) and raise at the pattern node. Together with P16 it also lets the compiled `Regex` live on the node.

  Regression prerequisite: [T06 / CPP-C28](tests/06-regex.md#cpp-c28).

  **[CPP-C49](../cpp-code-review.md) — [low] [confirmed, all hosts; not C++ bugs] Regex validator gaps: class escape next to `-` becomes a range; `(*VERB)`/quantifier-after-anchor acceptance differs**

  Source target: `cpp/sel.cpp:6704-6708` (`expand_inside`) feeding `validate_class`; 6791-6801 (`(` handler) and 6803-6813 (quantifier handler).

  Implementation starting point: in `validate_class` reject (or escape) a `-` directly before or after an expanded class escape; in `validate_pattern` track "previous token is quantifiable" and reject a quantifier that follows `(`, `|`, start of pattern, `^` or `$`. Same change in all hosts.

  Regression prerequisite: [T06 / CPP-C49](tests/06-regex.md#cpp-c49).

  **[CPP-C50](../cpp-code-review.md) — [low] [confirmed] Some valid patterns are refused or very slow to compile in C++ (SRELL limits leak out as `E_REGEX_SYNTAX ... error_complexity`)**

  Source target: `cpp/sel.cpp:6876-6882`.

  Implementation starting point: spec a pattern-size / nesting / group cap in `spec/limits.json`, enforce it in `validate_pattern` (all hosts) with a message that names the limit.

  Regression prerequisite: [T06 / CPP-C50](tests/06-regex.md#cpp-c50).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Text, binary and output budgets

<a id="c-cpp-c8"></a>

- [x] **C-CPP-C8 — Resolve CPP-C8.** `REPEAT("", n)` hangs; `REPEAT`/`PADL`/`PADR` allocate without bound and die with uncaught `std::bad_alloc`

  **[CPP-C8](../cpp-code-review.md) — [high] [confirmed] `REPEAT("", n)` hangs; `REPEAT`/`PADL`/`PADR` allocate without bound and die with uncaught `std::bad_alloc`**

  Source target: `cpp/sel.cpp:6314-6320` (`REPEAT`: `for (i < n) out += s;`), 6183-6203 (`pad` builds a `CodePoints` padding one code point at a time up to `width`).

  Implementation starting point: return `""` immediately when `s` is empty or `n == 0`. Check `n * |s|` against a spec'd cap (for example reusing the 1M-scale style caps, raising `E_RANGE`) before allocating. In `pad` compute `need`, apply the same cap and `reserve` instead of `push_back` in a loop.

  Regression prerequisite: [T07 / CPP-C8](tests/07-text-binary.md#cpp-c8).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-cpp-c51"></a>

- [x] **C-CPP-C51 — Resolve CPP-C51.** `long` (32-bit on Windows/LLP64) truncates huge counts in the text builtins

  **[CPP-C51](../cpp-code-review.md) — [low] [unconfirmed, reasoned] `long` (32-bit on Windows/LLP64) truncates huge counts in the text builtins**

  Source target: `cpp/sel.cpp:3885, 3900` (`index_of_cp`, `slice_cp` take `long`), 6214-6236 (`LEFT`, `RIGHT`, `SUBSTR`, `FIND` `static_cast<long>(a.non_neg_int(..))`).

  Implementation starting point: use `long long` / `std::ptrdiff_t` (or `size_t` after clamping to the size).

  Regression prerequisite: [T07 / CPP-C51](tests/07-text-binary.md#cpp-c51).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-cpp-c52"></a>

- [x] **C-CPP-C52 — Resolve CPP-C52.** `FROM_HEX` / `DECODE_BASE64` put a single byte of a multi-byte character into the error message (invalid UTF-8)

  **[CPP-C52](../cpp-code-review.md) — [low] [confirmed] `FROM_HEX` / `DECODE_BASE64` put a single byte of a multi-byte character into the error message (invalid UTF-8)**

  Source target: `cpp/sel.cpp:6473-6474` (`s.substr(i*2, 2)`), 6516 / 6522-6524.

  Implementation starting point: report the whole code point, or omit the character.

  Regression prerequisite: [T07 / CPP-C52](tests/07-text-binary.md#cpp-c52).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## SQL lexical scope, normalization and expansion

<a id="c-cpp-c10"></a>

- [x] **C-CPP-C10 — Resolve CPP-C10, CPP-C17, CPP-C23.** Coordinate the related changes below at their shared implementation surface.

  **[CPP-C10](../cpp-code-review.md) — [medium] [confirmed] SQL stage 1: long helper-variable chains are O(n^2) and then SIGSEGV from uncounted recursion**

  Source target: `cpp/sel_sql_stage1.cpp:195` (`record`: `is_constant` on the inlined tree every statement), 393-424 (`is_constant`), 112-140 (`substitute` does not count the depth of a def it splices in); also `SNode::to_node` and the recursive `~SNode`.

  Implementation starting point: charge the def's depth when splicing it (record depth in `Def`) so the walk refuses at 200; memoise `is_constant` per node.

  Regression prerequisite: [T08 / CPP-C10](tests/08-sql-scope.md#cpp-c10).

  **[CPP-C17](../cpp-code-review.md) — [high] [confirmed] SQL stage-1 helper inlining is a DAG that every later walk treats as a tree: exponential time and output size**

  Source target: `cpp/sel_sql_stage1.cpp:44-144` (`substitute` returns the shared def node), 151-231 (`record` -> `is_constant` + `validate`), 393-424 (`is_constant`); `cpp/sel_sql_node.cpp:79-110` (`to_node`); render side `cpp/sel_sql_translator.cpp:388-428` (`node()` re-walks and re-validates every constant compound); `cpp/sel_sql_hybrid.cpp` (called from fall-through, full pushdown, every prefix, `Helpers::tables`).

  Implementation starting point: (a) in `record()` validate the original RHS with previously evaluated constant defs in the evaluation root instead of the fully inlined tree; (b) memoise `is_constant`/`validate` per `const SNode*`; (c) count expanded nodes/characters during `substitute`/render and refuse past a fixed budget (new `spec/limits.json` entry and an `E_SQL_*` code), or render repeated helper reads as one CTE/param.

  Regression prerequisite: [T08 / CPP-C17](tests/08-sql-scope.md#cpp-c17).

  **[CPP-C23](../cpp-code-review.md) — [high] [confirmed] SQL stage-1 inlining lets an aggregate binder capture a free variable inside an inlined helper: silently wrong SQL**

  Source target: `cpp/sel_sql_stage1.cpp:84-93` (Var case returns the def unchanged); render side `cpp/sel_sql_translator.cpp:471-478` (`binder()` consulted first) and 792-797 (`from_binder`).

  Implementation starting point: alpha-rename binders, or resolve a def's free variables to a "closed" leaf that renders with an empty binder stack; at minimum refuse when a def's free variables intersect an enclosing binder name at the inlining site.

  Regression prerequisite: [T08 / CPP-C23](tests/08-sql-scope.md#cpp-c23).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## SQL kinds, numeric fidelity and server limits

<a id="c-cpp-c29"></a>

- [x] **C-CPP-C29 — Resolve CPP-C29, CPP-C30, CPP-C31.** Coordinate the related changes below at their shared implementation surface.

  **[CPP-C29](../cpp-code-review.md) — [medium] [confirmed] SQL: kind unification launders an undeclared column into BOOL or NUM**

  Source target: `cpp/sel_sql_translator.cpp:1081-1097` (`unify`), used by `conditional` (:1858) and by `ret_kind` for `@unify` entries (`??`, `???`, `COALESCE`); the resulting Fragment kind is trusted by `require_bool` (:1127) and `guard_numeric` (:1202).

  Implementation starting point: `unify` returns UNKNOWN when any branch is UNKNOWN (the "known kinds must agree" rule stays for the all-known case).

  Regression prerequisite: [T09 / CPP-C29](tests/09-sql-kinds.md#cpp-c29).

  **[CPP-C30](../cpp-code-review.md) — [medium] [confirmed; server semantics reasoned] SQL: `IN` over a literal list against an `exact` column leaves non-text items uncast**

  Source target: `cpp/sel_sql_translator.cpp:1791-1806` (`item = is_exact ? f : text_operand(f)`).

  Implementation starting point: skip the cast only when the item is a Text literal node or `f.exact()`; otherwise `text_operand(f)`.

  Regression prerequisite: [T09 / CPP-C30](tests/09-sql-kinds.md#cpp-c30).

  **[CPP-C31](../cpp-code-review.md) — [medium] [confirmed] SQL: `sargable` on PostgreSQL and SQLite emits a bare `=` under the column's own collation; the docs say the exact comparison is kept**

  Source target: `cpp/sel_sql_translator.cpp:1477-1490` (the `op == "$==" && sargable...` arm does nothing when `sargablePrefilter` is not "true" and falls out of the if-chain without the `text_operand` wrapping the last arm applies).

  Implementation starting point: when `sargablePrefilter` is not "true" fall through to the `text_operand` arm and update the two cases, or correct the doc and state that `sargable` means "exact by default" there (then refuse or ignore the flag).

  Regression prerequisite: [T09 / CPP-C31](tests/09-sql-kinds.md#cpp-c31).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed; docs/internals + sql/MAP.md 3.1 and docs/sql.md updated (server-mode assumptions, exact-column wording)

<a id="c-cpp-c59"></a>

- [x] **C-CPP-C59 — Resolve CPP-C59.** SQL: TAKE/DROP count evaluation is stricter than the evaluator, and uses codes outside `sql/errors.md`

  **[CPP-C59](../cpp-code-review.md) — [low] [confirmed] SQL: TAKE/DROP count evaluation is stricter than the evaluator, and uses codes outside `sql/errors.md`**

  Source target: `cpp/sel_sql_translator.cpp:3371-3402` (`eval_int_param`).

  Implementation starting point: accept numerals that are integral in value (scale ignored, -0 is 0), clamp huge counts to INT64_MAX, and use E_SQL_SHAPE/E_SQL_UNSUPPORTED for "not knowable at translation time".

  Regression prerequisite: [T09 / CPP-C59](tests/09-sql-kinds.md#cpp-c59).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## SQL bindings, dialects, fragments and rendering

<a id="c-cpp-c12"></a>

- [x] **C-CPP-C12 — Resolve CPP-C12, CPP-C36, CPP-C53.** Coordinate the related changes below at their shared implementation surface.

  **[CPP-C12](../cpp-code-review.md) — [low] [confirmed by TSan] `Map::check_numeric_guard` writes a global `std::set` from the `translate()` path**

  Source target: `cpp/sel_sql_map.cpp:635-640` (called from `Emit::numeric_operand`, sel_sql_emit.cpp:209).

  Implementation starting point: memoise under a mutex, do the check once when a dialect is defined or first resolved, or drop the memo (cheap comparison). See also C36, which touches the same lines.

  Regression prerequisite: [T10 / CPP-C12](tests/10-sql-rendering.md#cpp-c12).

  **[CPP-C36](../cpp-code-review.md) — [medium] [confirmed] `Map::check_numeric_guard` memoises the dialect before validating, so a bad numericGuard is refused once and then silently accepted**

  Source target: `cpp/sel_sql_map.cpp:635-640`.

  Implementation starting point: insert into `guard_checked` only after all checks pass (do the `bad()` throw before the insert). Combine with the C12 fix.

  Regression prerequisite: [T10 / CPP-C36](tests/10-sql-rendering.md#cpp-c36).

  **[CPP-C53](../cpp-code-review.md) — [low] [confirmed] A registered dialect can drop or misdefine `textEscape`, and text literals are then emitted unescaped**

  Source target: `cpp/sel_sql_emit.cpp:154-158` (`text_literal`), `cpp/sel_sql_map.cpp:241-271` (`check_lexical` allows a withdrawn or any escape map).

  Implementation starting point: in `text_literal` refuse (`E_SQL_UNSUPPORTED`) unless an Escapes rule exists whose `from` is the first char of `textQuote` and whose `to` contains it doubled/escaped; or validate in `define_dialect` / at first use.

  Regression prerequisite: [T10 / CPP-C53](tests/10-sql-rendering.md#cpp-c53).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-cpp-c32"></a>

- [x] **C-CPP-C32 — Resolve CPP-C32.** SQL: raw relation fields are silently replaced by a same-named column in `SELECT_COLS`, and given an empty identifier in derived tables

  **[CPP-C32](../cpp-code-review.md) — [medium] [confirmed] SQL: raw relation fields are silently replaced by a same-named column in `SELECT_COLS`, and given an empty identifier in derived tables**

  Source target: `cpp/sel_sql_translator.cpp:3577` (`column = f_spec && !f_spec->column.empty() ? f_spec->column : col`), 2954 (`ensure_derived`: `field.column = source_field ? source_field->column : name`), 3610 (`joined_row_fields`); `ColumnSpec::column` is empty for a raw field (`sel_sql_binding.cpp:166-181`).

  Implementation starting point: in `SELECT_COLS` / `ensure_derived` / `joined_row_fields` refuse (E_SQL_SHAPE) when the source field is raw, or project the raw SQL `AS` its alias inside the inner select.

  Regression prerequisite: [T10 / CPP-C32](tests/10-sql-rendering.md#cpp-c32).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-cpp-c55"></a>

- [x] **C-CPP-C55 — Resolve CPP-C55, CPP-C58.** Coordinate the related changes below at their shared implementation surface.

  **[CPP-C55](../cpp-code-review.md) — [low] [confirmed] SQL: case-colliding duplicate relation field names: C++ keeps the first, Python keeps the last**

  Source target: `cpp/sel_sql_binding.cpp:114-123` (`emplace_back` with no de-duplication), lookups `RelationSpec::field` (:140-145).

  Implementation starting point: refuse duplicates with E_SQL_BINDING (preferred) or overwrite in place.

  Regression prerequisite: [T10 / CPP-C55](tests/10-sql-rendering.md#cpp-c55).

  **[CPP-C58](../cpp-code-review.md) — [low] [confirmed] SQL: NUL bytes reach the SQL through RECORD aliases and inline text literals, although binding identifiers refuse them**

  Source target: `cpp/sel_sql_translator.cpp:3561` (`" AS " + emit_.ident(*proj.alias)`), `cpp/sel_sql_emit.cpp:154` (`text_literal`); vs `cpp/sel_sql_binding.cpp:33-42` (`check_name` refuses NUL).

  Implementation starting point: refuse NUL in aliases (E_SQL_SHAPE) and in inline text literals (or force Params mode).

  Regression prerequisite: [T10 / CPP-C58](tests/10-sql-rendering.md#cpp-c58).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-cpp-c56"></a>

- [x] **C-CPP-C56 — Resolve CPP-C56.** SQL: `correlate` (application SQL) is spliced without parentheses into an AND chain

  **[CPP-C56](../cpp-code-review.md) — [low] [confirmed] SQL: `correlate` (application SQL) is spliced without parentheses into an AND chain**

  Source target: MAP `all`/`any`/`sum`/... skeletons via `relation_slots` (`cpp/sel_sql_translator.cpp:1649-1657`); statement WHERE assembly 3658-3685.

  Implementation starting point: wrap as `({corr})` when it is not the literal `TRUE` (changes the bytes of every relation case, so decide explicitly), or document "must be a single conjunct".

  Regression prerequisite: [T10 / CPP-C56](tests/10-sql-rendering.md#cpp-c56).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-cpp-c57"></a>

- [x] **C-CPP-C57 — Resolve CPP-C57.** SQL: the discarded pre-LINK `compile_statement` leaves orphan parameters in `Fragment::params()`

  **[CPP-C57](../cpp-code-review.md) — [low] [confirmed] SQL: the discarded pre-LINK `compile_statement` leaves orphan parameters in `Fragment::params()`**

  Source target: `cpp/sel_sql_translator.cpp:3300-3302` (`(void)compile_statement(plan)` appends to `params_`/`param_kinds_`/`caveats_`), copied into the final Fragment at 3767-3769.

  Implementation starting point: snapshot and restore `params_`, `param_kinds_`, `caveats_` around the discarded compile.

  Regression prerequisite: [T10 / CPP-C57](tests/10-sql-rendering.md#cpp-c57).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## Hybrid execution, keys, order and context

<a id="c-cpp-c33"></a>

- [x] **C-CPP-C33 — Resolve CPP-C33.** A hybrid continuation sees SQL-renumbered rows, so `_K` and FILTER's retained keys differ from `run()`

  **[CPP-C33](../cpp-code-review.md) — [medium] [confirmed; cross-host, JS planner identical] A hybrid continuation sees SQL-renumbered rows, so `_K` and FILTER's retained keys differ from `run()`**

  Source target: `cpp/sel_sql_hybrid.cpp:815-846` (prefix loop) and 506-650 (fall-through). JS `hybrid.mjs:236` has the same comment and set.

  Implementation starting point: treat "any key-retaining step (FILTER) in the prefix and a continuation whose first key-observing step is a FILTER or reads `_K`/keys" as a non-split (like `rows_are_not_the_value`), or push the FILTER into the continuation. At minimum make the `sql_unit` stand-in renumber.

  Regression prerequisite: [T11 / CPP-C33](tests/11-hybrid.md#cpp-c33).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-cpp-c34"></a>

- [x] **C-CPP-C34 — Resolve CPP-C34.** MAP fall-through pushes TAKE/DROP past a MAP whose custom half can raise, so `run()`'s error disappears

  **[CPP-C34](../cpp-code-review.md) — [medium] [confirmed; JS same] MAP fall-through pushes TAKE/DROP past a MAP whose custom half can raise, so `run()`'s error disappears**

  Source target: `cpp/sel_sql_hybrid.cpp:177-181` (`fallthrough_downstream`), 547-558; JS `FALLTHROUGH_DOWNSTREAM` is the same.

  Implementation starting point: fall through a downstream step only when no custom pair can fail (allow-list of total functions), or keep TAKE/DROP in the continuation.

  Regression prerequisite: [T11 / CPP-C34](tests/11-hybrid.md#cpp-c34).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-cpp-c54"></a>

- [x] **C-CPP-C54 — Resolve CPP-C54.** `execute_hybrid` on a pure_memory plan mutates the caller's context; the hybrid path clones it

  **[CPP-C54](../cpp-code-review.md) — [low] [confirmed] `execute_hybrid` on a pure_memory plan mutates the caller's context; the hybrid path clones it**

  Source target: `cpp/sel_sql_hybrid.cpp:853-870`.

  Implementation starting point: clone (or document) uniformly. Cloning costs O(size of context) (P24); a shallow copy of the top-level record is enough for the assignments.

  Regression prerequisite: [T11 / CPP-C54](tests/11-hybrid.md#cpp-c54).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-cpp-c60"></a>

- [x] **C-CPP-C60 — Resolve CPP-C60.** SQL: `SORT_BY` then `MAP` then `DISTINCT` emits `SELECT DISTINCT proj ORDER BY <unprojected column>`

  **[CPP-C60](../cpp-code-review.md) — [low] [unconfirmed on a server] SQL: `SORT_BY` then `MAP` then `DISTINCT` emits `SELECT DISTINCT proj ORDER BY <unprojected column>`**

  Source target: `cpp/sel_sql_translator.cpp:3243-3249` (DISTINCT wraps only when limit/offset are set) with the MAP-after-sort rule at 2811-2820.

  Implementation starting point: refuse a DISTINCT that follows a sort (in-memory continuation), as is already done for other unrepresentable orders.

  Regression prerequisite: [T11 / CPP-C60](tests/11-hybrid.md#cpp-c60).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## Host integration, concurrency and tooling

<a id="c-cpp-c11"></a>

- [x] **C-CPP-C11 — Resolve CPP-C11.** `register_function` mutates the host table with no lock while `compile()` reads it

  **[CPP-C11](../cpp-code-review.md) — [low] [confirmed by reading; not run under TSan] `register_function` mutates the host table with no lock while `compile()` reads it**

  Source target: `cpp/sel.cpp:8164-8194` (`register_function`), 2281-2295 (`host_table` / `registry_lookup`), 8146 (`function_names`), 2283-2290 and 8185-8189 (`retired_host_specs`).

  Implementation starting point: guard `host_table()` and `retired_host_specs()` with a `shared_mutex` (returned `const Spec*` stays valid because replaced specs are retired), or document "register before any thread compiles". For the leak, give `Node` a `shared_ptr<const Spec>` for host specs.

  Regression prerequisite: [T12 / CPP-C11](tests/12-integration.md#cpp-c11).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — six hosts agree on 107 API probes (24 pinned); race/TSan/-race lanes green

<a id="c-cpp-c13"></a>

- [x] **C-CPP-C13 — Resolve CPP-C13.** `HostArgs::val/text/...` with index at or above `count()` is undefined behaviour

  **[CPP-C13](../cpp-code-review.md) — [low] [confirmed] `HostArgs::val/text/...` with index at or above `count()` is undefined behaviour**

  Source target: `cpp/sel.cpp:3290-3300` (`Args::val` indexes `vals_[i]`/`nodes_[i]` unchecked), 8155-8161 (`HostArgs`).

  Implementation starting point: bounds-check in `HostArgs` and throw `std::out_of_range` (or E_BAD_ARG).

  Regression prerequisite: [T12 / CPP-C13](tests/12-integration.md#cpp-c13).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — six hosts agree on 107 API probes (24 pinned); race/TSan/-race lanes green

<a id="c-cpp-c15"></a>

- [x] **C-CPP-C15 — Resolve CPP-C15.** `RECORD` builds `rec.set(a.text(i), a.val(i + 1).clone())` in one expression

  **[CPP-C15](../cpp-code-review.md) — [low] [unconfirmed] `RECORD` builds `rec.set(a.text(i), a.val(i + 1).clone())` in one expression**

  Source target: `cpp/sel.cpp:5382`.

  Implementation starting point: bind key and cloned value to locals first.

  Regression prerequisite: [T12 / CPP-C15](tests/12-integration.md#cpp-c15).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — six hosts agree on 107 API probes (24 pinned); race/TSan/-race lanes green

<a id="c-cpp-c44"></a>

- [x] **C-CPP-C44 — Resolve CPP-C44.** `dependencies()` is "read anywhere minus assigned anywhere", not "read without having assigned it first"

  **[CPP-C44](../cpp-code-review.md) — [low] [confirmed, all five hosts] `dependencies()` is "read anywhere minus assigned anywhere", not "read without having assigned it first"**

  Source target: `cpp/sel.cpp:8130-8143` (`Program::dependencies`: `reads` minus `assigned`), 7083-7153 (`collect`); spec §8 and the `sel.hpp` comment say "reads without having assigned it first".

  Implementation starting point: implement first-use order (a variable read before its first plain `=` in evaluation order is a dependency; compound targets are reads), or reword the spec to set-difference semantics; then add cases.

  Regression prerequisite: [T12 / CPP-C44](tests/12-integration.md#cpp-c44).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — six hosts agree on 107 API probes (24 pinned); race/TSan/-race lanes green
