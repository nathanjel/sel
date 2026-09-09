# The SEL → SQL dialect map

**Normative.** This document defines the format of `sql/dialects/*.json`. Where a
dialect document and this description disagree, `tools/gen-sql-map.mjs` is the
arbiter — it validates every rule stated here at generation time, so a malformed
map never reaches a host.

The design rationale, the translation pipeline these files feed, and the list of
places SEL and SQL genuinely differ are in `docs/SQL-TRANSLATION.md`. This file
is only the format.

---

## 1. Files and the chain

One document per dialect, named for the dialect it declares.

```
ansi ─┬─ mysql-family ─┬─ mariadb        <- the reference
      │                └─ mysql
      ├─ postgresql
      └─ sqlite
```

```jsonc
{
  "dialect": "mariadb",          // must equal the file's basename
  "extends": "mysql-family",     // absent only for "ansi"
  "version": "10.5",             // the minimum server this document assumes
  "target":  true,               // may this be named in a translate() call?
  "lexical": { ... },            // §3
  "ops":     { ... },            // §4, keyed by SEL operator token
  "funcs":   { ... },            // §4, keyed by SEL function name, upper case
  "skel":    { ... },            // §5, multi-part constructs
  "notes":   { ... }             // §6, free prose, not consumed by hosts
}
```

`"target": false` (or absent) marks a **base**: a document that exists to be
inherited and names no real server. `ansi` and `mysql-family` are bases.
Naming one in `translate()` is `E_SQL_DIALECT`, because a dialect no database
implements is not a dialect anyone should be able to aim at.

Every section is inherited **key by key**, not section by section: a leaf that
overrides one function does not restate the other thirty-one. Chains are acyclic
and at most eight deep, and every document must reach `ansi`.

`version` is dotted-numeric. It is compared only against an entry's `since`
(§4.5), and always the *target's* version — a base's version is never consulted,
which is why a version-gated entry belongs in the leaf that has the version.

### 1.1 `ansi` says what the standard says, and nothing else

`ansi` is the root, so it is what a dialect inherits when it overrides nothing,
and `defineDialect(name, ['extends' => 'ansi'])` is the documented way to add a
server this map has never heard of. That makes it a **claim about standard SQL**
rather than a convenient place to put shared defaults, and the two are not the
same thing.

They had drifted apart. Every shipped target overrides most of what `ansi`
declares, so nothing ever ran its version of an entry, and what accumulated
there was the MySQL spelling — the reference dialect's — sitting at the root
where a reader would take it for the portable one. Measured against
PostgreSQL 17:

| `ansi` said | on a conformant server | now |
|---|---|---|
| `CAST({0} AS CHAR)` | `CHAR` is `CHAR(1)`; `'abcdef'` became `'a'`, so `"5.00" $== "5"` was **true** | `CAST({0} AS CHARACTER VARYING)` |
| `CAST({0} AS DECIMAL(38,10))` | 38 digits overflow at `22003`, and a scale of 10 truncated silently | `CAST({0} AS NUMERIC)` |
| `""` (no collation) | linguistic ordering, so `"B" $< "a"` was false where SEL says true | `" COLLATE UCS_BASIC"` |
| `({0} + {1})`, `CHAR_LENGTH({0})`, `({0} \|\| {1})` | SEL numbers **are** text, so operands arrive untyped: `char_length(numeric)` and `integer \|\| integer` do not exist, and `'10' / '4'` is *ambiguous* | operands wrapped in `{textCast:N}` or `{numericCast:N}` |

The through-line is that **standard SQL is strongly typed and MySQL is not.**
SEL's numbers are text (spec §4), so a template is routinely handed an operand
of the "wrong" SQL type; MySQL coerces silently and a conformant server refuses.
Bare templates therefore looked correct for as long as only MySQL inherited
them.

MySQL's spelling did not change — it moved. `mysql-family.json` now states
`UPPER`, `LOWER`, `+`, `-`, `/`, `%`, `NEG` and `LEN` itself instead of
inheriting them, so the emitted SQL for `mariadb` and `mysql` is byte-identical
to before and the deviation is written down where a reader meets both. That is
the rule this section is really about: **a dialect that differs from the
standard says so in its own file.**

