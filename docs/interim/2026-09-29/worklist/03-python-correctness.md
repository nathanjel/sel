# PYTHON correctness

[Worklist](README.md) · [Shared tests](00-tests.md) · [Performance](07-performance.md)

52 findings; 41 implementation tasks. Report severity/confidence is preserved below. Work through one source area at a time; a grouped checkbox closes only when every listed finding is resolved.

Each task starts by reproducing the report against the current tree, then lands the linked shared regression cases before the fix. Fix sketches are starting points, not approved spec changes. Prefer one implementation source per task; touching spec, fixtures or an unavoidable API boundary is allowed. Do not edit generated artifacts without their generator. High-severity tasks take precedence within each area.

## Front end, depth and source positions

<a id="c-py-c1"></a>

- [x] **C-PY-C1 — Resolve PY-C1.** Uncaught `RecursionError` where SPEC 6.4 mandates a value or `E_DEPTH` (systemic; six sites)

  **[PY-C1](../python-code-review.md) — [high] [confirmed; re-verified by synthesizer] Uncaught `RecursionError` where SPEC 6.4 mandates a value or `E_DEPTH` (systemic; six sites)**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: two complementary options. (1) Cut frames per unit: fold `parse_list`/`parse_prefix` into `parse_sequence`/`parse_term` for the single-item path; fold `_dispatch` into `eval_node`, evaluate strict args in place, register builtins as plain functions (about 3 frames per level); make the lexer's brace scanner one iterative scanner with an explicit stack (EOF reports the innermost open context); give `is_constant` the same `depth >= 180` guard its siblings have; charge aggregate levels extra weight in the SQL depth counter. (2) A guaranteed recursion budget around `parse()`/`Program.run`/`translate` (`sys.setrecursionlimit(max(cur, cur_depth + 6*MAX_DEPTH + slack))`, restored in `finally`, under the same lock as PY-C2) plus a backstop that catches `RecursionError` and re-raises `E_DEPTH`. Do NOT fix (a) by charging the pipe step a depth unit (moves E_DEPTH positions away from other hosts). For (b) loop instead of recursing (fold right, same right-deep tree, so the eval-time `E_DEPTH` column stays identical). Catching `RecursionError` alone (s3) would turn a legal program into an error the other hosts do not raise.

  Regression prerequisite: [T01 / PY-C1](tests/01-frontend.md#py-c1).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c17"></a>

- [x] **C-PY-C17 — Resolve PY-C17.** CLI file/REPL input: universal-newline read changes the program, invalid UTF-8 crashes with `UnicodeDecodeError` instead of `E_UTF8`

  **[PY-C17](../python-code-review.md) — [medium] [confirmed; re-verified by synthesizer] CLI file/REPL input: universal-newline read changes the program, invalid UTF-8 crashes with `UnicodeDecodeError` instead of `E_UTF8`**

  Source target: `python/sel/_cli.py:53` (`open(args[0], encoding='utf-8')`, only OSError caught), `:75` (`input()`).

  Implementation starting point: `open(path, 'rb')` and decode with the project's UTF-8 codec (or `newline=''` plus `SelError('E_UTF8')` on `UnicodeDecodeError`); REPL read `sys.stdin.buffer` and wrap decode errors the same way; consider accepting `bytes` in `compile`. A UTF-8 BOM reaches the lexer as U+FEFF and is E_SYNTAX in every host (consistent).

  Regression prerequisite: [T01 / PY-C17](tests/01-frontend.md#py-c17).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — check-cli-source.sh passes on all six hosts

<a id="c-py-c26"></a>

- [x] **C-PY-C26 — Resolve PY-C26.** `x .> f(_, _)` (several placeholders) evaluates the left operand once per placeholder; Go disagrees with the other four

  **[PY-C26](../python-code-review.md) — [low] [confirmed] `x .> f(_, _)` (several placeholders) evaluates the left operand once per placeholder; Go disagrees with the other four**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: decide in the spec (first only, or all with a purity note); if "first only", `break` after the first replacement.

  Regression prerequisite: [T01 / PY-C26](tests/01-frontend.md#py-c26).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — Go break removed; all six hosts agree

<a id="c-py-c27"></a>

- [x] **C-PY-C27 — Resolve PY-C27.** E_UTF8 for a lone-surrogate source carries no position (0:0)

  **[PY-C27](../python-code-review.md) — [low] [confirmed] E_UTF8 for a lone-surrogate source carries no position (0:0)**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: report the surrogate's Pos, in every host at once.

  Regression prerequisite: [T01 / PY-C27](tests/01-frontend.md#py-c27).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — check-cli-source.sh passes on all six hosts

## Exact decimal arithmetic and limits

<a id="c-py-c25"></a>

- [x] **C-PY-C25 — Resolve PY-C25.** `import sel` overrides the deployer's `int_max_str_digits`, and a limit set afterwards leaks a raw `ValueError`

  **[PY-C25](../python-code-review.md) — [low-medium] [confirmed; re-verified by synthesizer] `import sel` overrides the deployer's `int_max_str_digits`, and a limit set afterwards leaks a raw `ValueError`**

  Source target: `python/sel/decimal.py:66-70`; every `int(str)`/`str(int)` in `decimal.py` (lines 183, 190-191, 203-207).

  Implementation starting point: do not touch the global; convert in chunks (<=4000-digit pieces combined by shifts/`divmod` by a power of ten; `format` symmetric). At minimum catch ValueError and raise E_RANGE. (Note s4 relies on this raised limit for the regex bound `int()`.)

  Regression prerequisite: [T02 / PY-C25](tests/02-decimal.md#py-c25).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — python 762 unit pass, oracle 94k 0 mismatches

<a id="c-py-c29"></a>

- [x] **C-PY-C29 — Resolve PY-C29.** The decimal oracle is not independent for `/` and `%` and never reaches interesting magnitudes

  **[PY-C29](../python-code-review.md) — [low] [confirmed] The decimal oracle is not independent for `/` and `%` and never reaches interesting magnitudes**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: for `/`, compute with `Decimal` at ample precision, `quantize(1e-10, ROUND_HALF_UP)` and check exactness with `(q * b == a)`; add 30+ digit operands, scale gaps of 20+, forced ties.

  Regression prerequisite: [T02 / PY-C29](tests/02-decimal.md#py-c29).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — python 762 unit pass, oracle 94k 0 mismatches

## Value ownership, mutation and host conversion

<a id="c-py-c28"></a>

- [x] **C-PY-C28 — Resolve PY-C28, PY-C30.** Coordinate the related changes below at their shared implementation surface.

  **[PY-C28](../python-code-review.md) — [low] [confirmed] `Value.scalar` has a public setter that leaves the cached decimal stale**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: setter clears `_dec_val`, or drop the setter (not listed in the contributing Value API table; nothing in the library uses it).

  Regression prerequisite: [T03 / PY-C28](tests/03-values.md#py-c28).

  **[PY-C30](../python-code-review.md) — [low] [confirmed] Value nesting created by assignment is checked only against the target path, not path + depth of the assigned value (all five hosts)**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: check `len(path) + depth(value) <= MAX_DEPTH` after resolving the path (Value already computes depth while cloning) and raise E_DEPTH at the target; spec wording in all hosts.

  Regression prerequisite: [T03 / PY-C30](tests/03-values.md#py-c30).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — python 762 unit pass, oracle 94k 0 mismatches

<a id="c-py-c32"></a>

- [x] **C-PY-C32 — Resolve PY-C32.** `Program.run(context)` turns falsy non-Value contexts into `{}`, while a truthy float is refused

  **[PY-C32](../python-code-review.md) — [low] [confirmed] `Program.run(context)` turns falsy non-Value contexts into `{}`, while a truthy float is refused**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: `context if context is not None else {}`; correct the docstring.

  Regression prerequisite: [T03 / PY-C32](tests/03-values.md#py-c32).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — python 762 unit pass, oracle 94k 0 mismatches

## Evaluator order, optimizer and recovery

<a id="c-py-c7"></a>

- [x] **C-PY-C7 — Resolve PY-C7, PY-C8.** Coordinate the related changes below at their shared implementation surface.

  **[PY-C7](../python-code-review.md) — [medium] [confirmed; re-verified by synthesizer] The math plan snapshots operands at load time, so a later sub-expression's mutation is not seen (SPEC 3.4)**

  Source target: `python/sel/eval.py:176-189` (`LOAD_VAR`/`LOAD_LEAF` store `as_decimal()` immediately), planned by `python/sel/math_plan.py:96-99, 227-231`.

  Implementation starting point: keep `Value` handles in the scratchpad and coerce at the operator (one cached `as_decimal` per operand at op time), or refuse to plan when a non-leaf-safe operand appears to the right of another operand; decide the ordering in the spec.

  Regression prerequisite: [T04 / PY-C7](tests/04-evaluation.md#py-c7).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[PY-C8](../python-code-review.md) — [medium] [confirmed] Which error wins in `X + Y` depends on plan vs tree-walk (and on an unrelated dead branch pushing the tree past depth 200)**

  Source target: `python/sel/eval.py:176-227` (`_eval_math_plan`: plan loads coerce at load time, before the right operand is evaluated) vs `:367-392` (`_eval_binary`: evaluate both operands, then coerce left, then right); `optimizer.py:633-637` only plans `physical` trees, never assignment-target index expressions; `optimizer.py:673` (`exceeds_depth` turns the whole optimiser off).

  Implementation starting point: choose one rule in the spec (s3 recommends: for `+ - * / %` the left operand is coerced before the right operand is evaluated) and make `_eval_binary` and the compound-assignment path do the same; or make plan loads defer coercion until both operands are evaluated (s2's preference: "operands are evaluated, then coerced left to right", which is what the walker and every non-arithmetic operator do).

  Regression prerequisite: [T04 / PY-C8](tests/04-evaluation.md#py-c8).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts 1600/1600; plain-vs-optimised 0 diffs

<a id="c-py-c9"></a>

- [x] **C-PY-C9 — Resolve PY-C9.** Optimiser SORT/SORT_DESC/SORT_BY + TAKE -> TOP/TOP_DESC/TOP_BY fusion changes the value and the error (all five hosts)

  **[PY-C9](../python-code-review.md) — [medium] [confirmed; partly re-verified] Optimiser SORT/SORT_DESC/SORT_BY + TAKE -> TOP/TOP_DESC/TOP_BY fusion changes the value and the error (all five hosts)**

  Source target: `python/sel/optimizer.py:473-484` (`second.name == 'TAKE'`; only `len(second.args) == 2` is checked).

  Implementation starting point: fuse only when `numeric_literal(second.args[1]) is not None` (a valid literal cannot raise or have effects). Same change in the other four hosts; spec first: add the boundary to the TOP/TAKE paragraph of 7.3.

  Regression prerequisite: [T04 / PY-C9](tests/04-evaluation.md#py-c9).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts green

## Aggregates, ordering, grouping and joins

<a id="c-py-c10"></a>

- [x] **C-PY-C10 — Resolve PY-C10.** Mutating a record from inside an aggregate that is iterating it crashes with a host `TypeError` (JS and PHP also crash; C++/Lisp answer)

  **[PY-C10](../python-code-review.md) — [medium] [confirmed; re-verified by synthesizer] Mutating a record from inside an aggregate that is iterating it crashes with a host `TypeError` (JS and PHP also crash; C++/Lisp answer)**

  Source target: `python/sel/eval.py:538` (`_walk_create(...).set(key, value)`) -> `python/sel/value.py:509-516` (`set` nulls `shape`/`storage` of a shaped record) while `value.py:193-195` (`iter_elements`) is still yielding `value.storage[index]`.

  Implementation starting point: pin the semantics in the spec (iterate a snapshot of the elements that existed when the aggregate started, which is what C++/Lisp do) and have the aggregates materialise `list(iter_elements(v))` before running the body, or make `Value.set` on a shaped record copy `storage` rather than null it in place.

  Regression prerequisite: [T05 / PY-C10](tests/05-relational.md#py-c10).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-py-c11"></a>

- [x] **C-PY-C11 — Resolve PY-C11.** `SELECT_COLS` with a repeated column name builds a record with duplicate keys (fast path)

  **[PY-C11](../python-code-review.md) — [medium] [confirmed; re-verified by synthesizer] `SELECT_COLS` with a repeated column name builds a record with duplicate keys (fast path)**

  Source target: `python/sel/builtins/structure.py:96-105` (`_record_shape(tuple(columns))` at :103).

  Implementation starting point: dedupe `columns` (first occurrence wins, order kept) before the fast path, or use `_unique_record_shape` and fall to the slow path when it returns None.

  Regression prerequisite: [T05 / PY-C11](tests/05-relational.md#py-c11).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-py-c12"></a>

- [x] **C-PY-C12 — Resolve PY-C12.** Equi-join numeric key fast path skips the digit cap: `LINK` joins where `==` raises E_RANGE

  **[PY-C12](../python-code-review.md) — [medium] [confirmed; re-verified by synthesizer] Equi-join numeric key fast path skips the digit cap: `LINK` joins where `==` raises E_RANGE**

  Source target: `python/sel/builtins/structure.py:211-218` (`canonical_join_key`: `int(text)` on the raw scalar when it is all ASCII digits, bypassing `D.parse` and its `MAX_INT_DIGITS` check).

  Implementation starting point: take the `int()` shortcut only for short texts (e.g. `len(text) <= 300`, or `< 4300`), or check `len(text.lstrip('0')) > MAX_INT_DIGITS`; otherwise fall through to `value.as_decimal()`.

  Regression prerequisite: [T05 / PY-C12](tests/05-relational.md#py-c12).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-py-c13"></a>

- [x] **C-PY-C13 — Resolve PY-C13.** The SORT comparator is not a total order on mixed numeric-looking / non-numeric TEXT, so results depend on the sort algorithm (across hosts, and inside Python on SORT+TAKE fusion into TOP)

  **[PY-C13](../python-code-review.md) — [medium] [confirmed] The SORT comparator is not a total order on mixed numeric-looking / non-numeric TEXT, so results depend on the sort algorithm (across hosts, and inside Python on SORT+TAKE fusion into TOP)**

  Source target: `python/sel/builtins/aggregate.py:324-362` (`compare_values`), `:423` (`list.sort`), `:542` (TOP heap).

  Implementation starting point: spec decision first: define a total order (e.g. rank NULL < BOOL < number-looking < other TEXT < BIN < other, compare within rank; or "number-looking TEXT always before non-number TEXT"), add mixed-list `.selt` cases, then change all hosts. It also enables the big speedup in PY-P1.

  Regression prerequisite: [T05 / PY-C13](tests/05-relational.md#py-c13).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-py-c37"></a>

- [x] **C-PY-C37 — Resolve PY-C37.** 2-argument `BUCKET` silently drops rows when two group keys differ structurally but share a key text

  **[PY-C37](../python-code-review.md) — [low] [confirmed; all six hosts agree] 2-argument `BUCKET` silently drops rows when two group keys differ structurally but share a key text**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: in the bare spelling group by `key_str`, not by identity; never `set` the same key twice.

  Regression prerequisite: [T05 / PY-C37](tests/05-relational.md#py-c37).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-py-c38"></a>

- [ ] **C-PY-C38 — Resolve PY-C38.** Aliasing vs copying is observable and differs between hosts (BUCKET 2-arg, LIST, RECORD, SORT*)

  **[PY-C38](../python-code-review.md) — [low] [confirmed] Aliasing vs copying is observable and differs between hosts (BUCKET 2-arg, LIST, RECORD, SORT*)**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: decide in SPEC 3.4 exactly which builtins copy; then drop the clone in Python's bare BUCKET or add the copy everywhere; correct the contributing.md sentence either way.

  Regression prerequisite: [T05 / PY-C38](tests/05-relational.md#py-c38).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

<a id="c-py-c39"></a>

- [x] **C-PY-C39 — Resolve PY-C39.** BUCKET (all hosts but C++) returns an empty result for a scalar source; the spec says a scalar is a one-element list

  **[PY-C39](../python-code-review.md) — [low] [confirmed] BUCKET (all hosts but C++) returns an empty result for a scalar source; the spec says a scalar is a one-element list**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: guard only on `val.is_null()`; let `elements` handle the scalar. Needs the spec to win over four hosts: add the case first.

  Regression prerequisite: [T05 / PY-C39](tests/05-relational.md#py-c39).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-py-c40"></a>

- [x] **C-PY-C40 — Resolve PY-C40.** Direction/limit expressions are skipped when the sorted list is empty (PHP disagrees)

  **[PY-C40](../python-code-review.md) — [low] [confirmed] Direction/limit expressions are skipped when the sorted list is empty (PHP disagrees)**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: spec sentence; evaluate and validate direction/limit before the empty shortcut in every host.

  Regression prerequisite: [T05 / PY-C40](tests/05-relational.md#py-c40).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Portable regex semantics and resource failures

<a id="c-py-c3"></a>

- [x] **C-PY-C3 — Resolve PY-C3, PY-C4, PY-C15, PY-C33, PY-C34, PY-C35.** Coordinate the related changes below at their shared implementation surface.

  **[PY-C3](../python-code-review.md) — [high] [confirmed; partly re-verified] Catastrophic regex backtracking: a 40-character subject hangs the process; no step or time bound**

  Source target: `python/sel/builtins/regex.py:339-346` (`re.compile`), `:357` (`rx.search`); spec has no regex work limit (SPEC 7.8, 6.4).

  Implementation starting point: spec-level decision first. (a) Reject a quantified group whose body can match non-empty text in two ways sharing a quantifier (static ambiguity check in `validate()`, identical in all hosts), or (b) give every host a step budget and a new catalogued error. Python cannot bound `re` steps without thread/signal or a hand-written matcher; a Thompson NFA is possible because the subset has no backreferences or lookaround, and it would also remove PY-C4.

  Regression prerequisite: [T06 / PY-C3](tests/06-regex.md#py-c3).

  **[PY-C4](../python-code-review.md) — [high] [confirmed; partly re-verified] The "portable" regex subset is not portable for quantified groups: PCRE-style hosts and ECMAScript-style hosts return different matches and captures**

  Source target: `python/sel/builtins/regex.py:94-160` (`validate` accepts any quantified group); SPEC 7.8 ("subset every host's engine agrees on").

  Implementation starting point: spec decision then a shared `validate()` change. Least invasive: reject (E_REGEX_SYNTAX) a quantified group whose body can match the empty string, and reject capture groups nested inside a quantified group when an alternation is involved (or all captures inside a quantified group). Alternatively define ECMAScript semantics and implement a small matcher for the subset.

  Regression prerequisite: [T06 / PY-C4](tests/06-regex.md#py-c4).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  **[PY-C15](../python-code-review.md) — [medium] [confirmed] Deeply nested groups in a regex pattern crash with an uncaught `RecursionError`**

  Source target: `python/sel/builtins/regex.py:343-345` (`except re.error` only).

  Implementation starting point: count group nesting in the shared `validate()` and raise `E_REGEX_SYNTAX` (or `E_DEPTH`) beyond a fixed cap such as 200, in all hosts; catch `RecursionError` around `re.compile` as belt-and-braces.

  Regression prerequisite: [T06 / PY-C15](tests/06-regex.md#py-c15).

  **[PY-C33](../python-code-review.md) — [low] [confirmed] `re.compile` emits `FutureWarning` for valid subset patterns (`[[]`, `[a&&b]`, `[a||b]`, `[a~~b]`, `[a--b]`), an uncaught exception under `-W error`**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: escape `[` (and the first char of doubled `&& || ~~`) inside classes after validation, e.g. in `_lower_anchors`'s in-class branch emit `\[`, `\&`, `\|`, `\~`.

  Regression prerequisite: [T06 / PY-C33](tests/06-regex.md#py-c33).

  **[PY-C34](../python-code-review.md) — [low] [confirmed] Regex pattern cache is unbounded**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: bound it (clear or drop oldest beyond 512-1024) or use `functools.lru_cache`.

  Regression prerequisite: [T06 / PY-C34](tests/06-regex.md#py-c34).

  **[PY-C35](../python-code-review.md) — [low] [confirmed, all five hosts] Literal regex patterns are not validated at compile time although the spec says they are**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: implement the compile-time check for literal pattern arguments in every host, or reword errors.md/7.8 to say run time. Decide in the spec first.

  Regression prerequisite: [T06 / PY-C35](tests/06-regex.md#py-c35).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — 116/116 on all six hosts; 0 mismatches vs reference over 24k-80k random patterns per host; resource probes clean; all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## Text, binary and output budgets

<a id="c-py-c14"></a>

- [x] **C-PY-C14 — Resolve PY-C14.** `REPEAT` / `PADL` / `PADR` have no size cap: raw `MemoryError` / `OverflowError`, hang, memory DoS

  **[PY-C14](../python-code-review.md) — [medium] [confirmed; re-verified by synthesizer] `REPEAT` / `PADL` / `PADR` have no size cap: raw `MemoryError` / `OverflowError`, hang, memory DoS**

  Source target: `python/sel/builtins/text.py:117` (`REPEAT`), `:127-137` (`_pad`); `Args.int`/`non_neg_int` in `eval.py:132-144` (`D.to_safe_int`, unbounded despite the name, `decimal.py:237`).

  Implementation starting point: short term: return `''` when the text is empty or n is 0; catch `MemoryError`/`OverflowError` around REPEAT/PADL/PADR and raise `E_RANGE` at the count argument. Properly: add a text-length cap to SPEC 6.4 / `spec/limits.json` (like the digit caps) with an ordinary E_RANGE, and check `len(s) * n` / `width` before allocating; build the pad as `(fill * (need // len(fill) + 1))[:need]` (also PY-P11).

  Regression prerequisite: [T07 / PY-C14](tests/07-text-binary.md#py-c14).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

<a id="c-py-c36"></a>

- [x] **C-PY-C36 — Resolve PY-C36.** `LTB(LIST())` raises E_NO_SCALAR, so `LTB(BTL(""))` fails

  **[PY-C36](../python-code-review.md) — [low] [confirmed, all hosts] `LTB(LIST())` raises E_NO_SCALAR, so `LTB(BTL(""))` fails**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: decide in the spec; if empty BIN is intended return `Value.bin(b'')` for an empty list, in all hosts.

  Regression prerequisite: [T07 / PY-C36](tests/07-text-binary.md#py-c36).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts pass files 27-29 (P5 ambiguity cases 28b pending)

## SQL lexical scope, normalization and expansion

<a id="c-py-c5"></a>

- [x] **C-PY-C5 — Resolve PY-C5.** SQL: `BUCKET ... SUM(g, body)` crashes with a host `TypeError` whenever the body contains a literal

  **[PY-C5](../python-code-review.md) — [high] [confirmed; re-verified by synthesizer] SQL: `BUCKET ... SUM(g, body)` crashes with a host `TypeError` whenever the body contains a literal**

  Source target: `python/sel/sql/translator.py:821` (`f"COALESCE(SUM({''.join(inner.parts)}), 0)"`), inside `_call`'s GROUP branch (809-821).

  Implementation starting point: build `Fragment(['COALESCE(SUM(', *inner.parts, '), 0)'], 'NUM', dialect, inner.params, inner.param_kinds, inner.caveats)` (or go through a skeleton/`fill`). Cross-host: `php/src/Sql/Translator.php:708` (`implode('', $inner->parts)`) and `js/src/sql/translator.mjs:890` (`inner.parts.join('')`) use the same idiom; there an int part is stringified, so s6 expects they silently emit the slot NUMBER as SQL text (`SUM(qty * 3)`), "worse than a crash". UNCONFIRMED for PHP/JS (not run).

  Regression prerequisite: [T08 / PY-C5](tests/08-sql-scope.md#py-c5).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c6"></a>

- [x] **C-PY-C6 — Resolve PY-C6.** SQL: exponential output from a linear program (helper reuse is inlined as a tree)

  **[PY-C6](../python-code-review.md) — [high] [confirmed] SQL: exponential output from a linear program (helper reuse is inlined as a tree)**

  Source target: `python/sel/sql/normalise.py:91,109` (`defs[name] = value`; `_substitute` returns the SAME node object for every read) plus `translator.py:222-269` (`_node` renders it as a tree).

  Implementation starting point: bound the rendered node count / parameter count (new E_SQL_* or an E_SQL_DEPTH-style refusal, specified so every host agrees), or refuse a helper read more than once when its right-hand side is non-trivial. Memoising the rendered Fragment would not help (slot numbers are absolute per occurrence).

  Regression prerequisite: [T08 / PY-C6](tests/08-sql-scope.md#py-c6).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c18"></a>

- [x] **C-PY-C18 — Resolve PY-C18.** SQL: name-based (unhygienic) substitution and element re-entry produce wrong SQL or a spurious `E_SQL_DEPTH`

  **[PY-C18](../python-code-review.md) — [medium] [confirmed] SQL: name-based (unhygienic) substitution and element re-entry produce wrong SQL or a spurious `E_SQL_DEPTH`**

  Source target: `python/sel/sql/normalise.py:163-166` (helper values substituted by name; a free variable in the helper's value can be captured by a use-site binder) and `translator.py:1178-1180 / 1461-1462 / _with_element 1498-1512` (a static list's element is rendered by `_node(b.payload)` inside the frame that binds the inner binder and `_K`, not the scope where the list was written).

  Implementation starting point: render a static list's elements against the frame stack that existed when the list was resolved (store the frame depth in the `Binder.NODE` payload and evaluate `_node` with `self.frames` truncated to it), and make stage 1 refuse (or alpha-rename) a helper whose free variables are also binder names at a use site.

  Regression prerequisite: [T08 / PY-C18](tests/08-sql-scope.md#py-c18).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c45"></a>

- [x] **C-PY-C45 — Resolve PY-C45.** SQL: a binder named like a scalar `value` binding is treated as that constant by the validation pre-check

  **[PY-C45](../python-code-review.md) — [low] [confirmed] SQL: a binder named like a scalar `value` binding is treated as that constant by the validation pre-check**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: pass the active binder names into `is_constant`, or drop shadowed names from `const_names` while a frame binds them.

  Regression prerequisite: [T08 / PY-C45](tests/08-sql-scope.md#py-c45).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## SQL kinds, numeric fidelity and server limits

<a id="c-py-c19"></a>

- [x] **C-PY-C19 — Resolve PY-C19, PY-C20, PY-C21.** Coordinate the related changes below at their shared implementation surface.

  **[PY-C19](../python-code-review.md) — [medium] [confirmed] SQL: `JOIN` over a static list accepts BOOL and BIN elements/separators that SEL rejects with `E_NOT_TEXT`**

  Source target: `python/sel/sql/translator.py:1744-1755` (`_join_aggregate`: `fold_pairwise('&', ...)` calls `_apply` directly, skipping the `_require_not_bool_operand` / kind guards that `_binary` applies to `&`, 626-628).

  Implementation starting point: in `_join_aggregate`, run each element fragment and the separator through the same kind guards `&`/text functions use.

  Regression prerequisite: [T09 / PY-C19](tests/09-sql-kinds.md#py-c19).

  **[PY-C20](../python-code-review.md) — [medium] [confirmed; server behaviour reasoned] SQL: `x IN (list)` with an `exact` TEXT needle skips the byte cast on every list item, so MariaDB/MySQL/SQLite compare numerically**

  Source target: `python/sel/sql/translator.py:774-785` (`is_exact = getattr(raw, 'exact')`; `item = f if is_exact else text_operand(f)`).

  Implementation starting point: reuse the `_binary` rule: skip the cast only if the item is exact or a text literal node; otherwise `text_operand(f)`.

  Regression prerequisite: [T09 / PY-C20](tests/09-sql-kinds.md#py-c20).

  **[PY-C21](../python-code-review.md) — [medium] [confirmed; server behaviour reasoned] SQL: `x IN <relation>` never checks that needle and column kinds are comparable**

  Source target: `python/sel/sql/translator.py:698-728` (relation branch of `_in_operator`); the scalar branch (749) and literal-list branch (782) both call `_require_comparable_kinds`.

  Implementation starting point: call `_require_comparable_kinds(needle, self._column_ref(b['fields'][scalar]), 'IN', n.pos)` in the relation branch.

  Regression prerequisite: [T09 / PY-C21](tests/09-sql-kinds.md#py-c21).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c41"></a>

- [x] **C-PY-C41 — Resolve PY-C41, PY-C42.** Coordinate the related changes below at their shared implementation surface.

  **[PY-C41](../python-code-review.md) — [low] [confirmed; server behaviour reasoned] SQL: TAKE/DROP counts above the server's integer range translate to SQL every server rejects**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: drop LIMIT when count >= 2^63, refuse (E_SQL_UNSUPPORTED) for OFFSET >= 2^63, cap the computed limit likewise.

  Regression prerequisite: [T09 / PY-C41](tests/09-sql-kinds.md#py-c41).

  **[PY-C42](../python-code-review.md) — [low] [confirmed on SQLite] SQL: unrolled IN / ANY / ALL / SUM over more than about 1000 list elements is rejected by SQLite at run time (O(n) deep on every server)**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: balanced folding on all hosts, or an explicit `E_SQL_UNSUPPORTED`/caveat above a documented element count. (Interacts with PY-P3.)

  Regression prerequisite: [T09 / PY-C42](tests/09-sql-kinds.md#py-c42).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## SQL bindings, dialects, fragments and rendering

<a id="c-py-c43"></a>

- [x] **C-PY-C43 — Resolve PY-C43.** SQL: identifiers from SEL text literals (RECORD field names, SELECT_COLS names) bypass the NUL/empty checks that binding names get

  **[PY-C43](../python-code-review.md) — [low] [confirmed] SQL: identifiers from SEL text literals (RECORD field names, SELECT_COLS names) bypass the NUL/empty checks that binding names get**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: apply a `_check_name`-equivalent refusal in `Emit.ident`.

  Regression prerequisite: [T10 / PY-C43](tests/10-sql-rendering.md#py-c43).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c44"></a>

- [x] **C-PY-C44 — Resolve PY-C44.** SQL bindings: constructors accept types that make no sense; `Binding.value(type=...)` ignores everything but NUM; case-colliding names silently merge

  **[PY-C44](../python-code-review.md) — [low] [confirmed] SQL bindings: constructors accept types that make no sense; `Binding.value(type=...)` ignores everything but NUM; case-colliding names silently merge**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: restrict column/raw types to NUM|TEXT|BOOL|BIN|UNKNOWN and value types to NUM|None; refuse case-colliding names (E_SQL_BINDING).

  Regression prerequisite: [T10 / PY-C44](tests/10-sql-rendering.md#py-c44).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c46"></a>

- [x] **C-PY-C46 — Resolve PY-C46.** `Fragment.as_value(mode)` accepts an unknown mode when the fragment has no parameter slot

  **[PY-C46](../python-code-review.md) — [low] [confirmed] `Fragment.as_value(mode)` accepts an unknown mode when the fragment has no parameter slot**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: validate `mode` at the top of `_join`.

  Regression prerequisite: [T10 / PY-C46](tests/10-sql-rendering.md#py-c46).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c49"></a>

- [x] **C-PY-C49 — Resolve PY-C49, PY-C50.** Coordinate the related changes below at their shared implementation surface.

  **[PY-C49](../python-code-review.md) — [low] [confirmed; also PHP and JS] `check_numeric_guard` memoises the dialect before it checks it, so the second use of a bad guard silently passes**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: add to the set only after the checks pass.

  Regression prerequisite: [T10 / PY-C49](tests/10-sql-rendering.md#py-c49).

  **[PY-C50](../python-code-review.md) — [low] [confirmed] Registered dialects can produce unescaped text/identifier literals; validation is per key, not per pairing**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: after resolving the effective lexical set require `textQuote in textEscape` and the `identQuote`/`identEscape` analogue (or a post-condition in `text_literal`/`ident`); reject `None` for these four keys.

  Regression prerequisite: [T10 / PY-C50](tests/10-sql-rendering.md#py-c50).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## Hybrid execution, keys, order and context

<a id="c-py-c22"></a>

- [x] **C-PY-C22 — Resolve PY-C22.** Hybrid: a split after `FILTER` renumbers rows, so the continuation's `_K` and the result keys differ from `run()` (also in JS)

  **[PY-C22](../python-code-review.md) — [medium] [confirmed] Hybrid: a split after `FILTER` renumbers rows, so the continuation's `_K` and the result keys differ from `run()` (also in JS)**

  Source target: `python/sel/sql/hybrid.py:807-829` (prefix loop, continuation over `_INPUT`), also `:437-459` (MAP fall-through continuation) and `:730` (latest-member).

  Implementation starting point: a prefix containing a `FILTER` not followed by a renumbering step (SORT_BY/TAKE/DROP/MAP/BUCKET...) is a valid split point only if the continuation cannot observe keys; otherwise fall back to a shorter prefix (or expose an ordinal, which the design refuses to invent).

  Regression prerequisite: [T11 / PY-C22](tests/11-hybrid.md#py-c22).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c23"></a>

- [x] **C-PY-C23 — Resolve PY-C23.** Hybrid: splitting in front of an in-memory `LINK` renames the left side from the relation to `_INPUT` (also in JS)

  **[PY-C23](../python-code-review.md) — [medium] [confirmed] Hybrid: splitting in front of an in-memory `LINK` renames the left side from the relation to `_INPUT` (also in JS)**

  Source target: `python/sel/sql/hybrid.py:823-824` (`input_node = Node('var', ..., name='_INPUT')`); the 3-arg LINK names its left side after the source variable (SPEC 7.4); `RelationalPlan.root_name` exists but the continuation drops it.

  Implementation starting point: when the first continuation step is a 3-arg LINK/LINK_LEFT, rewrite to the 5-arg form with the original root variable name (and lower-case alias) as the left binder, or bind an alias variable of that name.

  Regression prerequisite: [T11 / PY-C23](tests/11-hybrid.md#py-c23).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c24"></a>

- [x] **C-PY-C24 — Resolve PY-C24.** Hybrid: `_inline_literals` mis-scopes binders of the 4- and 5-argument forms, corrupting the continuation (also in JS)

  **[PY-C24](../python-code-review.md) — [medium] [confirmed] Hybrid: `_inline_literals` mis-scopes binders of the 4- and 5-argument forms, corrupting the continuation (also in JS)**

  Source target: `python/sel/sql/hybrid.py:551-566`. Only `len(node.args) == 3` is treated as "binder in args[1]"; `SORT_BY/BUCKET(list, binder, key, x)` (4 args) and `TOP_BY(list, binder, key, dir, n)` / `LINK(l, r, L, R, pred)` (5 args) have binders elsewhere.

  Implementation starting point: derive binder names from the function's actual shape (the evaluator's `shape()`/`do_sort`/`do_bucket`/LINK rules, or the helper stage 1 uses) and never inline into a binder-name position.

  Regression prerequisite: [T11 / PY-C24](tests/11-hybrid.md#py-c24).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c47"></a>

- [x] **C-PY-C47 — Resolve PY-C47.** Hybrid: MAP fall-through pushes `TAKE`/`DROP` past the local half of the MAP, so errors `run()` raises are lost

  **[PY-C47](../python-code-review.md) — [low] [confirmed] Hybrid: MAP fall-through pushes `TAKE`/`DROP` past the local half of the MAP, so errors `run()` raises are lost**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: allow TAKE/DROP downstream only when the custom half provably cannot raise, or keep them out of SQL, or document error-loss as accepted.

  Regression prerequisite: [T11 / PY-C47](tests/11-hybrid.md#py-c47).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c48"></a>

- [x] **C-PY-C48 — Resolve PY-C48.** Hybrid: `source_tables` reports a relation that is only used as a binder name (also in JS)

  **[PY-C48](../python-code-review.md) — [low] [confirmed] Hybrid: `source_tables` reports a relation that is only used as a binder name (also in JS)**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: skip binder positions or read the tables from the translator's own record of relations touched.

  Regression prerequisite: [T11 / PY-C48](tests/11-hybrid.md#py-c48).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c51"></a>

- [x] **C-PY-C51 — Resolve PY-C51.** `execute_hybrid` deep-clones the caller's context, so hybrid and pure-memory plans differ in whether the context is mutated

  **[PY-C51](../python-code-review.md) — [low] [confirmed] `execute_hybrid` deep-clones the caller's context, so hybrid and pure-memory plans differ in whether the context is mutated**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: pick one rule and document it (clone in all branches, or set `_INPUT` on the real context and delete afterwards). See PY-P16 for cost.

  Regression prerequisite: [T11 / PY-C51](tests/11-hybrid.md#py-c51).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

<a id="c-py-c52"></a>

- [x] **C-PY-C52 — Resolve PY-C52.** SQL: nested `ORDER BY` in a derived table is relied on for tie order

  **[PY-C52](../python-code-review.md) — [low] [unconfirmed on real servers; reproduced on SQLite] SQL: nested `ORDER BY` in a derived table is relied on for tie order**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: carry earlier sort keys as trailing keys of the outer ORDER BY when the columns are still visible.

  Regression prerequisite: [T11 / PY-C52](tests/11-hybrid.md#py-c52).

  Evidence gate: confirm on the named platform/configuration; otherwise record a supported non-defect/already-fixed disposition and retain the applicable invariant test.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — all six hosts sqlt 1265/1265 with reuse twin; oracle/hybrid lanes agree on SQLite (+ MariaDB/MySQL/PostgreSQL for PHP); PY-C1 site f closed

## Host integration, concurrency and tooling

<a id="c-py-c2"></a>

- [x] **C-PY-C2 — Resolve PY-C2.** `_gc.bulk_allocation` is not thread-safe: concurrent `Program.run` can leave the cyclic collector disabled for the whole process

  **[PY-C2](../python-code-review.md) — [high] [confirmed; re-verified by synthesizer] `_gc.bulk_allocation` is not thread-safe: concurrent `Program.run` can leave the cyclic collector disabled for the whole process**

  Source target: `python/sel/_gc.py:26-45` (module globals `_depth`, `_resume`, check-then-act, no lock); called from `python/sel/__init__.py:68` on every `Program.run`.

  Implementation starting point: guard enter/exit with a `threading.Lock` (state = depth + saved flag, decided under the lock), or make the pause per-thread and skip it when more than one thread holds it; or drop the global toggle and document `gc.freeze()`/thresholds for applications. Add a thread stress test to `python/tests/test_gc_pause.py`.

  Regression prerequisite: [T12 / PY-C2](tests/12-integration.md#py-c2).

  Contract gate: settle the relevant rule in the shared test family before choosing among the report’s proposed behaviors.

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — fails on the HEAD _gc.py, passes now

<a id="c-py-c16"></a>

- [x] **C-PY-C16 — Resolve PY-C16.** `SelError` cannot be pickled or copied: one error kills a `ProcessPoolExecutor`

  **[PY-C16](../python-code-review.md) — [medium] [confirmed; re-verified by synthesizer] `SelError` cannot be pickled or copied: one error kills a `ProcessPoolExecutor`**

  Source target: `python/sel/errors.py:18-35`.

  Implementation starting point: add `def __reduce__(self): return (SelError, (self.code, self.message, Pos(self.line, self.col, self.offset)))` (or pass all fields to `super().__init__`).

  Regression prerequisite: [T12 / PY-C16](tests/12-integration.md#py-c16).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — six hosts agree on 107 API probes (24 pinned); race/TSan/-race lanes green

<a id="c-py-c31"></a>

- [x] **C-PY-C31 — Resolve PY-C31.** `dependencies()` is order-insensitive, contradicting its own docstring (all five hosts)

  **[PY-C31](../python-code-review.md) — [low] [confirmed] `dependencies()` is order-insensitive, contradicting its own docstring (all five hosts)**

  Source target: Locate the symbols and sites named in the linked finding; preserve its complete scope.

  Implementation starting point: flow-sensitive walk (a read is a dependency unless an assignment definitely precedes it on every path; conservatively unless one appears earlier in a `;` sequence at the same or enclosing level), or change spec/docs to "minus every name the program ever assigns" and fix the docstring.

  Regression prerequisite: [T12 / PY-C31](tests/12-integration.md#py-c31).

  Done when every linked scenario passes on all applicable lanes; code/position, mutation, ordering and repeat-use behavior satisfy the shared contract. Record commit and fixture IDs here.

  Closed 2026-09-29 — six hosts agree on 107 API probes (24 pinned); race/TSan/-race lanes green
