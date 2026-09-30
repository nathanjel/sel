# T09: SQL kinds, numeric fidelity and server limits

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [x] **T09 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

  Closed 2026-09-30 — sql/cases/49-kind-guarantees, 19-kind-warrant; sql/oracle expressions/rows; tools/check-sql-limits.php; all six hosts agree at commit 3bc54e6 (conformance 2134/2134, sqlt 1309) and every finding row of the family is closed in coverage.csv.

**Destination:** `sql/cases kind-warrant/count/IN cases plus executed SQLite, MariaDB/MySQL and PostgreSQL fixtures as relevant`.

**Required cases and assertions:** Cross NUM/BOOL/TEXT/BIN/UNKNOWN through IF/COND/coalescing, SUM and JOIN. Test invalid data as well as values that happen to coerce correctly. For exact IN and sargable comparison, include "01", "1", 1, case variants and collations that would equate byte-distinct values. Execute SQLite MIN/MAX with numeric columns and quoted numeric literals; use high-precision decimal arithmetic on MySQL-family servers. Test TAKE/DROP 2.0, 2^53±1, int64 bounds, larger server limits and invalid counts; check error catalogue membership and literal precision. Unroll lists around server parser-depth limits and compare accepted SQL with evaluator results.

**Contract prerequisite:** Specify supported server ranges/caveats and warranted kinds. Safe refusals and supported SQL are distinct outcomes; a translator parity check alone cannot prove valid server behavior.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c27"></a>

### JS-C27: SQL `unify()` promotes an undeclared (UNKNOWN) column to a certain kind, bypassing the kind warrant

Source: [JS-C27](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

mariadb, F = `Binding.column('f','t')` undeclared, A likewise): `F AND TRUE` gives E_SQL_SHAPE (correct), but `IF(TRUE, F, TRUE) AND TRUE` and `(F ?? TRUE) AND TRUE` translate to `(CASE WHEN TRUE THEN f ELSE TRUE END AND TRUE)` / `(COALESCE(f, TRUE) AND TRUE)`. SEL: `F = 1; IF(TRUE, F, TRUE) AND TRUE` is E_NOT_BOOL. `A + 1 > 0` is guarded, but `IF(N > 0, A, 0) + 1 > 0` and `(A ?? 0) + 1 > 0` are `CASE ... END + 1` unguarded. PostgreSQL turns the boolean case into a run-time type error, so the exposure is the MySQL family and SQLite.

**Additional fixture requirements from the report:** Cases `warrant.bool.unknown-through-coalesce` and `warrant.numeric.unknown-through-if` in 19-kind-warrant.sqlt.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C27.

<a id="js-c55"></a>

### JS-C55: SQL LIMIT/OFFSET/TAKE/DROP counts go through a JS `Number`: exponent notation and rounding in the SQL text

Source: [JS-C55](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

js-6 `d.mjs`): `ITEMS .> TAKE(99999999999999999999999)` gives `LIMIT 1e+23` (Python: `LIMIT 99999999999999999999999`); `TAKE(9007199254740993)` gives `LIMIT 9007199254740992`; `DROP(99999999999999999999999)` gives `OFFSET 1e+23`.

**Additional fixture requirements from the report:** 28-slice-overflow.sqlt covers only the DROP sum. Case `stmt.take.over-2^53`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C55.

<a id="js-c56"></a>

### JS-C56: SQL TAKE/DROP counts with a written scale are refused although SEL accepts them

