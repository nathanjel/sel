# SEL — Simple Expression Language

**Version 0.1 (draft).** This document is normative. Where an implementation and
this document disagree, the implementation is wrong. Where this document and
`conformance/` disagree, that is a bug in one of them and must be resolved here
first.

---

## 1. Nature of the language

SEL has **no statements**. A program is one expression. Everything that looks
like control flow is a function call.

Functions do **not** receive values. They receive the caller's **AST nodes**, and
decide for themselves whether to evaluate each one, when, and how many times.
`IF` evaluates its first argument, then exactly one of the other two. `ALL`
evaluates its second argument once per element of its first. Neither is a
keyword; both are ordinary entries in the function table.

Two consequences shape the whole grammar:

- `;` and `,` are **ordinary binary operators**, not syntax. A block and an
  argument list are the same kind of tree, so `IF(c, (a; b), d)` needs no special
  parsing — the parenthesised sequence is just another operand. (`;` binds looser
  than `,`, so those inner parentheses are required; see `grammar.md`.)
- There is no `return`, no `break`, no `continue`, no loop, and no user-defined
  function. Every expression yields a value; the last one yields the program's.

SEL is designed for one job: validating data identically on a PHP backend and a
JS frontend. It prefers failing loudly over coercing quietly.

---

## 2. Source text

Source is UTF-8. Invalid UTF-8 in source is `E_UTF8` at the offending byte.

### 2.1 Comments

`#` begins a comment that runs to the end of the line. `#` inside a string
literal is not a comment.

### 2.2 Whitespace

Space (U+0020), tab (U+0009), CR (U+000D) and LF (U+000A) separate tokens and are
otherwise insignificant. No other character is whitespace.

### 2.3 Identifiers

```
identifier = (letter | "_") { letter | digit | "_" }
letter     = "A".."Z" | "a".."z"
digit      = "0".."9"
```

Identifiers are **ASCII only** and **case-insensitive**. They are canonicalised
to upper case internally, so `total`, `Total` and `TOTAL` are one name. This
applies to variables, function names and aggregate binders alike.

`TRUE`, `FALSE`, `AND`, `OR`, `NOT`, `XOR`, `EQL`, `IN`, `BAND`, `BOR`, `BXOR` are
reserved and may not be used as variable names (`E_RESERVED`).

### 2.4 Number literals

```
number = digit { digit } [ "." digit { digit } ]
```

No sign (use unary `-`), no exponent, no leading `.`, no trailing `.`.
`007` is valid and canonicalises to `7`. See §4.

### 2.5 Text literals

Two forms.

**Quoted, `"…"`** — supports escapes and interpolation.

| Escape | Means |
|---|---|
| `\\` | `\` |
| `\"` | `"` |
| `\n` `\t` `\r` | LF, TAB, CR |
| `\{` `\}` | literal brace |
| `\u{H…}` | code point, 1–6 hex digits |

Any other `\x` is `E_ESCAPE`. `\u{…}` above U+10FFFF or in the surrogate range
D800–DFFF is `E_RANGE`. An unterminated literal is `E_UNTERMINATED`.

**Raw, `'…'`** — no escapes, no interpolation. `''` inside means one `'`.
Everything else is literal. This form exists for regex patterns:
`'^\d{3}-\d{2}$'` needs no backslash doubling.

### 2.6 Interpolation

Inside a quoted literal, `{` … `}` contains a full SEL expression. The lexer
rewrites the literal into a concatenation before parsing proper:

```
"total: {A + 1}."     ==>     "total: " & (A + 1) & "."
```

This is a lexer pass, not a runtime feature; the resulting tree contains no trace
of it. Braces nest, and braces inside a string literal within the expression do
not terminate it. An unterminated `{` is `E_UNTERMINATED`. `{}` with nothing in
it is `E_SYNTAX`.

Interpolation lowers to `&` and obeys §5.2 exactly — there is no separate rule.
Leading and trailing segments are kept even when empty, so `"{A}"` becomes
`"" & A & ""`. That costs two no-op concatenations and buys uniformity: an
interpolation always applies scalar context and always rejects BOOL, and a
literal interpolating a BIN value yields BIN just as `&` would.

