# Finishing the C++ and Lisp hosts

**This document has a finite life.** It describes work in progress across
releases 0.4.0–0.7.0, and it is deleted in the 1.0.0 commit that finishes it.
If you are reading it after 1.0.0, it should not exist.

The file is called `PARSER-MIGRATION.md` because converting the parsers is what
it started as. Three hosts finished that, and two more things attached
themselves to the same turn — a host is opened once, and opening it three times
to make three related changes is how the hosts drifted apart in the first place.
The name stays because half a dozen source comments point at it; the scope is
what the next section says.

- [A host's turn: three deliverables](#a-hosts-turn-three-deliverables)
- [Why](#why)
- [The target shape](#the-target-shape)
- [What must not change](#what-must-not-change)
- [The five parity requirements](#the-five-parity-requirements)
- [Rider: the index bracket must count a depth level](#rider-the-index-bracket-must-count-a-depth-level)
- [Per host](#per-host)
- [How to do one](#how-to-do-one)

---

## A host's turn: three deliverables

C++ and Lisp each owe three things. They are listed together because they touch
the same files and want the same review, not because they are one change — land
them as separate commits, in this order.

**1. The index-bracket rider** (see below). Two lines. Do it FIRST, on its own,
before the parser is disturbed: it is the only open cross-host divergence, it is
independent of everything else, and doing it first means the parser conversion is
measured against a host that already agrees with the other three.

```
a[ ×150     js php python  →  E_DEPTH at 1:201
            cpp lisp       →  E_UNDEF_VAR at 1:1     ← this
```

Whichever host goes last also moves the two parked cases into
`conformance/10-limits.selt` and adds the sentence to `spec/SPEC.md` §6.4. Until
then no test guards the boundary in any host, which is the actual cost of
carrying this open.

**2. The parser conversion** — the original subject of this document. Read
[What must not change](#what-must-not-change) and
[The five parity requirements](#the-five-parity-requirements) first; each of the
five produces *a valid parse of the wrong tree* when it is wrong, so none is
caught by a compiler.

**3. The SEL→SQL translator.** `docs/SQL-TRANSLATION.md` is normative;
§14 records what each previous port cost and why. In outline, per host:

- an emitter in `tools/gen-sql-map.mjs` and one in `tools/gen-sql-cases.mjs`,
  each one function plus one line in that file's `OUTPUTS`. **The other hosts'
  generated files must regenerate byte-identical** — that is the check that you
  added a host rather than changed shared data.
- the layer itself, transcribed from `python/sel/sql/` (the JS port used Python
  rather than the PHP original, and 368 of 368 passed on the first full run).
- a `sqlt` runner, transcribed from `python/bin/sqlt`; the three host checks, not
  PHP's five. `oracle` and `sqldoc` stay PHP-only — §14, M7 says why.
- wiring in `tools/impls.sh` (`impl_sql`) and a check in `tools/mutate-sql.py`.
  A check that cannot reach the code under test cannot measure it, so the mutation
  harness needs the new runner before any mutation of the new layer means anything.
- mutations in `sql/mutations.json` for whatever the host had to decide for
  itself. Every host so far has had at least one such decision.

**What the JS port learned that generalises.** Most of its host-shaped decisions
were about JS objects and do not transfer. One does, and it is the one that
changes emitted bytes:

> **An aggregate's elements must live in an insertion-ordered container.** A
> `clist` built by indexed assignment may mix word keys with the positional keys
> `"1"`, `"2"`, … and it must unroll in the order the assignments were written.
> A JS object reorders integer-like keys to the front; **C++'s `std::map` sorts
> every key**, and a Lisp hash table has no order at all. `std::vector` of pairs
> and an alist are the shapes that work.

`conformance/`'s sibling case `agg.clist.mixed-keys-keep-insertion-order` in
`sql/cases/12-aggregates.sqlt` pins this for every host, and
`js-clist-order-through-a-plain-object` in `sql/mutations.json` proves the case
still catches it. Both were written because the JS port needed them; both are
waiting for C++ and Lisp.

*The JS layer is under adversarial review as this is written. Anything it
surfaces that generalises belongs in this subsection.*

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

## Rider: the index bracket must count a depth level

Not part of the conversion, but delivered *with* it host by host, because it
touches the same `parse_postfix` and would otherwise need a second pass over
five files.

An index is the one nesting door that recurses from **outside**
`parse_primary`'s `enter`/`leave` — the `while (at_op('['))` loop sits in
`parse_postfix`. So `a[a[…]]` charged one depth level per nesting where `(`,
`f(` and the prefix operators charge two. The counter and the stack disagreed:

| construct | host frames / level | levels charged | frames per charged level | frames when the guard fires |
|---|---|---|---|---|
| assign | 1 | 1 | 1.0 | 212 |
| neg / not | 2 | 1 | 2.0 | 411 |
| paren | 6 | 2 | 3.0 | 608 |
| call | 7 | 2 | 3.5 | 708 |
| **index, before** | **5** | **1** | **5.0** | **1008** |
| index, after | 5 | 2 | 2.5 | 508 |

(Frame counts are CPython's, measured with the guard lifted. The ratios are a
property of the grammar, not of Python; Python is just the host with the
tightest stack, so it is where the gap shows first.)

At 5.0 frames per charged level, `a[` ×198 reached CPython's 1000-frame limit
before the 200-level guard could fire: the Python host raised `RecursionError`
through its public CLI while the other four returned a clean `E_UNDEF_VAR`. Pure
index nesting is the worst case — every mix (`ABS(a[…])`, `(a[…])`, `a[1;…]`,
`a[-…]`) spends part of the same 200-level budget on cheaper constructs and
stays under 1000.

The fix is one `enter`/`leave` around the bracket, so `[` costs two levels like
`(` and `f(`:

```js
while (this.atOp('[')) {
  const br = this.next();
  this.enter(br);
  try {
    const idx = this.parseSequence();
    this.expectOp(']');
    node = { t: 'index', obj: node, idx, pos: br };
  } finally {
    this.leave();
  }
}
```

This is a **semantic change in every host**, not a Python repair: it moves the
accept/reject boundary from ~198 nestings to 99 and the reported position from
`1:399` to `1:201`. The deepest index nesting anywhere in `conformance/`,
`sql/cases/`, `docs/`, `spec/` and `examples/` is 3 — and that one is JSON
inside a SQL fixture, not SEL indexing; the fuzzer's deepest in 20 000 generated
programs is 4. So nothing real is near the old boundary or the new one.

### Why it lands host by host rather than all at once

Until the last host has it, the hosts disagree above 99 nestings — C++ and Lisp
accept to ~198 where JS, PHP and Python now stop at 99. That is tolerated
deliberately and it is bounded: `conformance/` tops out at 3 levels and
the generator at 4, so neither the suite nor the differential fuzz can see it.
What it does mean is that **the pinning cases cannot go into `conformance/`
yet** — that suite is normative for every host at once, with no per-host
expectations, so a case pinning `1:201` would fail the hosts that have not had
their turn.

They are written and parked here instead, to be added verbatim to
`conformance/10-limits.selt` in the same commit as the **last** host:

```
### name: lim.index-depth
--- note
An index bracket costs a level of the same 200 the parentheses draw on, because
the bracket loop recurses from outside the primary rule that would otherwise
count it. Uncounted it charged one level for five stack frames, and `a[` ×198
exhausted CPython's stack before the guard fired -- a host crash through the
public CLI, while the other four hosts still returned a clean error.

The error lands on the 100th bracket: 99 brackets and their sequences spend 198
levels, the outer sequence one more, and the 100th bracket is the 201st.
--- source
<a[ ×100, then 1, then ] ×100>
--- expect
error E_DEPTH at 1:201
===
### name: lim.index-depth-just-under
--- note
One shorter, and it is an ordinary program -- E_UNDEF_VAR, because `a` is not
bound, which is the point: it got past the parser. Pinning both sides so a host
that counts the bracket twice, or not at all, fails here rather than somewhere
far away.
--- source
<a[ ×99, then 1, then ] ×99>
--- expect
error E_UNDEF_VAR at 1:1
===
```

Per-host status:

- **JS** — done, with the conversion.
- **PHP** — done, with the conversion.
- **Python** — done. It has no conversion coming — its parser was already
  precedence climbing — so waiting for one would have left the only host that
  actually crashed with nothing scheduled. It took the rider on its own. With it
  the worst shape is `call` at 708 frames against CPython's 1000, a 29% margin,
  and `a[` ×50 000 returns a clean `E_DEPTH` through the public CLI.
- **C++** — outstanding, to land with its conversion.
- **Lisp** — outstanding, to land with its conversion. Whichever of these two
  goes last also adds the two cases above to `conformance/10-limits.selt` and
  the sentence to `spec/SPEC.md` §6.4 — §6.4 already says every nesting
  construct is counted and names the index among them, but says nothing about
  what each one *costs*, which is the gap this drifted through.

## Per host

### JS — done

Converted. The nine arrow thunks and both binary helpers are gone; `parseTerm`,
`parsePrefix`, `parsePostfix` and `parsePrimary` are what is left, and the tables
are `Map`s rather than plain objects so that a token spelled like a name on
`Object.prototype` cannot answer for a real operator. `parseSequence`'s
`enter`/`leave` was converged onto the protected form at the same time.

The index-bracket rider above landed here too: `parsePostfix` counts the `[`.

The easiest, and the one whose 35 frames motivated the exercise. The nine arrow
thunks and both binary helpers delete outright.

One wrinkle: `pos` in this host *is* the token object (tokens carry
`line`/`col`/`offset` inline), so table-driven `fail` calls keep passing tokens
rather than a separate `Pos`.

### PHP — done

Converted. The nine `fn () => …` closures and both helpers are gone. `INFIX_WORDS`
is a `private const`; `INFIX_OPS` is built once by a private static method
instead, because PHP has no loop in a constant expression and the assignment and
comparison operators are already named in `ASSIGN_OPS`/`COMPARE_OPS` — writing
them out a second time would be two places to forget one. The index-bracket
rider above landed here too: `parsePostfix` counts the `[`. The create-when-true
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
**This host's three deliverables**, in order:

1. **The index-bracket rider** — the bracket loop needs its own `enter`/`leave`,
   a fourth `Leave` site, or the factored type if you do that first. Land it
   before the conversion, not with it.
2. **The parser conversion** — everything above.
3. **The SQL translator** — `php/src/Sql/` is 6,381 lines and
   `python/sel/sql/` is 5,459; transcribe from the Python. The container warning
   in [A host's turn](#a-hosts-turn-three-deliverables) is aimed squarely at this
   host: `std::map` sorts its keys, so an aggregate's elements need a
   `std::vector` of pairs or they unroll in the wrong order.

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
**This host's three deliverables**, in order:

1. **The index-bracket rider** — `with-depth` around the bracket loop is the
   whole change. Land it before the conversion, not with it.
2. **The parser conversion** — everything above.
3. **The SQL translator** — transcribe from `python/sel/sql/`. A hash table has
   no iteration order, so an aggregate's elements want an alist; see the container
   warning in [A host's turn](#a-hosts-turn-three-deliverables). Whichever of the
   two hosts goes last also moves the two parked conformance cases and adds the
   `spec/SPEC.md` §6.4 sentence.

### One inconsistency worth settling

`parse_sequence`'s `enter`/`leave` was unprotected in JS, PHP and C++ and
protected in Lisp (`with-depth`) and Python (`try/finally`). It is harmless
either way, because a `fail` abandons the whole parse, but the asymmetry is the
first thing a reviewer asks about. JS and PHP converged onto the protected form
as part of their conversions; **C++ is the last one left**, and should converge
when it is converted.

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
