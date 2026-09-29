# C++ host code review

Date: 2026-09-29

## Scope and method

Host: `cpp/` (C++23). Rust and the other hosts were out of scope, except where a reviewer cross-checked behaviour against them.

Files: `cpp/sel.cpp` (about 8,200 lines: errors, UTF-8, decimal core, Value, lexer, parser, evaluator, builtins, optimiser, host API), `cpp/sel.hpp`, `cpp/sel_ast.hpp`, `cpp/sel_optimizer.cpp`, `cpp/sel_math_ops.hpp` (checked against the spec, not reviewed as generated code), the SQL layer (`cpp/sel_sql_translator.cpp`, `sel_sql_stage1.cpp`, `sel_sql_binding.cpp`, `sel_sql_node.cpp`, `sel_sql_hybrid.cpp`, `sel_sql_map.cpp`, `sel_sql_emit.cpp`, `sel_sql.cpp`) and the harnesses `cpp/bin/sqlt.cpp` and `cpp/tests/{unit,sql_unit}.cpp`. Generated data files (`sel_sql_map_data.cpp`, `case_data.cpp`) were not reviewed.

Method: seven slice reviewers (cpp-1 front end; cpp-2 numeric core and Values; cpp-3 evaluator, Args, optimiser, host API; cpp-4 text, regex, binary, null and control builtins; cpp-5 structure and aggregate builtins and the relational pipeline; cpp-6 SQL translator core; cpp-7 SQL rendering and hybrid planner). Each reviewer read its slice fully, confirmed findings by running `cpp/build/sel` or a private ASan/UBSan/TSan build of a copy of the sources in the scratchpad, and cross-checked against JS/PHP/Python/Lisp. Nothing was built in or written to the repo. This report is the deduplicated synthesis. No database was run, so SQL-server semantics are reasoned from the docs (labelled where it matters). Timings come from a shared, loaded box (two reviewers report load average around 16), so read them as ratios and complexity classes, not absolutes.

Note on the review files: the cpp-4 reviewer reported that other reviewers overwrote some scratchpad files. The seven findings files themselves were intact and consistent with their slices. Every memory-safety and abort finding below that I re-ran reproduced (see "Re-verified by synthesizer").

Findings tags: `confirmed` means reproduced by running something. `unconfirmed` means read or reasoned only. `measured`, `reasoned` and `guessed` are as the reviewer labelled them. Sources are cited as `cpp-N Cx` / `cpp-N Px`.

## Executive summary

- Overall: the sanitizer-fuzzed surface (lexer, parser, UTF-8, text/regex/binary combos, about 5,000 random SQL pipelines) is clean, and cross-host agreement on ordinary programs is very good. The problems sit at the seams. These are the int128/limb boundary in the decimal core, the evaluator's assumptions about pointer/reference stability, uncounted recursion in a few helpers, and the SQL layer's helper inlining and hybrid split.
- 14 high, 21 medium and 25 low findings (60 total, plus 25 performance items).
- Memory safety, worst first. Any SEL program (rule text from an untrusted author) can take the process down via three use-after-free bugs: an aggregate body that appends to the collection being iterated (C1), `A += (B = 1)` (C2), and a nested arithmetic plan resizing the shared scratchpad (C3). It can also take the process down via four uncounted-recursion stack overflows: `??` chains (C4), nested string interpolation (C5), long flat chains inside an aggregate body (C6) and long SQL helper chains (C10). Two more are uncaught C++ exceptions: srell `error_complexity` (C7) and `bad_alloc` (C8). A `MAX`/`MIN` with over 32,768 arguments corrupts the heap (C9).
- Correctness: the decimal core silently rounds a product to 18 digits when both operands have scale over 18 (C20, so `POWER(1.05, 30)` is wrong). `ROUND` and `/` round the wrong way past 2^126 (C21, signed-overflow UB). The numeric hash-join key gives false and missed matches (C22). The SQL stage-1 inliner lets an aggregate binder capture a free variable, which yields wrong SQL that is reported as success (C23).
- Resource exhaustion: the regex cache never evicts and costs about 250 KB per pattern (5 GB for 20,000 patterns, C16). SQL helper inlining is exponential, so about 25 statements hang (C17). Big-by-big decimal division is O(n*m) with string allocations (P1: 116.8 s against 0.23 s in JS for a 40-character program).
- Biggest performance wins: a limb-based divider plus early E_RANGE rejection in multiplication (P1, P2). Linear building of SQL `IN`/`ANY` chains (P3: 16,000 items takes 34 s today). Non-throwing `??` probes (P4: about 20 us per miss). Operator enum plus cached literals (P5: 1.66 s to 1.04 s measured). Fresh-temporary moves instead of deep clones (P7).
- Test-gap theme: `tools/decimal-oracle.py` never leaves 12 integer digits and scale 6, which is why about 8 int128/limb-boundary bugs survived the gate. The unit tests do not exercise nested plans, exceptions inside plans or slot limits. No `.selt` case mutates a collection from inside an aggregate body. `tests/sql_unit.cpp`'s hybrid equivalence stand-in keeps FILTER keys, which a driver cannot.

## Re-verified by synthesizer

Run against the already-built `cpp/build/sel` (release build, mtime Sep 29 11:34). Nothing was rebuilt.

