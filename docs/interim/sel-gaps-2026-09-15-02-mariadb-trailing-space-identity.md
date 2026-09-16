# F2 — Preserve trailing spaces in MariaDB text identity

Status: implemented in the working tree on 2026-09-16, including safe fallback
for bare-row DISTINCT; the default integration gate and live regressions pass.
Originally confirmed at `55f4aa6`. Proposed priority: **P1, silent wrong
selection and grouping**. Every host's MariaDB translator is affected, including
strict mode and native parameters. PostgreSQL/SQLite are successful controls
for the simple selection/grouping witnesses. MySQL is a required follow-up
test if the shared family map is changed, not a platform tested by this audit.
[Index](sel-gaps-2026-09-15-00-index.md).

## Implementation and verification update — 2026-09-16

Latest recheck extension: SQLite's default BINARY collation is not enough for
columns declared NOCASE or RTRIM; CAST preserves those declarations. The shared
SQLite map now explicitly emits `COLLATE BINARY` for exact text operations.
Direct projected fields also retain their declared type through derived tables,
so MAP/SELECT_COLS/slice wrappers cannot hide a TEXT key from the exact grouping
renderer. UNKNOWN group keys are refused instead of using native SQL equality.
SQL cases 38–40 and `tools/adversarial/identity.py` cover these additional paths.
The Python identity suite now contains 126 passing tests. Final results are in
the consolidated verification report linked from the index.