`NUMERIC` and `CHARACTER VARYING` carry no precision or length on purpose.
Standard SQL leaves both implementation-defined, and SEL's values have no fixed
width, so any number written here is a ceiling somebody eventually meets —
which is exactly how the `DECIMAL(38,10)` above was found.

None of this changed a shipped target's output: every one of these entries was
either already overridden or was moved to `mysql-family` in the same commit.
What changed is what a *new* dialect inherits.

### 1.2 How that is checked, with no ANSI server to check against

Nobody ships an ANSI server — it is a standard, not a product — so `ansi` is
`target: false` and the semantic oracle, which walks targets, had never
evaluated a single one of its entries. Two lanes cover it now:

- **Strings**, with no server: `sql/cases/20-ansi-fallback.sqlt` registers a
  probe dialect extending `ansi` and pins what it emits.
- **Semantics**, borrowing a connection: `php/bin/sqlo` registers the same probe
  and runs the whole closed corpus through it against **PostgreSQL**, chosen
  because it is the conformant one of the four. SQLite's type affinity accepts
  nearly any cast and MariaDB reads `||` as logical OR, so either would be
  choosing the server that hides the answer. The probe is registered inside
  `sqlo` and nowhere else, so `Sql::dialects()` still answers with the four real
  targets everywhere.

What that establishes is "ansi's entries agree with SEL on the one conformant
engine available", not "ansi is portable". A green run is evidence for Oracle or
SQL Server, not proof.

It also made `trim-charset` a measurement. The caveat says `TRIM(BOTH FROM x)`
strips only the pad character where SEL strips space, tab, CR and LF; every
shipped target overrides the entry, so the caveat had never once fired, and
`caveat_pins` could not ask for it because that gate walks targets too. The
corpus already contained `TRIM("\t\r\n x \n")`, so the probe witnessed it on
its first run without a single corpus line being added.

---

## 2. Entries

Three forms, and the distinction between the last two is the whole of the
graceful-refusal story.

| Form | Means |
|---|---|
| an **object** | supported; see §4 |
| a **string** | refused, and the string is the reason |
| `null` | refused, no reason given |

A string is strongly preferred over `null`. It becomes the message on the
`E_SQL_UNSUPPORTED` a caller sees:

```json
"RREPLACE": "SEL replacement syntax is $0-$9; MariaDB's is \\1, and rewriting one into the other needs the replacement to be a literal"
```

```
E_SQL_UNSUPPORTED at 1:14: RREPLACE has no mapping in dialect mariadb —
SEL replacement syntax is $0-$9; MariaDB's is \1, and rewriting one into
the other needs the replacement to be a literal
```

That message is the entire diagnostic apparatus. There is no second reporting
channel and no `explain()` API; a reason written once, here, next to the
decision it explains, reaches every host and every caller.

**Refusing in a child withdraws a parent's support.** Absent means "ask the
parent"; a string or `null` means "stop, this dialect cannot".

**`null` withdraws wherever it appears, and every lookup tests presence rather
than non-nullness so that it can.** A `null` lexical value withdraws that lexical
entry — §3's "a null `binaryLiteral` refuses BIN literals" is exactly this — and
a `null` at one count of an arity-keyed template withdraws that form, which the
`*` fallback does not rescue. Both were read as *absent* until a cross-host
review asked what a withdrawal actually did: the base's live value was inherited
in its place, so the documented withdrawal was unwritable in both hosts, and on
one of them the `null` reached the renderer and emitted the literal text `None`
into the SQL.

---

## 3. `lexical`

Everything about a dialect that is not a per-operator template. Inherited key by
key. Every key below must resolve for a `target` dialect; the generator checks.