---

## 3. Values

Every value is one node:

```
Value {
  kind      : NONE | TEXT | BIN | BOOL
  scalar    : characters | bytes | boolean | (absent when NONE)
  children  : ordered map from text key to Value
}
```

A value may have a scalar, children, both, or neither. Nothing in the language
branches on "is this a list" — see §3.2.

### 3.1 Kinds

- **TEXT** — a sequence of Unicode code points.
- **BIN** — a sequence of bytes, 0–255. Not text; not printable by assumption.
- **BOOL** — `TRUE` or `FALSE`. A distinct kind, not a string and not a number.
  This is what makes strictness cheap: every truth test is a kind check.
- **NONE** — no scalar of its own. A list built by `,` is NONE with children.

TEXT and BIN interconvert **through UTF-8 and nothing else**. There is no
encoding parameter anywhere in the language. Converting BIN to TEXT decodes
UTF-8 and raises `E_UTF8` if the bytes are not valid UTF-8 (including
overlong forms, surrogates encoded as CESU-8, and code points above U+10FFFF).
Converting TEXT to BIN always succeeds.

### 3.2 Scalar context

```
asScalar(v):
    if v.kind is not NONE      -> v.scalar
    else if v has children     -> asScalar(first child in insertion order)
    else                       -> E_NO_SCALAR
```

This is why `A[1] = 3` makes `A == 3` true, and why returning several values is
free: the caller reads `R` for the first and `R[2]`, `R[3]` for the rest.

### 3.3 Keys and ordering

Keys are text. Children keep **insertion order**; re-assigning an existing key
keeps its original position. Order is observable through `INDEXES`, `JOIN`,
`MAP`, and the conformance dump, so it is normative.

Indexing uses the index expression's scalar **verbatim as the key**, with no
numeric normalisation: `A[1]` reads key `"1"`, and `A[1.0]` reads key `"1.0"`,
which is a different key. Lists built by `,` use keys `"1"`, `"2"`, … so integer
indexing works as expected.

Reading a missing key is `E_NO_KEY`. Reading an undefined variable is
`E_UNDEF_VAR` — never an empty string. Use `HAS(x, key)` to test.

Assigning to `A[k]` creates `A` and any intermediate levels if absent.

---

## 4. Numbers

There are no floating-point numbers in SEL. A number is a TEXT value whose
content matches:

```
-? digit {digit} [ "." digit {digit} ]
```

Arithmetic is exact decimal, computed on digit strings. **No implicit trimming**:
`" 2"` is `E_NOT_NUM`; call `TRIM` first. BOOL and BIN are never numbers
(`E_NOT_NUM`).

### 4.1 Canonical form

Leading zeros in the integer part are removed, leaving at least one digit.
Trailing zeros in the fraction are **kept** — scale is part of the value. A zero
value never carries a minus sign.

```
007      -> 7
2.50     -> 2.50        (scale 2, not "2.5")
-0.00    -> 0.00
```

### 4.2 Scale of results

Let `sa`, `sb` be the operand scales.

| Op | Result scale |
|---|---|
| `+` `-` | `max(sa, sb)` |
| `*` | `sa + sb` |
| `%` | `max(sa, sb)` |
| `/` | see §4.3 |

So `2.50 + 2.50` is `5.00`, and `1.5 * 1.5` is `2.25`. Money keeps its cents.

### 4.3 Division

`DIV_SCALE` is **10**.

Long division runs to at most `DIV_SCALE` fractional digits. If the remainder
reaches zero at or before that point, the exact quotient is returned at its
**minimal** scale. Otherwise the result is rounded to exactly `DIV_SCALE`
fractional digits, half away from zero.

```
4 / 2      -> 2
10 / 4     -> 2.5
1 / 8      -> 0.125
1 / 3      -> 0.3333333333
2 / 3      -> 0.6666666667
```

Division or modulo by zero is `E_DIV_ZERO`.

`%` is the remainder of truncated division and takes the sign of the dividend:
`5 % 3` is `2`, `-5 % 3` is `-2`, `5.5 % 2` is `1.5`.

### 4.4 Rounding