Source: [JS-C56](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`ITEMS .> TAKE(2.0)` gives E_NOT_INT; `node js/bin/sel.mjs -e '(1,2,3) .> TAKE(2.0)'` gives `{"1"=t"1", "2"=t"2"}`. Also `1.0 * 2` and a `value` binding "2.0". Safe direction, wrong code.

**Additional fixture requirements from the report:** `stmt.refusal.take-float` pins only 1.5. Add `stmt.take.whole-number-with-scale`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C56.

## PHP report scenarios

<a id="php-c27"></a>

### PHP-C27: Group-path `SUM(g, body)` skips every kind check the relation path applies

Source: [PHP-C27](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`php6/t9.php`): `O .> BUCKET(_["CAT"], RECORD("s", SUM(_, _["STATUS"])))` -> `COALESCE(SUM(\`status\`), 0)`; same for BOOL (`SUM(\`flag\`)`) and `SUM(_, "x")` (compounds with PHP-C7 into `SUM(1)`).

**Additional fixture requirements from the report:** suggest `--- error E_SQL_SHAPE` cases for a TEXT and a BOOL field under a bucket SUM.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C27.

<a id="php-c28"></a>

### PHP-C28: Kind unification launders UNKNOWN into NUM/BOOL, so numeric and boolean guards are skipped

Source: [PHP-C28](../../php-code-review.md). Report labels: [medium] [confirmed; cross-host design].

**Scenario / reported observation:**

`php6/t7.php`, mariadb): `U + 1` is guarded (`CASE WHEN (u REGEXP ...) THEN CAST(...) ELSE NULL END + 1`) but `(U ?? 1) + 1` is `(COALESCE(\`u\`, 1) + 1)` and `IF(F, U, 1) == 1` is `(CASE WHEN f THEN u ELSE 1 END = 1)`; `IF(F, U, TRUE) AND TRUE` and `(U ?? TRUE) AND F` emit with no refusal while `U AND TRUE` is refused. `ABS(U ?? 1)`, `MIN(U ?? 1, 2)`, `LEFT("abc", U ?? 1)`, `SUM((1,2), _ * (U ?? 1))` likewise skip the guard `ABS(U)` gets.

**Additional fixture requirements from the report:** suggest `--- error` cases for `IF(F, U, TRUE) AND TRUE` and a guarded-output case for `(U ?? 1) + 1`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C28.

<a id="php-c31"></a>

### PHP-C31: JOIN over a static list or columns binding bypasses the `&` BOOL guard

Source: [PHP-C31](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`php6/t10.php`, `F` a BOOL column): `JOIN((F,"a"), "-")` -> `CONCAT(CONCAT(\`f\`, '-'), 'a')`; `JOIN((T,"a"), F)` uses the BOOL column as separator; a `columns` binding of (TEXT, BOOL) joins without complaint; `F & T` is refused.

**Additional fixture requirements from the report:** suggest `refuse.join-bool-element`, `refuse.join-bool-separator`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C31.

<a id="php-c33"></a>

### PHP-C33: Text-literal operands of arithmetic are emitted as bare quoted strings, which MariaDB/MySQL evaluate as DOUBLE

Source: [PHP-C33](../../php-code-review.md). Report labels: [medium] [unconfirmed: needs a MariaDB/MySQL server].

**Scenario / reported observation:**

emission confirmed (`php6/t6.php`): `"0.1" + "0.2" == 0.3` on mariadb -> `(('0.1' + '0.2') = 0.3)`; SEL answers TRUE (`php php/bin/sel -e '"0.1" + "0.2" == 0.3'`). Server behaviour (expected 0) not run. Same for `N * "0.1"` and a TEXT-typed `value` binding in arithmetic.

**Additional fixture requirements from the report:** oracle line `"0.1" + "0.2" == 0.3` and a `.sqlt` case pinning the emitted operand.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C33.

<a id="php-c50"></a>

### PHP-C50: TAKE/DROP counts written as whole numbers with a scale are refused with E_NOT_INT by the translator

Source: [PHP-C50](../../php-code-review.md). Report labels: [low] [confirmed, all hosts].

**Scenario / reported observation:**

`O .> TAKE(2.0)` -> E_NOT_INT (SqlError); `php php/bin/sel -e 'TAKE((3,1,2), 2.0)'` succeeds.

**Additional fixture requirements from the report:** `stmt.refusal.take-float` only pins 1.5 - suggest `stmt.take.whole-number-with-scale`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C50.

<a id="php-c51"></a>

