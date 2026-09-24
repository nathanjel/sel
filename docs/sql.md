# SEL and SQL — reference

The SQL layer translates a compiled SEL program for a database. It is a second
reader of the same syntax tree the evaluator runs — nothing about how a program
evaluates changes. This page is the reference; [SQL conditions](usage/sql-conditions.md)
and [SQL pipelines](usage/sql-pipelines.md) are the guided tour, and
[the design document](internals/sql-translation.md) is the full contract.

- [What it produces](#what-it-produces)
- [Dialects](#dialects)
- [Entry points](#entry-points)
- [Fragments](#fragments)
- [Bindings](#bindings)
- [Kinds, guards and strict mode](#kinds-guards-and-strict-mode)
- [Refusals](#refusals)
- [Caveats](#caveats)
- [Your own functions](#your-own-functions)
- [Extending the map](#extending-the-map)
- [Loading the SQL layer](#loading-the-sql-layer)

---

## What it produces

| You call | You get | Used as |
|---|---|---|
| `translate` | an **expression** | a `WHERE`, `HAVING`, `CHECK`, join condition or select-list column the application builds a statement around |
| `translate_statement` | a **statement** | a whole `SELECT` for a pipeline over relations — joins, filters, grouping, ordering, limits |
| `plan_hybrid` | a **plan** | the longest statement the database can answer, plus the in-memory program that finishes the job — or all SQL, or all memory |
| `execute_hybrid` | the **result** | runs a plan through the application's runner |

Four properties hold throughout:

1. **The map is data.** Every SQL spelling is authored once, in
   `sql/dialects/*.json`, and generated into every host. No host has an opinion
   of its own about what `UPPER` becomes.
2. **Refusal is a result.** What cannot be expressed faithfully is refused whole,
   before any SQL is written, with a code saying why.
3. **Output is deterministic.** The same program, dialect and bindings give the
   same bytes in every host — `sql/cases/*.sqlt` asserts the exact strings.
4. **Variables become schema.** The application says where each name lives, not
   what it holds.

## Dialects

| Dialect | Extends | Target? | Notes |
|---|---|---|---|
| `ansi` | — | no (a base) | what standard SQL says, and nothing else; strongly typed |
| `mysql-family` | `ansi` | no (a base) | what MariaDB and MySQL agree on |
| `mariadb` | `mysql-family` | yes | from 10.5; verified on 11.8 |
| `mysql` | `mysql-family` | yes | from 8.4 |
| `postgresql` | `ansi` | yes | from 15; verified on 17 |
| `sqlite` | `ansi` | yes | from 3.35; no exact decimal type, no `REGEXP` by default |

A base cannot be translated for (`E_SQL_DIALECT`); it exists to be inherited.
Lookups walk the chain key by key. `Sql.dialects()` lists the targets, including
any the application registered.

## Entry points

| | Python | JavaScript | PHP | C++ | Common Lisp |
|---|---|---|---|---|---|
| expression | `Sql.translate(p, d, b)` | `Sql.translate(p, d, b)` | `Sql::translate($p, $d, $b)` | `Sql::translate(p, d, b)` | `(sel.sql:translate p d b)` |
| …or `None` | `Sql.try_translate` | `Sql.tryTranslate` | `Sql::tryTranslate` | `Sql::try_translate` | `sel.sql:try-translate` |
| statement | `Sql.translate_statement` | `Sql.translateStatement` | `Sql::translateStatement` | `Sql::translate_statement` | `sel.sql:translate-statement` |
| plan | `plan_hybrid(p, d, b)` | `planHybrid(p, d, b)` | `Sql::planHybrid` | `Sql::plan_hybrid` | `sel.sql:plan-hybrid` |
| run a plan | `execute_hybrid(plan, runner, ctx)` | `executeHybrid(plan, runner, ctx)` | `Sql::executeHybrid` | `Sql::execute_hybrid` | `sel.sql:execute-hybrid` |

`p` is a compiled program, `d` a dialect name, `b` the bindings. An optional
fourth argument carries options: `strict` (below) in every host, and in the three
dynamic hosts the logical optimiser's `fuseFilters` and `foldConstants`, which the
planner forwards. The planner is the only entry point that optimises;
`translate` and `translate_statement` render the program as written.

A plan exposes `pure_sql`, `pure_memory` and `is_hybrid` (each host spells them
its way), the `sql_statement` fragment, the `continuation_program` that runs in
memory over `_INPUT`, and `source_tables` — the physical tables the SQL side
reads.

## Fragments

What `translate` and `translate_statement` return.

| Member | Gives |
|---|---|
| `as_condition(mode)` | the SQL, required to be boolean — `E_SQL_SHAPE` otherwise |
| `as_value(mode)` | the SQL as a value, for a select list |
| `as_statement(mode)` | a statement fragment's SQL |
| `bindings()` | in `params` mode, the values for the placeholders, in order |
| `kind` | `NUM`, `TEXT`, `BOOL`, `BIN` or `UNKNOWN` |
| `dialect` | the dialect it was rendered for |
| `caveats` | the closed-vocabulary names of every inexact entry used; empty means exact |

`mode` is `inline` (literals written into the SQL, escaped for the dialect — for
reading and for statements built once) or `params` (every text and binary literal
a placeholder, numbers inlined — what a driver should get). The placeholder is
`?` in every shipped dialect; a derived dialect can number them
([Extending the map](#extending-the-map)).

## Bindings

Built by constructors, in code — never decoded from a document, so a malformed
binding is impossible to construct rather than something to detect. Each host
spells them its own way (`Binding.column` / `Binding::column` /
`sel.sql:binding-column`); the arguments are the same.

| Constructor | Describes |
|---|---|
| `column(name, table?, type?, …)` | one column, qualified by a table or alias, with a type: `NUM`, `TEXT`, `BOOL`, `BIN` or `UNKNOWN` (the default) |
| `raw(sql, type?, …)` | an expression the application writes — the one place SQL is taken verbatim |
| `columns(c1, c2, …)` | several columns of one row, treated as a list: an aggregate over it unrolls, `V[2]` is the second |
| `relation(table, alias, fields, scalar?, correlate?, prefilter?)` | rows of a table. `fields` maps SEL keys to column bindings; `correlate` is the SQL joining it to the outer row; `scalar` names the field a bare element means |
| `relation_query(sql, alias, fields, …)` | the same, over a query the application writes |
| `value(v, type?)` | a constant supplied now, rendered as a literal (a placeholder in `params` mode) |
| `.with_unique_key("id")` | on a relation: a single-column unique, non-null key, which enables the "latest member per group" plan |

A `column` or `raw` binding takes flags that describe the column honestly and
make the SQL tighter:

| Flag | Says | Effect |
|---|---|---|
| `exact` | the column already compares bytes exactly (a binary collation, `utf8mb4_nopad_bin`, …) | text comparisons need no cast or collation, so an index can be used |
| `sargable` | the column's collation is case-insensitive | on MariaDB/MySQL, an index-friendly equality pre-filter plus the exact check |
| `guard` | the values may not all be numbers even though the type says `NUM` | keeps the numeric guard — for EAV values and other dirty columns |
| `collation` | a spelling of the above: `"exact"`/`"binary"`, `"sargable"`/`"prefilter"`, `"default"` | |
| `prefilter` | `"separate"` or `"inline"` | where a relation's pre-filter goes in an `EXISTS` |

The choice between `columns` and `relation` is the most consequential one: the
same `ALL(V, _ > 0)` becomes an `AND` over three columns of the current row, or a
`NOT EXISTS` over another table. Only the application knows which the schema has.

## Kinds, guards and strict mode

The translator infers a kind for every node. A declared type is what it starts
from; `UNKNOWN` costs something in each position:

- where a **boolean** is required (`AND`, `NOT`, an `IF` condition, a condition
  fragment), an `UNKNOWN` operand is refused — no dialect can be asked whether a
  column is boolean;
- as an **arithmetic or comparison operand**, it is wrapped in the dialect's
  *numeric guard*, so a value SEL would refuse becomes `NULL` rather than a number
  the server invented (`CASE WHEN col REGEXP '…' THEN CAST(col AS DECIMAL) END` on
  MariaDB). SQLite cannot test whether text is a number, so there the rule is
  refused;
- **text** comparisons are always rendered under a binary collation (`COLLATE
  "C"`, `utf8mb4_nopad_bin`, `COLLATE BINARY`), because SEL compares bytes —
  unless the binding says `exact`.

`strict: true` refuses every translation that would carry a caveat, for the
applications that want exact or nothing.

## Refusals

A refusal is an exception of the SQL error class with a code, a message and the
position of the SEL node responsible; `try_*` returns nothing instead.

| Code | When |
|---|---|
| `E_SQL_DIALECT` | no such dialect, a base rather than a target, or a server version below an entry's |
| `E_SQL_UNSUPPORTED` | no spelling in this dialect — or only a caveated one, under `strict` |
| `E_SQL_UNBOUND` | a variable the bindings do not describe |
| `E_SQL_BINDING` | a malformed binding, an unknown field, or two relations sharing an alias |
| `E_SQL_ASSIGN` | an assignment or sequence SQL cannot express (compound assignment, reassignment, an assignment inside a body, …) |
| `E_SQL_INVALID` | every argument is known, and SEL itself rejects the expression (the message carries SEL's own code) |
| `E_SQL_DEPTH` | nested deeper than SEL evaluates |
| `E_SQL_SHAPE` | a list where a scalar is needed, a row of several fields used as one value, a non-boolean condition, and the other shapes SQL has no value for |

[sql/errors.md](../sql/errors.md) is the normative list. A registration mistake —
a malformed dialect or entry — is the host's own start-up error, never an SQL
error, so `try_translate` cannot swallow it.

## Caveats

A caveat marks a spelling that is mapped but not exact. The vocabulary is closed,
so an application can branch on it:

| Caveat | Meaning |
|---|---|
| `decimal-float` | the server has no exact decimal type (SQLite) |
| `numeric-scale` | the value is equal but its scale differs from SEL's |
| `scale-limit` | the server's decimal type caps the result's scale (the MySQL family's `DECIMAL(65,10)`) |
| `division-scale` | `/` gives a different scale, or truncates |
| `modulo-integer` | `%` works on integers only |
| `power-float` | `POWER` returns a float |
| `unicode-case` | `UPPER`/`LOWER` map beyond ASCII |
| `trim-charset` | `TRIM` strips a different set of characters |
| `regex-engine` | the server's regex dialect is not SEL's portable subset |
| `concat-null` | concatenation yields `NULL` if any operand is `NULL` |
| `input-laxity` | the server accepts input SEL rejects (it agrees on everything SEL accepts) |
| `text-order` | a text sort key orders by bytes where SEL orders number-shaped text as numbers |
| `host-function` | the fragment uses the application's spelling of its own function, which SEL cannot check |
| `text-collation`, `length-units`, `rounding-mode` | in the vocabulary; no shipped dialect declares them |

An empty `caveats` list is the layer's promise that the fragment means exactly
what the program means — a promise `tools/check-sql-oracle.sh` tests against real
servers.

## Your own functions

A function the application registered ([spec §8.1](../spec/SPEC.md#81-host-functions))
has no SQL until the application gives it a spelling for a dialect — a call to a
database function, a stored function, or an inline expression — with the same
`define` that respells a builtin. [Your own functions, in SQL](usage/sql-functions.md)
is a worked example with PostgreSQL stored functions in all five languages;
[sql/MAP.md §4.7](../sql/MAP.md#47-spelling-a-host-function) is the contract.

| Rule | |
|---|---|
| Order | register the function first; a spelling for any other name is the host's start-up error |
| Arity | the registration's, recorded with the spelling; an entry's `arity` may only narrow it; a later registration with a different arity makes translation refuse (`E_SQL_UNSUPPORTED`) until the spelling is defined again |
| Result | `ret` is a scalar kind; a function that returns a list has no spelling |
| Arguments | the builtins' argument rules do not apply; the entry's `args` does, per position |
| Honesty | every use carries the caveat `host-function`; `strict` refuses it |
| `reset()` | drops the spellings; the functions stay registered |
| No spelling | `E_SQL_UNSUPPORTED` at the call, before its arguments are examined; the planner keeps the step in memory |

`args` — one per position, `ANY` past the end:

| Kind | The argument must be | It renders as |
|---|---|---|
| `ANY` | any scalar | itself |
| `TEXT` | not a BOOL or a BIN | itself |
| `NUM` | a number: a constant is checked when translating, a BOOL or BIN is refused | itself when declared `NUM`, otherwise through the numeric guard |
| `BOOL` / `BIN` | proved to be of that kind | itself |
| `LIST` | a list known when translating: a literal list, a `columns` binding, a list-valued `value` binding (a scalar is a list of one) — not a relation, an empty list, a nested or a filtered one | its elements, joined with `, `, for the template to bracket: `ARRAY[{0}]`, `({0})` |

The application promises that the spelling computes what its function computes;
nothing else can. Run the rules both ways over real data and compare — the
examples do, on every run.

## Extending the map

At run time, an application can register a dialect of its own that extends a
shipped one — for a driver that wants numbered placeholders, a server version with
a missing function, an extension that adds one — and can respell, withdraw or
add a builder for any of SEL's own functions. Only the difference is written;
everything else is inherited.
[Extending SEL](extending.md#extending-the-sql-layer) shows it in all five
languages. A permanent addition to a shipped dialect is a change to
`sql/dialects/*.json`, followed by `node tools/gen-sql-map.mjs`; the format is
[sql/MAP.md](../sql/MAP.md).

## Loading the SQL layer

The SQL layer is opt-in, so an application that only evaluates does not load a
dialect map: `from sel.sql import …` in Python, `sel-lang/sql` in JavaScript,
`php/src/Sql/bootstrap.php` in PHP, `sel_sql.hpp` and `sel_sql*.cpp` in C++, and
the `sel-lang/sql` system in Lisp.
