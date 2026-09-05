# SEL → SQL translation

**Status: design, not yet implemented.** This document is the plan for the SQL
layer. It is written in the same register as `spec/SPEC.md` — where it and a
future implementation disagree, resolve it here first — but it is *not* part of
the language spec. Nothing here changes how a SEL program evaluates. It
describes a second consumer of the same AST.

---

## 1. What this is

SEL today has one back end: `Evaluator`, which walks the AST against a `Value`
context. This adds a second: a **translator** that walks the same AST and emits
a **SQL expression string** for a named dialect, so that a validation rule
authored once can be pushed into the database instead of pulled out of it.

The unit of output is an **expression**, never a statement. What the translator
produces is a fragment you can drop into a `SELECT` list, a `WHERE` clause, a
`HAVING`, a `CHECK`, or a join condition — the same way SEL itself yields a
value rather than a program. Building the statement around it is the host
application's job and stays out of scope (§16).

Four properties are non-negotiable, and they are what the whole design is
arranged around:

1. **The map is data.** The SEL→SQL correspondence is authored once, in one
   language-neutral file per dialect, and every host consumes a mechanically
   generated transcription of it. There is no PHP opinion about what `UPPER`
   becomes that a JS host could contradict.
2. **Refusal is a first-class result.** Most of SEL does not survive the trip.
   `CRC32` has no PostgreSQL spelling, `SPLIT` yields a list, `ABORT` is a
   control-flow effect. Every one of those must fail *cleanly, whole, and
   before any SQL is emitted*, so the caller can fall back to the evaluator.
   Partial output is worse than none.
3. **Output is deterministic.** The same source, dialect and bindings produce
   the same byte string, every host, every run. That is what makes the unit
   tests in §13 a parity check rather than a smoke test.
4. **Variables become schema, not values.** SEL's `dependencies()` tells the
   host which names a rule reads. For SQL the host answers with *where those
   names live* — `o.total`, an alias, a correlated subquery — not with data.

---

## 2. What gets built

```
sql/                              language-neutral, the source of truth
  MAP.md                          normative description of the map format
  errors.md                       the translator's error codes
  dialects/ansi.json              base: what every server agrees on
  dialects/mysql-family.json      base: what MariaDB and MySQL agree on
  dialects/mariadb.json           the reference target
  dialects/mysql.json
  dialects/postgresql.json
  dialects/sqlite.json
  cases/*.sqlt                    translation cases, run by every host

tools/gen-sql-map.mjs             sql/dialects/*.json -> host source
tools/check-sql-map.sh            regenerate, diff, fail if stale

php/src/Sql/MapData.php           generated; committed
php/src/Sql/Map.php               resolution, inheritance, runtime registration
php/src/Sql/Bindings.php          the variable -> schema map
php/src/Sql/Normalise.php         stage 1: assignments and sequences out
php/src/Sql/Lower.php             stage 2: aggregates -> unrolls and subqueries
php/src/Sql/Kinds.php             stage 3: static kind inference
php/src/Sql/Translator.php        stage 4: render
php/src/Sql/Fragment.php          the result object
php/src/Sql/SqlError.php
php/src/Sql/bootstrap.php         opt-in require; the core does not pull this in
php/bin/sqlt                      the .sqlt case runner

python/sel/sql/_map.py            generated; committed
python/sel/sql/{map,bindings,normalise,lower,kinds,translator,fragment,errors}.py
python/bin/sqlt.py
python/tests/sql/                 functional tests against real databases

examples/sql-php.php              the PHP + MariaDB end-to-end proof
tools/impls.sh                    gains impl_sql
```

The generated files **are committed**, unlike `dist/`. They are source as far as
each host is concerned — Packagist and PyPI ship them, and a user who clones
must not need Node to get a working library. Staleness is caught the way the
bundle's is: `tools/check-sql-map.sh` regenerates into a temporary directory and
diffs, and `tools/check.sh` runs it. A generated file in version control goes
stale only if nothing checks it.

`php/src/bootstrap.php` does **not** require `Sql/`. The core is one file to
require with no dependencies and no cost you did not ask for; a host that never
translates should not parse a few thousand lines of dialect tables. SQL users
add one more `require` — and `composer.json` gains a second `autoload.files`
entry for it, since a Composer install has no sensible way to reach into
`vendor/` by path and the core is loaded exactly that way already.

---

## 3. The pipeline

```
source
  |  Sel::compile                       (unchanged)
  v
AST  ---------------------------------------------------------------.
  |                                                                  |
  |  Program::dependencies()  ->  names the host must bind           |
  |                                                                  |
  v                                                                  |
[1] Normalise    assignments inlined, sequences collapsed,           |
                 non-expression shapes refused                       |
  v                                                                  |
[2] Lower        aggregates -> static unrolls / EXISTS / scalar      |
                 subqueries; FILTER absorbed into ALL/ANY            |
  v                                                                  |
[3] Kinds        every node gets NUM | TEXT | BOOL | BIN | UNKNOWN   |
  v                                                                  |
[4] Render       map lookup per node; template fill; quoting         |
  v                                                                  |
Fragment { sql, kind, dialect }  --- or --- SqlError  <--------------'
```

Stages 1–3 are AST→AST. Only stage 4 produces characters. This ordering is what
makes whole-expression refusal cheap: a rule that cannot be translated fails in
stage 1, 2 or 3, before a single character has been written, so there is no
half-built string to discard and no partially-applied side effect.

Each stage is written once, in prose here and in code per host, and transcribed
between hosts the way `Parser` and `Evaluator` already are. The *data* is
shared; the *walk* is transcribed. That split is deliberate: a walk expressed as
data would be an interpreter, and we already have one.

---

## 4. The dialect map

### 4.1 Source of truth

One JSON document per dialect under `sql/dialects/`. JSON rather than the
line-oriented format `conformance/` uses, because unlike `.selt` this file is
read only by hosts that have a JSON parser in the standard library — which is
every host the SQL layer will ever have — and because the entries are nested
records rather than flat text blocks.

`tools/gen-sql-map.mjs` reads all of them, validates them against the rules
below, and emits one file per host. Validation at generation time is the point:
an unknown `ret`, a template referring to `{3}` in a two-argument function, a
dialect extending a dialect that does not exist — all of it is caught by the
generator, so no host ever has to defend against a malformed map.

### 4.2 The dialect document

```jsonc
{
  "dialect": "mysql",
  "extends": "ansi",
  "version": "8.0.4",          // the minimum this document assumes
  "lexical": { ... },          // §4.3
  "target":  true,             // may this be named in a translate() call?
  "ops":     { ... },          // keyed by SEL operator token
  "funcs":   { ... },          // keyed by SEL function name, upper case
  "skel":    { ... },          // multi-part constructs: CASE and the subqueries
  "notes":   { ... }           // prose, consumed by nobody
}
```

`extends` forms a chain, resolved at lookup:

```
ansi ─┬─ mysql-family ─┬─ mariadb        <- the reference
      │                └─ mysql
      ├─ postgresql
      └─ sqlite
```

**`ansi` and `mysql-family` are bases, not targets.** Neither is a dialect any
server implements, so neither may be named in a `translate()` call —
`E_SQL_DIALECT`. Offering `ansi` would invite someone to target a database that
does not exist and be surprised by which of their rules survived.

`mysql-family` exists because MariaDB and MySQL share almost every spelling and
disagree on a handful that matter: MySQL 8.0.4+ has `REGEXP_LIKE`, MariaDB has
`RLIKE`/`REGEXP` and `REGEXP_REPLACE`; MySQL's `CRC32` returns an integer that
must be padded, MariaDB's does too but its argument handling differs by version.
Putting the agreement in a shared base and the disagreements in two leaves is
better than making one of them a subclass of the other, because **MariaDB is the
reference** — it is what the end-to-end proof runs against — and a reference
dialect should not inherit from a dialect nobody has tested yet.

