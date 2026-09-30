# T10: SQL bindings, dialects, fragments and rendering

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [x] **T10 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

  Closed 2026-09-30 — sql/cases/50-rendering-and-registration (+52); tools/sqlapi.* probes; sqlt reuse twin; cpp tsan (sql_race); all six hosts agree at commit 03786e5 (conformance 2134/2134, sqlt 1309) and every finding row of the family is closed in coverage.csv.

**Destination:** `sql/cases registrations/bindings/rendering files, tools/sqlapi.* and repeated-use/concurrent SQL API probes`.

**Required cases and assertions:** Register dialects with missing/wrong quote/escape pairs, bad numeric guards, Unicode templates, malformed placeholders and inheritance changes. Use each rejected dialect twice and after reset/redefinition; C++ and other concurrent APIs must also be race-tested. Assert exact bytes and parameter order in inline/debug/params modes, invalid render modes even with zero slots, orphan-slot absence, and AST/binding reuse. Test empty/NUL/case-colliding field names, RECORD/SELECT_COLS aliases, raw relation fields through derived tables, correlate OR expressions, text NUL/backslash/quotes and relevant server string modes. Repeat Go translations to expose map-order nondeterminism; assert the emitted statement executes where the finding concerns server behavior.

**Contract prerequisite:** Decide identifier/server constraints and required escape pairings. SQL string snapshots do not substitute for a server assertion when collation, alias validity or literal parsing is disputed.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c24"></a>

### JS-C24: `checkNumericGuard` memoises the dialect before it checks, so a failing check is skipped on every later call

Source: [JS-C24](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

js7/guard.mjs): `map.defineDialect('pg-bad',{extends:'postgresql',lexical:{numericGuard:"CASE WHEN ({textCast:0} ~ '^.*$') THEN CAST({0} AS NUMERIC) ELSE NULL END"}})`, then translate `T .> FILTER(_["s"] > 0)` three times: call 0 throws "declares a numericGuard that does not carry '^-?[0-9]+(\.[0-9]+)?$'"; calls 1 and 2 return `... WHERE (CASE WHEN (... ~ '^.*$') THEN CAST(... AS NUMERIC) ELSE NULL END > 0)`.

**Additional fixture requirements from the report:** (runtime-registration checks are not in `.sqlt`); a unit test calling translate twice on a bad dialect.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C24.

<a id="js-c25"></a>

### JS-C25: A registered dialect whose `textEscape` map does not escape the quote is accepted and produces injectable inline literals

Source: [JS-C25](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

js7/esc.mjs): `map.defineDialect('my-nobs',{extends:'mariadb',lexical:{textEscape:{'\\':'\\\\'}}})`; translate `T .> FILTER(_["s"] $== "x' OR '1'='1")` gives `... = CAST('x' OR '1'='1' AS CHAR) COLLATE ...` (params mode is safe; inline mode and `LIKE`/CASE literals are not).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C25.

<a id="js-c28"></a>

### JS-C28: A `Binding.raw` field is dropped by SELECT_COLS and by derived-table wrapping, giving SQL that names a nonexistent column

Source: [JS-C28](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

js6/m.mjs; relation items/i with `TOTAL: Binding.raw('i.price * i.qty','NUM')`): `ITEMS .> SELECT_COLS("total")` gives ``SELECT `i`.`total` FROM `items` `i` `` (raw expression ignored). `ITEMS .> SORT_BY(_["ID"]) .> TAKE(2) .> FILTER(_["TOTAL"] > 5)` gives `... FROM (SELECT `i`.* ... LIMIT 2) `_sub1` WHERE (... `_sub1`.`TOTAL` ...)`. No sql/cases fixture combines `raw` with a statement.

**Additional fixture requirements from the report:** Cases `stmt.refusal.raw-field-select-cols` and `stmt.refusal.raw-field-across-derived-table`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C28.

<a id="js-c57"></a>

### JS-C57: SQL: rule-supplied identifiers are not validated like binding-supplied ones; SELECT_COLS bypasses the field allow-list on a relation with no declared fields

