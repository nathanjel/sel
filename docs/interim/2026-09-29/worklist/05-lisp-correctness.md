# LISP correctness

[Worklist](README.md) · [Shared tests](00-tests.md) · [Performance](07-performance.md)

47 findings; 31 implementation tasks. Report severity/confidence is preserved below. Work through one source area at a time; a grouped checkbox closes only when every listed finding is resolved.

Each task starts by reproducing the report against the current tree, then lands the linked shared regression cases before the fix. Fix sketches are starting points, not approved spec changes. Prefer one implementation source per task; touching spec, fixtures or an unavoidable API boundary is allowed. Do not edit generated artifacts without their generator. High-severity tasks take precedence within each area.

## Front end, depth and source positions

<a id="c-lisp-c4"></a>

- [x] **C-LISP-C4 — Resolve LISP-C4.** Nested interpolation: uncounted lexer recursion exhausts the control stack, and the lexer is quadratic in nesting

  **[LISP-C4](../lisp-code-review.md) — [high] [confirmed, re-verified by synthesizer] Nested interpolation: uncounted lexer recursion exhausts the control stack, and the lexer is quadratic in nesting**

  Source target: `lisp/src/lexer.lisp:168-194` (`lex-quoted`), :233-251 (`match-brace`), :253-264 (`skip-quoted`), :281-299 (`emit-parts`). Cycles: `lex-quoted -> match-brace -> skip-quoted -> match-brace`, and `emit-parts -> lex-range -> lex-quoted -> emit-parts`.

  Implementation starting point: (a) lex interpolation single-pass: lex the inner expression directly and stop at the unmatched `}` instead of pre-scanning with `match-brace` and re-lexing; (b) make the nesting an explicit stack or count it; a lexer-side cap cannot raise E_DEPTH itself because the parser's position (col 101 here) is not the lexer's, so an explicit stack keeps the parser the only source of E_DEPTH; (c) backstop only: `handler-case` `storage-condition` in `compile-source` (unreliable in SBCL; a second exhaustion before the guard page is re-armed killed the harness process).

  Regression prerequisite: [T01 / LISP-C4](tests/01-frontend.md#lisp-c4).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — old-vs-new lexer differential 0 diffs per host (100k-300k random strings); 100k-level nest in <2s on every host

<a id="c-lisp-c21"></a>

- [x] **C-LISP-C21 — Resolve LISP-C21.** Right-associative `??` / `???` chains recurse in the parser without counting depth

  **[LISP-C21](../lisp-code-review.md) — [medium] [confirmed; cross-host spec gap] Right-associative `??` / `???` chains recurse in the parser without counting depth**

  Source target: `lisp/src/parser.lisp:253-268` (the `#\R` branch of `parse-term`; only the assignment sub-branch is wrapped in `with-depth`).

  Implementation starting point: needs a spec decision in all hosts: (1) count the `??` recursion (`with-depth` when the operator is consumed, as for prefix operators) and pin the boundary in `10-limits.selt`, or (2) parse the chain iteratively and fold from the right (the tree stays deep, so the evaluator's counted recursion still guards it; changes no boundary). The other deep-tree shapes (`+`, `AND`, `&`, `.>` at 100,000) compile and hit E_DEPTH in `dependencies`, not the stack.

  Regression prerequisite: [T01 / LISP-C21](tests/01-frontend.md#lisp-c21).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts enter()/leave() in the R-assoc branch; conformance 1120/1120 on js, js-bundle-min, php, cpp, lisp, go, python

<a id="c-lisp-c31"></a>

- [x] **C-LISP-C31 — Resolve LISP-C31.** CLI: invalid UTF-8 in a source file (or `-e`) is not E_UTF8

  **[LISP-C31](../lisp-code-review.md) — [low] [confirmed] CLI: invalid UTF-8 in a source file (or `-e`) is not E_UTF8**

  Source target: `lisp/bin/sel.lisp` `read-text-file` / `main`. The library entry `compile-source` is fine (validates surrogates in `make-lexer`, `lexer.lisp:45`).

  Implementation starting point: read the file as octets and pass them through the (correct, fuzz-verified) `decode-utf8`; for argv use a latin-1 external format for the runtime and re-decode strictly.

  Regression prerequisite: [T01 / LISP-C31](tests/01-frontend.md#lisp-c31).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — check-cli-source.sh passes on all six hosts

<a id="c-lisp-c46"></a>

- [x] **C-LISP-C46 — Resolve LISP-C46.** Spec does not state that binding builtins never take the `.>` placeholder rule

  **[LISP-C46](../lisp-code-review.md) — [low] [confirmed; spec text gap, no divergence] Spec does not state that binding builtins never take the `.>` placeholder rule**

  Source target: `parser.lisp:376` `(not (spec-binds spec))`; identical in js (`parser.mjs:326`), python, php, cpp; `spec/SPEC.md` §5.10 rule 3 and `grammar.md` are silent.

  Implementation starting point: one sentence in §5.10 rule 3 plus a case where `_` appears in a binding function's args with enough arguments that the arity clause alone would trigger substitution.

  Regression prerequisite: [T01 / LISP-C46](tests/01-frontend.md#lisp-c46).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — Go break removed; all six hosts agree

## Exact decimal arithmetic and limits

<a id="c-lisp-c22"></a>

- [x] **C-LISP-C22 — Resolve LISP-C22.** `dec-format` (every number rendered to text) is corrupted when the embedder binds `*print-radix*`

  **[LISP-C22](../lisp-code-review.md) — [medium] [confirmed] `dec-format` (every number rendered to text) is corrupted when the embedder binds `*print-radix*`**

  Source target: `decimal.lisp:167` (`(write-to-string (dec-digits d) :base 10)`; `:radix` not given, so it inherits `*print-radix*`; contrast :224 which passes `:radix nil`).

  Implementation starting point: `:radix nil` at :167 (or bind `*print-radix*`/`*print-base*` inside `dec-format`); better, a hand-written formatter (P6) has no such dependence.

  Regression prerequisite: [T02 / LISP-C22](tests/02-decimal.md#lisp-c22).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — lisp test 658 pass

<a id="c-lisp-c45"></a>

- [x] **C-LISP-C45 — Resolve LISP-C45.** `make-int` uses a double-float constant

  **[LISP-C45](../lisp-code-review.md) — [low] [confirmed, no observable effect] `make-int` uses a double-float constant**

  Source target: `value.lisp:207` (`(floor (* (1- +max-int-digits+) 3.3219280948873626d0))`).

  Implementation starting point: `(defconstant +int-cap-bits+ 3321927)` or reuse `+max-int-bits+` (`decimal.lisp:22`).

  Regression prerequisite: [T02 / LISP-C45](tests/02-decimal.md#lisp-c45).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — lisp test 658 pass

## Value ownership, mutation and host conversion

<a id="c-lisp-c2"></a>

- [x] **C-LISP-C2 — Resolve LISP-C2.** A record with 16 or more fields corrupts the process-global record shape after a new key is assigned

  **[LISP-C2](../lisp-code-review.md) — [high] [confirmed, re-verified by synthesizer] A record with 16 or more fields corrupts the process-global record shape after a new key is assigned**

  Source target: `lisp/src/value.lisp:90-101` (`ensure-shaped-children` installs the shape's shared `key-map` as the value's `index`), :337-341 and :359-362 (`value-set` new-key branch), shapes cached in `*shape-cache*` (:35-57).

  Implementation starting point: in `ensure-shaped-children` do not alias `key-map`; call `%build-index` (fresh key->cons table) when count >= threshold, or set `(value-index v)` to nil before the recursion in `value-set`.

  Regression prerequisite: [T03 / LISP-C2](tests/03-values.md#lisp-c2).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — lisp test 658 pass

<a id="c-lisp-c32"></a>

- [x] **C-LISP-C32 — Resolve LISP-C32.** `from-native` raises CL `TYPE-ERROR` on malformed or heterogeneous list input

  **[LISP-C32](../lisp-code-review.md) — [low] [confirmed] `from-native` raises CL `TYPE-ERROR` on malformed or heterogeneous list input**

  Source target: `value.lisp:665-685` (alist detection looks only at `(first x)`, then `(car pair)` / `(cdr ...)` are applied to every element).

  Implementation starting point: validate that every element is a cons with a string car before entering the alist branch; guard improper lists.

  Regression prerequisite: [T03 / LISP-C32](tests/03-values.md#lisp-c32).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — lisp test 658 pass

<a id="c-lisp-c33"></a>

- [x] **C-LISP-C33 — Resolve LISP-C33.** Assignment (and `,`) can build a value nested past the 200 cap

  **[LISP-C33](../lisp-code-review.md) — [low] [confirmed; identical in js, php, cpp, python] Assignment (and `,`) can build a value nested past the 200 cap**

  Source target: `eval.lisp:474` (chain length only), :508 (`value-copy` starts its own depth count at 1), :311/:309 (`eval-list` `value-copy` without position).

  Implementation starting point: in `eval-assign` call `(value-copy-at rhs (length path) (node-pos node))` and pass `(node-pos node)` plus the list's own depth to the copies in `eval-list`.

  Regression prerequisite: [T03 / LISP-C33](tests/03-values.md#lisp-c33).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — lisp test 658 pass

<a id="c-lisp-c35"></a>

- [x] **C-LISP-C35 — Resolve LISP-C35.** TAKE/FILTER/DROP/DEDUPE/SORT/LINK alias their elements, and mutation through the source is observable

  **[LISP-C35](../lisp-code-review.md) — [low] [confirmed, spec-vs-behaviour note; Lisp follows the majority] TAKE/FILTER/DROP/DEDUPE/SORT/LINK alias their elements, and mutation through the source is observable**

  Source target: `structure.lisp:54-93` (TAKE, DROP), `aggregate.lisp:137-209`, :318-421.

  Implementation starting point: either copy in those builtins (a cost) or amend §3.4 / `contributing.md` to say the alias is observable when the body assigns through the source.

  Regression prerequisite: [T03 / LISP-C35](tests/03-values.md#lisp-c35).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — lisp test 658 pass

## Evaluator order, optimizer and recovery

<a id="c-lisp-c13"></a>

- [x] **C-LISP-C13 — Resolve LISP-C13, LISP-C14, LISP-C15.** Coordinate the related changes below at their shared implementation surface.

  **[LISP-C13](../lisp-code-review.md) — [medium] [confirmed, re-verified by synthesizer; cross-host spec gap] The math-plan compiler coerces each operand as it loads it, so error code and position differ from plain evaluation and from spec §7.1**

  Source target: `eval.lisp:153-168` (`:load-var` / `:load-leaf` call `as-dec` immediately), used by `+ - * / %` and ABS/SIGN/CEIL/FLOOR/TRUNC/ROUND/POWER/MIN/MAX; compiled at `optimizer.lisp:699`; `math-plan.lisp:74-79`.

  Implementation starting point: spec decision first (coerce-on-evaluate vs coerce-after-both). Then load all leaves of a plan as Values and coerce at the operator step in left-then-right order, or restrict the plan to leaves that cannot fail. All five hosts together.

  Regression prerequisite: [T04 / LISP-C13](tests/04-evaluation.md#lisp-c13).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[LISP-C14](../lisp-code-review.md) — [medium] [confirmed] `cannot-raise-p` over-approximates, so FILTER+FILTER fusion and the logical field-read rule can hide or reorder errors**

  Source target: `lisp/src/optimizer.lisp:434-449` (`cannot-raise-p`); used by the FILTER+FILTER rule at :553, the sort/MAP pushdown rules, and the sort/FILTER swap at :517-527 (reached from `hybrid.lisp:290-291`, :179 via `optimize-ast-logical`).

  Implementation starting point: `cannot-raise-p` answers "evaluation does not raise", but the callers need "the predicate yields a boolean". Return T for `:bool` only, or require the fused second predicate to be a `:bool` literal. For (b), in logical mode treat a field read as total only while the row is still the relation's row (no MAP/BUCKET/SELECT_COLS/LINK before it) and the field is declared.

  Regression prerequisite: [T04 / LISP-C14](tests/04-evaluation.md#lisp-c14).

  **[LISP-C15](../lisp-code-review.md) — [medium] [confirmed; all hosts] SORT/SORT_BY + TAKE -> TOP* fusion changes evaluation order of the count and drops key evaluation when the count is 0**

  Source target: `optimizer.lisp:498-507` (rule fires for any 2-item TAKE, not only a literal), with `do-top-sort` (`aggregate.lisp:429-437`, evaluates the count first).

  Implementation starting point: fuse only when the TAKE count is a literal non-negative integer (the `try-parse-int-literal` test the TAKE+TAKE rule already uses), and decide whether n = 0 should also be excluded.

  Regression prerequisite: [T04 / LISP-C15](tests/04-evaluation.md#lisp-c15).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts 1600/1600; plain-vs-optimised 0 diffs; all six hosts green

<a id="c-lisp-c34"></a>

- [x] **C-LISP-C34 — Resolve LISP-C34.** `register-builtin` is exported, unguarded and defaults to `:overwrite t`

  **[LISP-C34](../lisp-code-review.md) — [low] [confirmed] `register-builtin` is exported, unguarded and defaults to `:overwrite t`**

  Source target: `registry.lisp:77-83`, exported at `package.lisp:66`.

  Implementation starting point: default `:overwrite nil`, reject manifest names, or drop the export.

  Regression prerequisite: [T04 / LISP-C34](tests/04-evaluation.md#lisp-c34).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

<a id="c-lisp-c44"></a>

- [x] **C-LISP-C44 — Resolve LISP-C44.** Join pre-filter state can outlive a caught error

  **[LISP-C44](../lisp-code-review.md) — [low] [unconfirmed] Join pre-filter state can outlive a caught error**

  Source target: `eval.lisp:377-386` (`??` handler) with `aggregate.lisp:188` (`context-join-prefilter-report` set, then the body walk can raise).

  Implementation starting point: have the `??` handler (or `eval-node`) reset both prefilter slots, or clear the report in the FILTER walk's `unwind-protect`.

  Regression prerequisite: [T04 / LISP-C44](tests/04-evaluation.md#lisp-c44).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — host lanes green; asan clean (cpp); race green (go); LISP-C44 not reproduced, hardening + test

## Aggregates, ordering, grouping and joins

<a id="c-lisp-c1"></a>

- [x] **C-LISP-C1 — Resolve LISP-C1, LISP-C5, LISP-C9, LISP-C17.** Coordinate the related changes below at their shared implementation surface.

  **[LISP-C1](../lisp-code-review.md) — [high] [confirmed, re-verified by synthesizer] BUCKET splits equal text keys once a value's decimal cache is warm**

  Source target: `lisp/src/builtins/aggregate.lisp:569-592` (`eval-key-hash`, via `bucket-key-hash` at :635); root cause is the meaning given to `value-dec-val` (`value.lisp:415-425` `as-dec`, :186 `make-num`).

  Implementation starting point: hash a TEXT value by `(sxhash (value-scalar v))` only (or call `value-hash`); drop the dec branch. s2 verified in a patched copy that this gives `3` and `2,1` and keeps all 1091 conformance cases green. This unblocks P5 and the ISNUM cache in P27.

  Regression prerequisite: [T05 / LISP-C1](tests/05-relational.md#lisp-c1).

  **[LISP-C5](../lisp-code-review.md) — [high] [confirmed, TYPE-ERROR variant re-verified by synthesizer] TOP / TOP_BY / TOP_DESC allocate an N-element heap up front**

  Source target: `lisp/src/builtins/aggregate.lisp:276-277` (`make-bounded-heap` does `(make-array capacity ...)`), called from :484 with `limit = n` as written.

  Implementation starting point: `(make-bounded-heap (min limit <element count>) ...)`, or grow the array lazily.

  Regression prerequisite: [T05 / LISP-C5](tests/05-relational.md#lisp-c5).

  **[LISP-C9](../lisp-code-review.md) — [medium] [confirmed, re-verified by synthesizer] SORT / SORT_DESC / SORT_BY / TOP* of a scalar return an empty list**

  Source target: `aggregate.lisp:320` (`do-sort`: `(or (value-null-p val) (zerop (value-size val)))`) and :431/:439 (`do-top-sort`: `(zerop (value-size val))`).

  Implementation starting point: in both functions replace the `(zerop (value-size val))` test by `(null (aggregate-elements val))` (keep the `limit` test in `do-top-sort` after the list is evaluated).

  Regression prerequisite: [T05 / LISP-C9](tests/05-relational.md#lisp-c9).

  **[LISP-C17](../lisp-code-review.md) — [medium] [confirmed; cross-host, spec gap] The SORT ordering is not a total order for mixed numeric-looking and non-numeric text**

  Source target: `aggregate.lisp:243-274` (`compare-values`): the third `cond` branch (two text/bin values compare by bytes) sits before the rank fallback, next to the first branch (both numeric-looking compare as decimals).

  Implementation starting point: spec decision first: define a strict total order, e.g. all numeric-looking scalars (by decimal) before all non-numeric text (by bytes), which is what the `rank` fallback already implies across kinds; change the text/text branch to apply only when neither or both are numeric-looking. Add cases; then all hosts.

  Regression prerequisite: [T05 / LISP-C17](tests/05-relational.md#lisp-c17).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-lisp-c16"></a>

- [x] **C-LISP-C16 — Resolve LISP-C16.** Equi-join key classification ignores `;` sequences, `,` lists and assignments, producing a spurious E_UNDEF_VAR

  **[LISP-C16](../lisp-code-review.md) — [medium] [confirmed] Equi-join key classification ignores `;` sequences, `,` lists and assignments, producing a spurious E_UNDEF_VAR**

  Source target: `structure.lisp:172-195` (`expr-depends-only-on`), consumed by `try-extract-equi-keys` :197-217 and the hash-join path at :1083 ff.

  Implementation starting point: add `:seq`/`:list` (items) and `:assign` (both sides) clauses, plus `(t (setf all-ok nil))` for any kind not known to be a leaf (`:num :text :bool :null`). `node-contains-var-p` (`aggregate.lisp:8-21`) already walks these.

  Regression prerequisite: [T05 / LISP-C16](tests/05-relational.md#lisp-c16).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Portable regex semantics and resource failures

<a id="c-lisp-c3"></a>

- [x] **C-LISP-C3 — Resolve LISP-C3, LISP-C10, LISP-C11, LISP-C18, LISP-C19, LISP-C37.** Coordinate the related changes below at their shared implementation surface.

  **[LISP-C3](../lisp-code-review.md) — [high] [confirmed, 2 of 4 triggers re-verified by synthesizer] cl-ppcre stack exhaustion or runaway recursion escapes as an uncaught host condition**

  Source target: `lisp/src/builtins/regex.lisp:306-352` (every `cl-ppcre:scan` / `create-scanner`); no handler for `storage-condition` anywhere in `lisp/src`.

  Implementation starting point: wrap each `scan`/`create-scanner` in `handler-case` for `storage-condition` (SBCL re-protects the guard page on unwind) and raise a SEL error. That needs a spec decision (a catalogued code: an existing one such as E_REGEX_SYNTAX for compile-time nesting plus a run-time "regex too complex" code, or E_DEPTH). For the infinite-recursion case, refuse nullable-quantified groups in the validator. Optionally run the matcher on a thread with a larger control stack to raise the subject-length ceiling.

  Regression prerequisite: [T06 / LISP-C3](tests/06-regex.md#lisp-c3).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[LISP-C10](../lisp-code-review.md) — [medium] [confirmed, re-verified by synthesizer] `$` immediately followed by `.*` never matches (cl-ppcre bug on `\z.*`)**

  Source target: `regex.lisp:236` (the `$` -> `\z` lowering exposes it); the bug is inside cl-ppcre.

  Implementation starting point: lower `$` to a zero-width form cl-ppcre does not mis-analyse. s4 verified in raw cl-ppcre that `(?=\z)`, `(?!.)` and `(?:\z|[^\s\S])` followed by `(.*)` all match at 3 while `\z(?:)` and `(?:\z){1}` do not. Check the end-anchored speed-up is not lost for a common trailing `$`; only needed when `$` is the first element of the pattern or of a branch.

  Regression prerequisite: [T06 / LISP-C10](tests/06-regex.md#lisp-c10).

  **[LISP-C11](../lisp-code-review.md) — [medium] [confirmed, re-verified by synthesizer] A quantifier directly after `^` or `$` is accepted; the other four hosts reject it**

  Source target: `regex.lisp:158-239` (`validate-pattern`): `^`/`$` are emitted (230-236) and the quantifier arms (205-213) pass the following `* + ? {..}` through with no "nothing to repeat" check.

  Implementation starting point: in `validate-pattern` remember whether the previous emitted token was `^` or `$` and raise `bad-regex "nothing to repeat"` when a quantifier follows.

  Regression prerequisite: [T06 / LISP-C11](tests/06-regex.md#lisp-c11).

  **[LISP-C18](../lisp-code-review.md) — [medium] [confirmed; cross-host, spec decision] Empty-iteration and capture-reset semantics: cl-ppcre follows Perl, JS/C++ follow ECMAScript**

  Source target: whole design of `regex.lisp`; no validator rule addresses it.

  Implementation starting point: spec-level. Either reject in all hosts a `* + {n,m}` (max > 1) applied to a group that can match empty, and any capture inside a repeated group, or document the family split in §7.8 and pin one side. Lisp cannot follow ECMAScript with cl-ppcre.

  Regression prerequisite: [T06 / LISP-C18](tests/06-regex.md#lisp-c18).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[LISP-C19](../lisp-code-review.md) — [medium] [confirmed; cross-host] No bound on regex backtracking work; hosts diverge on the outcome**

  Source target: `regex.lisp:323-413` (no step budget, no timeout).

  Implementation starting point: spec-level cap (step or time budget -> new E_ code) implemented per host; in cl-ppcre a counter in a custom test hook or `sb-ext:with-timeout`. At minimum record the divergence and make C++ not abort.

  Regression prerequisite: [T06 / LISP-C19](tests/06-regex.md#lisp-c19).

  **[LISP-C37](../lisp-code-review.md) — [low] [confirmed, no current divergence] Regex flag parsing uses `char-downcase`**

  Source target: `regex.lisp:266-267` (`for f = (char-downcase ch)`).

  Implementation starting point: compare explicitly against `#\i #\I` etc. (or mask `char-code` for ASCII only).

  Regression prerequisite: [T06 / LISP-C37](tests/06-regex.md#lisp-c37).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — 116/116 on all six hosts; 0 mismatches vs reference over 24k-80k random patterns per host; resource probes clean; all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Text, binary and output budgets

<a id="c-lisp-c20"></a>

- [x] **C-LISP-C20 — Resolve LISP-C20.** Text-size explosions end in an uncatchable heap exhaustion

  **[LISP-C20](../lisp-code-review.md) — [medium] [confirmed; cross-host spec gap] Text-size explosions end in an uncatchable heap exhaustion**

  Source target: `text.lisp:130-136` (REPEAT), 138-155 (PADL/PADR), 59-75 (REPLACE), `regex.lisp:383-413` (RREPLACE); §6.4 caps only ROUND/POWER/regex-quantifier arguments.

  Implementation starting point: spec a text-length cap (e.g. E_RANGE above N) and check `n * (length s)` / `width` before allocating in REPEAT, PADL/PADR (and the result length in REPLACE/RREPLACE/`&`).

  Regression prerequisite: [T07 / LISP-C20](tests/07-text-binary.md#lisp-c20).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## SQL lexical scope, normalization and expansion

<a id="c-lisp-c6"></a>

- [x] **C-LISP-C6 — Resolve LISP-C6, LISP-C7.** Coordinate the related changes below at their shared implementation surface.

  **[LISP-C6](../lisp-code-review.md) — [high] [confirmed] SQL: `JOIN` over a FILTERed static list silently drops the FILTER**

  Source target: `lisp/src/sql/translator.lisp:2013-2052` (`translate-join`); filters collected by `classify` (1652-1659) and consumed by `agg-body` for ALL/ANY/SUM (1880) and `translate-count` (1956), but `translate-join` never reads `(source-filters src)`.

  Implementation starting point: if `(source-filters src)` is non-empty refuse E_SQL_SHAPE (as `translate-has` does), or apply each predicate per element. Fix in all hosts.

  Regression prerequisite: [T08 / LISP-C6](tests/08-sql-scope.md#lisp-c6).

  **[LISP-C7](../lisp-code-review.md) — [high] [confirmed] SQL: FILTER binder names leak into the aggregate body and other FILTERs' predicates**

  Source target: `lisp/src/sql/translator.lisp:1762` (`with-element`) and :1830 (`with-row`): one frame holds the aggregate's binder AND every absorbed FILTER binder, and body and all predicates are walked in it.

  Implementation starting point: give each absorbed predicate its own frame (binder -> elem, plus `_K`) and walk the body in a frame holding only the aggregate's own binder; same in `with-row`.

  Regression prerequisite: [T08 / LISP-C7](tests/08-sql-scope.md#lisp-c7).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-lisp-c23"></a>

- [x] **C-LISP-C23 — Resolve LISP-C23.** SQL: exponential time/output from a tiny program (assignment substitution builds a DAG that is walked as a tree)

  **[LISP-C23](../lisp-code-review.md) — [medium] [confirmed; cross-host] SQL: exponential time/output from a tiny program (assignment substitution builds a DAG that is walked as a tree)**

  Source target: `stage1.lisp:234-237` (`substitute-node` returns the shared `(cdr cell)` on every read), :297-364 (`record-statement`); walked by `walk-node` (`translator.lisp:97`) and `is-constant` at every compound node.

  Implementation starting point: count generated nodes/parts (or output characters) in `walk-node`/`make-literal` and refuse past a fixed budget (a new limit in `spec/limits.json`, all hosts), or refuse a second read of a non-trivial definition.

  Regression prerequisite: [T08 / LISP-C23](tests/08-sql-scope.md#lisp-c23).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## SQL kinds, numeric fidelity and server limits

<a id="c-lisp-c24"></a>

- [x] **C-LISP-C24 — Resolve LISP-C24.** SQL: UNKNOWN laundered to NUM by kind unification bypasses the numeric guard

  **[LISP-C24](../lisp-code-review.md) — [medium] [confirmed; cross-host] SQL: UNKNOWN laundered to NUM by kind unification bypasses the numeric guard**

  Source target: `translator.lisp:881-898` (`unify-kinds`: "UNKNOWN unifies with anything"), :900-913 (`ret-kind` for `@unify:`), :1259-1268 (`translate-conditional` result kind); `guard-numeric` :810-825 and `emit.lisp:181` (`emit-numeric-operand` passes any `:num` without a guard).

  Implementation starting point: unify to UNKNOWN when any contributing branch is UNKNOWN. Would change the kind of `X ?? 0` and some expected SQL; needs `sql-kinds.md` §4 text plus cases.

  Regression prerequisite: [T09 / LISP-C24](tests/09-sql-kinds.md#lisp-c24).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-lisp-c25"></a>

- [x] **C-LISP-C25 — Resolve LISP-C25.** SQL: `IN (numeric literals)` on an EXACT text column compares bare `x = 1`

  **[LISP-C25](../lisp-code-review.md) — [medium] [confirmed by reading; not run against a server] SQL: `IN (numeric literals)` on an EXACT text column compares bare `x = 1`**

  Source target: `translator.lisp:2133-2147` (`translate-in` branch D): `item` is `f` unchanged whenever the needle is `exact`, whatever `f`'s kind; contrast `translate-binary` :1080-1082.

  Implementation starting point: apply the `translate-binary` rule (skip the operand cast only when the item is exact or a TEXT literal node); otherwise `emit-text-operand` the item.

  Regression prerequisite: [T09 / LISP-C25](tests/09-sql-kinds.md#lisp-c25).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-lisp-c39"></a>

- [x] **C-LISP-C39 — Resolve LISP-C39.** SQL: TAKE/DROP counts are emitted as arbitrary-size integers

  **[LISP-C39](../lisp-code-review.md) — [low] [confirmed; JS also diverges] SQL: TAKE/DROP counts are emitted as arbitrary-size integers**

  Source target: `translator.lisp:2156-2169` (`eval-int-param` returns a bignum), :3137-3154 (rendered with `~D`).

  Implementation starting point: clamp TAKE past 2^63-1 to "no limit" and refuse an OFFSET past it; add the shared limit to `spec/limits`.

  Regression prerequisite: [T09 / LISP-C39](tests/09-sql-kinds.md#lisp-c39).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## SQL bindings, dialects, fragments and rendering

<a id="c-lisp-c36"></a>

- [x] **C-LISP-C36 — Resolve LISP-C36, LISP-C42.** Coordinate the related changes below at their shared implementation surface.

  **[LISP-C36](../lisp-code-review.md) — [low] [confirmed; needs an application-registered dialect] Missing or incomplete `textEscape` emits unescaped inline text (SQL injection class)**

  Source target: `emit.lisp:85-108` (`emit-text-literal`: `(if (not (and escape (listp escape))) (concatenate quote text quote) ...)`); `map.lisp:256-286` (`check-lexical` checks shape only; NIL "withdrawal" is always allowed, nothing checks that the quote character has a rule; `define-dialect` forces only `version`).

  Implementation starting point: in `check-lexical`/`define-dialect` require a non-empty map whose keys include the `textQuote` character (and refuse NIL), and have `emit-text-literal` refuse rather than concatenate when no rule covers the quote.

  Regression prerequisite: [T10 / LISP-C36](tests/10-sql-rendering.md#lisp-c36).

  **[LISP-C42](../lisp-code-review.md) — [low] [confirmed] `check-numeric-guard` memoises the dialect before verifying it**

  Source target: `map.lisp:161-163` (`(push dialect *guard-checked*)` before the checks).

  Implementation starting point: push onto `*guard-checked*` only after all checks pass.

  Regression prerequisite: [T10 / LISP-C42](tests/10-sql-rendering.md#lisp-c42).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-lisp-c38"></a>

- [x] **C-LISP-C38 — Resolve LISP-C38.** About 21 SQL refusal messages contain a literal `~` + newline

  **[LISP-C38](../lisp-code-review.md) — [low] [confirmed] About 21 SQL refusal messages contain a literal `~` + newline**

  Source target: `refuse` (`sql/errors.lisp:20`) does not call FORMAT, but call sites pass strings with `~<newline>` continuations: `translator.lisp` 138, 214, 536, 1400, 1448, 1634, 1662, 1827, 1848, 1864, 1993, 1999, 2003, 2139, 2808; `emit.lisp:134`; `stage1.lisp` 240, 243, 305, 320, 345 (the `binder-none` reasons at 1827/1848/1864 reach `refuse` through `from-binder` :654).

  Implementation starting point: run the message through `(format nil msg)` in `refuse` and `binder-none` (audit for bare `~`), or drop the `~`s at those sites.

  Regression prerequisite: [T10 / LISP-C38](tests/10-sql-rendering.md#lisp-c38).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-lisp-c40"></a>

- [x] **C-LISP-C40 — Resolve LISP-C40, LISP-C41.** Coordinate the related changes below at their shared implementation surface.

  **[LISP-C40](../lisp-code-review.md) — [low] [confirmed; JS reports the same] SQL: parameter slots of a discarded validation pass stay in `fragment-params`**

  Source target: `translator.lisp:2681-2685` (`compile-statement` called on the plan only so earlier steps refuse first; its literals were pushed into the shared params by `make-literal`, result discarded).

  Implementation starting point: snapshot/restore `translator-params`/`param-kinds`/`caveats` around the validation pass, or compact params at the end.

  Regression prerequisite: [T10 / LISP-C40](tests/10-sql-rendering.md#lisp-c40).

  **[LISP-C41](../lisp-code-review.md) — [low] [unconfirmed impact] SQL: NUL characters in TEXT literals and RECORD aliases are emitted as-is**

  Source target: `emit.lisp:85-108` and :148-154 (`emit-ident`); `binding.lisp:39` refuses NUL only for binding-supplied identifiers.

  Implementation starting point: refuse NUL in inline text literals/aliases with E_SQL_UNSUPPORTED (params mode is the driver's business).

  Regression prerequisite: [T10 / LISP-C41](tests/10-sql-rendering.md#lisp-c41).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## Hybrid execution, keys, order and context

<a id="c-lisp-c8"></a>

- [x] **C-LISP-C8 — Resolve LISP-C8.** Hybrid: a fall-through MAP whose custom pair reads `_K` is evaluated over renumbered SQL rows

  **[LISP-C8](../lisp-code-review.md) — [high] [confirmed] Hybrid: a fall-through MAP whose custom pair reads `_K` is evaluated over renumbered SQL rows**

  Source target: `lisp/src/sql/hybrid.lisp:574-716` (`try-plan-fallthrough`), whole-row guard at :617, `reads-whole-row-p` at :543-554.

  Implementation starting point: in `try-plan-fallthrough` return nil when any custom pair (or a dependency read) mentions the variable `_K` anywhere (conservatively, a var named `_K` in the subtree). Same for downstream steps that read `_K` (the arg walk at :631 collects only field reads).

  Regression prerequisite: [T11 / LISP-C8](tests/11-hybrid.md#lisp-c8).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-lisp-c26"></a>

- [x] **C-LISP-C26 — Resolve LISP-C26.** SQL: SORT/TOP(_BY) followed by LINK is not wrapped in a derived table (Lisp); JS and Python wrap it

  **[LISP-C26](../lisp-code-review.md) — [medium] [confirmed divergence; direction unclear] SQL: SORT/TOP(_BY) followed by LINK is not wrapped in a derived table (Lisp); JS and Python wrap it**

  Source target: `translator.lisp:2686-2692` (the LINK arm wraps only for group-by/projections/select-cols/limit/offset/distinct); JS `planHasRowsAbove` (`js/src/sql/translator.mjs:2076`, used at :2593) also includes `orderBy`.

  Implementation starting point: decide in `docs/internals/sql-translation.md`, add a case, align the other hosts (or this one).

  Regression prerequisite: [T11 / LISP-C26](tests/11-hybrid.md#lisp-c26).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-lisp-c27"></a>

- [x] **C-LISP-C27 — Resolve LISP-C27, LISP-C28, LISP-C29, LISP-C30.** Coordinate the related changes below at their shared implementation surface.

  **[LISP-C27](../lisp-code-review.md) — [medium] [confirmed] Hybrid: a split after a key-retaining FILTER hands the continuation renumbered rows**

  Source target: `hybrid.lisp:323-347` (step 3, longest translatable prefix) and :311-321.

  Implementation starting point: when the last prefix step retains keys (FILTER; check DISTINCT/DEDUPE against §7.3) refuse the split if the continuation reads `_K` before its first renumbering step, or move the split earlier.

  Regression prerequisite: [T11 / LISP-C27](tests/11-hybrid.md#lisp-c27).

  **[LISP-C28](../lisp-code-review.md) — [medium] [confirmed] Hybrid: `inline-literals` only understands the 3-item binder form**

  Source target: `hybrid.lisp:151-169` (:call case; `(= (length args) 3)` at :158 and :166).

  Implementation starting point: reuse one shared "binder positions of this call" function (the evaluator's rule: for a binding call with three or more items, item 1 is the binder, plus LINK's two-binder form) in `inline-literals`, `constant-call-p` and the optimizer.

  Regression prerequisite: [T11 / LISP-C28](tests/11-hybrid.md#lisp-c28).

  **[LISP-C29](../lisp-code-review.md) — [medium] [confirmed] Hybrid: splitting before a LINK renames the join's left side to `_INPUT`**

  Source target: `hybrid.lisp:335-338` (`cont-root`) and :700-706; `relational-plan.lisp:22` documents that the source variable names a 3-argument LINK's left side.

  Implementation starting point: keep the original variable name as the continuation root when it contains a LINK and have `execute-hybrid` bind the rows under both `_INPUT` and that name; or refuse a split that leaves a LINK in the continuation.

  Regression prerequisite: [T11 / LISP-C29](tests/11-hybrid.md#lisp-c29).

  **[LISP-C30](../lisp-code-review.md) — [medium] [confirmed] Hybrid: MAP fall-through evaluates custom pairs only on rows that survive the pushed-down TAKE/DROP/sort**

  Source target: `hybrid.lisp:541` (`+fallthrough-downstream+`), :623-634, pinned by `sql/cases/25-hybrid-plans.sqlt` `plan.hybrid.fallthrough-keeps-downstream-steps`.

  Implementation starting point: document the deliberate deviation in §12.1, or only push TAKE/DROP past the MAP when every custom pair cannot raise (`cannot-raise-p` exists; see C14 for its weaknesses).

  Regression prerequisite: [T11 / LISP-C30](tests/11-hybrid.md#lisp-c30).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-lisp-c43"></a>

- [x] **C-LISP-C43 — Resolve LISP-C43.** `source-tables` over-reports a binder that shadows a relation name

  **[LISP-C43](../lisp-code-review.md) — [low] [confirmed] `source-tables` over-reports a binder that shadows a relation name**

  Source target: `hybrid.lisp:41-59` (the walk treats every `:var` naming a binding as a read; used on the original tree for pure-memory plans at :246).

  Implementation starting point: track binder scope in the walk (same shared binder-position function as C28).

  Regression prerequisite: [T11 / LISP-C43](tests/11-hybrid.md#lisp-c43).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-lisp-c47"></a>

- [ ] **C-LISP-C47 — Resolve LISP-C47.** Doc drift on the fall-through's allowed downstream steps

  **[LISP-C47](../lisp-code-review.md) — [low] [confirmed] Doc drift on the fall-through's allowed downstream steps**

  Source target: `docs/internals/sql-translation.md` (MAP fall-through bullet lists `FILTER`) vs `hybrid.lisp:541` (`("SORT_BY" "TOP_BY" "TAKE" "DROP")`; FILTER deliberately excluded per the comment at :540).

  Implementation starting point: - Found by: s7-C10. - Where: `docs/internals/sql-translation.md` (MAP fall-through bullet lists `FILTER`) vs `hybrid.lisp:541` (`("SORT_BY" "TOP_BY" "TAKE" "DROP")`; FILTER deliberately excluded per the comment at :540). - What: the code is the safer side; the doc is stale. Not Lisp-only.

  Regression prerequisite: [T11 / LISP-C47](tests/11-hybrid.md#lisp-c47).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

## Host integration, concurrency and tooling

<a id="c-lisp-c12"></a>

- [x] **C-LISP-C12 — Resolve LISP-C12.** Process-global mutable hash tables are unsynchronised: concurrent use errors out or returns wrong records

  **[LISP-C12](../lisp-code-review.md) — [medium] [confirmed] Process-global mutable hash tables are unsynchronised: concurrent use errors out or returns wrong records**

  Source target: `value.lisp:35-57` (`*shape-cache*`, mutated at compile time for every `RECORD("lit",...)` via `parser.lisp:151-158` `prepare-record-shape`, and by `from-native`); `decimal.lisp:35-58` (`*pow10-cache*`, mutated on every miss for scale/exponent above 18, cleared by `clrhash`); `structure.lisp:326-327` (`*alias-plan-cache*`); `regex.lisp:258,284-303` (`*regex-cache*`); `*registry*` (do not register concurrently with running programs).

  Implementation starting point: `(make-hash-table ... :synchronized t)` for `*shape-cache*` and `*pow10-cache*` (each consulted once per RECORD literal at compile time, or only on miss), a mutex around the registry and alias-plan cache, or thread-local caches. Alternatively document the host as single-threaded and add a guard. A threaded stress test in `lisp/tests/unit.lisp` would catch regressions.

  Regression prerequisite: [T12 / LISP-C12](tests/12-integration.md#lisp-c12).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — six hosts agree on 107 API probes (24 pinned); race/TSan/-race lanes green