| Key | Type | Means |
|---|---|---|
| `identQuote` | string | the identifier quote character |
| `identEscape` | string | what that character becomes inside a quoted identifier |
| `textQuote` | string | the string-literal quote character |
| `textEscape` | object | character → replacement, applied to every text literal |
| `true` / `false` | string | the BOOL literals |
| `binaryLiteral` | string or null | template for a BIN literal, `{hex}` filled with lower-case hex; `null` refuses BIN literals |
| `textCollate` | string | appended to each operand of the `$` comparison family |
| `textCharset` | string or null | the charset name a dialect spells when it converts bytes to text; `null` where the dialect names none |
| `textCast` | string | template wrapping `{0}` to cast an operand to text |
| `numericCast` | string | template wrapping `{0}` for numeric coercion |
| `numericGuard` | string, **optional** | template wrapping `{0}`, yielding the number or NULL; the only key a target may leave undeclared |
| `binaryCast` | string | template wrapping `{0}` to cast a text or num operand to bytes |
| `isTrue` / `isNotTrue` | string | templates folding SQL's third truth value into two |
| `placeholder` | string | `params`-mode placeholder; `{n}` is the 1-based ordinal, absent for positional `?` |
| `sargablePrefilter` | string | `"true"` if the engine requires a coarse equality prefilter on sargable text equality (`col = 'val' AND ...`), `"false"` where bare equality is exact |

Four of these are load-bearing rather than cosmetic:

- **`textEscape` carries the backslash entry for MySQL and MariaDB and omits it
  for PostgreSQL and SQLite**, because MySQL treats `\` as an escape inside
  string literals under its default `sql_mode` and the other two do not. Getting
  this wrong is an injection, not a formatting nit, so it is data rather than a
  branch someone can forget to write.
- **`textCollate` is what makes `$==` honest.** SEL's `$` family compares bytes;
  MySQL's and MariaDB's default collation is case- and accent-insensitive, so a
  bare `=` would make `"A" $== "a"` true in the database and false in SEL.
- **`sargablePrefilter` controls whether sargable text comparisons emit a coarse
  index prefilter.** MySQL and MariaDB set `"true"` to emit
  `((col = 'val') AND (CAST(col...) = CAST('val'...)))` to enable index seeks
  under case-insensitive collations while preserving exact binary semantics;
  PostgreSQL and SQLite set `"false"` where text comparisons are exact by default.
  Derived dialects inherit this automatically through their `extends` chain.
  When `prefilter: 'separate'` is configured on relation or column bindings,
  `ANY` subqueries on dialects with `sargablePrefilter: "true"` emit separate sibling
  `EXISTS` preconditions conjoined by `AND` to allow query optimizers to plan composite
  indexes before evaluating collation-sensitive residuals.
- **`numericGuard` is what makes `==` honest about data it was not promised.**
  `numericCast` alone answers 0 for `'x'` on three of the four servers, so a
  comparison against 0 matched every row of a text column. The guard tests the
  value first and yields NULL when it is not a SEL number, and NULL is not
  selected. It wraps only an operand the binding did **not** declare `NUM`: a
  declared NUM is vouched for, emits no cast and keeps its index.

  It is the one lexical key a `target` may leave undeclared, and the absence is
  the answer rather than an oversight: `sqlite` has no `REGEXP` and `ansi` has
  no regex, which is already why `funcs.ISNUM` is unmapped on both. A dialect
  that cannot ask the question refuses the translation instead of guessing.

  Its pattern is SEL's own numeral grammar, and it is the same pattern
  `funcs.ISNUM` carries for that dialect — see §7.10.

- **`numericCast` is what makes `==` honest.** SEL's `==` compares numerically
  after aligning scale, so `"5.00" == "5"` is `TRUE`; SQL's `=` between two text
  columns compares text. §4.3 says exactly when the cast is applied.

---

## 4. `ops` and `funcs`

`ops` is keyed by the SEL operator token exactly as `spec/grammar.md` spells it
(`+`, `&`, `$<=`, `AND`, `BAND`, `IN`, `EQL`), plus `NEG` for unary minus.
`funcs` is keyed by the upper-case SEL function name.

The constructs that stage 2 lowers — `IF`, `COND`, `ABORT`, `COUNT`, `INDEXES`,
`HAS`, `ALL`, `ANY`, `MAP`, `FILTER`, `SUM`, `JOIN` — **never appear in `funcs`**.
They are not template-shaped; they use `skel` (§5) or are refused by the
lowering with a message of its own. The generator rejects a document that lists
one of them.

### 4.1 A supported entry

```jsonc
"UPPER":   { "tpl": "UPPER({0})",             "ret": "TEXT", "caveat": "unicode-case" },
"LEN":     { "tpl": "CHAR_LENGTH({0})",       "ret": "NUM"  },
"FIND":    { "tpl": "INSTR({1}, {0})",        "ret": "NUM", "arity": [2, 2] },
"REPLACE": { "tpl": "REPLACE({2}, {0}, {1})", "ret": "TEXT" },
"MIN":     { "tpl": "LEAST({*})",             "ret": "NUM"  },
"SUBSTR":  { "tpl": { "2": "SUBSTRING({0} FROM {1})",
                      "3": "SUBSTRING({0} FROM {1} FOR {2})" }, "ret": "TEXT" }
