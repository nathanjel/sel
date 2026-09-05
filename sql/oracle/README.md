# The SQL semantic oracle

`sql/cases/*.sqlt` asserts the string the translator emits. This asks a database
whether that string **means** what SEL means.

The distinction is the whole reason this directory exists. A map entry such as

```jsonc
"CHAR": { "tpl": "CHAR({0} USING utf8mb4)", "ret": "TEXT" }
```

is a claim about semantics. A case asserting that `CHAR(233)` translates to
`CHAR(233 USING utf8mb4)` is green whether or not that expression does what SEL's
`CHAR` does — and it does not: MariaDB reads the argument as a byte sequence, so
the answer is `NULL` where SEL says `é`. Above 128 it disagrees, below 128 it
agrees, and nothing in a case file can tell.

See `docs/SQL-TESTING.md` for the analysis this came out of.

## Running it

```
SEL_SQL_MARIADB_DSN='mysql:unix_socket=/var/lib/mysql/mysql.sock;dbname=sel_oracle;charset=utf8mb4' \
SEL_SQL_MARIADB_USER=you \
tools/check-sql-oracle.sh
```

The environment variable is named for the dialect: `SEL_SQL_<DIALECT>_DSN`, with
`_USER` and `_PASS` beside it. With no DSN the check prints a skip and succeeds —
a checkout with no database stays green, the same way one with no C++ toolchain
does. The named schema must exist and must be disposable: the row oracle drops
and recreates its tables every run.

## `expressions.selo`

Closed SEL expressions — literals only, no bindings — one per line, grouped by
the map entry the group exists to cover.

```
### entry: funcs.UPPER
UPPER("abc")
UPPER("zażółć gęślą jaźń")
```

Because they are closed, one corpus serves every dialect. What differs between
dialects is the map, and the map is exactly what is being measured.

Each expression is run **twice**: once with literals inlined and once through a
native prepared statement. Both, because they fail differently. In `inline` mode
a literal is quoted at the position it belongs to, so a fragment whose parameter
pool is in the wrong order still emits the right string and an ordering defect is
invisible. Only `params` mode can show it, and only with `ATTR_EMULATE_PREPARES`
off — emulation inlines client-side, and an emulated run passes whatever the
binding order is.

The rule the runner applies:

> An **exact** translation must agree with the server.
> An **inexact** one — a fragment carrying a caveat — is allowed to differ, and
> said so in advance.

That is the caveat vocabulary earning its place: it is not documentation, it is
the list of expressions this check is not allowed to fail on.

## `rows.json` and `fixture.sql`

The row-parity oracle: each rule is translated into a `WHERE` clause and run
against the fixture, and evaluated by SEL over the same rows loaded as a context.
The two id lists must match.

This is the only oracle that reaches a `relation` binding, because a correlated
subquery needs a query to correlate to.

Two guards, both of which have already caught something:

- **A rule must discriminate.** Selecting every order, or none, is agreement
  about nothing. A rule that does is a failure of the fixture unless it carries a
  `degenerate` key saying why — `sku matches, wrong case` has one, because empty
  *is* the result being checked.
- **`expect` is not the answer.** The answer is whatever SEL says. `expect` is a
  guard that the fixture still makes the rule mean what it meant when it was
  written, and a mismatch reports drift rather than a translation defect.

`fixture.sql` exists because the original did not. The M3 row-parity check ran
against a database made by hand at a shell, three commit messages cite its
result, and the database is gone. See `docs/SQL-TESTING.md` §9.

## What it found on its first two runs

Against code that had been reviewed three times and had a green 180-case suite:

| Defect | Shape |
|---|---|
| `PADL`/`PADR` truncated | `LPAD('7777', 2, '0')` is `'77'`; SEL returns `"7777"` |
| `MIN(1)`/`MAX(1)` emitted invalid SQL | `LEAST` and `GREATEST` need two arguments |
| `params` mode compared numbers as text | `(2.50 = 2.5)` is TRUE, `(? = ?)` over two strings is FALSE |
| `x IN <multi-field relation>` | server said `[1]`, SEL said `[]` — see below |
| the fixture loader loaded nothing | and reported nine disagreements over zero rows |

The fourth is the one worth reading twice, because it was not an implementation
slip: `docs/SQL-TRANSLATION.md` §7.6 used a multi-field relation in its own
worked example. A relation with more than one field is a list of **rows**, and
SEL compares a scalar against a row structurally — FALSE for every row, always.
No SEL context makes the documented translation true. It is refused now, and
`SKUS` in `rows.json` shows the shape that works.
