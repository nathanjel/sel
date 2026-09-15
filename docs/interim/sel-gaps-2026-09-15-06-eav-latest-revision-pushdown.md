# F6 — Push down latest EAV revision per entity

Status: confirmed capability gap at `55f4aa6`, **not a correctness defect**.
Proposed priority: **P2, high-volume extraction performance**. Every planner
chooses pure memory on PostgreSQL, MariaDB and SQLite in both strict settings;
all local host/package variants return the correct small-fixture result.
[Index](sel-gaps-2026-09-15-00-index.md).

## Use case and reproduction

An attribute history has entity ID `fk`, attribute name `cat`, observation value
`v` and globally unique increasing revision ID `id`. Extract the newest weight
observation per entity:

```sel
R = LIST(RECORD("id", 1, "cat", "weight", "v", 60, "fk", 1),
         RECORD("id", 2, "cat", "weight", "v", 65, "fk", 1),
         RECORD("id", 3, "cat", "weight", "v", 70, "fk", 2));
R .> BUCKET(_["fk"])
  .> MAP(RECORD("entity", _K,
               "latest", TOP_BY(_, _["id"], "DESC", 1)))
```

Expected/latest IDs: 2 for entity 1 and 3 for entity 2, each inside the nested
`latest` list. The same pipeline with relation bindings is case **19**
`eav.latest-per-entity` in [probe.py](../../tools/adversarial/probe.py).
Every plan is `pure_memory`, with no SQL prefix.

The SQL compiler can reduce ordinary bucket projections to scalar aggregates,
but this projection needs a selected **member row**, not a scalar aggregate or
the group key alone. The existing “do not split after a bare bucket” guard is
correct: a grouped SQL key list cannot be passed off as the original members.
Do not remove that guard to claim better pushdown.

## Measured cost and independently checked baseline

The scale fixture contains 100,000 revisions over 100 entities. SEL loads all
100,000 rows, although only 100 member rows are returned. With an `(fk,id)`
index and a globally unique `id`, the same selected revisions can be found by:

```sql
SELECT r.*
FROM r
JOIN (SELECT fk, MAX(id) AS latest FROM r GROUP BY fk) g
  ON r.id = g.latest
```

The test checked selected IDs, not merely counts, against local SEL. SQL
returns flat rows; rebuilding 100 small nested groups remains host work. This
is a demonstration of avoidable input volume, not a claim that this SQL emits
the complete SEL value or solves arbitrary per-group TOP_N.

Latest rerun, single observations at 100,000 rows:

| Path | Observed time |
|---|---|
| Python fetch + SEL ingestion + evaluation/materialization | 3.82 s |
| Indexed SQLite baseline returning 100 rows | 10.0 ms |
| Indexed PostgreSQL baseline returning 100 rows | 20.0 ms |
| Indexed MariaDB baseline returning 100 rows | 1.03 ms |

The durable result is **1,000× input-row amplification**. Timings are not a
statistical benchmark and Python timings do not describe PHP/JS/C++/Lisp speed.
Evidence/scripts: [scale.py](../../tools/adversarial/scale.py),
[scale-results.json](../../tools/adversarial/scale-results.json),
[scale-db.php](../../tools/adversarial/scale-db.php),
[scale-db-results.json](../../tools/adversarial/scale-db-results.json).

## Proposed staged implementation

1. Recognize a narrow, provably safe grouped-latest-member pattern: one bound
   relation, a supported partition key, descending unique revision key, TOP 1,
   and a result that needs only the selected row and group key. Preserve any
   input filter before selecting winners.
2. Add SQL lowering through an aggregate/join-back or a suitable ranked-window
   strategy per dialect. General TOP_N requires ranking rather than the simple
   MAX join. Do not select arbitrary nonaggregated columns beside MAX(id).
3. Describe the selected row and partition key in result metadata, then rebuild
   the nested `latest` list in a small continuation/decoder. Coordinate with
   [F5](sel-gaps-2026-09-15-05-hybrid-join-result-shape.md); a new SQL prefix is
   not sufficient without a valid result-shape contract.
4. Keep unsupported shapes on the existing correct fallback. Expose useful
   plan/explain information for “requires materializing group members” so a
   caller can see why SQL was not selected.

An immediate application workaround is a relation-query binding containing
the independently authored preselection SQL, followed by local reshaping.
Document that the host now owns selection/tie semantics; this is not an
automatic SEL optimization. For history with multiple attribute names, partition
by both entity and attribute (the fixture intentionally uses only `weight`).

## Code lanes

| Host | Planner boundary / statement grouping |
|---|---|
| Python | [hybrid.py](../../python/sel/sql/hybrid.py): `_bucket_rows_are_keys`, `plan_hybrid`, `_try_plan_fallthrough`; [translator.py](../../python/sel/sql/translator.py): `_bucket_projection`, BUCKET/MAP/TOP handling |
| JS | [hybrid.mjs](../../js/src/sql/hybrid.mjs): `bucketRowsAreKeys`, `planHybrid`, `tryPlanFallthrough`; [translator.mjs](../../js/src/sql/translator.mjs): bucket projections and sort/top lowering |
| PHP | [Hybrid.php](../../php/src/Sql/Hybrid.php): `bucketRowsAreKeys`, `plan`, `tryPlanFallthrough`; [Translator.php](../../php/src/Sql/Translator.php): BUCKET projection/sort paths |
| C++ | [sel_sql_hybrid.cpp](../../cpp/sel_sql_hybrid.cpp): prefix search and bucket guard; [sel_sql_translator.cpp](../../cpp/sel_sql_translator.cpp): `bucket_projection`, `analyze_pipeline`, `analyze_sort_step` |
| Lisp | [hybrid.lisp](../../lisp/src/sql/hybrid.lisp): `bucket-rows-are-keys-p`, `plan-hybrid`, `try-plan-fallthrough`; [translator.lisp](../../lisp/src/sql/translator.lisp): bucket projections and sort analysis |

Extend the relational IR only as needed: [Python](../../python/sel/sql/relational_plan.py),
[JS](../../js/src/sql/relational-plan.mjs), [PHP](../../php/src/Sql/RelationalPlan.php),
[C++](../../cpp/sel_sql.hpp), [Lisp](../../lisp/src/sql/relational-plan.lisp).
Local BUCKET/TOP semantics in the aggregate/structure builtins remain the oracle.

## Acceptance criteria

- For the recognized pattern, each supported dialect transfers O(number of
  groups × N) selected member rows, rather than every historical revision.
  Assert rows delivered to the callback/decoder, not just planner classification.
- Compare selected IDs and reconstructed nested values to local SEL. Preserve
  group/member order where the API promises it; SQL has no implicit source order.
- Define ties, missing values, zero/empty input, N=0/1/>1, filtered histories,
  composite entity/attribute partitions, and duplicate revisions before widening
  recognition. Unique revision IDs make the recorded baseline unambiguous.
- Unsupported ambiguous-order or computed-key cases must retain correct fallback.
- Keep scale checks separate from correctness tests; use row-count assertions as
  the stable performance gate and repeated measurements for any speed claim.
