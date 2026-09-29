# T05: Aggregates, ordering, grouping and joins

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [ ] **T05 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

**Destination:** `conformance/06-aggregates.selt, relational/bucket/joined-row files and property-test generators`.

**Required cases and assertions:** Run mutation during MAP/FILTER/SUM/ALL/SORT_BY/TOP_BY/BUCKET over packed lists, shaped records and entry-backed records: append, overwrite, add a non-positional key, and replace the source. Test all permutations of ("10","9","1a"), stable ties and DESC, transitivity/idempotence, and TOP(n) versus TAKE(SORT,n) for n=0,1,size,size+1 and huge n. Exercise scalar/NULL/empty inputs, empty keys, duplicate SELECT_COLS names, group keys with equal spelling but unequal structure, warm numeric caches, same-name binders, comma/sequence/assignment join operands, large exact decimal keys and hash collisions. Compare fast equi-join with a forced general path; assert row count, order, keys and aliasing, not just COUNT.

**Contract prerequisite:** Resolve iteration snapshot semantics, total sort order, BUCKET key collisions/signatures and copy sites. Numerical join identity must honor decimal limits and match the equality operator.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c1"></a>

### JS-C1: An aggregate body that grows the collection it iterates crashes with an uncaught TypeError or runs out of heap

Source: [JS-C1](../../js-code-review.md). Report labels: [high] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

- `node js/bin/sel.mjs -e 'A = (1,2); COUNT(MAP(A, A[COUNT(A) + 1] = 0))'` gives `TypeError: Cannot read properties of null (reading 'length')` at aggregate.mjs:69. Expected `2` (php prints 2). Same with FILTER, SUM, ALL and `COUNT(TOP_BY(A, (A[COUNT(A)+1] = 0; _), 5))` (line 469).
  - `node js/bin/sel.mjs -e 'A = LIST(1,2,3); COUNT(MAP(A, (A["k"]=1; _)))'` gives the same TypeError; php/lisp/python/cpp/go give `3`. The record variant `A = RECORD("a",1); COUNT(MAP(A,(A["b"]=2;_)))` crashes at :75. BUCKET is not affected (it snapshots via `entries()`).
  - `node --max-old-space-size=300 js/bin/sel.mjs -e 'R = RECORD("a",1); R["b"] = 2; R["c"]=3; COUNT(MAP(R, R[_K & "x"] = 1))'` gives `FATAL ERROR: ... JavaScript heap out of memory` (re-run by synthesizer: fatal within 7 s wall on a loaded box; js-3 measured rc 134 in ~2 s; php/cpp print 3).
  - Side note (js-5, spec gap, not JS-only): in-range mutation is live in js/python/cpp and a snapshot in php/lisp/go: `A = LIST(1,2,3); MAP(A,(A[3]=100;_)) .> JOIN(",")` gives `1,2,100` versus `1,2,3`. The spec does not say which.

**Additional fixture requirements from the report:** Cases: `A = (1,2); COUNT(MAP(A, A[COUNT(A)+1] = 0))` => 2 (and FILTER, SUM, ALL, SORT_BY key, TOP_BY key, BUCKET key), `A = LIST(1,2,3); COUNT(MAP(A, (A["k"]=1; _)))` => 3, the record variant => 3, and the Map-mode record variant. Needs the spec to say snapshot versus live for in-range mutation first.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C1.

<a id="js-c2"></a>

### JS-C2: SORT / SORT_BY / TOP comparator is not a total order for numeric-looking versus non-numeric text

Source: [JS-C2](../../js-code-review.md). Report labels: [high] [confirmed, re-verified by synthesizer for js and php].

**Scenario / reported observation:**

- `LIST("10","9","1a") .> SORT() .> JOIN(",")` gives `1a,9,10` in js and php (re-run by synthesizer), python `1a,9,10`; lisp/cpp/go `9,10,1a`.
  - `LIST("1a","x","100","1b","9","10","2","5","30","3b","4","33") .> SORT() .> JOIN(",")` gives js `10,100,1a,1b,2,4,5,9,30,33,3b,x`; php `100,1a,1b,2,4,5,9,10,30,33,3b,x`; lisp `2,3b,4,5,30,33,100,1a,1b,9,10,x`; cpp `2,5,9,10,30,100,1a,1b,3b,4,33,x`; go `100,1a,1b,2,5,9,10,30,3b,4,33,x` (five different answers).
  - JS internal: with L = that 12-element list, `S=SORT(L); JOIN(TAKE(S,4),",")` gives `10,100,1a,1b` but `JOIN(TOP(L,4),",")` gives `1a,1b,2,33`. `SORT(SORT(L))` differs from `SORT(L)` for L = ("10","9","1a").