Chains are acyclic and at most eight deep; the generator enforces both, and it
also enforces that every leaf dialect is reachable from `ansi`.

A dialect may be **versioned**: `sql/dialects/mariadb-10.6.json` declares
`{"dialect": "mariadb-10.6", "extends": "mariadb", "version": "10.6"}` and
withdraws the entries 10.6 lacks by mapping them to `null`. That is the mechanism for
"keep maps for different versions" — a version is just another dialect in the
chain, which means it needs no special code anywhere.

### 4.3 The lexical block

Everything about the dialect that is not a per-operator template. Inherited
key by key.

```jsonc
"lexical": {
  "identQuote":     "`",              // "\"" for pg/sqlite/ansi
  "identEscape":    "``",             // how the quote character escapes itself
  "textQuote":      "'",
  "textEscape":     { "'": "''", "\\": "\\\\" },
  "true":           "TRUE",
  "false":          "FALSE",
  "binaryLiteral":  "X'{hex}'",       // BIN literals; null = refuse
  "textCollate":    " COLLATE utf8mb4_bin",   // appended for $-family compares
  "numericCast":    "CAST({0} AS DECIMAL(38,10))",
  "isTrue":     "({0}) IS TRUE",
  "isNotTrue":  "({0}) IS NOT TRUE",
  "placeholder":  "?"          // "${n}" for PostgreSQL; see §9
}
```

`textEscape` carries the backslash entry for MySQL and MariaDB and omits it for
PostgreSQL and SQLite, because MySQL treats `\` as an escape inside string
literals under its default `sql_mode` and the other two do not. Getting this
wrong is a SQL injection, not a formatting nit, so it is data rather than a
branch someone can forget to write.

`textCollate` is why `$==` can be honest. SEL's `$` family compares bytes;
MySQL's default collation is case- and accent-insensitive, so a bare `=` would
make `"A" $== "a"` true in the database and false in SEL. PostgreSQL gets
`" COLLATE \"C\""`, SQLite gets `""` because BINARY is already its default.

`numericCast` is why `==` can be honest. SEL's `==` compares numerically after
aligning scale, so `"5.00" == "5"` is `TRUE`; SQL's `=` between two text columns
compares text. §9 says exactly when the cast is applied.

### 4.4 Entries

**`sql/MAP.md` is normative for everything in this section.** What follows is the
shape and the reasoning; the rules the generator actually enforces, and the
closed vocabularies, live there so that two documents cannot drift.

An entry is one of three things:

**An object — supported.** See below.

**A string — refused, and the string is the reason.** This is the form to
prefer, because it becomes the message on the `E_SQL_UNSUPPORTED` a caller sees:

```json
"CRC32": "PostgreSQL has no built-in CRC-32; pgcrypto's digest() offers other algorithms, not this one"
```

A reason written once, next to the decision it explains, reaches every host and
every caller. That is what makes §10's single error class sufficient.

**`null` — refused with no reason given.** A refusal in a child dialect
withdraws an entry the parent provided; *absent* means "not mentioned here, ask
the parent".

**A template entry.**

```jsonc
"UPPER":   { "tpl": "UPPER({0})",             "ret": "TEXT" },
"LEN":     { "tpl": "CHAR_LENGTH({0})",       "ret": "NUM"  },
"FIND":    { "tpl": "INSTR({1}, {0})",        "ret": "NUM", "arity": [2, 2] },
"REPLACE": { "tpl": "REPLACE({2}, {0}, {1})", "ret": "TEXT" },
"MIN":     { "tpl": "LEAST({*})",             "ret": "NUM"  },
"TO_HEX":  { "tpl": "LOWER(HEX({0}))",        "ret": "TEXT" }
```

Placeholders:

| Form | Means |
|---|---|
| `{n}` | argument *n*, zero-based, already rendered |
| `{*}` | every argument, joined with `, ` |
| `{n:}` | arguments *n* onward, joined with `, ` |
| `{{` `}}` | a literal brace |

Three of those entries are worth reading twice. `FIND` reorders its arguments,
because SEL is `FIND(needle, hay)` and SQL is `INSTR(hay, needle)`; a map that
could not reorder would need a wrapper function in every host. `REPLACE`
reorders three. `TO_HEX` wraps, because MySQL's `HEX` returns upper case and
§7.7 of the spec says lower. None of these is a special case in any host — they
are the same template mechanism doing its job.

`arity` narrows the SEL arity for this dialect. `FIND` takes an optional third
argument in SEL; PostgreSQL's `POSITION` has no "search from" parameter, so
`postgresql.json` declares `"arity": [2, 2]` and the three-argument form is
refused there while still working on MySQL. That is graceful degradation
expressed as data.

**A variant entry**, for operators whose spelling depends on the operand kinds:

```jsonc
"&":  { "variants": { "text": "CONCAT({0}, {1})",
                      "bin":  "CONCAT({0}, {1})" }, "ret": "@concat" },
"==": { "variants": { "num":    "({0} = {1})",
                      "coerce": "({numericCast:0} = {numericCast:1})" },
        "ret": "BOOL" },
"$<": { "variants": { "text": "({0}{textCollate} < {1}{textCollate})" },
        "ret": "BOOL" }
```

`{lexicalKey:n}` interpolates a lexical template around argument *n*, and
`{lexicalKey}` splices a lexical string. It keeps the cast and collation
spellings in one place rather than repeated across twelve comparison entries.

**The generator expands these**, which is why §4.6's PHP shows `CAST({0} AS
DECIMAL(38,10))` written out where the JSON said `{numericCast:0}`. The hosts
still carry the expansion code, because entries registered at runtime (§4.7)
never pass through the generator and an application writing a template deserves
the same vocabulary the shipped map has.

Which variant is chosen is **not** in the data — it is one of four fixed
selectors, named by the operator family and implemented identically in every
host:

| Family | Selector |
|---|---|
| `== != < <= > >=` | `num` when both operands infer NUM, else `coerce` |
| `$== $!= $< $<= $> $>=`, `EQL` on scalars | always `text` |
| `&` | `bin` when either operand infers BIN, else `text` |
| everything else | no variants |

**`ret`** is the static kind the node produces: `NUM`, `TEXT`, `BOOL`, `BIN`,
`UNKNOWN`, or one of two computed forms — `@concat` (BIN if any argument is BIN,
else TEXT) and `@unify:i,j` (the common kind of those arguments, UNKNOWN if they
disagree). `ret` is what §8 consumes.

**`caveat`** marks an entry that is mapped but does not match SEL exactly:

```jsonc
"UPPER": { "tpl": "UPPER({0})", "ret": "TEXT", "caveat": "unicode-case" },
"/":     { "tpl": "({0} / {1})", "ret": "NUM", "caveat": "division-scale" },
"ROUND": { "tpl": "ROUND({0}, {1})", "ret": "NUM", "caveat": "rounding-mode" }
```

Caveats are advisory by default and fatal under `strict`: `translate(…, ['strict' => true])`
turns every caveated entry into `E_SQL_UNSUPPORTED`. That gives an application
one switch between "best effort, and I know what I signed up for" — the mode
this project is being built for — and "only translate what is exactly
equivalent". The caveat vocabulary is closed and documented in `sql/MAP.md`;
§11 lists it.

**`since`** gates an entry on the dialect version:

```jsonc
"RMATCH": { "tpl": "REGEXP_LIKE({1}, {0})", "ret": "BOOL", "since": "8.0.4" }
```

Below that version, `E_SQL_DIALECT`. Version comparison is dotted-numeric and
nothing cleverer; the generator rejects a `since` that is not.

### 4.5 Resolution

```
lookup(dialect, section, key):
    for d in chain(dialect):            # self first, then extends, ...
        if key present in d[section]:
            return d[section][key]      # possibly null -> unsupported
    return MISSING                      # -> E_SQL_UNSUPPORTED