| Finding | Command | Observed |
|---|---|---|
| C1 (aggregate UAF) | `R["a"]=1; R["b"]=2; R["c"]=3; FILTER(R, (R["z" & _K] = 1; TRUE))` | Segmentation fault (rc 139). Reproduced. |
| C2 (compound assign) | `A = 1; A += (B = 1); A` | `1`. JS prints `2`. Reproduced (write silently lost in release). |
| C3 (math plan pointer) | `V = 1; X = 5; X + LEN(V+V+...+V)` (20 `V`) | Segmentation fault (rc 139). Reproduced. cpp-2's generator with 40 `A` printed `-3` here (cpp-2 reported `-3` at 35 and `E_RANGE` at 40): the result is heap-layout dependent, the bug is not. |
| C4 (`??` chain) | `'1 ' + '?? 1 ' * 50000` from a file | rc 139. Reproduced. |
| C5 (interpolation) | `"{"*12000 + 1 + "}""*12000` | rc 139. Reproduced. |
| C6 (walkers) | `MAP((1,2), _+_+...+_)` (60,000 terms) | rc 139. Reproduced. |
| C7 (srell) | `RMATCH("^(a+)+$", REPEAT("a",22) & "!")` | `terminate called after throwing an instance of 'srell::regex_error' what(): error_complexity`, rc 134. Reproduced. |
| C9 (uint16) | `A=1; MAX(A, ... x33000)` | rc 139. Reproduced. |
| C20 (dec_mul) | `POWER(1.05, 30)`; `0.1234567890123456789 * 0.1234567890123456789` | `4.321942375150662009268185637251557948`; `0.015241578753238836774881877789971041`. JS gives `4.321942375150662009157288198886473341473378241062164306640625`. Reproduced. |
| C21 (2*r) | `ROUND(0.90000000000000000000000000000000000000, 0)`; the big `/` | `0`; `0.0000000000`. JS gives `1`. Reproduced. |
| C22 (join key) | cpp-2 and cpp-5 LINK repros | `0` (missed match) and `1` (false match). Reproduced. |
| C24 (`% 1`) | `A=-1000...0; A % 1` | `-0`. Reproduced. |
| C25 (ROUND order) | `X="x"; ROUND(IF(TRUE,X,1), 2.5)` | `E_NOT_INT at line 1 column 28`. Reproduced (the other hosts report `E_NOT_NUM` at column 14, per cpp-3). |
| C26/C27 (regex) | `RREPLACE("a*?", "-", "aab")`; `RGROUPS("(?:(a)|b)+", "ab")` | `-----b-` vs JS `-a-a-b-`; groups `{"1"="ab","2"=""}` vs Python `{"1"="ab","2"="a"}`. Reproduced. |
| C28 | `IF(FALSE, RMATCH("(?=a)", "x"), 1)` | `1` (no compile-time E_REGEX_SYNTAX). Reproduced. |
| C38 | `R = LIST(1,2,3); MAP(R, IF(_K == "1", (R[3] = 99), _))` | `{99,2,99}` (live view). Reproduced. |
| C39 | `A="x"; A["k"]=1; B="x"; B["k"]=2; BUCKET(LIST(A,B,"x"), _)` | one member under `"x"`. Reproduced. |
| C41 (INT128_MIN) | `A=-9223372036854775808; B=18446744073709551616; C=A*B; ABS(C) > 0` | `FALSE`. Reproduced. |
| C43 | `LIST("9","10","1a") .> SORT_DESC() .> JOIN(",")` | `10,9,1a`. Reproduced. |
| C44 | `--deps -e 'X += 1'` | prints nothing. Reproduced. |
| C47 | `N = 0; (N = N + 1) .> MAX(_, _) & "/" & N` | `2/2`. Reproduced. |
| C8 (partial) | `LEN(REPEAT("", 400000000))` | `0` after 2.4 s (linear in n; cpp-4's 27 s at 4e9 is consistent). The `bad_alloc` path was not re-run (it needs a memory ulimit and 21 s). |

Not re-run: everything in the SQL layer (needs the reviewers' private drivers), sanitizer and TSan findings, and all timings.

---

## Correctness findings

### A. Memory safety, undefined behaviour and process aborts

Highest-impact class. Each of C1-C10 can be triggered from rule text alone.

Related undefined-behaviour items that produce wrong values are filed under section C: C21 (signed `__int128` overflow in `2 * r`) and C41 (`-INT128_MIN`).

#### CPP-C1 [high] [confirmed] Aggregates hold `const Value&` into the source collection while the body runs (heap-use-after-free)
- Found by: cpp-3 C3, cpp-5 C1. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:5568` (`walk`: ALL/ANY/SUM/MAP/FILTER), `do_sort` (~5709-5738), `do_bucket` (~5934-5988); reference obtained from `collection_item` (~3940).
- What: `const Value& item = collection_item(coll, i)` points into the collection's `children` vector (or `storage`). The body is then evaluated and `item` is read again (FILTER pushes it, `do_sort` does `indexed.push_back({item,...})`, `do_bucket` does `rows.push_back(item)`). A body that assigns a new key into the iterated record (`R["z"] = 1`) reallocates the vector and `item` dangles. SPEC §3.4 makes this legal ("evaluating an expression yields a value, not a snapshot"). `do_top` copies the element first and is fine; MAP/ANY/ALL/SUM never read `item` afterwards, so only some entry points crash. Unshaped records and materialised lists are affected. A list built by `,` uses packed `storage` and did not reproduce.
- Repro: `cpp/build/sel -e 'R["a"]=1; R["b"]=2; R["c"]=3; FILTER(R, (R["z" & _K] = 1; TRUE))'` gives a segmentation fault; the same with `SORT_BY(R, (R["z" & _K] = 1; 1))` and `BUCKET(...)` (cpp-5). cpp-3's ASan variant is `R["a"]=1; R["b"]=2; R["c"]=3; R["d"]=4; FILTER(R, (R["z"] = 1) > 0)`, also `BUCKET(R, (R["z"] = 1) & "")` and `SORT_BY(R, (R["z"] = 1))`. ASan reports heap-use-after-free READ in `Value::Value(const Value&)`. cpp-3 notes the release build "usually still prints the right answer" for the 4-key case, while cpp-5 got a hard segfault for the 3-key `_K` form.
- Fix sketch: bind by value (`const Value item = collection_item(...)`, one refcount increment) at the three sites and audit any other `const Value& x = collection_item/...` followed by `a.eval`. Decide the snapshot-vs-live rule at the same time (C38).
- Conformance gap: none. Suggest `agg.filter.body-appends-key-to-iterated-record` and the SORT_BY/BUCKET twins: they must at least not crash and should pin the semantics.

#### CPP-C2 [high] [confirmed] Compound assignment to a plain variable writes through a dangling pointer
- Found by: cpp-3 C1. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:3623-3647` (`eval_assign`, `NT::Var` branch, compound operators).
- What: `Value* current = ctx.root->get(name)` is taken before the RHS is evaluated and `*current = value` runs after it. If the RHS creates a variable and the root's `children` vector grows, the pointer dangles. In the release build the write is silently lost. SPEC §5.8/§3.4 require the store to land in the tree "as it exists once the RHS has run". The non-Var branch re-derives the path correctly.
- Repro: `cpp/build/sel -e 'A = 1; A += (B = 1); A'` prints `1`; JS prints `2` (expected). Also `A = 1; A += (B = 1, C = 2, D=3, E=4, F=5, G=6); A` gives `1`. ASan: heap-use-after-free in `Value::operator=` at `eval_assign` (sel.cpp:3646), freed by `Value::set` (vector realloc, sel.cpp:1935).
- Fix sketch: after the RHS, do `ctx.root->set(name, value)` (or re-`get` the slot), keep `target_value` for the arithmetic.
- Conformance gap: none. Suggest `asg.compound.rhs-creates-variable`: `A = 1; A += (B = 1); A` => `t"2"`, a variant with more than 4 new variables so the vector must reallocate, and `A &= (B = "x"); A`.

#### CPP-C3 [high] [confirmed] `eval_math_plan` keeps a raw `Dec*` into `ctx.math_scratchpad` across a nested plan that may resize it
- Found by: cpp-2 C2, cpp-3 C2. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:3758-3764` (`resize`, `Dec* scratchpad = data() + base`) and 3778-3782 (`LoadLeaf` calls `eval_node`, which may enter another plan that calls `resize`).
- What: the vector starts at 32 slots. An outer plan whose leaf evaluates a sub-expression containing a larger plan reallocates it, and the outer frame then reads and writes freed memory. Trivial valid rules trigger it (an arithmetic expression whose function argument contains about 30 or more slots of arithmetic). ASan: heap-use-after-free WRITE in `Dec::operator=` at sel.cpp:3780.
- Repro: `cpp/build/sel -e 'V = 1; X = 5; X + LEN(V+V+V+V+V+V+V+V+V+V+V+V+V+V+V+V+V+V+V+V)'` segfaults (rc 139); JS prints `7` (cpp-3). cpp-2: `python3 -c "print('A=1; A + LEN(\"x\" & (%s))' % '+'.join(['A']*40))"` gives garbage (`-3` at 35 `A`, `E_RANGE ... 1000000 fractional digits` at 40; the synthesizer got `-3` at 40); expected `4`.
- Fix sketch: index (`ctx.math_scratchpad[base + dst]`) instead of caching the pointer, or a per-frame `std::vector<Dec>`/`std::deque`. Do not keep `const Dec&` references (`d2`, `a`, `b`) across anything that can evaluate.
- Conformance gap: none. Suggest `num.plan.nested-plan-past-initial-scratchpad` (at least 33 slots total across an outer plan and a nested plan in a leaf), plus an ASan lane over it.

#### CPP-C4 [high] [confirmed] Right-associative `??` / `???` chains recurse in the parser with no depth count (SIGSEGV at about 20k operators)
- Found by: cpp-1 C1. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:2914-2919` (`Parser::parse_term`, non-assignment `'R'` branch: `n->r = parse_term(e->bp)`).
- What: the assignment branch calls `enter()` before recursing; the `??`/`???` branch does not. SPEC §6.4 requires every walk to be counted.
- Repro: `python3 -c "print('1 '+'?? 1 '*50000)" > q.sel; cpp/build/sel q.sel` gives rc 139 (also `'NULL '+'?? NULL '*20000+'?? 5'`; 10,000 still works, 20,000 crashes; `???` crashes at 100,000). With 250 operators `NULL ?? ... ?? 5` gives E_DEPTH from the evaluator and `1 ?? 1 ?? ...` gives `1`. JS/Python/Lisp also die on the 50,000 case with no E_DEPTH; PHP answers `1`. Only C++ takes the whole process down with no catchable error.
- Fix sketch: do NOT just `enter()` here. `1 ?? 1 ?? ...` (250 operators) is legal and short-circuits to 1, and the evaluator is the depth authority for such a chain. Parse the `R` non-assign chain iteratively (collect operand/op pairs, fold right-to-left into `Bin` nodes). The resulting right-deep tree is already protected downstream (`~Node` iterative; eval/dependencies/optimiser count). Same treatment in the other four hosts.
- Conformance gap: none. Suggest `lim.coalesce-chain-long-and-short-circuits` (5,000-operator `1 ?? 1 ?? ...` => `1`) and `lim.coalesce-chain-eval-depth` (all-`NULL` chain past 200 => E_DEPTH at the innermost node).

#### CPP-C5 [high] [confirmed] Nested string interpolation recurses unboundedly in the lexer (SIGSEGV at about 11-12k levels; quadratic before that)
- Found by: cpp-1 C2. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:2498-2533` (`lex_quoted`), 2582-2609 (`match_brace` <-> `skip_quoted`), 2627-2648 (`emit_parts` -> `lex_range` -> `lex_quoted`).
- What: `"{"{"{ ... 1 ... }"}"}"` recurses twice (to find the closer and again to tokenise). Neither path is counted and the lexer finishes before the parser's E_DEPTH (at about 50 levels) can fire. Each level rescans everything nested inside it, so time is O(depth x length): 1.1 s at 10,000 levels (40 KB), which still ends in a correct E_DEPTH.
- Repro: `python3 -c "print('\"{'*N+'1'+'}\"'*N)" > n.sel; cpp/build/sel n.sel`. N=5000 gives `E_DEPTH ... column 101` in about 0.25 s, N=10000 gives E_DEPTH in 1.1 s, N of 12000 and above gives rc 139. The threshold is stack-size dependent: about 700 levels crash a 512 KB worker-thread stack. JS/Python/Lisp also fail at N=20000; PHP spins for over 60 s (quadratic).
- Fix sketch: add a nesting counter to `lex_quoted`, `skip_quoted`'s `{` branch and `match_brace`, raise E_DEPTH at the opening brace/quote past a small bound (chosen so every parseable program, at most about 50-66 interpolation levels, still lexes). To remove the O(d x n) rescan, reuse the recorded close index or lex nested strings straight off the same cursor. Put the same bound in every host.
- Conformance gap: none (`lim.parse-depth` uses parens only). Suggest `lim.interp-depth-just-under` / `lim.interp-depth-past-the-cap` once the boundary is decided.

#### CPP-C6 [high] [confirmed] Uncounted recursive tree walkers overflow the C stack on a long flat expression inside an aggregate body
- Found by: cpp-3 C4. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:4430` `node_contains_var` (called at 5553 `walk`, 5701 `do_sort`, 5822 `do_top`, 5903 and 5968 `do_bucket`); also 4412 `expr_depends_only` and the `reads_only_fields` lambda in `leading_field_conjuncts` (5507-5519).
- What: SPEC §6.4 requires every recursion over a possibly deep tree to be counted. The parser builds `1+1+...` iteratively into a left-deep tree; neither the parser nor `opt_root` (which bails above the cap) counts it. The evaluator would raise E_DEPTH at 201, but these helpers run before the body is evaluated and recurse the whole tree.
- Repro: `python3 -c "print('MAP((1,2), '+'+'.join(['_']*60000)+')')" > f.sel; cpp/build/sel f.sel` gives rc 139 (20,000 terms correctly gives `E_DEPTH at line 1 column 39611`). Without the aggregate (`_A+_A+...` x400000) it gives E_DEPTH cleanly. The `FILTER(LINK(...), <100000-term body>)` form (`leading_field_conjuncts` / `expr_depends_only`) also exits 139. JS (RangeError) and Lisp (control stack exhausted) fail the same way; PHP dies earlier in the lexer with a memory-limit fatal.
- Fix sketch: give the helpers a depth parameter that returns conservatively (or fails E_DEPTH at the node the evaluator would) past MAX_DEPTH. Better: compute "contains `_K`" once at parse/optimise time and store a bool on the Node (this also fixes P17).
- Conformance gap: none. Suggest `lim.eval-depth.flat-chain-inside-aggregate-body`: a generated 5,000-term body inside MAP/FILTER/SORT_BY/BUCKET/TOP_BY expecting E_DEPTH at the 201st node.

#### CPP-C7 [high] [confirmed] srell `error_complexity` escapes as a raw C++ exception at match time (process abort)
- Found by: cpp-4 C1. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:6933-6995` (`RMATCH`/`RFIND`/`RGROUPS`/`RREPLACE` call `srell::regex_search` / iterate `u32sregex_iterator` with no try/catch; only the constructor in `compile_regex` (6876-6882) catches `srell::regex_error`).
- What: SRELL bounds each search with a 2^21 failure counter (`lcounter_defnum_`, srell.hpp:17276) and throws `regex_error(error_complexity)` when it trips. It is neither a SelError nor handled by `Program::run`: the CLI aborts with SIGABRT and an embedder gets a stray `std::runtime_error` derivative. Two triggers: a nested-quantifier pattern on a subject of about 22 characters, and an ordinary pattern on a long subject (one failure per character in a `(a|b)*` loop, so it trips at about 2.5M characters). JS/PHP/Python/Lisp answer FALSE.
- Repro: `cpp/build/sel -e 'RMATCH("^(a+)+$", REPEAT("a",22) & "!")'` aborts (n=20 answers FALSE in 68 ms; n=22 aborts in 0.8 s). `cpp/build/sel -e 'RMATCH("^(a|b)*c$", REPEAT("ab", 1500000))'` aborts; with `REPEAT("ab",1000000)` it gives `FALSE`. Other hosts answer FALSE at 1.5M.
- Fix sketch: wrap the search and iterator loops (including `++it`) in `catch (const srell::regex_error&)` and raise a SelError. This needs a spec decision for the code (no "regex too expensive" code exists; `E_RANGE` at the call node is the closest). Also add `catch (const std::exception&)` at the `Program::run` boundary so `bad_alloc`/`length_error` (C8) never escape as foreign exceptions.
- Conformance gap: none. At minimum a case that a 3M-character subject with `^(a|b)*c$` returns FALSE; `re.limit.*` cases only after the spec defines a step budget.

#### CPP-C8 [high] [confirmed] `REPEAT("", n)` hangs; `REPEAT`/`PADL`/`PADR` allocate without bound and die with uncaught `std::bad_alloc`
- Found by: cpp-4 C2. Partly re-verified by synthesizer (the empty-string loop is linear in n).
- Where: `cpp/sel.cpp:6314-6320` (`REPEAT`: `for (i < n) out += s;`), 6183-6203 (`pad` builds a `CodePoints` padding one code point at a time up to `width`).
- What: `non_neg_int` saturates at LLONG_MAX and no size cap exists (SPEC §6.4 caps only ROUND scale, POWER exponent, regex quantifier). With an empty `x` the loop runs n times doing nothing; `REPEAT("", 9000000000000000000)` never returns, whereas JS/PHP/Python return `""` in about 0.1 s. `REPEAT("ab", 10000000000)` and `PADL("x", 100000000000, "0")` grow until `std::bad_alloc`, uncaught, so the process aborts (or the OOM killer fires on an overcommitting box). JS raises RangeError and PHP a fatal error, so no host is graceful, but only C++ hangs on the empty case.
- Repro: `time cpp/build/sel -e 'LEN(REPEAT("", 4000000000))'` gives `0` after 27.4 s (JS 120 ms, PHP 95 ms). `(ulimit -v 3000000; cpp/build/sel -e 'LEN(REPEAT("ab", 10000000000))')` gives `terminate called after throwing an instance of 'std::bad_alloc'`, rc 134 after 21 s; same for `PADL("x",100000000000,"0")`. Synthesizer: `LEN(REPEAT("", 400000000))` gives `0` after 2.4 s.
- Fix sketch: return `""` immediately when `s` is empty or `n == 0`. Check `n * |s|` against a spec'd cap (for example reusing the 1M-scale style caps, raising `E_RANGE`) before allocating. In `pad` compute `need`, apply the same cap and `reserve` instead of `push_back` in a loop.
- Conformance gap: none. Suggest `text.repeat.empty-huge` (`REPEAT("", 9000000000000000000)` => `""`) and a spec'd-cap case.

#### CPP-C9 [medium] [confirmed] `MathStep` / plan slot indices are `uint16_t`: `MIN`/`MAX` with over 32,768 arguments corrupts the heap
- Found by: cpp-2 C4. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:266-282` (`dst/src1/src2/scratchpad_size` are `uint16_t`), 7830-7831 (`slot_count`, `alloc_slot`).
- What: a fold allocates 2n-1 slots for n arguments and the depth cap does not bound argument count. At n of 32,769 and above `slot_count` wraps, `scratchpad_size` becomes tiny while `dst` reaches 65,535, and the executor writes past the scratchpad. Any plan with more than 65,535 leaves or steps wraps the same way (only the fold reaches it from a depth-capped tree).
- Repro: `python3 -c "print('A=1; MAX(%s)' % ','.join(['A']*33000))" > f.sel; cpp/build/sel f.sel` gives rc 139 (ASan: heap-buffer-overflow WRITE in `Dec::operator=` from `eval_math_plan`:3771). 30,000 arguments works; JS prints `1` for 33,000.
- Fix sketch: use `uint32_t`/`size_t` slots, or make `emit` return nullopt (fall back to the tree evaluator) once `slot_count` passes 65,535.
- Conformance gap: none. Suggest a generated 33,000-argument `MAX` (a long conformance line, or a unit test).

#### CPP-C10 [medium] [confirmed] SQL stage 1: long helper-variable chains are O(n^2) and then SIGSEGV from uncounted recursion
- Found by: cpp-6 C3.
- Where: `cpp/sel_sql_stage1.cpp:195` (`record`: `is_constant` on the inlined tree every statement), 393-424 (`is_constant`), 112-140 (`substitute` does not count the depth of a def it splices in); also `SNode::to_node` and the recursive `~SNode`.
- What: `A0 = X; A1 = A0 + 1; ...; A_n; A_n` makes each statement's inlined value one level deeper. The depth guards fire only at 200 within one expression, so `record()`'s `is_constant` walks depth i on statement i (quadratic time and recursion depth n). A legal SEL program (the evaluator runs all 60,000 statements in 0.5 s) ends in a misleading `E_SQL_DEPTH` or a crash.
- Repro (private driver, mariadb, X a NUM column, from the cpp-6 report; not re-run): n=5000 0.45 s; n=20000 10.7 s (E_SQL_DEPTH); n=45000 106 s (E_SQL_DEPTH); n=60000 exit 139 (SIGSEGV, 1 MB program). About 150-250 bytes of stack per level (n=5000 crashes under `ulimit -s 1024`).
- Fix sketch: charge the def's depth when splicing it (record depth in `Def`) so the walk refuses at 200; memoise `is_constant` per node.
- Conformance gap: none. Suggest 250 chained helpers over a column expecting `E_SQL_DEPTH` at the statement that crosses 200, and a stress lane with 100k statements.

#### CPP-C11 [low] [confirmed by reading; not run under TSan] `register_function` mutates the host table with no lock while `compile()` reads it
- Found by: cpp-1 C4, cpp-3 C13.
- Where: `cpp/sel.cpp:8164-8194` (`register_function`), 2281-2295 (`host_table` / `registry_lookup`), 8146 (`function_names`), 2283-2290 and 8185-8189 (`retired_host_specs`).
- What: everything else shared and mutable in the TU takes a mutex, but `host_table()` is a plain `std::map`. Registering while another thread is in `parse_call`/`parse_pipe_step` is a data race (crash, not a stale read). The doc says "register before compiling", but says nothing about threads, while `sel.hpp` advertises thread-pool use of `Program`. Separately, replaced specs (and their `std::function` closures) are retired into a process-lifetime vector and never freed, so per-request/tenant re-registration leaks.
- Repro: not run. Two threads: one looping `sel::compile("F(1)")`, one calling `sel::register_function("F", 0, 1, fn)`.
- Fix sketch: guard `host_table()` and `retired_host_specs()` with a `shared_mutex` (returned `const Spec*` stays valid because replaced specs are retired), or document "register before any thread compiles". For the leak, give `Node` a `shared_ptr<const Spec>` for host specs.
- Conformance gap: n/a (host API).

#### CPP-C12 [low] [confirmed by TSan] `Map::check_numeric_guard` writes a global `std::set` from the `translate()` path
- Found by: cpp-7 C7.
- Where: `cpp/sel_sql_map.cpp:635-640` (called from `Emit::numeric_operand`, sel_sql_emit.cpp:209).
- What: `Registry::guard_checked` is a plain `std::set` that is `find`/`insert`-ed on first numeric-guard use, so two threads translating concurrently race. TSan (4 threads x 200 translates of `X > 5` on mariadb) reports `data race ... sel_sql_map.cpp:637`. Sharing one `Bindings` between threads also races on the non-atomic Value refcount (documented handle design), but the registry race is not needed by it. No doc says the SQL layer is single-threaded.
- Fix sketch: memoise under a mutex, do the check once when a dialect is defined or first resolved, or drop the memo (cheap comparison). See also C36, which touches the same lines.
- Conformance gap: not testable in `.sqlt`.

#### CPP-C13 [low] [confirmed] `HostArgs::val/text/...` with index at or above `count()` is undefined behaviour
- Found by: cpp-3 C12.
- Where: `cpp/sel.cpp:3290-3300` (`Args::val` indexes `vals_[i]`/`nodes_[i]` unchecked), 8155-8161 (`HostArgs`).
- What: a host function registered with `min < max` that reads an optional argument without testing `count()` gets a heap-buffer-overflow instead of an error; the other hosts raise a language-level error.
- Repro: `register_function("OPT", 1, 2, [](HostArgs& a){ return a.count() > 1 ? a.val(1) : a.val(5); }); compile("OPT(1)").run(c);` gives an ASan heap-buffer-overflow in `Args::val` (scratchpad `cpp3/src/t1.cpp oob`).
- Fix sketch: bounds-check in `HostArgs` and throw `std::out_of_range` (or E_BAD_ARG).
- Conformance gap: API-probe territory.

#### CPP-C14 [low] [unconfirmed as a bug; code smell] `dec_get_limbs` / `dec_get_digits` mutate `const Dec` through `const_cast`, including file-scope `const Dec DEC_ZERO`
- Found by: cpp-2 C9.
- Where: `cpp/sel.cpp:675-713, 804`.
- What: writing through a `const_cast` to an object defined const is UB. Benign today (`DEC_ZERO` is only copied), and `Dec::digits`/`limbs` are already `mutable`, so the casts are redundant. It would race if a shared `Node::dec` were ever asked for digits from two threads; today only local copies are.
- Fix sketch: delete the `const_cast`s and make `DEC_ZERO` non-const.

#### CPP-C15 [low] [unconfirmed] `RECORD` builds `rec.set(a.text(i), a.val(i + 1).clone())` in one expression
- Found by: cpp-3 C14.
- Where: `cpp/sel.cpp:5382`.
- What: argument evaluation order is unspecified (GCC evaluates right to left), so `clone()` (which can throw E_DEPTH on an over-deep host-supplied value) may run before the key's `as_text` error. Only reachable with an over-deep host value. The rest of the file sequences such pairs through named locals.
- Fix sketch: bind key and cloned value to locals first.

### B. Resource exhaustion (super-linear time or unbounded memory from small input)

#### CPP-C16 [high] [confirmed] Unbounded regex cache at about 250 KB per compiled pattern
- Found by: cpp-4 C3 (and P1).
- Where: `cpp/sel.cpp:6866-6884` (`static std::map<std::string, Regex> cache`, no eviction).
- What: every distinct pattern text is kept forever, and a compiled `srell::u32regex` costs about 250 KB even for `a1`. `FILTER(rows, RMATCH(_["pattern"], ...))` over attacker-influenced data, or a long-lived process compiling per-tenant rules, grows without limit. Each miss also costs about 0.29 ms while holding the global mutex.
- Repro (measured): `python3 rss.py cpp/build/sel -e "L = SPLIT(REPEAT('x,', 20000), ','); COUNT(FILTER(L, RMATCH('a' & _K, 'a')))"` gives max RSS 5077 MB in 5.8 s (100,000 patterns gives 25,371 MB in 26.7 s; do not rerun the 100k on a shared box). One repeated pattern stays at 22 MB.
- Fix sketch: bound the cache (LRU of a few hundred, or evict all past N) and hold `shared_ptr<Regex>` so an evicted entry stays valid for in-flight calls. Better, pre-compile literal patterns once at `Program` compile time and keep the `Regex` on the AST node (see P16, C28).
- Conformance gap: not expressible in `.selt`; suggest a `tools/` memory-bound probe.

#### CPP-C17 [high] [confirmed] SQL stage-1 helper inlining is a DAG that every later walk treats as a tree: exponential time and output size
- Found by: cpp-6 C1, cpp-7 C8 (the hybrid planner runs the translator several times, so its cost is about 2x).
- Where: `cpp/sel_sql_stage1.cpp:44-144` (`substitute` returns the shared def node), 151-231 (`record` -> `is_constant` + `validate`), 393-424 (`is_constant`); `cpp/sel_sql_node.cpp:79-110` (`to_node`); render side `cpp/sel_sql_translator.cpp:388-428` (`node()` re-walks and re-validates every constant compound); `cpp/sel_sql_hybrid.cpp` (called from fall-through, full pushdown, every prefix, `Helpers::tables`).
- What: `A_i = A_{i-1} + A_{i-1}` inlines by pointer, so the tree is a DAG of O(n) nodes, but `is_constant`, `SNode::to_node` (deep copy), `validate`, `node()` and the SQL text expand it as a tree, 2^n. The evaluator runs the same program in linear time. Only depth is bounded, and depth here is n. `docs/internals/sql-translation.md` §7.5 acknowledges duplication for `A = 1 + 2; A * A` but not the exponential case; no output-size limit exists in `spec/limits.json`. Also present in the Python host (n=16 took 1.2 s there) and in JS `translateStatement` (n=18: 3 s, 12 MB).
- Repro (measured with private drivers; not re-run):
  - cpp-6, mariadb, `A0=1; A1=A0+A0; ...; A24=A23+A23; TRUE`, no bindings: n=16 0.25 s, n=20 3.6 s, n=24 50 s (about x14 per 4 statements, all in `record()`). With a column `A0=X`, n=20 takes 4.7 s and emits a 12.6 MB SQL string. `cpp/build/sel -e` on the constant version with n=60 prints TRUE in 6 ms.
  - cpp-7, `X0 = COUNT(ORDERS); X1 = X0 + X0; ...; ORDERS .> MAP(RECORD("id", _["id"], "c", Xn))` (-O2): n=14 translate 98 ms / plan 149 ms; n=18 1.7 s / 1.7 s; n=20 4.4 s / 8.7 s (about 50 MB SQL); n=24 extrapolates to about 70 s and over 1 GB.
- Fix sketch: (a) in `record()` validate the original RHS with previously evaluated constant defs in the evaluation root instead of the fully inlined tree; (b) memoise `is_constant`/`validate` per `const SNode*`; (c) count expanded nodes/characters during `substitute`/render and refuse past a fixed budget (new `spec/limits.json` entry and an `E_SQL_*` code), or render repeated helper reads as one CTE/param.
- Conformance gap: none. Suggest a `.sqlt` case with about 30 doubling statements over a column expecting a bounded-time refusal, plus a timing guard in the fuzz lane.

#### CPP-C18 [medium] [confirmed] `RECORD(...)` with many literal keys costs O(n^2) at compile time
- Found by: cpp-1 C3.
- Where: `cpp/sel.cpp:1348-1358` `prepare_record_shape` (called from `Parser::finish_call`, sel.cpp:3160): duplicate check by `std::find` over the growing `keys` vector.
- What: n literal keys cost n^2/2 string compares before anything runs, and the shape-cache limits (`SHAPE_CACHE_MAX_KEYS` = 256) apply only afterwards in `intern_record_shape`. An untrusted 650 KB rule ties up a core for seconds, and JS compiles the same input in about 0.9 s total, so this is an extra C++-only quadratic.
- Repro (measured): `python3 -c "print('RECORD('+','.join('\"key%d\",%d'%(i,i) for i in range(N))+')')" > r.sel; cpp/build/sel --deps r.sel`. N=5,000 0.09 s; N=20,000 1.13 s; N=40,000 4.4 s (parse stage only, via a timed copy). JS at N=40,000 is 0.9 s.
- Fix sketch: give up on the shape as soon as `node.items.size()/2 > SHAPE_CACHE_MAX_KEYS` (an optimisation only; such shapes are not cached anyway), or dedupe with `unordered_set<string_view>`. Results identical: `prepare_record_shape` returns `{}` for duplicates and the fallback path handles them.
- Conformance gap: none needed (behaviour unchanged); a scale benchmark with a 20k-key RECORD would catch a regression.

#### CPP-C19 [medium] [confirmed] A caught error inside a math plan leaks the scratchpad frame (unbounded memory growth under `??`)
- Found by: cpp-2 C5, cpp-3 C5 (cpp-3 P3 repeats the numbers).
- Where: `cpp/sel.cpp:3763` (`math_scratchpad_top += size`) and 3849 (restored only on success). Catch sites that keep evaluating: `??`/`???` at 3476-3482 (swallow E_UNDEF_VAR/E_NO_KEY), also 4517, 4576, 4783 and the aggregate catch blocks.
- What: `eval_math_plan` is not exception safe. When a step throws and an enclosing `??` catches it, `math_scratchpad_top` stays advanced, so every caught failure permanently consumes `scratchpad_size` slots (about 96-100 bytes each, and the vector doubles). It also makes C3 more likely (the base grows, so nested plans hit the resize).
- Repro (measured):
  - cpp-2: `cpp/build/sel -e 'COUNT(SPLIT(REPEAT("a,", 100000), ",") .> MAP((Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q+Q + 1) ?? 0))'` gives max RSS 610 MB in 2.1 s; the same with `MAP(Q ?? 0)` gives 33 MB in 1.06 s; JS 125 MB in 2.0 s. With 400,000 rows and a 2-slot plan: 392 MB vs 121 MB baseline.
  - cpp-3: `L = SPLIT(REPEAT("a,", 300000), ","); COUNT(MAP(L, (1 + _["k"] + 2*_K) ?? 0))` gives peak RSS 662 MB (207 MB with `(1 + _["k"]) ?? 0`, 92 MB with the never-failing `(1 + 1) ?? 0`), 0.3 s to 5.8 s to 6.4 s across the three variants.
- Fix sketch: RAII guard restoring `math_scratchpad_top = base` (same idiom as `Pop` in `eval_node`); optionally probe missing variables without throwing (P4).
- Conformance gap: a leak is not observable to the `.selt` harness. Add a unit test that runs the `??` plan 100k times and asserts `ctx.math_scratchpad_top == 0`.

### C. Wrong results, spec violations and cross-host divergences

#### CPP-C20 [high] [confirmed] `dec_mul` silently rounds both operands to 18 fractional digits when both have scale over 18 (POWER inherits it)
- Found by: cpp-2 C1. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:1037-1048` (the `a.scale > 18 && b.scale > 18 && mantissa not +-1` branch of `dec_mul`).
- What: SPEC §4.2 says `*` has result scale `sa + sb` and is exact. The branch replaces both operands by `dec_round(x, 18)` before multiplying, so digits are lost (and the sign is lost when an operand rounds to 0). The `mantissa == +-1` exemption is why `POWER(0.1, n)` still passes conformance. POWER is repeated squaring through `dec_mul`, so any decimal power whose intermediate scale passes 18 is wrong: `POWER(1.05, 30)` is wrong from the 5th squaring. The constant folder runs the same code, so literals fold wrong too.
- Repro: `cpp/build/sel -e 'POWER(1.05, 30)'` gives `4.321942375150662009268185637251557948`; expected (JS/Python) `4.321942375150662009157288198886473341473378241062164306640625`. `0.1234567890123456789 * 0.1234567890123456789` gives `0.015241578753238836774881877789971041`; expected `0.01524157875323883675019051998750190521`. `POWER(0.5, 100)` gives `0.000000000000000000000000000000000000`; expected `0.00000000000000000000000000000078886090522101180541172856528278622967`. Oracle: 951 of 59,982 wide-range cases fail before the fix.
- Fix sketch: delete the branch. The small path still tries `__builtin_mul_overflow` and falls to the exact limb path (validated by cpp-2 on a scratch copy: 0 mismatches over 83k cases).
- Conformance gap: none. Suggest cases `POWER(1.05, 30)`, `0.1234567890123456789 * 0.1234567890123456789`, `POWER(0.5, 100)`, and a decimal-oracle mode with scales above 18 (see "Suggested fix order").

#### CPP-C21 [high] [confirmed] `ROUND` and `/` return the wrong value when the remainder exceeds 2^126 (signed `__int128` overflow in `2 * r`, UB)
- Found by: cpp-2 C3. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:1197` (`dec_round`: `if (2 * r >= p) q++;`) and 1122 (`dec_div`: `if (2 * r >= den) q++;`).
- What: `r < p` (or `< den`) can reach about 10^38 and `2 * r` overflows once `r >= 2^126`. It wraps negative in practice, so the value rounds down instead of half away from zero (SPEC §4.4). UBSan flags both sites on random 38-digit inputs.
- Repro: `cpp/build/sel -e 'ROUND(0.90000000000000000000000000000000000000, 0)'` prints `0` (JS `1`). `ROUND(0.99999999999999999999999999999999999999, 0)` prints `0`. `8600000000000000000000000000.0000000000 / 99999999999999999999999999999999999999` prints `0.0000000000` (JS `0.0000000001`). Oracle: 4 division and 4 round mismatches in 6,000 random cases.
- Fix sketch: compare `r >= p - r` / `r >= den - r` (no overflow since `r < p`), or use unsigned arithmetic.
- Conformance gap: none. Suggest `ROUND(0.99999999999999999999999999999999999999, 0)` => `1` and the division above.

#### CPP-C22 [high] [confirmed] Numeric equi-join fast key is wrong for decimals beyond int64: false matches and missed matches
- Found by: cpp-5 C2 (rated high, showed false matches), cpp-2 C7 (rated medium, showed the missed match). Both re-verified by synthesizer.
- Where: `cpp/sel.cpp:4479-4500` (cpp-5) / 4546-4550 (cpp-2), `make_fast_join_key`, the `d.small` branch, `BigDec` sub-case. (Line numbers differ between the two reports.)
- What: the small (int128) path strips trailing zeros from the mantissa `m` and lowers the scale `s`, but when the stripped `m` does not fit int64 it builds `key.text = dec_get_digits(d)` from the original, unstripped digit string. Equal values spelled differently get different keys (missed row); different values can get the same key (false row). The non-small branch (4501-4515) strips correctly. Only values with 19-38 significant digits are affected. SPEC §7.4 says pairs match "as the comparison would compare them".
- Repro:
  - False match (cpp-5): `A = LIST(RECORD("k","100000000000000000000000.0")); B = LIST(RECORD("k","1000000000000000000000000")); COUNT(LINK(A, B, a["k"] == b["k"]))` gives `1` (a joined row; 1e23 matched with 1e24!); JS/PHP/Python return an empty result. `"100000000000000000000000.0" == "1000000000000000000000000"` is FALSE in C++ itself.
  - Missed match (cpp-5): `A = LIST(RECORD("k","12345678901234567890.50")); B = LIST(RECORD("k","12345678901234567890.5")); COUNT(LINK(A, B, a["k"] == b["k"]))` gives `0` (JS `1`). Also `"123456789012345678900.0"` vs `"123456789012345678900"` gives `0` (JS `1`).
  - Missed match (cpp-2): `X = LIST(RECORD("id", 12345678901234567890.0)); Y = LIST(RECORD("id", 12345678901234567890, "name", "ann")); COUNT(LINK(X, Y, L, R, L["id"] == R["id"]))` gives `0` (JS/Python `1`).
  - Adding `AND TRUE` to the predicate forces the nested loop and gives the right answer, so the result depends on whether the optimiser can extract an equi-join.
- Fix sketch: build the BigDec text from the stripped `m` (`dec_digits_from_magnitude(|m|)`), or use one canonicalisation function for both branches.
- Conformance gap: none. Suggest `rel.link.equi-join-numeric-key-beyond-int64-trailing-zero-spelling` (both directions). cpp-5's random 6-seed differential did not hit it; the fuzzer needs a generator that produces the same value with different trailing zeros at 19-38 digits.

#### CPP-C23 [high] [confirmed] SQL stage-1 inlining lets an aggregate binder capture a free variable inside an inlined helper: silently wrong SQL
- Found by: cpp-6 C2. Also present in the Python host.
- Where: `cpp/sel_sql_stage1.cpp:84-93` (Var case returns the def unchanged); render side `cpp/sel_sql_translator.cpp:471-478` (`binder()` consulted first) and 792-797 (`from_binder`).
- What: `substitute` shadows defs by binders but not the reverse. A def whose RHS mentions a free variable `Y` is inlined into the body of an aggregate whose binder is also `Y`, and at render time `variable()` resolves that `Y` to the binder. sql-translation.md §6 rule 4 says capture happens at the assignment, "which is what SEL itself does". The translation reports success with different semantics.
- Repro (private driver, not re-run): `drv mariadb 'X = Y + 1; ALL((1,2,3), Y, Y > X)' Y=c:t.y:NUM` gives `(((1 > (1 + 1)) AND (2 > (2 + 1))) AND (3 > (3 + 1)))`, a constant FALSE. Correct is `(1 > (t.y + 1)) AND (2 > (t.y + 1)) AND (3 > (t.y + 1))`. Loud variant: `ANY((B + 1, 2), B, B > 0)` recurses until the depth guard and answers `E_SQL_DEPTH` ("the evaluator answers E_DEPTH", which is false: SEL evaluates it fine). Any name shared by a column binding and a binder/def triggers it (for example a helper `LIMIT_ = Q` plus `ANY(ITEMS, Q, ...)`).
- Fix sketch: alpha-rename binders, or resolve a def's free variables to a "closed" leaf that renders with an empty binder stack; at minimum refuse when a def's free variables intersect an enclosing binder name at the inlining site.
- Conformance gap: `norm.inline.captures-at-assignment` pins assignment-time capture for plain variables only. Suggest `norm.inline.def-not-captured-by-binder` (the column form above) and `agg.binder-same-name-as-list-variable`.

#### CPP-C24 [medium] [confirmed] Division/modulo by a divisor whose digit string is exactly "1" goes wrong once the dividend is beyond int128
- Found by: cpp-2 C6. `% 1` case re-verified by synthesizer.
- Where: `cpp/sel.cpp:617-633` (`divmod_abs` power-of-ten fast path with `k = b.size() - 1 == 0`): `r = strip(a.substr(a.size()))` is the empty string, not `"0"`. Consumers: `dec_div` (line 1135, `r == "0"`), `dec_mod` (line 1177, `dec_make(a.neg, r, s)`).
- What: with `r == ""`, an exact quotient is treated as inexact, so the result keeps scale DIV_SCALE instead of the minimal scale (SPEC §4.3), and `dec_make(neg, "", s)` skips the zero-sign normalisation, so `%` yields `-0` / `-0.0` (violates SPEC §4.1). Triggers for dividends the int128 path cannot take (about 28+ digits for `/`, 39+ for `%`, or a divisor of scale above 38).
- Repro (cpp vs JS): `A=1000000000000000000000000000000000000000000; A / 1` gives `1000000000000000000000000000000000000000000.0000000000` vs `1000000000000000000000000000000000000000000`. `A=-1000000000000000000000000000000000000000000; A % 1` gives `-0` vs `0` (then `B $== "0"` is FALSE). `33089435705329677516 / 0.000000001` gives `33089435705329677516000000000.0000000000` vs `33089435705329677516000000000`. `5 / 0.0000000000000000000000000000000000001` gives a trailing `.0000000000`. Oracle: 236 mismatches in 23,906 targeted cases, all this class.
- Fix sketch: handle `k == 0` in the pow10 branch (`q = a; r = "0"`) and make `dec_make` normalise `digits.empty()` to `"0"`.
- Conformance gap: none. Suggest `(-1000000000000000000000000000000000000000000) % 1` => `0` and the `/ 1` case.

#### CPP-C25 [medium] [confirmed] ROUND/POWER validate the second argument before coercing the first (C++ only, when the call is not planned)
- Found by: cpp-3 C6. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:6367-6390` (`ROUND`: `a.non_neg_int(1)` at 6373 then `a.dec(0)` at 6380; `POWER` at 6383/6390).
- What: the strict lane is left to right (spec §6.2, §7.1) and the other four hosts report the first argument's coercion error first. C++ reports the second whenever the math plan is not used. The plan path agrees with the other hosts, so the same program gets a different error depending on whether the optimiser managed to plan it.
- Repro: `X="x"; ROUND(IF(TRUE,X,1), 2.5)` gives `E_NOT_INT at line 1 column 28` in C++; JS/PHP/Lisp/Python give `E_NOT_NUM at line 1 column 14`. Same for `ROUND(IF(TRUE,X,1), -1)` (C++ `E_RANGE` col 28) and `POWER(IF(TRUE,X,1), -1)`. Plain `X="x"; ROUND(X, 2.5)` agrees everywhere (planned).
- Fix sketch: `const Dec x = a.dec(0); const long long n = a.non_neg_int(1);` in both lambdas.
- Conformance gap: none. Suggest `num.round.first-arg-error-before-scale-error` and a POWER twin with an unplanned first operand (`IF(TRUE, X, 1)`).

#### CPP-C26 [medium] [confirmed; cross-host split] RREPLACE walks zero-width matches PCRE-style, not like a global ECMAScript match
- Found by: cpp-4 C4. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:6962-6994` (`srell::u32sregex_iterator`; the comment at 6970-6973 claims ECMAScript behaviour).
- What: after an empty match, SRELL's iterator (like `std::regex_iterator` and PCRE) retries at the same position for a non-empty match. ECMAScript `replace(/../g)`, Python 3.7+ and cl-ppcre advance one code point. C++ and PHP agree with each other; JS, Python and Lisp agree with each other. SPEC §7.8 says only "all matches replaced", and the suite has only `x*` (stable). The file header says C++ agrees with JS "by construction". A 4,000-program random differential found 53 mismatches of this class.
- Repro: `RREPLACE("a*?", "-", "aab")` gives cpp/php `-----b-`, js/py/lisp `-a-a-b-`. `RREPLACE("|a", "-", "aa")` gives `-----` vs `-a-a-`. `RREPLACE("(?:|a)", "<$0>", "baab")` gives `<>b<><a><><a><>b<>` vs `<>b<>a<>a<>b<>`.
- Fix sketch: spec the rule (ES/Python semantics, the simpler one: after an empty match copy one code point and continue). In C++, replace the iterator by a manual loop over `regex_search(subject.begin()+pos, end, m, re, match_prev_avail)` (so `^` still cannot match mid-subject) that advances one code point on an empty match. PHP needs the same change (suppress the PCRE `NOTEMPTY_ATSTART` retry).
- Conformance gap: none. Suggest `re.replace.empty-then-nonempty` (`RREPLACE('a*?', "-", "aab")`, `RREPLACE('|a', "-", "aa")`) after the spec decision.

#### CPP-C27 [medium] [confirmed; cross-host split] Capture groups inside a repetition: ECMAScript hosts (C++, JS) vs PCRE-style hosts (PHP, Python, Lisp)
- Found by: cpp-4 C5. Re-verified by synthesizer (C++ vs Python).
- Where: `cpp/sel.cpp:6946-6960` (RGROUPS), 6902-6930 (`expand_replacement`); root cause is the engine (SRELL follows ECMAScript: captures reset at the start of each iteration).
- What: SPEC §7.8 says SEL accepts "a subset of syntax every host's regex engine agrees on", but `(?:(a)|b)+` has no single meaning. Also affects `$n` in RREPLACE. 60 mismatches of this class in the 4,000-program run.
- Repro: `RGROUPS("(?:(a)|b)+", "ab")` gives cpp/js `{"1"="ab","2"=""}`, php/py/lisp `{"1"="ab","2"="a"}`. `RREPLACE("(?:(a)|b)+", "[$1]", "ab")` gives `[]` vs `[a]`. `RGROUPS("(?:(a)|(b))*", "ab")` gives `("ab","","b")` vs `("ab","a","b")`.
- Fix sketch: needs a spec decision. Either reject a capturing group anywhere under a quantifier (validator change in every host; nothing else is portable) or pick one semantics and emulate it. C++ can only follow ES.
- Conformance gap: none. Suggest `re.reject.capture-in-repeat` or `re.groups.repeat-resets` once decided.

#### CPP-C28 [medium] [confirmed, all five hosts] Literal regex patterns are not validated at compile time
- Found by: cpp-4 C6. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:6759` (`validate_pattern` is called only from `compile_regex` at run time and from the SQL translator; nothing in the parser/optimiser).
- What: `spec/errors.md:34` and `:40` say `E_REGEX_SYNTAX` is compile-time only when the pattern is a literal. No host does it.
- Repro: `cpp/build/sel -e 'IF(FALSE, RMATCH("(?=a)", "x"), 1)'` gives `1`; js, php, python and lisp also answer `1` (expected `E_REGEX_SYNTAX` at the pattern's position).
- Fix sketch: in `compile`/the optimiser, for `RMATCH/RFIND/RGROUPS/RREPLACE` whose pattern argument is a text literal, call `validate_pattern` (plus a trial srell compile and the `i`-flag ASCII check when the flag is a literal) and raise at the pattern node. Together with P16 it also lets the compiled `Regex` live on the node.
- Conformance gap: none (`09-regex.selt` cannot tell compile-time from run-time). Suggest a bad literal in the untaken `IF` branch with a `compile` expectation.

#### CPP-C29 [medium] [confirmed] SQL: kind unification launders an undeclared column into BOOL or NUM
- Found by: cpp-6 C4. Same code and output in Python.
- Where: `cpp/sel_sql_translator.cpp:1081-1097` (`unify`), used by `conditional` (:1858) and by `ret_kind` for `@unify` entries (`??`, `???`, `COALESCE`); the resulting Fragment kind is trusted by `require_bool` (:1127) and `guard_numeric` (:1202).
- What: `unify` treats UNKNOWN as "unifies with anything" and returns the other branch's kind, so `IF(c, X, TRUE)` with `X` an undeclared column has kind BOOL. `docs/internals/sql-kinds.md` §4.2 refuses an UNKNOWN in a boolean position precisely because MariaDB answers `1 AND TRUE` as TRUE; the CASE wrapper defeats that refusal. The numeric side is the same: `IF(F, X, 1) + 1 == 2` is kind NUM, so no numeric guard is applied.
- Repro (private driver, not re-run): `drv mariadb 'IF(F, X, TRUE)' F=c:t.f:BOOL X=c:t.x` is accepted as a condition: `` CASE WHEN `t`.`f` THEN `t`.`x` ELSE TRUE END `` (a bare `X` is correctly refused, E_SQL_SHAPE). `drv mariadb 'IF(F, X, 1) + 1 == 2' ...` gives `` ((CASE WHEN `t`.`f` THEN `t`.`x` ELSE 1 END + 1) = 2) `` with no guard, whereas `X + 1 == 2` gets the REGEXP/CAST guard. On MariaDB `'abc'+1` is 1 (server behaviour reasoned from sql-kinds.md §4.1-4.2, not run); SEL raises E_NOT_BOOL / E_NOT_NUM.
- Fix sketch: `unify` returns UNKNOWN when any branch is UNKNOWN (the "known kinds must agree" rule stays for the all-known case).
- Conformance gap: none. Suggest `warrant.bool.if-cannot-launder-an-undeclared-column` and `warrant.numeric.if-branch-unknown-is-guarded`.

#### CPP-C30 [medium] [confirmed; server semantics reasoned] SQL: `IN` over a literal list against an `exact` column leaves non-text items uncast
- Found by: cpp-6 C5. Same in Python.
- Where: `cpp/sel_sql_translator.cpp:1791-1806` (`item = is_exact ? f : text_operand(f)`).
- What: when the needle is `exact` the code skips the text cast on the item too, which is right only for a text literal or another exact fragment. A numeric literal or NUM/UNKNOWN column is left bare, so on MariaDB/MySQL the comparison `varchar_col = 1` is numeric (`'1.0'`, `'01'`, `'1abc'` all equal 1), while SEL's IN is EQL (`"1.0" EQL 1` is FALSE). The neighbouring paths cast correctly: `S $== 1`, `S EQL 1` and `S IN (1)` (scalar branch) all emit `CAST(1 AS CHAR) COLLATE ...`, so `S IN (1)` and `S IN (1, 2)` translate differently.
- Repro (private driver, not re-run): `drv mariadb 'S IN (1, 2, "a")' S=c:t.s:TEXT:x` gives `` (((`t`.`s` = 1) OR (`t`.`s` = 2)) OR (`t`.`s` = 'a')) ``. `S IN (N, M)` with NUM/UNKNOWN columns gives `` ((`t`.`s` = `t`.`n`) OR (`t`.`s` = `t`.`m`)) ``. Contrast `S IN (1)` gives `` (`t`.`s` = CAST(1 AS CHAR) COLLATE utf8mb4_nopad_bin) ``.
- Fix sketch: skip the cast only when the item is a Text literal node or `f.exact()`; otherwise `text_operand(f)`.
- Conformance gap: `bind.in.exact.*` use only text literals. Suggest `bind.in.exact.numeric-literal-is-cast`.

#### CPP-C31 [medium] [confirmed] SQL: `sargable` on PostgreSQL and SQLite emits a bare `=` under the column's own collation; the docs say the exact comparison is kept
- Found by: cpp-6 C6. Same output in Python.
- Where: `cpp/sel_sql_translator.cpp:1477-1490` (the `op == "$==" && sargable...` arm does nothing when `sargablePrefilter` is not "true" and falls out of the if-chain without the `text_operand` wrapping the last arm applies).
- What: `docs/internals/sql-translation.md:654` says PG/SQLite retain the exact comparison, and that SQLite uses `COLLATE BINARY` so column-level NOCASE/RTRIM survive a cast and must be overridden. The code does the opposite: the flag exists to say "this column is case-insensitive", and there the residual exact check is exactly what is dropped. With a SQLite `COLLATE NOCASE` (or RTRIM) column, or PostgreSQL citext/nondeterministic-collation, `"a" = 'x'` matches `'X'` while SEL's `$==` is byte-exact. `bind.sargable.sqlite`/`.postgresql` pin the wrong way (their note: "text equality is already exact"), contradicting the doc.
- Repro (private driver, not re-run): `drv sqlite 'A $== "x"' A=c:t.a:TEXT:s` gives `("t"."a" = 'x')`; without the flag `(CAST("t"."a" AS TEXT) COLLATE BINARY = CAST('x' AS TEXT) COLLATE BINARY)`. Postgres: `("t"."a" = 'x')` vs `... COLLATE "C" ...`.
- Fix sketch: when `sargablePrefilter` is not "true" fall through to the `text_operand` arm and update the two cases, or correct the doc and state that `sargable` means "exact by default" there (then refuse or ignore the flag).
- Conformance gap: the two cases pin the current behaviour; the doc and cases disagree.

#### CPP-C32 [medium] [confirmed] SQL: raw relation fields are silently replaced by a same-named column in `SELECT_COLS`, and given an empty identifier in derived tables
- Found by: cpp-6 C7. Python differs (uses the SEL name in the derived-table path).
- Where: `cpp/sel_sql_translator.cpp:3577` (`column = f_spec && !f_spec->column.empty() ? f_spec->column : col`), 2954 (`ensure_derived`: `field.column = source_field ? source_field->column : name`), 3610 (`joined_row_fields`); `ColumnSpec::column` is empty for a raw field (`sel_sql_binding.cpp:166-181`).
- What: a `Binding::raw` field has `column == ""`. `SELECT_COLS("B")` then projects `` `i`.`B` `` (the SEL spelling) instead of the raw SQL, silently selecting whatever column `B` is. When the plan is wrapped in a derived table (any FILTER after TAKE/DROP/projection/sort) the outer reference is built from the empty name and the statement contains `` `_sub1`.`` `` (an empty identifier). Neither case refuses. The raw expression is not part of `SELECT i.*`, so no correct rendering exists; the right answer is a refusal.
- Repro (private driver, not re-run): relation `R` with fields `A -> a (NUM)` and `B -> raw "(x+1)" (NUM)`: `R .> SELECT_COLS("A","B")` gives `` SELECT `i`.`a`, `i`.`B` FROM `items` `i` ``; `R .> TAKE(3) .> FILTER(_["B"] > 1)` gives `` ... WHERE (CASE WHEN (`_sub1`.`` REGEXP ...`` ; contrast `R .> FILTER(_["B"] > 1)` gives `WHERE ((x+1) > 1)` (correct).
- Fix sketch: in `SELECT_COLS` / `ensure_derived` / `joined_row_fields` refuse (E_SQL_SHAPE) when the source field is raw, or project the raw SQL `AS` its alias inside the inner select.
- Conformance gap: none. Suggest `stmt.select-cols.raw-field-is-refused` and `stmt.derived.raw-field-is-refused`.

#### CPP-C33 [medium] [confirmed; cross-host, JS planner identical] A hybrid continuation sees SQL-renumbered rows, so `_K` and FILTER's retained keys differ from `run()`
- Found by: cpp-7 C1.
- Where: `cpp/sel_sql_hybrid.cpp:815-846` (prefix loop) and 506-650 (fall-through). JS `hybrid.mjs:236` has the same comment and set.
- What: SPEC §7.3 ("Keys are part of the value") says FILTER keeps its input's keys and a later `_K` sees them. The database returns a fresh list keyed 1..n, and the continuation runs over that. When a FILTER is in the SQL prefix and the continuation starts with a FILTER or reads `_K` (including the "custom half" of the MAP fall-through), keys differ from `run()`. The planner already guards this for MAP-then-FILTER (`fallthrough_downstream` omits FILTER; case `plan.fallthrough.filter-under-its-own-binder-...`) and `FILTER(_K ...) .> BUCKET`, but the general prefix loop has no guard. `sql_unit`'s stand-in returns the prefix AST value with FILTER keys intact, which is why the unit lane cannot see it.
- Repro (sqlite, bindings as in `sql_unit` `full_orders`, rows id 1..4 with amounts 10,5,7,12; stand-in DB renumbers; private harness, not re-run): `ORDERS .> FILTER(_["amount"] > 6) .> MAP(RECORD("id", _["id"], "k", REPEAT("x", _K)))`: `run()` gives k = "x","xxx","xxxx"; plan gives "x","xx","xxx" (hybrid: SQL = FILTER + `SELECT id`, MAP fall-through runs locally). `ORDERS .> FILTER(_["amount"] > 6) .> FILTER(REPEAT(_["name"], 2) $== "cc")`: `run()` gives `{"3"=...}`, plan gives `{"2"=...}`.
- Fix sketch: treat "any key-retaining step (FILTER) in the prefix and a continuation whose first key-observing step is a FILTER or reads `_K`/keys" as a non-split (like `rows_are_not_the_value`), or push the FILTER into the continuation. At minimum make the `sql_unit` stand-in renumber.
- Conformance gap: none. Suggest `plan.keys.filter-prefix-then-underscore-K-in-continuation` (expect pure_memory or a prefix that stops before the FILTER) and `plan.keys.filter-then-unsupported-filter`.

#### CPP-C34 [medium] [confirmed; JS same] MAP fall-through pushes TAKE/DROP past a MAP whose custom half can raise, so `run()`'s error disappears
- Found by: cpp-7 C2.
- Where: `cpp/sel_sql_hybrid.cpp:177-181` (`fallthrough_downstream`), 547-558; JS `FALLTHROUGH_DOWNSTREAM` is the same.
- What: SEL evaluates the MAP body for every row, then TAKE/DROP. With TAKE/DROP/SORT_BY+TAKE in SQL (`LIMIT`), the local half runs only for surviving rows, so errors on dropped rows vanish. This breaks the §12.1 promise "the continuation reports errors where run() would". The FILTER case is already handled ("REPEAT can raise, so the FILTER stays behind the MAP"); TAKE/DROP were not given the same reasoning.
- Repro: `ORDERS .> MAP(RECORD("id",_["id"], "s", REPEAT(_["name"], _["amount"] - 8))) .> TAKE(1)`: `run()` gives `E_RANGE@1:71`; plan (hybrid, SQL `... LIMIT 1`) gives `{"1"={"id"="1","s"="aa"}}`. Also `.> DROP(3)` and `.> SORT_BY(_["id"]) .> TAKE(1)`. Without the TAKE the plan agrees.
- Fix sketch: fall through a downstream step only when no custom pair can fail (allow-list of total functions), or keep TAKE/DROP in the continuation.
- Conformance gap: none. Suggest `plan.fallthrough.take-after-raising-custom-half-stays-local`.

#### CPP-C35 [medium] [confirmed; JS plans the same SQL] The planner's logical rewrite hoists FILTER above SORT_BY, hiding the sort key's error
- Found by: cpp-7 C3.
- Where: `cpp/sel_sql_hybrid.cpp:779-781` (`optimize_ast_logical(build_pipeline(...))`); the rewrite itself is in `sel.cpp` (optimiser).
- What: the planner is "the only entry point that optimises" (§12.1). Hoisting FILTER in front of MAP/sort is not error-preserving: in `run()` a failing `SORT_BY` key raises for every row; after the hoist the FILTER may have removed all rows, so the key is never evaluated. pure_memory plans (original tree) and hybrid plans then disagree.
- Repro: `ORDERS .> SORT_BY(_["nokey"]) .> FILTER(_["id"] > 100) .> TAKE(5)`: `run()` gives `E_NO_KEY@1:20`; plan is hybrid (SQL `WHERE id > 100`) and returns `{}`. Same with `.> MAP(RECORD("id", _["id"]))` and `SORT_BY(_["amount"] + _["nokey"])`. Position-only variant: `ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_))) .> SORT_BY(_["id"], "DESC") .> FILTER(_["id"] > 1) .> MAP(RECORD("id", _["id"], "k", _["amount"]))`: `run()` gives `E_NO_KEY@1:82` (SORT_BY), plan gives `E_NO_KEY@1:109` (FILTER).
- Fix sketch: hoist FILTER over a sort only when the sort key cannot raise (typed field read on a known record), as is already done for MAP.
- Conformance gap: none. Suggest `plan.sort.filter-not-hoisted-over-raising-sort-key`.

#### CPP-C36 [medium] [confirmed] `Map::check_numeric_guard` memoises the dialect before validating, so a bad numericGuard is refused once and then silently accepted
- Found by: cpp-7 C4. JS `map.mjs:227-229` and Python `map.py:283` have the same order (by reading, not run).
- Where: `cpp/sel_sql_map.cpp:635-640`.
- What: `guard_checked.insert(dialect)` runs before the comparison, and the comparison signals failure by throwing. The header says the check exists because a wrong guard "fails silently: it emits SQL that answers where SEL would not". After the first throw, later translations skip the check and emit that SQL.
- Repro (scratchpad `cpp7/guard.cpp`): dialect `evil` = `extending("mariadb")` with `numericGuard` = `CASE WHEN ({0} REGEXP 'x') THEN CAST({0} AS DECIMAL(65,10)) ELSE NULL END`; translate `X > 5` (X an UNKNOWN column) three times. First: `runtime_error: ... declares a numericGuard that does not carry '\\A-?[0-9]+...'`. Second and third: `` (CASE WHEN (`o`.`x` REGEXP 'x') THEN CAST(`o`.`x` AS DECIMAL(65,10)) ELSE NULL END > 5) ``.
- Fix sketch: insert into `guard_checked` only after all checks pass (do the `bad()` throw before the insert). Combine with the C12 fix.
- Conformance gap: none (`neutral.register.*` cases assert only the first refusal). Suggest a case that translates twice.

#### CPP-C37 [medium] [confirmed, all five hosts] Which error a program raises depends on whether the optimiser turned the arithmetic into a math plan
- Found by: cpp-3 C7.
- Where: `cpp/sel.cpp:3463-3534` (`eval_binary`: evaluate both operands, then coerce) vs 3766-3782 (`eval_math_plan`: `LoadVar`/`LoadLeaf` coerce each operand as soon as it is loaded) and the plan compiler 7826-8007.
- What: SPEC §6.2 says "strictly left to right wherever both operands are evaluated" but not whether operand 1 is coerced before operand 2 is evaluated. Two paths in the same host give different answers, and identically in JS/PHP/Lisp/Python. It makes "the optimiser must not change results" untestable for arithmetic.
- Repro: `A = "x"; A + B` gives `E_NOT_NUM` col 10 (plan: LoadVar coerces immediately), but `A = "x"; A + IF(TRUE, B, 1)` gives `E_UNDEF_VAR` col 23 (no plan: both evaluated, then coerced), and `A = "x"; A < B` (comparisons are never planned) is also `E_UNDEF_VAR`. Identical on all five hosts.
- Fix sketch: decide in the spec. Either "operands are evaluated, then coerced left to right" (the plan must load all leaves before coercing) or "each operand is coerced as evaluated" (`eval_binary` must coerce `l` before evaluating `r`). Add cases and change every host together.
- Conformance gap: none. Suggest `ord.arith.operand-coerced-before-next-operand-evaluated` with both planned and unplanned spellings.

#### CPP-C38 [medium] [confirmed; cross-host split] Hosts disagree on whether an aggregate iterates a snapshot or the live source when the body overwrites an element
- Found by: cpp-3 C9, cpp-5 C1 (tail). Re-verified by synthesizer (C++ side).
- Where: `cpp/sel.cpp:5560-5580` (`walk`), 5709 (`do_sort`), 5934 (`do_bucket`): all re-read `collection_item(coll, i)` each iteration after fixing `count` up front.
- What: with `R = LIST(1,2,3)` and a body that sets `R[3] = 99` while visiting element 1, C++/JS/Python see 99 at element 3; PHP/Lisp see 3. SORT_BY, TOP_BY and BUCKET split three different ways. SPEC §7.3 says the body runs "once per child in insertion order" and §3.4 says values are not snapshots, but nothing says whether the child sequence is fixed at entry. JS additionally crashes with a host TypeError when the body appends (`A = (1,2,3); MAP(A, A[COUNT(A)+1] = 1)` -> `TypeError: Cannot read properties of null (reading 'length')` at `js/src/builtins/aggregate.mjs:69`), a real JS bug outside this slice.
- Repro: `R = LIST(1,2,3); MAP(R, IF(_K == "1", (R[3] = 99), _))` gives cpp `{99,2,99}`, js `{99,2,99}`, python `{99,2,99}`, php `{99,2,3}`, lisp `{99,2,3}`. `SUM(R, IF(_K == "1", (R[3] = 99), _))` gives 200/200/200/104/104. cpp-5 adds: `X = LIST(1,2,3); FILTER(X, (X[2] = 99; TRUE))` gives cpp `1,99,3`, php/lisp `1,2,3`; `BUCKET(X, (X[3] = 0; _))` gives cpp keys `1,2,0`, others `1,2,3`.
- Fix sketch: decide in the spec. Snapshotting the child handles at entry (or binding `item` by value, C1) makes C++ match PHP/Lisp and removes the lifetime hazard.
- Conformance gap: none. Suggest `agg.source-mutated-by-body.*` cases once the rule is decided.

#### CPP-C39 [medium] [confirmed, all five hosts] Two-argument `BUCKET` groups by structural identity but names the group by scalar: distinct groups collapse and lose members
- Found by: cpp-5 C3. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:5934-5948` (group lookup by `structural_hash` + `eql`) and 5955-5959 (`out.set(g.key_str, ...)`).
- What: SPEC §7.3 "Bucket keys": in the two-argument spelling the key is an index key, "the key's scalar, verbatim". The code refuses only `Kind::None` and compares groups with `eql`, which also compares children. A TEXT value that carries children (`A = "x"; A["k"] = 1`) is a legal key whose scalar is `x`; two such keys with different children are two groups with the same `key_str`, and `Value::set` on the existing key replaces the earlier group's member list. Members disappear silently.
- Repro: `A = "x"; A["k"] = 1; B = "x"; B["k"] = 2; BUCKET(LIST(A, B, "x"), _)` gives `-{"x"=-{"1"=t"x"}}` in all five hosts (expected all three members under `"x"`). The 3-argument spelling is correct (identity, three groups).
- Fix sketch: in the two-argument spelling group on `key_str` (`unordered_map<string,size_t>`), not on `structural_hash`/`eql`. This also removes the structural hash of every key (P12).
- Conformance gap: none. Suggest `rel.bucket.two-arg-key-is-the-scalar-when-the-key-has-children`.

