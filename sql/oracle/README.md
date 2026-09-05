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
`_USER` and `_PASS` beside it. Every **target** dialect is run, not one named by
a flag — a dialect nobody asks a server about is a dialect whose map is a set of
claims. With no DSN a dialect prints a skip and succeeds, so a checkout with no
database stays green; SQLite needs nothing beyond `pdo_sqlite`, so in practice
there is always one server to ask:

```
SEL_SQL_SQLITE_DSN='sqlite::memory:' tools/check-sql-oracle.sh
```

MySQL needs a real server, and MariaDB's `mysql`/`mysqld` binaries are
compatibility symlinks rather than the thing itself — `SELECT VERSION()` on the
local one says `11.8.8-MariaDB`. A container is the usual way to get one:

```
docker run -d --name sel-mysql -e MYSQL_ALLOW_EMPTY_PASSWORD=1 \
  -e MYSQL_DATABASE=sel_oracle -p 13306:3306 mysql:8.4
```

A named schema must exist and must be disposable: the row oracle drops and
recreates its tables every run.

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

Two groups at the end are the exception, and have to be. A `### bindings: {…}`
header sets `value` bindings for what follows, because SEL has **no BIN literal**
— bytes only ever arrive from `TO_UTF8`, `FROM_HEX` or a host — so the branch of
`Emit::literal` that renders a binary literal could not otherwise be reached by
anything at all. `{"bin": "7ac3a9"}` in a corpus binding means those bytes; it is
a convenience of this file format, not part of the bindings API. The evaluator is
handed the same constants under the same names, so both sides are still being
asked the same question.

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

## `coverage.json` — the gate

`tools/check-sql-oracle.sh` also answers a question the other two cannot: *did
anything ask?* Every supported entry of every target dialect must have at least
one oracle expression that reaches it, or a line in `coverage.json` giving a
reason. There is no percentage and no threshold — a threshold is the "broad set
with hidden assumptions" this layer exists not to be.

Two properties make it worth having rather than decorative:

- **Coverage is measured, not declared.** It comes from a trace of `Map::entry()`,
  the one lookup every op, func and skeleton passes through. The `### entry:`
  headers are checked *against* that trace, not trusted as it. A header no
  expression under it reaches is reported; a header naming nothing real is a
  suite error, because `func.UPPER` for `funcs.UPPER` would otherwise be a claim
  nothing checks, which is the quietest way for a coverage gate to lie.
- **A group is an occurrence, not a name.** Two groups may carry the same header,
  and checking them together would let a mislabelled one hide behind an honest
  one. It did, until the check was tested by mislabelling a group and watching it
  pass.

Refusals need no coverage: a refusal has no semantics to check, it is the absence
of them.

This is the part that changes what "done" means for M5. Three more dialect
documents are about to be authored, each a few dozen semantic claims about a
server nobody has probed, and each new entry will arrive needing either an
expression or a written reason.

## The fuzz lane

`tools/fuzz-sql.sh` runs the same comparison over programs nobody wrote, from the
generator the six-host differential already uses.

It is a separate lane from `tools/fuzz.sh` rather than a mode of it, because the
two compare different things. `fuzz.sh` demands that six implementations produce
the same string; that is right for the language and wrong here — a translated
expression may legitimately answer `2.5000` where SEL says `2.5`, may refuse
outright, and may yield a list, which is not a SQL value at all.

Outcomes, and why each is what it is:

| Outcome | Meaning |
|---|---|
| agree | SEL and the server gave the same answer |
| differ | they did not, and the fragment claimed to be exact — a defect |
| caveat | they differ and the fragment said in advance that it might |
| refused | the translator would not translate it; an ordinary outcome |
| sel-error | the program does not evaluate; nothing to compare against |
| out of range | the server rejected the **values** as too large |
| invalid SQL | the server rejected the **syntax** — always a defect |

The last two are the split that matters. SEL's arithmetic is unbounded and
`DECIMAL(65,10)` is not, so a translated expression can be perfectly correct and
still overflow; that is the accepted limit of pushing a rule into a database. A
syntax error is never that. It means the layer emitted something that is not SQL,
which no input may cause.

A run that abstains on nearly everything is not a lane, so the driver fails when
fewer than one program in twenty reaches the server. About a quarter do today;
`--verbose` prints the refusal histogram, which is currently dominated by
`E_SQL_ASSIGN` — the generator's fixed `ITEMS[1]["QTY"] = 2` preamble is two
levels of indexed assignment, which stage 1 documents that it will not fold.

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