```

Resolution is per key, not per section, so `mariadb.json` overriding `RMATCH`
does not have to restate the other fifty functions `mysql-family` already gave
it. The generator **flattens the
chain at generation time** and emits fully resolved tables per dialect, so the
host does no chain walking at all — that is a lookup in a hash and nothing else.
Runtime registration (§4.7) is what re-introduces the chain, and it is the only
thing that does.

### 4.6 Generated host forms

PHP — a plain nested array, because that is what PHP loads fastest and what
reads most like the JSON it came from:

```php
<?php
// GENERATED by tools/gen-sql-map.mjs from sql/dialects/*.json — do not edit.
// Regenerate with: node tools/gen-sql-map.mjs
declare(strict_types=1);
namespace Sel\Sql;

final class MapData
{
    /** @var array<string, array<string, mixed>> */
    public const DIALECTS = [
        'mariadb' => [
            'extends' => 'mysql-family',
            'version' => '10.5',
            'lexical' => [
                'identQuote' => '`',
                'identEscape' => '``',
                'textQuote' => "'",
                'textEscape' => ["'" => "''", '\\' => '\\\\'],
                'true' => 'TRUE',
                'false' => 'FALSE',
                'textCollate' => ' COLLATE utf8mb4_bin',
                'numericCast' => 'CAST({0} AS DECIMAL(38,10))',
                'isTrue' => '({0}) IS TRUE',
                'isNotTrue' => '({0}) IS NOT TRUE',
            ],
            'ops' => [
                '+'  => ['tpl' => '({0} + {1})', 'ret' => 'NUM'],
                '/'  => ['tpl' => '({0} / {1})', 'ret' => 'NUM',
                         'caveat' => 'division-scale'],
                '&'  => ['variants' => ['text' => 'CONCAT({0}, {1})',
                                        'bin'  => 'CONCAT({0}, {1})'],
                         'ret' => '@concat'],
                '==' => ['variants' => ['num' => '({0} = {1})',
                                        'coerce' => '(CAST({0} AS DECIMAL(38,10))'
                                                  . ' = CAST({1} AS DECIMAL(38,10)))'],
                         'ret' => 'BOOL'],
                'BAND' => null,          // no portable byte-string bitwise
                // ...
            ],
            'funcs' => [
                'UPPER'  => ['tpl' => 'UPPER({0})', 'ret' => 'TEXT',
                             'caveat' => 'unicode-case'],
                'TRIM'   => ['tpl' => "REGEXP_REPLACE({0}, '^[ \\t\\r\\n]+"
                                    . "|[ \\t\\r\\n]+$', '')", 'ret' => 'TEXT',
                             'since' => '8.0.4'],
                'CRC32'  => ['tpl' => 'LPAD(LOWER(HEX(CRC32({0}))), 8, \'0\')',
                             'ret' => 'TEXT'],
                'SPLIT'  => null,        // yields a list
                'ABORT'  => null,        // control flow, not a value
                // ...
            ],
            'skel' => [
                'all' => ['tpl' => 'NOT EXISTS (SELECT 1 FROM {from} WHERE {corr}'
                                 . ' AND ({body}) IS NOT TRUE)'],
                'any' => ['tpl' => 'EXISTS (SELECT 1 FROM {from} WHERE {corr}'
                                 . ' AND ({body}) IS TRUE)'],
                'sum' => ['tpl' => '(SELECT COALESCE(SUM({body}), 0) FROM {from}'
                                 . ' WHERE {corr})'],
                'count' => ['tpl' => '(SELECT COUNT(*) FROM {from} WHERE {corr})'],
                'inRelation' => ['tpl' => '({needle} IN (SELECT {body} FROM {from}'
                                        . ' WHERE {corr}))'],
                // A refusal is a string, and the string is the reason. Uniform
                // with ops and funcs, and the reason it is not a bare-string
                // template: a refusal is a string too.
                'join' => 'GROUP_CONCAT does not specify an order without an'
                        . ' ORDER BY, and a relation binding has no key to'
                        . ' order by; SEL JOIN concatenates in insertion order',
            ],
        ],
        // 'mysql' => [...], 'postgresql' => [...], 'sqlite' => [...],
        // and the two bases, 'ansi' and 'mysql-family', which are not targets.
    ];
}
```

Python — the same shape, one module-level dict of `TypedDict`-ish plain dicts,
so the diff between the two generated files is mechanical and reviewable:

```python
# GENERATED by tools/gen-sql-map.mjs from sql/dialects/*.json — do not edit.
DIALECTS: dict[str, dict] = {
    'mariadb': {
        'extends': 'mysql-family',
        'version': '10.5',
        'lexical': {
            'identQuote': '`', 'identEscape': '``',
            'textQuote': "'", 'textEscape': {"'": "''", '\\': '\\\\'},
            'true': 'TRUE', 'false': 'FALSE',
            'textCollate': ' COLLATE utf8mb4_bin',
            'numericCast': 'CAST({0} AS DECIMAL(38,10))',
            'isTrue': '({0}) IS TRUE',
            'isNotTrue': '({0}) IS NOT TRUE',
        },
        'ops': {
            '+': {'tpl': '({0} + {1})', 'ret': 'NUM'},
            '&': {'variants': {'text': 'CONCAT({0}, {1})',
                               'bin': 'CONCAT({0}, {1})'}, 'ret': '@concat'},
            'BAND': None,
        },
        'funcs': {
            'UPPER': {'tpl': 'UPPER({0})', 'ret': 'TEXT', 'caveat': 'unicode-case'},
            'SPLIT': None,
        },
        'skel': { ... },
    },
}
```

### 4.7 Runtime registration

Mirrors `Registry::define` in shape and in spirit, because an application that
can add a SEL function must be able to add its SQL spelling in the same breath.

```php
use Sel\Sql\Map;

// A whole dialect. `extends` may name a generated dialect or another registered one.
Map::defineDialect('mysql-5.7', [
    'extends' => 'mysql',
    'version' => '5.7',
]);

// One entry. Same record shape as the JSON.
Map::define('mysql', 'func', 'GEO_NEAR', [
    'tpl' => 'ST_Distance_Sphere({0}, {1}) < {2}',
    'ret' => 'BOOL',
]);

// Withdraw one, for a deployment where it is unavailable.
Map::define('mysql-5.7', 'func', 'RMATCH', null);