**Additional fixture requirements from the report:** (`rel.sort.mixed` has one value per class). Cases: `LIST("10","9","1a") .> SORT()`, permutations of it, `SORT(SORT(x))` idempotence, `TOP(L,n)` equals `TAKE(SORT(L),n)`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C2.

<a id="js-c14"></a>

### JS-C14: DEDUPE/DISTINCT and bare/projection BUCKET go quadratic on hash-colliding keys (unseeded 32-bit FNV-1a)

Source: [JS-C14](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

generator `scratchpad/js5/flood.mjs`; bench `bench1.mjs`): n distinct 48-char strings, all same hash: DEDUPE n=1024 145 ms, n=4096 519 ms, n=8192 2186 ms; BUCKET(L,_,_) n=8192 3327 ms. Non-colliding: DEDUPE 7.5 ms, BUCKET 13 ms. 4x n gives 4-6x time; n=65536 extrapolates to ~2.5-3.5 minutes.

**Additional fixture requirements from the report:** (21-structural-hash-identity checks identity, not cost). Better a `tools/` benchmark scenario than a `.selt`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C14.

<a id="js-c15"></a>

### JS-C15: Numeric equi-join key canonicalisation is quadratic in the number of trailing fractional zeros

Source: [JS-C15](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`bench2.mjs`): `A=[{k:"1."+"0"*n}]; B=[{k:"1"}]; COUNT(LINK(A,B,_1["k"]==_2["k"]))`: n=5,000 60 ms, 20,000 1.0 s, 50,000 6.0 s, 100,000 19 s, 200,000 83 s. Plain `_1["k"]==_2["k"]` on the same values takes 2-34 ms.

**Additional fixture requirements from the report:** `rel.link.numeric-key-*` pin key equivalence but not cost.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C15.

<a id="js-c16"></a>

### JS-C16: `exprDependsOnlyOn` ignores `list` AST nodes: a comma-list operand is mis-classified as one-sided and the join raises a spurious E_UNDEF_VAR (JS and Lisp; php/python/cpp/go answer)