#### CPP-C40 [medium] [confirmed, all five hosts] Equi-join extraction ignores same-named binders, so the result depends on an unrelated `AND TRUE`
- Found by: cpp-5 C4.
- Where: `cpp/sel.cpp:4405-4419` (`extract_join_equi`), used at 4963 and 5015; contrast `right_ok = upper_name(b1) != upper_name(b2)` at ~5165, which the pre-filter does guard.
- What: with the same binder name on both sides (`LINK(L, R, x, x, ...)`, or the natural self-join `LINK(X, X, ...)` whose 3-arg binders are both `X`), the nested-loop path binds the name to the RIGHT row while the hash-join path evaluates the "left" expression against the left row. SPEC §7.4 says nothing about equal names.
- Repro (same in all five hosts): `COUNT(LINK(LIST(RECORD("k",1),RECORD("k",2)), LIST(RECORD("k",1),RECORD("k",3)), x, x, x["k"] == x["k"]))` gives `1`; with `AND TRUE` added it gives `4`. `X = LIST(RECORD("k",1),RECORD("k",2)); COUNT(LINK(X, X, x["k"] == x["k"]))` gives `2`, with `AND TRUE` gives `4`.
- Fix sketch: pick a meaning in the spec (the right binder wins is what the loop does) and make `extract_join_equi` refuse when `upper_name(b1) == upper_name(b2)`, in every host.
- Conformance gap: none. Suggest `rel.link.same-binder-name-both-sides` in both spellings.

