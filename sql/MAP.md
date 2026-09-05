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
| `numericCast` | string | template wrapping `{0}` for numeric coercion |
| `isTrue` / `isNotTrue` | string | templates folding SQL's third truth value into two |
| `placeholder` | string | `params`-mode placeholder; `{n}` is the 1-based ordinal, absent for positional `?` |

Three of these are load-bearing rather than cosmetic:

- **`textEscape` carries the backslash entry for MySQL and MariaDB and omits it
  for PostgreSQL and SQLite**, because MySQL treats `\` as an escape inside
  string literals under its default `sql_mode` and the other two do not. Getting
  this wrong is an injection, not a formatting nit, so it is data rather than a
  branch someone can forget to write.
- **`textCollate` is what makes `$==` honest.** SEL's `$` family compares bytes;
  MySQL's and MariaDB's default collation is case- and accent-insensitive, so a
  bare `=` would make `"A" $== "a"` true in the database and false in SEL.
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
| `tpl` | one of `tpl`/`variants` | a template, or an object keyed by argument count |
| `variants` | one of `tpl`/`variants` | named templates chosen by §4.3 |
| `ret` | yes | the static kind produced; §4.4 |
| `arity` | no | `[min, max]`, narrowing SEL's own arity for this dialect |
| `caveat` | no | mapped but inexact; §4.6 |
| `since` | no | requires at least this target version; §4.5 |

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
| `rounding-mode` | rounding is not half-away-from-zero |
| `modulo-integer` | `%` is integer-only |
| `power-float` | `POWER` returns a float |
| `text-collation` | a declared column collation can defeat `textCollate` |
| `regex-engine` | the regex dialect is not SEL's PCRE∩ECMAScript subset |
| `concat-null` | concatenation yields NULL if any operand is NULL |
| `trim-charset` | `TRIM` strips a different character set than SEL's space/tab/CR/LF |
| `length-units` | a length or position is counted in something other than code points |
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
2. Every `target` dialect resolves every `lexical` key in §3.
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

The generator flattens each chain and emits one fully resolved table per
dialect, so a host does no chain walking at all — a lookup is a hash access and
nothing else. Runtime registration re-introduces the chain, and it is the only
thing that does.
