# Examples

Every directory here is one program written five times — `python.py`,
`js.mjs`, `php.php`, `cpp.cpp`, `lisp.lisp` — and the five print byte-identical
output, recorded in `output.txt`. The test suite runs them all and compares them,
so the code the documentation quotes is code that runs.

| Directory | What it shows | Documented in |
|---|---|---|
| [`plain/`](plain/) | the host API: evaluate, compile once, contexts, results, errors, `dependencies()` | [Using SEL](../docs/usage/README.md) |
| [`complex/`](complex/) | the language's reach: aggregates, binders, text and regex, refusals, error positions | [Functions](../docs/functions.md) |
| [`repl/`](repl/) | a read-eval-print loop; fed `session.txt` | [A REPL in thirty lines](../docs/usage/repl.md) |
| [`validation/`](validation/) | a form's rule set: compiled once, `E_ABORT` versus a broken rule, re-check on change | [Validation](../docs/usage/validation.md) |
| [`scripting/`](scripting/) | host functions, and `fulfil.sel`, a script that decides what happens to an order | [Scripting](../docs/usage/scripting.md) |
| [`sql/`](sql/) | a rule pushed down: bindings, params mode, every dialect, refusal, caveats | [SQL reference](../docs/sql.md) |
| [`dialect/`](dialect/) | a dialect of your own: placeholders, respelling, withdrawing, builders | [Extending SEL](../docs/extending.md#extending-the-sql-layer) |
| [`sql-conditions/`](sql-conditions/) | rules as `WHERE` clauses on SQLite, MariaDB and PostgreSQL — naive and described bindings | [SQL conditions](../docs/usage/sql-conditions.md) |
| [`sql-star/`](sql-star/) | pipelines over a star schema, PostgreSQL | [Star schema](../docs/usage/sql-star.md) |
| [`sql-eav/`](sql-eav/) | pipelines over entity–attribute–value rows, SQLite | [EAV](../docs/usage/sql-eav.md) |
| [`sql-3nf/`](sql-3nf/) | pipelines over a normalised shop, PostgreSQL | [Third normal form](../docs/usage/sql-3nf.md) |
| [`sql-flat/`](sql-flat/) | pipelines over one wide export table, MariaDB | [Unnormalised data](../docs/usage/sql-flat.md) |
| [`sql-functions/`](sql-functions/) | the application's own functions spelled as PostgreSQL SQL and PL/pgSQL functions, checked against their local implementations | [Your own functions, in SQL](../docs/usage/sql-functions.md) |
| [`sql-complex/`](sql-complex/) | a report no database can take a share of: SQL loads, SEL computes | [Complex data](../docs/usage/in-memory.md) |
| [`memory-complex/`](memory-complex/) | the same report over data generated in memory | [Complex data](../docs/usage/in-memory.md) |
| [`lib/`](lib/) | what those share: the database runner per host, the support-desk generator and report | [SQL pipelines](../docs/usage/sql-pipelines.md#running-a-plan) |
| [`fn-simple/`](fn-simple/), [`fn-complex/`](fn-complex/), [`fn-sql/`](fn-sql/) | reference fragments for adding a builtin to SEL itself — strict, lazy, with a SQL spelling | [Contributing](../docs/contributing.md#adding-a-function) |

## Running them

```sh
tools/check-examples.sh                  # every category that needs only the host, all five hosts
tools/check-usage.sh                     # the ones marked LIVE, against real databases (Docker)
PYTHONPATH=python python3 examples/validation/python.py      # one of them, one host
```

A `LIVE` directory needs PostgreSQL, MariaDB or SQLite: `tools/check-usage.sh`
starts throwaway servers, loads each directory's `seed.<dialect>.sql` into a
database of its own, and runs the five hosts in an image that has every driver
(`tools/usage.Dockerfile`). The C++ programs build with `cd cpp && make`; the
database ones need the client libraries, so they are built inside that image
with `make -C cpp BUILD=build-usage build-usage/example-<dir>`.
