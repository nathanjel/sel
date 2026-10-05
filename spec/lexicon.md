# The lexicon — `spec/lexicon.json`

The reserved words, the operator tokens, the precedence table and what each
operator *is*, in one machine-readable file. Seven lexers, seven parsers and the
optimisers, join pre-filters and SQL translators behind them all need these
facts; before this file each kept its own copy.

**It is not a second authority.** `spec/grammar.md` (tokens, reserved words,
the `assign_op` and `compare_op` productions) and `spec/SPEC.md` §5 (the
precedence table) define the language; this file restates them so that hosts
can be built from them. `tools/gen-lexicon.mjs` refuses to render unless the
grammar's reserved words and token block, its two operator productions, and
every row of §5's table (operators and associativity, tightest first) say what
the lexicon says. Change the spec first.

```jsonc
{ "name": "COMPARE", "fixity": "infix", "assoc": "none", "operators": {
    "==":  { "family": "compare", "relation": "eq" },
    "EQL": { "family": "deep-compare" } } }
```

| Key | Meaning |
|---|---|
| `reserved` | the reserved words, in `spec/grammar.md`'s order. Every word operator is one; the others are the literals `TRUE` `FALSE` `NULL` |
| `punctuation` | symbol tokens that are not operators: `(` `)` `]` |
| `families.<f>` | an operator family: `spec` names the section that defines it, `title` is for the reference page |
| `levels[]` | the precedence levels, **tightest first** (§5's row order). The loosest binds at 1 and each tighter level at one more — the *binding power* every parser climbs by |
| `levels[].fixity` | `infix`, `prefix` or `postfix` |
| `levels[].assoc` | an infix level's associativity: `left`, `right` or `none` (a second operator of a `none` level in the same position is `E_SYNTAX`) |
| `levels[].operators.<token>` | one operator; a token that is an upper-case word lexes as an identifier and must be reserved |
| `.family` | which family it belongs to (one of `families`) |
| `.relation` | for the two comparison families only: `eq` `ne` `lt` `le` `gt` `ge`. Hosts store it as the index in that order |
| `.compound` | for a compound assignment: the binary operator it applies (`+=` applies `+`, §5.8) |
| `.shortCircuit` | the right operand may never run (`AND`, `OR`, `??`, `???`); a dependency walker must not count its reads as definite |
| `.name` | for a prefix operator: the name its parse-tree node records (`NEG`, `NOT`) |
| `.node` | for a postfix operator: `index` or `pipe` (a `.>` step is desugared into a call at parse time) |

The families: `arith` (§5.1), `concat` (§5.2), `compare` (§4.5, numeric),
`text-compare` (§5.3, bytewise), `deep-compare` (§5.4, `EQL` `IN`),
`coalesce` (§5.5), `logic` (§5.6), `bitwise` (§5.7), `assign`, `sequence`,
`list` and `postfix`. An infix operator of the `assign`, `list` or `sequence`
family builds that node; every other infix operator builds a binary node.

What each host does with it: the lexer's token list (`SYMBOLS`, longest first —
two tokens that can both match at a position are one a prefix of the other, so
longest-first is all maximal munch needs) and reserved words come from the
rendering; the parser builds its binding-power table from the infix and prefix
records; and every other place that asks "is this a comparison, an arithmetic
operator, a short-circuit, a byte comparison?" asks the record instead of
keeping a string list. Opcode numbers, node layouts and the operators'
semantics stay each host's own; where a host keeps a native dispatch keyed by
operator (an opcode table, a `switch`), it checks at load time that every
operator here has an entry.

Renderings, all committed: `js/src/_lexicon.mjs`, `python/sel/_lexicon.py`,
`php/src/Lexicon.php`, `cpp/sel_lexicon.hpp`, `lisp/src/lexicon.lisp`,
`go/internal/lexicon/lexicon.go`, `rust/src/lexicon.rs`, and
`docs/reference/lexicon.md` for readers. `node tools/gen-lexicon.mjs` (and
`--check`, run by `tools/check-generated.sh`).