### PHP-C51: Over-refusals from stale "measured" argument-kind tables

Source: [PHP-C51](../../php-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

SEL accepts BOOL/BIN arguments in functions the tables refuse: `IS_BLANK(TRUE)` is FALSE, `COALESCE(TRUE, FALSE)`, `IS_NULL(TRUE)` succeed, yet `IS_BLANK(F)`, `IS_NULL(F)`, `COALESCE(F, F2)`, `IS_BLANK(B)` for BOOL/BIN columns are refused (`php6/t11.php`). The other way round `??`/`???` take no argument-kind check (`F ??? F2`, `B ??? B2` translate). Safe direction only.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C51.

<a id="php-c52"></a>

### PHP-C52: Numeric literals of more than 65 digits are emitted bare on MariaDB/MySQL with no caveat

Source: [PHP-C52](../../php-code-review.md). Report labels: [low] [unconfirmed].

**Scenario / reported observation:**

`sql-translation.md` 11.3 lists scale caps but not the literal-width limit. From memory of the manuals a decimal literal wider than DECIMAL(65) becomes DOUBLE, so `<70 nines> + 1 == <70 nines>` (SEL FALSE) would be TRUE; no server available. `caveats` is empty (`php6/t21.php`).

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C52.

<a id="php-c58"></a>

### PHP-C58: An IN list or unrolled aggregate of about 1000 or more elements translates but fails at run time

Source: [PHP-C58](../../php-code-review.md). Report labels: [low] [confirmed on SQLite].

**Scenario / reported observation:**

`php6/t15.php`): `T IN ("k1", ..., "kN")` on sqlite via pdo_sqlite: N=900 runs; N=1100 raises `Expression tree is too large (maximum depth 1000)`. Not run on MariaDB/PostgreSQL.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C58.

## PYTHON report scenarios

<a id="py-c19"></a>

### PY-C19: SQL: `JOIN` over a static list accepts BOOL and BIN elements/separators that SEL rejects with `E_NOT_TEXT`