```

| Field | Required | Means |
|---|---|---|
| `tpl` | one of `tpl`/`variants` | a template, or an object keyed by argument count, with `*` as the fallback |
| `variants` | one of `tpl`/`variants` | named templates chosen by §4.3 |
| `ret` | yes | the static kind produced; §4.4 |
| `arity` | no | `[min, max]`, narrowing SEL's own arity for this dialect |
| `caveat` | no | mapped but inexact; §4.6 |
| `since` | no | requires at least this target version; §4.5 |

An arity-keyed `tpl` must resolve for every count the entry accepts: the
generator lists the counts it does not cover, and an entry with an unbounded
arity must carry a `*`. `MIN` is the case that needs it — `LEAST` and `GREATEST`
are variadic but require two arguments, and SEL's `MIN` takes one:

```jsonc
"MIN": { "tpl": { "1": "{0}", "*": "LEAST({*})" }, "ret": "NUM" }
```

`FIND` and `REPLACE` reorder their arguments, because SEL is
`FIND(needle, hay)` and SQL is `INSTR(hay, needle)`. `arity` on `FIND` is how
PostgreSQL refuses the three-argument form its `POSITION` cannot express while
MariaDB still accepts it — graceful degradation as data, with no host code
involved.

### 4.2 Template syntax

| Form | Fills with |
|---|---|
| `{n}` | argument *n*, zero-based, already rendered |
| `{*}` | every argument, joined with `, ` |
| `{n:}` | arguments *n* onward, joined with `, ` |
| `{key}` | the `lexical` string named `key` |
| `{key:n}` | argument *n* wrapped in the `lexical` template named `key` |
| `{{` `}}` | a literal brace |

`{key}` and `{key:n}` are **validated** by the generator and left in place, and
every host expands them when it fills the template.

Baking them in would be one fewer thing to do at render time and would quietly
break the reason lexical keys exist. `textCollate` is stated once and used by
thirteen comparison entries; an application on a server with a different binary
collation should be able to override that one key and have all thirteen follow.
With the templates pre-expanded the key is gone by then, and the application
would have to re-register every entry that mentioned it.

### Deploying on a connection charset the dialect does not assume

The shipped `mariadb` and `mysql` dialects assume a **utf8mb4 connection**.
On any other, every `$` comparison is MariaDB error **1253**,
`ER_COLLATION_CHARSET_MISMATCH` — reported from the field on utf8mb3.

The cause is not the column. `textCast` emits `CAST({0} AS CHAR)`, and a bare
`CHAR` is in the *connection's* charset; a collation has to belong to the
charset it is applied to. So **`textCollate` must agree with the connection**,
whatever the columns are.

Derive a dialect and override two keys:

```php
Map::defineDialect('cms-mariadb', [
    'extends' => 'mariadb',
    'lexical' => [
        'textCollate' => ' COLLATE utf8mb3_bin',
        'textCharset' => 'utf8mb3',
    ],
]);
```

`version` and `target` are inherited from the dialect being extended, so two
keys is the whole registration. Measured on MariaDB 11.8, `"A" $== "a"`:

| | utf8mb4 connection | utf8mb3 connection |
|---|---|---|
| shipped `mariadb` | answers | **1253** |
| the two keys above | **1253** | answers |

The override is therefore per deployment, not a portability improvement: a
dialect fixed for utf8mb3 is broken on utf8mb4, and vice versa.

#### Deriving it from the connection instead

Which is a good reason not to write the charset down at all. `defineDialect` is
ordinary run-time API, so an application can ask the connection it actually got
and register the matching dialect at start-up:

```php
/**
 * Register a dialect matching the charset this connection actually uses, and
 * return its name. Call once at start-up, before the first translate().
 */
