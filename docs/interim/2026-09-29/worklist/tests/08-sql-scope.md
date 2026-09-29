# T08: SQL lexical scope, normalization and expansion

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [ ] **T08 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

**Destination:** `sql/cases .sqlt scope/aggregate/bucket cases, SQL API tests and generated expansion probes`.

**Required cases and assertions:** Use outer _K, repeated binder names, a column/constant binding shadowed by a binder, helper definitions with free names, FILTER-local binders, non-name binder arguments and indexed-assignment aliasing. Compare translated results with SEL under at least two input bindings that distinguish correct lexical capture from an accidental constant. BUCKET SUM bodies must include numeric and text literals, nested IF and multiple slots; assert inline/debug/params SQL and ordered parameter values. Include filtered static JOIN, relation-name rebinding, helper chains, doubling DAGs and deep source/normalization walks. Verify a supported result or specified refusal without a host exception, partial parameter state or exponential expansion.

**Contract prerequisite:** Define lexical capture and budget accounting; refusal must be an allowed SQL refusal, not an invented evaluator error. Preserve AST immutability and parameter slot identity.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c7"></a>

### JS-C7: SQL translator: aggregate binders are dynamically scoped

Source: [JS-C7](../../js-code-review.md). Report labels: [high] [confirmed, repro 1 re-verified by synthesizer].

**Scenario / reported observation:**

js-6 helper `tr(src, bindings, dialect)`; `A` = `Binding.column('a','t','NUM')`, `C` = `Binding.columns(...)`):
  1. `ANY((0,0), ALL((_K, 5), I, I > 1))` (no bindings), mariadb and postgresql: SEL evaluates to TRUE (outer key "2" gives (2,5)). The translation uses the inner key '1' in both iterations, so the SQL is FALSE. Re-verified by synthesizer on mariadb: SEL gives `TRUE`; the translation is `(((CASE WHEN ('1' REGEXP ...) THEN CAST('1' AS DECIMAL(65,10)) ... END > 1) AND (5 > 1)) OR ((CASE WHEN ('1' REGEXP ...) ... > 1) AND (5 > 1)))`, i.e. `_K` is '1' in both halves.
  2. `ALL((A, 1), I, ALL((5, 6), A, I > 0))` with A a column: SEL with A=-1 gives FALSE; the translation is `((5 > 0) AND (6 > 0)) AND ((1 > 0) AND (1 > 0))`, constant TRUE.
  3. `ANY((A, 2), ALL((_, 5), _ > 0))`: SEL answers a value; translator says E_SQL_DEPTH "the evaluator answers E_DEPTH for it". Same for `ANY(C, ALL((_, 5), _ > 0))` and `ANY(C, X, ALL((X, 5), X, X > 0))`.
  4. `Sql.tryTranslate(compile('ANY((Y, 2), Y, ALL(Y, Z, Z > 0))'), 'mariadb', {Y: Binding.column('y','t','NUM')})` throws `RangeError: Maximum call stack size exceeded` (same for COUNT(Y), HAS(Y, 1), JOIN(Y, ",")); SEL gives TRUE.

**Additional fixture requirements from the report:** Cases `agg.scope.inner-binder-does-not-capture-outer-element-key` (repro 1), `agg.scope.same-binder-name-nested` (repro 3), `agg.scope.element-named-like-inner-binder` (repro 2), and a refusal-or-translation (not a throw) case for repro 4.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C7.

<a id="js-c8"></a>

### JS-C8: SQL translator: exponential output and time from small programs (no size or work budget)

Source: [JS-C8](../../js-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

measured, mariadb): column base, n=18: 1.0 s / 3.1 MB SQL; n=20: 3.6 s / 12.6 MB; n=22: 15 s / 50 MB. Constant base `X0 = 1`: n=16: 0.8 s, n=20: 14 s / 6.3 MB, n=22: 66 s / 25 MB. SEL evaluates the n=200 version in 23 ms. Via binders with no assignments: `ALL((A,A), V1, ALL((V1,V1), V2, ... V10 > 0))` grows 4x per level (n=10: 20 KB, n=15: 655 KB, a 306-char program; n=25 is hundreds of GB).

