# F1 — Compose TAKE/DROP as a bounded slice

Status: confirmed, open at `55f4aa6`. Proposed priority: **P1, silent wrong
rows**. Affects every translator on PostgreSQL, MariaDB and SQLite, with strict
off/on, inline/prepared SQL, and execution via a pure-SQL hybrid plan.
[Index and verification scope](sel-gaps-2026-09-15-00-index.md).

## User-visible failure and reproduction

A normalized-order export first caps eligible rows, then skips already processed
rows. The database reintroduces rows excluded by the earlier cap.

```sel
R = LIST(RECORD("id", 1), RECORD("id", 2), RECORD("id", 3));
R .> SORT_BY(_["id"]) .> TAKE(2) .> DROP(1)
  .> MAP(RECORD("id", _["id"]))
```

Local result: one row, `{id:2}`. SQL result: `{id:2}`, `{id:3}`.
Changing the two counts to `TAKE(1) .> DROP(2)` should return no rows, but
SQL returns `{id:3}`. This second witness rules out a mere output-format issue.

For SQL, bind `R` to table `r`, alias `r`, with NUM field `ID` mapped to `id`;
pass only the pipeline to the translator, not the local fixture assignment.
Complete recorded fixtures: cases **8** `normalized.take-drop` and **17**
`normalized.take-zero` in [probe.py](../../tools/adversarial/probe.py).
The actual PostgreSQL statement for case 8 is:

```sql
SELECT "_sub1"."id" AS "id"
FROM (
  SELECT "r".* FROM "r" "r"
  ORDER BY "r"."id" ASC LIMIT 2 OFFSET 1
) "_sub1"
```

No caveat is reported and strict mode does not prevent the error.

## Root cause and suggested correction

TAKE sets/minimizes `plan.limit`; DROP only increases `plan.offset`.
`LIMIT 2 OFFSET 1` means skip one original row and then take two, not take two
and then discard one. Compose a slice while it remains legal to combine the
steps in the same relational plan. One possible representation is `(offset,
length)`, with `length = unbounded` allowed:

```text
TAKE(n): length = n if unbounded else min(length, n)
DROP(n):
    skipped = n if unbounded else min(length, n)
    offset = checked_add(offset, skipped)
    if bounded: length -= skipped
```

`length == 0` must stay empty, not become “no LIMIT”. This is pseudocode, not
a reviewed patch. Keep existing derived-table boundaries across operations
whose order cannot commute; do not move pagination through FILTER, DISTINCT,
BUCKET or a different SORT. Check fixed-width overflow in C++/other bounded
host arithmetic and preserve the existing argument validation/error positions.

## Code lanes

| Host | Change/read points at the audited commit |
|---|---|
| Python | [translator.py](../../python/sel/sql/translator.py): `analyze_pipeline`, TAKE/DROP at 2003–2013; `compile_statement` LIMIT/OFFSET rendering around 2250 |
| JS | [translator.mjs](../../js/src/sql/translator.mjs): `analyzePipeline`, TAKE/DROP around 2130–2147; final LIMIT/OFFSET around 2511 |
| PHP | [Translator.php](../../php/src/Sql/Translator.php): TAKE/DROP switch around 2862–2876; LIMIT/OFFSET around 3272 |
| C++ | [sel_sql_translator.cpp](../../cpp/sel_sql_translator.cpp): `analyze_pipeline` around 2664–2675; rendering around 3064 |
| Lisp | [translator.lisp](../../lisp/src/sql/translator.lisp): `analyze-pipeline`, TAKE/DROP at 2216–2232; final pagination around 2414 |

Plan state lives in [Python](../../python/sel/sql/relational_plan.py),
[JS](../../js/src/sql/relational-plan.mjs),
[PHP](../../php/src/Sql/RelationalPlan.php),
[C++](../../cpp/sel_sql.hpp), and
[Lisp](../../lisp/src/sql/relational-plan.lisp). Review TOP/TOP_BY's limit
handling as a sibling path, without assuming those operators can commute with
earlier slices. The successful double-TAKE/double-DROP audit cases are controls.

## Acceptance criteria

- Original witnesses return `[2]` and `[]` in every accepted SQL/hybrid mode.
- Add desired-result tests for DROP smaller/equal/larger than a previous TAKE,
  TAKE(0), DROP(0), repeated mixed slices, empty input, and DROP followed by TAKE.
- Include a longer source than the cap so incorrect “extra” rows are observable;
  use explicit ORDER BY/SORT_BY with a unique key.
- Verify downstream MAP/FILTER/BUCKET and all three databases; assert rows as
  well as SQL. If a plan is refused, its local fallback must still be correct.
- A fix must not rely on post-fetch truncation: the SQL result itself must obey
  the bounded slice, including when the whole pipeline is classified pure SQL.