The Lisp prototype was tested against live MariaDB before rollout. The authored
family map now uses `utf8mb4_nopad_bin`; MySQL overrides it with
`utf8mb4_0900_bin`. All five generated maps were regenerated. Both treatments
retain TEXT, rather than returning binary group keys or making regex subjects
binary. MariaDB documents NO PAD collations in its
[supported collations](https://mariadb.com/docs/server/reference/data-types/string-data-types/character-sets/supported-character-sets-and-collations).

Two additional statement paths needed changes in all five hand-written lanes:

- Expressions over a collated group key (such as `LEN(_K)`) use the constant
  group representative `MIN(key)`, also outside HAVING. MySQL's default
  ONLY_FULL_GROUP_BY otherwise rejects these projections. Direct `_K`
  projections retain the GROUP BY expression itself.
- DISTINCT over MAP projections or explicitly selected TEXT columns applies
  the same exact text treatment and retains the output column name.

The five case runners still mirror MariaDB to MySQL, but pin the one deliberate
collation-spelling substitution. They do not read the expected spelling from
the map, so a bad map entry is not silently accepted. Runtime custom-collation
registration cases remain independent.

Verification completed:

- All five hosts pass 733 SQL cases, including `29-text-identity.sqlt` and
  `30-distinct-text.sqlt`. Three new local cases in
  `conformance/17-text-identity.selt` pass in all five hosts.
- Original grouping/equality/EAV attribute-join witnesses: 306 correct live
  SQL outcomes across the five source hosts plus a fresh wheel, strict off/on,
  inline/native parameters/planner prefixes where accepted, on MariaDB,
  PostgreSQL and SQLite. This rerun preceded the final DISTINCT additions.
- The expanded statement fixture contains empty text, spaces, trailing spaces,
  case distinctions and non-ASCII text. MariaDB 11.8.8, MySQL 8.4.11 and
  PostgreSQL 17 each pass 28 statements; SQLite passes 26 with two documented
  numeric-coercion refusals. Every successful statement is run inline and with
  native parameters. The broader expression/row/regex oracle also passed all
  four servers before the final two DISTINCT cases were added.
- The oracle JSON binding adapter now preserves exact/sargable/guard metadata;
  previously it silently dropped these flags. The statement fixture declares
  CAT sargable, so equality really tests the coarse prefilter plus exact
  residual. Selected cases also run in strict mode.
- Documentation no longer recommends treating any `_bin` schema collation as
  exact. `exact` is an explicit caller promise; a PAD SPACE collation does not
  meet it. Custom connection charset examples must choose a verified NO PAD
  collation rather than manufacture a `*_bin` name.

### Follow-up completion of the DISTINCT guard

All five translators now refuse bare-row DISTINCT/DEDUPE: relation fields are
read bindings, not a closed schema, so emitting a guessed deduplication projection
could drop undeclared data. Explicit UNKNOWN and NUM projections also refuse
DISTINCT without a structural-identity proof. Typed TEXT projections retain
NO PAD SQL support. Safe projections can still run as a prefix; deduplication
runs locally. Computed numeric projections are kept behind F3's identity barrier.

`sql/cases/31-distinct-schema.sqlt` and `python/tests/test_sql_identity.py`
cover the guard, including undeclared fields and actual continuation values.
The later recheck confirmed all original F2 witnesses across all five source
hosts plus a fresh wheel on PostgreSQL/MariaDB/SQLite. The final default
integration gate passes; historical audit JSON remains intact.

## Reproduction

Use SQL `VARCHAR` data (not a CHAR column which might already erase padding):

```sql
CREATE TABLE r(id INTEGER, cat VARCHAR(80));
INSERT INTO r VALUES (1, 'a'), (2, 'a ');
```

Bind R to r/r, ID as NUM and CAT as TEXT. Locally, equivalent fixture data is:

```sel
R = LIST(RECORD("id", 1, "cat", "a"),
         RECORD("id", 2, "cat", "a "));
R .> FILTER(_["cat"] $== "a") .> MAP(RECORD("id", _["id"]))
```

Expected: only ID 1. MariaDB: IDs 1 and 2. A second query:

```sel
R .> BUCKET(_["cat"], RECORD("cat", _K, "n", COUNT(_)))
```

Expected: `("a",1)` and `("a ",1)`. MariaDB: `("a",2)`.
Cases **0**, **1**, and **14** in [the audit](../../tools/adversarial/probe.py)
cover grouping, equality and an entity-key join between two EAV attribute
partitions. The attribute predicate also admits `color ` when asked for `color`.

## Root cause

The [mysql-family map](../../sql/dialects/mysql-family.json) declares
`textCast = CAST({0} AS CHAR)` and `textCollate = COLLATE utf8mb4_bin`
(lines 14–19). This server's collation retains padding comparison semantics:
case-sensitive/binary collation does not imply trailing-space-sensitive
identity. Group keys are marked exact after applying that treatment, so both
the fragment's caveats and strict-mode gate miss the semantic error.

The existing fix for grouping `A` versus `a` is necessary but insufficient.
Do not repair this by trimming local values: the byte distinction is part of
SEL's semantics and valid data can depend on it.

## Implementation direction

Choose and live-test a trailing-space-sensitive representation/comparison for
each affected dialect. Candidates include a supported no-padding collation or
a byte-string comparison/key representation. These are investigation options,
not verified drop-in replacements. Preserve the **original text value** of _K
and projected fields if the internal comparison key becomes binary.

Separate comparison identity from generic text coercion if necessary. Changing
the global `textCast`/`textCollate` blindly can affect regex, string functions,
ordering, numeric-to-text conversion, and result kinds. A length check beside
equality alone does not fix GROUP BY, DISTINCT or ordering. For sargable bindings,
retain an exact residual predicate after any coarse index prefilter; do not
mark a PAD SPACE schema collation exact simply because its name ends in `_bin`.

## Code lanes and generated data

| Host | Group-key path consuming the shared text treatment |
|---|---|
| Python | [translator.py](../../python/sel/sql/translator.py): `_group_key` at 904, group projection and GROUP BY near 2131/2216 |
| JS | [translator.mjs](../../js/src/sql/translator.mjs): `groupKey` at 309, projection/GROUP BY near 2368/2467 |
| PHP | [Translator.php](../../php/src/Sql/Translator.php): `groupKey` at 1110, projection/GROUP BY near 3099/3217 |
| C++ | [sel_sql_translator.cpp](../../cpp/sel_sql_translator.cpp): `group_key` at 465, projection/GROUP BY near 2897/3010 |
| Lisp | [translator.lisp](../../lisp/src/sql/translator.lisp): `group-key` at 321, projection/GROUP BY near 2264/2365 |

Also review the byte-comparison templates in [ansi.json](../../sql/dialects/ansi.json),
family overrides, and the host [binding flags](../../python/sel/sql/binding.py)
(`exact`, `sargable`, `guard`, `collation`, prefilter options). Equivalent binding
factories are [JS](../../js/src/sql/binding.mjs),
[PHP](../../php/src/Sql/Binding.php), [C++](../../cpp/sel_sql_binding.cpp),
and [Lisp](../../lisp/src/sql/binding.lisp).

Author changes in [sql/dialects](../../sql/dialects/) and regenerate with
[gen-sql-map.mjs](../../tools/gen-sql-map.mjs). Generated lanes are
[JS](../../js/src/sql/_map.mjs), [Python](../../python/sel/sql/_map.py),
[PHP](../../php/src/Sql/MapData.php), [C++](../../cpp/sel_sql_map_data.cpp),
[Lisp](../../lisp/src/sql/map-data.lisp). Do not patch generated copies separately.

## Acceptance criteria

- MariaDB returns the exact one-row/two-group expectations in every host,
  strict off/on, inline/native parameters, including projected _K and HAVING.
- Extend the live statement oracle with empty text, one/two spaces, trailing
  spaces, `A/a`, and non-ASCII text. Cover equality/inequality, group identity,
  sorting and exact-residual behavior; do not infer all operators work from `$==`.
- Preserve successful PostgreSQL/SQLite controls; test a real MySQL server
  before shipping a shared mysql-family change.
- Distinguish schema-level CHAR padding from translator-level VARCHAR comparison
  so a fixture does not accidentally hide the original characters.
- Until exact semantics are implemented, prefer a clear refusal or accurately
  propagated caveat with safe strict fallback over an incorrect “exact” result.
