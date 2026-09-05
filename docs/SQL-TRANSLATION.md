# SEL → SQL translation

**Status: M1–M5 are built — MariaDB, MySQL, PostgreSQL and SQLite, each verified against a running server (see §14); the Python port is plan.** This document is the
design for the SQL layer. It is written in the same register as `spec/SPEC.md` — where it and a
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

**Stage 3 is a stage in the design and a fold in the code.** The implementation
computes kinds during the stage 4 walk rather than in a pass of its own. The
walk is post-order, so every operand's kind is already known when its parent
needs it — which is exactly what a separate pass would have computed, at the
cost of a second traversal and a side table keyed by node identity that PHP's
array-valued AST cannot cheaply provide. Whole-expression refusal is unaffected:
nothing becomes characters until `asValue()` is called on a returned Fragment,
so a kind failure still escapes with no partial output. It is described
separately here because it is a separate set of rules, and a host that finds the
fold awkward may unfold it without changing a single emitted byte.

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

**The generator validates these and leaves them in place**; every host expands
them when it fills the template. Baking them in would be one fewer thing to do
at render time and would quietly break the reason lexical keys exist:
`textCollate` is stated once and used by thirteen comparison entries, so an
application on a server with a different binary collation should be able to
override that one key and have all thirteen follow. Pre-expanded, the key is
gone by then. The hosts need the expansion code regardless, since entries
registered at run time (§4.7) never pass through the generator at all.

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
                // Lexical references survive into the shipped map; see §4.2.
                '==' => ['variants' => ['num' => '({0} = {1})',
                                        'coerce' => '({numericCast:0} = {numericCast:1})'],
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
(((`x`.`a` > 0) AND (`x`.`b` > 0)) AND (`x`.`c` > 0))
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
(((`x`.`a` > 0) AND (`x`.`b` > 0)) AND (`x`.`c` > 0))

-- in a statement the application builds
SELECT a, b, c FROM x
 WHERE (((`x`.`a` > 0) AND (`x`.`b` > 0)) AND (`x`.`c` > 0))
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
ALL((1, 2, 3), _ > 0)     ->     (((1 > 0) AND (2 > 0)) AND (3 > 0))
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
   - any assignment inside an aggregate body or a call argument;
   - an assignment whose target is indexed by a non-constant expression.
4. **Inline.** Substitute each assignment's right-hand side for every read of
   its target in the statements that follow. **Capture happens at the
   assignment, not at the use**, which is what SEL itself does: in
   `A = B + 1; B = 2; A + B` the `B` inside `A` is the one that was in scope
   when `A` was written. Inlining at the use instead would quietly rewrite the
   program. An earlier draft of this document listed "a variable read before its
   assignment" as a refusal; it is not one, and `norm.inline.captures-at-assignment`
   pins the behaviour so nobody restores the rule. Substitution is by AST node, and
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
   (((1 > 0) AND (2 > 0)) AND ((3 > 0) AND (4 > 0)))
   ```

   The parentheses nest because the outer fold is handed two composite
   fragments, not four scalars — §7.1 says the same thing from the other end.

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

**This section is the M3 contract.** Iteration is the interesting half of the
whole exercise, and everything below is stated as an input and an exact output
so that it can be argued with before it is built.

### 7.0 Where it happens, and how the shape is decided

Aggregate lowering runs **inside the render walk**, not as an AST→AST pass
before it. Two of the three shapes have to render — a relation becomes a
subquery, which is characters — and the third needs the dialect's operator
templates, which an AST rewrite has no access to. Writing half of it as a tree
rewrite and half as rendering would put one rule in two places.

Which of the three shapes applies is decided by **what the first argument is**,
after stage 1 has done its inlining — never by what it might evaluate to:

| First argument, after stage 1 | Shape |
|---|---|
| a `list` node, or a `clist` from indexed assignment | static unroll (§7.1) |
| a `var` bound `kind: value` holding children | static unroll over those children |
| a `var` bound `kind: columns` | columns unroll (§7.2) |
| a `var` bound `kind: relation` | subquery (§7.3) |
| anything else that renders to a scalar | a **one-element list containing itself**, per spec §7.3 |
| a `var` bound `kind: value` holding a NONE with no children | the **empty list** |
| `FILTER(...)` | absorbed first (§7.5), then re-dispatched on *its* source |
| anything else | `E_SQL_SHAPE` |

The scalar row is not a convenience. It is what spec §7.3 already says, and it
is what makes `ALL(TOTAL, _ > 0)` mean the obvious thing when `TOTAL` is one
column:

```
ALL(TOTAL, _ > 0)                    ->   (`o`.`total` > 0)
```

### 7.1 Static list → unroll

```
ALL(list, body)  ->  (body[e1] AND body[e2] AND …)      empty -> TRUE
ANY(list, body)  ->  (body[e1] OR  body[e2] OR  …)      empty -> FALSE
SUM(list, body)  ->  (body[e1] + body[e2] + …)          empty -> 0
JOIN(list, sep)  ->  the dialect's concat, folded pairwise
COUNT(list)      ->  the count, as a literal
```

`body[e]` is the body rendered with the binder bound to that element and `_K`
bound to a TEXT literal of its key.

**The fold is pairwise-left, through the operator's own binary template** — the
same template a hand-written chain goes through. So `ALL((a, b, c), p)` and
`p(a) AND p(b) AND p(c)` produce the same bytes, and there is no rule about
which code path built an expression that anyone has to learn.

An n-ary fold would give flatter output, and it was the first plan. It does not
survive contact with the map: `AND` is spelled `({0} AND {1})`, and getting
`(x AND y AND z)` out of that means either splitting the template on `{1}` to
recover a separator, or adding a variadic-with-separator template form for the
sake of three operators. Neither is worth flatter parentheses — and going
pairwise stops `JOIN`, already pairwise below, being the odd one out.

```sel-case agg.static.all
ALL((1, 2, 3), _ > 0)
    (((1 > 0) AND (2 > 0)) AND (3 > 0))
```

```sel-case agg.static.any
ANY((1, 2), _ > 1)
    ((1 > 1) OR (2 > 1))
```

```sel-case agg.static.sum
SUM((1, 2, 3), _ * 2)
    (((1 * 2) + (2 * 2)) + (3 * 2))
```

```sel-case agg.static.join
JOIN((1, 2, 3), "-")
    CONCAT(CONCAT(CONCAT(CONCAT(1, '-'), 2), '-'), 3)
```

`JOIN` folds pairwise through the dialect's `&` template rather than through a
variadic one, because `&` is what SEL's `JOIN` *is* and a variadic concat would
need a new lexical key spelled two ways (`CONCAT(a, b, c)` here, `(a || b || c)`
elsewhere). The nesting is ugly and the alternative is map surface for one
function.

**Nesting falls out.** The binder names an element, and if that element is
itself a list node the inner aggregate simply dispatches on it again:

```sel-case agg.nested.static
R[1] = (1, 2); R[2] = (3, 4); ALL(R, ROW, ALL(ROW, _ > 0))
    (((1 > 0) AND (2 > 0)) AND ((3 > 0) AND (4 > 0)))
```

Note the shape of that output. The outer `ALL` folds **two composite
fragments**, so the parentheses nest — this is not the flat four-way `AND` an
earlier draft of §13.1 claimed, and the earlier claim was simply wrong: a fold
combines the operands it is handed, not the ones inside them.

### 7.2 `columns` binding → unroll over columns

Identical to §7.1 with column references as the elements and `_K` bound to the
ordinal as TEXT.

With `V` bound as `columns x.a, x.b, x.c`:

```sel-case agg.columns.unroll
ALL(V, C, C > 0)
    (((`x`.`a` > 0) AND (`x`.`b` > 0)) AND (`x`.`c` > 0))
```

which, dropped into a `WHERE`, is `SELECT a, b, c FROM x WHERE a > 0 AND b > 0
AND c > 0`. No subquery and none needed: the iteration happened at translation
time and left nothing behind.

The two-level spelling from the brief works because §7.0's scalar rule catches
the inner one — the binder names a single column, and an aggregate over a
scalar is a one-element list:

```sel-case agg.columns.two-level-spelling
ALL(V, C, ALL(C, _ > 0))
    ((`x`.`a` > 0) AND (`x`.`b` > 0))
```

### 7.3 `relation` binding → subquery

The map's `skel` section supplies the skeleton and the lowering fills it.

| SEL | Skeleton | `{body}` is |
|---|---|---|
| `ALL(rel, b)` | `all` | the body |
| `ANY(rel, b)` | `any` | the body |
| `SUM(rel, b)` | `sum` | the body |
| `COUNT(rel)` | `count` | — |
| `JOIN(rel, s)` | `join` | refused on MariaDB; see below |
| `x IN rel` | `inRelation` | the relation's `scalar` field — **one-field relations only** |

These are quotations of `sql/cases/12-aggregates.sqlt`, checked by
`php/bin/sqldoc`, so the lines are as long as the translator makes them:

```sel-case agg.relation.all
ALL(ITEMS, I, I["qty"] > 0)
    NOT EXISTS (SELECT 1 FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id` AND ((`oi`.`qty` > 0)) IS NOT TRUE)
```

```sel-case agg.relation.sum
SUM(ITEMS, _["QTY"] * _["PRICE"])
    (SELECT COALESCE(SUM((`oi`.`qty` * `oi`.`price`)), 0) FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id`)
```

```sel-case agg.relation.count
COUNT(ITEMS) == 0
    ((SELECT COUNT(*) FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id`) = 0)
```

`ITEMS` there declares one field. Over a relation declaring several, the same
expression is refused:

```sel-case agg.relation.in
SKU IN ITEMS
    (CAST(`o`.`sku` AS CHAR) COLLATE utf8mb4_bin IN (SELECT CAST(`oi`.`sku` AS CHAR) COLLATE utf8mb4_bin FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id`))
```

```sel-case refuse.in-over-a-multi-field-relation
SKU IN ITEMS
    E_SQL_SHAPE
```

**`IN` is refused over a relation with more than one field.** This example used
to bind the multi-field `ITEMS`, and that was wrong in a way no case file could
see. A relation with several fields is a list of **rows**; SEL compares a scalar
against a row structurally, which is FALSE for every row, always. Projecting one
column would translate something the evaluator never answers — and the row
oracle proved it, selecting order 1 on the server and nothing at all in SEL.

`scalar` therefore carries one meaning, not two: it says what a bare binder
resolves to inside an aggregate body (§7.4). Using it as a projection for `IN`
only agrees with SEL when the relation has nothing else in it, because that is
the only shape a host can model as a list of scalars. Bind the column you want to
search as its own one-field relation; `sql/oracle/rows.json` shows both.

`IS NOT TRUE` rather than `NOT (…)` is the point of care. SQL is three-valued
and SEL is not: if the body is NULL for some row, `NOT (body)` is NULL, the
`WHERE` rejects the row, and `NOT EXISTS` reports "every row satisfies it" —
silently the wrong answer for exactly the case a validation rule exists to
catch. `IS NOT TRUE` folds NULL into false, so a NULL body makes `ALL` false.

`{from}` fills with the quoted table and alias, or with the binding's
`from: {raw: …}` when it carries a query of its own. `{corr}` fills with the
binding's `correlate`, or with `lexical.true` when it has none — an
uncorrelated relation is a subquery over the whole table, which is legal and
occasionally what you want.

`correlate` is `{raw: …}` and nothing else in M3. A structured join condition is
a rabbit hole with no obvious floor, and the host wrote the aliases, so the host
can write the predicate that joins them.

**Nesting works and needs no machinery.** An inner relation's `correlate` names
the outer alias, because the host wrote it:

```sel-case agg.relation.nested
ALL(ORDERS, O, ALL(LINES, L, L["qty"] > 0))
    NOT EXISTS (SELECT 1 FROM `orders` `o` WHERE TRUE AND (NOT EXISTS (SELECT 1 FROM `lines` `l` WHERE `l`.`order_id` = `o`.`id` AND ((`l`.`qty` > 0)) IS NOT TRUE)) IS NOT TRUE)
```

Two relations sharing an alias in one expression is `E_SQL_BINDING`, checked
before anything is rendered.

### 7.4 The binder environment

A stack of frames, `name => binder`, consulted **before** the bindings map —
the same precedence `Sel\Context::lookup` gives an aggregate binder over a
variable. A binder is one of three things, matching the three shapes:

| Binder | `_` resolves to | `_["k"]` resolves to | `_K` |
|---|---|---|---|
| a static element | the element's AST node, re-entered | indexing that node | its key, as TEXT |
| a column | that column reference | `E_SQL_SHAPE` | the ordinal, as TEXT |
| a relation row | the relation's `scalar` field, or `E_SQL_SHAPE` if it declares none | the named field | **`E_SQL_SHAPE`** |

The **scalar** row of §7.0 binds as a static element whose node is the first
argument itself and whose key is `"1"`, matching what `Core::elements` does in
the evaluator. That single row is what makes `ALL(TOTAL, _ > 0)` and the
two-level `ALL(V, ALL(V, …))` above work, so it is not a special case in the
lowering — it is a one-element list.

A `value` binding holds `Value` children rather than AST nodes, so its elements
are **synthesised into nodes** before binding: a child with children becomes a
`clist`, and a scalar becomes a `text` or `num` node according to the binding's
declared `type` — the same rule `declaredKind` already applies to the whole
value in M2. Synthesising is smaller than a fourth binder shape and inherits
M2's quoting decision rather than restating it.

`_K` on a relation is refused because a row has no portable key. Inventing one —
`ROW_NUMBER()`, the primary key — would be a guess about the schema this layer
is careful never to make.

Binders shadow, and nested aggregates shadow independently, exactly as spec
§7.3 requires of the evaluator.

### 7.5 `FILTER` absorption

`FILTER` yields a list, so on its own it is refused. But "all the ones that
match also satisfy" is one of the commonest shapes a real rule takes, so four
combinations are absorbed:

```
ALL(FILTER(L, p), q)   ->   ALL(L, (NOT (p)) OR (q))
ANY(FILTER(L, p), q)   ->   ANY(L, (p) AND (q))
SUM(FILTER(L, p), q)   ->   SUM(L, CASE WHEN (p) THEN (q) ELSE 0 END)
COUNT(FILTER(L, p))    ->   SUM(L, CASE WHEN (p) THEN 1 ELSE 0 END)
```

Absorption happens **in the dispatch, not as an AST rewrite**, for one specific
reason: the `FILTER` and the enclosing aggregate may name their binders
differently, and rewriting the tree would mean substituting one name for the
other. Binding both names to the same element instead is three lines and cannot
get the substitution wrong.

Each rewrite is NULL-safe under the skeletons in §7.3. For `ALL`, a NULL `p`
with a FALSE `q` gives a NULL body, which `IS NOT TRUE` includes — so the
element is treated as having been in the filter and having failed, which is the
conservative reading. For `ANY`, a NULL `p` gives a body that is not TRUE, so
the `ANY` does not fire on it.

After absorption the result is re-dispatched on `L` — and re-dispatch is what
makes `FILTER(FILTER(L, p1), p2)` work too, since the inner one is absorbed on
the way through and the predicates conjoin. All three shapes get the rewrite for
free:

```sel-case agg.filter.absorbed-into-count-bare
COUNT(FILTER(ITEMS, I, I["qty"] <= 0))
    (SELECT COALESCE(SUM(CASE WHEN (`oi`.`qty` <= 0) THEN 1 ELSE 0 END), 0) FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id`)
```

`MAP` as an aggregate's source is **not** absorbed in M3 and is refused with a
reason. It would need real substitution rather than double binding, and one
unabsorbed source is a smaller cost than a substitution pass written to serve a
single case.

#### The dream solution, for future research

`FILTER` absorption works by double binding because a filter does not change
what the element *is* — it only decides whether the element takes part. `MAP`
does change it, and that is the whole difficulty: in `ALL(MAP(L, f), q)`, the
`_` inside `q` names the *result of `f`*, and the `_` inside `f` names the
original element. Two binders, two meanings, one name.

The general mechanism that covers `MAP` and much else is a **let-binding in the
fragment language**: rather than substituting `f` into every occurrence of `_`
in `q`, bind it once.

```
ALL(MAP(L, f), q)   ->   ALL(L, LET _ = f IN q)
```

with `LET` lowering per shape:

- **static and columns unroll** — no SQL construct needed at all. The unroll
  already renders `f` once per element; binding `_` in the frame to the
  *resulting Fragment* rather than to an AST node makes `q` see it. This is a
  fourth binder shape (`FRAGMENT`) and perhaps twenty lines. It would also make
  `SUM(MAP(L, f), _)` and the `JOIN(MAP(…), sep)` in
  `examples/order-validation.sel` translate.
- **relation** — a lateral derived table, which is what SQL's answer to `LET`
  actually is:

  ```sql
  NOT EXISTS (SELECT 1 FROM order_items oi,
                   LATERAL (SELECT f AS v) m
               WHERE corr AND (q over m.v) IS NOT TRUE)
  ```

  MariaDB has no `LATERAL` (as of 11.8), PostgreSQL has had it since 9.3, and
  SQLite has none. So the relation half is not portable today, and a dialect
  that lacks it would have to fall back to substituting `f` textually — which
  duplicates the expression once per mention of `_`, and duplicating a
  subquery-valued `f` is a performance trap rather than a correctness one.

Three things would have to be settled before building it, and none is obvious:

1. **Where the duplication budget sits.** Textual substitution is simple and
   can multiply work; `LATERAL` is clean and is not portable. A rule that picks
   between them per dialect makes the same SEL source translate to structurally
   different SQL, which the byte-exact suite would then have to encode per
   dialect rather than once.
2. **Whether `FRAGMENT` binders leak.** A Fragment holds parameter slots. Bind
   one to a name used three times in `q` and the slots are spliced three times —
   correct today, because splicing copies absolute indices rather than
   renumbering (§9), but it is an invariant worth pinning with a case before
   anything depends on it.
3. **Whether it should be `MAP`-shaped at all.** `LET` is more general than
   `MAP` absorption, and once it exists the obvious next question is whether
   stage 1 should use it for the helper variables it currently inlines by
   duplication — `A = 1 + 2; A * A` emits `(1 + 2)` twice today. That is a
   larger and more interesting change than `MAP`, and it argues for designing
   `LET` on its own terms rather than as an aggregate special case.

The honest summary: the unroll half is small and worth doing whenever `MAP`
becomes the thing people actually hit, and the relation half is blocked on
`LATERAL` being available in the dialects that matter. Neither is M3.

### 7.6 `COUNT`, `HAS`, `INDEXES`

| | static / columns | relation | scalar |
|---|---|---|---|
| `COUNT(x)` | the count, as a literal | `count` skeleton | `0` — a scalar has no children (spec §7.4) |
| `HAS(x, k)` | `TRUE`/`FALSE`, decided at translation time | `TRUE`/`FALSE` from the declared fields | `FALSE` |
| `INDEXES(x)` | `E_SQL_SHAPE` — yields a list | same | same |

`HAS` needs a **constant** key, for the same reason indexing does: which column
it asks about has to be known before the query runs.

### 7.7 What is refused, and where

| Construct | Code | Because |
|---|---|---|
| `MAP` or `FILTER` in a value position | `E_SQL_SHAPE` | yields a list |
| `MAP` as an aggregate's source | `E_SQL_SHAPE` | see §7.5 |
| `INDEXES` anywhere | `E_SQL_SHAPE` | yields a list |
| `_K` inside a relation body | `E_SQL_SHAPE` | a row has no portable key |
| `x IN rel` where `rel` declares more than one field | `E_SQL_SHAPE` | the relation is a list of rows, and SEL compares a scalar against a row structurally: FALSE for every row. Bind the projected column as its own one-field relation. Found by the row oracle, which got `[1]` from the server and `[]` from SEL |
| `rel[1]` — indexing a relation by position | `E_SQL_SHAPE` | rows have no order without an `ORDER BY`. A numeric key on a relation takes this branch **before** the field lookup, so the message is about ordering rather than a missing field |
| `JOIN` over a relation, on MariaDB | `E_SQL_UNSUPPORTED` | `GROUP_CONCAT` does not specify an order, and SEL's `JOIN` concatenates in insertion order. The reason comes from the map |
| a non-BOOL aggregate body for `ALL`/`ANY` | `E_SQL_SHAPE` | SEL has no truthiness |
| a non-NUM aggregate body for `SUM` | `E_SQL_SHAPE` | symmetric with the above; `UNKNOWN` passes, as it does for BOOL |
| `HAS(x, k)` with a non-constant `k` | `E_SQL_SHAPE` | which column it asks about must be known before the query runs |
| two relations sharing an alias | `E_SQL_BINDING` | the correlation would name the wrong rows |

### 7.8 Worked contract: the project's own rule

`examples/order-validation.sel` is the honest test, and the honest result is
that **it does not translate**. That is worth showing in full, because "which of
my rules can be pushed down" is the question an application actually has.

With `ITEMS` bound as a relation over `order_items oi` correlated on
`oi.order_id = o.id`, fields `QTY`, `PRICE`, `SKU`, scalar `SKU`, and
`CUSTOMER`, `POSTCODE`, `CREDIT_LIMIT` bound as columns of `o`:

**What translates.** Each of these is a conjunct the host can compile on its
own and push down:

```sel-case agg.relation.count
COUNT(ITEMS) == 0
    ((SELECT COUNT(*) FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id`) = 0)
