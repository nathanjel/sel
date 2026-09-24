# SEL documentation

SEL is a small expression language for business rules with five
implementations — Python, JavaScript, PHP, C++23 and Common Lisp — that give the
same answer, in memory or translated for a database. Start wherever your
question is.

## Reading paths

| If you want to… | Read |
|---|---|
| know what SEL is and why it is shaped this way | [Overview](overview.md) |
| trust that five hosts really agree | [How parity is achieved and guaranteed](parity.md) |
| write rules | [Syntax](syntax.md) → [Operators](operators.md) → [Functions](functions.md) |
| call SEL from an application | [Using SEL](usage/README.md), then [Validation](usage/validation.md) or [Scripting](usage/scripting.md) |
| filter rows in a database with a rule | [SQL conditions](usage/sql-conditions.md) |
| run whole queries and data pipelines | [SQL pipelines](usage/sql-pipelines.md), then the schema that looks like yours |
| look up a binding, a dialect or a refusal code | [SEL and SQL — reference](sql.md) |
| add functions, dialects or SQL spellings | [Extending SEL](extending.md) |
| change SEL itself | [Contributing](contributing.md) |

## Contents

**Start here**

- [Overview](overview.md) — the problem, what a program is, the design
  principles, where SEL fits, what it leaves out
- [How parity is achieved and guaranteed](parity.md) — never the host's idea of
  a number or a string; generated artifacts; every layer of the gate

**The language**

- [Syntax](syntax.md) — programs, names, values and kinds, numbers, text,
  lists and records, booleans, `NULL`, variables, binders, errors
- [Operators](operators.md) — precedence, exact arithmetic, the three comparison
  families, coalescing, assignment, the pipeline operator `.>`
- [Functions](functions.md) — control, aggregates, sorting, grouping, slicing,
  joins, null safety, text, numbers, binary, regular expressions

**Using SEL** — every snippet in five languages, quoted from programs the test
suite runs

- [Using SEL](usage/README.md) — installing; the host API in one page
- [A REPL in thirty lines](usage/repl.md)
- [Validation](usage/validation.md) — a form's rules, in the browser and on the server
- [Scripting with host functions](usage/scripting.md) — the application's
  functions, the business's policy

**SEL and SQL**

- [SQL conditions](usage/sql-conditions.md) — a rule as a `WHERE` clause, on
  SQLite, MariaDB and PostgreSQL, naive and described
- [SQL pipelines](usage/sql-pipelines.md) — whole statements, hybrid plans, and
  the example data
  - [Star schema](usage/sql-star.md) — PostgreSQL
  - [Entity–attribute–value](usage/sql-eav.md) — SQLite
  - [Third normal form](usage/sql-3nf.md) — PostgreSQL
  - [Unnormalised data](usage/sql-flat.md) — MariaDB
  - [Complex data, in memory](usage/in-memory.md) — PostgreSQL, and no database at all
- [SEL and SQL — reference](sql.md) — dialects, entry points, fragments,
  bindings, kinds, refusals, caveats

**Extending**

- [Extending SEL](extending.md) — host functions, dialects of your own, SQL
  spellings, and how a builtin joins the language
- [Contributing](contributing.md) — the order of work, the argument and value
  APIs, operators, conformance cases, the traps each host has fallen into, the
  checks, a sixth host

**Reference**

- [Builtin index](reference/builtins.md) and [Limits and error codes](reference/limits.md) — generated from the specification's manifests
- [The specification](../spec/SPEC.md), [the grammar](../spec/grammar.md) and
  [the error codes](../spec/errors.md) — normative
- [The dialect map format](../sql/MAP.md) and [the SQL error codes](../sql/errors.md)
- [The conformance suite](../conformance/README.md) and [the SQL case format](../sql/cases/README.md)

**Internals**

- [SQL translation: the design](internals/sql-translation.md) and
  [the kind warrant](internals/sql-kinds.md)
- [Math-plan operations](internals/math-ops.md) — generated
- [The test harness](../tools/README.md), [Packaging](../PACKAGING.md), [Changelog](../CHANGELOG.md)

## Two ways to read this

These pages are Markdown, and read well on GitHub. The same pages are also a
styled site — language tabs instead of five stacked code blocks, a navigation
sidebar, dark mode:

```sh
node tools/build-docs.mjs --serve 8080      # needs `npm install` once
docker build -f docs/Dockerfile -t sel-docs . && docker run --rm -p 8080:80 sel-docs
```

Every `expr  => result` example on these pages is executed by all five
implementations, every code block quoted from `examples/` is compared with the
file it came from, and every link is checked — the documentation is part of the
test suite.