Source: [JS-C16](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`LINK(LIST(RECORD("k",1)), LIST(RECORD("k",1)), (_1["k"], _2["k"]) == _1["k"]) .> COUNT` gives `E_UNDEF_VAR ... undefined variable _1` in js and lisp; `1` in php/python/cpp/go. `... _1["k"] == (_2["k"], _1["k"]) AND TRUE` (forces the generic path) gives `0` in all hosts, so the plain form disagrees with itself.

**Additional fixture requirements from the report:** Add the two expressions to 15-relational.selt.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C16.

<a id="js-c48"></a>

### JS-C48: SORT clones its elements but TOP/TAKE do not, so the SORT+TAKE-to-TOP fusion changes an observable result in JS (and C++)

Source: [JS-C48](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`A = LIST(LIST("a")); TAKE(SORT(A),1)["1"][(A["1"]["1"]="z"; "1")]` gives `z` in all hosts (fused). `... TAKE(IF(TRUE, SORT(A), 0),1)["1"][...]` (not fusable) gives `a` in js and cpp, `z` in php/python/lisp/go. `LIST(A)["1"]["1"][...]`: js/lisp/cpp clone (`a`), php/python alias (`z`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C48.

<a id="js-c49"></a>

### JS-C49: BUCKET over a scalar returns an empty list instead of the one-element list §7.3 prescribes

Source: [JS-C49](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`BUCKET("abc", _)` gives `-` in js/php/lisp/python/go; cpp gives `-{"abc"=-{"1"=t"abc"}}`. `COUNT(BUCKET("abc", _, _))` gives 0 versus cpp 1.

**Additional fixture requirements from the report:** Case `BUCKET("abc", _)`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C49.

<a id="js-c50"></a>

### JS-C50: Bare `BUCKET(list, key)` silently drops a whole group when two distinct group keys have the same index-key text

Source: [JS-C50](../../js-code-review.md). Report labels: [low] [confirmed, all hosts].

**Scenario / reported observation:**

`A="x"; A[1]=2; L=LIST("x",A); BUCKET(L,_) .> INDEXES .> JOIN(",")` gives `x` (one key), `BUCKET(L,_) .> MAP(COUNT(_))` gives `1`, while `BUCKET(L,_,COUNT(_))` gives `1,1`. Every host prints the same.

**Additional fixture requirements from the report:** Add the repro to 16-bucket.selt.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C50.

<a id="js-c51"></a>

### JS-C51: Same-name binders (`LINK(T, T, T["id"] == T["mgr"])`) evaluate differently on the equi and the per-pair path

Source: [JS-C51](../../js-code-review.md). Report labels: [low] [confirmed, all hosts] ["id"] ["mgr"].

**Scenario / reported observation:**

`T = LIST(RECORD("id",1,"mgr",2), RECORD("id",2,"mgr",1), RECORD("id",3,"mgr",9)); LINK(T,T,T["id"]==T["mgr"]) .> COUNT` gives 2 on all hosts; the same with `AND TRUE` gives 0 on all hosts.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C51.

<a id="js-c52"></a>

### JS-C52: Spec signature `BUCKET(list, binder, key)` is declared in spec/builtins.json (form 2) but not implemented

Source: [JS-C52](../../js-code-review.md). Report labels: [low] [confirmed, all hosts].

**Scenario / reported observation:**

`BUCKET(LIST(RECORD("cat","A")), R, R["cat"])` gives `E_UNDEF_VAR ... R` on every host (the manifest says R is a binder).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C52.

## PHP report scenarios

<a id="php-c22"></a>

### PHP-C22: SORT/SORT_BY comparator is not a total order for mixed number-shaped and other text; hosts return different orders

Source: [PHP-C22](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`php php/bin/sel -e "SORT(LIST('9','1a','10','2b','30','3')) .> JOIN(',')"` -> PHP `1a,9,10,2b,3,30`; js/py/cpp `10,1a,2b,3,9,30`; lisp `1a,2b,3,9,10,30` (three different answers). `SORT(LIST('10','9','1a'))` -> `1a,9,10` (php/js/py) vs `9,10,1a` (cpp/lisp).

**Additional fixture requirements from the report:** partial (`rel.sort.mixed` covers kinds only) - suggest `rel.sort.number-shaped-vs-plain-text`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C22.

<a id="php-c24"></a>

### PHP-C24: BUCKET reports `_K` as an invented counter for an element whose record key is `""`

Source: [PHP-C24](../../php-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`php php/bin/sel -e 'BUCKET(RECORD("",5,"x",6,"y",7), _K)'` -> `-{"1"=-{"1"=t"5"}, "x"=..., "y"=...}`; js gives `-{""=-{"1"=t"5"}, ...}` (re-verified both). `BUCKET(RECORD("",5,"x",6), IF(_K $== "", "empty", "other"))` -> PHP `{"other"={5,6}}`, others `{"empty"={5}, "other"={6}}`. `MAP(RECORD("",5,"x",6), _K)` is fine everywhere.

**Additional fixture requirements from the report:** suggest `rel.bucket.empty-record-key-K`: `BUCKET(RECORD("",5,"x",6), IF(_K $== "", "empty", "other"))` expecting `-{"empty"=-{"1"=t"5"}, "other"=-{"1"=t"6"}}`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C24.

<a id="php-c25"></a>

### PHP-C25: BUCKET of a scalar returns empty instead of grouping the one-element list the scalar stands for

Source: [PHP-C25](../../php-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`php php/bin/sel -e 'BUCKET("abc", _)'` prints `-` (re-verified); `cpp/build/sel -e 'BUCKET("abc", _)'` prints `-{"abc"=-{"1"=t"abc"}}` (re-verified). `BUCKET("abc", _, COUNT(_))` -> `-` in PHP, `-{"1"=t"1"}` in cpp. `TOP("abc",_,1)` is empty only in lisp.

**Additional fixture requirements from the report:** suggest `rel.bucket.scalar-is-one-element-list` and `rel.top.scalar-is-one-element-list`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C25.

<a id="php-c26"></a>

### PHP-C26: Joined rows hold each binder row under three keys, so deep copy / hash / EQL / dump blow up exponentially with join depth

Source: [PHP-C26](../../php-code-review.md). Report labels: [medium] [confirmed, cross-host design].

**Scenario / reported observation:**

`B=LIST(RECORD("id",1)); Y=LINK(LINK(LINK(LINK(LINK(LINK(LINK(B,B,A1,C1,TRUE),B,A2,C2,TRUE),B,A3,C3,TRUE),B,A4,C4,TRUE),B,A5,C5,TRUE),B,A6,C6,TRUE),B,A7,C7,TRUE); COUNT(Y)` takes 52 s in PHP. Without the `Y=` about 0.1 s. By chain length with assignment: k=4 0.17 s, k=5 0.46 s, k=6 4.7 s, k=7 52 s. `COUNT(DEDUPE(<k=6 chain>))` 4.1 s.

**Additional fixture requirements from the report:** a time-boxed case with a 6-deep chain and an assignment.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C26.

<a id="php-c46"></a>

### PHP-C46: Bare BUCKET silently drops a group when two EQL-distinct keys spell the same text

Source: [PHP-C46](../../php-code-review.md). Report labels: [low] [confirmed, cross-host].

**Scenario / reported observation:**

`x="a"; x["k"]=1; y="a"; y["k"]=2; BUCKET(LIST(x,y), _)` gives `-{"a"=-{"1"=t"a"{"k"=t"2"}}}` on all five hosts (x is lost). The projected spelling returns two groups.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C46.

<a id="php-c47"></a>

### PHP-C47: The BUCKET table row `BUCKET(list, [binder,] key)` implies a 3-argument binder+key form that no host has

Source: [PHP-C47](../../php-code-review.md). Report labels: [low] [confirmed; spec/doc issue, all hosts agree] [binder,].

**Scenario / reported observation:**

`php php/bin/sel -e 'BUCKET(LIST(1,2,3), X, X > 1)'` -> `E_UNDEF_VAR at line 1 column 21`, identical on other hosts.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C47.

## PYTHON report scenarios

<a id="py-c10"></a>

### PY-C10: Mutating a record from inside an aggregate that is iterating it crashes with a host `TypeError` (JS and PHP also crash; C++/Lisp answer)

Source: [PY-C10](../../python-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`python3 -m sel -e 'R = RECORD("a",1,"b",2); SUM(R, x, (R["c"] = 10; x))'` -> `TypeError: 'NoneType' object is not subscriptable`; cpp/lisp print 3; node: `TypeError: Cannot read properties of null (reading 'length')` from `aggregate.mjs`; php: `Warning: Trying to access array offset on null` then a wrong error. Also `MAP(R, x, (R["c"] = x; x))`, `FILTER`, `SORT_BY`, LINK bodies. Adding a key to a list being iterated does not crash in Python.

**Additional fixture requirements from the report:** Suggest `R = RECORD("a",1,"b",2); SUM(R, x, (R["c"] = 10; x))` expecting 3, and the same via MAP/FILTER.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C10.

<a id="py-c11"></a>

### PY-C11: `SELECT_COLS` with a repeated column name builds a record with duplicate keys (fast path)

Source: [PY-C11](../../python-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`PYTHONPATH=$PWD/python python3 -m sel -e 'R = SELECT_COLS(LIST(RECORD("a",1,"b",2)), "a", "a", "b"); LIST(COUNT(R[1]), R[1])'` -> py `{"1"=t"3", "2"={"a"=t"1","a"=t"1","b"=t"2"}}`; js/php/lisp/cpp/go -> COUNT 2 and `{"a"=1,"b"=2}`. Also `SELECT_COLS(..., "b","a","b") .> MAP(INDEXES(_) .> JOIN(","))`: py `b,a,b`, others `b,a`.

**Additional fixture requirements from the report:** Suggest `SELECT_COLS(LIST(RECORD("a",1,"b",2)), "a", "b", "a")` expecting one `a` and `b`, for uniform and non-uniform row shapes, in `15-relational.selt`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C11.

<a id="py-c12"></a>

### PY-C12: Equi-join numeric key fast path skips the digit cap: `LINK` joins where `==` raises E_RANGE

Source: [PY-C12](../../python-code-review.md). Report labels: [medium] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

s5: `BIG = PADL("9", 1000005, "9"); LINK(LIST(RECORD("a", "1")), LIST(RECORD("b", BIG)), _1["a"] == _2["b"]) .> COUNT()` -> py `0`; js, cpp, go `E_RANGE at line 1 column 98: number has more than 1000000 integer digits`; with `AND TRUE` appended (nested loop) every host incl. py raises E_RANGE. s2: `O = LIST(RECORD("k", REPEAT("9", 1500000))); C = LIST(RECORD("k", REPEAT("9", 1500000))); O .> LINK(C, _1["k"] == _2["k"]) .> LEN()` -> python `1500000` (a match), php `1500000`, js `E_RANGE at 2:16`, cpp `E_RANGE at 2:16`; `REPEAT("9",1500000) == REPEAT("9",1500000)` is `E_RANGE` on all four (Lisp not run). Synthesizer re-ran the s5 form: py `0` (1.9 s), js `E_RANGE at line 1 column 98: number has more than 1000000 integer digits`.

**Additional fixture requirements from the report:** (`10-limits.selt` has no join-key case). Suggest `rel.link.equi-key-over-digit-cap` (needs a generated 1,000,001-digit text, e.g. via PADL) expecting E_RANGE.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C12.

<a id="py-c13"></a>

### PY-C13: The SORT comparator is not a total order on mixed numeric-looking / non-numeric TEXT, so results depend on the sort algorithm (across hosts, and inside Python on SORT+TAKE fusion into TOP)

Source: [PY-C13](../../python-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`("10","1a","2","9","9a","100") .> SORT() .> JOIN(",")` -> py/js/php/go `10,1a,2,9,100,9a`; lisp `9,10,100,1a,2,9a`; cpp `2,9,10,100,1a,9a`. Python fusion: `X = LIST("9a","2","9","10","20","a","b","100","1a","3x"); LIST(JOIN(TAKE(SORT(X),4),","), (S = SORT(X); JOIN(TAKE(S,4),",")))` -> `10,1a,2,3x` vs `2,9,10,20`.

**Additional fixture requirements from the report:** for mixed numeric/non-numeric text (06-aggregates / 15-relational sort homogeneous keys only). Suggest `agg.sort.mixed-number-and-text-keys` with the list above.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C13.

<a id="py-c37"></a>

### PY-C37: 2-argument `BUCKET` silently drops rows when two group keys differ structurally but share a key text

Source: [PY-C37](../../python-code-review.md). Report labels: [low] [confirmed; all six hosts agree].

**Scenario / reported observation:**

`A="x"; A["k"]=1; R = LIST(A, "x", A) .> BUCKET(_); JOIN(LIST(COUNT(R), COUNT(R["x"])), ",")` -> `1,1` on py/js/php/lisp/cpp/go (3 rows in, 1 row out). `... .> BUCKET(_, COUNT(_)) .> JOIN(",")` -> `2,1`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C37.

<a id="py-c38"></a>

### PY-C38: Aliasing vs copying is observable and differs between hosts (BUCKET 2-arg, LIST, RECORD, SORT*)

Source: [PY-C38](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`A = LIST(RECORD("k",1,"x",1)); R = LIST(BUCKET(A, _["k"]), (A[1]["x"] = 5; 0)); R[1]["1"][1]["x"]` -> py 1, php 5. `... LIST(LIST(A), (A[1]["x"]=5;0)); R[1][1][1]["x"]` -> py 5, js 1.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C38.

<a id="py-c39"></a>

### PY-C39: BUCKET (all hosts but C++) returns an empty result for a scalar source; the spec says a scalar is a one-element list

Source: [PY-C39](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`BUCKET("x", _)` -> py/js/php/lisp/go `-` (empty; synthesizer confirmed on py), cpp `{"x"={"1"="x"}}`; `BUCKET("x", _, COUNT(_)) .> COUNT()` -> 0 vs cpp 1.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C39.

<a id="py-c40"></a>

### PY-C40: Direction/limit expressions are skipped when the sorted list is empty (PHP disagrees)

Source: [PY-C40](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`SORT_BY(LIST(), _+0, "X")` -> py `-`, php `E_BAD_ARG at line 1 column 22`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C40.

## CPP report scenarios

<a id="cpp-c1"></a>

### CPP-C1: Aggregates hold `const Value&` into the source collection while the body runs (heap-use-after-free)

Source: [CPP-C1](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`cpp/build/sel -e 'R["a"]=1; R["b"]=2; R["c"]=3; FILTER(R, (R["z" & _K] = 1; TRUE))'` gives a segmentation fault; the same with `SORT_BY(R, (R["z" & _K] = 1; 1))` and `BUCKET(...)` (cpp-5). cpp-3's ASan variant is `R["a"]=1; R["b"]=2; R["c"]=3; R["d"]=4; FILTER(R, (R["z"] = 1) > 0)`, also `BUCKET(R, (R["z"] = 1) & "")` and `SORT_BY(R, (R["z"] = 1))`. ASan reports heap-use-after-free READ in `Value::Value(const Value&)`. cpp-3 notes the release build "usually still prints the right answer" for the 4-key case, while cpp-5 got a hard segfault for the 3-key `_K` form.

**Additional fixture requirements from the report:** Suggest `agg.filter.body-appends-key-to-iterated-record` and the SORT_BY/BUCKET twins: they must at least not crash and should pin the semantics.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C1.

<a id="cpp-c38"></a>

### CPP-C38: Hosts disagree on whether an aggregate iterates a snapshot or the live source when the body overwrites an element

Source: [CPP-C38](../../cpp-code-review.md). Report labels: [medium] [confirmed; cross-host split].

**Scenario / reported observation:**

`R = LIST(1,2,3); MAP(R, IF(_K == "1", (R[3] = 99), _))` gives cpp `{99,2,99}`, js `{99,2,99}`, python `{99,2,99}`, php `{99,2,3}`, lisp `{99,2,3}`. `SUM(R, IF(_K == "1", (R[3] = 99), _))` gives 200/200/200/104/104. cpp-5 adds: `X = LIST(1,2,3); FILTER(X, (X[2] = 99; TRUE))` gives cpp `1,99,3`, php/lisp `1,2,3`; `BUCKET(X, (X[3] = 0; _))` gives cpp keys `1,2,0`, others `1,2,3`.

**Additional fixture requirements from the report:** Suggest `agg.source-mutated-by-body.*` cases once the rule is decided.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C38.

<a id="cpp-c39"></a>

### CPP-C39: Two-argument `BUCKET` groups by structural identity but names the group by scalar: distinct groups collapse and lose members

Source: [CPP-C39](../../cpp-code-review.md). Report labels: [medium] [confirmed, all five hosts].

**Scenario / reported observation:**

`A = "x"; A["k"] = 1; B = "x"; B["k"] = 2; BUCKET(LIST(A, B, "x"), _)` gives `-{"x"=-{"1"=t"x"}}` in all five hosts (expected all three members under `"x"`). The 3-argument spelling is correct (identity, three groups).

**Additional fixture requirements from the report:** Suggest `rel.bucket.two-arg-key-is-the-scalar-when-the-key-has-children`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C39.

<a id="cpp-c40"></a>

### CPP-C40: Equi-join extraction ignores same-named binders, so the result depends on an unrelated `AND TRUE`

Source: [CPP-C40](../../cpp-code-review.md). Report labels: [medium] [confirmed, all five hosts].

**Scenario / reported observation:**

same in all five hosts): `COUNT(LINK(LIST(RECORD("k",1),RECORD("k",2)), LIST(RECORD("k",1),RECORD("k",3)), x, x, x["k"] == x["k"]))` gives `1`; with `AND TRUE` added it gives `4`. `X = LIST(RECORD("k",1),RECORD("k",2)); COUNT(LINK(X, X, x["k"] == x["k"]))` gives `2`, with `AND TRUE` gives `4`.

**Additional fixture requirements from the report:** Suggest `rel.link.same-binder-name-both-sides` in both spellings.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C40.

<a id="cpp-c43"></a>

### CPP-C43: Mixed numeric-looking/non-numeric text sorts with an intransitive comparator

Source: [CPP-C43](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`LIST("9","10","1a") .> SORT_DESC() .> JOIN(",")` gives cpp `10,9,1a`, lisp `10,9,1a`, but js/php/py `1a,10,9`.

**Additional fixture requirements from the report:** Suggest `rel.sort.mixed-numeric-and-nonnumeric-text-is-a-total-order`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C43.

## LISP report scenarios

<a id="lisp-c1"></a>

### LISP-C1: BUCKET splits equal text keys once a value's decimal cache is warm

Source: [LISP-C1](../../lisp-code-review.md). Report labels: [high] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

fresh process; synthesizer output shown):
  - `X = "5"; Y = X * 1; JOIN(BUCKET(LIST(X, "5", 5), _, COUNT(_)), ",")` -> Lisp `1,2`, JS `3` (expected `3`). Lisp output re-verified.
  - `R = LIST("1","1"); x = R[1] + 0; BUCKET(R, _, COUNT(_))` -> Lisp `-{"1"=t"1", "2"=t"1"}`, JS one group with count 2. Re-verified.
  - `X = "01"; Y = X + 0; JOIN(BUCKET(LIST(X, "01", "1"), _, COUNT(_)), ",")` -> Lisp `1,1,1`, expected `2,1` (s2, not re-run).
  - `X = "1.0"; Y = X + 0; JOIN(BUCKET(LIST(X, "1.0"), _, COUNT(_)), ",")` -> Lisp `1,1`, JS `2` (s2, not re-run).
  - `R = LIST(RECORD("k","1"),RECORD("k","1"),RECORD("k","2")); x = ANY(R, _["k"] > 0); BUCKET(R, _["k"], COUNT(_))` -> Lisp 3 groups `{"1"=1,"2"=1,"3"=1}`, expected `{"1"=2,"2"=1}`; the two-argument twin `BUCKET(R, _["k"])` loses a row (s5, not re-run). In a 50k-row run, `x = ANY(U, _ > 0); BUCKET(U, _, COUNT(_))` over 500 distinct numeric-text values returned 502 groups.

**Additional fixture requirements from the report:** The existing cases only use literals. Suggest in `21-structural-hash-identity.selt` / `15-relational.selt`: the `"5"` case above => `3`, the `"01"` case => `2,1`, and the record-key case => `{1=2, 2=1}` plus its two-argument twin (row count preserved).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C1.

<a id="lisp-c5"></a>

### LISP-C5: TOP / TOP_BY / TOP_DESC allocate an N-element heap up front

Source: [LISP-C5](../../lisp-code-review.md). Report labels: [high] [confirmed, TYPE-ERROR variant re-verified by synthesizer].

**Scenario / reported observation:**

`lisp/bin/sel -e 'COUNT(TOP(LIST(3,1,2), 1000000000000000000000000000000))'` -> unhandled TYPE-ERROR backtrace (re-verified); `lisp/bin/sel -e 'TOP(LIST(3,1,2), 1000000000)'` -> "Heap exhausted" (s5, not re-run to avoid a large allocation); `node js/bin/sel.mjs` gives `3` for both.

**Additional fixture requirements from the report:** Suggest `LIST(3,1,2) .> TOP(1000000000000000000000000000000) .> COUNT()  =>  3` and a `TOP_BY` twin.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C5.

<a id="lisp-c9"></a>

### LISP-C9: SORT / SORT_DESC / SORT_BY / TOP* of a scalar return an empty list

Source: [LISP-C9](../../lisp-code-review.md). Report labels: [medium] [confirmed, re-verified by synthesizer].

**Scenario / reported observation:**

`lisp/bin/sel -e 'SORT("str")'` -> `-` (empty, re-verified); `node js/bin/sel.mjs -e 'SORT("str")'` -> `-{"1"=t"str"}` (re-verified). Same for `SORT_DESC(5)`, `SORT_BY(5,_)`, `TOP("s",_,1)`, `TOP("s",1)`. Optimiser interplay: `S="str"; S .> SORT .> FILTER(TRUE) .> MAP(_)` prints `-{"1"=t"str"}` while `S="str"; S .> SORT .> FILTER(1) .> TAKE(5)` is `E_NOT_BOOL` optimised versus `-` unoptimised. An exhaustive harness found 743 diffs of this family.

**Additional fixture requirements from the report:** Suggest `sort.scalar-is-one-element-list`, `sort_by.scalar...`, `top.scalar...`, and `rel.filter.pushdown-sort-scalar-source` (`"s" .> SORT .> FILTER(TRUE) .> MAP(_)`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C9.

<a id="lisp-c16"></a>

### LISP-C16: Equi-join key classification ignores `;` sequences, `,` lists and assignments, producing a spurious E_UNDEF_VAR

Source: [LISP-C16](../../lisp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`R = LIST(RECORD("id",1)); S = LIST(RECORD("id",2), RECORD("id",3)); COUNT(LINK(R, S, A, B, (A["id"]; B["id"]) == B["id"]))` -> Lisp `E_UNDEF_VAR ... undefined variable B` (re-verified); python, php, cpp print `2`; JS prints `2`. The `,` form `COUNT(LINK(R, S, A, B, COUNT((A["id"], B["id"])) == B["id"]))` gives E_UNDEF_VAR in Lisp AND JS while python, php, cpp print `1`. `LINK(R, S, A, B, (B["id"]; A["id"]) == B["id"])` gives E_UNDEF_VAR in Lisp and one row in JS/Python.

**Additional fixture requirements from the report:** Suggest cases with a `;` and a `,` sub-expression in an otherwise-equi LINK predicate that reads the other binder.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C16.

<a id="lisp-c17"></a>

### LISP-C17: The SORT ordering is not a total order for mixed numeric-looking and non-numeric text

Source: [LISP-C17](../../lisp-code-review.md). Report labels: [medium] [confirmed; cross-host, spec gap].

**Scenario / reported observation:**

`L = LIST("10","1a","9","2","1b","11","x","3.5"); S = SORT(L); JOIN(TAKE(S,4),",") & " | " & JOIN(TOP(L,4),",")` -> Lisp `2,9,10,1a | 2,10,11,1a`, JS `10,11,1a,1b | 2,10,11,1a`. `SORT(LIST("10","9","1a"))`: Lisp `9,10,1a`, JS/Python/PHP `1a,9,10`, C++ `9,10,1a`.

**Additional fixture requirements from the report:** Suggest `("10","9","1a") .> SORT() .> JOIN(",")`, permutations, and `TOP` vs `SORT..TAKE` equality on the same list.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C17.

## GO report scenarios

<a id="go-c8"></a>

### GO-C8: TOP / TOP_BY / TOP_DESC / BUCKET treat a scalar source as empty; the SORT+TAKE fusion changes results

Source: [GO-C8](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`TOP(5,3)`, `TOP_BY(5,X,X,3)`, `TOP_DESC(5,3)`: Go and Lisp empty; JS/PHP/Python/C++ `{"1"=5}`. `COUNT(TOP(5,1))` 0 vs 1. `BUCKET(5,_,_)`: Go/JS/PHP/Python/Lisp empty, C++ `{"1"={"1"=5}}`; `BUCKET(5,_)`: C++ `{"5"={"1"=5}}`, the others empty. `X = 5; TAKE(SORT_BY(X,_),1)` diverges the same way.

**Additional fixture requirements from the report:** `agg.scalar-is-one-element-list` covers only ALL and FILTER. Add `agg.top.scalar-source` (`TOP(5,3)` -> `-{"1"=t"5"}`), `rel.top-by.scalar-source`, `agg.bucket.scalar-source` (`BUCKET(5,_,COUNT(_))` -> `-{"1"=t"1"}`) and `rel.sort-take.scalar-source-fusion`. Lisp also diverges on TOP; BUCKET diverges in four hosts (see Cross-host).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C8.

<a id="go-c23"></a>

### GO-C23: SORT / SORT_BY / TOP comparator is not a consistent order, so results depend on the sort algorithm

Source: [GO-C23](../../go-code-review.md). Report labels: [medium] [confirmed, spec gap, cross-host].

**Scenario / reported observation:**

`LIST("2","10","1a","3","1b","20","2a") .> SORT() .> JOIN(",")`: Go/JS/PHP/Python `2,10,1a,1b,3,20,2a`; C++ `1a,2,3,10,1b,20,2a`; Lisp `1b,2,3,10,1a,20,2a`.

**Additional fixture requirements from the report:** Add `rel.sort.numeric-text-vs-text` with the list above once the rule is decided.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C23.

<a id="go-c26"></a>

### GO-C26: Aggregate iteration is a snapshot for shaped records/lists but live for entries-backed records; other hosts iterate live

Source: [GO-C26](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`R = RECORD("a",1,"b",2); MAP(R, (R["b"] = 99; _))` -> Go `{"1"=t"1","2"=t"2"}`; JS, PHP, Python, Lisp, C++ `"2"=t"99"`. Entries-backed (`R = NULL; R["a"]=1; R["b"]=2; MAP(R, (R["b"] = 99; _))`) Go gives `t"99"`. go-5: `R = RECORD("a",1); R["b"]=2; R["c"]=3; MAP(R, (R["c"] = 100) + _)` gives `101,102,200` (entries) vs `101,102,103` (shaped) in Go; JS/PHP/C++ give 200 for the shaped form. Lists: `A = LIST(1,2,3); MAP(A, (A[3] = 100) + _)` 103 in Go/PHP, 200 in JS/C++; `MAP(A,(A[4]=100)+_)` crashes the JS host with a TypeError (for the JS report).

**Additional fixture requirements from the report:** Add `agg.map.body-mutates-later-element` once the rule is chosen.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C26.
