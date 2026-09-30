# T06: Portable regex semantics and resource failures

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [x] **T06 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

  Closed 2026-09-30 — conformance/28-regex-portability, 28b-regex-ambiguity; tools/regex-ambiguity-ref.py, check-regex-ambiguity-diff.py, check-regex-resources.py; all six hosts agree at commit 3bc54e6 (conformance 2134/2134, sqlt 1309) and every finding row of the family is closed in coverage.csv.

**Destination:** `shared regex .selt cases, compile API probes and isolated regex resource/cache stress tests`.

**Required cases and assertions:** Cross all RMATCH/RFIND/RGROUPS/RREPLACE entry points with zero-width/adjacent-empty matches, quantified captures, greedy/lazy empty iterations, anchors, PCRE verbs, embedded POSIX/collating classes, class escapes next to hyphens, ASCII flags and Unicode subjects. Test counted repeats 999/1000/1001/65535/65536, leading-zero counts, nested-repeat products, and group-depth boundaries once specified. A matching long-subject case is required: a false-result-only case hides Go repeat failures. Include `(a+)+` and ambiguous alternations with bounded sizes, PCRE JIT/backtrack/recursion limits, C++ complexity exceptions, Python warnings-as-errors and Lisp stack conditions. A resource failure must never masquerade as FALSE/0/empty/unchanged text. Churn unique patterns and verify bounded retained cache memory.

**Contract prerequisite:** Choose portable empty-match/capture semantics, literal-pattern validation phase, structural limits and a resource-failure policy. A proposed new error code or regex engine in a review is not an approved contract. Go linear matching still needs all semantic fixtures.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c6"></a>

### JS-C6: RMATCH / RFIND / RGROUPS / RREPLACE have no protection against catastrophic backtracking

Source: [JS-C6](../../js-code-review.md). Report labels: [high] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

