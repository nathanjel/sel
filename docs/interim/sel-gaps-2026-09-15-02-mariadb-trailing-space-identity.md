# F2 — Preserve trailing spaces in MariaDB text identity

Status: confirmed, open at `55f4aa6`. Proposed priority: **P1, silent wrong
selection and grouping**. Every host's MariaDB translator is affected, including
strict mode and native parameters. PostgreSQL/SQLite are successful controls
for the simple selection/grouping witnesses. MySQL is a required follow-up
test if the shared family map is changed, not a platform tested by this audit.
[Index](sel-gaps-2026-09-15-00-index.md).

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
