# T11: Hybrid execution, keys, order and context

[Worklist](../README.md) · [Test harness and completion rules](../00-tests.md)

- [x] **T11 — Create and run this shared regression family across JS, PHP, Python, C++, Lisp and Go.**

  Closed 2026-09-30 — sql/cases/51-hybrid-parity, 25-hybrid-plans, 52-audit-cases; sql/oracle/hybrid.json; tools/check-hybrid-parity*.{mjs,py}, -driver.py, -go.py; php sqlo hybrid; all six hosts agree at commit 3bc54e6 (conformance 2134/2134, sqlt 1309) and every finding row of the family is closed in coverage.csv.

**Destination:** `sql/cases/25-hybrid-plans.sqlt plus executing hybrid API/oracle fixtures`.

**Required cases and assertions:** For each source run direct SEL, the chosen hybrid plan and pure-memory fallback on the same ordered rows and context. Check ordered tree dumps, original FILTER keys/_K, implicit LINK binder names, source_tables, helper effects and final caller context. Include FILTER then local MAP/FILTER, partial MAP with a custom function raising on a row removed by downstream TAKE/DROP, SORT key failures hidden by rewrites, same-name helpers and 4/5-argument binders. Exercise nonempty and empty contexts and preserved unrelated variables. For SQL ordering use explicit unique ordering keys; test BUCKET first-appearance order, ties, derived tables and DISTINCT with unprojected sort keys on real relevant servers. Repeated planning must leave the AST unchanged and stay bounded.

**Contract prerequisite:** Set the caller-context mutation and row-key/order contract. If SQL cannot preserve it, require the documented fallback/refusal. Do not assert a database natural row order.

The entries below are scenario requirements, grouped by reporting host for traceability. Merge equivalent repros into one named shared fixture, and record all covered IDs in its note/coverage ledger. Retain every distinct variant. These are report-derived observations and proposed expectations; resolve them against the spec before making them golden. “None” in an original conformance-gap field means missing coverage, not no testing work.

## JS report scenarios

<a id="js-c22"></a>

### JS-C22: Hybrid planner: a SQL prefix ending in a FILTER loses FILTER's retained keys; the continuation sees SQL row ordinals

Source: [JS-C22](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

js7/h.mjs, SQLite, rows id 1..6, `BACKWARDS` has no SQL spelling on sqlite):
  - `ORDERS .> FILTER(_["id"]>2) .> FILTER(BACKWARDS(_["name"]) $== "ahpla")`: `run()` gives `{"3"={id=3,...}}`; `executeHybrid` gives `{"1"={id=3,...}}`.
  - `ORDERS .> FILTER(_["id"]>2) .> MAP(RECORD("k", BACKWARDS(_["name"]) & _K))`: `run()` gives k = ahpla3, ammag4, ïnü5, ahplA6; hybrid gives ahpla1, ammag2, ïnü3, ahplA4.
  - `ORDERS .> FILTER(_["id"]>2) .> MAP(RECORD("a", _["id"], "b", BACKWARDS(_["name"]) & _K))` (fallthrough path): same wrong `_K`.
  - Fuzz: ~1.5% of random 1-4 step pipelines hit this ("KEYS" class).

**Additional fixture requirements from the report:** In 25-hybrid-plans.sqlt: `ORDERS .> FILTER(...) .> FILTER(<no-sql fn>)` = pure_memory (or hybrid with prefix stopping before the FILTER), and `... .> FILTER(...) .> MAP(RECORD("k", <no-sql fn> & _K))`. The `--- plan` runner checks only classification/SQL text, so an executing case against SQLite is needed.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C22.

<a id="js-c23"></a>

### JS-C23: Hybrid fallthrough (partially local MAP) with downstream TAKE/DROP hides errors the custom half raises on rows the LIMIT cuts off

