# F4 — Implement RECORD duplicate-key semantics in SQL projections

Status: confirmed, open at `55f4aa6`. Proposed priority: **P1, accepted query
fails in the database**. PostgreSQL and MariaDB fail for all five translators
and the wheel, strict off/on and inline/native parameters. The SQLite witness
falls back to a hybrid plan and happens to succeed with last-column-wins
associative decoding; this is not proof that duplicate SQL projections are safe.
[Index](sel-gaps-2026-09-15-00-index.md).

## Reproduction

```sel
R = LIST(RECORD("id", 1, "fk", 9));
R .> MAP(RECORD("x", _["id"], "x", _["fk"]))
  .> FILTER(_["x"] > 5)
```

Expected/local: `{x:9}`. Repeated RECORD keys are legal; the last value wins.
This is explicitly tested by `rel.record.duplicate-key` in
[conformance/15-relational.selt](../../conformance/15-relational.selt).

For SQL, bind R to r/r and ID/FK as NUM integer columns with values `(1,9)`.
The projection becomes `SELECT id AS x, fk AS x ...`; the filter is in an
outer query which reads `x` from that derived table:

```sql
-- Simplified from the recorded statement; the actual filter also has a
-- numeric guard because the derived column has UNKNOWN kind.
SELECT q.* FROM (SELECT id AS x, fk AS x FROM r) q WHERE q.x > 5
```

PostgreSQL: `42702`, `column reference "x" is ambiguous`.
MariaDB: `1060 / 42S21`, `Duplicate column name 'x'`.
The planner labels both cases pure SQL, with no strict-mode protection.
Cases **12** `normalized.duplicate-filter` and **15** `star.duplicate-derived`
in [probe.py](../../tools/adversarial/probe.py) cover the same failure with
and without an intervening TAKE(1). Case **7** records the duplicate-column
projection itself, before a downstream filter forces the database error.

## Root cause and implementation direction

Each MAP/RECORD pair is appended independently to SQL `projections`. SQL select
aliases are being used as though they were SEL map insertions. They are not:
duplicate column names can survive a SELECT but become ambiguous/invalid when
wrapped, and driver decoding policies vary.

Implement one effective output field per SEL key, retaining the last value
expression and the correct field insertion order. Before removing earlier
expressions, resolve their evaluation semantics. A RECORD expression can raise
or have observable work even when its field is later overwritten; physical
MAP-to-LAZY_RECORD optimization also needs to preserve the defined behavior.
The audit's witness uses harmless field reads, so it proves the alias bug but
does not settle the general dead-expression problem.

A conservative initial fix can refuse duplicate-key projection shapes and
back up the hybrid split before that MAP. A partial fix that deduplicates only
the final SELECT but leaves stale aliases in derived-field inference or hybrid
fall-through metadata will still be incorrect.

## Code lanes

| Host | SQL MAP projection loop | Local RECORD semantics |
|---|---|---|
| Python | [translator.py](../../python/sel/sql/translator.py), `analyze_pipeline` around 1991; also `_output_field_names`, `_wrap_plan_as_derived_table` | [structure.py](../../python/sel/builtins/structure.py), `_record` at 44 and `_lazy_record` |
| JS | [translator.mjs](../../js/src/sql/translator.mjs), projection append around 2106 | [structure.mjs](../../js/src/builtins/structure.mjs), RECORD registration around 68 |
| PHP | [Translator.php](../../php/src/Sql/Translator.php), RECORD pair loop around 2838 | [Core.php](../../php/src/Builtins/Core.php), RECORD registration at 82; [Structure.php](../../php/src/Builtins/Structure.php), constructors and LAZY_RECORD |
| C++ | [sel_sql_translator.cpp](../../cpp/sel_sql_translator.cpp), pair loop around 2651, `output_field_names`/`ensure_derived` | [sel.cpp](../../cpp/sel.cpp), RECORD at 3462 and LAZY_RECORD at 3479 |
| Lisp | [translator.lisp](../../lisp/src/sql/translator.lisp), `projs` construction around 2197–2207, derived-table handling | [structure.lisp](../../lisp/src/builtins/structure.lisp), RECORD at 32 and LAZY_RECORD at 55 |

Review analogous BUCKET `bucket_projection` loops and each host's hybrid
`tryPlanFallthrough`/`try_plan_fallthrough`: they also inspect RECORD pairs.
Their wider duplicate-key behavior is follow-up coverage, not an additional
confirmed finding here. See [F5's planner lane map](sel-gaps-2026-09-15-05-hybrid-join-result-shape.md).

## Acceptance criteria

- The two original cases yield one column `x`, value `9`, in every accepted
  SQL path, or safely fall back before the problematic projection.
- Do not treat an associative decoder silently dropping a duplicate SQL column
  as a successful translation. Assert column names and positional row width.
- Add duplicate-key tests with distinct intermediate keys (to pin field order),
  downstream FILTER/SORT/TAKE and a second MAP, plus MAP mixed SQL/local fields.
- Pin behavior of overwritten error-producing expressions in raw and optimized
  local execution before implementing any deduplication that removes evaluation.
- Test key case distinctions using the actual SEL key contract and dialect
  alias rules; do not assume `x` and `X` are interchangeable in every layer.
- Parameter binding order/count must follow the retained SQL expressions;
  discarded pairs must not leave orphan placeholders or stale bindings.
