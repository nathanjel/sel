# Common Lisp (SBCL) host: code and performance review

Date: 2026-09-29. Scope: `lisp/` (SBCL 2.6.8, cl-ppcre from Quicklisp). Rust ignored.

## Scope and method

Seven reviewers each took one slice, read it in full, cross-checked it against `spec/` and `conformance/`, and confirmed suspected bugs by running the host (usually with a differential run against js/php/python/cpp). One synthesizer merged the reports, de-duplicated them and re-ran the top findings.

| Slice | Files |
|---|---|
| 1 front end | `lexer.lisp`, `parser.lisp`, `errors.lisp`, `limits.lisp`, `utf8.lisp`, `package.lisp`, `bin/sel.lisp` |
| 2 numeric core and values | `decimal.lisp`, `value.lisp`, `builtins/number.lisp`, `math-plan.lisp`, `math-ops.lisp` |
| 3 evaluator | `eval.lisp`, `registry.lisp`, `sel.lisp`, `optimizer.lisp` |
| 4 text, regex, binary, null, control builtins | `builtins/text.lisp`, `regex.lisp`, `binary.lisp`, `null.lisp`, `control.lisp` (+ `utf8.lisp` hex/encode) |
| 5 structure/aggregate builtins, in-memory relational pipeline | `builtins/structure.lisp`, `builtins/aggregate.lisp` |
| 6 SQL translator core | `sql/translator.lisp`, `stage1.lisp`, `binding.lisp`, `fragment.lisp`, `errors.lisp`, `package.lisp`, `emit.lisp` |
| 7 SQL rendering and hybrid | `sql/hybrid.lisp`, `emit.lisp`, `map.lisp`, `relational-plan.lisp` |

The review is against what was on disk on 2026-09-29. `lisp/src/builtins/aggregate.lisp` is modified in the working tree by another session. Slice 5 read the on-disk version; its only difference from HEAD is a paren move in `do-top-sort`, and behaviour is correct. Line numbers for `aggregate.lisp` therefore differ by a few lines between slices (for example `:431` vs `:439`).

No database was run (Docker was forbidden), so SQL findings come from reading emitted SQL against SEL semantics. Ratings keep the reviewers' labels: `confirmed` = reproduced by the reviewer; `unconfirmed` = reasoned only; `measured` / `reasoned` / `guessed` for performance. "Re-verified by synthesizer" means I re-ran the repro on 2026-09-29 (read-only).

## Executive summary

- Overall the Lisp host is in good shape on its core: five independent decimal, UTF-8, lexer/parser and dialect-map differentials (about 18,000 decimal cases, 65,000 UTF-8 strings, about 190 front-end edge cases, 4,500 SQL translations) found zero disagreements. Almost every defect below is in an edge of a feature, not in the core.
- Worst correctness problems (all re-verified by the synthesizer):
  1. BUCKET silently splits equal text keys once any of them has been read as a number (LISP-C1).
  2. A record with 16 or more fields corrupts the process-global record-shape cache after a new key is assigned (LISP-C2).
  3. Three independent ways for an ordinary program to crash the process with a non-SEL host condition (LISP-C3, C4, C5).
- Lisp-specific risk: SBCL signals `storage-condition` (control-stack or heap exhaustion) and CL `type-error`, none of which is a `sel-error`. Nothing in `lisp/src` handles them, so `sel-cli` prints a backtrace and library callers get a foreign condition. Sources: cl-ppcre recursion (C3), the lexer's uncounted interpolation nesting (C4), `TOP` with a huge N (C5), text-size explosions (C20), the `??` chain (C21), `from-native` (C32).
- cl-ppcre is Perl-flavoured, which produces silent wrong answers: `$.*` never matches (C10), `^*` is accepted (C11), nullable-loop capture semantics differ from JS/C++ (C18), and there is no backtracking budget (C19).
- The SQL layer has two high-severity translator bugs shared byte-for-byte with JS (C6 filtered `JOIN` drops the FILTER, C7 FILTER binder leaks into the body) and one high hybrid-planner bug (C8, `_K` in a fall-through MAP), plus a family of hybrid and optimizer rewrites that are not value/error preserving (C14, C27 to C30).
- Biggest performance wins (all measured): the `emit-parts` `length` call is quadratic in the number of interpolations (P1, 71 s for 64,000 `{1}` interpolations, 0.1 s after a one-line change); `eval-binary` does up to about 25 `string=` per node (P3, 1.5 to 1.9x on comparison-heavy FILTERs); text sort keys are re-encoded on every compare (P4, about 5x); huge-number arithmetic is schoolbook with redundant re-parsing (P2, 5 to 150 s per operation, 5 to 20x reachable); translator and `emit-fill` are quadratic in operand count (P11, P12).
- Cross-host: about 20 findings are shared with other hosts and need a spec decision first (see "Cross-host findings"). Do not fix those in Lisp alone.

Counts: 47 correctness findings (8 high, 22 medium, 17 low) and 27 performance findings (4 high, 10 medium, 13 low; P15 to P17 and P19 are rated low-medium and P18 medium/low by the reviewers, counted here as low).

## Correctness findings

Numbering: LISP-C1 to C8 high, C9 to C30 medium, C31 to C47 low. Within a severity, confirmed before unconfirmed and higher impact first. "Found by" lists slice and original id (for example `s2-C1` = slice 2, finding C1).

### High

### LISP-C1 [high] [confirmed, re-verified by synthesizer] BUCKET splits equal text keys once a value's decimal cache is warm
- Found by: s2-C1, s5-C1.
- Where: `lisp/src/builtins/aggregate.lisp:569-592` (`eval-key-hash`, via `bucket-key-hash` at :635); root cause is the meaning given to `value-dec-val` (`value.lisp:415-425` `as-dec`, :186 `make-num`).
- What: `eval-key-hash` hashes a TEXT key by (sign, scale, digits) when a decimal is cached on the value and by its spelling when it is not. `dec-val` is meant to be a pure cache, but `as-dec` fills it on the first numeric read, so two keys with the same spelling hash differently depending on whether one of them was ever used as a number. `value-eql` compares by spelling, so they land in different buckets. In the two-argument form both groups get the same record key and `value-set` overwrites, so rows are dropped. Spec §7.3 says bucket keys compare as spellings (`hash.bucket.numeric-cache`: `"1"` and `1` share a group, `"1.0"` and `"01"` do not). The suite passes only because the optimizer's `copy-node-shallow` (`optimizer.lisp:14-26`) drops `dec-val` from numeric literals (see P5), which hides the bug for literals. DEDUPE/DISTINCT are unaffected (`value-hash` hashes the spelling). This is the trap described in `docs/contributing.md` for joins.
- Repro (fresh process; synthesizer output shown):
  - `X = "5"; Y = X * 1; JOIN(BUCKET(LIST(X, "5", 5), _, COUNT(_)), ",")` -> Lisp `1,2`, JS `3` (expected `3`). Lisp output re-verified.
  - `R = LIST("1","1"); x = R[1] + 0; BUCKET(R, _, COUNT(_))` -> Lisp `-{"1"=t"1", "2"=t"1"}`, JS one group with count 2. Re-verified.
  - `X = "01"; Y = X + 0; JOIN(BUCKET(LIST(X, "01", "1"), _, COUNT(_)), ",")` -> Lisp `1,1,1`, expected `2,1` (s2, not re-run).
  - `X = "1.0"; Y = X + 0; JOIN(BUCKET(LIST(X, "1.0"), _, COUNT(_)), ",")` -> Lisp `1,1`, JS `2` (s2, not re-run).
  - `R = LIST(RECORD("k","1"),RECORD("k","1"),RECORD("k","2")); x = ANY(R, _["k"] > 0); BUCKET(R, _["k"], COUNT(_))` -> Lisp 3 groups `{"1"=1,"2"=1,"3"=1}`, expected `{"1"=2,"2"=1}`; the two-argument twin `BUCKET(R, _["k"])` loses a row (s5, not re-run). In a 50k-row run, `x = ANY(U, _ > 0); BUCKET(U, _, COUNT(_))` over 500 distinct numeric-text values returned 502 groups.
- Fix sketch: hash a TEXT value by `(sxhash (value-scalar v))` only (or call `value-hash`); drop the dec branch. s2 verified in a patched copy that this gives `3` and `2,1` and keeps all 1091 conformance cases green. This unblocks P5 and the ISNUM cache in P27.
- Conformance gap: none. The existing cases only use literals. Suggest in `21-structural-hash-identity.selt` / `15-relational.selt`: the `"5"` case above => `3`, the `"01"` case => `2,1`, and the record-key case => `{1=2, 2=1}` plus its two-argument twin (row count preserved).

### LISP-C2 [high] [confirmed, re-verified by synthesizer] A record with 16 or more fields corrupts the process-global record shape after a new key is assigned
- Found by: s2-C2.
- Where: `lisp/src/value.lisp:90-101` (`ensure-shaped-children` installs the shape's shared `key-map` as the value's `index`), :337-341 and :359-362 (`value-set` new-key branch), shapes cached in `*shape-cache*` (:35-57).
- What: `key-map` (key -> integer position) is shared by every record of that shape. With at least `+index-threshold+` (16) keys it becomes `(value-index v)`. `value-set` of a NEW key clears `shape`/`storage`, sees a non-nil `index` and does `(setf (gethash key idx) cell)`, storing a cons cell into the shared table. Every other record of that shape then believes the new key exists at "position" = a cons. Also reachable through the documented host route (alist rows, `docs/usage/README.md:338`): a wide DB row (16 or more columns) that a rule decorates poisons every later row for the life of the process, until the 256-entry cache is cleared. With 15 keys everything is correct.
- Repro (K1..K17 are 17 pairs `"K1",1,...,"K17",17`): `R = RECORD(<17 pairs>); R["Z"] = 5; S = RECORD(<same 17 pairs>); HAS(S, "Z")` -> Lisp `TRUE` (expected `FALSE`; JS/C++ `FALSE`). Same program ending `S["Z"]` -> uncaught `SB-INT:INVALID-ARRAY-INDEX-ERROR` instead of `E_NO_KEY`. Both re-verified.
- Fix sketch: in `ensure-shaped-children` do not alias `key-map`; call `%build-index` (fresh key->cons table) when count >= threshold, or set `(value-index v)` to nil before the recursion in `value-set`.
- Conformance gap: none (nothing builds a 16-key or larger record and then adds a key). Suggest: the `HAS(S,"Z")` program above => `FALSE`, and `S["Z"]` => `E_NO_KEY`; plus a `lisp/tests/unit.lisp` case via `from-native` with a 20-column alist.

