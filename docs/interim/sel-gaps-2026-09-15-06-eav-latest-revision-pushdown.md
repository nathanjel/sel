# F6 — Push down latest EAV revision per entity

Status update 2026-09-16: the narrow unique-revision TOP 1 strategy is implemented
in all five hosts, starting in Lisp. Focused/live tests and the default integration
gate pass. Original status: confirmed capability gap at `55f4aa6`, **not a correctness defect**.
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
At the audited commit every plan was `pure_memory`, with no SQL prefix.
Without the new explicit unique-key metadata, this remains the safe answer.

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

Historical audit rerun, single observations at 100,000 rows:

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

### Implemented contract (2026-09-16)

Relation bindings may declare a single-column, non-null unique key using
`with_unique_key("id")` (Python/C++; JS/PHP `withUniqueKey`, Lisp `binding-with-unique-key`).
This is a caller/schema promise, like a declared SQL kind, not a uniqueness
guess based on a field's name. The first grouped-latest optimization requires
that key to be a direct NUM column, and the partition to be a direct TEXT or
NUM column with the same physical and SEL field name. Correlated/raw source
relations and unproven/computed keys retain fallback.

The initial recognized projection contains the group key and descending TOP_BY
1 member list, with no other member-dependent aggregates. A filtered history is
selected before grouping. The initial recognizer supports default binders;
named-binder spellings retain fallback. Composite partitions, TOP 0 or TOP N>1, other source
sorts, and missing uniqueness metadata retain fallback. Duplicate/null revisions
violate the explicit unique-key declaration; applications without that database
constraint must not declare it. Nullable partition keys still reach the local
continuation and retain SEL's error behavior.

The SQL uses an aggregate/join-back over one filtered input CTE: MAX(revision)
selects the whole winner row, and MIN(revision) orders the resulting groups.
This preserves first-group order for input explicitly sorted by that unique
revision ascending. Without an explicit source sort, bound SQL relations have
no implicit order guarantee; membership remains exact. The local continuation
runs the original bucket/projection on the selected full rows, retaining the
nested list shape and any later operations. Plan metadata identifies the
selected-member strategy and its partition/revision fields.

Focused verification: 123 Python tests cover strict modes, four dialect plans,
live SQLite, null/empty/filtered inputs, CTE-name collisions, undeclared payload
fields, conservative fallback, and the 100,000-row transfer bound. Shared
`conformance/20-latest-member.selt` and SQL cases 36–37 cover all hosts. Five
mutation entries remove the TOP 1 guard, one per host.

`tools/adversarial/latest.py` ran 432 real SQL executions and 216 exact hybrid
replays across PostgreSQL, MariaDB and SQLite, all five source hosts and a fresh
Python wheel, strict off/on and inline/native prepared parameters. The 100,000
history rows produced exactly 100 transferred winner rows, retaining complete
nested SEL output. This proves a 1,000× transfer reduction, not a timing claim.

The implementations are `try-plan-latest-member` (Lisp), `_try_latest_member`
(Python), `tryLatestMember` (JS/PHP), and `try_latest_member` (C++), in each host's
hybrid planner listed below. Binding constructors and the HybridPlan structures
carry the new metadata; `tools/gen-sql-cases.mjs` accepts `uniqueKey` in fixtures.

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
