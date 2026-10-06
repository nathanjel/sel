# Contributing

How to change SEL itself — a builtin, an operator, the optimiser, a new host —
and what to watch out for while doing it. Extending SEL *from an application*,
with host functions and SQL dialects of your own, needs none of this: see
[Extending SEL](extending.md).

Read [the invariance traps](#the-traps) before you write anything. Every one of
them has already caught someone on this codebase. [How parity is achieved and
guaranteed](parity.md) is the background.

- [The rule](#the-rule)
- [Where everything lives](#where-everything-lives)
- [Adding a function](#adding-a-function)
- [The Args API](#the-args-api)
- [The Value API](#the-value-api)
- [Adding an operator](#adding-an-operator)
- [Writing conformance cases](#writing-conformance-cases)
- [The traps](#the-traps)
- [Changing the optimiser or the planner](#changing-the-optimiser-or-the-planner)
- [Running the checks](#running-the-checks)
- [Writing documentation](#writing-documentation)
- [Adding an implementation](#adding-an-implementation)

---

## The rule

**Spec first, then tests, then every host, then `tools/check.sh`.**

```
spec/            say what it does
conformance/     write the cases (they will fail)
js/src/          implement
php/src/         implement
cpp/sel.cpp      implement
lisp/src/        implement
python/sel/      implement
go/sel/          implement
rust/src/        implement
tools/check.sh   all green, or it isn't done
```

Never implement in one host and "port it later". The implementations exist to
disagree with each other; that only works if they arrive together. A feature that
lives in one host for a week is a feature nobody has tested.

No implementation is the reference. When they disagree, `spec/` and
`conformance/` decide which is wrong.

---

## Where everything lives

The hosts share one layering, so they can be read side by side, but not one file
layout: JS, Python and Lisp are file for file alike, C++ is one translation unit
(its column names the section comment, `// --- decimal`, in `cpp/sel.cpp`), and
PHP, Go and Rust group some layers differently. The table below is the actual
map; keep it true when you move code.

| Concern | JS | PHP | C++ (`cpp/sel.cpp`) | Lisp | Python | Go | Rust |
|---|---|---|---|---|---|---|---|
| Errors | `js/src/errors.mjs` | `php/src/SelError.php` | `--- errors` | `lisp/src/errors.lisp` | `python/sel/errors.py` | `go/sel/errors.go` | `rust/src/utf8.rs` (`SelError`) |
| UTF-8 codec | `js/src/utf8.mjs` | `php/src/Utf8.php` | `--- utf8` | `lisp/src/utf8.lisp` | `python/sel/utf8.py` | `go/internal/utf8/` | `rust/src/utf8.rs`, `rust/src/text.rs` (`SelStr`) |
| Exact decimal | `js/src/decimal.mjs` | `php/src/Dec.php` | `--- decimal` | `lisp/src/decimal.lisp` | `python/sel/decimal.py` | `go/internal/decimal/` | `rust/src/dec.rs`, `rust/src/large_dec.rs` |
| The value | `js/src/value.mjs` | `php/src/Value.php` | `--- value` | `lisp/src/value.lisp` | `python/sel/value.py` | `go/sel/value.go`, `go/sel/shape.go` | `rust/src/value.rs`, `rust/src/shape.rs` |
| Function table | `js/src/registry.mjs` | `php/src/Registry.php` | `--- registry` | `lisp/src/registry.lisp` | `python/sel/registry.py` | `go/sel/registry.go` | `rust/src/builtins/mod.rs` (`registry()`: one central table of `register_native` calls) |
| Tokeniser | `js/src/lexer.mjs` | `php/src/Lexer.php` | `--- lexer` | `lisp/src/lexer.lisp` | `python/sel/lexer.py` | `go/sel/lexer.go` | `rust/src/parser.rs` (the lexer half) |
| Parser | `js/src/parser.mjs` | `php/src/Parser.php` | `--- parser` | `lisp/src/parser.lisp` | `python/sel/parser.py` | `go/sel/parser.go` | `rust/src/parser.rs` |
| Evaluator | `js/src/eval.mjs` | `php/src/Evaluator.php` | `--- eval` | `lisp/src/eval.lisp` | `python/sel/eval.py` | `go/sel/eval.go`, `go/sel/context.go` | `rust/src/eval.rs`, `rust/src/context.rs` |
| Argument framework | `js/src/eval.mjs` (`Args`) | `php/src/Args.php` | `--- eval` (`Args`) | `lisp/src/eval.lisp` (`args-*`) | `python/sel/eval.py` (`Args`) | `go/sel/args.go` | `rust/src/args.rs` |
| Built-ins | `js/src/builtins/*.mjs` | `php/src/Builtins/*.php` | `--- builtins` | `lisp/src/builtins/*.lisp` | `python/sel/builtins/*.py` | `go/sel/builtins_*.go` | `rust/src/builtins/*.rs` |
| Regex validator | `js/src/builtins/regex*.mjs` | `php/src/Builtins/Regex*.php` | `--- regex` and after | `lisp/src/builtins/regex.lisp` | `python/sel/builtins/*regex*.py` | `go/sel/builtins_regex.go`, `regex_syntax.go`, `regex_emit.go`, `regex_ambiguity.go`, `regex_counter.go` | `rust/src/regex.rs`, `regex_ambiguity.rs`, `regex_counter.rs`, `builtins/regex_ops.rs` |
| Joins (`LINK`) | `js/src/builtins/structure.mjs` | `php/src/Builtins/Structure.php` | `--- structure`, `--- the join pre-filter` | `lisp/src/builtins/structure.lisp` | `python/sel/builtins/structure.py` | `go/sel/join_plan.go`, `join_prefilter.go` | `rust/src/join_plan.rs`, `join_prefilter.rs` |
| Optimiser | `js/src/optimizer.mjs` | `php/src/Optimizer.php` | `--- AST optimizer` | `lisp/src/optimizer.lisp` | `python/sel/optimizer.py` | `go/sel/optimizer.go` | `rust/src/optimizer.rs` |
| Math plan | `js/src/math_plan.mjs` | `php/src/MathPlan.php` | `struct MathPlan`, `--- eval` | `lisp/src/math-plan.lisp` | `python/sel/math_plan.py` | `go/sel/optimizer.go` (`compileMathPlan`), `eval.go` (`evalMathPlan`), `plan_regs.go` | `rust/src/math_plan.rs` |
| SQL layer | `js/src/sql/` | `php/src/Sql/` | `cpp/sel_sql*.{hpp,cpp}` | `lisp/src/sql/` | `python/sel/sql/` | `go/sel/sql/` | `rust/src/sql/` |
| Host API | `js/src/sel.mjs` | `php/src/Sel.php` | `cpp/sel.hpp` | `lisp/src/sel.lisp` | `python/sel/__init__.py` | `go/sel/program.go`, `go/sel/doc.go` | `rust/src/lib.rs`, `rust/src/program.rs` |

Where the built-ins are grouped differently: JS, Python and Lisp split them into
`control`, `null`, `number`, `text`, `binary`, `regex`, `structure` and
`aggregate`. PHP's `Core.php` holds control and the sorts and `DISTINCT`, and its
`Structure.php` the `TOP` family, `BUCKET` and `DEDUPE`; its null functions are
`NullOps.php`. Go has no null file (`COALESCE` and `IS_BLANK` are in
`builtins_control.go`). Rust's `core.rs` is control and null, `math.rs` is
numbers, and its aggregates are in `structure.rs`. Go and Rust keep join
planning top-level, beside the evaluator, where the other hosts keep it with the
structure built-ins.

PHP has no autoloader for the language; add any new file to
`php/src/bootstrap.php`. JS built-ins are imported from
`js/src/builtins/index.mjs`, Python's from `python/sel/builtins/__init__.py`, and
Rust's are registered in `registry()` in `rust/src/builtins/mod.rs`; a Go file in
package `sel` registers itself from its `init()`. All of this must happen before
parsing, because unknown function names are a **compile-time** error.

**Every parser is precedence climbing** over the same productions:
`parse_program` → `parse_sequence` → `parse_list` → `parse_term` →
`parse_prefix` → `parse_postfix` → `parse_primary`, spelled in each language's
case (`parseTerm` in JS, PHP and Go, `parse-term` in Lisp), with the sixteen
precedence levels of `spec/SPEC.md` §5 as a pair of lookup tables rather than
sixteen functions. Rust's methods drop the prefix — `sequence`, `list`, `term`,
`prefix`, `postfix`, `primary` (with `primary_inner`), entered through `parse` —
and every host adds `parse_call` and `parse_pipe_step` for calls and `.>`
steps. `python/sel/parser.py`'s module docstring is the rationale.

Every host has the SEL→SQL layer; `docs/internals/sql-translation.md` is the
design and `sql/cases/*.sqlt` grades every one of them against it.

---

## Adding a function

Most extensions are a function, and a function is cheap: one table entry per host
plus cases. No grammar changes, no new node types, no parser work.

**A shipped builtin is declared once, in `spec/builtins.json`** (format:
`spec/builtins.md`): its name, min/max, any extra arity rule, and the lazy/binds
flags. `node tools/gen-builtins.mjs` renders that into each host's native table
and into `docs/reference/builtins.md`; commit the renderings with the entry
(`tools/check-generated.sh` fails while they are stale). Each host's `define`
then holds the definition to the manifest at startup — a min/max/lazy/binds
that disagrees, or a manifest name no module defined, refuses to load — and
installs the extra arity rule from it, so `COND`'s odd count is written once
for every host; `tools/check-manifest.sh` then calls every builtin with every
count around its range and every binding form with a distinct name in each
slot, checking what each host accepts and what `dependencies()` reads against
the manifest's own prediction — a table can say one thing and a host do
another, and that is the gate that notices. A binding builtin also declares its *forms* there — which
argument is the source, a binder name, a per-element body or an outer
expression, and how a count that admits two forms is disambiguated — and the
dependency walker and the SQL layer's stage 1 classify arguments through the
one generated classifier (`bindingForm` and its four twins) rather than
retyping the rules. A function that is *not* in the manifest (the examples
below, an application's own) passes through `define` untouched; a binding one
gets the two classic shapes, `F(list, body)` and `F(list, NAME, body)`.

There are **two lanes**, and picking the right one is most of the design work.

| | Lane A — strict | Lane B — lazy (AST) |
|---|---|---|
| Declared | (nothing extra) | `lazy: true` |
| Gets | values, already evaluated | argument **nodes** |
| Arguments evaluated | all of them, left to right, before the body runs | only the ones you ask for, as often as you ask |
| You write | one expression over typed accessors | an explicit walk, with frames if it binds |
| Use it for | anything that just computes | control flow, and anything that repeats or skips a body |
| In core | everything except the six below | `IF`, `COND`, `ALL`, `ANY`, `MAP`, `FILTER`, `SUM` |

**Start in Lane A.** It is the whole point of the argument framework: the
framework has already checked the arity, evaluated each argument exactly once,
and will raise the right error at the right source position for you. A Lane A
function is usually four lines and cannot get evaluation order wrong.

Move to Lane B only when the function must *not* evaluate something — a branch it
does not take, or a body it runs once per element. That is the property the AST
calling convention exists to provide, and it is also where all the sharp edges
are.

Both lanes are worked below, end to end, in every host. Neither
example is part of core SEL, so both can be lifted as-is.

---

### Lane A — a strict function

Worked example: `ORD_SUFFIX(n)`, returning `1st`, `2nd`, `3rd`, `4th`.

All of it — the spec row, the conformance cases, and one file per host — is in
**[examples/fn-simple/](../examples/fn-simple/)**, whose README carries the order
of work and the four places that have no autoloader. The JS:

<!-- from: examples/fn-simple/js.mjs -->
```js
define({
  name: 'ORD_SUFFIX', min: 1, max: 1,
  fn: (args) => {
    const n = args.nonNegInt(0);
    const tens = n % 100;
    if (tens >= 11 && tens <= 13) return Value.text(`${n}th`);
    const ones = n % 10;
    return Value.text(`${n}${ones === 1 ? 'st' : ones === 2 ? 'nd' : ones === 3 ? 'rd' : 'th'}`);
  },
});
```

`php.php`, `cpp.cpp`, `lisp.lisp`, `python.py` and `rust.rs` sit beside it, saying the same
thing in their own spelling.

Note what is *not* there: no argument count check, no type check, no `eval` call,
no try/catch. `nonNegInt(0)` evaluates argument 0 once, requires it to be a whole
number ≥ 0, and raises `E_NOT_INT` or `E_RANGE` against **that argument's**
source position if it is not. That is [the Args API](#the-args-api) doing the
work, and it is why a Lane A function is four lines rather than twenty.

Then `tools/check.sh`, green on every host, or it isn't done.

---

### Lane B — a lazy (AST) function

A lazy function declares `lazy: true` and reads argument **nodes** instead of
values. Nothing is evaluated for it; it decides what to evaluate, when, and how
many times. The smallest one in core is `IF` — which is why `IF` needs no syntax
— in `js/src/builtins/control.mjs` and its four siblings.

The interesting half is a function that evaluates one body argument **once per
element**, with a name bound to that element: an aggregate. Worked example,
again in every host with its cases, in
**[examples/fn-complex/](../examples/fn-complex/)**:

**`FIRST(list, body)`** — the first element for which `body` is `TRUE`, or TEXT
`""` if none matches. Like `FILTER`, but it stops at the first hit and returns
the element rather than a list. The three-argument form `FIRST(list, X, body)`
names the binder, exactly as the core aggregates do.

```sel
FIRST((1, 8, 3, 9), _ > 5)                       # the element 8
FIRST(ITEMS, IT, IT["QTY"] > 0)["SKU"]           # first line that has a quantity
FIRST((1, 2), _ > 5)                             # "" — nothing matched
```

<!-- from: examples/fn-complex/js.mjs -->
```js
import { define } from '../registry.mjs';
import { Value, NONE } from '../value.mjs';

define({
  name: 'FIRST', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    const three = args.count() === 3;
    const binder = three ? args.symbol(1) : '_';
    const body = args.node(three ? 2 : 1);

    const list = args.val(0);
    const items = list.size() > 0 ? list.entries()
      : list.kind === NONE ? [] : [['1', list]];

    for (const [key, item] of items) {
      ctx.pushFrame(new Map([[binder, item], ['_K', Value.text(key)]]));
      try {
        if (args.evalNode(body).asBool(body.pos)) return item.clone();
      } finally {
        ctx.popFrame();
      }
    }
    return Value.text('');
  },
});
```

Four things that code has to get right, and all four are the reason Lane A is
the default. [The README beside it](../examples/fn-complex/) says why each one
bites:

1. **Declare `binds: true`** — or `dependencies()` reports the binder as an
   input field the host is expected to supply.
2. **Push a frame per element and pop it on the way out, including when the body
   raises** — or a failed body leaves the binder in scope for whatever runs next.
3. **Bind `_K` as well as the element.**
4. **Handle the no-children cases** the way spec/SPEC.md §7.3 specifies: a scalar
   is a one-element list containing itself; a childless NONE is genuinely empty,
   which is what `FILTER` returns when nothing matched.

---

### Unusual arity

`min`/`max` cover most cases. For a rule they cannot express, declare
`arityError`, checked at compile time right after min/max:

```js
arityError: (n) => (n % 2 === 0
  ? `COND takes condition/result pairs and a final default (an odd number of arguments), got ${n}`
  : null),
```

A count check inside the function body is not the same thing: it runs only
when the call is reached, so `IF(TRUE, 1, LINK(1, 1, 1, 1))` answered `1` in
the hosts that checked "3 or 5" in `doLink` and `E_ARITY` in the one that
declared it. For a shipped builtin the rule is now written in
`spec/builtins.json` (`arity.allowed` or `arity.parity`, with its message) and
installed by the registry; `conformance/11-arity.selt` pins the compile-time
position with a call that is never reached. `arityError` on `define` remains
the way a host's own function declares one.

---

## The Args API

Argument accessors evaluate at most once and cache, so reading the same argument
twice is free and cannot double a side effect. Every typed accessor reports
failures against that argument's own position.

| Call | Gives you |
|---|---|
| `count()` | argument count |
| `val(i)` | the `Value`, evaluated once |
| `text(i)` | `string` — `E_NOT_TEXT` on BIN or BOOL |
| `bytes(i)` | bytes — TEXT is encoded as UTF-8 |
| `bool(i)` | `bool` — `E_NOT_BOOL`, no truthiness |
| `dec(i)` | a decimal record — `E_NOT_NUM` |
| `int(i)` | whole number — `E_NOT_INT` on a fraction |
| `nonNegInt(i)` | whole number ≥ 0 — also `E_RANGE` |
| `node(i)` | the raw AST node (lazy functions) |
| `evalNode(n)` | evaluate a node now (lazy functions) |
| `symbol(i)` | the identifier name, `E_EXPECT_SYMBOL` if not a bare name |
| `posOf(i)` | that argument's position, for your own `fail()` calls |

Raise your own errors with `fail(code, message, args.posOf(i))` — always a
registered code from `spec/errors.md`, always the position of the thing that is
actually wrong.

## The Value API

| Call | Notes |
|---|---|
| `Value.text/bin/bool/num/int/none/list` | constructors |
| `.kind` | `NONE` `TEXT` `BIN` `BOOL` |
| `.size()` | child count — a method in every host, and `tools/check-api.sh` has a probe to keep it one |
| `.get/set/has/keys/values/entries` | children, insertion-ordered |
| `.asText/asBytes/asBool/asDecimal(pos)` | applies scalar context, throws on mismatch |
| `.scalarSource(pos)` | the value supplying the scalar |
| `.clone()` / `->copy()` | deep copy — assignment uses this, and only four other places do (see the traps) |
| `.eql(other)` | structural equality, key order significant |
| `.dump()` | canonical form; **must** be byte-identical across hosts |

Return a fresh `Value` from a built-in. Never mutate an argument — `A` and the
value the caller passed are the same object.

---

## Adding an operator

Genuinely more work than a function, and usually not worth it: an operator costs
a precedence level, a grammar production, a spec change, a lexicon entry and an
evaluator branch in every host, where a function costs one table entry. Add one only when the thing
is *syntax* — used constantly and unreadable as a call.

If you still want it, here is the whole checklist. Worked example: `//`, integer
division, binding like `*`.

**1. `spec/grammar.md`** — add the token to the operator list. Order matters:
the tokeniser matches longest-first, so `//` must appear before `/`. Add it to
the relevant production.

**2. `spec/SPEC.md` §5** — add it to the precedence table and describe its
semantics, including which kinds it accepts and which error it raises.

**3. Conformance cases** — in `03-operators.selt`, covering precedence against
its neighbours, associativity, and the failure modes.

**4. `spec/lexicon.json`** — the token, in its level's `operators` (or a new
level, in §5's row order), with its family: `{ "family": "arith" }` for `//`
in the `MUL` level. Then `node tools/gen-lexicon.mjs`. That one edit is every
host's tokeniser (its `SYMBOLS` list, longest first — the generator sorts it,
so `//` cannot end up after `/`) and every parser's binding-power table (the
seven parsers build their `infix` tables from the rendering), and the
generator refuses to render while steps 1 and 2 say something else: it checks
the grammar's token block, reserved words, `assign_op` and `compare_op`, and
every row of §5's table. A **word** operator is reserved there too
(`reserved`), so no lexer can go on accepting it as a variable name. A *new*
level is a new entry in `levels`; every binding power above it moves up by
one in all seven hosts at once. Format: `spec/lexicon.md`.

**5. Nothing per parser** — but know what the table means, because a wrong
row is a valid parse of the wrong tree, which no compiler catches:

- **Associativity is three-valued.** `left` parses its right side at `bp + 1`,
  `right` at `bp` — that is what makes it right-associative — and `none` at
  `bp + 1` and then rejects a second operator at the same level. A comparison
  is `none`, and its `E_SYNTAX` is reported at the **second** operator.
- **A prefix operator is not a primary.** `NOT` binds at 7 and unary `-` at
  16, and `parse_prefix` accepts each only when the caller's `min_bp` reaches
  it. Putting them in `parse_primary`, where textbook precedence climbing puts
  them, makes `NOT a == b` parse as `(NOT a) == b` and breaks two of the depth
  pins at the same time.
- **The family is what the rest of the host reads.** The evaluator's compound
  assignments, the constant folder, the optimiser's "cannot raise" and
  predicate checks, the join pre-filter, the dependency walker (`shortCircuit`:
  the right side may never run) and the SQL translators all classify an
  operator through its lexicon record, not through lists of their own. A new
  comparison needs its `relation`; a new short-circuit operator needs
  `shortCircuit`.

**6. Every evaluator** — a branch in `evalBinary` / `eval_binary` /
`eval-binary`. Each host's opcode table is checked against the lexicon when it
loads, so a host that lacks the branch refuses to start rather than answering
for an operator it does not know. Use the operand's own position for type
errors and the operator's for arithmetic ones:

```js
case '//': return Value.num(D.trunc(D.div(l.asDecimal(lp), r.asDecimal(rp), node.pos)));
```

In C++ and Lisp, bind the two coerced operands to named locals first. Writing
them as two arguments to one call leaves their order unspecified in C++, and
which operand's position an error reports is observable — see the traps.

A **comparison** operator is the one case where the evaluator is two edits, not
one: the branch in `evalBinary` hands off to `compareResult` /
`compare_result` / `compare-result`, which turns a `-1 | 0 | 1` into a boolean
and must learn the new operator too. Every host used to answer for an operator
it did not name — `>=` in most of them, `FALSE` in JS — so forgetting this
second edit produced wrong answers rather than an error. Every host now refuses
with `E_SYNTAX unknown comparison operator`, which is what you will see if you
skip it.

**7. `Program.dependencies()`** — nothing to do for a binary operator; `bin`
nodes are already walked. A node type that binds names is a different story.

**8. `tools/gen-programs.mjs`** — add it to `ARITH` or the relevant list so the
fuzzer exercises it. An operator the fuzzer never emits is an operator with no
differential coverage.

**9. `docs/operators.md`** — the precedence table and a `=>` example, which the
doc checker will then run in every host.

Reserved **words** (`AND`, `EQL`, …) are lexed as identifiers and handled in the
parser; the lexicon's `reserved` list (step 4) is what makes them unusable as
variable names in every lexer.

---

## Writing conformance cases

Format is in `conformance/README.md`. The short version:

```
### name: category.thing.detail
--- note
Why this case exists, when that is not obvious.
--- setup
A = 1
--- source
A + 1
--- expect
num 2
===
```

- Names are unique across the whole suite and grouped by dotted prefix.
- `--- setup` is SEL, run against a fresh context first. A failure there is
  reported as a *suite* bug, not a test failure.
- Expectations: `text "…"`, `num …`, `bin <hex>`, `bool TRUE|FALSE`, `none`,
  `tree <dump>`, `error E_CODE`, `error E_CODE at line:col`.
- **Assert on error codes, never message text.** Messages are free to change.
- Assert a position when the position is the point — that innermost-failure
  reporting is a promise, and it needs holding to.

Add a `--- note` whenever a case encodes a decision rather than an obvious fact.
The regex file is mostly notes, because every case there is a fossil of some
engine disagreement.

When the fuzzer finds a disagreement, **add the minimal case first, then fix**.
The case is the part that lasts.

---

## The traps

Every item here is a real divergence that was found in this codebase, not a
hypothetical.

The first group applies everywhere; the Python group at the end is separated only
because its traps are the least worn-in. The two hosts whose regex engine
follows Perl — Lisp and Python — appear in both groups.

### Any host

**Never use the host's regex flags naively.** PHP's `u` modifier turns on PCRE2's
UCP, so `\d` matches Arabic-Indic digits and `\w` matches `é`; ECMAScript's `u`
does not. Every host therefore *rewrites* `\d`, `\w`, `\s` into explicit ASCII
classes before compiling. `\b` had to be refused outright, because a word
boundary is defined in terms of the engine's word characters and no rewrite fixes
that. `\v` means "any vertical whitespace" in PCRE and U+000B in ECMAScript.

**Never use the host's string length or indexing.** PHP counts bytes, JS counts
UTF-16 units, C++ `std::string` counts bytes, SEL counts code points. Use
`Utf8::chars()` / `toCodePoints()` / `decode_utf8()`. `preg_*` returns byte
offsets and JS returns UTF-16 offsets — convert both with `cpIndex`. Lisp and the
C++ `u32string` paths are already code points, which is exactly why it is easy to
forget that the others are not.

**Never hash a join key from a formatted decimal.** An equi-join on `==` pairs
elements as `==` compares them, so `1.50`, `"1.5"` and `1.5` are one key. JS and
Lisp keyed on `format(asDecimal(x))`, which keeps the written scale, and PHP
keyed a cached decimal one way and a not-yet-parsed text another — so a fresh
process joined `1.00` to `"1.00"` only *after* some other comparison had parsed
the text. Strip trailing fraction zeros, fold negative zero, and build the key
from the same helper whichever path produced the decimal
(`rel.link.numeric-key-*` in `15-relational.selt`). The `_1`/`_2` binders are
positions, not names: an unnamed argument is bound bare, and most hosts once
added a `_2` key holding the element to itself.

**Never use the host's case mapping.** `strtoupper` is byte- and locale-based;
`toUpperCase` and `string-upcase` are full Unicode; `std::toupper` is
locale-dependent and byte-wise. `UPPER`/`LOWER` are ASCII-only by decision.

**Never use the host's string comparison.** JS compares in UTF-16 order and CL's
`string<` in code-point order, both of which disagree with UTF-8 byte order above
U+FFFF. Compare bytes explicitly.

**Never use the host's idea of a digit.** SBCL's `DIGIT-CHAR-P` accepts every
Unicode decimal digit, so U+0661 ARABIC-INDIC DIGIT ONE parsed as a number in the
Lisp host until the fuzzer caught it. Number literals, `\u{...}` escapes, hex,
regex quantifiers and `$1` replacement references are all ASCII by specification;
`lisp/src/utf8.lisp` has `ascii-digit-p` and `ascii-hex-value` for this and
nothing may use `DIGIT-CHAR-P`. It is the same trap as `\d` under UCP, wearing a
different hat.

**Never introduce a float.** Not for rounding, not for a quick length ratio, not
anywhere. Use `Dec`. PHP `Value::fromNative` rejects floats on purpose.

**Watch PHP array keys.** PHP silently converts numeric-string keys to integers.
`Value` casts every key back to string on the way out. A packed array is also
renumbered from 1 in `fromNative`, because SEL lists are 1-based — this one
slipped through the conformance suite and was only caught by an example that
indexed `ITEMS[1]` directly.

**Watch PHP's regex delimiter.** A pattern may contain `/`; `escapeDelimiter`
handles it. JS needs no delimiter at all, so it is easy to forget.

**Never read a PHP decimal's `digits` directly.** A big number the evaluator
computes may be lazy: `digits` is null and its magnitude is a GMP in `gmp`, its
digits written only when text is asked for (with ext-gmp; that made Mandelbrot
2.4x faster). Hand decimals to `Dec`; read digits with `Dec::digits()` or
`Dec::format()`, or take today's array with `Dec::eager()`. `Value::asDecimal()`
and `Args::dec()` always return today's array; the evaluator's
`Value::asDecimalLazy()` does not.

**Watch replacement syntax.** Never hand a user replacement string to
`preg_replace` or `String.replace`. SEL splices matches by hand so that `$&`,
`` $` `` and `\1` stay literal.

**Watch C++ evaluation order.** The order in which function arguments are
evaluated is unspecified, and GCC does it right to left. SEL evaluates strictly
left to right (§6.2), and the difference is observable: `TRUE $== FALSE` must
report the *left* operand's position. Writing `bytes_compare(l.as_bytes(lp),
r.as_bytes(rp))` reported the right one. Bind each coerced operand to a named
local first; `eval_binary` says so in a comment for the next person.

**A value is not a snapshot.** Evaluating an expression yields the value itself,
so a mutation made by a later sub-expression is visible through a reference
obtained earlier (§3.4) — `A[A["k"] = "k"]` finds the key its own index
expression just created. Every host aliases by default and copies where the
spec says: the table in §3.4 is the list — `=`, `,`, `LIST`, `RECORD` and the §7.3
aggregates (`MAP`, `FILTER`, `SORT*`, `BUCKET`, `TOP*`) copy what they collect;
`TAKE`, `DROP` and `DISTINCT` return a fresh container that holds the source's own
elements. That the aggregate copy is *observable* is easy to miss, because a
binder cannot be assigned (`E_BAD_ASSIGN`) — but the aggregated *source variable*
can, from inside the body or from a sibling expression: `X = LIST(RECORD("k",1));
MAP(X, _)[(X[1]["k"] = 9; 1)]["k"]` is `1` when MAP copied and `9` when it
aliased, and hosts once split on exactly that. Copy sites are
covered by `conformance/25-value-ownership.selt`, and that file is where a new one
gets its case. This is also why `LAZY_RECORD` had to go: a record whose fields were evaluated on first read made a copy
observable, and the hosts split on it. There are no lazy values. If you add a built-in that stores one value inside
another, it belongs on that list, and if you add a copy anywhere else you have
invented a divergence.

Leaving a copy out is allowed exactly where §3.4 allows it, where nothing can
tell. PHP, C++ and Python make none in a program whose evaluated tree holds no
assignment and no host-function call (`Context::$writeFree`,
`Context::write_free`, Python's `Context.write_free`, decided once with the
physical tree; Python counts a function `define()`d outside the manifest as the
application's too, `registry.is_host_function`), and none of a value the body
built itself (PHP's `Core::buildsItsResult`; C++'s `adopt_or_clone` keeps any
value nothing else holds; Python's `aggregate.collected`); either way they still
make the copy's depth check (`Value::checkDepthBelow`,
`Internals::check_clone_depth`, `Value.check_depth`), because `E_DEPTH` is the
one thing a copy reports that such a program can see. C++ also lets a `FILTER`
keep its rows uncopied when the next pipeline step copies what it keeps (a
`MAP`, `FILTER`, sort or `TOP`) and neither can write (`borrow_rows`); Python
does so before a `MAP` (`Node.adopt_items`). Made everywhere, those copies were
a fifth to a third of the PHP scale scenarios' time, over a quarter of C++
scenario 2's and a tenth of Python scenario 1's. The snapshot an aggregate
visits (§7.3) is left out the same way: C++ walks a write-free program's
collections in place (`Snapshot::live`), since nothing can change them, where
taking it touched every element twice (scenario 6's 90,000 joined rows; the
snapshot's handle is also why `Internals::exclusively_held` is asked only outside
such programs). A write-free program's result may hold the context's own values,
as a bare variable's and `TAKE`'s always could; a host that changes a result
through its API clones it first.

C++ is the host where this is easy to get wrong, because `Value` is a handle
over an intrusive, reference-counted `Impl` and copying it *looks* like a deep
copy. It is not: use
`clone()`. Up to and including 0.2.0 the C++ `Value` really did deep-copy on
assignment, which made it disagree with the other hosts in six ways — three
`E_NO_KEY`s where an index expression created the key its own base then read,
an `E_NO_SCALAR` from a compound assignment reading its target across the
right-hand side, a wrong tree from an aggregate binder that named a copy rather
than the element, and one confidently wrong number. There is no
cycle collector behind the handle, so the five clone sites are also what stops
`A[1] = A` from leaking; `cd cpp && make asan` runs the suite under the leak
checker to keep that true — and the SQL case suite and SQL unit with it, so the
translator and the planner are held to the same checkers (SEL-0045).

**Resolving an assignment target to a path is not a C++ workaround.** Every host
walks the target chain into a list of keys and re-derives from the root
afterwards, and they do it for the reason in §5.7 — the store lands at that path
in the tree *as it exists once the right-hand side has run* — not because of any
host's pointer rules. Index expressions are still evaluated exactly once, in
order, before the right-hand side; that ordering is observable too.

**Watch cl-ppcre's anchors.** It follows Perl, where `$` also matches before a
trailing newline — the same reason the PHP host needs PCRE's `D` modifier. The
Lisp host lowers `^` and `$` to `\A` and `\z` in its rewrite pass. It also does
not apply the *simple* case folding that ECMAScript's `iu` and PCRE2's `ui` both
do, so the two non-ASCII code points that fold to ASCII letters (U+212A and
U+017F) are folded in the subject before matching, and group text and
replacements are sliced from the original.

**Watch `E_DEPTH`.** Every host caps parse and evaluation nesting at 200. If you
add recursion, it must be counted, or a hostile rule becomes a stack overflow.
This is not hypothetical and the rule was already broken once: the prefix
operators recursed into themselves without passing through either of the two
functions that track depth, so a chain of them was bounded by nothing. They live
in `parsePrefix` in the table-driven hosts and in `parseNot`/`parseUnary` in the
transcribed ones; the hazard is the same in both shapes. `-` repeated about twenty thousand times raised a `RangeError` in JS and
**segfaulted the C++ host** through its public CLI. Count the nesting *only when
the operator is actually consumed*, or every other expression loses a level and
`lim.parse-depth` moves.

### Python

**Do not run a query with the cyclic collector's default policy over a large
resident context — and do not add cycles to a run.** `Program.run` pauses the
cyclic collector for the run (`python/sel/_gc.py`), because CPython's
collector triggers on allocation counts and a full pass walks the whole
resident context to find cycles that SEL's tree-shaped values never form: on
the 137,100-row scale fixture, 27–40% of the join-heavy scenarios' time was
collector wall time finding nothing. The pause is re-entrant, exception-safe,
and hands the collector back exactly as found. It is safe only while a run
creates no cyclic garbage: a new builtin must not build self-referential
structures per element (closures that capture themselves, back-pointers from
child to parent). The optimiser's math-plan compiler is the one known source,
about ten closure objects per plan, reclaimed at the first pass after the run;
`python/tests/test_gc_pause.py` measures both facts. An application that
serves many queries over one large context can go further with `gc.freeze()`
after loading it, which removes the context from every future full pass.

**A frame keeps its names while it is pushed.** The `Context` counts the names
its pushed frames bind (`Context.bound`, kept by `push_frame` and
`pop_frame`), and `lookup` reads a name the count does not hold straight from
the root, without walking the frames. A binding builtin — or a host's own,
`define(..., binds=True)` — sets its binders' values in the frame it pushed and
never adds a name to it afterwards; new names go in a new frame.
`python/tests/test_binder_frames.py` checks every binding form and the
conformance cases that bind. Do not try to resolve names at compile time from
the binding forms instead: SPEC 7.4's three-argument `LINK` binds its
relations' names and their lowercase, which no form lists
(`rel.link.relation-names-bind-in-pred`).

**A math plan has two executors, and they must agree.** `_interpret_math_plan`
runs a plan step by step; once the plan has run `_PLAN_HOT` times,
`_eval_planned` swaps in the function `_compile_math_plan` generates from the
same steps (for plans up to `_PLAN_MAX_STEPS`). A new opcode, a moved coercion
or a changed error position goes into both; an import-time check refuses an
opcode the compiler cannot translate, and
`test_a_compiled_math_plan_answers_as_the_interpreted_one` compares the two.
Most conformance cases run a plan once and never reach the compiled tier, so
after touching either, run the suite and `tools/check-eval-equivalence.py`
with `_PLAN_HOT = 1` too.

**The physical tree's nodes carry their evaluator.** `optimizer.bind_handlers`
stamps `Node.ev` on every node the physical tree owns; `eval_node` uses it
before its table (`_EVAL`). A node shared with the caller's AST has none, and
neither has a `replaced()` copy: a rewrite that runs after binding and changes
a node's `t`, `op` or plan must clear `ev`, or the node keeps running its old
evaluator. `eval_cond`, which FILTER, IF, COND and the join's pre-filter ask
whether a node holds, reads `ev` too: the evaluator of a comparison, `$==`,
`$!=`, `AND` and `OR` has a `_cond_*` form answering a bool, which the
`_eval_*` form wraps, so there is one copy of each. And `handler_for` binds a
`RECORD` with distinct literal keys to `structure._eval_record`
(`eval._CALL_HANDLERS`), a second copy of `_record`'s copy-or-hold rule, which
the plain tree still runs: a change to one is a change to both.

**The physical tree is a function of the AST alone, in every host.** `run`
evaluates a rewritten tree (TOP fusion, whatever a host adds), built once per
program and kept; the data a program runs over never changes it, and
`tools/check-api.sh` probes that. A physical optimisation that reads data
belongs at run time, never in the tree — and **no rewrite moves a `FILTER`
across a `LINK`.** Every host once pushed a FILTER's one-sided conjuncts under
the join (a "join predicate pushdown"); SEL-0051 made it tentative, and
SEL-0054 retired it: a FILTER moved onto a side renumbered the joined rows,
skipped the join keys of the rows it dropped (and of the joins above them),
and took relation names for members under explicit binders — none of which
the tree can see (spec §7.4). What it bought, the join does at run time, in
every host's `LINK` (SEL-0052, SEL-0054, after Python's SEL-0049/0050): a
`FILTER` whose source is a join hands the join its conjuncts as *stages*,
one per FILTER in the order they run, each conjunct with the fields it reads
(a nested `_["orders"]["year"]` reads `orders`; only the FILTER's own binder,
exactly as named, is the element) and, for a comparison of literals and bare
fields, the (field, kind) requirements under which it cannot raise. With both
sides at hand the join walks the conjuncts in order: it applies to its left
rows those whose fields no right side carries (a joined row takes a left
element's field whenever the right element of its pair lacks the name, spec
§7.4; a read through the left binder's own name is the row itself); applies
to its right rows, once each, those that read only through its own right
binder (`_["products"]["is_active"]`: that member is the right element in
every joined row), not over `LINK_LEFT`; passes over one that is total — its
field on every row of the one side that has it, with the kind, and that side
not a `LINK_LEFT`'s null-extended one — and stops at the first that is
neither. **Each stage is judged against the joins between its FILTER and the
join testing it**, never those above its FILTER: a FILTER in the middle of a
chain reads rows no upper join has touched. A rejected left row is dropped
(the numbering kept where something observes it); a rejected right row stays
in its bucket, counted for the numbering and joined to any left row a
conjunct raised on; a row a conjunct raises on is kept and reported, so the
FILTER evaluates everything, and a right row kept on an error cancels the
left drops after the first right conjunct. The join reports which conjuncts
every row it built passed, whether it kept a row on an error, and whether it
dropped any; **the FILTER skips the conjuncts its join applied** when nothing
was kept on an error (the saving the pushdown had), and a join above skips
those applied below. Two more rules govern dropping *below* the FILTER's own
join, when an equi-join with pure sources evaluates its right side first and
hands what is still askable down its left spine: each handing join sends its
own left key down as an obligation, and the join that drops must prove, from
the rows, that no such key can raise on a dropped row (the field it reads
present on every row of the relation it reads through, or, for a promoted
field, carried non-null by every row of its one owner) — otherwise an
`E_NO_KEY` the upper join would have raised is lost; and a join whose left
side came back empty with rows dropped below evaluates that side again as
written, since as written it would have computed every right key. Joined
rows are built pair by pair (SEL-0053), so a dropped row changes no other
row. Every host's compiled row plans are keyed on the pair's two shapes and
check, as they copy, the only two facts that change a row — each left
field nested record or not, each right field it takes a non-NULL scalar —
never trusting a first element; a new fast path must keep both checks (or
prove them, as the right-rows-all-flat pass does in JS, C++ and Lisp), and
`tools/join-rows-oracle/run.sh` holds every host to a model of §7.4. Nothing here is visible in a
value; `conformance/15-relational.selt`'s `passed-over.*`, `early.*` and
`as-written.*` cases pin the boundaries where a wrong proof would lose an
error, and the gate's join-then-filter lane (`tools/join-filter-oracle/`)
compares generated join-then-filter programs with the same programs through
helper variables on every host, and fails on any difference.

**Never call `round()`, and never let a float in.** Python's `round()` is half to
even — `round(2.5)` is 2 — and SEL rounds half away from zero everywhere. `/` on
ints produces a float, and `math` takes floats. `python/sel/decimal.py` uses `int`
and `//` only, and `Value.from_native` refuses a float outright rather than guess
a decimal form for it.

**Never use Python's idea of a digit.** `"٣".isdigit()` is true and `int("٣")` is
3; `int(" 12 ")` strips whitespace and `int("1_2")` is 12. Number literals,
`\u{...}` escapes, hex and quantifier bounds are ASCII by specification, so
nothing may reach `int()` before an explicit ASCII check has passed. It is the
SBCL `DIGIT-CHAR-P` trap wearing a different hat, with two extra brims.

**Never use `str.splitlines()`.** It also breaks on `\v`, `\f`, `\x1c`–`\x1e`,
U+0085, U+2028 and U+2029. The `.selt` reader, the corpus reader and the oracle
reader all use `.split('\n')`; the corpus rule is *exactly one* trailing newline
removed, so `.rstrip('\n')` is wrong too.

**Never use `str.upper()`/`str.lower()`.** They apply full Unicode mapping and
can change a string's length: `"ß".upper()` is `"SS"`. `UPPER`/`LOWER` are
ASCII-only by decision.

**Never use `str.strip()`.** It takes the Unicode whitespace property; SEL's
whitespace is exactly space, tab, CR and LF.

**Watch `re.IGNORECASE`.** It folds *four* non-ASCII code points onto ASCII
letters — U+212A and U+017F, which is correct, and U+0130 and U+0131, which is
not: nothing else folds those onto `i`. The fix is `re.ASCII | re.IGNORECASE` to
turn all four off, then folding the two correct ones in the subject by hand. That
is safe only because both are one code point mapping to one code point, so
offsets survive; group text and replacements are still sliced from the
**original** subject. Do not add U+00DF to that table — it folds to `ss` only
under *full* folding, which changes length.

**Watch Python's `$`.** Like PCRE's and Perl's, it matches before a trailing
newline. `^` and `$` are lowered to `\A` and `\Z` after the shared validator has
run, never inside it — `\A` is not ECMAScript and would break the JS host.

**Watch `bool` being an `int`.** `isinstance(True, int)` is true, so
`from_native` must test for `bool` first or `True` becomes the TEXT value `"1"`.

**Watch the stdlib's leniency.** `base64.b64decode` ignores characters outside
the alphabet unless asked not to, and even then disagrees about padding. SEL
specifies exactly which inputs are `E_BAD_ARG`, so base64 is written out by hand
like everything else.

---

## Changing the optimiser or the planner

The pipeline optimiser (`optimizer.*` in each host) and the hybrid planner
(`sql/hybrid.*`) are the two pieces written five times without a shared data
file to keep them honest — the dialect map is data every host loads, but a
rewrite is a walk, and a walk is transcribed. `sql/cases/25-hybrid-plans.sqlt`
is what stands in for the data file, and the cross-language review that
produced it found every one of the items below broken in at least one host
while all 801 language cases were green. So, for any change to either:

- **Order.** The planner runs stage 1 (`normalise`) first, for its verdict;
  then it unwinds the result expression *through* its helpers — a helper read
  as the pipeline's source is unwound into the pipeline, a literal helper is
  inlined at its reads at the read's position, any other helper stays a read
  and travels as an assignment in front of whatever the translator or the
  continuation is handed; then the logical optimiser; then the split. Never
  unwind the raw AST as if it were an expression: a helper assignment is a
  `seq`, and a `seq` is not a pipeline. And never plan stage 1's *tree*: it
  inlines every helper at its definition-site position, which is right for a
  refusal message and wrong for a continuation (`Y = "x"; … + Y` was
  reported at the `"x"` in the memory half and at the `Y` from `run()`, in
  every host). And the planner is the *only* caller
  of the optimiser in the SQL layer: `translate()` and `translate_statement()`
  run stage 1 alone, in every host, and the `.sqlt` runners check the two
  entry points against each other on every statement case. An optimiser
  pre-pass inside the translator is how two hosts came to render a program
  three ways.
- **One sweep.** The logical rewrites are one left-to-right pass over
  adjacent step pairs, rules tried in one fixed order at each position,
  repeated to a fixed point — in every host, including Lisp
  (`logical-step-pair`). Ordered per-rule passes reach a different fixed
  point (`SORT_BY .> TAKE` fused before the MAP/sort swap saw it), and the
  planner's SQL prefix is pinned byte for byte.
- **Ownership.** Neither the optimiser nor the planner writes into the tree it
  is handed. Copy the node, rewrite the copy, return it. A write-back is
  invisible until a constant folds, which is why the immutability fixtures
  fold one on purpose; if you add a rewrite, add a fixture whose input the
  rewrite would change.
- **Metadata meaning.** `source_tables` is physical names, first use first,
  deduplicated by physical name. Every return path of the planner fills every
  field of the plan — `continuation_ast` is the original AST on the
  pure-memory path, not left empty.
- **Options.** One options object, forwarded to both the optimiser and the
  translator. A key must mean the same thing in every host that accepts it.
- **Fallback.** Anything stage 1 or the translator refuses is a shorter prefix
  or a pure-memory plan, never an exception out of `plan_hybrid`. The two
  refusals that do escape — a base dialect, an alias collision — escape before
  any classification is attempted.
- **Shape.** A prefix is a split point only if its SQL rows are the value the
  evaluator would have produced for it — and "prefix" includes the whole
  pipeline: the full-pushdown probe is guarded like the prefix loop. A bare
  `BUCKET` breaks that (keys, not groups); `bucketRowsAreKeys` in each planner
  is the list of such steps, `joinRowsLackBinders` beside it is the other (a
  `LINK`'s SQL row is its promoted fields, without the binders SEL's row
  carries, until a `MAP`, a `SELECT_COLS` or a `BUCKET` projects it — finding
  Y), and a new step whose SQL result differs in shape from its SEL result
  belongs with them. The MAP fall-through has its own list,
  `FALLTHROUGH_DOWNSTREAM`: the steps that may follow a split MAP into the SQL
  because they keep its rows as they are. A new pipeline step is *not* in that
  list until you have shown the continuation still sees the MAP's input.
- **Vocabulary, again.** The externally facing verb is `BUCKET`; `GROUP_BY` is
  a SQL clause and does not appear in SEL. Internal plan fields may be named
  for the SQL they render.
- **Vocabulary.** There is one list of pipeline operators per host, in the
  optimiser; the translator and the planner read it from there. Do not add a
  second.
- **Positions.** The optimiser is invisible: an error a program raises after
  optimisation carries the same code and position as before it (spec §6.3
  names the node that actually failed, and the conformance suite asserts the
  column). A fold that builds a new literal stamps it with the folded node's
  position; a fold that would return a *child* must copy it and re-stamp it
  the same way, which is only exact for a leaf literal — `IF(TRUE, 1 / 0, 2)`
  is not folded, because the `/` inside has a column of its own. This is how
  most hosts came to report `IF(TRUE, "x", 1) >= 1` at the `"x"` while C++,
  whose IF arm never fired, reported the IF; the fuzzer compares positions,
  and so must any fold you add. A helper is the other way to move a position
  (see **Order**): inline only what is a literal, and at the read's position.
- **Depth.** The evaluator is the depth authority (spec §6.4): a tree that
  reaches the cap is evaluated as written, so every optimiser returns it as
  written — `exceedsDepth` / `exceeds_depth` / `opt_exceeds_depth` /
  `exceeds-depth-p` at the entry point, a bounded walk that counts as the
  evaluator counts (root at 1, a child one deeper, an assignment's target
  excluded). Folding a leaf at the boundary erased the `E_DEPTH` a chain of
  201 additions raises in the other hosts, and Lisp's optimiser raised it itself,
  at its own count, on a branch the evaluator never visits
  (`lim.eval-depth-*` in `conformance/10-limits.selt`). An optimiser must
  neither raise `E_DEPTH` nor make it disappear; not rewriting such a tree
  loses nothing, because it either raises or keeps its deep part where the
  evaluator never goes.

- **Values.** A rewrite that moves a step is only sound when the moved step
  cannot see the difference, and "reads only pass-through fields" is not the
  whole of that: `COUNT(_)` reads the row the `MAP` reshaped, `_K` reads the
  key the `MAP` (or a sort) renumbered, and a keyless `SORT()` compares the
  `MAP`'s outputs. `readsRowOrKey` / `stepReadsKey` in each optimiser are
  the guards, next to the field-set check, and every rule that crosses a
  `MAP`, a `SELECT_COLS` or a sort uses them; a new rule that crosses one of
  those must too. And keys are part of the value (spec §7.3): `FILTER` keeps
  its input's keys where `MAP`, `SELECT_COLS` and the sorts renumber, so a
  `FILTER` moved in front of one of them carries the source's keys where the
  program as written carried the step's — in the answer, and in a later
  `_K`. `keysRenumberedBy` / `keys_renumbered_by` / `opt_keys_renumbered_by`
  / `keys-renumbered-by-p` is the third guard: the swap is taken only when
  the step after the `FILTER` renumbers again without reading `_K`; at the
  end of a pipeline, or before another `FILTER`, it is not
  (`rel.map.then-filter-*`, `rel.sort.then-filter-keeps-the-sorted-keys`,
  `rel.select-cols.then-filter-keeps-the-keys`). The SQL lane has no keys to
  keep, but the continuation runs the same rewritten tree, so the planner's
  optimiser is guarded the same way and a `MAP(…) .> FILTER(…)` tail is
  rendered as a `WHERE` over the MAP's derived table. This is how most hosts agreed that `LIST(3, 1, 2) .>
  MAP(0 - _) .> SORT()` is `-1, -2, -3` while the unoptimised evaluator said
  `-3, -2, -1` — the fuzzer compares hosts, not lanes, so it never saw it.
- **Binders.** A `LINK`'s binders are scoped to its predicate (spec §7.4);
  after it the joined row carries them as keys, and `O["id"]` in a later step
  is `E_UNDEF_VAR` as written. The join-filter pushdown therefore attributes
  only a read *through the row* — `_["O"]["id"]` — to a side; a
  binder-qualified read is left where it is, to fail. It used to push
  `FILTER(O["id"] > 1)` into the `LINK`'s left source, where `O` is bound,
  and answered rows for a program that raises, while the same
  pipeline through a helper never took the rewrite. The translators keep the
  same scope: only `_` (and a step's own binder) after the join.
- **Fuzz.** `tools/gen-programs.mjs` emits pipelines — `.>` chains of every
  step, the sorts' forms, `_K` after a renumbering step, bare and projected
  buckets, joins with and without named binders, helpers as sources — and
  chains at the depth cap, since every cross-host divergence the 2026-09-15
  review found lived in a shape the generator could not produce. With `--sql` the pipelines read the relations every host's `sqlfuzz`
  runner binds (ORDERS, CUSTOMERS), and the runners print three lanes per
  program: `translate`, `translate_statement` and `plan_hybrid`. A new step
  or form belongs in the generator's `step()`; a new planner rule, in
  `sql/mutations.json`, next to the `*-filter-swap-ignores-keys` and
  `*-bucket-keys-are-rows` entries that keep the plan cases honest.
- **Sources.** A step's input is a list after any step, but the *first* step
  reads the source, which may be a scalar: `FILTER(5, TRUE)` is `(5)`, and
  dropping the FILTER made it `5`. A rewrite that removes a first step must
  know what the source is (`sourceIsList`), and a bound relation is not known
  to be a list — which is also what keeps `ORDERS .> FILTER(TRUE)` a pipeline
  the planner can push down.
- **Forms.** The evaluator resolves the three-argument `SORT_BY`/`TOP_BY` by
  the *shape* of its arguments (a text literal third is the direction, else
  a bare name second is the binder), so a fold that puts a literal where a
  computed value was changes which form runs. `stepArgOptions` /
  `opt_step_arg_folds` / `step-arg-folds-p` walk that one slot with folding
  off; a new shape-resolved argument belongs in the same place.

Then, for a cross-host rewrite: one language-neutral case in
`sql/cases/25-hybrid-plans.sqlt` showing what it does, one showing the shape it
refuses to touch where that is not obvious, and the immutability check the
runner applies to every planner case. Host-local tests
(`tools/check-js-optimizer.mjs`, `tools/check-php-optimizer.php`,
`python/tests/test_unit.py`, `cpp/tests/sql_unit.cpp`, `lisp/tests/unit.lisp`)
carry what the shared fixture cannot express — an optimiser option that only
three hosts accept, the `run()` cache — and nothing else.

---

## Running the checks

```
tools/check.sh                 everything, side by side; the report in a fixed order
                               starts Docker databases for the DSN-backed layers, or fails;
                               SEL_SKIP_DB_TESTS=1 opts out, SEL_SQL_<DIALECT>_DSN supplies your own
```

The layers run concurrently under two bounds from `tools/impls.sh`: `SEL_JOBS`
leaf commands at once (default three quarters of the hardware threads on an
idle box, half on a busy one) and `SEL_PHP_JOBS` PHP invocations among them
(half of `SEL_JOBS`). Both are `flock` slots in one directory that every nested
tool shares — a leaf takes a slot, a script that only queues leaves does not —
so `SEL_JOBS=2 tools/check.sh` really is two processes, mutation runner
included. A first Lisp step runs alone to warm ASDF's cache before the rest
start.

Every configuration of the default roster has to be built first; one that is
not fails the run with `MISSING`:

```
make -C cpp                builds build/{sel,conformance,batch,e2e,api,ast,check-decimal,unit,sqlt,sqlunit,sqlfuzz,sqlreplay,...}
make -C go                 go/build/*
bash rust/build.sh         rust/build/*
npm run build              dist/sel.mjs, dist/sel.min.mjs (js-bundle, js-bundle-min)
cd cpp && make test        unit tests, then the suite
lisp/bin/test              the Lisp unit tests
PYTHONPATH=$PWD/python pytest python/tests    the Python unit tests
cd go && go test -race ./...                  the Go unit tests
cd rust && cargo test --workspace             the Rust unit tests and allocation budgets
```

`SEL_IMPLS` narrows a run, and a narrowed run says so: the gate prints the
configurations of the default roster it is **not** running, and its last line
is `GREEN, PARTIAL — …` rather than `ALL GREEN`. `SEL_EXTRA_IMPLS` adds to the
roster instead of replacing it. Two slow lanes have opt-outs, which the last
line also names: `SEL_SKIP_SANITIZERS=1` (the C++ TSan and ASan builds),
`SEL_SKIP_SQL_BUDGETS=1` (the translator budget lane) and
`SEL_SKIP_DECIMAL_MUTATIONS=1` (the decimal mutation lane); so do
`SEL_SKIP_DB_TESTS=1` and `SEL_SKIP_PYTHON_UNIT=1`.

What the gate runs per host beyond the shared layers, and why a host is exempt
where it is:

| Lane | Hosts | Exempt, and why |
|---|---|---|
| plain vs optimised, whole conformance corpus | JS, PHP, Python (`tools/check-eval-equivalence.*`); C++ (`cpp/tests/unit.cpp`) and Go (`go/sel/eval_order_test.go`) in their unit lanes | Lisp: a fixed list of sources in `lisp/tests/unit.lisp`; Rust: only the FILTER/MAP elision probe in `rust/tests/filter_copy_elision.rs` — the plain walk is internal API there, and a whole-corpus probe is an open item for that host |
| metadata (`tools/metadata/*`: value-shape caches, record churn) | JS, PHP, Python, Lisp, C++ | Go, Rust: no metadata probe; Rust's layout and allocation counts are pinned by `rust/tests/value_layout.rs` and `*_allocations.rs`, Go has no equivalent — an open item for those hosts |
| hybrid parity on SQLite (`sql/oracle/hybrid.json`, its `application` section included) | JS, Python (in process), Lisp, Go, Rust (driver), PHP (`sqlo hybrid`, oracle lane) | C++: no SQLite in the standard library and no driver yet; held by sqlt, sqlapi and its executed-plan unit tests |
| reference builtin fragments (`examples/fn-*`) | JS, Python, PHP, Lisp (`tools/check-ref-fragments.sh`), Go (`check-go-fragments.sh`), Rust (`check-rust-fragments.sh`) | C++: its fragments compile only inside `sel.cpp` |
| clippy, warnings denied | Rust (both crates) | skipped with a note where `cargo clippy` is not installed |
| regex validator vs reference (`tools/check-regex-ambiguity-diff.sh`) | every host with an `impl_regex_verdict` driver | the JS bundles carry `js/src`'s validator verbatim |
| C++ sanitizers (`make tsan`, every race probe; `make asan`) | C++ | — (`SEL_SKIP_SANITIZERS=1` opts out) |

Manual by design, never run by the gate: `tools/stress.sh` (programs of several
hundred thousand nodes, minutes per host), `tools/check-sql-limits.php` (PHP's
own resource limits), the benchmarks under `tools/scale-test/` and
`tools/commit-benchmark/`, and `tools/oracle-db.sh` on its own (the gate starts
it).

`python-wheel` is not in the default roster because it needs building first, the
way C++ does. It runs the same suite through the *installed* package rather than
the source tree, which is the only layer that catches a packaging mistake:

```
python3 -m build --outdir dist/python
python3 -m venv python/.venv-wheel
python/.venv-wheel/bin/pip install dist/python/*.whl
SEL_EXTRA_IMPLS=python-wheel tools/check.sh
```

Individually, while iterating:

```
node js/bin/conformance.mjs           the suite, JS
php  php/bin/conformance              the suite, PHP
cpp/build/conformance                 the suite, C++
lisp/bin/conformance                  the suite, Lisp
PYTHONPATH=$PWD/python python3 python/bin/conformance.py   the suite, Python
go/build/conformance                  the suite, Go
rust/build/conformance                the suite, Rust
node js/bin/conformance.mjs conformance/07-text.selt      one file
tools/check-docs.sh                   the => examples in the docs
tools/check-examples.sh               examples/, every host, byte-identical, = output.txt
tools/check-usage.sh                  the database examples, every host, real servers (Docker)
node tools/build-docs.mjs --check     every link and anchor in the docs
tools/check-decimal.sh 20000          decimal vs Python's decimal and the exact oracle
tools/mutate-decimal.sh               break every decimal core on purpose; the checks must notice
tools/e2e.sh                          one rule set through every host API
tools/fuzz.sh 4000 12345              differential fuzz, count and seed
```

The numbers have three lanes of their own. `tools/check-decimal.sh` holds every
core to two oracles: Python's `decimal` module on narrow operands, and
`tools/decimal-oracle-exact.py`, exact rationals written from §4 at the widths
the cores hold. `conformance/24-decimal-boundaries.selt` and
`32-numeric-plans.selt` are generated from the exact oracle by
`tools/gen-decimal-cases.py` (never edit them by hand). And
`tools/mutate-decimal.sh` breaks each core on purpose -- the mutants are
`tools/decimal-mutations.json` -- and requires one of those checks to notice.

That last lane exists because an oracle only covers the inputs it generates. At
0.9.2 the C++ multiply rounded both operands to 18 fractional digits whenever
both had more, and every lane was green: the oracle of the day generated at most
12 integer and 6 fractional digits, so no product reached the broken path, and
"0 mismatches" said nothing about the inputs it never made. A mutant proves the
coverage instead of assuming it. When one survives, the fix is a generator
(`decimal-oracle-exact.py` for one operation, `gen-decimal-cases.py` for an
expression) and a regeneration, not a hand-written case; when you add a fast
path to a core, add its mutant. `tools/mutate-decimal.sh --weak` replays the
0.9.2-era checks and shows the bug class surviving them.

Narrow any of them to a subset with `SEL_IMPLS`, which is useful when you have
changed one host and want the loop tight:

```
SEL_IMPLS="js cpp" tools/fuzz.sh 4000 1
```

The fuzzer is the one that finds things nobody thought of. Run it with several
seeds before believing a change is finished:

```
for s in 1 2 3 4 5; do tools/fuzz.sh 15000 $s; done
```

And a REPL, for when you just want to poke at it:

```
node js/bin/sel.mjs
php  php/bin/sel
cpp/build/sel
lisp/bin/sel
PYTHONPATH=$PWD/python python3 -m sel
go/build/sel
rust/build/sel
```

## Writing documentation

The documentation is Markdown that reads on GitHub and is rendered into the
site by `tools/build-docs.mjs`; it is also part of the test suite, so it has
three rules:

- **An example that shows a result is executed.** Inside a ```` ```sel ```` block,
  and in a table cell's code span, `EXPRESSION  => RESULT` (two spaces before the
  arrow) is run by every host through `tools/check-docs.sh`. Write the result the
  way `bin/sel` prints it; `!E_CODE` for an error.
- **Host code is quoted, never retyped.** Wrap the code in an example file in
  `EXAMPLE-BEGIN name` / `EXAMPLE-END name` comments and quote it with
  `<!-- from: examples/dir/file#name -->` on the line before the fence;
  `tools/check-snippets.py` requires the block to be byte-identical to the
  region, dedented. A file with no regions — a `.sel` program, an `output.txt`
  transcript — is quoted whole. Every `examples/<category>/output.txt` is checked
  against what all seven hosts print.
- **Code in seven languages is a tab group**: `<!-- tabs -->`, then one
  `<details><summary>Language</summary> … </details>` per language (the first
  `open`), then `<!-- /tabs -->`. GitHub shows collapsible sections; the site
  shows tabs.

Every page is listed in `docs/nav.json`, and `node tools/build-docs.mjs --check`
fails on a broken link or anchor. The user-facing pages are the ones the
packages ship (`tools/check-package-docs.sh` holds the list); `docs/internals/`
and this page stay in the repository.

## Adding an implementation

Register it in `tools/impls.sh` — an arm in every role function there
(`tools/check-registry.sh` fails until each has one) — and give it the entry
points `tools/README.md` requires: a conformance runner, a corpus batch runner,
an e2e driver, an API-parity probe and a decimal-oracle checker, then the SQL
layer's runners. Everything else in `tools/` iterates that list, so nothing else
needs changing — except the prose: add the host to the map above, to the
commands in `CLAUDE.md` and in this page, and to the package description
(`tools/check-roster.py` and `tools/check-version.sh` fail until you do). The `.selt` and corpus
formats are line-oriented precisely so that a new port needs no parser beyond
the one it is already writing.

Go and Rust are the most recent hosts; their history is a reasonable template for
the shape and order of the work. Port in
dependency order — errors, UTF-8, decimal, value, registry, lexer, parser,
evaluator, built-ins, host API — and get `tools/check-decimal.sh` green before
anything depends on the decimal core, because it is the slowest layer to debug
afterwards.