### LISP-C3 [high] [confirmed, 2 of 4 triggers re-verified by synthesizer] cl-ppcre stack exhaustion or runaway recursion escapes as an uncaught host condition
- Found by: s4-C1.
- Where: `lisp/src/builtins/regex.lisp:306-352` (every `cl-ppcre:scan` / `create-scanner`); no handler for `storage-condition` anywhere in `lisp/src`.
- What: cl-ppcre compiles repetition of a complex sub-pattern into recursive closures, so control-stack use grows with SUBJECT length and with pattern nesting. SBCL signals `SB-KERNEL::CONTROL-STACK-EXHAUSTED` (a `storage-condition`, not a `sel-error`); `sel-cli` handles only `sel-error`, so the CLI prints a backtrace and exits 1, and a library embedder receives a non-SEL condition. Spec §6.4 says every recursing construct is bounded so the host stack is never reached; regex builtins have no equivalent. Three triggers, all with small or ordinary inputs:
  1. Infinite recursion on a 2-character subject (lazy loop over a nullable group, nested in another loop): `(?:a(?:x*)*?)*[!]` vs `"aa"`. Only fails with a non-literal-required-char pattern (`(?:a(?:x*)*?)*!` is answered instantly by cl-ppcre's required-string precheck).
  2. Recursion proportional to subject length: `^(?:ab|a)*$` dies at about 20,000 chars (TRUE at 16,000); `^([a-z]+ ?)*$` on 150 KB; `^(?:[^,]*,)*[^,]*$` on 300 KB; `^(?:[ab]c?)+$` on 3 MB; `^(a){N}$` dies at N=20,000 (65,535 is the spec's own cap, `re.quantifier.bound-at-the-cap`). Simple char-class loops (`^(?:a|b)*$`, 2 M chars) are fine, so it is pattern-shape dependent.
  3. Pattern nesting: `RMATCH(REPEAT('(?:',50000) & 'a' & REPEAT(')',50000), 'a')` (Python also dies here with a Traceback).
- Repro (each prints `Unhandled SB-KERNEL::CONTROL-STACK-EXHAUSTED`, exit 1; js/python/cpp print FALSE/TRUE):
  - `lisp/bin/sel -e "RMATCH('(?:a(?:x*)*?)*[!]', \"aa\")"` (re-verified)
  - `lisp/bin/sel -e "RMATCH('^(?:ab|a)*$', REPEAT('a', 30000))"` (re-verified)
  - `lisp/bin/sel -e "RMATCH('^(a){20000}$', REPEAT('a', 20000))"` (s4)
  - `lisp/bin/sel -e "RMATCH(REPEAT('(?:',50000) & 'a' & REPEAT(')',50000), 'a')"` (s4)
- Fix sketch: wrap each `scan`/`create-scanner` in `handler-case` for `storage-condition` (SBCL re-protects the guard page on unwind) and raise a SEL error. That needs a spec decision (a catalogued code: an existing one such as E_REGEX_SYNTAX for compile-time nesting plus a run-time "regex too complex" code, or E_DEPTH). For the infinite-recursion case, refuse nullable-quantified groups in the validator. Optionally run the matcher on a thread with a larger control stack to raise the subject-length ceiling.
- Conformance gap: none. Suggest `re.stack.long-subject-alternation` (`^(?:ab|a)*$` on 100,000 `a`), `re.stack.nullable-lazy-loop` (`(?:a(?:x*)*?)*[!]` on `aa` -> FALSE), and a nesting-depth case once the spec picks a limit.

### LISP-C4 [high] [confirmed, re-verified by synthesizer] Nested interpolation: uncounted lexer recursion exhausts the control stack, and the lexer is quadratic in nesting
- Found by: s1-C1 (also s1-P3).
- Where: `lisp/src/lexer.lisp:168-194` (`lex-quoted`), :233-251 (`match-brace`), :253-264 (`skip-quoted`), :281-299 (`emit-parts`). Cycles: `lex-quoted -> match-brace -> skip-quoted -> match-brace`, and `emit-parts -> lex-range -> lex-quoted -> emit-parts`.
- What: Interpolation is resolved in the lexer, before the parser's E_DEPTH counter sees anything, so `"{"{"{...1...}"}"}"` recurses in the host stack uncounted. Spec §6.4: every construct that can nest is counted; exceeding is E_DEPTH. Each `lex-quoted` also calls `match-brace` over the whole remaining literal, so work is O(n * depth). On SBCL's 2 MB control stack the failure is `SB-KERNEL::CONTROL-STACK-EXHAUSTED`, not a `sel-error`. Expected result is `E_DEPTH` at line 1 col 101 (what the parser produces for nestings up to about 4,000).
- Repro (measured, in-process compile time): nesting 500: 0.03 s; 1000: 0.12 s; 2000: 0.50 s; 4000: 2.3 s; 5000: 4.3 s (E_DEPTH 1:101 as expected); 6000: `CONTROL-STACK-EXHAUSTED`; 100,000 levels: 32 s then stack exhaustion. CLI (re-verified with n=8000: "Unhandled SB-KERNEL::CONTROL-STACK-EXHAUSTED", rc=1):
  `python3 -c 'n=8000;s="1"\nfor i in range(n): s="\"{%s}\""%s\nprint(s,end="")' > in.sel; lisp/bin/sel in.sel`
  Other hosts at nesting 3000: cpp and php answer E_DEPTH 1:101 (php 3.7 s, quadratic there too); js and python crash (RangeError / traceback). Lisp at 3000 takes 1.8 s.
- Fix sketch: (a) lex interpolation single-pass: lex the inner expression directly and stop at the unmatched `}` instead of pre-scanning with `match-brace` and re-lexing; (b) make the nesting an explicit stack or count it; a lexer-side cap cannot raise E_DEPTH itself because the parser's position (col 101 here) is not the lexer's, so an explicit stack keeps the parser the only source of E_DEPTH; (c) backstop only: `handler-case` `storage-condition` in `compile-source` (unreliable in SBCL; a second exhaustion before the guard page is re-armed killed the harness process).
- Conformance gap: none. `lim.parse-depth` covers parens and calls only. Suggest `lim.interp-nesting-depth` (about 150 nested `"{...}"`, E_DEPTH at the parser's position, identical in all hosts) and a large-nesting case in whichever lane exercises large inputs.

### LISP-C5 [high] [confirmed, TYPE-ERROR variant re-verified by synthesizer] TOP / TOP_BY / TOP_DESC allocate an N-element heap up front
- Found by: s5-C2.
- Where: `lisp/src/builtins/aggregate.lisp:276-277` (`make-bounded-heap` does `(make-array capacity ...)`), called from :484 with `limit = n` as written.
- What: capacity is the user's `n`, not `min(n, size)`. `TOP(LIST(3,1,2), 1000000000)` requests an 8 GB vector: "Heap exhausted", backtrace, exit 1. `n` above `array-dimension-limit` (for example 10^30, which `args-non-neg-int` accepts) raises a raw `SB-KERNEL::ARRAY-INVALID-DIMENSION-ERROR`/`TYPE-ERROR`, not a `sel-error`, so the host program crashes. Even n = 5*10^7 allocates 400 MB for a 3-element list (1.4 s). Spec §7.4 defines `TOP(list, n)` as the first n of SORT, so any n is legal and yields the whole list. TAKE and DROP clamp correctly. Also reachable through the optimizer's `SORT .> TAKE(n)` fusion.
- Repro: `lisp/bin/sel -e 'COUNT(TOP(LIST(3,1,2), 1000000000000000000000000000000))'` -> unhandled TYPE-ERROR backtrace (re-verified); `lisp/bin/sel -e 'TOP(LIST(3,1,2), 1000000000)'` -> "Heap exhausted" (s5, not re-run to avoid a large allocation); `node js/bin/sel.mjs` gives `3` for both.
- Fix sketch: `(make-bounded-heap (min limit <element count>) ...)`, or grow the array lazily.
- Conformance gap: none. Suggest `LIST(3,1,2) .> TOP(1000000000000000000000000000000) .> COUNT()  =>  3` and a `TOP_BY` twin.

### LISP-C6 [high] [confirmed] SQL: `JOIN` over a FILTERed static list silently drops the FILTER
- Found by: s6-C1. Cross-host (JS emits byte-identical wrong SQL).
- Where: `lisp/src/sql/translator.lisp:2013-2052` (`translate-join`); filters collected by `classify` (1652-1659) and consumed by `agg-body` for ALL/ANY/SUM (1880) and `translate-count` (1956), but `translate-join` never reads `(source-filters src)`.
- What: spec §7.3 says only the elements the FILTER keeps take part. The translator emits the concatenation of ALL elements. `translate-has` refuses when filters are present; `translate-join` neither refuses nor applies them.
- Repro: `translate` on `JOIN(FILTER(("a","b","c"), _ $== "a"), ",")`, mariadb -> `CONCAT(CONCAT(CONCAT(CONCAT('a', ','), 'b'), ','), 'c')`; `lisp/bin/sel -e 'JOIN(FILTER(("a","b","c"), _ $== "a"), ",")'` -> `a`. `JOIN(FILTER(("a","b","c"), FALSE), ",")` -> SQL `a,b,c`, SEL `""`.
- Fix sketch: if `(source-filters src)` is non-empty refuse E_SQL_SHAPE (as `translate-has` does), or apply each predicate per element. Fix in all hosts.
- Conformance gap: none. Suggest `agg.join.filtered-source-is-refused` in `sql/cases/12-aggregates.sqlt`.

### LISP-C7 [high] [confirmed] SQL: FILTER binder names leak into the aggregate body and other FILTERs' predicates
- Found by: s6-C2. Cross-host (JS gives identical wrong SQL for the first repro).
- Where: `lisp/src/sql/translator.lisp:1762` (`with-element`) and :1830 (`with-row`): one frame holds the aggregate's binder AND every absorbed FILTER binder, and body and all predicates are walked in it.
- What: in SEL (spec §7.3, `FILTER(list, x, pred)`) `x` is in scope only inside `pred`. Here programs SEL rejects with E_UNDEF_VAR translate, and worse, a body variable or binding named like a FILTER binder is silently captured by the element instead of resolving to the outer one: a wrong query, not a refusal.
- Repro (bindings `V = binding-columns(a, b)` NUM, `X = binding-column("x")` NUM), postgresql: `ANY(FILTER(V, x, x > 1), q, q > X)` -> `((("a" > 1) AND ("a" > "a")) OR (("b" > 1) AND ("b" > "b")))`; SEL (`V=(5,3); X=4; ...`) -> `TRUE`, the SQL is constantly FALSE. Also `ANY(FILTER(V, a, a > 1), q, a < 9)` translates (SEL: E_UNDEF_VAR at the body `a`), and `ANY(FILTER(FILTER(V, a, b > 1), b, a < 9), q, q > 0)` translates (SEL: E_UNDEF_VAR).
- Fix sketch: give each absorbed predicate its own frame (binder -> elem, plus `_K`) and walk the body in a frame holding only the aggregate's own binder; same in `with-row`.
- Conformance gap: none. Suggest `agg.filter.binder-does-not-leak-into-body` and `...-undefined-in-body` (E_SQL_INVALID / E_SQL_UNBOUND).

### LISP-C8 [high] [confirmed] Hybrid: a fall-through MAP whose custom pair reads `_K` is evaluated over renumbered SQL rows
- Found by: s7-C1. Python's planner produces the same continuation shape and the same wrong `_K`.
- Where: `lisp/src/sql/hybrid.lisp:574-716` (`try-plan-fallthrough`), whole-row guard at :617, `reads-whole-row-p` at :543-554.
- What: the custom half of a fall-through MAP runs in memory over the rows SQL returns, after downstream SORT_BY/TOP_BY/TAKE/DROP were pushed into the statement and after any upstream FILTER (which retains ordinal keys, spec §7.3). `_K` in the MAP body is the row's key in the MAP's input, but the continuation sees keys 1..n of the post-sort/post-limit rowset. `reads-whole-row-p` rejects `_` only; `_K` is a different variable so a pair using it passes every guard. Silent wrong values.
- Repro (harness, postgresql; full = `sel:run` of the whole program):
  - `ORDERS .> MAP(RECORD("a", _["amount"], "x", JOIN(LIST(_["name"], _K), "-"))) .> DROP(1)` -> full x = al-2, cy-3, dee-4, al-5; hybrid x = al-1, cy-2, dee-3, al-4 (SQL `... OFFSET 1`).
  - `... .> SORT_BY(_["a"])` -> full x = cy-3, bob-1, al-2, al-5, dee-4; hybrid = cy-1, bob-2, al-3, al-4, dee-5.
  - `ORDERS .> FILTER(_["amount"] > 4) .> MAP(RECORD("a", _["amount"], "x", JOIN(LIST(_["name"], _K), "-")))` -> full dee-4 / al-5; hybrid dee-3 / al-4.
- Fix sketch: in `try-plan-fallthrough` return nil when any custom pair (or a dependency read) mentions the variable `_K` anywhere (conservatively, a var named `_K` in the subtree). Same for downstream steps that read `_K` (the arg walk at :631 collects only field reads).
- Conformance gap: none. Suggest plan-level cases `plan.fallthrough.custom-pair-reading-key-splits-before-the-map` (plain, with downstream DROP, with downstream SORT_BY, with upstream FILTER), plus an executed unit test since `.sqlt` cannot express the run half.

### Medium

### LISP-C9 [medium] [confirmed, re-verified by synthesizer] SORT / SORT_DESC / SORT_BY / TOP* of a scalar return an empty list
- Found by: s3-C1 (medium), s5-C5 (low). Lisp-only divergence (js/php/python/cpp agree with each other).
- Where: `aggregate.lisp:320` (`do-sort`: `(or (value-null-p val) (zerop (value-size val)))`) and :431/:439 (`do-top-sort`: `(zerop (value-size val))`).
- What: spec §7.3: an argument with no children "is treated as a one-element list containing itself when it has a scalar ... and as an empty list when it is NONE". `value-size` of a scalar is 0, so the guard treats every scalar as empty. `aggregate-elements` (`structure.lisp:7`) already implements the right rule and is used a few lines below but never reached. Second effect (s3): the in-memory optimizer moves a `FILTER` in front of a sort (rule at `optimizer.lisp:523`), so in Lisp the same program gives a different answer optimised and unoptimised; the optimised answer is the one that agrees with the other hosts.
- Repro: `lisp/bin/sel -e 'SORT("str")'` -> `-` (empty, re-verified); `node js/bin/sel.mjs -e 'SORT("str")'` -> `-{"1"=t"str"}` (re-verified). Same for `SORT_DESC(5)`, `SORT_BY(5,_)`, `TOP("s",_,1)`, `TOP("s",1)`. Optimiser interplay: `S="str"; S .> SORT .> FILTER(TRUE) .> MAP(_)` prints `-{"1"=t"str"}` while `S="str"; S .> SORT .> FILTER(1) .> TAKE(5)` is `E_NOT_BOOL` optimised versus `-` unoptimised. An exhaustive harness found 743 diffs of this family.
- Fix sketch: in both functions replace the `(zerop (value-size val))` test by `(null (aggregate-elements val))` (keep the `limit` test in `do-top-sort` after the list is evaluated).
- Conformance gap: none. Suggest `sort.scalar-is-one-element-list`, `sort_by.scalar...`, `top.scalar...`, and `rel.filter.pushdown-sort-scalar-source` (`"s" .> SORT .> FILTER(TRUE) .> MAP(_)`).

### LISP-C10 [medium] [confirmed, re-verified by synthesizer] `$` immediately followed by `.*` never matches (cl-ppcre bug on `\z.*`)
- Found by: s4-C2. Lisp-only.
- Where: `regex.lisp:236` (the `$` -> `\z` lowering exposes it); the bug is inside cl-ppcre.
- What: cl-ppcre treats a scanner whose first element after leading zero-width anchors is `.*` (dotall) as "can only match at the start position" and tries position 0 only, so a match at end-of-subject is never tried. Spec §7.8: `$` anchors to the end, so `$.*` must match empty at the end. Affects leading `$` followed by `.*`, `.*?`, `(.*)`, `(?:.*)`, also `(?:$).*`, `$$.*`, `($.*)`, `$.*$`, `$()(.*)`. Not affected: `$x*`, `$[a-z]*`, `$.{0,3}`, `a?$.*`, `(?:x|$).*`. Silent wrong RFIND/RMATCH/RREPLACE result.
- Repro: `lisp/bin/sel -e "RFIND('$.*', \"abc\")"` -> `0` (re-verified), js -> `4` (re-verified); `RREPLACE('$.*', '!', "abc")` -> `abc` (others `abc!`); `RMATCH('$.*', "abc")` -> FALSE (others TRUE). Raw: `(cl-ppcre:scan "\\z.*" "abc")` -> NIL.
- Fix sketch: lower `$` to a zero-width form cl-ppcre does not mis-analyse. s4 verified in raw cl-ppcre that `(?=\z)`, `(?!.)` and `(?:\z|[^\s\S])` followed by `(.*)` all match at 3 while `\z(?:)` and `(?:\z){1}` do not. Check the end-anchored speed-up is not lost for a common trailing `$`; only needed when `$` is the first element of the pattern or of a branch.
- Conformance gap: none. Suggest `re.anchor.end-then-dotstar` (`RFIND('$.*', "abc")` -> 4; `RREPLACE('$.*', '!', "abc")` -> `abc!`).

### LISP-C11 [medium] [confirmed, re-verified by synthesizer] A quantifier directly after `^` or `$` is accepted; the other four hosts reject it
- Found by: s4-C3. Lisp-only.
- Where: `regex.lisp:158-239` (`validate-pattern`): `^`/`$` are emitted (230-236) and the quantifier arms (205-213) pass the following `* + ? {..}` through with no "nothing to repeat" check.
- What: JS (u mode), Python, SRELL and PCRE all say "nothing to repeat" for `^*`, `$?`, `^+?`, `${0}`, `^{2}`. cl-ppcre accepts them. The file header says the validator exists to close off cl-ppcre's permissiveness (see the `{2,1}` note at :77-83); this is the same class of gap and defeats "a rule accepted here is accepted everywhere" (§7.8).
- Repro: `lisp/bin/sel -e "RMATCH('^*a', \"a\")"` -> `TRUE` (re-verified); js: `E_REGEX_SYNTAX at line 1 column 8` (re-verified); py/cpp/php also E_REGEX_SYNTAX. Same for `a$+`, `$?`, `^+?`, `${0}`, `^{2}a`. (`(^)*` and `(?:^)+` are valid in js/py/php; cpp rejects `(?:^)+`, see X3.)
- Fix sketch: in `validate-pattern` remember whether the previous emitted token was `^` or `$` and raise `bad-regex "nothing to repeat"` when a quantifier follows.
- Conformance gap: none. Suggest `re.reject.quantified-anchor` for `^*`, `$?`, `a$+`, `^{2}` (use raw `'...'` literals, since `{2}` inside `"..."` is interpolation).

### LISP-C12 [medium] [confirmed] Process-global mutable hash tables are unsynchronised: concurrent use errors out or returns wrong records
- Found by: s1-C3 (medium), s2-C4 (medium), s5-C7 (low, unconfirmed), s4-C8 (low, thread part unconfirmed), s3 thread notes.
- Where: `value.lisp:35-57` (`*shape-cache*`, mutated at compile time for every `RECORD("lit",...)` via `parser.lisp:151-158` `prepare-record-shape`, and by `from-native`); `decimal.lisp:35-58` (`*pow10-cache*`, mutated on every miss for scale/exponent above 18, cleared by `clrhash`); `structure.lisp:326-327` (`*alias-plan-cache*`); `regex.lisp:258,284-303` (`*regex-cache*`); `*registry*` (do not register concurrently with running programs).
- What: `sel.lisp` reasons about two threads racing to fill a program's physical tree ("benign"), so threaded use is contemplated, but these are plain SBCL tables, and `grep synchronized|sb-thread|mutex lisp/src` finds nothing. Nothing documents that the host is single-threaded, and Go has an explicit concurrency test (`go/sel/concurrency_test.go`).
- Repro: s1 scratchpad `l1/t8.lisp`: 8 `sb-thread` threads each compiling 20,000 distinct `RECORD("aN",1,"bID",2)["bID"]` programs. 1 and 2 threads: 0 errors; 8 threads: 5,200 to 8,200 errors per thread (about 52k "Unsafe concurrent operations", 2 "Corrupt NEXT-chain", 44 silently wrong `E_NO_KEY "no key b6"`, i.e. the wrong shape returned). s2 scratchpad `p8.lisp`: 6 threads doing `(dec-add (dec-parse "1.<k zeros>1") ...)`, k in 19..319, gave `SIMPLE-ERROR: Unsafe concurrent operations on #<HASH-TABLE :TEST EQL :COUNT 14>` within a few thousand iterations. Regex and alias caches: reasoned, not tested.
- Fix sketch: `(make-hash-table ... :synchronized t)` for `*shape-cache*` and `*pow10-cache*` (each consulted once per RECORD literal at compile time, or only on miss), a mutex around the registry and alias-plan cache, or thread-local caches. Alternatively document the host as single-threaded and add a guard. A threaded stress test in `lisp/tests/unit.lisp` would catch regressions.
- Conformance gap: none (no host-API concurrency lane).

### LISP-C13 [medium] [confirmed, re-verified by synthesizer; cross-host spec gap] The math-plan compiler coerces each operand as it loads it, so error code and position differ from plain evaluation and from spec §7.1
- Found by: s3-C2 (medium), s2-C6 (low).
- Where: `eval.lisp:153-168` (`:load-var` / `:load-leaf` call `as-dec` immediately), used by `+ - * / %` and ABS/SIGN/CEIL/FLOOR/TRUNC/ROUND/POWER/MIN/MAX; compiled at `optimizer.lisp:699`; `math-plan.lisp:74-79`.
- What: plain `eval-binary` (`eval.lisp:388-403`) evaluates both operands and then coerces (left, then right). The plan interleaves evaluate-l, coerce-l, evaluate-r, coerce-r. When the left operand is a non-number and the right operand raises, the plan reports the left operand's E_NOT_NUM/E_NULL, plain evaluation the right's E_NO_KEY/E_UNDEF_VAR. Spec §7.1: "A strict function's arguments are all evaluated, left to right, before the body runs" and §6.3 "evaluation stops at the first failure", so `MAX(TRUE, U)` must be E_UNDEF_VAR. Inside one host it is inconsistent between operators (`X["a"] + X["b"]` E_NOT_NUM vs `X["a"] > X["b"]` E_NO_KEY) and it depends on tree depth (a program past the depth cap is "returned as written" at `optimizer.lisp:727` and takes the plain path). Values are identical (0 diffs in about 20k random programs); only error code/position differ. Spec §6.4 says the optimizer must be invisible, but §5.1/§6.2 do not say when an operand is coerced (s2's reading of the same divergence).
- Repro: `lisp/bin/sel -e 'MAX(TRUE, U)'` -> `E_NOT_NUM at line 1 column 5` (re-verified); expected E_UNDEF_VAR at column 10. `x = "abc"; x + (1/0)` -> `E_NOT_NUM @1:12` but `x = "abc"; x + IF(TRUE, 1/0, 1)` -> `E_DIV_ZERO`. `X = RECORD("a","q"); X["a"] + X["b"]` -> `E_NOT_NUM` @23 but `X["a"] > X["b"]` -> `E_NO_KEY` @32. js, php, cpp and python print exactly the same as optimised Lisp, so this is a shared design of the five math-plan compilers, not a Lisp slip; only `(eval-node (program-ast p))` differs.
- Fix sketch: spec decision first (coerce-on-evaluate vs coerce-after-both). Then load all leaves of a plan as Values and coerce at the operator step in left-then-right order, or restrict the plan to leaves that cannot fail. All five hosts together.
- Conformance gap: none. Suggest `math.order.left-coercion-after-right-eval` (and `num.coerce-order.plan` / `.tree`) with `MAX(TRUE, U)`, `R["a"] + R["b"]` with text left and missing right key, for `+ - * / % ABS MIN MAX ROUND POWER`.

### LISP-C14 [medium] [confirmed] `cannot-raise-p` over-approximates, so FILTER+FILTER fusion and the logical field-read rule can hide or reorder errors
- Found by: s3-C3, s7-C6. Cross-host for the s3 part (js, php, python print the same as Lisp); the s7 part is in the shared logical optimizer.
- Where: `lisp/src/optimizer.lisp:434-449` (`cannot-raise-p`); used by the FILTER+FILTER rule at :553, the sort/MAP pushdown rules, and the sort/FILTER swap at :517-527 (reached from `hybrid.lisp:290-291`, :179 via `optimize-ast-logical`).
- What: (a, s3) literals, binders and `_K` are treated as "never raise", but `FILTER(1)`, `FILTER(NULL)`, `FILTER(_)`, `FILTER(_K)` raise per row (`as-bool`). Fusing `FILTER(p1) .> FILTER(p2)` into `FILTER(p1 AND p2)` interleaves rows, so if p2 raises on row 1 and p1 on row 2 the fused program reports p2's error where the written one reports p1's. The invariant "same value or same error code and position as unoptimised evaluation" (SEM-07 in the worklist) is broken. (b, s7) In the logical mode a `_["f"]` read is treated as total ("rows are a bound relation's") even after a MAP/BUCKET whose rows are the projection's, or for fields the relation never declared; `SORT_BY(_["r"]) .> FILTER(...) .> <renumbering step>` is rewritten to filter first, so the sort never sees rows that would have raised E_NO_KEY. `docs/internals/sql-translation.md`: "a rewrite keeps the program's value, or it does not fire".
- Repro (a): `lisp/bin/sel -e 'LIST(1,5) .> FILTER(IF(_ == 5, 1 == (1/0), TRUE)) .> FILTER(3)'` -> `E_NOT_BOOL at line 1 column 61`; unoptimised (`(eval-node (program-ast p) ...)`) -> `E_DIV_ZERO at line 1 column 39` (what the spec requires). Same for `FILTER(_)` and `FILTER(_K)`. Scalar-source variant: `S .> SORT .> FILTER(NULL) .> TAKE(-1)`.
- Repro (b): `ORDERS .> MAP(RECORD("n", _["name"])) .> SORT_BY(_["r"]) .> FILTER(FALSE) .> TAKE(1)` -> `run`: E_NO_KEY; hybrid (statement `... WHERE FALSE`): empty result. Also `ORDERS .> SORT_BY(_["nosuch"]) .> FILTER(_["name"] $== "zzz") .> DROP(0)` and `ORDERS .> BUCKET(_["customer_id"], RECORD("c", _K, "n", COUNT(_))) .> SORT_BY(_["amount"]) .> FILTER(FALSE) .> SORT_BY(_["customer_id"], "DESC")`.
- Fix sketch: `cannot-raise-p` answers "evaluation does not raise", but the callers need "the predicate yields a boolean". Return T for `:bool` only, or require the fused second predicate to be a `:bool` literal. For (b), in logical mode treat a field read as total only while the row is still the relation's row (no MAP/BUCKET/SELECT_COLS/LINK before it) and the field is declared.
- Conformance gap: none. Suggest `rel.filter.fuse.non-boolean-second-predicate-keeps-first-error` and `plan.optimizer.sort-key-missing-field-blocks-filter-swap`.

### LISP-C15 [medium] [confirmed; all hosts] SORT/SORT_BY + TAKE -> TOP* fusion changes evaluation order of the count and drops key evaluation when the count is 0
- Found by: s3-C4.
- Where: `optimizer.lisp:498-507` (rule fires for any 2-item TAKE, not only a literal), with `do-top-sort` (`aggregate.lisp:429-437`, evaluates the count first).
- What: in the written program the sort (list, then every key body) completes before `TAKE`'s argument is evaluated (§6.2). `TOP_BY(list, key, n)` evaluates `n` first, so (a) a side-effecting or failing `n` runs before the keys, (b) a bad `n` is reported instead of the key error, (c) with n = 0 no key is evaluated so key errors vanish. §7.4 only promises TOP(...,0) still evaluates the list, so (c) is arguably allowed for the builtin; (a)/(b) are not equivalence-preserving for the rewrite of a written `SORT_BY .> TAKE`.
- Repro: `C=0; LIST(3,1,2) .> SORT_BY(_ + (C=C+1)) .> TAKE(C)` -> unoptimised `-{"1"=t"1","2"=t"3","3"=t"2"}` (TAKE(3)); optimised `-` (TAKE(0)) (re-verified the optimised output `-`). `LIST(3,1,2) .> SORT_BY(U + 0) .> TAKE(U2)`: unoptimised E_UNDEF_VAR @24 (key), optimised @39 (count). `LIST(3,"a",2) .> SORT_BY(_ + 0) .> TAKE(-1)`: unoptimised E_NOT_NUM @26, optimised E_RANGE @41. All five hosts agree with each other (optimised).
- Fix sketch: fuse only when the TAKE count is a literal non-negative integer (the `try-parse-int-literal` test the TAKE+TAKE rule already uses), and decide whether n = 0 should also be excluded.
- Conformance gap: none. Suggest `rel.sort-take.count-evaluated-after-keys` (side-effect count) and `rel.sort-take.negative-count-after-key-error`.

### LISP-C16 [medium] [confirmed] Equi-join key classification ignores `;` sequences, `,` lists and assignments, producing a spurious E_UNDEF_VAR
- Found by: s5-C3. JS shares part of it (`,` form).
- Where: `structure.lisp:172-195` (`expr-depends-only-on`), consumed by `try-extract-equi-keys` :197-217 and the hash-join path at :1083 ff.
- What: the `case` on node kind has no clause for `:seq`, `:list` or `:assign` (and no otherwise-clause), so such a subtree is treated as "depends on nothing" and passes regardless of what it reads. `(A["id"]; B["id"]) == B["id"]` is classified as a left-only key against a right-only key and run as a hash join; the left key is evaluated in a frame binding only the left binder, so the read of `B` raises E_UNDEF_VAR, whereas nested-loop semantics (§7.4: `pred` sees both binders) give a normal result.
- Repro: `R = LIST(RECORD("id",1)); S = LIST(RECORD("id",2), RECORD("id",3)); COUNT(LINK(R, S, A, B, (A["id"]; B["id"]) == B["id"]))` -> Lisp `E_UNDEF_VAR ... undefined variable B` (re-verified); python, php, cpp print `2`; JS prints `2`. The `,` form `COUNT(LINK(R, S, A, B, COUNT((A["id"], B["id"])) == B["id"]))` gives E_UNDEF_VAR in Lisp AND JS while python, php, cpp print `1`. `LINK(R, S, A, B, (B["id"]; A["id"]) == B["id"])` gives E_UNDEF_VAR in Lisp and one row in JS/Python.
- Fix sketch: add `:seq`/`:list` (items) and `:assign` (both sides) clauses, plus `(t (setf all-ok nil))` for any kind not known to be a leaf (`:num :text :bool :null`). `node-contains-var-p` (`aggregate.lisp:8-21`) already walks these.
- Conformance gap: none. Suggest cases with a `;` and a `,` sub-expression in an otherwise-equi LINK predicate that reads the other binder.

### LISP-C17 [medium] [confirmed; cross-host, spec gap] The SORT ordering is not a total order for mixed numeric-looking and non-numeric text
- Found by: s5-C4.
- Where: `aggregate.lisp:243-274` (`compare-values`): the third `cond` branch (two text/bin values compare by bytes) sits before the rank fallback, next to the first branch (both numeric-looking compare as decimals).
- What: `"10" > "9"` (numeric), `"10" < "1a"` (bytes), `"1a" < "9"` (bytes), so 9 < 10 < 1a < 9 is a cycle. Sorting such data is algorithm-dependent (SBCL merge sort, V8 TimSort, PHP sort, C++ stable_sort, Python sort) and §7.3 does not define a total order. Real triggers: id columns like `"10","9","A1"`, or a text column with a few non-numeric sentinels. The optimizer's `SORT .> TAKE(n)` -> `TOP` fusion (heap-based) yields a different prefix than the unfused sort on the same host.
- Repro: `L = LIST("10","1a","9","2","1b","11","x","3.5"); S = SORT(L); JOIN(TAKE(S,4),",") & " | " & JOIN(TOP(L,4),",")` -> Lisp `2,9,10,1a | 2,10,11,1a`, JS `10,11,1a,1b | 2,10,11,1a`. `SORT(LIST("10","9","1a"))`: Lisp `9,10,1a`, JS/Python/PHP `1a,9,10`, C++ `9,10,1a`.
- Fix sketch: spec decision first: define a strict total order, e.g. all numeric-looking scalars (by decimal) before all non-numeric text (by bytes), which is what the `rank` fallback already implies across kinds; change the text/text branch to apply only when neither or both are numeric-looking. Add cases; then all hosts.
- Conformance gap: none. Suggest `("10","9","1a") .> SORT() .> JOIN(",")`, permutations, and `TOP` vs `SORT..TAKE` equality on the same list.

### LISP-C18 [medium] [confirmed; cross-host, spec decision] Empty-iteration and capture-reset semantics: cl-ppcre follows Perl, JS/C++ follow ECMAScript
- Found by: s4-C4.
- Where: whole design of `regex.lisp`; no validator rule addresses it.
- What: for a quantified group whose body can match empty, or that contains captures, Perl-family (cl-ppcre, Python, PCRE/PHP) and ECMAScript (JS, C++/SRELL) disagree on the overall match and on capture values. The "portable subset" is not portable for nullable loop bodies. RMATCH's boolean was unaffected in the cases checked; RGROUPS/RREPLACE/RFIND-length differ. Seen 25 times in 14,000 random programs (every js-vs-lisp diff outside C10/C11 was this).
- Repro (five hosts): `RGROUPS('(a|)+', "a")` lisp/py/php `["a",""]`, js/cpp `["a","a"]`; `RGROUPS('(a|)*', "aa")` lisp/py/php `["aa",""]`, js/cpp `["aa","a"]`; `RGROUPS('(?:(a)|b)*', "ab")` lisp/py/php `["ab","a"]`, js/cpp `["ab",""]` (ECMAScript resets captures each iteration); `RGROUPS('(?:a*?){1,2}', "a")` lisp/py/php `[""]`, js/cpp `["a"]` (overall match differs, so RREPLACE/RFIND-length differ too).
- Fix sketch: spec-level. Either reject in all hosts a `* + {n,m}` (max > 1) applied to a group that can match empty, and any capture inside a repeated group, or document the family split in §7.8 and pin one side. Lisp cannot follow ECMAScript with cl-ppcre.
- Conformance gap: none (`09-regex.selt` has no lazy-quantifier RGROUPS or nullable-loop case). Suggest `re.groups.nullable-loop`, `re.groups.capture-reset-per-iteration`.

### LISP-C19 [medium] [confirmed; cross-host] No bound on regex backtracking work; hosts diverge on the outcome
- Found by: s4-C5 (also s4-P4).
- Where: `regex.lisp:323-413` (no step budget, no timeout).
- What: a small input can hang the evaluator. Lisp: `RMATCH('^(a|aa)+$', REPEAT('a',40) & 'b')` took about 40 s (roughly x1.6 per extra char; n=34: 2.0 s, n=36: 4.1 s; JS about the same, n=36: 3.9 s); `^(\w+\s?)*$` on 30 `a` + `!` did not finish in 60 s. Outcomes differ: PHP's PCRE backtrack limit returns FALSE almost instantly (a silent wrong answer), C++/SRELL aborts the process (`terminate called after throwing an instance of 'srell::regex_error' what(): error_complexity` at n=30), Lisp/JS/Python run to completion. §6.4 is silent on regexes.
- Repro: `lisp/bin/sel -e "RMATCH('^(a|aa)+$', REPEAT('a',40) & 'b')"` (about 40 s); `cpp/build/sel -e "RMATCH('^(a|aa)+\$', REPEAT('a',30) & 'b')"` (abort); `php php/bin/sel` same expression -> FALSE at once.
- Fix sketch: spec-level cap (step or time budget -> new E_ code) implemented per host; in cl-ppcre a counter in a custom test hook or `sb-ext:with-timeout`. At minimum record the divergence and make C++ not abort.
- Conformance gap: none.

### LISP-C20 [medium] [confirmed; cross-host spec gap] Text-size explosions end in an uncatchable heap exhaustion
- Found by: s4-C6.
- Where: `text.lisp:130-136` (REPEAT), 138-155 (PADL/PADR), 59-75 (REPLACE), `regex.lisp:383-413` (RREPLACE); §6.4 caps only ROUND/POWER/regex-quantifier arguments.
- What: `REPEAT("a", 150000000)` exhausts SBCL's 1 GB dynamic space ("Heap exhausted during allocation", exit 1, no SEL error). Same for `REPLACE("a", REPEAT("b",1000000), REPEAT("a",1000000))` (about 1e12 characters). JS raises an uncaught `RangeError: Invalid string length`, so it is a spec gap, but Lisp's ceiling is low (1 GB, 4 bytes/char, `with-output-to-string` needs a second copy).
- Repro: `lisp/bin/sel -e 'LEN(REPEAT("a", 150000000))'` -> heap exhausted, exit 1; `node js/bin/sel.mjs -e 'LEN(REPEAT("a", 3000000000))'` -> RangeError.
- Fix sketch: spec a text-length cap (e.g. E_RANGE above N) and check `n * (length s)` / `width` before allocating in REPEAT, PADL/PADR (and the result length in REPLACE/RREPLACE/`&`).
- Conformance gap: none.

### LISP-C21 [medium] [confirmed; cross-host spec gap] Right-associative `??` / `???` chains recurse in the parser without counting depth
- Found by: s1-C2.
- Where: `lisp/src/parser.lisp:253-268` (the `#\R` branch of `parse-term`; only the assignment sub-branch is wrapped in `with-depth`).
- What: `a ?? b ?? c ...` recurses `parse-term -> parse-term` per link. §6.4 says every nesting construct is counted (and uses the `-` chain as its cautionary tale); `??` is not in its "what each construct costs" list, so this is a spec gap the code copies. With a non-null head the evaluator short-circuits and never hits the depth check, so any length up to the host stack is accepted; with a null head the evaluator gives E_DEPTH (1:1593 for `NULL ?? NULL ...`), so the two paths disagree about the same shape.
- Repro (in-process): `1 ?? 1 ?? ... ?? 1`: n=12000 gives `1`; n=15000 gives `STORAGE-CONDITION` (CONTROL-STACK-EXHAUSTED). CLI with n=20000: "Unhandled CONTROL-STACK-EXHAUSTED", rc=1. Same for `???`. Other hosts: cpp segfaults (rc=139) at 20,000; js RangeError and python RecursionError at about 5,000; php out of memory at 100,000.
- Fix sketch: needs a spec decision in all hosts: (1) count the `??` recursion (`with-depth` when the operator is consumed, as for prefix operators) and pin the boundary in `10-limits.selt`, or (2) parse the chain iteratively and fold from the right (the tree stays deep, so the evaluator's counted recursion still guards it; changes no boundary). The other deep-tree shapes (`+`, `AND`, `&`, `.>` at 100,000) compile and hit E_DEPTH in `dependencies`, not the stack.
- Conformance gap: none. Suggest `lim.coalesce-chain-*` at about 300 and 30,000 links with a non-null head.

### LISP-C22 [medium] [confirmed] `dec-format` (every number rendered to text) is corrupted when the embedder binds `*print-radix*`
- Found by: s2-C3. Lisp-only.
- Where: `decimal.lisp:167` (`(write-to-string (dec-digits d) :base 10)`; `:radix` not given, so it inherits `*print-radix*`; contrast :224 which passes `:radix nil`).
- What: with `*print-radix*` true SBCL prints `12345.` for an integer, so the scale split lands one character off. Silent wrong numbers/text for an embedder that binds the variable (common in REPL/loggers), no SEL error.
- Repro: `(let ((*print-radix* t)) (value-scalar (make-num "1.50")))` -> `15.0.`; `(sel::dec-format (sel::dec-parse "12345"))` -> `"12345."`.
- Fix sketch: `:radix nil` at :167 (or bind `*print-radix*`/`*print-base*` inside `dec-format`); better, a hand-written formatter (P6) has no such dependence.
- Conformance gap: none (unit test only; add a `lisp/tests/unit.lisp` case).

### LISP-C23 [medium] [confirmed; cross-host] SQL: exponential time/output from a tiny program (assignment substitution builds a DAG that is walked as a tree)
- Found by: s6-C3.
- Where: `stage1.lisp:234-237` (`substitute-node` returns the shared `(cdr cell)` on every read), :297-364 (`record-statement`); walked by `walk-node` (`translator.lisp:97`) and `is-constant` at every compound node.
- What: `A0 = X + 1; A1 = A0 + A0; ... An = A(n-1) + A(n-1); An > 0` is linear for the evaluator, but the translator inlines the definition at every read, so output and time are 2^n. The depth guard (200) does not bound it. Same family: nested aggregates over static lists multiply (`ANY(L1, a, ANY(L2, b, ...))` = n^k terms): a 181-character rule with 4 nested 12-element lists took 46 s and 3.6 MB.
- Repro: postgresql, X NUM column: n=10 -> 84 KB in 69 ms; n=12 -> 336 KB / 319 ms (source 190 bytes); n=18 about 21 MB / about 20 s; n=30 would be about 20 GB. JS (`js/src/sql`) shows the same doubling (n=18: 21 MB, 9.5 s).
- Fix sketch: count generated nodes/parts (or output characters) in `walk-node`/`make-literal` and refuse past a fixed budget (a new limit in `spec/limits.json`, all hosts), or refuse a second read of a non-trivial definition.
- Conformance gap: none. Suggest a limit case with a 30-statement doubling chain that must refuse quickly.

### LISP-C24 [medium] [confirmed; cross-host] SQL: UNKNOWN laundered to NUM by kind unification bypasses the numeric guard
- Found by: s6-C4.
- Where: `translator.lisp:881-898` (`unify-kinds`: "UNKNOWN unifies with anything"), :900-913 (`ret-kind` for `@unify:`), :1259-1268 (`translate-conditional` result kind); `guard-numeric` :810-825 and `emit.lisp:181` (`emit-numeric-operand` passes any `:num` without a guard).
- What: `sql-kinds.md`'s warrant: an operand read as a number is wrapped so a value SEL would refuse (E_NOT_NUM) becomes NULL instead of the server's coercion. `IF(P, X, 1)`, `X ?? 0`, `COALESCE(X, 0)` with an undeclared column X take the kind of the other branch (NUM), so the result reaches arithmetic and comparison unwrapped, while `X + 1` alone is wrapped.
- Repro (mariadb, X undeclared, P BOOL): `IF(P, X, 1) + 1` -> `(CASE WHEN `p` THEN `x` ELSE 1 END + 1)`, kind NUM; `(X ?? 0) + 1` -> `(COALESCE(`x`, 0) + 1)`; `COALESCE(X, 0) > 5` (postgresql) -> `(COALESCE("x", 0) > 5)`; versus `X + 1` -> `CASE WHEN (`x` REGEXP ...) THEN CAST(`x` AS DECIMAL(65,10)) ELSE NULL END + 1`. For x='abc' MariaDB gives 1 where SEL raises E_NOT_NUM; PostgreSQL raises a type error. JS the same.
- Fix sketch: unify to UNKNOWN when any contributing branch is UNKNOWN. Would change the kind of `X ?? 0` and some expected SQL; needs `sql-kinds.md` §4 text plus cases.
- Conformance gap: none. Suggest `warrant.numeric.coalesce-over-an-undeclared-column`, `warrant.numeric.if-branches-with-an-undeclared-column`.

### LISP-C25 [medium] [confirmed by reading; not run against a server] SQL: `IN (numeric literals)` on an EXACT text column compares bare `x = 1`
- Found by: s6-C5. JS identical.
- Where: `translator.lisp:2133-2147` (`translate-in` branch D): `item` is `f` unchanged whenever the needle is `exact`, whatever `f`'s kind; contrast `translate-binary` :1080-1082.
- What: SEL's IN is structural (EQL): `"01" IN (1)` is FALSE. `x = 1` with a text column compares numerically in MariaDB/MySQL (`'01' = 1`, `'abc' = 0` are true); PostgreSQL raises 42804. `X $== 1` and `X EQL 1` on the same column are cast correctly.
- Repro: bindings `X = binding-column("x", nil, :text, :exact t)`; `X IN (1, 2)` mariadb -> `((`x` = 1) OR (`x` = 2))`. `X IN ("1","2")` is fine.
- Fix sketch: apply the `translate-binary` rule (skip the operand cast only when the item is exact or a TEXT literal node); otherwise `emit-text-operand` the item.
- Conformance gap: none. Suggest `op.in.exact-column-against-numeric-literals`.

### LISP-C26 [medium] [confirmed divergence; direction unclear] SQL: SORT/TOP(_BY) followed by LINK is not wrapped in a derived table (Lisp); JS and Python wrap it
- Found by: s6-C6.
- Where: `translator.lisp:2686-2692` (the LINK arm wraps only for group-by/projections/select-cols/limit/offset/distinct); JS `planHasRowsAbove` (`js/src/sql/translator.mjs:2076`, used at :2593) also includes `orderBy`.
- What: byte parity is the product, so this is a defect in some host. On the merits the Lisp shape looks safer: an ORDER BY inside a derived table without LIMIT is not preserved by MariaDB (the translator's own comment at :2827-2833) or guaranteed by PostgreSQL, so JS/Python may lose the sort (and a later `TAKE` becomes arbitrary). 20 of 3,000 random pipelines differ, plus consequential differences: `LINK_LEFT` twice after a sort is REFUSED in Lisp and translated in JS; E_SQL_BINDING vs E_SQL_SHAPE for a later bad field.
- Repro: `O .> SORT(_["AMT"]) .> LINK(C, _["CID"] == C["ID"])` postgresql: Lisp `... FROM "orders" "o" INNER JOIN "customers" "c" ON (...) ORDER BY "o"."amt" ASC`; JS and Python `... FROM (SELECT "o".* FROM "orders" "o" ORDER BY "amt" ASC) "_sub1" INNER JOIN "customers" "c" ON (...)` (no outer ORDER BY). With `.> TAKE(3)` appended the JS/Python form ends in `LIMIT 3` with no ORDER BY at the join level.
- Fix sketch: decide in `docs/internals/sql-translation.md`, add a case, align the other hosts (or this one).
- Conformance gap: none (`sql/cases/26-links.sqlt:515` and `47-link-rows.sqlt:636` have a SELECT_COLS or limit before the LINK). Suggest `link.after-sort-order-by-placement`.

### LISP-C27 [medium] [confirmed] Hybrid: a split after a key-retaining FILTER hands the continuation renumbered rows
- Found by: s7-C2.
- Where: `hybrid.lisp:323-347` (step 3, longest translatable prefix) and :311-321.
- What: SEL's FILTER keeps its input's keys (§7.3); a database rowset is 1..n. The planner splits after (or leaves as last prefix step) a FILTER without asking whether the first remaining step reads `_K`. `rows-are-not-the-value-p` covers open buckets and unprojected joins only.
- Repro: `ORDERS .> FILTER(_["amount"] > 4) .> MAP(RECORD("x", _K))` -> hybrid, SQL `SELECT o.* ... WHERE amount > 4`; full x = 1,2,4,5, hybrid x = 1,2,3,4. Also `... .> FILTER(JOIN(LIST(_["name"], "x"), "-") $== "al-x") .> MAP(RECORD("k", _K))` -> full k = 2,5, hybrid k = 2,4. When the continuation ends in a FILTER the result keys differ (`{"2","5"}` vs `{"2","4"}`); that part may be accepted as "SQL returns a rowset", the `_K` reads are not.
- Fix sketch: when the last prefix step retains keys (FILTER; check DISTINCT/DEDUPE against §7.3) refuse the split if the continuation reads `_K` before its first renumbering step, or move the split earlier.
- Conformance gap: none. Suggest `plan.split.filter-then-key-reader-is-not-a-split-point`.

### LISP-C28 [medium] [confirmed] Hybrid: `inline-literals` only understands the 3-item binder form
- Found by: s7-C3. Python reproduces E_EXPECT_SYMBOL.
- Where: `hybrid.lisp:151-169` (:call case; `(= (length args) 3)` at :158 and :166).
- What: the binder-name skip is applied only to calls with exactly three items. SORT_BY/BUCKET with an explicit binder have four (`SORT_BY(src, R, key, "DESC")`), TOP_BY five, LINK with two binders five. There the binder token in item 1 is itself replaced by the helper literal and the body is inlined with the binder unbound. The same tree feeds the translator and the continuation, so the continuation is not a valid program.
- Repro: `R = 5; ORDERS .> FILTER(_["amount"] > 4) .> SORT_BY(R, R["id"], "DESC") .> MAP(RECORD("x", JOIN(LIST(_["name"], "q"), "-")))` -> plan is hybrid; the continuation over the rows gives E_EXPECT_SYMBOL while `run` of the program succeeds. Same with `BUCKET(R, R["customer_id"], RECORD(...))`. Without the `R = 5;` helper the pipeline plans as pure SQL; with it `ORDERS .> SORT_BY(R, R["id"], "DESC") .> TAKE(2)` is downgraded from pure_sql to pure_memory.
- Fix sketch: reuse one shared "binder positions of this call" function (the evaluator's rule: for a binding call with three or more items, item 1 is the binder, plus LINK's two-binder form) in `inline-literals`, `constant-call-p` and the optimizer.
- Conformance gap: none. Suggest `plan.helpers.literal-named-like-an-explicit-binder-is-not-inlined` for SORT_BY, TOP_BY, BUCKET and 5-item LINK.

### LISP-C29 [medium] [confirmed] Hybrid: splitting before a LINK renames the join's left side to `_INPUT`
- Found by: s7-C4. Python plans the same split (its run half was not executed).
- Where: `hybrid.lisp:335-338` (`cont-root`) and :700-706; `relational-plan.lisp:22` documents that the source variable names a 3-argument LINK's left side.
- What: a joined row holds the left side under the source variable's name (§7.4). The continuation's source is `_INPUT`, so a LINK in the continuation keys the left row as `_INPUT`/`_input` and later steps reading `_["ORDERS"]...` fail.
- Repro: `ORDERS .> TAKE(4) .> LINK(CUSTOMERS, _1["customer_id"] == _2["cid"]) .> FILTER(_["ORDERS"]["id"] > 1)` -> plan hybrid (prefix `TAKE 4`); `run` succeeds, the continuation raises E_NO_KEY; without a failing read the result rows carry the `_INPUT` key instead of `ORDERS`. Also `ORDERS .> TAKE(4) .> FILTER(<unsupported>) .> LINK(...) .> SORT_BY(_["ORDERS"]["id"])`.
- Fix sketch: keep the original variable name as the continuation root when it contains a LINK and have `execute-hybrid` bind the rows under both `_INPUT` and that name; or refuse a split that leaves a LINK in the continuation.
- Conformance gap: none. Suggest an executed unit case per host plus `plan.split.before-link-keeps-left-name`.

### LISP-C30 [medium] [confirmed] Hybrid: MAP fall-through evaluates custom pairs only on rows that survive the pushed-down TAKE/DROP/sort
- Found by: s7-C5.
- Where: `hybrid.lisp:541` (`+fallthrough-downstream+`), :623-634, pinned by `sql/cases/25-hybrid-plans.sqlt` `plan.hybrid.fallthrough-keeps-downstream-steps`.
- What: `run` evaluates the whole MAP and then TAKE; the plan pushes TAKE into SQL so the custom half never runs on rows cut off. §12.1 promises the continuation reports errors where `run` would. With `ABORT("x")` the first row raises in both, which is why the pinned case does not notice; a data-dependent raise does. This deviation is unstated in §12.1.
- Repro: `ORDERS .> MAP(RECORD("a", _["amount"], "x", IF(_["id"] == 4, ABORT("boom"), 1))) .> TAKE(2)` -> `run`: E_ABORT; hybrid: two rows (SQL `... LIMIT 2`). Same shape with a custom pair dividing by a column holding 0.
- Fix sketch: document the deliberate deviation in §12.1, or only push TAKE/DROP past the MAP when every custom pair cannot raise (`cannot-raise-p` exists; see C14 for its weaknesses).
- Conformance gap: `plan.fallthrough.filter-under...` pins the shape but never a raising custom pair; suggest a unit test with a raising pair beyond the TAKE window.

### Low

### LISP-C31 [low] [confirmed] CLI: invalid UTF-8 in a source file (or `-e`) is not E_UTF8
- Found by: s1-C4. JS also diverges (substitutes U+FFFD); cpp and php print E_UTF8.
- Where: `lisp/bin/sel.lisp` `read-text-file` / `main`. The library entry `compile-source` is fine (validates surrogates in `make-lexer`, `lexer.lisp:45`).
- What: spec §2: "Invalid UTF-8 in source is `E_UTF8`". The CLI opens files with `:external-format :utf-8` and does not handle the SBCL decoding error.
- Repro: `printf '"a\xffb"' > bad.sel; lisp/bin/sel bad.sel` -> "Unhandled SB-INT:STREAM-DECODING-ERROR" (rc 1). cpp and php: "E_UTF8 at line 0 column 0: invalid start byte 0xff at byte 2". With `-e $'"a\xffb"'` SBCL prints "WARNING: Error initializing *POSIX-ARGV*: :UTF-8 c-string decoding error".
- Fix sketch: read the file as octets and pass them through the (correct, fuzz-verified) `decode-utf8`; for argv use a latin-1 external format for the runtime and re-decode strictly.
- Conformance gap: none (suite is text-in). Not relevant to library users.

### LISP-C32 [low] [confirmed] `from-native` raises CL `TYPE-ERROR` on malformed or heterogeneous list input
- Found by: s2-C5.
- Where: `value.lisp:665-685` (alist detection looks only at `(first x)`, then `(car pair)` / `(cdr ...)` are applied to every element).
- What: spec §8 wants a SEL error (review HOST-20: "never a CL TYPE-ERROR"). Also: a list of string lists (CSV rows `(("a" "b") ("c" "d"))`) is silently taken as an alist with keys "a","c" (inherent CL ambiguity, worth one docs sentence).
- Repro: `(from-native '(("a" . 1) 5))`, `'(("a" . 1) (2 . 3))`, `'(1 2 . 3)`, `'((1 . 2))`, `(list (cons "a" 1) nil)` -> `TYPE-ERROR` (measured); expected `E_BAD_ARG`.
- Fix sketch: validate that every element is a cons with a string car before entering the alist branch; guard improper lists.
- Conformance gap: none (host API; add to `lisp/tests/unit.lisp`).

### LISP-C33 [low] [confirmed; identical in js, php, cpp, python] Assignment (and `,`) can build a value nested past the 200 cap
- Found by: s3-C5.
- Where: `eval.lisp:474` (chain length only), :508 (`value-copy` starts its own depth count at 1), :311/:309 (`eval-list` `value-copy` without position).
- What: §6.4: a value's nesting is capped and the error is reported at the assignment target. `resolve-target` checks `1+ (length chain)` but the stored RHS can itself be deep, so target depth + value depth exceeds the cap; the assignment succeeds and the value cannot then be read (E_DEPTH at 0:0, no position).
- Repro: `A[1]x150 = 1; B[1]x150 = A; 7` prints `7` (a 300-level value exists); `... ; B` gives `E_DEPTH at line 0 column 0`; `... ; C = LIST(B); 7` gives E_DEPTH at 0:0 in Lisp/JS/C++ and at the node in PHP/Python. `A = 5; A[1]x199 = 1; (A, 2)` returns then fails at 0:0 on printing.
- Fix sketch: in `eval-assign` call `(value-copy-at rhs (length path) (node-pos node))` and pass `(node-pos node)` plus the list's own depth to the copies in `eval-list`.
- Conformance gap: none (`lim.value-depth*` covers chain-only). Suggest `lim.assign-depth-target-plus-value`.

### LISP-C34 [low] [confirmed] `register-builtin` is exported, unguarded and defaults to `:overwrite t`
- Found by: s3-C6. Lisp-only.
- Where: `registry.lisp:77-83`, exported at `package.lisp:66`.
- What: `register-function` (the documented §8.1 path) refuses builtin names, malformed names, reserved words, bad arity and non-functions. `register-builtin` does none of these and skips the manifest agreement `define-builtin` enforces; with `:overwrite t` it replaces core functions process-wide. Not in the docs (`grep register-builtin docs/` empty); only in `tests/unit.lisp`.
- Repro: `(sel:register-builtin "count" 1 1 (lambda (a c) (declare (ignore a c)) (sel:make-int 42)))` then `COUNT(LIST(1,2,3))` gives 42; `register-function "COUNT"` refuses with "is a builtin".
- Fix sketch: default `:overwrite nil`, reject manifest names, or drop the export.
- Conformance gap: n/a (`tools/check-api.sh` could probe "host function cannot replace builtin").

### LISP-C35 [low] [confirmed, spec-vs-behaviour note; Lisp follows the majority] TAKE/FILTER/DROP/DEDUPE/SORT/LINK alias their elements, and mutation through the source is observable
- Found by: s5-C6.
- Where: `structure.lisp:54-93` (TAKE, DROP), `aggregate.lisp:137-209`, :318-421.
- What: `docs/contributing.md` justifies not copying ("no program can tell apart, because a binder cannot be assigned"), but the source variable can be assigned in the body: `R = LIST(RECORD("id",1), RECORD("id",2)); MAP(TAKE(R,2), (R[2]["id"] = 99; _["id"]))` prints `1, 99` on all hosts, whereas §3.4 says aggregates copy what they collect (would give `1, 2`). Hosts also diverge in neighbouring cases (JS `SORT_BY` result copies: `2,1` vs Lisp/py/cpp/php `2,99`; bare `BUCKET(R,key)` copies in lisp/js/py, aliases in cpp/php).
- Fix sketch: either copy in those builtins (a cost) or amend §3.4 / `contributing.md` to say the alias is observable when the body assigns through the source.
- Conformance gap: none.

### LISP-C36 [low] [confirmed; needs an application-registered dialect] Missing or incomplete `textEscape` emits unescaped inline text (SQL injection class)
- Found by: s6-C7, s7-C7 (same root cause).
- Where: `emit.lisp:85-108` (`emit-text-literal`: `(if (not (and escape (listp escape))) (concatenate quote text quote) ...)`); `map.lisp:256-286` (`check-lexical` checks shape only; NIL "withdrawal" is always allowed, nothing checks that the quote character has a rule; `define-dialect` forces only `version`).
- What: `dialect-lexical` returns NIL for both "absent" and "withdrawn"; `textEscape` of NIL/`()`, a map without an entry for the quote, or a root dialect that never declares it all fall into the unescaped branch. The `map.lisp` comment already calls the string case "an injection". Shipped dialects are all fine (`''` doubling verified for mariadb/mysql/postgresql/sqlite; mysql also doubles `\`).
- Repro: `(define-dialect "pg-noesc" '(:extends "postgresql" :version "16" :target t :lexical (("textEscape" . nil))))` then `T $== "a' OR '1'='1"` inline -> `CAST('a' OR '1'='1' AS TEXT)`; `:lexical (("textEscape" ("x" . "y")))` behaves the same. s7: `NAME $== "it's' OR 1=1 --"` -> `CAST('it's' OR 1=1 --' AS TEXT)`.
- Fix sketch: in `check-lexical`/`define-dialect` require a non-empty map whose keys include the `textQuote` character (and refuse NIL), and have `emit-text-literal` refuse rather than concatenate when no rule covers the quote.
- Conformance gap: none. Suggest a `--- register` case that must be refused.

### LISP-C37 [low] [confirmed, no current divergence] Regex flag parsing uses `char-downcase`
- Found by: s4-C7.
- Where: `regex.lisp:266-267` (`for f = (char-downcase ch)`).
- What: contradicts "never use the host's case mapping". Harmless today (only ASCII `I`/`M`/`S` fold to flag letters; U+0130 is left alone by SBCL 2.6.8, `RMATCH("a","A","\u{130}")` -> E_BAD_ARG like every other host) but would change with another SBCL/Unicode table. All five hosts accept an uppercase `I` although §7.8 says "`i` and nothing else": a spec nuance.
- Fix sketch: compare explicitly against `#\i #\I` etc. (or mask `char-code` for ASCII only).
- Conformance gap: partial (`re.flag.unknown`). Suggest `re.flag.non-ascii-case-variant` (U+0130, U+0131) and pin whether `I` is legal.

### LISP-C38 [low] [confirmed] About 21 SQL refusal messages contain a literal `~` + newline
- Found by: s6-C8.
- Where: `refuse` (`sql/errors.lisp:20`) does not call FORMAT, but call sites pass strings with `~<newline>` continuations: `translator.lisp` 138, 214, 536, 1400, 1448, 1634, 1662, 1827, 1848, 1864, 1993, 1999, 2003, 2139, 2808; `emit.lisp:134`; `stage1.lisp` 240, 243, 305, 320, 345 (the `binder-none` reasons at 1827/1848/1864 reach `refuse` through `from-binder` :654).
- Repro: `(1, 2)` -> `a list is not a SQL value; a list can only be the thing an ~\naggregate iterates`; `O .> BUCKET(_,...) .> MAP(...) .> ... _K` -> `a row of a relation has no key: SQL rows ~\nare unordered ...`.
- Fix sketch: run the message through `(format nil msg)` in `refuse` and `binder-none` (audit for bare `~`), or drop the `~`s at those sites.
- Conformance gap: none (messages are not contract).

### LISP-C39 [low] [confirmed; JS also diverges] SQL: TAKE/DROP counts are emitted as arbitrary-size integers
- Found by: s6-C9.
- Where: `translator.lisp:2156-2169` (`eval-int-param` returns a bignum), :3137-3154 (rendered with `~D`).
- Repro: `R .> TAKE(99999999999999999999999)` -> `LIMIT 99999999999999999999999` in every dialect; PostgreSQL/SQLite reject it (out of bigint range), MariaDB rejects above 18446744073709551615. `DROP` likewise; `DROP(9007199254740993)` is exact here but JS prints `9007199254740992` and `LIMIT 1e+23` for 1e23 (mostly a JS bug).
- Fix sketch: clamp TAKE past 2^63-1 to "no limit" and refuse an OFFSET past it; add the shared limit to `spec/limits`.
- Conformance gap: none. Suggest `stmt.take.count-beyond-int64`.

### LISP-C40 [low] [confirmed; JS reports the same] SQL: parameter slots of a discarded validation pass stay in `fragment-params`
- Found by: s6-C10.
- Where: `translator.lisp:2681-2685` (`compile-statement` called on the plan only so earlier steps refuse first; its literals were pushed into the shared params by `make-literal`, result discarded).
- Repro: `O .> FILTER(_["AMT"] > 3) .> SORT(_["AMT"]) .> LINK(C, _["CID"] == C["ID"])` -> `(length (fragment-params f))` = 2 but the part list references only slot 2. `(bindings f)` is right; `fragment-params` and the `:debug` numbering carry an orphan. An application binding `fragment-params` would send an extra parameter.
- Fix sketch: snapshot/restore `translator-params`/`param-kinds`/`caveats` around the validation pass, or compact params at the end.
- Conformance gap: none. Suggest a `--- mode params` case with a literal in a FILTER before a SORT and a LINK.

### LISP-C41 [low] [unconfirmed impact] SQL: NUL characters in TEXT literals and RECORD aliases are emitted as-is
- Found by: s6-C11.
- Where: `emit.lisp:85-108` and :148-154 (`emit-ident`); `binding.lisp:39` refuses NUL only for binding-supplied identifiers.
- What: `X $== "a\u{0}b"` inline emits a raw NUL. No injection was found (a truncating driver leaves an unterminated string = syntax error), but PostgreSQL cannot hold NUL in text and other servers differ; not checked against a live server.
- Fix sketch: refuse NUL in inline text literals/aliases with E_SQL_UNSUPPORTED (params mode is the driver's business).
- Conformance gap: none.

### LISP-C42 [low] [confirmed] `check-numeric-guard` memoises the dialect before verifying it
- Found by: s7-C8.
- Where: `map.lisp:161-163` (`(push dialect *guard-checked*)` before the checks).
- Repro: register `pgbad` extending postgresql with `numericGuard "CAST({0} AS NUMERIC)"`; the first `translate` of `NAME + 1` signals; the second returns `(CAST(CAST("o"."name" AS TEXT) ...`, the guard the check rejected.
- Fix sketch: push onto `*guard-checked*` only after all checks pass.
- Conformance gap: none (unit lane could pin it).

### LISP-C43 [low] [confirmed] `source-tables` over-reports a binder that shadows a relation name
- Found by: s7-C9.
- Where: `hybrid.lisp:41-59` (the walk treats every `:var` naming a binding as a read; used on the original tree for pure-memory plans at :246).
- Repro: `LIST(1) .> MAP(ORDERS, ORDERS)` -> `source_tables` `("orders")`, pure_memory. Over-reporting is conservative rather than unsafe (drives grants/connection choice).
- Fix sketch: track binder scope in the walk (same shared binder-position function as C28).
- Conformance gap: none.

### LISP-C44 [low] [unconfirmed] Join pre-filter state can outlive a caught error
- Found by: s3-C7.
- Where: `eval.lisp:377-386` (`??` handler) with `aggregate.lisp:188` (`context-join-prefilter-report` set, then the body walk can raise).
- What: frames and depth are restored by `unwind-protect`, but the report slot is cleared only when consumed. The consumer keys its lookup on node identity and the reviewer could not build a program that consumes a stale report: a hardening note only.
- Fix sketch: have the `??` handler (or `eval-node`) reset both prefilter slots, or clear the report in the FILTER walk's `unwind-protect`.
- Conformance gap: none.

### LISP-C45 [low] [confirmed, no observable effect] `make-int` uses a double-float constant
- Found by: s2-C7.
- Where: `value.lisp:207` (`(floor (* (1- +max-int-digits+) 3.3219280948873626d0))`).
- What: only a bit-length prefilter (s2 checked it cannot cause a false accept/reject), but it is the only float in the numeric core and the project rule is absolute.
- Fix sketch: `(defconstant +int-cap-bits+ 3321927)` or reuse `+max-int-bits+` (`decimal.lisp:22`).
- Conformance gap: n/a.

### LISP-C46 [low] [confirmed; spec text gap, no divergence] Spec does not state that binding builtins never take the `.>` placeholder rule
- Found by: s1-C5.
- Where: `parser.lisp:376` `(not (spec-binds spec))`; identical in js (`parser.mjs:326`), python, php, cpp; `spec/SPEC.md` §5.10 rule 3 and `grammar.md` are silent.
- What: `(1,2,3,4) .> FILTER(_ % 2 == 0)` works in all five hosts because binding functions never substitute `_`; the rule is unwritten, so a sixth host could get it wrong.
- Fix sketch: one sentence in §5.10 rule 3 plus a case where `_` appears in a binding function's args with enough arguments that the arity clause alone would trigger substitution.
- Conformance gap: `pipe.placeholder.*` cover non-binding functions only. Suggest `pipe.placeholder.binding-fn-untouched`.

### LISP-C47 [low] [confirmed] Doc drift on the fall-through's allowed downstream steps
- Found by: s7-C10.
- Where: `docs/internals/sql-translation.md` (MAP fall-through bullet lists `FILTER`) vs `hybrid.lisp:541` (`("SORT_BY" "TOP_BY" "TAKE" "DROP")`; FILTER deliberately excluded per the comment at :540).
- What: the code is the safer side; the doc is stale. Not Lisp-only.

## Performance findings

Ordered by impact. Prior Lisp performance work (CHANGELOG entry "Common Lisp alias lookups, prepared arguments and GC-aware measurements (2026-09-20)", `tools/lisp-runtime/`, `tools/benchmark-lisp-traversal.lisp`, `docs/interim/lisp-runtime/`) covered: alias-plan lookups in `structure.lisp`, prepared argument vectors and `make-args` allocation, AST node allocation size, `walk-create`/`resolve-target`/assignment/`COALESCE` traversal, and retained-memory probes, using scenarios S1 to S6 and Mandelbrot. I only skimmed those logs and the README; none of the findings below touch those areas except where noted, so every entry here should be treated as NEW. Explicit overlaps: P27 lists per-call `args` allocation, which s3 says prior work already covered ("not re-measured").

### LISP-P1 [impact: high] [measured] `emit-parts` calls `(length acc)` per interpolation: quadratic in the number of interpolations
- Found by: s1-P1.
- Where: `lexer.lisp:292` and :295 (`(mark (length acc))` and `(= (length acc) (1+ mark))`); `acc` is the whole reversed token list so far.
- Evidence: one literal `"{1}{1}...{1}"`: N=4,000: 0.20 s; 16,000: 3.6 s; 64,000 (192 KB): 71 s. Other hosts compile the 16,000 case in under 1 s (whole process). Replacing both `length` calls with an identity check (`(eq acc mark)` where `mark` is the cons just after pushing the opening paren, i.e. "no token was added by `lex-range`") gives 4,000: 0.003 s; 16,000: 0.012 s; 64,000: 0.106 s. Semantics identical (`{ }` and comment-only bodies still raise E_SYNTAX at the same position); the reviewer ran the patched function in-session only.
- Fix sketch: the empty-body test becomes `(eq acc mark-after-open-paren)`. DoS-class quadratic on tens of KB of input; the single best fix in slice 1.

### LISP-P2 [impact: high] [measured] Legal 1,000,000-digit numbers cost 5 to 150 s per operation (schoolbook conversion and multiplication plus redundant work)
- Found by: s2-P1, s1-P8, s5-P4 (part 2). Related: P5.
- Where: `decimal.lisp:60-66` (`num-digits`), :82-93 (`dec-guard`), :117-124 (`parse-bignum-string`), :211-232 (`dec-trim-scale`), :303-307 (`%dec-mul-general`), :41-58 (`pow10`); `parser.lisp:394-398`; `optimizer.lisp:14-26,66,108,130`.
- Why slow: SBCL bignum `*` and `truncate` are O(n^2) (squaring 10^500000: 1.3 s vs 0.17 s with a 12-line Karatsuba). Programs of 40 bytes to 2 MB can burn minutes where other hosts need 0.03 to 7 s. Avoidable costs, each measured separately:
  1. `dec-trim-scale` ends with `(parse-integer text :end end)` (quadratic in SBCL: 100k digits 1.0 s; 1M digits over 100 s), the one place not using the D&C `parse-bignum-string`: `CANON(<900k-digit literal with 400k trailing zeros>)` 49 s (JS 2.1 s, PHP 0.43 s, C++ 0.03 s).
  2. `dec-guard` on a too-large result calls `num-digits` (5.7 s for a 1M-digit value, builds 10^999999 to compare) and `%dec-mul-general` multiplies first and asks second: `<999999 nines> * <999999 nines>` = E_RANGE after 152 s (37 s in isolation; JS 6.8 s, PHP 2.7 s, C++ 26 s).
  3. A number literal is parsed, re-formatted at parse time (`parse-primary` formats `parsed` back into `node-s` to canonicalise `007`), parsed again by `fold-node` (`dec-parse (node-s l)`), and again by `compile-math-plan` because `copy-node-shallow` drops `node-dec-val` (P5). `<1M nines> + 1`: 22 s uncontended; `<1M nines> == <999998 nines>8`: 39 s (JS 3.2 s, PHP 0.74 s, C++ 0.05 s).
  4. `pow10(k)` for k > 18 is `(expt 10 k)`: 5.5 s for k = 10^6 (0.3 s with Karatsuba; 5^k << k halves it). `ROUND(1, 1000000)`: 11.8 s (contended) / 6.1 s vs JS 0.9 s, PHP 0.36 s, Python 1.2 s, C++ 0.03 s. TRUNC/FLOOR/CEIL/ROUND-down/`dec-integerp`/`dec-cmp` pay a fresh 10^scale when the scale is large and the cache (64 entries / 1M weight, cleared wholesale) misses, even where bit lengths decide (`TRUNC(0.<999999 zeros>1)` is 0 by inspection; 3.1 s).
  5. Printing 10^6-digit integers with `write-to-string` is itself about 4.7 to 9 s (`dec-format`, `decimal.lisp:165`).
  s1 adds: dec-parse of a 1,000,000-digit literal takes 10.7 s (10,000 digits 0.004 s; 100,000 0.21 s; 200,000 0.66 s), roughly quadratic, in `parse-bignum-string` / `dec-format` and not the lexer.
- Evidence (measured, uncontended unless noted): dec-parse 999,999 digits 6.5 s; dec-format 7.3 s; `+1` 22.3 s; `==` 39.4 s; CANON 49.0 s; `POWER(9999999999, 99999)` 6.9 s vs JS 0.77 s / PHP 0.53 s / Py 1.05 s (C++ 5.7 s); `POWER(3.14159, 100000)` 1.7 s vs 0.3 to 0.5 s. In a scratchpad copy with `dec-val` copied in `copy-node-shallow` (plus C1 fixed), fold/plan using `node-dec-val`, `parse-primary` keeping the token when canonical, `dec-trim-scale` using `parse-bignum-string`, pre-multiply digit-count bound and bit-length guard: `+1` 22.3 -> 7.6 s, `==` 39.4 -> 5.6 s, CANON 49 -> 8.1 s, `*` E_RANGE 37 -> 5.9 s. Adding a 12-line Karatsuba (`kmul`, threshold 6000 bits) for parse, mul and `pow10`: `==` 1.2 s, `*` E_RANGE 1.3 s, `POWER(9999999999,99999)` 5.3 s (now bounded by `dec-format` printing), `+1` 4.5 s. All 1091 conformance cases and the 6,000-case fuzz stayed green.
- Fix sketch: (a) replace `parse-integer` in `dec-trim-scale`; (b) bound-check before multiplying and decide `dec-guard` from `integer-length` (lo = 1+floor((bits-1)*30102/100000), hi = 1+floor(bits*30103/100000)); (c) stop re-parsing/re-formatting literals (P5); (d) build 10^k via Karatsuba or 5^k<<k and short-circuit TRUNC/FLOOR/CEIL/ROUND-down/`dec-integerp` when `integer-length(digits)` proves `digits < 10^scale`; (e) optional Karatsuba for parse/mul and a D&C formatter. All keep results byte-identical.

### LISP-P3 [impact: high] [measured] `eval-binary` dispatches on the operator with chains of `string=`
- Found by: s3-P1.
- Where: `eval.lisp:365-430` (`eval-binary`), :327-334 (`compare-result`), :420-423 (`(subseq op 1)` allocation for `$` operators).
- Why slow: for every `>`/`<`/`==` node the evaluator runs `string=` against AND, OR, ??, ???, then `member ... :test #'string=` over 5 arithmetic operators, `&`, EQL, IN, XOR, three bitwise ops, `char= #\$`, then `member` over 12 compare operators, then `compare-result` runs up to 6 more. Up to about 25 generic `string=` calls per binary node.
- Evidence: sb-sprof flat profile of `X .> FILTER(_["a"] > 10 AND _["b"] < 5 AND _["a"] != 77) .> COUNT` over 200k rows: `STRING=*` 34.4% self, `EVAL-BINARY` 11.7%, `%MEMBER-TEST` 1.5%, `COMPARE-RESULT` 1.4%. A prototype (`binary-op-code` maps the operator string to a keyword by length/char, then `case`) is byte-identical on 23k+ random programs and took the same three filters from 330/681/519 ms to 170/385/326 ms (min of 5 reps, repeated twice): 1.5 to 1.9x on its own.
- Fix sketch: intern the operator once per node (an `op` keyword/fixnum slot on `node`, filled in `bin-node`, or computed by char-dispatch) and `case` on it in `eval-binary`, `compare-result` and `eval-assign`; drop the `(subseq op 1)` allocation.

### LISP-P4 [impact: high for text sorts] [measured] SORT/SORT_BY/TOP on text keys re-encode UTF-8 and re-parse a decimal on every comparison
- Found by: s5-P1.
- Where: `aggregate.lisp:243-274` (`compare-values`), called from comparator lambdas at :415, :474-478, :530.
- Why slow: for non-numeric text, `looks-numeric` runs `dec-parse` (and fails) per operand per compare, then `bytes-compare` calls `as-bytes`, which UTF-8 encodes both strings into fresh vectors. n log n compares, each doing this twice.
- Evidence: 50k records, `SORT_BY(R, _["name"])`: 1.2 to 1.8 s and 180 MB consed, versus 0.2 s for numeric keys and 0.04 s for integer keys. Profile: ENCODE-UTF8 42%, DEC-PARSE 16%. A prototype decorating each key once (dec or NIL, bytes or NIL, kind) and comparing with the same rules: 0.35 s including decoration, about 5x faster, identical order. TOP pays the same (`TOP 3 text` 50k: 36 ms, 13.7 MB for a 3-row result).
- Fix sketch: extend `sort-item` with precomputed `dec`, `bytes`, `null-p`, `kind` filled once per item; a comparator mirroring `compare-values` on those fields (keep `compare-values` as the reference for the mixed-kind fallback and for whatever C17 decides).

### LISP-P5 [impact: medium] [measured] `copy-node-shallow` drops `node-dec-val`, so every optimised numeric literal loses its parsed decimal
- Found by: s3-P2, s2-P3. Depends on C1 being fixed first.
- Where: `optimizer.lisp:14-26` (copies every slot except `dec-val` and `argument-plan`; the struct comment at `parser.lisp:25` acknowledges only the latter). Folded `:num` nodes at :66/:119 carry no `dec-val`. Consumers: :66/108-109/130-131 (`dec-parse (node-s ...)`), `math-plan.lisp:82-83`, `eval.lisp:262`.
- Why slow: `eval-dispatch :num` builds the value with `(node-dec-val node)`, nil in the physical tree, so `as-dec` calls `dec-parse` (11% in the profile: DEC-PARSE, DIGITS-ONLY-P, DEC-NUMBER-STRING-P, %MAKE-DEC) each time a literal is compared or passed to a builtin outside a math plan. Cheap for `5`, quadratic pain for large literals (P2.3).
- Evidence: `A > 1`, 300k runs, min of 7: optimised 278 to 341 ms vs plain-tree 239 to 283 ms (optimised is slower; three separate processes); with `dec-val` copied and folded literals given theirs: 257 to 282 ms vs 254 to 280 ms. `A == 5 AND B != 3`: 560 to 692 ms optimised vs 418 to 546 plain, patched 426 to 503 vs 422 to 498. Combined with P3 the 200k-row FILTER benchmark goes 681 -> about 290 ms. s2: measured with the P2 patch, which needs C1 fixed first, otherwise `hash.bucket.numeric-cache` and `rel.bucket.map-spelling-equals-projected-spelling` fail because `eval-key-hash` reads dec-val presence as "numeric".
- Fix sketch: add `(node-dec-val copy) (node-dec-val n)` to `copy-node-shallow`, use `node-dec-val` in fold, and set it on folded results (`dres` is already a DEC). Order matters: fix C1 first.

### LISP-P6 [impact: medium] [measured] `dec-parse` and `dec-format` are slow for ordinary short numbers
- Found by: s2-P2.
- Where: `decimal.lisp:105-163` (`dec-number-string-p` + `dec-parse`), :165-180 (`dec-format`).
- Why slow: a decimal with a fraction takes the general path: `dec-number-string-p` (3 scans, `find`/`position` on a non-simple string), `subseq`, `position`, two `subseq`s, `concatenate`, `position-if` with a closure, `parse-bignum-string`, `dec-make`. `dec-format` goes through `write-to-string` + `concatenate`/`subseq` x2.
- Evidence (2M iterations): `dec-parse "12345.67"` 472 ns, `"12345"` 162 ns; `dec-format` of 12345.67 286 ns. A single-pass fixnum-accumulating parser for at most 19 chars (falls back otherwise; scratchpad `p6.lisp`) takes 64.5 ns / 48.5 ns (7.3x / 3.3x) and agrees on 18 edge strings; a hand-written digit-buffer formatter for `digits < 2^62`, `scale < 20` takes 65 ns (4.4x) and also removes the `*print-radix*` dependency of C22.
- Fix sketch: as above; every text->number and number->text conversion in a rule pays this.

### LISP-P7 [impact: medium] [measured] DEDUPE/DISTINCT on BIN values is quadratic (`value-hash` hashes a BIN only by its length)
- Found by: s2-P4.
- Where: `value.lisp:554-557` (`(:bin ... (sxhash (length b)))`); use `builtins/structure.lisp:159` (`do-dedupe`).
- Evidence: `COUNT(DEDUPE(B))` over N distinct 4-byte BINs: N=2,000 0.41 s, 4,000 1.33 s, 8,000 5.98 s (x4 per doubling), vs 1 ms for 8,000 distinct texts.
- Fix sketch: fold the octets into the hash (FNV over the vector, or `sxhash` of a length-prefixed form). Results unaffected; only bucket placement changes.

### LISP-P8 [impact: medium] [measured] Rows without a record shape take the slow `make-joined-row` path in LINK (about 6x slower)
- Found by: s5-P2.
- Where: `structure.lisp:403-446` (`make-joined-row`), selected at :617-619 when a row has no shape/storage.
- Why slow: an EQUAL hash table per joined row, `ascii-upcase` of every key of both rows with `mapcar`, and `member` scans (O(fields^2)) per row; the compiled-plan path (`make-join-plan`) avoids all of it. Rows are unshaped after `value-set`-built records, assignments like `R[i]["x"] = ...`, `RECORD` with duplicate keys, and host API values built with `make-none`+`value-set` (`from-native` builds shaped ones).
- Evidence: 50k x 25k equi-join on 4-field rows: 0.93 s / 143 MB unshaped vs 0.18 s / 26 MB shaped (profile: MAKE-JOINED-ROW 72%, STRING=* 15%).
- Fix sketch: shape unshaped rows once before the join via `get-record-shape`, or cache per key-list plans for children-based rows as the plans do for shapes.

### LISP-P9 [impact: medium for non-trivial predicates] [measured + reasoned] Nested-loop LINK: per-pair row aliasing, and no hash join for AND-conjunctions or predicates mentioning an outer variable
- Found by: s5-P3.
- Where: `structure.lisp:1336-1349` (fallback: `(ensure-row-table-alias item2 b2)` inside the inner loop) and :197-217 / :172-195 (only a bare `==`/`$==` between a b1-only and a b2-only expression qualifies; a free variable such as `OFFSET` counts as "depends on something else").
- Why slow: (a) the aliased right row is rebuilt for every (left,right) pair though it depends only on the right row: n*m allocations. Profile of a 700x700 non-equi LINK: ENSURE-ROW-TABLE-ALIAS 13%, %MAKE-VALUE-RAW 9.5%. (b) `A["k"] == B["k"] AND A["d"] < B["d"]` is O(n*m) with the full evaluator per pair: 1000x1000 = 4.4 s, 450 MB; the same-size equi-join takes ms. Extracting an equi conjunct with a residual predicate must keep error ordering, so this is a design task.
- Fix sketch: (a) precompute an `aliased-val2` vector once (safe, same for every left row); (b) optional: accept AND-chains whose first conjunct is an equi key, evaluating the rest per candidate pair in order.

### LISP-P10 [impact: medium] [measured] `execute-hybrid` deep-copies the entire caller context on every call
- Found by: s7-P2.
- Where: `hybrid.lisp:743-746` (`sel:value-copy` of the caller's context).
- Evidence: with a 300,000-element list in the context, 20 executions of a trivial hybrid plan take 2.53 s (about 127 ms each) versus about 0 with an empty context.
- Fix sketch: skip the copy when the continuation contains no `:assign` node, or copy only the top level and copy-on-assign the touched key. Behaviour identical because nothing can write through an alias without an assignment.

### LISP-P11 [impact: medium] [measured] SQL: `emit-fill` splices whole part lists at every nesting level; left-folded n-ary translations are super-linear
- Found by: s6-P1, s7-P1.
- Where: `translator.lisp:1009-1019` (`fold-pairwise` -> `apply-entry` -> `emit-fill`), `emit.lisp:220-317` (`splice`, `push-str`; :238-249).
- Evidence: `X IN L` with a value-binding of n elements: n=1,600 115 ms, 3,200 478 ms, 6,400 1.46 s (4x per doubling); `ANY(L, _ > 5)` n=1,600 149 ms, 3,200 585 ms; sb-sprof of the n=3,200 ANY: `splice` 55% total, `push-str` 25%, `list-nreverse` 12%. `ANY` nested 4 deep x 12 elements (3.6 MB output) took 46 s. s7: IN list of n text literals: n=250 0.010 s, 1000 0.081 s, 4000 0.66 s (6000 about 1 s; profile of the 6000 case: SPLICE 34% self, PUSH-STR 17%, MAKE-LITERAL 12%, LIST-NREVERSE 11%); COALESCE of n literals: 4000 -> 0.15 s. Replacing `(nth i args)` in `join-from` with a list walk did NOT help (s7), so the cost is the part-list copying, not `nth`.
- Fix sketch: build the disjunction as one n-ary template fill (`{*}` join with " OR ") or a balanced tree, or make fragments a rope/vector that `emit-fill` nconc-appends. Output must stay byte-identical (left-associative shape is pinned by cases; parenthesisation is the risk).

### LISP-P12 [impact: medium] [measured] SQL: `make-literal` and fragment rendering are O(P^2) in the number of parameters
- Found by: s6-P2, s6-P3.
- Where: `translator.lisp:80-86` (slot id from `(length (translator-params tr))`, 23% self time in the n=3,200 ANY profile); `fragment.lisp:49-64, 66-94, 133-144` (`frag-join`, `bindings`, `slot-inline-p` use `nth` per slot).
- Evidence: 12,800 params: `as-value :params` 0.5 s, `bindings` 0.38 s, `:inline` 0.42 s (1,600 params: 5 to 12 ms).
- Fix sketch: keep a counter in the translator struct (the list is already reversed for O(1) push); convert `params`/`param-kinds` to vectors once per render.

### LISP-P13 [impact: medium] [measured] Nested interpolation re-scans the remaining literal at every level and allocates positions eagerly
- Found by: s1-P3. Fixed together with C4.
- Where: `lexer.lisp:188` (`match-brace` pre-scan per `{`), :233-264 (`match-brace` / `skip-quoted` each call `lexer-pos-at` and allocate a `pos` even when no error results).
- Evidence: O(n * depth): 1000 levels 0.12 s, 4000 levels 2.3 s. The eager-`pos` share is guessed small and was not separately measured.
- Fix sketch: single-pass lexing (C4 (a)); compute `pos` lazily, only on the failure path.

### LISP-P14 [impact: medium] [measured] RECORD key duplicate check is quadratic (`remove-duplicates :test #'string=` on a list)
- Found by: s1-P2, s5-P6 (reasoned), s2 (`from-native`).
- Where: `parser.lisp:157` (`prepare-record-shape`), `value.lisp:673` (`from-native`), `structure.lisp:42` (first evaluation of RECORD).
- Evidence: compiling `RECORD("k0",0,"k1",1,...)`: 1,000 keys 0.03 s; 4,000 0.55 s; 8,000 1.6 s; 16,000 7.7 s. Isolated `remove-duplicates` over 16,000 strings 6.7 s; a hash-set check 0.002 s; `get-record-shape` itself 0.003 s. Thousands of literal keys are unusual, but generated rule text is not.
- Fix sketch: `get-record-shape` already builds an `equal` hash table of key->index; detect duplicates in that pass, or use a local `equal` hash table in `prepare-record-shape`.

### LISP-P15 [impact: low-medium] [measured] SQL: dialect lookups are unindexed and re-derive the inheritance chain every call
- Found by: s6-P4, s7-P5.
- Where: `map.lisp:58-60` (assoc over the dialect table), :89-101 (`dialect-chain` allocates and does `member` string compares each call), :108-119 (`dialect-lexical`), :183-205 (`dialect-entry`); used by `apply-entry`, `emit-fill`, `emit-text-literal`, `lex-text`.
- Evidence: a typical 20-node rule translates in 0.22 ms; sb-sprof shows `string=*`/`%assoc-test`/`%member-test`/`dialect-chain` about 45% and `push-str`/`concatenate` (emit-fill builds every template char by char via `(concatenate 'string ...)`) about 18%. 1M lookups: hit near the front of funcs 0.63 s; a MISS (unsupported function, walked by `contains-unsupported-sql-p` for every call node of every custom pair) 5.9 s (about 6 us each); lexical lookup 0.75 to 1.3 s per million. Negligible per translation, noticeable only in fall-through planning over big RECORDs.
- Fix sketch: cache a flattened per-(dialect, section) hash table (invalidate in `define-dialect`/`define-entry`/`map-reset`, with a generation counter), and pre-parse each template into a part vector once.

### LISP-P16 [impact: low-medium] [measured] SQL stage 1 is O(n^2) in the number of statements
- Found by: s6-P5.
- Where: `stage1.lisp:297-364` (`defs` is an alist: `assoc` + `(append defs (list ...))` per statement, `assoc` per variable read in `substitute-node`), plus `is-constant` re-walking the growing value.
- Evidence: chain `A0 = X; A1 = A0 + 1; ...; An > 0`: n=4,000 1.4 s, 8,000 5.3 s, 16,000 about 21 s (all refused at the end with E_SQL_DEPTH). A 350 KB script costs 20 s before any refusal.
- Fix sketch: hash table for `defs`, and stop early on the E_SQL_DEPTH condition (already deeper than 200 by statement 200).

### LISP-P17 [impact: low-medium] [measured] Every regex pattern compiles two scanners, only RREPLACE uses the second
- Found by: s4-P2.
- Where: `regex.lisp:288-303` (`(cons head (build nil))`), :318-320 (RMATCH/RFIND/RGROUPS discard the tail scanner).
- Evidence: 5,000 unique patterns: `compile-regex` 128 ms vs 58 ms for one scanner (about 25 us vs 12 us); about 1 KB retained per cache entry. Irrelevant for fixed literal patterns; matters for patterns built from data.
- Fix sketch: build the tail scanner lazily on the first RREPLACE continuation; keep the validation on the head build. (Cache boundedness is a separate matter, see C12.)

### LISP-P18 [impact: medium (s4) / low (s1)] [measured] `bytes-to-hex` formats each byte with `format`
- Found by: s1-P5, s4-P1.
- Where: `utf8.lisp:86-89` (used by TO_HEX, `binary.lisp:19-20`).
- Evidence: 0.48 s per MB (s1); 2 M bytes: 428 ms vs 28 ms with a 16-char lookup table filling a preallocated string (s4, 15x). encode-utf8 of the same 2 M ASCII chars: 27 ms, decode 56 ms, CRC32 32 ms, so hex is the outlier in the binary group. `format ~2,'0x` per byte through a string stream, then `string-downcase` over the whole result.
- Fix sketch: fill a preallocated `(make-string (* 2 n))` from `"0123456789abcdef"`; output is already lower-case so `string-downcase` (the wrong tool anyway) goes away.

### LISP-P19 [impact: low-medium] [measured for the zero-stripping; reasoned for the rest] Join-key canonicalisation does redundant work
- Found by: s5-P4.
- Where: `structure.lisp:222-240` (`canonical-numeric-string`), :252-271 (`extract-join-key`, calls it twice at :257), :1310 (`(reverse matches)` per left row), :1229 (`format nil "~d"`).
- What: (1) a non-integer numeric text key is parsed twice; (2) trailing-zero stripping is `(floor digits 10)` in a loop, O(z) bignum divisions, quadratic in the number of trailing fraction zeros: isolated, 20,000 zeros 0.8 s, 50,000 4.7 s, 100,000 20 s; a two-row equi LINK on keys with 100,000 fraction zeros took 75 s in Lisp (36 s JS, 0.6 s PHP), about 27% in this loop and the rest in generic decimal parse/format (see P2); (3) each left row does `(reverse matches)` though buckets could be reversed once after the build; (4) `emit` formats the position with `format` per joined row when `numbered`.
- Fix sketch: bind the canonical string once; strip trailing zeros by string (or divide by 10^k in chunks); `nreverse` buckets after the build phase.

### LISP-P20 [impact: low] [measured] Per-element `(format nil "~d" i)` for list keys
- Found by: s2-P5, s3-P4, s5-P5.
- Where: `value.lisp:110, :299-300, :572-577` (`value-hash`), :718; `eval.lisp:310-311` (`eval-list`); aggregate walk for rows beyond 10,000.
- Evidence: `(format nil "~d" 12345)` 133 ns; `value-hash` of a 1000-int list 154 us, `value-keys` 106 us, `value-eql` 65 us, `value-copy` 56 us per 1000 elements. `aggregate.lisp` keeps `*index-string-cache*` (1..10000) and `format-index-string`, but `eval.lisp` loads first and does not use it. A prototype using the cache: a 6-item comma list inside a 100k-row MAP 444 -> 399 ms (about 10%, noisy). DEDUPE of 50k two-element lists takes 0.1 s, so it is not a bottleneck today. Profile with 200k rows: `%OUTPUT-INTEGER-IN-BASE` 4.8% + `GET-OUTPUT-STREAM-STRING` 1.2%.
- Fix sketch: move the cache into `value.lisp` (or `eval.lisp`) so both use it; for `value-hash` hash the integer directly (the mix must match the children-based branch, which hashes the key string, since equal lists in the two representations must hash equal).

### LISP-P21 [impact: low] [measured] REPEAT / PADL / PADR build strings one write at a time
- Found by: s4-P3.
- Where: `text.lisp:135-136, 147-149`.
- Evidence: `with-output-to-string` + `dotimes` writes with a final copy out of the stream, so a large result needs about 2x memory (drives the C20 heap exhaustion). REPEAT 5 M chars 287 ms; 50 M chars about 5 s (js 3 s, cpp 0.8 s); PADL 5 M 179 ms.
- Fix sketch: `(make-string (* n len))` then `replace` per copy (or doubling `replace`); PAD fills directly into a preallocated result.

### LISP-P22 [impact: low] [measured] `match-operator` scans all 33 operator strings with `string=` for every operator token
- Found by: s1-P4.
- Where: `lexer.lisp:140-145`.
- Evidence: tokenizing a 204 KB / 66k-token program takes 0.074 s. A first-character guard before the `string=` loop cut tokenize from 0.219 s to 0.119 s (-45%, 5 passes). Tiny rules (34 chars, `compile-source` about 13 us) spend about 7 us in the lexer.
- Fix sketch: `case` on the first character to pick candidates. Longest-match order must stay (`???` before `??`, `$<=` before `$<`).

### LISP-P23 [impact: low] [measured] `evaluate` (compile + run once) pays the full optimizer for nothing
- Found by: s3-P3.
- Where: `sel.lisp:48-49` and :26-32 (physical tree built on first `run`).
- Evidence: `IF(A > 1 AND B < 5, A * 2 + B, ROUND(A / 3, 2))`: parse 29.9 us, optimize 7.5 us, run 2.3 us (plain tree also 2.3 us); `evaluate` 35.4 us vs parse + plain-run 25.9 us (+37%). The struct comment already says so.
- Fix sketch: have `evaluate` run the un-optimised AST (same results modulo C9/C13 to C15), or optimise on the second run of a program.

### LISP-P24 [impact: low] [measured] SQL: `emit-text-literal` pays per-call sorting and per-character rule search
- Found by: s7-P4.
- Where: `emit.lisp:85-108`.
- Evidence: 100k literals of 44 chars: 1.1 to 1.2 s (about 11 us each; a 1 MB literal 0.24 s, so per-call overhead plus about 250 ns/char). Each call does two dialect-lexical chain walks, `copy-list` + `stable-sort` of the rules, and a `find-if` with a fresh closure per character.
- Fix sketch: cache the sorted rule list per (dialect, overlay generation); for shipped dialects scan with a small char->replacement table (rules are single-character).

### LISP-P25 [impact: low] [measured] Hybrid helper-assignment bookkeeping is super-quadratic in the number of non-literal helpers
- Found by: s7-P3.
- Where: `hybrid.lisp:200-236` (`read-names` recomputed on every pass of `referenced-assignments`; `wrap` called once per candidate split k).
- Evidence: chain of n non-literal helpers `H_i = H_{i-1} + COUNT(ORDERS)` before a 3-step pipeline: n=25 9 ms, 50 42 ms, 100 261 ms, 200 799 ms. Literal helpers cheap (n=100: 9 ms).
- Fix sketch: compute `read-names` once per statement and close the dependency set in a single reverse pass.

### LISP-P26 [impact: low] [measured] Shape-cache key hashing: `sxhash` of a list of strings looks at only the first few elements
- Found by: s1-P6.
- Where: `value.lisp:36, :42` (`(gethash keys *shape-cache*)`, `equal` table keyed on a list).
- Evidence: 200 key lists sharing a 6-key prefix all had the same `sxhash`, so they collide in one bucket and each lookup compares up to 256 lists with `equal` (cache capped at 256 entries / 256 keys). Only matters for many similar-prefix shapes; unmeasured end to end.
- Fix sketch: key the cache on a length-prefixed string join of the keys, or a hand-rolled hash of all keys.

### LISP-P27 [impact: low] [mostly reasoned or guessed] Smaller items
- `registry-lookup` upcases (allocates) on every call node: `registry.lisp:85-86`, called from `parser.lisp:370, :460` with a token value the lexer already upper-cased (s1-P7, guessed, not measured). Fix: `registry-lookup-canonical`.
- `looks-numeric` (`value.lisp:428-437`, ISNUM) parses and discards the decimal; `ISNUM(x) AND x > 5` parses x twice (1327 ns vs 817 ns for `a*b+c/3-2`). Cache into `value-dec-val` after C1 (s2-P6, reasoned).
- `value-copy-at` copies BIN octets on every assignment/`,` (`value.lisp:461, :486`); a BIN cannot be mutated in place, so this is O(len) per copy for nothing (s2-P6, reasoned).
- `dec-div` (`decimal.lisp:373`) always builds a bignum numerator: 88 to 110 ns for ordinary operands; a fixnum fast path would roughly halve it. `dec-add/mul/cmp` are 24 to 62 ns already; declaring `int-val` as `(signed-byte 62)` would give < 2x (s2-P6, reasoned).
- `make-args` allocates a 7-slot struct and a cache vector per strict call (`eval.lisp:61-72`); `eval-dispatch :num` and `:text` allocate a fresh value per evaluation (:262-263). Sharing an immutable literal value was not proven safe (values are mutable handles). Prior lisp-runtime work already covered `args` (s3-P5, reasoned).
- `ascii-case` (`text.lisp:113-120`) uses `map 'string` with a closure (UPPER of 5 M chars 331 ms) and allocates even when nothing changes; `trim-text` uses `member` on a list per char; `FIND/REPLACE/SPLIT` use generic `search` (5000-vs-20000-char worst case 1.1 s, O(nm)); `BTL`/`LTB` allocate one value per byte (1 M bytes 444 / 617 ms) (s4-P5, measured, not hot enough to matter). Cached RMATCH call overhead is about 1 us (200,000 calls 194 ms) and fine.
- cl-ppcre backtracking is about 5x slower than V8 on quadratic patterns: `RMATCH('a*[b]', REPEAT('a', 40000))` lisp 11.3 s, js 2.2 s, cpp 4 ms (SRELL prefilters); n=20000: 2.9 s / 0.6 s / 4 ms. Not cheaply fixable; a first-set/required-class precheck for patterns starting with `x*` would help (cl-ppcre only checks required literal strings, which is why `a*b` is fast and `a*[b]` is not) (s4-P4).
- SQL: `translate-in` branch D and `translate-conditional` use `nth` in loops (`translator.lisp:1246-1258, 2128-2148`); a 3,000-element IN also yields 3,000-deep parenthesised OR nesting that servers with a parser stack limit (MariaDB about thousands) may reject; worth a limit or a flat `OR` chain if bytes may change (s6-P6, reasoned).

## Cross-host findings

These need a spec decision or a change in all hosts (CLAUDE.md "The one rule": spec first, then conformance cases, then every host, then `tools/check.sh`). Do not fix them in Lisp alone. Status is as reported by the reviewers; hosts named were actually run by them.

| Lisp id | Issue | Other hosts | Action |
|---|---|---|---|
| C4 | Nested interpolation: uncounted recursion | cpp and php answer E_DEPTH 1:101 (php quadratic); js and python crash | Add `lim.interp-nesting-depth`; make every host count interpolation nesting |
| C21 | `??` / `???` chain uncounted | cpp segfault at 20,000; js/python about 5,000; php OOM at 100,000 | Spec: add `??` to the §6.4 cost list (or iterative parse) |
| C13 | Math-plan coerces operands at load time | all five hosts print the same as optimised Lisp | Spec §5.1/§6.2/§7.1: pin when an operand is coerced, then fix all five plans |
| C14 | `cannot-raise-p` over-approximation (FILTER fusion; logical field reads) | js, php, python print the same as Lisp for (a) | Change the shared rewrite rules in all hosts |
| C15 | SORT+TAKE -> TOP fusion reorders count evaluation | all hosts agree with each other (optimised) | Restrict fusion to literal non-negative TAKE counts everywhere |
| C17 | SORT is not a total order for mixed numeric/non-numeric text | all hosts differ from each other | Spec: define a total order first |
| C18 | Regex empty-iteration / capture-reset semantics | Perl family (lisp, py, php) vs ECMAScript (js, cpp) | Spec §7.8: reject or pin |
| C19 | No regex backtracking budget | php returns FALSE, cpp aborts, js/py/lisp run on | Spec: budget + new error code |
| C20 | No text-length cap | js RangeError (uncaught) | Spec: cap + E_RANGE |
| C16 | Equi-join key classification ignores `;`/`,`/`=` | js shares the `,` case; python, php, cpp correct | Fix js and lisp; add cases |
| C33 | Assignment nests past 200 | js, php, cpp, python identical | Fix in all hosts; add `lim.assign-depth-target-plus-value` |
| C35 | Aliasing observable through the source variable | contributing.md claim is wrong on all hosts; hosts also disagree on SORT_BY and bare BUCKET copies | Amend §3.4 or copy in all hosts |
| C6, C7 | SQL: filtered JOIN drops FILTER; binder leakage | JS emits identical wrong SQL | Fix all translators; add cases |
| C23 | SQL: exponential substitution | JS same (n=18: 21 MB, 9.5 s) | New budget limit in `spec/limits.json` |
| C24, C25 | SQL: UNKNOWN laundering; IN on exact text column | JS identical | Kind rules in `sql-kinds.md` §4 |
| C26 | SQL: SORT then LINK derived-table wrapping | Lisp differs from JS and Python (direction unclear) | Decide, document, align |
| C39, C40 | SQL: unbounded LIMIT counts; orphan param slots | JS also diverges on C39; JS same on C40 | Shared limit; fix params bookkeeping |
| C8, C28, C29 | Hybrid planner defects | Python reproduces C8, C28 and plans C29 the same | Fix in every host that has a planner |
| C46 | Binding builtins never take the `.>` placeholder | no divergence today | One sentence in §5.10 plus a case |
| C31 | Invalid UTF-8 via CLI | cpp/php E_UTF8; js substitutes U+FFFD | Align CLIs |
| C9 (s5 note) | `BUCKET(5,_)` result differs in cpp | cpp differs from js, php, python, lisp | Cross-check cpp |

Regex divergences seen on the way (s4, not Lisp bugs; recorded for the per-host reports):
- X1: RREPLACE with a pattern that can match empty AND non-empty at the same position: cpp and php retry a non-empty match at the same offset after an empty one (PCRE `NOTEMPTY_ATSTART` style); lisp/js/python advance one character. `RREPLACE('b*?','<$0>',"abc")`: lisp/js/py `<>a<>b<>c<>`, cpp/php `<>a<><b><>c<>`; `RREPLACE('a||b','<$0>',"ab")`: lisp/js/py `<a><>b<>`, cpp/php `<a><><b><>`. Only `re.replace.zero-width` (`x*`) is in the suite.
- X2: regex group nesting depth: `RMATCH(REPEAT('(?:',1000) & 'a' & REPEAT(')',1000),'a')` lisp/js TRUE, cpp E_REGEX_SYNTAX (error_complexity), php E_REGEX_SYNTAX, python uncaught RecursionError at 1000 and 50,000. `^(a){65535}$` on 65,535 `a`: js/py/cpp TRUE, php E_REGEX_SYNTAX (PCRE link-size), lisp crashes (C3).
- X3: cpp rejects `(?:^)+` (E_REGEX_SYNTAX) that js/py/php/lisp accept.
- X4: PHP `RMATCH('^(?:a|b)*$', REPEAT('a', 2000000))` -> FALSE (PCRE backtrack/JIT limit); all others TRUE.

Lisp-only divergences (fix in Lisp; other hosts are the reference or unaffected): C1, C2, C3 (cl-ppcre stack), C5, C9, C10, C11, C12 (threading; Go has a test, others single-threaded by nature), C22, C31 (Lisp part), C32, C34, C36 (needs an application-registered dialect; the JS translator was not checked), C37, C38, C41 to C43, C45.

## Suggested conformance cases

`Expr` shows the intent; where a case needs a large input, use `REPEAT(...)` so the `.selt` stays small. `.sqlt` and plan/unit rows are marked.

| Name idea | Expression | Expected |
|---|---|---|
| `hash.bucket.warm-decimal-cache` (LISP-C1) | `X = "5"; Y = X * 1; JOIN(BUCKET(LIST(X, "5", 5), _, COUNT(_)), ",")` | `3` |
| `hash.bucket.warm-decimal-cache-01` | `X = "01"; Y = X + 0; JOIN(BUCKET(LIST(X, "01", "1"), _, COUNT(_)), ",")` | `2,1` |
| `rel.bucket.warm-cache-record-key` | `R = LIST(RECORD("k","1"),RECORD("k","1"),RECORD("k","2")); x = ANY(R, _["k"] > 0); BUCKET(R, _["k"], COUNT(_))` | `{1=2, 2=1}`; two-argument twin keeps all rows |
| `record.wide-record-new-key-isolated` (C2) | `R = RECORD(<17 pairs>); R["Z"] = 5; S = RECORD(<same 17 pairs>); HAS(S, "Z")` | `FALSE`; `S["Z"]` => `E_NO_KEY` |
| `re.stack.long-subject-alternation` (C3) | `RMATCH('^(?:ab|a)*$', REPEAT('a', 100000))` | agreed result (TRUE) or agreed error code; never a host crash |
| `re.stack.nullable-lazy-loop` | `RMATCH('(?:a(?:x*)*?)*[!]', "aa")` | `FALSE` |
| `lim.interp-nesting-depth` (C4) | about 150 nested `"{...}"` around `1` | `E_DEPTH` at the parser position, same in every host |
| `top.n-beyond-list-size` (C5) | `LIST(3,1,2) .> TOP(1000000000000000000000000000000) .> COUNT()` | `3`; `TOP_BY` twin also `3` |
| `agg.join.filtered-source-is-refused` (C6, `.sqlt`) | `JOIN(FILTER(("a","b","c"), _ $== "a"), ",")` | `E_SQL_SHAPE` |
| `agg.filter.binder-does-not-leak-into-body` (C7, `.sqlt`) | `ANY(FILTER(V, x, x > 1), q, q > X)` with outer binding `X` | SQL compares `q` to outer column `X`, not the element |
| `plan.fallthrough.custom-pair-reading-key-splits-before-the-map` (C8, plan) | `ORDERS .> MAP(RECORD("a", _["amount"], "x", JOIN(LIST(_["name"], _K), "-"))) .> DROP(1)` | plan splits before the MAP (or refuses fall-through) |
| `sort.scalar-is-one-element-list` (C9) | `SORT("s")`, `SORT_BY(5,_)`, `TOP(5,3)` | one-child tree `{1="s"}` etc. |
| `rel.filter.pushdown-sort-scalar-source` | `"s" .> SORT .> FILTER(TRUE) .> MAP(_)` | equals the same pipeline with plain FILTER |
| `re.anchor.end-then-dotstar` (C10) | `RFIND('$.*', "abc")`; `RREPLACE('$.*', '!', "abc")` | `4`; `abc!` |
| `re.reject.quantified-anchor` (C11) | `RMATCH('^*a', "a")`, `'$?'`, `'a$+'`, `'^{2}a'` (raw literals) | `E_REGEX_SYNTAX` |
| `math.order.left-coercion-after-right-eval` (C13) | `MAX(TRUE, U)`; `R = RECORD("a","q"); R["a"] + R["b"]` | `E_UNDEF_VAR` at col 10 / `E_NO_KEY` (spec decides); same as tree evaluation |
| `rel.filter.fuse.non-boolean-second-predicate-keeps-first-error` (C14) | `LIST(1,5) .> FILTER(IF(_ == 5, 1 == (1/0), TRUE)) .> FILTER(3)` | `E_DIV_ZERO` at col 39 |
| `plan.optimizer.sort-key-missing-field-blocks-filter-swap` (C14b, plan) | `ORDERS .> MAP(RECORD("n", _["name"])) .> SORT_BY(_["r"]) .> FILTER(FALSE) .> TAKE(1)` | `E_NO_KEY` |
| `rel.sort-take.count-evaluated-after-keys` (C15) | `C=0; LIST(3,1,2) .> SORT_BY(_ + (C=C+1)) .> TAKE(C)` | the TAKE(3) result `{1=1, 2=3, 3=2}` |
| `rel.sort-take.negative-count-after-key-error` | `LIST(3,"a",2) .> SORT_BY(_ + 0) .> TAKE(-1)` | `E_NOT_NUM` @26 |
| `rel.link.equi-key-with-sequence-reading-other-binder` (C16) | `R = LIST(RECORD("id",1)); S = LIST(RECORD("id",2), RECORD("id",3)); COUNT(LINK(R, S, A, B, (A["id"]; B["id"]) == B["id"]))` | `2`; and the `,` twin => `1` |
| `sort.mixed-numeric-text-total-order` (C17, after spec) | `LIST("10","9","1a") .> SORT() .> JOIN(",")` and permutations | one agreed order; `TOP(L,n)` equals `TAKE(SORT(L),n)` |
| `re.groups.nullable-loop` (C18, after spec) | `RGROUPS('(a|)+', "a")` | agreed value or `E_REGEX_SYNTAX` |
| `re.groups.capture-reset-per-iteration` | `RGROUPS('(?:(a)|b)*', "ab")` | agreed value |
| `re.budget.catastrophic-backtracking` (C19, after spec) | `RMATCH('^(a|aa)+$', REPEAT('a',40) & 'b')` | agreed error code or result, fast |
| `text.size.repeat-cap` (C20, after spec) | `LEN(REPEAT("a", 150000000))` | `E_RANGE` (or the spec's cap) |
| `lim.coalesce-chain-300` / `-30000` (C21) | `1 ?? 1 ?? ... ?? 1` | `E_DEPTH` or the agreed answer |
| `unit: dec-format-print-radix` (C22, lisp unit) | `(let ((*print-radix* t)) (value-scalar (make-num "1.50")))` | `"1.50"` |
| `limit: sql.substitution-doubling` (C23, `.sqlt`) | 30-statement `A(n) = A(n-1) + A(n-1)` chain | refuses quickly |
| `warrant.numeric.coalesce-over-an-undeclared-column` (C24, `.sqlt`) | `COALESCE(X, 0) > 5` with undeclared `X` | numeric guard applied |
| `op.in.exact-column-against-numeric-literals` (C25, `.sqlt`) | `X IN (1, 2)`, exact TEXT column | operand cast, not bare `x = 1` |
| `link.after-sort-order-by-placement` (C26, `.sqlt`) | `O .> SORT(_["AMT"]) .> LINK(C, _["CID"] == C["ID"])` | one agreed shape in all hosts |
| `plan.split.filter-then-key-reader-is-not-a-split-point` (C27, plan) | `ORDERS .> FILTER(_["amount"] > 4) .> MAP(RECORD("x", _K))` | split earlier / no key loss |
| `plan.helpers.literal-named-like-an-explicit-binder-is-not-inlined` (C28, plan) | `R = 5; ORDERS .> SORT_BY(R, R["id"], "DESC") .> TAKE(2)` | still `pure_sql` |
| `plan.split.before-link-keeps-left-name` (C29, plan) | `ORDERS .> TAKE(4) .> LINK(CUSTOMERS, _1["customer_id"] == _2["cid"]) .> FILTER(_["ORDERS"]["id"] > 1)` | continuation and `run` agree |
| `unit: fallthrough.raising-pair-beyond-take-window` (C30) | `ORDERS .> MAP(RECORD("a", _["amount"], "x", IF(_["id"] == 4, ABORT("boom"), 1))) .> TAKE(2)` | equals `run` (E_ABORT) or documented deviation |
| `lim.assign-depth-target-plus-value` (C33) | `A[1]x150 = 1; B[1]x150 = A; 7` | `E_DEPTH` at the assignment target |
| `register: text-escape-must-cover-quote` (C36, `.sqlt --- register`) | dialect with `textEscape` NIL / `()` / no quote entry | registration refused |
| `re.flag.non-ascii-case-variant` (C37) | `RMATCH("a","A","\u{130}")` | `E_BAD_ARG` |
| `stmt.take.count-beyond-int64` (C39, `.sqlt`) | `R .> TAKE(99999999999999999999999)` | clamped or refused, same in all hosts |
| `params: discarded-validation-literals` (C40, `.sqlt --- mode params`) | `O .> FILTER(_["AMT"] > 3) .> SORT(_["AMT"]) .> LINK(C, _["CID"] == C["ID"])` | `fragment-params` length equals slots referenced |
| `pipe.placeholder.binding-fn-untouched` (C46) | `_` inside a binding function's args with enough args | `_` stays the element binder |

## Suggested fix order

Tier 1: silent wrong results in plain in-memory Lisp, small localised changes (fix in Lisp; add the cases first):
1. LISP-C1: hash TEXT bucket keys by spelling only. One function; unblocks P5, the ISNUM cache.
2. LISP-C2: stop aliasing the shared `key-map` in `ensure-shaped-children`.
3. LISP-C9: SORT/TOP of a scalar through `aggregate-elements`.
4. LISP-C5: clamp the `TOP` heap capacity to the element count.
5. LISP-C10, C11: regex `$.*` lowering and quantified-anchor rejection.
6. LISP-P1: replace the two `(length acc)` calls in `emit-parts` (one-line change, 71 s -> 0.1 s).

Tier 2: robustness (needs a small spec decision on a catalogued code, then all hosts):
7. LISP-C4 and C21: count lexer interpolation nesting and `??` chains (`lim.*` cases first), with the single-pass lexer (P13).
8. LISP-C3 and C19: `storage-condition` handler around cl-ppcre plus a spec'd regex budget; C20 text-length cap.
9. LISP-C12: `:synchronized t` on `*shape-cache*` and `*pow10-cache*`, mutex on the registry and alias-plan cache, or document single-threaded.

Tier 3: optimizer and evaluator equivalence (spec-first, all hosts):
10. LISP-C13, C14, C15 (math-plan coercion order, `cannot-raise-p`, TOP fusion), then C16 (join key classification), C17 (SORT total order), C18 (regex nullable loops).

Tier 4: SQL layer and hybrid planner (all hosts with a translator/planner):
11. LISP-C6 (refuse filtered JOIN), C7 (per-predicate frames), C8/C27/C28/C29 (hybrid split guards: `_K`, key-retaining FILTER, binder positions, `_INPUT` left name), C24/C25 (kind unification and IN cast), C26 (decide derived-table shape).
12. LISP-C23: node/output budget (needs a `spec/limits.json` entry).

Tier 5: performance, in order of payoff per effort (fix C1 first for P5):
13. LISP-P3 (operator interning, 1.5 to 1.9x on FILTER) and P5 (`copy-node-shallow` keeps `dec-val`, needs C1).
14. LISP-P4 (decorated sort keys, about 5x on text sorts), P7 (BIN hash), P14 (RECORD duplicate check), P18 (hex table), P12 (counter and vectors in the SQL fragment).
15. LISP-P2 and P6 (decimal parsing/formatting; Karatsuba only if 10^6-digit numbers matter), P8/P9 (LINK unshaped rows, hoist aliasing), P10 (skip hybrid context copy), P11 (n-ary IN fold).
16. The remaining low items (P15 to P27) opportunistically.

Housekeeping (low): C22 (`:radix nil`), C31, C32, C34, C36/C42 (dialect registration hardening), C38, C40, C45; C33, C35, C46, C47 need spec or doc text first.

## Checked and found sound

Front end (s1)
- Lexer vs `spec/grammar.md`: whitespace is exactly space/tab/CR/LF; identifiers ASCII-only, case-insensitive, upcased by hand (`ascii-upcase`). No `digit-char-p`, `alpha-char-p`, `char-upcase` or `read-from-string` in the slice. `1.` is not a number, `1.5.5` is a syntax error at the second dot, no exponent or hex; longest-match operators hold (`$<=`, `???`, `.>`, `<=`).
- Escapes and interpolation: `\u{...}` 1 to 6 ASCII hex digits (Arabic-Indic rejected), E_RANGE above U+10FFFF and for D800-DFFF, E_ESCAPE for empty or 7 digits, E_UNTERMINATED when `}` missing, trailing backslash E_UNTERMINATED; `{}`, `{ }` and comment-only bodies are E_SYNTAX at the right offset; braces nest; unterminated `{` is E_UNTERMINATED at the `{`. About 190 hand-written lexer/parser cases: 0 divergences in code or line:col against js, cpp, py and php.
- Positions are code-point based; only LF breaks lines (CR, U+0085, U+2028, VT, FF do not; all hosts agree). `lexer-pos-at` is binary search. A lone surrogate is E_UTF8 from `make-lexer`.
- Parser: precedence climbing matches grammar.md; `NOT` at 7, unary minus at 16 with min-bp gates; non-associative comparisons; right-associative `??`/assignment; trailing `;`; call-argument flattening and `node-grouped`; `.>` placeholder and prepend rules; arity errors at the name token; E_BAD_ASSIGN positions. Depth counting: `with-depth` around `parse-sequence`, `parse-primary`, the index bracket, both prefix operators and assignment; 100,000-deep parens, `-` x 1,000,000 and `a[a[...` give E_DEPTH with the other hosts' columns and never the host stack; long left-associative chains are guarded by the evaluator and `dependencies`. `lisp/bin/conformance conformance/01-lexical.selt conformance/10-limits.selt`: 69 passed.
- Read-only literals are never destructively modified; `binding-form` copies before `push`; `nreverse` only on fresh lists.
- `utf8.lisp` `decode-utf8`/`encode-utf8`: 65,000 random byte strings agree with Python's strict decoder, round-trip holds; `bytes-compare` bytewise; encode 1M chars 25 to 100 ms, decode 40 to 175 ms.
- `errors.lisp` / `limits.lisp` / `package.lisp`: `sel-error` carries code, message, 1-based line/col, 0-based offset; `+max-depth+` defined from the generated file.

Numeric core and values (s2)
- Decimal arithmetic vs an independent Fraction oracle: + - * / % (exact-quotient minimal scale, half-away rounding, negative-zero results, dividend-signed `%`), ROUND/TRUNC/FLOOR/CEIL/CANON/POWER/MIN/MAX/ABS/SIGN/comparisons: 18,000 random cases, operand sizes 1 to 30 digits and scales 0 to 25 concentrated around the 60-bit/scale-18 fast-path boundary: no mismatch. About 100 cross-host edge expressions (`1/30000000000`, `-0 / 5`, `CEIL(-0.5)`, `POWER(0,0)`, `ROUND(2.5, 1.0)`, `"1e3"`, `"٣"`, `"1_0"`, `" 5"`): identical to JS/C++/Python.
- Fast-path invariants: `int-val` set only when |digits| < 2^60 and scale <= 18; the 2^60 sum edge stays inside fixnum; negative zero never constructed. No rationals or floats leak (except C45); no `digit-char-p`.
- Caps: E_RANGE for over-long numerals in `dec-parse` (integer and fractional digits), FALSE for ISNUM; `dec-guard` bound correct; `POWER`/`ROUND` argument caps and order E_NOT_INT -> negative E_RANGE -> cap E_RANGE agree between the builtin path and the plan executor.
- Math plan: copy-propagation rules keep scale and values and still coerce x; depth sound because `optimize-root` refuses trees that reach the cap; MIN/MAX fold picks the first of equal values; leaves evaluated in source order; conformance 02/10/23 pass (101/101 on the copy).
- `value.lisp`: list-key parsing rejects leading zeros/zero/more than 9 digits; shaped/list/children representations compare and hash consistently; `value-copy-at` copies dec cache and enforces the depth cap; `value-set` copies new keys and validates them; `from-native`/`make-*` boundary checks raise `E_BAD_ARG`/`E_RANGE` for floats/ratios/bad bytes/non-strings. Hash-table test choice (`equal` for strings, `eql` for integers) correct. Shape cache bounded to 256 entries / 256 keys / 16,384 chars.

Evaluator, registry, host API, optimizer (s3)
- Depth accounting: `eval-node` increments before dispatch and `unwind-protect`s the decrement (benchmarked away in-process: no measurable difference, not worth removing). No `storage-condition` escaped from nested MAP/FILTER/SORT_BY/TOP_BY/SUM/LINK/BUCKET/IF/COND/`??`/assignment templates at the deepest depth the parser accepts (MAP: 98 nestings; others 99 to 100). `collect-deps`, `exceeds-depth-p`, `compile-math-plan`, `optimize-tree` are depth-guarded; `optimize-root` returns over-deep trees untouched.
- Every `ctx-push-frame` site is inside `unwind-protect`. The only `handler-case` in the evaluator is the `??`/`???` one and catches `sel-error` only. The `(error () node)` handlers in `fold-node` are catch-all but only around decimal-core calls whose failure means "leave it for the evaluator".
- Assignment: path-resolve-then-store, target index expressions evaluated once left to right, compound assignment reads its target before the RHS, re-derivation after the RHS; 31 tricky programs identical across js, python and lisp.
- Constant folding: about 20k random constant-heavy expressions agree between optimised and plain on value and error position. Math-plan values equal plain evaluation (only error ordering differs, C13). Exhaustive 3-step pipeline search (about 270k programs) found no other divergence beyond C9/C13/C14/C15; the fix-point loop terminates and is cheap.
- Registry: `define-builtin` refuses manifest disagreement, duplicates and its own arity rule; `assert-builtin-manifest-covered` runs at load; `register-function` guards names, reserved words, builtins, arity and callable-ness; `registry-lookup`'s `string-upcase` cannot see non-ASCII names.
- No CLOS/generic-function dispatch in the eval loop; `dependencies` is capped at the evaluator depth and iterative over assignment-target chains. `program-physical-ast` publishes the tree then its key in that order (safe on x86-64).

Text, regex, binary, null, control (s4)
- UPPER/LOWER are genuinely ASCII-only (`UPPER("ǆßÿŉ")`, `LOWER("İẞÀ")`, `UPPER("ſ")`, `LOWER("K")` unchanged); code point vs octet handling agrees for LEN/LEFT/RIGHT/SUBSTR/FIND/BACKWARDS/PADL/PADR/REPEAT/CODE/CHAR on non-BMP and boundary code points; TRIM strips exactly space/tab/CR/LF; error precedence matches the other hosts.
- Regex rewrite: `\d \w \s` become ASCII classes (also inside classes); 400 random classes x about 900 code points (U+0661 and the four folding points) with and without `i`: zero diffs across js/py/cpp; `^`/`$` lowering to `\A`/`\z` works (`RMATCH('^a$',"a\n")` FALSE); RREPLACE's tail scanner matches js/py; K/ſ subject folding keeps offsets; U+0130/U+0131/U+212B do not fold; reversed `{2,1}` and `{65536}` rejected; POSIX classes, `\b`, `\v`, backreferences, lookaround, possessive, atomic, named groups, inline modifiers, `\A\z`, empty class and bare `}`/`]` are rejected with E_REGEX_SYNTAX at the same column; flag errors and argument-evaluation order identical; replacement splicing (`$0-$9`, `$$`, literal `\1`, `$&`, non-ASCII digits) identical. About 14,000 generated programs: Lisp agrees with Python on all of them except C10 and C11-type cases.
- RREPLACE zero-width advance steps a whole code point; `from > n` terminates; 1 M matches in 385 ms, linear.
- Binary: FROM_HEX, DECODE_BASE64 (padding, `Q===`, `====`, trailing-bit leniency), ENCODE_BASE64, CRC32 (`123456789` -> cbf43926), BTL/LTB, BLEN/TO_UTF8/FROM_UTF8 (overlong C0AF, surrogate EDB080, F4908080, truncated E282 -> E_UTF8; BOM kept) match js/py/cpp/php on code and column. Null/control: COALESCE, GET, PATH, IS_NULL, IS_BLANK/IS_PRESENT, IF/COND laziness, ABORT: 23 probes identical across five hosts. No recursion in those five files.

Structure/aggregate builtins (s5)
- `DEDUPE`/`DISTINCT` keep first occurrence in stable order (50k records 0.08 s), agree with js/py/cpp/php. `SORT`/`SORT_BY`/`SORT_DESC` use `stable-sort` on a freshly consed list of `sort-item` structs with the original index as tie-breaker, never on the input; `TOP*` matched `SORT..TAKE` on a 120-row tie-heavy fuzz for n in {0,1,2,3,5,8,13,40,119,120,121} ASC and DESC (Lisp and JS).
- `aggregate-walk` is memory-safe when the body mutates the source (loop bound and vector captured once); `_K` frames allocated only when needed. FILTER preserves keys; the join pre-filter hand-off protocol looks consistent; LINK/LINK_LEFT edge cases agree across js/py/cpp/php. Join keys strip trailing zeros and fold -0; the plain-integer shortcut is an ASCII check; `$==` uses byte-exact keys.
- The `ensure-row-table-alias` plan cache is bounded and case-insensitive via `ascii-upcase`. `SELECT_COLS`, `TAKE`, `DROP`, `COUNT`, `INDEXES`, `HAS`, `JOIN`, `LIST`, `RECORD` (duplicate keys keep first position, last value) agree on edge inputs. No `sort`/`nreverse`/`delete`/`nconc` is applied to a list that can alias user-visible storage. Recursion in the slice is bounded by the parser's 200 cap or delegated to depth-counting functions.

SQL translator and rendering (s6, s7)
- Identifier quoting doubles the quote via `identEscape`; binding names checked for empty/NUL; hostile names `x"y` / `` t`" `` quoted correctly in mariadb, postgresql and sqlite. Shipped dialects escape `'` (and `\` for the MySQL family) in single-pass longest-match; TEXT literals are never concatenated into SQL in params mode; NUM/BOOL/BIN come from recovered canonical forms (`numeric-literal`), so `"1 OR 1=1"` cannot become a number. `~D` instead of `~a` for numbers (immune to a rebound `*print-base*`).
- Depth accounting in `walk-node`/`substitute-node` agrees with the evaluator at 196 to 201. Whole-expression refusal holds (`as-value`/`as-condition` refuse before any characters are produced). `validate-constant` refuses `FALSE AND (1/0>0)`-class programs. A 14k-translation random fuzz and a 4,500-translation typed differential against JS matched byte for byte (kinds, params-mode SQL, refusal codes); 1,500 random pipelines x 2 dialects identical except the SORT/TOP -> LINK family (C26). `lisp/bin/sqlt plan` passes (203 passed).
- Statement compiler LIMIT/OFFSET forms for MySQL family, SQLite and PostgreSQL are right; derived-table aliases are per-translation (`*subquery-counter*` is dynamically bound: deterministic and thread-safe). `merge-slots`, `fill-named`, `frame-set` (write order = evaluator's `_K` precedence), `ret-kind` `@unify:` parsing consistent with the docs apart from C24. The `:sargable` bare `=` in postgresql/sqlite/ansi is documented behaviour.
- `emit-fill`: `{{`/`}}`, absolute slots, out-of-range slot refusal, lexical-reference cycle refusal, `binaryCast` skipped for BIN operands; `replace-all` is a plain subsequence replace. `dialect-chain` is cycle-safe; `dialect-lexical` respects NIL withdrawal; `dialect-entry` checks the whole overlay chain first. `plan-hybrid` classification fills every slot; refused stage-1 programs become pure_memory; open/sealed buckets and unprojected joins are not split points. About 15,000 single-relation random pipelines: the only divergences were C8, C14b (s7-C6), C27, C30. The latest-member strategy uses constant control strings and identifiers quoted by `emit-ident` (no directive injection); read but not executed. `relational-plan.lisp` is data only. `execute-hybrid` hands runners `:params` SQL plus `(bindings frag)`.

## Reviewer caveats (what was not covered)

- No live database run anywhere; SQL semantic drift (collation, NULL, sort stability) is unchecked, and hybrid was checked against an in-memory model. The "latest member" strategy was read only.
- Not reviewed: `hybrid.lisp` beyond slice 7's read, `map-data.lisp` (spot-check only), the join pre-filter proofs (`join-stage-walk`, `join-keys-safe-p`, `join-totality-p`; only edge cases run), `tools/join-rows-oracle`, thread safety of the regex cache (reasoned, not tested), Go host (its `go/bin/sel` is a source directory), PHP/C++ only spot-checked in most slices.
- Timings were taken on a shared 8-core box; huge-number timings vary about 2x with load, ratios are what matter.
- Synthesizer re-verification (read-only `lisp/bin/sel` / `node js/bin/sel.mjs` runs, 2026-09-29) covered: C1, C2, C3 (2 of 4 triggers), C4, C5 (TYPE-ERROR variant), C9, C10, C11, C13, C15 (optimised output), C16. All reproduced as described; no claim needed correction. The remaining findings are propagated as the reviewers reported them.
