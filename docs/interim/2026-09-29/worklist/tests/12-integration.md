# T12: Host integration, concurrency and tooling

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [x] **T12 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

  Closed 2026-09-30 — tools/api.* probes 87-114 + tools/api-pins.txt + check-api-compare.py; tools/check-cli-source.sh misuse; host T12 tests; tools/check-php-version.sh, check-php-integration.php; all six hosts agree at commit 3bc54e6 (conformance 2134/2134, sqlt 1309) and every finding row of the family is closed in coverage.csv.

**Destination:** `tools/api.*, tools/sqlapi.*, CLI/runtime probes, Go race tests, C++ ASan/UBSan/TSan, Python/Lisp concurrency harnesses and metadata checks`.

**Required cases and assertions:** Port equivalent public API misuse and dependency analysis probes to all six hosts, including reads before/conditionally after assignment, missing CLI operands, bad native values and registered nil callbacks. Exercise shared Programs with independent and shared read-only contexts, fresh versus warm caches, repeated failures then successful runs, and two separate logical clients in one process. Use Go -race, C++ TSan separately from ASan/UBSan, threaded Lisp where supported, and Python GC enabled/disabled across overlapping calls and exceptions. Test SelError pickle/copy/process-pool round trips in Python. Check generated limit/manifest coverage, declared API guarantees and documentation examples; host-specific mechanics require an explicit applicability note for other hosts.

**Contract prerequisite:** A host-only mechanism can be N/A elsewhere only with a reason and an equivalent externally observable invariant test. Unconfirmed findings need a reproducer or a documented disposition, not a speculative source change.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c29"></a>

### JS-C29: Public entry points leak host exceptions for non-string input