js-4 measured): `RMATCH('^(a+)+$', REPEAT('a', N) & 'b')` gives FALSE after 74 ms at N=20, 188 ms at N=24, 700 ms at N=26, 3.5 s at N=28, 14 s at N=30 (about 4x per 2 characters). Re-run by synthesizer at N=26: FALSE, 8.1 s wall including startup on a heavily loaded box (exponential growth confirmed; the absolute number is noisier than js-4's).

**Additional fixture requirements from the report:** Cases `re.dos.nested-plus` and `re.dos.alt-star` once a bound exists.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C6.

<a id="js-c17"></a>

### JS-C17: V8 regexp backtrack-stack overflow escapes as an uncaught RangeError from a regex builtin

Source: [JS-C17](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`RMATCH('^(a|b)*$', REPEAT('a', 5000000))` throws the RangeError; n=3,500,000 returns TRUE. `LEN(RREPLACE('(a|b)*', 'x', REPEAT('a', 5000000)))` throws the same.

**Additional fixture requirements from the report:** , and a test needs a multi-megabyte subject, so use host unit tests.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C17.

<a id="js-c18"></a>

### JS-C18: The regex class validator rejects POSIX classes only at the start of a class: `[a[:digit:]` is accepted here and rejected by PCRE

Source: [JS-C18](../../js-code-review.md). Report labels: [medium] [confirmed] [a[:digit:].

**Scenario / reported observation:**

`RMATCH('[a[:digit:]', ':')` gives TRUE in js/python/cpp/lisp and `E_REGEX_SYNTAX ... PCRE rejected /[a[:digit:]/` in php. `[a[.x.]` and `[a[=x=]` split the same way.

**Additional fixture requirements from the report:** `re.posix-class` cases cover the leading form only. Add `re.class.posix-not-leading` and `re.class.collating-element`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C18.

<a id="js-c19"></a>

### JS-C19: Group nesting depth is not bounded by the regex validator; PCRE and SRELL reject at 250, JS/Python/Lisp accept

Source: [JS-C19](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

pattern `'(' * 300 + 'a' + ')' * 300` with `RMATCH(<pat>, 'a')`: js TRUE, python TRUE, lisp TRUE, php `E_REGEX_SYNTAX ... PCRE rejected`, cpp `E_REGEX_SYNTAX ... error_complexity`. PHP boundary: 250 nested groups TRUE, 251 rejected.

**Additional fixture requirements from the report:** Cases `re.limits.group-depth-250-ok` and `re.limits.group-depth-251-rejected`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C19.

<a id="js-c43"></a>

### JS-C43: `\d \w \s` expansion inside a regex class turns an adjacent `-` into a range (hosts agree; the result is neither engine's meaning)

Source: [JS-C43](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`RMATCH('[\s-z]', 'a')` gives TRUE in all five hosts (PCRE semantics: FALSE). `RMATCH('[+-\d]', '5')` gives FALSE and `RMATCH('[+-\d]', ',')` gives TRUE. `RMATCH('[\w-.]', 'a')` gives `E_REGEX_SYNTAX ... Range out of order` with the rewritten pattern in the message.

**Additional fixture requirements from the report:** Case `re.class.escape-adjacent-hyphen`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C43.

<a id="js-c44"></a>

### JS-C44: Regex flag letters are matched case-insensitively using the host's `toLowerCase`

Source: [JS-C44](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`RMATCH('a', 'A', 'I')` gives TRUE in all hosts.

**Additional fixture requirements from the report:** `re.flag.unknown` exists; add `re.flag.uppercase-I`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C44.

<a id="js-c45"></a>

### JS-C45: The regex validator does not reject quantified anchors (`^*`, `$+`, `^{2}`); JS relies on V8, Lisp accepts them

Source: [JS-C45](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`RMATCH('^*', 'aaa')` gives `E_REGEX_SYNTAX` in js/php/python/cpp and TRUE in lisp; `$+` and `^{2}` split the same way.

**Additional fixture requirements from the report:** Case `re.quant.on-anchor`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C45.

## PHP report scenarios

<a id="php-c3"></a>

### PHP-C3: PCRE resource errors (backtrack / recursion / JIT stack limit) become silently wrong results

Source: [PHP-C3](../../php-code-review.md). Report labels: [high] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`php php/bin/sel -e "RMATCH('^(?:a|b)*\$', REPEAT('a', 60000))"` -> PHP FALSE; js/py/cpp/lisp TRUE (re-verified by synthesizer: PHP FALSE, js TRUE). `RFIND('(?:a|b)*c', REPEAT('a',60000) & 'c')` -> PHP 0, others 1. `LEN(RREPLACE('^(?:a|b)*\$','X',REPEAT('a',60000)))` -> PHP 60000, others 1. `COUNT(RGROUPS('^(?:a|b)*\$', REPEAT('a',60000)))` -> PHP 0, others 1. Backtrack variant: `RMATCH('^(?:(a+)+c|.*)\$', REPEAT('a',22)&'b')` -> PHP FALSE, js/py/lisp TRUE (cpp aborts, see "Other-host notes" at the end). `preg_last_error_msg()` said "Recursion limit exhausted" (49,999 vs 50,000+) and "Backtrack limit exhausted".

**Additional fixture requirements from the report:** suggest a 100,000-char subject against `^(?:a|b)*$` (expect TRUE) after the spec picks a resource-limit policy.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C3.

<a id="php-c4"></a>

### PHP-C4: PCRE backtracking verbs `(*FAIL)`, `(*ACCEPT)`, `(*COMMIT)`, `(*UTF8)` are accepted and change matching

Source: [PHP-C4](../../php-code-review.md). Report labels: [high] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`php php/bin/sel -e "RMATCH('(*FAIL)', 'a')"` -> FALSE (others E_REGEX_SYNTAX; re-verified: PHP FALSE, js E_REGEX_SYNTAX). `RMATCH('a(*ACCEPT)b','ac')` -> TRUE (re-verified). `RMATCH('(*UTF8)a','a')` and `RMATCH('(*COMMIT)a','a')` -> TRUE (others E_REGEX_SYNTAX). `(*LIMIT_MATCH=1)` is rejected by PCRE itself.

**Additional fixture requirements from the report:** suggest `re.reject.pcre-verb`: `RMATCH('(*FAIL)','a')` => E_REGEX_SYNTAX, and `RMATCH('a(*ACCEPT)b','ac')`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C4.

<a id="php-c18"></a>

### PHP-C18: Class escapes used as range endpoints are expanded into a wrong range (`[+-\d]`, `[\s-x]`, `[\w-a]`)

Source: [PHP-C18](../../php-code-review.md). Report labels: [medium] [confirmed] [+-\d] [\s-x] [\w-a].

**Scenario / reported observation:**

`php php/bin/sel -e "RMATCH('^[+-\d]\$','5')"` -> FALSE (expected E_REGEX_SYNTAX; every host FALSE). `RMATCH('^[+-\d]\$','/')` -> TRUE. `RMATCH('^[\s-x]\$','a')` -> TRUE. `RMATCH('^[\w-a]\$','`')` -> TRUE. Native: `node -e "new RegExp('^[\\\\s-x]$','u')"` throws; `php -r 'var_dump(preg_match("/^[\\s-x]$/u","a"));'` warns "invalid range" and returns false.

**Additional fixture requirements from the report:** suggest `re.reject.class-escape-in-range` for `[+-\d]`, `[\d-z]`, `[a-\s]` (the last is already rejected everywhere, by accident).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C18.

<a id="php-c19"></a>

### PHP-C19: `[a[:alpha:]`, `[a[.b.]`, `[a[=b=]`: PHP raises E_REGEX_SYNTAX where every other host matches

Source: [PHP-C19](../../php-code-review.md). Report labels: [medium] [confirmed] [a[:alpha:] [a[.b.] [a[=b=].

**Scenario / reported observation:**

`php php/bin/sel -e "RMATCH('[a[:alpha:]','[')"` -> E_REGEX_SYNTAX "PCRE rejected"; js/py/cpp/lisp TRUE. Same for `RMATCH('[a[.b.]','[')` and `RMATCH('[a[=b=]','[')`.

**Additional fixture requirements from the report:** suggest `re.reject.posix-class-not-at-start`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C19.

<a id="php-c20"></a>

### PHP-C20: RREPLACE with a pattern that can match empty: PHP (and C++) retry non-empty at the same position, JS/Python/Lisp advance by one

Source: [PHP-C20](../../php-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`php php/bin/sel -e "RREPLACE('b*?','-','abb')"` -> `-a-----` (re-verified); js/py/lisp -> `-a-b-b-` (re-verified for js); cpp `-a-----`. `RREPLACE('(?:|a)','-','aa')` -> `-----` vs `-a-a-`. `COUNT(SPLIT(RREPLACE('x??','-','xx'),'-'))` -> 6 vs 4.

**Additional fixture requirements from the report:** suggest `re.replace.lazy-empty-then-nonempty` (`RREPLACE('b*?','-','abb')`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C20.

<a id="php-c21"></a>

### PHP-C21: Capture groups inside a quantified group: PCRE keeps the last participating value, ECMAScript resets each iteration

Source: [PHP-C21](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`php php/bin/sel -e "RGROUPS('(?:(a)|b)*','ab')"` -> group `a` (php, py, lisp) vs `""` (js, cpp). Also `RGROUPS('(?:(a)|(b))+','ab')` and `RGROUPS('(z)((a+)?(b+)?(c))*','zaacbbbcac')` (group 5 `bbb` vs `""`).

**Additional fixture requirements from the report:** suggest `re.groups.quantified-group-capture` once decided.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C21.

<a id="php-c45"></a>

### PHP-C45: PCRE structural limits make valid portable patterns E_REGEX_SYNTAX in PHP only

Source: [PHP-C45](../../php-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

300 nested `(?:` around `a` (`RMATCH(<generated>, 'a')`): PHP E_REGEX_SYNTAX, js/py TRUE. `RMATCH('(?:a{60000}){60000}', 'a')` -> PHP E_REGEX_SYNTAX "PCRE rejected", js/py/cpp FALSE.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C45.

## PYTHON report scenarios

<a id="py-c3"></a>

### PY-C3: Catastrophic regex backtracking: a 40-character subject hangs the process; no step or time bound

Source: [PY-C3](../../python-code-review.md). Report labels: [high] [confirmed; partly re-verified].

**Scenario / reported observation:**

Python): `python3 -m sel -e "RMATCH('^(a+)+\$', REPEAT('a', 28) & '!')"` gave FALSE after 32 s (JS 40 s, PHP 0.38 s). `RMATCH('(a|aa)+$', REPEAT('a', 40) & 'b')` did not finish in 60 s (killed by `timeout`). `RMATCH('(a|b|ab)*c', REPEAT('ab', 20))` took 1.15 s. Each doubling of the subject roughly squares the time. Synthesizer ran the same pattern with `REPEAT('a', 22)`: FALSE in 0.9 s (consistent with exponential growth; the 28 and 40 cases were not re-run to avoid multi-minute jobs).

**Additional fixture requirements from the report:** Suggest `re.dos.nested-plus`, `re.dos.overlapping-alt` (must return or raise within a bound).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C3.

<a id="py-c4"></a>

### PY-C4: The "portable" regex subset is not portable for quantified groups: PCRE-style hosts and ECMAScript-style hosts return different matches and captures

Source: [PY-C4](../../python-code-review.md). Report labels: [high] [confirmed; partly re-verified].

**Scenario / reported observation:**

py / js / php / cpp; Lisp agrees with py where checked):
  - `RGROUPS('(|a)+', 'aa')`: py `{"","" }`, js/cpp `{"aa","a"}`, php `{"",""}`, lisp `{"",""}`.
  - `RREPLACE('(a*?)+', '<$1>', 'aaa')`: py `<>a<>a<>a<>`, js/cpp `<a><>`, php `<><><><><><><>`, lisp `<>a<>a<>a<>` (three different answers; PHP also differs from Python).
  - `RGROUPS('(a*)*', 'aa')`: py/php group 2 `""`, js/cpp `"aa"`. Likewise `(a?)*`, `(a?)+`, `(a*)+`, `(a|)+b`, `(a*)*b`, `(a+|b*)*`, `(a|b*)*c`.
  - `RGROUPS('(?:(a)|(b))*', 'ab')`: py/php group 2 `"a"`, js/cpp `""`. Same for `((a)|(b))*`, `(?:(a)|b){2}` on `ab`, `(?:(a)|(b))+` on `ba`.
  - Synthesizer: `RGROUPS('(|a)+', 'aa')` py `-{"1"=t"", "2"=t""}`, js `-{"1"=t"aa", "2"=t"a"}`.

**Additional fixture requirements from the report:** `09-regex.selt` has no quantified capture groups. Suggest `re.groups.empty-iteration`, `re.groups.capture-reset`, `re.replace.empty-iteration`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C4.

<a id="py-c15"></a>

### PY-C15: Deeply nested groups in a regex pattern crash with an uncaught `RecursionError`

Source: [PY-C15](../../python-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`python3 -c "import sel; p=sel.compile(\"RMATCH('\"+'('*1000+'a'+')'*1000+\"', 'a')\"); p.run(sel.Value.from_native({}))"` -> `RecursionError: maximum recursion depth exceeded`. 400 levels: TRUE.

**Additional fixture requirements from the report:** Suggest `re.depth.nested-groups-cap` at and one past the cap.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C15.

<a id="py-c33"></a>

### PY-C33: `re.compile` emits `FutureWarning` for valid subset patterns (`[[]`, `[a&&b]`, `[a||b]`, `[a~~b]`, `[a--b]`), an uncaught exception under `-W error`

Source: [PY-C33](../../python-code-review.md). Report labels: [low] [confirmed] [[] [a&&b] [a||b] [a~~b] [a--b].

**Scenario / reported observation:**

`python3 -m sel -e "RMATCH('[[]', '[')"` prints the warning and TRUE; `python3 -W error -m sel -e "RMATCH('[[]', '[')"` -> `FutureWarning` traceback. (`[a--b]` is then rejected anyway as a bad range, like other hosts.)

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C33.

<a id="py-c34"></a>

### PY-C34: Regex pattern cache is unbounded

Source: [PY-C34](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

process-global dict keyed on `(ignore_case, pattern)`, no eviction; patterns can be data-driven. `re` keeps its own bounded cache (512), so this one buys only the cost of `validate()`/`_lower_anchors`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C34.

<a id="py-c35"></a>

### PY-C35: Literal regex patterns are not validated at compile time although the spec says they are

Source: [PY-C35](../../python-code-review.md). Report labels: [low] [confirmed, all five hosts].

**Scenario / reported observation:**

`IF(FALSE, RMATCH('(?=a)', 'a'), 1)` yields `1` on py, js, php, cpp and lisp; `sel.compile("RMATCH('(?=a)','a')")` succeeds. Synthesizer: `1` (confirmed on py).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C35.

## CPP report scenarios

<a id="cpp-c7"></a>

### CPP-C7: srell `error_complexity` escapes as a raw C++ exception at match time (process abort)

Source: [CPP-C7](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`cpp/build/sel -e 'RMATCH("^(a+)+$", REPEAT("a",22) & "!")'` aborts (n=20 answers FALSE in 68 ms; n=22 aborts in 0.8 s). `cpp/build/sel -e 'RMATCH("^(a|b)*c$", REPEAT("ab", 1500000))'` aborts; with `REPEAT("ab",1000000)` it gives `FALSE`. Other hosts answer FALSE at 1.5M.

**Additional fixture requirements from the report:** At minimum a case that a 3M-character subject with `^(a|b)*c$` returns FALSE; `re.limit.*` cases only after the spec defines a step budget.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C7.

<a id="cpp-c16"></a>

### CPP-C16: Unbounded regex cache at about 250 KB per compiled pattern

Source: [CPP-C16](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

measured): `python3 rss.py cpp/build/sel -e "L = SPLIT(REPEAT('x,', 20000), ','); COUNT(FILTER(L, RMATCH('a' & _K, 'a')))"` gives max RSS 5077 MB in 5.8 s (100,000 patterns gives 25,371 MB in 26.7 s; do not rerun the 100k on a shared box). One repeated pattern stays at 22 MB.

**Additional fixture requirements from the report:** not expressible in `.selt`; suggest a `tools/` memory-bound probe.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C16.

<a id="cpp-c26"></a>

### CPP-C26: RREPLACE walks zero-width matches PCRE-style, not like a global ECMAScript match

Source: [CPP-C26](../../cpp-code-review.md). Report labels: [medium] [confirmed; cross-host split].

**Scenario / reported observation:**

`RREPLACE("a*?", "-", "aab")` gives cpp/php `-----b-`, js/py/lisp `-a-a-b-`. `RREPLACE("|a", "-", "aa")` gives `-----` vs `-a-a-`. `RREPLACE("(?:|a)", "<$0>", "baab")` gives `<>b<><a><><a><>b<>` vs `<>b<>a<>a<>b<>`.

**Additional fixture requirements from the report:** Suggest `re.replace.empty-then-nonempty` (`RREPLACE('a*?', "-", "aab")`, `RREPLACE('|a', "-", "aa")`) after the spec decision.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C26.

<a id="cpp-c27"></a>

### CPP-C27: Capture groups inside a repetition: ECMAScript hosts (C++, JS) vs PCRE-style hosts (PHP, Python, Lisp)

Source: [CPP-C27](../../cpp-code-review.md). Report labels: [medium] [confirmed; cross-host split].

**Scenario / reported observation:**

`RGROUPS("(?:(a)|b)+", "ab")` gives cpp/js `{"1"="ab","2"=""}`, php/py/lisp `{"1"="ab","2"="a"}`. `RREPLACE("(?:(a)|b)+", "[$1]", "ab")` gives `[]` vs `[a]`. `RGROUPS("(?:(a)|(b))*", "ab")` gives `("ab","","b")` vs `("ab","a","b")`.

**Additional fixture requirements from the report:** Suggest `re.reject.capture-in-repeat` or `re.groups.repeat-resets` once decided.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C27.

<a id="cpp-c28"></a>

### CPP-C28: Literal regex patterns are not validated at compile time

Source: [CPP-C28](../../cpp-code-review.md). Report labels: [medium] [confirmed, all five hosts].

**Scenario / reported observation:**

`cpp/build/sel -e 'IF(FALSE, RMATCH("(?=a)", "x"), 1)'` gives `1`; js, php, python and lisp also answer `1` (expected `E_REGEX_SYNTAX` at the pattern's position).

**Additional fixture requirements from the report:** (`09-regex.selt` cannot tell compile-time from run-time). Suggest a bad literal in the untaken `IF` branch with a `compile` expectation.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C28.

<a id="cpp-c49"></a>

### CPP-C49: Regex validator gaps: class escape next to `-` becomes a range; `(*VERB)`/quantifier-after-anchor acceptance differs

Source: [CPP-C49](../../cpp-code-review.md). Report labels: [low] [confirmed, all hosts; not C++ bugs].

**Scenario / reported observation:**

`RMATCH('[\s-a]', 'Z')` gives `TRUE` on all five. `RMATCH('[\w-.]', 'a')` gives `E_REGEX_SYNTAX` on all.

**Additional fixture requirements from the report:** `re.class-escape-inside-class` (`09-regex.selt:290`) does not exercise adjacent hyphens. Suggest `re.reject.class-escape-range-endpoint` and `re.reject.quantifier-on-anchor` (`^*`, `$+`, `(*ANY)`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C49.

<a id="cpp-c50"></a>

### CPP-C50: Some valid patterns are refused or very slow to compile in C++ (SRELL limits leak out as `E_REGEX_SYNTAX ... error_complexity`)

Source: [CPP-C50](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

(a) group nesting deeper than 256 is rejected with the unhelpful message `error_complexity`: `RMATCH(REPEAT('(?:',300) & 'a' & REPEAT(')',300), 'a')` is `E_REGEX_SYNTAX` in cpp and php, TRUE in js, python, lisp. (b) Compile time is super-linear in the number of groups: `(a)` x2000 is 77 ms, x4000 234 ms, x8000 1.3 s, x100000 did not finish in minutes for a 300 KB pattern. There is no pattern-length or group-count cap anywhere (SPEC §6.4 lists only the quantifier bound), so a user-supplied pattern is a CPU sink. (c) `(.{65535}){65535}` is accepted by cpp/js/py and rejected by PHP (PCRE size limit).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C50.

## LISP report scenarios

<a id="lisp-c3"></a>

### LISP-C3: cl-ppcre stack exhaustion or runaway recursion escapes as an uncaught host condition

Source: [LISP-C3](../../lisp-code-review.md). Report labels: [high] [confirmed, 2 of 4 triggers re-verified by synthesizer].

**Scenario / reported observation:**

each prints `Unhandled SB-KERNEL::CONTROL-STACK-EXHAUSTED`, exit 1; js/python/cpp print FALSE/TRUE):
  - `lisp/bin/sel -e "RMATCH('(?:a(?:x*)*?)*[!]', \"aa\")"` (re-verified)
  - `lisp/bin/sel -e "RMATCH('^(?:ab|a)*$', REPEAT('a', 30000))"` (re-verified)
  - `lisp/bin/sel -e "RMATCH('^(a){20000}$', REPEAT('a', 20000))"` (s4)
  - `lisp/bin/sel -e "RMATCH(REPEAT('(?:',50000) & 'a' & REPEAT(')',50000), 'a')"` (s4)

**Additional fixture requirements from the report:** Suggest `re.stack.long-subject-alternation` (`^(?:ab|a)*$` on 100,000 `a`), `re.stack.nullable-lazy-loop` (`(?:a(?:x*)*?)*[!]` on `aa` -> FALSE), and a nesting-depth case once the spec picks a limit.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C3.

<a id="lisp-c10"></a>

### LISP-C10: `$` immediately followed by `.*` never matches (cl-ppcre bug on `\z.*`)

Source: [LISP-C10](../../lisp-code-review.md). Report labels: [medium] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

`lisp/bin/sel -e "RFIND('$.*', \"abc\")"` -> `0` (re-verified), js -> `4` (re-verified); `RREPLACE('$.*', '!', "abc")` -> `abc` (others `abc!`); `RMATCH('$.*', "abc")` -> FALSE (others TRUE). Raw: `(cl-ppcre:scan "\\z.*" "abc")` -> NIL.

**Additional fixture requirements from the report:** Suggest `re.anchor.end-then-dotstar` (`RFIND('$.*', "abc")` -> 4; `RREPLACE('$.*', '!', "abc")` -> `abc!`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C10.

<a id="lisp-c11"></a>

### LISP-C11: A quantifier directly after `^` or `$` is accepted; the other four hosts reject it

Source: [LISP-C11](../../lisp-code-review.md). Report labels: [medium] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

`lisp/bin/sel -e "RMATCH('^*a', \"a\")"` -> `TRUE` (re-verified); js: `E_REGEX_SYNTAX at line 1 column 8` (re-verified); py/cpp/php also E_REGEX_SYNTAX. Same for `a$+`, `$?`, `^+?`, `${0}`, `^{2}a`. (`(^)*` and `(?:^)+` are valid in js/py/php; cpp rejects `(?:^)+`, see X3.)

**Additional fixture requirements from the report:** Suggest `re.reject.quantified-anchor` for `^*`, `$?`, `a$+`, `^{2}` (use raw `'...'` literals, since `{2}` inside `"..."` is interpolation).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C11.

<a id="lisp-c18"></a>

### LISP-C18: Empty-iteration and capture-reset semantics: cl-ppcre follows Perl, JS/C++ follow ECMAScript

Source: [LISP-C18](../../lisp-code-review.md). Report labels: [medium] [confirmed; cross-host, spec decision].

**Scenario / reported observation:**

five hosts): `RGROUPS('(a|)+', "a")` lisp/py/php `["a",""]`, js/cpp `["a","a"]`; `RGROUPS('(a|)*', "aa")` lisp/py/php `["aa",""]`, js/cpp `["aa","a"]`; `RGROUPS('(?:(a)|b)*', "ab")` lisp/py/php `["ab","a"]`, js/cpp `["ab",""]` (ECMAScript resets captures each iteration); `RGROUPS('(?:a*?){1,2}', "a")` lisp/py/php `[""]`, js/cpp `["a"]` (overall match differs, so RREPLACE/RFIND-length differ too).

**Additional fixture requirements from the report:** (`09-regex.selt` has no lazy-quantifier RGROUPS or nullable-loop case). Suggest `re.groups.nullable-loop`, `re.groups.capture-reset-per-iteration`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C18.

<a id="lisp-c19"></a>

### LISP-C19: No bound on regex backtracking work; hosts diverge on the outcome

Source: [LISP-C19](../../lisp-code-review.md). Report labels: [medium] [confirmed; cross-host].

**Scenario / reported observation:**

`lisp/bin/sel -e "RMATCH('^(a|aa)+$', REPEAT('a',40) & 'b')"` (about 40 s); `cpp/build/sel -e "RMATCH('^(a|aa)+\$', REPEAT('a',30) & 'b')"` (abort); `php php/bin/sel` same expression -> FALSE at once.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C19.

<a id="lisp-c37"></a>

### LISP-C37: Regex flag parsing uses `char-downcase`

Source: [LISP-C37](../../lisp-code-review.md). Report labels: [low] [confirmed, no current divergence].

**Scenario / reported observation:**

contradicts "never use the host's case mapping". Harmless today (only ASCII `I`/`M`/`S` fold to flag letters; U+0130 is left alone by SBCL 2.6.8, `RMATCH("a","A","\u{130}")` -> E_BAD_ARG like every other host) but would change with another SBCL/Unicode table. All five hosts accept an uppercase `I` although §7.8 says "`i` and nothing else": a spec nuance.

**Additional fixture requirements from the report:** partial (`re.flag.unknown`). Suggest `re.flag.non-ascii-case-variant` (U+0130, U+0131) and pin whether `I` is legal.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C37.

## GO report scenarios

<a id="go-c1"></a>

### GO-C1: Regex counted repeats above 1000 (and `{00001}`) silently become "never matches"

Source: [GO-C1](../../go-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

raw strings, because `{}` in `"..."` is interpolation):
  - `RMATCH('a{1001}', REPEAT('a',1001))` -> Go `FALSE`; js/py/cpp/php/lisp `TRUE`
  - `RMATCH('a{0,2000}','zzz')` -> Go `FALSE`; js/py `TRUE`
  - `RMATCH('^a{1001,}$', REPEAT('a',1001))` -> Go `FALSE`; js/py `TRUE`
  - `RMATCH('^(?:a{1000}){2}$', REPEAT('a',2000))` -> Go `FALSE`; js/py `TRUE`
  - `RMATCH('^(a{300}){300}$', REPEAT('a',90000))` -> Go `FALSE`; js/py `TRUE`
  - `RMATCH('a{00001}','a')` -> Go `FALSE`; js/cpp/php/lisp `TRUE`
  - `RFIND('b{2,5000}','abb')` -> Go `0`; js/py `2`

**Additional fixture requirements from the report:** Add to `09-regex.selt`: `RMATCH('^a{1001}$', REPEAT('a',1001))` TRUE; `RMATCH('^(?:a{1000}){2}$', REPEAT('a',2000))` TRUE; `RMATCH('a{0,2000}','')` TRUE; `RMATCH('a{00001}','a')` TRUE; `RMATCH('^a{65535}$', REPEAT('a',65535))` TRUE.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C1.

<a id="go-c10"></a>

### GO-C10: RREPLACE drops an empty match that abuts the previous match

Source: [GO-C10](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`RREPLACE('a*','-','baac')` (above); `RREPLACE('\s*','_','a b')` -> Go `_a_b_`, others `_a__b_`; `RREPLACE('(b*|[é-ü])','<$0|$1>','baac')` -> Go `<b|b>a<|>a<|>c<|>` vs others `<b|b><|>a<|>a<|>c<|>`.

**Additional fixture requirements from the report:** `re.replace.zero-width` (`'x*'` on "ab") does not hit the abutting case. Add `RREPLACE('a*', "-", "baac")` -> `"-b--c-"` and one with a capture group.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C10.

<a id="go-c28"></a>

### GO-C28: Quantifier on an anchor (`^*`, `$*`, `^+`) is accepted; four hosts reject it

Source: [GO-C28](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`RMATCH('^*','a')` -> Go TRUE, Lisp TRUE; JS/PHP/C++/Python `E_REGEX_SYNTAX`. Same for `$*` and `^+`.

**Additional fixture requirements from the report:** Add `RMATCH('^*','a')` -> `E_REGEX_SYNTAX` (Lisp needs the same fix).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C28.

<a id="go-c30"></a>

### GO-C30: RGROUPS capture retention across repeated groups differs by engine

Source: [GO-C30](../../go-code-review.md). Report labels: [low] [confirmed, cross-host split, not Go-specific].

**Scenario / reported observation:**

`RGROUPS('(?:(a)|b)+','ab')`: Go/PHP/Lisp/Python `{1:"ab",2:"a"}`; JS/C++ (ECMAScript reset) `{1:"ab",2:""}`. Also `RGROUPS('((a)|(b))+','ab')` group 3, `RGROUPS('(?:(a)|(b))*','ba')`. Go is in the 4:2 majority; the suite has no case.

**Additional fixture requirements from the report:** ; add a case once decided.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C30.

<a id="go-c31"></a>

### GO-C31: `\d \w \s` rewriting inside a class next to `-` makes accidental ranges

Source: [GO-C31](../../go-code-review.md). Report labels: [low] [confirmed, spec-level, all six hosts agree].

**Scenario / reported observation:**

the textual expansion is not neutral next to `-`: `[+-\d]` becomes `[+-0-9]` = range `+`..`0` then `-`,`9`, matching `,` `-` `.` `/` `9` but NOT `5`: `RMATCH('^[+-\d]$','5')` FALSE and `RMATCH('^[+-\d]$',',')` TRUE in Go, JS, C++, PHP, Lisp, Python. `[\w-z]` becomes `_-z` (matches the backtick): `RMATCH('^[\w-z]$','`')` TRUE everywhere. `[\w-.]` becomes the reversed range `_-.` -> `E_REGEX_SYNTAX`. In PCRE `-` after a shorthand is literal; in ECMAScript-u it is a syntax error.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C31.

<a id="go-c32"></a>

### GO-C32: RE2's program-size limit surfaces as E_REGEX_SYNTAX for valid patterns

Source: [GO-C32](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`RMATCH('[^a]{1000}' x 10000, 'a')` (100 KB pattern, every quantifier legal under 7.8): build with `python3 -c "print(\"RMATCH('\" + '[^a]{1000}'*10000 + \"','a')\")"`; Go -> `E_REGEX_SYNTAX ... expression too large`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C32.
