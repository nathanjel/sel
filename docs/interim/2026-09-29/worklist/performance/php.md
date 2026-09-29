# PHP performance

[Worklist](../README.md) · [Performance protocol](../07-performance.md)

Report measurements below are historical evidence, not verified targets for this machine. Recreate each workload in the repository; scratchpad paths mentioned by reviewers are not dependencies. The shared performance protocol applies to every task, including reasoned/guessed opportunities and subitems bundled in a finding.

<a id="php-p1"></a>

## PHP-P1: Every text builtin re-splits its argument into a PHP array of code points, even for O(1)/O(k) answers

- [ ] **P-PHP-P1 — Measure and address this finding.**

Source: [PHP-P1](../../php-code-review.md). Report labels: [high] [measured].

**Source target:** `Text.php:51` (LEN), 55 (LEFT), 60 (RIGHT), 67 (SUBSTR), 177 (CODE), 188 (TRIM/LTRIM/RTRIM), 213 (UPPER/LOWER), 153 (BACKWARDS), 232-249 (PADL/PADR); `Utf8::chars()` (`Utf8.php:76-88`).

**Benchmark seed / reported evidence:** measured, 1M-code-point subject, per call): LEN 227 ms (ASCII) / 383 ms (UTF-8); CODE 226 / 373; LEFT(x,3) 220 / 272; RIGHT(x,3) 283 / 250; SUBSTR(x,5,3) 353 / 241; TRIM 299 / 327; UPPER 565 / 438; BACKWARDS 296 / 327; PADL(x,2000000,'.') 1067 / 1469 ms. Short strings fine (`LEN('hello world')` 0.02 ms). Beyond 4.19M code points fatal. Byte-level LEN is ~1 ms/MB.

**Implementation experiment:** all byte-identical): LEN = count non-continuation bytes with `preg_match_all('/[^\x80-\xBF]/', $s)` (no `u` flag) or ASCII fast path; LEFT/SUBSTR/RIGHT/CODE walk lead bytes for k code points from the start (end for RIGHT), O(k); UPPER/LOWER `strtr($s, 'abc...z', 'ABC...Z')` (~1 ms/MB, cannot touch multibyte); TRIM `ltrim`/`rtrim` with " \t\r\n"; PADL/PADR `str_repeat($fill, intdiv($need, $k))` + code-point slice; BACKWARDS stays O(n). For `Utf8::chars` itself (slice 1 P4): `if (!preg_match('/[\x80-\xff]/', $s)) return strlen($s) === 0 ? [] : str_split($s);` (keep the empty guard for PHP < 8.2): 2,967-byte source chars() 0.51 ms vs `str_split` 0.13 ms; do not use `preg_split('//u')` (1.29 ms).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p2"></a>

## PHP-P2: FIND / REPLACE / SPLIT use a pure-PHP O(n*m) scan over code-point arrays

- [ ] **P-PHP-P2 — Measure and address this finding.**

Source: [PHP-P2](../../php-code-review.md). Report labels: [high] [measured].

**Source target:** `Text.php:23-40` (`indexOfCp`), used by FIND (94), REPLACE (108), SPLIT (128).

**Benchmark seed / reported evidence:** php -d memory_limit=2G): `FIND(N,H)`, H = 'a' x 2n, N = 'a' x n + 'b': n=10,000 6.3 s; n=20,000 22.7 s; n=40,000 (H=80k) 77 s. JS 0.39/1.45/5.4 s; Python 1 ms. A 40 KB input pins a worker for >20 s (DoS-shaped). Non-adversarial: `FIND('b', <1M 'a'>)` 277 ms, `REPLACE('a','bb',<300k 'a'>)` 344 ms, `SPLIT(<300k>,'a')` 666 ms.

**Implementation experiment:** valid UTF-8 substring search on bytes is exact (self-synchronising encoding), so use `strpos($hay, $needle, $byteOffset)`, converting offsets with a code-point count only when a result is needed; `$from` mapped to a byte offset by walking `$from` code points; REPLACE/SPLIT via `strpos` loops or `str_replace`/`explode` for non-empty needles. Byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p3"></a>

## PHP-P3: SORT / SORT_BY re-classify each key on every comparison, and every non-numeric text key builds and catches an exception

- [ ] **P-PHP-P3 — Measure and address this finding.**

Source: [PHP-P3](../../php-code-review.md). Report labels: [high] [measured].

**Source target:** `Core.php:207-239` (`compareValues`), `usort` at 313-319; `Value::looksNumeric` (`Value.php:826`).