**Additional fixture requirements from the report:** Cases `norm.inline.expansion-budget` (n=30 doubling chain gives a refusal) and an aggregate-nesting equivalent.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C8.

<a id="js-c26"></a>

### JS-C26: SQL stage-1 inlining captures a definition's free names into aggregate binders

Source: [JS-C26](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`X2 = A; ALL((5,6), A, X2 > 0)` with A a column. SEL with A=-1: FALSE. Translation: `((5 > 0) AND (6 > 0))`.

**Additional fixture requirements from the report:** Case `norm.inline.capture-not-by-binder`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C26.

<a id="js-c53"></a>

### JS-C53: SQL: a binder that reuses the name of a scalar `value` binding is treated as that constant

Source: [JS-C53](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

bindings `V = Value.text('5')`, `A` undeclared column. `ALL((A, A), V, V + 1 > 0)` gives `((a + 1) > 0) AND ((a + 1) > 0)` unguarded, while binder `I` gets the CASE/REGEXP guard. With `V = "abc"`, `ALL((A, 2), V, V + 1 > 0)` gives E_SQL_INVALID (E_NOT_NUM); SEL accepts it. Relation form: `ALL(R, V, V["QTY"] + 1 > 0)` gives E_SQL_INVALID (E_NO_KEY).

**Additional fixture requirements from the report:** Case `const.binder-shadows-value-binding`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C53.

<a id="js-c54"></a>

### JS-C54: SQL layer: non-SqlError host exceptions escape `tryTranslate` / `planHybrid`, and definition chains are refused with a wrong message

Source: [JS-C54](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

(a) `X0 = N; X1 = X0 + 1; ...; X20000 > 0` with N a column: `RangeError: Maximum call stack size exceeded` thrown by `Sql.translate` (js-6 `u.mjs`, 20000 and 100000 definitions; 3000 gives E_SQL_DEPTH; SEL evaluates 3000 defs fine). (b) `Sql.tryTranslate(compile('COUNT(V)'), 'mariadb', {V: Binding.value(Value.list([Value.num('1'), Value.null()]))})` throws `SelError E_NULL` (also ANY/JOIN over V); `V[2] == 1` on the same binding translates, then `asValue('inline')` throws SqlError E_SQL_BINDING after `tryTranslate` returned a Fragment. (c) js-7 `deep.mjs`: `ORDERS` + `' .> TAKE(1)'` x 20000 gives `RangeError` at hybrid.mjs:95; x250..5000 gives `pure_memory` (fine); x150 gives pure_sql. (d) 250 definitions over a constant give E_SQL_INVALID "SEL rejects this expression (E_DEPTH: ...)"; over a column, E_SQL_DEPTH. SEL prints TRUE for 3000.

**Additional fixture requirements from the report:** A plan case with a >MAX_DEPTH chain expecting the same code in every host.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C54.

<a id="js-c58"></a>

### JS-C58: SQL: an assigned keyed list cannot be indexed directly (missing feature, safe direction)

Source: [JS-C58](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`R[1] = N; R[2] = 2; R[1] + R[2] > 0` is refused ("only a bound name can be indexed"), while `SUM(R, I, I)` translates.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C58.

## PHP report scenarios

<a id="php-c7"></a>

### PHP-C7: `SUM(g, body)` inside a BUCKET projection emits parameter slot numbers instead of literals

Source: [PHP-C7](../../php-code-review.md). Report labels: [high] [confirmed; not re-verified].

**Scenario / reported observation:**

scratchpad `php6/t8.php`): `O .> BUCKET(_["CAT"], RECORD("cat", _K, "s", SUM(_, _["TOTAL"] * 2)))` on mariadb -> `... COALESCE(SUM((\`total\` * 1)), 0) AS \`s\` ...`, expected `(\`total\` * 2)`. `SUM(_, _["TOTAL"] * 7 + 5)` -> `((total * 1) + 2)`. `SUM(_, IF(_["STATUS"] $== "x", _["TOTAL"], 0))` -> `CASE WHEN (... = CAST(1 AS CHAR) ...) THEN total ELSE 2 END`. JS: `js/src/sql/translator.mjs:890` (`inner.parts.join('')`); Python: `python/sel/sql/translator.py:821` (TypeError escapes `try_translate`); Go: `go/sel/sql/translator.go:1727`.

**Additional fixture requirements from the report:** all `SUM(_, ...)` cases in `sql/cases/24-bucket.sqlt` and `25-hybrid-plans.sqlt` use a bare column or `_K`. Suggest `ITEMS .> BUCKET(_["dept"], RECORD("t", SUM(_, _["amount"] * 2)))`, one with a text literal in an IF body, and a `--- params` check.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C7.

<a id="php-c29"></a>

### PHP-C29: A static-list element that mentions `_K` is evaluated in the inner aggregate's scope

Source: [PHP-C29](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`php6/t18.php`): `ANY(("a","b"), o, ANY((_K, "zz"), c, c $== "2"))`: SEL TRUE (re-run by synthesizer: `php php/bin/sel` prints TRUE); the translation compares `'1'` and `'zz'` with `'2'` in both outer iterations: constant FALSE. `ALL(("a","b"), o, ANY((_K, "zz"), c, c $== _K))`: SEL FALSE, translation TRUE. `ANY(("a","b"), o, JOIN((_K, "x"), "-") $== "2-x")`: SEL TRUE, translation `CONCAT('1','-','x')` in both iterations. Related: `ANY((X, 2), X, X > 1)` is refused as E_SQL_DEPTH while SEL answers TRUE.

**Additional fixture requirements from the report:** first repro as an `agg.nested.*` case; pin the E_SQL_DEPTH-for-a-valid-program case.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C29.

<a id="php-c30"></a>

### PHP-C30: A FILTER's binder name is bound in the aggregate body's scope

Source: [PHP-C30](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`php6/t19.php`, `X` a NUM column): `ALL(FILTER((1,2,3), x, x > 1), y, y < X)` renders `(1 < 1)`, `(2 < 2)`, `(3 < 3)` - column `x` never appears; with `x = 5` SEL is TRUE and the SQL FALSE. `ALL(FILTER((1,2,3), x, y > 0), y, y < 9)` translates while SEL raises E_UNDEF_VAR (column 32).

**Additional fixture requirements from the report:** suggest `agg.filter.binder-does-not-leak-into-body` (row and static shapes).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C30.

<a id="php-c32"></a>

### PHP-C32: No bound on generated-SQL size: 20 short assignments produce 8 MB / 17 s (2^n blow-up), also in hybrid planning

Source: [PHP-C32](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

slice 6 (`php6/t3.php`): n=14: 131 KB, 0.24 s; n=18: 2.1 MB, 3.6 s; n=20: 8.4 MB, 17.7 s (peak PHP memory small; time and output are the cost). Slice 7 (`p7/d1.php`, helper pattern `A1 = LEN("x") + LEN("y"); A2 = A1 + A1; ...; ORDERS .> FILTER(_["id"] > An)`): n=8 91 ms / 4 KB; n=12 1.6 s / 66 KB; n=14 6.4 s / 262 KB; n=16 26.9 s / 1.05 MB (`planHybrid` same plus ~10%).

**Additional fixture requirements from the report:** a case with 30 doubling assignments expecting a refusal.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C32.

<a id="php-c34"></a>

### PHP-C34: Helper that rebinds a relation name is applied twice (`ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)`)

Source: [PHP-C34](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

sqlite harness): `ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)` memory ids 3,4,5; plan is `pure_sql` with `LIMIT 3 OFFSET 4` -> ids 5,6,7. Hybrid variant with a REPEAT MAP: ids 3-5 vs 5-7.

**Additional fixture requirements from the report:** `ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)` in `25-hybrid-plans`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C34.

## PYTHON report scenarios

<a id="py-c5"></a>

### PY-C5: SQL: `BUCKET ... SUM(g, body)` crashes with a host `TypeError` whenever the body contains a literal

Source: [PY-C5](../../python-code-review.md). Report labels: [high] [confirmed; re-verified by synthesizer].

**Scenario / reported observation:**

`R = Binding.relation('items', alias='i', fields={'cat': column('cat',type='TEXT'), 'qty': column('qty',type='NUM')})`; `Sql.translate(compile('R .> BUCKET(_["cat"], RECORD("k", _K, "s", SUM(_, _["qty"] * 2)))'), 'mariadb', {'R': R})` -> `TypeError: sequence item 1: expected str instance, int found` (same on postgresql, sqlite). Without the `* 2` it works (`COALESCE(SUM(`qty`), 0) AS `s``). Synthesizer reproduced the TypeError on mariadb.

**Additional fixture requirements from the report:** ; every group-SUM case in `sql/cases/24-bucket.sqlt` uses a bare field. Suggest bucket SUM with `_["amount"] * 2` and `_["amount"] + 1`, including params mode (bindings() count must equal placeholders).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C5.

<a id="py-c6"></a>

### PY-C6: SQL: exponential output from a linear program (helper reuse is inlined as a tree)

Source: [PY-C6](../../python-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`C` = NUM column, program `V0 = C + 1; V1 = V0 + V0; ...; Vn = V(n-1) + V(n-1); Vn > 0`: n=12: 0.31 s / 57 KB; n=14: 1.25 s; n=16: 5.1 s / 917 KB / 65,537 params; n=18: 21.7 s / 3.6 MB / 262,145 params; n=30 practically non-terminating; n=40 exhausts memory. Synthesizer re-ran n=12 (mariadb): 0.5 s, 57,345 characters of output (same order; box loaded).

**Additional fixture requirements from the report:** Suggest a 25-line doubling chain expecting a refusal.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C6.

<a id="py-c18"></a>

### PY-C18: SQL: name-based (unhygienic) substitution and element re-entry produce wrong SQL or a spurious `E_SQL_DEPTH`

Source: [PY-C18](../../python-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

see (a)-(c) (harness `py6/h.py` `tr`/`ev`); not re-run by the synthesizer.

**Additional fixture requirements from the report:** Suggest `agg.scope.outer-key-in-inner-list`, `norm.inline.binder-does-not-capture-helper`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C18.

<a id="py-c45"></a>

### PY-C45: SQL: a binder named like a scalar `value` binding is treated as that constant by the validation pre-check

Source: [PY-C45](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`R` one-field relation, `P = Binding.value(Value.text('abc'))`: `ANY(R, P, P["qty"] > 0)` -> `E_SQL_INVALID ... (E_NO_KEY: no key "qty")`; the same program without the P binding translates to `EXISTS (SELECT 1 ...)`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C45.

## CPP report scenarios

<a id="cpp-c10"></a>

### CPP-C10: SQL stage 1: long helper-variable chains are O(n^2) and then SIGSEGV from uncounted recursion

Source: [CPP-C10](../../cpp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

private driver, mariadb, X a NUM column, from the cpp-6 report; not re-run): n=5000 0.45 s; n=20000 10.7 s (E_SQL_DEPTH); n=45000 106 s (E_SQL_DEPTH); n=60000 exit 139 (SIGSEGV, 1 MB program). About 150-250 bytes of stack per level (n=5000 crashes under `ulimit -s 1024`).

**Additional fixture requirements from the report:** Suggest 250 chained helpers over a column expecting `E_SQL_DEPTH` at the statement that crosses 200, and a stress lane with 100k statements.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C10.

<a id="cpp-c17"></a>

### CPP-C17: SQL stage-1 helper inlining is a DAG that every later walk treats as a tree: exponential time and output size

Source: [CPP-C17](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

measured with private drivers; not re-run):
  - cpp-6, mariadb, `A0=1; A1=A0+A0; ...; A24=A23+A23; TRUE`, no bindings: n=16 0.25 s, n=20 3.6 s, n=24 50 s (about x14 per 4 statements, all in `record()`). With a column `A0=X`, n=20 takes 4.7 s and emits a 12.6 MB SQL string. `cpp/build/sel -e` on the constant version with n=60 prints TRUE in 6 ms.
  - cpp-7, `X0 = COUNT(ORDERS); X1 = X0 + X0; ...; ORDERS .> MAP(RECORD("id", _["id"], "c", Xn))` (-O2): n=14 translate 98 ms / plan 149 ms; n=18 1.7 s / 1.7 s; n=20 4.4 s / 8.7 s (about 50 MB SQL); n=24 extrapolates to about 70 s and over 1 GB.

**Additional fixture requirements from the report:** Suggest a `.sqlt` case with about 30 doubling statements over a column expecting a bounded-time refusal, plus a timing guard in the fuzz lane.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C17.

<a id="cpp-c23"></a>

### CPP-C23: SQL stage-1 inlining lets an aggregate binder capture a free variable inside an inlined helper: silently wrong SQL

Source: [CPP-C23](../../cpp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

private driver, not re-run): `drv mariadb 'X = Y + 1; ALL((1,2,3), Y, Y > X)' Y=c:t.y:NUM` gives `(((1 > (1 + 1)) AND (2 > (2 + 1))) AND (3 > (3 + 1)))`, a constant FALSE. Correct is `(1 > (t.y + 1)) AND (2 > (t.y + 1)) AND (3 > (t.y + 1))`. Loud variant: `ANY((B + 1, 2), B, B > 0)` recurses until the depth guard and answers `E_SQL_DEPTH` ("the evaluator answers E_DEPTH", which is false: SEL evaluates it fine). Any name shared by a column binding and a binder/def triggers it (for example a helper `LIMIT_ = Q` plus `ANY(ITEMS, Q, ...)`).

**Additional fixture requirements from the report:** `norm.inline.captures-at-assignment` pins assignment-time capture for plain variables only. Suggest `norm.inline.def-not-captured-by-binder` (the column form above) and `agg.binder-same-name-as-list-variable`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C23.

## LISP report scenarios

<a id="lisp-c6"></a>

### LISP-C6: SQL: `JOIN` over a FILTERed static list silently drops the FILTER

Source: [LISP-C6](../../lisp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`translate` on `JOIN(FILTER(("a","b","c"), _ $== "a"), ",")`, mariadb -> `CONCAT(CONCAT(CONCAT(CONCAT('a', ','), 'b'), ','), 'c')`; `lisp/bin/sel -e 'JOIN(FILTER(("a","b","c"), _ $== "a"), ",")'` -> `a`. `JOIN(FILTER(("a","b","c"), FALSE), ",")` -> SQL `a,b,c`, SEL `""`.

**Additional fixture requirements from the report:** Suggest `agg.join.filtered-source-is-refused` in `sql/cases/12-aggregates.sqlt`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C6.

<a id="lisp-c7"></a>

### LISP-C7: SQL: FILTER binder names leak into the aggregate body and other FILTERs' predicates

Source: [LISP-C7](../../lisp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

bindings `V = binding-columns(a, b)` NUM, `X = binding-column("x")` NUM), postgresql: `ANY(FILTER(V, x, x > 1), q, q > X)` -> `((("a" > 1) AND ("a" > "a")) OR (("b" > 1) AND ("b" > "b")))`; SEL (`V=(5,3); X=4; ...`) -> `TRUE`, the SQL is constantly FALSE. Also `ANY(FILTER(V, a, a > 1), q, a < 9)` translates (SEL: E_UNDEF_VAR at the body `a`), and `ANY(FILTER(FILTER(V, a, b > 1), b, a < 9), q, q > 0)` translates (SEL: E_UNDEF_VAR).

**Additional fixture requirements from the report:** Suggest `agg.filter.binder-does-not-leak-into-body` and `...-undefined-in-body` (E_SQL_INVALID / E_SQL_UNBOUND).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C7.

<a id="lisp-c23"></a>

### LISP-C23: SQL: exponential time/output from a tiny program (assignment substitution builds a DAG that is walked as a tree)

Source: [LISP-C23](../../lisp-code-review.md). Report labels: [medium] [confirmed; cross-host].

**Scenario / reported observation:**

postgresql, X NUM column: n=10 -> 84 KB in 69 ms; n=12 -> 336 KB / 319 ms (source 190 bytes); n=18 about 21 MB / about 20 s; n=30 would be about 20 GB. JS (`js/src/sql`) shows the same doubling (n=18: 21 MB, 9.5 s).

**Additional fixture requirements from the report:** Suggest a limit case with a 30-statement doubling chain that must refuse quickly.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C23.

## GO report scenarios

<a id="go-c2"></a>

### GO-C2: SQL: a non-name binder argument becomes a child-less node and nil-derefs the translator

Source: [GO-C2](../../go-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`BUCKET("x", A[1], "y", 1)` (above). JS: `ERR E_SQL_SHAPE A is bound as a column, which has no parts to index`. Statement forms: `ITEMS .> MAP(BUCKET("x", _["NAME"], "x", _["QTY"]))`; `ITEMS .> BUCKET(_["NAME"]) .> MAP(BUCKET("entity", COUNT(_), "latest", TOP_BY(_, _["PRICE"], "DESC", 1)))` -> `index out of range [0] with length 0` at `count`.

**Additional fixture requirements from the report:** Add sqlt case `shape.binder.non-name-binder-is-refused` for the expression and both statement forms.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C2.

<a id="go-c3"></a>

### GO-C3: SQL: `SUM(<group>, body)` inside a BUCKET projection drops every literal and emits invalid SQL

Source: [GO-C3](../../go-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

the case above -> Go `... COALESCE(SUM((`i`.`price` * )), 0) AS `t` ...`; JS `... SUM((`i`.`price` * 1)) ...`. `SUM(_, IF(_["NAME"] $== "x", 1, 2))` -> Go `CASE WHEN (CAST(`i`.`name` AS CHAR) COLLATE ... = CAST( AS CHAR) ...) THEN  ELSE  END`. Same on PostgreSQL and SQLite.

**Additional fixture requirements from the report:** (only `SUM(_, _["col"])` is in `24-bucket.sqlt`). Add `bucket.sum.literal-in-body` with a NUM literal and a TEXT literal (`IF(_["dept"] $== "x", 1, 2)`), in inline and params modes.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C3.

<a id="go-c4"></a>

### GO-C4: SQL stage 1 aliases indexed-assignment lists: a later write to the source changes an earlier copy

Source: [GO-C4](../../go-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`R[1] = 5; X = R; R[2] = 6; COUNT(X)` -> SEL `1`, Go translator `2`, JS translator `2`. `R[1] = 5; X = R; X[2] = 7; COUNT(R)` -> SEL `1`, translators `2`. `R[1] = A; X = R; R[2] = B; SUM(X, _ + 1)` -> SEL `E_UNDEF_VAR` (A unbound); with columns SEL sums one element, translator two.

**Additional fixture requirements from the report:** Add `assign.indexed.copy-is-not-alias` with the two programs.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C4.

<a id="go-c20"></a>

### GO-C20: SQL: assignment inlining is exponential in program size (documented DoS; SEL evaluates the same program linearly)

Source: [GO-C20](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

Go, mariadb): n=14 0.23 s, n=18 4.0 s (6 MB SQL), n=20 19 s (25 MB); n=30 would be ~2^30 nodes. `sel -e` evaluates n=60 in 6 ms. A 1 KB rule can exhaust CPU and memory of whatever process translates rules.

**Additional fixture requirements from the report:** Add `limit.inline-expansion` (n=40 doubling chain -> refusal).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C20.
