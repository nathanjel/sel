# The builtin manifest — `spec/builtins.json`

One entry per builtin, keyed by its upper-case name, sorted. It is the one
place a builtin's *shape* is authored: the parts of the function table that
every host must agree on before a program is even run. Bodies stay native, in
each host's `builtins/`; custom host registration (`register`, `register_builtin`,
`Registry::define` outside the shipped table) is not described here.

```jsonc
"LINK": {
  "arity": { "min": 3, "max": 5, "allowed": [3, 5],
             "message": "LINK takes 3 or 5 arguments, got {count}" },
  "lazy": true,
  "binds": true,
  "signatures": ["LINK(left, right, pred)", "LINK(left, right, L, R, pred)"],
  "spec": "§7.4"
}
```

| Key | Meaning |
|---|---|
| `arity.min`, `arity.max` | the accepted count range; `max` is a number or `"variadic"` |
| `arity.allowed` | the exact counts accepted inside the range (`[3, 5]`), when not every count in it is |
| `arity.parity` | `"odd"` or `"even"`: every count in the range with that parity |
| `arity.message` | the E_ARITY message for a count the extra rule refuses; `{count}` is the count. Required with `allowed`/`parity`, forbidden without |
| `lazy` | receives argument nodes, not values (§7.1, Lane B in `docs/contributing.md`) |
| `binds` | introduces an element binder (implies `lazy`) |
| `pipeline` | a pipeline step (`.>` chains it; argument 1 is the rows): `{ "keepsRows": bool, "sorts": bool }` — whether every row it returns is one of its input rows, unchanged (fewer, or reordered), and whether it sorts. See "Classification" |
| `regex` | a regex builtin: `{ "pattern": i, "flags": j }`, the argument indexes of its pattern and of its optional flags |
| `sql` | how the SQL translators type its arguments: `numericArgs` (indexes that must be numbers, or `"all"` for a variadic builtin), `binArg` / `boolArg` (`true`: a BIN / BOOL argument is accepted rather than refused) |
| `yieldsList` | its result is a list (or a record) whatever its arguments; every pipeline step has it |
| `signatures` | the forms as `spec/SPEC.md` writes them, for the generated `docs/reference/builtins.md` |
| `spec` | the section of `spec/SPEC.md` that defines it |

What each host does with it, at startup and natively — no JSON is read at run
time: `define` looks the name up in the generated table (`js/src/builtins/_manifest.mjs`,
`python/sel/builtins/_manifest.py`, `php/src/Builtins/ManifestData.php`,
`cpp/sel_builtins_manifest.hpp`, `lisp/src/builtins/manifest-data.lisp`),
refuses a definition whose min/max/lazy/binds disagree with it, and installs
the extra arity rule from it — so `COND`'s odd count and `LINK`'s three-or-five
are written once, here. When registration is complete the host also refuses to
start with a manifest name it never defined. A host therefore cannot drift
from this file; it can only fail to load.

Adding a builtin: add its entry here, run `node tools/gen-builtins.mjs`, commit
the five renderings and `docs/reference/builtins.md` with it, then define it in every
host. `tools/check-generated.sh` fails while a rendering is stale.

## Binding forms

A builtin with `binds` also declares `forms`: one entry per accepted argument
list, in the order the evaluator tries them.

```jsonc
"SORT_BY": {
  "forms": [
    { "roles": ["source", "key"],                                   "binds": ["_", "_K"] },
    { "roles": ["source", "key", "outer"],  "when": { "arg": 2, "is": "text" }, "binds": ["_", "_K"] },
    { "roles": ["source", "binder", "key"], "when": { "arg": 1, "is": "name" }, "binds": ["_K"] },
    { "roles": ["source", "key", "outer"],                          "binds": ["_", "_K"] },
    { "roles": ["source", "binder", "key", "outer"],                "binds": ["_K"] }
  ]
}
```

| Key | Meaning |
|---|---|
| `roles` | one per argument: `source`/`outer` are evaluated where the call is; `binder` is a bare name, never evaluated; `body`/`key`/`proj`/`pred` run inside the binder scope |
| `when` | `{ "arg": i, "is": "name" \| "text" }`: this form applies when argument *i* is a bare (ungrouped) name or a text literal; the last form for a count has no guard |
| `binds` | the implicit names bound inside (`_`, `_K`, `_1`, `_2`); every `binder` argument's own name is bound as well |

Every count the arity accepts has a form. Hosts render the forms into the same
tables as the rest of the manifest and expose one classifier
(`bindingForm` / `binding_form` / `Registry::bindingForm` / `binding_form()` /
`BINDING-FORM`) that the dependency walker and the SQL layer's stage 1 use —
so "which argument runs inside the binder, and what does it see" is decided in
one place per host, from one authored table.

Rust and Go also render each form's `roles` (C++, JS, PHP, Python and Lisp
derive what they need from the scopes), so a host can decode "which argument
is the key, which the direction" from the form it matched instead of
re-deriving it.

## Classification

The facts that are not about arity but that the evaluator, the optimiser, the
hybrid planner and the SQL translators all classify builtins by. Before they
were here, every host kept its own lists — four of them in one host — and
nothing compared the copies.

```jsonc
"SORT_BY": { "pipeline": { "keepsRows": true, "sorts": true }, "yieldsList": true, … }
"RREPLACE": { "regex": { "pattern": 0, "flags": 3 }, … }
"ROUND": { "sql": { "numericArgs": [0, 1] }, … }
```

- **`pipeline`** is the pipeline vocabulary: the optimiser unwinds a chain of
  these, the planners split one, and `keepsRows` is what lets a rewrite assume a
  row is still the source's (a FILTER, a sort, TAKE, DROP, DISTINCT and DEDUPE
  return input rows; MAP, SELECT_COLS, BUCKET and the LINKs build new ones).
  `sorts` marks the steps whose order a later step can lose in SQL.
- **`regex`** says where a regex builtin takes its pattern (checked at compile
  time when it is a literal, §7.8) and its flags (read then for `i`).
- **`sql`** is the SQL translators' argument typing, measured against SEL
  rather than designed: a builtin's numeric arguments, and the few that take a
  BIN or a BOOL where the translator otherwise refuses one (`sql/oracle/`
  re-measures them).
- **`yieldsList`** marks the builtins whose result is a list, so the scalar
  rule does not apply to them in SQL.

The generator enforces the shapes (a sort keeps its rows; a step yields a
list; flags are the last, optional argument; only a strict builtin's
arguments are typed), and each host renders them as native tables beside the
rest of the manifest.