// The escape hatch, for what a template cannot say. A builder receives the
// rendered arguments and the emitter, and returns a Fragment. This is the SQL
// layer's equivalent of Registry's `fn`.
Map::defineBuilder('postgresql', 'func', 'JSON_AT', function (Emit $e, array $args): Fragment {
    return $e->frag($args[0]->sql . ' -> ' . $e->textLiteral($args[1]->constText()), 'TEXT');
});
```

Two differences from `Registry::define`, both deliberate:

- **Redefinition is allowed, last writer wins.** `Registry` throws on a duplicate
  because a duplicate SEL function is always a bug. A duplicate SQL entry is
  usually an application deliberately overriding a shipped default for its own
  schema or server build, which is exactly the extension point asked for.
- **Registration is per dialect and chains at runtime.** The generated tables are
  pre-flattened (§4.5); runtime entries sit in an overlay consulted first, and
  the overlay *does* walk `extends`. So registering against `ansi` reaches every
  dialect, and registering against `mysql` reaches `mariadb`.

Python is the same three calls, snake-cased: `map.define_dialect`,
`map.define`, `map.define_builder`.

---

## 5. Bindings

The host answers `dependencies()` with a **bindings map**: upper-case SEL name →
a record saying where that name lives in the schema. Any name still read after
stage 1 that has no binding is `E_SQL_UNBOUND`. Nothing is inferred; a rule that
reads `TOTAL` against a schema that never heard of it must fail loudly, not
guess a column.

Four kinds.

### 5.1 `column`

```php
'TOTAL' => ['kind' => 'column', 'table' => 'o', 'column' => 'total', 'type' => 'NUM'],
'NAME'  => ['kind' => 'column', 'column' => 'customer_name', 'type' => 'TEXT'],
'NOW'   => ['kind' => 'column', 'raw' => 'CURRENT_TIMESTAMP', 'type' => 'UNKNOWN'],
```

`table` is the table name or alias and is optional. `type` seeds kind inference
(§8) and defaults to `UNKNOWN`. `raw` bypasses quoting entirely and is the
escape hatch for expressions the binding needs to be — it is the one place the
caller can inject arbitrary SQL, so it is spelled distinctly and documented as
such. Everything without `raw` is quoted with `identQuote`, with the quote
character escaped per `identEscape`.

`TOTAL > 100` against the first binding renders, on MySQL:

```sql
(`o`.`total` > 100)
```

### 5.2 `columns`

An ordered set of column references treated as a list. This is the binding that
makes the user's motivating example work.

```php
'VALUES' => ['kind' => 'columns', 'items' => [
    ['table' => 'x', 'column' => 'a', 'type' => 'NUM'],
    ['table' => 'x', 'column' => 'b', 'type' => 'NUM'],
    ['table' => 'x', 'column' => 'c', 'type' => 'NUM'],
]],
```

`ALL(VALUES, V, V > 0)` unrolls over the items:

```sql
((`x`.`a` > 0) AND (`x`.`b` > 0) AND (`x`.`c` > 0))
```

which, dropped into a `WHERE`, is the `select a, b, c from x where a > 0 AND b > 0
AND c > 0` the brief asks for. No subquery is involved and none is needed —
the "list" is a set of columns on the row already in scope, so iteration is
unrolling, not a join.

Indexing works positionally: `VALUES[2]` is `` `x`.`b` `` (keys are `"1"`, `"2"`,
… exactly as `,` builds them). `COUNT(VALUES)` folds to `3` at translation time.
`INDEXES(VALUES)` is still refused — it yields a list.

### 5.3 `relation`

A correlated set of rows. This is what becomes a subquery.

```php
'ITEMS' => [
    'kind'      => 'relation',
    'from'      => 'order_items',
    'alias'     => 'oi',
    'correlate' => ['raw' => 'oi.order_id = o.id'],
    'fields'    => [
        'QTY'   => ['column' => 'qty',   'type' => 'NUM'],
        'PRICE' => ['column' => 'price', 'type' => 'NUM'],
    ],
    'scalar'    => 'QTY',          // what a bare binder reference means
],
```

- `from` may instead be `['raw' => '(SELECT a, b FROM t WHERE …)']`, which is how
  a binding "carries a query".
- `correlate` is the join back to the outer row. Omit it for an uncorrelated set.
- `fields` names what `BINDER["qty"]` resolves to. A key that is not in `fields`
  is `E_SQL_BINDING` — again, no guessing.
- `scalar` says what a bare `BINDER` means, so `ALL(ITEMS, _ > 0)` has something
  to compare. Absent, a bare reference is `E_SQL_SHAPE`.

`ALL(ITEMS, I, I["qty"] > 0)` on MySQL:

```sql
NOT EXISTS (SELECT 1 FROM `order_items` `oi` WHERE oi.order_id = o.id
            AND ((`oi`.`qty` > 0)) IS NOT TRUE)
```

### 5.4 `value`

A constant supplied at translation time, inlined as a literal. Takes a SEL
`Value`, so a list becomes a list and unrolls exactly like a literal list node.

```php
'LIMITS' => ['kind' => 'value', 'value' => Value::fromNative([10, 20, 30])],
```

This is how a host passes a runtime parameter today, before the parameterised
output mode of §16 exists.

### 5.5 Choosing between `columns` and `relation`

The same SEL source translates two completely different ways depending on which
binding kind the host supplies, and the choice is the host's because only the
host knows the schema. Both are shown here in full because the difference is
the single most consequential thing a caller decides.

**Same rule, both times:**

```
ALL(VALUES, V, V > 0)
```

**(a) `columns` — the values are three columns of the row already in scope.**

```php
'VALUES' => ['kind' => 'columns', 'items' => [
    ['table' => 'x', 'column' => 'a', 'type' => 'NUM'],
    ['table' => 'x', 'column' => 'b', 'type' => 'NUM'],
    ['table' => 'x', 'column' => 'c', 'type' => 'NUM'],
]],
```

```sql
-- Fragment::asCondition()
((`x`.`a` > 0) AND (`x`.`b` > 0) AND (`x`.`c` > 0))

-- in a statement the application builds
SELECT a, b, c FROM x
 WHERE ((`x`.`a` > 0) AND (`x`.`b` > 0) AND (`x`.`c` > 0))
```

No subquery, no join, no correlation. The iteration happened at translation
time and left nothing behind. **This is the default** when a host has the
choice: it is the cheapest thing the database can be asked to do, it plans like
hand-written SQL, and it is what the motivating example wanted.

**(b) `relation` — the values are rows of another table.**

```php
'VALUES' => [
    'kind'      => 'relation',
    'from'      => 'order_items',
    'alias'     => 'oi',
    'correlate' => ['raw' => '`oi`.`order_id` = `o`.`id`'],
    'scalar'    => 'AMOUNT',
    'fields'    => ['AMOUNT' => ['column' => 'amount', 'type' => 'NUM']],
],
```

```sql
-- Fragment::asCondition()
NOT EXISTS (SELECT 1 FROM `order_items` `oi`
             WHERE `oi`.`order_id` = `o`.`id`
               AND ((`oi`.`amount` > 0)) IS NOT TRUE)

-- in a statement the application builds
SELECT o.id FROM orders o
 WHERE NOT EXISTS (SELECT 1 FROM `order_items` `oi`
                    WHERE `oi`.`order_id` = `o`.`id`
                      AND ((`oi`.`amount` > 0)) IS NOT TRUE)
