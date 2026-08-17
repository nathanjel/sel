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

| Concern | JS | PHP | C++ (`cpp/sel.cpp`) | Lisp |
|---|---|---|---|---|
| Errors | `js/src/errors.mjs` | `php/src/SelError.php` | `--- errors` | `lisp/src/errors.lisp` |
| UTF-8 codec | `js/src/utf8.mjs` | `php/src/Utf8.php` | `--- utf8` | `lisp/src/utf8.lisp` |
| Exact decimal | `js/src/decimal.mjs` | `php/src/Dec.php` | `--- decimal` | `lisp/src/decimal.lisp` |
| The value | `js/src/value.mjs` | `php/src/Value.php` | `--- value` | `lisp/src/value.lisp` |
| Function table | `js/src/registry.mjs` | `php/src/Registry.php` | `--- registry` | `lisp/src/registry.lisp` |
| Tokeniser | `js/src/lexer.mjs` | `php/src/Lexer.php` | `--- lexer` | `lisp/src/lexer.lisp` |
| Parser | `js/src/parser.mjs` | `php/src/Parser.php` | `--- parser` | `lisp/src/parser.lisp` |
| Evaluator | `js/src/eval.mjs` | `php/src/Evaluator.php` | `--- eval` | `lisp/src/eval.lisp` |
| Argument framework | `js/src/eval.mjs` (`Args`) | `php/src/Args.php` | `--- eval` (`Args`) | `lisp/src/eval.lisp` (`args-*`) |
| Built-ins | `js/src/builtins/*.mjs` | `php/src/Builtins/*.php` | `--- builtins` | `lisp/src/builtins/*.lisp` |
| Host API | `js/src/sel.mjs` | `php/src/Sel.php` | `cpp/sel.hpp` | `lisp/src/sel.lisp` |

PHP has no autoloader; add any new file to `php/src/bootstrap.php`. JS built-ins
are imported from `js/src/builtins/index.mjs`. Both must happen before parsing,
because unknown function names are a **compile-time** error.

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

Both lanes are worked below, end to end, in all four implementations. Neither
example is part of core SEL, so both can be lifted as-is.

---

### Lane A — a strict function

Worked example: `ORD_SUFFIX(n)`, returning `1st`, `2nd`, `3rd`, `4th`.

#### 1. Spec

Add a row to the right table in `spec/SPEC.md` §7. If it can fail in a new way,
add the code to `spec/errors.md` — reuse an existing code unless the new
condition is genuinely distinct.

```markdown
| `ORD_SUFFIX(n)` | TEXT — `n` with its English ordinal suffix; `n` must be a non-negative integer |
```

#### 2. Conformance cases

In the matching `conformance/*.selt`. Write these before the code — they should
fail for the right reason first.

```
### name: txt.ord-suffix.basic
--- source
ORD_SUFFIX(1) & " " & ORD_SUFFIX(2) & " " & ORD_SUFFIX(3) & " " & ORD_SUFFIX(4)
--- expect
text "1st 2nd 3rd 4th"
===
### name: txt.ord-suffix.teens
--- source
ORD_SUFFIX(11) & " " & ORD_SUFFIX(12) & " " & ORD_SUFFIX(13)
--- expect
text "11th 12th 13th"
===
### name: txt.ord-suffix.rejects-fraction
--- source
ORD_SUFFIX(1.5)
--- expect
error E_NOT_INT
===
```

#### 3. JS

In the relevant `js/src/builtins/*.mjs`:

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

Note what is *not* there: no argument count check, no type check, no `eval` call,
no try/catch. `nonNegInt(0)` evaluates argument 0 once, requires it to be a whole
number ≥ 0, and raises `E_NOT_INT` or `E_RANGE` against **that argument's**
source position if it is not.

#### 4. PHP

In the matching `php/src/Builtins/*.php`, inside `register()`:

```php
Registry::define(['name' => 'ORD_SUFFIX', 'min' => 1, 'max' => 1,
    'fn' => static function (Args $a): Value {
        $n = $a->nonNegInt(0);
        $tens = $n % 100;
        if ($tens >= 11 && $tens <= 13) {
            return Value::text("{$n}th");
        }
        return Value::text($n . match ($n % 10) { 1 => 'st', 2 => 'nd', 3 => 'rd', default => 'th' });
    }]);
```

#### 5. C++

In the matching section of `cpp/sel.cpp`, inside the relevant `register_*()`:

```cpp
define(Spec{"ORD_SUFFIX", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
              const long long n = a.non_neg_int(0);
              const long long tens = n % 100;
              if (tens >= 11 && tens <= 13) return make_text(std::to_string(n) + "th");
              switch (n % 10) {
                case 1: return make_text(std::to_string(n) + "st");
                case 2: return make_text(std::to_string(n) + "nd");
                case 3: return make_text(std::to_string(n) + "rd");
                default: return make_text(std::to_string(n) + "th");
              }
            }});
```

