# T07: Text, binary and output budgets

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [ ] **T07 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

**Destination:** `text/binary/null .selt cases, generated size-boundary probes and memory-limited subprocess tests`.

**Required cases and assertions:** Exercise REPEAT/PADL/PADR/REPLACE/JOIN/concatenation, collection doubling and join fan-out at legal boundary, boundary+1 and huge integer arguments without allocating the requested huge output. Include empty input with huge count, zero count, unchanged padding, 200000-code-point replacement/separator, astral and multibyte inputs, and error precedence with another invalid argument. Pin code-point lengths, FIND offsets and exact binary bytes. Cover LTB(BTL(empty)), scaled integral byte values, malformed hex/base64 with multibyte diagnostics, empty PATH components and empty keys. Distinguish safe result truncation/saturation from an allocation request.

**Contract prerequisite:** Specify output/work budgets and their error codes centrally before tests assume a numerical cap. An existing machine allocation failure is not a portable limit. Decide empty LTB and scaled-byte behavior.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c4"></a>

### JS-C4: REPLACE throws an uncaught RangeError when the replacement is longer than about 125k code points

Source: [JS-C4](../../js-code-review.md). Report labels: [high] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

`LEN(REPLACE("a", REPEAT("b", 100000), "a"))` gives `t"100000"`. `node js/bin/sel.mjs -e 'LEN(REPLACE("a", REPEAT("b", 150000), "a"))'` gives an uncaught `RangeError` (re-run by synthesizer; crash at text.mjs:78).

**Additional fixture requirements from the report:** Case `text.replace.long-replacement` with a 200,000-code-point replacement; the same for SPLIT with a long separator (fine today, unpinned).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C4.

<a id="js-c5"></a>

### JS-C5: REPEAT, PADL and PADR have no size cap; the JS host throws host exceptions or exhausts memory

Source: [JS-C5](../../js-code-review.md). Report labels: [high] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

`node js/bin/sel.mjs -e "REPEAT('a', 1000000000000)"` gives `RangeError: Invalid string length` (re-run by synthesizer). `REPEAT('', 1$(printf '%0400d' 0))` gives `RangeError: Invalid count value`. `PADL("abc", 99999999999999999999999, "x")` gives `RangeError: Invalid array length`. `REPEAT("", 99999999999999999999999)` correctly gives `""` (finite count). js-2 N1: `REPEAT("ab", 9999999999)` and `PADL("a", 99999999999, "x")` escape as raw `RangeError` from `program.run`.

