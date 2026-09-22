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
| `plan` | — | a **planner** case: `pure_sql`, `hybrid`, `pure_memory` or `refused` |
| `tables` | — | with `plan`: the physical sources, one per line, first-use order; empty means none |
| `note` | — | prose, ignored |

A case with `--- plan` asks `plan_hybrid` rather than `translate` (see
docs/SQL-TRANSLATION.md §12.1). The runner asserts the classification, the
`tables`, and for `pure_sql` and `hybrid` that `expect` is the prefix
statement; a `pure_memory` plan has no `expect`, and a `refused` plan has
`error` and nothing else — the code alone, since planning refuses only on the
bindings or the dialect (a base dialect, an alias collision), which blame no
node of the rule, so the error carries no position and the generator refuses
one written; a refusal that blames a place in the program is a translate
case. A program stage 1 refuses, including one past the depth limit, is a
`pure_memory` plan rather than a refusal (`plan.pure-memory.*`). Every
planner case also checks that the continuation
is present exactly when the classification says so, and that the program's
AST is the same tree after planning and after the physical optimiser as
before — which is how the fixtures see an optimiser that writes into its input.

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
| `plan` | the hybrid planner's contract: classification, physical sources, prefix, immutability |

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