Every rounding in SEL — `ROUND`, and the inexact case of `/` — is **half away
from zero**. `ROUND(2.5, 0)` is `3`; `ROUND(-2.5, 0)` is `-3`.

### 4.5 Comparison

`==`, `!=`, `<`, `<=`, `>`, `>=` compare numerically after aligning scales, so
`"5.00" == "5"` is `TRUE`. The `$` family compares text, so `"5.00" $== "5"` is
`FALSE`. Both are true statements about the same pair of values, which is the
point of having two families.

---

## 5. Operators

Tightest binding first. Same-row operators associate as marked.

| # | Operators | Assoc | Notes |
|---|---|---|---|
| 1 | `x[k]` `f(…)` `( )` | left | indexing, call, grouping |
| 2 | `-x` | prefix | numeric negation |
| 3 | `*` `/` `%` | left | |
| 4 | `+` `-` | left | |
| 5 | `&` | left | concatenation |
| 6 | `BAND` | left | BIN, equal length |
| 7 | `BXOR` | left | BIN, equal length |
| 8 | `BOR` | left | BIN, equal length |
| 9 | `==` `!=` `<` `<=` `>` `>=` `$==` `$!=` `$<` `$<=` `$>` `$>=` `EQL` `IN` | **none** | |
| 10 | `NOT x` | prefix | |
| 11 | `AND` | left | short-circuit |
| 12 | `XOR` | left | |
| 13 | `OR` | left | short-circuit |
| 14 | `=` `+=` `-=` `*=` `/=` `%=` `&=` | right | |
| 15 | `,` | left | list build / argument separator |
| 16 | `;` | left | sequence |

Two deliberate choices:

**Comparisons are non-associative.** `a < b < c` is `E_SYNTAX` at parse time
rather than a confusing `E_NOT_BOOL` at runtime.

**`NOT` binds looser than comparison.** `NOT a == b` means `NOT (a == b)`, which
is what it reads like. This differs from C-family `!`.

### 5.1 Arithmetic — `+` `-` `*` `/` `%` and unary `-`

Both operands must be numbers (§4). Anything else is `E_NOT_NUM`.

### 5.2 Concatenation — `&`

Operands must be TEXT, BIN, or numbers. If both are TEXT the result is TEXT; if
either is BIN the result is BIN, with TEXT operands encoded as UTF-8. BOOL is
`E_NOT_TEXT`.

### 5.3 Text comparison — `$==` `$!=` `$<` `$<=` `$>` `$>=`

Both operands are taken as bytes (TEXT via UTF-8) and compared **bytewise**. For
valid UTF-8 this is code-point order. It is specified as byte order because JS
strings compare in UTF-16 order natively, which disagrees above U+FFFF; an
implementation must not use its host's native comparison.

### 5.4 Deep comparison — `EQL`, `IN`

`a EQL b` is `TRUE` when both have the same kind, equal scalars (BIN compared
bytewise, TEXT bytewise, numbers **not** normalised — `EQL` is structural), and
children with the same keys **in the same order**, pairwise `EQL`.

`x IN list` is `TRUE` when some child of `list` is `EQL` to `x`. If `list` has no
children it is compared directly against `x`.

### 5.5 Logic — `AND` `OR` `NOT` `XOR`

Operands must be BOOL (`E_NOT_BOOL`). There is no truthiness: `IF(name, …)` is an
error, not a shortcut. Write `IF(name $!= "", …)`.

`AND` and `OR` **short-circuit**: `FALSE AND (1/0)` is `FALSE`, not an error.
`XOR` evaluates both.

### 5.6 Bitwise — `BAND` `BOR` `BXOR`

Both operands must be BIN of **equal length** (`E_NOT_BIN`, `E_LEN_MISMATCH`).
The result is BIN of that length. These operate on byte strings, not integers.

### 5.7 Assignment

The target must be an identifier, optionally followed by index operations
(`A`, `A[1]`, `A["x"][2]`). Anything else is `E_BAD_ASSIGN` at parse time.

`=` copies **by value**: the assigned value, including all children, is deep
copied. Two variables never share structure.

The target is resolved **before** the right-hand side is evaluated: the base
variable and every intermediate level are created first, and each index
expression is evaluated once, left to right. So `A[COUNT(A)] = 1` sees the `A`
that resolving the target just created.

