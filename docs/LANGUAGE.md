# SEL language reference

For people writing rules. If you are implementing SEL, or arguing about what it
should do, `spec/` is the normative text and this is not.

Every `=>` example below is executed against both hosts by `tools/check-docs.sh`,
so nothing here can quietly stop being true.

- [Mental model](#mental-model)
- [Values](#values)
- [Numbers](#numbers)
- [Text](#text)
- [Lists](#lists)
- [Booleans](#booleans)
- [Operators](#operators)
- [Control flow](#control-flow)
- [Iteration](#iteration)
- [Function reference](#function-reference)
- [Errors](#errors)
- [Gotchas](#gotchas)
- [Coming from Aster](#coming-from-aster)

---

## Mental model

**A SEL program is one expression.** There are no statements. What looks like
control flow is a function call:

```sel
IF(2 > 1, "yes", "no")  => yes
```

`IF` is not a keyword. It is an entry in the function table, like `LEN`. What
makes it work is that **functions receive the caller's syntax tree, not values**,
and decide for themselves what to evaluate:

```sel
IF(1 > 0, "safe", 1 / 0)  => safe
```

The division never happens. Nothing was "optimised away" — the argument was
simply never evaluated, because `IF` chose not to.

`;` and `,` are ordinary operators, not punctuation. `;` runs both sides and
yields the right one; `,` builds a list. So a block and an argument list are the
same kind of thing:

```sel
A = 1; A + 1  => 2
```

The last expression is the program's result.

---

## Values

A value has a **kind** and, independently, may have **children** (an ordered
key→value map). The four kinds:

| Kind | What it is |
|---|---|
| `TEXT` | a sequence of Unicode code points. Numbers are TEXT. |
| `BIN` | a sequence of bytes. Not text; not assumed printable. |
| `BOOL` | `TRUE` or `FALSE`. A real kind, not a string. |
| `NONE` | no scalar of its own — a plain list is NONE with children. |

TEXT and BIN convert **only through UTF-8**, and only when you ask:

```sel
TO_UTF8("ż")               => bin:c5bc
FROM_UTF8(FROM_HEX("c5bc"))  => ż
LEN(FROM_HEX("41"))        => !E_NOT_TEXT
```

That last one is deliberate: text functions do not silently decode bytes.

---

## Numbers

**There is no floating point.** A number is TEXT that parses as
`-?digits[.digits]`, and arithmetic is exact decimal.

```sel
0.10 + 0.20 == 0.30  => TRUE
19.99 * 3            => 59.97
```

**Scale is part of the value** and is preserved by `+`, `-` and `*`. Money keeps
its cents:

```sel
2.50 + 2.50  => 5.00
1.5 * 1.5    => 2.25
```

Division reports its minimal scale when it is exact, and ten digits when it is
not, rounded half away from zero:

```sel
4 / 2   => 2
10 / 4  => 2.5
1 / 8   => 0.125
1 / 3   => 0.3333333333
2 / 3   => 0.6666666667
```

Leading zeros go, trailing zeros in the fraction stay, and zero is never
negative:

```sel
007     => 7
-0.00   => 0.00
```

There is **no implicit trimming**. A value out of a text field is not a number
until you make it one:

```sel
" 2" + 1        => !E_NOT_NUM
TRIM(" 2") + 1  => 3
```

`%` is the remainder of truncated division and takes the sign of the dividend:

```sel
5 % 3    => 2
-5 % 3   => -2
5.5 % 2  => 1.5
```

`SQRT`, `LOG` and `RANDOM` do not exist — the first two have no exact decimal
result, and the third would make a rule untestable.

---

## Text

Two literal forms. **Quoted** takes escapes and interpolation:

```sel
"a\tb"          => a	b
"\u{17C}"       => ż
"a\{not interpolated\}"  => a{not interpolated}
```

Escapes are `\\ \" \n \t \r \{ \}` and `\u{HEX}`. Anything else is `!E_ESCAPE`.

**Raw** `'…'` takes none — no escapes, no interpolation, `''` for a literal
quote. This is the form for regex patterns:

```sel
'^\d{3}$'  => ^\d{3}$
'it''s'    => it's
```

### Interpolation

`{…}` inside a quoted literal holds a full expression:

```sel
A = 3; "value {A} here"           => value 3 here
A = 3; "{A + 1}"                  => 4
P = 19.99; Q = 3; "{ROUND(P * Q, 2)} PLN"  => 59.97 PLN
```

It is resolved by the tokeniser, which rewrites the literal into a `&` chain
before parsing — so it costs nothing at run time and obeys exactly the rules
`&` obeys. Which means a boolean is refused:

```sel
"{1 == 1}"  => !E_NOT_TEXT
```

### Positions are 1-based, and count code points

`0` means "not found". Nothing counts bytes or UTF-16 units:

```sel
LEN("Zażółć")     => 6
BLEN("Zażółć")    => 10
LEN("👍a")        => 2
LEFT("👍ab", 1)   => 👍
FIND("ż", "Zażółć")  => 3
FIND("q", "abc")     => 0
```

`UPPER` and `LOWER` are **ASCII-only**, on purpose — see [Gotchas](#gotchas):

```sel
UPPER("aÄz")  => AÄZ
```

---

## Lists

A variable is a key→value map, kept in insertion order. The `,` operator builds
one, keyed from `"1"`:

```sel
(3, 2, 1)                  => -{"1"=t"3", "2"=t"2", "3"=t"1"}
JOIN(("a", "b", "c"), "-")  => a-b-c
```

Indexing uses the index **verbatim as a key**:

```sel
A["x"] = 1; A["x"]     => 1
A[1] = "a"; HAS(A, "1.0")  => FALSE
A = (1, 2); A[3]       => !E_NO_KEY
```

An operand that is a list contributes its *values*, which is what makes the
append idiom work:

```sel
A = ("a", "b"); A = (A, "c"); JOIN(A, "-")  => a-b-c
```

### Scalar context

Using a list where a scalar is wanted takes its **first** value, recursively.
This is why `A[1] = 3` makes `A == 3` true, and why returning several values
costs nothing:

```sel
A[1] = 3; A == 3               => TRUE
A = (7, 8); A + 0              => 7
R[1] = "ok"; R[2] = "why"; R & "/" & R[2]  => ok/why
```

Nothing in the language asks "is this a list". A scalar is just a value whose
first value is itself.

### Missing things are errors

Reading an undefined variable or a missing key fails loudly rather than yielding
an empty string:

```sel
FOO + 1  => !E_UNDEF_VAR
```

Use `HAS(list, key)` to test, and `COUNT` to size:

```sel
A["x"] = 1; HAS(A, "x")  => TRUE
COUNT(("a", "b"))        => 2
COUNT("abc")             => 0
INDEXES(("a", "b"))      => -{"1"=t"1", "2"=t"2"}
```

`COUNT("abc")` is `0` because a scalar has no children — not because the text is
empty.

---

## Booleans

`TRUE` and `FALSE` are their own kind. **There is no truthiness.** Comparisons
produce booleans, and `IF`, `AND`, `OR`, `NOT`, `XOR` accept nothing else:

```sel
IF(1, "a", "b")   => !E_NOT_BOOL
"x" AND TRUE      => !E_NOT_BOOL
```

Say what you mean:

```sel
NAME = ""; IF(NAME $== "", "missing", "ok")  => missing
```

This is the single biggest source of "works on the backend, not on the frontend"
in most validation code, and SEL removes it by not having the feature.

`AND` and `OR` short-circuit:

```sel
FALSE AND (1 / 0 == 0)  => FALSE
TRUE OR (1 / 0 == 0)    => TRUE
```

---

## Operators

Tightest binding first.

| # | Operators | Notes |
|---|---|---|
| 1 | `x[k]` `f(…)` `( )` | indexing, call, grouping |
| 2 | `-x` | negation |
| 3 | `*` `/` `%` | |
| 4 | `+` `-` | |
| 5 | `&` | concatenation |
| 6 | `BAND` → `BXOR` → `BOR` | on BIN of equal length |
| 9 | `==` `!=` `<` `<=` `>` `>=` `$==` `$!=` `$<` `$<=` `$>` `$>=` `EQL` `IN` | **cannot chain** |
| 10 | `NOT x` | looser than comparison |
| 11 | `AND` → `XOR` → `OR` | `AND`/`OR` short-circuit |
| 14 | `=` `+=` `-=` `*=` `/=` `%=` `&=` | right-associative |
| 15 | `,` | list build / argument separator |
| 16 | `;` | sequence |

Two choices that will catch you out if you assume C:

```sel
NOT 1 == 2   => TRUE
1 < 2 < 3    => !E_SYNTAX
```

`NOT` binds *looser* than comparison, so that reads as `NOT (1 == 2)`.
Comparisons do not chain — the error arrives at parse time rather than as a
confusing type error later.

### Three families of comparison

The same pair of values can be equal as numbers and different as text. That is
the point, not a bug:

```sel
"5.00" == "5"    => TRUE
"5.00" $== "5"   => FALSE
"5.00" EQL "5"   => FALSE
```

- `==` and friends compare **numerically**.
- `$==` and friends compare **text**, bytewise in UTF-8 order.
- `EQL` compares **structurally** — kind, scalar and children, in order.
- `IN` tests membership using `EQL`.

```sel
"PL" IN ("DE", "PL")   => TRUE
(1, 2) EQL (1, 2)      => TRUE
```

### Concatenation

`&` joins text, and numbers are text:

```sel
"A" & "B"   => AB
2 & "A"     => 2A
"a" & TRUE  => !E_NOT_TEXT
```

If either side is BIN the result is BIN, with text encoded as UTF-8.

### Assignment

```sel
A = 2                      => 2
A = 1; A += 2; A *= 3; A   => 9
A = "x"; A &= "y"; A       => xy
```

Assignment yields the value assigned, and copies **by value** all the way down —
two variables never share structure:

```sel
A = (1, 2); B = A; B[1] = 9; A[1]  => 1
```

Compound forms need an existing target, and the target must be a name with
optional indexing:

```sel
A += 1  => !E_UNDEF_VAR
3 = 4   => !E_BAD_ASSIGN
```

---

## Control flow

There is `IF`, `COND` and `ABORT`. That is all of it.

```sel
IF(1 > 2, "yes", "no")             => no
"[" & IF(1 > 2, "yes") & "]"       => []
```

The two-argument form yields empty text when the condition is false.

For more than two branches, `COND` takes condition/result pairs and a final
default:

```sel
S = 85; COND(S >= 90, "A", S >= 80, "B", S >= 70, "C", "F")  => B
```

Conditions run in order and stop at the first `TRUE`; only the matching result is
evaluated:

```sel
COND(TRUE, "safe", TRUE, 1 / 0, 1 / 0)  => safe
```

The default is **mandatory** — the argument count must be odd. With an even
count, one miscounted comma would shift every pair by one and still compile:

```sel
COND(TRUE, "a", FALSE, "b")  => !E_ARITY
```

`ABORT` fails on purpose, with your message, as `E_ABORT`:

```sel
ABORT("out of stock")  => !E_ABORT
IF(FALSE, ABORT("never"), "fine")  => fine
```

---

## Iteration

There are no loops. Aggregates evaluate a body expression once per element, with
`_` bound to the element and `_K` to its key:

```sel
ALL((1, 2, 3), _ > 0)                  => TRUE
ANY((1, -2), _ < 0)                    => TRUE
JOIN(MAP((1, 2), _ * 2), ",")          => 2,4
JOIN(FILTER((1, 2, 3, 4), _ % 2 == 0), ",")  => 2,4
SUM((1.5, 2.5), _)                     => 4.0
M["a"] = 1; M["b"] = 2; JOIN(MAP(M, _K & "=" & _), " ")  => a=1 b=2
```

`ALL` and `ANY` short-circuit, so this never divides by zero:

```sel
ALL((0, 1), _ > 0 AND 1 / _ > 0)  => FALSE
```

`MAP` renumbers from 1; `FILTER` **keeps the original keys**, so a filtered list
is still addressable the way the source was:

```sel
FILTER((1, 2, 3, 4), _ % 2 == 0)  => -{"2"=t"2", "4"=t"4"}
```

For nesting, the three-argument form names the binder instead of using `_`:

```sel
R[1] = (1, 2); R[2] = (3, 4); ALL(R, ROW, ALL(ROW, _ > 0))  => TRUE
```

The name must be a bare identifier — the function inspects the syntax tree it
was handed to check:

```sel
ALL((1, 2), 1, _ > 0)  => !E_EXPECT_SYMBOL
```

Empty and scalar edge cases behave the way you would want:

```sel
ALL(FILTER((1, 2), _ > 5), _ > 0)  => TRUE
ALL(5, _ > 0)                      => TRUE
```

An empty list satisfies `ALL` vacuously; a scalar behaves as a one-element list.

### Folding a list down to one value

There is no general `REDUCE`. The two folds people actually reach for are
built in, and between them they cover most of it:

```sel
JOIN(("a", "b", "c"), "-")             => a-b-c
SUM((1.50, 2.25, 3.00), _)             => 6.75
```

`JOIN` **is** the concatenate-with-separator fold, and it is strict — its second
argument is a separator, not a body. To fold something other than the elements
themselves, map first and join after:

```sel
JOIN(MAP(("a", "b"), UPPER(_)), ", ")                  => A, B
JOIN(FILTER((1, 2, 3, 4), _ % 2 == 0), "+")            => 2+4
JOIN(MAP(("x", "y"), _K & ":" & _), " ")               => 1:x 2:y
```

`JOIN` follows the same edge cases as the aggregates — a scalar is a one-element
list, and a list that filtered down to nothing yields empty text:

```sel
JOIN("solo", "-")                                  => solo
LEN(JOIN(FILTER((1, 3), _ % 2 == 0), "-"))         => 0
```

For any *other* fold, accumulate into a variable. Assignment is an ordinary
operator and an aggregate body is an ordinary expression, so a body of
`(ACC = …; TRUE)` folds while `ALL` walks:

```sel
ACC = "1"; ALL((2, 3, 4), (ACC = ACC * _; TRUE)); ACC   => 24
ACC = "0"; ALL((3, 9, 2), (ACC = MAX(ACC, _); TRUE)); ACC  => 9
```

Two things to keep in mind with that idiom. The body must yield a BOOL, hence
the `; TRUE)` — and it must yield **`TRUE`**, because `ALL` short-circuits on
`FALSE` and would stop the walk early. And the accumulator is an ordinary
variable in the ordinary context, so it survives after the aggregate and is
visible to whatever runs next; give it a name you mean.

You cannot assign to the binder itself — it is scoped to one element and vanishes
with it:

```sel
ALL((1, 2), (_ = 5; TRUE))  => !E_BAD_ASSIGN
```

If you find yourself writing that idiom often, a real `FIRST`/`REDUCE` is a
dozen lines per host and needs no grammar change — `docs/EXTENDING.md` works one
through end to end.

---

## Function reference

54 functions. Names are case-insensitive.

### Control

| Signature | Yields |
|---|---|
| `IF(cond, then [, else])` | one branch, evaluated lazily |
| `COND(c1, r1, …, default)` | first matching result; odd argument count required |
| `ABORT(message)` | always fails with `E_ABORT` |

### Structure

| Signature | Yields |
|---|---|
| `COUNT(x)` | number of children |
| `INDEXES(x)` | list of keys, in order |
| `HAS(x, key)` | BOOL |

### Aggregates

| Signature | Yields |
|---|---|
| `ALL(list [, name], body)` | BOOL, short-circuits, `TRUE` when empty |
| `ANY(list [, name], body)` | BOOL, short-circuits, `FALSE` when empty |
| `MAP(list [, name], body)` | list of results, renumbered from 1 |
| `FILTER(list [, name], body)` | matching elements, **keys preserved** |
| `SUM(list [, name], body)` | exact sum, `0` when empty |
| `JOIN(list, separator)` | TEXT — strict, the second argument is not a body |

### Text

| Signature | Yields | |
|---|---|---|
| `LEN(x)` | code point count | `LEN("👍a")  => 2` |
| `LEFT(x, n)` / `RIGHT(x, n)` | leading / trailing n | `RIGHT("abc", 2)  => bc` |
| `SUBSTR(x, start [, len])` | from 1-based `start` | `SUBSTR("abcdef", 3, 2)  => cd` |
| `FIND(needle, hay [, from])` | 1-based position, 0 if absent | `FIND("c", "abc")  => 3` |
| `REPLACE(needle, repl, hay)` | all occurrences | `REPLACE("a", "X", "banana")  => bXnXnX` |
| `SPLIT(x, sep)` | list | `JOIN(SPLIT("a,b", ","), "-")  => a-b` |
| `TRIM` / `LTRIM` / `RTRIM` | strips space, tab, CR, LF | `TRIM("  x  ")  => x` |
| `UPPER(x)` / `LOWER(x)` | **ASCII only** | `UPPER("aÄz")  => AÄZ` |
| `BACKWARDS(x)` | code points reversed | `BACKWARDS("ab👍")  => 👍ba` |
| `REPEAT(x, n)` | n copies | `REPEAT("ab", 3)  => ababab` |
| `PADL` / `PADR(x, n, fill)` | pad to n, never truncates | `PADL("7", 3, "0")  => 007` |
| `CHAR(n)` | code point n | `CHAR(128077)  => 👍` |
| `CODE(x)` | code point of first char | `CODE("👍")  => 128077` |

### Numbers

| Signature | Yields | |
|---|---|---|
| `ABS(x)` `SIGN(x)` | | `SIGN(-0.1)  => -1` |
| `CEIL` `FLOOR` `TRUNC(x)` | scale 0 | `FLOOR(-2.5)  => -3` |
| `ROUND(x, n)` | scale exactly n, half away from zero | `ROUND(2.5, 0)  => 3` |
| `MIN(…)` `MAX(…)` | variadic | `MIN(3, 1, 2)  => 1` |
| `POWER(x, n)` | n a non-negative integer | `POWER(2.5, 2)  => 6.25` |
| `ISNUM(x)` | BOOL, never throws | `ISNUM(" 2")  => FALSE` |

### Binary

| Signature | Yields | |
|---|---|---|
| `BLEN(x)` | byte length | `BLEN("ż")  => 2` |
| `TO_UTF8(x)` / `FROM_UTF8(x)` | TEXT ↔ BIN | `TO_UTF8("ż")  => bin:c5bc` |
| `TO_HEX(x)` / `FROM_HEX(x)` | lower-case hex | `TO_HEX("AB")  => 4142` |
| `ENCODE_BASE64` / `DECODE_BASE64` | standard alphabet, padded, strict | `ENCODE_BASE64("hello")  => aGVsbG8=` |
| `CRC32(x)` | CRC-32/ISO-HDLC, 8 hex digits | `CRC32("123456789")  => cbf43926` |
| `BTL(x)` / `LTB(list)` | bytes ↔ list of 0–255 | `LTB((65, 66))  => bin:4142` |

### Regular expressions

Write patterns as raw `'…'` literals so backslashes need no doubling.

| Signature | Yields | |
|---|---|---|
| `RMATCH(pat, subj [, flags])` | BOOL | `RMATCH('^\d{2}-\d{3}$', "31-874")  => TRUE` |
| `RFIND(pat, subj [, flags])` | 1-based position, 0 if absent | `RFIND('b', "abc")  => 2` |
| `RGROUPS(pat, subj [, flags])` | whole match at `"1"`, captures after | `RGROUPS('(a)(b)', "ab")  => -{"1"=t"ab", "2"=t"a", "3"=t"b"}` |
| `RREPLACE(pat, repl, subj [, flags])` | TEXT, all matches | `RREPLACE('\s+', " ", "a   b")  => a b` |

Replacements understand `$0`–`$9` and `$$`. Everything else is literal — JS's
`$&` and PCRE's `\1` are **not** special:

```sel
RREPLACE('(a)(b)', "$2$1", "abab")  => baba
RREPLACE('a', "x$&y", "a")          => x$&y
```

The only flag is `i`, and it needs an ASCII-only pattern.

**Supported:** literals, `.` `^` `$`, `[…]` with ranges and negation,
`\d \D \w \W \s \S`, `\n \r \t \f`, escaped metacharacters, `* + ? {n} {n,}
{n,m}` and lazy `?` forms, `( )`, `(?: )`, `|`.

**Refused, at compile time**, because the two engines would disagree:
`\b` `\B` `\v`, backreferences, lookahead, lookbehind, atomic groups, possessive
quantifiers, `(?i)`, `\A \z \Z \G \K`, POSIX classes, `\p{…}`, and `\D \W \S`
inside a character class.

```sel
RMATCH('(?=a)', "a")     => !E_REGEX_SYNTAX
RMATCH('[[:alpha:]]', "a")  => !E_REGEX_SYNTAX
RMATCH('\bx', "x")       => !E_REGEX_SYNTAX
```

Two behaviours worth knowing:

```sel
RMATCH('^a.b$', "a\nb")  => TRUE
RMATCH('^abc$', "abc\n")  => FALSE
```

`.` always matches any character including newlines, and `^`/`$` anchor to the
very ends of the subject. Both are fixed, not switchable.

---

## Errors

Evaluation stops at the first failure. The error carries a **stable code**, a
message, and the position of the node that actually failed — never the caller's.
Match on the code; the message is free to change.

Caught before the rule ever runs:

| Code | When |
|---|---|
| `E_SYNTAX` | it does not parse, including chained comparisons |
| `E_UNTERMINATED` `E_ESCAPE` | bad text literal |
| `E_RESERVED` | a reserved word used as a variable |
| `E_BAD_ASSIGN` | assignment to something that is not a name |
| `E_UNKNOWN_FUNC` | no such function |
| `E_ARITY` | wrong argument count |
| `E_REGEX_SYNTAX` | pattern outside the portable subset |
| `E_DEPTH` | nested too deeply |

At run time:

| Code | When |
|---|---|
| `E_UNDEF_VAR` `E_NO_KEY` `E_NO_SCALAR` | something is not there |
| `E_NOT_NUM` `E_NOT_TEXT` `E_NOT_BIN` `E_NOT_BOOL` `E_NOT_INT` | wrong kind |
| `E_EXPECT_SYMBOL` | an aggregate binder that is not a bare name |
| `E_DIV_ZERO` `E_UTF8` `E_RANGE` `E_BAD_ARG` `E_LEN_MISMATCH` | bad value |
| `E_ABORT` | you called `ABORT` |

`E_ABORT` is the one you raise on purpose, and the one worth showing a user.

---

## Gotchas

**`UPPER` and `LOWER` only touch A–Z.** PHP's `strtoupper` is byte- and
locale-based; JS's `toUpperCase` applies full Unicode mapping. They cannot be
reconciled without shipping a case table, and a language whose whole job is
agreeing across hosts would rather be visibly limited than quietly wrong.

**Text out of a form is not a number.** No implicit trimming, ever. `TRIM` first.

**`COUNT` of a scalar is 0.** It counts children, not characters. `LEN` counts
characters.

**Index keys are literal.** `A[1]` and `A[1.0]` are different keys, because the
index's text is the key.

**`\d` is ASCII.** Both hosts rewrite `\d`, `\w` and `\s` into explicit ASCII
classes before compiling, so an Arabic-Indic digit does not match:

```sel
RMATCH('^\d$', "\u{0661}")  => FALSE
```

**Sequencing inside an argument needs parentheses,** because `;` binds looser
than `,`:

```sel
IF(TRUE, (A = 1; A + 1), 0)  => 2
```

Without them, `IF(c, a; b, d)` is a single argument, not three.

---

## Coming from Aster

SEL takes Aster's calling convention, its `;`/`,`-as-operators grammar, its
interpolation pass, its key-value variables with scalar-context-takes-first, and
its split between numeric and text operator families.

It is **not compatible**. What will bite you porting a rule by eye:

| Aster | SEL |
|---|---|
| `FIND` is 0-based | all positions are 1-based, `0` means not found |
| `'X'` and `' '` are booleans | `TRUE` / `FALSE` are a distinct kind, no truthiness |
| `AND`/`OR` evaluate both sides; `\|AND` short-circuits | `AND`/`OR` short-circuit; there are no `\|` variants |
| floating point | exact decimal, scale preserved |
| `WHILE`, `FOR`, `DEFUN`, `LAMBDA` | none — use `COND` and the aggregates |
| `{"A"}` dynamic symbols | gone, which is what buys `dependencies()` |
| `\` alternative indexing, `~=`, `NAND`, `NOR` | gone |
| XML, XPath, XSLT, JSON, GZIP, ZIP, `QSORT`, `RANDOM` | gone |