#### CPP-C41 [low] [confirmed] `INT128_MIN` mantissa is reachable and negation/abs/format on it are UB; comparisons afterwards are wrong
- Found by: cpp-2 C8. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:915` (`dec_negate`), 951 (`dec_abs`), 679/702 (`dec_get_limbs`/`dec_get_digits`), 870 (`dec_format_buf`), 1103-1104 (`dec_div`), 1156-1157 (`dec_mod`), 1194 (`dec_round`), 1614 (`Value::num(Dec)`); `dec_from_limbs` (734) and `__builtin_mul_overflow` results deliberately admit exactly -2^127.
- What: `-(-2^127)` wraps back to itself, so `-C` stays negative, `ABS(C) > 0` is FALSE, and `dec_format_buf` prints an empty digit string (used by `structural_hash`, so DEDUPE/BUCKET can hash the small and big spellings of the same number differently). UBSan reports `negation of 0x8000...0 cannot be represented in type '__int128'`.
- Repro: `A=-9223372036854775808; B=18446744073709551616; C=A*B; ABS(C) > 0` gives cpp `FALSE`, JS `TRUE`. `D=-C; D<0` gives cpp `TRUE`, JS `FALSE`. (Variables, because literals fold to the digit form and hide it.)
- Fix sketch: never create a small Dec with mantissa == INT128_MIN (route it to the limb form in `dec_from_mantissa`/`dec_from_limbs`); validated by cpp-2 on a scratch copy.
- Conformance gap: none. Suggest the two expressions above.

#### CPP-C42 [low] [confirmed] Aggregates alias what they collect, but SPEC §3.4 and `contributing.md` say they copy (and that C++ does)
- Found by: cpp-5 C6.
- Where: `cpp/sel.cpp:5773-5780` (`MAP` `out.push_back(r)`), FILTER entries, `do_sort` (count>=2: no clone; count==1: clones), `do_bucket` rows, TAKE/DROP/DISTINCT.
- What: SPEC §3.4: "`,` (§5.9) and the aggregates copy what they collect". `docs/contributing.md:530` (cited by the reviewer; not re-read by the synthesizer) says C++ clones in MAP/FILTER and that aliasing is unobservable because a binder cannot be assigned. Both statements are false for the current tree: the clones were removed in `5220ea3` ("wip c++ optimization"), and a program can tell, since the collection is reachable through its variable. `SORT(list)` with no body still clones, `SORT(list, body)` does not: inconsistent inside C++.
- Repro: `X = LIST(RECORD("k",1)); MAP(X, _)[(X[1]["k"] = 9; 1)]["k"]` gives cpp `9`; a copy-at-collect implementation gives `1`. FILTER/TAKE/DISTINCT: all `9` in cpp; `SORT(X)` gives `1` but `SORT(X, 1)` gives `9`; JS gives `1` for `SORT(X, 1)`; PHP gives `9` even for `LIST(X)[1]`. The five hosts disagree with each other on all of these.
- Fix sketch: needs one decision: either amend §3.4 and contributing.md to say aggregates alias (matches most hosts and the perf work), or restore the clones (cost: P7). Pin with a case either way.
- Conformance gap: none. Suggest `values.aggregate-collect-copy-vs-alias` for MAP, FILTER, SORT, TAKE.

#### CPP-C43 [low] [confirmed] Mixed numeric-looking/non-numeric text sorts with an intransitive comparator
- Found by: cpp-5 C5. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:5570-5610` (`compare_values`), used by `do_sort` (`stable_sort`) and `do_top` (heap).
- What: two TEXT keys that both parse as numbers compare numerically, otherwise byte-wise. `"9" < "10"` (numeric), `"10" < "1a"` and `"1a" < "9"` (bytes) form a cycle. `std::stable_sort` is memory-safe against it, but the output depends on the algorithm, and TOP (heap) and SORT (merge) can disagree. `rel.sort.mixed` covers NULL/BOOL/number/text ranks but not this.
- Repro: `LIST("9","10","1a") .> SORT_DESC() .> JOIN(",")` gives cpp `10,9,1a`, lisp `10,9,1a`, but js/php/py `1a,10,9`.
- Fix sketch: define a total order in the spec (for example class first: numeric-looking values before other text, then numeric or bytes within a class), add cases, and implement with a precomputed key class (also fixes P10).
- Conformance gap: none. Suggest `rel.sort.mixed-numeric-and-nonnumeric-text-is-a-total-order`.

#### CPP-C44 [low] [confirmed, all five hosts] `dependencies()` is "read anywhere minus assigned anywhere", not "read without having assigned it first"
- Found by: cpp-3 C10. Re-verified by synthesizer.
- Where: `cpp/sel.cpp:8130-8143` (`Program::dependencies`: `reads` minus `assigned`), 7083-7153 (`collect`); spec §8 and the `sel.hpp` comment say "reads without having assigned it first".
- What: order is ignored and compound assignment counts as an assignment, so a variable that must come from the host is dropped (`X += 1` raises E_UNDEF_VAR unless X exists). `sql-conditions.md` builds SQL column bindings from `dependencies()`, so a rule using `TOTAL += 5` would get no binding.
- Repro: `cpp/build/sel --deps -e 'X += 1'` prints nothing; `... 'X + 1; X = 2'` prints nothing (JS/PHP/Lisp/Python identical).
- Fix sketch: implement first-use order (a variable read before its first plain `=` in evaluation order is a dependency; compound targets are reads), or reword the spec to set-difference semantics; then add cases.
- Conformance gap: `dependencies()` is exercised only by `tools/check-api` probes; suggest API probes for these two programs.

#### CPP-C45 [low] [confirmed, all five hosts] `SORT_BY(...) .> TAKE(n)` fused into `TOP_BY` changes which error is raised
- Found by: cpp-3 C8.
- Where: `cpp/sel.cpp:7641-7652` (`opt_logical_steps` SORT/SORT_DESC/SORT_BY + TAKE fusion).
- What: the fused `TOP_BY` evaluates `n` before the per-element key, so a program whose key raises AND whose `n` is bad reports the `n` error; written unfused (sort, then TAKE) it reports the key error. §7.4 says a fused step must still evaluate the list but is silent on argument order.
- Repro: `L = (RECORD("a",1), RECORD("a",2)); L .> SORT_BY(_["k"]) .> TAKE("x")` gives `E_NOT_NUM col 66` on all five; the helper form (`S = SORT_BY(L, _["k"]); TAKE(S, "x")`) gives `E_NO_KEY col 53`. Same with `TAKE(1/0)` (E_DIV_ZERO vs E_NO_KEY) and `TAKE(Q)` (E_UNDEF_VAR vs E_NO_KEY).
- Fix sketch: spec the order, or fuse only when `n` is a literal (cannot raise).
- Conformance gap: `rel.optimiser.*` covers MAP/FILTER swaps, not a raising TAKE count after a raising sort key.

#### CPP-C46 [low] [confirmed, all hosts] E_DEPTH from equality/hash paths carries no position (`line 0 column 0`)
- Found by: cpp-3 C11, cpp-5 C7.
- Where: `cpp/sel.cpp:3394-3400` `is_in()` (called at 3509 without `node.pos`, while `EQL` passes it at 3508); `dedupe` (5330-5350) and `do_bucket` (5925-5953) call `structural_hash()`/`eql()` with the default `Pos pos = {}`.
- What: "Errors carry the innermost failing node's position" (SPEC §6.3, CLAUDE.md). These calls happen inside a builtin that has `a.pos()` / `key_node->pos`. cpp-3's values nested past MAX_DEPTH are only buildable through the host API, but cpp-5's repro is source-only.
- Repro: cpp-5: `X = LIST(1); MAP(SPLIT(REPEAT("a,",197),","), (X = LIST(X); 1)); COUNT(DISTINCT(LIST(LIST(X))))` gives `E_DEPTH at line 0 column 0` in all five hosts; same for `BUCKET(LIST(LIST(X)), _, COUNT(_))`. cpp-3: `D EQL D` reports `1:3` but `D IN LIST(D)`, `DISTINCT(LIST(D,D))`, `BUCKET(LIST(D,D), _)` report `0:0` (host-built 3M-level chain, `scratchpad/cpp3/src/t2.cpp`).
- Fix sketch: pass `node.pos` / `a.pos()` / `key_node->pos` into `eql`/`structural_hash` at these sites.
- Conformance gap: `10-limits` pins the codes, not the position for these builtins. Suggest `limits.depth.distinct-element-position` and `limits.depth.bucket-key-position`.

#### CPP-C47 [low] [confirmed, all five hosts] `.>` placeholder spec gaps: multiple `_` evaluate the head once per placeholder; binding functions are exempt from substitution
- Found by: cpp-1 C5, cpp-1 C6. Both re-verified/consistent across hosts; the `2/2` case re-verified by synthesizer.
- Where: `cpp/sel.cpp:3053-3059` (`args[i] = left` for every bare `_`) and 3052 (`!spec->binds && count >= spec->min`).
- What: SPEC §5.10 rule 3 says "one of the top-level arguments is a bare `_` ... that placeholder is replaced by x" (singular). With two placeholders the same NodePtr is placed twice and evaluated twice, so a side-effecting head runs twice. Separately, binding builtins (FILTER, MAP, ...) skip the substitution, in all five hosts (for example `js/src/parser.mjs:326`), which the spec and grammar.md do not state.
- Repro: `N = 0; (N = N + 1) .> MAX(_, _) & "/" & N` prints `2/2` on all hosts (single evaluation would give `1/1`). `(1,2,3) .> FILTER((4,5,6), _)` gives `E_EXPECT_SYMBOL at line 1 column 20` on all hosts.
- Fix sketch: decide in the spec: reject more than one placeholder (E_SYNTAX at the second `_`), replace only the first, or state per-placeholder evaluation; document the binding-function exemption in SPEC §5.10 and grammar.md.
- Conformance gap: `14-pipeline.selt` has `pipe.placeholder.first/second` only. Suggest a double-placeholder case and `pipe.placeholder.binding-function-is-not-substituted`.