Source: [PY-C19](../../python-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

columns T TEXT, F BOOL, X BIN. mariadb `JOIN((T,F), ",")` -> ``CONCAT(CONCAT(`t`, ','), `f`)``; `JOIN((T,T), F)` and `JOIN((F,F2), ",")` likewise; `JOIN((X,T), ",")` -> kind BIN. postgresql: `(CAST("t" AS TEXT) || ...CAST("f" AS TEXT))` (yields 'true'/'false'). SEL: `JOIN(("a", TRUE), ",")` -> E_NOT_TEXT; `JOIN(("a","b"), TRUE)` -> E_NOT_TEXT; `JOIN((TO_UTF8("a"),"b"), ",")` -> E_NOT_TEXT.

**Additional fixture requirements from the report:** (`sql/cases/12-aggregates.sqlt` covers text only). Suggest `agg.join.bool-element-refused`, `agg.join.bin-separator-refused`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C19.

<a id="py-c20"></a>

### PY-C20: SQL: `x IN (list)` with an `exact` TEXT needle skips the byte cast on every list item, so MariaDB/MySQL/SQLite compare numerically

Source: [PY-C20](../../python-code-review.md). Report labels: [medium] [confirmed; server behaviour reasoned].

**Scenario / reported observation:**

`T` = `Binding.column('t', type='TEXT', exact=True)`, `C` = NUM column. mariadb `T IN ("a", 3)` -> ``((`t` = 'a') OR (`t` = 3))`` (`'3.0' = 3` and `'3abc' = 3` are true on MariaDB: reasoned, not run); compare `T EQL 3` -> ``(`t` = CAST(3 AS CHAR) COLLATE utf8mb4_nopad_bin)`` which is correct. Real SQLite check: table `x(t TEXT, c NUMERIC)` with row ('3.0', 3): `("t" = 'a') OR ("t" = "c")` returns 1, SEL `"3.0" IN ("a", 3)` is FALSE. PostgreSQL errors loudly (text = integer), acceptable.

**Additional fixture requirements from the report:** Suggest `op.in.exact-needle-number-item` for mariadb and sqlite.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C20.

<a id="py-c21"></a>

### PY-C21: SQL: `x IN <relation>` never checks that needle and column kinds are comparable

Source: [PY-C21](../../python-code-review.md). Report labels: [medium] [confirmed; server behaviour reasoned].

**Scenario / reported observation:**

`S = relation('sk', alias='s', fields={'sku': column('sku', type='TEXT')}, scalar='sku')`, F BOOL column, X BIN column: mariadb `F IN S` -> ``((CAST(`f` AS CHAR) COLLATE utf8mb4_nopad_bin IN (SELECT CAST(`sku` AS CHAR) ... FROM `sk` `s` WHERE TRUE)) IS TRUE)``; `TRUE IN S`, `X IN S` translate too (postgresql, sqlite same). A server would select rows with sku '1'/'true' (reasoned). Versus `F IN (T, T)` and `TRUE IN ("1","a")`, which are refused.

**Additional fixture requirements from the report:** Suggest `agg.in-relation.bool-needle-refused`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C21.

<a id="py-c41"></a>

### PY-C41: SQL: TAKE/DROP counts above the server's integer range translate to SQL every server rejects

Source: [PY-C41](../../python-code-review.md). Report labels: [low] [confirmed; server behaviour reasoned].

**Scenario / reported observation:**

`sel.sql.Sql.translate(compile('R .> TAKE(99999999999999999999999)'), 'postgresql', {'R': relation('orders', alias='o')})` -> `LIMIT 99999999999999999999999`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C41.

<a id="py-c42"></a>

### PY-C42: SQL: unrolled IN / ANY / ALL / SUM over more than about 1000 list elements is rejected by SQLite at run time (O(n) deep on every server)

Source: [PY-C42](../../python-code-review.md). Report labels: [low] [confirmed on SQLite].

**Scenario / reported observation:**

`T IN ("v0", ..., "v1199")` on sqlite, execute the inline SQL.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C42.

## CPP report scenarios

<a id="cpp-c29"></a>

### CPP-C29: SQL: kind unification launders an undeclared column into BOOL or NUM

Source: [CPP-C29](../../cpp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

private driver, not re-run): `drv mariadb 'IF(F, X, TRUE)' F=c:t.f:BOOL X=c:t.x` is accepted as a condition: `` CASE WHEN `t`.`f` THEN `t`.`x` ELSE TRUE END `` (a bare `X` is correctly refused, E_SQL_SHAPE). `drv mariadb 'IF(F, X, 1) + 1 == 2' ...` gives `` ((CASE WHEN `t`.`f` THEN `t`.`x` ELSE 1 END + 1) = 2) `` with no guard, whereas `X + 1 == 2` gets the REGEXP/CAST guard. On MariaDB `'abc'+1` is 1 (server behaviour reasoned from sql-kinds.md §4.1-4.2, not run); SEL raises E_NOT_BOOL / E_NOT_NUM.

**Additional fixture requirements from the report:** Suggest `warrant.bool.if-cannot-launder-an-undeclared-column` and `warrant.numeric.if-branch-unknown-is-guarded`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C29.

<a id="cpp-c30"></a>

### CPP-C30: SQL: `IN` over a literal list against an `exact` column leaves non-text items uncast

Source: [CPP-C30](../../cpp-code-review.md). Report labels: [medium] [confirmed; server semantics reasoned].

**Scenario / reported observation:**

private driver, not re-run): `drv mariadb 'S IN (1, 2, "a")' S=c:t.s:TEXT:x` gives `` (((`t`.`s` = 1) OR (`t`.`s` = 2)) OR (`t`.`s` = 'a')) ``. `S IN (N, M)` with NUM/UNKNOWN columns gives `` ((`t`.`s` = `t`.`n`) OR (`t`.`s` = `t`.`m`)) ``. Contrast `S IN (1)` gives `` (`t`.`s` = CAST(1 AS CHAR) COLLATE utf8mb4_nopad_bin) ``.

