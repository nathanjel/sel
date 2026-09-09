# SEL → SQL translation

**Status: M1–M5 are built and reviewed — MariaDB, MySQL, PostgreSQL and SQLite, each verified against a running server, then put through a five-lane contract review that reproduced twenty-four violations the suite had reported clean (see §14 and §11.5). M6 is built too: **PHP and Python emit the same SQL for all cases**, which is what makes "the map is data" a measurement.** This document is the
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
  "placeholder":  "?"          // every dialect so far; see §9
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
"MIN":   { "tpl": "LEAST({*})", "ret": "NUM", "caveat": "numeric-scale" }
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

// One entry. Same record shape as the JSON. Slots are ZERO-based.
Map::define('mysql', 'funcs', 'GEO_NEAR', [
    'tpl' => 'ST_Distance_Sphere({0}, {1}) < {2}',
    'ret' => 'BOOL',
]);

// Withdraw one, for a deployment where it is unavailable.
Map::define('mysql-5.7', 'funcs', 'RMATCH', null);

// The escape hatch, for what a template cannot say. A builder receives the
// emitter and the already-rendered arguments, and returns a Fragment.
Map::defineBuilder('postgresql', 'funcs', 'LEN', fn (Emit $emit, array $args) =>
    new Fragment(['length(', ...$args[0]->parts, ')'], 'NUM', $emit->dialect()));