The value is then stored **at that path, in the tree as it exists once the
right-hand side has been evaluated**. This matters only when the right-hand side
or a later index expression replaces a level the walk already passed through:

```
A[1] = (A = 2); A       ==>   t"2"{"1"=t"2"}
```

The alternative — keeping hold of the container object found during the walk and
writing into it — discards the assignment silently whenever that object has since
been detached from the tree, and a write that nothing can ever read is a worse
answer than a visible one.

The compound forms `+= -= *= /= %= &=` read the target, apply the matching binary
operator, and store back. The target must already exist.

An assignment evaluates to the value assigned.

### 5.8 Sequence — `;`

Evaluates left then right, yields the right. A trailing `;` is permitted and
yields the value before it.

### 5.9 List building — `,`

Produces a NONE value with children keyed `"1"`, `"2"`, … Each operand
contributes: a value **with children and no scalar of its own** contributes each
of its children's values in order; anything else contributes itself. Keys of
contributed children are **not** preserved — the result is always renumbered
from 1.

This is what makes the append idiom work:

```
A = ("a", "b");
A = (A, "c");      # A is now three elements, keys 1..3
```

---

## 6. Evaluation

### 6.1 Context

One `Value` is the root context. Variables are its direct children. Host code
builds and reads it with the same API the interpreter uses — there is no second
representation of state.

There is no lexical scoping and no call stack of frames. The single exception is
aggregate binders (§7.3), which push one name for the duration of one element.

### 6.2 Order

Evaluation is strictly left to right wherever both operands are evaluated. The
only operators that skip evaluation are `AND` and `OR`. The only functions that
skip or repeat evaluation are `IF` and the aggregates.

### 6.3 Errors

Evaluation stops at the first failure. An error carries a stable code, a human
message, and the **position of the node that actually failed** — no caller wraps
it, re-messages it, or adds a stack. The innermost failure is what the host sees.

Codes are listed in `errors.md`. **Conformance tests assert on code and position
only**, never on message text, so messages remain free to change and to be
translated.

### 6.4 Limits

Parser nesting depth and evaluation depth are capped (implementation-defined,
at least 200) and exceeding either is `E_DEPTH`. This is a denial-of-service
guard, not a language feature.

Three arguments name a size rather than a value, and a large one asks for more
work or more memory than any host has. Each is capped, and exceeding the cap is
an ordinary SEL error rather than a host failure:

| Argument | Cap | Beyond it |
|---|---|---|
| `ROUND(x, n)` scale | 1 000 000 | `E_RANGE` |
| `POWER(x, n)` exponent | 100 000 | `E_RANGE` |
| a regex quantifier bound, as in `a{n}` or `a{n,m}` | 65 535 | `E_REGEX_SYNTAX` |

The quantifier cap is PCRE2's own hard limit rather than a number of SEL's
choosing: above 65 535 PCRE refuses to compile the pattern at all, so no cap
above it could be honoured on a PHP host.

These caps exist because without them the four hosts fail in four different
ways, and one of them fails *quietly*: `ROUND(1.5, 4294967296)` exhausted memory
on two hosts and raised a host-level `RangeError` on a third, while
`POWER(10, 4294967299)` returned `1000` in JS — a confident wrong answer, caused
by a shift that silently truncates the exponent to 32 bits. A rule that asks for
a million-digit scale is a mistake in the rule; the language should say so in the
same vocabulary as every other mistake.

---

## 7. Functions

### 7.1 Calling

`NAME(a, b, c)` — the parenthesised expression is a `,` tree, flattened into an
argument vector. `NAME()` has zero arguments. Names are case-insensitive.
An unknown name is `E_UNKNOWN_FUNC` at **parse time**, not run time.

Each function declares a minimum and maximum arity, checked by the framework
before the body runs, so no function body counts its own arguments (`E_ARITY`).

A function is declared **strict** or **lazy**. A strict function's arguments are
all evaluated, left to right, before the body runs. A lazy function receives the
argument nodes and evaluates what it chooses. Only `IF` and the aggregates are
lazy.