```

Here the element count is not known until the query runs, so the iteration has
to survive into the SQL. `NOT EXISTS … IS NOT TRUE` is the shape that gets the
empty case right (`ALL` over no rows is `TRUE`, matching SPEC §7.3) and the NULL
case right (a NULL body makes `ALL` false rather than vacuously true).

**And the third, for completeness — a literal list needs no binding at all:**

```
ALL((1, 2, 3), _ > 0)     ->     ((1 > 0) AND (2 > 0) AND (3 > 0))
```

The rule that decides between them is mechanical and lives in stage 2: look at
what the first argument resolves to, and take the matching path (§7.1, §7.2,
§7.3). Nothing guesses.

---

## 6. Stage 1 — normalisation

SEL is an expression language, but it has assignment and `;`, and SQL has
neither. Stage 1 is the restrictive step the brief asks for: it turns a small,
well-behaved class of programs into a single expression and refuses the rest.

The rules, in order:

1. **Flatten the top-level `seq`.** `a; b; c` becomes a list of statements with
   `c` as the result expression. A `seq` anywhere *other* than the top level
   (inside a call argument, inside an aggregate body) is `E_SQL_ASSIGN`.
2. **Collect assignments.** Every statement but the last must be an `assign`.
   A non-assignment in a non-final position computes a value nobody reads; in
   SEL that is legal and useless, in SQL it is unrepresentable. `E_SQL_ASSIGN`.
3. **Refuse what cannot be inlined.**
   - a compound assignment (`+=`, `&=`, …) — it reads its own target, so
     inlining it duplicates the read and changes nothing about the fact that
     there is no place to put the write;
   - a variable assigned more than once;
   - a variable read before its assignment;
   - any assignment inside an aggregate body or a call argument;
   - an assignment whose target is indexed by a non-constant expression.
4. **Inline.** Substitute each assignment's right-hand side for every read of
   its target in the statements that follow. Substitution is by AST node, and
   the inlined subtree keeps its original `pos` so an error inside it still
   points at where the author wrote it.
5. **Fold indexed assignment into a keyed-list node.** `R[1] = (1, 2);
   R[2] = (3, 4)` with constant, disjoint keys and no intervening read of `R`
   collapses into one synthetic node:

   ```
   ['t' => 'clist', 'entries' => [['1', <list 1,2>], ['2', <list 3,4>]], 'pos' => …]
   ```

   `clist` is a **distinct node kind, not a `list` node**, and the difference is
   load-bearing twice over. First, rule 6 flattens `list` and never touches
   `clist`, so the structure this rule just built survives — writing it as SEL
   source would not: `((1, 2), (3, 4))` is `(1, 2, 3, 4)` per SPEC §5.9, and the
   inner aggregate would see four scalars instead of two pairs. Assignment is
   what nests here, because it stores a list *as a child* rather than
   contributing its children, and only a node kind that does the same can
   represent it. Second, `clist` carries the **actual keys**, so
   `R["a"] = 1; R["b"] = 2` gives `_K` of `"a"` and `"b"`; a renumbered `list`
   node could only ever offer `"1"` and `"2"`.

   So

   ```
   R[1] = (1, 2); R[2] = (3, 4); ALL(R, ROW, ALL(ROW, _ > 0))
   ```

   reaches stage 2 as an `ALL` over a two-entry `clist`, which unrolls twice,
   statically, to

   ```sql
   ((1 > 0) AND (2 > 0) AND (3 > 0) AND (4 > 0))
   ```

   Stage 2 treats `clist` and `list` identically as iteration sources (§7.1);
   the only thing that distinguishes them is that rule 6 leaves one alone.

6. **Static list construction.** `list` nodes — and only `list` nodes — are
   flattened per SPEC §5.9: an operand that is a list node with no scalar
   contributes its children, and keys are renumbered from 1, so that stage 2
   sees the same list shape the evaluator would have built.

What survives stage 1 is one expression tree with no `assign` and no `seq`.
Anything else has already been refused, with a code and a position.

```php
// php/src/Sql/Normalise.php — the shape of it
final class Normalise
{
    /** @param array<string,mixed> $ast @return array<string,mixed> */
    public static function run(array $ast): array
    {
        $stmts = $ast['t'] === 'seq' ? $ast['items'] : [$ast];
        $result = array_pop($stmts);
        $defs = [];                       // name => node

        foreach ($stmts as $s) {
            if ($s['t'] !== 'assign') {
                throw SqlError::at('E_SQL_ASSIGN',
                    'only assignments may precede the result expression', $s['pos']);
            }
            if ($s['op'] !== '=') {
                throw SqlError::at('E_SQL_ASSIGN',
                    "compound assignment {$s['op']} cannot be translated", $s['pos']);
            }
            self::record($defs, $s);      // enforces rules 3 and 5
        }

        return self::substitute($result, $defs);
    }
}
```

---

## 7. Stage 2 — lowering the aggregates

Iteration is the interesting half of this whole exercise, and it has exactly
three shapes. Which one applies is decided by what the aggregate's first
argument *is* after stage 1 — not by what it might evaluate to.

### 7.1 Static list → unroll

The first argument is a `list` node, or a `var` bound to `kind: value` holding a
list, or an index into one.

```
ALL(list, body)  ->  ((body[e1]) AND (body[e2]) AND …)      empty -> TRUE
ANY(list, body)  ->  ((body[e1]) OR  (body[e2]) OR  …)      empty -> FALSE
SUM(list, body)  ->  ((body[e1]) + (body[e2]) + …)          empty -> 0
MAP / FILTER     ->  a list of fragments; legal only where a list is legal,
                     which today means as another aggregate's first argument