**Benchmark seed / reported evidence:** measured): sorting 100,000 random values end to end: SORT of ints 4.87 s; SORT of `'k'.n` text 13.9 s; SORT_BY on a record field 5.8 s / 13.2 s (context ingestion alone 0.69 s). `looksNumeric()` on non-numeric text is 2.7 us/call vs 0.4 us with a cached decimal, because `asDecimal()` calls `fail()` (SelError with trace and json_encode'd message), catches it and caches nothing. Prototype classifying each key once (null flag, decimal or bytes) and sorting the decorated array with the same rules: 1.16 s for ints (vs 3.68 s same data) and 1.35 s for text (vs 8.56 s), i.e. 3.2x and 6.4x.

**Implementation experiment:** in `doSort` decorate each element once with `[isNull, decimalOrNull, bytesOrNull, kind, idx]` and compare those; make `Value::looksNumeric` not throw (parse without `fail()`) and memoise a negative result. Revisit with PHP-C22 first (a total order simplifies the comparator).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p4"></a>

## PHP-P4: Non-GMP long division is a per-digit repeated-subtraction loop

- [ ] **P-PHP-P4 — Measure and address this finding.**

Source: [PHP-P4](../../php-code-review.md). Report labels: [high without ext-gmp, none with it] [measured].

**Source target:** `Dec.php:303-314` (`divModAbs` schoolbook; `:286-301` covers only divisors <= 9 digits).

**Benchmark seed / reported evidence:** no gmp, `Dec::div`): 20 digits / 10 digits 42.6 us (GMP 3.0); 60/30 328 us; 200/100 2.2 ms; 1000/500 45 ms (GMP 25 us); 4000/2000 2.1 s; 8000/4000 8.3 s; 16000/8000 32.6 s (quadratic, x4 per doubling). `tools/benchmark-php-runtime.php` `decimal.large.div` = 171 us without gmp vs 6 us with. `/` first multiplies the dividend by 10^10, so ordinary 10-18-digit divisors hit it. A 16 KB rule input holds the process 30+ s (DoS).

**Implementation experiment:** Knuth algorithm D over the base-1e7 limbs `mulAbs` builds (O(la*lb/49) instead of O(la*lb*~5) with string churn); or keep the digit loop but estimate the digit from the top 15 digits with int math and drop the `cmpAbs` loop. Integer-only.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p5"></a>

## PHP-P5: Non-GMP multiply (and so POWER) is quadratic, no Karatsuba

- [ ] **P-PHP-P5 — Measure and address this finding.**

Source: [PHP-P5](../../php-code-review.md). Report labels: [high without ext-gmp, none with it] [measured].

**Source target:** `Dec.php:197-240`.

**Benchmark seed / reported evidence:** no gmp): 10k digits squared 142 ms, 30k 0.99 s, 100k 12.9 s (gmp 4/7/64 ms). `POWER(99, 99999)` 24 s; `POWER(1.0000001, 100000)` (legal: 700,002 digits) 168 s without gmp vs 0.80 s with (JS 1.3 s, Python 1.7 s). `POWER(9999999999, 99999)` (999,990 digits) 1.4 s with gmp.

**Implementation experiment:** Karatsuba above ~40 limbs on the existing limb arrays, and let `power` square through it. If ext-gmp is effectively required for the 1M-digit caps, say so in `docs/usage` (PHP section): the caps advertise sizes the fallback cannot serve.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p6"></a>

## PHP-P6: Equi-join key extraction refuses any key expression mentioning a variable other than the binders, dropping LINK to O(n*m)

- [ ] **P-PHP-P6 — Measure and address this finding.**

Source: [PHP-P6](../../php-code-review.md). Report labels: [high] [measured; cross-host design].

**Source target:** `Structure.php:83-99` (`exprDependsOnlyOn`, `default`/`'var'` case), `111-127` (`tryExtractEquiKeys`).

**Benchmark seed / reported evidence:** 300 rows each: 4.1 ms with `A["id"]==B["id"]`, 1416 ms with `A["id"]+K==B["id"]`; 600 rows each 5.5 ms vs 5395 ms CPU (~1000x, quadratic). Same result.

**Implementation experiment:** allow variables not bound by this LINK (and not assigned in the predicate) as constants in `exprDependsOnlyOn`, provided the key expression has no `assign` node and its sub-calls are pure (`pureSource()` exists). Evaluation order and error positions unchanged (right keys first, then `checkJoinPair`).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p7"></a>

## PHP-P7: assignment-chain complexity — covered by correctness work