### 7.2 Control

| Signature | Meaning |
|---|---|
| `IF(cond, then)` | `cond` must be BOOL. Evaluates and yields `then` if `TRUE`; yields TEXT `""` if `FALSE`. |
| `IF(cond, then, else)` | Evaluates and yields exactly one branch. |
| `COND(c1, r1, …, default)` | Flat multi-branch selection. See below. |
| `ABORT(message)` | Always fails with `E_ABORT` and the given message. |

`COND` takes condition/result pairs followed by a mandatory default. It evaluates
conditions in order, stops at the first `TRUE`, and evaluates only that result —
identical in every respect to the nested `IF` ladder it replaces. Each condition
must be BOOL.

```
COND(SCORE >= 90, "A",
     SCORE >= 80, "B",
     SCORE >= 70, "C",
                  "F")
```

**The argument count must be odd** (`E_ARITY` otherwise, at compile time).
`IF` can safely let its two-argument form default to `""` because there is one
branch and nothing to mis-pair. `COND` cannot: with an even count, a single
miscounted comma shifts every condition/result pair by one and the rule still
compiles. Requiring the default makes that a compile-time error rather than a
wrong answer at run time. Write `""` explicitly when you mean nothing.

`COND` adds no grammar and no new node type — `,` is already an ordinary operator
and the parser already hands functions a flattened argument vector.

### 7.3 Aggregates

The reason no loop is needed. Each evaluates its body argument once per child of
its first argument, in insertion order.

| Signature | Yields |
|---|---|
| `ALL(list, body)` | BOOL — `TRUE` if `body` is `TRUE` for every element. Short-circuits on the first `FALSE`. Empty list yields `TRUE`. |
| `ANY(list, body)` | BOOL — `TRUE` if `body` is `TRUE` for any element. Short-circuits. Empty list yields `FALSE`. |
| `MAP(list, body)` | list of each `body` result, renumbered from `"1"`. |
| `FILTER(list, body)` | the elements for which `body` is `TRUE`, **keys preserved**. |
| `SUM(list, body)` | the exact sum of each `body` result. Empty list yields `0`. |
| `JOIN(list, sep)` | TEXT — **strict**, not an aggregate body; concatenates each element's scalar with `sep` between. |

Within a body, `_` is bound to the element and `_K` to its key.

A three-argument form replaces `_` with a name of your choosing:

```
ALL(items, ITEM, ITEM["qty"] > 0)
```

The second argument must be a **bare identifier node**, which the function checks
by inspecting the AST it was handed — `E_EXPECT_SYMBOL` otherwise. `_K` is still
available. Binders shadow any variable of the same name for the duration of the
body and are removed afterwards; nested aggregates shadow independently.

If the first argument has no children, it is treated as a **one-element list
containing itself** when it has a scalar (consistent with §3.2), and as an
**empty list** when it is NONE. The second case is what `FILTER` returns when
nothing matched, so `ALL(FILTER(…), …)` is `TRUE` rather than a scalar-context
failure.

### 7.4 Structure

| Signature | Yields |
|---|---|
| `COUNT(x)` | number of children |
| `INDEXES(x)` | list of the keys, in order |
| `HAS(x, key)` | BOOL |

### 7.5 Text

Positions are **1-based**, and `0` means "not found". Lengths and positions count
**code points**, never bytes or UTF-16 units. This differs from Aster, which is
0-based; one base for everything is worth the divergence.

| Signature | Yields |
|---|---|
| `LEN(x)` | code point count |
| `LEFT(x, n)` / `RIGHT(x, n)` | leading / trailing `n` code points; fewer if shorter |
| `SUBSTR(x, start [, len])` | from `start` (1-based) for `len` code points, or to the end |
| `FIND(needle, hay [, from])` | 1-based position, or `0` |
| `REPLACE(needle, repl, hay)` | all occurrences, left to right, non-overlapping |
| `SPLIT(x, sep)` | list; empty `sep` is `E_BAD_ARG` |
| `TRIM(x)` / `LTRIM(x)` / `RTRIM(x)` | strips space, tab, CR, LF |
| `UPPER(x)` / `LOWER(x)` | **ASCII only** — see below |
| `BACKWARDS(x)` | code points reversed |
| `REPEAT(x, n)` | `n` copies, `n >= 0` |
| `PADL(x, n, fill)` / `PADR(x, n, fill)` | pad to `n` code points; no truncation if longer |
| `CHAR(n)` | the code point `n` as TEXT |
| `CODE(x)` | code point of the first character |

