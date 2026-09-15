# SEL adversarial audit

Audited commit: `ef44fa2aea403d04386f2d77478a7a283b44611e` (0.7.4), 2026-09-15.

Rechecked against `55f4aa6` on the same date: **all findings still stand**.
See [the recheck results](RECHECK-55f4aa6.md). Current JSON artifacts contain the
recheck; original evidence is preserved in `baseline-ef44fa2.tar.gz`.

SEL delivers a useful central abstraction: one business expression with exact
local decimal arithmetic, explicit types, stable failures and the same meaning
across five host languages. Its relational operators extend that expression to
selection, joins and grouping. Typed SQL bindings describe physical storage,
while the translator and hybrid planner decide what can run in a database.
The ambitious part is preserving both row shape and expression semantics across
that boundary, including after optimizations.

This investigation found five correctness defects and a substantial pushdown
limitation. The principal defects reproduce across all five implementations:
agreement between implementations does not establish agreement with the local
language semantics. Several wrong translations have **no caveat and remain
accepted with `strict: true`**.

## Scope and evidence

Twenty scenarios cover normalized parent/child joins, star-style dimension-key
aggregation and measures, and EAV attribute selection/revision history. The
minimal fixtures deliberately use `R` and `S` so each host has the same small
binding adapter:

| Model | Interpretation of fixture fields |
|---|---|
| Normalized | `R.id` is an order ID, `R.fk` a customer FK; `S.id` is the referenced customer ID and `S.cat` its label |
| Star | `R` contains fact rows; `cat` is a dimension label, `v` a measure and `fk` another dimension key |
| EAV | `fk` is entity ID, `cat` attribute name, `v` attribute value, `id` revision ID; the two relation bindings also exercise joins between two attribute partitions |

These are reduced relational models, not full production schemas. Their small
sizes make the expected results independently checkable. The EAV scale fixture
then grows to 100,000 revisions over 100 entities.

Local execution covers Python, PHP, JS, C++, Lisp, both generated JS bundles,
and a freshly built/installed Python wheel. Translation covers all five source
implementations and the wheel. Each emitted query is executed against SQLite,
PostgreSQL and MariaDB wherever translation is accepted, with strict mode both
off and on, using inline SQL, native prepared statements and planner-prefix SQL.
The hybrid executors are also exercised with the actual prefix results.

Versions: PostgreSQL 17.10, MariaDB 11.8.8, PHP 8.4.24/PDO SQLite 3.46.1;
Python 3.14.6 with SQLite 3.51.2 for the local scale measurement; Node 24.16.0,
GCC 16.1.1, SBCL 2.5.2. The Python wheel runs under the tooling image's Python
3.12. Database image digests and reproduction commands are pinned in
[`reproduce.sh`](reproduce.sh). Database containers are disposable.

There are 120 translation/planning combinations per variant: 20 scenarios ×
3 dialects × 2 strict settings. Refused statements are recorded, not counted
as database executions. The final run contains **1,620 live SQL executions**
and **720 hybrid execution outcomes** (including upstream SQL errors) across the six source/package SQL
variants. Generated artifacts are indexed in [README.md](README.md).
No production implementation was modified and no fixes are claimed here.

## Findings

| ID | Type | Result | PostgreSQL | MariaDB | SQLite |
|---|---|---|---|---|---|
| F1 | Wrong data | Pagination returns rows already excluded by TAKE | Wrong | Wrong | Wrong |
| F2 | Wrong data | Trailing spaces compare/group as equal | Correct control | Wrong | Correct control |
| F3 | Wrong data | Numeric grouping collapses distinct SEL scalar identities | Wrong | Wrong | Refuses; local fallback works |
| F4 | Failure | A valid duplicate-key RECORD produces invalid derived SQL | SQL error | SQL error | Hybrid works in this fixture |
| F5 | Failure | Hybrid continuation cannot read joined relation names | `E_NO_KEY` | `E_NO_KEY` | `E_NO_KEY` by default; strict falls back safely |
| F6 | Limitation | Latest EAV revision per entity requires all rows locally | Pure memory | Pure memory | Pure memory |

All rows in this table have been checked across the five source implementations
and the packaged Python variant. The local fixtures also agree in both JS bundles.

### F1 — TAKE followed by DROP expands the eligible rows

Cases 8 (`normalized.take-drop`) and 17 (`normalized.take-zero`).

```sel
R .> SORT_BY(_["id"]) .> TAKE(2) .> DROP(1)
  .> MAP(RECORD("id", _["id"]))
```

With IDs `[1,2,3]`, local SEL correctly returns `[2]`. Every SQL implementation
returns `[2,3]` on every database. `TAKE(1) .> DROP(2)` is worse: local SEL
returns nothing, but SQL returns ID 3. Inline, prepared, and hybrid execution
agree on the wrong result, including strict mode.

Cause: the statement compiler adds `DROP` to OFFSET without decreasing the
existing LIMIT. It emits `LIMIT 2 OFFSET 1`, which applies the limit to a
different slice. See [`translator.py`](../../python/sel/sql/translator.py),
`analyze_pipeline`'s TAKE/DROP arms around lines 2004–2013.