#### 6. Common Lisp

In the matching `lisp/src/builtins/*.lisp`:

```lisp
(define-builtin "ORD_SUFFIX" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (let* ((n (args-non-neg-int a 0))
           (tens (mod n 100)))
      (%text (format nil "~d~a" n
                     (if (<= 11 tens 13)
                         "th"
                         (case (mod n 10) (1 "st") (2 "nd") (3 "rd") (t "th"))))))))
```

A new file must be added to the `:components` list in `lisp/sel.asd`, and to
`php/src/bootstrap.php` on the PHP side — neither has an autoloader.

#### 7. Check

```
tools/check.sh
```

---

### Lane B — a lazy (AST) function

A lazy function declares `lazy: true` and reads argument **nodes** instead of
values. Nothing is evaluated for it; it decides what to evaluate, when, and how
many times. The smallest possible example is `IF`, which is why `IF` needs no
syntax:

```js
define({
  name: 'IF', min: 2, max: 3, lazy: true,
  fn: (args) => {
    if (args.bool(0)) return args.val(1);        // arg 2 is never evaluated
    if (args.count() === 3) return args.val(2);
    return Value.text('');
  },
});
```

The interesting half is a function that evaluates one body argument **once per
element**, with a name bound to that element — an aggregate. Worked example:

**`FIRST(list, body)`** — the first element for which `body` is `TRUE`, or TEXT
`""` if none matches. Like `FILTER`, but it stops at the first hit and returns
the element rather than a list. The three-argument form `FIRST(list, X, body)`
names the binder, exactly as the core aggregates do.

```sel
FIRST((1, 8, 3, 9), _ > 5)                       # the element 8
FIRST(ITEMS, IT, IT["QTY"] > 0)["SKU"]           # first line that has a quantity
FIRST((1, 2), _ > 5)                             # "" — nothing matched
```

Four things this has to get right, and all four are the reason Lane B is not the
default:

1. **Declare `binds: true`.** That is what tells `dependencies()` the second
   argument of the three-argument form is a *binder*, not a variable being read.
   Without it, `FIRST(ITEMS, IT, IT["QTY"] > 0)` reports `IT` as an input field
   the host is expected to supply.
2. **Push a frame per element, and pop it on the way out — including when the
   body raises.** A body that fails must not leave the binder in scope for
   whatever runs next.
3. **Bind `_K` as well as the element**, so the body can see the key.
4. **Handle the no-children cases** the way §7.3 specifies: a scalar behaves as a
   one-element list containing itself, and a childless NONE is genuinely empty.
   The second case is what `FILTER` returns when nothing matched.

#### Conformance cases

```
### name: agg.first.matches
--- source
FIRST((1, 8, 3, 9), _ > 5)
--- expect
num 8
===
### name: agg.first.no-match-is-empty-text
--- source
FIRST((1, 2), _ > 5)
--- expect
text ""
===
### name: agg.first.stops-at-the-first-hit
--- note
The body must not run for later elements — 1/0 would raise if it did.
--- source
FIRST((8, 1), IF(_ > 5, TRUE, 1/0 == 1))
--- expect
num 8
===
### name: agg.first.named-binder
--- source
FIRST((1, 8), X, X > 5)
--- expect
num 8
===
### name: agg.first.body-must-be-bool
--- source
FIRST((1, 8), _)
--- expect
error E_NOT_BOOL
===
```

#### JS

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

#### PHP

```php
Registry::define(['name' => 'FIRST', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
    'fn' => static function (Args $a, Context $ctx): Value {
        $three = $a->count() === 3;
        $binder = $three ? $a->symbol(1) : '_';
        $body = $a->node($three ? 2 : 1);

        $list = $a->val(0);
        $items = $list->size() > 0 ? $list->entries()
            : ($list->kind === Value::NONE ? [] : [['1', $list]]);

        foreach ($items as [$key, $item]) {
            $ctx->pushFrame([$binder => $item, '_K' => Value::text($key)]);
            try {
                if ($a->evalNode($body)->asBool($body['pos'])) {
                    return $item->copy();
                }
            } finally {
                $ctx->popFrame();
            }
        }
        return Value::text('');
    }]);
```

#### C++

```cpp
define(Spec{"FIRST", 2, 3, /*lazy=*/true, /*binds=*/true, nullptr,
            [](Args& a, Context& ctx) -> Value {
              const bool three = a.count() == 3;
              const std::string binder = three ? a.symbol(1) : std::string("_");
              const Node& body = a.node(three ? 2 : 1);

              for (const auto& [key, item] : elements(a.val(0))) {
                ctx.frames.push_back({{binder, item}, {"_K", make_text(key)}});
                bool hit = false;
                try {
                  hit = a.eval(body).as_bool(body.pos);
                } catch (...) {
                  ctx.frames.pop_back();   // C++ has no `finally`
                  throw;
                }
                ctx.frames.pop_back();
                if (hit) return item;
              }
              return make_text("");
            }});
```

