# F5 — Make joined SQL-prefix results safe for local continuation

Status: confirmed, open at `55f4aa6`. Proposed priority: **P1, accepted hybrid
plan fails during continuation**. PostgreSQL/MariaDB fail in both strict
settings. SQLite fails with strict off; strict on refuses the numeric join's
approximation and successfully falls back to pure memory. The five host
executors and installed wheel all reproduce this behavior.
[Index](sel-gaps-2026-09-15-00-index.md).

## Reproduction and observed handover

R is orders with `(id,fk) = (1,1),(2,1),(3,1)`; S is the referenced customer
with `id=1,cat="dimension"`. The full fixtures also carry `cat,v,fk` columns
on both relations, as recorded in case **13** `normalized.join-page` in
[probe.py](../../tools/adversarial/probe.py).

```sel
R .> LINK(S, _1["fk"] == _2["id"])
  .> SORT_BY(_["R"]["id"]) .> TAKE(2) .> DROP(1)
  .> MAP(RECORD("id", _["R"]["id"], "dimension", _["S"]["cat"]))
```

Local result: `{id:2, dimension:"dimension"}`. The whole-statement translation
is refused, but the planner returns a hybrid plan with this PostgreSQL prefix:

```sql
SELECT "r".* FROM "r" "r"
INNER JOIN "s" "s" ON ("r"."fk" = "s"."id")
ORDER BY "r"."id" ASC LIMIT 2 OFFSET 1
```

**The prefix selects only `r.*`.** It does not select `s.cat` and does not
encode nested `R`/`S` records. Ordinary associative database rows are therefore
flat left-table records. The remaining MAP still reads `_["R"]["id"]` and
fails with `E_NO_KEY` (Python reports column 104 in the recorded source).

This corrects a detail in the earlier narrative: the failure is not caused by
duplicate result column names from both sides in this particular prefix.
The right-hand values are absent entirely. Merely wrapping returned columns
under R cannot reconstruct S; the SQL/result contract itself needs correction.

The API usage under test is normal: `Sql.execute_hybrid(plan, db_runner,
context)`, with the callback returning SEL Values built from actual associative
driver rows. [replay.py](../../tools/adversarial/replay.py) and the host adapters
replay each host's real prefix results through its public executor. No callback
is expected to invent omitted S data or reimplement the query outside the plan.

## Root cause and containment

The longest-translatable-prefix search checks SQL renderability but not whether
the SQL output has the SEL structure needed by the continuation. The statement
compiler's default SELECT emits the base alias's `.*`; the executor binds those
rows directly to `_INPUT`. Joined local rows, however, expose relation-named
nested values (and selected promoted fields). Both shape and field availability
are lost at the split.

Immediate safe option: reject a split after a join unless its output shape is
proven compatible with the remaining program; back up before the join, or
choose pure memory. Do not assume every join must be local: a complete supported
flat projection can be valid SQL, but its shape must be established explicitly.

Longer-term option: include a result descriptor in the plan. Select every needed
field with unique internal aliases, decode them into the expected nested SEL
records, and retain type/null/source metadata. Define behavior for field-name
collisions, self-joins, three-way joins and unmatched LINK_LEFT rows. Source table
names alone are not a result descriptor; do not infer nesting from driver column
names after duplicate names or omitted fields have already lost information.

F1 independently makes this prefix's pagination wrong. Fixing LIMIT alone does
not fix the missing R/S structure. Add a minimized regression without DROP so
the two fixes can be reviewed independently; the captured all-host evidence is
for the original query above.

## Code lanes

| Host | Prefix selection / execution | SQL default projection |
|---|---|---|
| Python | [hybrid.py](../../python/sel/sql/hybrid.py): `plan_hybrid` at 413, prefix loop at 454, `execute_hybrid` at 474 | [translator.py](../../python/sel/sql/translator.py): `compile_statement`, base-alias `.*` around 2159; derived wrapping around 1729 |
| JS | [hybrid.mjs](../../js/src/sql/hybrid.mjs): `planHybrid` at 376, `executeHybrid` at 443 | [translator.mjs](../../js/src/sql/translator.mjs): base-alias projection around 2400 |
| PHP | [Hybrid.php](../../php/src/Sql/Hybrid.php): `Hybrid::plan` at 104, `execute` at 531 | [Translator.php](../../php/src/Sql/Translator.php): base-alias projection around 3136 |
| C++ | [sel_sql_hybrid.cpp](../../cpp/sel_sql_hybrid.cpp): `Sql::plan_hybrid` at 390, `Sql::execute_hybrid` at 476 | [sel_sql_translator.cpp](../../cpp/sel_sql_translator.cpp): base-alias projection around 2931; `ensure_derived` |
| Lisp | [hybrid.lisp](../../lisp/src/sql/hybrid.lisp): `plan-hybrid` at 64, `execute-hybrid` at 395 | [translator.lisp](../../lisp/src/sql/translator.lisp): statement SELECT construction and `wrap-plan-as-derived-table` |

Plan/public types are in the hybrid modules above, [cpp/sel_sql.hpp](../../cpp/sel_sql.hpp),
and [JS SQL declarations](../../js/src/sql.d.ts). If adding a descriptor, update
all public types and consumers as one feature. Local join shape can be traced
through the structure builtins and C++ `make_null_record`/joined row assembly
in [sel.cpp](../../cpp/sel.cpp).

The original whole-statement refusal also differs: JS/PHP/Python use
`E_SQL_BINDING`; C++/Lisp use `E_SQL_SHAPE`. Resolve that parity discrepancy
without mistaking uniform refusal strings for a repaired hybrid executor.

## Acceptance criteria

- All affected modes return the local result or deliberately choose a safe
  earlier split/pure-memory plan. No accepted plan may fail for missing R/S
  fields introduced solely by its own handover.
- Assert generated result columns, decoded nested shape and final values using
  real prefix rows; do not test only a manually pre-nested callback fixture.
- Fix/test the original witness and a DROP-free witness independently of F1.
- Cover right-only fields, overlapping names, explicit flat MAP before splitting,
  custom local functions after joins, self-joins, and LINK_LEFT unmatched rows.
- Preserve duplicate joined row multiplicity and ordering where promised. If a
  descriptor changes the public API, document callback responsibilities and
  keep SQL/parameter order valid in every host.