Source: [JS-C57](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

js-6 `cc.mjs`): ``SELECT `i`.`password`, `i`.`x``; DROP TABLE t; --` FROM `items` `i` `` (quoted, inert); ``SELECT 1 AS `a``b^@` FROM `items` `i` `` (NUL); `ITEMS .> FILTER(_["password"] $== "x")` gives E_SQL_BINDING.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C57.

## PHP report scenarios

<a id="php-c37"></a>

### PHP-C37: `Map::defineDialect` accepts quote/escape pairs that make text literals injectable

Source: [PHP-C37](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`Map::defineDialect('q1', ['extends'=>'sqlite','lexical'=>['textQuote'=>'"']]); Emit::textLiteral('q1', 'a"b OR 1=1 --')` -> `"a"b OR 1=1 --"`. `['textEscape'=>[]]` on sqlite -> `'a'b OR 1=1 --'`. `['textQuote'=>'\'','textEscape'=>["'"=>"\\'"]]` on mysql emits `'a\\'b OR 1=1 --'`, i.e. escapes the quote with a backslash but the input `\'` is not touched.

**Additional fixture requirements from the report:** `11-registration` has the string-typed case only.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C37.

<a id="php-c49"></a>

### PHP-C49: `Map::checkNumericGuard` marks the dialect as checked before validating it

Source: [PHP-C49](../../php-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`php6/t22.php`): register a derived dialect with a guard lacking ISNUM's pattern; `U + 1` throws once, then emits `CASE WHEN (u IS NOT NULL) THEN CAST(u AS DECIMAL(65,10)) END` on calls 2 and 3.

**Additional fixture requirements from the report:** (needs a runner-level test that translates twice).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C49.

<a id="php-c53"></a>

### PHP-C53: NUL characters reach the server (RECORD-key aliases, inline TEXT literals); small binding-validation gaps

Source: [PHP-C53](../../php-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`ORDERS .> MAP(RECORD("a\u{0}b", _["id"]))` translates to `SELECT "o"."id" AS "a<NUL>b" ...` on all dialects.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C53.

<a id="php-c54"></a>

### PHP-C54: RECORD-key aliases are not checked against server identifier rules

Source: [PHP-C54](../../php-code-review.md). Report labels: [low] [unconfirmed on server].

**Scenario / reported observation:**

the case-collision guard exists, but an empty key becomes `AS ""` (PostgreSQL rejects, `Binding.php:283` notes it; MariaDB expected to), and two keys sharing a 63-byte prefix collide silently on PostgreSQL after truncation (one result key where SEL has two). Rendering only: `ORDERS .> MAP(RECORD("", _["id"]))` -> `SELECT "o"."id" AS "" ...`; two 71-char keys -> two 71-char aliases (postgresql).

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C54.

<a id="php-c56"></a>

### PHP-C56: PostgreSQL text literals assume `standard_conforming_strings=on`

Source: [PHP-C56](../../php-code-review.md). Report labels: [low] [unconfirmed].

**Scenario / reported observation:**

with legacy `standard_conforming_strings=off`, `\'` inside inline SQL escapes the quote; input `\' OR 1=1 --` would terminate the literal. Default since PG 9.1 and the docs steer to params mode, but the assumption is not in `sql/MAP.md` or dialect notes.

**Additional fixture requirements from the report:** (no server).

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C56.

<a id="php-c57"></a>

### PHP-C57: Small API/validation inconsistencies in the SQL layer

Source: [PHP-C57](../../php-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

Found by: slice 7 C12, C15.
- `Binding::column('a','t',['NUM'])` interpolates an array into the message ("Array to string conversion" warning; an ErrorException under a strict handler that `tryTranslate*` does not catch) - `Binding.php:329-332`; use `get_debug_type`.
- `Hybrid::execute` returns whatever the runner returned for a pure_sql plan (native array stays an array) but a `Value` for hybrid and pure_memory (`Hybrid.php:944`). `Fragment::asValue('bogus')` is accepted when the fragment has no slots (`Fragment.php:228-238`). `new Bindings(['x'=>..., 'X'=>...])` and RELATION fields `a`/`A` silently collapse (last wins). `checkAliases` compares aliases case-sensitively although SQLite/MySQL identifiers are case-insensitive (unconfirmed).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C57.

## PYTHON report scenarios

<a id="py-c43"></a>

### PY-C43: SQL: identifiers from SEL text literals (RECORD field names, SELECT_COLS names) bypass the NUL/empty checks that binding names get

Source: [PY-C43](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

postgresql `R .> MAP(RECORD("a\u{0}b", _["id"]))` -> `SELECT "id" AS "a\x00b" FROM ...`; `R .> MAP(RECORD("", _["id"]))` -> `AS ""`; `R2 .> SELECT_COLS("a\u{0}b", "")` (relation with no declared fields) -> `SELECT "a\x00b", "" FROM "orders"`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C43.

<a id="py-c44"></a>

### PY-C44: SQL bindings: constructors accept types that make no sense; `Binding.value(type=...)` ignores everything but NUM; case-colliding names silently merge

Source: [PY-C44](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`f = Sql.translate(compile('C'), 'mariadb', {'C': Binding.column('c', type='STATEMENT')}); f.kind == 'STATEMENT'; f.as_statement()`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C44.

<a id="py-c46"></a>

### PY-C46: `Fragment.as_value(mode)` accepts an unknown mode when the fragment has no parameter slot

Source: [PY-C46](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`Sql.translate(compile('C > C'), 'mariadb', B).as_value('bogus')` returns the SQL; with a literal in the expression it raises RuntimeError.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C46.

<a id="py-c49"></a>

### PY-C49: `check_numeric_guard` memoises the dialect before it checks it, so the second use of a bad guard silently passes

Source: [PY-C49](../../python-code-review.md). Report labels: [low] [confirmed; also PHP and JS].

**Scenario / reported observation:**

`map.define_dialect('badpg', {'extends':'postgresql','lexical':{'numericGuard': "CASE WHEN ({textCast:0} ~ '^zzz$') THEN CAST({0} AS NUMERIC) ELSE NULL END"}}); e = Emit('badpg'); e.numeric_operand(Fragment(['"c"'],'TEXT','badpg'))` - first call ValueError, second returns the `'^zzz$'` guard.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C49.

<a id="py-c50"></a>

### PY-C50: Registered dialects can produce unescaped text/identifier literals; validation is per key, not per pairing

Source: [PY-C50](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`define_dialect('dq', {'extends':'ansi','lexical':{'textQuote':'"'}}); emit.text_literal('dq','a" OR 1=1 --')` -> `"a" OR 1=1 --"`. `lexical={'textEscape':{}}` on ansi -> `'a' OR 1=1 --'`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C50.

## CPP report scenarios

<a id="cpp-c12"></a>

### CPP-C12: `Map::check_numeric_guard` writes a global `std::set` from the `translate()` path

Source: [CPP-C12](../../cpp-code-review.md). Report labels: [low] [confirmed by TSan].

**Scenario / reported observation:**

`Registry::guard_checked` is a plain `std::set` that is `find`/`insert`-ed on first numeric-guard use, so two threads translating concurrently race. TSan (4 threads x 200 translates of `X > 5` on mariadb) reports `data race ... sel_sql_map.cpp:637`. Sharing one `Bindings` between threads also races on the non-atomic Value refcount (documented handle design), but the registry race is not needed by it. No doc says the SQL layer is single-threaded.

**Additional fixture requirements from the report:** not testable in `.sqlt`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C12.

<a id="cpp-c32"></a>

### CPP-C32: SQL: raw relation fields are silently replaced by a same-named column in `SELECT_COLS`, and given an empty identifier in derived tables

Source: [CPP-C32](../../cpp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

private driver, not re-run): relation `R` with fields `A -> a (NUM)` and `B -> raw "(x+1)" (NUM)`: `R .> SELECT_COLS("A","B")` gives `` SELECT `i`.`a`, `i`.`B` FROM `items` `i` ``; `R .> TAKE(3) .> FILTER(_["B"] > 1)` gives `` ... WHERE (CASE WHEN (`_sub1`.`` REGEXP ...`` ; contrast `R .> FILTER(_["B"] > 1)` gives `WHERE ((x+1) > 1)` (correct).

**Additional fixture requirements from the report:** Suggest `stmt.select-cols.raw-field-is-refused` and `stmt.derived.raw-field-is-refused`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C32.

<a id="cpp-c36"></a>

### CPP-C36: `Map::check_numeric_guard` memoises the dialect before validating, so a bad numericGuard is refused once and then silently accepted

Source: [CPP-C36](../../cpp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

scratchpad `cpp7/guard.cpp`): dialect `evil` = `extending("mariadb")` with `numericGuard` = `CASE WHEN ({0} REGEXP 'x') THEN CAST({0} AS DECIMAL(65,10)) ELSE NULL END`; translate `X > 5` (X an UNKNOWN column) three times. First: `runtime_error: ... declares a numericGuard that does not carry '\\A-?[0-9]+...'`. Second and third: `` (CASE WHEN (`o`.`x` REGEXP 'x') THEN CAST(`o`.`x` AS DECIMAL(65,10)) ELSE NULL END > 5) ``.

**Additional fixture requirements from the report:** (`neutral.register.*` cases assert only the first refusal). Suggest a case that translates twice.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C36.

<a id="cpp-c53"></a>

### CPP-C53: A registered dialect can drop or misdefine `textEscape`, and text literals are then emitted unescaped

Source: [CPP-C53](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`cpp7/esc.cpp`, not re-run): `DialectSpec::extending("sqlite").lexical("textEscape", std::nullopt)` (or `lexical_escapes("textEscape", {{"x","y"}})`), translate `NAME $== "a' OR '1'='1"` inline -> `CAST('a' OR '1'='1' AS TEXT) COLLATE BINARY = ...` (injection). Shipped dialects emit `'a'' OR ''1''=''1'`.

**Additional fixture requirements from the report:** Suggest `neutral.register.text-escape-withdrawn-refuses-text-literals`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C53.

<a id="cpp-c55"></a>

### CPP-C55: SQL: case-colliding duplicate relation field names: C++ keeps the first, Python keeps the last

Source: [CPP-C55](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

private driver): `drv mariadb 'ANY(R, I, I["A"] > 0)' 'R=r:t:t:::A/x/NUM;a/y/NUM'` gives `... (`t`.`x` > 0)`; Python gives `(`t`.`y` > 0)`.

**Additional fixture requirements from the report:** Suggest `bind.relation.duplicate-field-after-uppercasing`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C55.

<a id="cpp-c56"></a>

### CPP-C56: SQL: `correlate` (application SQL) is spliced without parentheses into an AND chain

Source: [CPP-C56](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

private driver): `drv mariadb 'ANY(ITEMS, I, I["QTY"] > 0)' 'ITEMS=r:oi:oi::oi.a=o.id OR oi.b=o.id:QTY/qty/NUM'` gives `` EXISTS (SELECT 1 FROM `oi` `oi` WHERE oi.a=o.id OR oi.b=o.id AND ((`oi`.`qty` > 0)) IS TRUE) ``.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C56.

<a id="cpp-c57"></a>

### CPP-C57: SQL: the discarded pre-LINK `compile_statement` leaves orphan parameters in `Fragment::params()`

Source: [CPP-C57](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`R .> FILTER(_["N"] $== "zz") .> SORT_BY(_["N"]) .> LINK(S, _1["A"] == _2["A"] AND _2["M"] $== "yy")` in params mode gives `params=3 bindings=2`.

**Additional fixture requirements from the report:** cases compare `bindings()`; suggest asserting `params().size() == bindings().size()` in the runner.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C57.

<a id="cpp-c58"></a>

### CPP-C58: SQL: NUL bytes reach the SQL through RECORD aliases and inline text literals, although binding identifiers refuse them

Source: [CPP-C58](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

private driver): `drv sqlite 'R .> MAP(RECORD("a\u{0}b", _["A"]))' 'R=r:items:i:::A/a/NUM' --stmt` gives `SELECT "i"."a" AS "a\0b" FROM ...` (hexdump shows 0x00).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C58.

## LISP report scenarios

<a id="lisp-c36"></a>

### LISP-C36: Missing or incomplete `textEscape` emits unescaped inline text (SQL injection class)

Source: [LISP-C36](../../lisp-code-review.md). Report labels: [low] [confirmed; needs an application-registered dialect].

**Scenario / reported observation:**

`(define-dialect "pg-noesc" '(:extends "postgresql" :version "16" :target t :lexical (("textEscape" . nil))))` then `T $== "a' OR '1'='1"` inline -> `CAST('a' OR '1'='1' AS TEXT)`; `:lexical (("textEscape" ("x" . "y")))` behaves the same. s7: `NAME $== "it's' OR 1=1 --"` -> `CAST('it's' OR 1=1 --' AS TEXT)`.

**Additional fixture requirements from the report:** Suggest a `--- register` case that must be refused.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C36.

<a id="lisp-c38"></a>

### LISP-C38: About 21 SQL refusal messages contain a literal `~` + newline

Source: [LISP-C38](../../lisp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`(1, 2)` -> `a list is not a SQL value; a list can only be the thing an ~\naggregate iterates`; `O .> BUCKET(_,...) .> MAP(...) .> ... _K` -> `a row of a relation has no key: SQL rows ~\nare unordered ...`.

**Additional fixture requirements from the report:** (messages are not contract).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C38.

<a id="lisp-c40"></a>

### LISP-C40: SQL: parameter slots of a discarded validation pass stay in `fragment-params`

Source: [LISP-C40](../../lisp-code-review.md). Report labels: [low] [confirmed; JS reports the same].

**Scenario / reported observation:**

`O .> FILTER(_["AMT"] > 3) .> SORT(_["AMT"]) .> LINK(C, _["CID"] == C["ID"])` -> `(length (fragment-params f))` = 2 but the part list references only slot 2. `(bindings f)` is right; `fragment-params` and the `:debug` numbering carry an orphan. An application binding `fragment-params` would send an extra parameter.

**Additional fixture requirements from the report:** Suggest a `--- mode params` case with a literal in a FILTER before a SORT and a LINK.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C40.

<a id="lisp-c41"></a>

### LISP-C41: SQL: NUL characters in TEXT literals and RECORD aliases are emitted as-is

Source: [LISP-C41](../../lisp-code-review.md). Report labels: [low] [unconfirmed impact].

**Scenario / reported observation:**

`X $== "a\u{0}b"` inline emits a raw NUL. No injection was found (a truncating driver leaves an unterminated string = syntax error), but PostgreSQL cannot hold NUL in text and other servers differ; not checked against a live server.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C41.

<a id="lisp-c42"></a>

### LISP-C42: `check-numeric-guard` memoises the dialect before verifying it

Source: [LISP-C42](../../lisp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

register `pgbad` extending postgresql with `numericGuard "CAST({0} AS NUMERIC)"`; the first `translate` of `NAME + 1` signals; the second returns `(CAST(CAST("o"."name" AS TEXT) ...`, the guard the check rejected.

**Additional fixture requirements from the report:** (unit lane could pin it).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C42.

## GO report scenarios

<a id="go-c18"></a>

### GO-C18: SQL: derived-table column order is nondeterministic between runs (Go map iteration)

Source: [GO-C18](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

as reported): `ITEMS .> TAKE(5) .> LINK(TAGS, a, b, a["NAME"] $== b["TAG"])` (statement, mariadb) run 6 times: the select list alternates between `_sub1`.`price`, `_sub1`.`qty`, `_sub1`.`name` and a different order. The corpus passes only because the cases use one field or get lucky. Not re-run by the synthesizer: the synthesizer's stand-in program (a self-join of ITEMS) was refused with `E_SQL_SHAPE`, which did not exercise this path.

**Additional fixture requirements from the report:** the sqlt harness runs each case once; run each twice (or 5x) and require equal text, plus a two-field wrapped-then-linked case.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C18.

<a id="go-c19"></a>

### GO-C19: SQL: `ColumnBinding`/`RawBinding` ignore `collation`, `splitSargable`, and do not validate `prefilter`

Source: [GO-C19](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

Go vs JS, mariadb, `ANY(T, _ $== "x")` over a TEXT column): `collation="sargable"` -> Go `... CAST(t.c AS CHAR) COLLATE ... = CAST('x' AS CHAR) ...` with no coarse `t.c = 'x'` term; JS emits `((t.c = 'x') AND (CAST...))`. `splitSargable=true`: Go single EXISTS, JS `EXISTS(coarse) AND EXISTS(exact)`. `prefilter="bogus"` / `collation="bogus"`: Go accepts, JS `E_SQL_BINDING`.

**Additional fixture requirements from the report:** `bind.collation.*` cases exist but the Go harness binds them through pre-resolved `bindCol`; add Go unit tests calling `ColumnBinding` with string collation/prefilter values.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C19.

<a id="go-c24"></a>

### GO-C24: `Emit.Fill` mangles non-ASCII bytes in templates (`string(tpl[i])`)

Source: [GO-C24](../../go-code-review.md). Report labels: [medium/low] [confirmed] [i].

**Scenario / reported observation:**

`sql.Define("mariadb","funcs","UPPER", {"tpl":"F({0}, 'é ż')","ret":"TEXT"})`, translate `UPPER(NAME)` -> Go `F(`o`.`name`, 'Ã© Å¼')`; JS `F(`o`.`name`, 'é ż')`. go-6: `MY_CRC('zażółć', {0})` -> `MY_CRC('zaÅ¼Ã³ÅÄ', 'x')`.

**Additional fixture requirements from the report:** Add a `.sqlt` `--- register` case with a non-ASCII template (it would catch every host).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C24.

<a id="go-c37"></a>

### GO-C37: SQL: aliases derived from the program (RECORD keys, SELECT_COLS names) bypass the empty/NUL identifier checks that bindings get

Source: [GO-C37](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`ITEMS .> MAP(RECORD("", _["NAME"]))` -> `SELECT "i"."name" AS "" FROM ...`; `RECORD("a\u{0}b", ...)` -> alias containing byte 0.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C37.

<a id="go-c39"></a>

### GO-C39: `Emit.Fill` edge cases differ from JS/Python/PHP (registered templates only)

Source: [GO-C39](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

(a) `{n:}` with `n >= len(args)`: JS/Python/PHP join the empty sub-list and emit nothing; Go refuses ("neither an argument nor a lexical entry"). `Define(mariadb funcs UPPER {"tpl":"F({0}, {1:})"})`, `UPPER(NAME)` -> Go `E_SQL_UNSUPPORTED`, JS ``F(`o`.`name`, )``. (b) A lexical key whose value is the empty string: JS pushes it, Go refuses `valStr == ""` (`emit.go:337`); dialect extending mariadb with `lexical.textCollate = ""`, tpl `F({0}{textCollate})` -> Go `E_SQL_UNSUPPORTED`, JS ``F(`o`.`name`)``. MAP.md treats null as the withdrawal, not "".

**Additional fixture requirements from the report:** Add two `--- register` cases.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C39.

<a id="go-c40"></a>

### GO-C40: SQL: small Go/JS divergences and nondeterminism

Source: [GO-C40](../../go-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

- **Found by:** go-6 C14.
- `normalise.go:96-101` `constantKey` treats a TEXT key `""` as "not constant" (`R[""] = 1; R[""]` is `E_SQL_ASSIGN` at the target) whereas JS accepts the empty key and refuses later with `E_SQL_SHAPE`. Both refuse; codes differ.
- `binding.go:277-292 / 330-353`: `NewBindings` records `order` by ranging a Go map, so `CheckAliases` reports "relations X and Y share the alias" with X/Y in random order; `makeRelation` with the `map[string]*Binding` form gives a nondeterministic winner when two field names differ only by ASCII case and adds duplicate FieldOrder entries; the `[]FieldEntry` form also appends duplicates.
- `statement.go:2076-2094` `withJoinBinders` compares `&plan.Joins[i] == &join` on a by-value parameter (never true; the fallback is what works). Dead code, harmless.
- **Fix sketch:** sort names / dedupe case-folded field names; fix or delete the dead comparison.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C40.

<a id="go-c43"></a>

### GO-C43: SQL: NUL in an inline text literal is emitted raw

Source: [GO-C43](../../go-code-review.md). Report labels: [low] [unconfirmed effect].

**Scenario / reported observation:**

TEXT may contain U+0000 (spec 3). Inline SQL then carries a raw NUL byte. Through a C-string client API the statement is truncated at the NUL, losing the closing quote; the reviewer expects a syntax error rather than injection but could not run a server. `params` mode is unaffected. Demonstrated only that Go emits the raw byte (`V & "x'y"` with V = "a'b\\c\x00d" on mariadb/postgresql/sqlite). JS/PHP/Python do the same.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C43.