**Additional fixture requirements from the report:** `bind.in.exact.*` use only text literals. Suggest `bind.in.exact.numeric-literal-is-cast`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C30.

<a id="cpp-c31"></a>

### CPP-C31: SQL: `sargable` on PostgreSQL and SQLite emits a bare `=` under the column's own collation; the docs say the exact comparison is kept

Source: [CPP-C31](../../cpp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

private driver, not re-run): `drv sqlite 'A $== "x"' A=c:t.a:TEXT:s` gives `("t"."a" = 'x')`; without the flag `(CAST("t"."a" AS TEXT) COLLATE BINARY = CAST('x' AS TEXT) COLLATE BINARY)`. Postgres: `("t"."a" = 'x')` vs `... COLLATE "C" ...`.

**Additional fixture requirements from the report:** the two cases pin the current behaviour; the doc and cases disagree.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C31.

<a id="cpp-c59"></a>

### CPP-C59: SQL: TAKE/DROP count evaluation is stricter than the evaluator, and uses codes outside `sql/errors.md`

Source: [CPP-C59](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`TAKE((1,2,3), 2.0)`, `-0` and `99999999999999999999999` are accepted by the evaluator (`{"1","2"}`, empty, all three) but the translator answers E_NOT_INT / E_RANGE / E_RANGE. All are safe refusals, but E_NOT_INT/E_RANGE/E_NOT_NUM/E_ARITY/E_BAD_ARG are SEL runtime/compile codes that `sql/errors.md`'s registry does not list for translator refusals (every host does the same, so it is a registry gap). `TAKE(N)` with an unbound/column `N` is reported as E_SQL_INVALID ("SEL rejects ... E_UNDEF_VAR"), which reads as if the rule were wrong.

**Additional fixture requirements from the report:** `stmt.refusal.take-float` pins E_NOT_INT for 1.5 only; nothing for 2.0.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C59.

## LISP report scenarios

<a id="lisp-c24"></a>

### LISP-C24: SQL: UNKNOWN laundered to NUM by kind unification bypasses the numeric guard

Source: [LISP-C24](../../lisp-code-review.md). Report labels: [medium] [confirmed; cross-host].

**Scenario / reported observation:**

mariadb, X undeclared, P BOOL): `IF(P, X, 1) + 1` -> `(CASE WHEN `p` THEN `x` ELSE 1 END + 1)`, kind NUM; `(X ?? 0) + 1` -> `(COALESCE(`x`, 0) + 1)`; `COALESCE(X, 0) > 5` (postgresql) -> `(COALESCE("x", 0) > 5)`; versus `X + 1` -> `CASE WHEN (`x` REGEXP ...) THEN CAST(`x` AS DECIMAL(65,10)) ELSE NULL END + 1`. For x='abc' MariaDB gives 1 where SEL raises E_NOT_NUM; PostgreSQL raises a type error. JS the same.

**Additional fixture requirements from the report:** Suggest `warrant.numeric.coalesce-over-an-undeclared-column`, `warrant.numeric.if-branches-with-an-undeclared-column`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C24.

<a id="lisp-c25"></a>

### LISP-C25: SQL: `IN (numeric literals)` on an EXACT text column compares bare `x = 1`

Source: [LISP-C25](../../lisp-code-review.md). Report labels: [medium] [confirmed by reading; not run against a server].

**Scenario / reported observation:**

bindings `X = binding-column("x", nil, :text, :exact t)`; `X IN (1, 2)` mariadb -> `((`x` = 1) OR (`x` = 2))`. `X IN ("1","2")` is fine.

**Additional fixture requirements from the report:** Suggest `op.in.exact-column-against-numeric-literals`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C25.

<a id="lisp-c39"></a>

### LISP-C39: SQL: TAKE/DROP counts are emitted as arbitrary-size integers