JOIN(list, sep)  ->  CONCAT(e1, sep, e2, sep, …) per the dialect's concat
COUNT(list)      ->  the count, folded to a literal
```

`body[e]` is the body rendered with the binder bound to that element's node and
`_K` bound to a TEXT literal of its key.

**An unroll folds n-ary; a written-out chain folds binary.** `ALL((a, b, c), …)`
emits one `(x AND y AND z)`, because the fold has all its parts at once and
joining them flat is both shorter and unambiguous. A hand-written `a AND b AND c`
parses as two `bin` nodes and renders through the `AND` template twice, giving
`((a AND b) AND c)`. The two spellings mean the same thing and are not expected
to produce the same string; the difference is which code path built them, and it
is worth knowing before someone reads it as a bug. Because binding is per element and the
substitution is structural, nesting falls out for free — the inner `ALL` in the
example above simply sees a `list` node as *its* first argument and unrolls
again.

Note that SEL's short-circuit guarantee (`FALSE AND (1/0)` is `FALSE`) does not
survive: SQL's `AND` may evaluate either side. §11 lists this.

### 7.2 `columns` binding → unroll over columns

Identical to §7.1 except the elements are column references rather than literal
nodes, and `_K` is the ordinal as text. This is the case that produces the
`where a > 0 AND b > 0 AND c > 0` shape.

### 7.3 `relation` binding → subquery

The map's `skel` section supplies the skeleton (§4.6) and the lowering fills it:

| SEL | Skeleton |
|---|---|
| `ALL(rel, b)` | `all` — `NOT EXISTS (… WHERE corr AND (b) IS NOT TRUE)` |
| `ANY(rel, b)` | `any` — `EXISTS (… WHERE corr AND (b) IS TRUE)` |
| `SUM(rel, b)` | `sum` — `(SELECT COALESCE(SUM(b), 0) FROM … WHERE corr)` |
| `COUNT(rel)` | `count` |
| `JOIN(rel, s)` | `join` — `GROUP_CONCAT` / `STRING_AGG` per dialect |
| `x IN rel` | `in` — `(x IN (SELECT scalar FROM … WHERE corr))` |
| `MAP`, `FILTER`, `INDEXES` over a relation | `E_SQL_SHAPE` — they yield lists |

`IN` is the odd one in that table: it is a `bin` node, not a `call`, so its
lowering lives on the operator path rather than the aggregate one, and `{body}`
in the `in` skeleton is filled from the relation's `scalar` field rather than
from a body argument there is no room to write.

`{corr}` fills with the binding's `correlate`, or with the dialect's `true`
literal when the binding omits it — an uncorrelated relation is a subquery over
the whole table, which is legal and occasionally what you want.

`IS NOT TRUE` rather than `NOT (…)` is the point of care here. SQL is
three-valued and SEL is not: if the body is NULL for some row, `NOT (body)` is
NULL, the `WHERE` rejects the row, and `NOT EXISTS` reports "all rows satisfy
it" — silently the wrong answer for exactly the case a validation rule exists to
catch. `IS NOT TRUE` folds NULL into false, so a NULL body makes `ALL` false,
which is the conservative reading. All three target databases support
`IS [NOT] TRUE` at the versions in scope.

`_K` inside a relation body is `E_SQL_SHAPE`. A row has no portable key, and
inventing one (`ROW_NUMBER()`, the primary key) would be a guess about the
schema this layer is careful never to make.

Nested relations work: an inner `ALL` over a second relation binding produces a
nested `EXISTS` correlated to the inner alias, provided the inner binding's
`correlate` names it. Alias collision between two relations in one expression is
`E_SQL_BINDING` — the host chose the aliases, so the host can fix them.

### 7.4 One rewrite worth doing

```
ALL(FILTER(L, p), q)   ->   ALL(L, (NOT (p)) OR (q))
ANY(FILTER(L, p), q)   ->   ANY(L, (p) AND (q))
SUM(FILTER(L, p), q)   ->   SUM(L, CASE WHEN (p) THEN (q) ELSE 0 END)
COUNT(FILTER(L, p))    ->   SUM(L, CASE WHEN (p) THEN 1 ELSE 0 END)
```

`FILTER` yields a list, so it is otherwise unusable — but "all the ones that
match also satisfy" is one of the most common shapes a real validation rule
takes. These four rewrites cost a few dozen lines and turn `FILTER` from
"always refused" into "usable wherever it was going to be consumed anyway". They
are applied before the shape dispatch above, so the rewritten form then takes
whichever of the three paths its list argument calls for.

```php
// php/src/Sql/Lower.php — the dispatch, condensed
private function aggregate(array $node, Env $env): array
{
    [$binder, $body] = self::shape($node);
    $src = $this->resolveSource($node['args'][0], $env);   // list | columns | relation

    switch ($src['shape']) {
        case 'list':
        case 'columns':
            $parts = [];
            foreach ($src['elements'] as $key => $elem) {
                $parts[] = $this->lower($body, $env->bind($binder, $elem, (string) $key));
            }
            return $this->fold($node['name'], $parts, $node['pos']);

        case 'relation':
            $inner = $env->bindRelation($binder, $src);
            $skel  = $this->map->agg(self::SKELETON[$node['name']] ?? null, $node['pos']);
            return $this->fillSkeleton($skel, $src, $this->lower($body, $inner));
    }
}
```

---

## 8. Stage 3 — kind inference

A small, monotone pass that labels every node `NUM`, `TEXT`, `BOOL`, `BIN`,
`LIST` or `UNKNOWN`. It exists for three jobs and no others:

1. choosing the variant for `==` and `&` (§4.4);
2. refusing a `LIST` in scalar position — `SPLIT`, `INDEXES`, `MAP`, `FILTER`,
   `RGROUPS`, `BTL` and a bare list node, anywhere their value would have to be
   a single SQL scalar;
3. checking that `AND`, `OR`, `NOT`, `XOR` and the aggregate bodies got `BOOL`,
   and that `CASE WHEN` gets a condition — the same checks the evaluator makes
   at run time, made here at translation time so the failure is a clean refusal
   rather than a database type error.

Rules: literals give their own kind; a `var` gives its binding's declared `type`;
an `index` into a `columns`/`relation` binding gives the field's `type`;
everything else gives its map entry's `ret`. `UNKNOWN` is not an error — it means
"ask the database", which for an untyped binding is the honest answer. `UNKNOWN`
where `BOOL` is required is allowed and rendered through `isTrue`; `UNKNOWN`
in a numeric comparison selects the `coerce` variant.

---

## 9. Stage 4 — rendering

A straight post-order walk producing `Fragment { sql, kind }`.

**Parenthesisation is in the templates, not in a precedence table.** Every
binary template wraps itself in `( )`. The output is noisier than a human would
write and it is *unconditionally correct* and byte-stable, which is what §13's
tests need. A precedence-aware emitter is a plausible later refinement and is
not worth the risk now.

**Literals.**

- Number: emitted verbatim in SEL's canonical form (§4.1 of the spec), so `2.50`
  stays `2.50` and keeps its scale. Never reformatted, never floated.
- Text: quoted with `textQuote`, every character in `textEscape` replaced. A
  code point outside the printable ASCII range is passed through as UTF-8 — the
  connection charset is the host's problem and not something to escape blindly.
- Bool: `lexical.true` / `lexical.false`.
- Bin: `binaryLiteral`, or refused where the dialect has none.

**Identifiers** are quoted with `identQuote` and the quote character doubled per
`identEscape`, unless the binding said `raw`.

**Literals are held apart from the SQL, always.**

The renderer never concatenates a literal into a string. It builds a **part
list** — alternating chunks of finished SQL and numbered parameter slots — and
every literal, whether it came from a `num`/`text`/`bool` node or from a
`kind: value` binding, becomes a slot with its `Value` recorded in order.

```
parts:  ['(`o`.`total` > ', SLOT 1, ' AND `o`.`state` = ', SLOT 2, ')']
params: [Value::num('100'), Value::text('open')]
```

Joining is the last thing that happens, and there are two ways to do it:

| Mode | Slot becomes | Result |
|---|---|---|
| `inline` (default) | the dialect-quoted literal | ``(`o`.`total` > 100 AND `o`.`state` = 'open')`` |
| `params` | the dialect's placeholder — `?`, or `$n` for PostgreSQL | ``(`o`.`total` > ? AND `o`.`state` = ?)`` plus the array |
| `debug` | `~1~`, `~2~`, … | ``(`o`.`total` > ~1~ AND `o`.`state` = ~2~)`` |

This is a structural decision and not a rendering flag bolted on later, because
the alternative — emit a string, then find the literals in it again — is
exactly the class of bug that turns into a SQL injection. A part list cannot be
confused about where a literal ends, whatever the literal contains. Retrofitting
it would mean touching every template-fill site in every host, which is why it
is here in M2 rather than deferred.

The `~n~` debug spelling is `~` because SEL has no `~` token, so a slot marker
can never be mistaken for something the translator emitted from source. It is
for reading and for error messages only — never for substitution, which is what
the part list exists to make unnecessary. (PostgreSQL's regex operator *is* `~`,
which is precisely why the marker is not something the joiner ever has to scan
for.)

**The result object.**

```php
final class Fragment
{
    /** @var list<string|int> finished SQL chunks and 1-based parameter slots */
    public array $parts;
    /** @var list<\Sel\Value> one per slot, in order */
    public array $params;
    public string $kind;      // NUM | TEXT | BOOL | BIN | UNKNOWN
    public string $dialect;
    /** @var list<string> */
    public array $caveats;    // which inexactnesses were accepted

    /** Usable as a condition: BOOL as-is, UNKNOWN wrapped in the IS TRUE test. */
    public function asCondition(string $mode = 'inline'): string;

    /** Usable in a select list, GROUP BY, ORDER BY, HAVING. Any kind. */
    public function asValue(string $mode = 'inline'): string;

