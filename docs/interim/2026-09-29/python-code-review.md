# SEL Python host: code review (correctness and performance)

Date: 2026-09-29. Repo state: working tree on `main` at the time of review (HEAD `faff480`, 0.9.2, plus uncommitted work from other sessions; Rust ignored).

## Scope and method

Everything under `python/sel/` was reviewed by seven slice reviewers working in parallel and read-only (Python 3.14.7; the box was heavily loaded, so absolute timings are noisy and ratios within one run are what matter). This report is the deduplicated synthesis of their seven slice reports (`s1`..`s7` below).

| Slice | Files |
|---|---|
| s1 | front end: `lexer.py`, `parser.py`, `errors.py`, `utf8.py`, `_limits.py` (consumer) |
| s2 | numeric core and values: `decimal.py`, `value.py`, `builtins/number.py`, `math_plan.py`, `_gc.py` |
| s3 | evaluator, registry, host API, optimiser: `eval.py`, `registry.py`, `__init__.py`, `optimizer.py`, `_cli.py` |
| s4 | text/regex/binary/null/control builtins |
| s5 | `builtins/structure.py`, `builtins/aggregate.py` (LINK, BUCKET, SORT*, TOP*, ...) |
| s6 | SQL translator core: `sql/translator.py`, `normalise.py`, `binding*.py`, `binder.py`, `fragment.py`, `errors.py` |
| s7 | SQL rendering and hybrid: `sql/hybrid.py`, `map.py`, `emit.py`, `constants.py`, `relational_plan.py` |

Labels: **confirmed** = reproduced by the reviewer on the host; **measured** vs **reasoned** for performance as the reviewers labelled it. "Re-verified by synthesizer" = the synthesizer re-ran the given repro read-only against the working tree on 2026-09-29. Nothing was upgraded in confidence. The Python conformance suite (1091 cases) passes, so every finding below is a gap in the suite, not a regression it detects.

## Executive summary

- **Health:** the core is sound. Decimal arithmetic, lexing/parsing precedence, assignment semantics, joins, the optimiser (about 45,000 random pipelines, zero unexplained differences), regex whitelist/anchor lowering and SQL quoting/injection safety all held up under differential probing. Findings cluster at the edges: limits, host-exception leaks, and under-specified semantics.
- **Counts:** 52 correctness findings (6 high, 18 medium, 1 low-medium, 27 low; 51 confirmed, 1 unconfirmed on real servers) and 31 performance findings (3 high, 15 medium, 3 low-medium, 10 low).
- **Worst thing (systemic):** Python's 1000-frame stack means the language's own `E_DEPTH` cap of 200 is unreachable for several constructs: a legal program (pipeline steps, `??` chains, nested interpolation, a 198-stage `.> SORT`/`.> MAP` chain, helper chains in the SQL layer) dies with an uncaught `RecursionError` where the other hosts return a value or `E_DEPTH` (PY-C1).
- **Other high-severity items:** the GC pause in `_gc.py` is not thread-safe and can leave the collector disabled for the whole process (PY-C2); Python `re` backtracks catastrophically (a 40-character subject hangs) and the "portable" regex subset is not portable for quantified groups (PY-C3, PY-C4, both cross-host spec issues); the SQL translator crashes with `TypeError` on `SUM(g, body-with-literal)` inside `BUCKET` (PY-C5) and blows up exponentially on a linear program of about 150 bytes (PY-C6).
- **Biggest performance wins:** SORT/SORT_BY/TOP* re-derive key kind/decimal/bytes on every comparison (measured 35-45x available, PY-P1); LINK `exec`s freshly generated source per call and per shape pair (6.3x on nested LINKs, up to 40-150x on heterogeneous rows, PY-P2); `fold_pairwise` in the SQL translator is quadratic (31.9 s for an 8000-item `IN` list, PY-P3); `dataclasses.replace` in the optimiser's `copy_node` (about 9x per node, roughly 2x on every one-shot `evaluate()`, PY-P4).
- **Cross-host:** at least 17 findings are spec-level (shared by other hosts) and need spec, conformance case, then all hosts; see "Cross-host findings".

---

# Correctness findings

Ordered by severity, then confidence. "Found by" lists every slice/finding that reported it.

## High

### PY-C1 [high] [confirmed; re-verified by synthesizer] Uncaught `RecursionError` where SPEC 6.4 mandates a value or `E_DEPTH` (systemic; six sites)
- Found by: s1 C1, C2, C3; s3 C2; s5 C4; s6 C3; s7 C4.
- What: SPEC 6.4 makes `E_DEPTH` (cap 200) the only outcome for over-deep nesting, and a program under the cap must run. Python spends several frames per depth unit, so CPython's default limit of 1000 is exhausted first and a foreign exception escapes (not a `SelError`). `python/bin/batch.py:83` already has a special `!HOST RecursionError` line, so the fuzzer knows. The parser header comment (`parser.py:1-19`) claims it is impossible. Other hosts accept the programs or answer `E_DEPTH` at the pinned column.
- Sites:

| # | Site | Where | Trigger | Frames per unit / threshold |
|---|---|---|---|---|
| a | pipe-step argument nesting | `parser.py:352-381` (`parse_pipe_step`), `:335-350` | `x .> f(<sequence>)` nested | 6 frames/unit; fails at n=166 (150 ok) |
| b | right-assoc `??` / `???` | `parser.py:269-272` (no `enter()/leave()`) | 989+ operators | 1 frame/operator; first failing n=989 |
| c | nested interpolation, unterminated `"{` runs | `lexer.py:184-214`, `:245-291`, `:305-327` | nested `"{...}"` | 3+2 frames/level; first failing depth 331; parser would answer E_DEPTH at about 50 |
| d | evaluator call chains | `eval.py:163-173`, `:230`, `:111-114`, builtin lambdas | 198-stage `.> SORT`, `.> MAP(_)`, `.> TOP(_,2)` | about 5 frames/level; SORT/SORT_DESC/SORT_BY/MAP need 998, TOP/TOP_BY 999, BUCKET 800, SELECT_COLS/LIST/RECORD 602, plain `&`/`??`/call chains 604, assign chain 595, nested aggregates 498 |
| e | aggregate/LINK dispatch | `aggregate.py:47-71` (`walk`), `structure.py:892`, `aggregate.py:365`, `:432` | `LIST(1) .> MAP(_) x198 .> COUNT()`; also `SUM(1)`, `ALL(TRUE)`, `SORT()`, `TOP(1)`, `TOP_BY(1,1)`, `LINK(LIST(1), TRUE)`, `LINK_LEFT(...)` | 197 stages fine, 198 fails; MAP with `LIST(_)` body fails at about 190-199 |
| f | SQL layer | `sql/translator.py:253-269` (about 8 frames/SEL level through aggregates), `normalise.py:136`, `constants.py:181-239` (`is_constant`/`_constant_call`, 3 frames/node, no counter) | helper chains: `V0 = C; V1 = ANY(V0, F); ... V197 = ANY(V196, F); V197` (RecursionError at n=197 and 198; n<=196 fine); `A0 = R .> FILTER(_["id"] > 0); A1 = A0 .> TAKE(11); ...` with n=340 (about 9 KB) fails in `translate_statement`, `try_translate`, `plan_hybrid` (n=300 fine) | `try_translate*` catches only `SqlError`, so even the refusal-tolerant API leaks; `plan_hybrid`'s "never an exception" contract is broken. `_contains_unsupported_sql` has the same exposure once `is_constant` is fixed |

- Repro:
  - (a)
    ```
    PYTHONPATH=$PWD/python python3 -c "
    import sel
    s='1'
    for i in range(180): s='1 .> MAX('+s+')'
    sel.compile(s)"          # -> RecursionError (traceback)
    ```
    n=150 ok, n=166 RecursionError, n=180 RecursionError. Reviewer's cross-host run at n=180: py Traceback; js, php, cpp, lisp, go = `1`; at n=250 the other five = `E_DEPTH at line 1 column 1792`.
  - (b) `python3 -m sel -e "$(python3 -c "print('a'+'??a'*3000)")"` -> Traceback ending `RecursionError` in `parse_primary`; js/php/cpp/lisp/go -> `E_DEPTH at line 1 column 598: evaluation nested too deeply`. `'a'+'???a'*2000` fails the same way.
  - (c) `s='1'; for _ in range(400): s='"{'+s+'}"'` -> py Traceback, all other hosts `E_DEPTH at line 1 column 101`. Unterminated: `python3 -c "print('\"{'*600)"` as source -> py Traceback; js/php/cpp/lisp/go `E_UNTERMINATED at line 1 column 1200` (100 repeats agree on all six, column 200).
  - (d) `PYTHONPATH=$PWD/python python3 -m sel -e "$(python3 -c "print('LIST(1,2)' + ' .> SORT'*198)")"` -> `RecursionError`; JS, PHP, C++, Lisp print `-{"1"=t"1", "2"=t"2"}`. Measured with 0/5/30/100 extra caller frames.
  - (e) `PYTHONPATH=$PWD/python python3 -m sel -e "$(python3 -c "print('LIST(1)' + ' .> MAP(_)'*198 + ' .> COUNT()')")"` -> py `RecursionError`; js `E_DEPTH at line 1 column 6: evaluation nested too deeply`.
  - (f) see the trigger column.
  - Synthesizer re-ran (a) n=180, (b), (c) nested-400, (d) and (e): each raised `RecursionError`. (f) not re-run.
- Fix sketch: two complementary options. (1) Cut frames per unit: fold `parse_list`/`parse_prefix` into `parse_sequence`/`parse_term` for the single-item path; fold `_dispatch` into `eval_node`, evaluate strict args in place, register builtins as plain functions (about 3 frames per level); make the lexer's brace scanner one iterative scanner with an explicit stack (EOF reports the innermost open context); give `is_constant` the same `depth >= 180` guard its siblings have; charge aggregate levels extra weight in the SQL depth counter. (2) A guaranteed recursion budget around `parse()`/`Program.run`/`translate` (`sys.setrecursionlimit(max(cur, cur_depth + 6*MAX_DEPTH + slack))`, restored in `finally`, under the same lock as PY-C2) plus a backstop that catches `RecursionError` and re-raises `E_DEPTH`. Do NOT fix (a) by charging the pipe step a depth unit (moves E_DEPTH positions away from other hosts). For (b) loop instead of recursing (fold right, same right-deep tree, so the eval-time `E_DEPTH` column stays identical). Catching `RecursionError` alone (s3) would turn a legal program into an error the other hosts do not raise.
- Conformance gap: none; `10-limits.selt` pins E_DEPTH only for constructs that use few frames. Suggest `lim.pipe-depth-just-under` and `lim.pipe-depth` (modelled on `lim.index-depth[-just-under]`), `lim.coalesce-chain`, `lim.interp-depth`, `lex.unterminated.deep-brace-run`, and a 198-stage `.> SORT` / `.> MAP(_)` / `.> TOP_BY` pipeline expecting the value. For (f) a `.sqlt` case with a 400-step helper chain expecting a refusal / pure_memory plan (Python unit test needed for the exact-cap cases since source cannot exceed the parser cap). JS has the same shape in (b) and survives only via a bigger stack.

### PY-C2 [high] [confirmed; re-verified by synthesizer] `_gc.bulk_allocation` is not thread-safe: concurrent `Program.run` can leave the cyclic collector disabled for the whole process
- Found by: s3 C1 (rated high), s2 C3 (rated medium). Rated high here: the damage is process-wide and belongs to the host application, not to SEL.
- Where: `python/sel/_gc.py:26-45` (module globals `_depth`, `_resume`, check-then-act, no lock); called from `python/sel/__init__.py:68` on every `Program.run`.
- What: `__enter__` does `if _depth == 0: _resume = gc.isenabled(); gc.disable()` and only later `_depth += 1`. A second thread entering between `gc.disable()` and the increment sees `_depth == 0`, reads `gc.isenabled()` == False, overwrites `_resume = False`, and on the last exit `gc.enable()` is never called. The application's cyclic garbage then leaks for the rest of the process. `+=`/`-=` on module globals are also lost-update races (and in a free-threaded build outright). `contributing.md` says "re-entrant, exception-safe"; `_gc.py`'s docstring concedes only "delays cycle collection for the others".
- Repro: s3: 4 threads each calling `Program.run({})` on `1 + 1` 4000 times, then `gc.isenabled()`: left disabled in 7 of 40 trials at the default switch interval; with `sys.setswitchinterval(1e-6)` it fails on the first try. s2: 8 threads x 50 ms, 200 trials: disabled after the first 1, 3, 12 and 25 trials in four runs; bare `with bulk_allocation(): pass` loop with `setswitchinterval(1e-6)` fails in trial 0. Synthesizer: 4 threads x 3000 runs of `1 + 1` with `setswitchinterval(1e-6)` -> `gc.isenabled()` False in trial 0.
- Fix sketch: guard enter/exit with a `threading.Lock` (state = depth + saved flag, decided under the lock), or make the pause per-thread and skip it when more than one thread holds it; or drop the global toggle and document `gc.freeze()`/thresholds for applications. Add a thread stress test to `python/tests/test_gc_pause.py`.
- Conformance gap: not a language case; needs a python/tests thread test asserting `gc.isenabled()` afterwards.

### PY-C3 [high] [confirmed; partly re-verified] Catastrophic regex backtracking: a 40-character subject hangs the process; no step or time bound
- Found by: s4 C1.
- Where: `python/sel/builtins/regex.py:339-346` (`re.compile`), `:357` (`rx.search`); spec has no regex work limit (SPEC 7.8, 6.4).
- What: the portable subset allows nested quantifiers and overlapping alternation (`(a+)+`, `(a|aa)+`, `(a|b|ab)*`). Python `re` has no step limit, so a rule with a pattern or subject taken from data runs for exponential time. JS (irregexp) and Lisp (cl-ppcre) have it too; PHP finished `(a+)+$` in 0.4 s (PCRE auto-possessifies and has a backtrack limit), so the hosts do not agree on whether such a rule terminates.
- Repro (Python): `python3 -m sel -e "RMATCH('^(a+)+\$', REPEAT('a', 28) & '!')"` gave FALSE after 32 s (JS 40 s, PHP 0.38 s). `RMATCH('(a|aa)+$', REPEAT('a', 40) & 'b')` did not finish in 60 s (killed by `timeout`). `RMATCH('(a|b|ab)*c', REPEAT('ab', 20))` took 1.15 s. Each doubling of the subject roughly squares the time. Synthesizer ran the same pattern with `REPEAT('a', 22)`: FALSE in 0.9 s (consistent with exponential growth; the 28 and 40 cases were not re-run to avoid multi-minute jobs).
- Fix sketch: spec-level decision first. (a) Reject a quantified group whose body can match non-empty text in two ways sharing a quantifier (static ambiguity check in `validate()`, identical in all hosts), or (b) give every host a step budget and a new catalogued error. Python cannot bound `re` steps without thread/signal or a hand-written matcher; a Thompson NFA is possible because the subset has no backreferences or lookaround, and it would also remove PY-C4.
- Conformance gap: none. Suggest `re.dos.nested-plus`, `re.dos.overlapping-alt` (must return or raise within a bound).

### PY-C4 [high] [confirmed; partly re-verified] The "portable" regex subset is not portable for quantified groups: PCRE-style hosts and ECMAScript-style hosts return different matches and captures
- Found by: s4 C2.
- Where: `python/sel/builtins/regex.py:94-160` (`validate` accepts any quantified group); SPEC 7.8 ("subset every host's engine agrees on").
- What: two independent semantic differences survive the whitelist. (1) Empty iterations: ECMAScript refuses an empty iteration and backtracks into a non-empty alternative; PCRE-style engines (Python, PHP, cl-ppcre) accept one more empty iteration and stop. (2) Capture reset: ECMAScript clears captures inside a quantified group at each iteration start; PCRE-style engines keep the previous iteration's value. Python sits on the PCRE side with PHP and Lisp; JS and C++ (SRELL) on the other.
- Repro (py / js / php / cpp; Lisp agrees with py where checked):
  - `RGROUPS('(|a)+', 'aa')`: py `{"","" }`, js/cpp `{"aa","a"}`, php `{"",""}`, lisp `{"",""}`.
  - `RREPLACE('(a*?)+', '<$1>', 'aaa')`: py `<>a<>a<>a<>`, js/cpp `<a><>`, php `<><><><><><><>`, lisp `<>a<>a<>a<>` (three different answers; PHP also differs from Python).
  - `RGROUPS('(a*)*', 'aa')`: py/php group 2 `""`, js/cpp `"aa"`. Likewise `(a?)*`, `(a?)+`, `(a*)+`, `(a|)+b`, `(a*)*b`, `(a+|b*)*`, `(a|b*)*c`.
  - `RGROUPS('(?:(a)|(b))*', 'ab')`: py/php group 2 `"a"`, js/cpp `""`. Same for `((a)|(b))*`, `(?:(a)|b){2}` on `ab`, `(?:(a)|(b))+` on `ba`.
  - Synthesizer: `RGROUPS('(|a)+', 'aa')` py `-{"1"=t"", "2"=t""}`, js `-{"1"=t"aa", "2"=t"a"}`.
- Fix sketch: spec decision then a shared `validate()` change. Least invasive: reject (E_REGEX_SYNTAX) a quantified group whose body can match the empty string, and reject capture groups nested inside a quantified group when an alternation is involved (or all captures inside a quantified group). Alternatively define ECMAScript semantics and implement a small matcher for the subset.
- Conformance gap: none. `09-regex.selt` has no quantified capture groups. Suggest `re.groups.empty-iteration`, `re.groups.capture-reset`, `re.replace.empty-iteration`.

### PY-C5 [high] [confirmed; re-verified by synthesizer] SQL: `BUCKET ... SUM(g, body)` crashes with a host `TypeError` whenever the body contains a literal
- Found by: s6 C1.
- Where: `python/sel/sql/translator.py:821` (`f"COALESCE(SUM({''.join(inner.parts)}), 0)"`), inside `_call`'s GROUP branch (809-821).
- What: `Fragment.parts` alternates SQL strings and int parameter-slot indexes (`fragment.py:21-26`). Every literal in the body is an int slot, so `''.join(inner.parts)` raises `TypeError`, not a `SqlError`, and `Sql.try_translate` (documented to swallow ordinary refusals) propagates it. Even without the raise, flattening would discard the slot so the value would be unbound. `sql/errors.md`: only `SqlError` is expected out of translate.
- Repro: `R = Binding.relation('items', alias='i', fields={'cat': column('cat',type='TEXT'), 'qty': column('qty',type='NUM')})`; `Sql.translate(compile('R .> BUCKET(_["cat"], RECORD("k", _K, "s", SUM(_, _["qty"] * 2)))'), 'mariadb', {'R': R})` -> `TypeError: sequence item 1: expected str instance, int found` (same on postgresql, sqlite). Without the `* 2` it works (`COALESCE(SUM(`qty`), 0) AS `s``). Synthesizer reproduced the TypeError on mariadb.
- Fix sketch: build `Fragment(['COALESCE(SUM(', *inner.parts, '), 0)'], 'NUM', dialect, inner.params, inner.param_kinds, inner.caveats)` (or go through a skeleton/`fill`). Cross-host: `php/src/Sql/Translator.php:708` (`implode('', $inner->parts)`) and `js/src/sql/translator.mjs:890` (`inner.parts.join('')`) use the same idiom; there an int part is stringified, so s6 expects they silently emit the slot NUMBER as SQL text (`SUM(qty * 3)`), "worse than a crash". **UNCONFIRMED for PHP/JS (not run).**
- Conformance gap: none; every group-SUM case in `sql/cases/24-bucket.sqlt` uses a bare field. Suggest bucket SUM with `_["amount"] * 2` and `_["amount"] + 1`, including params mode (bindings() count must equal placeholders).

