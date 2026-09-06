# Converting the parsers to precedence climbing

**This document has a finite life.** It describes work in progress across
releases 0.4.0–0.7.0, and it is deleted in the 1.0.0 commit that finishes it.
If you are reading it after 1.0.0, it should not exist.

- [Why](#why)
- [The target shape](#the-target-shape)
- [What must not change](#what-must-not-change)
- [The five parity requirements](#the-five-parity-requirements)
- [Per host](#per-host)
- [How to do one](#how-to-do-one)

---

## Why

Four of the five parsers transcribe `spec/grammar.md` one function per
production: `parseSequence` → `parseList` → `parseAssignment` → `parseOr` →
`parseXor` → `parseAnd` → `parseNot` → `parseComparison` → `parseBitOr` →
`parseBitXor` → `parseBitAnd` → `parseConcat` → `parseAdditive` →
`parseMultiplicative` → `parseUnary` → `parsePostfix` → `parsePrimary`. It is a
respectable style and it makes the parser a literal reading of the grammar.

It also costs a stack frame per precedence level per level of nesting, and the
JS and PHP hosts pay for a thunk at each binary helper on top:

| Host | how the sub-parser is passed | frames per nesting level |
|---|---|---|
| JS | arrow thunk `() => this.parseXor()` ×9 | **35**, measured |
| PHP | `fn () => $this->parseXor()` ×9 | 35 |
| C++ | `NodePtr (Parser::*sub)()`, no frame | ~26 |
| Lisp | `#'parse-xor` + `funcall`, no frame | ~26 |
| **Python** | **precedence table** | **6** |

The 35 is not an estimate: parsing `'('.repeat(n) + '1'` on the JS host and
counting `Error.stack` frames gives 44 at one paren and 1409 at forty, dead
linear at 35.0 per level. `E_DEPTH` trips at 100 nested parens, so a transcribed
parser needs ~3500 frames to reach its own limit.

That is invisible in JS, PHP, C++ and Lisp, and fatal in Python, whose default
recursion limit is 1000. It is why `python/sel/parser.py` was written as
precedence climbing rather than ported — and, having been written, it is the
shape the other four should adopt. The payoff is not speed. It is that adding an
operator stops being "a line in two tokenisers, a method in every parser, wired
into the chain in the same place" and becomes **a row in a table**.

## The target shape

`python/sel/parser.py` is the reference. Read its module docstring first; it is
written as the rationale for this document and is not repeated here.

```
parse_program → parse_sequence → parse_list → parse_term → parse_prefix
                                            → parse_postfix → parse_primary
```

- **One integer per precedence level**, higher binds tighter, matching
  `spec/SPEC.md` §5 line for line. `BP_SEQ` and `BP_LIST` exist for
  documentation only — `;` and `,` stay hand-written N-ary loops in
  `parse_sequence`/`parse_list`, outside the table, because they build N-ary
  nodes rather than binary ones.
- **Two tables, not one.** Word operators (`AND`, `EQL`, `BOR`, …) lex as
  identifiers and symbol operators as `op` tokens, so they are looked up
  separately. Same binding powers.
- **Associativity is a three-valued tag**: `L`, `R`, `N`. `L` parses its right
  side at `bp + 1`, `R` at `bp` (that is what makes it right-associative), and
  `N` at `bp + 1` and then rejects a second operator at the same level.
- **Prefix operators carry their own binding power** and are gated on the
  caller's `min_bp`. This is the part that is not textbook — see below.

## What must not change

Everything in `conformance/10-limits.selt` pins the depth arithmetic, and it is
exact:

| Case | Pins |
|---|---|
| `lim.parse-depth` | `E_DEPTH at 1:101` for 100 nested parens ⇒ **exactly two depth increments per paren** (`parse_sequence` + `parse_primary`) |
| `lim.prefix-depth-neg` | `E_DEPTH at 1:200` for a `-` chain |
| `lim.prefix-depth-not` | `E_DEPTH at 1:797` for a `NOT` chain — `NOT ` is four columns wide, so the limit falls at a different column |
| `lim.prefix-depth-does-not-shift-parens` | prefix counting costs a level only when a prefix operator is actually consumed |
| `lim.double-semicolon` | `1;;2` is `E_SYNTAX at 1:3`, which falls out of the trailing-`;` rule |

**The most likely way to get this wrong is to fold prefix operators into
`parse_primary`**, which is where textbook precedence climbing puts them. That
breaks `lim.parse-depth` and `lim.prefix-depth-does-not-shift-parens` at the same
time, and it silently changes what `NOT a == b` parses as.

`conformance/12-misuse.selt` pins node positions through ~140 `at line:col`
expectations. `conformance/04-values.selt`'s `alias.` family pins the `grouped`
flag through `(A) = 1`.

## The five parity requirements

Per host, as an acceptance checklist. Each one produces a *valid parse of the
wrong tree* when it is wrong, so none of them is caught by a compiler.

1. **`NOT` is a loose prefix operator; unary `-` is a tight one.** `NOT` binds at
   7 — looser than comparison at 8, tighter than `AND` at 6 — and `-` binds at
   15. `parse_prefix` accepts each only when the caller's `min_bp` reaches it,
   and falls through to `parse_primary` when it does not. That fallthrough is
   what makes `a == NOT b` and `-NOT x` `E_RESERVED`: `parse_primary` sees a bare
   reserved word. Check `NOT a == b`, `a == NOT b`, `-NOT x`, `NOT NOT x`,
   `a AND NOT b`, `NOT a AND b`, `1 * NOT TRUE`.
2. **Comparison is non-associative**, and the `E_SYNTAX` is reported at the
   *second* operator, not the first and not the expression.
3. **`,` and `;` build N-ary `list`/`seq` nodes.** `dependencies()` walks
   `items`, and `parse_call` flattens a top-level `list` into the argument
   vector — a left-leaning binary tree breaks both, and breaks them quietly.
4. **The `grouped` flag** marks a parenthesised node: it makes `F((1,2))` one
   argument rather than two, and `(A) = 1` an `E_BAD_ASSIGN` while `A = 1` is
   fine.
5. **Node positions.** `bin` and `un` take the *operator* token's position,
   `list` and `seq` take `items[0]`'s, `assign` takes the *target*'s, `index`
   takes the `[`'s.

## Per host

### JS — done

Converted. The nine arrow thunks and both binary helpers are gone; `parseTerm`,
`parsePrefix`, `parsePostfix` and `parsePrimary` are what is left, and the tables
are `Map`s rather than plain objects so that a token spelled like a name on
`Object.prototype` cannot answer for a real operator. `parseSequence`'s
`enter`/`leave` was converged onto the protected form at the same time.

The easiest, and the one whose 35 frames motivated the exercise. The nine arrow
thunks and both binary helpers delete outright; the tables are plain objects.

One wrinkle: `pos` in this host *is* the token object (tokens carry
`line`/`col`/`offset` inline), so table-driven `fail` calls keep passing tokens
rather than a separate `Pos`.

### PHP — done

Converted. The nine `fn () => …` closures and both helpers are gone. `INFIX_WORDS`
is a `private const`; `INFIX_OPS` is built once by a private static method
instead, because PHP has no loop in a constant expression and the assignment and
comparison operators are already named in `ASSIGN_OPS`/`COMPARE_OPS` — writing
them out a second time would be two places to forget one. The create-when-true
`grouped` idiom and its `empty()` reads are untouched. `parseSequence`'s
`enter`/`leave` was converged onto the protected form at the same time.

Structurally identical to JS. The nine `fn () => …` closures and both helpers
delete. The tables can stay `private const` arrays — constant arrays referencing
other class constants are legal:

```php
private const INFIX_OPS = ['+' => [self::BP_ADD, 'L'], /* … */];
```

`in_array($t['value'], self::COMPARE_OPS, true)` becomes
`isset(self::INFIX_OPS[$t['value']])`, which is a speedup as a side effect.

**Preserve the `grouped` idiom verbatim.** PHP creates the key only when it is
true (`Parser.php:345`), so every read is `empty($node['grouped'])`
(`Parser.php:366`, `:425`). A rewrite that starts initialising it to `false`
would be fine; a rewrite that keeps the create-when-true style but reads it with
`$node['grouped']` would emit notices and misbehave.

### C++ — the hardest, and it changes the most

`NodePtr (Parser::*sub)()` and both binary helpers delete entirely; that is the
single biggest deletion in the exercise.

- The table wants a function-local `static const std::map<std::string,
  std::pair<int, char>>`, matching the existing `static const std::set` house
  style at `sel.cpp:1212,1217`.
- **Nodes are `shared_ptr<const Node>`.** `parse_term` must hold a mutable
  `std::shared_ptr<Node>` locally and convert only on return, or it cannot set
  `s`/`l`/`r`. `grouped` still needs the copy at `sel.cpp:1496` for the same
  reason.
- **Do not rename node fields.** `l`/`r` serve `bin`, `index` *and* `assign`;
  `items` serves `seq`, `list` *and* call arguments. Introducing Python's
  `target`/`value`/`obj`/`idx` names means touching `eval`, `check_target` and
  `dependencies` too, and that is a different change.
- The three textually identical local `Leave` RAII structs (`sel.cpp:1354`,
  `:1421`, `:1451`) collapse to two sites and should be factored into one type
  while you are there. C++ has no `finally`; this is the equivalent.

### Lisp

`#'parse-xor` and friends and both helpers delete.

- Build the table with `defparameter` and a hash table, following the existing
  `+assign-ops+` style at `parser.lisp:21-23` despite the `+…+` naming —
  `defconstant` on a hash table signals on reload.
- **`:test #'equal` is mandatory.** Keys are strings; `eql` will silently never
  match and every operator will look unknown.
- `make-node` takes only `(kind pos)`, so the three `parse-term` branches need
  local `bin-node`/`un-node` helpers or they will be several times wordier than
  the Python original.
- `parser.lisp:253` reads the lookahead without a bounds guard, relying on the
  EOF sentinel, where the other four guard explicitly. Keep the sentinel
  assumption or add the guard, but do it deliberately.

### One inconsistency worth settling

`parse_sequence`'s `enter`/`leave` is unprotected in JS (`57`/`65`), PHP
(`99`/`109`) and C++ (`1278`/`1286`), and protected in Lisp (`with-depth`) and
Python (`try/finally`). It is harmless today because a `fail` abandons the whole
parse, but the asymmetry is the first thing a reviewer asks about. Converge on
the protected form.

## How to do one

One host per release, so a regression is attributable to one commit.

1. Convert the parser. Do not touch the lexer, the evaluator or the node shape.
2. `tools/check.sh` — all 600 cases on every host, plus the API probes, the doc
   examples and the fuzzer. The battery is what makes this safe: a parser
   rewrite that keeps every pinned `E_SYNTAX`, `E_DEPTH`, `E_ARITY`,
   `E_BAD_ASSIGN` and `E_RESERVED` position is a rewrite that did not change the
   language.
3. `for s in 1 2 3 4 5; do tools/fuzz.sh 20000 $s; done`. About a third of the
   fuzz corpus is invalid on purpose, and it compares error positions, which is
   exactly what a parser change puts at risk.
4. If something diverges, add the minimal conformance case **before** fixing it.

When the last host is converted, update the correspondence table in
`docs/EXTENDING.md` — it will finally be true at the function level and not just
the file level — rewrite the "Adding an operator" checklist around the table, and
delete this file.