```

Splice the argument's **`parts`**, never its rendered SQL. A part list is
strings alternating with parameter slots, so splicing keeps a bound value bound;
flattening it to a string first would inline whatever the argument carried and
quietly turn a prepared statement back into concatenation.

Two host differences worth knowing before you write one. A builder is invoked
with three positional arguments — JS and PHP may declare fewer and ignore the
rest, Python and Common Lisp must accept all three. And the Lisp host passes the
**dialect** where the others pass an emitter, because every emit function in
that host takes a dialect as its first argument, so the dialect *is* the emitter
there; it also has no exported Fragment constructor, so a builder must use
`sel.sql::%fragment`.

A working builder in all five hosts is in
[examples/dialect/](../examples/dialect/), which `tools/check-examples.sh` runs.

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
a `Binding` saying where that name lives in the schema. Any name still read after
stage 1 that has no binding is `E_SQL_UNBOUND`. Nothing is inferred; a rule that
reads `TOTAL` against a schema that never heard of it must fail loudly, not
guess a column.

**A Binding is built by a constructor, in code. It is never decoded from a
document, and no host in this project parses one.** That is not a style
preference; it is the fix for a defect class the layer actually shipped. The API
used to take a nested map shaped like JSON and validate that shape by hand in
each host — and PHP's decoder represents a JSON object and a JSON array as the
same type while Python's tells them apart, so `items` given as an object was
accepted by one host and refused by the other, `from` given as an array was
refused by one and spliced into an identifier by the other. Neither answer was a
decision anybody made. A constructor makes the whole class **unrepresentable**
rather than refusable:

```php
$bindings = [
    'TOTAL' => Binding::column('total', 'o', 'NUM'),
    'ITEMS' => Binding::relation('order_items', 'oi',
        fields: ['QTY' => Binding::column('qty', type: 'NUM')],
        correlate: '`oi`.`order_id` = `o`.`id`'),
];
```

```python
bindings = {
    'TOTAL': Binding.column('total', 'o', 'NUM'),
    'ITEMS': Binding.relation('order_items', 'oi',
        fields={'QTY': Binding.column('qty', type='NUM')},
        correlate='`oi`.`order_id` = `o`.`id`'),
}
```

An application whose bindings come from a schema file, a config or an ORM
**generates these calls** — the same relationship `sql/dialects/*.json` has with
the generated dialect map, and the same one `sql/cases/*.sqlt` has with the
generated case tables. One program reads the source; every host loads code.

The checks live in the constructor *bodies* rather than in parameter types, and
that is deliberate: PHP would enforce a `string` parameter and refuse an array
with a `TypeError`, but Python's annotations enforce nothing at run time, JS has
no types to declare and Lisp's are advisory. A guarantee written as a signature
is a guarantee three of the six hosts do not make. Written in the body it is the
same refusal, with the same `E_SQL_BINDING` code, everywhere — and `SqlError` is
the class an application catches, where a `TypeError` is not.

Four kinds.

### 5.1 `column` and `raw`

```php
'TOTAL'    => Binding::column('total', 'o', 'NUM'),
'NAME'     => Binding::column('customer_name', type: 'TEXT'),
'TYPEPATH' => Binding::column('typepath', 'cms_entry', 'TEXT', exact: true),
'NOW'      => Binding::raw('CURRENT_TIMESTAMP', type: 'UNKNOWN'),
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

#### Index Sargability, Collation, and Numeric Guards

Relational database optimizers require expressions over indexed columns to be *sargable* (search-argument-able) to perform B-tree index seek and range scans rather than falling back to full table scans. `Binding::column` and `Binding::raw` provide metadata parameters to declare schema properties:

- **`exact: bool = false`**: Declares that the underlying column or expression already provides exact binary comparison semantics (e.g. `_bin` or binary collations, byte-identical ASCII, UUIDs). When true:
  - String equality, inequality, and ordering comparisons (`$==`, `$!=`, `$<`, `$<=`, `$>`, `$>=`) omit defensive `CAST(... AS CHAR)` and `COLLATE` wrapping.
  - The translator emits bare comparisons (`col = 'val'`), enabling index seeks across MariaDB, MySQL, PostgreSQL, and SQLite.
  - In `x IN ("a", "b")` expansions over literal lists, each comparison is emitted uncast (`((col = 'a') OR (col = 'b'))`), enabling B-tree index range scans.
- **`sargable: bool = false`**: For case-insensitive columns (e.g. MySQL `_ci` collations):
  - On MariaDB and MySQL, emits a coarse equality prefilter combined with the exact binary check: `((col = 'val') AND (CAST(col AS CHAR) COLLATE utf8mb4_bin = CAST('val' AS CHAR) COLLATE utf8mb4_bin))`. The database query engine uses the index for the coarse equality prefilter to discard non-matching rows, executing the exact collation check only on candidate rows.
  - On PostgreSQL and SQLite, where text equality is already exact by default, emits clean bare equality (`col = 'val'`).
- **`guard: bool = false`**: Forces regex validation and safe numeric casting (`CASE WHEN col REGEXP ... THEN CAST(col AS DECIMAL) ELSE NULL END`) even when the binding is declared `NUM`. This protects against engine errors (such as MariaDB error 1292: `Truncated incorrect DOUBLE value`) when querying heterogeneous or dirty EAV string columns.
- **`collation: ?string = null`**: String alias for collation configuration:
  - `'binary'` or `'exact'`: sets `exact = true`.
  - `'sargable'` or `'prefilter'`: sets `sargable = true`.
  - `'default'` or `'none'`: default behavior.
- **`prefilter: string = 'inline'`**: Controls whether coarse index prefilters remain inline or can emit separate sibling preconditions in aggregate subqueries:
  - `'separate'` (or boolean `true`, alias `splitSargable: true`): on engines where `sargablePrefilter: true`, marks the coarse prefilter to be planned as a separate precondition where supported.
  - `'inline'` (or boolean `false`, default): keeps coarse prefilters inline with exact residual checks.


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
    'prefilter' => 'separate',     // 'separate' | 'inline', or boolean true/false (alias splitSargable)
],
```

- `from` may instead be `['raw' => '(SELECT a, b FROM t WHERE …)']`, which is how
  a binding "carries a query".
- `correlate` is the join back to the outer row. Omit it for an uncorrelated set.
- `fields` names what `BINDER["qty"]` resolves to. A key that is not in `fields`
  is `E_SQL_BINDING` — again, no guessing.
- `scalar` says what a bare `BINDER` means, so `ALL(ITEMS, _ > 0)` has something
  to compare. Absent, a bare reference is `E_SQL_SHAPE`.
- `prefilter` (`'separate'` | `'inline'`, with aliases `true` / `false` and `splitSargable: true`)
  controls how sargable conditions inside `ANY` are emitted:
  - When `'separate'` (or configured on a referenced field without relation override): on engines
    with `sargablePrefilter: true` (MariaDB, MySQL), `ANY` emits two sibling `EXISTS` subqueries
    conjoined by `AND`:
    `(EXISTS (SELECT 1 FROM rel WHERE corr AND coarse_prefilter) AND EXISTS (SELECT 1 FROM rel WHERE corr AND body IS TRUE))`
  - The first subquery carries clean, unadorned index-friendly conditions without `IS TRUE` or defensive
    wrappers (conjoining all exact and coarse conditions through `AND`, e.g. `((g.fname = 'f_group') AND (g.value = 'news'))`).
    MariaDB and MySQL optimizers recognize these direct equality conjuncts to plan composite B-tree indexes
    (such as `(fname, value, cmsid)` on EAV tables) and decorrelate the subquery into a semijoin (`LooseScan`),
    dramatically pruning candidate rows before evaluating residual collation expressions in the second subquery.
  - On engines with `sargablePrefilter: false` (PostgreSQL, SQLite), sargable equality does not
    produce coarse prefilters, emitting a single clean `EXISTS` subquery without duplication.
  - An explicit `prefilter: 'inline'` on the relation overrides any column-level `'separate'`
    preference, keeping all checks in a single subquery.

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
    ((CAST(`o`.`sku` AS CHAR) COLLATE utf8mb4_bin IN (SELECT CAST(`oi`.`sku` AS CHAR) COLLATE utf8mb4_bin FROM `order_items` `oi` WHERE `oi`.`order_id` = `o`.`id`)) IS TRUE)
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

A relation nested inside **itself** is refused separately, when the binder frame
is pushed rather than up front: the aliases are equal, so the check above sees
one alias and not two, and the inner `{corr}` would name the outer row while
reading the inner one. `ALL(ORDERS, O, ALL(ORDERS, O, …))` is `E_SQL_SHAPE`
with its own message. Two *different* relations with distinct aliases nest
correctly and are unaffected — §7.3 above is that case.

### 7.4 The binder environment

A stack of frames, `name => binder`, consulted **before** the bindings map —
the same precedence `Sel\Context::lookup` gives an aggregate binder over a
variable. A binder is one of three things, matching the three shapes:

| Binder | `_` resolves to | `_["k"]` resolves to | `_K` |
|---|---|---|---|
| a static element | the element's AST node, re-entered | indexing that node | its key, as TEXT |
| a column | that column reference | `E_SQL_SHAPE` | the ordinal, as TEXT |
| a relation row | the relation's `scalar` field — but `E_SQL_SHAPE` if the relation declares more than one field, and `E_SQL_SHAPE` if it declares none | the named field | **`E_SQL_SHAPE`** |

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

A bare `_` over a **multi-field** relation is refused for a different reason,
and it is the same defect as the multi-field `IN` in §7.7 reached through the
binder instead of the operator. A row of two or more fields is a *map* in SEL,
not one value, so `ANY(ITEMS, _ $== "AB-1000")` compares a map against text —
structurally false for every row, so `[]` in SEL — and every one of the four
servers answered `[1]`, because the `scalar` field silently stood in for the
row. A one-field relation genuinely is a scalar and still works; for anything
wider, name the field. `scalar` remains what a *one-field* relation may declare
so a bare reference reads well, not a projection of a wide one.

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
| `HAS(x, k)` | `TRUE`/`FALSE`, decided at translation time | **`E_SQL_SHAPE`** | `FALSE` |
| `INDEXES(x)` | `E_SQL_SHAPE` — yields a list | same | same |

`HAS` needs a **constant** key, for the same reason indexing does: which column
it asks about has to be known before the query runs.

`HAS` over a relation used to answer from the declared fields, and that answered
a different question in both directions. A relation is a *list of rows*, so its
keys are `"1"`, `"2"`, … and never a field name — which is how the row oracle's
own context builds it. `HAS(ITEMS, "QTY")` was `TRUE` where SEL says `FALSE`,
and `HAS(SKUS, "1")` was `FALSE` where SEL says `TRUE`. The positional direction
cannot be answered by an expression at all, because it needs the row count, so
refusal is the only honest outcome for either and the whole row is refused.

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
the binding did not say.

It used to mean "ask the database", which for an untyped binding was called the
honest answer: `UNKNOWN` where `BOOL` was required was allowed, and `UNKNOWN` in
a numeric comparison selected the `coerce` variant. Measured, the database does
not know — `1 AND TRUE` is TRUE on MariaDB and `CAST('x' AS DECIMAL)` is 0 — so
both of those matched rows SEL refuses. Neither holds now: an `UNKNOWN` operand
in a numeric position is wrapped so a non-number becomes NULL, and in a boolean
position it is refused. See [SQL-KINDS.md](SQL-KINDS.md), which is the statement
of that rule.

**`isTrue` is no longer read by the layer at all.** It stays in the map because
it is published vocabulary and a registration may still name it, but the `all`,
`any` and `inRelation` skeletons carry `IS TRUE` / `IS NOT TRUE` in their own
templates, and `Fragment::asCondition` stopped wrapping.

This paragraph used to say the opposite, and used to defend it. `asCondition`
wrapped an `UNKNOWN` fragment in `IS TRUE`, an `UNKNOWN` operand inside the tree
was emitted bare, and wrapping every interior operand was "considered and
rejected" on the grounds that folding NULL to false replaces one wrong answer
with another. The reasoning was sound about NULL and wrong about everything
else: `IS TRUE` folds a NULL to false, but it does not fold a *number*, and
`1 IS TRUE` is TRUE on MariaDB where SEL raises `E_NOT_BOOL`. So the wrap did not
make an undeclared column safe to use as a condition; it only made it look
handled.

There is nothing to wrap it in, either — no dialect can ask "is this a boolean",
because in the MySQL family a boolean *is* a `TINYINT` and testing `IN (0, 1)`
would admit a NUM column SEL refuses. An undeclared column in a boolean position
is now refused. Declare the binding `BOOL`.

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
a literal that needs quoting, whether it came from a `text` node or from a
`kind: value` binding, becomes a slot with its `Value` recorded in order.

```
parts:  ['(`o`.`total` > 100 AND `o`.`state` = ', SLOT 1, ')']
params: [Value::text('open')]
```

**A NUM, BOOL or BIN literal is not a slot.** `Fragment::isInline` says so, and
the reason is that *neither carries any character the caller chose* — there is
nothing for a placeholder to protect, and both are damaged by being sent as a
string.

For NUM, no coercion of a bound string reproduces a bare numeric literal. MariaDB
reads `2.50` as `DECIMAL` with scale 2 and `12345678901234567890.12345` as
`DECIMAL` with 25 digits; a parameter is untyped, and every way of giving it a
type picks the wrong one. `CAST(? AS DECIMAL(65,10))` pads the scale, so
`TRIM(2.50)` answered `"2.5000000000"`. `(? + 0)` drops the scale and floats
above seventeen digits. With neither, `(? = ?)` compares two strings and
`2.50 = 2.5` is FALSE. After `Emit::numericLiteral` the characters a NUM literal
can contain are digits, one `.` and a leading `-`, by construction.

For BOOL, the token comes out of `lexical.true`/`lexical.false` — out of the
dialect document, not out of a rule. Binding it as a string breaks SQLite
outright: `1 = '1'` is **0** there, so `TRUE XOR TRUE` answered TRUE in `params`
mode and FALSE inline. Found by the fuzz lane on SQLite's first run. TEXT is the
kind with a quoting problem, and TEXT is what gets a slot.

Joining is the last thing that happens, and there are three ways to do it:

| Mode | Slot becomes | Result |
|---|---|---|
| `inline` (default) | the dialect-quoted literal | ``(`o`.`total` > 100 AND `o`.`state` = 'open')`` |
| `params` | the dialect's `lexical.placeholder` | ``(`o`.`total` > 100 AND `o`.`state` = ?)`` plus the array |
| `debug` | `~1~`, `~2~`, … | ``(`o`.`total` > 100 AND `o`.`state` = ~1~)`` |

Every dialect written so far spells the placeholder `?`, PostgreSQL included:
`$1` is the *protocol's* numbering and PDO does not use it. It stays a lexical
key because a host that talks the wire protocol directly would need `$n`, and
the numbering the part list already carries is exactly what such a spelling
needs.

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
    public string $kind;      // NUM | TEXT | BOOL | BIN | LIST | UNKNOWN
    public string $dialect;
    /** @var list<string> */
    public array $caveats;    // which inexactnesses were accepted

    /** Usable as a condition. A declared BOOL, and nothing else. */
    public function asCondition(string $mode = 'inline'): string;

    /** Usable in a select list, GROUP BY, ORDER BY, HAVING. Any kind but LIST. */
    public function asValue(string $mode = 'inline'): string;

    /** The bound values for `params` mode, in placeholder order. */
    public function bindings(): array;
}
```

`inline` is the default because it is what makes §13.1's byte-exact suite
possible and what a human debugging a rule wants to see. An application issuing
the query should pass `params` and hand `bindings()` to the driver.

`asCondition()` on a `NUM`, `TEXT` or `UNKNOWN` fragment is `E_SQL_SHAPE` —
UNKNOWN since the kind warrant, §8 above. That is the one
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
| `E_SQL_DIALECT` | the dialect itself is wrong: no such name, or a base like `ansi` that no server runs. Also mapped-but-too-old, when an entry's `since` is above the dialect's declared version — no entry declares one today |
| `E_SQL_UNBOUND` | a variable with no binding |
| `E_SQL_BINDING` | a malformed binding, an unknown field, an alias collision |
| `E_SQL_ASSIGN` | an assignment or sequence stage 1 refuses |
| `E_SQL_SHAPE` | a list where a scalar is required, `_K` on a relation, a non-BOOL condition, an aggregate over something untranslatable |
| `E_SQL_INVALID` | every argument is a literal and SEL rejects the expression — see §11.4 |
| `E_SQL_DEPTH` | the expression nests deeper than SEL will evaluate, at the evaluator's own `MAX_DEPTH` — see §11.4 |

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
| `rounding-mode` | SEL rounds half away from zero everywhere. **No dialect declares this**, and that was measured rather than assumed: `mysql-family` carried it on `ROUND` and did not need it. The operands reach `ROUND` through `numericCast`, so they are `DECIMAL`, and both MySQL and MariaDB round `DECIMAL` half away from zero — `ROUND(-2.5,0)` is `-3`, `ROUND(2.675,2)` is `2.68`, agreeing with SEL on every probe. The caveat was written for the `FLOAT` behaviour the cast means these operands never have. Removing it was a **coverage gain**: it had been exempting every `ROUND` in the corpus from the exactness check. The name stays in the vocabulary because the divergence is real in general. |
| `trim-charset` | `TRIM` strips exactly space, tab, CR and LF in SEL, and ANSI `TRIM` strips spaces only. Declared on `ansi` and **inherited by no target**: all four override it with SEL's exact set — `mysql-family` through `REGEXP_REPLACE`, PostgreSQL through `btrim(…, E' \\t\\r\\n')`, SQLite through `trim(X, Y)`. `ansi` is not a target, so nothing runs it and the symmetry check never asks. It is the base being a base: the conservative answer, overridden everywhere a real server was probed. |
| `input-laxity` | The server accepts input SEL rejects. `DECODE_BASE64("aGVsbG8")` — unpadded — is `E_BAD_ARG` in SEL and a value on MySQL and PostgreSQL. Structurally unwitnessable, for the reason §11.5 gives. |
| `length-units` | In the vocabulary and used by nothing: every dialect's `LEN` counts what SEL counts. Kept because a dialect whose `LENGTH` is bytes is the obvious next one to be written, and a mutation exists that adds it to prove the check would notice. |
| `modulo-integer` | **(verified)** SQLite truncates both operands to integers before `%`, so `5.5 % 2` is `1.0` rather than `1.5`. |
| `numeric-scale` | **(verified)** The value is equal and the scale is not. MariaDB's `LEAST(17, 123.456)` is `17.000` where SEL's `MIN` returns `17` — invisible until the result is read as text. It is also the only caveat a **skeleton** carries: MariaDB gives a `CASE` over NUM branches one type and pads to the widest scale, so `IF(FALSE, 2.50, 3)` is `3.00` there and `3` in SEL and on MySQL 8.4. That is `mariadb.json`'s first and only override, and it is the reason `skel` entries reach `Fragment::caveats` at all — before it, a skeleton could declare an inexactness that `strict` never saw. |
| `scale-limit` | **(verified)** DECIMAL caps the scale of a product and SEL does not, so a result needing more fractional digits is truncated to the cap — to zero, when every surviving digit is one. `0.00000000000000000000000000000001 * 2` is `2e-32` in SEL and on MariaDB and `0.000000000000000000000000000000` on MySQL 8.4: the boundary is **30** there and **38** on MariaDB. It sits on `mysql-family` because both truncate; only the digit they stop at differs. `+` and `-` do not truncate on either, and `/` already declares `division-scale` for the same shape of loss. PostgreSQL's `numeric` has no such cap and carries no caveat, so a multiplication defect still fails the build there. |
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

A caveat says "the value may differ". These say something else: **a rule that
would *fail* in SEL may quietly *succeed* in SQL** — or, once, the reverse. All
are verified. None is fixable by a template, because the guard SEL applies is a
run-time check on a value the translator does not have.

| SEL | The server |
|---|---|
| `1 / 0` raises `E_DIV_ZERO` | MariaDB, MySQL and SQLite answer `NULL`; PostgreSQL raises |
| `"abc" == 1` raises `E_NOT_NUM` | SQLite's `CAST('abc' AS NUMERIC)` is `0`, so the comparison is simply false |
| `CHAR(0)` is the NUL code point | PostgreSQL's `chr(0)` raises *null character not permitted*: its `text` cannot hold one at all |

`CHAR(0)` is the one that runs the other way — SEL has an answer and the server
refuses — and it is here rather than fixed for the same reason as the others.
It is not a caveat, because a caveat is about a value and this is about failure.
Refusing `CHAR` outright would cost every legitimate use of it to prevent one
input. And §11.4's check does not reach it, because that check asks SEL whether
the expression is valid and SEL says it is.

The first two rows have shrunk twice since they were written, and §11.4 is why.
First, where both operands are literals, `1 / 0` became a refusal rather than a
translation, and so did `LEFT("abc", " 2")`. Then the constant check learned that
a scalar `value` binding is a literal the host wrote down, and that stage 1's
assignments are constant when their right-hand sides are — so `V / 0` with
`V` bound as `{kind: value, value: 1}` is refused too, and so is
`A = 1 / 0; TRUE` — even though stage 1 drops a definition nothing reads. **What survives here is the same divergence with a
column in it, and nothing else.** A column is the one operand whose value this
layer genuinely cannot know.

The `NULL` case is partly contained, and the word doing the work is *partly*.
An aggregate body folds through `IS [NOT] TRUE`, and `asCondition` wraps an
`UNKNOWN` fragment in `isTrue`, so a NULL reaching a `WHERE` rejects its row
rather than being read as true. A `BOOL` fragment renders bare, which is correct
for every value SEL can produce and becomes row-rejection for a NULL that SEL
could not have produced at all.

Inside the expression, there is no containment and none is attempted — see §8.
`A AND B` over two nullable columns renders `` (`a` AND `b`) `` and the server's
three-valued logic decides. That is not an oversight to be fixed by wrapping
every interior operand: SEL's answer for a `NONE` operand is `E_NO_SCALAR`, an
*error*, so folding NULL to false inside would be a second wrong answer dressed
as a fix. The boundary is where a two-valued answer is forced and therefore
where folding is honest; the inside is a divergence, and this row is it.

There is no containment for the second. It is stated here because the honest
version of "SEL and SQL agree" is "they agree on every value SEL would have
accepted", and a rule whose job is to *reject* bad input is exactly the rule that
notices the difference.

`IN` deserves its own note. SEL's `IN` is `EQL`-based and therefore structural —
it compares kind, scalar bytes and children. SQL's `IN` is a value comparison
under a collation. For scalar operands under `textCollate` the two agree, and
that is the only case the translator accepts: `IN` where either side has
children is `E_SQL_SHAPE`.

### 11.3 Where numbers stop being exact

Three of the four servers have a boundary SEL does not have, and all three are
declared rather than discovered. They are collected here because they are the
same fact in three spellings — *the target's number type is finite* — and
because each is only visible far outside the range a validation rule normally
reaches, which is exactly why none of them was noticed until a corpus was
written that went looking.

| Dialect | Exact through | Beyond it |
|---|---|---|
| MariaDB | 38 fractional digits | a product truncates to 38 — `scale-limit` |
| MySQL 8.4 | 30 fractional digits | a product truncates to 30 — `scale-limit` |
| SQLite | 19 significant digits (int64) | arithmetic *and comparison* become IEEE double — `decimal-float` |
| PostgreSQL | — | `numeric` is unbounded; no caveat, and it is the reason the other three can carry one |

SQLite's comparison row is the one worth reading twice. `CAST('2.50' AS NUMERIC)
= CAST('2.5' AS NUMERIC)` is exactly right and stays right through nineteen
digits; at twenty, both sides become doubles and
`99999999999999999999 = 99999999999999999998` is true. The template is not
wrong and no template is right, so the six numeric comparison entries carry
`decimal-float` and `strict` refuses them. The byte comparisons — `$==`, `$<`
and the rest — are separate entries with no cast, and `IN` casts to text, so
none of those loses exactness.

The cost of a caveat is that the oracle stops *failing the build* on that entry
for that dialect; it still compares and still reports. What makes that
affordable is having four dialects: PostgreSQL declares neither of these
caveats, so a defect in the shared code behind multiplication or comparison
still turns the build red there. A three-dialect corpus could not have accepted
either caveat this cheaply.

### 11.4 The translator asks SEL first, where it can

For four milestones the translator answered *can this be pushed into that
database* without ever asking *is this a SEL expression*. `LEFT("abc", -1)` is
not one — SEL raises `E_RANGE` — and it translated cleanly into all four
dialects:

| | answer |
|---|---|
| SEL | `E_RANGE` |
| MariaDB, MySQL, SQLite | `''` |
| PostgreSQL | `'ab'` |

None of them SEL's, from a translation that reported success. Seventeen
expressions behaved this way, and they did not agree with each other either —
`SUBSTR("abc", -1)` is `'c'` on three servers and `'abc'` on PostgreSQL,
`ROUND(1.5, -1)` is `0` on three and `2` on SQLite, `LEFT("abc", 2.7)` is
`'abc'` on three and `'ab'` on SQLite. That is the core promise inverted: a
translation that passes is supposed to mean the contract holds.

Where every leaf of a subtree is a literal, the answer is knowable at
translation time, so the translator hands the subtree to **SEL's own evaluator**
and refuses what SEL refuses, with SEL's code and SEL's position:

```
E_SQL_INVALID at 1:13: SEL rejects this expression (E_RANGE: LEFT argument 2
must not be negative), so there is nothing to translate; a database would
answer something rather than fail
```

Four properties of the check, each of which is a decision:

- **It is validation, not constant folding.** The value is computed and thrown
  away. An expression that passes emits the SQL it always emitted — `LEFT("abc",
  1 + 1)` is still `LEFT('abc', (1 + 1))`. Folding would have been the tempting
  next step and would have blinded the oracle: an expression replaced by its
  answer no longer exercises the server's version of the operation, which is the
  only thing `sql/oracle/` exists to compare.
- **It runs after the node translates, not before.** Every refusal the
  translator already had keeps its own message. `TRUE + 1` is an expression SEL
  rejects *and* a BOOL where a number is required; the second is the sentence an
  author can act on, and it is the same sentence `FLAG + 1` gets, where no value
  is known. `ABORT` and an unportable regex are refused by name on the way past
  and never reach the check.
- **It reuses `Evaluator::evalNode`.** There is one copy of SEL's argument
  rules, in the evaluator, and this asks it. A second copy in the translator
  would be a second thing to keep in step with the spec, and the defect being
  fixed here is precisely what happens when the translator has its own opinion
  about what SEL means.
- **A constant subtree is checked wherever it appears, including where SEL's
  own laziness would never reach it.** `FALSE AND (1 / 0 > 0)` is refused, even
  though SEL answers `FALSE` without dividing and both MariaDB and PostgreSQL
  were asked and short-circuit it too. The alternative was to check only the
  outermost constant node, and that made `FALSE AND (1 / 0 > 0)` translate while
  `F AND (1 / 0 > 0)`, with a column beside the same division, refused — one
  expression, two answers, decided by whether the operand next to it happened to
  be written down. §11.2's first row is the other half of the argument: SQL does
  not promise not to evaluate the branch it does not take.
- **A value binding is a constant, and so is an assignment.** The check began
  as "every leaf of this subtree is a literal", which read the source and not
  the inputs. A scalar `value` binding is a literal the *host* wrote down — the
  translator has the `Value` in hand — so `V / 0` is refused when `V` is bound
  to `1`, and the name is lifted into a `Context` the evaluator is handed.
  Stage 1's assignments inherit it, and are checked in `Normalise::record`
  *before* `substitute` — which matters most for a definition nothing reads.
  Stage 1 drops those, so `A = 1 / 0; TRUE` translated to `TRUE` and all four
  servers answered `TRUE` where SEL raises at the assignment. After substitution
  the subtree is gone and there is nothing left to check. `column`, `columns`
  and `relation` bindings are not constants and never become ones.
- **It costs what evaluation costs.** `REPEAT(REPEAT("x", 3000), 3000)` is a
  constant subtree and translating it now builds the nine-million-character
  string once. Measured at parity with evaluating the same expression, and
  bounded by the same limits, but it is no longer true that translation is
  cheap regardless of what is being translated.

**What it cannot do is check a value it does not have.** `LEFT("abc", N)` where
`N` is a column translates, because a column's value is not knowable here and
will not be until the query runs. Everything else that once looked unknowable
has been folded in — literals from the start, then host-supplied `value`
bindings, then assignments over both.

That used to be stated as "the residual is exactly one thing: a column", and it
was not true, in two different ways.

The first is fixed. The check ran only where the *whole* node was constant, so
one column anywhere in a node switched it off for every constant inside it:
`T + (1 + "x")` refused and `(T + 1) + "x"` — the same expression, differently
parenthesised — translated, and `CAST('x' AS DECIMAL)` is `0` on MariaDB, MySQL
and SQLite. A constant in a numeric operand position is now asked on its own,
wherever it sits, because what it settles does not depend on the rest: `"x"` is
not a number whatever the column holds. Pinned as the `const.numeric.*` family.

The second is not fixed, and is a real divergence rather than an unknowable
one. `LEFT(col, -1)` raises `E_RANGE` in SEL for *every* value of `col` — the
offending argument is the `-1`, which is written down — and it still translates.
Closing it needs SEL's own per-builtin argument checks, not the numeric-operand
test above, because `-1` *is* a number and only `LEFT` knows it may not be
negative. Pinned as `const.residual.argument-constraint-beside-a-column` so it
is a recorded gap rather than an oversight.

`-N` where `N` is a column is not a constant either, however much it looks like
`-1`: the test is about leaves, not about shape. Both that and `LEFT(col, -1)`
are pinned as cases in `sql/cases/16-constants.sqlt` so the residual is a
recorded decision rather than an oversight.

### 11.5 What the checks cannot see

The sections above list divergences the checks found. This one lists the shapes
of divergence the checks are structurally unable to find, which is a different
and less comfortable list. It was written after a review round that reproduced
twenty-four contract violations against a suite reporting zero differences, and
every one of them is an instance of something here.

**The closed corpus measures the map, not the bindings.** `sql/oracle/` compares
SEL against four servers over 391 expressions and finds them identical, and that
is a real result about `sql/dialects/*.json`: the templates are right. It is not a result about the translator, because the corpus is closed
by design — an expression, no host input — and **sixteen of the review's
twenty-four defects needed a binding**. A multi-field relation compared against
text. `HAS` over a relation. A column bound `raw` with a type nobody checked.
`COUNT` of a row. None of those is expressible as a closed expression, so a
corpus of closed expressions can run clean for a milestone while they sit there.
`sql/oracle/rows.json` exists for this reason and is the direction to extend when
a new binder shape lands, not the expression corpus.

The other eight are the more useful half, because each names a *different* blind
spot: four needed a byte string that could distinguish a right cast from a wrong
one, two needed a literal newline in a line-oriented file, and two needed a map
entry that did not exist when the corpus was written.
`docs/history/SQL-TESTING.md` §2 has the table.

**A corpus of symmetric inputs cannot see an asymmetric bug.** This is §4 of
`docs/history/SQL-TESTING.md` and it keeps recurring in new clothing. The sharpest
instance: the closed corpus contained exactly one BIN value, `7ac3a9`, and it is
valid UTF-8 — which made it the one byte string that could not distinguish a
correct `binaryCast` from a missing one, because a wrong cast round-trips it
unchanged. The value was chosen to look like a hash, and looking like a hash and
being a *discriminating* hash are unrelated properties.

**A caveat is one-directional, and the second direction had to be bolted on.**
A caveat says "this entry may disagree", which tells the oracle not to fail. It
does not say the entry *does* disagree, so declaring one is free, silences every
check on that entry, and changes no output character — the exact profile of a
defect nothing can see. `caveat_symmetry` in `php/bin/sqlo` is the other
direction: every declared caveat must be *witnessed* by an expression that
actually crosses it, or excused by name. `rounding-mode` on `mysql-family` was
found this way — declared, never witnessed, and on inspection not needed at all,
because `numericCast` means the operands are `DECIMAL` and both servers round
`DECIMAL` SEL's way.

The same question asked of a *guard* rather than a caveat has the same answer and
a different instrument. A mixed-BIN/TEXT check added during the review turned out
to be unreachable — `requireComparableKinds` already refused every input that
could reach it — and what said so was its mutation surviving: break the guard on
purpose, and every check stays green because the guard was never the thing
refusing. It was removed and the dual purpose documented on the check that does
the work. A guard nothing can reach is a caveat nothing witnesses, one layer
down.

**Two divergences are unwitnessable by construction, and are excused rather than
checked.** `concat-null` and `input-laxity` are refusal-class divergences wearing
a caveat's clothes. `concat-null` says concatenation yields NULL if an operand
is NULL — but SEL has no null, so the operand is a `NONE` and `N & "!"` is
`E_NO_SCALAR`. `input-laxity` says the server accepts input SEL rejects. In both
cases SEL has *no answer to disagree with*, so no expression can put the two
sides in opposition; this is §11.2's territory and not §11.3's. They stay
declared because an application branching on `Fragment::caveats` still wants to
be told, and they are named in `sql/oracle/coverage.json` under
`caveats_unwitnessable` with the reason — which is itself mutation-tested, so
adding a name there to silence a complaint is not free.

**And a check that has never failed is a claim, not a result.** The slot
invariant was committed, trusted, and did not fire when the bug it existed for
was reinstated by hand. `tools/mutate-sql.py` exists to ask "would we know?", and
it has since caught the mirror check going vacuous, the coverage gate recomputing
its own denominator, and — in the most embarrassing instance — itself, reporting
four mutations as "caught by sqldoc" while `sqldoc` was red on the unmutated
tree and would have reported anything as caught. It now refuses to run on a red
baseline.

---

## 12. Using it

PHP, the shape the first end-to-end proof takes:

```php
<?php
require __DIR__ . '/../php/src/bootstrap.php';
require __DIR__ . '/../php/src/Sql/bootstrap.php';

use Sel\Sel;
use Sel\Sql\Binding;
use Sel\Sql\Sql;

$program = Sel::compile('TOTAL > 100 AND ALL(ITEMS, I, I["qty"] > 0)');

// dependencies() says what must be bound: ['ITEMS', 'TOTAL']
$bindings = [
    'TOTAL' => Binding::column('total', 'o', 'NUM'),
    'ITEMS' => Binding::relation('order_items', 'oi',
        fields: ['QTY' => Binding::column('qty', type: 'NUM')],
        correlate: '`oi`.`order_id` = `o`.`id`'),
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

Python — the same call, the same bindings, the host's spelling:

```python
import sel
from sel.sql import Binding, Sql

bindings = {
    'TOTAL': Binding.column('total', 'o', 'NUM'),
    'ITEMS': Binding.relation('order_items', 'oi',
        fields={'QTY': Binding.column('qty', type='NUM')},
        correlate='`oi`.`order_id` = `o`.`id`'),
}

program = sel.compile('TOTAL > 100 AND ALL(ITEMS, I, I["qty"] > 0)')
frag = Sql.try_translate(program, 'mysql', bindings)
if frag is None:
    ...                       # fall back to the evaluator
cur.execute(f'SELECT o.id FROM orders o WHERE {frag.as_condition()}')
```

The two are the same calls in two spellings, and `frag.as_condition()` is byte
for byte the SQL shown for PHP. That is not a claim about care taken: it is
`sql/cases/*.sqlt` run under two runners, from one generated table that
`tools/check-sql-cases.sh` keeps current.

---

## 13. Tests

Two layers, and they test different things.

### 13.1 Translation cases — `sql/cases/*.sqlt`

Line-oriented, `.selt`'s sibling, with two extra sections. These assert the
**exact string**, which is what makes them a cross-host parity check: if PHP and
Python emit different SQL for the same input, the suite says so, and no database
is needed to find out.

**Two runners read them now** — `php/bin/sqlt` and `python/bin/sqlt` — and both
report `xxx passed, 0 failed`. Each host parses the file format itself, because a
parser shared across five languages is not a thing that exists, so
`tools/check-sql-cases.sh` diffs what each one *loaded*: the `at` and the name of
every case, in file order, byte for byte. Comparing the counts would not do —
two parsers can lose and gain a case each and agree on the total, and a runner
that silently reads 338 passes 338 and prints a green line nobody reads twice.

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

## 14. History

The delivery sequence, the alpha criteria, what was ruled out of scope and the
decisions taken are in **[history/SQL-DELIVERY.md](history/SQL-DELIVERY.md)**.
They described work to be done; the work is done, and this document describes
what exists. The reasoning is kept in full rather than summarised — it is why
the layer is shaped the way it is.

The analysis that produced the test strategy — seven classes of check that could
not fail, and what was built to fix each — is in
**[history/SQL-TESTING.md](history/SQL-TESTING.md)**.