### PY-C6 [high] [confirmed] SQL: exponential output from a linear program (helper reuse is inlined as a tree)
- Found by: s6 C2.
- Where: `python/sel/sql/normalise.py:91,109` (`defs[name] = value`; `_substitute` returns the SAME node object for every read) plus `translator.py:222-269` (`_node` renders it as a tree).
- What: stage 1 inlines a helper by node reference, so `V1 = V0 + V0; V2 = V1 + V1; ...` is a DAG of n nodes whose rendering is a tree of 2^n nodes. `E_SQL_DEPTH` does not help because depth stays at n (<= 199). SEL evaluates the same program in linear time (V40 with V0=1 in 12 ms), so "a rule that translates is a rule that evaluates" fails for cost. `constants.is_constant`/`validate` walk the same DAG as a tree, so constant-only versions are exponential in validation too.
- Repro: `C` = NUM column, program `V0 = C + 1; V1 = V0 + V0; ...; Vn = V(n-1) + V(n-1); Vn > 0`: n=12: 0.31 s / 57 KB; n=14: 1.25 s; n=16: 5.1 s / 917 KB / 65,537 params; n=18: 21.7 s / 3.6 MB / 262,145 params; n=30 practically non-terminating; n=40 exhausts memory. Synthesizer re-ran n=12 (mariadb): 0.5 s, 57,345 characters of output (same order; box loaded).
- Fix sketch: bound the rendered node count / parameter count (new E_SQL_* or an E_SQL_DEPTH-style refusal, specified so every host agrees), or refuse a helper read more than once when its right-hand side is non-trivial. Memoising the rendered Fragment would not help (slot numbers are absolute per occurrence).
- Conformance gap: none. Suggest a 25-line doubling chain expecting a refusal.

## Medium