#### CPP-C48 [low] [unconfirmed against spec intent] E_UTF8 for invalid source bytes carries position 0:0
- Found by: cpp-1 C7.
- Where: `cpp/sel.cpp:2365` (`decode_utf8(source)` with default `Pos{}`), 97-135.
- What: SPEC §2 says "Invalid UTF-8 in source is E_UTF8 at the offending byte". The error is raised with `Pos{}`; only the message says "at byte N". C++, PHP and Python behave alike. A code-point position is not defined for undecodable bytes, so this may be intended. Flagged for a spec owner.
- Repro: `sel -e "$(printf '1 + "a\xffb"')"` gives `E_UTF8 at line 0 column 0: invalid start byte 0xff at byte 6`.
- Fix sketch: document "position 0:0, byte offset in the message" in `spec/errors.md`, or report the line/column of the last valid code point before the bad byte, in every host.
- Conformance gap: cannot be expressed in `.selt`; `tools/check-api.sh` could carry a byte-level case.

#### CPP-C49 [low] [confirmed, all hosts; not C++ bugs] Regex validator gaps: class escape next to `-` becomes a range; `(*VERB)`/quantifier-after-anchor acceptance differs
- Found by: cpp-4 C10, C11.
- Where: `cpp/sel.cpp:6704-6708` (`expand_inside`) feeding `validate_class`; 6791-6801 (`(` handler) and 6803-6813 (quantifier handler).
- What: (a) `\s` is rewritten in place to ` \t\n\r\f\x0b`, so `[\s-a]` becomes a range U+000B..`a` and matches `Z`, consistently on all five hosts (ES/PCRE would treat it as an error or literal hyphen); `[\w-.]` becomes an out-of-order range and a cryptic `E_REGEX_SYNTAX`, a spelling common in real patterns. (b) PHP's PCRE reads `(*ANY)` as a start verb (`RMATCH("(*ANY)a","a")` is TRUE on PHP, `E_REGEX_SYNTAX` on cpp/js/py); Lisp accepts `^*a`, `$*`, `$+`, `^{1,2}x` where the other four raise (541 Lisp-only mismatches in the random run; those inspected were all this acceptance). SRELL rejects these, so C++ is correct.
- Repro: `RMATCH('[\s-a]', 'Z')` gives `TRUE` on all five. `RMATCH('[\w-.]', 'a')` gives `E_REGEX_SYNTAX` on all.
- Fix sketch: in `validate_class` reject (or escape) a `-` directly before or after an expanded class escape; in `validate_pattern` track "previous token is quantifiable" and reject a quantifier that follows `(`, `|`, start of pattern, `^` or `$`. Same change in all hosts.
- Conformance gap: `re.class-escape-inside-class` (`09-regex.selt:290`) does not exercise adjacent hyphens. Suggest `re.reject.class-escape-range-endpoint` and `re.reject.quantifier-on-anchor` (`^*`, `$+`, `(*ANY)`).

#### CPP-C50 [low] [confirmed] Some valid patterns are refused or very slow to compile in C++ (SRELL limits leak out as `E_REGEX_SYNTAX ... error_complexity`)
- Found by: cpp-4 C7.
- Where: `cpp/sel.cpp:6876-6882`.
- What: (a) group nesting deeper than 256 is rejected with the unhelpful message `error_complexity`: `RMATCH(REPEAT('(?:',300) & 'a' & REPEAT(')',300), 'a')` is `E_REGEX_SYNTAX` in cpp and php, TRUE in js, python, lisp. (b) Compile time is super-linear in the number of groups: `(a)` x2000 is 77 ms, x4000 234 ms, x8000 1.3 s, x100000 did not finish in minutes for a 300 KB pattern. There is no pattern-length or group-count cap anywhere (SPEC §6.4 lists only the quantifier bound), so a user-supplied pattern is a CPU sink. (c) `(.{65535}){65535}` is accepted by cpp/js/py and rejected by PHP (PCRE size limit).
- Fix sketch: spec a pattern-size / nesting / group cap in `spec/limits.json`, enforce it in `validate_pattern` (all hosts) with a message that names the limit.
- Conformance gap: none.

#### CPP-C51 [low] [unconfirmed, reasoned] `long` (32-bit on Windows/LLP64) truncates huge counts in the text builtins
- Found by: cpp-4 C8.
- Where: `cpp/sel.cpp:3885, 3900` (`index_of_cp`, `slice_cp` take `long`), 6214-6236 (`LEFT`, `RIGHT`, `SUBSTR`, `FIND` `static_cast<long>(a.non_neg_int(..))`).
- What: `non_neg_int` saturates at LLONG_MAX; where `long` is 32-bit, the cast gives -1, so `LEFT("abc", 9223372036854775807)` would clamp to `""` instead of `"abc"`, and any count over 2^31 misbehaves. `conanfile.py:58` shows Windows is a packaged target. Fine on this LP64 box; not run.
- Fix sketch: use `long long` / `std::ptrdiff_t` (or `size_t` after clamping to the size).
- Conformance gap: none. Suggest `text.left.huge-count` (`LEFT("abc", 9223372036854775807)` = `abc`) so any LLP64 build fails it.

#### CPP-C52 [low] [confirmed] `FROM_HEX` / `DECODE_BASE64` put a single byte of a multi-byte character into the error message (invalid UTF-8)
- Found by: cpp-4 C9.
- Where: `cpp/sel.cpp:6473-6474` (`s.substr(i*2, 2)`), 6516 / 6522-6524.
- What: the messages slice by bytes, so non-ASCII input produces a message that is not valid UTF-8 (`DECODE_BASE64("é==")` -> `invalid base64 character "\xC3"`; `FROM_HEX("aéb")` -> `"a\xC3" is not hex`). An embedder that serialises `SelError::message()` as JSON/UTF-8 will choke. Code and position are correct.
- Repro: `cpp/build/sel -e 'DECODE_BASE64("é==")' | xxd` shows a lone `c3` inside the quotes.
- Fix sketch: report the whole code point, or omit the character.
- Conformance gap: n/a (messages are not asserted).