    /** The bound values for `params` mode, in placeholder order. */
    public function bindings(): array;
}
```

`inline` is the default because it is what makes §13.1's byte-exact suite
possible and what a human debugging a rule wants to see. An application issuing
the query should pass `params` and hand `bindings()` to the driver.

`asCondition()` on a `NUM` or `TEXT` fragment is `E_SQL_SHAPE`. That is the one
place the layer is opinionated about where a fragment may go, and it is worth
being opinionated: silently accepting `WHERE o.total` is how MySQL turns a
validation rule into a truthiness test SEL spent its whole design avoiding.

---

## 10. Errors and refusal

**One class and nothing else.** `Sel\Sql\SqlError` carries a code, a message
and the position of the node that failed — the same three things a `SelError`
carries, for the same reason. There is no second reporting channel, no
diagnostics object and no "explain why this rule cannot be pushed down" API:
the message is the explanation, and it is written to be read by whoever has to
act on it. `CRC32 has no mapping in dialect postgresql` at `1:14` needs no
further apparatus. Codes live in `sql/errors.md`, **not** in `spec/errors.md`: these are
translator failures, not language failures, and a SEL program that cannot be
translated is not a wrong program.

| Code | Means |
|---|---|
| `E_SQL_UNSUPPORTED` | no mapping for this operator or function in this dialect (or a `caveat` under `strict`) |
| `E_SQL_DIALECT` | mapped, but the dialect version is below the entry's `since` |
| `E_SQL_UNBOUND` | a variable with no binding |
| `E_SQL_BINDING` | a malformed binding, an unknown field, an alias collision |
| `E_SQL_ASSIGN` | an assignment or sequence stage 1 refuses |
| `E_SQL_SHAPE` | a list where a scalar is required, `_K` on a relation, a non-BOOL condition, an aggregate over something untranslatable |

Two entry points, because both callers are real:

```php
Sql::translate($program, $dialect, $bindings, $options): Fragment   // throws SqlError
Sql::tryTranslate($program, $dialect, $bindings, $options): ?Fragment  // null on refusal
```

`tryTranslate` is the one an application uses in its hot path. Refusal is an
expected, ordinary outcome — "this rule cannot be pushed down, evaluate it in
PHP" — and an expected outcome should not require a `try`/`catch` to observe. It
catches only `SqlError`; a bug in the translator must not be swallowed.

`translate` is the one to use when you want to know *why*, which is to say
during development, in a build-time audit of a rule set, or in a test. The two
differ only in how the failure arrives.

---

## 11. Where SQL and SEL differ

Every one of these is a real, known divergence. They are listed once, here, so
that nobody has to rediscover them, and each maps to a `caveat` name.

| Caveat | What differs |
|---|---|
| — (structural) | **No short-circuit.** `FALSE AND (1/0)` is `FALSE` in SEL; SQL may evaluate both sides and raise. Rules that lean on short-circuiting as a guard change meaning. |
| — (structural) | **NULL.** SEL has no null. Any nullable column makes three-valued logic reachable. Aggregate skeletons use `IS [NOT] TRUE` to fold NULL to false; nothing else does. A translated rule is only as sound as the schema's nullability. |
| `unicode-case` | `UPPER`/`LOWER` are ASCII-only in SEL. MySQL and PostgreSQL apply full Unicode case mapping; SQLite happens to agree with SEL. |
| `division-scale` | SEL's `/` yields ten fractional digits, half away from zero. MySQL's DECIMAL division adds four; PostgreSQL's `/` on integers **truncates** (so `postgresql.json` casts both operands to `numeric`); SQLite divides in floating point. |
| `rounding-mode` | SEL rounds half away from zero everywhere. MySQL and PostgreSQL agree on `DECIMAL`/`numeric`; SQLite's `round()` is floating point and does not. |
| `modulo-integer` | SQLite's `%` is integer-only, so `5.5 % 2` is not `1.5`. |
| `power-float` | `POWER` returns a float in every dialect; SEL's is exact. |
| `text-collation` | The `$` family is bytewise in SEL. The `textCollate` lexical entry forces a binary collation; a column with an incompatible declared collation can still defeat it. |
| `regex-engine` | SEL's regex subset is what PCRE and ECMAScript agree on. MySQL 8.0.4+ and MariaDB use ICU/PCRE, PostgreSQL uses POSIX ARE — the subset mostly survives, lazy quantifiers and some classes do not. SQLite has no `REGEXP` without a user function and refuses outright. |
| `concat-null` | `CONCAT` / `\|\|` yields NULL if any operand is NULL; SEL's `&` cannot. |
| — (refused) | `BAND`/`BOR`/`BXOR` — no portable byte-string bitwise operator exists. `ABORT` — a control-flow effect, not a value. `SPLIT`, `INDEXES`, `BTL`, `RGROUPS` — list-valued. `CHAR`/`CODE` on SQLite where the spelling differs by build. |

`IN` deserves its own note. SEL's `IN` is `EQL`-based and therefore structural —
it compares kind, scalar bytes and children. SQL's `IN` is a value comparison
under a collation. For scalar operands under `textCollate` the two agree, and
that is the only case the translator accepts: `IN` where either side has
children is `E_SQL_SHAPE`.

---

## 12. Using it

PHP, the shape the first end-to-end proof takes:

```php
<?php
require __DIR__ . '/../php/src/bootstrap.php';
require __DIR__ . '/../php/src/Sql/bootstrap.php';

use Sel\Sel;
use Sel\Sql\Sql;

$program = Sel::compile('TOTAL > 100 AND ALL(ITEMS, I, I["qty"] > 0)');

// dependencies() says what must be bound: ['ITEMS', 'TOTAL']
$bindings = [
    'TOTAL' => ['kind' => 'column', 'table' => 'o', 'column' => 'total', 'type' => 'NUM'],
    'ITEMS' => [
        'kind' => 'relation', 'from' => 'order_items', 'alias' => 'oi',
        'correlate' => ['raw' => '`oi`.`order_id` = `o`.`id`'],
        'fields' => ['QTY' => ['column' => 'qty', 'type' => 'NUM']],
    ],
];

$frag = Sql::tryTranslate($program, 'mysql', $bindings);

if ($frag === null) {
    // Graceful refusal: this rule stays in PHP.
    $rows = $pdo->query('SELECT * FROM orders o')->fetchAll();
    $rows = array_filter($rows, fn ($r) => $program->run($r)->asBool());
} else {
    $sql = 'SELECT o.id FROM orders o WHERE ' . $frag->asCondition();
    $rows = $pdo->query($sql)->fetchAll();
}
```

with `$frag->asCondition()` being

```sql
((`o`.`total` > 100) AND NOT EXISTS (SELECT 1 FROM `order_items` `oi`
  WHERE `oi`.`order_id` = `o`.`id` AND ((`oi`.`qty` > 0)) IS NOT TRUE))
```

Python, once the port lands — same call, host spelling:

```python
from sel import compile as sel_compile
from sel.sql import try_translate

program = sel_compile('TOTAL > 100 AND ALL(ITEMS, I, I["qty"] > 0)')
frag = try_translate(program, 'postgresql', bindings)
if frag is None:
    ...                       # fall back to program.run(context)