```

```sel-case agg.filter.absorbed-into-count
COUNT(FILTER(ITEMS, _["QTY"] <= 0)) > 0
    ((SELECT COALESCE(SUM(CASE WHEN (`oi`.`qty` <= 0) THEN 1 ELSE 0 END), 0) FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id`) > 0)
```

```sel-case agg.contract.over-credit-limit
SUM(ITEMS, _["QTY"] * _["PRICE"]) > CREDIT_LIMIT
    ((SELECT COALESCE(SUM((`oi`.`qty` * `oi`.`price`)), 0) FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id`) > `o`.`credit_limit`)
```

```sel-case agg.contract.all-skus-well-formed
ALL(ITEMS, RMATCH('^[A-Z]{2}-\d{4}$', _["SKU"]))
    NOT EXISTS (SELECT 1 FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id` AND ((`oi`.`sku` COLLATE utf8mb4_bin REGEXP '(?s)^[A-Z]{2}-[0-9]{4}$')) IS NOT TRUE)
```

That last one is `\d` rewritten to `[0-9]` — see §7.10.

**What does not, and why.** Two constructs in the rule need an order that a
relation does not have:

- `FIRST_SKU = IF(COUNT(ITEMS) > 0, ITEMS[1]["SKU"], "-")` — `ITEMS[1]` asks for
  the first row of a set that has no first row. `E_SQL_SHAPE`.
- `BAD_SKUS = JOIN(MAP(FILTER(ITEMS, …), _["SKU"]), ", ")` — `MAP` as a source,
  and `JOIN` over a relation, which MariaDB cannot order.

So `Sql::tryTranslate` on the whole rule returns `null`, and `Sql::translate`
says which construct stopped it and where. **That is the design working**, not
failing: the rule was written to produce a human-readable message naming a
specific bad SKU, and a `WHERE` clause is not the place that job belongs.

**The pattern this suggests.** An application pushes down the conjuncts it can
to narrow the rows, then runs the whole rule on what comes back:

```php
$prefilters = ['COUNT(ITEMS) > 0', 'SUM(ITEMS, _["QTY"] * _["PRICE"]) <= CREDIT_LIMIT'];
$where = [];
foreach ($prefilters as $src) {
    $f = Sql::tryTranslate(Sel::compile($src), 'mariadb', $bindings);
    if ($f !== null) {
        $where[] = $f->asCondition();
    }
}
$sql = 'SELECT * FROM orders o' . ($where ? ' WHERE ' . implode(' AND ', $where) : '');
// then evaluate the full rule in PHP over the rows that came back
```

This needs no new API — each conjunct is its own program — and it is worth
naming because it is what "best effort" looks like in practice. Whole-expression
refusal is about never emitting half a string; it was never about forbidding a
caller from asking about half a rule.

**Note what the prefilter above selects.** Pushing down
`SUM(…) <= CREDIT_LIMIT` narrows to the orders that *pass* that check, so an
order that exceeds its limit never reaches PHP and never receives its
"exceeds the credit limit" message. That is right when the application wants
the acceptable orders and wrong when it wants every order with its verdict. For
the second job the prefilter has to be widened or dropped — pushing down a
validation rule and pushing down a *search* are different tasks that happen to
share a translator.

### 7.9 Implementation shape

A binder, and the stack the translator consults before its bindings:

```php
final class Binder
{
    public const NODE = 'node';        // a static element: an AST node
    public const COLUMN = 'column';    // one column reference
    public const ROW = 'row';          // a row of a relation

    public string $shape;
    /** @var array<string,mixed>|null */ public ?array $node;      // NODE
    /** @var array<string,mixed>|null */ public ?array $column;    // COLUMN
    /** @var array<string,mixed>|null */ public ?array $relation;  // ROW
    public ?string $key;               // what _K yields; null on a relation
}
```

The dispatch, which is the whole of §7.0 in one switch:

```php
private function aggregate(array $n): Fragment
{
    [$binder, $body] = self::shape($n);          // 2- and 3-argument forms
    $src = $this->source($n['args'][0], $n);     // absorbs FILTER, then classifies

    switch ($src->shape) {
        case Source::STATIC_LIST:
        case Source::COLUMNS:
            $parts = [];
            foreach ($src->elements as $key => $elem) {
                $this->push([$binder => $elem, '_K' => Binder::key((string) $key)]);
                try {
                    $parts[] = $this->guarded($n['name'], $body, $src->filter);
                } finally {
                    $this->pop();
                }
            }
            return $this->fold($n['name'], $parts, $n['pos']);

        case Source::RELATION:
            $this->push([$binder => Binder::row($src->relation)]);
            try {
                $inner = $this->guarded($n['name'], $body, $src->filter);
            } finally {
                $this->pop();
            }
            return $this->skeletonFor($n['name'], $src->relation, $inner, $n['pos']);
    }
}
```

`guarded()` is where §7.5 lives: it renders the body, and when the source
carried a filter predicate it renders that too — with both binder names bound to
the same element — and combines the two per the aggregate.

The fold, which is why this is not an AST rewrite — it needs the dialect's
operator template, and a tree rewrite has no access to one:

```php
/** @param list<Fragment> $parts */
private function fold(string $name, array $parts, array $pos): Fragment
{
    if ($parts === []) {
        return match ($name) {                    // spec §7.3's empty cases
            'ALL' => $this->literal(Value::bool(true), 'BOOL'),
            'ANY' => $this->literal(Value::bool(false), 'BOOL'),
            'SUM' => $this->literal(Value::num('0'), 'NUM'),
            default => refuse('E_SQL_SHAPE', "{$name} over an empty list", $pos),
        };
    }
    if (count($parts) === 1 && $name !== 'JOIN') {
        return $parts[0];
    }
    // Pairwise-left through the operator's own template, which is the path a
    // hand-written chain takes too — so the two produce the same bytes.
    $op = ['ALL' => 'AND', 'ANY' => 'OR', 'SUM' => '+'][$name];
    $acc = array_shift($parts);
    foreach ($parts as $next) {
        $acc = $this->apply('ops', $op, [$acc, $next], $pos);
    }
    return $acc;
}
```

And the relation case, which is a named-slot fill of a skeleton the map owns:

```php
private function skeletonFor(string $name, array $rel, Fragment $body, array $pos): Fragment
{
    $skel = $this->skeleton(self::SKELETON[$name], $pos);   // refuses with the map's reason
    $from = isset($rel['from']['raw'])
        ? $rel['from']['raw'] . ($rel['alias'] ? ' ' . $this->emit->ident($rel['alias']) : '')
        : $this->emit->ident($rel['from'])
          . ($rel['alias'] ? ' ' . $this->emit->ident($rel['alias']) : '');
    $corr = $rel['correlate']['raw'] ?? (string) $this->emit->lex('true');

    return new Fragment(
        $this->fillNamed($skel, ['from' => [$from], 'corr' => [$corr], 'body' => [$body]], $pos),
        self::RETURNS[$name], $this->dialect);
}
```

Nothing here is new machinery: `fillNamed`, `skeleton`, `Emit::ident` and the
part-list splicing all shipped in M2 and are already exercised by `IF`/`COND`.

### 7.10 Three M2 corrections that land with M3

M3's own examples exercise three constructs M2 gets wrong. None is aggregate
machinery — all three are map bugs — but §7.3's `SKU IN ITEMS` and §7.8's
`RMATCH` inherit them, so they are fixed here rather than left for a later pass.

**(a) `IN` compares without the collation, and a collation is not enough.**

```
"OPEN" IN ("open", "held")       SEL: FALSE
    ('OPEN' IN ('open', 'held'))              MariaDB 11.8: 1
```

The `list` variant omits `{textCollate}` where the `scalar` variant has it, and
the `inRelation` skeleton omits it too. Adding the collation fixes that case and
uncovers a second, deeper one:

```
3.0 IN (3)                       SEL: FALSE      (IN is EQL-based, and EQL does
                                                  not normalise numbers)
    (3.0 COLLATE utf8mb4_bin = 3 COLLATE utf8mb4_bin)     MariaDB: 1
```

A collation does not make MariaDB compare two numeric literals as text; it
compares them as numbers and `3.0 = 3`. **The whole `$` family, `EQL` and `IN`
are affected**, because all of them are specified as byte comparisons and all of
them can receive numbers. The fix is a new `textCast` lexical entry applied
alongside `textCollate`:

```jsonc
"textCast": "CAST({0} AS CHAR)"
```
```
"$==":  { "variants": { "text": "({textCast:0}{textCollate} = {textCast:1}{textCollate})" }, … }
```

Verified on 11.8: with the cast, `3.0` vs `3` is `0`, `'A'` vs `'a'` is `0`,
`'OPEN' IN ('open')` is `0`, `3 IN (1, 3)` is `1`, and `CAST(2.50 AS CHAR)` is
`2.50` — the scale survives, which it must, since scale is part of a SEL number.

This is why `textCollate` alone was never sufficient and why the M2 differential
did not catch it: every one of its 73 expressions compared operands the SEL
parser had already given the same form.

**(b) `RMATCH` and `RFIND` silently drop the `i` flag.**

```
RMATCH('^a$', "A", "i")          SEL: TRUE
    ('A' COLLATE utf8mb4_bin REGEXP CONCAT('(?s)', '^a$'))    MariaDB: 0
```

The template names `{0}` and `{1}` only, `Emit::fill` ignores arguments a
template does not mention, and the generator checks only that no slot exceeds
the arity. Three arguments in, two used, no complaint anywhere.

The flag is mappable — `(?si)` works on 11.8 — so the fix is an arity-keyed
template whose three-argument form emits `(?si)`, plus the rule that the flag
must be a **literal**, for the same reason the pattern must be (below).

And the generator gains the check that would have caught it: **a single-string
`tpl` on an entry whose effective arity spans more than one count is an error.**
Either the entry narrows its `arity` or it supplies an arity-keyed `tpl`. That
is a whole-class fix rather than a spot fix, and `FIND` and `SUBSTR` already
have the arity-keyed form it asks for.

**(c) `RMATCH` and `RFIND` emit the author's pattern verbatim.**

Spec §7.8 requires `\d`, `\w` and `\s` to be *rewritten* into explicit ASCII
classes before compiling, precisely because a library flag would otherwise
decide what they mean. MariaDB's engine does not do that rewrite, and forcing a
binary collation does not stop it. Verified on 11.8:

```
SELECT '٣' COLLATE utf8mb4_bin REGEXP '^\d$'   ->  1     SEL says FALSE
SELECT 'é' COLLATE utf8mb4_bin REGEXP '^\w$'   ->  1     SEL says FALSE
```

The fix is not to copy the rewrite into the SQL layer. `Sel\Builtins\Regex`
already has it, and a second copy is a second thing to keep in step. M3 exposes
the existing one:

```php
// Sel\Builtins\Regex — the private validate() made reachable, unchanged.
public static function portableSource(string $pattern, ?array $pos = null): string;
```

and the translator calls it on the pattern before emitting. Two consequences,
both wanted:

- A pattern SEL would reject is refused by the translator with the same
  `E_REGEX_SYNTAX` at the same offset, rather than being handed to a database
  that might accept it.
- **The pattern must be a literal.** A pattern taken from a column cannot be
  rewritten, so `RMATCH(PATTERN_COLUMN, x)` is `E_SQL_UNSUPPORTED`. This is a
  narrowing of what M2 accepted, and what M2 accepted was unsound.

All three are the same failure repeated: **an entry was written to look right
rather than checked against the server.** The M1 probing caught four of these
because it asked MariaDB; these three survived because the M2 differential
compared only expressions whose operands the parser had already normalised. M3's
cases add the asymmetric pairs — `"OPEN"` against `"open"`, `3.0` against `3`,
a pattern with a flag — that the earlier corpus lacked.

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

**A slot records the form its literal is written in, not just its value.** SEL
numbers *are* TEXT values (spec §4), so `Value::num('5.00')` and
`Value::text('5.00')` are the same object, and nothing about a value can say
whether the author wrote `5.00` or `"5.00"`. Only the AST knows — a `num` node
against a `text` node — so the form travels with the slot from the moment the
literal is created.

This is not cosmetic. Emitted bare, `"5.00" $== "5"` becomes `5.00 = 5`, which
the database answers `TRUE` and SEL answers `FALSE`. For a value the *host*
supplies there is no AST to consult, so nothing is guessed there either: a
`value` binding is quoted unless it declares `type: NUM`. Inferring from the
characters would send a product code of `"00123"` to the database as the number
`123`.

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

The rows below marked **(verified)** were checked against a running server by
`sql/oracle/`. The rest are read from documentation and are claims until a
dialect that needs them exists — which is not a formality: the `CHAR`/`CODE` row
of an earlier draft said SQLite's spelling "differs by build", and SQLite turned
out to support both exactly as SEL means them.

| Caveat | What differs |
|---|---|
| — (structural) | **No short-circuit.** `FALSE AND (1/0)` is `FALSE` in SEL; SQL may evaluate both sides and raise. Rules that lean on short-circuiting as a guard change meaning. |
| — (structural) | **NULL.** SEL has no null. Any nullable column makes three-valued logic reachable. Aggregate skeletons use `IS [NOT] TRUE` to fold NULL to false; nothing else does. A translated rule is only as sound as the schema's nullability. |
| `unicode-case` | `UPPER`/`LOWER` are ASCII-only in SEL. MySQL and PostgreSQL apply full Unicode case mapping. **(verified)** SQLite agrees with SEL exactly, so `sqlite.json` overrides the caveat away rather than inheriting it. |
| `division-scale` | SEL's `/` yields ten fractional digits, half away from zero. MySQL's DECIMAL division adds four. **(verified)** PostgreSQL's `/` on integers **truncates** — `10 / 4` is `2` — so `postgresql.json` casts both operands to `numeric`, leaving sixteen fractional digits against SEL's ten. The M1 prediction, measured. |
| `decimal-float` | **(verified)** SQLite has no exact decimal type: arithmetic is int64 or IEEE double. Not a scale difference — a different number system. `0.1 + 0.2` is `0.30000000000000004` there and `0.3` in SEL, and no template fixes it, so every arithmetic entry carries this and `strict` refuses the lot. |
| `rounding-mode` | SEL rounds half away from zero everywhere. **(verified)** PostgreSQL agrees exactly — `round(2.5,0)` is `3` and `round(-2.5,0)` is `-3` — so `postgresql.json` carries no such caveat. MariaDB needs it. |
| `modulo-integer` | **(verified)** SQLite truncates both operands to integers before `%`, so `5.5 % 2` is `1.0` rather than `1.5`. |
| `numeric-scale` | **(verified)** The value is equal and the scale is not. MariaDB's `LEAST(17, 123.456)` is `17.000` where SEL's `MIN` returns `17` — invisible until the result is read as text. |
| `power-float` | `POWER` returns a float in every dialect; SEL's is exact. |
| `text-collation` | The `$` family is bytewise in SEL. The `textCollate` lexical entry forces a binary collation; a column with an incompatible declared collation can still defeat it. |
| `regex-engine` | SEL's regex subset is what PCRE and ECMAScript agree on. MySQL 8.0.4+ and MariaDB use ICU/PCRE, PostgreSQL uses POSIX ARE — the subset mostly survives, lazy quantifiers and some classes do not. SQLite has no `REGEXP` without a user function and refuses outright. |
| `concat-null` | `CONCAT` / `\|\|` yields NULL if any operand is NULL; SEL's `&` cannot. |
| — (refused) | `BAND`/`BOR`/`BXOR` — no portable byte-string bitwise operator exists. `ABORT` — a control-flow effect, not a value. `SPLIT`, `INDEXES`, `BTL`, `RGROUPS` — list-valued. **(verified)** `CHAR`/`CODE` on MariaDB, whose `ORD` and `CHAR` read bytes rather than code points — SQLite's `unicode()` and `char()` are code points and are mapped there. |

### 11.1 A caveat is about a value, not about a type

PostgreSQL will not coerce, and that turns three things this layer had been
getting away with into errors — every one of them a place where SEL's own rule
was being approximated rather than followed:

| SEL says | MySQL and SQLite | PostgreSQL |
|---|---|---|
| `0 != FALSE` is `E_NOT_NUM` | coerce the boolean to `0` and answer `TRUE` | `cannot cast type boolean to numeric` |
| `UPPER(13 + 4)` is `"17"` — numbers *are* text | coerce and answer | `function upper(integer) does not exist` |
| `SIGN(13)` is exact | exact | resolves to the **`double precision`** overload |

The first is now refused, in every dialect, because SEL refuses it. The second is
why every text operand in `postgresql.json` is wrapped in `{textCast:n}` — the
same lexical key the byte-comparison family already uses one level down. The
third is the one worth remembering: it is not a type error and no query fails.
`pg_typeof(sign(13))` is `double precision`, so an expression that reads as exact
quietly stops being exact, and only a server was ever going to say so.

### 11.2 Divergences a caveat cannot express

A caveat says "the value may differ". These two say something else: **a rule that
would *fail* in SEL may quietly *succeed* in SQL.** Both are SQLite; both are
verified; neither is fixable at translation time, because the guard SEL applies
is a run-time check on a value nobody has yet.

| SEL | SQLite |
|---|---|
| `1 / 0` raises `E_DIV_ZERO` | answers `NULL` |
| `"abc" == 1` raises `E_NOT_NUM` | `CAST('abc' AS NUMERIC)` is `0`, so the comparison is simply false |

The `NULL` case is partly contained: an aggregate body folds through
`IS [NOT] TRUE`, and `asCondition` wraps an `UNKNOWN` fragment in `isTrue`, so a
NULL reaching a `WHERE` rejects its row rather than being read as true. A `BOOL`
fragment renders bare, which is correct for every value SEL can produce and
becomes row-rejection for a NULL that SEL could not have produced at all.

There is no containment for the second. It is stated here because the honest
version of "SEL and SQL agree" is "they agree on every value SEL would have
accepted", and a rule whose job is to *reject* bad input is exactly the rule that
notices the difference.

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
((("x"."a" > 0) AND ("x"."b" > 0)) AND ("x"."c" > 0))
===
### name: agg.nested.static-after-inlining
--- dialect
sqlite
--- source
R[1] = (1, 2); R[2] = (3, 4); ALL(R, ROW, ALL(ROW, _ > 0))
--- expect
(((1 > 0) AND (2 > 0)) AND ((3 > 0) AND (4 > 0)))
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

**M1 — the map exists. DONE.** `sql/MAP.md`, `sql/errors.md`, `sql/dialects/ansi.json`,
`mysql-family.json` and `mariadb.json`, `tools/gen-sql-map.mjs`, `tools/check-sql-map.sh`, and the
generated PHP file. No translator yet; the deliverable is that the data is
authored, validated and generated.

**M2 — PHP translates the core. DONE.** Stages 1, 3 and 4 for operators, literals,
`column` bindings, `IF`/`COND`, and the non-aggregate functions. `sql/cases/`
covering `lex.*`, `op.*`, `func.*`, `norm.*`, `refuse.*`; `php/bin/sqlt`;
`impl_sql` wired into `check.sh`.

**M3 — PHP does aggregates. DONE.** Stage 2, all three shapes, the FILTER rewrites,
the `agg.*` cases.

**M4 — PHP + MariaDB end to end. DONE**, and delivered somewhere other than
planned. The gate was "a schema, a rule pushed into a `WHERE`, the same rule
evaluated in PHP over the same rows, and an assertion that the two select the
same ids". That is `sql/oracle/rows.json` and `sql/oracle/fixture-*.sql`, run by
`php/bin/sqlo rows` — a committed, re-runnable check in `tools/check.sh` rather
than an `examples/sql-php.php` nobody would run twice. The first version of it
was an example, and it is the reason `docs/SQL-TESTING.md` §9 exists: it lived in
a scratch directory and its results were cited in three commit messages nobody
could reproduce.

**M4½ — the checks the layer was missing. DONE.** Six items from
`docs/SQL-TESTING.md`: the semantic oracle, its coverage gate, the SQL fuzz lane,
executable documentation, mutation testing, and the emitter's narrowed literal
path. Not in the original plan, and it found seventeen defects in code that had
already passed three reviews.

**M5 — MySQL, PostgreSQL and SQLite maps. DONE.**

MySQL was authored the other way round from every dialect before it: the leaf was
written **empty** — `extends: mysql-family` and nothing else — and the oracle was
asked whether the claim held rather than a person being asked to remember. It
did, on MySQL 8.4.11: the same 223 expressions agree, the same 6 are refused, all
10 row rules agree, and 6,000 generated programs give numbers identical to
MariaDB's. One entry moved *up*: `RMATCH` had been sitting in `mariadb.json`
because at M1 there was no MySQL to check it against, and the family layer means
"verified to agree", not "probably agrees".

That the leaf stays empty is now a check rather than a memory. `php/bin/sqlt`
re-runs every `mariadb` case under `mysql` and requires the same string, the same
error and the same parameters — 186 of them — so an override added to one leaf
and not the other fails the suite. The alternative was a `14-mysql.sqlt` of
copies, two hundred lines asserting that a copy is a copy.

The plan said "data and cases only; the translator does not change. If it does,
that is a bug in the M1 design and should be fixed as one." The translator
changed three times for SQLite, and the plan's own test is the right way to score
them:

- **`arity` was never enforced.** `sql/MAP.md` §4.1 has documented it since M1 as
  "how PostgreSQL refuses the three-argument form its `POSITION` cannot express
  — graceful degradation as data, with no host code involved". There was no host
  code involved and no degradation either: the generator validated the field and
  nothing read it. A bug, exactly as the plan predicted, found the hour SQLite
  was written. It was about to do the same nothing for PostgreSQL.
- **BOOL literals were parameterised.** `1 = '1'` is `0` in SQLite, so
  `TRUE XOR TRUE` answered TRUE in `params` mode and FALSE inline. A bug in M2's
  render model, not new design.
- **`numericLiteral`** is the one genuine addition to the map's surface: one
  lexical key so a dialect can say how it spells a number. SQLite needs it
  because it has no decimal type at all, which is not something ANSI, MySQL or
  PostgreSQL will ask for.

So: two M1/M2 defects the second dialect exposed, and one new key. The prediction
held.

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
