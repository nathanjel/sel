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
| `lazy` | receives argument nodes, not values (§7.1, Lane B in `docs/EXTENDING.md`) |
| `binds` | introduces an element binder (implies `lazy`) |
| `signatures` | the forms as `spec/SPEC.md` writes them, for the generated `docs/BUILTINS.md` |
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
the five renderings and `docs/BUILTINS.md` with it, then define it in every
host. `tools/check-generated.sh` fails while a rendering is stale.