`UPPER` and `LOWER` map **only** `A`–`Z` ↔ `a`–`z` and leave every other code
point untouched. PHP's `strtoupper` is byte- and locale-based while JS's
`toUpperCase` applies full Unicode case mapping; there is no way to make those
agree without shipping a case table, and guessing would break the invariant
silently rather than loudly.

### 7.6 Numbers

| Signature | Yields |
|---|---|
| `ABS(x)` `SIGN(x)` | `SIGN` is `-1`, `0` or `1` |
| `CEIL(x)` `FLOOR(x)` `TRUNC(x)` | scale 0 |
| `ROUND(x, n)` | scale exactly `n`, `n >= 0`, half away from zero |
| `MIN(a, b, …)` `MAX(a, b, …)` | at least one argument |
| `POWER(x, n)` | `n` a non-negative integer; result scale `scale(x) * n` |
| `ISNUM(x)` | BOOL — whether `x` parses as a number |

`SQRT`, `LOG` and `RANDOM` do not exist: the first two have no exact decimal
result, and the third would make the conformance suite meaningless.

### 7.7 Binary

| Signature | Yields |
|---|---|
| `BLEN(x)` | byte length |
| `TO_UTF8(x)` | BIN — the UTF-8 bytes of TEXT `x` |
| `FROM_UTF8(x)` | TEXT — decodes BIN `x`, `E_UTF8` if invalid |
| `TO_HEX(x)` | TEXT — lower-case hex of BIN `x` |
| `FROM_HEX(x)` | BIN — even-length hex, either case; `E_BAD_ARG` otherwise |
| `ENCODE_BASE64(x)` | TEXT — standard alphabet, always padded |
| `DECODE_BASE64(x)` | BIN — padding required, `E_BAD_ARG` on any invalid character |
| `CRC32(x)` | TEXT — CRC-32/ISO-HDLC as 8 lower-case hex digits |
| `BTL(x)` | list of byte values 0–255 |
| `LTB(list)` | BIN from a list of byte values; `E_RANGE` outside 0–255 |

Functions taking BIN accept TEXT and encode it as UTF-8 first.

### 7.8 Regular expressions

SEL accepts a **subset of syntax that PCRE and ECMAScript agree on**, checked at
compile time. Anything outside the subset is `E_REGEX_SYNTAX` with the offset of
the offending character — a clear failure instead of a silent divergence between
backend and frontend.

| Signature | Yields |
|---|---|
| `RMATCH(pat, subj [, flags])` | BOOL |
| `RFIND(pat, subj [, flags])` | 1-based code point position of the first match, or `0` |
| `RREPLACE(pat, repl, subj [, flags])` | TEXT, all matches replaced |
| `RGROUPS(pat, subj [, flags])` | list: whole match at `"1"`, capture *n* at `"n+1"`; empty list if no match |

**Allowed:** literal characters, `.` `^` `$`, character classes `[…]` with ranges
and negation, the escapes `\d \D \w \W \s \S`, the control escapes `\n \r \t \f`,
escaped metacharacters, quantifiers `* + ? {n} {n,} {n,m}` and their lazy `?`
forms, groups `( )`, non-capturing groups `(?: )`, and alternation `|`.

**Rejected:** POSIX classes `[[:alpha:]]`, `\p{…}`, backreferences, lookahead and
lookbehind, atomic groups, possessive quantifiers, inline modifiers `(?i)`,
`\A \z \Z \G \K`, conditionals, recursion, and the three below.

- **`\b` and `\B`.** A word boundary is defined in terms of the engine's notion of
  a word character, and the two engines disagree — PHP's `u` modifier enables
  PCRE2's UCP, so `é` is a word character there and is not in ECMAScript. There
  is no rewrite that fixes this. Write the boundary explicitly, e.g.
  `(^|[^0-9A-Za-z_])`.
