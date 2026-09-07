# Extending SEL

How to add a function or an operator, and what to watch out for while doing it.

Read [the invariance traps](#the-traps) before you write anything. Every one of
them has already caught someone on this codebase.

- [The rule](#the-rule)
- [Where everything lives](#where-everything-lives)
- [Adding a function](#adding-a-function)
- [The Args API](#the-args-api)
- [The Value API](#the-value-api)
- [Adding an operator](#adding-an-operator)
- [Writing conformance cases](#writing-conformance-cases)
- [The traps](#the-traps)
- [Running the checks](#running-the-checks)

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
tools/check.sh   all green, or it isn't done
```

Never implement in one host and "port it later". The implementations exist to
disagree with each other; that only works if they arrive together. A feature that
lives in one host for a week is a feature nobody has tested.

No implementation is the reference. When they disagree, `spec/` and
`conformance/` decide which is wrong.

---

## Where everything lives

The hosts are deliberately structured the same, file for file, so they can be
read side by side. C++ is one translation unit, so its column names the section
comment (`// --- decimal`) rather than a file.

| Concern | JS | PHP | C++ (`cpp/sel.cpp`) | Lisp | Python |
|---|---|---|---|---|---|
| Errors | `js/src/errors.mjs` | `php/src/SelError.php` | `--- errors` | `lisp/src/errors.lisp` | `python/sel/errors.py` |
| UTF-8 codec | `js/src/utf8.mjs` | `php/src/Utf8.php` | `--- utf8` | `lisp/src/utf8.lisp` | `python/sel/utf8.py` |
| Exact decimal | `js/src/decimal.mjs` | `php/src/Dec.php` | `--- decimal` | `lisp/src/decimal.lisp` | `python/sel/decimal.py` |
| The value | `js/src/value.mjs` | `php/src/Value.php` | `--- value` | `lisp/src/value.lisp` | `python/sel/value.py` |
| Function table | `js/src/registry.mjs` | `php/src/Registry.php` | `--- registry` | `lisp/src/registry.lisp` | `python/sel/registry.py` |
| Tokeniser | `js/src/lexer.mjs` | `php/src/Lexer.php` | `--- lexer` | `lisp/src/lexer.lisp` | `python/sel/lexer.py` |
| Parser | `js/src/parser.mjs` | `php/src/Parser.php` | `--- parser` | `lisp/src/parser.lisp` | `python/sel/parser.py` |
| Evaluator | `js/src/eval.mjs` | `php/src/Evaluator.php` | `--- eval` | `lisp/src/eval.lisp` | `python/sel/eval.py` |
| Argument framework | `js/src/eval.mjs` (`Args`) | `php/src/Args.php` | `--- eval` (`Args`) | `lisp/src/eval.lisp` (`args-*`) | `python/sel/eval.py` (`Args`) |
| Built-ins | `js/src/builtins/*.mjs` | `php/src/Builtins/*.php` | `--- builtins` | `lisp/src/builtins/*.lisp` | `python/sel/builtins/*.py` |
| Host API | `js/src/sel.mjs` | `php/src/Sel.php` | `cpp/sel.hpp` | `lisp/src/sel.lisp` | `python/sel/__init__.py` |

PHP has no autoloader; add any new file to `php/src/bootstrap.php`. JS built-ins
are imported from `js/src/builtins/index.mjs` and Python's from
`python/sel/builtins/__init__.py`. All three must happen before parsing, because
unknown function names are a **compile-time** error.

**All five parsers are precedence climbing**, and the table is true at the
function level as well as the file level: `parse_program` → `parse_sequence` →
`parse_list` → `parse_term` → `parse_prefix` → `parse_postfix` → `parse_primary`
in every host, with the sixteen precedence levels of `spec/SPEC.md` §5 as a pair
of lookup tables rather than sixteen functions. `python/sel/parser.py` was the
pilot and its module docstring is the rationale; the other four were transcribed
from it, one host at a time.

Nothing differs between the hosts now. All five parse by precedence climbing,
and all five have the SEL→SQL layer; `docs/SQL-TRANSLATION.md` is the design and
`sql/cases/*.sqlt` grades every one of them against it.

---

## Adding a function

Most extensions are a function, and a function is cheap: one table entry per host
plus cases. No grammar changes, no new node types, no parser work.

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

Both lanes are worked below, end to end, in all five implementations. Neither
example is part of core SEL, so both can be lifted as-is.

---

### Lane A — a strict function

Worked example: `ORD_SUFFIX(n)`, returning `1st`, `2nd`, `3rd`, `4th`.

All of it — the spec row, the conformance cases, and one file per host — is in
**[examples/fn-simple/](../examples/fn-simple/)**, whose README carries the order
of work and the three places that have no autoloader. The JS:

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

`php.php`, `cpp.cpp`, `lisp.lisp` and `python.py` sit beside it, saying the same
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
again in all five hosts with its cases, in
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
    const items = list.size > 0 ? list.entries()
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
a precedence level, a grammar production, a spec change, and a line in two
tokenisers, where a function costs one table entry. Add one only when the thing
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

**4. Every tokeniser** — `OPERATORS` in `js/src/lexer.mjs`,
`php/src/Lexer.php` and `python/sel/lexer.py`, `operators()` in `cpp/sel.cpp`,
`+operators+` in `lisp/src/lexer.lisp`. Same list, same order, longest first.
Getting this wrong makes `//` lex as two `/` tokens and the failure will look
like a parser bug.

**5. Every parser** — a row in a table, in all five, which is the whole point of
the migration that finished:

```python
INFIX_OPS = { ..., '//': (BP_MUL, 'L'), ... }        # python
```
```js
const INFIX_OPS = new Map([ ..., ['//', [BP_MUL, 'L']], ... ]);   // js
```
```php
private const INFIX_OPS = [ ..., '//' => [self::BP_MUL, 'L'], ... ];   // php
```
```cpp
static const std::map<std::string, Infix> ops = { ..., {"//", {BP_MUL, 'L'}}, ... };
```
```lisp
(setf (gethash "//" m) (cons +bp-mul+ #\L))          ; lisp
```

A *new* precedence level is a new `BP_` constant with the ones above it
renumbered — no new function, and nothing to wire into a chain. A **word**
operator goes in the second table (`INFIX_WORDS`), because words lex as
identifiers and symbols as ops, so they cannot share a key space.

Three things the tables get wrong quietly, none of which the compiler catches,
because each produces *a valid parse of the wrong tree*:

- **Associativity is three-valued.** `L` parses its right side at `bp + 1`, `R`
  at `bp` — that is what makes it right-associative — and `N` at `bp + 1` and
  then rejects a second operator at the same level. A comparison is `N`, and its
  `E_SYNTAX` is reported at the **second** operator.
- **A prefix operator is not a primary.** `NOT` binds at 7 and unary `-` at 15,
  and `parse_prefix` accepts each only when the caller's `min_bp` reaches it.
  Putting them in `parse_primary`, where textbook precedence climbing puts them,
  makes `NOT a == b` parse as `(NOT a) == b` and breaks two of the depth pins at
  the same time.
- **The list keys are compared as strings.** Lisp's table needs
  `:test #'equal`; with the default `eql` no operator ever matches and every
  program is a syntax error at its own first operator. JS uses a `Map` rather
  than an object so that a token spelled like a name on `Object.prototype`
  cannot answer for a real operator.

**6. Every evaluator** — a branch in `evalBinary` / `eval_binary` /
`eval-binary`. Use the operand's own position for type errors and the operator's
for arithmetic ones:

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
it did not name — `>=` in four of them, `FALSE` in JS — so forgetting this
second edit produced wrong answers rather than an error. All five now refuse
with `E_SYNTAX unknown comparison operator`, which is what you will see if you
skip it.

**7. `Program.dependencies()`** — nothing to do for a binary operator; `bin`
nodes are already walked. A node type that binds names is a different story.

**8. `tools/gen-programs.mjs`** — add it to `ARITH` or the relevant list so the
fuzzer exercises it. An operator the fuzzer never emits is an operator with no
differential coverage.

**9. `docs/LANGUAGE.md`** — the precedence table and a `=>` example, which the
doc checker will then run.

Reserved **words** (`AND`, `EQL`, …) are lexed as identifiers and handled in the
parser, so they also need adding to the reserved list in every lexer, or they
stay usable as variable names.

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
because that host is the newest and its traps are the least worn-in. Both hosts
whose regex engine follows Perl — Lisp and Python — appear in both groups.

### Any host

**Never use the host's regex flags naively.** PHP's `u` modifier turns on PCRE2's
UCP, so `\d` matches Arabic-Indic digits and `\w` matches `é`; ECMAScript's `u`
does not. Both hosts therefore *rewrite* `\d`, `\w`, `\s` into explicit ASCII
classes before compiling. `\b` had to be refused outright, because a word
boundary is defined in terms of the engine's word characters and no rewrite fixes
that. `\v` means "any vertical whitespace" in PCRE and U+000B in ECMAScript.

**Never use the host's string length or indexing.** PHP counts bytes, JS counts
UTF-16 units, C++ `std::string` counts bytes, SEL counts code points. Use
`Utf8::chars()` / `toCodePoints()` / `decode_utf8()`. `preg_*` returns byte
offsets and JS returns UTF-16 offsets — convert both with `cpIndex`. Lisp and the
C++ `u32string` paths are already code points, which is exactly why it is easy to
forget that the others are not.

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
expression just created. Every host aliases by default and copies at exactly
five places: `,` collecting a child, `,` collecting a value, the assignment
store, `MAP` collecting a result, and `FILTER` collecting an element. If you add
a built-in that stores one value inside another, it belongs on that list, and if
you add a copy anywhere else you have invented a divergence.

C++ is the host where this is easy to get wrong, because `Value` is a handle
over a `shared_ptr` and copying it *looks* like a deep copy. It is not: use
`clone()`. Up to and including 0.2.0 the C++ `Value` really did deep-copy on
assignment, which made it disagree with the other four in six ways — three
`E_NO_KEY`s where an index expression created the key its own base then read,
an `E_NO_SCALAR` from a compound assignment reading its target across the
right-hand side, a wrong tree from an aggregate binder that named a copy rather
than the element, and one confidently wrong number. There is no
cycle collector behind the handle, so the five clone sites are also what stops
`A[1] = A` from leaking; `cd cpp && make asan` runs the suite under the leak
checker to keep that true.

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

## Running the checks

```
tools/check.sh                 everything, in order
```

C++ has to be built first, or it is skipped with a note:

```
cd cpp && make            builds build/{sel,conformance,batch,e2e,api,ast,check-decimal,unit}
cd cpp && make test       unit tests, then the suite
lisp/bin/test             the Lisp unit tests
PYTHONPATH=$PWD/python pytest python/tests    the Python unit tests
```

`python-wheel` is not in the default roster because it needs building first, the
way C++ does. It runs the same suite through the *installed* package rather than
the source tree, which is the only layer that catches a packaging mistake:

```
python3 -m build --outdir dist/python
python3 -m venv python/.venv-wheel
python/.venv-wheel/bin/pip install dist/python/*.whl
SEL_IMPLS="python-wheel" tools/check.sh
```

Individually, while iterating:

```
node js/bin/conformance.mjs           the suite, JS
php  php/bin/conformance              the suite, PHP
cpp/build/conformance                 the suite, C++
lisp/bin/conformance                  the suite, Lisp
PYTHONPATH=$PWD/python python3 python/bin/conformance.py   the suite, Python
node js/bin/conformance.mjs conformance/07-text.selt      one file
tools/check-docs.sh                   the => examples in the docs
tools/check-decimal.sh 20000          decimal vs Python's decimal
tools/e2e.sh                          one rule set through every host API
tools/fuzz.sh 4000 12345              differential fuzz, count and seed
```

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
```

## Adding an implementation

Register it in `tools/impls.sh` and give it the five entry points described in
`tools/README.md` — a conformance runner, a corpus batch runner, an e2e driver,
an API-parity probe and a decimal-oracle checker. Everything else in `tools/`
iterates that list, so nothing else needs changing. The `.selt` and corpus
formats are line-oriented precisely so that a new port needs no parser beyond
the one it is already writing.

The Python host is the most recent one and was written from the JS sources; its
commit is a reasonable template for the shape and order of the work. Port in
dependency order — errors, UTF-8, decimal, value, registry, lexer, parser,
evaluator, built-ins, host API — and get `tools/check-decimal.sh` green before
anything depends on the decimal core, because it is the slowest layer to debug
afterwards.
