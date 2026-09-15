# F3 — Preserve SEL identity for computed numeric group keys

Status: confirmed, open at `55f4aa6`. Proposed priority: **P1, wrong group
cardinality**. All five hosts and the wheel merge groups on PostgreSQL/MariaDB,
including strict mode. SQLite refuses the text-to-number computation and its
pure-memory fallback correctly returns two groups.
[Index](sel-gaps-2026-09-15-00-index.md).

## Reproduction and required semantics

```sel
R = LIST(RECORD("cat", "1"), RECORD("cat", "1.0"));
R .> BUCKET(_["cat"] + 0, RECORD("key", _K, "n", COUNT(_)))
```

Local result: two groups, keys `"1"` and `"1.0"`, each with count 1.
PostgreSQL: one group with count 2. MariaDB: one group with count 2, with the
key rendered as `1.0000000000` in the audit's driver output. This is **not**
just numeric formatting drift: a group has disappeared.

For SQL, bind R to r/r and CAT to VARCHAR `cat` with kind TEXT, containing
`'1'` and `'1.0'`; translate only the pipeline. Complete witness: case **6**
`eav.numeric-text-group` in [probe.py](../../tools/adversarial/probe.py).

Projected BUCKET groups by SEL structural identity, not numeric comparison.
Arithmetic produces text scalars with representation/scale preserved here;
`"1"` and `"1.0"` are different structural keys even if numerically equal.
See [conformance/16-bucket.selt](../../conformance/16-bucket.selt), including
the identity-contract comment and projected-list-key tests.

## Root cause and design decision

The SQL walk infers NUM for the computed key and emits ordinary numeric
GROUP BY. Database equality for NUMERIC/DECIMAL merges values the local
structural hash and `eql` keep distinct. No caveat marks this loss, so strict
mode also accepts the result.

An SQL numeric type and an exact numeric comparison are not proof of exact
SEL **identity**. Likewise, `CAST(computed_numeric AS TEXT)` is not a general
repair: the computation/coercion may already have normalized the representation
or imposed a different scale before the cast.

Suggested short-term resolution: refuse affected grouping shapes unless an
identity-preserving translation can be proven, letting the planner back up
before BUCKET. A documented approximation would at least need a propagated
caveat and strict-mode refusal; do not silently redefine local BUCKET equality.

For fuller support, describe what identity information a binding/expression
preserves, encode the grouping key without losing it, and reconstruct _K in
the original SEL form. Keep that proof distinct from existing text `exact`
flags and numeric-kind inference. An integer column may admit a simpler proof
than a computed decimal derived from text, but that must be established rather
than assumed for every NUM fragment.

## Code lanes

| Host | Local identity reference | SQL group-key renderer |
|---|---|---|
| Python | [aggregate.py](../../python/sel/builtins/aggregate.py): `do_bucket`, structural hash/`eql` at 418–420 | [translator.py](../../python/sel/sql/translator.py): `_group_key` at 904, bucket projection and GROUP BY |
| JS | [aggregate.mjs](../../js/src/builtins/aggregate.mjs): `doBucket`, `structuralHash` around 443 | [translator.mjs](../../js/src/sql/translator.mjs): `groupKey` at 309 |
| PHP | [Structure.php](../../php/src/Builtins/Structure.php): bucket logic around 700–760 | [Translator.php](../../php/src/Sql/Translator.php): `groupKey` at 1110 |
| C++ | [sel.cpp](../../cpp/sel.cpp): BUCKET registration at 4102 and bucket-building helper around 3971; `Value::structural_hash` | [sel_sql_translator.cpp](../../cpp/sel_sql_translator.cpp): `group_key` at 465 |
| Lisp | [aggregate.lisp](../../lisp/src/builtins/aggregate.lisp): `do-bucket` at 494 | [translator.lisp](../../lisp/src/sql/translator.lisp): `group-key` at 321 |

Review the NUMERIC/DECIMAL conversions and `numericGuard` in
[postgresql.json](../../sql/dialects/postgresql.json) and
[mysql-family.json](../../sql/dialects/mysql-family.json). The planner must
retain group members if translation becomes unavailable; do not split at a
bare grouped SQL key list (see F5/F6 and the existing bucket split guard).

## Acceptance criteria

- The witness yields two groups in all executing modes, or is refused with a
  correct local fallback. Strict mode must never silently return count 2.
- Add local contract tests for `1`, `1.0`, `1.00` and computed decimal keys;
  use the exact local results as the oracle rather than a float-based normalizer.
- Proposed additional tests: negative zero representations, leading-zero source
  text, composite keys, nested _K use and HAVING. First pin their actual local
  semantics; these variants have not all been proven defective by the audit.
- Test both projected BUCKET and bare-BUCKET-then-MAP spellings without assuming
  their key contracts are interchangeable.
- Assert group counts, member multiplicity and projected keys, not just numeric
  equality of output values. Preserve known-correct ordinary group cases.