**Additional fixture requirements from the report:** Cases `text.repeat.huge-count` and `text.padl.huge-width` (both `E_RANGE`), `text.repeat.empty-huge-count` (returns `""`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C5.

<a id="js-c41"></a>

### JS-C41: Nothing bounds the size of a value, the work of `,`, or the size of a join result

Source: [JS-C41](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`S='A=(1,2);'$(printf 'A=(A,A);%.0s' $(seq 30))'COUNT(A)'; node --max-old-space-size=500 js/bin/sel.mjs -e "$S"` (256-byte program, `FATAL ERROR: heap out of memory`, rc 134); js-5 `oom.mjs` for the join.

**Additional fixture requirements from the report:** n/a.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C41.

<a id="js-c46"></a>

### JS-C46: `LTB` of an empty list fails, so `LTB(BTL(x))` does not round-trip for empty input

Source: [JS-C46](../../js-code-review.md). Report labels: [low] [confirmed, consistent across hosts].

**Scenario / reported observation:**

`BLEN(LTB(BTL("")))` gives `E_NO_SCALAR at line 1 column 10: value has no scalar and no children` (js, php, python, cpp, lisp).

**Additional fixture requirements from the report:** Case `bin.ltb.empty`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C46.

<a id="js-c47"></a>

### JS-C47: Behaviour of `PATH(x, "")` is unspecified and untested

Source: [JS-C47](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

the empty path returns the target itself, not the value under key `""`. All hosts agree (`PATH(RECORD("", 5), "")` returns the record), but §7.9 ("walks dot-separated child keys") could equally read it as one segment `""`. Keys containing `.` cannot be addressed (`PATH(RECORD("a.b",5), "a.b")` is NULL).

**Additional fixture requirements from the report:** Case `null.path.empty-path`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C47.

## PHP report scenarios

<a id="php-c6"></a>

### PHP-C6: REPEAT / PADL / PADR (and exponential value growth) have no size cap: PHP dies with an uncatchable "Allowed memory size exhausted" fatal

Source: [PHP-C6](../../php-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`php php/bin/sel -e "LEN(PADL('a', 5000000, 'x'))"` -> PHP Fatal (js/py/cpp/lisp print 5000000). `LEN(REPEAT('ab', 10000000000))` -> PHP Fatal ("tried to allocate 20000000032 bytes"), JS raw RangeError, Lisp heap exhaustion. `LEN(PADL('a', 99999999999999999999, 'x'))` -> PHP Fatal. Doubling: `python3 -c "print('A = (1,2); ' + 'A = (A, A); '*30 + 'COUNT(A)')" > dbl.sel; php php/bin/sel dbl.sel` -> PHP Fatal "Allowed memory size of 134217728 bytes exhausted".

**Additional fixture requirements from the report:** suggest `lim.repeat-cap` / `lim.pad-cap` once the spec has a number.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C6.

<a id="php-c23"></a>

### PHP-C23: Memory-limit walls at ordinary sizes: per-code-point arrays, RREPLACE match arrays, hash-array tokens

Source: [PHP-C23](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

(a) `php php/bin/sel -e "LEN(REPEAT('ab', 3000000))"` -> PHP Fatal at `Utf8.php` line 84 (others print 6000000); `LEN(REPEAT('a', 4000000))` works, 6-8M fatals. (b) `php php/bin/sel -e "LEN(RREPLACE('a','bb',REPEAT('a', 500000)))"` -> PHP Fatal (200,000 matches fine: 400000; others print 2000000 for 1,000,000). (c) `1` + `+1`*100000 (200 KB, 200,002 tokens): lexing 79.4 MB = 397 B/token, parsing another ~56 MB for 50K operators; at 100K operators the parse dies with "Allowed memory size of 134217728 bytes exhausted".

**Additional fixture requirements from the report:** (memory limits are not conformance material).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C23.

## PYTHON report scenarios

<a id="py-c14"></a>

### PY-C14: `REPEAT` / `PADL` / `PADR` have no size cap: raw `MemoryError` / `OverflowError`, hang, memory DoS

Source: [PY-C14](../../python-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`python3 -m sel -e 'REPEAT("a", 1000000000000)'` -> `MemoryError` traceback. `python3 -m sel -e 'LEN(REPEAT("", 1000000000000000000000000000000))'` -> `OverflowError`; JS and PHP print `0`. `python3 -m sel -e 'LEN(PADL("a",100000000000,"x"))'` -> minutes of CPU, then MemoryError (57 s at 100% CPU before MemoryError under a 3 GB ulimit). s3: `REPEAT("", 1000000000000000000000000000000)` -> OverflowError; `REPEAT("ab", 1000000000000)` -> MemoryError (under `ulimit -v`). Synthesizer re-ran `REPEAT("", 10^30)`: `OverflowError: cannot fit 'int' into an index-sized integer`.

**Additional fixture requirements from the report:** Suggest `text.repeat.huge-count` (E_RANGE or the cap's code) and `text.repeat.empty-huge-count` (`""`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C14.

<a id="py-c36"></a>

### PY-C36: `LTB(LIST())` raises E_NO_SCALAR, so `LTB(BTL(""))` fails

Source: [PY-C36](../../python-code-review.md). Report labels: [low] [confirmed, all hosts].

**Scenario / reported observation:**

`python3 -m sel -e 'LTB(BTL(""))'` -> `E_NO_SCALAR` (synthesizer: confirmed); by symmetry expected an empty BIN.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C36.

## CPP report scenarios

<a id="cpp-c8"></a>

### CPP-C8: `REPEAT("", n)` hangs; `REPEAT`/`PADL`/`PADR` allocate without bound and die with uncaught `std::bad_alloc`

Source: [CPP-C8](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`time cpp/build/sel -e 'LEN(REPEAT("", 4000000000))'` gives `0` after 27.4 s (JS 120 ms, PHP 95 ms). `(ulimit -v 3000000; cpp/build/sel -e 'LEN(REPEAT("ab", 10000000000))')` gives `terminate called after throwing an instance of 'std::bad_alloc'`, rc 134 after 21 s; same for `PADL("x",100000000000,"0")`. Synthesizer: `LEN(REPEAT("", 400000000))` gives `0` after 2.4 s.

**Additional fixture requirements from the report:** Suggest `text.repeat.empty-huge` (`REPEAT("", 9000000000000000000)` => `""`) and a spec'd-cap case.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C8.

<a id="cpp-c51"></a>

### CPP-C51: `long` (32-bit on Windows/LLP64) truncates huge counts in the text builtins

Source: [CPP-C51](../../cpp-code-review.md). Report labels: [low] [unconfirmed, reasoned].

**Scenario / reported observation:**

`non_neg_int` saturates at LLONG_MAX; where `long` is 32-bit, the cast gives -1, so `LEFT("abc", 9223372036854775807)` would clamp to `""` instead of `"abc"`, and any count over 2^31 misbehaves. `conanfile.py:58` shows Windows is a packaged target. Fine on this LP64 box; not run.

**Additional fixture requirements from the report:** Suggest `text.left.huge-count` (`LEFT("abc", 9223372036854775807)` = `abc`) so any LLP64 build fails it.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C51.

<a id="cpp-c52"></a>

### CPP-C52: `FROM_HEX` / `DECODE_BASE64` put a single byte of a multi-byte character into the error message (invalid UTF-8)

Source: [CPP-C52](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`cpp/build/sel -e 'DECODE_BASE64("é==")' | xxd` shows a lone `c3` inside the quotes.

**Additional fixture requirements from the report:** n/a (messages are not asserted).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C52.

## LISP report scenarios

<a id="lisp-c20"></a>

### LISP-C20: Text-size explosions end in an uncatchable heap exhaustion

Source: [LISP-C20](../../lisp-code-review.md). Report labels: [medium] [confirmed; cross-host spec gap].

**Scenario / reported observation:**

`lisp/bin/sel -e 'LEN(REPEAT("a", 150000000))'` -> heap exhausted, exit 1; `node js/bin/sel.mjs -e 'LEN(REPEAT("a", 3000000000))'` -> RangeError.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C20.

## GO report scenarios

<a id="go-c11"></a>

### GO-C11: Huge REPEAT / PADL / PADR / JOIN sizes crash the process (uncatchable) instead of raising a SEL error

Source: [GO-C11](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`bash -c 'ulimit -v 8000000; go/build/sel -e "PADL(\"a\", 5000000000, \"x\")"'` -> fatal error: out of memory. `go/build/sel -e 'REPEAT("ab", 99999999999999999999)'` -> panic trace. `LEN(JOIN(SPLIT(REPEAT("a,",2000000),","), REPEAT("b",2000000)))` under `ulimit -v 6000000` -> `runtime: out of memory: cannot allocate 4000002867200-byte block`; JS `RangeError: Invalid string length`. Translator: `PADL("7", 318446744073709551616, "0") $== NAME` -> `PANIC: makeslice: len out of range` from `sql.Translate`; `LEN(REPEAT("abc", 100000000))` = 300000000 in 0.9 s, so a constant in a translated rule can burn memory at translation time.

**Additional fixture requirements from the report:** ; needs the spec decision, then e.g. `LEN(REPEAT("ab", 99999999999999999999))` -> `error E_RANGE` and a translator case `pad.count-out-of-range`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C11.

<a id="go-c14"></a>

### GO-C14: Exponential value growth from a 300-byte program ends in an unrecoverable `fatal error: out of memory`

Source: [GO-C14](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`go/build/sel -e "A = 1; $(python3 -c "print(' '.join(['A = (A, A);']*30))") COUNT(A)"` under `ulimit -v 3000000` -> `fatal error: out of memory`, exit 2. 18/20/22 doublings: 0.2/0.8/2.9 s, results 262144/1048576/4194304.

**Additional fixture requirements from the report:** ; needs a limits decision first.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C14.

<a id="go-c29"></a>

### GO-C29: LTB accepts integral-but-scaled numbers (`1.0`, `"1.0"`); every other host raises E_RANGE

Source: [GO-C29](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`BTL(LTB(LIST(1.0)))` -> Go `1`, others `E_RANGE`; `LTB(LIST("1.0"))` -> Go `bin:01`, js/py `E_RANGE`. `LEFT("abc",1.0)` is `a` in all hosts.

**Additional fixture requirements from the report:** Add `LTB((65, 1.0))` with an explicit decision.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C29.
