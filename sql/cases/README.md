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
| `kind` | static kind inference and what it decides |
| `cond` | `IF` and `COND` |
| `mode` | inline, params and debug rendering |
| `dialect` | targets, bases, versions and runtime registration |
| `strict` | caveats made fatal |
| `refuse` | everything that cannot be translated, and where it fails |
