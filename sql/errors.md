# SEL → SQL translator error codes

Normative registry for the translator. Codes are **stable identifiers**;
messages are not — but unlike `spec/errors.md`, the message here is expected to
be read. A translator error is not a bug report, it is an answer: *this rule
cannot be pushed into this database, and here is what stopped it.* The
`sql/cases/*.sqlt` suite asserts on code and position only, so messages stay
free to change and to be translated.

These codes are deliberately **not** in `spec/errors.md`. They are translator
failures, not language failures. A SEL program that cannot be translated is not
a wrong program — it is a program that will be evaluated the ordinary way.

Every error carries the same four fields a `SelError` does:

| Field | Meaning |
|---|---|
| `code` | one of the identifiers below |
| `message` | human text, written to be acted on |
| `line`, `col` | 1-based, in code points, of the node that failed |
| `offset` | 0-based code point offset from the start of source |

There is one class, `SqlError`, and one message. No diagnostics object, no
second reporting channel, no `explain()` API.

---

## Codes

| Code | Raised when |
|---|---|
| `E_SQL_DIALECT` | the named dialect does not exist, is a base rather than a target, or is below an entry's `since` |
| `E_SQL_UNSUPPORTED` | an operator or function has no mapping in this dialect — or has one carrying a `caveat` while `strict` is set |
| `E_SQL_UNBOUND` | a variable is read that the bindings do not name |
| `E_SQL_BINDING` | a binding is malformed, names an unknown field, or collides with another relation's alias |
| `E_SQL_ASSIGN` | stage 1 refuses an assignment or a sequence: compound assignment, reassignment, a read before the write, an assignment inside an aggregate body or a call argument, a non-constant index on the target, or a non-assignment before the result expression |
| `E_SQL_SHAPE` | a list where a scalar is required; `_K` inside a relation body; a non-BOOL where a condition is required; an aggregate over something that is neither a list, a `columns` binding nor a `relation` binding; `asCondition()` on a non-BOOL fragment |

---

## Where the message comes from

For `E_SQL_UNSUPPORTED`, from the map itself. A refusal in `sql/dialects/*.json`
may be spelled as a string, and that string is the reason (`sql/MAP.md` §2):

```json
"CRC32": "PostgreSQL has no built-in CRC-32; pgcrypto's digest() offers other algorithms, not this one"
```

produces

```
E_SQL_UNSUPPORTED at 1:1: CRC32 has no mapping in dialect postgresql —
PostgreSQL has no built-in CRC-32; pgcrypto's digest() offers other
algorithms, not this one
```

Writing the reason next to the decision is what makes one class and one message
sufficient. The alternative — a generic "unsupported" plus an API to go and ask
why — puts the explanation somewhere the person who made the decision will never
look again.

For every other code the message is written at the raise site, and it names the
thing that was wrong rather than the rule that was violated: `ITEMS is bound as
a column, so ALL cannot iterate it` rather than `bad aggregate source`.

---

## Refusal is an ordinary outcome

```php
Sql::translate($program, $dialect, $bindings, $options): Fragment    // throws
Sql::tryTranslate($program, $dialect, $bindings, $options): ?Fragment  // null
```

`tryTranslate` catches `SqlError` and nothing else — a bug in the translator
must not be swallowed by the code path that exists to handle expected refusals.
Use it in an application's hot path, where "this one stays in PHP" is the
answer. Use `translate` when you want the reason: during development, in a
build-time audit of a rule set, or in a test.