cur.execute(f'SELECT o.id FROM orders o WHERE {frag.as_condition()}')
```

---

## 13. Tests

Two layers, and they test different things.

### 13.1 Translation cases — `sql/cases/*.sqlt`

Line-oriented, `.selt`'s sibling, with two extra sections. These assert the
**exact string**, which is what makes them a cross-host parity check: if PHP and
Python emit different SQL for the same input, the suite says so, and no database
is needed to find out.

```
% Aggregates over a relation binding.

### name: agg.all.relation.mysql
--- dialect
mysql
--- bindings
{"ITEMS": {"kind": "relation", "from": "order_items", "alias": "oi",
           "correlate": {"raw": "`oi`.`order_id` = `o`.`id`"},
           "fields": {"QTY": {"column": "qty", "type": "NUM"}}}}
--- source
ALL(ITEMS, I, I["qty"] > 0)
--- expect
NOT EXISTS (SELECT 1 FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id` AND ((`oi`.`qty` > 0)) IS NOT TRUE)
===
### name: agg.all.columns-unroll
--- dialect
postgresql
--- bindings
{"VALUES": {"kind": "columns", "items": [
  {"table": "x", "column": "a", "type": "NUM"},
  {"table": "x", "column": "b", "type": "NUM"},
  {"table": "x", "column": "c", "type": "NUM"}]}}
--- source
ALL(VALUES, V, V > 0)
--- expect
(("x"."a" > 0) AND ("x"."b" > 0) AND ("x"."c" > 0))
===
### name: agg.nested.static-after-inlining
--- dialect
sqlite
--- source
R[1] = (1, 2); R[2] = (3, 4); ALL(R, ROW, ALL(ROW, _ > 0))
--- expect
((1 > 0) AND (2 > 0) AND (3 > 0) AND (4 > 0))
===
### name: refuse.split-is-a-list
--- dialect
mysql
--- source
SPLIT("a,b", ",")
--- error
E_SQL_UNSUPPORTED 1:1
===
### name: refuse.crc32-has-no-postgres-spelling
--- dialect
postgresql
--- source
CRC32("x")
--- error
E_SQL_UNSUPPORTED 1:1
```

`bindings` is JSON, breaking `.selt`'s no-JSON rule deliberately: the sections
are nested records rather than flat blocks, and unlike `.selt` this format is
read only by hosts that have a JSON parser in the standard library. That is
every host the SQL layer will ever have.

The runner is `php/bin/sqlt` and `python/bin/sqlt.py`, registered as `impl_sql`
in `tools/impls.sh`, with a step in `tools/check.sh`. Adding a third host to the
SQL layer is one entry there and nothing else, the same as adding a host today.

Case categories to cover: `lex.*` (quoting and escaping, including a table named
`` ` `` and a string containing `'` and `\`), `op.*`, `func.*` per dialect,
`agg.*` (all three shapes and the FILTER rewrites), `norm.*` (inlining accepted
and refused), `kind.*` (variant selection), `refuse.*`, `dialect.*` (`since`
gating), `strict.*` (caveats fatal).

### 13.2 Functional tests — a real database

Python only, as the brief says: `uv` resolves the drivers locally and no other
host has to grow a database dependency.

```
python/tests/sql/
  conftest.py          fixtures; skip a backend when its DSN is absent
  test_sqlite.py       in-process, always runs
  test_mysql.py        SEL_SQL_MYSQL_DSN
  test_postgresql.py   SEL_SQL_PG_DSN
```

Drivers as an optional extra in `pyproject.toml` — `[project.optional-dependencies]
sqltest = ["PyMySQL", "psycopg[binary]"]` — so the core package keeps its "no
dependencies" claim, which is a stated selling point and worth protecting.

The strong check is **differential, against the evaluator**, in the shape
`tools/check-decimal.sh` already uses with its Python oracle:

> For every case whose bindings are all `kind: value` — that is, every case
> whose translation is a closed expression over literals — evaluate the SEL
> source with `Program.run()`, translate it, run `SELECT <fragment>`, and
> compare. Any disagreement is either a map bug or an entry that needs a
> caveat.

That turns the whole conformance corpus into SQL test material for free,
minus the constructs the translator refuses — and the refusals are themselves
the interesting output, since the list of what a dialect cannot do is exactly
what an application needs to know before it commits to pushing rules down.

Booleans need care when comparing: MySQL and SQLite return `1`/`0`, PostgreSQL
returns `t`/`f`, and SEL returns a BOOL. The comparison normalises to SEL kinds
before asserting, and the normalisation is per driver.

---

## 14. Delivery sequence

Milestones, each one leaving the tree green.

**M1 — the map exists.** `sql/MAP.md`, `sql/errors.md`, `sql/dialects/ansi.json`,
`mysql-family.json` and `mariadb.json`, `tools/gen-sql-map.mjs`, `tools/check-sql-map.sh`, and the
generated PHP file. No translator yet; the deliverable is that the data is
authored, validated and generated.

**M2 — PHP translates the core.** Stages 1, 3 and 4 for operators, literals,
`column` bindings, `IF`/`COND`, and the non-aggregate functions. `sql/cases/`
covering `lex.*`, `op.*`, `func.*`, `norm.*`, `refuse.*`; `php/bin/sqlt`;
`impl_sql` wired into `check.sh`.

**M3 — PHP does aggregates.** Stage 2, all three shapes, the FILTER rewrites,
the `agg.*` cases.

**M4 — PHP + MariaDB end to end.** `examples/sql-php.php` against a real
MariaDB: a schema, an order-validation rule, the rule pushed into a `WHERE`,
the same rule evaluated in PHP over the unfiltered rows, and an assertion that
the two select the same ids. **This is the gate.** Nothing below starts until
it passes.

**M5 — MySQL, PostgreSQL and SQLite maps.** Data and cases only; the translator does
not change. If it does, that is a bug in the M1 design and should be fixed as
one.

**M6 — Python port.** Transcribed from the PHP, generated map consumed as-is,
the same `sql/cases/` suite passing byte-identically. That equality is the
whole point of §13.1.

**M7 — the functional harness.** `python/tests/sql/`, the differential runner,
the three backends.

**M8 — alpha.** Commit to GitHub and mark it with a lightweight tag. Nothing
is published to Packagist, npm or PyPI, and `tools/check.sh` is not required to
be green: the point of the tag is to be able to name the state the PHP+MariaDB
proof passed in, not to ship it. See §15.

**Later, and separately:** JS, C++ and Lisp ports. Each is a transcription of a
design that three hosts will have already agreed on, which is the cheapest
moment to do it.

A note on how to build M2–M3 and M5–M6: those are the phases where fanning work
out pays. Authoring four dialect documents, writing the case files per category,
and transcribing PHP→Python are largely independent and verifiable against a
fixed suite. I have not launched anything — say the word and I will run it as a
workflow, which will spawn on the order of a dozen agents and cost accordingly.
M1 and M4 should be done in one head; they are where the decisions are.

---

## 15. Marking the alpha

**The alpha is a commit and a lightweight tag. Nothing is published.**

That decision removes a constraint rather than imposing one.
`tools/check-version.sh` compares seven manifests by **literal string
equality**, has no notion of a prerelease, and the ecosystems spell one three
incompatible ways — npm `0.4.0-alpha.1`, PyPI `0.4.0a1`, Conan and CMake
something else again. Putting any of those in the manifests fails the check
today. Since nothing is being published, none of it applies: the manifests keep
whatever plain version they carry, the tag names the commit, and the spelling
problem is deferred to the first *published* prerelease, where the fix is a
ten-line normalisation table in `check-version.sh`.

`tools/check.sh` is likewise not a gate on the tag. It runs six implementations
of a language of which two will have a SQL layer; a red step somewhere in that
matrix is not a reason to withhold a marker on work that has been proven
end to end against a real MariaDB. **M4 is the gate that matters.** The tag is
bookkeeping.

---

## 16. Out of scope

Stated as out of scope in the brief, and confirmed here:

- translating SEL into stored procedures or user-defined functions;
- building whole statements — `SELECT`, `FROM`, `JOIN` and `GROUP BY` are the
  application's, and the translator emits an expression to put in them;
- guaranteeing that a translated rule succeeds. Type errors at the database are
  an accepted outcome at this stage; that is the "best effort" the brief asks
  for, and §11 is the honest list of where it bites.

Deliberately deferred, and named here so it is a decision rather than an
oversight:

- **Named and driver-specific placeholder styles.** `params` mode (§9) ships in
  M2 with `?` and `$n`; `:name` and anything a particular driver prefers can be
  added as one more lexical entry when something needs it.
- **A precedence-aware emitter**, to stop wrapping everything in parentheses.
- **Date and time.** SEL has no date type, so there is nothing to map yet.
- **`ORDER BY` / window constructs.** SEL has no ordering vocabulary.

---

## 17. Decisions taken

Recorded so the reasoning survives the conversation that produced it.

**Unrolling is the default; a subquery is what you ask for.** When a host can
express a list either way, bind it as `columns` and let stage 2 unroll it. §5.5
shows both renderings of the same rule side by side. A `relation` binding is for
the case where the element count is genuinely not known until the query runs.

**One error class, and the message is the explanation.** `SqlError` with a code,
a descriptive message and a position. No diagnostics object, no `explain()`, no
second channel. `translate()` throws it, `tryTranslate()` returns null, and an
application that wants the reason calls the first (§10).

**`ansi` and `mysql-family` are bases, never targets.** Naming either in a
`translate()` call is `E_SQL_DIALECT`. A dialect no server implements is not a
dialect anyone should be able to aim at (§4.2).

**MariaDB is the reference.** The end-to-end proof (M4) runs against MariaDB —
11.8 locally — and `mariadb.json` is authored first. `mysql.json` is its sibling
under `mysql-family` rather than its parent, so the tested dialect never
inherits from an untested one (§4.2).

**Literals live in a part list from the first line of the renderer.** Inline
output stays the default and stays byte-exact for the suite, but the renderer
never concatenates a literal into a string, so `params` mode is a way of joining
rather than a second code path. `~n~` is the debug spelling of a slot, chosen
because SEL has no `~` token (§9).

**The alpha is a commit and a lightweight tag, published nowhere.** M4 is the
gate; the tag is bookkeeping (§15).