function dialect_for(PDO $pdo, string $base = 'mariadb', string $name = 'app'): string
{
    $charset = (string) $pdo->query('SELECT @@character_set_connection')->fetchColumn();
    // Every charset MariaDB 11.8 ships has a <charset>_bin collation except
    // `binary`, whose binary collation is spelled `binary`.
    $collation = $charset === 'binary' ? 'binary' : "{$charset}_bin";

    Map::defineDialect($name, [
        'extends' => $base,
        'lexical' => [
            'textCollate' => " COLLATE {$collation}",
            'textCharset' => $charset,
        ],
    ]);
    return $name;
}
```

Run against MariaDB 11.8 on both, the same code registers
`utf8mb4_bin` and `utf8mb3_bin` respectively and `"A" $== "a"` answers on each —
the two cells the static form gets wrong become right, and the deployment stops
being able to drift away from its own configuration.

Four things it is worth knowing before using it:

- **Registration is process-global and happens once.** It must run before the
  first `translate()`. Under PHP-FPM that is per request, so either accept one
  extra round trip or pass the charset the application already put in its own
  DSN. Asking the server is the safer of the two: the connection can end up on a
  charset nobody asked for, which is the whole failure being avoided.
- **MySQL family only.** `@@character_set_connection` does not exist on
  PostgreSQL or SQLite, so branch on `$base` if the helper is ever shared.
- **`Map::reset()` drops every registration**, not just this one.
- **Redefinition is last writer wins** (§4.2, deliberately), so two subsystems
  registering the same name will disagree in silence.

Unlike the worked programs under `examples/`, this block is illustrative: no
lane executes it. It was run against the pinned MariaDB on both charsets before
being written down, which is not the same as staying true.

`textCharset` is invisible until `textCast` is also set to `{0}` — the note on
that key recommends it where columns already carry a binary collation — because
until then the outer cast converts `FROM_UTF8`'s result to the connection
charset before the collation applies. Override both anyway: they are one
decision, and splitting them is how the field report happened.

Do **not** reach for `textCollate: ""`. It is legal, it silences 1253, and it
silently restores the case- and accent-insensitive comparison the key exists to
prevent — `"A" $== "a"` becomes true in the database and stays false in SEL.

The generator rejects a template referring to an argument the entry's arity
cannot supply, and a `{key}` naming a `lexical` entry that does not resolve.

### 4.3 `variants`

For operators whose spelling depends on the kinds of their operands. Which
variant applies is **not** in the data — it is one of three fixed selectors,
named by the operator family and implemented identically in every host.

| Family | Variants | Selector |
|---|---|---|
| `==` `!=` `<` `<=` `>` `>=` | `num`, `coerce` | `num` when both operands infer NUM, else `coerce` |
| `$==` `$!=` `$<` `$<=` `$>` `$>=`, `EQL` | `text` | always `text` |
| `&` | `text`, `bin` | `bin` when either operand infers BIN, else `text` |
| `IN` | `list`, `scalar` | `list` when the right operand is a list, else `scalar` |

```jsonc
"==": { "variants": { "num":    "({0} = {1})",
                      "coerce": "({numericCast:0} = {numericCast:1})" },
        "ret": "BOOL" },
"$<": { "variants": { "text": "({0}{textCollate} < {1}{textCollate})" },
        "ret": "BOOL" },
"IN": { "variants": { "list":   "({0} IN ({1:}))",
                      "scalar": "({0}{textCollate} = {1}{textCollate})" },
        "ret": "BOOL" }