No second implementation task: [C-PHP-C14](../02-php-correctness.md#c-php-c14) owns the identical `array_unshift` fix. Add the reported 40000-index workload and its n/2n/4n scaling measurements to that task’s acceptance evidence. Run the equivalent bounded workload on every lane.

<a id="php-p8"></a>

## PHP-P8: The left fold that unrolls IN lists and static aggregates is quadratic

- [ ] **P-PHP-P8 — Measure and address this finding.**

Source: [PHP-P8](../../php-code-review.md). Report labels: [high] [measured].

**Source target:** `Sql/Translator.php:674-682` (`foldPairwise`); each step through `apply` -> `Emit::fill` (`Emit.php:331-339`, `splice`).

**Benchmark seed / reported evidence:** `php6/p1.php`, mariadb, `T IN ("k1", ..., "kN")`): N=500 0.13 s; 1000 0.47 s; 2000 1.5 s; 4000 4.7 s; 8000 20.3 s (each doubling ~3.5-4.3x). Rendering is 0.03 s; `Sel::compile` of the 4000-element list 0.07-0.1 s. A patched `splice` using `array_push(...array_slice())` still takes 2.2 s at N=4000, so the copying is the bulk. Serves ALL/ANY/SUM/JOIN over `value` lists and columns bindings too.

**Implementation experiment:** build the left-nested chain in one pass: split the template around `{0}`/`{1}` once (prefix P, middle M, suffix S), emit `P*(n-1) e1 (M e_i S)...` so the bytes are identical to the pairwise-left fold; or keep an append-only builder for the accumulator. Output must stay pairwise-left (`sql-translation.md` 7.1). See PHP-C58 for the depth-1000 SQLite limit that motivates a balanced fold above a threshold.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p9"></a>

## PHP-P9: Tokenizer is a per-character interpreted loop (~70% of front-end time)

- [ ] **P-PHP-P9 — Measure and address this finding.**

Source: [PHP-P9](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Lexer.php:112-177` and helpers; `179-198` (`matchOperator`); `Utf8.php:21-66` (`validate`); `Lexer.php:50-54, 316, 355, 377`; `Parser.php:135-141, 386`.

**Benchmark seed / reported evidence:** measured): full compile of a 3 KB program ~4.2 ms tokenize vs ~1.4 ms parse; ~1.2 us per source byte. A single anchored `preg_match_all` token alternation over the same source takes 0.93 ms for all 986 matches, ~4x cheaper than the whole lexer. Negative result: swapping isDigit/isAlpha string comparisons for a char-class table did NOT speed up the lexer end to end. - `matchOperator` scans all 31 operators for every operator token: a first-byte dispatch table (`self::$byFirst[$chars[$i]]`) gave `examples/lib/tickets-generate.sel` (3 KB) 4.3 -> 2.8 ms, `order-validation.sel` (1.3 KB) 1.5 -> 1.15 ms (~25-35% of lexing; token streams byte-identical by serialize+md5; noisy box, direction stable over 6 runs). - `Utf8::validate` is a PHP byte loop; `preg_match('//u', $s) === 1` is exactly "valid UTF-8" (837,420 crafted + random strings, zero mismatches); `Value::checkText` already uses it. 2,967-byte source: 0.144 ms vs 0.001 ms; 85 KB: 6.6 ms vs ~0.02 ms (fall through to the hand loop only on failure, see PHP-C42). pcre.jit is Off on the reviewer's box.

**Implementation experiment:** for the ASCII, non-string parts use `strspn`/`preg_match('/\G.../A')` on the byte string to take whole identifier, number, whitespace and comment runs (ASCII byte offset == code point offset; keep the existing path when non-ASCII bytes exist; keep ASCII-only `\d`/`\w` semantics with explicit classes); first-byte operator dispatch; `preg_match('//u')` fast path in `Utf8::validate`/Lexer ctor; `Utf8::chars` ASCII fast path (PHP-P1); lazy `posAt()` in matchBrace/skipQuoted/skipRaw (only on failure; O(d^2) allocations in the nested case, see PHP-C5); `strpos` loop for lineStarts; hoist `infixOps()/infixWords()` and give `ASSIGN_OPS` an "assign" flag in the table (parse is only ~25% of front-end time, small effect).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p10"></a>

## PHP-P10: Tokens and AST nodes are hash arrays: ~400 B per token, ~560 B per node

- [ ] **P-PHP-P10 — Measure and address this finding.**

Source: [PHP-P10](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Lexer.php:107, 144, 154, 170` (`['type'=>..,'value'=>..] + $pos`), Parser node arrays with `'pos' => $t`.

**Benchmark seed / reported evidence:** memory_get_usage): `1` + `+1`*100000 (200 KB, 200,002 tokens): lexing 79.4 MB = 397 B/token (and per source byte in that dense case), parsing ~56 MB for 50K operators (~560 B/node); at 100K operators parse dies at 128M. Alternatives measured: 5-key assoc+merge 397 B, `final class Tok` with 5 promoted props 159 B, packed list 237 B (2.5x smaller for an object token).