Impact: pagination can expose rows outside an earlier eligibility cap; reports,
batch extraction and “take the first eligible N, then skip processed rows” are
affected. A corrective implementation needs to compose slices, including zero
remaining length, rather than independently accumulate SQL clauses.

### F2 — MariaDB's binary collation still ignores trailing spaces

Cases 0, 1 and 14: dimension grouping, text selection and an EAV attribute join.

```sel
R .> FILTER(_["cat"] $== "a") .> MAP(RECORD("id", _["id"]))
R .> BUCKET(_["cat"], RECORD("cat", _K, "n", COUNT(_)))
```

For `(1,"a")` and `(2,"a ")`, local SEL selects only ID 1 and makes two
groups of one. MariaDB selects **both IDs** and returns **one group of two**.
PostgreSQL and SQLite are successful controls. The EAV predicate over attribute
names `color` and `color ` likewise produces one extra join result in MariaDB.

All hosts emit the same defective translation. Strict mode still accepts the
simple equality and grouping cases without caveats. In the EAV join case,
MariaDB strict mode is affected too; SQLite may instead refuse its numeric join
comparison under strict mode.

Cause: [`mysql-family.json`](../../sql/dialects/mysql-family.json) uses
`CAST(... AS CHAR) COLLATE utf8mb4_bin`. On the tested MariaDB server that
collation has padding semantics: “binary collation” is not sufficient for
SEL's byte-exact text identity. This remains broken even though the previous
case-sensitivity grouping defect was fixed.

Impact: padded attribute names and dimension codes match unintended records or
merge report groups. A byte-exact comparison/grouping strategy needs to cover
trailing spaces as well as letter case, and be tested against real servers.

### F3 — Numeric GROUP BY does not preserve SEL grouping identity

Case 6 (`eav.numeric-text-group`).

```sel
R .> BUCKET(_["cat"] + 0, RECORD("key", _K, "n", COUNT(_)))
```

Input text values are `"1"` and `"1.0"`. Local addition preserves their
representations; projected BUCKET groups by structural identity, so the result
has two keys, `"1"` and `"1.0"`, each with count 1. PostgreSQL and MariaDB
group the numeric SQL expression by numeric equality and produce count 2.

This is a cardinality error, not merely different formatting of a decimal.
It reproduces in every translator, inline and prepared, strict off and on,
with no caveat. SQLite refuses the numeric conversion from a text binding and
its pure-memory plan produces the correct two groups.

The local grouping contract is explicit in `conformance/16-bucket.selt` and
[`aggregate.py`](../../python/sel/builtins/aggregate.py) around lines 412–420:
the projected spelling uses structural hashing and `eql`. The SQL grouping
walk treats the computed NUM key as an ordinary SQL numeric group key.

Impact: computed grouping keys can merge records that the local version keeps
separate. The boundary needs either an identity-preserving encoding or an honest
refusal/caveat where the SQL type has erased that identity.

### F4 — Valid RECORD overwrite semantics become invalid SQL

Cases 12 and 15 (with and without intervening TAKE).

```sel
R .> MAP(RECORD("x", _["id"], "x", _["fk"]))
  .> FILTER(_["x"] > 5)
```

For `id=1,fk=9`, local SEL returns `{x:9}`: the last occurrence of a RECORD
key wins, a behavior expressly covered by `rel.record.duplicate-key`.
The SQL projection contains `id AS x, fk AS x`; its outer filter reads `x`.

PostgreSQL raises `42702: column reference "x" is ambiguous`. MariaDB raises
`1060 / 42S21: Duplicate column name 'x'`. All hosts classify it as pure SQL;
strict mode and prepared statements do not prevent the failure.

SQLite's numeric filter over a derived unknown column is refused, causing a
hybrid split. With ordinary associative driver decoding (last duplicate column
wins), that fixture happens to return the correct result. The harness retains
both positional columns too, so it does not mistake this for a correct SQL
projection.

Cause: the MAP translation appends every RECORD pair as a projection instead
of implementing last-write-wins. See `translator.py` around line 1991 and the
corresponding projection loops in the other four hosts. Simply deduplicating
must also respect evaluation/error behavior of overwritten expressions.

### F5 — A joined prefix loses the row shape required by its continuation

Case 13 (`normalized.join-page`).

```sel
R .> LINK(S, _1["fk"] == _2["id"])
  .> SORT_BY(_["R"]["id"]) .> TAKE(2) .> DROP(1)
  .> MAP(RECORD("id", _["R"]["id"], "dimension", _["S"]["cat"]))
```

Local SEL returns order 2 with its dimension label. The whole SQL translation
is refused after the join is wrapped, but the planner accepts a joined prefix
and leaves the final projection local. Its prefix selects only `r.*`: the actual
SQL rows have flat left-table columns such as `id,cat,v,fk`, and omit the right
relation's fields entirely. The continuation
still asks for `_["R"]["id"]`, and the public hybrid executor raises
`E_NO_KEY` when supplied standard associative result rows as SEL Values.