`elements()` is the shared helper next to the core aggregates; it is the
no-children rule from point 4 above, written once.

#### Common Lisp

```lisp
(define-builtin "FIRST" 2 3
  (lambda (a ctx)
    (let* ((three (= (args-count a) 3))
           (binder (if three (args-symbol a 1) "_"))
           (body (args-node a (if three 2 1)))
           (list (args-val a 0))
           (items (cond ((plusp (value-size list)) (value-entries list))
                        ((eq (value-kind list) :none) '())
                        (t (list (cons "1" list))))))
      (loop for (key . item) in items
            do (ctx-push-frame ctx (list (cons binder item)
                                         (cons "_K" (make-text key))))
               (let ((hit (unwind-protect
                               (as-bool (args-eval a body) (node-pos body))
                            (ctx-pop-frame ctx))))
                 (when hit (return (value-copy item))))
            finally (return (make-text "")))))
  :lazy t :binds t)
```

All four return `t"8"` for `FIRST((1, 8, 3, 9), _ > 5)`, `t""` for no match, and
`E_NOT_BOOL` at column 14 for `FIRST((1,8), _)` — which is what
`tools/check.sh` is for.

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
| `.size` (JS) / `->size()` (PHP) | child count |
| `.get/set/has/keys/values/entries` | children, insertion-ordered |
| `.asText/asBytes/asBool/asDecimal(pos)` | applies scalar context, throws on mismatch |
| `.scalarSource(pos)` | the value supplying the scalar |
| `.clone()` (JS) / `->copy()` (PHP) | deep copy — assignment uses this |
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

**4. Every tokeniser** — `OPERATORS` in `js/src/lexer.mjs` and
`php/src/Lexer.php`, `operators()` in `cpp/sel.cpp`, `+operators+` in
`lisp/src/lexer.lisp`. Same list, same order, longest first. Getting this wrong
makes `//` lex as two `/` tokens and the failure will look like a parser bug.

**5. Every parser** — `parseMultiplicative` already loops over a list, so:

```js
parseMultiplicative() { return this.parseOpBinary(['*', '/', '%', '//'], () => this.parseUnary()); }
```

A *new* precedence level means a new method in every parser, wired into the
chain in the same place, and mirrored in `grammar.md`.

**6. Every evaluator** — a branch in `evalBinary` / `eval_binary` /
`eval-binary`. Use the operand's own position for type errors and the operator's
for arithmetic ones:

```js
case '//': return Value.num(D.trunc(D.div(l.asDecimal(lp), r.asDecimal(rp), node.pos)));
```

In C++ and Lisp, bind the two coerced operands to named locals first. Writing
them as two arguments to one call leaves their order unspecified in C++, and
which operand's position an error reports is observable — see the traps.

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

**Watch C++ pointer invalidation.** `Value`'s children live in a `std::vector`,
so a pointer into it dies when the tree grows. The other hosts can hold a live
reference to an assignment target across the evaluation of the right-hand side;
C++ resolves the target to a *path* and walks it again afterwards. Index
expressions are still evaluated exactly once, in order, before the right-hand
side — that ordering is observable too.

**Watch cl-ppcre's anchors.** It follows Perl, where `$` also matches before a
trailing newline — the same reason the PHP host needs PCRE's `D` modifier. The
Lisp host lowers `^` and `$` to `\A` and `\z` in its rewrite pass. It also does
not apply the *simple* case folding that ECMAScript's `iu` and PCRE2's `ui` both
do, so the two non-ASCII code points that fold to ASCII letters (U+212A and
U+017F) are folded in the subject before matching, and group text and
replacements are sliced from the original.

**Watch `E_DEPTH`.** Every host caps parse and evaluation nesting at 200. If you
add recursion, it must be counted, or a hostile rule becomes a stack overflow.

---

## Running the checks

```
tools/check.sh                 everything, in order
```

C++ has to be built first, or it is skipped with a note:

```
cd cpp && make            builds build/{sel,conformance,batch,e2e,check-decimal}
cd cpp && make test       unit tests, then the suite
lisp/bin/test             the Lisp unit tests
```

Individually, while iterating:

```
node js/bin/conformance.mjs           the suite, JS
php  php/bin/conformance              the suite, PHP
cpp/build/conformance                 the suite, C++
lisp/bin/conformance                  the suite, Lisp
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
```

## Adding an implementation

Register it in `tools/impls.sh` and give it the four entry points described in
`tools/README.md` — a conformance runner, a corpus batch runner, an e2e driver
and a decimal-oracle checker. Everything else in `tools/` iterates that list, so
nothing else needs changing. The `.selt` and corpus formats are line-oriented
precisely so that a new port needs no parser beyond the one it is already
writing.