```

`IN`'s `list` variant sees a flattened argument vector — the left operand at `0`
and each element of the right from `1` — which is what `{1:}` joins.

A missing variant is a refusal for that shape alone: a dialect that provides
`num` but not `coerce` accepts `1 == 2` and refuses `A == B` on untyped columns.

### 4.4 `ret`

The static kind the entry produces, consumed by stage 3.

`NUM` · `TEXT` · `BOOL` · `BIN` · `UNKNOWN`, or one of two computed forms:

- `@concat` — BIN if any argument is BIN, else TEXT.
- `@unify:i,j` — the common kind of those arguments, `UNKNOWN` if they disagree.

`UNKNOWN` is not an error. It means "ask the database", which for an untyped
binding is the honest answer.

### 4.5 `since`

```jsonc
"RMATCH": { "tpl": "REGEXP_LIKE({1}, {0})", "ret": "BOOL", "since": "8.0.4" }
```

**Reserved, and used by no dialect today.** The mechanism is implemented and
checked — `Translator` compares the entry's `since` against the target's declared
`version` and refuses `E_SQL_DIALECT` below it — but every entry in every dialect
written so far is available in the version that dialect declares, so nothing
exercises it against a real server. It is documented here as the answer for older
servers rather than as something in use: `mysql.json` notes that a MySQL 5.7 leaf
would need most of the regex family gated or refused, and writing that leaf is
what would first make `since` live data. Treat the example below as a shape, not
as a citation.

Below that version, `E_SQL_DIALECT`. Comparison is dotted-numeric and nothing
cleverer. The version compared is always the **target's**, never a base's, so a
version-gated entry belongs in the leaf that declares the version — putting one
in a base makes it depend on whichever leaf inherited it, which is a fact about
the chain rather than about the server.

A **versioned dialect** is the general mechanism for older servers, and needs no
special code anywhere because it is only another link:

```jsonc
{ "dialect": "mariadb-10.1", "extends": "mariadb", "version": "10.1", "target": true,
  "funcs": { "RMATCH": "MariaDB 10.1 has no REGEXP_REPLACE-based anchoring rewrite" } }
```

### 4.5¼ A registered dialect can be a root, and carries only four keys

`defineDialect` takes `extends`, `version`, `target` and `lexical`, and nothing
else. `ops`, `funcs` and `skel` entries are defined one at a time with `define`,
and passing them in the dialect spec is **refused** rather than ignored — it used
to be accepted and silently dropped, which is a registration that looks like it
worked.

`extends` must be **present**, and may be `null` for a dialect with no parent, as
`ansi` has. Present-and-null rather than absent, because forgetting the key is a
typo and must not quietly produce a root that inherits nothing. A root has no
version to inherit, so it must declare one.

That a root is declarable is what makes this true:

> **Anything the shipped map contains, an application could have registered.**

Which is the property a host relies on when it ships its map as generated *code*
rather than as data to be read: the generator emits a sequence of `defineDialect`
and `define` calls, the runtime executes them at start-up, and no file is
deployed alongside the application. 217 calls rebuild the whole shipped map —
the chain preserved, so no dialect repeats what it inherits — and the result is
indistinguishable from the shipped one, entry for entry.

### 4.5½ Registration is checked against this document, at run time

Everything §4 and §5 require of a *shipped* entry, `Map::define` and
`Map::defineDialect` require of a *registered* one — against the same
vocabulary, because `tools/gen-sql-map.mjs` **emits** it into the generated map
as `RULES` rather than each host retyping the list.

That is not tidiness. Nothing checked a runtime registration, so an entry with
no `ret`, a `tpl` that was a JSON list, an `arity` of strings, a `since` of
`"abc"` and a caveat somebody invented were all accepted — and the hosts then
improvised differently over each one, because improvising is what code does when
it has no rule. Two of those improvisations were wrong in *both* hosts at once:
a `textEscape` given as a string made both skip escaping entirely and emit
`'it's'` unquoted, and a `null` lexical value — which §3 documents as a
withdrawal — was read as absent and inherited from the base, so the documented
withdrawal was unimplementable.