Source: [LISP-C39](../../lisp-code-review.md). Report labels: [low] [confirmed; JS also diverges].

**Scenario / reported observation:**

`R .> TAKE(99999999999999999999999)` -> `LIMIT 99999999999999999999999` in every dialect; PostgreSQL/SQLite reject it (out of bigint range), MariaDB rejects above 18446744073709551615. `DROP` likewise; `DROP(9007199254740993)` is exact here but JS prints `9007199254740992` and `LIMIT 1e+23` for 1e23 (mostly a JS bug).

**Additional fixture requirements from the report:** Suggest `stmt.take.count-beyond-int64`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C39.

## GO report scenarios

<a id="go-c15"></a>

### GO-C15: SQL: UNKNOWN "unifies with anything", so an undeclared column routed through IF/COND/`??`/`???` skips the numeric guard

Source: [GO-C15](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

measured on SQLite via Python sqlite3): `IF(FLAG, 1, UNK) < 1` -> `(CAST(CASE WHEN "o"."flag" THEN '1' ELSE "o"."unk" END AS NUMERIC) < CAST('1' AS NUMERIC))`; row flag=0, unk='abc': SQLite TRUE, SEL `E_NOT_NUM: not a number: "abc"`. The oracle fuzz found 18 of 234 random expressions violating the warrant this way (e.g. `A < IF(TRUE, U, -1)`, `(U ?? -1) <= (-1 * A)`). On mariadb `IF(FLAG, 1, UNK) > 0` emits `CASE WHEN flag THEN 1 ELSE unk END > 0` with no guard (reasoned; MariaDB coerces 'abc' to 0).

**Additional fixture requirements from the report:** Add `warrant.unify.unknown-branch-stays-unknown` (mariadb: guard present; sqlite: E_SQL_UNSUPPORTED).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C15.

<a id="go-c16"></a>

### GO-C16: SQL: SUM bodies accept UNKNOWN without a numeric guard

Source: [GO-C16](../../go-code-review.md). Report labels: [medium] [confirmed for SQLite, reasoned for MariaDB/MySQL].

**Scenario / reported observation:**

bindings U=UNKNOWN col, N=NUM col, COLS=(U,N), T=relation(U,N). sqlite `SUM(COLS, _)` -> `("t"."u" + "t"."n")`; row u='abc', n=1: SQLite gives 1, SEL `E_NOT_NUM`. `SUM(T, _["U"])` -> `COALESCE(SUM("t"."u"), 0)` gives 0.0 on 'abc' in SQLite (measured). MariaDB emits bare `SUM(`t`.`u`)` (reasoned). PostgreSQL casts for COLS (loud, acceptable), `SUM("t"."u")` is a type error (loud).

**Additional fixture requirements from the report:** Add `warrant.sum.unknown-body`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C16.

<a id="go-c17"></a>

### GO-C17: SQLite MIN/MAX over a numeric column and a quoted numeric literal compare storage classes, not numbers

Source: [GO-C17](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`MIN(A, 1) > 1` (A NUM column, sqlite) -> `(CAST(min("o"."a", '1') AS NUMERIC) > CAST('1' AS NUMERIC))`; row a=2.5: SQLite TRUE (`select min(2.5,'0')` is 2.5), SEL `MIN(2.5, 1)` is 1 so FALSE. The oracle also flagged `MIN(LEN(U), MIN(1,1))` and `MIN(1, A) IN (A, A)`.

**Additional fixture requirements from the report:** for numeric-affinity columns. Add an oracle case with an INTEGER/REAL column.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C17.

<a id="go-c38"></a>

### GO-C38: SQL: TAKE/DROP counts - Go refuses beyond int64 with a non-registry code; JS silently loses precision beyond 2^53

Source: [GO-C38](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`ITEMS .> TAKE(18446744073709551616)` -> Go `E_RANGE`, JS `LIMIT 18446744073709552000`; `TAKE(9223372036854775807)` Go exact, JS `...776000`. Go is the better behaviour; no case pins it.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C38.
