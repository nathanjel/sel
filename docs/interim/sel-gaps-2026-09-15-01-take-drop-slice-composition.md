# F1 — Compose TAKE/DROP as a bounded slice

Status: implemented and verified in all five hosts on 2026-09-16. The default
integration gate and live regressions pass; see the consolidated verification report.
Originally confirmed at `55f4aa6`. Priority: **P1, silent wrong
rows**. Affected every translator on PostgreSQL, MariaDB and SQLite, with strict
off/on, inline/prepared SQL, and execution via a pure-SQL hybrid plan.
[Index and verification scope](sel-gaps-2026-09-15-00-index.md).

## Implementation and verification update — 2026-09-16

Implemented first in Lisp, then Python, JS, PHP and C++. DROP now subtracts
`min(count, remaining_limit)` from the limit and adds only that many skipped
rows to the offset. Zero remains a real limit. If adding offsets would exceed
the exact integer range shared by the hosts (2^53−1), a derived-table boundary
keeps the two slices separate instead of overflowing/rounding their sum.
Existing boundaries around FILTER, grouping, sorting and projection remain.

Desired-result coverage now lives in:

- `conformance/16-slices.selt`: seven local semantic cases, passing in all five hosts.
- `sql/cases/27-slices.sqlt` and `28-slice-overflow.sqlt`: the three dialects,
  including repeated large offsets. Two older expectations in statement/bucket
  cases incorrectly encoded F1 and have been corrected. The complete SQL case
  suite passes in all five hosts (723 cases per host).
- `python/tests/test_sql_slices.py`: 96 live SQLite tests covering strict off/on,
  empty/nonempty input, MAP/FILTER/BUCKET, direct inline/prepared SQL where
  supported, and actual hybrid execution. All pass. SQLite's derived numeric
  FILTER refusal is tested through its correct local continuation.
- `sql/oracle/statements.json`: ten additional slice scenarios. The statement
  oracle passes against disposable PostgreSQL 17, MariaDB 11.8, and SQLite:
  respectively 18/18/16 successful statements, with SQLite's two documented
  refusals and no mismatches.

The original two witnesses were additionally executed against all three live
databases using every source translator plus a freshly installed Python wheel,
strict off/on, inline/native parameters/planner-prefix SQL: **216 correct SQL
results**. Both witnesses remain pure SQL and return `[2]` and `[]`; no
post-fetch correction is involved. Historical audit JSON and `verify.py` still
describe the old bugs and have not been replaced with fresh baseline evidence.

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