**Implementation experiment:** token as a small final class (type, value, offset; line/col computed lazily by `fail()`/pos accessor via the lineStarts table) or at least a packed list; a refactor of `$t['line']`-style consumers (`fail()`, Parser `'pos'`). Independently a source-size/token-count guard turning "fatal" into a SelError. Byte-identical results.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p11"></a>

## PHP-P11: Literals and internal results are re-validated: `Value::text` UTF-8 check on every literal evaluation

- [ ] **P-PHP-P11 — Measure and address this finding.**

Source: [PHP-P11](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Value.php:236-240, 253-258`; `Evaluator.php:148-156` (`case 'num'`/`'text'`); `concat` (valid + valid is valid); `Value::set` (`checkText($key)` on every write).

**Benchmark seed / reported evidence:** `evalNode` on the literal `7` 795 ns and on `"abc"` 772 ns; with a scratch `Value::textTrusted` 497 and 419 ns (~40-45%). `Value::text('active')` 679 ns, of which `preg_match('//u')` is 279-303 ns (`mb_check_encoding` is ~3x cheaper; pcre.jit=Off); `set()` on an existing key 444 ns. In `MAP(N, ...)` over 200k elements each literal is re-created and re-validated per element.

**Implementation experiment:** internal `Value::textTrusted(string)` / private-constructor factory for parser literals, `Dec` output (digits only), ASCII-only builtin results and concat of two TEXT values; in `set` skip `checkText` when the key already exists in the shape/map. Validation stays for host input and byte-cutting builtins.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p12"></a>

## PHP-P12: Internal decimal results are re-validated by `Value::num(array)` -> `Dec::checked`

- [ ] **P-PHP-P12 — Measure and address this finding.**

Source: [PHP-P12](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Value.php:283-289`, `Dec.php:445-459`; callers `Evaluator.php:141` (every math-plan result), `:271`, `:314-318`, `:442-448`, `Number.php` (ABS..MAX), `Core.php:547` (SUM).

**Benchmark seed / reported evidence:** `Dec::checked(canonical)` 1080 ns (slice 2) / 591 ns (slice 3, tiny result); `Value::num(Dec::add(..))` 4106 ns vs `Dec::add` alone 1931 ns (~a quarter of each numeric result); `Value::num(array)` 869 ns vs `Dec::mul` 975 ns; scratch `Value::numTrusted` took `x * 2 + 1` from 4196 to 3707 ns (12%, noisy). `strspn` adds an O(n) pass for 1M-digit results.

**Implementation experiment:** package-private `Value::fromDec(array)` / `numTrusted` (`new self(TEXT, null)` plus `decVal = $d`) for Evaluator/Number/Core; keep `num()` validating for host input (HOST-13/14). Note the interplay with PHP-C40 (forged native cache).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p13"></a>

## PHP-P13: `Dec::div` has no native fast path for small operands

- [ ] **P-PHP-P13 — Measure and address this finding.**

Source: [PHP-P13](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Dec.php:720-745`.

**Benchmark seed / reported evidence:** prototype (`php2/DecX.php`, 20-line branch): 3427 ns -> 1488 ns per `123.45 / 67.89` (2.3x); output identical on 200,000 random pairs (19-digit mantissas, scales 0-12, both signs, exact and inexact quotients, ties at the 11th digit). Guards: `x <= intdiv(PHP_INT_MAX, p1)` and `y <= intdiv(PHP_INT_MAX>>1, p2)` (keeps `2r >= D` from overflowing); exclude PHP_INT_MIN mantissas; result via `fromIntFast`. `Dec::mod` has the same shape, measured only 23% faster (optional).

**Implementation experiment:** Found by: slice 2 P4. Plan status: new. - Where: `Dec.php:720-745`. - Evidence: prototype (`php2/DecX.php`, 20-line branch): 3427 ns -> 1488 ns per `123.45 / 67.89` (2.3x); output identical on 200,000 random pairs (19-digit mantissas, scales 0-12, both signs, exact and inexact quotients, ties at the 11th digit). Guards: `x <= intdiv(PHP_INT_MAX, p1)` and `y <= intdiv(PHP_INT_MAX>>1, p2)` (keeps `2r >= D` from overflowing); exclude PHP_INT_MIN mantissas; result via `fromIntFast`. `Dec::mod` has the same shape, measured only 23% faster (optional).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p14"></a>

## PHP-P14: `cmpAbs` compares digit strings with `<` (PHP numeric-string comparison)

- [ ] **P-PHP-P14 — Measure and address this finding.**

Source: [PHP-P14](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Dec.php:63` (`$a === $b ? 0 : ($a < $b ? -1 : 1)`).

**Benchmark seed / reported evidence:** 24-digit strings `<` 777 ns vs `strcmp` 79 ns; 301-digit strings 3.5 us vs 0.18 us (1M iterations, best of 7).

**Implementation experiment:** `$c = strcmp($a, $b); return $c < 0 ? -1 : ($c > 0 ? 1 : 0);` (equal lengths already established; result identical).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p15"></a>

## PHP-P15: `Dec::parse` can take a strspn-based short path (2x)

- [ ] **P-PHP-P15 — Measure and address this finding.**

Source: [PHP-P15](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Dec.php:478-502`.

**Benchmark seed / reported evidence:** `parse("123.45")` 1124 ns today (the `preg_match` is 270 ns of it). A path for strlen <= 18 (`strspn` validation + `ltrim` + building the six-key array directly with the native mantissa, no `guard`/`make`/`parseMantissa`) measured 615 ns and returned identical arrays (`!==`, including key order) for 3000 random + 20 edge strings incl. "-0", "007", "5.", ".5", "5\n", " 1". Hoisting `(string)PHP_INT_MAX` out of `parseMantissa` gave 2%, not worth it. 18 digits can never trip either digit cap so `guard` is skipped. Reference: `php2/parsefast.php`.

**Implementation experiment:** Found by: slice 2 P5. Plan status: new. - Where: `Dec.php:478-502`. - Evidence: `parse("123.45")` 1124 ns today (the `preg_match` is 270 ns of it). A path for strlen <= 18 (`strspn` validation + `ltrim` + building the six-key array directly with the native mantissa, no `guard`/`make`/`parseMantissa`) measured 615 ns and returned identical arrays (`!==`, including key order) for 3000 random + 20 edge strings incl. "-0", "007", "5.", ".5", "5\n", " 1". Hoisting `(string)PHP_INT_MAX` out of `parseMantissa` gave 2%, not worth it. 18 digits can never trip either digit cap so `guard` is skipped. Reference: `php2/parsefast.php`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p16"></a>

## PHP-P16: `Value::copy()` of a keyed list re-validates every key and every element

- [ ] **P-PHP-P16 — Measure and address this finding.**

Source: [PHP-P16](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Value.php:881-887` (`copyAt` list branch -> `self::list($values, $this->listKeys)`), `:308-339`.

**Benchmark seed / reported evidence:** `php2/copycost.php`): copy of n=100 plain list 29.7 us vs keyed 62.1 us; n=1000 278 us vs 674 us (2.4x); n=3 1.4 vs 2.7 us. `list()` runs an `instanceof` pass over all values and, with keys, `checkText` (`preg_match('//u')`) and a `$seen` duplicate table per key; `copyAt` calls it on every assignment/`,`/aggregate collect of a keyed list (FILTER results keep keys).

**Implementation experiment:** build the copy through a private constructor that assigns `storage`/`listKeys` directly (as `fromShape` does).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p17"></a>

## PHP-P17: Cached `decVal` costs ~410 B per numeric Value (plan item 4.4 not realised)

- [ ] **P-PHP-P17 — Measure and address this finding.**

Source: [PHP-P17](../../php-code-review.md). Report labels: [medium for memory] [measured].

**Source target:** `Dec.php:405-411` (neg, digits, scale, native, nativeDigits, nativeNeg), `Value.php:814-822`.

**Benchmark seed / reported evidence:** 50,000 rows `['AMOUNT'=>'12.34']`: 706 B/row after `fromNative`; after one `SUM(ROWS,r,r["AMOUNT"])` +408 B/row retained (+58%). Repo's own benchmark: 434 B per array vs 167 B for a 4-property object; PHP hash tables have an 8-slot minimum so dropping keys does not help.

**Implementation experiment:** final class `DecVal {bool $neg; string $digits; int $scale; ?int $native}` (readonly), or cache only `native`+`scale` and re-derive digits lazily; `nativeDigits/nativeNeg` become unnecessary (PHP-C40 disappears with them).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p18"></a>

## PHP-P18: RREPLACE memory and constant factors

- [ ] **P-PHP-P18 — Measure and address this finding.**

Source: [PHP-P18](../../php-code-review.md). Report labels: [medium] [reasoned / measured constants].

**Source target:** `Regex.php:442-463`: nested array per match then walked again; `substr` + `.=` per match. At 100k matches 230-566 ms per MB-size subject.

**Implementation experiment:** streaming loop as in PHP-C20, `$out` as an array of pieces joined once.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p19"></a>

## PHP-P19: ENCODE_BASE64 / DECODE_BASE64 / CRC32 are byte-at-a-time PHP loops

- [ ] **P-PHP-P19 — Measure and address this finding.**

Source: [PHP-P19](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Binary.php:50-63, 66-107, 112-121`.

**Benchmark seed / reported evidence:** 1 MB): ENCODE_BASE64 ~218 ms vs native `base64_encode` 0.31 ms; DECODE_BASE64 (1.3 MB) 400 ms vs `base64_decode` 1.09 ms; CRC32 83 ms vs `crc32()` 0.11 ms. `array_flip(str_split(self::B64))` is rebuilt on every decode call.

**Implementation experiment:** keep strict validation as one anchored byte regex (`^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$`, no `u`), then `base64_decode`; PHP decodes non-canonical trailing bits like the hand loop (`QR==` -> `A` in both). `base64_encode` and `crc32()` match the required variants (CRC-32/ISO-HDLC, standard padded alphabet). Or, if hand-written versions are preferred on principle, hoist the flip table to a static and skip per-character `str_split`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p20"></a>

## PHP-P20: LINK compiles a new plan with `eval()` for every LINK invocation and shape pair (~60 us each), never cached across calls

- [ ] **P-PHP-P20 — Measure and address this finding.**

Source: [PHP-P20](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Structure.php:617-715` (`compileSpecializedJoinProjector`), called from `makeJoinProjector` (`:717-803`).

**Benchmark seed / reported evidence:** an `eval` of a plan-sized string measures 60 us CPU. `MAP(K, COUNT(LINK(R,S,A,B,A["id"]==B["id"])))` with 20x20 rows runs ~250 us per iteration vs 72 us for the equivalent FILTER body; at least one plan (matched) and often two (LINK_LEFT with unmatched rows) are compiled each time. `eval` also defeats opcache and needs eval to be permitted.

**Implementation experiment:** cache by the generated code string (or `(ops, slots, lnested, rkept)`) a factory closure `static function($shape,$rshape){ return [...]; }` in a process-static array (capped like `RecordShape::$cache`), bound to shapes by call. Results identical. (eval codegen interpolates only integer slot numbers: no injection surface.)

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p21"></a>

## PHP-P21: Non-equi (nested-loop) LINK re-aliases every right row for every left row

- [ ] **P-PHP-P21 — Measure and address this finding.**

Source: [PHP-P21](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Structure.php:1562-1598`.

**Benchmark seed / reported evidence:** `$aliasRight($rightItem)` (new Value + storage copy) runs inside the inner `forEachElement`, so m right rows are aliased n times; per pair also `strtolower($b1)`/`strtolower($b2)`, six `setFrameValue` calls, and a mirror into a second local `$frame` nothing reads. 500x500 pairs with predicate FALSE: 3.8 us CPU per pair with named binders vs 2.5 us with `_1,_2` (aliasing ~1.3 us). Realistic predicates (`A["id"]+B["id"]==100`) ~17 us per pair dominated by the evaluator, so 10-20% of a slow path.

**Implementation experiment:** pre-alias the right side once (safe when the predicate has no assignment; `pureSource($predicate)` decides), hoist `strtolower` and second-frame writes out of the loops, drop the dead `$frame[...]` mirror.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p22"></a>

## PHP-P22: `Hybrid::execute` deep-copies the whole caller context on every call

- [ ] **P-PHP-P22 — Measure and address this finding.**

Source: [PHP-P22](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Sql/Hybrid.php:945`.

**Benchmark seed / reported evidence:** `Value::copy()` of a context holding one 20k-row table = 111 ms (~5.5 us/row) on top of the query, per execution; the continuation usually reads only `_INPUT` and a few small relations.

**Implementation experiment:** shallow root aliasing the caller's top-level children and owning only `_INPUT` (safe as long as continuation assignments go to the fresh root; copy-on-write per top-level key if aliased mutation of nested values must be prevented).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p23"></a>

## PHP-P23: `Emit::fill` copies templates one character at a time through a closure (6x)

- [ ] **P-PHP-P23 — Measure and address this finding.**

Source: [PHP-P23](../../php-code-review.md). Report labels: [medium] [measured].

**Source target:** `Sql/Emit.php:316-437` (the `$push($tpl[$i])` branch at 364-368); `Translator.php:2900-2907` (`fillNamed`).

**Benchmark seed / reported evidence:** 60-char template `fill()` = 36.5 us per call; with a `strcspn($tpl,'{}',$i)` run-copy 5.9 us (6x). `translateStatement` of a ~300-node FILTER 19.7-20.7 ms -> 15.0-18.5 ms (best of 7, twice); emitted SQL byte-identical (md5 equal on sqlite/mariadb/postgresql). Slice 6 (instrumented): `Emit::fill` ~24-31% of translate time.

**Implementation experiment:** when `$tpl[$i]` is not `{`/`}`, `$run = strcspn($tpl,'{}',$i)` and `$push(substr($tpl,$i,$run))`; better, a parsed-template cache (segments plus slot indexes) per dialect.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p24"></a>

## PHP-P24: Template lookup rebuilds the dialect chain per node; `textLiteral` re-sorts its escape keys per literal

- [ ] **P-PHP-P24 — Measure and address this finding.**

Source: [PHP-P24](../../php-code-review.md). Report labels: [low-medium] [measured].

**Source target:** `Sql/Map.php:267-277, 290-304, 380-404` (`chain`/`entry`/`lexical`); `Emit.php:140-153`.

**Benchmark seed / reported evidence:** a 10-node expression does ~21 `Map::entry` and 26 `Map::lexical` calls, each rebuilding the chain (`in_array`, `exists`, `record`): entry+lexical ~17% of translate time, `Constants::isConstant` ~3.5% (0.7-0.9 ms expression). Caching `Map::chain` per dialect plus `strcspn`-chunked pushing took a translation from 0.63-0.79 ms to 0.55-0.61 ms (10-25%, noisy). Micro: `Map::chain` 2.2 us, `entry` 2.7 us, `lexical` 3.5 us, `Emit::textLiteral` 6.7 us (usort + array_combine + strtr every time; `strtr` with an array already applies longest-first, so the usort is redundant).

**Implementation experiment:** memoise `chain($dialect)` and the resolved `textEscape` map per dialect, invalidated on `defineDialect`/`define`/`reset` (the same points that clear `$guardChecked`); drop the usort.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p25"></a>

## PHP-P25: `structuralHash` allocates a HashContext even for scalar leaves

- [ ] **P-PHP-P25 — Measure and address this finding.**

Source: [PHP-P25](../../php-code-review.md). Report labels: [low-medium] [measured].

**Source target:** `Value.php:907-912`; users DISTINCT (`Core.php:168`), DEDUPE (`Structure.php:31`), BUCKET (`Structure.php:1766`).

**Benchmark seed / reported evidence:** 763 ns per text scalar vs 169 ns for a plain `kind.':'.scalar` key (slice 2); 200k text items: DEDUPE 613 ms and bare BUCKET 843 ms, hashing ~1 us of ~3-4 us per element (slice 5); records ~7 us per 4-field record. `$buckets[$hash][] = $item` creates a one-element array per distinct hash (376+ B each).

**Implementation experiment:** for a TEXT/BIN/BOOL value with no children return `kind:len:scalar` directly, keep xxh3 only for containers (buckets confirm with `eql()`, so any injective key is safe); store a single value in the bucket, upgrading to a list only on collision.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p26"></a>

## PHP-P26: Sort/heap costs in TOP: `_K` bound for every row; heap used when limit >= rows

- [ ] **P-PHP-P26 — Measure and address this finding.**

Source: [PHP-P26](../../php-code-review.md). Report labels: [low-medium] [measured].

**Source target:** `Structure.php:1680-1683` (`Value::text($key)`, two `setFrameValue`, mirror write) and `1644-1707`.

**Benchmark seed / reported evidence:** `TOP(R,_["v"],10)` ~6 us per row vs 1.4 us for `MAP(R,_["v"])` on 200k rows, ~1.1 us of it `_K` (rest is `Core::compareValues`). 50k rows `TOP(R,_["v"],25000)` 1.68 s vs `SORT_BY(R,_["v"])` 1.44 s (loaded box, indicative).

**Implementation experiment:** `$needsK = Core::containsVar($body, '_K')` as in `doBucket`; when `$limit >= size` skip the heap and go straight to one `usort` on decorated keys (ties still break by `idx`, result identical).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p27"></a>

## PHP-P27: `Dec` SUM accumulates through full six-key descriptors

- [ ] **P-PHP-P27 — Measure and address this finding.**

Source: [PHP-P27](../../php-code-review.md). Report labels: [low-medium] [measured].

**Source target:** `Builtins/Core.php:541-547` (driven by `Dec::add`).

**Benchmark seed / reported evidence:** 100k `Dec::add` chain 109 ms (1.09 us/add) vs a native-int accumulator 11.7 ms when scales are equal and mantissas native; end to end SUM over 50k rows 2.5 us/row warm, ~40% of it. Sketch: `Dec::sumInto(&$acc, $d)` staying in an int until scale changes or `is_int` fails, then materialise once.

**Implementation experiment:** Found by: slice 2 P11. Plan status: new. - Where: `Builtins/Core.php:541-547` (driven by `Dec::add`). - Evidence: 100k `Dec::add` chain 109 ms (1.09 us/add) vs a native-int accumulator 11.7 ms when scales are equal and mantissas native; end to end SUM over 50k rows 2.5 us/row warm, ~40% of it. Sketch: `Dec::sumInto(&$acc, $d)` staying in an int until scale changes or `is_int` fails, then materialise once.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p28"></a>

## PHP-P28: Per-node overhead in `evalNode`: try/finally plus a second dispatch call

- [ ] **P-PHP-P28 — Measure and address this finding.**

Source: [PHP-P28](../../php-code-review.md). Report labels: [low-medium] [measured].

**Source target:** `Evaluator.php:25-39`.

**Benchmark seed / reported evidence:** `evalNode` on `TRUE` 275 ns vs 70 ns for `Value::bool` alone (~200 ns framework per node). For `COUNT(MAP(N,_))` over 200k elements the floor is ~1.2 us/element (loaded 16-thread box).

**Implementation experiment:** expect 5-10%, unverified): only `??` and builtins that catch a SelError need depth restored - save `$ctx->depth` at those catch sites (`Structure.php` 186/221/230/1345/1431, `Value.php:838`, `Evaluator.php:296`) and drop the `finally`; move the `mathPlan` check to the parser/optimiser with a distinct node type.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p29"></a>

## PHP-P29: The optimiser costs more than it saves for one-shot, non-pipeline rules; constant folding stops short of text `&`

- [ ] **P-PHP-P29 — Measure and address this finding.**

Source: [PHP-P29](../../php-code-review.md). Report labels: [low] [measured].

**Source target:** `Sel.php:80-112` (`run` builds the physical tree on first use); `Optimizer.php:213-294` (`foldNode`).

**Benchmark seed / reported evidence:** small rule `X * 2 + 1 > 10 AND S $== "a"`: parse 95 us, `Optimizer::optimize` 34 us, eval physical 14.4 us vs plain 21.3 us (saves 7 us/run, break-even after 5 runs); medium rule optimize 81 us vs 7 us saved (break-even after 11 runs). `Sel::evaluate()` is one-shot. `"abc" & "def"` (two TEXT literals) is 3.0-3.5 us per element in a MAP; folding to a `text` node is exactly equivalent and cannot fail.

**Implementation experiment:** skip `optimize` in `Sel::evaluate` when the tree has no pipeline call and no mathPlan candidate (byte-identical only once PHP-C13 is fixed); add `'&'` with two `text` literal children to `foldNode`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="php-p30"></a>

## PHP-P30: Assorted small items

- [ ] **P-PHP-P30 — Measure and address this finding.**

Source: [PHP-P30](../../php-code-review.md). Report labels: [low] [measured / reasoned].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Implementation experiment:** Plan status: new. - Regex compile cache is unbounded and the `i` flag re-scans the pattern per call (`Regex.php:48-49, 327-351`): 100,000 distinct dynamic patterns retain ~14 MB in `self::$cache` and take 3.07 s to compile (30 us each); PCRE's own cache holds 4,096 patterns, so a second pass over 100k still costs 1.25 s. Bound the map and do the ASCII check after the cache lookup keyed on flags+pattern (slice 4 P6, measured). - `Text.php` trim (187) builds a `$space` array per call and uses `in_array` per character; `Regex::escapeDelimiter` re-splits the whole pattern into characters even without `/` (`strpos` first) (slice 4 P7, reasoned). - `Structure::canonicalDecimalKey` / `canonicalJoinKey` allocate per row (`:149-233`); measured join speed is fine (50k x 50k equi-join ~370 ms) (slice 5 P7, reasoned, low value). - LINK pre-renders the whole plan just to surface earlier refusals first (`Translator.php:3557-3560`): linear in pipeline size per LINK (slice 6 P3, reasoned). - `planHybrid` prefix search is O(steps^2) when the first unsupported step is early (`Hybrid.php:177-201`, each failed candidate re-runs `Normalise::run` + a full `translateStatement`): alternating TAKE/DROP with an unsupported FILTER at step 3: n=10 4 ms, n=40 21 ms, n=80 78 ms, n=120 125 ms (one translate of the supported steps 0.7/2.4/6.9/7.1 ms). Binary-search the split or use the refusal position to jump below it; repeated tree walks in `plan()` (`tables` closure re-runs Normalise, `referencedAssignments`, `fieldReferences`) are worth fixing together (slice 7 P3, measured; P5, reasoned).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.
