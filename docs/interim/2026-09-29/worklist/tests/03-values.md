# T03: Value ownership, mutation and host conversion

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [ ] **T03 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

**Destination:** `conformance/04-values.selt, conformance/21-structural-hash-identity.selt, tools/api.* and per-host runtime tests`.

**Required cases and assertions:** Warm decimal/hash/shape caches, mutate values, then compare scalar text, arithmetic, EQL, hash, dump and iteration order. Exercise comma collection, LIST/RECORD, assignment, nested children, native input and returned results. Use two independent contexts/programs in the same process to expose shared BOOL or shape poisoning; use widths 15/16/17 and numeric-looking/NUL/empty keys. Probe sparse arrays, unsupported host objects, false-like contexts, malformed descriptors and reordered native object keys via equivalent host API adapters. Test path-depth plus assigned-subtree depth at the cap, and copying versus aliasing through observable later mutation.

**Contract prerequisite:** Decide permitted native types, native key-order guarantees and copy/alias boundaries in the spec/API documentation. Keep host-only representations in adapter tests; the equivalent SEL behavior must still run in all six lanes.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c21"></a>

### JS-C21: `Value.fromNative` on a sparse array builds a corrupt list; later use throws raw TypeError

Source: [JS-C21](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`compile('L, 5').run({L:[1,,3]})` throws `TypeError: Cannot read properties of undefined (reading 'clone')`; `DEDUPE(L)` and `JOIN(L, ",")` throw `TypeError: .for is not iterable`; `SORT(L)` returns a dump with a dangling comma. `undefined` inside an ordinary array is fine (NULL); only holes break.

**Additional fixture requirements from the report:** (api probes do not use a sparse array).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C21.

<a id="js-c34"></a>

### JS-C34: `fromNative` silently turns unsupported objects into NULL/records instead of E_BAD_ARG

Source: [JS-C34](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`Value.fromNative(new Date()).dump()` gives `-`; `Value.fromNative(new Map([[1,2]])).dump()` gives `-`; `Value.fromNative(new Int8Array([1,2])).dump()` gives a record `-{"0"=t"1", "1"=t"2"}`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C34.

<a id="js-c35"></a>

### JS-C35: JS `fromNative` accepts fractional numbers; PHP and Python refuse floats

Source: [JS-C35](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`Value.fromNative(0.1+0.2).scalar` gives `0.30000000000000004` (JS); PHP `Value::fromNative(0.5)` and Python `Value.from_native(0.5)` give E_BAD_ARG.

**Additional fixture requirements from the report:** tools/api probes do not cover floats; add one on all hosts.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C35.

<a id="js-c36"></a>

### JS-C36: `toNative` then `fromNative` is not an inverse for records whose keys look like array indices

Source: [JS-C36](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`v = RECORD("b", 1, "2", 2, "a", 3)`; `v.dump()` is `-{"b"=t"1", "2"=t"2", "a"=t"3"}`, `Value.fromNative(v.toNative()).dump()` is `-{"2"=t"2", "b"=t"1", "a"=t"3"}`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C36.

<a id="js-c37"></a>

### JS-C37: `Value.scalar` setter leaves a stale cached decimal

Source: [JS-C37](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`w = Value.num('12'); w.scalar = 'abc'; w.looksNumeric()` gives `true` (expected false).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C37.

## PHP report scenarios

<a id="php-c1"></a>

### PHP-C1: Shared `Value::bool()` flyweights are mutable; one program can poison `TRUE`/`FALSE` process-wide

Source: [PHP-C1](../../php-code-review.md). Report labels: [high] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

host API script; not expressible through `php/bin/sel`):
  ```
  $c1 = Value::fromNative(['ACTIVE'=>true]);  Sel::compile('ACTIVE["NOTE"] = "x"')->run($c1);
  Value::fromNative(['OK'=>true,'BAD'=>false])->dump()   // -{"OK"=TRUE{"NOTE"=t"x"}, "BAD"=FALSE}
  Sel::compile('TRUE')->run(Value::none())->dump()        // TRUE{"NOTE"=t"x"}   (expected TRUE)
  Sel::compile('OK == TRUE')                              // now E_NOT_NUM for a different reason
  ```
  Slice 3 variant: `$root = Value::none(); $root->set('FLAG', Value::bool(true)); Sel::compile('FLAG["k"] = 1; 0')->run($root); Sel::evaluate('COUNT(TRUE)')` -> observed `t"1"`, expected `t"0"`; `Value::fromNative(['A'=>true,'B'=>true])` then `A["z"]=1` shows up in `B`. Re-verified by synthesizer with the slice-2 script: prints `TRUE{"NOTE"=t"x"}` for a later plain `TRUE`, and `-{"OK"=TRUE{"NOTE"=t"x"}, "BAD"=FALSE}`.

**Additional fixture requirements from the report:** (conformance drives programs, never a host-supplied BOOL that is assigned into). Add to `tools/check-php-runtime.php`: run `B["k"]=1` over a `fromNative` bool, then assert `Value::bool(true)->size()===0`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C1.

<a id="php-c2"></a>

### PHP-C2: `,` no longer copies what it collects; PHP disagrees with spec 3.4 and every other host

Source: [PHP-C2](../../php-code-review.md). Report labels: [high] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

child contribution `A = RECORD("a", RECORD("b",1)); (A, A["a"]["b"] = 99)[1]["b"]`: PHP `99`; js/python/cpp/lisp `1` (expected `t"1"`). Value contribution: `A = "x"; A["k"] = RECORD("b",1); (A, A["k"]["b"] = 99)[1]["k"]["b"]`: PHP `99`; js/cpp/lisp `1`. Re-verified by synthesizer: `php php/bin/sel -e 'A = RECORD("a", RECORD("b",1)); (A, A["a"]["b"] = 99)[1]["b"]'` prints `99`, `node js/bin/sel.mjs` prints `1`.

**Additional fixture requirements from the report:** `conformance/04-values.selt` "identity and aliasing" (164-260) covers index bases, binders, compound targets but not `,`. Suggest `alias.comma-copies-a-child` and `alias.comma-copies-a-value`, expecting `t"1"`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C2.

<a id="php-c10"></a>

### PHP-C10: `Value::num("007")` keeps the raw text (0.9.2 regression); the API parity probe fails for PHP on HEAD

Source: [PHP-C10](../../php-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`php tools/api.php` vs `node tools/api.mjs` differ on exactly one line: `17 ctor.num.canonicalises = t"007"` (php) vs `t"7"` (js). Also `Value::num('-0')->dump()` = `t"-0"`. Re-verified by synthesizer (both tools run; the diff is exactly that line, 85 lines each). I did not run `tools/check-api.sh` itself (it uses the slot/roster machinery), but it diffs these same reports, so the slice-2 claim that the API gate is red for PHP on HEAD is consistent with what I ran; the roster reference host decides which side prints as `MISMATCH`.

**Additional fixture requirements from the report:** covered only by `tools/api.php` (`ctor.num.canonicalises`), not `conformance/*.selt`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C10.

<a id="php-c39"></a>

### PHP-C39: Malformed `Value` constructor calls: silent truncation or a host `TypeError` instead of `E_BAD_ARG`

Source: [PHP-C39](../../php-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`scratchpad/php2/badcalls.php` (non-strict caller).

**Additional fixture requirements from the report:** `tools/api.php` has `error.host.*` probes for `num`/`dec` only; add int/text/bin/list malformed-call probes.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C39.

## PYTHON report scenarios

<a id="py-c28"></a>

### PY-C28: `Value.scalar` has a public setter that leaves the cached decimal stale

Source: [PY-C28](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`v = Value.text('1'); v.as_decimal(); v.scalar = '2'; v.as_decimal().digits` -> `1` (should be 2). `w = Value.num('1'); w.scalar = 'abc'; w.looks_numeric()` -> `True`, `w.as_text()` -> `abc`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C28.

<a id="py-c30"></a>

### PY-C30: Value nesting created by assignment is checked only against the target path, not path + depth of the assigned value (all five hosts)

Source: [PY-C30](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`A[1]...(150 times) = 1; B[1]...(100 times) = A; B` -> `E_DEPTH at line 0 column 0` in all five hosts (the assignment itself returns silently; `... = A; 1` prints 1).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C30.

<a id="py-c32"></a>

### PY-C32: `Program.run(context)` turns falsy non-Value contexts into `{}`, while a truthy float is refused

Source: [PY-C32](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`python3 -c "import sel; print(sel.compile('1').run(0.0).dump())"` -> `t"1"` (should be E_BAD_ARG like `run(1.5)`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C32.

## CPP report scenarios

<a id="cpp-c42"></a>

### CPP-C42: Aggregates alias what they collect, but SPEC §3.4 and `contributing.md` say they copy (and that C++ does)

Source: [CPP-C42](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`X = LIST(RECORD("k",1)); MAP(X, _)[(X[1]["k"] = 9; 1)]["k"]` gives cpp `9`; a copy-at-collect implementation gives `1`. FILTER/TAKE/DISTINCT: all `9` in cpp; `SORT(X)` gives `1` but `SORT(X, 1)` gives `9`; JS gives `1` for `SORT(X, 1)`; PHP gives `9` even for `LIST(X)[1]`. The five hosts disagree with each other on all of these.

**Additional fixture requirements from the report:** Suggest `values.aggregate-collect-copy-vs-alias` for MAP, FILTER, SORT, TAKE.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C42.

## LISP report scenarios

<a id="lisp-c2"></a>

### LISP-C2: A record with 16 or more fields corrupts the process-global record shape after a new key is assigned

Source: [LISP-C2](../../lisp-code-review.md). Report labels: [high] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

K1..K17 are 17 pairs `"K1",1,...,"K17",17`): `R = RECORD(<17 pairs>); R["Z"] = 5; S = RECORD(<same 17 pairs>); HAS(S, "Z")` -> Lisp `TRUE` (expected `FALSE`; JS/C++ `FALSE`). Same program ending `S["Z"]` -> uncaught `SB-INT:INVALID-ARRAY-INDEX-ERROR` instead of `E_NO_KEY`. Both re-verified.

**Additional fixture requirements from the report:** (nothing builds a 16-key or larger record and then adds a key). Suggest: the `HAS(S,"Z")` program above => `FALSE`, and `S["Z"]` => `E_NO_KEY`; plus a `lisp/tests/unit.lisp` case via `from-native` with a 20-column alist.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C2.

<a id="lisp-c32"></a>

### LISP-C32: `from-native` raises CL `TYPE-ERROR` on malformed or heterogeneous list input

Source: [LISP-C32](../../lisp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`(from-native '(("a" . 1) 5))`, `'(("a" . 1) (2 . 3))`, `'(1 2 . 3)`, `'((1 . 2))`, `(list (cons "a" 1) nil)` -> `TYPE-ERROR` (measured); expected `E_BAD_ARG`.

**Additional fixture requirements from the report:** (host API; add to `lisp/tests/unit.lisp`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C32.

<a id="lisp-c33"></a>

### LISP-C33: Assignment (and `,`) can build a value nested past the 200 cap

Source: [LISP-C33](../../lisp-code-review.md). Report labels: [low] [confirmed; identical in js, php, cpp, python].

**Scenario / reported observation:**

`A[1]x150 = 1; B[1]x150 = A; 7` prints `7` (a 300-level value exists); `... ; B` gives `E_DEPTH at line 0 column 0`; `... ; C = LIST(B); 7` gives E_DEPTH at 0:0 in Lisp/JS/C++ and at the node in PHP/Python. `A = 5; A[1]x199 = 1; (A, 2)` returns then fails at 0:0 on printing.

**Additional fixture requirements from the report:** (`lim.value-depth*` covers chain-only). Suggest `lim.assign-depth-target-plus-value`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C33.

<a id="lisp-c35"></a>

### LISP-C35: TAKE/FILTER/DROP/DEDUPE/SORT/LINK alias their elements, and mutation through the source is observable

Source: [LISP-C35](../../lisp-code-review.md). Report labels: [low] [confirmed, spec-vs-behaviour note; Lisp follows the majority].

**Scenario / reported observation:**

`docs/contributing.md` justifies not copying ("no program can tell apart, because a binder cannot be assigned"), but the source variable can be assigned in the body: `R = LIST(RECORD("id",1), RECORD("id",2)); MAP(TAKE(R,2), (R[2]["id"] = 99; _["id"]))` prints `1, 99` on all hosts, whereas §3.4 says aggregates copy what they collect (would give `1, 2`). Hosts also diverge in neighbouring cases (JS `SORT_BY` result copies: `2,1` vs Lisp/py/cpp/php `2,99`; bare `BUCKET(R,key)` copies in lisp/js/py, aliases in cpp/php).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C35.

## GO report scenarios

<a id="go-c7"></a>

### GO-C7: TAKE/DROP results share the source list's backing array

Source: [GO-C7](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

above; also `A = (1,2,3); DROP(A,1)[(A[2] = 9; "1")]` -> Go `9`, JS/PHP `2`; `A = LIST(1,2,3); LIST(DROP(A,1), A[2] = 9)` -> Go shows 9,3, the others 2,3. JS, PHP, Python, C++, Lisp all give the detached result.

**Additional fixture requirements from the report:** Add `struct.take.detached-from-source` (`A = LIST(1,2,3); LIST(TAKE(A,2), A[1] = 9)` expecting `-{"1"=-{"1"=t"1","2"=t"2"},"2"=t"9"}`) plus the DROP twin.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C7.

<a id="go-c27"></a>

### GO-C27: `docs/contributing.md`'s claim that aggregate aliasing "cannot be observed" is false; SORT and BUCKET disagree across hosts

Source: [GO-C27](../../go-code-review.md). Report labels: [low] [confirmed, cross-host].

**Scenario / reported observation:**

`A = LIST(RECORD("x",1)); LIST(SORT(A), A[1]["x"] = 9)`: Go and PHP show x=9, JS and C++ show 1. `... LIST(BUCKET(A,1), A[1]["x"] = 9)`: Go and JS show 1, PHP and C++ show 9. MAP/FILTER/TAKE/DISTINCT/3-arg BUCKET show 9 in all four hosts.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C27.
