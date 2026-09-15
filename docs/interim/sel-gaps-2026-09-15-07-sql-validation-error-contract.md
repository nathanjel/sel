# C1 — Make SQL validation/error semantics explicit

Status: observed at `55f4aa6`; **documented semantic limitation**, not a newly
discovered defect. Proposed priority: **P2, API contract/documentation and
validation planning**. This work item captures stress-test results that should
not get lost alongside the five correctness bugs.
[Index](sel-gaps-2026-09-15-00-index.md).

## Observed boundaries

| Scenario / audit case | Local SEL | SQL behavior |
|---|---|---|
| Dirty numeric text, case 10 | `E_NOT_NUM` | PostgreSQL/MariaDB guarded predicate drops the invalid row, including strict mode; SQLite refuses and evaluates locally |
| Division precision, cases 2/3 | `1/3` yields `0.3333333333` | Dialects differ in scale/precision; fragments expose `division-scale` or `decimal-float`, and strict mode refuses |
| Division by zero, case 11 | `E_DIV_ZERO` | PostgreSQL raises a DB error; MariaDB/SQLite SELECT returns NULL in tested default mode; the arithmetic caveat prevents strict pushdown |

The first witness is:

```sel
R = LIST(RECORD("id", 1, "cat", "bad"),
         RECORD("id", 2, "cat", "2"));
R .> FILTER(_["cat"] + 0 > 0) .> MAP(RECORD("id", _["id"]))
```

Bind CAT as TEXT to a VARCHAR column. Local evaluation rejects `"bad"`.
PostgreSQL/MariaDB return only ID 2: a numeric guard produces NULL for the bad
input, and WHERE eliminates the resulting unknown predicate. The fragment can
have no caveat even with strict enabled. This is intentional guard-to-NULL
behavior documented in the binding design, but a validator must not interpret
the successful selection as “all input data was valid”.

## Proposed processing decision

Clarify whether `strict` promises only refusal of declared approximations or
also preservation of local failures. Its current observed behavior is the
former; do not promise the latter without implementation support. Keep
selection policies and validation policies explicit at the API/plan boundary.

Possible follow-up work, requiring a design decision rather than an automatic
bug patch:

1. Document examples of dirty-input filtering and runtime error differences
   beside the strict-mode and hybrid API contracts, not only in binding internals.
2. Expose a diagnostic/capability for possible error-to-NULL or error-to-filtered-row
   behavior, distinct from numerical approximation. If adding a validation-safe
   policy, define its refusal/fallback behavior in all five hosts.
3. Offer an invalid-input preflight or a validation mode that keeps the relevant
   operations local. A database-only successful SELECT must not suppress a
   required validation failure by accident.
4. For a preflight plus subsequent selection, define consistent-snapshot behavior
   if concurrent writes matter. Do not claim a two-query check validates a later,
   potentially changed dataset without that guarantee.

Using a separate invalid-value query based on ISNUM is an application-level
option to investigate. Combining a numeric predicate with a guard in a SQL
WHERE clause does not by itself establish SEL's error/evaluation-order semantics.
Do not remove safe numeric guards and expose database coercion errors as a fix.

## Code and documentation lanes

The behavior is authored in `numericGuard` in
[mysql-family.json](../../sql/dialects/mysql-family.json) (around line 17) and
[postgresql.json](../../sql/dialects/postgresql.json) (around line 20).
The same generated map is consumed by all five translators; regenerate rather
than patch generated copies. See [F2's generated-lane map](sel-gaps-2026-09-15-02-mariadb-trailing-space-identity.md).

| Host | Binding/fragment surfaces to inspect if changing policy |
|---|---|
| Python | [binding.py](../../python/sel/sql/binding.py), [fragment.py](../../python/sel/sql/fragment.py), [translator.py](../../python/sel/sql/translator.py) |
| JS | [binding.mjs](../../js/src/sql/binding.mjs), [fragment.mjs](../../js/src/sql/fragment.mjs), [translator.mjs](../../js/src/sql/translator.mjs) |
| PHP | [Binding.php](../../php/src/Sql/Binding.php), [Fragment.php](../../php/src/Sql/Fragment.php), [Translator.php](../../php/src/Sql/Translator.php) |
| C++ | [sel_sql_binding.cpp](../../cpp/sel_sql_binding.cpp), [sel_sql.hpp](../../cpp/sel_sql.hpp), [sel_sql_translator.cpp](../../cpp/sel_sql_translator.cpp) |
| Lisp | [binding.lisp](../../lisp/src/sql/binding.lisp), [fragment.lisp](../../lisp/src/sql/fragment.lisp), [translator.lisp](../../lisp/src/sql/translator.lisp) |

Read [SQL-KINDS.md](../SQL-KINDS.md), the binding and strict-mode sections of
[SQL-TRANSLATION.md](../SQL-TRANSLATION.md), and the numeric-guard consistency
checks in the host SQL map modules. If policy changes, propagate it through all
planner entry points and host declarations, not just scalar translation.

## Acceptance criteria

- Document a precise strict-mode guarantee illustrated by all three observed
  boundaries. Avoid claiming identical error codes/locations across native DB
  errors and local evaluation unless implemented.
- Pin expected behavior for malformed numeric text and division by zero in both
  default and strict modes across the database oracle and hybrid tests.
- If introducing validation-safe execution, a dirty row cannot disappear into
  a successful result: surface a defined validation result/error or choose local
  execution, consistently in every host.
- Preserve the existing explicit division caveats and successful clean-input
  controls. Do not turn documented approximations into undocumented behavior.