Source: [JS-C23](../../js-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`ORDERS .> MAP(RECORD("a", _["id"], "b", LEN(BACKWARDS(_["name"])) + IF(_["id"] > 4, "x", 1))) .> TAKE(2)`: `run()` gives `E_NOT_NUM`; `executeHybrid` (plan `hybrid`, SQL `SELECT id AS a, name, id ... LIMIT 2`) gives `{"1"={a=1,b=6},"2"={a=2,b=5}}`. Same with `.> DROP(1)`.

**Additional fixture requirements from the report:** Case `plan.fallthrough.take-after-a-map-that-can-raise-stays-local`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C23.

<a id="js-c59"></a>

### JS-C59: Pure-SQL bucket drops an explicit SORT_BY that fixes the group order

Source: [JS-C59](../../js-code-review.md). Report labels: [low] [confirmed, SQLite only].

**Scenario / reported observation:**

`ORDERS .> SORT_BY(_["customer_id"], "DESC") .> BUCKET(_["customer_id"], RECORD("c", _K, "n", COUNT(_)))` on SQLite: `run()` gives c = 12, 11, 10; `executeHybrid` (pure_sql) gives 10, 11, 12. Statement: `SELECT MIN(customer_id) AS c, COUNT(*) AS n FROM (SELECT o.* FROM orders o ORDER BY o.customer_id DESC) _sub1 GROUP BY CAST(_sub1.customer_id AS TEXT) COLLATE BINARY`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C59.

<a id="js-c60"></a>

### JS-C60: `executeHybrid` treats the context differently for hybrid versus pure-memory plans

Source: [JS-C60](../../js-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

js7/c.mjs (13 context shapes; no crashes, only the mutation/clone difference).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for JS-C60.

## PHP report scenarios

<a id="php-c8"></a>

### PHP-C8: Hybrid split on a key-retaining FILTER loses SEL's row keys: continuation `_K` / result keys differ from pure memory

Source: [PHP-C8](../../php-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

sqlite harness, ORDERS ids 1..10): `ORDERS .> FILTER(_["id"] > 2) .> MAP(RECORD("i", _["id"], "k", _K, "z", REPEAT(_["name"], 2)))` memory `k`="3".."10", hybrid "1".."8". `ORDERS .> FILTER(_["id"] > 2) .> FILTER(LEN(REPEAT(_["name"],2)) > 5)` memory keys 3..8, hybrid 1..6. `ORDERS .> TAKE(6) .> FILTER(_["id"] > 2) .> FILTER(LEN(REPEAT(_["name"],2)) > 5)` memory 3..6, hybrid 1..4. Related (pure_sql): `ORDERS .> FILTER(_["id"] > 8)` memory keys "9","10", pure_sql "1","2" (12.1 never says pure_sql renumbers - needs a documented decision).

**Additional fixture requirements from the report:** for the plain split. Suggest in `25-hybrid-plans.sqlt`: `ORDERS .> FILTER(...) .> MAP(RECORD("k", _K, "z", <unpushable>))` must be pure_memory (or execute equal).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C8.

<a id="php-c9"></a>

### PHP-C9: LINK in the hybrid continuation renames the left binder to `_INPUT` (spec 7.4 violation)

Source: [PHP-C9](../../php-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

`ORDERS .> FILTER(_["id"] > 2) .> SORT_BY(REPEAT(_["name"], 2)) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> MAP(RECORD("n", _["ORDERS"]["name"]))` memory: 6 rows; hybrid: E_NO_KEY. Without the MAP the hybrid rows contain `"_INPUT"=...,"_input"=...` where memory has `"ORDERS"`,`"orders"`.

**Additional fixture requirements from the report:** suggest a hybrid split immediately before an unbound-name `LINK` reading `_["ORDERS"]`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C9.

<a id="php-c35"></a>

### PHP-C35: Pure-SQL plans do not preserve SEL's order through SORT_BY .> LINK (and BUCKET group order)

Source: [PHP-C35](../../php-code-review.md). Report labels: [medium] [confirmed on SQLite; unconfirmed on MariaDB].

**Scenario / reported observation:**

sqlite): `ORDERS .> SORT_BY(_["id"], "DESC") .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"]) .> MAP(RECORD("o", _["O"]["id"], "n", _["C"]["name"])) .> TAKE(2)` memory o=10,8; pure_sql o=1,2. `ORDERS .> SORT_BY(_["id"], "DESC") .> BUCKET(_["customer_id"]) .> MAP(RECORD("c", _K, "n", COUNT(_)))` memory groups 11,14,12,10,13; SQL 10,11,12,13,14. Slice 6: `O .> SORT_BY(_["TOTAL"]) .> LINK(C, ...)` emits `FROM (SELECT ... ORDER BY total ASC) _sub1 INNER JOIN ...` (`php6/t4.php`); MariaDB behaviour not run.

**Additional fixture requirements from the report:** add a statement-oracle row; group order is not discussed in 12.1.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C35.

<a id="php-c36"></a>

### PHP-C36: Hybrid/optimiser reorders and pushes steps ahead of local work, suppressing errors SEL would raise

Source: [PHP-C36](../../php-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`ORDERS .> SORT_BY(_["id"]) .> MAP(RECORD("a", _["id"], "z", REPEAT(_["name"], 1 + 0 * (1 / (_["id"] - 5))))) .> TAKE(2)` memory E_DIV_ZERO (row id 5), hybrid returns two rows; same with `SORT_BY(_["a"], "DESC") .> TAKE(2)`. `ORDERS .> SORT_BY(_["nokey"]) .> FILTER(_["customer_id"] == 7) .> MAP(RECORD("i", _["id"]))` memory E_NO_KEY, hybrid empty.

**Additional fixture requirements from the report:** (`25-hybrid-plans` pins shape, not error equivalence).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C36.

<a id="php-c55"></a>

### PHP-C55: `sourceTables` over-reports (binders and assignment targets named like a relation)

Source: [PHP-C55](../../php-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`MAP(LIST(1,2), ORDERS, ORDERS + 1)` -> `["orders"]`; `ORDERS = LIST(1); COUNT(ORDERS)` -> `["orders"]`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PHP-C55.

## PYTHON report scenarios

<a id="py-c22"></a>

### PY-C22: Hybrid: a split after `FILTER` renumbers rows, so the continuation's `_K` and the result keys differ from `run()` (also in JS)

Source: [PY-C22](../../python-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`py7/t8.py`, `py7/t9.py`; SQLite; rows id=1..6): `R .> FILTER(_["id"] > 2) .> MAP(RECORD("id", _["id"], "k", _K))` is a hybrid plan; `run()` gives k = 3,4,5,6, `execute_hybrid` gives k = 1,2,3,4. `R .> FILTER(_["id"] > 2) .> FILTER(REPEAT("a", _["id"]) $== "aaaaa")`: `run()` = `{"5": row5}`, hybrid = `{"3": row5}`. Fall-through variant: `R .> FILTER(_["id"] > 2) .> MAP(RECORD("id", _["id"], "x", REPEAT("a", _K)))` gives "aaa.." in run() and "a.." in hybrid. JS `Sql.planHybrid` on the first program yields the same k = 1,2 (`py7/j1.mjs`): a spec-level planner gap.

**Additional fixture requirements from the report:** Suggest in `25-hybrid-plans.sqlt`: `FILTER(...) .> MAP(RECORD("k", _K))` must not split after the FILTER (or must be refused), and `FILTER(sql) .> FILTER(unsupported)` must keep original keys.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C22.

<a id="py-c23"></a>

### PY-C23: Hybrid: splitting in front of an in-memory `LINK` renames the left side from the relation to `_INPUT` (also in JS)

Source: [PY-C23](../../python-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`py7/t14.py`): `R .> SORT_BY(_["id"]) .> LINK(S, PADL(_1["cat"], 1, "0") $== _2["scat"]) .> MAP(RECORD("id", _["R"]["id"], "l", _["S"]["label"]))` - run() = `{"1": {"id":"1","l":"L1"}}`; execute_hybrid = `ERR E_NO_KEY`. JS (`py7/j3.mjs`): same `hybrid ERR E_NO_KEY`. Fuzz `py7/fzl.py 1 300` shows the raw-row variant.

**Additional fixture requirements from the report:** Suggest a `--- plan` case with an unrenderable LINK predicate after a splittable prefix whose MAP reads `_["ORDERS"]`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C23.

<a id="py-c24"></a>

### PY-C24: Hybrid: `_inline_literals` mis-scopes binders of the 4- and 5-argument forms, corrupting the continuation (also in JS)

Source: [PY-C24](../../python-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`py7/t2.py`, SQLite): `N = 5; R .> FILTER(_["id"]>1) .> SORT_BY(N, N["id"], "DESC") .> MAP(RECORD("id", _["id"]))` - run() = 5 rows; execute_hybrid = `ERR E_EXPECT_SYMBOL`. Same for `BUCKET(N, N["cat"], RECORD(...))` and `TOP_BY(N, N["id"], "DESC", 2)`. With a binder name that is not a helper (`o`) the same programs are pure_sql and correct. JS (`py7/j1.mjs`): `ERR E_EXPECT_SYMBOL`.

**Additional fixture requirements from the report:** Suggest cases for a literal helper named like the binder for SORT_BY(4), BUCKET(4), TOP_BY(5), LINK(5).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C24.

<a id="py-c47"></a>

### PY-C47: Hybrid: MAP fall-through pushes `TAKE`/`DROP` past the local half of the MAP, so errors `run()` raises are lost

Source: [PY-C47](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`py7/t3.py`): rows with `amt` = 1,2,3,4,"zz",6: `R .> SORT_BY(_["id"]) .> MAP(RECORD("id", _["id"], "rep", REPEAT(_["name"], _["amt"]))) .> TAKE(2)` - run() = ERR E_NOT_NUM; hybrid = 2 rows (SQL `... ORDER BY id LIMIT 2`). Also via fuzz (`py7/fz.py 1 500`, PADL with a row-dependent width, E_RANGE lost).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C47.

<a id="py-c48"></a>

### PY-C48: Hybrid: `source_tables` reports a relation that is only used as a binder name (also in JS)

Source: [PY-C48](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`py7/j2.mjs`, `py7/t13.py`): bindings R->"r", S->"s": `R .> FILTER(S, S["ID"] > 1)` -> SQL `SELECT r.* FROM r WHERE ...` but `source_tables == ['r', 's']` (JS prints the same). Docs say it names the tables "a grant or a connection is chosen by", so it can demand access to a table never read.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C48.

<a id="py-c51"></a>

### PY-C51: `execute_hybrid` deep-clones the caller's context, so hybrid and pure-memory plans differ in whether the context is mutated

Source: [PY-C51](../../python-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`T = "x"; R .> SORT_BY(_["id"]) .> MAP(RECORD("id", _["id"], "x", PADL(_["name"], 3, T)))` (hybrid): `run()` leaves `T` in the context, `execute_hybrid` does not; a pure-memory plan for `T = "x"; R .> MAP(RECORD("x", PADL(_["name"], 3, T)))` does leave `T`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C51.

<a id="py-c52"></a>

### PY-C52: SQL: nested `ORDER BY` in a derived table is relied on for tie order

Source: [PY-C52](../../python-code-review.md). Report labels: [low] [unconfirmed on real servers; reproduced on SQLite].

**Scenario / reported observation:**

SQLite fuzz (`py7/fz.py 12 600`): `R .> SORT_BY(_["id"]) .> SORT_BY(_["name"]) .> SELECT_COLS("id","cat") .> TOP_BY(_, _["cat"], "DESC", 2)` renders `SELECT ... FROM (SELECT id, cat FROM r ORDER BY name, id) ORDER BY cat DESC LIMIT 2`; SEL's stable ties are not preserved by SQLite's top-N sorter (got ids 1,5; expected 5,6). Same for `MAP` + `SORT_BY .> SORT_BY .> SORT_BY(..., "DESC") .> TAKE(4)`. No server guarantees an inner ORDER BY survives an outer ORDER BY; the docs' own rule ("a later sort's keys come first ... never a derived table around the first sort") is not applied across a MAP/SELECT_COLS/TAKE boundary. Not investigated on PostgreSQL/MySQL.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for PY-C52.

## CPP report scenarios

<a id="cpp-c33"></a>

### CPP-C33: A hybrid continuation sees SQL-renumbered rows, so `_K` and FILTER's retained keys differ from `run()`

Source: [CPP-C33](../../cpp-code-review.md). Report labels: [medium] [confirmed; cross-host, JS planner identical].

**Scenario / reported observation:**

sqlite, bindings as in `sql_unit` `full_orders`, rows id 1..4 with amounts 10,5,7,12; stand-in DB renumbers; private harness, not re-run): `ORDERS .> FILTER(_["amount"] > 6) .> MAP(RECORD("id", _["id"], "k", REPEAT("x", _K)))`: `run()` gives k = "x","xxx","xxxx"; plan gives "x","xx","xxx" (hybrid: SQL = FILTER + `SELECT id`, MAP fall-through runs locally). `ORDERS .> FILTER(_["amount"] > 6) .> FILTER(REPEAT(_["name"], 2) $== "cc")`: `run()` gives `{"3"=...}`, plan gives `{"2"=...}`.

**Additional fixture requirements from the report:** Suggest `plan.keys.filter-prefix-then-underscore-K-in-continuation` (expect pure_memory or a prefix that stops before the FILTER) and `plan.keys.filter-then-unsupported-filter`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C33.

<a id="cpp-c34"></a>

### CPP-C34: MAP fall-through pushes TAKE/DROP past a MAP whose custom half can raise, so `run()`'s error disappears

Source: [CPP-C34](../../cpp-code-review.md). Report labels: [medium] [confirmed; JS same].

**Scenario / reported observation:**

`ORDERS .> MAP(RECORD("id",_["id"], "s", REPEAT(_["name"], _["amount"] - 8))) .> TAKE(1)`: `run()` gives `E_RANGE@1:71`; plan (hybrid, SQL `... LIMIT 1`) gives `{"1"={"id"="1","s"="aa"}}`. Also `.> DROP(3)` and `.> SORT_BY(_["id"]) .> TAKE(1)`. Without the TAKE the plan agrees.

**Additional fixture requirements from the report:** Suggest `plan.fallthrough.take-after-raising-custom-half-stays-local`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C34.

<a id="cpp-c54"></a>

### CPP-C54: `execute_hybrid` on a pure_memory plan mutates the caller's context; the hybrid path clones it

Source: [CPP-C54](../../cpp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`cpp7/ctx.cpp`, not re-run): `Y = 5; ORDERS .> SORT_BY(REPEAT(_["name"],2)) .> MAP(RECORD("y", Y))` -> pure_memory, `ctx.has("Y")` is 1 after; hybrid and pure_sql plans of similar programs leave it 0.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C54.

<a id="cpp-c60"></a>

### CPP-C60: SQL: `SORT_BY` then `MAP` then `DISTINCT` emits `SELECT DISTINCT proj ORDER BY <unprojected column>`

Source: [CPP-C60](../../cpp-code-review.md). Report labels: [low] [unconfirmed on a server].

**Scenario / reported observation:**

`drv postgresql 'R .> SORT_BY(_["N"]) .> MAP(RECORD("a", _["A"])) .> DISTINCT' 'R=r:items:i:::A/a/TEXT;N/n/NUM' --stmt`.

**Additional fixture requirements from the report:** no case orders before DISTINCT.

**Triage:** reproduce the claimed defect under the stated platform/configuration first; record evidence if it is unreachable, already fixed, or a contract/documentation issue.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for CPP-C60.

## LISP report scenarios

<a id="lisp-c8"></a>

### LISP-C8: Hybrid: a fall-through MAP whose custom pair reads `_K` is evaluated over renumbered SQL rows

Source: [LISP-C8](../../lisp-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

harness, postgresql; full = `sel:run` of the whole program):
  - `ORDERS .> MAP(RECORD("a", _["amount"], "x", JOIN(LIST(_["name"], _K), "-"))) .> DROP(1)` -> full x = al-2, cy-3, dee-4, al-5; hybrid x = al-1, cy-2, dee-3, al-4 (SQL `... OFFSET 1`).
  - `... .> SORT_BY(_["a"])` -> full x = cy-3, bob-1, al-2, al-5, dee-4; hybrid = cy-1, bob-2, al-3, al-4, dee-5.
  - `ORDERS .> FILTER(_["amount"] > 4) .> MAP(RECORD("a", _["amount"], "x", JOIN(LIST(_["name"], _K), "-")))` -> full dee-4 / al-5; hybrid dee-3 / al-4.

**Additional fixture requirements from the report:** Suggest plan-level cases `plan.fallthrough.custom-pair-reading-key-splits-before-the-map` (plain, with downstream DROP, with downstream SORT_BY, with upstream FILTER), plus an executed unit test since `.sqlt` cannot express the run half.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C8.

<a id="lisp-c26"></a>

### LISP-C26: SQL: SORT/TOP(_BY) followed by LINK is not wrapped in a derived table (Lisp); JS and Python wrap it

Source: [LISP-C26](../../lisp-code-review.md). Report labels: [medium] [confirmed divergence; direction unclear].

**Scenario / reported observation:**

`O .> SORT(_["AMT"]) .> LINK(C, _["CID"] == C["ID"])` postgresql: Lisp `... FROM "orders" "o" INNER JOIN "customers" "c" ON (...) ORDER BY "o"."amt" ASC`; JS and Python `... FROM (SELECT "o".* FROM "orders" "o" ORDER BY "amt" ASC) "_sub1" INNER JOIN "customers" "c" ON (...)` (no outer ORDER BY). With `.> TAKE(3)` appended the JS/Python form ends in `LIMIT 3` with no ORDER BY at the join level.

**Additional fixture requirements from the report:** (`sql/cases/26-links.sqlt:515` and `47-link-rows.sqlt:636` have a SELECT_COLS or limit before the LINK). Suggest `link.after-sort-order-by-placement`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C26.

<a id="lisp-c27"></a>

### LISP-C27: Hybrid: a split after a key-retaining FILTER hands the continuation renumbered rows

Source: [LISP-C27](../../lisp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`ORDERS .> FILTER(_["amount"] > 4) .> MAP(RECORD("x", _K))` -> hybrid, SQL `SELECT o.* ... WHERE amount > 4`; full x = 1,2,4,5, hybrid x = 1,2,3,4. Also `... .> FILTER(JOIN(LIST(_["name"], "x"), "-") $== "al-x") .> MAP(RECORD("k", _K))` -> full k = 2,5, hybrid k = 2,4. When the continuation ends in a FILTER the result keys differ (`{"2","5"}` vs `{"2","4"}`); that part may be accepted as "SQL returns a rowset", the `_K` reads are not.

**Additional fixture requirements from the report:** Suggest `plan.split.filter-then-key-reader-is-not-a-split-point`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C27.

<a id="lisp-c28"></a>

### LISP-C28: Hybrid: `inline-literals` only understands the 3-item binder form

Source: [LISP-C28](../../lisp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`R = 5; ORDERS .> FILTER(_["amount"] > 4) .> SORT_BY(R, R["id"], "DESC") .> MAP(RECORD("x", JOIN(LIST(_["name"], "q"), "-")))` -> plan is hybrid; the continuation over the rows gives E_EXPECT_SYMBOL while `run` of the program succeeds. Same with `BUCKET(R, R["customer_id"], RECORD(...))`. Without the `R = 5;` helper the pipeline plans as pure SQL; with it `ORDERS .> SORT_BY(R, R["id"], "DESC") .> TAKE(2)` is downgraded from pure_sql to pure_memory.

**Additional fixture requirements from the report:** Suggest `plan.helpers.literal-named-like-an-explicit-binder-is-not-inlined` for SORT_BY, TOP_BY, BUCKET and 5-item LINK.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C28.

<a id="lisp-c29"></a>

### LISP-C29: Hybrid: splitting before a LINK renames the join's left side to `_INPUT`

Source: [LISP-C29](../../lisp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`ORDERS .> TAKE(4) .> LINK(CUSTOMERS, _1["customer_id"] == _2["cid"]) .> FILTER(_["ORDERS"]["id"] > 1)` -> plan hybrid (prefix `TAKE 4`); `run` succeeds, the continuation raises E_NO_KEY; without a failing read the result rows carry the `_INPUT` key instead of `ORDERS`. Also `ORDERS .> TAKE(4) .> FILTER(<unsupported>) .> LINK(...) .> SORT_BY(_["ORDERS"]["id"])`.

**Additional fixture requirements from the report:** Suggest an executed unit case per host plus `plan.split.before-link-keeps-left-name`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C29.

<a id="lisp-c30"></a>

### LISP-C30: Hybrid: MAP fall-through evaluates custom pairs only on rows that survive the pushed-down TAKE/DROP/sort

Source: [LISP-C30](../../lisp-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`ORDERS .> MAP(RECORD("a", _["amount"], "x", IF(_["id"] == 4, ABORT("boom"), 1))) .> TAKE(2)` -> `run`: E_ABORT; hybrid: two rows (SQL `... LIMIT 2`). Same shape with a custom pair dividing by a column holding 0.

**Additional fixture requirements from the report:** `plan.fallthrough.filter-under...` pins the shape but never a raising custom pair; suggest a unit test with a raising pair beyond the TAKE window.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C30.

<a id="lisp-c43"></a>

### LISP-C43: `source-tables` over-reports a binder that shadows a relation name

Source: [LISP-C43](../../lisp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

`LIST(1) .> MAP(ORDERS, ORDERS)` -> `source_tables` `("orders")`, pure_memory. Over-reporting is conservative rather than unsafe (drives grants/connection choice).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C43.

<a id="lisp-c47"></a>

### LISP-C47: Doc drift on the fall-through's allowed downstream steps

Source: [LISP-C47](../../lisp-code-review.md). Report labels: [low] [confirmed].

**Scenario / reported observation:**

the code is the safer side; the doc is stale. Not Lisp-only.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for LISP-C47.

## GO report scenarios

<a id="go-c5"></a>

### GO-C5: `ExecuteHybrid` throws away the caller's whole context (`IsNone()` is true for every list/record)

Source: [GO-C5](../../go-code-review.md). Report labels: [high] [confirmed].

**Scenario / reported observation:**

JS/PHP/Python clone the caller's context and add `_INPUT`. Go replaces any record context with an empty record, so every name the continuation reads from the caller (thresholds, other tables for a LINK/ANY, helper inputs) is `E_UNDEF_VAR`. This defeats the main purpose of a hybrid plan (sql-translation.md 12.1). Nothing in Go calls `ExecuteHybrid` (no test, not in sqlapi or e2e), which is why it went unnoticed.

**Additional fixture requirements from the report:** (.sqlt cases never execute a plan). Add a Go unit test in `go/sel/sql` and a cross-host `execute` probe in `tools/sqlapi.*` (JS already has one in `check-js-optimizer.mjs`).

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C5.

<a id="go-c21"></a>

### GO-C21: `containsUnsupportedSql` visits every call's arguments twice: hybrid planner time is exponential in call nesting

Source: [GO-C21](../../go-code-review.md). Report labels: [medium] [confirmed].

**Scenario / reported observation:**

`ORDERS .> MAP(RECORD("plus", ABS(ABS(...ABS(_["amount"])...)), "tag", ABORT("x")))` planned for mariadb (bindings as in `sql/cases/25`): n=14 nested ABS 14 ms, n=18 217 ms, n=20 1.1 s, n=22 4.27 s (doubling per level; n=30 extrapolates to ~17 min, not run). JS on the same program: 22 ms at n=22. Cross-host: `H1 = H0 + H0; H2 = H1 + H1; ...` then a MAP reading H16: Go 1.2 s, JS 0.6 s, both 2^n.

**Additional fixture requirements from the report:** Add a plan case (or a Go unit test with a time bound) with 40-deep `ABS(...)` in a pushable pair next to an `ABORT("x")` pair.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C21.

<a id="go-c22"></a>

### GO-C22: A hybrid split before a LINK changes the joined row (binder names), so hybrid != pure memory

Source: [GO-C22](../../go-code-review.md). Report labels: [medium] [confirmed, cross-host: JS behaves identically].

**Scenario / reported observation:**

`ORDERS .> TAKE(1) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])` (plan hybrid): in-memory result keys `ORDERS`, `orders`; after hybrid execution `_INPUT`, `_input`. 67 of 4,000 random pipelines mismatched this way, all with a LINK after the split, plus a few where the original raises E_NO_KEY (`FILTER(_["customer_id"] $== "b")` on a joined row) and the hybrid returns a value/empty list. No other mismatch kind appeared.

**Additional fixture requirements from the report:** (`sql/cases/25` pins only the prefix SQL text). Add an executed-plan case for `X .> TAKE(1) .> LINK(...)`.

**Completion record:** named fixture(s), spec-backed expected result/error/phase, all-six-host run matrix, and any platform-only adapter are required for GO-C22.