All five executors and the installed wheel reproduce the failure. PostgreSQL
and MariaDB are affected in both strict settings. SQLite is affected with
strict off; strict on rejects the numeric join's approximate translation and
falls all the way back to memory, which succeeds.

This is separate from F1, although the scenario exposes both. The exception is
caused by the missing nested `R` field, not by which page rows survive. Wrapping
the returned fields under `R` would still not recover the omitted `S` values.
The generated SQL and the plan's result-shape contract both need attention;
ordinary documented row handover cannot reconstruct information not selected.

Cause: the generic longest-prefix loop in
[`hybrid.py`](../../python/sel/sql/hybrid.py) around lines 454–470 validates
translatability but not joined-row shape preservation. `execute_hybrid` places
the fetched rows directly in `_INPUT`. Until result decoding is modeled, this
split should be refused or moved before the join.

The whole-statement refusal also differs across implementations: JS/PHP/Python
report `E_SQL_BINDING`; C++/Lisp report `E_SQL_SHAPE`. The generated prefix and
the eventual runtime failure otherwise agree.

### F6 — Latest revision per entity has no useful pushdown

Case 19 (`eav.latest-per-entity`).

```sel
R .> BUCKET(_["fk"])
  .> MAP(RECORD("entity", _K,
               "latest", TOP_BY(_, _["id"], "DESC", 1)))
```

This is a routine temporal EAV extraction: for each entity, take its newest
weight observation. Every planner returns `pure_memory`, for every tested
dialect and strict setting. Local evaluation is correct. This is a pushdown
limitation, not an impossible SEL expression.

With 100,000 revisions and 100 entities, the fallback requires 100,000 input
rows to return 100 newest revisions. An indexed SQL aggregate joined back to
the source returns those 100 rows directly:

```sql
SELECT r.*
FROM r
JOIN (SELECT fk, MAX(id) AS latest FROM r GROUP BY fk) g
  ON r.id = g.latest
```

Revision IDs are unique in this fixture. The SQL result is flat; grouping the
100 resulting rows into SEL's nested output remains a small host-side step.
This baseline therefore measures the avoidable input volume, not a claim that
SQL itself returns the identical nested SEL value. Both routes were checked
to select exactly the same revision IDs.

Single-run timings at 100,000 rows (not a statistical throughput benchmark):

| Execution | Time / result |
|---|---|
| Python SEL evaluation + result materialization | 2,721 ms |
| Python fetch + conversion into SEL Values | 1,274 ms additional |
| Indexed SQLite baseline, 100 rows | 10.2 ms |
| Indexed PostgreSQL baseline, 100 rows | 19.3 ms |
| Indexed MariaDB baseline, 100 rows | 1.0 ms |
| PostgreSQL full fetch, 100,000 rows | 86.0 ms before SEL ingestion/evaluation |
| MariaDB full fetch, 100,000 rows | 69.2 ms before SEL ingestion/evaluation |

The robust result is **1,000× more rows transferred/materialized**; the timings
depend on this machine, runtime and cache state. Python evaluation timings must
not be attributed to the other four languages. The all-host confirmation is
the same pure-memory plan and correct small-fixture result.

An application can work around this with an explicit relation-query binding or
preselection SQL, then reshape the small result locally. Automatic per-group
top-N pushdown/result shaping would directly address this limitation.

## Other observed boundaries and controls

- Division precision differs in SQL: `1/3` is `0.3333333333` locally, but the
  databases return different scales/precision. The fragments carry
  `division-scale` or `decimal-float`; strict mode refuses these. This is an
  advertised approximation, unlike F1–F4's uncaveated defects.
- Division by zero raises locally and in PostgreSQL, but returns SQL NULL in
  the tested MariaDB/SQLite selects. These expressions are also covered by the
  arithmetic caveats; do not treat default SQL mode as an exception-equivalent
  validation engine.
- A dirty EAV numeric conversion (`"bad" + 0 > 0`) raises `E_NOT_NUM` locally
  but its guarded SQL predicate silently excludes the row on PostgreSQL and
  MariaDB, even in strict mode. Guard-to-NULL is documented in the binding
  design, so this is a documented validation/selection semantic limitation,
  not a newly discovered implementation surprise. SQLite refuses and falls
  back to the local error. A rule that must reject malformed data needs a
  separate invalid-value check.
- Double TAKE, double DROP, composite grouping and the simple missing-row outer
  join control behaved as expected within their documented representation and
  ordering constraints. Group result order without ORDER BY is not treated as
  an error by this audit.

## Recommended order of work

Address F1 and F2 first: ordinary successful queries silently return the wrong
rows, and strict mode currently gives no protection. F3 belongs in the same
semantic-correctness work. F4 and F5 should either execute correctly or fail
during planning with a safe fallback; errors after accepting a pure/hybrid
plan are particularly disruptive to integrations. F6 is a separate capability
investment once the existing split points are reliable.

Turn these fixtures into desired-behavior regression cases in the statement
database oracle as well as cross-host tests. All five implementations emitting
identical SQL was true for almost every probe here, including the broken ones.
