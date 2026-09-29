# PHP host code review

Date: 2026-09-29. Repo state: HEAD `faff480` (0.9.2) plus the uncommitted work-tree noted in `git status` (Go/Rust work; PHP tree unmodified).

## Scope and method

Files under `php/src/` (about 17,000 lines), reviewed by seven slice reviewers working in parallel, read-only, each reading its slice in full and confirming suspected bugs by running the host (PHP 8.5.10, PCRE2 10.47; no PHP 8.1 and no MariaDB/MySQL/PostgreSQL servers were available, so those items are marked unconfirmed):

| Slice | Files |
|---|---|
| 1 | front end: `Lexer`, `Parser`, `SelError`, `Limits` (consumer), `Utf8` |
| 2 | numeric core and values: `Dec`, `Value`, `Builtins/Number`, `MathPlan`, `MathOps` (consumer) |
| 3 | `Evaluator`, `Registry`, `Args`, `Context`, `Sel`, `bootstrap`, `Optimizer` |
| 4 | `Builtins/Core`, `Text`, `Regex`, `Binary`, `NullOps` |
| 5 | `Builtins/Structure` (aggregates and the in-memory relational pipeline) |
| 6 | `Sql/Translator`, `Emit`, `Fragment`, `Normalise`, `Bindings`, `Binding`, parts of `Map`/`Constants` |
| 7 | `Sql/Hybrid`, `Map`, `Emit`, `Constants`, `Normalise`, `Binding`, `Bindings`, `Binder`, `Fragment`, `Sql`, `RelationalPlan` |

The synthesizer (this document) deduplicated the seven reports, re-ran the repros of the main correctness findings (`php php/bin/sel -e '...'`, read-only), and compared perf findings with `docs/interim/2026-09-29/php-optimization-plan.md`. "Found by" cites `slice N Cx/Px` = the reviewer's own numbering. Confidence labels are the reviewers' unless a line says "re-verified by synthesizer".

Numbering: PHP-C1.. correctness (ordered high, medium, low; within a band by confidence), PHP-P1.. performance (by impact).

## Executive summary

- Arithmetic, lexer/parser semantics, quoting/escaping in the SQL layer and most builtins are sound: differential fuzzes (15k lexer programs, 60k+ arithmetic expressions, 40k pipelines, 3k hybrid pipelines, 837k UTF-8 inputs, 2x20k decimal boundary cases vs bcmath) found no value divergences in those areas. The problems cluster in (a) the 0.9.2 performance work, (b) the regex validator/PCRE seam, (c) the SQL translator/hybrid planner, and (d) resource-exhaustion (DoS) shapes.
- Worst correctness items: two regressions from the performance plan that break the language contract. Shared mutable `Value::bool()` singletons let one program poison `TRUE`/`FALSE` for the whole process (PHP-C1), and `,` no longer copies what it collects, so PHP now disagrees with all four other hosts (PHP-C2). Both re-verified. Also `Value::num("007")` regressed and turns `tools/check-api.sh` red for PHP on HEAD (PHP-C10, re-verified).
- The regex seam is the largest single risk: PCRE backtrack/recursion/JIT-stack failures silently become FALSE/0/unchanged (PHP-C3), and `(*FAIL)`/`(*ACCEPT)` verbs pass the "portable subset" validator (PHP-C4). Both re-verified.
- SQL layer: `SUM(g, body)` inside a BUCKET projection emits literal slot numbers instead of values (PHP-C7, wrong SQL with no error; the same code in JS, a TypeError in Python), and the hybrid planner can lose row keys, rename the LINK binder to `_INPUT`, apply a helper twice, and drop errors (PHP-C8, C9, C34-C36).
- DoS shapes that need small input: nested interpolation is super-linear in the lexer (PHP-C5), `A[1][1]...=` is quadratic (PHP-C14), `FIND` is O(n*m) in userland PHP (40 KB input ~ 77 s, PHP-P2), no-GMP `/` and `*` are quadratic, uncapped `REPEAT`/`PADL`, `foldPairwise` on IN lists is quadratic (PHP-P8), and helper assignments blow up 2^n in the translator (PHP-C32).
- Biggest performance wins, none of which are in the optimization plan: byte-level text builtins instead of per-code-point arrays (LEN of 1M chars 227 ms -> ~1 ms), `strpos`-based FIND/REPLACE/SPLIT (77 s -> ms), decorated SORT with no per-compare exceptions (3-6x), removing repeated UTF-8/decimal revalidation of internal values (25-45% of literal/numeric-node cost), first-byte operator dispatch and regex-run lexing (lexing is ~70% of front-end time).
- Several plan items are marked done but the reviewers found them not fully realised (flyweights are unsafe, `_K` omission only covers BUCKET, projector memoisation is per call, `decVal` is still a 6-key array).

Counts: 58 correctness findings (9 high, 28 medium, 21 low), 30 performance findings (8 high, of which PHP-P7 duplicates PHP-C14; 15 medium; 7 low-medium/low).

---

# Correctness findings

## High

### PHP-C1 [high] [confirmed; re-verified by synthesizer] Shared `Value::bool()` flyweights are mutable; one program can poison `TRUE`/`FALSE` process-wide
Found by: slice 2 C1, slice 3 C2. Planned change that introduced it: plan item 1.6.
- Where: `php/src/Value.php:265-273` (`Value::bool`, static `$valTrue/$valFalse`), `:684` (`set`), `:1091` (`fromNativeAt` -> `self::bool`); reached from `Evaluator.php` (`case 'bool'`, `evalUnary`, `evalBinary`).
- What: every TRUE/FALSE is one of two process-wide mutable objects. Assignment copies its right-hand side (SPEC 3.4/5.7), but a boolean that comes from host data (`Value::fromNative(['ACTIVE'=>true])`, or `->set('FLAG', Value::bool(true))`) is the singleton itself, so `ACTIVE["NOTE"] = "x"` mutates it. From then on every `TRUE`, every comparison result and every `true` in other contexts carries the child: cross-request/cross-tenant corruption in long-lived PHP processes (Swoole, RoadRunner, Octane, workers, test suites), and corruption of every other boolean within one FPM request. `Program::run` mutates the context in place, so arbitrary rule text can trigger it.
- Repro (host API script; not expressible through `php/bin/sel`):
  ```
  $c1 = Value::fromNative(['ACTIVE'=>true]);  Sel::compile('ACTIVE["NOTE"] = "x"')->run($c1);
  Value::fromNative(['OK'=>true,'BAD'=>false])->dump()   // -{"OK"=TRUE{"NOTE"=t"x"}, "BAD"=FALSE}
  Sel::compile('TRUE')->run(Value::none())->dump()        // TRUE{"NOTE"=t"x"}   (expected TRUE)
  Sel::compile('OK == TRUE')                              // now E_NOT_NUM for a different reason
  ```
  Slice 3 variant: `$root = Value::none(); $root->set('FLAG', Value::bool(true)); Sel::compile('FLAG["k"] = 1; 0')->run($root); Sel::evaluate('COUNT(TRUE)')` -> observed `t"1"`, expected `t"0"`; `Value::fromNative(['A'=>true,'B'=>true])` then `A["z"]=1` shows up in `B`. Re-verified by synthesizer with the slice-2 script: prints `TRUE{"NOTE"=t"x"}` for a later plain `TRUE`, and `-{"OK"=TRUE{"NOTE"=t"x"}, "BAD"=FALSE}`.