- **`\v`.** In PCRE it means "any vertical whitespace"; in ECMAScript it means
  U+000B. Same spelling, different language.
- **`\D`, `\W`, `\S` inside a character class**, which cannot be expanded (below).
  Negate the whole class instead. A leading `]` is likewise rejected — PCRE reads
  `[]` as a literal bracket and ECMAScript as an empty class — so write `\]`.

**`\d`, `\w` and `\s` are rewritten, not passed through.** Both hosts expand them
into explicit ASCII classes before compiling:

| Escape | Becomes |
|---|---|
| `\d` / `\D` | `[0-9]` / `[^0-9]` |
| `\w` / `\W` | `[0-9A-Za-z_]` / `[^0-9A-Za-z_]` |
| `\s` / `\S` | `[ \t\n\r\f\x0b]` / `[^ \t\n\r\f\x0b]` |

Inside a character class the bracketed form is dropped, so `[\d.]` becomes
`[0-9.]`. This makes the definitions structural rather than dependent on a
library flag: without it, `\d` matches Arabic-Indic digits under PHP and not
under JS.

**Flags: `i` and nothing else.** `i` is rejected with `E_BAD_ARG` on a pattern
containing non-ASCII literals, because case folding is the one area the two
engines cannot be made to agree.

`m` and `s` are deliberately not offered. JS treats `\r`, U+2028 and U+2029 as
line terminators and PCRE treats only `\n` as one, so every construct whose
meaning depends on where a line ends — `.`, `^`, `$` under `m` — would differ
between backend and frontend. Instead:

- **Dotall is permanently on.** `.` means "any code point", in both hosts. Write
  `[^\n]` when you mean "not a newline"; that is portable and says what it means.
- **`^` and `$` anchor only to the ends of the subject.** PCRE's `$` otherwise
  also matches before a trailing newline, so PHP must additionally compile with
  the `D` modifier.

Four requirements on implementations, without which the two hosts diverge:

1. **Compile with `u` and dotall in both hosts** (`us` in JS, `usD` in PHP), and
   expand the class escapes as above. `u` gives code point matching in both; the
   expansion is what makes `\d`, `\w` and `\s` mean the same thing, since PHP's
   `u` also enables UCP and JS's does not.
2. **Report code point offsets.** `preg_*` returns byte offsets and JS returns
   UTF-16 unit offsets; neither is what SEL reports.
3. **Splice replacements manually** from match offsets. Do not hand the
   replacement string to `preg_replace` or `String.replace`. SEL replacement
   syntax is `$0`–`$9` for the whole match and captures, and `$$` for a literal
   `$`; every other character is literal. This avoids PCRE's `\1` and JS's
   `` $& ``, `` $' `` and `` $` ``.

A capture that did not participate in the match yields TEXT `""`.

---

## 8. Host interface

Both implementations expose the same shape:

```
Sel.compile(source)          -> Program        # throws on syntax error
Program.run(context)         -> Value
Program.dependencies()       -> array of variable names, upper case
Value.text/bin/num/bool/list/fromNative/toNative
```

`dependencies()` returns every variable the program reads, determined statically
without evaluating it. This is possible only because SEL has no dynamic symbol
operator, and it is how a frontend knows which inputs should re-trigger which
rule.

---

## 9. Relationship to Aster

SEL takes Aster's calling convention — arguments passed as AST, evaluated at the
callee's discretion — its `;`/`,`-as-operators grammar, its string interpolation
pass, its key-value variables with scalar-context-takes-first, and its split
between numeric and text operator families.

It does not take Aster's loops, `DEFUN`/`LAMBDA`, `LOCALS`/`WITH`, dynamic
symbols, XML/XPath/XSLT, JSON, GZIP/ZIP, `QSORT`, `RANDOM`, `\` indexing, `~=`,
`NAND`/`NOR`, `|AND`/`|OR`, `'X'`/`' '` booleans, or its floating-point numbers.

It is not a port and is not compatible. Known divergences that will bite someone
porting a rule by eye: 1-based string positions, strict BOOL instead of `'X'`,
short-circuiting `AND`/`OR` by default, and exact decimal instead of float.
