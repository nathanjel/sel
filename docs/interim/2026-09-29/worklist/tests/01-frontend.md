# T01: Front end, depth and source positions

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [x] **T01 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

  Closed 2026-09-30 — conformance/01-lexical, 10-limits, 14-pipeline (+31); tools/check-cli-source.sh; tools/stress.sh coalesce/interp/pipe shapes; all six hosts agree at commit 03786e5 (conformance 2134/2134, sqlt 1309) and every finding row of the family is closed in coverage.csv.

**Destination:** `conformance/10-limits.selt plus generated bounded subprocess probes; CLI byte fixtures and compile/dependencies API probes`.

**Required cases and assertions:** Generate right-associative `??` and `???`, nested interpolation, flat aggregate-body trees, wide RECORDs, and malformed partial parses. Test one below, exactly at, and one above each specified depth/size boundary; separately test a very large input under process limits. Cover isolated and mixed parentheses inside interpolation, multiple pipe placeholders with a side-effect counter, binding builtins, CR/CRLF, invalid UTF-8 and lone surrogates through the host APIs that can represent them. Assert compile versus run phase, code and code-point position; valid adjacent cases must remain accepted. No raw exception, signal, or timeout is an acceptable SEL result.

**Contract prerequisite:** Settle coalescing/interpolation depth charges, pipeline placeholder evaluation count and malformed-source positions before pinning changed expectations. Do not move errors earlier merely to shorten runtime unless the contract permits it.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c3"></a>

### JS-C3: Right-associative `??` / `???` recursion is not depth-counted: hostile input gives a host RangeError instead of E_DEPTH

Source: [JS-C3](../../js-code-review.md). Report labels: [high] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

`node -e "import('/home/nathan/workspaces/nth/sel/js/src/sel.mjs').then(m=>m.compile('1 ?? '.repeat(20000)+'1'))"` throws `RangeError: Maximum call stack size exceeded` (re-run by synthesizer). At n=250 every host parses and runs it (result 1). js-1's script: n=199 ok, n>=200 E_DEPTH from `dependencies`, n=8000 RangeError from `compile`. Other hosts at n=20000: cpp segfaults (exit 139), Python RecursionError, Lisp control stack exhausted; PHP and Go are fine. First RangeError in JS at n=4003 for `??` and n=7816 for `???` (varies with JIT state).

**Additional fixture requirements from the report:** (`grep '??' conformance/10-limits.selt` is empty). Cases `lim.coalesce-depth` (`1 ?? ` repeated 200 and 201 times, E_DEPTH at a pinned offset) and `lim.coalesce-depth-just-under`, pattern `lim.assign-depth`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C3.

<a id="js-c10"></a>

### JS-C10: Lexer recursion for nested interpolation is not depth-bounded; cost grows faster than linearly

Source: [JS-C10](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

js-1 `interp.mjs` prints for depth 1000 `E_DEPTH 1:101`, for 3000 and 5000 a `RangeError`. A file of 5,000 nested levels: php, cpp, go and lisp print `E_DEPTH at line 1 column 101`; js and Python print RangeError / RecursionError.

**Additional fixture requirements from the report:** `lim.parse-depth` covers 100 nested parens only. Add `lim.interp-depth` with a few thousand nested levels expecting `E_DEPTH 1:101` (also catches quadratic blowups by timeout).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C10.

<a id="js-c11"></a>

### JS-C11: An interpolation body is spliced as raw tokens without checking that its parentheses balance

Source: [JS-C11](../../js-code-review.md). Report labels: [medium] [confirmed, all five hosts agree].

**Scenario / reported observation:**

js, php, cpp, python, go print identical results): `"{1) + (2}"` gives `3` (expected E_SYNTAX); `COUNT("{1), (2}")` gives `2` (expected E_SYNTAX); `"a{1);(2}"` gives `2`, discarding the literal text; `A=(1,2); COUNT("{A) ; (A}")` gives `0`. Re-verified by synthesizer: `"{1) + (2}"` gives `3` and `COUNT("{1), (2}")` gives `2`.

