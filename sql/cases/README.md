# SQL translation cases

The format is `conformance/*.selt`'s sibling, with the sections a translation
needs instead of the ones an evaluation needs. Every host that has a SQL layer
runs this directory and must produce **the same bytes**; that string equality is
the cross-host parity check, and it needs no database to run.

```
### name: category.thing.detail
--- dialect
mariadb
--- bindings
{"TOTAL": {"kind": "column", "table": "o", "column": "total", "type": "NUM"}}
--- source
TOTAL > 100
--- expect
(`o`.`total` > 100)
===
```

| Section | Default | Means |
|---|---|---|
| `dialect` | required | the target to translate to |
| `source` | required | the SEL program |
| `expect` | — | the exact string; mutually exclusive with `error` |
| `error` | — | `CODE` or `CODE line:col` |
| `bindings` | `{}` | JSON, as passed to `Sql::translate` |
| `options` | `{}` | JSON, e.g. `{"strict": true}` |
| `as` | `value` | `value` or `condition` |
| `mode` | `inline` | `inline`, `params` or `debug` |
| `params` | — | the bound values as SEL dumps, comma separated |
| `note` | — | prose, ignored |

`bindings` and `options` are JSON, which `conformance/*.selt` deliberately
avoids. The reason the rule differs here: `.selt` is read by five hosts, two of
which have no JSON parser in their standard library, while this directory is
read only by hosts that have a SQL layer — and every one of those does. The
sections are nested records rather than flat text blocks, so the format that
fits them is the one that nests.

An expectation asserts on **code and position** for errors and on the **exact
string** for output. Messages are never asserted, so they stay free to change
and to be translated.

Categories:

| Prefix | Covers |
|---|---|
| `lex` | quoting and escaping: identifiers, text, numbers, booleans |
| `op` | operators, including variant selection |
| `func` | the mapped functions, per dialect |
| `norm` | stage 1: what inlines and what is refused |
| `kinds` | static kind inference and what it decides |
| `cond` | `IF` and `COND` |
| `mode` | inline, params and debug rendering |
| `dialect` | targets, bases, versions and runtime registration |
| `strict` | caveats made fatal |
| `refuse` | everything that cannot be translated, and where it fails |
| `agg` | the aggregates: unrolling, relations, `JOIN`, element order |
| `bind` | the binding kinds and what each refuses |
| `const` | constant folding, and what counts as knowable |
| `register` | runtime `define` and `defineDialect` |
| `pg` / `sqlite` | what one target does that the others do not |
| `neutral` | that a host's own spelling does not reach the output |
| `pin` | a rendering some other document quotes, so it cannot drift |
| `review` | a finding from an adversarial review, kept as a case |

## How the cases are read

**No host reads this directory.** `tools/gen-sql-cases.mjs` parses every `.sqlt`
file and emits `php/bin/CaseData.php`, `python/bin/case_data.py` and
`js/bin/case-data.mjs`, one per host with a translator, which the runners load as
code — the same relationship `sql/dialects/*.json` has with the
generated dialect map. `tools/check-sql-cases.sh` fails if either generated file
is stale, so editing a case means regenerating:

    node tools/gen-sql-cases.mjs

It works this way because the alternative did not. Each runner used to parse the
files itself and decode the `--- bindings` block with its own JSON parser, and
PHP's decoder represents a JSON object and a JSON array as the same type while
Python's tells them apart — so the two hosts disagreed about what `"items": {…}`
meant, and the two mappings had to be patched separately to agree. One reader
cannot disagree with itself.

A `--- bindings` block is emitted as **constructor calls**, so a malformed one is
refused by the host's own `Binding` constructor rather than by the tool — which
is what the cases asserting `E_SQL_BINDING` are for. A JSON *number* is refused
by the generator itself: PHP's decoder turns a 20-digit integer into a float,
Python keeps it exact, and JS cannot tell `1.0` from `1`, so a number is written
as a string with `"type": "NUM"` beside it.