Source: [JS-C29](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`node -e "import('/home/nathan/workspaces/nth/sel/js/src/sel.mjs').then(m=>{try{m.compile(12)}catch(e){console.log(e.code,e.message)}})"` prints `E_SYNTAX unexpected end of input`; `node js/bin/sel.mjs -e`.

**Additional fixture requirements from the report:** (host API probe).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C29.

<a id="js-c40"></a>

### JS-C40: `Program.dependencies()` is order-insensitive and drops variables read before, or only conditionally after, an assignment

Source: [JS-C40](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`node js/bin/sel.mjs --deps -e 'A + 1; A = 2'` prints empty (expected A). `--deps -e 'IF(X, A = 1, 0); A'` prints X only.

**Additional fixture requirements from the report:** (only tools/check-api.sh probes it). API probes for both programs.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C40.

## PHP report scenarios

<a id="php-c38"></a>

### PHP-C38: `Program::dependencies()` is order-insensitive: a variable read before it is assigned is not reported

Source: [PHP-C38](../../php-code-review.md). Report labels: [low] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`php php/bin/sel --deps -e 'A + 1; A = 2'` prints nothing (re-verified); the run needs `A` from the context.

**Additional fixture requirements from the report:** for read-before-assign order.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C38.

<a id="php-c48"></a>

### PHP-C48: Cosmetic: error message text differs from other hosts

Source: [PHP-C48](../../php-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

Found by: slice 2 C7, slice 5 C5. Codes and positions agree (messages are not contract).
- `ROUND(1, 99999999999999999999)` reports "scale 9223372036854775807 exceeds the maximum" (JS/Python print the real number): `Dec.php:592-597` `toInt` saturates via `(int)"digits"` (re-verified). The saturation is correct for every in-range comparison tried; a future caller doing arithmetic on the clamp would turn it into a float.
- `LINK_LEFT(LIST(RECORD("k","१२")), LIST(RECORD("k",1)), A,B,A["k"]==B["k"])` prints `not a number: "१२"` (PHP, `Value.php:292` `json_encode($d)`) vs literal characters (js, cpp). Fix: `JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES` if parity is wanted.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C48.

## PYTHON report scenarios

<a id="py-c2"></a>

### PY-C2: `_gc.bulk_allocation` is not thread-safe: concurrent `Program.run` can leave the cyclic collector disabled for the whole process

Source: [PY-C2](../../python-code-review.md). Report labels: [high] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

s3: 4 threads each calling `Program.run({})` on `1 + 1` 4000 times, then `gc.isenabled()`: left disabled in 7 of 40 trials at the default switch interval; with `sys.setswitchinterval(1e-6)` it fails on the first try. s2: 8 threads x 50 ms, 200 trials: disabled after the first 1, 3, 12 and 25 trials in four runs; bare `with bulk_allocation(): pass` loop with `setswitchinterval(1e-6)` fails in trial 0. Synthesizer: 4 threads x 3000 runs of `1 + 1` with `setswitchinterval(1e-6)` -> `gc.isenabled()` False in trial 0.

**Additional fixture requirements from the report:** not a language case; needs a python/tests thread test asserting `gc.isenabled()` afterwards.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C2.

<a id="py-c16"></a>

### PY-C16: `SelError` cannot be pickled or copied: one error kills a `ProcessPoolExecutor`

Source: [PY-C16](../../python-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

script with `ProcessPoolExecutor(1)` submitting `lambda s: sel.compile(s)` on `'1 +'` -> `BrokenProcessPool: A process in the process pool was terminated abruptly`. Also `import pickle, sel; try: sel.compile('1 +') except sel.SelError as e: pickle.dumps(e); pickle.loads(_)` -> TypeError. Synthesizer re-ran the pickle form: `TypeError: SelError.__init__() missing 1 required positional argument: 'message'`.

**Additional fixture requirements from the report:** not a language case; add to python/tests (pickle round-trip keeps code/line/col/offset).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C16.

<a id="py-c31"></a>

### PY-C31: `dependencies()` is order-insensitive, contradicting its own docstring (all five hosts)

Source: [PY-C31](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`python3 -m sel --deps -e 'A + 1; A = 2'` -> empty (same in cpp, lisp, js, php); evaluation -> E_UNDEF_VAR at 1:1. Synthesizer: `--deps` printed nothing (confirmed).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C31.

## CPP report scenarios

<a id="cpp-c11"></a>

### CPP-C11: `register_function` mutates the host table with no lock while `compile()` reads it

Source: [CPP-C11](../../cpp-code-review.md). Report labels: [low] [confirmed by reading; not run under TSan].

**Scenario / reported observation:**

not run. Two threads: one looping `sel::compile("F(1)")`, one calling `sel::register_function("F", 0, 1, fn)`.

**Additional fixture requirements from the report:** n/a (host API).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C11.

<a id="cpp-c13"></a>

### CPP-C13: `HostArgs::val/text/...` with index at or above `count()` is undefined behaviour

Source: [CPP-C13](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`register_function("OPT", 1, 2, [](HostArgs& a){ return a.count() > 1 ? a.val(1) : a.val(5); }); compile("OPT(1)").run(c);` gives an ASan heap-buffer-overflow in `Args::val` (scratchpad `cpp3/src/t1.cpp oob`).

**Additional fixture requirements from the report:** API-probe territory.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C13.

<a id="cpp-c15"></a>

### CPP-C15: `RECORD` builds `rec.set(a.text(i), a.val(i + 1).clone())` in one expression

Source: [CPP-C15](../../cpp-code-review.md). Report labels: [low] [unconfirmed].

**Scenario / reported observation:**

argument evaluation order is unspecified (GCC evaluates right to left), so `clone()` (which can throw E_DEPTH on an over-deep host-supplied value) may run before the key's `as_text` error. Only reachable with an over-deep host value. The rest of the file sequences such pairs through named locals.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C15.

<a id="cpp-c44"></a>

### CPP-C44: `dependencies()` is "read anywhere minus assigned anywhere", not "read without having assigned it first"

Source: [CPP-C44](../../cpp-code-review.md). Report labels: [low] [confirmed, all five hosts].

**Scenario / reported observation:**

`cpp/build/sel --deps -e 'X += 1'` prints nothing; `... 'X + 1; X = 2'` prints nothing (JS/PHP/Lisp/Python identical).

**Additional fixture requirements from the report:** `dependencies()` is exercised only by `tools/check-api` probes; suggest API probes for these two programs.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C44.

## LISP report scenarios

<a id="lisp-c12"></a>

### LISP-C12: Process-global mutable hash tables are unsynchronised: concurrent use errors out or returns wrong records

Source: [LISP-C12](../../lisp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

s1 scratchpad `l1/t8.lisp`: 8 `sb-thread` threads each compiling 20,000 distinct `RECORD("aN",1,"bID",2)["bID"]` programs. 1 and 2 threads: 0 errors; 8 threads: 5,200 to 8,200 errors per thread (about 52k "Unsafe concurrent operations", 2 "Corrupt NEXT-chain", 44 silently wrong `E_NO_KEY "no key b6"`, i.e. the wrong shape returned). s2 scratchpad `p8.lisp`: 6 threads doing `(dec-add (dec-parse "1.<k zeros>1") ...)`, k in 19..319, gave `SIMPLE-ERROR: Unsafe concurrent operations on #<HASH-TABLE :TEST EQL :COUNT 14>` within a few thousand iterations. Regex and alias caches: reasoned, not tested.

**Additional fixture requirements from the report:** (no host-API concurrency lane).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C12.

## GO report scenarios

<a id="go-c6"></a>

### GO-C6: Read-only evaluation writes lazy caches into shared input Values (data race, possible torn string read)

Source: [GO-C6](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

the case above. go-3 also raced `R["x"] + 1`, `SUM(L, _["v"])`, `SORT_BY(L, _["v"])`, `R["name"] & R["x"]` over one root, and a root holding `N` from `X = 1.5; X * 3` then `N & "x"` (race at `value.go:63/64`). go-5: 200 `NewText("<n>")` items, 4 goroutines alternating `SORT(L) .> COUNT()` and `SUM(L, _ + 1)`: dozens of races. Results were still correct; the race report is the failure.

**Additional fixture requirements from the report:** not expressible in `.selt`; needs a Go race test in `concurrency_test.go`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C6.

<a id="go-c36"></a>

### GO-C36: `RegisterFunction` accepts a nil function

Source: [GO-C36](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`RegisterFunction("Zed", 0, 1, nil)` returns normally; `MustCompile("ZED()").Run(nil)` -> nil-pointer panic that escapes `Program.Run` (re-panicked, `program.go:64-70`).

**Additional fixture requirements from the report:** API probe: add `host.fn.refuse.not-callable` in `tools/api.*` for all hosts.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C36.

<a id="go-c41"></a>

### GO-C41: "Manifest names never defined" is checked only under `go test`, not at load

Source: [GO-C41](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`docs/contributing.md` says `define` refuses to load on a manifest name no module defined; JS does this in `assertManifestCovered()` (`registry.mjs:83`). Go detects only per-definition mismatches at init. Today the 78 manifest names equal the 78 registered names, so nothing is wrong now.

**Additional fixture requirements from the report:** `tools/check-manifest.sh` covers arity behaviour, not coverage.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C41.
