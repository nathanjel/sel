# The math-operation manifest — `spec/math-ops.json`

Every host compiles pure-arithmetic subtrees into a flat *math plan* (a
three-address program over a scratchpad of decimals) instead of walking the
AST. The plan's *vocabulary* — which operations exist, what source syntax each
one comes from, how many operands it takes and where its errors point — was
retyped in five compilers. This file authors it once.

```jsonc
"ROUND": { "source": { "builtin": "ROUND" }, "arity": 2, "auxPosition": 1 }
```

| Key | Meaning |
|---|---|
| `source.operator` | a binary operator token (`+`, `-`, `*`, `/`, `%`) |
| `source.prefix` | a prefix operator name (`NEG`) |
| `source.builtin` | a builtin call; must exist in `spec/builtins.json` with an arity this operation can serve |
| `arity` | `1` (one operand), `2` (two), or `"fold"`: one or more operands, combined pairwise left to right into one result |
| `auxPosition` | for a two-operand builtin, the argument whose position is the *auxiliary* error position (`ROUND`'s scale, `POWER`'s exponent); the step's own position is the call's |

What is **not** here, on purpose: the plan's loads (`LOAD_VAR`, `LOAD_CONST`,
`LOAD_LEAF`), the opcode *numbers* (Python and PHP count from 4, JS the same,
C++ has an enum, Lisp keywords), the scratchpad layout, the copy-propagation
rules and the arithmetic itself. Those are each host's own; a common binary
encoding was never the goal. The generated table gives each host the source
mapping, the operand counts and the position rule, and each host maps the
symbolic name to its native opcode and checks at load time that every
operation here has one.

Renderings: `js/src/_math_ops.mjs`, `python/sel/_math_ops.py`,
`php/src/MathOps.php`, `cpp/sel_math_ops.hpp`, `lisp/src/math-ops.lisp`, and
`docs/internals/math-ops.md` for readers. `node tools/gen-math-ops.mjs` (and `--check`,
run by `tools/check-generated.sh`).