**Additional fixture requirements from the report:** (`lex.interp.*` uses balanced bodies). Cases `lex.interp.unbalanced-close` (`"{1) + (2}"` E_SYNTAX), `lex.interp.unbalanced-open` (`"{(1}"`, errors today only by accident at end of input), and `COUNT("{1), (2}")` E_SYNTAX.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C11.

<a id="js-c30"></a>

### JS-C30: Source with an unpaired surrogate raises E_UTF8 at position 0:0:0, not at the offending character

Source: [JS-C30](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`compile('1 +\n \ud800')` throws E_UTF8 with line 0, col 0, offset 0. Expected line 2, col 2, offset 5 (code point index).

**Additional fixture requirements from the report:** ; a `.selt` cannot carry invalid bytes, so an API-level case.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C30.

<a id="js-c31"></a>

### JS-C31: Pipeline call arguments cost one nesting level, not two; multi-placeholder pipes diverge in Go

Source: [JS-C31](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

(b) above; (a) `python3 -c "print('1 .> ABS('*200+'1'+')'*200)"` piped into `-e` on each host.

**Additional fixture requirements from the report:** `pipe.placeholder.first`/`.second` test single placeholders only. No multi-`_` or pipe-depth case.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C31.

<a id="js-c32"></a>

### JS-C32: Error catalogue phases are wrong for two codes the front end raises at compile time

Source: [JS-C32](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`compile('"\\u{110000}"')` throws E_RANGE with no evaluation.

**Additional fixture requirements from the report:** n/a.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C32.

## PHP report scenarios

<a id="php-c5"></a>

### PHP-C5: Nested string interpolation makes the lexer quadratic or worse: a 12.8 KB source costs 9 s, 40 KB does not finish in 300 s

Source: [PHP-C5](../../php-code-review.md). Report labels: [high] [confirmed; re-verified by synthesizer as super-linear].

**Scenario / reported observation:**

measured by reviewer, PHP 8.5, memory_limit=-1): source = `'"{' * d + '1' + '}"' * d`, timing `Sel\Lexer::tokenizeSource`: d=400 0.10 s, d=800 0.39 s, d=1600 1.4 s, d=3200 (12.8 KB) 8.8 s; via `php php/bin/sel file` d=10000 (40 KB) still running after 300 s. Same input in `cpp/build/sel`: E_DEPTH at 1:101 in 0.86 s. Output for depths <= 40 is fine; for larger E_DEPTH at 1:101 (identical to C++), so only the time is wrong. Synthesizer re-run through `php php/bin/sel file` on a loaded box: d=400 0.37 s, d=800 0.77 s, d=1600 2.4 s (super-linear, same answer E_DEPTH at 1:101).

**Additional fixture requirements from the report:** suggest `lim.interp-nesting-depth` (a few hundred nested `"{...}"` levels expecting E_DEPTH; at least a cheap depth-100 case) plus a gate time budget.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C5.

<a id="php-c14"></a>

### PHP-C14: Assignment-target chains cost O(n^2): `array_unshift` in a loop, 120 KB source takes ~10 s

Source: [PHP-C14](../../php-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`python3 -c "print('A'+'[1]'*40000+' = 1')" > f.sel; php php/bin/sel f.sel` -> E_DEPTH after 9.7 s (n=5000 0.41 s, 20000 2.9 s, 40000 9.7 s); js does the 40000 file in 1.0 s. Synthesizer: 20000-index file, E_DEPTH at 1:59999 after 1.9 s (loaded box).

**Additional fixture requirements from the report:** `10-limits.selt` covers the E_DEPTH result, not the time; a timeout-bounded case would.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C14.

<a id="php-c15"></a>

### PHP-C15: A failed parse of a very long flat chain still segfaults PHP: several throw sites do not dismantle the partial tree

Source: [PHP-C15](../../php-code-review.md). Report labels: [medium] [confirmed; not re-verified].

**Scenario / reported observation:**

`php -d memory_limit=-1 php/bin/sel FILE`, exit 139, "Segmentation fault"): `NOSUCH(1` + `+1`*300000 + `)`; `1` + `+1`*300000 + `)`; `A[1` + `+1`*300000 + ` ` (missing `]`); `1 == 1` + `+1`*300000 + ` == 2`. The same chain with a valid program gives the proper runtime E_DEPTH. Threshold: `NOSUCH(...)` with 150000 `+1` -> E_UNKNOWN_FUNC (correct), 200000 -> segfault. Needs ~400 KB of source and >~300 MB memory_limit (128M default dies earlier with a PHP fatal, see PHP-P10), so reachable only where memory_limit is raised.

**Additional fixture requirements from the report:** only feasible as a generated large case in the fuzz/`check-api` lanes (syntax error after a 250k-operator flat chain must report E_SYNTAX, not crash).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C15.

<a id="php-c16"></a>

### PHP-C16: Right-associative `??` / `???` chains are not depth-counted (spec 6.4)

Source: [PHP-C16](../../php-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`1` + ` ?? 1`*20000: php -> `1` (rc 0; re-verified); node -> uncaught RangeError; python -> RecursionError; cpp -> rc 139. 200000 operators: php -> "Allowed memory size of 134217728 bytes exhausted". Expected per 6.4: E_DEPTH at the operator that crosses the limit, identical on every host.

**Additional fixture requirements from the report:** suggest `lim.coalesce-depth` / `lim.coalesce-depth-just-under` next to `lim.assign-depth`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C16.

<a id="php-c42"></a>

### PHP-C42: E_UTF8 for invalid source carries no position

Source: [PHP-C42](../../php-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`printf '"a\xff" & 1' > x.sel; php php/bin/sel x.sel` -> `E_UTF8 at line 0 column 0`; same for `cpp/build/sel`.

**Additional fixture requirements from the report:** (source-level E_UTF8 is untested; only FROM_UTF8 cases exist).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C42.

<a id="php-c43"></a>

### PHP-C43: `strtoupper` / `strtolower` / `strcasecmp` are locale-dependent on the PHP versions `composer.json` still allows

Source: [PHP-C43](../../php-code-review.md). Report labels: [low] [unconfirmed - no PHP 8.1].

**Scenario / reported observation:**

not run (needs 8.1 plus a Turkish locale).

**Additional fixture requirements from the report:** (needs a host-API test with `setlocale` on 8.1).

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C43.

<a id="php-c44"></a>

### PHP-C44: Error catalogue says E_RANGE and E_UTF8 are run-time-only, but the PHP front end raises both at compile time

Source: [PHP-C44](../../php-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`php php/bin/sel --deps -e '"\u{110000}"'` -> `E_RANGE at line 1 column 2` without evaluating anything.

**Additional fixture requirements from the report:** existing `lex.escape.surrogate-rejected` covers behaviour.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C44.

## PYTHON report scenarios

<a id="py-c1"></a>

### PY-C1: Uncaught `RecursionError` where SPEC 6.4 mandates a value or `E_DEPTH` (systemic; six sites)

Source: [PY-C1](../../python-code-review.md). Report labels: [high] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

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

**Additional fixture requirements from the report:** ; `10-limits.selt` pins E_DEPTH only for constructs that use few frames. Suggest `lim.pipe-depth-just-under` and `lim.pipe-depth` (modelled on `lim.index-depth[-just-under]`), `lim.coalesce-chain`, `lim.interp-depth`, `lex.unterminated.deep-brace-run`, and a 198-stage `.> SORT` / `.> MAP(_)` / `.> TOP_BY` pipeline expecting the value. For (f) a `.sqlt` case with a 400-step helper chain expecting a refusal / pure_memory plan (Python unit test needed for the exact-cap cases since source cannot exceed the parser cap). JS has the same shape in (b) and survives only via a bigger stack.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C1.

<a id="py-c17"></a>

### PY-C17: CLI file/REPL input: universal-newline read changes the program, invalid UTF-8 crashes with `UnicodeDecodeError` instead of `E_UTF8`

Source: [PY-C17](../../python-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`printf 'A = "a\r\nb";\nLEN(A)' > x.sel; python3 -m sel x.sel` -> 3 (cpp/lisp/js/php: 4). `printf 'A = 1 # c\r+ 2\r\nA' > y.sel` -> `E_SYNTAX at line 3 column 1` (all other hosts: line 2). `printf '"a\xffb"' > z.sel; python3 -m sel z.sel` -> `UnicodeDecodeError` traceback (cpp/php: `E_UTF8`). `printf '1 + \xff' > x.sel; PYTHONPATH=$PWD/python python3 -m sel x.sel` (s1). Synthesizer re-ran the CRLF case: `3`.

**Additional fixture requirements from the report:** (CLI is not covered by conformance; `tools/e2e.sh` or `check-api` could carry a CRLF-in-literal probe).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C17.

<a id="py-c26"></a>

### PY-C26: `x .> f(_, _)` (several placeholders) evaluates the left operand once per placeholder; Go disagrees with the other four

Source: [PY-C26](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`C = 0; R = (C += 1) .> MAX(_, _); C` -> py/js/php/cpp/lisp `2`; go `E_UNDEF_VAR at line 1 column 31`; three placeholders give 3 in py.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C26.

<a id="py-c27"></a>

### PY-C27: E_UTF8 for a lone-surrogate source carries no position (0:0)

Source: [PY-C27](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`python3 -c "import sel; sel.compile('1 + \ud800')"` -> `E_UTF8` line 0 col 0 offset 0.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C27.

## CPP report scenarios

<a id="cpp-c4"></a>

### CPP-C4: Right-associative `??` / `???` chains recurse in the parser with no depth count (SIGSEGV at about 20k operators)

Source: [CPP-C4](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`python3 -c "print('1 '+'?? 1 '*50000)" > q.sel; cpp/build/sel q.sel` gives rc 139 (also `'NULL '+'?? NULL '*20000+'?? 5'`; 10,000 still works, 20,000 crashes; `???` crashes at 100,000). With 250 operators `NULL ?? ... ?? 5` gives E_DEPTH from the evaluator and `1 ?? 1 ?? ...` gives `1`. JS/Python/Lisp also die on the 50,000 case with no E_DEPTH; PHP answers `1`. Only C++ takes the whole process down with no catchable error.

**Additional fixture requirements from the report:** Suggest `lim.coalesce-chain-long-and-short-circuits` (5,000-operator `1 ?? 1 ?? ...` => `1`) and `lim.coalesce-chain-eval-depth` (all-`NULL` chain past 200 => E_DEPTH at the innermost node).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C4.

<a id="cpp-c5"></a>

### CPP-C5: Nested string interpolation recurses unboundedly in the lexer (SIGSEGV at about 11-12k levels; quadratic before that)

Source: [CPP-C5](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`python3 -c "print('\"{'*N+'1'+'}\"'*N)" > n.sel; cpp/build/sel n.sel`. N=5000 gives `E_DEPTH ... column 101` in about 0.25 s, N=10000 gives E_DEPTH in 1.1 s, N of 12000 and above gives rc 139. The threshold is stack-size dependent: about 700 levels crash a 512 KB worker-thread stack. JS/Python/Lisp also fail at N=20000; PHP spins for over 60 s (quadratic).

**Additional fixture requirements from the report:** (`lim.parse-depth` uses parens only). Suggest `lim.interp-depth-just-under` / `lim.interp-depth-past-the-cap` once the boundary is decided.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C5.

<a id="cpp-c6"></a>

### CPP-C6: Uncounted recursive tree walkers overflow the C stack on a long flat expression inside an aggregate body

Source: [CPP-C6](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`python3 -c "print('MAP((1,2), '+'+'.join(['_']*60000)+')')" > f.sel; cpp/build/sel f.sel` gives rc 139 (20,000 terms correctly gives `E_DEPTH at line 1 column 39611`). Without the aggregate (`_A+_A+...` x400000) it gives E_DEPTH cleanly. The `FILTER(LINK(...), <100000-term body>)` form (`leading_field_conjuncts` / `expr_depends_only`) also exits 139. JS (RangeError) and Lisp (control stack exhausted) fail the same way; PHP dies earlier in the lexer with a memory-limit fatal.

**Additional fixture requirements from the report:** Suggest `lim.eval-depth.flat-chain-inside-aggregate-body`: a generated 5,000-term body inside MAP/FILTER/SORT_BY/BUCKET/TOP_BY expecting E_DEPTH at the 201st node.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C6.

<a id="cpp-c18"></a>

### CPP-C18: `RECORD(...)` with many literal keys costs O(n^2) at compile time

Source: [CPP-C18](../../cpp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

measured): `python3 -c "print('RECORD('+','.join('\"key%d\",%d'%(i,i) for i in range(N))+')')" > r.sel; cpp/build/sel --deps r.sel`. N=5,000 0.09 s; N=20,000 1.13 s; N=40,000 4.4 s (parse stage only, via a timed copy). JS at N=40,000 is 0.9 s.

**Additional fixture requirements from the report:** needed (behaviour unchanged); a scale benchmark with a 20k-key RECORD would catch a regression.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C18.

<a id="cpp-c47"></a>

### CPP-C47: `.>` placeholder spec gaps: multiple `_` evaluate the head once per placeholder; binding functions are exempt from substitution

Source: [CPP-C47](../../cpp-code-review.md). Report labels: [low] [confirmed, all five hosts].

**Scenario / reported observation:**

`N = 0; (N = N + 1) .> MAX(_, _) & "/" & N` prints `2/2` on all hosts (single evaluation would give `1/1`). `(1,2,3) .> FILTER((4,5,6), _)` gives `E_EXPECT_SYMBOL at line 1 column 20` on all hosts.

**Additional fixture requirements from the report:** `14-pipeline.selt` has `pipe.placeholder.first/second` only. Suggest a double-placeholder case and `pipe.placeholder.binding-function-is-not-substituted`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C47.

<a id="cpp-c48"></a>

### CPP-C48: E_UTF8 for invalid source bytes carries position 0:0

Source: [CPP-C48](../../cpp-code-review.md). Report labels: [low] [unconfirmed against spec intent].

**Scenario / reported observation:**

`sel -e "$(printf '1 + "a\xffb"')"` gives `E_UTF8 at line 0 column 0: invalid start byte 0xff at byte 6`.

**Additional fixture requirements from the report:** cannot be expressed in `.selt`; `tools/check-api.sh` could carry a byte-level case.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C48.

## LISP report scenarios

<a id="lisp-c4"></a>

### LISP-C4: Nested interpolation: uncounted lexer recursion exhausts the control stack, and the lexer is quadratic in nesting

Source: [LISP-C4](../../lisp-code-review.md). Report labels: [high] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

measured, in-process compile time): nesting 500: 0.03 s; 1000: 0.12 s; 2000: 0.50 s; 4000: 2.3 s; 5000: 4.3 s (E_DEPTH 1:101 as expected); 6000: `CONTROL-STACK-EXHAUSTED`; 100,000 levels: 32 s then stack exhaustion. CLI (re-verified with n=8000: "Unhandled SB-KERNEL::CONTROL-STACK-EXHAUSTED", rc=1):
  `python3 -c 'n=8000;s="1"\nfor i in range(n): s="\"{%s}\""%s\nprint(s,end="")' > in.sel; lisp/bin/sel in.sel`
  Other hosts at nesting 3000: cpp and php answer E_DEPTH 1:101 (php 3.7 s, quadratic there too); js and python crash (RangeError / traceback). Lisp at 3000 takes 1.8 s.

**Additional fixture requirements from the report:** `lim.parse-depth` covers parens and calls only. Suggest `lim.interp-nesting-depth` (about 150 nested `"{...}"`, E_DEPTH at the parser's position, identical in all hosts) and a large-nesting case in whichever lane exercises large inputs.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C4.

<a id="lisp-c21"></a>

### LISP-C21: Right-associative `??` / `???` chains recurse in the parser without counting depth

Source: [LISP-C21](../../lisp-code-review.md). Report labels: [medium] [confirmed; cross-host spec gap].

**Scenario / reported observation:**

in-process): `1 ?? 1 ?? ... ?? 1`: n=12000 gives `1`; n=15000 gives `STORAGE-CONDITION` (CONTROL-STACK-EXHAUSTED). CLI with n=20000: "Unhandled CONTROL-STACK-EXHAUSTED", rc=1. Same for `???`. Other hosts: cpp segfaults (rc=139) at 20,000; js RangeError and python RecursionError at about 5,000; php out of memory at 100,000.

**Additional fixture requirements from the report:** Suggest `lim.coalesce-chain-*` at about 300 and 30,000 links with a non-null head.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C21.

<a id="lisp-c31"></a>

### LISP-C31: CLI: invalid UTF-8 in a source file (or `-e`) is not E_UTF8

Source: [LISP-C31](../../lisp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`printf '"a\xffb"' > bad.sel; lisp/bin/sel bad.sel` -> "Unhandled SB-INT:STREAM-DECODING-ERROR" (rc 1). cpp and php: "E_UTF8 at line 0 column 0: invalid start byte 0xff at byte 2". With `-e $'"a\xffb"'` SBCL prints "WARNING: Error initializing *POSIX-ARGV*: :UTF-8 c-string decoding error".

**Additional fixture requirements from the report:** (suite is text-in). Not relevant to library users.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C31.

<a id="lisp-c46"></a>

### LISP-C46: Spec does not state that binding builtins never take the `.>` placeholder rule

Source: [LISP-C46](../../lisp-code-review.md). Report labels: [low] [confirmed; spec text gap, no divergence].

**Scenario / reported observation:**

`(1,2,3,4) .> FILTER(_ % 2 == 0)` works in all five hosts because binding functions never substitute `_`; the rule is unwritten, so a sixth host could get it wrong.

**Additional fixture requirements from the report:** `pipe.placeholder.*` cover non-binding functions only. Suggest `pipe.placeholder.binding-fn-untouched`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C46.

## GO report scenarios

<a id="go-c9"></a>

### GO-C9: Pipeline placeholder `_` replaces only the FIRST occurrence

Source: [GO-C9](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

SPEC.md:449 and grammar.md say "the bare placeholder `_` is replaced by x" and are silent on repeats; JS, Python, PHP, C++ and Lisp all substitute every top-level bare `_`. Go stops after the first, so the second stays a variable read. Removing the `break` makes Go agree with JS on all 80k parse-corpus programs (98 differed before).

**Additional fixture requirements from the report:** `pipe.placeholder.first`/`.second` (`14-pipeline.selt:47,54`) use a single `_`. Add `pipe.placeholder.repeated` (`LIST(1,2) .> LIST(_, _)`) and `pipe.placeholder.repeated-below-min-arity` (`5 .> MAX(_, _)`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C9.

<a id="go-c13"></a>

### GO-C13: Lexer is quadratic in interpolation nesting depth and its recursion is unbounded (fatal stack overflow)

Source: [GO-C13](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

measured, source `'"{'*d + '1' + '}"'*d` via `go/build/sel file`): d=1,000: 0.02 s; 4,000: 0.25 s; 16,000: 3.8 s; 32,000: 17.6 s; 64,000 (256 KB): 88 s CPU; every run ends `E_DEPTH at 1:101`. `'"{' * 8000000` (16 MB) -> `runtime: goroutine stack exceeds 1000000000-byte limit / fatal error: stack overflow`, exit 2. For context, JS dies with a RangeError trace at d=2,000 and C++ segfaults at d=16,000 (and takes 4.3 s at 16k): a cross-host lexer hole; Go is the most resilient.

**Additional fixture requirements from the report:** Pin `lim.interp-nesting-*` (100 nested interpolations -> E_DEPTH at the same position everywhere); the timing part is not expressible in `.selt`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C13.