A malformed registration raises the **host's startup-error class**
(`LogicException`, `RuntimeError`, …) and never `SqlError`: it is a mistake in
the application's start-up, not a rule that cannot be translated, and
`tryTranslate()` must not swallow it. `sql/cases/18-host-neutrality.sqlt` pins
one case per rule, so every future host inherits them.

### 4.6 `caveat`

Marks an entry that is mapped but does not match SEL exactly. Advisory by
default; fatal under `translate(…, ['strict' => true])`, which turns every
caveated entry into `E_SQL_UNSUPPORTED`.

The vocabulary is **closed** — the generator rejects a name not on this list —
so that `Fragment::$caveats` is something an application can branch on rather
than a bag of prose. Each is described in full in `docs/SQL-TRANSLATION.md` §11.

| Caveat | Short form |
|---|---|
| `unicode-case` | `UPPER`/`LOWER` are ASCII-only in SEL, Unicode-aware in the server |
| `division-scale` | `/` yields a different scale, or truncates |
| `numeric-scale` | the result's decimal scale differs from SEL's, though the value is equal |
| `scale-limit` | the server's decimal type caps the result scale, and a result needing more fractional digits is truncated to it |
| `decimal-float` | the server has no exact decimal type; arithmetic is integer or binary floating point |
| `rounding-mode` | rounding is not half-away-from-zero — **in the vocabulary, declared by no dialect**: `mysql-family` carried it on `ROUND` and was probed not to need it, because `numericCast` means the operands are `DECIMAL` and both servers round `DECIMAL` SEL's way |
| `modulo-integer` | `%` is integer-only |
| `power-float` | `POWER` returns a float |
| `text-collation` | a declared column collation can defeat `textCollate` |
| `regex-engine` | the regex dialect is not SEL's PCRE∩ECMAScript subset |
| `concat-null` | concatenation yields NULL if any operand is NULL |
| `trim-charset` | `TRIM` strips a different character set than SEL's space/tab/CR/LF |
| `length-units` | a length or position is counted in something other than code points — **in the vocabulary, declared by no dialect**: every target's `LEN` counts what SEL counts. A mutation adds it to `ansi`'s `LEN` to prove the check would notice |
| `input-laxity` | the server accepts input SEL rejects, though it agrees on everything SEL accepts |

---

## 5. `skel`

Templates for the multi-part constructs no single entry can express. Placeholders
are named rather than numbered, and values follow §2 exactly: **an object is
support, a string is a refusal carrying its reason, `null` is a refusal without
one.**

A skeleton has no `ret`, no arity and no variants, so `{ "tpl": "…" }` is a
wrapper around its only field and spelling it as a bare string is the obvious
economy. It is also a trap, and the reason the rule is uniform instead: a
refusal is a string too, so a bare-string skeleton would make
`"EXISTS (SELECT 1 …)"` and `"no server spells this the same way"` the same
shape. Neither the generator nor a host could tell them apart, and the failure
mode is a refusal reason emitted into a query as SQL.

| Key | Placeholders | Used by |
|---|---|---|
| `case` | `{branches}`, `{else}` | `IF`, `COND` |
| `caseBranch` | `{cond}`, `{then}` | one `WHEN` of the above |
| `all` | `{from}`, `{corr}`, `{body}` | `ALL` over a `relation` binding |
| `any` | `{from}`, `{corr}`, `{body}` | `ANY` |
| `sum` | `{from}`, `{corr}`, `{body}` | `SUM` |
| `count` | `{from}`, `{corr}` | `COUNT` |
| `join` | `{from}`, `{corr}`, `{body}`, `{sep}` | `JOIN` |
| `inRelation` | `{needle}`, `{from}`, `{corr}`, `{body}` | `x IN relation` |

```jsonc
"all":  { "tpl": "NOT EXISTS (SELECT 1 FROM {from} WHERE {corr} AND ({body}) IS NOT TRUE)" },
"any":  { "tpl": "EXISTS (SELECT 1 FROM {from} WHERE {corr} AND ({body}) IS TRUE)" },
"join": "GROUP_CONCAT does not specify an order without an ORDER BY, and a relation binding has no key to order by"
```