#### CPP-C53 [low] [confirmed] A registered dialect can drop or misdefine `textEscape`, and text literals are then emitted unescaped
- Found by: cpp-7 C5. JS `emit.mjs:134-161` behaves the same.
- Where: `cpp/sel_sql_emit.cpp:154-158` (`text_literal`), `cpp/sel_sql_map.cpp:241-271` (`check_lexical` allows a withdrawn or any escape map).
- What: the JSON dialects escape correctly (ansi/pg/sqlite `'`->`''`, mysql also `\`), but `text_literal` falls back to `quote + text + quote` when no escape map is found, and `check_lexical` accepts `null` ("withdrawal is always allowed") or a map that does not escape the quote. Everything downstream trusts that one place to stop SQL injection in inline mode. The C++ registration also does not reject an empty `from` key (JS does); `text_literal` skips it, so it is harmless.
- Repro (`cpp7/esc.cpp`, not re-run): `DialectSpec::extending("sqlite").lexical("textEscape", std::nullopt)` (or `lexical_escapes("textEscape", {{"x","y"}})`), translate `NAME $== "a' OR '1'='1"` inline -> `CAST('a' OR '1'='1' AS TEXT) COLLATE BINARY = ...` (injection). Shipped dialects emit `'a'' OR ''1''=''1'`.
- Fix sketch: in `text_literal` refuse (`E_SQL_UNSUPPORTED`) unless an Escapes rule exists whose `from` is the first char of `textQuote` and whose `to` contains it doubled/escaped; or validate in `define_dialect` / at first use.
- Conformance gap: none. Suggest `neutral.register.text-escape-withdrawn-refuses-text-literals`.

#### CPP-C54 [low] [confirmed] `execute_hybrid` on a pure_memory plan mutates the caller's context; the hybrid path clones it
- Found by: cpp-7 C6. JS `executeHybrid` has the same asymmetry by reading.
- Where: `cpp/sel_sql_hybrid.cpp:853-870`.
- What: pure_memory returns `continuation_program->run(context)` on the caller's Value (a shared handle), while hybrid runs on `context.clone()`. Assignments (helper `Y = 5;`) therefore appear in the caller's context only when nothing was pushed down.
- Repro (`cpp7/ctx.cpp`, not re-run): `Y = 5; ORDERS .> SORT_BY(REPEAT(_["name"],2)) .> MAP(RECORD("y", Y))` -> pure_memory, `ctx.has("Y")` is 1 after; hybrid and pure_sql plans of similar programs leave it 0.
- Fix sketch: clone (or document) uniformly. Cloning costs O(size of context) (P24); a shallow copy of the top-level record is enough for the assignments.
- Conformance gap: none.

#### CPP-C55 [low] [confirmed] SQL: case-colliding duplicate relation field names: C++ keeps the first, Python keeps the last
- Found by: cpp-6 C8.
- Where: `cpp/sel_sql_binding.cpp:114-123` (`emplace_back` with no de-duplication), lookups `RelationSpec::field` (:140-145).
- What: `Binding::relation(..., {{"A", col x}, {"a", col y}})` stores both under key `A`; `field("A")` returns the first, while the other hosts assign into a map so the last wins. The binding spec says nothing, so this is a divergence and a silently ignored declaration.
- Repro (private driver): `drv mariadb 'ANY(R, I, I["A"] > 0)' 'R=r:t:t:::A/x/NUM;a/y/NUM'` gives `... (`t`.`x` > 0)`; Python gives `(`t`.`y` > 0)`.
- Fix sketch: refuse duplicates with E_SQL_BINDING (preferred) or overwrite in place.
- Conformance gap: none. Suggest `bind.relation.duplicate-field-after-uppercasing`.

#### CPP-C56 [low] [confirmed] SQL: `correlate` (application SQL) is spliced without parentheses into an AND chain
- Found by: cpp-6 C9.
- Where: MAP `all`/`any`/`sum`/... skeletons via `relation_slots` (`cpp/sel_sql_translator.cpp:1649-1657`); statement WHERE assembly 3658-3685.
- What: `WHERE {corr} AND (...)`. A correlate with a top-level OR changes meaning by precedence (`a OR b AND body`).
- Repro (private driver): `drv mariadb 'ANY(ITEMS, I, I["QTY"] > 0)' 'ITEMS=r:oi:oi::oi.a=o.id OR oi.b=o.id:QTY/qty/NUM'` gives `` EXISTS (SELECT 1 FROM `oi` `oi` WHERE oi.a=o.id OR oi.b=o.id AND ((`oi`.`qty` > 0)) IS TRUE) ``.
- Fix sketch: wrap as `({corr})` when it is not the literal `TRUE` (changes the bytes of every relation case, so decide explicitly), or document "must be a single conjunct".
- Conformance gap: none.

#### CPP-C57 [low] [confirmed] SQL: the discarded pre-LINK `compile_statement` leaves orphan parameters in `Fragment::params()`
- Found by: cpp-6 C10. Same in Python.
- Where: `cpp/sel_sql_translator.cpp:3300-3302` (`(void)compile_statement(plan)` appends to `params_`/`param_kinds_`/`caveats_`), copied into the final Fragment at 3767-3769.
- What: `bindings()` is derived from the part list and is right; `params()` (a public accessor) has extra entries, and the slot ids of the real statement start past the orphans.
- Repro: `R .> FILTER(_["N"] $== "zz") .> SORT_BY(_["N"]) .> LINK(S, _1["A"] == _2["A"] AND _2["M"] $== "yy")` in params mode gives `params=3 bindings=2`.
- Fix sketch: snapshot and restore `params_`, `param_kinds_`, `caveats_` around the discarded compile.
- Conformance gap: cases compare `bindings()`; suggest asserting `params().size() == bindings().size()` in the runner.

#### CPP-C58 [low] [confirmed] SQL: NUL bytes reach the SQL through RECORD aliases and inline text literals, although binding identifiers refuse them
- Found by: cpp-6 C11.
- Where: `cpp/sel_sql_translator.cpp:3561` (`" AS " + emit_.ident(*proj.alias)`), `cpp/sel_sql_emit.cpp:154` (`text_literal`); vs `cpp/sel_sql_binding.cpp:33-42` (`check_name` refuses NUL).
- What: `RECORD("a\u{0}b", ...)` yields `AS "a<NUL>b"`, and `"x\u{0}y"` yields a literal with a raw NUL in Inline mode (Params mode unaffected). With a C-string driver the statement is truncated at the NUL to an unterminated identifier/literal (an error, not an injection).
- Repro (private driver): `drv sqlite 'R .> MAP(RECORD("a\u{0}b", _["A"]))' 'R=r:items:i:::A/a/NUM' --stmt` gives `SELECT "i"."a" AS "a\0b" FROM ...` (hexdump shows 0x00).
- Fix sketch: refuse NUL in aliases (E_SQL_SHAPE) and in inline text literals (or force Params mode).
- Conformance gap: none.

#### CPP-C59 [low] [confirmed] SQL: TAKE/DROP count evaluation is stricter than the evaluator, and uses codes outside `sql/errors.md`
- Found by: cpp-6 C12.
- Where: `cpp/sel_sql_translator.cpp:3371-3402` (`eval_int_param`).
- What: `TAKE((1,2,3), 2.0)`, `-0` and `99999999999999999999999` are accepted by the evaluator (`{"1","2"}`, empty, all three) but the translator answers E_NOT_INT / E_RANGE / E_RANGE. All are safe refusals, but E_NOT_INT/E_RANGE/E_NOT_NUM/E_ARITY/E_BAD_ARG are SEL runtime/compile codes that `sql/errors.md`'s registry does not list for translator refusals (every host does the same, so it is a registry gap). `TAKE(N)` with an unbound/column `N` is reported as E_SQL_INVALID ("SEL rejects ... E_UNDEF_VAR"), which reads as if the rule were wrong.
- Fix sketch: accept numerals that are integral in value (scale ignored, -0 is 0), clamp huge counts to INT64_MAX, and use E_SQL_SHAPE/E_SQL_UNSUPPORTED for "not knowable at translation time".
- Conformance gap: `stmt.refusal.take-float` pins E_NOT_INT for 1.5 only; nothing for 2.0.

#### CPP-C60 [low] [unconfirmed on a server] SQL: `SORT_BY` then `MAP` then `DISTINCT` emits `SELECT DISTINCT proj ORDER BY <unprojected column>`
- Found by: cpp-6 C13.
- Where: `cpp/sel_sql_translator.cpp:3243-3249` (DISTINCT wraps only when limit/offset are set) with the MAP-after-sort rule at 2811-2820.
- What: the statement is `SELECT DISTINCT CAST("i"."a" AS TEXT) COLLATE "C" AS "a" FROM "items" "i" ORDER BY "i"."n" ASC`. PostgreSQL rejects it (42P10); MySQL 8 with ONLY_FULL_GROUP_BY rejects it (3065); MariaDB accepts it with an unspecified representative row, whereas SEL's DISTINCT keeps the first-seen element in sorted order. Reasoned from documented server behaviour, not run. A loud error is acceptable; silent misordering on MariaDB is not.
- Repro: `drv postgresql 'R .> SORT_BY(_["N"]) .> MAP(RECORD("a", _["A"])) .> DISTINCT' 'R=r:items:i:::A/a/TEXT;N/n/NUM' --stmt`.
- Fix sketch: refuse a DISTINCT that follows a sort (in-memory continuation), as is already done for other unrepresentable orders.
- Conformance gap: no case orders before DISTINCT.

---

## Performance findings

Ordered by impact. "Prior work" means what I found under `docs/interim/cpp-collection` (benchmark logs only) plus the README of `tools/cpp-collection`, the CHANGELOG entry for commit `a003a73`, and its benchmark review (read from git history). That work targeted container allocation (co-allocated list/record payloads, compact 72-byte scalar header, optional decimal payload; a bounded collection-block pool and an inline-decimal patch were rejected) and the S1-S6 scale scenarios and Mandelbrot. Anything not in that area is new. Items adjacent to that work are flagged.

#### CPP-P1 [impact: high (DoS)] [measured] Big-by-big decimal division is O(n*m*9) with a string allocation per step, and division falls off a cliff at about 28 digits
- Found by: cpp-2 P1, P2. New.
- Where: `cpp/sel.cpp:649-660` (`divmod_abs` generic branch), reached from `dec_div` (1129-1132) and `dec_mod` (1172-1177); small path 1096-1128.
- What / why: long division one decimal digit at a time. `rem = strip(rem + a[i])` allocates and copies an m-digit string, then up to 9 rounds of `cmp_abs` + `sub_abs`, and each `sub_abs` converts both strings to base-1e9 limbs and back (3 allocations). The int128 small path multiplies `|a| * 10^n_pow` and gives up on overflow, so any dividend above about 28 significant digits takes the string path.
- Evidence (measured; cpp-2 box at load about 16, so absolutes are inflated 2-3x): `LEN(POWER(9, 100000) / POWER(7, 50000))` (95k / 42k digits): C++ 116.8 s, JS 0.23 s. `POWER(3,40000)/POWER(7,20000)`: 2.09 s vs 0.18 s. A quotient of 20k/10k digits takes 0.44 s (2x size -> 4.7x time). A 40-character program can pin a core for many minutes (caps allow 1M-digit operands). Realistic data: `dec_div` 30-digit/20-digit 58 us, 43-digit/20-digit 86 us, versus 210 ns for small operands. 200,000 `A / B` with those operands: C++ 16.8 s, JS 1.2 s; 200,000 `%` with a 61-digit dividend: C++ 19.4 s, JS 0.78 s.
- Fix sketch: Knuth algorithm D on the existing base-1e9 limbs (O(n*m/81), no allocation); keep the single-limb and power-of-ten fast paths; use it whenever the int128 path overflows, and for `dec_mod` above 38 digits. Byte-identical (exact integer division).

#### CPP-P2 [impact: high (DoS) / medium otherwise] [measured] Schoolbook multiplication with no early E_RANGE rejection
- Found by: cpp-2 P3. New.
- Where: `cpp/sel.cpp:1057-1063` (`dec_mul` limb path: `dec_guard(dec_from_limbs(..., mul_limbs(la, lb), ...))`), 422-486 (`mul_limbs`), 488-555 (`sqr_limbs`), 1261-1271 (`dec_power`).
- What: the result-size guard (`MAX_INT_DIGITS`/`MAX_FRAC_DIGITS`) runs only after the O(n*m) product, though the product's digit count is known within one digit from the operands (at least `da + db - 1`, scale exactly `sa + sb`); and there is no Karatsuba.
- Evidence (measured): `POWER(9999999999999999999999999999, 100000)` (28-digit base, doomed to E_RANGE): C++ 18.6 s, JS 1.1 s, Python 4.8 s. `A = POWER(99999999, 100000); A * A` (E_RANGE): 15.4 s vs 1.1 s. A legal 800k-digit `POWER(99999999, 100000)` takes 5.7 s vs 1.3 s in JS. `REPEAT("9",500000) * itself` 3.4 s vs 1.8 s.
- Fix sketch: in the limb path compute `min_digits = digits(a) + digits(b) - 1` and `fail("E_RANGE")` before multiplying if `min_digits - (sa + sb) > MAX_INT_DIGITS` or `sa + sb > MAX_FRAC_DIGITS`; do the same in `dec_power` (estimate `digits(base)*n`); add a Karatsuba threshold (about 64 limbs). Error code and position unchanged.

#### CPP-P3 [impact: high] [measured] SQL `IN` / `ANY` / `JOIN` literal lists translate in O(N^2) (`fold_pairwise` re-splices the accumulator through `Emit::fill`)
- Found by: cpp-6 P1, cpp-7 P1. New.
- Where: `cpp/sel_sql_translator.cpp:1389-1402` (`fold_pairwise`), 1808 (`IN` list), 2680 (`aggregate` unroll), 2802 (`JOIN`); helpers `apply` (:1333), `Emit::fill` (`cpp/sel_sql_emit.cpp:244-351`, `splice` lambda copying every part), `Fragment` copy semantics.
- What / why: each step does `const Fragment pair[] = {acc, parts[i]}` (a full copy), `apply` -> `fill` splices both into a new vector (second copy), and every literal is its own part, so the vector has about 2N elements of 48+ bytes. The left-nested output `((((a OR b) OR c) OR d)...` is inherently O(N) long, but building it stepwise copies O(N) per step.
- Evidence (measured): cpp-6 (driver, -O1): `ANY((0..n-1), _ == X)` n=1000 103 ms, 4000 1.59 s, 16000 34.4 s (357 KB output); `X IN (0..n-1)` n=1000 133 ms, 4000 2.5 s, 16000 33.7 s (1.6 MB); exponent about 1.9. cpp-7 (-O2, mariadb): `ID IN (0..n-1)` n=1000 78 ms, 5000 2.06 s, 20000 43.4 s (text list: 91 ms / 2.06 s / 46.5 s). gprof at n=4000: 49% in the `push` lambda of `fill` (16M calls), 14% `vector<Part>::push_back`, 13% `fold_pairwise`, 8% `Fragment` copy ctor (48k copies). Compile is 8-17 ms at n=20000, so translation is the whole cost. JS shows the same curve (16,000 items: 8.7 s).
- Fix sketch: byte-identical linear build: resolve the operator template once; if it has the shape `pre{0}mid{1}post` (all shipped AND/OR/+/& entries do), emit all `pre`s first in reverse order, then item0, then `mid_i item_i post_i` (a single vector appended in place); fall back to the current loop otherwise. At the very least `std::move` `acc` into the pair and use a span over the two operands. Note the unrolled output nests N levels deep and `fold_pairwise` is outside the `node()` depth guard, so servers with parser recursion limits may reject long IN lists (reasoned, not tested against a DB); a documented cap is worth considering.

#### CPP-P4 [impact: medium-high] [measured] `??` / `???` on a missing key cost about 20 microseconds each (implemented with a C++ exception)
- Found by: cpp-3 P1. New.
- Where: `cpp/sel.cpp:3476-3484` (`eval_binary`, `try { eval_node(l) } catch (SelError&)` around E_NO_KEY / E_UNDEF_VAR).
- What / why: the missing-field default (`REC["opt"] ?? 0`) is the common idiom for optional data. A miss allocates a `SelError` (two strings and a formatted message with `quote_dump`) and unwinds through every `eval_node`/`eval_dispatch` frame.
- Evidence (measured): `L = SPLIT(REPEAT("a,",300000),","); COUNT(MAP(L, (_["k"] & "x") ?? 0))` = 6.09 s CPU (20 us per miss) vs 0.27 s for the non-throwing `HAS(_, "k")` equivalent; JS is 8.5 s, so this is not C++-only, but C++ has the most to gain.
- Fix sketch: when the left operand is a `Var` or an `Index` chain over `Var`/`Text` keys (no calls), evaluate it with a non-throwing probe (walk the chain, return "missing" instead of `fail`) and go straight to the right side; keep try/catch for anything else. Results and positions identical, since a pure chain can only fail with those two codes.

#### CPP-P5 [impact: medium] [measured] Operator dispatch by string comparison and per-evaluation literal allocation dominate small-predicate loops
- Found by: cpp-3 P2, cpp-2 P6. New. Literal caching is adjacent to the container-allocation work but not part of it.
- Where: `cpp/sel.cpp:3463-3534` (`eval_binary`: up to about 15-17 `std::string == const char*` tests, then `is_compare_op(Token{Tok::Op, op, {}})` which constructs a Token and does a `std::set<std::string>` lookup, then up to 6 more compares in `compare_result`), 2714-2716, 3364-3375, 3702-3711 (`Num`: `Internals::from_dec(*node.dec)` copies the `Dec` and heap-allocates a new one per evaluation; `Text`: `make_text(node.s)` copies per evaluation). `as_dec` returns a 96-byte `Dec` by value, twice per operator.
- Evidence (measured): gprof of `COUNT(FILTER(L, _ > 5 AND _ < 900000))` over 1M elements: 39,000,100 calls to `operator==(string const&, char const*)` for 8,000,004 node evaluations (about 9% of self time), 2M `std::set::find` and 6M `Dec` copy constructions. Experiment (scratchpad `cpp3/exp`, not in repo; -O2, best of 5 CPU seconds, box shared so +/-15%): cache an opcode on the Node and cache literal `Value`s on the Node: `COUNT(FILTER(L, _K > 5))` 1.66 s to 1.04 s; `COUNT(FILTER(L, _ > 5 AND _ < 900000))` 1.54 to 1.22 s; `SUM(L, _*2+1)` 1.29 to 1.09 s (list construction of about 0.55-0.8 s is included in each figure). cpp-2: `MAP(XS, _ > _)` about 700-870 ns/element vs about 430-510 for `_ + 1` (compiled plan); the dispatch share was not isolated (guess).
- Fix sketch: resolve the operator to an enum once in the parser/optimiser (stored in `Node`) and switch on it; hold a shared immutable `Value` for Num/Text/Bool/Null literal nodes (safe: `=`/`,`/aggregates clone what they keep). In `Context` rather than the shared AST if a non-atomic `ref_count` would be shared across threads (cpp-2's caution). Route comparison through a `dec_cmp`-returning plan op or the math plan. Byte-identical output.

#### CPP-P6 [impact: medium] [measured] 128-bit divisions by 10 in hot formatting and rounding paths
- Found by: cpp-2 P4. New.
- Where: `cpp/sel.cpp:576-585` (`dec_digits_from_magnitude`), 866-902 (`dec_format_buf`), 927-938 (`dec_trim_scale`), 1112-1120 (`dec_div` exact case), 1192-1199 (`dec_round`), 1275-1284 (`dec_to_int`), 4530-4533 (join key trim loop).
- What: `__int128 % 10` and `/ 10` compile to `__modti3`/`__divti3` calls (25-40 ns each); a 7-digit number pays 14 of them.
- Evidence (measured): `dec_format` of `12345.67` 284 ns, `dec_format_buf` 127 ns, `dec_add` 52 ns, `dec_mul` 47 ns, `dec_div` small 210 ns.
- Fix sketch: when the magnitude fits in 64 bits (almost always) use `uint64_t` division or a two-digit table; use `dec_format_buf`-style output in `dec_format` too; count trailing zeros in 64 bits for the exact-division trim.

#### CPP-P7 [impact: medium] [measured] `LIST` / `RECORD` (and `,`, `=`, MAP collection) deep-clone freshly built temporaries
- Found by: cpp-5 P1, cpp-2 P5(a). Adjacent to the container-allocation work (that work changed allocation shape, not when clones happen).
- Where: `cpp/sel.cpp:5155-5168` (`LIST`: `a.val(i).clone()`, `RECORD`: `a.val(i + 1).clone()`), 3423ff (`eval_list` clones every collected element), 1522-1554 (`clone_at`).
- What / why: the clone is required for values reachable from a variable, but a value returned by MAP/FILTER/RECORD/arithmetic is referenced only by `Args::vals_`, so cloning it copies the whole tree for nothing (O(size) time, 2x transient memory).
- Evidence (measured, cpp-5): 300k-row `MAP(S, RECORD("id",_K,"g",_K,"v",_K))` takes 1.2-1.5 s; wrapping it in one `LIST(...)` adds 0.5-0.65 s, and each further wrapper the same (`LIST(LIST(LIST(LIST(MAP(...)))))` 2.7 s). Typical programs do `RECORD("rows", FILTER(...), ...)`. cpp-2: `MAP(XS, 5000)` costs 300-400 ns per element more than `MAP(XS, _)` (literal materialisation + clone + free).
- Fix sketch: give `Args` a `take_val(i)` that moves the value out of `vals_[i]` and, if the Impl's refcount is 1 and it is scalar-only or uniquely owned, returns it without clone; clone only when shared. The same test applies to the assignment store in `eval_assign` (`eval_node(rhs).clone()`) and to `,`. Byte-identical.

#### CPP-P8 [impact: medium] [measured for the pieces, reasoned for the total] Every numeric Value costs two allocations and 192 bytes
- Found by: cpp-2 P5(c). Adjacent to prior work (the inline-decimal patch was measured there and not adopted wholesale).
- Where: `cpp/sel.hpp:133-140` (`Dec` is 96 bytes: string + vector + int128, all always present), `cpp/sel.cpp:1407-1413` (`from_dec` = `Impl` + `make_unique<Dec>`).
- Evidence: `tools/benchmark-cpp-value.cpp`: an integer Value retains 192 bytes vs 80 for text; 200k integers build in 39 ms; `Value::integer` 110 ns (2 mallocs). `SUM(XS, _)` 130 ns/element, `MAP(XS, _ + 1)` 430 ns, `MAP(XS, _ > 5000)` 930 ns/element (loaded box).
- Fix sketch: shrink `Dec` for the small case (union of mantissa / heap bignum) so the number payload lives inside `Impl`; keep the compact scalar header the prior work protected.

#### CPP-P9 [impact: medium] [measured] Text builtins decode the whole string to `vector<char32_t>` even when a byte scan would do
- Found by: cpp-4 P2. New.
- Where: `cpp/sel.cpp:3883` (`cps_of`) used by `LEN`, `LEFT`, `RIGHT`, `SUBSTR`, `CODE`, `TRIM`/`LTRIM`/`RTRIM`, `UPPER`/`LOWER` (`ascii_case`, 6162), `BACKWARDS`, `FIND`, `REPLACE`, `SPLIT`.
- What / why: each call allocates 4 bytes per code point and re-encodes the result, though the operation is byte-decidable: `LEN` = count of non-continuation bytes; `UPPER`/`LOWER` = byte loop (bytes 0x80+ are never A-Z, UTF-8 is self-synchronising); `TRIM` = scan the ends; `LEFT(x,n)`/`CODE` = walk until n code points; `FIND`/`REPLACE`/`SPLIT` = byte search of the valid UTF-8 needle.
- Evidence (measured, 40 MB text, `BLEN(f(S))` minus base 277 ms, noisy): `UPPER` +1320 ms, `BACKWARDS` +1200, `TRIM` +870, `LEFT(S,3)` +500, `LEN` +425 (vs `BLEN` +35). For short strings about 0.5 us per call (`FILTER(L, LEN(_)==4)` over 1M strings 1.0-1.7 s vs `BLEN` 1.0-1.5 s).
- Fix sketch: byte fast paths as above; byte-identical since input is already valid UTF-8.

#### CPP-P10 [impact: medium] [measured baseline, reasoned cause] Sort comparator re-classifies both keys on every comparison
- Found by: cpp-5 P3. New.
- Where: `cpp/sel.cpp:5570-5610` (`compare_values`), 5745 (`stable_sort`).
- What: each comparison calls `looks_numeric()` twice (a `scalar_source` walk + a decimal-cached check; on an uncached text it also runs `dec_parse` without caching), then `as_dec` twice (returns a `Dec` by value), then `dec_cmp`.
- Evidence: 200k numeric-text keys, `SORT(X)` costs about 620 ms, roughly 3 us/element, about 175 ns per comparison (17.6 comparisons/element). Text-only keys are cheaper (about 2 us/element).
- Fix sketch: decorate once per key (O(n)) into `SortEntry {class, const Dec*/string_view}` and compare the decorated keys. Same total order as today; combine with the C43 fix so the order is defined once.

#### CPP-P11 [impact: medium] [measured] `TOP*` with a large N is 2-3x slower than the full sort it replaces
- Found by: cpp-5 P2. New.
- Where: `cpp/sel.cpp:5790-5865` (`do_top`, heap + a `compare` that string-compares `direction == "DESC"` on every call).
- Evidence (measured; 200k pseudo-random numeric texts; data build 388 ms): `SORT(X)` +620 ms; `TOP(X,10)` +60; `TOP(X,1000)` +96; `TOP(X,20000)` +570 (about a full sort); `TOP(X,100000)` +1220 (2x a full sort). On ascending data with DESC (every element displaces the root) `TOP_DESC(X,1000)` +630 vs `SORT_DESC` +250.
- Fix sketch: hoist `const bool desc` out of the lambda; when `k * 8 >= source_size` fall back to `stable_sort` + truncate (same output, as idx breaks ties); use a hole-based sift instead of swaps.

#### CPP-P12 [impact: medium] [measured] Bare `BUCKET(list, key)` is allocation-heavy per group (about 3.5 us/group)
- Found by: cpp-5 P4. New (adjacent to the S1-S6 join scenarios but not covered by them).
- Where: `cpp/sel.cpp:5895-5960` (`do_bucket`).
- What: per new group: a `GroupEntry` (key Value + key string + `vector<Value> rows{item}` = 1 malloc) pushed into an un-reserved `vector`, plus an `unordered_map<uint64_t, vector<size_t>>` node and a vector (2 mallocs), plus `structural_hash` of every key. The bare path assembles the result with `out.set(key_str, ...)` per group, which copies the key string twice (children + the lazily built index, no reserve, rehashes).
- Evidence (measured): 200k unique numeric-text keys: `BUCKET(X, _)` 1168 ms vs `BUCKET(X,_,COUNT(_))` 888 ms vs `DISTINCT(X)` 483 ms, generation baseline 273 ms; 6 groups: 402 ms (cost is per group, not per row).
- Fix sketch: for the bare form group on `key_str` with one `unordered_map<string,size_t>` (this is the C39 fix); build the result record with `Internals::with_children` in one shot (keys unique by construction); `reserve` `groups`; build the record `index` lazily.

#### CPP-P13 [impact: medium, only on wide nested lists] [measured] Each parenthesised group shallow-copies its node, copying the whole `items` vector
- Found by: cpp-1 P1. New.
- Where: `cpp/sel.cpp:3117-3119` (`auto copy = std::make_shared<Node>(*inner); copy->grouped = true;`).
- What / why: the copy exists only to set `grouped`. The Node copy constructor copies the `items` vector (an atomic refcount increment per element), and `~Node` then walks the original's stolen children again. Nested parens around a wide `,` list or call cost O(depth x width). The inner node was created by `make()` moments ago and is uniquely owned.
- Evidence (measured): `(` x90 + a 200,000-element list + `)` x90 (1.29 MB) compiles in 1.05 s; the same list under one paren pair in 0.14 s (parse stage 1307 ms vs about 90 ms in the instrumented copy). About 7x on that shape, negligible on ordinary rules.
- Fix sketch: make `parse_sequence()`/`parse_list` return `std::shared_ptr<Node>` and set `grouped = true` in place, or use the same `const_cast` justification `~Node` documents (valid for a fresh `make_shared<Node>`). The copy preserves `pos`, so positions do not change.

#### CPP-P14 [impact: medium] [measured] Every SQL `translate()` copies the whole `Bindings` and scans all of them
- Found by: cpp-6 P2. New.
- Where: `cpp/sel_sql.cpp:180` (`Translator t(dialect, bindings, options)` takes `Bindings` by value), `cpp/sel_sql_translator.cpp:300-304, :323` and `cpp/sel_sql_stage1.cpp:466-478` (`const_scope` calls `names()` then `get()`, each allocating an upper-cased key, for every binding to find the scalar `value` ones).
- Evidence (measured, -O2): translating `COL0 > 5 AND COL0 < 10` 20,000 times: 2 bindings 17.9 us/call, 51 bindings 34.9 us, 501 bindings 278.7 us (15x slower for an identical rule).
- Fix sketch: hold `const Bindings&` (or `shared_ptr<const Bindings>`) in the Translator; build the const scope lazily, only for the names in the program's `dependencies()`, or cache the value-binding list inside `Bindings`.

#### CPP-P15 [impact: medium for one-shot use] [measured] `evaluate(source, ctx)` pays the optimiser on every call and it costs about as much as parsing
- Found by: cpp-3 P5. New.
- Where: `cpp/sel.cpp:8114-8117` (`physical_ast` built on first `run`), 8137 (`evaluate` = `compile(source).run(...)`).
- Evidence (measured, `scratchpad/cpp3/src/bench1.cpp`, 100k iterations; compile / run / compile+run): `TOTAL > 10 AND STATUS $== "open"`: 2.15 / 0.93 / 5.19 us (optimiser about 2.1 us). `ROUND(PRICE*QTY*(1+TAX/100)-DISC,2) > 100`: 6.18 / 1.37 / 14.14 us (about 6.6 us). `COUNT(FILTER(ITEMS, ...)) >= 1`: 5.23 / 3.95 / 18.99 us (about 10 us). The header's rationale ("the copy costs more than evaluating a small rule") is right, but the convenience `evaluate()` ends up 2-3x slower than parse+run.
- Fix sketch: have `evaluate()` run the un-optimised `ast_` (or optimise lazily from the second `run` on a Program). Results identical.

#### CPP-P16 [impact: medium] [reasoned + measured deltas] Per-call regex overhead for literal patterns, and the subject is converted twice at 4-8x size
- Found by: cpp-4 P5, P3. New.
- Where: `cpp/sel.cpp:6831-6888` (`compile_regex`, flag decode, key string, global mutex + `std::map` lookup), 6890-6900 (`cps_of(...)` -> `vector<char32_t>` -> `std::u32string(subject.begin(), subject.end())`), 6962-6970 (same in RREPLACE).
- What: every call decodes the flags, decodes the whole pattern to check ASCII when `i` is set, builds a key `(i? "i ":" ") + pattern`, takes one process-wide mutex (also serialising threads on the hot path and holding it during a compile) and does a `std::map` string lookup. The subject makes two full copies; a 40 MB subject peaks at about 320 MB.
- Evidence: 1M `RMATCH` calls about 0.9 us each; the `i` flag adds about 0.5 us for the pattern decode (measured, noisy). `RMATCH("^abc", S)` on 40 MB takes 1170 ms vs `LEN(S)` +425 ms; the difference is the second copy plus SRELL trying the `^` anchor at every start offset (the second part is a guess, not measured separately).
- Fix sketch: literal patterns are known at compile time (C28): validate and compile once when the `Program` is built and keep a `shared_ptr<const Regex>` on the call node. Decode the subject straight into a `u32string` (`decode_utf8_to(std::u32string&)`).

#### CPP-P17 [impact: low-medium] [measured by call count] `node_contains_var` re-scans and allocates on every aggregate call
- Found by: cpp-3 P4, cpp-5 P8 (cpp-5 labelled it guessed; cpp-3's gprof numbers are measured). New.
- Where: `cpp/sel.cpp:4430` (called at 5553, 5701, 5822, 5903, 5968). Each visited `Var` node builds two upper-cased `std::string`s (`upper_name(node.s)` and `upper_name(std::string(wanted))`).
- Evidence: gprof of `COUNT(MAP(L, SUM(LIST(1), _+_+...(40 terms))))` over 100k rows: `upper_name` called 8,000,160 times (80 per inner aggregate call), the top self-time entry (15%), and with the string constructors/destructors about 25% of the run. Matters for small inner lists (nested aggregates); negligible for big lists. cpp-5 measured an inner `ANY(LIST(1), TRUE)` at about 0.4 us per call in total.
- Fix sketch: compute "reads `_K`" once per body node at parse/optimise time (a bool on Node) or compare case-insensitively without allocating; the same change fixes C6.

#### CPP-P18 [impact: low-medium] [measured] RREPLACE re-parses the replacement and makes four temporaries per match
- Found by: cpp-4 P4. New.
- Where: `cpp/sel.cpp:6902-6930` (`expand_replacement`), 6980-6990.
- What: for each match it walks `repl` bytes, builds a `std::string`, decodes it again into a `CodePoints`, and calls `m[g].str()` (another allocation) per `$n`.
- Evidence (measured): `RREPLACE("a","bb",S)` with 5M matches in 40 MB: 2.5 s (about 0.4 us/match); per element on 1M short strings RREPLACE about 2.4 us vs RMATCH about 0.9 us.
- Fix sketch: pre-parse `repl` once into segments (literal u32 chunks and group numbers) and append to the u32 output directly. Do NOT validate `$n` against `mark_count()` up front: the error must fire only when a match happens.

#### CPP-P19 [impact: low-medium] [measured] Base64 decode uses `strchr` per input character; encode appends char by char
- Found by: cpp-4 P6. New.
- Where: `cpp/sel.cpp:6420-6423` (`b64_index`), 6483-6498, 6500-6538.
- Evidence: 40 MB decode about +1.1 s vs encode +0.6 s. A 256-entry constexpr table plus `reserve` should be several times faster (estimate, not measured).
- Fix sketch: 256-entry lookup table; `reserve` the output.

#### CPP-P20 [impact: low-medium] [measured] `FILTER` result always materialises string keys
- Found by: cpp-5 P6. Adjacent to the container work.
- Where: `cpp/sel.cpp:5735-5749`.
- What: every kept element allocates `collection_key()` (`std::to_string`) and a 40-byte `pair<string,Value>`; the result is the unpacked `children` form, so later `entries()`/`find` hit hash-index building at INDEX_THRESHOLD.
- Evidence: `FILTER(A, cond)` over 200k rows adds about 150 ms (about 0.75 us/kept row) vs `MAP(A,_)` about free.
- Fix sketch: detect "kept keys are exactly 1..k in order" while walking (source packed and no gap) and return `Value::list(items)`; fall back otherwise. Byte-identical dump.

#### CPP-P21 [impact: low-medium] [measured; reasoned split] `RECORD` with literal keys builds an intermediate record, then a second shaped Value
- Found by: cpp-5 P5. Adjacent to the prior literal-key RECORD work in JS/C++ (CHANGELOG 2026-09-20: "prepared layout on the call node"); this is the remaining C++ half.
- Where: `cpp/sel.cpp:5170-5186`.
- What: even when `a.record_shape()` is set, it builds `rec` with `set()` per pair (key string copies, linear finds), calls `rec.keys()` (a `vector<string>` copy) to compare with the shape, `rec.entries()` (materialises children), and finally re-packs into `Internals::shaped`.
- Evidence: `MAP(S, RECORD("id",_K))` +0.83 us/row over `MAP(S,_K)`; three fields about 3 us/row vs `LIST(_K,_K)` 0.75 us/row (200k rows). Reasoned that about half is the intermediate structure.
- Fix sketch: if `a.record_shape()`: `storage.reserve(n)`; for each pair evaluate the (literal) key, then `storage.push_back(a.val(2i+1).clone())`; then `Internals::shaped(shape, storage)`. Keep the general path for dynamic keys. Same evaluation order.

#### CPP-P22 [impact: low] [measured / reasoned] Naive O(n*m) substring search, pad and repeat loops
- Found by: cpp-4 P7. New.
- Where: `cpp/sel.cpp:3885-3898` (`index_of_cp`), 6183-6203 (`pad`), 6314-6320 (`REPEAT`).
- Evidence (measured): `FIND(REPEAT("a",20000) & "b", REPEAT("a",200000))` takes 4.2 s (4e9 compares); `REPLACE`/`SPLIT` share it. `PADL("x",20000000,"ab")` 1.2 s; `REPEAT` appends without `reserve` (40 MB in about 280 ms).
- Fix sketch: byte-level `memmem` / `std::string::find` (glibc two-way); `reserve` in `pad` and `REPEAT`. See C8 for the caps.

#### CPP-P23 [impact: low] [reasoned] `DISTINCT` uses `unordered_map<uint64_t, vector<Value>>` (one node and one vector block per distinct value)
- Found by: cpp-5 P7. New.
- Where: `cpp/sel.cpp:5335-5352`. 200k unique numeric texts: about 1.1 us/element.
- Fix sketch: an open-addressing table of `uint32_t` indices into `out` (compare with `eql` against `out[idx]`) removes two mallocs per distinct value.

#### CPP-P24 [impact: low] [reasoned; one guessed] Small per-call and per-run allocation and hybrid-planner overheads
- Found by: cpp-3 P6, cpp-7 P2, P3, P4, cpp-6 P3, P4. New.
- Where and what:
  - `Args` allocates a `std::vector<std::optional<Value>>` per call node evaluation (`cpp/sel.cpp:3279`); use an inline array for at most 4 arguments.
  - `Context` starts the math scratchpad at 32 default-constructed `Dec` (about 100 bytes each, about 3 KB) on the first plan of every `Program::run` (3761); size it to the plan, or pool it.
  - `plan_hybrid` re-normalises and re-translates the same tree up to (steps + 3) times (`cpp/sel_sql_hybrid.cpp:760-766, 789-793, 806, 815-846`, `Helpers::tables` 500-503). Measured negligible for short pipelines (160 filters after an unsupported step: 2 ms) but it doubles the cost for C17-style inputs. Cache the `normalise` result per (tree, scope); try prefixes from the first refusal position.
  - `execute_hybrid` deep-clones the caller's whole context per call (`cpp/sel_sql_hybrid.cpp:868`; guessed). A shallow top-level copy gives the same isolation for top-level assignments (see C54).
  - `is_constant`/`validate` re-run at each compound node, and `binary`/`guard_numeric`/`coerce_scale_limits` each call them again; `SNode::to_node` deep-copies per call (`cpp/sel_sql_translator.cpp:408-427`). O(depth x size) for ordinary chains (reasoned, not measured); cache `{is_constant, validated}` by `const SNode*` per translation.
  - `Emit::fill` appends template text one char at a time via `push(tpl.substr(i,1))` (`cpp/sel_sql_emit.cpp:244-351`); `text_operand` copies `f.parts_` before the cast overwrites it (:228); `classify` deep-copies `RelationSpec` per aggregate (translator.cpp:2390); `Map::lexical`/`chain`/`shipped` allocate per lookup (`cpp/sel_sql_map.cpp:717-777`, under 2% of the time in gprof at n=4000, so only relevant after P3 is fixed).
- Fix sketch: as listed per bullet; all byte-identical.

#### CPP-P25 [impact: low] [reasoned; front-end measured] Front-end allocation and copy overheads
- Found by: cpp-1 P2, P3, P4; cpp-2 P7. New.
- Where and what:
  - Token stream and AST allocate about 60-170 bytes of heap per source byte (`Lexer` `CodePoints chars_` = 4 bytes/byte, `Token` holds a `std::string`, `make_shared<Node>` about 200-230 B, a full `Dec` per number literal; `cpp/sel.cpp:2360-2400, 2798-2803, 3075-3079`). RSS delta after `compile()` (measured): 1.26 MB list of 200k numbers gives 74 MB; 0.6 MB `A[1][1]...` gives 98 MB; 0.34 MB `A0*2 + ...` gives 43 MB. The spec has no source-size limit. Throughput about 10 MB/s (7-12 us per small rule).
  - `Token` is copied by value at each parse step (`cpp/sel.cpp:2888, 2970, 3068`), and text literals are encoded code point by code point (`lex_raw`/`lex_quoted`, 2475-2531). A 5 MB string literal compiles in about 60 ms (measured); using `const Token&`, moving `t.value`, and appending raw byte ranges for escape-free stretches removes about half.
  - `match_operator` scans a 31-entry table with `std::string` compares per operator token and returns a `std::string` (2456-2469); flat `A[1][1]...` (600k tokens) lexes at about 93 ms (about 150 ns/token, measured). A switch on the first code point would avoid the allocation.
  - `dec_cmp` copies and scales limbs when scales differ, and `dec_round` etc. rebuild strings for huge scales (`cpp/sel.cpp:1074-1088, 1201-1207`); a digit-count pre-check settles most comparisons without allocation (reasoned).
- Fix sketch: only worth doing after P13 and C18: keep byte offsets into the source and build tokens as `string_view`s; drop `chars_` after computing line starts.

---

## Cross-host findings (fix must be spec-first across all hosts)

Per CLAUDE.md "The one rule": spec, then conformance case, then every host, then `tools/check.sh`. The items below are shared with, or need a decision affecting, other hosts. C++-only bugs (C20, C21, C22, C24, C25, C41) are omitted, but note that the rule still applies to their conformance cases, and JS/Python were used as oracles in those repros.

| Item | Hosts affected (per reviewers) | Spec decision needed | Suggested spec / conformance case |
|---|---|---|---|
| C4 `??` chain recursion | C++ segfault; JS RangeError, Python RecursionError, Lisp control stack exhausted; PHP answers 1 | Yes: chains of `R`-associative binaries are evaluator-depth-only, or counted in the parser | `lim.coalesce-chain-long-and-short-circuits`, `lim.coalesce-chain-eval-depth` in `10-limits.selt` |
| C5 nested interpolation depth | C++ segfault; JS/Python/Lisp fail at N=20000; PHP quadratic (over 60 s) | Yes: a lexer nesting bound (a value that lets every parse-accepted program lex) | `lim.interp-depth-just-under`, `lim.interp-depth-past-the-cap` |
| C6 uncounted helper walkers | C++ segfault; JS (`nodeContainsVar`), Lisp overflow; PHP dies in the lexer (memory) | No new rule, §6.4 covers it | `lim.eval-depth.flat-chain-inside-aggregate-body` (MAP/FILTER/SORT_BY/BUCKET/TOP_BY, 5,000-term body => E_DEPTH at the 201st node) |
| C7 regex step budget / `error_complexity` | C++ aborts; other hosts answer FALSE (JS takes 8 s at n=26) | Yes: what a step budget does, and which error code | at least a 3M-char subject with `^(a\|b)*c$` => FALSE; `re.limit.*` after the decision |
| C8 REPEAT / PAD caps | C++ hangs on empty and aborts on bad_alloc; JS RangeError, PHP fatal error | Yes: a size cap and its error (E_RANGE?) in `spec/limits.json` | `text.repeat.empty-huge` (`REPEAT("", 9000000000000000000)` => `""`) and a capped-size case |
| C17 / C10 / C23 SQL helper inlining (size, depth, capture) | Python also exponential (n=16 in 1.2 s) and has the binder capture; JS also exponential (n=18: 3 s, 12 MB) | Yes: an output-size/expansion limit and error code; binder capture semantics | `.sqlt`: 30 doubling statements over a column => bounded-time refusal; `norm.inline.def-not-captured-by-binder`; `agg.binder-same-name-as-list-variable`; 250 chained helpers => `E_SQL_DEPTH` |
| C26 RREPLACE empty matches | C++ and PHP vs JS, Python, Lisp | Yes: ES/Python advance-one-code-point rule (simpler) | `re.replace.empty-then-nonempty`: `RREPLACE('a*?', "-", "aab")` => `-a-a-b-`, `RREPLACE('\|a', "-", "aa")` => `-a-a-` (expected values are the JS/Python/Lisp outputs, pending the decision) |
| C27 captures under a quantifier | C++ and JS (ES) vs PHP, Python, Lisp | Yes: reject capturing groups under a quantifier, or pick one semantics | `re.reject.capture-in-repeat` |
| C28 literal-regex compile-time validation | All five hosts | No new rule, the spec says it (`spec/errors.md:34,40`) | bad literal in an untaken `IF` branch, with a compile expectation |
| C49 regex validator gaps | `[\s-a]` all hosts; `(*ANY)` PHP; quantifier after `^`/`$` Lisp | Yes: reject `-` next to a class escape; reject quantifier after `(`, `\|`, start, `^`, `$` | `re.reject.class-escape-range-endpoint`, `re.reject.quantifier-on-anchor` |
| C50 regex size/nesting/group cap | C++ (nesting over 256, super-linear compile) and PHP (PCRE size limit) differ from JS/Python/Lisp | Yes: pattern-size / nesting / group cap in `spec/limits.json` | a limits case per cap |
| C37 plan vs unplanned coercion order | All five | Yes: when operand 1 is coerced relative to operand 2's evaluation | `ord.arith.operand-coerced-before-next-operand-evaluated` (planned and unplanned) |
| C38 aggregate snapshot vs live | C++/JS/Python live; PHP/Lisp snapshot; JS also crashes with a TypeError when the body appends (`js/src/builtins/aggregate.mjs:69`, `:469`) | Yes | `agg.source-mutated-by-body.*` |
| C42 aggregates alias vs copy | Five-way disagreement (PHP aliases even `LIST(X)[1]`); SPEC §3.4 and contributing.md disagree with the tree | Yes: amend the docs or restore clones | `values.aggregate-collect-copy-vs-alias` (MAP, FILTER, SORT, TAKE) |
| C39 two-arg `BUCKET` collapse | All five | Yes: the group key is the scalar, so group on it | `rel.bucket.two-arg-key-is-the-scalar-when-the-key-has-children` |
| C40 same-named binders in `LINK` | All five | Yes: right binder wins, and the hash path must refuse | `rel.link.same-binder-name-both-sides` (both spellings) |
| C43 mixed numeric/text sort order | js/php/py differ from cpp/lisp | Yes: define a total order | `rel.sort.mixed-numeric-and-nonnumeric-text-is-a-total-order` |
| C44 `dependencies()` semantics | All five | Yes: first-use order vs set difference | API probes in `tools/check-api`: `X += 1`; `X + 1; X = 2` |
| C45 SORT_BY+TAKE fusion error order | All five | Yes: evaluation order of `n` vs key | a raising key with a bad `TAKE` count, fused vs unfused |
| C46 E_DEPTH position in DISTINCT/BUCKET/IN | All five (cpp-5), C++ (cpp-3) | No new rule (§6.3) | `limits.depth.distinct-element-position`, `limits.depth.bucket-key-position` |
| C47 `.>` placeholder rules | All five | Yes: single vs multiple `_`; binding-function exemption | double-placeholder case; `pipe.placeholder.binding-function-is-not-substituted` |
| C48 E_UTF8 position | C++, PHP, Python (0:0) | Yes: document 0:0 or report the last valid code point | byte-level API probe (not expressible in `.selt`) |
| C29 / C30 / C31 / C36 / C55 SQL kind, IN, sargable, guard memo, duplicate fields | Python same output for C29/C30/C31; JS/Python have the C36 ordering; Python keeps the last duplicate (C55) | Yes for C31 (doc vs cases), C55 (refuse vs overwrite) | `warrant.bool.if-cannot-launder-an-undeclared-column`, `warrant.numeric.if-branch-unknown-is-guarded`, `bind.in.exact.numeric-literal-is-cast`, plus fixing `bind.sargable.sqlite/.postgresql` once the doc/behaviour question is decided, `bind.relation.duplicate-field-after-uppercasing` |
| C33 / C34 / C35 hybrid planner equivalence | JS planner produces the identical plan split (checked for C33 and C35; C34 by reading) | Yes: which steps may be pushed past a possibly-raising step, and the keys rule | `plan.keys.filter-prefix-then-underscore-K-in-continuation`, `plan.keys.filter-then-unsupported-filter`, `plan.fallthrough.take-after-raising-custom-half-stays-local`, `plan.sort.filter-not-hoisted-over-raising-sort-key`; and a random-pipeline differential lane (see fix order) |
| C53 / C54 dialect text escape, hybrid context isolation | JS same | Small: refuse withdrawn textEscape; clone uniformly | `neutral.register.text-escape-withdrawn-refuses-text-literals` |
| C59 TAKE count strictness in the translator; error registry | All hosts | Yes: extend `sql/errors.md` registry or use SQL codes | a `TAKE(x, 2.0)` translator case |

Also for the record, from the other-host observations in the reviewers' notes: JS `aggregate.mjs:69`/`:469` throws a host `TypeError` when an aggregate body appends to its own source; Lisp exhausts the heap on the C1 `FILTER` case; PHP alone validates the sort direction of `SORT_BY(LIST(), x, NOSUCH, "ASCX")` on an empty list; Lisp promotes no fields for list-shaped rows in `LINK`. These are outside this report's scope.

---

## Suggested conformance cases

The cases marked "spec first" need a decision before the expected value is fixed. Expected values for the other cases come from the JS/Python outputs cited in the findings, or the spec.

| Name idea | Expression | Expected |
|---|---|---|
| `num.mul.scale-over-18-exact` | `0.1234567890123456789 * 0.1234567890123456789` | `0.01524157875323883675019051998750190521` |
| `num.power.compound-interest` | `POWER(1.05, 30)` | `4.321942375150662009157288198886473341473378241062164306640625` |
| `num.power.half-100` | `POWER(0.5, 100)` | `0.00000000000000000000000000000078886090522101180541172856528278622967` |
| `num.round.remainder-above-2^126` | `ROUND(0.99999999999999999999999999999999999999, 0)` | `1` |
| `num.div.remainder-above-2^126` | `8600000000000000000000000000.0000000000 / 99999999999999999999999999999999999999` | `0.0000000001` (per JS) |
| `num.mod.one-negative-big` | `(-1000000000000000000000000000000000000000000) % 1` | `0` |
| `num.div.by-one-big` | `A=1000000000000000000000000000000000000000000; A / 1` | `1000000000000000000000000000000000000000000` |
| `num.abs.int128-min-product` | `A=-9223372036854775808; B=18446744073709551616; C=A*B; ABS(C) > 0` | `TRUE` |
| `num.neg.int128-min-product` | same `C`; `D=-C; D<0` | `FALSE` |
| `num.plan.nested-plan-past-initial-scratchpad` | `V = 1; X = 5; X + LEN(V+V+...+V)` (20 `V`) | `7` (must not crash; add an ASan lane) |
| `num.minmax.33000-args` | `A=1; MAX(A, ... x33000)` (generated) | `1` |
| `num.round.first-arg-error-before-scale-error` | `X="x"; ROUND(IF(TRUE,X,1), 2.5)` | `E_NOT_NUM` at the first argument (col 14) |
| `num.power.first-arg-error-before-exponent-error` | `X="x"; POWER(IF(TRUE,X,1), -1)` | `E_NOT_NUM` at the first argument |
| `asg.compound.rhs-creates-variable` | `A = 1; A += (B = 1); A` | `t"2"` |
| `asg.compound.rhs-creates-many-variables` | `A = 1; A += (B = 1, C = 2, D=3, E=4, F=5, G=6); A` | `2` |
| `asg.compound.concat-rhs-creates-variable` | `A &= (B = "x"); A` after `A = ...` | spec-derived: concatenation with the new variable's value |
| `agg.filter.body-appends-key-to-iterated-record` | `R["a"]=1; R["b"]=2; R["c"]=3; FILTER(R, (R["z" & _K] = 1; TRUE))` | must not crash; result per the snapshot/live decision (spec first) |
| `agg.sort-by.body-appends-key-to-iterated-record`, `agg.bucket.body-appends-key-to-iterated-record` | the SORT_BY / BUCKET twins | same |
| `agg.source-mutated-by-body.map` | `R = LIST(1,2,3); MAP(R, IF(_K == "1", (R[3] = 99), _))` | spec first: `{99,2,99}` (live) or `{99,2,3}` (snapshot) |
| `lim.coalesce-chain-long-and-short-circuits` | `1 ?? 1 ?? ... ` (5,000 operators) | `1` |
| `lim.coalesce-chain-eval-depth` | `NULL ?? NULL ?? ... ?? 5` (past 200) | `E_DEPTH` at the innermost node |
| `lim.interp-depth-just-under` / `lim.interp-depth-past-the-cap` | `"{"{ ... 1 ... }"}"` at N levels | boundary spec first; past-cap => `E_DEPTH` at the opening brace |
| `lim.eval-depth.flat-chain-inside-aggregate-body` | `MAP((1,2), _+_+...)` with a 5,000-term body (also FILTER/SORT_BY/BUCKET/TOP_BY) | `E_DEPTH` at the 201st node |
| `text.repeat.empty-huge` | `REPEAT("", 9000000000000000000)` | `""` |
| `text.left.huge-count` | `LEFT("abc", 9223372036854775807)` | `abc` |
| `re.limit.long-subject-alternation` | `RMATCH("^(a\|b)*c$", REPEAT("ab", 1500000))` | `FALSE` (all hosts) |
| `re.replace.empty-then-nonempty` | `RREPLACE('a*?', "-", "aab")`; `RREPLACE('\|a', "-", "aa")` | spec first; ES/Python rule gives `-a-a-b-` and `-a-a-` |
| `re.reject.capture-in-repeat` | `RGROUPS("(?:(a)\|b)+", "ab")` | spec first (likely `E_REGEX_SYNTAX`) |
| `re.compile-time.bad-literal-in-untaken-branch` | `IF(FALSE, RMATCH("(?=a)", "x"), 1)` | `E_REGEX_SYNTAX` at the pattern (compile-time) |
| `re.reject.class-escape-range-endpoint`, `re.reject.quantifier-on-anchor` | `[\s-a]`, `[\w-.]`, `^*`, `$+`, `(*ANY)` | spec first (`E_REGEX_SYNTAX`) |
| `rel.link.equi-join-numeric-key-beyond-int64-trailing-zero-spelling` | `COUNT(LINK(A, B, a["k"] == b["k"]))` with one-row A/B holding `"12345678901234567890.50"` vs `"12345678901234567890.5"`; and `"100000000000000000000000.0"` vs `"1000000000000000000000000"` | `1` for the first pair, `0` for the second |
| `rel.link.same-binder-name-both-sides` | `COUNT(LINK(L, R, x, x, x["k"] == x["k"]))` vs `... AND TRUE` | equal results (spec first: which side wins) |
| `rel.bucket.two-arg-key-is-the-scalar-when-the-key-has-children` | `A = "x"; A["k"] = 1; B = "x"; B["k"] = 2; BUCKET(LIST(A, B, "x"), _)` | one group `"x"` with all three members |
| `rel.sort.mixed-numeric-and-nonnumeric-text-is-a-total-order` | `LIST("9","10","1a") .> SORT_DESC() .> JOIN(",")` | spec first |
| `rel.optimiser.top-by-fusion-error-order` | `L .> SORT_BY(_["k"]) .> TAKE("x")` (L rows lack `k`) | spec first: `E_NO_KEY` at the key (unfused order) or `E_NOT_NUM` |
| `values.aggregate-collect-copy-vs-alias` | `X = LIST(RECORD("k",1)); MAP(X, _)[(X[1]["k"] = 9; 1)]["k"]` (and FILTER, SORT, TAKE) | spec first: `1` (copy) or `9` (alias) |
| `ord.arith.operand-coerced-before-next-operand-evaluated` | `A = "x"; A + IF(TRUE, B, 1)`; `A = "x"; A + B`; `A = "x"; A < B` | spec first: consistent code for planned and unplanned |
| `pipe.placeholder.double-placeholder` | `N = 0; (N = N + 1) .> MAX(_, _) & "/" & N` | spec first (`1/1` if evaluated once, or `E_SYNTAX`) |
| `pipe.placeholder.binding-function-is-not-substituted` | `(1,2,3) .> FILTER((4,5,6), _)` | `E_EXPECT_SYMBOL` (document it) |
| `limits.depth.distinct-element-position`, `limits.depth.bucket-key-position` | the cpp-5 `DISTINCT` / `BUCKET` programs | `E_DEPTH` with a real position (not 0:0) |
| API probe | `--deps` of `X += 1`; `X + 1; X = 2` | spec first: `X` is a dependency (or documented as not) |
| `.sqlt` `norm.inline.def-not-captured-by-binder` | `X = Y + 1; ALL((1,2,3), Y, Y > X)` with `Y` a NUM column | `(1 > (t.y + 1)) AND (2 > (t.y + 1)) AND (3 > (t.y + 1))` (dialect spelling as usual) |
| `.sqlt` `agg.binder-same-name-as-list-variable` | `ANY((B + 1, 2), B, B > 0)` | translates as SEL evaluates it, or a refusal that names the capture (not `E_SQL_DEPTH`) |
| `.sqlt` `stmt.select-cols.raw-field-is-refused`, `stmt.derived.raw-field-is-refused` | `R .> SELECT_COLS("A","B")`; `R .> TAKE(3) .> FILTER(_["B"] > 1)` (B raw) | `E_SQL_SHAPE` |
| `.sqlt` `warrant.bool.if-cannot-launder-an-undeclared-column` | `IF(F, X, TRUE)` (X undeclared) | refusal `E_SQL_SHAPE` (as a bare `X`) |
| `.sqlt` `warrant.numeric.if-branch-unknown-is-guarded` | `IF(F, X, 1) + 1 == 2` | numeric guard applied |
| `.sqlt` `bind.in.exact.numeric-literal-is-cast` | `S IN (1, 2, "a")` (S exact TEXT) | each numeric item cast as in `S IN (1)` |
| `.sqlt` `bind.relation.duplicate-field-after-uppercasing` | fields `A` and `a` | `E_SQL_BINDING` |
| `.sqlt` 30 doubling helpers over a column | `A1=A0+A0; ... A30` | bounded-time refusal |
| `.sqlt` translate-twice guard | translate `X > 5` twice with the bad numericGuard dialect | refused both times |
| `plan.keys.*`, `plan.fallthrough.take-after-raising-custom-half-stays-local`, `plan.sort.filter-not-hoisted-over-raising-sort-key` | the cpp-7 programs | pure_memory, or `run()`'s error/keys preserved (see C33-C35) |
| Unit tests (not `.selt`) | `??` plan run 100k times; `ctx.math_scratchpad_top == 0`; nested-plan scratchpad growth; slot limit; `Context` after exception | as stated |

---

## Suggested fix order

Group 1: memory-safety and process-abort fixes. Small, local, and none of them changes spec. Do these first.

1. C1 (bind `item` by value in `walk`/`do_sort`/`do_bucket`), C2 (re-derive the slot after the RHS), C3 (index the scratchpad instead of caching a pointer; then C19 with an RAII guard in the same function), C9 (`uint32_t` slots or fall back to the tree evaluator). Each is a local change (by-value binding, re-deriving a slot, index instead of pointer, wider integer type), and together they close every reproduced use-after-free and heap overflow.
2. C7 (catch `srell::regex_error` at the match sites; pick a code, likely `E_RANGE`) plus a `catch (const std::exception&)` boundary in `Program::run` to cover C8's `bad_alloc`. Then C8's early return for empty `s`/`n == 0`.
3. C6 (a bool on Node, or a depth parameter). This also removes P17 for free.
4. C4 (iterative right-assoc chain in the parser) and C5 (lexer nesting counter). These need the same change in the other hosts, so do them together with the spec/`10-limits.selt` cases.

Group 2: wrong-result fixes with small diffs and known-good fix sketches (cpp-2 validated its decimal fixes on a scratch copy: 0 mismatches over 83k oracle cases).

5. C20 (delete the 18-digit rounding branch), C21 (`r >= p - r`), C24 (`k == 0` in the pow10 path plus `dec_make` normalisation), C41 (never create a small Dec at INT128_MIN), C22 (build the BigDec text from the stripped mantissa), C25 (swap two lines).
6. Extend `tools/decimal-oracle.py` to 45-digit operands, scales to 45, and values around 2^63/2^64/2^126/2^127/10^18/10^19/10^37/10^38. This is the single most useful test change: it would have caught C20, C21, C24, C41 and C22 (and needs no host code).

Group 3: SQL-layer correctness (need `.sqlt` cases and, mostly, the same change in Python/JS).

7. C29 (`unify` returns UNKNOWN), C30 (cast non-text items), C31 (the sargable arm), C32 (refuse raw fields), C23 (binder capture), C17/C10 (memoise `is_constant`, count depth of spliced defs, and add an expansion budget), C36 (insert after check), C53 (refuse withdrawn textEscape).
8. C33-C35 (hybrid equivalence): promote cpp-7's random pipeline differential (about 30 lines each of `gen.py`/`h.cpp`/`an.py`) to `tools/`, and make `sql_unit`'s stand-in renumber. It found all three within 400 samples.

Group 4: spec decisions that unblock several findings at once (cheap to decide, then one change per host).

9. Aggregate iteration semantics (C38, C42, also settles C1's design), `??`/plan coercion order (C37), the regex semantics group (C26, C27, C28, C49, C50, C7), the `.>` placeholder rules (C47), `dependencies()` (C44), BUCKET/LINK identity rules (C39, C40), sort total order (C43), and size caps for REPEAT/PAD/regex/SQL output. Add the conformance cases from the table above as each is decided.

Group 5: performance, in order of payoff per effort.

10. P3 (linear `fold_pairwise` build), P2 early E_RANGE rejection (an O(1) check), C18 (stop the dup-check past 256 keys), C16 (bounded regex cache, then P16 compile-once). Each is a few lines to a few dozen and removes a DoS-class cost.
11. P4 (non-throwing `??` probe), P5 (operator enum plus cached literals; measured 1.66 s to 1.04 s), P7 (move fresh temporaries), P10-P11 (sort key decoration, TOP fallback).
12. P1 (Knuth D on base-1e9 limbs) is the larger piece of work; it is what brings 116.8 s down towards JS's 0.23 s and the 58-86 us `/` on 30-43 digit operands down to nanoseconds.
13. The remaining items (P6, P8, P9, P12-P15, P17-P25) as opportunity allows. P9 (byte fast paths for text builtins) and P8 (smaller `Dec`) interact with the prior container-allocation work: re-run `tools/cpp-collection/benchmark.py` before and after.

Group 6: the low-severity tail (C11-C15, C46, C48, C51, C52, C54-C60) can be done as a hygiene pass. C11 and C12 are one shared decision (document single-threaded registration, or add a shared mutex).

---

## Checked and found sound

Sanitizer and fuzz corpora (all clean unless noted):

- cpp-1: all 1,091 `--- source` bodies of `conformance/*.selt` through an ASan+UBSan `sel --deps`: 0 reports. In-process mutation fuzz (700k mutated conformance sources: fragment insertion, byte flips including invalid UTF-8, unbalanced quotes/braces, `\u{` escapes) through `compile()` + `dependencies()` under ASan+UBSan+LSan: 0 reports, 0 leaks. `decode_utf8` vs Python's strict decoder on 300k random 1-5 byte strings: 0 disagreements.
- cpp-2: 83,000 wide-range decimal oracle cases (values around 2^63, 2^64, 2^126, 2^127, 10^18, 10^19, 10^37, 10^38, scales 0-45) agree once the C20/C21/C24/C41 fixes are applied; ASan+UBSan clean.
- cpp-3: TSan, 8 threads, one shared `Program` (math plan, LINK, FILTER, RMATCH, UPPER, fresh contexts, concurrent `compile`): nothing reported. ASan repros for C1-C3.
- cpp-4: about 27k generated programs (text/binary/regex/null/control combos, UTF-8 fuzz, base64/hex fuzz, 4,000 random regex programs) through an ASan+UBSan+LSan `batch`: 0 reports, 0 leaks, byte-identical to the normal build. An 18.5k text/binary/regex/null/control combination corpus and 3.3k UTF-8 corpus: no divergence vs JS/PHP/Python (value, error code and column); the divergences found come from regex semantics, resource limits and spec gaps.
- cpp-5: private ASan+UBSan build; a 6-seed differential fuzz of join keys (did not hit C22, which needs a dedicated generator); FILTER-over-LINK with dropped rows and raising conjuncts, LINK_LEFT, scalar rows, empty/NULL sides, case-folded binders, 600-1,200 distinct row shapes: no divergence.
- cpp-6: `cpp/build/sqlt` 1,065 passed, 0 failed on the objects used. A fuzz of malformed binder/arity shapes (`ALL((1,2), 1+1, 3)`, `R .> LINK(R, X, 2, TRUE)`, `SELECT_COLS()`, `TAKE()`, ...) produced only SqlError / SelError-compile errors, no `std::exception`.
- cpp-7: about 5,000 random hybrid pipelines (sqlite, mariadb) plus about 80 probes under ASan+UBSan, with a TSan set: no ASan/UBSan report (the only divergences are C33-C35 in plan equivalence). `sqlt` and `sqlunit` green (1,065 passed / 85 checks) before starting.

Front end (cpp-1):
- UTF-8 codec bounds, overlong/surrogate/>10FFFF rejection, `\u{...}` escape handling (1-6 digits, leading zeros counted, E_ESCAPE/E_RANGE/E_UNTERMINATED positions), code-point-based lexer positions with astral characters, ASCII-only identifiers/digits/case folding (no `std::toupper`, no locale).
- Interpolation with nested strings, raw strings, comments, `{}`, `\{ \}`, stray `}` matches the other hosts; parser table matches `spec/grammar.md` level for level; comparison non-associativity, `NOT`/`-` operand-position errors are identical across hosts. About 45 hand-picked edge inputs identical to JS/PHP/Lisp.
- Depth accounting: paren, call paren, index bracket, prefix `-`/`NOT`, assignment are counted, `Leave` is RAII; 1,000,000-long `-`, `NOT`, `(`, `+1`, `.> LEN`, `[1]`, `AND TRUE`, `& "a"` chains give a clean E_DEPTH for run and `--deps` (only `??`, C4, and interpolation, C5, were missed). 1,000,000 `A=1;` statements are fine.
- `Node::~Node` iterative teardown holds (1M-deep left-leaning and pipeline chains); the `use_count()==1` guard is correct for the `grouped` copy; the `const_cast` is valid. Token-vector indexing is safe (`Eof` always last). Literal handling: 1,000,000 integer / fractional digits accepted, +1 digit gives `E_RANGE` at the token in about 20 ms.
- Registry: replaced host functions are retired rather than freed, so Node `spec` pointers stay valid; the record-shape cache is bounded (256 entries, 256 keys, 16 KB) and mutex-protected.

Numeric core and Values (cpp-2):
- `mul_limbs`/`sqr_limbs` carry scheme within the `divq` precondition for all cap-permitted lengths; x86 `divq` fallback matches the portable path; `add_limbs`/`sub_limbs`/`cmp_limbs`/`scale_up_limbs` fine; `POW10_128[0..38]` correct and every index bounded.
- `align_small`, small `dec_add`/`dec_sub`/`dec_cmp`/`dec_mod` (apart from C41) agree with the oracle. `dec_parse`/`dec_is_number`/`dec_make`/`dec_small_mantissa` (38-digit boundary, canonicalisation), `dec_floor/ceil/trunc/is_integer/to_int`, `dec_trim_scale`, `dec_format`/`dec_format_buf` (apart from INT128_MIN), `Args::integer/non_neg_int` (saturating; ROUND rejects n > 1,000,000 before allocating).
- POWER/ROUND E_RANGE/E_NOT_INT positions agree with JS and Python for planned mixed bad arguments; E_DEPTH is identical when a math plan is nested under other calls (chains of 150-201 `+` under 0-60 wrapper calls). 1,000,000-digit operands: parse, `+`, compare, `%` by a small divisor, `/` by a 10-digit divisor are faster than JS; limit errors raised.
- MIN/MAX tie behaviour and scale preservation agree in plan and builtin forms; copy propagation (`x+0`, `x-0`, `0+x`, `x*1`, `1*x`, scale-0 only) preserves coercion errors.
- Value: handle refcounting (non-atomic, single-thread by contract), `Impl` freelist with sized delete, iterative `destroy`, depth-counted `clone_at/eql_at/dump_at/structural_hash` (200), `Value::set` / shaped-record / packed-list conversions keep children and storage in step, `Value::num(Dec)` validation. No `shared_ptr` cycles except a host doing `v.set("k", v)` (documented). The physical tree is built under `call_once`; the `Dec` caches are only mutated on local copies.
- Test gap: `tests/unit.cpp` `test_decimal` (about 30 checks) uses only small operands; `test_math_plan` does not test nested plans, scratchpad growth, exceptions inside a plan or slot limits.

Evaluator (cpp-3):
- Operand sequencing in `eval_binary`, `concat`, `bitwise`, `XOR`, `$`-compares: every coerced pair goes through named locals; `JOIN` error precedence matches the other four hosts. `??`/`???` catch only E_NO_KEY/E_UNDEF_VAR and rethrow the rest; aggregate frames are popped on every exceptional path (`MAP((1,2), X, Q) ?? X` gives `E_UNDEF_VAR X` like JS).
- `resolve_target` / the non-Var `eval_assign` branch re-derive the path after the RHS; assignment chains charge E_DEPTH. `eval_node`'s depth guard is RAII; stack need at the deepest legal nesting is at most 192-256 KB (release build).
- Fusions checked for E_DEPTH drift (FILTER+FILTER). `register_function` validation and replacement semantics match §8.1. Host values nested 3,000,000 deep: `clone/dump/eql/structural_hash` raise E_DEPTH and destruction is iterative. 64-bit-edge integer arguments (`TAKE/DROP/LEFT/SUBSTR/FIND/CHAR/ROUND`) agree with all four other hosts. Math-plan copy propagation keeps scale and error positions. LINK predicates that try to assign their sources give E_BAD_ASSIGN with no sanitizer report.

Text, regex, binary, null, control (cpp-4):
- UTF-8 validation for `FROM_UTF8` agrees with all hosts on 3.3k inputs; `UPPER`/`LOWER` ASCII-only; `TRIM` set exactly SP/TAB/CR/LF; `IS_BLANK`; `CHAR` range/surrogate checks; `CODE`; `BACKWARDS` by code point; `SUBSTR`/`LEFT`/`RIGHT`/`FIND` clamping without overflow.
- Char signedness: `hex_value`, `b64_index`, `CRC32`, `BTL`, `ENCODE_BASE64` cast correctly; base64 decode is strict (padding, length%4, bytes at or above 0x80 rejected).
- `LTB`, `GET`/`PATH`, `COALESCE`/`IF`/`COND`/`ABORT` laziness and error positions match. Regex: `\d \w \s` ASCII expansion, refusals (`\b \B \v`, backreference, lookaround, possessive, atomic, named, inline modifiers, POSIX classes), quantifier cap 65535, `{`/`}`/`]` strictness, class edge cases, `^`/`$` whole-subject, dotall on, code-point offsets, flags (`i` only; `I` accepted; others E_BAD_ARG; `i` with non-ASCII pattern E_BAD_ARG), case-insensitive U+212A/U+017F/U+0130/U+0131 behaviour: all identical on a 60-pattern differential. The regex cache is mutex-guarded and node-stable. `expand_replacement` semantics (`$$`, `$0`-`$9`, non-participating groups, `$n` beyond group count errors only on a match). `RGROUPS` returns the empty list on no match.

Structure, aggregates, pipeline (cpp-5):
- `TAKE/DROP/COUNT/INDEXES/HAS/LIST/RECORD` edge cases (scalar = one element, NULL, huge n, duplicate keys, `RECORD(NULL,1)` E_NULL, odd arity E_ARITY, `TAKE(x,0)` still evaluates `x`) agree in all five hosts. `SELECT_COLS`, `JOIN`, sort/`TOP_BY` direction and limit evaluation order and error positions agree.
- `do_top`'s tie-break makes it agree with the stable sort for consistent comparators; `std::stable_sort` with the C43 intransitive comparator is memory-safe; `SortEntry` push_back is well-defined.
- Hash-join correctness apart from C22/C40: bucket insertion order, NULL keys never match, `check_join_pair` order, BIN/TEXT `$==` by bytes, booleans raise E_NOT_NUM, `-0`/`0.0`, `-5`/`5`, `-5.5`/`5.5`, 1e23-scale negatives agree with JS. `JoinProjector`'s plan cache keyed by raw shape pointers is sound in the current code (row lifetimes pin the shapes), but the invariant is fragile if rows start being dropped before projection. `AliasPlan` cache and `intern_record_shape` are mutex-protected, and clearing at 256 entries is safe. Non-equi LINK's right-row alias creation is not the bottleneck (about 1.5 us/pair is predicate evaluation).
- `Value::destroy` iterative; `clone_at`/`eql_at`/`structural_hash`/`dump_at` depth-counted; `eql_at` and `structural_hash` consistent.

SQL layer (cpp-6, cpp-7):
- Identifier quoting (`Emit::ident` doubles the quote character; every alias/table/column from bindings goes through it; `Binding::column/relation` reject empty and NUL names). Only `raw`, `relation_query` and `correlate` are verbatim, by design.
- Text literal quoting: MySQL/MariaDB double `'` and `\`; PostgreSQL/SQLite double `'`; single-pass escaping; literals are structurally separate from SQL (part list), so params mode cannot mis-split; NUM/BOOL/BIN literals canonical. Inline mode assumes default server settings (`standard_conforming_strings=on`, no `NO_BACKSLASH_ESCAPES`, utf8mb4 connection); Params mode is the recommended path. Only registered dialects can weaken this (C53).
- Whole-expression refusal: a fresh `Translator` per call; frames, depth, statement plan, `in_where` are restored by RAII or try/catch on every exit path checked; no partial Fragment escapes. `try_translate` catches `SqlError` only; a `SelError` from `validate_pattern` is converted. Dialect handling: unknown/base/wrong-case/empty dialect give E_SQL_DIALECT in the documented order; unsupported entries carry the map's reason; arity-keyed `null` withdrawal honoured; `since` compared against the target version.
- Kind guards cover every builtin in `spec/builtins.json` that reads a number (ABS/SIGN/CEIL/FLOOR/TRUNC/ROUND/POWER/CHAR/CANON, MIN/MAX, LEFT/RIGHT/SUBSTR/REPEAT/PADL/PADR/FIND); BOOL and BIN tables agree with the measured lists; a constant in numeric position is checked by value per operand; `guard_numeric` does not self-wrap.
- Aggregates: `IS [NOT] TRUE` folding in `all`/`any`; FILTER absorption shapes match §7.5; binder shadowing for `_K`; self-nested relation alias refusal; COUNT/HAS scalar rule; empty-list identities. Slot numbering is creation-ordered and `bindings()` walks the parts, so reordering templates (FIND) stay correct. Stage 1 refusals (compound assignment, reassignment, non-constant index targets, mixed whole/indexed assignment, duplicate keys), clist aliasing (`R[1]=1; A=R; R[2]=2; COUNT(A)`), constants validated at the assignment even when the definition is dropped.
- C++ lifetime review of the translator: `Binder` references into `frames_` stay valid (a `Frame` is a vector moved on outer reallocation); `require_bool(node(...), ...)` copies the temporary within the full expression at each call site; `string_view`s into the map are used only while no `define` can run. Registry lifetimes (`OwnedEntry`/`OwnedDialect`, arena never frees on redefine, `unique_ptr` stable addresses): no dangling views. Hybrid planner lifetimes: `Helpers` holds references to outliving locals; all tree edits copy; Node is `const` in the plan. `Map::chain` cycle guard works; `define` upper-cases only `funcs` keys.
- Hybrid planner: literal helpers inlined with read position; constant-fold errors (`N = 1/0`, `"abc"+1`, `TAKE(-1)`, huge literals) fall to pure_memory with `run()`'s code and position; helper-as-source unwinding; LINK/LINK_LEFT, bare/open BUCKET and BUCKET-MAP closure agree with `run()`. `try_latest_member` guards match §12.1 (single unique key, NUM revision, TEXT/NUM partition, exactly one `TOP_BY DESC 1` plus `_K`, only FILTER and ASC revision sorts before the bucket), parameter order preserved, `items.size()` guarded (reasoned; not executed against a DB).
- `Emit::fill`: `{{`/`}}`, unknown-slot refusal, canonical slot grammar (3-digit cap), lexical cycle guard, binaryCast skip for BIN operands; no OOB found. Dialect inheritance in the replay data matches `sql/dialects/*.json`. `sqlt.cpp` / `map_replay.cpp` harness logic is correct.
- Doc tension only: the interior three-valued-logic divergence (`NOT (A>0 AND B>0)` with A NULL, B negative; `A>0 OR B>0` with A NULL, B positive) is documented in sql-translation.md §11.2 and the caveat table, but sql-kinds.md §3 ("NULL is inside the warrant ... for free") reads stronger than §11.2 concedes.
- Test gaps: `sql_unit`'s hybrid equivalence stand-in keeps FILTER keys (which a driver cannot), so C33 passes the unit lane; the "85/85 checks passed" line is a hard-coded string, not a count; `.sqlt` plan cases assert classification, tables and SQL text only, and none executes the continuation, so no case can catch C33-C35.

Coverage limits of this review: no real database was run (SQL server semantics are reasoned); Windows/LLP64 was not built (C51 reasoned only); the lexer/decimal/evaluator slices did not read each other's internals beyond call boundaries; `cpp/bin/api.cpp` was not read line by line; generated data files and the Rust host were not reviewed; timings are from a shared box.