### PY-C7 [medium] [confirmed; re-verified by synthesizer] The math plan snapshots operands at load time, so a later sub-expression's mutation is not seen (SPEC 3.4)
- Found by: s2 C1.
- Where: `python/sel/eval.py:176-189` (`LOAD_VAR`/`LOAD_LEAF` store `as_decimal()` immediately), planned by `python/sel/math_plan.py:96-99, 227-231`.
- What: SPEC 3.4: "a mutation performed by a later sub-expression is visible through a reference obtained earlier". The tree-walker (`_eval_binary`) evaluates both operands to Values, then reads the scalar, so it sees the mutation; the plan reads the left operand's scalar before the right operand runs. The derailing list in `emit` (assign/seq/list/IF/COND) only protects against a literal assignment operand; any call containing an assignment (`LEN((A[1] = 10))`) is a `LOAD_LEAF`. All five hosts print 3: a design-level cross-host divergence from the spec, not Python-only.
- Repro: `A = LIST(1,2); A + LEN((A[1] = 10))` -> optimised (`Program.run`, every host's CLI) `3`; plain tree walk (`eval_node(program.ast, ...)`) `12`. Spec-consistent answer: 12 (compare `A = LIST(1,2); A == (A[1] = 10)` which is TRUE everywhere). Same with `A * LEN(A[1] = 10)` (2 vs 20) and `MAX(A, LEN(A[1] = 10))` (2 vs 10). Synthesizer: optimised path `3` confirmed; the plain-walk `12` was not re-run.
- Fix sketch: keep `Value` handles in the scratchpad and coerce at the operator (one cached `as_decimal` per operand at op time), or refuse to plan when a non-leaf-safe operand appears to the right of another operand; decide the ordering in the spec.
- Conformance gap: none. Suggest `alias.arith-operand-sees-later-mutation`: `A = LIST(1,2); A + LEN((A[1] = 10))` -> `num 12`.

### PY-C8 [medium] [confirmed] Which error wins in `X + Y` depends on plan vs tree-walk (and on an unrelated dead branch pushing the tree past depth 200)
- Found by: s3 C5, s2 C2.
- Where: `python/sel/eval.py:176-227` (`_eval_math_plan`: plan loads coerce at load time, before the right operand is evaluated) vs `:367-392` (`_eval_binary`: evaluate both operands, then coerce left, then right); `optimizer.py:633-637` only plans `physical` trees, never assignment-target index expressions; `optimizer.py:673` (`exceeds_depth` turns the whole optimiser off).
- What: `A + (1/0)` with `A = "x"` is E_NOT_NUM at the left operand when planned but E_DIV_ZERO when walked. The walker runs (i) compound assignments, (ii) assignment-target index expressions, (iii) any tree reaching the depth cap, (iv) numerals the plan compiler refuses. Comparisons, `&`, `XOR` use the walker even when planned (`A = "x"; A < B` -> E_UNDEF_VAR), so `+` and `<` order coercion differently. SPEC 6.2/6.3 does not say whether a bad left operand or a failing right operand fails first; all five hosts implement the plan reading for `+ - * / %`, so the walker is the odd one out in every host. Contradicts the principle of 6.4 ("nothing that runs before the evaluator may change that answer"). ~1.8% of random arithmetic expressions differ, always and only in which error/position wins; values were identical over 30,000 programs.
- Repro: `A="x"; A + (1/0)` -> E_NOT_NUM@1:8 in all five hosts; `X = "x"; A[X + (1/0)] = 1` -> E_DIV_ZERO@1:18 in all five hosts; `X="x"; A = X + (1/0)` -> E_NOT_NUM@1:13. Dead-code sensitivity (all five hosts): `A="x"; IF(TRUE, A + (1/0), 1)` -> E_NOT_NUM@1:17 but `A="x"; IF(TRUE, A + (1/0), <260-term 1+1+... chain>)` -> E_DIV_ZERO@1:23. Same code, different position: `Z * ((MIN(A, Z) + -0) * J)` with `Z = ""`: walker E_NOT_NUM@1:15, plan E_NOT_NUM@1:2. s2: `X="a"; IF(TRUE, X + Y, 1)` -> `E_NOT_NUM at 1:17`; with the 260-term dead chain -> `E_UNDEF_VAR at 1:21` (py, js, php, cpp, lisp identical); same for `ROUND(X, Y)`, `MAX(TRUE, U, 0)`.
- Fix sketch: choose one rule in the spec (s3 recommends: for `+ - * / %` the left operand is coerced before the right operand is evaluated) and make `_eval_binary` and the compound-assignment path do the same; or make plan loads defer coercion until both operands are evaluated (s2's preference: "operands are evaluated, then coerced left to right", which is what the walker and every non-arithmetic operator do).
- Conformance gap: none. Suggest `A = "x"; A + (1/0)`, `A = "x"; A += (1/0)`, `X = "x"; A[X + (1/0)] = 1`, plus a shallow / dead-deep-branch pair so both paths are held to one answer.

### PY-C9 [medium] [confirmed; partly re-verified] Optimiser SORT/SORT_DESC/SORT_BY + TAKE -> TOP/TOP_DESC/TOP_BY fusion changes the value and the error (all five hosts)
- Found by: s3 C3.
- Where: `python/sel/optimizer.py:473-484` (`second.name == 'TAKE'`; only `len(second.args) == 2` is checked).
- What: `TAKE(SORT_BY(x, key), n)` evaluates every `key`, then `n`. The fused `TOP_BY(x, key, n)` evaluates and validates `n` first. Any `n` that is not a valid literal reorders effects and errors: an error in `n` (E_RANGE, E_NOT_NUM, E_NOT_INT, E_NULL, E_DIV_ZERO) beats an error in a key, and a side effect in `n` (an assignment) now happens before the keys are computed. The neighbouring TAKE+TAKE and DROP+DROP merges are restricted to non-negative integer literals (`numeric_literal`); this one is not. SPEC 7.3 only says TOP is "the first n of SORT_BY(...), as one step".
- Repro (value): `A = 1; L = LIST(1,2,3); L .> SORT_BY(_ * A) .> TAKE((A = -1; 2))` -> unoptimised (`eval_node(program.ast, ...)`) `[1, 2]`; optimised program and cpp/lisp/js/php give `[3, 2]` (all five agree on the fused answer). Repro (error): `T .> SORT_BY(_["z"]) .> TAKE(-1)` over records: as written E_NO_KEY@1:15 (the key), optimised E_RANGE@1:30; `TAKE("x")` -> E_NOT_NUM, `TAKE(1.5)` -> E_NOT_INT, `SORT_DESC(_["z"]) .> TAKE(NULL)` -> E_NULL, `SORT_BY(_["a"]/_["c"]) .> TAKE(1/0)` -> position 1:21 vs 1:40. Synthesizer: optimised value `[3, 2]` confirmed; `LIST(1,2) .> SORT_BY(1/0) .> TAKE(-1)` returned `E_RANGE at line 1 column 35: TOP_BY argument 3 must not be negative` (the reasoned unfused answer is E_DIV_ZERO); the unoptimised side was not re-run.
- Fix sketch: fuse only when `numeric_literal(second.args[1]) is not None` (a valid literal cannot raise or have effects). Same change in the other four hosts; spec first: add the boundary to the TOP/TAKE paragraph of 7.3.
- Conformance gap: none. Suggest in `15-relational.selt` next to `rel.optimiser.sort-error-before-a-filter-then-take`: `LIST(1,2) .> SORT_BY(1/0) .> TAKE(-1)` expecting E_DIV_ZERO, and the assignment-in-n case expecting `[1, 2]`.

### PY-C10 [medium] [confirmed; re-verified by synthesizer] Mutating a record from inside an aggregate that is iterating it crashes with a host `TypeError` (JS and PHP also crash; C++/Lisp answer)
- Found by: s3 C4.
- Where: `python/sel/eval.py:538` (`_walk_create(...).set(key, value)`) -> `python/sel/value.py:509-516` (`set` nulls `shape`/`storage` of a shaped record) while `value.py:193-195` (`iter_elements`) is still yielding `value.storage[index]`.
- What: an assignment in an aggregate body that adds a key to the very record being iterated converts it from shaped to dict storage mid-iteration; the generator then indexes `None`. Uncaught `TypeError`, not a SEL value or SelError. The correct behaviour is unspecified, but C++ and Lisp complete with snapshot semantics (they sum the original two elements), and a host exception is never allowed.
- Repro: `python3 -m sel -e 'R = RECORD("a",1,"b",2); SUM(R, x, (R["c"] = 10; x))'` -> `TypeError: 'NoneType' object is not subscriptable`; cpp/lisp print 3; node: `TypeError: Cannot read properties of null (reading 'length')` from `aggregate.mjs`; php: `Warning: Trying to access array offset on null` then a wrong error. Also `MAP(R, x, (R["c"] = x; x))`, `FILTER`, `SORT_BY`, LINK bodies. Adding a key to a list being iterated does not crash in Python.
- Fix sketch: pin the semantics in the spec (iterate a snapshot of the elements that existed when the aggregate started, which is what C++/Lisp do) and have the aggregates materialise `list(iter_elements(v))` before running the body, or make `Value.set` on a shaped record copy `storage` rather than null it in place.
- Conformance gap: none. Suggest `R = RECORD("a",1,"b",2); SUM(R, x, (R["c"] = 10; x))` expecting 3, and the same via MAP/FILTER.

### PY-C11 [medium] [confirmed; re-verified by synthesizer] `SELECT_COLS` with a repeated column name builds a record with duplicate keys (fast path)
- Found by: s5 C1.
- Where: `python/sel/builtins/structure.py:96-105` (`_record_shape(tuple(columns))` at :103).
- What: when every row shares the first row's shape and has every requested column, the fast path builds the output shape straight from `columns` without a uniqueness check (the sibling `_unique_record_shape` does). `INDEXES` lists the key twice, `COUNT` is inflated, the dump prints the key twice, and a later `R[1]["a"] = 9` leaves two `a` entries (shape `key_map` points at the last slot while iteration shows both). The slow path (differing shapes / missing column) merges duplicates correctly, so the result depends on whether the data happens to be uniform. SPEC 3.3/7.4: record keys are unique, a repeated key keeps its first position.
- Repro: `PYTHONPATH=$PWD/python python3 -m sel -e 'R = SELECT_COLS(LIST(RECORD("a",1,"b",2)), "a", "a", "b"); LIST(COUNT(R[1]), R[1])'` -> py `{"1"=t"3", "2"={"a"=t"1","a"=t"1","b"=t"2"}}`; js/php/lisp/cpp/go -> COUNT 2 and `{"a"=1,"b"=2}`. Also `SELECT_COLS(..., "b","a","b") .> MAP(INDEXES(_) .> JOIN(","))`: py `b,a,b`, others `b,a`.
- Fix sketch: dedupe `columns` (first occurrence wins, order kept) before the fast path, or use `_unique_record_shape` and fall to the slow path when it returns None.
- Conformance gap: none. Suggest `SELECT_COLS(LIST(RECORD("a",1,"b",2)), "a", "b", "a")` expecting one `a` and `b`, for uniform and non-uniform row shapes, in `15-relational.selt`.

### PY-C12 [medium] [confirmed; re-verified by synthesizer] Equi-join numeric key fast path skips the digit cap: `LINK` joins where `==` raises E_RANGE
- Found by: s5 C2 (medium), s2 C4 (low-medium).
- Where: `python/sel/builtins/structure.py:211-218` (`canonical_join_key`: `int(text)` on the raw scalar when it is all ASCII digits, bypassing `D.parse` and its `MAX_INT_DIGITS` check).
- What: SPEC 6.4: "A numeral too long to hold is E_RANGE wherever it appears ... or as text that arithmetic reads." `int()` succeeds up to the 2,000,000-digit limit that `decimal.py` sets in `sys.set_int_max_str_digits`; above 2M digits `int` raises ValueError, which the `except Exception: pass` swallows and the slow path then raises correctly, so the behaviour flips at 2M digits. The same operands through the nested-loop path or plain `==` raise E_RANGE, so the outcome depends on the physical join chosen. Also costs about 1.5 s of `int()` conversion on a 1,000,005-digit string. PHP and Lisp share the bug (PHP per s2, PHP and Lisp per s5's probe printing 0); JS, C++, Go raise.
- Repro: s5: `BIG = PADL("9", 1000005, "9"); LINK(LIST(RECORD("a", "1")), LIST(RECORD("b", BIG)), _1["a"] == _2["b"]) .> COUNT()` -> py `0`; js, cpp, go `E_RANGE at line 1 column 98: number has more than 1000000 integer digits`; with `AND TRUE` appended (nested loop) every host incl. py raises E_RANGE. s2: `O = LIST(RECORD("k", REPEAT("9", 1500000))); C = LIST(RECORD("k", REPEAT("9", 1500000))); O .> LINK(C, _1["k"] == _2["k"]) .> LEN()` -> python `1500000` (a match), php `1500000`, js `E_RANGE at 2:16`, cpp `E_RANGE at 2:16`; `REPEAT("9",1500000) == REPEAT("9",1500000)` is `E_RANGE` on all four (Lisp not run). Synthesizer re-ran the s5 form: py `0` (1.9 s), js `E_RANGE at line 1 column 98: number has more than 1000000 integer digits`.
- Fix sketch: take the `int()` shortcut only for short texts (e.g. `len(text) <= 300`, or `< 4300`), or check `len(text.lstrip('0')) > MAX_INT_DIGITS`; otherwise fall through to `value.as_decimal()`.
- Conformance gap: none (`10-limits.selt` has no join-key case). Suggest `rel.link.equi-key-over-digit-cap` (needs a generated 1,000,001-digit text, e.g. via PADL) expecting E_RANGE.

### PY-C13 [medium] [confirmed] The SORT comparator is not a total order on mixed numeric-looking / non-numeric TEXT, so results depend on the sort algorithm (across hosts, and inside Python on SORT+TAKE fusion into TOP)
- Found by: s5 C3.
- Where: `python/sel/builtins/aggregate.py:324-362` (`compare_values`), `:423` (`list.sort`), `:542` (TOP heap).
- What: two TEXT values compare numerically when both parse as numbers and bytewise otherwise, so `"2" < "10"` (numeric), `"10" < "1a"` (bytes), `"1a" < "2"` (bytes): a cycle. Timsort, Lisp/C++/Go sorts and the TOP heap then give different, algorithm-defined answers. SPEC 7.3 only says "sorted ascending"; `docs/functions.md` does not say how a number meets a non-number text. Consequences: host divergence, and inside Python `TAKE(SORT(X), 4)` (fused into `TOP`) differs from `S = SORT(X); TAKE(S, 4)` for the same data (269 of 300 random shuffles of `"10","9","9a","2","1a","100","b","a","20","3x"` differ), contradicting "physical tree is a function of the AST alone" and SPEC 7.4's "TOP is SORT+TAKE as one step".
- Repro: `("10","1a","2","9","9a","100") .> SORT() .> JOIN(",")` -> py/js/php/go `10,1a,2,9,100,9a`; lisp `9,10,100,1a,2,9a`; cpp `2,9,10,100,1a,9a`. Python fusion: `X = LIST("9a","2","9","10","20","a","b","100","1a","3x"); LIST(JOIN(TAKE(SORT(X),4),","), (S = SORT(X); JOIN(TAKE(S,4),",")))` -> `10,1a,2,3x` vs `2,9,10,20`.
- Fix sketch: spec decision first: define a total order (e.g. rank NULL < BOOL < number-looking < other TEXT < BIN < other, compare within rank; or "number-looking TEXT always before non-number TEXT"), add mixed-list `.selt` cases, then change all hosts. It also enables the big speedup in PY-P1.
- Conformance gap: none for mixed numeric/non-numeric text (06-aggregates / 15-relational sort homogeneous keys only). Suggest `agg.sort.mixed-number-and-text-keys` with the list above.

### PY-C14 [medium] [confirmed; re-verified by synthesizer] `REPEAT` / `PADL` / `PADR` have no size cap: raw `MemoryError` / `OverflowError`, hang, memory DoS
- Found by: s4 C3 (medium), s3 C8 (low).
- Where: `python/sel/builtins/text.py:117` (`REPEAT`), `:127-137` (`_pad`); `Args.int`/`non_neg_int` in `eval.py:132-144` (`D.to_safe_int`, unbounded despite the name, `decimal.py:237`).
- What: SPEC 6.4 caps only ROUND scale, POWER exponent and regex quantifiers; there is no text-size cap. `a.text(0) * n` raises a raw `MemoryError` (10^12) or `OverflowError`; neither is a `SelError`. `_pad` builds `''.join(genexpr over range(need))`, allocating `need` one-character strings first. `LEN(REPEAT("a", 3000000000))` allocates 3 GB and answers 3000000000. Cross-host difference: `REPEAT("", 10^30)` is defined and empty (0 in JS and PHP) but raises `OverflowError` in Python. JS (`RangeError: Invalid string length`) and PHP (fatal memory error) have the same class of problem.
- Repro: `python3 -m sel -e 'REPEAT("a", 1000000000000)'` -> `MemoryError` traceback. `python3 -m sel -e 'LEN(REPEAT("", 1000000000000000000000000000000))'` -> `OverflowError`; JS and PHP print `0`. `python3 -m sel -e 'LEN(PADL("a",100000000000,"x"))'` -> minutes of CPU, then MemoryError (57 s at 100% CPU before MemoryError under a 3 GB ulimit). s3: `REPEAT("", 1000000000000000000000000000000)` -> OverflowError; `REPEAT("ab", 1000000000000)` -> MemoryError (under `ulimit -v`). Synthesizer re-ran `REPEAT("", 10^30)`: `OverflowError: cannot fit 'int' into an index-sized integer`.
- Fix sketch: short term: return `''` when the text is empty or n is 0; catch `MemoryError`/`OverflowError` around REPEAT/PADL/PADR and raise `E_RANGE` at the count argument. Properly: add a text-length cap to SPEC 6.4 / `spec/limits.json` (like the digit caps) with an ordinary E_RANGE, and check `len(s) * n` / `width` before allocating; build the pad as `(fill * (need // len(fill) + 1))[:need]` (also PY-P11).
- Conformance gap: none. Suggest `text.repeat.huge-count` (E_RANGE or the cap's code) and `text.repeat.empty-huge-count` (`""`).

### PY-C15 [medium] [confirmed] Deeply nested groups in a regex pattern crash with an uncaught `RecursionError`
- Found by: s4 C4.
- Where: `python/sel/builtins/regex.py:343-345` (`except re.error` only).
- What: `re.compile` parses nested groups recursively; between 400 and 1000 nested parentheses raises `RecursionError`, not caught as `re.error`, instead of `E_REGEX_SYNTAX`/`E_DEPTH`. Nesting is not counted anywhere. Other hosts: JS and Lisp accept 1000 levels; PHP and C++ reject with E_REGEX_SYNTAX, so the answer differs by host too.
- Repro: `python3 -c "import sel; p=sel.compile(\"RMATCH('\"+'('*1000+'a'+')'*1000+\"', 'a')\"); p.run(sel.Value.from_native({}))"` -> `RecursionError: maximum recursion depth exceeded`. 400 levels: TRUE.
- Fix sketch: count group nesting in the shared `validate()` and raise `E_REGEX_SYNTAX` (or `E_DEPTH`) beyond a fixed cap such as 200, in all hosts; catch `RecursionError` around `re.compile` as belt-and-braces.
- Conformance gap: none. Suggest `re.depth.nested-groups-cap` at and one past the cap.

### PY-C16 [medium] [confirmed; re-verified by synthesizer] `SelError` cannot be pickled or copied: one error kills a `ProcessPoolExecutor`
- Found by: s1 C4.
- Where: `python/sel/errors.py:18-35`.
- What: `SelError.__init__(code, message, pos)` calls `super().__init__(message)`, so `e.args == (message,)`; pickle/copy rebuild via `cls(*args)`, supplying one positional arg to a constructor requiring two. `pickle.loads(pickle.dumps(e))`, `copy.copy(e)`, `copy.deepcopy(e)` raise `TypeError: SelError.__init__() missing 1 required positional argument: 'message'`. In a worker the failure happens while returning the result, so the pool is declared broken instead of delivering the validation error.
- Repro: script with `ProcessPoolExecutor(1)` submitting `lambda s: sel.compile(s)` on `'1 +'` -> `BrokenProcessPool: A process in the process pool was terminated abruptly`. Also `import pickle, sel; try: sel.compile('1 +') except sel.SelError as e: pickle.dumps(e); pickle.loads(_)` -> TypeError. Synthesizer re-ran the pickle form: `TypeError: SelError.__init__() missing 1 required positional argument: 'message'`.
- Fix sketch: add `def __reduce__(self): return (SelError, (self.code, self.message, Pos(self.line, self.col, self.offset)))` (or pass all fields to `super().__init__`).
- Conformance gap: not a language case; add to python/tests (pickle round-trip keeps code/line/col/offset).

### PY-C17 [medium] [confirmed; re-verified by synthesizer] CLI file/REPL input: universal-newline read changes the program, invalid UTF-8 crashes with `UnicodeDecodeError` instead of `E_UTF8`
- Found by: s3 C6 (medium), s1 C5 (low).
- Where: `python/sel/_cli.py:53` (`open(args[0], encoding='utf-8')`, only OSError caught), `:75` (`input()`).
- What: text-mode `open` translates `\r\n` and lone `\r` to `\n`: a CRLF inside a string literal is one character shorter, and line numbers in errors are computed on translated text (lone CR line endings count as newlines). The other hosts read bytes untouched. Invalid UTF-8 in the file (or on REPL stdin) raises an uncaught `UnicodeDecodeError`, where cpp answers `E_UTF8 at line 0 column 0: invalid start byte 0xff at byte 4` and php also prints `E_UTF8`; JS/PHP-per-s1 silently substitute U+FFFD (s1 says JS/PHP mangle to an unrelated E_SYNTAX: their bug). The `-e` path already maps bad bytes to E_UTF8 (`python3 -m sel -e $'"a\xffb"'`), so file mode is inconsistent. Python has the strict decoder (`utf8.decode_utf8`) but the source path does not use it; the API takes `str`, so `compile()` cannot be handed bytes.
- Repro: `printf 'A = "a\r\nb";\nLEN(A)' > x.sel; python3 -m sel x.sel` -> 3 (cpp/lisp/js/php: 4). `printf 'A = 1 # c\r+ 2\r\nA' > y.sel` -> `E_SYNTAX at line 3 column 1` (all other hosts: line 2). `printf '"a\xffb"' > z.sel; python3 -m sel z.sel` -> `UnicodeDecodeError` traceback (cpp/php: `E_UTF8`). `printf '1 + \xff' > x.sel; PYTHONPATH=$PWD/python python3 -m sel x.sel` (s1). Synthesizer re-ran the CRLF case: `3`.
- Fix sketch: `open(path, 'rb')` and decode with the project's UTF-8 codec (or `newline=''` plus `SelError('E_UTF8')` on `UnicodeDecodeError`); REPL read `sys.stdin.buffer` and wrap decode errors the same way; consider accepting `bytes` in `compile`. A UTF-8 BOM reaches the lexer as U+FEFF and is E_SYNTAX in every host (consistent).
- Conformance gap: none (CLI is not covered by conformance; `tools/e2e.sh` or `check-api` could carry a CRLF-in-literal probe).

### PY-C18 [medium] [confirmed] SQL: name-based (unhygienic) substitution and element re-entry produce wrong SQL or a spurious `E_SQL_DEPTH`
- Found by: s6 C4.
- Where: `python/sel/sql/normalise.py:163-166` (helper values substituted by name; a free variable in the helper's value can be captured by a use-site binder) and `translator.py:1178-1180 / 1461-1462 / _with_element 1498-1512` (a static list's element is rendered by `_node(b.payload)` inside the frame that binds the inner binder and `_K`, not the scope where the list was written).
- What: `sql-translation.md` says capture happens at the assignment and "binders shadow ... exactly as spec 7.3". Two violations produce silent wrong answers, breaking the warrant of `sql-kinds.md` section 1: (a) helper capture: `A = C; ANY((10,20), C, A == C)` with C a bound column and the binder also called C: SEL evaluates A as the column value, the translator substitutes `var C` for A, the binder then captures it, giving `((10 = 10) OR (20 = 20))`, a constant TRUE; SEL with C=5 answers FALSE (`compile(src).run({'C': 5})`). (b) element scope: `ANY((10,20), x, ANY((_K, "9"), y, y $== "2"))`: the inner list's `_K` is the OUTER key ("2" on the second outer element) so SEL answers TRUE, but the element is rendered under the inner frame where `_K` is the inner element's key, giving `'1'='2' OR '9'='2'` twice, constant FALSE. (c) the same mechanism with the default binder gives a bogus refusal: `ANY((C,D), ALL((_, 5), _ > 6))` and `ALL((C,D), x, ALL((x,7), x, x > 1))` return `E_SQL_DEPTH` (the element `_` resolves to the frame binder whose payload is `_` again, unbounded self-recursion stopped by the depth guard); SEL evaluates both fine (FALSE).
- Repro: see (a)-(c) (harness `py6/h.py` `tr`/`ev`); not re-run by the synthesizer.
- Fix sketch: render a static list's elements against the frame stack that existed when the list was resolved (store the frame depth in the `Binder.NODE` payload and evaluate `_node` with `self.frames` truncated to it), and make stage 1 refuse (or alpha-rename) a helper whose free variables are also binder names at a use site.
- Conformance gap: none. Suggest `agg.scope.outer-key-in-inner-list`, `norm.inline.binder-does-not-capture-helper`.

### PY-C19 [medium] [confirmed] SQL: `JOIN` over a static list accepts BOOL and BIN elements/separators that SEL rejects with `E_NOT_TEXT`
- Found by: s6 C5.
- Where: `python/sel/sql/translator.py:1744-1755` (`_join_aggregate`: `fold_pairwise('&', ...)` calls `_apply` directly, skipping the `_require_not_bool_operand` / kind guards that `_binary` applies to `&`, 626-628).
- What: SPEC 5.2 ends "BOOL is E_NOT_TEXT", and BIN is E_NOT_TEXT for text functions. The translator refuses `T & F` but not the same concatenation spelled as JOIN, so a row SEL refuses can be selected. Result kind also becomes BIN when any element is BIN.
- Repro: columns T TEXT, F BOOL, X BIN. mariadb `JOIN((T,F), ",")` -> ``CONCAT(CONCAT(`t`, ','), `f`)``; `JOIN((T,T), F)` and `JOIN((F,F2), ",")` likewise; `JOIN((X,T), ",")` -> kind BIN. postgresql: `(CAST("t" AS TEXT) || ...CAST("f" AS TEXT))` (yields 'true'/'false'). SEL: `JOIN(("a", TRUE), ",")` -> E_NOT_TEXT; `JOIN(("a","b"), TRUE)` -> E_NOT_TEXT; `JOIN((TO_UTF8("a"),"b"), ",")` -> E_NOT_TEXT.
- Fix sketch: in `_join_aggregate`, run each element fragment and the separator through the same kind guards `&`/text functions use.
- Conformance gap: none (`sql/cases/12-aggregates.sqlt` covers text only). Suggest `agg.join.bool-element-refused`, `agg.join.bin-separator-refused`.

### PY-C20 [medium] [confirmed; server behaviour reasoned] SQL: `x IN (list)` with an `exact` TEXT needle skips the byte cast on every list item, so MariaDB/MySQL/SQLite compare numerically
- Found by: s6 C6.
- Where: `python/sel/sql/translator.py:774-785` (`is_exact = getattr(raw, 'exact')`; `item = f if is_exact else text_operand(f)`).
- What: `_binary` skips the cast only when the other operand is also exact or a TEXT literal (646-651). The IN unrolling skips it whenever the NEEDLE is exact, whatever the item is (number literal, NUM column, ordinary TEXT column). The item keeps its server type and MariaDB/MySQL compare string-to-number numerically; SQLite applies NUMERIC affinity. SEL's IN is EQL-structural: text "3.0" is not the number 3.
- Repro: `T` = `Binding.column('t', type='TEXT', exact=True)`, `C` = NUM column. mariadb `T IN ("a", 3)` -> ``((`t` = 'a') OR (`t` = 3))`` (`'3.0' = 3` and `'3abc' = 3` are true on MariaDB: reasoned, not run); compare `T EQL 3` -> ``(`t` = CAST(3 AS CHAR) COLLATE utf8mb4_nopad_bin)`` which is correct. Real SQLite check: table `x(t TEXT, c NUMERIC)` with row ('3.0', 3): `("t" = 'a') OR ("t" = "c")` returns 1, SEL `"3.0" IN ("a", 3)` is FALSE. PostgreSQL errors loudly (text = integer), acceptable.
- Fix sketch: reuse the `_binary` rule: skip the cast only if the item is exact or a text literal node; otherwise `text_operand(f)`.
- Conformance gap: none. Suggest `op.in.exact-needle-number-item` for mariadb and sqlite.

### PY-C21 [medium] [confirmed; server behaviour reasoned] SQL: `x IN <relation>` never checks that needle and column kinds are comparable
- Found by: s6 C7.
- Where: `python/sel/sql/translator.py:698-728` (relation branch of `_in_operator`); the scalar branch (749) and literal-list branch (782) both call `_require_comparable_kinds`.
- What: a BOOL or BIN needle is cast to characters and compared with a TEXT scalar column, exactly the defect `_require_comparable_kinds` documents (`CAST(FALSE AS CHAR)` is '0', `CAST(TRUE AS CHAR)` is '1'). SEL answers FALSE for every row because the kinds differ.
- Repro: `S = relation('sk', alias='s', fields={'sku': column('sku', type='TEXT')}, scalar='sku')`, F BOOL column, X BIN column: mariadb `F IN S` -> ``((CAST(`f` AS CHAR) COLLATE utf8mb4_nopad_bin IN (SELECT CAST(`sku` AS CHAR) ... FROM `sk` `s` WHERE TRUE)) IS TRUE)``; `TRUE IN S`, `X IN S` translate too (postgresql, sqlite same). A server would select rows with sku '1'/'true' (reasoned). Versus `F IN (T, T)` and `TRUE IN ("1","a")`, which are refused.
- Fix sketch: call `_require_comparable_kinds(needle, self._column_ref(b['fields'][scalar]), 'IN', n.pos)` in the relation branch.
- Conformance gap: none. Suggest `agg.in-relation.bool-needle-refused`.

### PY-C22 [medium] [confirmed] Hybrid: a split after `FILTER` renumbers rows, so the continuation's `_K` and the result keys differ from `run()` (also in JS)
- Found by: s7 C1.
- Where: `python/sel/sql/hybrid.py:807-829` (prefix loop, continuation over `_INPUT`), also `:437-459` (MAP fall-through continuation) and `:730` (latest-member).
- What: SPEC 7.3 "Keys are part of the value. FILTER is the one step that keeps its input's keys ... a `_K` read after a step sees that step's keys." When the SQL prefix contains a `FILTER` (with only key-keeping steps after it), rows return from the database as a fresh list numbered 1..n, so the continuation and the final answer see different keys than `run()`. `sql-translation.md` 12.1 ("The rewrites keep keys") shows the authors care about this for the optimiser but the split point has no guard.
- Repro (`py7/t8.py`, `py7/t9.py`; SQLite; rows id=1..6): `R .> FILTER(_["id"] > 2) .> MAP(RECORD("id", _["id"], "k", _K))` is a hybrid plan; `run()` gives k = 3,4,5,6, `execute_hybrid` gives k = 1,2,3,4. `R .> FILTER(_["id"] > 2) .> FILTER(REPEAT("a", _["id"]) $== "aaaaa")`: `run()` = `{"5": row5}`, hybrid = `{"3": row5}`. Fall-through variant: `R .> FILTER(_["id"] > 2) .> MAP(RECORD("id", _["id"], "x", REPEAT("a", _K)))` gives "aaa.." in run() and "a.." in hybrid. JS `Sql.planHybrid` on the first program yields the same k = 1,2 (`py7/j1.mjs`): a spec-level planner gap.
- Fix sketch: a prefix containing a `FILTER` not followed by a renumbering step (SORT_BY/TAKE/DROP/MAP/BUCKET...) is a valid split point only if the continuation cannot observe keys; otherwise fall back to a shorter prefix (or expose an ordinal, which the design refuses to invent).
- Conformance gap: none. Suggest in `25-hybrid-plans.sqlt`: `FILTER(...) .> MAP(RECORD("k", _K))` must not split after the FILTER (or must be refused), and `FILTER(sql) .> FILTER(unsupported)` must keep original keys.

### PY-C23 [medium] [confirmed] Hybrid: splitting in front of an in-memory `LINK` renames the left side from the relation to `_INPUT` (also in JS)
- Found by: s7 C2.
- Where: `python/sel/sql/hybrid.py:823-824` (`input_node = Node('var', ..., name='_INPUT')`); the 3-arg LINK names its left side after the source variable (SPEC 7.4); `RelationalPlan.root_name` exists but the continuation drops it.
- What: `R .> SORT_BY(id) .> LINK(S, <pred SQL cannot render>) .> ...`: the prefix is `SELECT r.* FROM r ORDER BY id` and the continuation `_INPUT .> LINK(S, ...)`. The joined row holds the left row under `_INPUT`/`_input` instead of `R`/`r`, so a later `_["R"]["id"]` raises E_NO_KEY where `run()` works, and a raw joined row has different keys.
- Repro (`py7/t14.py`): `R .> SORT_BY(_["id"]) .> LINK(S, PADL(_1["cat"], 1, "0") $== _2["scat"]) .> MAP(RECORD("id", _["R"]["id"], "l", _["S"]["label"]))` - run() = `{"1": {"id":"1","l":"L1"}}`; execute_hybrid = `ERR E_NO_KEY`. JS (`py7/j3.mjs`): same `hybrid ERR E_NO_KEY`. Fuzz `py7/fzl.py 1 300` shows the raw-row variant.
- Fix sketch: when the first continuation step is a 3-arg LINK/LINK_LEFT, rewrite to the 5-arg form with the original root variable name (and lower-case alias) as the left binder, or bind an alias variable of that name.
- Conformance gap: none. Suggest a `--- plan` case with an unrenderable LINK predicate after a splittable prefix whose MAP reads `_["ORDERS"]`.

### PY-C24 [medium] [confirmed] Hybrid: `_inline_literals` mis-scopes binders of the 4- and 5-argument forms, corrupting the continuation (also in JS)
- Found by: s7 C3.
- Where: `python/sel/sql/hybrid.py:551-566`. Only `len(node.args) == 3` is treated as "binder in args[1]"; `SORT_BY/BUCKET(list, binder, key, x)` (4 args) and `TOP_BY(list, binder, key, dir, n)` / `LINK(l, r, L, R, pred)` (5 args) have binders elsewhere.
- What: with a literal helper (`N = 5`) named like such a binder, the binder is not shadowed in the body and the binder token itself (args[1]) is replaced by the literal, giving `SORT_BY(5, 5["id"], ...)`, which raises E_EXPECT_SYMBOL at execute time; when nothing can be pushed the plan silently degrades to pure memory. Contradicts "a binder shadows a same-named helper inside its body". Conversely the 3-arg branch treats any bare var in args[1] (e.g. `LINK(l, RIGHT, pred)`, `SORT_BY(list, KEYVAR, "DESC")`) as a binder, which only suppresses inlining (harmless).
- Repro (`py7/t2.py`, SQLite): `N = 5; R .> FILTER(_["id"]>1) .> SORT_BY(N, N["id"], "DESC") .> MAP(RECORD("id", _["id"]))` - run() = 5 rows; execute_hybrid = `ERR E_EXPECT_SYMBOL`. Same for `BUCKET(N, N["cat"], RECORD(...))` and `TOP_BY(N, N["id"], "DESC", 2)`. With a binder name that is not a helper (`o`) the same programs are pure_sql and correct. JS (`py7/j1.mjs`): `ERR E_EXPECT_SYMBOL`.
- Fix sketch: derive binder names from the function's actual shape (the evaluator's `shape()`/`do_sort`/`do_bucket`/LINK rules, or the helper stage 1 uses) and never inline into a binder-name position.
- Conformance gap: none. Suggest cases for a literal helper named like the binder for SORT_BY(4), BUCKET(4), TOP_BY(5), LINK(5).

## Low-medium

### PY-C25 [low-medium] [confirmed; re-verified by synthesizer] `import sel` overrides the deployer's `int_max_str_digits`, and a limit set afterwards leaks a raw `ValueError`
- Found by: s2 C5.
- Where: `python/sel/decimal.py:66-70`; every `int(str)`/`str(int)` in `decimal.py` (lines 183, 190-191, 203-207).
- What: (a) the module raises the process-global limit to 2,000,000 whenever it is non-zero and lower. A deployer who set `-X int_max_str_digits=5000` or `PYTHONINTMAXSTRDIGITS` as hardening has it silently undone for all code in the process; a non-zero explicit limit is a choice the application made. (b) If the limit is lowered again after import, any number above it raises `ValueError` from `D.parse`/`D.format`, not a SelError (SPEC 8: every host failure is a SelError).
- Repro: `python3 -X int_max_str_digits=5000 -c "import sys, sel; print(sys.get_int_max_str_digits())"` -> `2000000` (synthesizer: confirmed). `import sel, sys; sys.set_int_max_str_digits(5000); sel.evaluate('9'*6000 + ' + 1')` -> `ValueError: Exceeds the limit (5000 digits) for integer string conversion` (also `POWER(10,5000) + 1`, `LEN(POWER(10,5000))`).
- Fix sketch: do not touch the global; convert in chunks (<=4000-digit pieces combined by shifts/`divmod` by a power of ten; `format` symmetric). At minimum catch ValueError and raise E_RANGE. (Note s4 relies on this raised limit for the regex bound `int()`.)
- Conformance gap: not expressible in `.selt`; python/tests case that lowers the limit.

## Low

Each entry: Where / What / Repro / Fix sketch. Conformance suggestions for these are collected in the table further down.

### PY-C26 [low] [confirmed] `x .> f(_, _)` (several placeholders) evaluates the left operand once per placeholder; Go disagrees with the other four
- Found by: s1 C6. Where: `parser.py:371-376` (replaces EVERY bare `_` with the same `left` Node object).
- What: SPEC 5.10 rule 3 says "one of the top-level arguments is a bare `_`". py/js/php/cpp/lisp agree with each other (left evaluated per occurrence, side effects double, same Node object at two places); go rejects with `E_UNDEF_VAR ... _`. Spec ambiguity rather than a Python bug.
- Repro: `C = 0; R = (C += 1) .> MAX(_, _); C` -> py/js/php/cpp/lisp `2`; go `E_UNDEF_VAR at line 1 column 31`; three placeholders give 3 in py.
- Fix sketch: decide in the spec (first only, or all with a purity note); if "first only", `break` after the first replacement.

### PY-C27 [low] [confirmed] E_UTF8 for a lone-surrogate source carries no position (0:0)
- Found by: s1 C7. Where: `lexer.py:73` (`to_code_points(source, None)`), `utf8.py:45`.
- What: SPEC 2 says "at the offending byte" and 6.3 "position of the node that actually failed"; JS behaves the same (checked), Python knows the index cheaply. No valid UTF-8 input can trigger it.
- Repro: `python3 -c "import sel; sel.compile('1 + \ud800')"` -> `E_UTF8` line 0 col 0 offset 0.
- Fix sketch: report the surrogate's Pos, in every host at once.

### PY-C28 [low] [confirmed] `Value.scalar` has a public setter that leaves the cached decimal stale
- Found by: s2 C6. Where: `value.py:257-259` (setter) with `_dec_val` cache at 249, 589-595, 609-615.
- Repro: `v = Value.text('1'); v.as_decimal(); v.scalar = '2'; v.as_decimal().digits` -> `1` (should be 2). `w = Value.num('1'); w.scalar = 'abc'; w.looks_numeric()` -> `True`, `w.as_text()` -> `abc`.
- Fix sketch: setter clears `_dec_val`, or drop the setter (not listed in the contributing Value API table; nothing in the library uses it).

### PY-C29 [low] [confirmed] The decimal oracle is not independent for `/` and `%` and never reaches interesting magnitudes
- Found by: s2 C7. Where: `tools/decimal-oracle.py:41-66` (`sel_div`, `sel_mod`), `69-75` (`rnd`).
- What: it re-implements the cores' divmod-and-round algorithm (same `divmod(n * 10**10, den)`, same `2 * r >= den`); operands at most 12 integer digits and scale <= 6; no tie cases for `/`, no scale gaps, no zero-result-sign cases, nothing near digit caps; `round` uses n in 0..8. Hand cases in `python/tests/test_decimal_native.py` do use `Decimal.quantize(ROUND_HALF_UP)`, the right shape.
- Fix sketch: for `/`, compute with `Decimal` at ample precision, `quantize(1e-10, ROUND_HALF_UP)` and check exactness with `(q * b == a)`; add 30+ digit operands, scale gaps of 20+, forced ties.

### PY-C30 [low] [confirmed] Value nesting created by assignment is checked only against the target path, not path + depth of the assigned value (all five hosts)
- Found by: s3 C7. Where: `eval.py:582-583` (`len(chain) + 1 > MAX_DEPTH`) and `:511` (`clone(node.pos)` starts from the value's own root).
- What: SPEC 6.4 says a value's nesting is capped and exceeding it is E_DEPTH "reported at the assignment target". Assigning a 150-deep value at a 100-deep path creates a 251-deep value and succeeds; reading it later fails with E_DEPTH at 0:0 or an unrelated node. `(1, A)` builds A's depth + 1 without complaint until assigned.
- Repro: `A[1]...(150 times) = 1; B[1]...(100 times) = A; B` -> `E_DEPTH at line 0 column 0` in all five hosts (the assignment itself returns silently; `... = A; 1` prints 1).
- Fix sketch: check `len(path) + depth(value) <= MAX_DEPTH` after resolving the path (Value already computes depth while cloning) and raise E_DEPTH at the target; spec wording in all hosts.

### PY-C31 [low] [confirmed] `dependencies()` is order-insensitive, contradicting its own docstring (all five hosts)
- Found by: s3 C9. Where: `python/sel/__init__.py:88-91` (`sorted(n for n in reads if n not in assigned)`), docstring `:83-86`; SPEC 8 "every variable the program reads".
- What: `assigned` is filled over the whole program, so a read before the assignment is dropped: `A + 1; A = 2` reports no dependencies but evaluation raises E_UNDEF_VAR; `IF(B, A = 1, 0); A` reports only `B`. A frontend using the list to decide which inputs re-trigger a rule will miss `A`.
- Repro: `python3 -m sel --deps -e 'A + 1; A = 2'` -> empty (same in cpp, lisp, js, php); evaluation -> E_UNDEF_VAR at 1:1. Synthesizer: `--deps` printed nothing (confirmed).
- Fix sketch: flow-sensitive walk (a read is a dependency unless an assignment definitely precedes it on every path; conservatively unless one appears earlier in a `;` sequence at the same or enclosing level), or change spec/docs to "minus every name the program ever assigns" and fix the docstring.

### PY-C32 [low] [confirmed] `Program.run(context)` turns falsy non-Value contexts into `{}`, while a truthy float is refused
- Found by: s3 C10. Where: `python/sel/__init__.py:65` (`Value.from_native(context or {})`).
- What: `run(0)`, `run("")`, `run(False)`, `run(0.0)`, `run([])` evaluate against an empty root, but `run(1.5)` raises `E_BAD_ARG`. Also the docstring says the context "is mutated in place by any assignments", true only for a `Value`; a plain dict is copied by `from_native` (`evaluate('A = 5', d)` leaves `d` unchanged).
- Repro: `python3 -c "import sel; print(sel.compile('1').run(0.0).dump())"` -> `t"1"` (should be E_BAD_ARG like `run(1.5)`).
- Fix sketch: `context if context is not None else {}`; correct the docstring.

### PY-C33 [low] [confirmed] `re.compile` emits `FutureWarning` for valid subset patterns (`[[]`, `[a&&b]`, `[a||b]`, `[a~~b]`, `[a--b]`), an uncaught exception under `-W error`
- Found by: s4 C5. Where: `regex.py:343` (`re.compile(source, opts)`); class body passed through verbatim by `_validate_class` (`regex.py:244`).
- Repro: `python3 -m sel -e "RMATCH('[[]', '[')"` prints the warning and TRUE; `python3 -W error -m sel -e "RMATCH('[[]', '[')"` -> `FutureWarning` traceback. (`[a--b]` is then rejected anyway as a bad range, like other hosts.)
- Fix sketch: escape `[` (and the first char of doubled `&& || ~~`) inside classes after validation, e.g. in `_lower_anchors`'s in-class branch emit `\[`, `\&`, `\|`, `\~`.

### PY-C34 [low] [confirmed] Regex pattern cache is unbounded
- Found by: s4 C6. Where: `regex.py:312` (`_cache`), `335-346`.
- What: process-global dict keyed on `(ignore_case, pattern)`, no eviction; patterns can be data-driven. `re` keeps its own bounded cache (512), so this one buys only the cost of `validate()`/`_lower_anchors`.
- Evidence: 20,000 distinct small patterns left 20,000 entries and about 10.5 MB traced (tracemalloc), >500 bytes per entry.
- Fix sketch: bound it (clear or drop oldest beyond 512-1024) or use `functools.lru_cache`.

### PY-C35 [low] [confirmed, all five hosts] Literal regex patterns are not validated at compile time although the spec says they are
- Found by: s4 C7. Where: `regex.py:315-347` (validation only inside the running call); `spec/errors.md` lines 34 and 40, SPEC 7.8 ("checked at compile time").
- What: errors.md says `E_REGEX_SYNTAX` "is compile-time only when the pattern is a literal". Hosts agree with each other, so this is a uniform spec/implementation gap.
- Repro: `IF(FALSE, RMATCH('(?=a)', 'a'), 1)` yields `1` on py, js, php, cpp and lisp; `sel.compile("RMATCH('(?=a)','a')")` succeeds. Synthesizer: `1` (confirmed on py).
- Fix sketch: implement the compile-time check for literal pattern arguments in every host, or reword errors.md/7.8 to say run time. Decide in the spec first.

### PY-C36 [low] [confirmed, all hosts] `LTB(LIST())` raises E_NO_SCALAR, so `LTB(BTL(""))` fails
- Found by: s4 C8. Where: `binary.py:135-138` (`items = v.values() if v.size() > 0 else [v]`).
- What: SPEC 7.7 is silent on the empty list; all five hosts agree, so it may be intended. The documented inverse pair does not round-trip on the empty value.
- Repro: `python3 -m sel -e 'LTB(BTL(""))'` -> `E_NO_SCALAR` (synthesizer: confirmed); by symmetry expected an empty BIN.
- Fix sketch: decide in the spec; if empty BIN is intended return `Value.bin(b'')` for an empty list, in all hosts.

### PY-C37 [low] [confirmed; all six hosts agree] 2-argument `BUCKET` silently drops rows when two group keys differ structurally but share a key text
- Found by: s5 C5. Where: `aggregate.py:597-611` (grouping by `structural_hash`/`eql`) and `:621-623` (`out.set(g['key_str'], ...)`).
- What: the bare spelling's group key is an index key (SPEC 7.3: "the key's scalar, verbatim"), but groups are formed by full `eql`. A TEXT value that also has children (`A = "x"; A["k"] = 1`) is not `eql` to `"x"`, so it becomes a second group with the same `key_str`; the second `out.set("x", ...)` overwrites the first and members vanish. The bare form should equal the 3-arg form `.> MAP(proj)`; here it does not. Spec-level wart, not a Python slip.
- Repro: `A="x"; A["k"]=1; R = LIST(A, "x", A) .> BUCKET(_); JOIN(LIST(COUNT(R), COUNT(R["x"])), ",")` -> `1,1` on py/js/php/lisp/cpp/go (3 rows in, 1 row out). `... .> BUCKET(_, COUNT(_)) .> JOIN(",")` -> `2,1`.
- Fix sketch: in the bare spelling group by `key_str`, not by identity; never `set` the same key twice.

### PY-C38 [low] [confirmed] Aliasing vs copying is observable and differs between hosts (BUCKET 2-arg, LIST, RECORD, SORT*)
- Found by: s5 C6. Where: `aggregate.py:623` (`row.clone()` in 2-arg BUCKET); `structure.py:45-58` (LIST/RECORD alias args); `aggregate.py:424` (SORT aliases).
- What: `contributing.md` says an aggregate's copy is something "no program can tell apart because a binder cannot be assigned". It can, by mutating the source after the aggregate ran from a later sibling argument. Results (x seen through the result): BUCKET 2-arg py/js/lisp/go copy (1), php/cpp alias (5); Python's own 3-arg BUCKET and MAP/FILTER/TAKE/DISTINCT/TOP/LINK alias (5) on every host. LIST(A) and RECORD("q", A): py/php/go alias (5), js/lisp/cpp copy (1). SORT/SORT_DESC/SORT_BY: js and cpp copy (1) (SORT_BY: js only), others alias (5). SPEC 3.4 does not settle which builtins copy.
- Repro: `A = LIST(RECORD("k",1,"x",1)); R = LIST(BUCKET(A, _["k"]), (A[1]["x"] = 5; 0)); R[1]["1"][1]["x"]` -> py 1, php 5. `... LIST(LIST(A), (A[1]["x"]=5;0)); R[1][1][1]["x"]` -> py 5, js 1.
- Fix sketch: decide in SPEC 3.4 exactly which builtins copy; then drop the clone in Python's bare BUCKET or add the copy everywhere; correct the contributing.md sentence either way.

### PY-C39 [low] [confirmed] BUCKET (all hosts but C++) returns an empty result for a scalar source; the spec says a scalar is a one-element list
- Found by: s5 C7. Where: `aggregate.py:561` (`if val.is_null() or val.size() == 0: return`).
- What: SPEC 7.3 ("If the first argument has no children, it is treated as a one-element list containing itself when it has a scalar") applies to the aggregates table that contains BUCKET. MAP, DISTINCT, TAKE, LINK, SELECT_COLS treat the scalar as one element on every host. (Lisp additionally returns 0 for `SORT("x")` and `TOP("x",1)` where the rest give 1.)
- Repro: `BUCKET("x", _)` -> py/js/php/lisp/go `-` (empty; synthesizer confirmed on py), cpp `{"x"={"1"="x"}}`; `BUCKET("x", _, COUNT(_)) .> COUNT()` -> 0 vs cpp 1.
- Fix sketch: guard only on `val.is_null()`; let `elements` handle the scalar. Needs the spec to win over four hosts: add the case first.

### PY-C40 [low] [confirmed] Direction/limit expressions are skipped when the sorted list is empty (PHP disagrees)
- Found by: s5 C8. Where: `aggregate.py:366-371` (`do_sort` returns before reading the direction), `:434-436` (`do_top` evaluates the limit but not the direction).
- What: `SORT_BY(LIST(), _+0, "X")` and `SORT_BY(LIST(), _+0, 5+"x")` return an empty list in py/js/lisp/cpp but `E_BAD_ARG` / `E_NOT_NUM` in PHP; a direction expression with a side effect (`(A = "DESC"; A)`) is silently not run on empty input while it is on non-empty input. SPEC 7.4 only pins "a count of zero still evaluates the list".
- Repro: `SORT_BY(LIST(), _+0, "X")` -> py `-`, php `E_BAD_ARG at line 1 column 22`.
- Fix sketch: spec sentence; evaluate and validate direction/limit before the empty shortcut in every host.

### PY-C41 [low] [confirmed; server behaviour reasoned] SQL: TAKE/DROP counts above the server's integer range translate to SQL every server rejects
- Found by: s6 C8. Where: `translator.py:2184-2196` (`_eval_int_param`), `2424-2440`, `2758-2769`.
- What: `ITEMS .> TAKE(99999999999999999999999)` -> `... LIMIT 99999999999999999999999` (SEL: all rows). PostgreSQL rejects LIMIT/OFFSET beyond 2^63-1, MariaDB beyond 2^64-1, SQLite errors on overflow. `TAKE(18446744073709551616) .> DROP(1)` on postgresql/sqlite emits `LIMIT 18446744073709551615 OFFSET 1`. `sql/cases/28-slice-overflow.sqlt` handles sums past 2^53 but not single counts past the server range. Loud failure at run time, not a wrong row.
- Repro: `sel.sql.Sql.translate(compile('R .> TAKE(99999999999999999999999)'), 'postgresql', {'R': relation('orders', alias='o')})` -> `LIMIT 99999999999999999999999`.
- Fix sketch: drop LIMIT when count >= 2^63, refuse (E_SQL_UNSUPPORTED) for OFFSET >= 2^63, cap the computed limit likewise.

### PY-C42 [low] [confirmed on SQLite] SQL: unrolled IN / ANY / ALL / SUM over more than about 1000 list elements is rejected by SQLite at run time (O(n) deep on every server)
- Found by: s6 C9. Where: `translator.py:786` and `1472` (`fold_pairwise` builds a LEFT-nested chain).
- What: real SQLite: 900 elements works; 1200 -> `Expression tree is too large (maximum depth 1000)`. Other servers have stack/nesting limits too (unconfirmed). No warning, caveat or refusal; docs silent. A balanced fold changes the emitted bytes for every case with 3+ elements, so it needs a cross-host decision and regenerated `sql/cases`.
- Repro: `T IN ("v0", ..., "v1199")` on sqlite, execute the inline SQL.
- Fix sketch: balanced folding on all hosts, or an explicit `E_SQL_UNSUPPORTED`/caveat above a documented element count. (Interacts with PY-P3.)

### PY-C43 [low] [confirmed] SQL: identifiers from SEL text literals (RECORD field names, SELECT_COLS names) bypass the NUL/empty checks that binding names get
- Found by: s6 C10. Where: `translator.py:2622-2623, 2625-2649` (`emit.ident(projection['alias'])`, `emit.column(table, column)`) vs `binding.py:245-261` (`_check_name`).
- What: quote doubling is correct, so no injection; a NUL truncates the statement inside a quoted identifier (syntax error), and an empty alias is a server error on PostgreSQL.
- Repro: postgresql `R .> MAP(RECORD("a\u{0}b", _["id"]))` -> `SELECT "id" AS "a\x00b" FROM ...`; `R .> MAP(RECORD("", _["id"]))` -> `AS ""`; `R2 .> SELECT_COLS("a\u{0}b", "")` (relation with no declared fields) -> `SELECT "a\x00b", "" FROM "orders"`.
- Fix sketch: apply a `_check_name`-equivalent refusal in `Emit.ident`.

### PY-C44 [low] [confirmed] SQL bindings: constructors accept types that make no sense; `Binding.value(type=...)` ignores everything but NUM; case-colliding names silently merge
- Found by: s6 C11. Where: `binding.py:59-87, 170-194`, `_check_type` at 264 (accepts all of `Fragment.KINDS` incl. LIST and STATEMENT).
- What: `Binding.column('c', type='STATEMENT')` yields a STATEMENT-kind Fragment (`` `c; DROP TABLE x` `` came back correctly quoted, so not an injection); `type='LIST'` is refused only later; `Binding.value(v, 'BOOL')` on a text value silently behaves as TEXT; `Bindings.__init__`/`_make_relation` silently merge names differing only by ASCII case (`{'a':..., 'A':...}`, fields `qty`/`QTY`): last one wins.
- Repro: `f = Sql.translate(compile('C'), 'mariadb', {'C': Binding.column('c', type='STATEMENT')}); f.kind == 'STATEMENT'; f.as_statement()`.
- Fix sketch: restrict column/raw types to NUM|TEXT|BOOL|BIN|UNKNOWN and value types to NUM|None; refuse case-colliding names (E_SQL_BINDING).

### PY-C45 [low] [confirmed] SQL: a binder named like a scalar `value` binding is treated as that constant by the validation pre-check
- Found by: s6 C12. Where: `constants.py: is_constant` (var: `n.name in bound`, ignores translator frames), used at `translator.py:261-266, 1878, 1928`.
- What: an inner binder called like a value binding is evaluated as the value; the outcome is a spurious refusal, never wrong SQL.
- Repro: `R` one-field relation, `P = Binding.value(Value.text('abc'))`: `ANY(R, P, P["qty"] > 0)` -> `E_SQL_INVALID ... (E_NO_KEY: no key "qty")`; the same program without the P binding translates to `EXISTS (SELECT 1 ...)`.
- Fix sketch: pass the active binder names into `is_constant`, or drop shadowed names from `const_names` while a frame binds them.

### PY-C46 [low] [confirmed] `Fragment.as_value(mode)` accepts an unknown mode when the fragment has no parameter slot
- Found by: s6 C13. Where: `fragment.py:155-181` (mode validated only inside the per-slot branch).
- Repro: `Sql.translate(compile('C > C'), 'mariadb', B).as_value('bogus')` returns the SQL; with a literal in the expression it raises RuntimeError.
- Fix sketch: validate `mode` at the top of `_join`.

### PY-C47 [low] [confirmed] Hybrid: MAP fall-through pushes `TAKE`/`DROP` past the local half of the MAP, so errors `run()` raises are lost
- Found by: s7 C5. Where: `hybrid.py:290` (`FALLTHROUGH_DOWNSTREAM = {SORT_BY, TOP_BY, TAKE, DROP}`), `:374-384`.
- What: `run()` evaluates the whole MAP over every row before the TAKE/DROP; the hybrid plan puts LIMIT/OFFSET in SQL so the local half only sees kept rows. Docs say refused parts "raise what `run()` raises". Also doc/code drift: `sql-translation.md` 12.1 ("The MAP fall-through keeps the rows it splits over") still lists `FILTER` among allowed downstream steps, the code excludes it for key reasons.
- Repro (`py7/t3.py`): rows with `amt` = 1,2,3,4,"zz",6: `R .> SORT_BY(_["id"]) .> MAP(RECORD("id", _["id"], "rep", REPEAT(_["name"], _["amt"]))) .> TAKE(2)` - run() = ERR E_NOT_NUM; hybrid = 2 rows (SQL `... ORDER BY id LIMIT 2`). Also via fuzz (`py7/fz.py 1 500`, PADL with a row-dependent width, E_RANGE lost).
- Fix sketch: allow TAKE/DROP downstream only when the custom half provably cannot raise, or keep them out of SQL, or document error-loss as accepted.

### PY-C48 [low] [confirmed] Hybrid: `source_tables` reports a relation that is only used as a binder name (also in JS)
- Found by: s7 C6. Where: `hybrid.py:134-161` (`_source_tables.visit`): any `var` naming a relation binding counts, including the binder-name argument of a 3-arg aggregate.
- Repro (`py7/j2.mjs`, `py7/t13.py`): bindings R->"r", S->"s": `R .> FILTER(S, S["ID"] > 1)` -> SQL `SELECT r.* FROM r WHERE ...` but `source_tables == ['r', 's']` (JS prints the same). Docs say it names the tables "a grant or a connection is chosen by", so it can demand access to a table never read.
- Fix sketch: skip binder positions or read the tables from the translator's own record of relations touched.

### PY-C49 [low] [confirmed; also PHP and JS] `check_numeric_guard` memoises the dialect before it checks it, so the second use of a bad guard silently passes
- Found by: s7 C7. Where: `python/sel/sql/map.py:283-285` (`_guard_checked.add(dialect)` precedes the checks that raise). JS `map.mjs:229`, PHP `Map.php:334` do the same order.
- What: ValueError on first use, then the disagreeing guard is returned on the second; child dialects likewise.
- Repro: `map.define_dialect('badpg', {'extends':'postgresql','lexical':{'numericGuard': "CASE WHEN ({textCast:0} ~ '^zzz$') THEN CAST({0} AS NUMERIC) ELSE NULL END"}}); e = Emit('badpg'); e.numeric_operand(Fragment(['"c"'],'TEXT','badpg'))` - first call ValueError, second returns the `'^zzz$'` guard.
- Fix sketch: add to the set only after the checks pass.

### PY-C50 [low] [confirmed] Registered dialects can produce unescaped text/identifier literals; validation is per key, not per pairing
- Found by: s7 C8. Where: `map.py:372-406` (`_check_lexical`), `emit.py:113-135` (`text_literal`), `:223-232` (`ident`).
- What: a dialect overriding `textQuote` but inheriting `textEscape`, or setting `textEscape` to `{}`/`None`, emits literals whose closing quote is not escaped (injection in inline mode); `identQuote` without `identEscape` likewise; `None` for `textQuote`/`identEscape` renders the text `None`. The registrant is trusted application code, so this is hardening, not a remote hole.
- Repro: `define_dialect('dq', {'extends':'ansi','lexical':{'textQuote':'"'}}); emit.text_literal('dq','a" OR 1=1 --')` -> `"a" OR 1=1 --"`. `lexical={'textEscape':{}}` on ansi -> `'a' OR 1=1 --'`.
- Fix sketch: after resolving the effective lexical set require `textQuote in textEscape` and the `identQuote`/`identEscape` analogue (or a post-condition in `text_literal`/`ident`); reject `None` for these four keys.

### PY-C51 [low] [confirmed] `execute_hybrid` deep-clones the caller's context, so hybrid and pure-memory plans differ in whether the context is mutated
- Found by: s7 C9. Where: `hybrid.py:851`.
- What: `Program.run` documents that the context is mutated in place; the pure-memory branch of `execute_hybrid` runs on the caller's context, the hybrid branch on `context.clone()`, so helper assignments the continuation performs are not visible to the caller and `_INPUT` is not leaked.
- Repro: `T = "x"; R .> SORT_BY(_["id"]) .> MAP(RECORD("id", _["id"], "x", PADL(_["name"], 3, T)))` (hybrid): `run()` leaves `T` in the context, `execute_hybrid` does not; a pure-memory plan for `T = "x"; R .> MAP(RECORD("x", PADL(_["name"], 3, T)))` does leave `T`.
- Fix sketch: pick one rule and document it (clone in all branches, or set `_INPUT` on the real context and delete afterwards). See PY-P16 for cost.

### PY-C52 [low] [unconfirmed on real servers; reproduced on SQLite] SQL: nested `ORDER BY` in a derived table is relied on for tie order
- Found by: s7 C10 (translator, outside s7's slice). Where: emitted by `translator.py`, visible through hybrid plans.
- What: SQLite fuzz (`py7/fz.py 12 600`): `R .> SORT_BY(_["id"]) .> SORT_BY(_["name"]) .> SELECT_COLS("id","cat") .> TOP_BY(_, _["cat"], "DESC", 2)` renders `SELECT ... FROM (SELECT id, cat FROM r ORDER BY name, id) ORDER BY cat DESC LIMIT 2`; SEL's stable ties are not preserved by SQLite's top-N sorter (got ids 1,5; expected 5,6). Same for `MAP` + `SORT_BY .> SORT_BY .> SORT_BY(..., "DESC") .> TAKE(4)`. No server guarantees an inner ORDER BY survives an outer ORDER BY; the docs' own rule ("a later sort's keys come first ... never a derived table around the first sort") is not applied across a MAP/SELECT_COLS/TAKE boundary. Not investigated on PostgreSQL/MySQL.
- Fix sketch: carry earlier sort keys as trailing keys of the outer ORDER BY when the columns are still visible.

---

# Performance findings

Ordered by impact. Labels are the reviewers' (measured / reasoned / guessed); every fix must keep results byte-identical.

## High

### PY-P1 [high] [measured] SORT / SORT_BY / SORT_DESC / TOP* re-derive both keys' kind, decimal and UTF-8 bytes on every comparison
- Found by: s5 P1.
- Where: `aggregate.py:324-362` (`compare_values`), `:373-424` (`do_sort`), `:471-543` (`do_top`).
- Why: `cmp_to_key(cmp_func)` calls `compare_values` about n log n times; each call runs `is_null` x2, `looks_numeric` x2 (a `scalar_source` walk plus, for any text not already cached as a number, a full regex `D.parse`; non-numbers are never cached, so text keys re-parse every time), then `as_decimal`/`D.cmp` or `as_bytes` x2 (fresh UTF-8 encode each). TOP does the same inside a Python-level binary heap and calls `compare` twice per `worse`. Tie-break dicts (`{'item','key','idx'}`) add three dict lookups per compare.
- Evidence (measured, 100k rows): `R .> SORT_BY(_["n"])` 7.6 s (numeric text); `SORT_BY(_["s"])` 14.6 s (non-numeric text; about 11 s in profile is looks_numeric+as_bytes+parse); `TOP_BY(_["n"],10)` 0.8 s and `TOP 1000` 1.5 s vs 0.2 s for a bare `MAP(_["n"])`. A prototype that classifies each key once and sorts on a precomputed Python key (signed int for all-scale-0 numbers, bytes for all-non-numeric text; `sorted(range(n), key=keys.__getitem__)`, stable) gave 0.18 s and 0.25 s for the two 100k cases (35-45x), identical order (checked equal to the comparator output).
- Fix sketch: compute each key's (rank, sortkey) once in `do_sort`/`do_top`; if every key is in one homogeneous class (all number-looking: signed int when all scales are 0, else int scaled to the max scale when small (<= ~64), else keep the comparator; all non-number text/BIN: the bytes; all BOOL) sort/heap on that key with `idx` as the implicit stable tie-break (`heapq.nsmallest`/`nlargest` with `(key, idx)`); mixed classes fall back to the comparator. Better still after PY-C13 defines a total order: always precomputed keys.

### PY-P2 [high] [measured] Every LINK call `exec`s freshly generated source per (left shape, right shape) pair; nested LINKs and heterogeneous rows are 40-150x slower than the amortised cost
- Found by: s5 P2.
- Where: `structure.py:460-528` (`_compile_plan`, `exec` at :527), `:542-604` (`make_join_projector`, `plans` per call).
- Why: `_compile_plan` builds Python source and compiles it (about 0.7-1 ms) for the first pair of every shape combination in every `_link` call; nothing survives the call. Two workloads pay repeatedly: (a) a LINK inside another aggregate body, (b) data with many distinct layouts (optional fields). Compounding (b): `_SHAPES` (`value.py:41-61`) is cleared wholesale at 256 entries, so with more than about 256 layouts in rotation the interning cache thrashes and identical-key rows get distinct `RecordShape` objects (300 layouts x 6000 rows -> 6000 distinct shape objects).
- Evidence (measured): `A .> MAP(LINK(LIST(_), B, _1["x"] == _2["bid"]) .> COUNT())` over 3000 rows x 7-row B: 3430 ms vs 26 ms for one equivalent LINK; cProfile 55% in `exec`. Monkey-patching `exec` with a source->code-object cache: 540 ms (6.3x, output identical). Heterogeneous rows: 2000 rows with 2000 distinct layouts 1496 ms vs 10 ms for one layout; 6000 rows over 300 layouts (cache thrash) 4257 ms vs 113 ms for 100 layouts (40x).
- Fix sketch: cache the compiled code object process-wide keyed by the generated source text (depends only on op list and guards, not shape objects; bound the dict like `_ALIAS_PLANS`), and/or compile a plan only after a shape pair has been seen a few times, using `make_joined_row` before. Independently make `_SHAPES` an LRU (or evict half) instead of `clear()`, and key `plans` on key tuples rather than shape identity.

### PY-P3 [high] [measured] SQL `fold_pairwise` is quadratic: every step re-copies the accumulated part list
- Found by: s6 P1.
- Where: `translator.py:788-797` calling `_apply` -> `emit.py:242-374` `fill` (`splice` at 287-292 copies `args[0].parts` element by element).
- Why: the left operand grows by O(1) parts per step but is copied in full into the new Fragment each time: total O(n^2) in unrolled terms.
- Evidence (mariadb, params mode): `T IN (literal list)`: n=500 0.16 s, n=2000 2.3 s, n=8000 31.9 s (4x n is about 14x time); same for a `value`-binding allow-list (29.9 s at 8000), `ANY((0..n), x, C == x)` (34 s at 8000) and `SUM` (2.1 s at 2000). cProfile at n=2000: 13.0 s of 14.3 s in `splice`/`push`. Realistic allow-lists of 1000-5000 entries cost 0.6-7 s per translation.
- Fix sketch: fold left-deep analytically: fill the operator template once with sentinels to get prefix/infix/suffix strings, then emit `A^(n-1) x0 (B xi C)...` in one linear pass (bytes identical), or make `Fragment.parts` a persistent rope/linked segments that `fill` can splice by reference. Balanced folding would change bytes (see PY-C42).

## Medium

### PY-P4 [medium] [measured] `dataclasses.replace` in `copy_node` makes the optimiser about as expensive as parsing
- Found by: s3 P1; also visible in s6 P3 (stage 1 rebuilds every node with `dataclasses.replace`: 7% of translation) and s7 P3 (300-statement helper chain dominated by `build_pipeline`/`copy_node`).
- Where: `optimizer.py:30-33` (`copy_node`), called for every node by `optimize_tree` (:615) and again by `build_pipeline`, `rename_var`, merges; `normalise.py:181-216`.
- Why: `dataclasses.replace` walks `fields()` and re-runs `__init__` with 21 keyword arguments: about 14.5 us per node on Python 3.14 vs about 1.6 us for a direct `Node(...)` call.
- Evidence (measured, min of several, noisy box): an 841-node program (40 rules) `physical_ast()` 19-30 ms of which 75% is `_replace`; the 13-node reference rule: `compile` 364 us, `compile + physical_ast` 780 us, `evaluate()` one-shot 1039 us vs 44 us for `run` alone. A hand-written `copy_node` took `compile + physical_ast` from 1043 us to 524 us. Since `evaluate(source, ctx)` cannot reuse the cached physical tree, this is roughly 2x on every one-shot evaluation and every cold compile.
- Fix sketch: explicit constructor (or a `Node.copy()` using `__slots__` assignment), keeping the `list(...)` copies of `args`/`items`; in stage 1 return the original node from `_substitute` when no child changed.

### PY-P5 [medium] [measured] Frozen dataclasses (`Dec`, `Pos`, and `Node` defaults) cost 3-4x more to construct than necessary
- Found by: s2 P1 (`Dec`), s1 P4 (`Pos`, `Node`).
- Where: `decimal.py:73-77` (`@dataclass(frozen=True, slots=True)`), `make` at 84-85; `errors.py:9-15` (`Pos`); `lexer.py:81-89, 111` (`pos_at` per token, plus eagerly in `skip_quoted`/`skip_raw`/`match_brace`/`read_escape`/`lex_raw`/`lex_quoted` even when no error follows); `parser.py:123-124` (`items`/`args` default_factory).
- Why: frozen dataclasses set each field via `object.__setattr__`. `D.add` is 1.2 us of which about 1.0 us is constructing the result.
- Evidence (micro, best of 200k): frozen `Dec(False,1250,2)` 803 ns; plain `__slots__` class with `__init__` 225 ns; NamedTuple 497 ns; tuple 40 ns; `D.make` 1075 ns; `D.add` 1201 ns; `D.mul` 1085 ns. End to end (100k rows, `ROWS .> MAP(_["p"] * _["q"] + 1)` and `FILTER(_["p"] * 2 > 50)`), monkeypatching `D.Dec` to a plain slots class with hand-written `__eq__`/`__hash__`: 8-17% faster (noisy). With PY-P6 on the pure plan path (`A*B+C`, `process_time`, best of 9): 9.4 us -> 5.8 us and `A*B+C - A*B*C/B` 20.3 us -> 15.4 us (-24..-39%, noisy). `Pos(1,2,3)`: frozen slots dataclass 0.82 us, plain slots dataclass 0.22 us, NamedTuple 0.45 us, hand-written `__slots__` 0.31 us; `pos_at` (bisect + Pos) about 10% of tokenize time (0.234 s of 2.07 s). `Node` about 1.0 us and 320 bytes, partly from two empty default lists (112 bytes); a shared empty tuple would save about 35% of node memory if no consumer appends in place (not audited).
- Fix sketch: plain `__slots__` classes (immutable by convention; the library never mutates), keep `__eq__`/`__hash__`/`__repr__` (tests compare `Dec`s with `==`; `Dec ==` compares representation, 150/2 != 15/1, so keep the field-wise definition); `make` skips `bool()` (`neg and digits != 0`); compute `pos` lazily in error branches. I found no `Pos(` construction outside errors/lexer.

### PY-P6 [medium] [measured] `Value.num(dec)` re-validates every internal result
- Found by: s2 P2.
- Where: `value.py:404-412`, `_check_decimal` 134-142; hot callers `eval.py:229`/`_eval_math_plan` return, `_eval_binary`, `builtins/number.py`.
- Why/evidence: every arithmetic result already passed `D.guard` and was built by `make`, yet `Value.num` reruns `isinstance`, two `type(...) is int` checks and `D.guard`. `Value.num(dec)` 1331 ns vs 507 ns to build the same Value directly; `_check_decimal` + `guard` about 15% of plan time in a cProfile of the MAP-arith run.
- Fix sketch: internal `Value._num_owned(dec)` (no check) for `eval.py`, `number.py` and the plan; keep `Value.num` for the public boundary.

### PY-P7 [medium] [measured] One un-plannable operand throws away the plan for the whole expression, including sub-plans
- Found by: s2 P3.
- Where: `math_plan.py:218-226` (`emit` returns None for assign/seq/list/IF/COND and any non-math `bin`/`un`), `optimizer.py:594-597, 635-637` (children of a math node are visited with `in_math=True`, so never compile their own plan).
- Why/evidence: `(A*B+C-A*B*C/B) + IF(D, 1, 2)` has no plan anywhere in the physical tree. `A*B+C - A*B*C/B`: plan 17.7 us vs tree walk 28.2 us per run (-37%); with the `+ IF(D,1,2)` sibling the run is back to tree-walk speed (33.2 vs 31.1 us). `IF(D, A*B+C, A-B)` and `(A*B+C) == 3` do get sub-plans; the loss is specific to "math node with a derailing descendant".
- Fix sketch: when `compile_math_plan` on a root returns None, retry on each child that is itself a math op (or treat a derailing child as an opaque `LOAD_LEAF` where safe; see PY-C7 for what is unsafe).

### PY-P8 [medium] [measured] Constant list operands (`x IN LIST("a","b",...)`, `x IN ("a","b")`) are rebuilt and cloned for every row
- Found by: s3 P2.
- Where: `eval.py:310-332` (`_eval_list`), `:431-432` (`IN`), builtins LIST call path.
- Evidence (measured, process time, 20,000 rows, min of 5): `FILTER(_["b"] IN LIST(10 literals))` 415 ms, `IN LIST(3 literals)` 237 ms, `IN ("x1","x2","x3")` 214 ms, equivalent `$== OR $== OR $==` 184 ms, plain `_["a"] == 3` 65 ms.
- Fix sketch: in the physical tree, mark an `IN` whose right operand is `list`/`LIST(...)` of literals and build its `Value` once per `Program` (private to the `IN`; never return or alias it into user-visible values). A membership set keyed by the same EQL identity removes the O(k) scan for large literal lists.

### PY-P9 [medium] [measured] Constant folding re-parses and re-formats literals and drops the decoded `Dec`; folded literals are re-parsed on every evaluation
- Found by: s2 P5.
- Where: `optimizer.py:62-63` (`literal_num(value: str, pos)` carries no `dec`), 98, 116-117, 134 (`D.parse(node.l.v)` although `node.l.dec` exists from `parser.py:390-391`), 145 (`D.format(result)`), 392; `eval.py _dispatch` 'num' branch builds `Value(TEXT, node.v)` and caches `_dec_val` only when `node.dec` is set.
- Evidence: (a) any `lit op lit` fold pays two regex parses + one format per operand pair once per Program (a 1M+1M-digit literal: compile 9.0 s, first `run()` another 7.6 s in `fold`, vs 3.9 s for one `D.parse` of that literal). (b) more important for ordinary programs: after folding the node has `dec=None`, so every evaluation re-parses the constant. `process_time`, best of 7: `A > 3` 8.1 us, `A > 1+2` 11.6 us, `A > 1.5*2` 12.9 us, `A > 3.0` 8.2 us (3.5-4.7 us extra per evaluation, about 45%).
- Fix sketch: `literal_num(text, pos, dec)` with the computed `Dec`, use `node.dec` when present; the fold's `except Exception` then no longer needs to re-parse.

### PY-P10 [medium] [measured] Lexer: linear scan of 34 operators with a slice per candidate; per-character loops; redundant O(n) work in `Lexer.__init__`
- Found by: s1 P1, P2, P3.
- Where: `lexer.py:154-160` (`match_operator`, called from :146); `:97-152` (`lex_range`) and `:330-331` (`ascii_upper`); `:73-79` and `utf8.py:27-38`.
- Why/evidence: (P1) per operator token the loop does `len(op)`, a bounds check and a slice per operator until match; `+`, `(`, `,` sit at positions 22-32 so most tokens pay about 25 iterations. cProfile: `match_operator` + `len()` = 47% of tokenize time on typical rule text (0.976 s of 2.07 s), 55% on operator-dense input. With a 3/2/1-char set lookup, byte-identical token lists: 'big' rule text 1091 -> 777 ms, `1+1+...` (200k) 4182 -> 2630 ms. (P2) whitespace/comment/ident/number scanning iterate in Python with helper calls (3 calls per identifier char); a regex-driven main loop with explicit ASCII classes (`[A-Za-z_][A-Za-z0-9_]*`, `[0-9]+(?:\.[0-9]+)?`, `[ \t\r\n]*`, `str.find('\n', i, to)` for comments) is about 2x faster: prototype (`py1proto2.py`) gave identical token lists and error tuples on 1,278 inputs (probes + every `--- source` in conformance): big-rule text 466 -> 244 ms, `1+1+...` 200k 2310 -> 1340 ms, 200k identifiers 1695 -> 782 ms, 1 MB comment 237 -> 116 ms, 1 MB whitespace 216 -> 150 ms; `word.upper()` is safe on an already-ASCII identifier (`ascii_upper` runs a generator per identifier: 56,000 resumes in a 76k-token file). (P3) `to_code_points(source, None)` builds a list of `ord()` values just to discard it (57 ms on 1 MB, 12,500 lines) where `utf8.validate_text` does it in C (0.0003 ms), and the line-start table is a Python `enumerate` loop (112 ms vs 5 ms with `str.find` hops): about 170 of the 210 ms `Lexer.__init__` takes, i.e. 40-80% of lexing a whitespace/comment-heavy 1 MB source; microseconds for typical rules.
- Fix sketch: `s[i:i+3] in OPS3`, then 2, then `s[i] in OPS1` (sets built from OPERATORS at import); regex-driven main loop keeping explicit ASCII classes (no `\w`, `\d`, `\s`); `validate_text(source)` and a `find` loop (or lazy Pos).

### PY-P11 [medium] [measured] Text builtins loop per character in Python: PADL/PADR (about 800x), UPPER/LOWER, TRIM/LTRIM/RTRIM
- Found by: s4 P1, P2, P5.
- Where: `text.py:135` (`''.join(fill[i % len(fill)] for i in range(need))`), `:99-111` (`_ascii_case`), `:76-85`.
- Evidence: `PADL("a", 1000000, "xy")` 245 ms; 5M 1.1 s; `(fill * (need // len(fill) + 1))[:need]` takes 0.3 ms for 1M, verified identical for fill lengths 1-3. UPPER/LOWER on 1M chars: 287 ms (ASCII) / 273 ms (mixed); for pure ASCII `s.isascii()` then `s.upper()`/`s.lower()` is byte-identical and takes 2.4 ms; for non-ASCII `s.translate(table)` with a fixed 26-entry `{ord: ord}` table takes 63 ms (4x faster), still ASCII-only. TRIM family: 1M chars of padding 195 ms vs `s.strip(' \t\r\n')` (explicit set, so not the Unicode-whitespace trap) 8 ms.
- Fix sketch: as above; `if s.isascii(): return s.upper() if up else s.lower()` else `translate`; never call `upper()` on non-ASCII input. The pad rewrite also removes the memory blow-up in PY-C14.

### PY-P12 [medium] [measured] base64, CRC32, hex decode and byte-list conversions are per-byte Python loops
- Found by: s4 P3.
- Where: `binary.py` `_encode_base64` (57-68), `_decode_base64` (71-97), `_crc32` (115-121), `_from_hex` (37-49), `BTL`/`_ltb` (124-146).
- Evidence (1 MB input, including about 3 ms setup): ENCODE_BASE64 730 ms (`base64.b64encode`: 1.8 ms); DECODE_BASE64 about 0.9 s beyond the encode; CRC32 539 ms (`zlib.crc32`: 0.11 ms); FROM_HEX 580 ms; BTL 2.2 s and LTB 3.3 s (a `Value.int` per byte).
- Fix sketch (byte-identical): `base64.b64encode(b).decode('ascii')` for encode. For decode, validate the whole string once with an explicit ASCII regex (`(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?` via `fullmatch`) then `base64.b64decode` (trailing-bit handling matches: neither rejects non-zero pad bits). Hex: `fullmatch('[0-9a-fA-F]*')` then `bytes.fromhex`. `zlib.crc32` is the same CRC-32/ISO-HDLC, but the file docstring keeps the table version on purpose, so that one is a project decision.

### PY-P13 [medium] [measured] `RREPLACE` re-parses the replacement string for every match and does extra slicing
- Found by: s4 P4.
- Where: `regex.py:407-435` (`_expand`) and `449-455` (loop).
- Evidence: `RREPLACE("a","bb",REPEAT("a",1000000))` 2.9 s; a prototype treating a `$`-free replacement as a constant and keeping `rx.search` in a local: identical output in 1.0 s (2.5x). A replacement with `$0` takes 4.1 s.
- Fix sketch: pre-parse the replacement once per call into literal chunks and group numbers (keeping "group > rx.groups -> E_BAD_ARG only when a match occurs"), then per match only join the parts.

### PY-P14 [medium] [measured] Nested-loop LINK re-creates the right row's table alias for every pair
- Found by: s5 P3.
- Where: `structure.py:1227-1247`.
- Why/evidence: `ensure_row_table_alias(right_item, b2)` (shape lookup, list copy, new `Value`) runs inside the inner loop, n*m times, although it depends only on the right element; the three per-pair `frame[...]` stores and the `iter_collection_items` restart per left row repeat too. cProfile of `LINK(A, B, _1["x"] < _2["y"])`, 500x500: `ensure_row_table_alias` 250,501 calls, 2.2 s of 10.7 s (21%) and 250k `_from_shape` allocations; unprofiled 1500x1500 with an always-FALSE predicate takes 8 s (3.6 us/pair) before the predicate does any work.
- Fix sketch: `rights = [alias(r) for r in iter_collection_items(right_value)]` once before the outer loop (rows are immutable to the join; the equi path already does this once per right row); joined rows byte-identical.

### PY-P15 [medium] [measured] 2-argument `BUCKET` deep-copies every member row
- Found by: s5 P4.
- Where: `aggregate.py:623`.
- Why/evidence: `Value._list_owned([row.clone() for row in g['rows']])` deep-copies each row although the projected spelling, MAP, FILTER, TAKE and DISTINCT hand out the same objects; the consumer copies again on assignment. 100k 4-field rows, 1000 groups: bare `BUCKET(_["k"])` 1.78 s vs `BUCKET(_["k"], COUNT(_))` 0.94 s: about 0.85 s (8.5 us/row) is the clone.
- Fix sketch: drop `.clone()` (after settling PY-C38) or clone lazily.

### PY-P16 [medium] [measured] `execute_hybrid` deep-clones the whole context on every call
- Found by: s7 P1.
- Where: `hybrid.py:851` (`root = context.clone() ...`).
- Evidence: 100,000-row context (`py7/t7.py`): `context.clone()` 1.06 s; `execute_hybrid` with 10 SQL rows and that context 1.37 s vs 0.0005 s with an empty context.
- Fix sketch: build a shallow wrapper record (children shared) plus `_INPUT`, or set and remove `_INPUT` on the caller's context (see PY-C51 for semantics).

### PY-P17 [medium] [reasoned; prototype 2-7%] The interpreter loop pays an extra Python call and a long if-chain per node
- Found by: s3 P3 (with P4, P5, P6 as smaller items, see PY-P29).
- Where: `eval.py:163-173` (`eval_node`) -> `:230-307` (`_dispatch`), `:342-442` (`_eval_binary`), `:176-227` (`_eval_math_plan`), `:299-305` (strict call).
- Why: every node costs `eval_node` + `_dispatch`; `_dispatch` tests 11 tags before reaching `'call'`; `_eval_binary` walks an `op ==` chain (a `<` after about 10 tests); every strict call allocates an `Args` plus a list and loops `args.val(i)`; the plan executor compares `op` against up to 18 `IntEnum` constants per step. cProfile on the reference rule (13 nodes, 5 binaries): `_eval_binary`, `_dispatch`, `eval_node` are the top three by tottime (0.56 s, 0.47 s, 0.39 s of 4.2 s profiled).
- Evidence: reasoned from the profile; a conservative prototype (fuse eval_node/_dispatch, test `call`, `bin`, `var`, `text` first, strict args in place, no validate on text literals) measured only 2-7% (rule 0.93x, FILTER 0.95x, MAP 0.95x, SUM 0.98x, interleaved A/B, min of 15). Bigger wins need a structural change (compile each physical node once into a closure, or a per-node handler slot chosen in `optimize_tree`); a rewrite, not measured. It would also help PY-C1(d) (fewer frames).
- Fix sketch: physical-tree "compile to closures" or per-node `ev` handler; jump-table the math-plan opcodes (`handlers[step.op]`). Keep error positions as now.

### PY-P18 [medium] [measured] SQL: constant-subtree validation re-evaluates each constant subtree once per ancestor level (O(depth x size))
- Found by: s6 P2.
- Where: `translator.py:261-267` (`_node`: `is_constant` walk + `validate` -> `eval_node` at EVERY compound constant node, deliberately: see the comment about `FALSE AND (1/0 > 0)`).
- Evidence: `LEN(UPPER(UPPER(...UPPER(REPEAT("a", 50000))...))) > 0`, no columns: translate/eval time ratio 4.8x at depth 2, 5.1x at 4, 8.2x at 8, 11.1x at 16 (1.78 s vs 0.16 s); grows linearly with depth, so up to roughly 50x at the parse cap of about 99.
- Fix sketch: per-translation memo keyed by node identity (constant nodes are pure), each node evaluated once and ancestors reuse child results; keep the per-node error check by having the memoised evaluator raise the innermost failing node's error the first time; also cache `is_constant` per node id.

## Low-medium

### PY-P19 [low-medium] [measured] `mod` uses `%` on huge ints, about 9x slower than `divmod` on CPython 3.12+
- Found by: s2 P4. Where: `decimal.py:323` (`A % B`); `div` (304) and `round` (336) already use `divmod`.
- Evidence: 3,321,000-bit % 1,660,000-bit: `A % B` 5.84 s, `divmod(A, B)[1]` 0.65 s, `A // B` 0.88 s (Python 3.14.7); a single `X % Y` with 1M-digit operands (both legal) costs about 5.4 s (`D.mod`, measured directly); `/` on the same sizes 0.95 s. Cause: since 3.12 `divmod`/`//` dispatch to the sub-quadratic `_pylong` path but `%` does not.
- Fix sketch: `return make(a.neg, divmod(A, B)[1], s)`; byte-identical.

### PY-P20 [low-medium] [measured] SQL `text_literal` escapes character by character in Python
- Found by: s7 P2. Where: `emit.py:113-135` (re-sorts the key list per call; loops `for k in keys: startswith` per character).
- Evidence: 1 MB text with no special characters 0.44 s (sqlite) / 0.57 s (mysql); 1.1 MB with quotes/backslashes 0.49 s / 0.70 s; a 3-character literal costs 7 us (`timeit`). Inline mode with large value bindings pays this per literal.
- Fix sketch: per-dialect `str.translate` table when all keys are single characters (all shipped dialects), or one compiled alternation regex sorted longest-first with a single `sub`; add a `not any(k in text)` fast path. Bytes identical.

### PY-P21 [low-medium] [measured] `_contains_unsupported_sql` ignores the `ops` table, so a MAP with a bit-operator pair loses the fall-through
- Found by: s7 P4. Where: `hybrid.py:220-247` looks only at `funcs`; `BAND`, `BOR`, `BXOR` have withdrawn `ops` entries in every dialect and `bin` nodes are never classified.
- Evidence: PostgreSQL: `R .> SORT_BY(_["id"]) .> MAP(RECORD("id", _["id"], "n", _["name"], "b", TO_HEX(FROM_HEX("0f") BAND FROM_HEX("3c"))))` plans as prefix `SELECT r.* FROM r ORDER BY id` (all columns transferred) instead of projecting `id`, `n` in SQL as the `PADL` variant does (`py7/t18.py`). Correct result either way; more data moves.
- Fix sketch: classify `bin`/`un` nodes against `ops` (arity and withdrawal) the way calls are, or ask the translator to render each pair alone.

## Low

### PY-P22 [low] [measured] Comparisons and additions across a huge scale gap build `10**gap`; the power cache holds only one large exponent
- Found by: s2 P6. Where: `decimal.py:88-110` (`_pow10`, cache weight cap 1,048,576 digits), `244-249` (`_aligned`), `277-287` (`cmp`), `230-234` (`is_integer`).
- Evidence: `cmp(1, 0.<999,999 zeros>1)` 0.30 s, and 0.32-0.33 s again for gaps of 999,999 and 999,998 (each new large exponent clears the cache); `cmp` of scale 500,000 vs 1: 0.26 s. `==`, `<`, `MIN`, `SORT` keys and `ISNUM`-guarded joins go through this.
- Fix sketch: in `cmp`, compare adjusted exponents (`_num_digits(digits) - scale`, or bit_length bounds) first and align only when within 1; in `is_integer` reject via `(digits & -digits).bit_length() - 1 < scale` (trailing zero bits) before computing `10**scale`.

### PY-P23 [low] [measured] Over-cap products are computed in full before being refused
- Found by: s2 P7. Where: `decimal.py:273-274` (`mul` then `guard`).
- Evidence: 1M-digit x 1M-digit: 3.7 s to produce an `E_RANGE`; `POWER(POWER(10,20),100000)` 1.6 s to fail at its last squaring.
- Fix sketch: pre-check `a.digits.bit_length() + b.digits.bit_length() - 1` against the bit length of `10**(MAX_INT_DIGITS + scale)` before multiplying (the product has at least that many bits), keeping the exact `guard` afterwards.

### PY-P24 [low] [reasoned] Small per-value overheads in `value.py`
- Found by: s2 P8. `Value.__init__` assigns nine slots (421 ns for a bare scalar `Value`) although most scalar Values never use `children/shape/storage/list_keys/_list_key_map` (a light scalar subclass or lazy attributes would roughly halve scalar allocation). `is_vacuous()` (`value.py:275-280`) formats a numeric Value (`.scalar` -> `D.format`) before testing the first character; a `_dec_val is not None` early `False` avoids a full int->str for `???` on million-digit numbers. `_structural_hash_at` (`value.py:835-839`) calls `hash(str(i))` per element of every dense list; a precomputed, on-demand-grown table of index hashes avoids one allocation per element in DEDUPE/EQL-bucket paths. `check_sized_int` (`builtins/number.py:24`) builds its message with `f'{what} {n} ...'`, i.e. `str()` of a possibly million-digit integer, on the error path.

### PY-P25 [low] [measured for SUM; rest reasoned] SUM allocates a `Dec` per addition; aggregate `walk` machinery costs about 30% of trivial bodies
- Found by: s5 P5. Where: `aggregate.py:298-306` (`_sum`), `:47-71` (`walk`), `:258-259` (`keep`), `:410` (`do_sort` per-element frame).
- Evidence: `R .> SUM(_["n"])` over 100k rows: `add`+`make`+`__init__` about 40% (0.54 s of 1.4 s profiled). `walk` re-tests `'_K' in frame` per element, calls a `visit` closure that calls `keep`, `iter_elements` is a generator; `do_sort` builds a new frame dict and a `Value.text(k)` (with `validate_text`) for `_K` per element even when the key body does not mention `_K` (TOP already checks `node_contains_var`). Bare MAP over 100k rows is 0.2-0.28 s, so at most tens of percent.
- Fix sketch: accumulate SUM as a signed int plus a scale (materialise one `Dec`, keeping the digit-cap check where it would trip); hoist the `_K` test and frame out of the per-element path in `do_sort`; inline `keep`.

### PY-P26 [low] [reasoned] DISTINCT/BUCKET hash whole records; `first_collection_item` materialises a dict-mode record
- Found by: s5 P6. Where: `value.py:810-848` (used by `structure.py:126`, `aggregate.py:598`); `structure.py:29-35`.
- Evidence: `structural_hash` walks each row in Python (3.8 us per 4-field record, `eql` 5.2 us) then buckets on the int; for TEXT/number keys a plain `dict` keyed on the scalar would use C hash/eq (0.14 us). DISTINCT of 100k unique records 1.6 s (measured) of which the hash is roughly a quarter; the rest guessed. `first_collection_item` builds the full `elements()` list of a dict-mode record only to read entry 0 (called twice per LINK plus `_first_keys`).
- Fix sketch: fast path for childless TEXT/BOOL keys (key = `(kind, scalar)`), and `next(iter(value.children.values()))` in `first_collection_item`.

### PY-P27 [low] [measured] Hybrid: quadratic prefix search and repeated stage 1 in `plan_hybrid`
- Found by: s7 P3. Where: `hybrid.py:807-829` (every candidate prefix runs `helpers.wrap`, stage 1, the translator, and `helpers.tables` runs stage 1 again), `:620-634` (`_referenced_assignments` is a fixed point re-walking values each pass).
- Evidence: 90 chained MAP steps with an untranslatable tail: 0.06 s at 25, 0.23 s at 50, 0.72 s at 90 (`py7/t6.py`, roughly quadratic); a 300-statement helper chain planned in 0.94 s vs 0.15 s at 200 (`py7/t15.py`, dominated by `build_pipeline`/`copy_node`). Realistic pipelines (under 10 steps) cost well under 1 ms.
- Fix sketch: translate the longest prefix once and shrink; memoise stage 1 for shared prefixes; process `_referenced_assignments` in reverse program order (one pass).

### PY-P28 [low] [measured share, reasoned gain] SQL translator per-translation overhead: template re-tokenising, repeated tree copy in stage 1, `chain()` recomputation; needle/separator re-rendered per element
- Found by: s6 P3, P4. Where: `emit.py:242-374` (`fill` scans each template char by char, calling `push` about 700 times per 10-clause rule), `normalise.py:181-216`, `map.py:237` (`chain()` rebuilt on every `lexical()`/`entry()` call); `translator.py:774, 1749` (`_in_operator`/`_join_aggregate` re-render the needle/separator per element).
- Evidence: cProfile of a 10-clause rule x100: `fill` 36% cumulative, `chain` 10%, `dataclasses.replace` 7%. Wall time is small (1.7 ms per translation of that rule; caching `chain()` alone made no measurable difference); only matters for hot paths translating thousands of rules. The per-element re-render is required today by the slot-numbering rule, so output size is O(n x needle), not avoidable without changing the part-list design; do NOT "fix" it by reusing one Fragment (comment at 767-773: breaks `params`).
- Fix sketch: pre-tokenise templates into (literal, slot) segments once per string (dict cache); return the original node from `_substitute` when no child changed.

### PY-P29 [low] [reasoned/guessed] Evaluator micro-costs: boolean-context `Value` allocation, assignment/literal paths, `_bitwise`
- Found by: s3 P4, P5, P6.
- (a) [reasoned/guessed] `eval.py:346-352, 397-413, 337-338`: `AND`/`OR`/`NOT`/comparisons return `Value.bool(...)` that consumers immediately `as_bool`; an `eval_bool(node, ctx)` fast path for `bin`/`un` comparison nodes returning a Python bool would skip two or three Values per row. Estimated 5-10% of a predicate-heavy FILTER, guessed, not measured.
- (b) [reasoned] `eval.py:238-239` `Value.text(node.v)` re-validates a lexer-validated literal on every evaluation (120,000 `validate_text` calls in a 20,000-row MAP visible in cProfile; use `Value(TEXT, node.v)` as the `num` branch does); `:573-599` `_resolve_target` re-walks from the root for every index (O(k^2) for a k-deep target, k <= 200); `:513/:538` compound assignment walks the path twice plus once in resolve.
- (c) [guessed] `eval.py:494-498` `_bitwise` builds bytes with `bytes(x & y for x, y in zip(a, b))`, about 50x slower than `(int.from_bytes(a,'big') & int.from_bytes(b,'big')).to_bytes(len(a),'big')` for large BIN values; only matters for kilobyte-sized operands.

### PY-P30 [low] [reasoned] Per-call overhead in regex `_compile` under the `i` flag and in `_args_for`
- Found by: s4 P6. Where: `regex.py:328-333` and `380`.
- Evidence: 100,000-row FILTER: RMATCH costs 11.1 us/row (14.0 us/row with `i`) vs 12.4 us/row for a bare `LEN` comparison, so the builtin is a small share; the flag path adds about 3 us/row. With `i`, every call loops over all pattern characters (`ord(ch) > 0x7F`) before consulting the cache and always `translate`s the subject.
- Fix sketch: `pattern.isascii()` instead of the loop; `subject if subject.isascii() else subject.translate(_FOLD)`; put the cache lookup before the flag check for the no-flag case.

### PY-P31 [low] [measured/reasoned] Lexer: text literal bodies copied char by char; nested interpolation rescanned per level
- Found by: s1 P5, P6. Where: `lexer.py:164-182` (`lex_raw`), `:184-214` (`lex_quoted`, `buf.append(c)` per character); `:204-209` with `:245-291` (`match_brace` scans the whole body including nested literals, then `lex_range` lexes it, and nested literals re-run `match_brace`).
- Evidence: 1 MB text literal tokenizes in about 530 ms, 1 MB raw literal about 480 ms (measured), almost all in the append loop; scanning to the next interesting character with `str.find` or a compiled `[^"\\{]*` regex and appending slices gets near memcpy speed. Nested interpolation is quadratic in depth (50 levels: 4.6 ms, 100: 14 ms, 150: 28 ms per compile) but currently capped by PY-C1(c); it becomes the cost once that is fixed with an iterative scanner.
- Fix sketch: slice-append scanning; a single-pass lexer that pushes/pops interpolation contexts.

---

# Cross-host findings

Per CLAUDE.md "The one rule": spec first, then conformance cases, then every host, then `tools/check.sh`. These are the Python findings that the reviewers say are shared with (or decided by) other hosts, so the fix must be spec-led. "Hosts" is who the reviewers say is affected; unchecked hosts are not asserted.

| Finding | Hosts affected (per reviewers) | Spec/decision needed | Suggested first case |
|---|---|---|---|
| PY-C1 RecursionError vs E_DEPTH | Python only for the crash; JS `??` chain shares the shape (s1) | none for the crash; add limit cases so the other hosts are held to the same E_DEPTH columns | `lim.pipe-depth[-just-under]`, `lim.coalesce-chain`, `lim.interp-depth`, 198-stage `.> SORT` |
| PY-C3 regex catastrophic backtracking | JS (40 s), Lisp; PHP bounded (0.38 s); C++ not reported | choose static ambiguity rule in `validate()` or a step budget + new error code | `re.dos.nested-plus`, `re.dos.overlapping-alt` |
| PY-C4 quantified-group semantics | PCRE side: Python, PHP, Lisp; ECMAScript side: JS, C++ | reject empty-matching quantified group bodies and captures inside quantified groups (or define ECMAScript semantics) | `re.groups.empty-iteration`, `re.groups.capture-reset`, `re.replace.empty-iteration` |
| PY-C5 SQL bucket `SUM` idiom | PHP `Translator.php:708`, JS `translator.mjs:890` use the same idiom (s6: expected to emit the slot number as SQL text; UNCONFIRMED) | none; bug fix in all three | bucket SUM with `_["amount"] * 2` / `+ 1`, params mode |
| PY-C6 SQL exponential helper inlining | not checked in other hosts (s6 ran Python only) | specify a rendered-size / parameter-count bound and its error | 25-line doubling chain expecting a refusal |
| PY-C7 math plan operand snapshot | all five hosts print 3 | SPEC 3.4 vs plan design: keep handles or refuse to plan | `alias.arith-operand-sees-later-mutation` -> 12 |
| PY-C8 coercion/error precedence | all five hosts | SPEC 6.2: left operand coerced before right is evaluated (s3) or operands evaluated then coerced (s2) | `A = "x"; A + (1/0)`, `A += (1/0)`, `X = "x"; A[X + (1/0)] = 1`, shallow vs dead-deep-branch pair |
| PY-C9 SORT+TAKE fusion | all five hosts agree on the (wrong) fused answer | SPEC 7.3: fuse only when `n` is a numeric literal | `LIST(1,2) .> SORT_BY(1/0) .> TAKE(-1)` -> E_DIV_ZERO; assignment-in-n -> `[1, 2]` |
| PY-C10 mutation during aggregate iteration | JS, PHP crash; C++, Lisp use snapshot semantics | pin snapshot semantics | `R = RECORD("a",1,"b",2); SUM(R, x, (R["c"] = 10; x))` -> 3 (+ MAP/FILTER) |
| PY-C12 equi-join digit cap | PHP and Lisp share the bypass (s5: both print 0; s2: PHP); JS, C++, Go raise E_RANGE | none (SPEC 6.4 already says E_RANGE); fix in Python, PHP, Lisp | `rel.link.equi-key-over-digit-cap` |
| PY-C13 SORT comparator total order | Lisp, C++, Go/others give different orders on mixed keys | define a total order in SPEC 7.3 | `agg.sort.mixed-number-and-text-keys` |
| PY-C14 unbounded REPEAT/PAD | JS (`RangeError`), PHP (fatal memory) also die; `REPEAT("", 10^30)` is `0` in JS/PHP, OverflowError in Python | add a text-length cap to SPEC 6.4 / `spec/limits.json` | `text.repeat.huge-count`, `text.repeat.empty-huge-count` |
| PY-C15 regex nesting depth | JS, Lisp accept 1000; PHP, C++ reject E_REGEX_SYNTAX; Python crashes | count group nesting in shared `validate()` | `re.depth.nested-groups-cap` |
| PY-C17 invalid UTF-8 in source | cpp E_UTF8; JS/PHP silently substitute U+FFFD (s1) | SPEC 2 wording holds; fix JS/PHP and Python | API/CLI-level test (no `.selt` form) |
| PY-C22, C23, C24, C48, C49 hybrid/SQL map issues | C22, C23, C24, C48 also in JS (s7); C49 also PHP and JS | planner guard (C22), 5-arg rewrite (C23), binder shapes (C24) | cases in `25-hybrid-plans.sqlt` (see table) |
| PY-C26 multiple `_` placeholders | py/js/php/cpp/lisp evaluate once per `_`; Go rejects | SPEC 5.10 rule 3 ("one of") | pipeline case with two `_` |
| PY-C27 lone-surrogate E_UTF8 position | JS same as Python | SPEC 2/6.3 position wording | API-level test |
| PY-C30 assigned-value depth | all five hosts | SPEC 6.4: check `len(path) + depth(value)` | repro with `1` tail expecting E_DEPTH at target |
| PY-C31 `dependencies()` order-insensitivity | all five hosts | SPEC 8 | `A + 1; A = 2` |
| PY-C35 literal regex compile-time check | all five hosts | implement or reword errors.md / SPEC 7.8 | `re.literal-checked-at-compile` |
| PY-C36 `LTB(LIST())` | all five hosts agree | SPEC 7.7 empty-list rule | `bin.ltb.empty-list` |
| PY-C37 bare BUCKET key-text collision | all six hosts agree | SPEC 7.3 | scalar-with-children key in `16-bucket.selt` |
| PY-C38 aliasing vs copying | py/js/lisp/go vs php/cpp differ per builtin (see entry) | SPEC 3.4: name exactly which builtins copy; fix contributing.md sentence | LIST / RECORD / SORT / BUCKET aliasing cases |
| PY-C39 BUCKET scalar source | only C++ follows the spec text | SPEC 7.3 (spec already supports C++) | `rel.bucket.scalar-source` |
| PY-C40 direction/limit on empty list | PHP differs from py/js/lisp/cpp | SPEC 7.4 sentence | `SORT_BY(LIST(), _+0, "X")` |
| PY-C42 SQLite expression depth | cross-host decision (balanced fold changes emitted bytes and `sql/cases`) | balanced fold or documented refusal | large IN list case |
| PY-C19, C20, C21, C41, C43, C44, C45 SQL translator rules | shared translator design; other hosts not checked | rules are cross-host by construction (data and walk transcribed per host) | see cases table |
| Observations from other hosts' behaviour (not Python bugs) | Lisp accepts `^+` and `$*` (`RMATCH('^+', 'aaaaaa')` TRUE) while py/js/php/cpp raise E_REGEX_SYNTAX; C++ rejects `(?:^)*a` (`error_badrepeat`) while py/js/php/lisp return TRUE; PHP `RREPLACE('(a*?)+', '<$1>', 'aaa')` differs from Python/Lisp; Lisp `HAS(LIST(1,2), 2)` differs from all others; Lisp evaluates a side-effecting equi-join key once per row (`N=0; LINK(..., _1["id"] == (N = N + 1; _2["id"])); N` gives 2, others 4); Lisp `SORT("x")`/`TOP("x",1)` return 0 elements; PHP evaluates SORT_BY/TOP_BY direction/limit for an empty list; JS `DROP+DROP` sum beyond the safe-integer range (Python unbounded int, noted for the JS reviewer) | for the respective host reviewers | n/a |

---

# Suggested conformance cases

Names are ideas in the `category.thing.detail` scheme; "expected" is what the reviewers consider correct or the value the decision should pin. Cases marked (spec) need a spec decision before the expected value is fixed.

| Name idea | Expression | Expected |
|---|---|---|
| `lim.pipe-depth-just-under` | 190 x `1 .> MAX(` ... `)` nested | value `1` |
| `lim.pipe-depth` | 250 x nested `1 .> MAX(` ... `)` | `E_DEPTH` at the pinned column (other hosts: line 1 column 1792 for n=250) |
| `lim.coalesce-chain` | `a` followed by 1500 x `??a` | `E_DEPTH` at eval position (other hosts column 598) |
| `lim.interp-depth` | >= 60 nested `"{...}"` | `E_DEPTH` at the pinned column |
| `lex.unterminated.deep-brace-run` | 600 x `"{` | `E_UNTERMINATED` at the innermost `{` |
| `lim.sort-chain-198` | `LIST(1,2)` + 198 x ` .> SORT` (also `MAP(_)`, `TOP_BY`) | the list |
| `alias.arith-operand-sees-later-mutation` | `A = LIST(1,2); A + LEN((A[1] = 10))` | `12` |
| `num.coerce.left-before-right-eval` (spec) | `A = "x"; A + (1/0)` ; `A = "x"; A += (1/0)` ; `X = "x"; A[X + (1/0)] = 1` | decided error/position; plus shallow / dead-deep-branch pair `IF(TRUE, A + (1/0), <260-term chain>)` |
| `num.div.tie-at-eleventh-digit` | `1/2048` | `0.0004882813` |
| `rel.optimiser.take-n-not-literal-error` | `LIST(1,2) .> SORT_BY(1/0) .> TAKE(-1)` | `E_DIV_ZERO` |
| `rel.optimiser.take-n-effect` | `A = 1; L = LIST(1,2,3); L .> SORT_BY(_ * A) .> TAKE((A = -1; 2))` | `[1, 2]` |
| `agg.iterate.record-mutated-in-body` (spec) | `R = RECORD("a",1,"b",2); SUM(R, x, (R["c"] = 10; x))` (also MAP, FILTER) | `3` (snapshot) |
| `rel.select-cols.duplicate-column` | `SELECT_COLS(LIST(RECORD("a",1,"b",2)), "a", "b", "a")` (uniform and non-uniform row shapes) | one `a`, one `b` |
| `rel.link.equi-key-over-digit-cap` | `BIG = PADL("9", 1000005, "9"); LINK(LIST(RECORD("a", "1")), LIST(RECORD("b", BIG)), _1["a"] == _2["b"]) .> COUNT()` | `E_RANGE` |
| `agg.sort.mixed-number-and-text-keys` (spec) | `("10","1a","2","9","9a","100") .> SORT() .> JOIN(",")` (+ `TAKE(SORT(X),4)` vs `S = SORT(X); TAKE(S,4)`) | one decided order, equal for fused and unfused |
| `rel.bucket.same-key-text` (spec) | `A="x"; A["k"]=1; R = LIST(A, "x", A) .> BUCKET(_); ...` | 3 rows in, 3 rows out |
| `rel.bucket.scalar-source` | `BUCKET("x", _)` ; `BUCKET("x", _, COUNT(_)) .> COUNT()` | `{"x"={"1"="x"}}` ; `1` (spec text) |
| `agg.sort.direction-on-empty-list` (spec) | `SORT_BY(LIST(), _+0, "X")` | decided (`-` or `E_BAD_ARG`) |
| `alias.builtin-copy-*` (spec) | `A = LIST(RECORD("k",1,"x",1)); R = LIST(BUCKET(A, _["k"]), (A[1]["x"] = 5; 0)); R[1]["1"][1]["x"]`; `LIST(LIST(A), (A[1]["x"]=5;0))`; RECORD, SORT, SORT_BY variants | decided 1 or 5 per builtin |
| `text.repeat.huge-count` (spec) | `REPEAT("a", 100000000000)` | `E_RANGE` (or the cap's code) |
| `text.repeat.empty-huge-count` | `LEN(REPEAT("", 1000000000000000000000000000000))` | `0` |
| `re.dos.nested-plus` (spec) | `RMATCH('^(a+)+$', REPEAT('a', 40) & '!')` | returns or raises within a bound |
| `re.dos.overlapping-alt` (spec) | `RMATCH('(a|aa)+$', REPEAT('a', 40) & 'b')` | returns or raises within a bound |
| `re.groups.empty-iteration` (spec) | `RGROUPS('(|a)+', 'aa')` | one decided answer (py `{"",""}`, js/cpp `{"aa","a"}`) |
| `re.groups.capture-reset` (spec) | `RGROUPS('(?:(a)|(b))*', 'ab')` | one decided answer (py group 2 `"a"`, js/cpp `""`) |
| `re.replace.empty-iteration` (spec) | `RREPLACE('(a*?)+', '<$1>', 'aaa')` | one decided answer |
| `re.depth.nested-groups-cap` | 1000 nested parentheses around `a`, and at/one past the cap | `E_REGEX_SYNTAX` or `E_DEPTH` past cap |
| `re.class.literal-open-bracket` | `RMATCH('[[]', '[')`, `[a&&b]` etc. | `TRUE`, no diagnostics |
| `re.literal-checked-at-compile` (spec) | `IF(FALSE, RMATCH('(?=a)', 'a'), 1)` | decided (`1` today on all hosts; `E_REGEX_SYNTAX` at compile if spec is kept) |
| `bin.ltb.empty-list` (spec) | `LTB(BTL(""))` | decided (empty BIN by symmetry, or `E_NO_SCALAR`) |
| `deps.read-before-assign` (spec) | `A + 1; A = 2` | decided dependency list |
| `pipe.placeholder.multiple` (spec) | `C = 0; R = (C += 1) .> MAX(_, _); C` | decided (`2` today, Go rejects) |
| `lim.assigned-value-depth` | `A[1]...(150) = 1; B[1]...(100) = A; 1` | `E_DEPTH` at the target column |
| SQL `agg.bucket-sum.expr-body` | `R .> BUCKET(_["cat"], RECORD("k", _K, "s", SUM(_, _["qty"] * 2)))` and `+ 1`, params mode | translates; bindings() count equals placeholders |
| SQL `sql.helper-doubling-chain` (spec) | `V0 = C + 1; V1 = V0 + V0; ... V25; V25 > 0` | refusal (`E_SQL_DEPTH` or a new size code) |
| SQL `agg.scope.outer-key-in-inner-list` | `ANY((10,20), x, ANY((_K, "9"), y, y $== "2"))` | not constant FALSE; matches SEL (TRUE) |
| SQL `norm.inline.binder-does-not-capture-helper` | `A = C; ANY((10,20), C, A == C)` | not constant TRUE |
| SQL `agg.nested-default-binder` | `ANY((C,D), ALL((_, 5), _ > 6))` ; `ALL((C,D), x, ALL((x,7), x, x > 1))` | translates (no spurious `E_SQL_DEPTH`) |
| SQL `agg.join.bool-element-refused` | `JOIN((T,F), ",")` | refusal (SEL: `E_NOT_TEXT`) |
| SQL `agg.join.bin-separator-refused` | `JOIN((X,T), ",")`, `JOIN((T,T), F)` | refusal |
| SQL `op.in.exact-needle-number-item` | `T IN ("a", 3)` with exact TEXT needle, mariadb and sqlite | item cast; `"3.0" IN ("a", 3)` FALSE |
| SQL `agg.in-relation.bool-needle-refused` | `F IN S` with BOOL `F`, TEXT scalar relation | refusal |
| SQL `slice.huge-count` | `R .> TAKE(99999999999999999999999)` on postgresql | no `LIMIT` past 2^63 (spec) |
| SQL `sql.identifier-nul-empty` | `R .> MAP(RECORD("a\u{0}b", _["id"]))`, `RECORD("", ...)` | refusal |
| SQL large IN | `T IN ("v0", ..., "v1199")` on sqlite | works (balanced fold) or documented refusal (spec) |
| SQL `sql.deep-helper-chain` | 400-step helper chain `A0 = ...; A1 = A0 .> TAKE(11); ...` | refusal / `pure_memory` plan, no RecursionError |
| Hybrid `plan.filter-then-key-read` | `R .> FILTER(_["id"] > 2) .> MAP(RECORD("id", _["id"], "k", _K))`; `FILTER(sql) .> FILTER(unsupported)` | not split after FILTER, or original keys kept (k = 3..6) |
| Hybrid `plan.link-after-split-keeps-root-name` | `R .> SORT_BY(_["id"]) .> LINK(S, PADL(_1["cat"], 1, "0") $== _2["scat"]) .> MAP(RECORD("id", _["R"]["id"], "l", _["S"]["label"]))` | same as `run()` |
| Hybrid `plan.literal-helper-named-like-binder` | `N = 5; R .> FILTER(...) .> SORT_BY(N, N["id"], "DESC") ...`; BUCKET(4), TOP_BY(5), LINK(5) variants | same as `run()` |
| Hybrid `plan.source-tables-binder-name` | `R .> FILTER(S, S["ID"] > 1)` | `source_tables == ['r']` |
| Hybrid `plan.fallthrough-take-error` | `R .> SORT_BY(_["id"]) .> MAP(RECORD(..., "rep", REPEAT(_["name"], _["amt"]))) .> TAKE(2)` with a bad `amt` row | `E_NOT_NUM` like `run()` |
| Register `--- register` guard twice | translate twice with a bad `numericGuard` | ValueError both times |
| Register `--- register` refusals | `textQuote` overridden without `textEscape`; `textEscape` `{}` | refused |
| Python-only tests (not `.selt`) | pickle round-trip of `SelError`; thread test asserting `gc.isenabled()`; lowered `int_max_str_digits`; `Value.scalar` setter; CLI CRLF/invalid-UTF-8; `run(0.0)` | see entries PY-C16, C2, C25, C28, C17, C32 |

---

# Suggested fix order

Effort: S = a few lines, M = a focused change in one host, L = spec + all hosts, XL = design.

**1. Small, Python-only, do first (S, high value)**
- PY-C2: lock in `_gc.py` + thread stress test.
- PY-C5: splice `inner.parts` instead of `''.join` in `translator.py:821` (then check PHP/JS `implode`/`join` idiom, currently unconfirmed).
- PY-C16 `__reduce__` on `SelError`; PY-C11 dedupe `SELECT_COLS` columns; PY-C10 snapshot elements before running aggregate body; PY-C12 `len(text)` bound before `int()`; PY-C49 memoise-after-check; PY-C46; PY-C32; PY-C28.
- Perf quick wins with byte-identical output: PY-P4 (`copy_node` constructor), PY-P19 (`divmod`), PY-P11 (PADL/PADR/UPPER/LOWER/TRIM), PY-P12 (base64/hex), PY-P9 (carry `Dec` through folds), PY-P6 (`Value._num_owned`), PY-P14 (hoist right alias), PY-P15 (after PY-C38), PY-P10 lexer operator lookup and `Lexer.__init__`.

**2. Medium, Python-only (M)**
- PY-C1: cut frames per depth unit and/or a guaranteed recursion budget plus `RecursionError` -> `E_DEPTH` backstop at `Program.run`/`compile`/`translate` (also PY-C15 as a backstop). Highest correctness value; add the limit cases first so the other hosts are pinned.
- PY-C14 short term (catch `MemoryError`/`OverflowError`, empty-text shortcut); PY-C17 (bytes + strict decode in the CLI); PY-C25 (chunked int conversion); PY-C33/C34.
- PY-P1 (precomputed sort keys, homogeneous classes only) and PY-P2 (cache `exec`'d plans, LRU `_SHAPES`): the two largest measured runtime wins.
- PY-P3 (linear `fold_pairwise` with identical bytes), PY-P5 (plain slots classes for `Dec`/`Pos`), PY-P7, PY-P8, PY-P13, PY-P16/P18.
- SQL correctness: PY-C18, C19, C20, C21 (all local to the translator and cheap once the case is written), PY-C22-C24 (hybrid; JS has the same bugs), PY-C47/C48, C43-C45.

**3. Spec-first, all hosts (L)** (see "Cross-host findings"; write the spec sentence and `.selt`/`.sqlt` case before touching any host)
- PY-C3 and PY-C4 regex DoS and quantified-group semantics (one `validate()` change for all hosts; also PY-C15 nesting cap and PY-C35 compile-time check).
- PY-C7 and PY-C8 arithmetic operand snapshot / coercion order (settle the ordering rule, then align the plan and the walker together; PY-P7 and PY-P17 change the same code).
- PY-C9 SORT+TAKE fusion guard (`numeric_literal`), PY-C13 total order for SORT (unlocks PY-P1 in its simplest form), PY-C38/C37/C39/C40 aliasing and BUCKET/SORT edge rules, PY-C30, PY-C31, PY-C26/C36.
- PY-C14 text-length cap; PY-C6 SQL rendered-size bound; PY-C42 balanced fold (changes emitted bytes and regenerated `sql/cases`), PY-C41.

**4. Larger optimisations (XL, optional)**: PY-P17 (compile physical nodes to closures: also reduces stack frames for PY-C1), PY-P26 (scalar-key fast paths for DISTINCT/BUCKET), PY-P27/P28 (translator/planner caches).

---

# Checked and found sound

Merged from the seven reviewers; these areas were probed and should not need re-reviewing.

**Front end (s1)**
- Precedence table vs `grammar.md` (NOT bp 7 vs unary minus bp 16; `NOT` refused inside comparison/additive operands as E_RESERVED; comparison non-associativity incl. word operators; `??`/`???` right-assoc; assignment right-assoc with E_BAD_ASSIGN at the target node); node positions identical to php/js/cpp on 141 + 46 probes (code and position), about 190 lexer/parser edge inputs total.
- Depth accounting (enter/leave on `parse_sequence`, `parse_primary`, prefix operators, index brackets, assignment; try/finally); NOT/`-`/`=`/`[ ]`/parens/call chains give E_DEPTH at the same columns as other hosts up to 3000-5000 repetitions. Only the constructs in PY-C1(a)-(c) escape.
- Lexer: ASCII-only digit/alpha/space predicates; NBSP/U+0085/U+000B/U+000C/U+2028/Arabic-Indic digits/non-ASCII letters E_SYNTAX like the other hosts; `_HEX_RE.fullmatch` and the length check precede `int(hexs, 16)`; `\u{...}` error codes/positions agree with php/js/cpp; code-point columns correct for non-BMP and CRLF/CR-only sources; number lexing (`1.`, `1..2`, `1.>ABS`, `007`); longest-match operators; comments and empty interpolation positions; `''` in raw strings.
- Reserved-word/function handling (`TRUE(1)`, `NULL()`, `AND(1,2)`, `1 .> AND`, lookup-after-argument-parse ordering, arity errors at the name token, pipeline forms) and `check_target` (`(A[1]) = 5` E_BAD_ASSIGN, `(A)[1] = 5` allowed) agree with the other hosts.
- 4301/5000/100000-digit numeric literals compile; 1M digits about 2.6 s. `utf8.py` decode diagnostics (overlong/surrogate/>10FFFF/truncated); no `errors='replace'` value escapes. `_limits.py` consumer uses `MAX_DEPTH` 200.

**Numeric core and values (s2)**
- `decimal.py` against SPEC 4: parse (ASCII regex + `fullmatch`, caps before `int()`), add/sub/mul/div (exact -> minimal scale, else 10 digits half away, ties `2r >= D`), mod, round, floor/ceil/trunc, power (0^0=1), trim_scale, format, cmp; about 60 edge expressions identical on py/js/php/cpp/lisp. `guard`'s `_MAX_INT_BITS = 3321929` is exact; digit-cap boundary probes agree on all hosts. No stdlib `decimal`, no float, no `round()`/`math.floor` in the core.
- `Value` has no `__bool__` but has `__len__` (a childless scalar is falsy); the whole suite (1091 cases) uses no implicit truthiness in the library (host code doing `if value:` would be wrong; consider `__bool__ = True`).
- `structural_hash` consistent with `eql`; `_eql_at`/`_clone_at`/`_dump_at`/`_to_native_at`/`_from_native_at`/`_structural_hash_at` carry a depth counter (E_DEPTH at 201); `bool`-is-`int` handled (`from_native`, `Value.int(True)` E_BAD_ARG, `Value.bin([True])` E_RANGE, floats refused); list index parsing rejects `01`, `1.0`, Unicode digits; append/insert converts to dict once; shared `RecordShape.key_map` never mutated; shape cache bounded (256 entries / 16k chars).
- `math_plan.py`: identity rules preserve value, scale and sign and still coerce the operand at load; `steps.pop()` bookkeeping; depth-counted `emit`; ops table completeness check. 24k randomised plan-vs-tree-walk expressions: equal in value, error code and position once undefined variables (PY-C8) were excluded; depth-boundary cases (199/200/201-term chains, 99/100 nested ABS) agree.
- `Program.run` GC pause is exception-safe and re-entrant in a single thread. `number.py` ROUND/POWER caps, `E_NOT_INT` before `E_RANGE`, MIN/MAX tie behaviour (first wins), SIGN/ABS/CEIL/FLOOR/TRUNC/CANON edge cases agree cross-host.
- Latent, not a bug today: `div` indexes `_POW10_SMALL[DIV_SCALE]` (table size 19); if `spec/limits.json` ever raises DIV_SCALE above 18 it becomes an IndexError (generated value is 10). `tools/benchmark-python-runtime.py` is fine as a measurement tool.

**Evaluator, registry, host API, optimiser (s3)**
- Assignment paths (SPEC 5.8): target resolved before RHS, each index evaluated once left to right, store lands at the re-derived path; 25 tricky programs identical in all five hosts. `=` clones; `,`/list building clones contributed children.
- Binder frames popped on error; `??`/`???` catch exactly E_NO_KEY and E_UNDEF_VAR; `ctx.depth` restored in `finally`; E_DEPTH boundary programs (19 families x n=190..205) give identical codes/positions between optimised and unoptimised walk.
- Optimiser soundness: about 45,000 random pipelines and 38,000 random arithmetic programs through `eval_node(program.ast)` vs `program.run`: zero differences apart from PY-C8 and PY-C9. TAKE+TAKE / DROP+DROP merges (numeric literals only), FILTER+FILTER fusion, FILTER(TRUE) removal, SELECT_COLS/FILTER swap, `step_arg_options` folding all hold up. `exceeds_depth` gating keeps optimiser recursions under the cap (about 206-209 frames for optimise, 202-205 for `dependencies()` at the deepest legal shapes; 3.10/3.11 comprehension frames unverified).
- `_cached_slot` cache is safe with shared Programs; `physical_ast()` racing to build is benign; `register_function` validation matches SPEC 8.1; `Args` evaluates values at most once and reports at the argument position; `binding_form`/`_collect` are depth-bounded. CLI `-e` path: E_UTF8 for invalid argv bytes and `show()` output agree on 10 probes; `--deps`, `--functions` fine.

**Text/regex/binary/null/control (s4)**
- `\d \w \s` are ASCII; `\b \B \v` and backreferences rejected; `^`/`$` lowering to `\A`/`\Z` correct incl. `[$]`, `[^^]`, `\^` and `RMATCH('a$', "a\n")` FALSE; case folding (`re.ASCII | re.IGNORECASE` plus the U+212A/U+017F pre-fold) consistent on all five hosts; quantifier validation (`{2,1}`, 65535/65536 boundary, `a{2}{3}`, `a**`, `a+*`, ...) E_REGEX_SYNTAX everywhere; `int()` on the bound digit run safe (400,000 digits 0.27 s); zero-width iteration (`RREPLACE('x*','-','axb')` = `-a--b-`); astral characters agree; replacement expansion (`$1`, `$10`, `$٣`, `$&`) agrees; RGROUPS non-participating groups.
- Text builtins: UPPER/LOWER ASCII-only; no `strip`/`splitlines`/`round`/`isdigit`/`int()` on unchecked text; LEN/LEFT/RIGHT/SUBSTR/FIND/CODE/CHAR count code points; huge start/len/from arguments clipped correctly; CHAR rejects surrogates and > U+10FFFF; empty pad fill is E_BAD_ARG everywhere.
- Binary: FROM_HEX rejects Arabic-Indic digits and odd length; base64 padding rules strict and correct; FROM_UTF8 strict decode; `LTB(LIST(65.0))`, `0.5` E_RANGE. Null/control: COALESCE/GET/PATH/IF/COND lazy; `GET(NULL, NULL, 1)` E_NULL; COND odd-count via registry; ABORT position column 7. No cyclic structures created (fine for the paused-GC design).

**Structure/aggregate/relational (s5)**
- Aliasing in TAKE/DROP/DISTINCT/SELECT_COLS/LINK/TOP (other than PY-C38); `RecordShape`s immutable; `Value.set` on a shaped record converts to dict rather than mutate the shared shape; `_clone_at` shares shape but copies `list_keys`.
- Join keys are only `int`, `(int, scale)`, `bytes` or `_JoinBad` (no float/bool collisions: 1 == True == 1.0 avoided); `1.50`/`"1.5"`/`1.5` meet; DISTINCT/BUCKET bucket on `structural_hash` and confirm with `eql`; `DISTINCT(LIST(1, 1.0, "1", 1.00))` keeps scale distinct.
- About 35 LINK/LINK_LEFT probes (swapped operands, `$==` vs `==`, NULL/BOOL/list keys, FILTERed left with custom keys, duplicate/explicit binders, case-fold names, nested-record carry, NULL right fields, scalar and record sources, side-effecting key expressions) agree with js/php/lisp/cpp/go except as noted; `expr_depends_only_on` correctly refuses to hash-join a key reading a non-binder variable. Generated join-plan source embeds only integer slot indices, never user text (no injection through field names). About 150 differential probes in total.
- Stability of SORT/SORT_BY/SORT_DESC/TOP (tie-break on original index, DESC included); direction parsing uses `ascii_upper`; `_K` after FILTER correct; TOP/TOP_BY limit and SORT_BY direction are "outer" arguments evaluated before the key body on all six hosts. `structural_hash`, `eql`, `clone` depth-counted; equi-join `int(text)` is the only digit-cap bypass found; LINK has no output-size guard (`LINK(A,B,TRUE)` is n*m rows, inherent). Generated join functions do not create per-run cycles (GC note in contributing.md holds). The pre-filter / stage-walk machinery was probed for complexity but not proved correct (the join oracles were not run).

**SQL translator and rendering (s6, s7)**
- Identifier quoting doubles the quote character for all four dialects; text literals (mysql-family escapes `'` and `\` in one left-to-right pass; postgresql/sqlite double `'`; params mode binds TEXT and inlines NUM/BOOL/BIN by design); NUM literals re-emitted from `decimal.format` only (`1 OR 1=1`, `0x10`, `1_0`, Arabic-Indic digits, `+1`, `.5`, `5.`, `1e3`, `NaN` refused; negatives parenthesised); BIN via `bytes.hex()`; ORDER BY direction validated; LIMIT/OFFSET are Python ints. `Emit.fill` slot grammar is a fullmatch, parameter indices absolute, self-referencing lexical expansion refused. `Emit.literal`/`_numeric_literal`/`ident` sound.
- Generated `_map.py` equals a fresh key-by-key flatten of `sql/dialects/*.json` for all six dialects (0 differences); `chain()` cannot loop; `define_dialect` validates version (ASCII `[0-9]` regex), target, lexical types.
- No caller Node mutated by translation; repeated translation gives identical output; `_list_key` ASCII class + 9-digit cap; `_numeric_cast_scale` checks `isascii()` first; regex rewrite reuses the language validator and turns SelError into E_SQL_UNSUPPORTED; `E_SQL_DEPTH` enforced in stage 1 and the render walk (assignment and `;` wrappers charged); whole-expression refusal (no partial output); kind guards (BOOL/BIN as numeric operands, non-BOOL conditions, UNKNOWN in AND/OR/XOR/NOT/IF, IF/COND/`??`/`???` unified kinds, `&` refuses BOOL, sqlite/ansi unguarded TEXT/UNKNOWN numeric operand); BAND/BOR/BXOR refused in all shipped dialects; alias handling (`check_aliases`, self-nested relation, second LINK of the same alias). Binding validation: typed constructors make old dict-shape divergences unrepresentable. A 4-seed crash fuzz (about 9000 programs x 2 dialects) raised only the SqlError family.
- Documented residuals treated as deliberate: `C / 0`, `C % 0`, `LEFT(col, -1)`, unguarded UNKNOWN aggregate body (sql-kinds.md 4/4.1a). Small inconsistency not reported: `check_aliases` and `_with_row` compare aliases case-sensitively, `LINK` case-insensitively; SQLite treats aliases case-insensitively.
- Hybrid split contract: about 4000 random pipelines vs SQLite (values compared exactly, order-only differences masked): no value mismatch outside PY-C22-C24, PY-C47 and the order-only class; bucket handling, join rows without binders, identity barrier, MAP fall-through dependency/collision rules, whole-row reads, downstream reads of unprojected keys, helper unwinding, literal-helper inlining position stamping, latest-member CTE all held. Nesting up to the parser cap (about 97-98 call levels in a FILTER) plans and translates without RecursionError; 190-step pipelines plan fine. `relational_plan.py`: no shared mutable defaults. Planner never writes to the caller's AST in the paths read. Downstream `SORT_BY(_)`, `COUNT(_)`, `LEN(_)`, `GET(_, ...)` after a partial MAP are refused and end as pure memory (safe).