`IS NOT TRUE` rather than `NOT (…)` is the point of care in those two. SQL is
three-valued and SEL is not: if the body is NULL for some row, `NOT (body)` is
NULL, the `WHERE` rejects the row, and `NOT EXISTS` reports "every row satisfies
it" — silently the wrong answer for exactly the case a validation rule exists to
catch. `IS NOT TRUE` folds NULL into false, so a NULL body makes `ALL` false,
which is the conservative reading.

`{corr}` fills with the binding's `correlate`, or with `lexical.true` when the
binding omits one; an uncorrelated relation is a subquery over the whole table,
which is legal and occasionally what you want.

---

## 6. `notes`

Free prose, keyed by anything, consumed by nobody. It is where a decision that
does not fit in an entry's reason string goes, so the next person reading the
document finds it in the document rather than in a commit message.

```jsonc
"notes": {
  "why-no-bitwise": "MySQL's & operates on integers, not byte strings. SEL's BAND is a byte-string operator over BIN of equal length, and there is no portable spelling of that.",
  "trim": "MariaDB's TRIM(BOTH x FROM s) takes one string, not a character set, so the four-character strip SEL specifies needs REGEXP_REPLACE."
}
```

---

## 7. What the generator guarantees

Every one of these is checked by `tools/gen-sql-map.mjs`, so no host has to
defend against a malformed map:

1. `dialect` equals the file's basename; `extends` names a document that exists;
   the chain is acyclic, at most eight deep, and reaches `ansi`.
2. Every `target` dialect resolves every `lexical` key in §3, except
   `numericGuard`, which is optional because two of the four targets cannot
   express it. Nothing else may be optional: a dialect that cannot quote an
   identifier is not a dialect.
3. Every entry is an object, a string, or `null`; every object has exactly one of
   `tpl`/`variants` and a valid `ret`.
4. Every `{n}` in a template is within the entry's effective arity, and that
   arity is within the SEL function's own declared arity.
5. Every `{key}` names a `lexical` entry that resolves for every dialect that
   inherits the template.
6. Every `caveat` is on the closed list in §4.6; every `since` and `version` is
   dotted-numeric.
7. No lowered construct (§4) appears in `funcs`.
8. Every `variants` object uses only the names its operator family defines.
9. Every `skel` key is one of §5's, and every named placeholder in a skeleton is
   one that key defines — a `{frm}` for `{from}` is an error, not literal text.
10. Where a dialect declares `numericGuard`, it carries the same numeral pattern
    as that dialect's `funcs.ISNUM`. They ask the same question, and this map's
    rule is that one place defines a thing; two copies of a numeral grammar is
    the drift that rule exists to prevent.

**Rule 10 at run time.** It is checked when the map is generated, and a
generated map is not the only map: registering a derived dialect is the
documented way to adapt this one to a server, and the generator never sees the
result. So it is checked again, in every host, the first time a dialect's guard
is used — `Map::checkNumericGuard`, called from the one place `numericGuard` is
read. A guard that does not carry what its `ISNUM` tests raises the same
registration error a malformed `define()` does.

Not in `defineDialect`, because registration has no end: `funcs.ISNUM` is
defined one entry at a time, so at the moment a dialect is declared its `ISNUM`
may not exist yet. By the time a guard is being *used*, both sides are
registered.

This one key is checked and the others are not, and the asymmetry is the point.
Every other lexical value fails loudly when it is wrong — a template that cannot
expand raises at expansion. A wrong `numericGuard` fails silently: it emits SQL
that answers where SEL would not, which is the single outcome
[docs/SQL-KINDS.md](../docs/SQL-KINDS.md) exists to rule out. It is not the same
standing as a binding declared `NUM` over a column that is not. That is a claim
the caller makes about their own data; this is a claim about SEL's numeral
grammar, which the caller has no way to check.

Each host's `sqlreplay` registers a dialect whose guard accepts integers only —
narrower than `ISNUM`, so it passes the `2.5` it should stop — and requires the
translation to be refused.

The generator flattens each chain and emits one fully resolved table per
dialect, so a host does no chain walking at all — a lookup is a hash access and
nothing else. Runtime registration re-introduces the chain, and it is the only
thing that does.