- Fix sketch: make the singletons immutable or copy-on-write (`set()` and the evaluator's assignment walk replace a flyweight root/intermediate with a copy before writing); simplest safe alternative: `fromNativeAt`, `Program::run` context intake and the public `Value::bool()` allocate fresh objects, and the flyweight becomes a private `sharedBool()` used only for internal results that are never stored (anything that can become a mutation target - the context root and children, the value returned from `run()` - must be fresh). A `LogicException` on `set()` of a flyweight is a further option. The same audit applies to plan item 1.7 (null/none flyweights).
- Conformance gap: none (conformance drives programs, never a host-supplied BOOL that is assigned into). Add to `tools/check-php-runtime.php`: run `B["k"]=1` over a `fromNative` bool, then assert `Value::bool(true)->size()===0`.

### PHP-C2 [high] [confirmed; re-verified by synthesizer] `,` no longer copies what it collects; PHP disagrees with spec 3.4 and every other host
Found by: slice 3 C1. Introduced by plan items 1.3/1.4 ("Remove ->copy() from LIST / evalList").
- Where: `php/src/Evaluator.php:248-262` (`evalList`).
- What: spec 3.4 says "`,` (5.9) and the aggregates copy what they collect", and `docs/contributing.md` ("A value is not a snapshot") lists `,` collecting a child and `,` collecting a value as deliberate copy sites. `evalList` now stores aliases, so a later sub-expression of the same `,` that mutates the source changes the already-collected element.
- Repro: child contribution `A = RECORD("a", RECORD("b",1)); (A, A["a"]["b"] = 99)[1]["b"]`: PHP `99`; js/python/cpp/lisp `1` (expected `t"1"`). Value contribution: `A = "x"; A["k"] = RECORD("b",1); (A, A["k"]["b"] = 99)[1]["k"]["b"]`: PHP `99`; js/cpp/lisp `1`. Re-verified by synthesizer: `php php/bin/sel -e 'A = RECORD("a", RECORD("b",1)); (A, A["a"]["b"] = 99)[1]["b"]'` prints `99`, `node js/bin/sel.mjs` prints `1`.
- Fix sketch: restore `->copy($node['pos'])` for both branches (with its E_DEPTH position). If too costly, copy only when the item list has more than one item and a later item is not a pure literal or variable; copy first, optimise later.
- Conformance gap: none - `conformance/04-values.selt` "identity and aliasing" (164-260) covers index bases, binders, compound targets but not `,`. Suggest `alias.comma-copies-a-child` and `alias.comma-copies-a-value`, expecting `t"1"`.

### PHP-C3 [high] [confirmed; re-verified by synthesizer] PCRE resource errors (backtrack / recursion / JIT stack limit) become silently wrong results
Found by: slice 4 C1.
- Where: `php/src/Builtins/Regex.php:403, 410, 419, 442` (every `preg_*` call; none checks `preg_last_error()`).
- What: on `pcre.backtrack_limit` (1,000,000), `pcre.recursion_limit` (100,000) or JIT-stack exhaustion `preg_match` returns `false`. `RMATCH` does `=== 1` so yields FALSE, `RFIND` 0, `RGROUPS` the "no match" empty value, `RREPLACE` returns the subject unchanged (`preg_match_all` false, `$all` empty). The spec (7.8) has no "gave up" outcome and the other four hosts return the true answer, so `NOT RMATCH(...)`-style rules silently accept. Thresholds are low for ordinary patterns: `^(?:[a-z]|[0-9])+$` fails on a 50,000-char subject; `^(?:(a+)+c|.*)$` fails at ~22 chars where the true answer is TRUE. The threshold also depends on the deployment php.ini (pcre.jit, limits), so the same rule gives different answers per server.
- Repro: `php php/bin/sel -e "RMATCH('^(?:a|b)*\$', REPEAT('a', 60000))"` -> PHP FALSE; js/py/cpp/lisp TRUE (re-verified by synthesizer: PHP FALSE, js TRUE). `RFIND('(?:a|b)*c', REPEAT('a',60000) & 'c')` -> PHP 0, others 1. `LEN(RREPLACE('^(?:a|b)*\$','X',REPEAT('a',60000)))` -> PHP 60000, others 1. `COUNT(RGROUPS('^(?:a|b)*\$', REPEAT('a',60000)))` -> PHP 0, others 1. Backtrack variant: `RMATCH('^(?:(a+)+c|.*)\$', REPEAT('a',22)&'b')` -> PHP FALSE, js/py/lisp TRUE (cpp aborts, see "Other-host notes" at the end). `preg_last_error_msg()` said "Recursion limit exhausted" (49,999 vs 50,000+) and "Backtrack limit exhausted".
- Fix sketch: after each `preg_*` call test `preg_last_error() !== PREG_NO_ERROR` (or `=== false`) and raise a SEL error. That needs a catalogued code (`spec/errors.md`, `spec/limits.json`, `tools/check-error-codes.sh`), e.g. a run-time `E_REGEX_LIMIT`; a spec decision. Optionally retry once with different limits. `RREPLACE` must distinguish `false` from "zero matches".
- Conformance gap: none - suggest a 100,000-char subject against `^(?:a|b)*$` (expect TRUE) after the spec picks a resource-limit policy.

### PHP-C4 [high] [confirmed; re-verified by synthesizer] PCRE backtracking verbs `(*FAIL)`, `(*ACCEPT)`, `(*COMMIT)`, `(*UTF8)` are accepted and change matching
Found by: slice 4 C2.
- Where: `php/src/Builtins/Regex.php:132-148` (`(` handling) then `:156` (`*` treated as a quantifier).
- What: after `(` only `(?` is special-cased; the following `*` is consumed by the quantifier branch, so `(*VERB)` reaches PCRE, which reads a verb or start-of-pattern option. Spec 7.8 and the file's header promise the validator "must reject exactly the same patterns" in every host; JS/Py/C++/Lisp raise E_REGEX_SYNTAX ("Nothing to repeat").
- Repro: `php php/bin/sel -e "RMATCH('(*FAIL)', 'a')"` -> FALSE (others E_REGEX_SYNTAX; re-verified: PHP FALSE, js E_REGEX_SYNTAX). `RMATCH('a(*ACCEPT)b','ac')` -> TRUE (re-verified). `RMATCH('(*UTF8)a','a')` and `RMATCH('(*COMMIT)a','a')` -> TRUE (others E_REGEX_SYNTAX). `(*LIMIT_MATCH=1)` is rejected by PCRE itself.
- Fix sketch: in the `(` branch reject when the next code point is `*` (and `+`), with the "this group type is not portable" message; more generally reject any `(` followed by a quantifier character.
- Conformance gap: none - suggest `re.reject.pcre-verb`: `RMATCH('(*FAIL)','a')` => E_REGEX_SYNTAX, and `RMATCH('a(*ACCEPT)b','ac')`.

### PHP-C5 [high] [confirmed; re-verified by synthesizer as super-linear] Nested string interpolation makes the lexer quadratic or worse: a 12.8 KB source costs 9 s, 40 KB does not finish in 300 s
Found by: slice 1 C1. Shared shape with JS (quadratic, dies with RangeError at d=3200) and C++ (0.86 s at d=10000), so this is cross-host.
- Where: `php/src/Lexer.php:231-267` (`lexQuoted`), `314-351` (`matchBrace`), `353-373` (`skipQuoted`), `401-425` (`emitParts` -> `lexRange`).
- What: for `"{"{"{...1...}"}"}"` every nesting level re-scans everything inside it: `lexQuoted` calls `matchBrace` on the whole remainder (which recurses through `skipQuoted`), then `emitParts` re-lexes the inner range with `lexRange`, whose own `lexQuoted` calls `matchBrace` again. Work is O(depth * length), and nothing bounds it: E_DEPTH lives in the parser, which runs only after the whole token stream is built. Spec 6.4 calls E_DEPTH "a denial-of-service guard"; this bypasses it for tiny inputs.
- Repro (measured by reviewer, PHP 8.5, memory_limit=-1): source = `'"{' * d + '1' + '}"' * d`, timing `Sel\Lexer::tokenizeSource`: d=400 0.10 s, d=800 0.39 s, d=1600 1.4 s, d=3200 (12.8 KB) 8.8 s; via `php php/bin/sel file` d=10000 (40 KB) still running after 300 s. Same input in `cpp/build/sel`: E_DEPTH at 1:101 in 0.86 s. Output for depths <= 40 is fine; for larger E_DEPTH at 1:101 (identical to C++), so only the time is wrong. Synthesizer re-run through `php php/bin/sel file` on a loaded box: d=400 0.37 s, d=800 0.77 s, d=1600 2.4 s (super-linear, same answer E_DEPTH at 1:101).
- Fix sketch: memoise `matchBrace($i)` and `skipQuoted($j)` results by start index in two per-Lexer arrays (a successful scan of a start offset is independent of the caller; errors throw and are not cached). Prototype by the reviewer (LexerM.php): d=3200 9.4 s -> 0.08 s, d=12800 0.39 s, token stream byte-identical (serialize+md5 equal at d=400/1600/3200). Counting interpolation nesting in the lexer would change which error wins, so memoisation is the byte-identical fix.
- Conformance gap: none - suggest `lim.interp-nesting-depth` (a few hundred nested `"{...}"` levels expecting E_DEPTH; at least a cheap depth-100 case) plus a gate time budget.

### PHP-C6 [high] [confirmed] REPEAT / PADL / PADR (and exponential value growth) have no size cap: PHP dies with an uncatchable "Allowed memory size exhausted" fatal
Found by: slice 4 C7, slice 3 C8. Spec gap shared by all hosts; PHP's threshold is unusually low for PAD.
- Where: `php/src/Builtins/Text.php:156-159` (`str_repeat`), `230-249` (`pad()` builds a PHP array with one element per pad character); `Evaluator::evalAssign` `->copy()`, `evalList`.
- What: spec 6.4 caps the arguments that "name a size" and says exceeding a cap "is an ordinary SEL error rather than a host failure"; REPEAT's count and PADL/PADR's width are that kind of argument and are uncapped. In PHP the result is a fatal (not an exception), killing the request/worker. `pad()` builds a 5M-element array, so `PADL('a', 5000000, 'x')` (a 5 MB result) dies under the default 128M limit. Exponential growth without any big literal: `A = (1,2); A = (A, A);` thirty times exhausts memory too. Other hosts return the answer or die differently (JS RangeError/heap OOM, Lisp heap exhaustion); spec/limits.md lists only depth and decimal caps.
- Repro: `php php/bin/sel -e "LEN(PADL('a', 5000000, 'x'))"` -> PHP Fatal (js/py/cpp/lisp print 5000000). `LEN(REPEAT('ab', 10000000000))` -> PHP Fatal ("tried to allocate 20000000032 bytes"), JS raw RangeError, Lisp heap exhaustion. `LEN(PADL('a', 99999999999999999999, 'x'))` -> PHP Fatal. Doubling: `python3 -c "print('A = (1,2); ' + 'A = (A, A); '*30 + 'COUNT(A)')" > dbl.sel; php php/bin/sel dbl.sel` -> PHP Fatal "Allowed memory size of 134217728 bytes exhausted".
- Fix sketch: spec adds a result-length cap for REPEAT/PADL/PADR (E_RANGE above N code points, stated in `spec/limits.json`), ideally also an element/byte cap for `,`/copy. In PHP build padding with `str_repeat` plus a code-point-aware slice (see PHP-P1). Until then document that `memory_limit` is the guard.
- Conformance gap: none - suggest `lim.repeat-cap` / `lim.pad-cap` once the spec has a number.

### PHP-C7 [high] [confirmed; not re-verified] `SUM(g, body)` inside a BUCKET projection emits parameter slot numbers instead of literals
Found by: slice 6 C1. Cross-host: JS emits the same wrong SQL, Python raises an uncaught `TypeError`, Go drops slots (not run).
- Where: `php/src/Sql/Translator.php:708` (`implode('', $inner->parts)`).
- What: a Fragment's `parts` alternate SQL strings and int slot ids (Fragment.php header). The bucket-member `SUM(g, [x,] body)` fast path joins them with `implode`, so every literal in the body (numbers and text) is replaced by its 1-based slot index and the real value is dropped from the SQL (still in `params` but unreferenced). The statement is valid but computes a different number, with no refusal. Contradicts `docs/internals/sql-translation.md` section 9 ("the renderer never concatenates a literal into a string") and the warrant (`sql-kinds.md` section 1).
- Repro (scratchpad `php6/t8.php`): `O .> BUCKET(_["CAT"], RECORD("cat", _K, "s", SUM(_, _["TOTAL"] * 2)))` on mariadb -> `... COALESCE(SUM((\`total\` * 1)), 0) AS \`s\` ...`, expected `(\`total\` * 2)`. `SUM(_, _["TOTAL"] * 7 + 5)` -> `((total * 1) + 2)`. `SUM(_, IF(_["STATUS"] $== "x", _["TOTAL"], 0))` -> `CASE WHEN (... = CAST(1 AS CHAR) ...) THEN total ELSE 2 END`. JS: `js/src/sql/translator.mjs:890` (`inner.parts.join('')`); Python: `python/sel/sql/translator.py:821` (TypeError escapes `try_translate`); Go: `go/sel/sql/translator.go:1727`.
- Fix sketch: splice: `new Fragment(array_merge(['COALESCE(SUM('], $inner->parts, ['), 0)']), 'NUM', $this->dialect, ...)`, carrying `$inner`'s caveats. Same fix in every host.
- Conformance gap: none - all `SUM(_, ...)` cases in `sql/cases/24-bucket.sqlt` and `25-hybrid-plans.sqlt` use a bare column or `_K`. Suggest `ITEMS .> BUCKET(_["dept"], RECORD("t", SUM(_, _["amount"] * 2)))`, one with a text literal in an IF body, and a `--- params` check.

### PHP-C8 [high] [confirmed] Hybrid split on a key-retaining FILTER loses SEL's row keys: continuation `_K` / result keys differ from pure memory
Found by: slice 7 C1. Cross-host (JS `js/src/sql/hybrid.mjs` verified, Python by grep).
- Where: `php/src/Sql/Hybrid.php:177-201` (prefix loop), `550-675` (`tryPlanFallthrough`), `readsWholeRow` at `491` ignores `_K`.
- What: spec 7.3 and `docs/internals/sql-translation.md` 12.1 ("The rewrites keep keys"): FILTER keeps its input's keys; only MAP/sort/SELECT_COLS/TAKE/DROP renumber. A SQL prefix ending in (or being) a FILTER hands the continuation rows keyed 1..n, so a continuation MAP/FILTER reading `_K` sees 1..n, and a key-keeping continuation returns 1..n instead of the retained keys. The fall-through path guards FILTER downstream of the MAP only, the plain prefix split has no guard; `_K` is a bare `var`, so `fieldReferences`/`readsWholeRow` never see it.
- Repro (sqlite harness, ORDERS ids 1..10): `ORDERS .> FILTER(_["id"] > 2) .> MAP(RECORD("i", _["id"], "k", _K, "z", REPEAT(_["name"], 2)))` memory `k`="3".."10", hybrid "1".."8". `ORDERS .> FILTER(_["id"] > 2) .> FILTER(LEN(REPEAT(_["name"],2)) > 5)` memory keys 3..8, hybrid 1..6. `ORDERS .> TAKE(6) .> FILTER(_["id"] > 2) .> FILTER(LEN(REPEAT(_["name"],2)) > 5)` memory 3..6, hybrid 1..4. Related (pure_sql): `ORDERS .> FILTER(_["id"] > 8)` memory keys "9","10", pure_sql "1","2" (12.1 never says pure_sql renumbers - needs a documented decision).
- Fix sketch: track "last step kept the input keys" over the prefix and refuse the split unless the continuation begins with a renumbering step that does not read `_K`; treat `_K` var reads like a whole-row read in `readsWholeRow`.
- Conformance gap: none for the plain split. Suggest in `25-hybrid-plans.sqlt`: `ORDERS .> FILTER(...) .> MAP(RECORD("k", _K, "z", <unpushable>))` must be pure_memory (or execute equal).

### PHP-C9 [high] [confirmed] LINK in the hybrid continuation renames the left binder to `_INPUT` (spec 7.4 violation)
Found by: slice 7 C2. Cross-host (JS `hybrid.mjs:382,617`, Python `hybrid.py:437,730,823`).
- Where: `php/src/Sql/Hybrid.php:191, 617, 649` (`'name' => '_INPUT'`).
- What: SPEC 7.4 "Joined rows": in the three-argument form the left binders are the argument's own name when it is a bare name or a pipeline whose source is one. After a split before a `LINK` the continuation source is `_INPUT`, so joined rows are keyed `_INPUT`/`_input` instead of `ORDERS`/`orders`; a later `_["ORDERS"][...]` raises E_NO_KEY. `joinRowsLackBinders` only guards a LINK inside the prefix.
- Repro: `ORDERS .> FILTER(_["id"] > 2) .> SORT_BY(REPEAT(_["name"], 2)) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("n", _["ORDERS"]["name"]))` memory: 6 rows; hybrid: E_NO_KEY. Without the MAP the hybrid rows contain `"_INPUT"=...,"_input"=...` where memory has `"ORDERS"`,`"orders"`.
- Fix sketch: do not split before a 3-argument LINK whose left has no earlier LINK, or bind the continuation source under the original relation name, or rewrite to the 5-argument form.
- Conformance gap: none - suggest a hybrid split immediately before an unbound-name `LINK` reading `_["ORDERS"]`.

## Medium

### PHP-C10 [medium] [confirmed; re-verified by synthesizer] `Value::num("007")` keeps the raw text (0.9.2 regression); the API parity probe fails for PHP on HEAD
Found by: slice 2 C2.
- Where: `php/src/Value.php:290-296` (`new self(self::TEXT, $d)`; was `null` in 0.9.1; docblock at `:277` still says "007 becomes 7").
- What: SPEC 8 "Value.num canonicalises its argument (4.1)" and 4.1 "A zero value never carries a minus sign". PHP stores the caller's string as the scalar, so `asText()`, `dump()`, `toNative()`, `eql` and `structuralHash` see "007"/"-0" while `asDecimal()` says 7/0. JS and Python give `t"7"`/`t"0"`.
- Repro: `php tools/api.php` vs `node tools/api.mjs` differ on exactly one line: `17 ctor.num.canonicalises = t"007"` (php) vs `t"7"` (js). Also `Value::num('-0')->dump()` = `t"-0"`. Re-verified by synthesizer (both tools run; the diff is exactly that line, 85 lines each). I did not run `tools/check-api.sh` itself (it uses the slot/roster machinery), but it diffs these same reports, so the slice-2 claim that the API gate is red for PHP on HEAD is consistent with what I ran; the roster reference host decides which side prints as `MISMATCH`.
- Fix sketch: `new self(self::TEXT, null)` (getScalar formats lazily from `decVal`, which `parse` canonicalised); or store `Dec::format($parsed)` instead of `$d`.
- Conformance gap: covered only by `tools/api.php` (`ctor.num.canonicalises`), not `conformance/*.selt`.

### PHP-C11 [medium] [confirmed; re-verified by synthesizer] FILTER+FILTER fusion treats a bare variable or literal predicate as "cannot raise", so the optimised program reports a different error
Found by: slice 3 C3. Cross-host: all five hosts print the E_NOT_BOOL (re-verified for PHP and JS), so the fusion rule is shared.
- Where: `php/src/Optimizer.php:463-484` (fusion) using `cannotRaise` at `:532-554` (`case 'var'`, literal cases return true).
- What: fused, `FILTER(p1) .> FILTER(q)` becomes `FILTER(p1 AND q)`, which runs `q` on rows before `p1` has been evaluated on later rows. `q = _`, `1`, `"x"`, `NULL`, `_K` raise E_NOT_BOOL/E_NULL when FILTER coerces them, so the fused tree raises on an early row before `p1`'s error on a later row. Spec 6.2/6.3: stop at the first failure in left-to-right order.
- Repro: `LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER(_)`: plain tree E_DIV_ZERO at 1:25; optimised (what `run()` does) E_NOT_BOOL at 1:50 (re-verified: PHP and JS both E_NOT_BOOL at 1:50). Same for `.> FILTER(1)`, `FILTER("x")`, `FILTER(NULL)` (E_NULL), `FILTER(_K)`.
- Fix sketch: in the non-logical case only a literal `bool` counts as cannot-raise; `var` counts only when its static kind is known bool (never here). Simplest: `cannotRaise` returns true for `bool` literals only when `!$logical`.
- Conformance gap: none - suggest `opt.filter-fusion-keeps-first-error`: the expression above, expecting `!E_DIV_ZERO`.

### PHP-C12 [medium] [confirmed] SORT/SORT_BY + TAKE fusion into TOP/TOP_BY drops key errors when the count is 0 and evaluates the count before the keys
Found by: slice 3 C4. Cross-host through `run()` (all five hosts return the fused answer).
- Where: `php/src/Optimizer.php:391-404`.
- What: `SORT_BY(k) .> TAKE(0)` evaluates the key on every row and raises on a bad one; `TOP_BY(k, 0)` returns the empty list without evaluating keys. Also the fused `TOP_BY(list, key, n)` evaluates `n` before sorting, so an invalid count masks a key error the unfused pipeline raises first. Spec 7.3 keeps "a count of zero still evaluates the list" but says nothing licensing skipping the key.
- Repro (Program::ast vs physicalAst): `LIST(RECORD("a",0),RECORD("a",1)) .> SORT_BY(1/_["a"]) .> TAKE(0)`: plain E_DIV_ZERO 1:47, `run()` `-` (empty). `... .> SORT_BY(1/_["a"]) .> TAKE("x")`: plain E_DIV_ZERO, optimised E_NOT_NUM 1:64. `... .> TAKE(nosuch)`: E_DIV_ZERO vs E_UNDEF_VAR. `... .> TAKE(-1)`: E_DIV_ZERO vs E_RANGE. `LIST(3,"x",2) .> SORT(_ + 1) .> TAKE(0)`: E_NOT_NUM vs OK. (One-row inputs do not raise in either form.)
- Fix sketch: fuse only when the TAKE count is a literal integer > 0 (`numericLiteral() > 0`); or make TOP* with n=0 still evaluate every key and evaluate n after the keys (host-wide: spec plus five hosts).
- Conformance gap: none for key errors - suggest `opt.sort-take-zero-still-raises` and `opt.sort-take-count-error-order`.

### PHP-C13 [medium] [confirmed; re-verified by synthesizer] Math plan coerces each leaf operand at load time, so a coercion error on an earlier operand masks an error on a later one (the plain tree gives the opposite)
Found by: slice 3 C5. Cross-host (all five hosts agree with each other; the divergence is between plan and tree).
- Where: `php/src/Evaluator.php:53-66` (`LOAD_VAR`/`LOAD_LEAF` call `asDecimal` immediately) vs `evalBinary` `:308-323` (evaluate both, then coerce).
- What: for `+ - * / %`, unary `-`, ROUND, POWER, MIN, MAX, ABS, SIGN, CEIL, FLOOR, TRUNC the plain evaluator evaluates all operands then coerces; the plan coerces operand 1 before operand 2 is loaded. Spec 6.2 "strictly left to right wherever both operands are evaluated" and 6.3 favour the tree-walk reading. Internally inconsistent: `"x" + nosuch` gives E_NOT_NUM but `"x" == nosuch`, `"x" XOR nosuch`, `TRUE & nosuch`, `"x" BAND nosuch`, `"x" EQL nosuch` give E_UNDEF_VAR. Consequence in PHP: `Program::run()` (physical) and consumers of the plain tree (SQL constant folding `Sql/Constants.php:340-388`, `Translator.php:3631`) report different errors for the same expression.
- Repro: `x = "abc"; x + nosuch` -> E_NOT_NUM at 1:12 in `php/bin/sel` (re-verified), plain tree (`Evaluator::evalNode($p->ast)`) E_UNDEF_VAR at 1:16. `x = "abc"; x * (1/0)`: E_NOT_NUM 1:12 vs E_DIV_ZERO 1:18. `ROUND("b", ABS(TRUE))`: E_NOT_NUM 1:7 vs 1:16. `MIN(FALSE, x)` (x undefined): E_NOT_NUM vs E_UNDEF_VAR. A 24k-expression fuzz gave ~45 diffs per 6000, all error code/position swaps of this kind, never a value difference.
- Fix sketch: (a) `LOAD_*` store the raw `Value` and coerce at the consuming step in operand order (matches 6.2; near-free); or (b) declare the current order the spec and change `evalBinary`. Needs a spec sentence, a `.selt` case and a change in every host's plan compiler.
- Conformance gap: none - suggest `ord.arith-evaluates-both-then-coerces`: `"x" + nosuch` expecting `!E_UNDEF_VAR`.

### PHP-C14 [medium] [confirmed; re-verified by synthesizer] Assignment-target chains cost O(n^2): `array_unshift` in a loop, 120 KB source takes ~10 s
Found by: slice 3 C6 / P3.
- Where: `php/src/Evaluator.php:497` (`resolveTarget`: `array_unshift($chain, $n['idx'])`); the E_DEPTH check is at `:515`, after the loop.
- What: `A[1][1]...[1] = 1` is one flat chain, not a nesting, so neither parser nor evaluator caps see it. Each unshift is O(n).
- Repro: `python3 -c "print('A'+'[1]'*40000+' = 1')" > f.sel; php php/bin/sel f.sel` -> E_DEPTH after 9.7 s (n=5000 0.41 s, 20000 2.9 s, 40000 9.7 s); js does the 40000 file in 1.0 s. Synthesizer: 20000-index file, E_DEPTH at 1:59999 after 1.9 s (loaded box).
- Fix sketch: `$chain[] = $n['idx']; ... $chain = array_reverse($chain);`; better, check chain length against MAX_DEPTH while walking and stop. Reviewer measured with that patch on a scratch copy: 40000 goes 9.6 s -> 1.5 s (the rest is the parser).
- Conformance gap: `10-limits.selt` covers the E_DEPTH result, not the time; a timeout-bounded case would.

### PHP-C15 [medium] [confirmed; not re-verified] A failed parse of a very long flat chain still segfaults PHP: several throw sites do not dismantle the partial tree
Found by: slice 1 C2.
- Where: `php/src/Parser.php:274-282` (parseProgram trailing-token check), `419-425` (comparison-chain error; `$right` local), `495-505` (parsePostfix: `$idx` when `expectOp(']')` fails), `525-538` and `617-632` (parseCall/parsePipeStep: `$inner`/`$args` when expectOp(')'), E_UNKNOWN_FUNC or E_ARITY fires), `596-606` (parenthesised primary: `$inner`).
- What: the file's header (`dismantle()`, 204-241) says an abandoned deep tree must be taken apart iteratively because PHP frees nested arrays recursively and dies with SIGSEGV. parseSequence/parseList/parseTerm do that, but a chain (`1+1+1+...`) held in a local variable at the moment an exception is thrown from the listed sites is freed recursively during unwinding. `1+1+...` is one frame building a tree as deep as the source is long. (Also true for a successful compile whose Program is discarded without `__destruct` running - not in this slice.)
- Repro (`php -d memory_limit=-1 php/bin/sel FILE`, exit 139, "Segmentation fault"): `NOSUCH(1` + `+1`*300000 + `)`; `1` + `+1`*300000 + `)`; `A[1` + `+1`*300000 + ` ` (missing `]`); `1 == 1` + `+1`*300000 + ` == 2`. The same chain with a valid program gives the proper runtime E_DEPTH. Threshold: `NOSUCH(...)` with 150000 `+1` -> E_UNKNOWN_FUNC (correct), 200000 -> segfault. Needs ~400 KB of source and >~300 MB memory_limit (128M default dies earlier with a PHP fatal, see PHP-P10), so reachable only where memory_limit is raised.
- Fix sketch: wrap each site in the same try/catch-dismantle idiom, or a small helper that dismantles a list of local nodes: `$node` in parseProgram, `$right`/`$left` in the N branch, `$idx`/`$node` in parsePostfix, `$inner`/`$args` in parseCall/parsePipeStep/parsePrimary, `$args` (+ `$left` for pipe steps) in finishCall.
- Conformance gap: none - only feasible as a generated large case in the fuzz/`check-api` lanes (syntax error after a 250k-operator flat chain must report E_SYNTAX, not crash).

### PHP-C16 [medium] [confirmed; re-verified by synthesizer] Right-associative `??` / `???` chains are not depth-counted (spec 6.4)
Found by: slice 1 C3. Cross-host spec gap (JS RangeError, Python RecursionError, C++ SIGSEGV; PHP is only spared the segfault).
- Where: `php/src/Parser.php:409-413` (`if ($assoc === 'R') { $right = $this->parseTerm($bp); ... }`).
- What: `a ?? b ?? c ...` recurses parseTerm -> termLoop -> parseTerm once per operator with no `enter()/leave()`, unlike assignment (counted at 396) and prefix operators. 6.4: "every construct that can nest is counted". PHP never raises E_DEPTH; it runs until memory_limit and dies with a PHP fatal (uncatchable, not a SelError).
- Repro: `1` + ` ?? 1`*20000: php -> `1` (rc 0; re-verified); node -> uncaught RangeError; python -> RecursionError; cpp -> rc 139. 200000 operators: php -> "Allowed memory size of 134217728 bytes exhausted". Expected per 6.4: E_DEPTH at the operator that crosses the limit, identical on every host.
- Fix sketch: in the `'R'` (non-assignment) branch wrap the recursive parseTerm in `enter($t)`/try/finally `leave()` as the assignment branch does, in all hosts; pin the cost in spec 6.4 plus a `lim.coalesce-depth` case (existing pins are unaffected).
- Conformance gap: none - suggest `lim.coalesce-depth` / `lim.coalesce-depth-just-under` next to `lim.assign-depth`.

### PHP-C17 [medium] [confirmed, no-GMP only] `mulAbs` limb accumulators overflow to float -> uncaught `TypeError`
Found by: slice 2 C3.
- Where: `php/src/Dec.php:210-241` (7-digit limbs, `$acc[$i+$j] += $ai * $limbsB[$j]`, `intdiv($t, 10000000)` at `:225`).
- What: each product is < 1e14 and up to min(na,nb) products land in one accumulator with no intermediate carry. Past min(na,nb) > 92,233 limbs (~645,600 digits) the sum exceeds PHP_INT_MAX, becomes float and `intdiv()` throws PHP's TypeError (a host exception, not a SelError; violates 6.4/errors). 650,000 digits with 350,000 fractional digits is inside both caps (int digits 300k, frac 350k) and the product is too. Only reachable without ext-gmp (a supported configuration; plan item 6.4).
- Repro (Dec API, `scratchpad/php2/mul_overflow.php`): `$a = Dec::parse(str_repeat('9',300000).'.'.str_repeat('9',350000)); Dec::mul($a,$a);` after ~3 min: `PHP Warning: The float 9.22339907765E+18 is not representable as an int` then `TypeError: intdiv(): Argument #1 ($num1) must be of type int, float given`. With GMP the same call returns in 0.9 s. (A CLI repro would need a 1.3 MB source file; not built.)
- Fix sketch: carry-normalise every ~9000 rows of `i` (or 6-digit limbs), or add Karatsuba (PHP-P5) whose base case has no such bound; wrap the fallback core so no host exception escapes.
- Conformance gap: none - a no-GMP unit test with two 646k-digit operands (slow) or a direct limb-bound assertion.

### PHP-C18 [medium] [confirmed] Class escapes used as range endpoints are expanded into a wrong range (`[+-\d]`, `[\s-x]`, `[\w-a]`)
Found by: slice 4 C3. Cross-host: all hosts agree on the same wrong meaning; native engines reject these patterns.
- Where: `php/src/Builtins/Regex.php:271-275` (EXPAND_INSIDE substitution in `validateClass`).
- What: `\d \w \s` inside a class are textually replaced by `0-9`, `0-9A-Za-z_`, ` \t\n\r\f\x0b`; adjacent to `-` the replacement turns into a range of unrelated characters. JS u-mode ("Invalid character class"), Python ("bad character range") and PCRE2 ("invalid range in character class") all reject them. `[+-\d]` becomes `[+-0-9]` = range `+`..`0`, `-`, `9`; matches `/` not `5`.
- Repro: `php php/bin/sel -e "RMATCH('^[+-\d]\$','5')"` -> FALSE (expected E_REGEX_SYNTAX; every host FALSE). `RMATCH('^[+-\d]\$','/')` -> TRUE. `RMATCH('^[\s-x]\$','a')` -> TRUE. `RMATCH('^[\w-a]\$','`')` -> TRUE. Native: `node -e "new RegExp('^[\\\\s-x]$','u')"` throws; `php -r 'var_dump(preg_match("/^[\\s-x]$/u","a"));'` warns "invalid range" and returns false.
- Fix sketch: in `validateClass` track whether the previous item was a class escape and whether the next non-`]` char is an unescaped `-` (or the escape follows a `-`); reject with E_REGEX_SYNTAX ("class escape cannot be a range endpoint") as ES u-mode does. All hosts, spec and case first.
- Conformance gap: none - suggest `re.reject.class-escape-in-range` for `[+-\d]`, `[\d-z]`, `[a-\s]` (the last is already rejected everywhere, by accident).

### PHP-C19 [medium] [confirmed] `[a[:alpha:]`, `[a[.b.]`, `[a[=b=]`: PHP raises E_REGEX_SYNTAX where every other host matches
Found by: slice 4 C4.
- Where: `php/src/Builtins/Regex.php:250-252` (POSIX-class check runs only at the very start of a class).
- What: PCRE parses `[:name:]`, `[.x.]`, `[=x=]` anywhere in a class; ECMAScript, Python, C++ (SRELL) and Lisp treat them as literal characters. So the validator accepts a pattern PHP cannot compile (or, for ones PCRE accepts, silently reads as a POSIX class).
- Repro: `php php/bin/sel -e "RMATCH('[a[:alpha:]','[')"` -> E_REGEX_SYNTAX "PCRE rejected"; js/py/cpp/lisp TRUE. Same for `RMATCH('[a[.b.]','[')` and `RMATCH('[a[=b=]','[')`.
- Fix sketch: reject an unescaped `[` inside a class when followed by `:`, `.` or `=` (not only at class start), or emit `\[` for every literal `[` in a class (valid in both engines).
- Conformance gap: none - suggest `re.reject.posix-class-not-at-start`.

### PHP-C20 [medium] [confirmed; re-verified by synthesizer] RREPLACE with a pattern that can match empty: PHP (and C++) retry non-empty at the same position, JS/Python/Lisp advance by one
Found by: slice 4 C5. Cross-host split (2/3) - spec is silent.
- Where: `php/src/Builtins/Regex.php:442-447` (`preg_match_all`).
- What: after an empty match at p, `preg_match_all` retries at p with NOTEMPTY_ATSTART|ANCHORED; ECMAScript `replace(/re/g)` and Python `re.sub` step past p. Results differ for patterns whose empty alternative is preferred (lazy `b*?`, `(?:|a)`).
- Repro: `php php/bin/sel -e "RREPLACE('b*?','-','abb')"` -> `-a-----` (re-verified); js/py/lisp -> `-a-b-b-` (re-verified for js); cpp `-a-----`. `RREPLACE('(?:|a)','-','aa')` -> `-----` vs `-a-a-`. `COUNT(SPLIT(RREPLACE('x??','-','xx'),'-'))` -> 6 vs 4.
- Fix sketch: spec picks one (ES/Python is the majority; "advance one code point after an empty match" is simple). In PHP replace `preg_match_all` with a loop of `preg_match($re, $s, $m, PREG_OFFSET_CAPTURE|PREG_UNMATCHED_AS_NULL, $offset)`, copying one code point after an empty match at byte p, keeping full-subject calls so `^`/lookbehind context is right. Also fixes PHP-C23 (RREPLACE memory) and PHP-P18.
- Conformance gap: none - suggest `re.replace.lazy-empty-then-nonempty` (`RREPLACE('b*?','-','abb')`).

### PHP-C21 [medium] [confirmed] Capture groups inside a quantified group: PCRE keeps the last participating value, ECMAScript resets each iteration
Found by: slice 4 C6. Cross-host (PHP/Python/Lisp vs JS/C++).
- Where: `php/src/Builtins/Regex.php:416-429, 433-464` (engine behaviour; no rewrite possible except by refusal).
- What: `(?:(a)|b)*` on `ab`: PCRE/Python/Lisp report group 1 = `a`; JS/C++ report the empty text. Spec 7.8 says only "a capture that did not participate in the match yields TEXT `""`", ambiguous here.
- Repro: `php php/bin/sel -e "RGROUPS('(?:(a)|b)*','ab')"` -> group `a` (php, py, lisp) vs `""` (js, cpp). Also `RGROUPS('(?:(a)|(b))+','ab')` and `RGROUPS('(z)((a+)?(b+)?(c))*','zaacbbbcac')` (group 5 `bbb` vs `""`).
- Fix sketch: spec decision: refuse capturing groups under a quantifier (portable-subset rule, E_REGEX_SYNTAX; cheapest, matches how `\b` was handled) or specify ES semantics and emulate (hard).
- Conformance gap: none - suggest `re.groups.quantified-group-capture` once decided.

### PHP-C22 [medium] [confirmed] SORT/SORT_BY comparator is not a total order for mixed number-shaped and other text; hosts return different orders
Found by: slice 4 C10. Design common to all hosts.
- Where: `php/src/Builtins/Core.php:207-239` (`compareValues`).
- What: two values that both look numeric compare numerically, other text compares by bytes. With `"9"`, `"10"`, `"1a"`: 9 < 10 (numeric), 10 < 1a (bytes), 1a < 9 (bytes): a cycle, so `usort`'s result depends on input order and host algorithm. `docs/functions.md` says "Numbers sort as numbers and text by its UTF-8 bytes"; `rel.sort.mixed` pins kinds only.
- Repro: `php php/bin/sel -e "SORT(LIST('9','1a','10','2b','30','3')) .> JOIN(',')"` -> PHP `1a,9,10,2b,3,30`; js/py/cpp `10,1a,2b,3,9,30`; lisp `1a,2b,3,9,10,30` (three different answers). `SORT(LIST('10','9','1a'))` -> `1a,9,10` (php/js/py) vs `9,10,1a` (cpp/lisp).
- Fix sketch: define a total order in the spec and every host (rank first: NULL < BOOL < number-shaped < other TEXT < BIN, then compare within rank), or refuse the mix. A rank-first comparator also makes the decorated sort of PHP-P3 a single-key sort.
- Conformance gap: partial (`rel.sort.mixed` covers kinds only) - suggest `rel.sort.number-shaped-vs-plain-text`.

### PHP-C23 [medium] [confirmed] Memory-limit walls at ordinary sizes: per-code-point arrays, RREPLACE match arrays, hash-array tokens
Found by: slice 4 C8 and C9, slice 1 P1 (memory side). The PHP fatals are uncatchable; other hosts answer.
- Where: `php/src/Builtins/Text.php` (LEN 51, LEFT 55, RIGHT 60, SUBSTR 67, FIND 81-82, REPLACE 99-101, SPLIT 120-121, TRIM 188, BACKWARDS 153, pad 232, CODE 177, asciiCase 213) all via `Utf8::chars()`; `Regex::escapeDelimiter/validate`; `Regex.php:442-447`; `Lexer.php:107,144,154,170` and Parser node arrays.
- What: (a) a packed PHP array costs 16 B/element and doubles on growth, so at `memory_limit=128M` any text above 4,194,304 code points is fatal in LEN, CODE, LEFT, ...; (b) RREPLACE keeps every match as a nested array (~250 B), so ~500,000 matches exceed 128M; (c) tokens are 5-key hash arrays (~397 B/token) and AST nodes ~560 B/node, so the default limit is a wall at ~130 KB of dense source, the parse dying with a PHP fatal instead of a SelError (a 600 KB source dies inside the lexer).
- Repro: (a) `php php/bin/sel -e "LEN(REPEAT('ab', 3000000))"` -> PHP Fatal at `Utf8.php` line 84 (others print 6000000); `LEN(REPEAT('a', 4000000))` works, 6-8M fatals. (b) `php php/bin/sel -e "LEN(RREPLACE('a','bb',REPEAT('a', 500000)))"` -> PHP Fatal (200,000 matches fine: 400000; others print 2000000 for 1,000,000). (c) `1` + `+1`*100000 (200 KB, 200,002 tokens): lexing 79.4 MB = 397 B/token, parsing another ~56 MB for 50K operators; at 100K operators the parse dies with "Allowed memory size of 134217728 bytes exhausted".
- Fix sketch: (a) byte-level counting/slicing (PHP-P1); (b) streaming `preg_match` loop (PHP-C20) or a constant-memory `preg_replace_callback`; (c) token as small final class or packed list (PHP-P11) and a source-size/token-count guard turning "fatal" into a SelError.
- Conformance gap: none (memory limits are not conformance material).

### PHP-C24 [medium] [confirmed; re-verified by synthesizer] BUCKET reports `_K` as an invented counter for an element whose record key is `""`
Found by: slice 5 C1.
- Where: `php/src/Builtins/Structure.php:1756` (`Value::text($key === '' ? (string) (++$index) : $key)`).
- What: spec 7.3: within a body `_K` is the element's key. PHP substitutes a running counter whenever the key is `""` (legal for a RECORD); the other four hosts and PHP's own MAP and TOP give `""`. `$index` advances only on empty keys, so it is not a position; looks like a leftover from an earlier layout.
- Repro: `php php/bin/sel -e 'BUCKET(RECORD("",5,"x",6,"y",7), _K)'` -> `-{"1"=-{"1"=t"5"}, "x"=..., "y"=...}`; js gives `-{""=-{"1"=t"5"}, ...}` (re-verified both). `BUCKET(RECORD("",5,"x",6), IF(_K $== "", "empty", "other"))` -> PHP `{"other"={5,6}}`, others `{"empty"={5}, "other"={6}}`. `MAP(RECORD("",5,"x",6), _K)` is fine everywhere.
- Fix sketch: bind `Value::text($key)` unconditionally and drop `$index`; `forEachElement` already yields the true key.
- Conformance gap: none - suggest `rel.bucket.empty-record-key-K`: `BUCKET(RECORD("",5,"x",6), IF(_K $== "", "empty", "other"))` expecting `-{"empty"=-{"1"=t"5"}, "other"=-{"1"=t"6"}}`.

### PHP-C25 [medium] [confirmed; re-verified by synthesizer] BUCKET of a scalar returns empty instead of grouping the one-element list the scalar stands for
Found by: slice 5 C2. Cross-host: only cpp is right; PHP, JS, Python, Lisp wrong (Lisp also drops TOP's scalar case).
- Where: `php/src/Builtins/Structure.php:1724` (`$value->isNull() || $value->size() === 0`).
- What: SPEC 7.3: an aggregate whose first argument has no children treats it "as a one-element list containing itself when it has a scalar", only NONE as empty. `size() === 0` is also true for every scalar TEXT/BIN/BOOL, so the early return swallows them. DEDUPE, TOP, MAP and LINK take the scalar path correctly in PHP.
- Repro: `php php/bin/sel -e 'BUCKET("abc", _)'` prints `-` (re-verified); `cpp/build/sel -e 'BUCKET("abc", _)'` prints `-{"abc"=-{"1"=t"abc"}}` (re-verified). `BUCKET("abc", _, COUNT(_))` -> `-` in PHP, `-{"1"=t"1"}` in cpp. `TOP("abc",_,1)` is empty only in lisp.
- Fix sketch: test `$value->kind === Value::NONE && $value->size() === 0` instead of `size() === 0`; the rest already handles scalars through `forEachElement`. Fix other hosts in the same change.
- Conformance gap: none - suggest `rel.bucket.scalar-is-one-element-list` and `rel.top.scalar-is-one-element-list`.

### PHP-C26 [medium] [confirmed, cross-host design] Joined rows hold each binder row under three keys, so deep copy / hash / EQL / dump blow up exponentially with join depth
Found by: slice 5 C4.
- Where: `makeJoinedRow` (`Structure.php:505-547`), `rowPlan` (`:557-594`): left row under `A`, `a`, `_1`; right under `B`, `b`, `_2`; each row itself carries alias entries. `copy()`, `structuralHash()`, `eqlAt()`, `dump()` walk the shared PHP objects as a tree.
- What: LINK itself is cheap through sharing, but assignment `Y = LINK(...)` (deep per spec), DEDUPE/BUCKET hashing, EQL or dumping multiply size ~10x per chained LINK. Not PHP-specific (JS 1.3 s, cpp 2.1 s at 6 levels, python 4.2 s, PHP 4.7 s). No limit bounds it; E_DEPTH does not trigger.
- Repro: `B=LIST(RECORD("id",1)); Y=LINK(LINK(LINK(LINK(LINK(LINK(LINK(B,B,A1,C1,TRUE),B,A2,C2,TRUE),B,A3,C3,TRUE),B,A4,C4,TRUE),B,A5,C5,TRUE),B,A6,C6,TRUE),B,A7,C7,TRUE); COUNT(Y)` takes 52 s in PHP. Without the `Y=` about 0.1 s. By chain length with assignment: k=4 0.17 s, k=5 0.46 s, k=6 4.7 s, k=7 52 s. `COUNT(DEDUPE(<k=6 chain>))` 4.1 s.
- Fix sketch: spec decision (e.g. do not store binder rows under three keys, or cap size/work). Host-only mitigation: memoise `copyAt` / `updateStructuralHash` per shared object within one traversal - safe for hashing and equality, not for copy (must yield unshared results).
- Conformance gap: none - a time-boxed case with a 6-deep chain and an assignment.

### PHP-C27 [medium] [confirmed] Group-path `SUM(g, body)` skips every kind check the relation path applies
Found by: slice 6 C2.
- Where: `php/src/Sql/Translator.php:702-709` (vs `aggBody`, `:1993-2011`, which calls `requireNum`).
- What: `SUM(ITEMS, _["STATUS"])` over a TEXT field is refused, but the same body under a BUCKET group is emitted bare. TEXT, BOOL, UNKNOWN and constant text bodies pass and no `numericGuard` is applied. SEL raises E_NOT_NUM; MariaDB/MySQL sum numeric prefixes (`sql-kinds.md` 4.1), so a group total is a definite answer SEL would not give.
- Repro (`php6/t9.php`): `O .> BUCKET(_["CAT"], RECORD("s", SUM(_, _["STATUS"])))` -> `COALESCE(SUM(\`status\`), 0)`; same for BOOL (`SUM(\`flag\`)`) and `SUM(_, "x")` (compounds with PHP-C7 into `SUM(1)`).
- Fix sketch: route the body through `requireNum()` and the same constant check (`Constants::requireNumeric`), i.e. reuse `aggBody` instead of duplicating.
- Conformance gap: none - suggest `--- error E_SQL_SHAPE` cases for a TEXT and a BOOL field under a bucket SUM.

### PHP-C28 [medium] [confirmed; cross-host design] Kind unification launders UNKNOWN into NUM/BOOL, so numeric and boolean guards are skipped
Found by: slice 6 C3. JS emits the same SQL (checked).
- Where: `php/src/Sql/Translator.php:2576-2593` (`unify`), used by `conditional()` (`:1435`) and `@unify` in `retKind` (`:2544`) for `??`, `???`, COALESCE.
- What: `unify` returns the first known kind, so `U ?? 1` or `IF(F, U, 1)` gets NUM even though `U` is an undeclared column; `numericOperand` returns a NUM fragment untouched and `requireBool` accepts BOOL, bypassing the guards promised by `sql-kinds.md` 4-5 and `sql-translation.md` 8. Per the doc's own MariaDB measurements (`1 AND TRUE` is 1, `CAST('x' AS DECIMAL)` is 0) this selects rows SEL refuses with E_NOT_BOOL / E_NOT_NUM.
- Repro (`php6/t7.php`, mariadb): `U + 1` is guarded (`CASE WHEN (u REGEXP ...) THEN CAST(...) ELSE NULL END + 1`) but `(U ?? 1) + 1` is `(COALESCE(\`u\`, 1) + 1)` and `IF(F, U, 1) == 1` is `(CASE WHEN f THEN u ELSE 1 END = 1)`; `IF(F, U, TRUE) AND TRUE` and `(U ?? TRUE) AND F` emit with no refusal while `U AND TRUE` is refused. `ABS(U ?? 1)`, `MIN(U ?? 1, 2)`, `LEFT("abc", U ?? 1)`, `SUM((1,2), _ * (U ?? 1))` likewise skip the guard `ABS(U)` gets.
- Fix sketch: `unify` returns UNKNOWN whenever any branch is UNKNOWN (still may refuse two conflicting known kinds); typed branches stay typed.
- Conformance gap: none - suggest `--- error` cases for `IF(F, U, TRUE) AND TRUE` and a guarded-output case for `(U ?? 1) + 1`.

### PHP-C29 [medium] [confirmed] A static-list element that mentions `_K` is evaluated in the inner aggregate's scope
Found by: slice 6 C4.
- Where: `php/src/Sql/Translator.php:1965-1967` with `:2021-2035` (`withElement` always binds `_K`), `:1544-1548` (`fromBinder` NODE renders the element node lazily in the current frame).
- What: literal-list elements are stored as AST nodes and rendered where the binder is used, i.e. inside the inner frame. A free `_K` in an element refers to the enclosing aggregate's key in SEL but resolves to the inner frame's `_K` here. An element mentioning the inner binder's name recurses until the depth guard.
- Repro (`php6/t18.php`): `ANY(("a","b"), o, ANY((_K, "zz"), c, c $== "2"))`: SEL TRUE (re-run by synthesizer: `php php/bin/sel` prints TRUE); the translation compares `'1'` and `'zz'` with `'2'` in both outer iterations: constant FALSE. `ALL(("a","b"), o, ANY((_K, "zz"), c, c $== _K))`: SEL FALSE, translation TRUE. `ANY(("a","b"), o, JOIN((_K, "x"), "-") $== "2-x")`: SEL TRUE, translation `CONCAT('1','-','x')` in both iterations. Related: `ANY((X, 2), X, X > 1)` is refused as E_SQL_DEPTH while SEL answers TRUE.
- Fix sketch: resolve the element's free names in the frame stack as it was when the list was written (capture depth in `Binder::node`, render with frames truncated to it), or substitute `_K`/binder name before pushing the frame; failing that, refuse such elements.
- Conformance gap: none - first repro as an `agg.nested.*` case; pin the E_SQL_DEPTH-for-a-valid-program case.

### PHP-C30 [medium] [confirmed] A FILTER's binder name is bound in the aggregate body's scope
Found by: slice 6 C5.
- Where: `php/src/Sql/Translator.php:2026-2028` (`withElement`: `$frame[$filter['binder']] = $elem`) and `:2087-2089` (`withRow`).
- What: absorbing a FILTER binds every filter binder to the same element in one shared frame; body and other filters render in it. In SEL the filter's binder exists only inside its predicate. A body naming an outer variable spelled like a filter binder reads the element; a body/predicate naming the other binder (undefined in SEL) translates.
- Repro (`php6/t19.php`, `X` a NUM column): `ALL(FILTER((1,2,3), x, x > 1), y, y < X)` renders `(1 < 1)`, `(2 < 2)`, `(3 < 3)` - column `x` never appears; with `x = 5` SEL is TRUE and the SQL FALSE. `ALL(FILTER((1,2,3), x, y > 0), y, y < 9)` translates while SEL raises E_UNDEF_VAR (column 32).
- Fix sketch: render each filter predicate in a frame holding only its own binder (and `_K`), and the body in a frame holding only the body binder.
- Conformance gap: none - suggest `agg.filter.binder-does-not-leak-into-body` (row and static shapes).

### PHP-C31 [medium] [confirmed] JOIN over a static list or columns binding bypasses the `&` BOOL guard
Found by: slice 6 C6.
- Where: `php/src/Sql/Translator.php:2279-2291` (`joinAggregate`, `foldPairwise('&')`); guard lives only in `binary()` `:482-485`.
- What: `A & B` with a BOOL operand is refused ("SEL answers E_NOT_TEXT") but JOIN unrolls into the same `&` template without `requireNotBoolOperand`, on elements or separator. MariaDB concatenates a boolean as `1`, PostgreSQL as `true`; SEL raises E_NOT_TEXT (`JOIN(("a",TRUE), "-")`).
- Repro (`php6/t10.php`, `F` a BOOL column): `JOIN((F,"a"), "-")` -> `CONCAT(CONCAT(\`f\`, '-'), 'a')`; `JOIN((T,"a"), F)` uses the BOOL column as separator; a `columns` binding of (TEXT, BOOL) joins without complaint; `F & T` is refused.
- Fix sketch: in `joinAggregate` run `requireNotBoolOperand` on each element fragment and on the separator (and the relation-branch `sep`).
- Conformance gap: none - suggest `refuse.join-bool-element`, `refuse.join-bool-separator`.

### PHP-C32 [medium] [confirmed] No bound on generated-SQL size: 20 short assignments produce 8 MB / 17 s (2^n blow-up), also in hybrid planning
Found by: slice 6 C8, slice 7 C6. Cross-host (JS n=16 gives 524 KB).
- Where: `php/src/Sql/Normalise.php:25-33, 154-160` and `:158-232` (`substitute` inlines every read of a defined name and returns `$defs[name]` verbatim, no size accounting); `Translator` renders every use. No budget anywhere.
- What: stage 1 replaces each read of `A` with A's whole right-hand side. Depth 200 bounds nesting, not fan-out, so `A0 = X + X; A1 = A0 + A0; ...` is linear to evaluate in SEL and 2^n in translation. Static-list aggregates multiply the same way (`ANY(L, ANY(L, ANY(L, ...)))` unrolls to |L|^3 bodies from ~200 chars: k=25 gives 15625 leaves, 450 KB, 2 s). A PHP memory-limit failure is a fatal, not a `SqlError`, so `tryTranslate()` cannot turn it into "stay in memory". Not in `sql/errors.md` or the DoS discussion.
- Repro: slice 6 (`php6/t3.php`): n=14: 131 KB, 0.24 s; n=18: 2.1 MB, 3.6 s; n=20: 8.4 MB, 17.7 s (peak PHP memory small; time and output are the cost). Slice 7 (`p7/d1.php`, helper pattern `A1 = LEN("x") + LEN("y"); A2 = A1 + A1; ...; ORDERS .> FILTER(_["id"] > An)`): n=8 91 ms / 4 KB; n=12 1.6 s / 66 KB; n=14 6.4 s / 262 KB; n=16 26.9 s / 1.05 MB (`planHybrid` same plus ~10%).
- Fix sketch: charge every rendered node against a budget and refuse past it (new `E_SQL_SIZE` / `E_SQL_TOO_LARGE` / E_SQL_UNSUPPORTED, added to `spec/limits.json`), or in stage 1 refuse / leave local a non-leaf helper read more than once when the expanded node count exceeds a limit; or share subtrees by emitting a reused variable once.
- Conformance gap: none - a case with 30 doubling assignments expecting a refusal.

### PHP-C33 [medium] [unconfirmed: needs a MariaDB/MySQL server] Text-literal operands of arithmetic are emitted as bare quoted strings, which MariaDB/MySQL evaluate as DOUBLE
Found by: slice 6 C7.
- Where: `php/src/Sql/Translator.php:465-479` and `:2636-2649` (`guardNumeric` returns constants unchanged); template `({0} + {1})` in `sql/dialects/mysql-family.json`.
- What: SEL lets a numeric-looking text literal take part in arithmetic and computes exactly. For a constant operand the translator only validates it and emits `'0.1'`; MariaDB and MySQL convert string operands of `+ - * / %` to DOUBLE (MySQL manual, "Type Conversion in Expression Evaluation"), so `'0.1' + '0.2' = 0.3` is not exact there. PostgreSQL is fine (template casts to NUMERIC); SQLite has no exact decimals anyway. The oracle corpus only has integral cases (`sql/oracle/expressions.selo:780-788`).
- Repro: emission confirmed (`php6/t6.php`): `"0.1" + "0.2" == 0.3` on mariadb -> `(('0.1' + '0.2') = 0.3)`; SEL answers TRUE (`php php/bin/sel -e '"0.1" + "0.2" == 0.3'`). Server behaviour (expected 0) not run. Same for `N * "0.1"` and a TEXT-typed `value` binding in arithmetic.
- Fix sketch: render a constant text operand in a numeric position through `Emit::numericLiteral` (exact, canonical) or `numericCast`.
- Conformance gap: none - oracle line `"0.1" + "0.2" == 0.3` and a `.sqlt` case pinning the emitted operand.

### PHP-C34 [medium] [confirmed] Helper that rebinds a relation name is applied twice (`ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)`)
Found by: slice 7 C3. JS emits the same `OFFSET 4`.
- Where: `php/src/Sql/Hybrid.php:136` (`unwindThroughHelpers`, 845-858) with `917-922` (`withHelpers`) / `889-907`.
- What: the planner unwinds through the helper (prepending its definition to the steps) and also keeps the assignment in front of the tree because the tree still reads `ORDERS`; stage 1 inlines that assignment again, so DROP applies twice. Plain `translate()` is right. Only when the helper name equals the pipeline source (`$seen` stops the loop but the assignment survives `referencedAssignments`).
- Repro (sqlite harness): `ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)` memory ids 3,4,5; plan is `pure_sql` with `LIMIT 3 OFFSET 4` -> ids 5,6,7. Hybrid variant with a REPEAT MAP: ids 3-5 vs 5-7.
- Fix sketch: after unwinding through `defs[N]` where N is also read by the result, drop that assignment from `$leading`, or refuse to unwind when a def's source is its own name.
- Conformance gap: none - `ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)` in `25-hybrid-plans`.

### PHP-C35 [medium] [confirmed on SQLite; unconfirmed on MariaDB] Pure-SQL plans do not preserve SEL's order through SORT_BY .> LINK (and BUCKET group order)
Found by: slice 7 C4 (confirmed), slice 6 C13 (unconfirmed: MariaDB drops ORDER BY in a derived table without LIMIT).
- Where: classification `php/src/Sql/Hybrid.php:160-170`; root cause in `Translator.php` join/derived-table rendering (`:3561` `ensureDerived($plan, planHasRowsAbove)` for LINK, comment `:2947-2952`).
- What: `SORT_BY` then `LINK` renders `FROM (SELECT ... ORDER BY id DESC) _sub1 INNER JOIN customers c ON ...` with no outer ORDER BY; SQL does not promise a subquery's order survives a join; SQLite reorders loops and returns ascending. SEL promises "left order then right order" (7.4); with `TAKE(2)` the wrong rows are selected. BUCKET: SEL yields groups in first-appearance order, SQL `GROUP BY` in engine order.
- Repro (sqlite): `ORDERS .> SORT_BY(_["id"], "DESC") .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> MAP(RECORD("o", _["O"]["id"], "n", _["C"]["name"])) .> TAKE(2)` memory o=10,8; pure_sql o=1,2. `ORDERS .> SORT_BY(_["id"], "DESC") .> BUCKET(_["customer_id"]) .> MAP(RECORD("c", _K, "n", COUNT(_)))` memory groups 11,14,12,10,13; SQL 10,11,12,13,14. Slice 6: `O .> SORT_BY(_["TOTAL"]) .> LINK(C, ...)` emits `FROM (SELECT ... ORDER BY total ASC) _sub1 INNER JOIN ...` (`php6/t4.php`); MariaDB behaviour not run.
- Fix sketch: carry the left ORDER BY to the outer statement of a join (or classify sort+LINK as non-pushable unless the outer statement re-sorts); for BUCKET add an ORDER BY reproducing first appearance (needs a row rank) or declare group order engine-defined; or refuse/keep in memory a LINK after an un-LIMITed sort.
- Conformance gap: none - add a statement-oracle row; group order is not discussed in 12.1.

### PHP-C36 [medium] [confirmed] Hybrid/optimiser reorders and pushes steps ahead of local work, suppressing errors SEL would raise
Found by: slice 7 C5 and C11.
- Where: `php/src/Sql/Hybrid.php:482` (`FALLTHROUGH_DOWNSTREAM`), `583-595`, `641-643`; `Hybrid.php:140-143` (`Optimizer::optimize(..., false, $options)`, root cause in the Optimizer).
- What: with a partially local `MAP(RECORD(pushable..., custom...))` every downstream TAKE/DROP/TOP_BY goes into SQL and the continuation is the MAP alone over surviving rows; SEL evaluates the MAP on every row first, so an error in the custom half on a discarded row is never seen. Separately the logical optimiser moves a FILTER before a SORT_BY, so a sort-key error is not evaluated when the filter empties the input.
- Repro: `ORDERS .> SORT_BY(_["id"]) .> MAP(RECORD("a", _["id"], "z", REPEAT(_["name"], 1 + 0 * (1 / (_["id"] - 5))))) .> TAKE(2)` memory E_DIV_ZERO (row id 5), hybrid returns two rows; same with `SORT_BY(_["a"], "DESC") .> TAKE(2)`. `ORDERS .> SORT_BY(_["nokey"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("i", _["id"]))` memory E_NO_KEY, hybrid empty.
- Fix sketch: restrict downstream steps to those that never drop rows (SORT_BY) or require the custom half to be provably total; otherwise fall to the ordinary prefix split. Keep the swap only when the sort key cannot fail (known column), or accept and document.
- Conformance gap: none (`25-hybrid-plans` pins shape, not error equivalence).

### PHP-C37 [medium] [confirmed] `Map::defineDialect` accepts quote/escape pairs that make text literals injectable
Found by: slice 7 C7.
- Where: `php/src/Sql/Map.php:129-131, 491-537` (`checkLexical`) and `Emit.php:140-153` (`textLiteral`), `262-267` (`ident`).
- What: `checkLexical` checks each key alone. A dialect overriding `textQuote` but inheriting the parent's `textEscape` (or setting `textEscape` to `[]`) yields a quote character that is never escaped; the layer's own comment (Map.php:506-508) calls that class an injection. `ident` has the same property for `identQuote`/`identEscape`.
- Repro: `Map::defineDialect('q1', ['extends'=>'sqlite','lexical'=>['textQuote'=>'"']]); Emit::textLiteral('q1', 'a"b OR 1=1 --')` -> `"a"b OR 1=1 --"`. `['textEscape'=>[]]` on sqlite -> `'a'b OR 1=1 --'`. `['textQuote'=>'\'','textEscape'=>["'"=>"\\'"]]` on mysql emits `'a\\'b OR 1=1 --'`, i.e. escapes the quote with a backslash but the input `\'` is not touched.
- Fix sketch: after resolving the effective lexical set require: `textEscape` has a key equal to `textQuote`; `identEscape` contains `identQuote`; and if any escape value introduces `\` then `\` itself is a key. Refuse with LogicException like the other checks.
- Conformance gap: `11-registration` has the string-typed case only.

## Low

### PHP-C38 [low] [confirmed; re-verified by synthesizer] `Program::dependencies()` is order-insensitive: a variable read before it is assigned is not reported
Found by: slice 3 C7. Cross-host (js prints the same).
- Where: `php/src/Sel.php:121-129` (`array_diff($reads, $assigned)`).
- What: the doc-comment says "reads without having assigned it first" but every name assigned anywhere is subtracted. Spec 8: "every variable the program reads".
- Repro: `php php/bin/sel --deps -e 'A + 1; A = 2'` prints nothing (re-verified); the run needs `A` from the context.
- Fix sketch: track assignment order in `collect`, or change the comment/spec to "never assigned anywhere"; all hosts together.
- Conformance gap: none for read-before-assign order.

### PHP-C39 [low] [confirmed] Malformed `Value` constructor calls: silent truncation or a host `TypeError` instead of `E_BAD_ARG`
Found by: slice 2 C4.
- Where: `php/src/Value.php:299` (`int(int $n)`), `:236` (`text(string)`), `:260` (`bin(string)`), `:308` (`list(array)`), `:379` (`record`).
- What: SPEC 8: "A malformed call is E_BAD_ARG ... never the host's own exception"; `Value.bin` of a sequence of numbers takes whole numbers 0..255. The scalar type declarations mean a non-strict caller gets `Value::int(1.5)` = `t"1"` (deprecation only), `Value::int("7")`, `Value::int(true)`, `Value::text(5)`, `Value::bin(5)`, `Value::bool("x")` = TRUE succeed; strict callers and other misuse (`int(null)`, `text([])`, `bin([65,66])`, `list("x")`, `record(['a'],'x')`) throw TypeError. `Value::bin` cannot take a list of byte numbers; `fromEntries([[null,$v]])` turns a null key into `""`.
- Repro: `scratchpad/php2/badcalls.php` (non-strict caller).
- Fix sketch: drop scalar type declarations on public constructors (keep `mixed`), check `is_int/is_string/...` by hand and `fail('E_BAD_ARG', ..., null)`; give `bin` an `array` branch.
- Conformance gap: none - `tools/api.php` has `error.host.*` probes for `num`/`dec` only; add int/text/bin/list malformed-call probes.

### PHP-C40 [low] [confirmed] `Dec::cmp` fast path ignores `nativeNeg`; `Dec::checked` trusts a supplied native cache
Found by: slice 2 C5. Relates to plan item 5.2 (Dec::cmp fast path).
- Where: `php/src/Dec.php:686-691` (cmp), `:455-457` (checked); contrast `intMantissa` `:340-345` which validates both.
- What: `cmp`'s first branch checks only `nativeDigits`, so a descriptor from `Dec::parse('5')` edited to `neg=true` compares as +5. `checked()` returns the array untouched when a `native` key is present, so a forged `native=7` for digits '5' makes `F + 1` = 8 while `F` prints 5.
- Repro (`php2/forged2.php`): `$d = Dec::parse('5'); $d['neg']=true;` context X=Value::num($d), Y=3: `X < Y` -> FALSE (expected TRUE), `MIN(X,Y)` -> 3 (expected -5), text prints -5.
- Fix sketch: `cmp` uses `intMantissa()` (already validates); `checked` always rebuilds via `make()` (~0.3 us) or compares `native` with `parseMantissa`. (The cost interacts with PHP-P12.)
- Conformance gap: none; host-API only, hand-edited descriptors.

### PHP-C41 [low] [confirmed for un-normalised descriptors] ext-gmp paths parse digit strings with base auto-detection (octal)
Found by: slice 2 C6.
- Where: `php/src/Dec.php:69, 116, 162, 282` (`gmp_add($a,$b)` etc. with plain strings).
- What: `gmp_add("010","1")` is 9 (base 0: leading 0 = octal, `0x` hex); 8/9 after a leading zero throws ValueError. Internal call sites feed canonical digits, so unreachable through `Value`/evaluator; reachable through public `Dec::add/sub/mul/...` with a three-field descriptor whose digits have a leading zero (which the header allows): `Dec::add(['neg'=>false,'digits'=>'0100000000000000000001','scale'=>0], one)` = 1152921504606846978 with GMP, correct without.
- Repro: `scratchpad/php2/octal.php` (`php` vs `php -n`).
- Fix sketch: `gmp_init($s, 10)` or `ltrim` in the public entry points.
- Conformance gap: none.

### PHP-C42 [low] [confirmed] E_UTF8 for invalid source carries no position
Found by: slice 1 C4. C++ does the same (0:0); JS/Python cannot receive invalid source bytes.
- Where: `php/src/Lexer.php:46` (`Utf8::validate($source)` with no `$pos`), `Utf8.php:21-65`.
- What: SPEC 2 "Invalid UTF-8 in source is E_UTF8 at the offending byte"; errors.md: every error carries line/col/offset. PHP reports line 0 column 0; the byte index is only in the message.
- Repro: `printf '"a\xff" & 1' > x.sel; php php/bin/sel x.sel` -> `E_UTF8 at line 0 column 0`; same for `cpp/build/sel`.
- Fix sketch: `validate()` throws with (or returns) the byte offset; convert by counting code points in the valid prefix and line/col by counting `"\n"`; decide the spec question (offset of the first bad byte in code points of the valid prefix) and pin it. Needs a host-API test, not a `.selt`.
- Conformance gap: none (source-level E_UTF8 is untested; only FROM_UTF8 cases exist).

### PHP-C43 [low] [unconfirmed - no PHP 8.1] `strtoupper` / `strtolower` / `strcasecmp` are locale-dependent on the PHP versions `composer.json` still allows
Found by: slice 1 C5, slice 3 C9, slice 4 C12, slice 7 C13 (slice 2 notes the same for `strtolower` in `RecordShape::alias`).
- Where: `Lexer.php:154`; `Registry.php:26,100,160,198,207`; `Optimizer.php:539,540,660,679,683`; `Sql/Bindings.php:38-53`, `Sql/Binding.php:254-260`, `Hybrid.php` (many), `Map.php:164,549,677`; `Regex.php:311`; `Core.php:268,276,281,411` (`strcasecmp`); `Value.php` (`RecordShape::alias`). `Hybrid.php:242` and `Constants.php:150` already use the explicit `strtr` alphabet.
- What: composer allows `php >= 8.1`; these functions became locale-insensitive only in 8.2. Under a single-byte Turkish locale set by the embedder `i` would not map to `I`, so `if(...)`/`limit` become E_UNKNOWN_FUNC and flag `I` an unknown flag. Contradicts `docs/contributing.md` ("Never use the host's case mapping"). Identifiers are ASCII by construction, so impact is small; on 8.5 nothing is wrong.
- Repro: not run (needs 8.1 plus a Turkish locale).
- Fix sketch: a shared ASCII-upper helper `strtr($s, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')`, or raise the floor to 8.2 in `composer.json` and the docs.
- Conformance gap: none (needs a host-API test with `setlocale` on 8.1).

### PHP-C44 [low] [confirmed] Error catalogue says E_RANGE and E_UTF8 are run-time-only, but the PHP front end raises both at compile time
Found by: slice 1 C6.
- Where: `php/src/Limits.php:41-42` (generated from `spec/limits.json:30-31` `"E_UTF8"/"E_RANGE": {"phase": "run"}`); raised at `Lexer.php:46` (E_UTF8) and `:302` (E_RANGE for `\u{110000}` / surrogates); number literals past the digit cap raise E_RANGE from `Dec::parse` during compile.
- What: phases should be `both` for these; `errors.md` lists E_RANGE only under run time. Generator/manifest accuracy: a consumer of `Limits::ERROR_CODES` would get "compile() may throw these" wrong.
- Repro: `php php/bin/sel --deps -e '"\u{110000}"'` -> `E_RANGE at line 1 column 2` without evaluating anything.
- Fix sketch: set phase to `both` for E_RANGE and E_UTF8 in `spec/limits.json` and list them in `errors.md`'s compile-time table.
- Conformance gap: existing `lex.escape.surrogate-rejected` covers behaviour.

### PHP-C45 [low] [confirmed] PCRE structural limits make valid portable patterns E_REGEX_SYNTAX in PHP only
Found by: slice 4 C11.
- Where: `php/src/Builtins/Regex.php:346-349` ("PCRE rejected").
- What: PCRE2 refuses paren nesting deeper than 250 and patterns whose compiled size exceeds its link limit; JS and Python accept and match (C++/SRELL rejects deep nesting with `error_complexity`, as E_REGEX_SYNTAX). The spec's only regex cap is the 65,535 quantifier bound.
- Repro: 300 nested `(?:` around `a` (`RMATCH(<generated>, 'a')`): PHP E_REGEX_SYNTAX, js/py TRUE. `RMATCH('(?:a{60000}){60000}', 'a')` -> PHP E_REGEX_SYNTAX "PCRE rejected", js/py/cpp FALSE.
- Fix sketch: add nesting depth (and maybe total repeat size) to the spec's portable limits so every host rejects the same patterns; count group nesting in `validate()`.
- Conformance gap: none.

### PHP-C46 [low] [confirmed, cross-host] Bare BUCKET silently drops a group when two EQL-distinct keys spell the same text
Found by: slice 5 C3. All five hosts identical - a spec question.
- Where: `php/src/Builtins/Structure.php:1765-1791`.
- What: groups are identified by EQL (hash + `eql`), but the result record is keyed by `asText()`. A TEXT carrying children vs a plain TEXT (or two texts with different children) become two groups and the second `$out->set($keyString, ...)` overwrites the first. `bucketKeyText` only rejects `kind === NONE`. Spec 7.3: the two-argument key is "the key's scalar, verbatim, and only text or a number will do".
- Repro: `x="a"; x["k"]=1; y="a"; y["k"]=2; BUCKET(LIST(x,y), _)` gives `-{"a"=-{"1"=t"a"{"k"=t"2"}}}` on all five hosts (x is lost). The projected spelling returns two groups.
- Fix sketch: merge groups by `keyString` in the bare spelling, or refuse a key that has children with E_NOT_TEXT. Spec first.
- Conformance gap: none.

### PHP-C47 [low] [confirmed; spec/doc issue, all hosts agree] The BUCKET table row `BUCKET(list, [binder,] key)` implies a 3-argument binder+key form that no host has
Found by: slice 5 C6.
- Where: `spec/SPEC.md` line 673 vs `Structure.php:1729-1737` (3 arguments = key + proj; binder only with 4).
- Repro: `php php/bin/sel -e 'BUCKET(LIST(1,2,3), X, X > 1)'` -> `E_UNDEF_VAR at line 1 column 21`, identical on other hosts.
- What / Fix sketch: fix the spec row and `docs/functions.md` (or add a bare-identifier disambiguation on all hosts). A bare bucket with a custom binder cannot be written today.
- Conformance gap: none.

### PHP-C48 [low] [confirmed] Cosmetic: error message text differs from other hosts
Found by: slice 2 C7, slice 5 C5. Codes and positions agree (messages are not contract).
- `ROUND(1, 99999999999999999999)` reports "scale 9223372036854775807 exceeds the maximum" (JS/Python print the real number): `Dec.php:592-597` `toInt` saturates via `(int)"digits"` (re-verified). The saturation is correct for every in-range comparison tried; a future caller doing arithmetic on the clamp would turn it into a float.
- `LINK_LEFT(LIST(RECORD("k","१२")), LIST(RECORD("k",1)), A,B,A["k"]==B["k"])` prints `not a number: "१२"` (PHP, `Value.php:292` `json_encode($d)`) vs literal characters (js, cpp). Fix: `JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES` if parity is wanted.

### PHP-C49 [low] [confirmed] `Map::checkNumericGuard` marks the dialect as checked before validating it
Found by: slice 6 C10.
- Where: `php/src/Sql/Map.php:331-337` (`self::$guardChecked[$dialect] = true;` before the checks that throw `LogicException`).
- What: the first translation throws; every later one in the same process silently uses the mismatching `numericGuard`, i.e. the "answers for rows SEL refuses" case the check exists to stop.
- Repro (`php6/t22.php`): register a derived dialect with a guard lacking ISNUM's pattern; `U + 1` throws once, then emits `CASE WHEN (u IS NOT NULL) THEN CAST(u AS DECIMAL(65,10)) END` on calls 2 and 3.
- Fix sketch: set the flag only after all checks pass.
- Conformance gap: none (needs a runner-level test that translates twice).

### PHP-C50 [low] [confirmed, all hosts] TAKE/DROP counts written as whole numbers with a scale are refused with E_NOT_INT by the translator
Found by: slice 6 C11.
- Where: `php/src/Sql/Translator.php:3639-3641` (`evalIntParam`).
- What: spec 7.4 gives E_NOT_INT only for a non-integer; the evaluator accepts `2.0` (`TAKE((3,1,2), 2.0)` returns two elements, `LEFT("abc", 2.0)` is `ab`). Translator refuses `TAKE(2.0)`/`DROP(2.0)` because `scale !== 0` and claims SEL would refuse them. Safe refusal, false claim. JS same.
- Repro: `O .> TAKE(2.0)` -> E_NOT_INT (SqlError); `php php/bin/sel -e 'TAKE((3,1,2), 2.0)'` succeeds.
- Fix sketch: accept a value whose fraction is zero; all hosts.
- Conformance gap: `stmt.refusal.take-float` only pins 1.5 - suggest `stmt.take.whole-number-with-scale`.

### PHP-C51 [low] [confirmed] Over-refusals from stale "measured" argument-kind tables
Found by: slice 6 C12.
- Where: `php/src/Sql/Translator.php:1228-1231, 1278-1290` (`BIN_ARGUMENT_OK`, `BOOL_ARGUMENT_OK`).
- What: SEL accepts BOOL/BIN arguments in functions the tables refuse: `IS_BLANK(TRUE)` is FALSE, `COALESCE(TRUE, FALSE)`, `IS_NULL(TRUE)` succeed, yet `IS_BLANK(F)`, `IS_NULL(F)`, `COALESCE(F, F2)`, `IS_BLANK(B)` for BOOL/BIN columns are refused (`php6/t11.php`). The other way round `??`/`???` take no argument-kind check (`F ??? F2`, `B ??? B2` translate). Safe direction only.
- Fix sketch: re-measure with the loop the comment describes and regenerate both sets.
- Conformance gap: none.

### PHP-C52 [low] [unconfirmed] Numeric literals of more than 65 digits are emitted bare on MariaDB/MySQL with no caveat
Found by: slice 6 C14.
- Where: `Emit::numericLiteral` (`php/src/Sql/Emit.php:105-138`), reached from `literal()`.
- What: `sql-translation.md` 11.3 lists scale caps but not the literal-width limit. From memory of the manuals a decimal literal wider than DECIMAL(65) becomes DOUBLE, so `<70 nines> + 1 == <70 nines>` (SEL FALSE) would be TRUE; no server available. `caveats` is empty (`php6/t21.php`).
- Fix sketch: add a `wide-literal` caveat or refuse over 65 digits for mysql-family.
- Conformance gap: none.

### PHP-C53 [low] [confirmed] NUL characters reach the server (RECORD-key aliases, inline TEXT literals); small binding-validation gaps
Found by: slice 6 C15, slice 7 C8.
- Where: `php/src/Sql/Emit.php:262-267` (`ident`), `:140-153` (`textLiteral`); `Binding::checkName` (`Binding.php:314`) guards binding names only; `Binding::column(..., $type)` `checkType`; `Translator::$inWhere` (set `:3914, :3922`, never read).
- What: `Binding` rejects NUL in names because "no dialect can quote" it, but an alias derived from a RECORD key and inline TEXT literals containing NUL are emitted raw; libpq/sqlite3 truncate the SQL there, giving a syntax error (fails closed as far as found, not an injection; params mode binds it). Also `Binding::column(..., $type)` accepts every `Fragment::KINDS` value including LIST and STATEMENT, which then flow through kind checks as column kinds.
- Repro: `ORDERS .> MAP(RECORD("a\u{0}b", _["id"]))` translates to `SELECT "o"."id" AS "a<NUL>b" ...` on all dialects.
- Fix sketch: `refuse('E_SQL_UNSUPPORTED')` for NUL in `ident()` and in inline `textLiteral`; restrict column types to NUM, TEXT, BOOL, BIN, UNKNOWN; remove `$inWhere`.
- Conformance gap: none.

### PHP-C54 [low] [unconfirmed on server] RECORD-key aliases are not checked against server identifier rules
Found by: slice 7 C9.
- Where: `php/src/Sql/Emit.php:262` (`ident`) fed by projection aliases.
- What: the case-collision guard exists, but an empty key becomes `AS ""` (PostgreSQL rejects, `Binding.php:283` notes it; MariaDB expected to), and two keys sharing a 63-byte prefix collide silently on PostgreSQL after truncation (one result key where SEL has two). Rendering only: `ORDERS .> MAP(RECORD("", _["id"]))` -> `SELECT "o"."id" AS "" ...`; two 71-char keys -> two 71-char aliases (postgresql).
- Fix sketch: refuse (-> local plan) empty aliases and, for postgresql, aliases longer than 63 bytes or colliding after truncation.
- Conformance gap: none.

### PHP-C55 [low] [confirmed] `sourceTables` over-reports (binders and assignment targets named like a relation)
Found by: slice 7 C10.
- Where: `php/src/Sql/Hybrid.php:314-338`.
- What: any `var` node whose name is a relation binding counts, including an aggregate's binder name, its uses, and an assignment target; documented as the physical sources the plan reads (grants/connection choice).
- Repro: `MAP(LIST(1,2), ORDERS, ORDERS + 1)` -> `["orders"]`; `ORDERS = LIST(1); COUNT(ORDERS)` -> `["orders"]`.
- Fix sketch: skip `target` of assignments and respect binder scope like `Normalise::substitute`.
- Conformance gap: none.

### PHP-C56 [low] [unconfirmed] PostgreSQL text literals assume `standard_conforming_strings=on`
Found by: slice 7 C14.
- Where: MapData postgresql `textEscape` (`'` only); `Emit::textLiteral`.
- What: with legacy `standard_conforming_strings=off`, `\'` inside inline SQL escapes the quote; input `\' OR 1=1 --` would terminate the literal. Default since PG 9.1 and the docs steer to params mode, but the assumption is not in `sql/MAP.md` or dialect notes.
- Fix sketch: document it, or emit `E'...'` with backslash doubling for postgresql.
- Conformance gap: none (no server).

### PHP-C57 [low] [confirmed] Small API/validation inconsistencies in the SQL layer
Found by: slice 7 C12, C15.
- `Binding::column('a','t',['NUM'])` interpolates an array into the message ("Array to string conversion" warning; an ErrorException under a strict handler that `tryTranslate*` does not catch) - `Binding.php:329-332`; use `get_debug_type`.
- `Hybrid::execute` returns whatever the runner returned for a pure_sql plan (native array stays an array) but a `Value` for hybrid and pure_memory (`Hybrid.php:944`). `Fragment::asValue('bogus')` is accepted when the fragment has no slots (`Fragment.php:228-238`). `new Bindings(['x'=>..., 'X'=>...])` and RELATION fields `a`/`A` silently collapse (last wins). `checkAliases` compares aliases case-sensitively although SQLite/MySQL identifiers are case-insensitive (unconfirmed).

### PHP-C58 [low] [confirmed on SQLite] An IN list or unrolled aggregate of about 1000 or more elements translates but fails at run time
Found by: slice 6 C9.
- Where: `php/src/Sql/Translator.php:635-663` (`inOperator`), `:674-682` (`foldPairwise`), documented choice in `sql-translation.md` 7.1 ("pairwise-left").
- What: N elements become N-1 nested `(a OR b)` pairs, an expression tree N deep; SQLite's default `SQLITE_MAX_EXPR_DEPTH` is 1000. A big allow-list bound as a `value` list yields SQL the server rejects (loud failure; permitted by the warrant, but this is the case the layer exists for).
- Repro (`php6/t15.php`): `T IN ("k1", ..., "kN")` on sqlite via pdo_sqlite: N=900 runs; N=1100 raises `Expression tree is too large (maximum depth 1000)`. Not run on MariaDB/PostgreSQL.
- Fix sketch: beyond a per-dialect element count fold as a balanced tree (through the operator template), or refuse so the planner keeps it in memory. Byte-identity with other hosts affected only above the threshold. (Interacts with PHP-P8.)
- Conformance gap: none.

---

# Performance findings

"Plan status" compares with `docs/interim/2026-09-29/php-optimization-plan.md` (all 48 items marked done there). Nothing below except the noted partials is in the plan.

## High impact

### PHP-P1 [high] [measured] Every text builtin re-splits its argument into a PHP array of code points, even for O(1)/O(k) answers
Found by: slice 4 P2 (also slice 1 P4 for `Utf8::chars`). Plan status: new. Related correctness: PHP-C23.
- Where: `Text.php:51` (LEN), 55 (LEFT), 60 (RIGHT), 67 (SUBSTR), 177 (CODE), 188 (TRIM/LTRIM/RTRIM), 213 (UPPER/LOWER), 153 (BACKWARDS), 232-249 (PADL/PADR); `Utf8::chars()` (`Utf8.php:76-88`).
- Evidence (measured, 1M-code-point subject, per call): LEN 227 ms (ASCII) / 383 ms (UTF-8); CODE 226 / 373; LEFT(x,3) 220 / 272; RIGHT(x,3) 283 / 250; SUBSTR(x,5,3) 353 / 241; TRIM 299 / 327; UPPER 565 / 438; BACKWARDS 296 / 327; PADL(x,2000000,'.') 1067 / 1469 ms. Short strings fine (`LEN('hello world')` 0.02 ms). Beyond 4.19M code points fatal. Byte-level LEN is ~1 ms/MB.
- Fix sketch (all byte-identical): LEN = count non-continuation bytes with `preg_match_all('/[^\x80-\xBF]/', $s)` (no `u` flag) or ASCII fast path; LEFT/SUBSTR/RIGHT/CODE walk lead bytes for k code points from the start (end for RIGHT), O(k); UPPER/LOWER `strtr($s, 'abc...z', 'ABC...Z')` (~1 ms/MB, cannot touch multibyte); TRIM `ltrim`/`rtrim` with " \t\r\n"; PADL/PADR `str_repeat($fill, intdiv($need, $k))` + code-point slice; BACKWARDS stays O(n). For `Utf8::chars` itself (slice 1 P4): `if (!preg_match('/[\x80-\xff]/', $s)) return strlen($s) === 0 ? [] : str_split($s);` (keep the empty guard for PHP < 8.2): 2,967-byte source chars() 0.51 ms vs `str_split` 0.13 ms; do not use `preg_split('//u')` (1.29 ms).

### PHP-P2 [high] [measured] FIND / REPLACE / SPLIT use a pure-PHP O(n*m) scan over code-point arrays
Found by: slice 4 P1. Plan status: new.
- Where: `Text.php:23-40` (`indexOfCp`), used by FIND (94), REPLACE (108), SPLIT (128).
- Evidence (php -d memory_limit=2G): `FIND(N,H)`, H = 'a' x 2n, N = 'a' x n + 'b': n=10,000 6.3 s; n=20,000 22.7 s; n=40,000 (H=80k) 77 s. JS 0.39/1.45/5.4 s; Python 1 ms. A 40 KB input pins a worker for >20 s (DoS-shaped). Non-adversarial: `FIND('b', <1M 'a'>)` 277 ms, `REPLACE('a','bb',<300k 'a'>)` 344 ms, `SPLIT(<300k>,'a')` 666 ms.
- Fix sketch: valid UTF-8 substring search on bytes is exact (self-synchronising encoding), so use `strpos($hay, $needle, $byteOffset)`, converting offsets with a code-point count only when a result is needed; `$from` mapped to a byte offset by walking `$from` code points; REPLACE/SPLIT via `strpos` loops or `str_replace`/`explode` for non-empty needles. Byte-identical.

### PHP-P3 [high] [measured] SORT / SORT_BY re-classify each key on every comparison, and every non-numeric text key builds and catches an exception
Found by: slice 4 P3; slice 5 notes SORT of 200k short texts takes ~31 s CPU and drives TOP's per-row cost. Plan status: new.
- Where: `Core.php:207-239` (`compareValues`), `usort` at 313-319; `Value::looksNumeric` (`Value.php:826`).
- Evidence (measured): sorting 100,000 random values end to end: SORT of ints 4.87 s; SORT of `'k'.n` text 13.9 s; SORT_BY on a record field 5.8 s / 13.2 s (context ingestion alone 0.69 s). `looksNumeric()` on non-numeric text is 2.7 us/call vs 0.4 us with a cached decimal, because `asDecimal()` calls `fail()` (SelError with trace and json_encode'd message), catches it and caches nothing. Prototype classifying each key once (null flag, decimal or bytes) and sorting the decorated array with the same rules: 1.16 s for ints (vs 3.68 s same data) and 1.35 s for text (vs 8.56 s), i.e. 3.2x and 6.4x.
- Fix sketch: in `doSort` decorate each element once with `[isNull, decimalOrNull, bytesOrNull, kind, idx]` and compare those; make `Value::looksNumeric` not throw (parse without `fail()`) and memoise a negative result. Revisit with PHP-C22 first (a total order simplifies the comparator).

### PHP-P4 [high without ext-gmp, none with it] [measured] Non-GMP long division is a per-digit repeated-subtraction loop
Found by: slice 2 P1. Plan status: new (plan 6.4 only tested correctness of the fallback).
- Where: `Dec.php:303-314` (`divModAbs` schoolbook; `:286-301` covers only divisors <= 9 digits).
- Evidence (no gmp, `Dec::div`): 20 digits / 10 digits 42.6 us (GMP 3.0); 60/30 328 us; 200/100 2.2 ms; 1000/500 45 ms (GMP 25 us); 4000/2000 2.1 s; 8000/4000 8.3 s; 16000/8000 32.6 s (quadratic, x4 per doubling). `tools/benchmark-php-runtime.php` `decimal.large.div` = 171 us without gmp vs 6 us with. `/` first multiplies the dividend by 10^10, so ordinary 10-18-digit divisors hit it. A 16 KB rule input holds the process 30+ s (DoS).
- Fix sketch: Knuth algorithm D over the base-1e7 limbs `mulAbs` builds (O(la*lb/49) instead of O(la*lb*~5) with string churn); or keep the digit loop but estimate the digit from the top 15 digits with int math and drop the `cmpAbs` loop. Integer-only.

### PHP-P5 [high without ext-gmp, none with it] [measured] Non-GMP multiply (and so POWER) is quadratic, no Karatsuba
Found by: slice 2 P2. Plan status: new. Fixes PHP-C17 too.
- Where: `Dec.php:197-240`.
- Evidence (no gmp): 10k digits squared 142 ms, 30k 0.99 s, 100k 12.9 s (gmp 4/7/64 ms). `POWER(99, 99999)` 24 s; `POWER(1.0000001, 100000)` (legal: 700,002 digits) 168 s without gmp vs 0.80 s with (JS 1.3 s, Python 1.7 s). `POWER(9999999999, 99999)` (999,990 digits) 1.4 s with gmp.
- Fix sketch: Karatsuba above ~40 limbs on the existing limb arrays, and let `power` square through it. If ext-gmp is effectively required for the 1M-digit caps, say so in `docs/usage` (PHP section): the caps advertise sizes the fallback cannot serve.

### PHP-P6 [high] [measured; cross-host design] Equi-join key extraction refuses any key expression mentioning a variable other than the binders, dropping LINK to O(n*m)
Found by: slice 5 P1. Plan status: new. JS uses the same allow-list design.
- Where: `Structure.php:83-99` (`exprDependsOnlyOn`, `default`/`'var'` case), `111-127` (`tryExtractEquiKeys`).
- What: `A["id"] + K == B["id"]`, where K is an ordinary program variable or context input (constant during the LINK), is not treated as an equi-key; the nested loop evaluates the full predicate per pair.
- Evidence: 300 rows each: 4.1 ms with `A["id"]==B["id"]`, 1416 ms with `A["id"]+K==B["id"]`; 600 rows each 5.5 ms vs 5395 ms CPU (~1000x, quadratic). Same result.
- Fix sketch: allow variables not bound by this LINK (and not assigned in the predicate) as constants in `exprDependsOnlyOn`, provided the key expression has no `assign` node and its sub-calls are pure (`pureSource()` exists). Evaluation order and error positions unchanged (right keys first, then `checkJoinPair`).

### PHP-P7 [high for adversarial input] [measured] `array_unshift` in `resolveTarget`
See PHP-C14 (same finding; 40000-index chain 9.6 s -> 1.5 s after the fix). Found by slice 3 P3/C6. Plan status: new.

### PHP-P8 [high] [measured] The left fold that unrolls IN lists and static aggregates is quadratic
Found by: slice 6 P1. Plan status: new.
- Where: `Sql/Translator.php:674-682` (`foldPairwise`); each step through `apply` -> `Emit::fill` (`Emit.php:331-339`, `splice`).
- Evidence (`php6/p1.php`, mariadb, `T IN ("k1", ..., "kN")`): N=500 0.13 s; 1000 0.47 s; 2000 1.5 s; 4000 4.7 s; 8000 20.3 s (each doubling ~3.5-4.3x). Rendering is 0.03 s; `Sel::compile` of the 4000-element list 0.07-0.1 s. A patched `splice` using `array_push(...array_slice())` still takes 2.2 s at N=4000, so the copying is the bulk. Serves ALL/ANY/SUM/JOIN over `value` lists and columns bindings too.
- Fix sketch: build the left-nested chain in one pass: split the template around `{0}`/`{1}` once (prefix P, middle M, suffix S), emit `P*(n-1) e1 (M e_i S)...` so the bytes are identical to the pairwise-left fold; or keep an append-only builder for the accumulator. Output must stay pairwise-left (`sql-translation.md` 7.1). See PHP-C58 for the depth-1000 SQLite limit that motivates a balanced fold above a threshold.

## Medium impact

### PHP-P9 [medium] [measured] Tokenizer is a per-character interpreted loop (~70% of front-end time)
Found by: slice 1 P5, P2, P3, P6. Plan status: new.
- Where: `Lexer.php:112-177` and helpers; `179-198` (`matchOperator`); `Utf8.php:21-66` (`validate`); `Lexer.php:50-54, 316, 355, 377`; `Parser.php:135-141, 386`.
- Evidence (measured): full compile of a 3 KB program ~4.2 ms tokenize vs ~1.4 ms parse; ~1.2 us per source byte. A single anchored `preg_match_all` token alternation over the same source takes 0.93 ms for all 986 matches, ~4x cheaper than the whole lexer. Negative result: swapping isDigit/isAlpha string comparisons for a char-class table did NOT speed up the lexer end to end.
  - `matchOperator` scans all 31 operators for every operator token: a first-byte dispatch table (`self::$byFirst[$chars[$i]]`) gave `examples/lib/tickets-generate.sel` (3 KB) 4.3 -> 2.8 ms, `order-validation.sel` (1.3 KB) 1.5 -> 1.15 ms (~25-35% of lexing; token streams byte-identical by serialize+md5; noisy box, direction stable over 6 runs).
  - `Utf8::validate` is a PHP byte loop; `preg_match('//u', $s) === 1` is exactly "valid UTF-8" (837,420 crafted + random strings, zero mismatches); `Value::checkText` already uses it. 2,967-byte source: 0.144 ms vs 0.001 ms; 85 KB: 6.6 ms vs ~0.02 ms (fall through to the hand loop only on failure, see PHP-C42). pcre.jit is Off on the reviewer's box.
- Fix sketch: for the ASCII, non-string parts use `strspn`/`preg_match('/\G.../A')` on the byte string to take whole identifier, number, whitespace and comment runs (ASCII byte offset == code point offset; keep the existing path when non-ASCII bytes exist; keep ASCII-only `\d`/`\w` semantics with explicit classes); first-byte operator dispatch; `preg_match('//u')` fast path in `Utf8::validate`/Lexer ctor; `Utf8::chars` ASCII fast path (PHP-P1); lazy `posAt()` in matchBrace/skipQuoted/skipRaw (only on failure; O(d^2) allocations in the nested case, see PHP-C5); `strpos` loop for lineStarts; hoist `infixOps()/infixWords()` and give `ASSIGN_OPS` an "assign" flag in the table (parse is only ~25% of front-end time, small effect).

### PHP-P10 [medium] [measured] Tokens and AST nodes are hash arrays: ~400 B per token, ~560 B per node
Found by: slice 1 P1. Plan status: new. Correctness side: PHP-C23(c).
- Where: `Lexer.php:107, 144, 154, 170` (`['type'=>..,'value'=>..] + $pos`), Parser node arrays with `'pos' => $t`.
- Evidence (memory_get_usage): `1` + `+1`*100000 (200 KB, 200,002 tokens): lexing 79.4 MB = 397 B/token (and per source byte in that dense case), parsing ~56 MB for 50K operators (~560 B/node); at 100K operators parse dies at 128M. Alternatives measured: 5-key assoc+merge 397 B, `final class Tok` with 5 promoted props 159 B, packed list 237 B (2.5x smaller for an object token).
- Fix sketch: token as a small final class (type, value, offset; line/col computed lazily by `fail()`/pos accessor via the lineStarts table) or at least a packed list; a refactor of `$t['line']`-style consumers (`fail()`, Parser `'pos'`). Independently a source-size/token-count guard turning "fatal" into a SelError. Byte-identical results.

### PHP-P11 [medium] [measured] Literals and internal results are re-validated: `Value::text` UTF-8 check on every literal evaluation
Found by: slice 2 P8, slice 3 P1, slice 5 P4 (`Value::text` ~1.1 us in TOP's `_K`). Plan status: new.
- Where: `Value.php:236-240, 253-258`; `Evaluator.php:148-156` (`case 'num'`/`'text'`); `concat` (valid + valid is valid); `Value::set` (`checkText($key)` on every write).
- Evidence: `evalNode` on the literal `7` 795 ns and on `"abc"` 772 ns; with a scratch `Value::textTrusted` 497 and 419 ns (~40-45%). `Value::text('active')` 679 ns, of which `preg_match('//u')` is 279-303 ns (`mb_check_encoding` is ~3x cheaper; pcre.jit=Off); `set()` on an existing key 444 ns. In `MAP(N, ...)` over 200k elements each literal is re-created and re-validated per element.
- Fix sketch: internal `Value::textTrusted(string)` / private-constructor factory for parser literals, `Dec` output (digits only), ASCII-only builtin results and concat of two TEXT values; in `set` skip `checkText` when the key already exists in the shape/map. Validation stays for host input and byte-cutting builtins.

### PHP-P12 [medium] [measured] Internal decimal results are re-validated by `Value::num(array)` -> `Dec::checked`
Found by: slice 2 P6, slice 3 P2. Plan status: new.
- Where: `Value.php:283-289`, `Dec.php:445-459`; callers `Evaluator.php:141` (every math-plan result), `:271`, `:314-318`, `:442-448`, `Number.php` (ABS..MAX), `Core.php:547` (SUM).
- Evidence: `Dec::checked(canonical)` 1080 ns (slice 2) / 591 ns (slice 3, tiny result); `Value::num(Dec::add(..))` 4106 ns vs `Dec::add` alone 1931 ns (~a quarter of each numeric result); `Value::num(array)` 869 ns vs `Dec::mul` 975 ns; scratch `Value::numTrusted` took `x * 2 + 1` from 4196 to 3707 ns (12%, noisy). `strspn` adds an O(n) pass for 1M-digit results.
- Fix sketch: package-private `Value::fromDec(array)` / `numTrusted` (`new self(TEXT, null)` plus `decVal = $d`) for Evaluator/Number/Core; keep `num()` validating for host input (HOST-13/14). Note the interplay with PHP-C40 (forged native cache).

### PHP-P13 [medium] [measured] `Dec::div` has no native fast path for small operands
Found by: slice 2 P4. Plan status: new.
- Where: `Dec.php:720-745`.
- Evidence: prototype (`php2/DecX.php`, 20-line branch): 3427 ns -> 1488 ns per `123.45 / 67.89` (2.3x); output identical on 200,000 random pairs (19-digit mantissas, scales 0-12, both signs, exact and inexact quotients, ties at the 11th digit). Guards: `x <= intdiv(PHP_INT_MAX, p1)` and `y <= intdiv(PHP_INT_MAX>>1, p2)` (keeps `2r >= D` from overflowing); exclude PHP_INT_MIN mantissas; result via `fromIntFast`. `Dec::mod` has the same shape, measured only 23% faster (optional).

### PHP-P14 [medium] [measured] `cmpAbs` compares digit strings with `<` (PHP numeric-string comparison)
Found by: slice 2 P3. Plan status: new (plan 5.2 adds an int fast path in `Dec::cmp` but not this slow path).
- Where: `Dec.php:63` (`$a === $b ? 0 : ($a < $b ? -1 : 1)`).
- What: two equal-length all-digit strings are numeric strings, so `<` runs the smart numeric compare. Correct today (19-21-digit pairs differing only in the last digit checked, with and without int overflow) but depends on an engine special case and costs 10-20x `strcmp`. Sits in `cmp()`'s slow path, mixed-sign `add`, `divModAbs` (up to 9x per digit, see PHP-P4) and `round`.
- Evidence: 24-digit strings `<` 777 ns vs `strcmp` 79 ns; 301-digit strings 3.5 us vs 0.18 us (1M iterations, best of 7).
- Fix sketch: `$c = strcmp($a, $b); return $c < 0 ? -1 : ($c > 0 ? 1 : 0);` (equal lengths already established; result identical).

### PHP-P15 [medium] [measured] `Dec::parse` can take a strspn-based short path (2x)
Found by: slice 2 P5. Plan status: new.
- Where: `Dec.php:478-502`.
- Evidence: `parse("123.45")` 1124 ns today (the `preg_match` is 270 ns of it). A path for strlen <= 18 (`strspn` validation + `ltrim` + building the six-key array directly with the native mantissa, no `guard`/`make`/`parseMantissa`) measured 615 ns and returned identical arrays (`!==`, including key order) for 3000 random + 20 edge strings incl. "-0", "007", "5.", ".5", "5\n", " 1". Hoisting `(string)PHP_INT_MAX` out of `parseMantissa` gave 2%, not worth it. 18 digits can never trip either digit cap so `guard` is skipped. Reference: `php2/parsefast.php`.

### PHP-P16 [medium] [measured] `Value::copy()` of a keyed list re-validates every key and every element
Found by: slice 2 P7. Plan status: new (plan 1.x removed copies but not this).
- Where: `Value.php:881-887` (`copyAt` list branch -> `self::list($values, $this->listKeys)`), `:308-339`.
- Evidence (`php2/copycost.php`): copy of n=100 plain list 29.7 us vs keyed 62.1 us; n=1000 278 us vs 674 us (2.4x); n=3 1.4 vs 2.7 us. `list()` runs an `instanceof` pass over all values and, with keys, `checkText` (`preg_match('//u')`) and a `$seen` duplicate table per key; `copyAt` calls it on every assignment/`,`/aggregate collect of a keyed list (FILTER results keep keys).
- Fix sketch: build the copy through a private constructor that assigns `storage`/`listKeys` directly (as `fromShape` does).

### PHP-P17 [medium for memory] [measured] Cached `decVal` costs ~410 B per numeric Value (plan item 4.4 not realised)
Found by: slice 2 P9. Plan status: plan item 4.4 ("compact decimal representation") is marked done, but the representation is still the six-key array.
- Where: `Dec.php:405-411` (neg, digits, scale, native, nativeDigits, nativeNeg), `Value.php:814-822`.
- Evidence: 50,000 rows `['AMOUNT'=>'12.34']`: 706 B/row after `fromNative`; after one `SUM(ROWS,r,r["AMOUNT"])` +408 B/row retained (+58%). Repo's own benchmark: 434 B per array vs 167 B for a 4-property object; PHP hash tables have an 8-slot minimum so dropping keys does not help.
- Fix sketch: final class `DecVal {bool $neg; string $digits; int $scale; ?int $native}` (readonly), or cache only `native`+`scale` and re-derive digits lazily; `nativeDigits/nativeNeg` become unnecessary (PHP-C40 disappears with them).

### PHP-P18 [medium] [reasoned / measured constants] RREPLACE memory and constant factors
Found by: slice 4 P4. Correctness side: PHP-C20, PHP-C23(b).
- Where: `Regex.php:442-463`: nested array per match then walked again; `substr` + `.=` per match. At 100k matches 230-566 ms per MB-size subject.
- Fix sketch: streaming loop as in PHP-C20, `$out` as an array of pieces joined once.

### PHP-P19 [medium] [measured] ENCODE_BASE64 / DECODE_BASE64 / CRC32 are byte-at-a-time PHP loops
Found by: slice 4 P5. Plan status: new. (The loops are deliberate: "visibly the same algorithm".)
- Where: `Binary.php:50-63, 66-107, 112-121`.
- Evidence (1 MB): ENCODE_BASE64 ~218 ms vs native `base64_encode` 0.31 ms; DECODE_BASE64 (1.3 MB) 400 ms vs `base64_decode` 1.09 ms; CRC32 83 ms vs `crc32()` 0.11 ms. `array_flip(str_split(self::B64))` is rebuilt on every decode call.
- Fix sketch: keep strict validation as one anchored byte regex (`^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$`, no `u`), then `base64_decode`; PHP decodes non-canonical trailing bits like the hand loop (`QR==` -> `A` in both). `base64_encode` and `crc32()` match the required variants (CRC-32/ISO-HDLC, standard padded alphabet). Or, if hand-written versions are preferred on principle, hoist the flip table to a static and skip per-character `str_split`.

### PHP-P20 [medium] [measured] LINK compiles a new plan with `eval()` for every LINK invocation and shape pair (~60 us each), never cached across calls
Found by: slice 5 P3. Plan status: plan item 3.8 ("memoize compiled join projectors") is marked done, but the reviewer found the `$plans` array lives only for one LINK call.
- Where: `Structure.php:617-715` (`compileSpecializedJoinProjector`), called from `makeJoinProjector` (`:717-803`).
- Evidence: an `eval` of a plan-sized string measures 60 us CPU. `MAP(K, COUNT(LINK(R,S,A,B,A["id"]==B["id"])))` with 20x20 rows runs ~250 us per iteration vs 72 us for the equivalent FILTER body; at least one plan (matched) and often two (LINK_LEFT with unmatched rows) are compiled each time. `eval` also defeats opcache and needs eval to be permitted.
- Fix sketch: cache by the generated code string (or `(ops, slots, lnested, rkept)`) a factory closure `static function($shape,$rshape){ return [...]; }` in a process-static array (capped like `RecordShape::$cache`), bound to shapes by call. Results identical. (eval codegen interpolates only integer slot numbers: no injection surface.)

### PHP-P21 [medium] [measured] Non-equi (nested-loop) LINK re-aliases every right row for every left row
Found by: slice 5 P2. Plan status: new.
- Where: `Structure.php:1562-1598`.
- Evidence: `$aliasRight($rightItem)` (new Value + storage copy) runs inside the inner `forEachElement`, so m right rows are aliased n times; per pair also `strtolower($b1)`/`strtolower($b2)`, six `setFrameValue` calls, and a mirror into a second local `$frame` nothing reads. 500x500 pairs with predicate FALSE: 3.8 us CPU per pair with named binders vs 2.5 us with `_1,_2` (aliasing ~1.3 us). Realistic predicates (`A["id"]+B["id"]==100`) ~17 us per pair dominated by the evaluator, so 10-20% of a slow path.
- Fix sketch: pre-alias the right side once (safe when the predicate has no assignment; `pureSource($predicate)` decides), hoist `strtolower` and second-frame writes out of the loops, drop the dead `$frame[...]` mirror.

### PHP-P22 [medium] [measured] `Hybrid::execute` deep-copies the whole caller context on every call
Found by: slice 7 P4. Plan status: new.
- Where: `Sql/Hybrid.php:945`.
- Evidence: `Value::copy()` of a context holding one 20k-row table = 111 ms (~5.5 us/row) on top of the query, per execution; the continuation usually reads only `_INPUT` and a few small relations.
- Fix sketch: shallow root aliasing the caller's top-level children and owning only `_INPUT` (safe as long as continuation assignments go to the fresh root; copy-on-write per top-level key if aliased mutation of nested values must be prevented).

### PHP-P23 [medium] [measured] `Emit::fill` copies templates one character at a time through a closure (6x)
Found by: slice 7 P1, slice 6 P2. Plan status: new.
- Where: `Sql/Emit.php:316-437` (the `$push($tpl[$i])` branch at 364-368); `Translator.php:2900-2907` (`fillNamed`).
- Evidence: 60-char template `fill()` = 36.5 us per call; with a `strcspn($tpl,'{}',$i)` run-copy 5.9 us (6x). `translateStatement` of a ~300-node FILTER 19.7-20.7 ms -> 15.0-18.5 ms (best of 7, twice); emitted SQL byte-identical (md5 equal on sqlite/mariadb/postgresql). Slice 6 (instrumented): `Emit::fill` ~24-31% of translate time.
- Fix sketch: when `$tpl[$i]` is not `{`/`}`, `$run = strcspn($tpl,'{}',$i)` and `$push(substr($tpl,$i,$run))`; better, a parsed-template cache (segments plus slot indexes) per dialect.

## Low

### PHP-P24 [low-medium] [measured] Template lookup rebuilds the dialect chain per node; `textLiteral` re-sorts its escape keys per literal
Found by: slice 6 P2, slice 7 P2. Plan status: new.
- Where: `Sql/Map.php:267-277, 290-304, 380-404` (`chain`/`entry`/`lexical`); `Emit.php:140-153`.
- Evidence: a 10-node expression does ~21 `Map::entry` and 26 `Map::lexical` calls, each rebuilding the chain (`in_array`, `exists`, `record`): entry+lexical ~17% of translate time, `Constants::isConstant` ~3.5% (0.7-0.9 ms expression). Caching `Map::chain` per dialect plus `strcspn`-chunked pushing took a translation from 0.63-0.79 ms to 0.55-0.61 ms (10-25%, noisy). Micro: `Map::chain` 2.2 us, `entry` 2.7 us, `lexical` 3.5 us, `Emit::textLiteral` 6.7 us (usort + array_combine + strtr every time; `strtr` with an array already applies longest-first, so the usort is redundant).
- Fix sketch: memoise `chain($dialect)` and the resolved `textEscape` map per dialect, invalidated on `defineDialect`/`define`/`reset` (the same points that clear `$guardChecked`); drop the usort.

### PHP-P25 [low-medium] [measured] `structuralHash` allocates a HashContext even for scalar leaves
Found by: slice 2 P10, slice 5 P6. Plan status: new.
- Where: `Value.php:907-912`; users DISTINCT (`Core.php:168`), DEDUPE (`Structure.php:31`), BUCKET (`Structure.php:1766`).
- Evidence: 763 ns per text scalar vs 169 ns for a plain `kind.':'.scalar` key (slice 2); 200k text items: DEDUPE 613 ms and bare BUCKET 843 ms, hashing ~1 us of ~3-4 us per element (slice 5); records ~7 us per 4-field record. `$buckets[$hash][] = $item` creates a one-element array per distinct hash (376+ B each).
- Fix sketch: for a TEXT/BIN/BOOL value with no children return `kind:len:scalar` directly, keep xxh3 only for containers (buckets confirm with `eql()`, so any injective key is safe); store a single value in the bucket, upgrading to a list only on collision.

### PHP-P26 [low-medium] [measured] Sort/heap costs in TOP: `_K` bound for every row; heap used when limit >= rows
Found by: slice 5 P4, P5. Plan status: partial - plan item 5.1 made `_K` conditional in BUCKET only.
- Where: `Structure.php:1680-1683` (`Value::text($key)`, two `setFrameValue`, mirror write) and `1644-1707`.
- Evidence: `TOP(R,_["v"],10)` ~6 us per row vs 1.4 us for `MAP(R,_["v"])` on 200k rows, ~1.1 us of it `_K` (rest is `Core::compareValues`). 50k rows `TOP(R,_["v"],25000)` 1.68 s vs `SORT_BY(R,_["v"])` 1.44 s (loaded box, indicative).
- Fix sketch: `$needsK = Core::containsVar($body, '_K')` as in `doBucket`; when `$limit >= size` skip the heap and go straight to one `usort` on decorated keys (ties still break by `idx`, result identical).

### PHP-P27 [low-medium] [measured] `Dec` SUM accumulates through full six-key descriptors
Found by: slice 2 P11. Plan status: new.
- Where: `Builtins/Core.php:541-547` (driven by `Dec::add`).
- Evidence: 100k `Dec::add` chain 109 ms (1.09 us/add) vs a native-int accumulator 11.7 ms when scales are equal and mantissas native; end to end SUM over 50k rows 2.5 us/row warm, ~40% of it. Sketch: `Dec::sumInto(&$acc, $d)` staying in an int until scale changes or `is_int` fails, then materialise once.

### PHP-P28 [low-medium] [measured] Per-node overhead in `evalNode`: try/finally plus a second dispatch call
Found by: slice 3 P4. Plan status: new.
- Where: `Evaluator.php:25-39`.
- Evidence: `evalNode` on `TRUE` 275 ns vs 70 ns for `Value::bool` alone (~200 ns framework per node). For `COUNT(MAP(N,_))` over 200k elements the floor is ~1.2 us/element (loaded 16-thread box).
- Fix sketch (expect 5-10%, unverified): only `??` and builtins that catch a SelError need depth restored - save `$ctx->depth` at those catch sites (`Structure.php` 186/221/230/1345/1431, `Value.php:838`, `Evaluator.php:296`) and drop the `finally`; move the `mathPlan` check to the parser/optimiser with a distinct node type.

### PHP-P29 [low] [measured] The optimiser costs more than it saves for one-shot, non-pipeline rules; constant folding stops short of text `&`
Found by: slice 3 P6, P5. Plan status: new.
- Where: `Sel.php:80-112` (`run` builds the physical tree on first use); `Optimizer.php:213-294` (`foldNode`).
- Evidence: small rule `X * 2 + 1 > 10 AND S $== "a"`: parse 95 us, `Optimizer::optimize` 34 us, eval physical 14.4 us vs plain 21.3 us (saves 7 us/run, break-even after 5 runs); medium rule optimize 81 us vs 7 us saved (break-even after 11 runs). `Sel::evaluate()` is one-shot. `"abc" & "def"` (two TEXT literals) is 3.0-3.5 us per element in a MAP; folding to a `text` node is exactly equivalent and cannot fail.
- Fix sketch: skip `optimize` in `Sel::evaluate` when the tree has no pipeline call and no mathPlan candidate (byte-identical only once PHP-C13 is fixed); add `'&'` with two `text` literal children to `foldNode`.

### PHP-P30 [low] [measured / reasoned] Assorted small items
Plan status: new.
- Regex compile cache is unbounded and the `i` flag re-scans the pattern per call (`Regex.php:48-49, 327-351`): 100,000 distinct dynamic patterns retain ~14 MB in `self::$cache` and take 3.07 s to compile (30 us each); PCRE's own cache holds 4,096 patterns, so a second pass over 100k still costs 1.25 s. Bound the map and do the ASCII check after the cache lookup keyed on flags+pattern (slice 4 P6, measured).
- `Text.php` trim (187) builds a `$space` array per call and uses `in_array` per character; `Regex::escapeDelimiter` re-splits the whole pattern into characters even without `/` (`strpos` first) (slice 4 P7, reasoned).
- `Structure::canonicalDecimalKey` / `canonicalJoinKey` allocate per row (`:149-233`); measured join speed is fine (50k x 50k equi-join ~370 ms) (slice 5 P7, reasoned, low value).
- LINK pre-renders the whole plan just to surface earlier refusals first (`Translator.php:3557-3560`): linear in pipeline size per LINK (slice 6 P3, reasoned).
- `planHybrid` prefix search is O(steps^2) when the first unsupported step is early (`Hybrid.php:177-201`, each failed candidate re-runs `Normalise::run` + a full `translateStatement`): alternating TAKE/DROP with an unsupported FILTER at step 3: n=10 4 ms, n=40 21 ms, n=80 78 ms, n=120 125 ms (one translate of the supported steps 0.7/2.4/6.9/7.1 ms). Binary-search the split or use the refusal position to jump below it; repeated tree walks in `plan()` (`tables` closure re-runs Normalise, `referencedAssignments`, `fieldReferences`) are worth fixing together (slice 7 P3, measured; P5, reasoned).

## Plan cross-check

| Plan item | Status per plan | Reviewer finding |
|---|---|---|
| 1.3, 1.4 remove `copy()` from LIST / `evalList` | done | breaks spec 3.4 for `,` (PHP-C2) |
| 1.6, 1.7 boolean/null/none flyweights | done | shared mutable singletons corrupt process state (PHP-C1); null/none flyweights need the same audit |
| 2.x slot cache | done | shared by plain and physical trees; sound (slice 3) |
| 3.8 memoise compiled join projectors | done | plans are per LINK call, not across calls (PHP-P20) |
| 4.4 compact decimal representation | done | still a six-key array, ~410 B/Value (PHP-P17) |
| 5.1 conditional `_K` allocation | done (BUCKET) | TOP still binds `_K` per row (PHP-P26) |
| 5.2 `Dec::cmp` int fast path | done | ignores `nativeNeg` for forged descriptors (PHP-C40) |
| 6.4 GMP vs pure-fallback matrix | done (correctness) | fallback division/multiply are quadratic and multiply can TypeError (PHP-P4, P5, C17) |

Everything else in this section (text builtins, sorting, regex, lexer, translator) is outside the plan's scope (which targeted Scenario 1: LINK/MAP/FILTER/BUCKET over decimals).

---

# Cross-host findings (fix must be spec-first across all hosts, per CLAUDE.md "The one rule")

| PHP entry | Hosts affected (per reviewers) | Spec / conformance action |
|---|---|---|
| PHP-C5 lexer super-linear on nested interpolation | JS quadratic (RangeError at d=3200), C++ 0.86 s at d=10000; Python/Lisp/Go not checked | `lim.interp-nesting-depth` (E_DEPTH, few hundred levels); consider a spec sentence that lexer work is bounded by the depth cap |
| PHP-C6 uncapped REPEAT/PAD/`,` growth | all hosts (JS RangeError, Lisp heap exhaustion, PHP fatal) | add a result-length cap to `spec/limits.json`/6.4 (`E_RANGE`), then `lim.repeat-cap`, `lim.pad-cap` |
| PHP-C3 regex resource exhaustion | PHP-specific behaviour (others answer correctly; C++ aborts on SRELL complexity, see notes) | decide a policy (new coded error or a portable-complexity rule); case after that |
| PHP-C7 SUM(g, body) slots | JS same wrong SQL, Python TypeError, Go drops slots | `.sqlt` cases with a literal in a BUCKET-member SUM body and a `--- params` check |
| PHP-C8, C9, C34, C36 hybrid planner (keys, `_INPUT`, helper twice, error suppression) | JS (verified for C8, C9, C34), Python (by grep for C8, C9) | cases in `25-hybrid-plans.sqlt`; document whether pure_sql renumbers keys (12.1) |
| PHP-C11 FILTER fusion masks first error | all five hosts print E_NOT_BOOL | `opt.filter-fusion-keeps-first-error` |
| PHP-C12 SORT+TAKE -> TOP fusion | all five hosts | `opt.sort-take-zero-still-raises`, `opt.sort-take-count-error-order` |
| PHP-C13 math plan coerces early | all five hosts agree with each other and disagree with the plain tree | spec 6.2 sentence + `ord.arith-evaluates-both-then-coerces`; change every host's plan compiler |
| PHP-C16 `??` depth not counted | JS RangeError, Python RecursionError, C++ segfault | 6.4 sentence + `lim.coalesce-depth`, `lim.coalesce-depth-just-under` |
| PHP-C18 class-escape range endpoints | all hosts accept with the same wrong meaning | `re.reject.class-escape-in-range` |
| PHP-C20 RREPLACE empty-match semantics | PHP/C++ vs JS/Python/Lisp | spec picks one; `re.replace.lazy-empty-then-nonempty` |
| PHP-C21 quantified capture groups | PHP/Python/Lisp vs JS/C++ | spec decision (refuse or ES semantics); `re.groups.quantified-group-capture` |
| PHP-C22 SORT comparator not total | all hosts disagree, three different orders | total order in spec; `rel.sort.number-shaped-vs-plain-text` |
| PHP-C25 BUCKET of a scalar | PHP, JS, Python, Lisp wrong; cpp right (Lisp also TOP) | `rel.bucket.scalar-is-one-element-list`, `rel.top.scalar-is-one-element-list` |
| PHP-C26 joined-row blow-up | JS 1.3 s, cpp 2.1 s, Python 4.2 s at 6 levels | spec decision (binder rows under three keys / size cap); time-boxed case |
| PHP-C28 UNKNOWN laundering in `unify` | JS emits the same SQL | `--- error` cases for `IF(F, U, TRUE) AND TRUE`, guarded-output case for `(U ?? 1) + 1` |
| PHP-C32 SQL size blow-up | JS (n=16 gives 524 KB) | new coded refusal + `spec/limits.json`; a 30-doubling-assignments case |
| PHP-C38 `dependencies()` order | JS same | spec 8 wording (or ordered tracking) |
| PHP-C46 bare BUCKET drops same-text groups | all five hosts identical | spec 7.3 decision |
| PHP-C47 BUCKET binder+key row | all five hosts | fix spec row/docs |
| PHP-C50 TAKE(2.0) refused by translator | JS same | `stmt.take.whole-number-with-scale` |
| PHP-P6 equi-key with outside variable | JS same allow-list design | perf only, no spec change (order and errors unchanged) |
| PHP-C42 E_UTF8 without position | C++ same (0:0) | pin position semantics in spec 2; host-API test |
| PHP-C44 E_RANGE/E_UTF8 phase | generated from `spec/limits.json` | edit `spec/limits.json` and `errors.md`, regenerate |
| PHP-C43 locale-dependent case functions | only PHP >= 8.1 <8.2 | none in spec; raise floor or use `strtr` |

Other-host notes surfaced while probing (not PHP bugs, for the other host reports): C++ `RMATCH('^(?:(a+)+c|.*)$', REPEAT('a',22)&'b')` aborts the process (SRELL `error_complexity` uncaught at match time); Lisp `RMATCH('^*','')`, `'$*'`, `'^+a'` return TRUE instead of E_REGEX_SYNTAX; Python `SELECT_COLS(LIST(RECORD('a',1,'b',2)),'b','a','b')` dumps a duplicate key `b`; Lisp raises an unhandled TYPE-ERROR for `TOP(LIST(1,2), _, 9223372036854775807)` and `TOP(..., 99999999999999999999)`.

---

# Suggested conformance cases

Names are proposals; expected values come from the reviewers unless noted. "decision" = spec must choose first.

| Name idea | Expression | Expected |
|---|---|---|
| `alias.comma-copies-a-child` | `A = RECORD("a", RECORD("b",1)); (A, A["a"]["b"] = 99)[1]["b"]` | `t"1"` |
| `alias.comma-copies-a-value` | `A = "x"; A["k"] = RECORD("b",1); (A, A["k"]["b"] = 99)[1]["k"]["b"]` | `t"1"` |
| `opt.filter-fusion-keeps-first-error` | `LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER(_)` | `!E_DIV_ZERO` |
| `opt.sort-take-zero-still-raises` | `LIST(RECORD("a",0),RECORD("a",1)) .> SORT_BY(1/_["a"]) .> TAKE(0)` | `!E_DIV_ZERO` |
| `opt.sort-take-count-error-order` | `LIST(RECORD("a",0),RECORD("a",1)) .> SORT_BY(1/_["a"]) .> TAKE("x")` | `!E_DIV_ZERO` |
| `ord.arith-evaluates-both-then-coerces` | `"x" + nosuch` | `!E_UNDEF_VAR` (decision: spec 6.2 wording) |
| `lim.coalesce-depth` (+ `-just-under`) | `1` followed by ` ?? 1` repeated past the depth cap | `!E_DEPTH` at the crossing operator |
| `lim.interp-nesting-depth` | a few hundred nested `"{...}"` levels | `!E_DEPTH` (plus a time budget) |
| `lim.repeat-cap` / `lim.pad-cap` | `LEN(REPEAT('ab', 10000000000))`, `LEN(PADL('a', 99999999999999999999, 'x'))` | `!E_RANGE` (decision: cap number) |
| `re.reject.pcre-verb` | `RMATCH('(*FAIL)','a')`; `RMATCH('a(*ACCEPT)b','ac')` | `!E_REGEX_SYNTAX` |
| `re.reject.class-escape-in-range` | `RMATCH('^[+-\d]$','5')`; `[\d-z]`; `[a-\s]` | `!E_REGEX_SYNTAX` |
| `re.reject.posix-class-not-at-start` | `RMATCH('[a[:alpha:]','[')` | decision: literal `TRUE` (other hosts) or `!E_REGEX_SYNTAX` for all |
| `re.replace.lazy-empty-then-nonempty` | `RREPLACE('b*?','-','abb')` | decision: `-a-b-b-` (ES/Python) vs `-a-----` |
| `re.groups.quantified-group-capture` | `RGROUPS('(?:(a)|b)*','ab')` | decision: `""` (ES) or `!E_REGEX_SYNTAX` |
| `re.resource-limit` | `RMATCH('^(?:a|b)*$', REPEAT('a', 100000))` | `TRUE` (after the spec picks a policy) |
| `rel.sort.number-shaped-vs-plain-text` | `SORT(LIST('9','1a','10','2b','30','3')) .> JOIN(',')` | decision: one order across hosts (currently three) |
| `rel.bucket.empty-record-key-K` | `BUCKET(RECORD("",5,"x",6), IF(_K $== "", "empty", "other"))` | `-{"empty"=-{"1"=t"5"}, "other"=-{"1"=t"6"}}` |
| `rel.bucket.scalar-is-one-element-list` | `BUCKET("abc", _)` | `-{"abc"=-{"1"=t"abc"}}` |
| `rel.top.scalar-is-one-element-list` | `TOP("abc",_,1)` | non-empty (Lisp currently empty) |
| `rel.link.deep-chain-assignment-time` | 6-deep `LINK` chain assigned to `Y` then `COUNT(Y)` | time-boxed (decision) |
| `lim.assign-chain-time` | `'A'+'[1]'*40000+' = 1'` | `!E_DEPTH` within a time budget |
| host-API: `ctor.num.canonicalises` | `Value.num("007")`, `Value.num("-0")` | `t"7"`, `t"0"` (exists in `tools/api.php`) |
| host-API (`tools/check-php-runtime.php`) | `B["k"]=1` over a `fromNative` bool, then `Value::bool(true)->size()` | `0` |
| host-API | `Value::int(1.5)`, `Value::text(5)`, `Value::bin([65,66])`, `Value::list("x")` | `E_BAD_ARG` |
| host-API | source `"a\xff" & 1` | `E_UTF8` with a position (decision) |
| `.sqlt` bucket sum literals | `ITEMS .> BUCKET(_["dept"], RECORD("t", SUM(_, _["amount"] * 2)))`; text literal in an IF body; `--- params` check | literal `2` preserved in SQL/params |
| `.sqlt` bucket sum kind | `O .> BUCKET(_["CAT"], RECORD("s", SUM(_, _["STATUS"])))` (TEXT and BOOL fields) | `--- error E_SQL_SHAPE` |
| `.sqlt` unknown laundering | `IF(F, U, TRUE) AND TRUE`; `(U ?? 1) + 1` | refusal; guarded output |
| `.sqlt` `agg.nested.*` `_K` scope | `ANY(("a","b"), o, ANY((_K, "zz"), c, c $== "2"))` | SEL answer TRUE reproduced by the translation (or refusal); also pin `ANY((X, 2), X, X > 1)` |
| `.sqlt` `agg.filter.binder-does-not-leak-into-body` | `ALL(FILTER((1,2,3), x, x > 1), y, y < X)` (X a NUM column); `ALL(FILTER((1,2,3), x, y > 0), y, y < 9)` | column `x` in output; E_UNDEF_VAR refusal |
| `.sqlt` `refuse.join-bool-element` / `-separator` | `JOIN((F,"a"), "-")`; `JOIN((T,"a"), F)` (F BOOL column) | refusal (E_SQL_SHAPE) |
| `.sqlt` size refusal | 30 doubling assignments `A1 = A0 + A0; ...` | coded refusal (decision: code name) |
| `.sqlt` oracle | `"0.1" + "0.2" == 0.3` | TRUE, and the emitted operand not a bare quoted string |
| `.sqlt` `stmt.take.whole-number-with-scale` | `O .> TAKE(2.0)` | same as `TAKE(2)` |
| `25-hybrid-plans` keys | `ORDERS .> FILTER(...) .> MAP(RECORD("k", _K, "z", <unpushable>))` | pure_memory, or execute equal |
| `25-hybrid-plans` binder | split before an unbound-name `LINK` then `_["ORDERS"]["name"]` | no E_NO_KEY; equal to memory |
| `25-hybrid-plans` helper | `ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)` | ids 3,4,5 (`LIMIT 3 OFFSET 2`) |
| `25-hybrid-plans` order | `SORT_BY(...,"DESC") .> LINK(...) .> MAP(...) .> TAKE(2)`; `SORT_BY .> BUCKET(...)` | o=10,8; groups 11,14,12,10,13 |
| `25-hybrid-plans` error equivalence | `... .> MAP(RECORD("a",_["id"],"z",REPEAT(_["name"],1+0*(1/(_["id"]-5))))) .> TAKE(2)` | E_DIV_ZERO (no silent push of TAKE) |
| `11-registration` dialect | `Map::defineDialect('q1', ['extends'=>'sqlite','lexical'=>['textQuote'=>'"']])` | LogicException at definition |
| host-API translate twice | register a dialect with a bad `numericGuard`, translate twice | both throw |

---

# Suggested fix order

## Now (small, high value, PHP-only or trivially cross-host)
1. PHP-C1: stop the shared-bool poisoning (fresh objects for host-facing bools, copy-on-write or private flyweight). Add the `check-php-runtime.php` test.
2. PHP-C2: restore `copy()` in `evalList` (both branches); add the two alias cases.
3. PHP-C10: `Value::num` scalar `null`/canonical (one line); turns `tools/check-api.sh` green for PHP.
4. PHP-C4 and PHP-C3 (at least the `preg_last_error()` check raising something, once a code is chosen): the regex seam; PHP-C24 (one-line `_K` fix) and PHP-C25 (`kind === NONE` test).
5. PHP-C14/P7: `array_unshift` -> append + reverse, or bail at MAX_DEPTH while walking.
6. PHP-C5: memoise `matchBrace`/`skipQuoted` (byte-identical; prototype exists).
7. PHP-C15: dismantle the partial tree at the remaining throw sites.

## Next (spec-first, all hosts; cheap once decided)
8. Spec decisions with cases: PHP-C11/C12 (fusion rules), PHP-C13 (operand coercion order), PHP-C16 (`??` depth), PHP-C6 (size caps), PHP-C18/C19/C20/C21 (regex portable subset), PHP-C22 (total sort order), PHP-C46/C47/C38.
9. SQL correctness: PHP-C7 (slice splice; trivial), PHP-C27 (reuse `aggBody`), PHP-C28 (`unify`), PHP-C31, PHP-C29/C30 (frames), PHP-C32 (size budget), then the hybrid planner batch PHP-C8, C9, C34-C36, C37 with cases in `25-hybrid-plans`.

## Performance, by payoff per effort
10. Byte-level text builtins (PHP-P1) and `strpos`-based FIND/REPLACE/SPLIT (PHP-P2): removes most memory fatals (PHP-C23a) and the 77 s DoS.
11. Decorated SORT with non-throwing `looksNumeric` (PHP-P3), ideally together with the PHP-C22 total order; TOP `_K` gating and heap bypass (PHP-P26).
12. Trusted constructors for internal text/decimal results (PHP-P11, P12) and `cmpAbs` -> `strcmp` (PHP-P14), `Dec::div` native path (PHP-P13), `Dec::parse` short path (PHP-P15), keyed-list copy (PHP-P16).
13. LINK: equi-key with outside variables (PHP-P6), process-wide projector cache (PHP-P20), pre-aliased right side (PHP-P21).
14. SQL: single-pass `foldPairwise` (PHP-P8, plus balanced fold above a threshold for PHP-C58), `Emit::fill` `strcspn` (PHP-P23), dialect chain/template caches (PHP-P24).
15. Front end: first-byte operator dispatch, `preg_match('//u')` validate, ASCII `chars`, then regex-run lexing and a slimmer token (PHP-P9, P10).
16. Non-GMP arithmetic: Knuth D, Karatsuba (PHP-P4, P5; also fixes PHP-C17), only if the fallback is meant to be supported at the advertised caps; otherwise document that ext-gmp is required.
17. Remaining low items: hash/HashContext (PHP-P25), SUM accumulator (PHP-P27), evalNode `finally` (PHP-P28), optimiser skip for one-shot rules (PHP-P29, after PHP-C13), base64/CRC32 native (PHP-P19), decVal object (PHP-P17), `Hybrid::execute` copy (PHP-P22).

Do last: cosmetic and hygiene items (PHP-C41, C43 (raise the PHP floor to 8.2 is the cheapest fix), C44, C48, C49, C51-C57).

---

# Checked and found sound (merged from all seven reviewers)

Front end
- Lexer is a faithful transcription of `js/src/lexer.mjs`: on ~90 hand-written edge cases and 15,000 fuzzed sources (token soup, grammar-aware generator: pipes, placeholders, RECORD, interpolation, escapes, comments, non-ASCII, CRLF, BOM, NBSP) PHP and JS agree on code, line, col and offset; no PHP host exception ever escaped (only SelError).
- Numbers (`1.`, `1..2`, `1.5.5`, `.5`, `1e5`, fullwidth/Arabic-Indic digits, `007`), escapes (`\u{...}` with `/D`, <= 6 digits, surrogate/>10FFFF = E_RANGE, `{`/`}`, unterminated forms), interpolation (`{}`/whitespace/comment-only bodies, `}` in nested strings, multi-line bodies, astral columns), line/column counting (only LF; CR/VT/FF/NEL/U+2028 are not whitespace or line breaks; BOM unexpected; columns in code points) all agree with the other hosts including positions.
- Parser: precedence table matches `grammar.md`; NOT/unary-minus binding gates, comparison non-chaining (incl. word operators), assignment-target validation, trailing `;`, `F((1,2))` grouping, placeholder substitution, arity at the name token. Depth enter/leave discipline reproduces `conformance/10-limits.selt` pins. `dismantle()/steal()` covers every node shape (only the throw sites in PHP-C15 are unprotected); `prepareRecordShape` duplicate detection agrees.
- `Utf8::validate` agrees with PCRE `//u` on 837,420 inputs; `chr/ord/chars` round-trip all 1,112,064 scalar values; `cpIndex` right for 1-4 byte characters. `SelError`/`fail()` positions, MAX_DEPTH from Limits; no mbstring/ctype/locale functions other than the `strtoupper` family (PHP-C43); no PHP 8.5 deprecations (`error_reporting=-1`).

Numeric core and values
- Every native fast path (`add`, `mul`, `fastAligned`, `addAbs` <= 18 digits, `mulAbs` <= 18 total and the 9-digit split, `divModAbs` chunk loop) is guarded by `is_int` or digit-count arithmetic; `PHP_INT_MIN` special-cased. Boundary tests (2^63-1, 2^63, -2^63, 10^18, 2^64, 3037000500^2 +-3) vs bcmath for add/sub/mul/cmp/mod/div: 0 mismatches in 2x20,000. GMP vs fallback: 3000 pairs x 9 ops identical; 6000 random SEL expressions identical in PHP(GMP), PHP(`-n`), JS; `php -n php/bin/conformance` passes 02, 10, 22, 23.
- `Dec::parse` `/D` present, digit-cap boundaries OK/E_RANGE/E_RANGE with pcre.jit on and off, no catastrophic backtracking; `div` rounding, minimal exact-quotient scale, never "-0"; `mod` sign; `ROUND/FLOOR/CEIL/TRUNC/CANON` on negatives and carry; `POWER(0,0)` and the "do not square after the last bit" fix agree with JS/Python.
- MathPlan copy-propagation keeps operand coercion; error precedence and E_RANGE/E_DEPTH positions identical to JS/Python (apart from PHP-C13, which all hosts share); Number.php MIN/MAX ties, ISNUM agree on 25 probes.
- `Value`: array-key coercion handled at every exit; copy-on-write in `set()`/`fromShape`/`list`/`values()`; depth counted in copy/eql/dump/hash/toNative/fromNative; DISTINCT/BUCKET numeric identity agrees; hash buckets confirmed by `eql` (a collision costs time only); prefix-free hash encoding. `RecordShape::intern`/`alias` plan cache safe against `spl_object_id` reuse. `fromNative` rejects floats/objects/resources/NAN.

Evaluator, optimiser, host API
- Left-to-right evaluation of all binary operators other than the math plan; AND/OR short-circuit; `??`/`???` catch exactly E_NO_KEY and E_UNDEF_VAR; frames and `joinPrefilter` restored on error paths (every `pushFrame` has a `finally`); assignment paths (5.7/5.8) incl. `E_BAD_ASSIGN` and the chain depth check; value depth via `copy()` gives E_DEPTH at the same columns as JS/C++.
- Optimiser: `exceedsDepth` keeps E_DEPTH observable; FILTER fusion adds a level only to literal/variable predicates; constant folding (24k random expressions, ~150 edge cases) shows no value difference; `IF(TRUE, ...)` hoisting re-stamps positions; `SORT_BY/TOP_BY` third-slot guard works; TAKE+TAKE, DROP+DROP overflow guard, `FILTER(TRUE)` removal, DISTINCT/DEDUPE, FILTER/SELECT_COLS/SORT_BY/MAP swaps match the plain tree in ~40k random pipelines (only PHP-C12 diffs). SlotCache safe (keyed by shape identity plus a literal key). `Program`: `physicalOf !== ast` is a pointer compare, `__destruct` dismantles, `gc_disable` restored in `finally`.
- Registry/host functions: reserved words and builtin names refused, ASCII-only name regex with `\z`, compiled program keeps its function (8.1), wrong return types raise `UnexpectedValueException`, host exceptions propagate with frames and depth unwound. Empty programs, `;`, `()`: syntax errors.

Builtins
- Regex validator otherwise mirrors the JS one (`\b \B \v \p \A \z \Z \G \K`, backrefs, lookaround, named/atomic groups, inline modifiers, possessive quantifiers, `{n,m}` bounds, unmatched `}`/`]`, leading `]`); `\d \w \s` expanded to ASCII classes; `usD` flags give code-point `.` and whole-subject `^`/`$`; `i` flag (Kelvin sign, long s, U+0131), flags `I`/duplicates/`m`/`s`/unknown behave as in other hosts; RFIND code-point positions; RGROUPS trailing unmatched groups `""`; RREPLACE `$0-$9`, `$$`; delimiter handling.
- Text builtins' error codes and positions agree across probes; huge integer arguments saturate consistently; UPPER/LOWER ASCII-only; TRIM strips exactly space/tab/CR/LF; non-lazy builtins evaluate arguments left to right.
- Binary (FROM_HEX, DECODE_BASE64 padding/trailing bits, LTB, BTL, CRC32 of empty), NullOps (IS_NULL, COALESCE, GET, PATH, IS_BLANK, IS_PRESENT on NULL, empty list/record, U+00A0, U+2003, VT, FF, BIN, BOOL), and Core (COND/IF laziness, RECORD duplicate keys and numeric-looking keys, COUNT/INDEXES/HAS on scalars/NULL, TAKE/DROP on scalars/records/keyed lists, DISTINCT(NULL), JOIN, SELECT_COLS, FILTER key preservation) agree with other hosts. No `mb_*`, `setlocale`, `ctype_*`, `intval`, `is_numeric` or native float in the slice-4 files.

Structure / in-memory relational pipeline
- Numeric-string array-key coercion in `$buckets[$key]` (boundary values 9223372036854775807/8, 18-20 digit integers, "-0", "-05", ".1" vs "0.10", "00012" vs 12); non-numeric `$==` keys carry a `b` prefix; `keys()/entries()/forEachElement` cast keys back to string; numeric/case-differing/repeated field names join and project identically; stable ordering for bucket lists, LINK output, TOP ties, DEDUPE; DEDUPE/BUCKET depth limits at 198-200 levels; joined-row plans keyed by `spl_object_id` are safe; assignment inside LINK predicates deep-copies (no COW-vs-handle bug); `eval()` codegen interpolates only integer slot numbers; error order/positions for hash-join vs nested-loop match SEM-06 cases. ~400 random LINK/LINK_LEFT/BUCKET/DEDUPE/TOP programs on heterogeneous records and ~150 numeric-boundary join keys (php vs node vs cpp): zero result or error-code divergences.

SQL layer
- Identifier quoting (quote doubling per dialect; injection strings in aliases and column names come out quoted; NUL and empty names refused in `Binding::checkName`); text literals (single quote doubled everywhere, backslash doubled for mysql-family, params mode binds TEXT, NUM/BOOL/BIN inlined only after `Dec::format`/map tokens, invalid UTF-8 rejected so multibyte escape tricks cannot be built); NUM literals canonical and negative parenthesised; `Emit::fill` slot grammar, `{{`/`}}`, lexical cycle detection, absolute slot numbers; `Fragment::bindings()` follows output order so reordered templates bind correctly.
- Whole-expression refusal (nothing becomes characters until `asValue()`; all failures are `SqlError`; SelError from constant validation, TAKE counts and regex rewriting is converted; no partial result escapes apart from the memory fatal in PHP-C32); depth accounting (200 terms translate, 201 refuse); TAKE/DROP composition; regex rewriting; IN/HAS/COUNT sources and index-key canonicalisation; shadowing guard; `tryLatestMember` (8 grouped-latest programs equal memory); dialect inheritance matches `sql/dialects/*.json` for the lexical keys spot-checked.
- Planner robustness: ~8000 (program x dialect) plans over random pipelines produced no uncaught non-SqlError and no SqlError leaking from `tryTranslate*`; 1200 random pipelines through SQLite showed zero value mismatches between hybrid and memory once row keys are stripped and sort orders are total (so the residual divergence classes are PHP-C8, C9, C34-C36).
