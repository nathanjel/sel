# Syntax

How a SEL program is written, and what its values are. The operators have
[a page of their own](operators.md), and so do [the functions](functions.md).
`spec/SPEC.md` is the normative text; this page is for people writing rules.

Every line below of the form `EXPRESSION  => RESULT` is executed by all five
implementations on every commit (`tools/check-docs.sh`). A result is written the
way the command-line tool prints it: text as it is, `TRUE`/`FALSE`, `bin:` and
hex for bytes, a tree dump for a structure, and `!E_CODE` for an error.

- [A program is one expression](#a-program-is-one-expression)
- [Comments and whitespace](#comments-and-whitespace)
- [Names](#names)
- [Values and kinds](#values-and-kinds)
- [Numbers](#numbers)
- [Text](#text)
- [Lists and records](#lists-and-records)
- [Booleans](#booleans)
- [NULL](#null)
- [Variables](#variables)
- [Binders: `_`, `_K` and named ones](#binders-_-_k-and-named-ones)
- [Errors](#errors)
- [The grammar](#the-grammar)

---

## A program is one expression

There are no statements. What looks like control flow is a function call:

```sel
IF(2 > 1, "yes", "no")  => yes
```

`IF` is not a keyword. It is an entry in the function table, like `LEN`. What
makes it work is that **a function receives the caller's syntax tree, not
values**, and decides for itself what to evaluate:

```sel
IF(1 > 0, "safe", 1 / 0)  => safe
```

The division never happens: the argument was never evaluated, because `IF`
chose not to.

`;` and `,` are ordinary operators. `;` evaluates both sides and yields the
right one; `,` builds a list. A block and an argument list are the same kind of
thing, and the last expression is the program's result:

```sel
A = 1; A + 1  => 2
```

## Comments and whitespace

`#` starts a comment that runs to the end of the line, except inside a text
literal. Spaces, tabs, carriage returns and newlines separate tokens and mean
nothing else, so a rule can be laid out however reads best:

```sel
"#1" & "a" # the rest of this line is a comment  => #1a
```

## Names

Variables, functions and binders are **ASCII identifiers** —
`[A-Za-z_][A-Za-z0-9_]*` — and **case-insensitive**: `total`, `Total` and `TOTAL`
are one name.

```sel
total = 5; TOTAL + Total  => 10
```

`TRUE`, `FALSE`, `NULL`, `AND`, `OR`, `NOT`, `XOR`, `EQL`, `IN`, `BAND`, `BOR`
and `BXOR` are reserved:

```sel
IN = 1  => !E_RESERVED
```

A name that is not a variable in the context and was not assigned is an error,
never an empty string:

```sel
FOO + 1  => !E_UNDEF_VAR
```

## Values and kinds

A value has a **kind** and, independently, may have **children** — an ordered
key→value map. There are four kinds:

| Kind | What it is |
|---|---|
| `TEXT` | a sequence of Unicode code points. **Numbers are TEXT.** |
| `BIN` | a sequence of bytes; not text, not assumed printable |
| `BOOL` | `TRUE` or `FALSE` — a kind of its own, not a string |
| `NONE` | no scalar of its own. A plain list is NONE with children; `NULL` is a NONE with neither |

TEXT and BIN convert **only through UTF-8**, and only when you ask:

```sel
TO_UTF8("ż")                  => bin:c5bc
FROM_UTF8(FROM_HEX("c5bc"))   => ż
LEN(FROM_HEX("41"))           => !E_NOT_TEXT
```

Text functions never decode bytes behind your back.

## Numbers

**There is no floating point.** A number is TEXT that reads as
`-?digits[.digits]`, and arithmetic on it is exact decimal. A literal is written
the same way:

```sel
0.10 + 0.20 == 0.30  => TRUE
19.99 * 3            => 59.97
```

**Scale is part of the value**, so money keeps its cents:

```sel
2.50 + 2.50  => 5.00
1.5 * 1.5    => 2.25
```

A leading zero goes and a trailing fraction zero stays; zero is never negative:

```sel
007     => 7
-0.00   => 0.00
```

Text from outside is a number only if it is spelled like one — **there is no
implicit trimming**:

```sel
" 2" + 1        => !E_NOT_NUM
TRIM(" 2") + 1  => 3
ISNUM(" 2")     => FALSE
```

How the operators treat numbers — division's scale, rounding, remainders — is
in [Operators](operators.md#arithmetic).

## Text

Two literal forms. **Quoted** text takes escapes and interpolation:

```sel
"a\tb"                    => a	b
"\u{17C}"                 => ż
"a\{not interpolated\}"   => a{not interpolated}
```

The escapes are `\\ \" \n \t \r \{ \}` and `\u{HEX}`; anything else is an
error:

```sel
"\q"  => !E_ESCAPE
```

**Raw** text in single quotes takes none — no escapes, no interpolation, `''`
for a quote. It is the form for regular expressions:

```sel
'^\d{3}$'  => ^\d{3}$
'it''s'    => it's
```

### Interpolation

`{…}` inside quoted text holds any expression:

```sel
A = 3; "value {A} here"                     => value 3 here
A = 3; "{A + 1}"                            => 4
P = 19.99; Q = 3; "{ROUND(P * Q, 2)} PLN"   => 59.97 PLN
```

The lexer rewrites the literal into a chain of `&` before parsing, so it costs
nothing at run time and obeys exactly what `&` obeys — a boolean is refused
rather than guessed at:

```sel
"{1 == 1}"  => !E_NOT_TEXT
```

### Positions count code points, from 1

`0` means "not found". Nothing counts bytes or UTF-16 units:

```sel
LEN("Zażółć")        => 6
BLEN("Zażółć")       => 10
LEN("👍a")           => 2
LEFT("👍ab", 1)      => 👍
FIND("ż", "Zażółć")  => 3
FIND("q", "abc")     => 0
```

Text compares in UTF-8 byte order, the same on every host, and `UPPER`/`LOWER`
touch A–Z only ([why](parity.md#text)).

## Lists and records

A variable holding several values is an ordered key→value map. `,` builds a
list keyed from `"1"`:

```sel
(3, 2, 1)                    => -{"1"=t"3", "2"=t"2", "3"=t"1"}
JOIN(("a", "b", "c"), "-")   => a-b-c
```

`LIST` builds one without flattening, and `RECORD` builds one with keys of your
choosing:

```sel
LIST(1, LIST(2, 3)) .> COUNT                     => 2
RECORD("sku", "AB-1", "qty", 3)["qty"]           => 3
```

An index is used **verbatim as a key**, so `1` and `1.0` are different keys:

```sel
A["x"] = 1; A["x"]          => 1
A[1] = "a"; HAS(A, "1.0")   => FALSE
A = (1, 2); A[3]            => !E_NO_KEY
```

An operand of `,` that is a list contributes its values, which is what makes
appending work:

```sel
A = ("a", "b"); A = (A, "c"); JOIN(A, "-")  => a-b-c
```

### Scalar context

Where a single value is wanted, a list gives its **first** value, recursively.
Returning several values costs nothing, and nothing in the language asks "is
this a list":

```sel
A[1] = 3; A == 3                            => TRUE
A = (7, 8); A + 0                           => 7
R[1] = "ok"; R[2] = "why"; R & "/" & R[2]   => ok/why
```

`COUNT` counts children, so a scalar has none; `LEN` counts characters:

```sel
COUNT(("a", "b"))   => 2
COUNT("abc")        => 0
INDEXES(("a", "b")) => -{"1"=t"1", "2"=t"2"}
```

## Booleans

`TRUE` and `FALSE` are a kind of their own, and **there is no truthiness**.
Comparisons produce booleans, and `IF`, `AND`, `OR`, `NOT` and `XOR` accept
nothing else:

```sel
IF(1, "a", "b")   => !E_NOT_BOOL
"x" AND TRUE      => !E_NOT_BOOL
```

Say what you mean instead:

```sel
NAME = ""; IF(NAME $== "", "missing", "ok")  => missing
```

This removes the single most common source of "works on the server, not in the
browser" in validation code.

## NULL

`NULL` is an absence, distinct from empty text and from zero, and it never turns
into either on its own. Arithmetic, comparison and text functions refuse it:

```sel
NULL + 1        => !E_NULL
UPPER(NULL)     => !E_NULL
NULL == 0       => !E_NULL
```

It is handled on purpose, with the coalescing operators and the null-safety
functions:

```sel
NULL ?? "default"         => default
"   " ??? "fallback"      => fallback
IS_NULL(NULL)             => TRUE
GET(NULL, "k", "def")     => def
COALESCE(NULL, "a", "b")  => a
```

See [Operators](operators.md#coalescing) and
[Functions](functions.md#null-safety-and-navigation).

## Variables

A program reads variables from the **context** the host passes in, and may
assign its own. Assignment is an operator that yields the value assigned:

```sel
A = 2                      => 2
A = 1; A += 2; A *= 3; A   => 9
```

**Assignment copies**, all the way down — two variables never share structure:

```sel
A = (1, 2); B = A; B[1] = 9; A[1]  => 1
```

An indexed assignment creates what it needs, and lands where it says it lands,
in whatever the variable is *after* the right-hand side ran:

```sel
A[COUNT(A)] = 1; A          => -{"0"=t"1"}
A[1] = (A = 2); A           => t"2"{"1"=t"2"}
```

The context is changed in place, which is how a rule hands values back to the
host ([Using SEL](usage/README.md#variables-flow-back)).

## Binders: `_`, `_K` and named ones

A function that walks a list — `ALL`, `MAP`, `FILTER`, `SUM`, `SORT_BY`,
`BUCKET` and the rest — evaluates its body once per element, with `_` bound to
the element and `_K` to its key:

```sel
M["a"] = 1; M["b"] = 2; JOIN(MAP(M, _K & "=" & _), " ")  => a=1 b=2
```

A three-argument form names the binder instead, which is how an outer element
stays reachable from an inner body:

```sel
R[1] = (1, 2); R[2] = (3, 4); ALL(R, ROW, ALL(ROW, _ > 0))  => TRUE
```

The name must be a bare identifier, and a binder cannot be assigned — it lives
for one element:

```sel
ALL((1, 2), 1, _ > 0)       => !E_EXPECT_SYMBOL
ALL((1, 2), (_ = 5; TRUE))  => !E_BAD_ASSIGN
```

## Errors

Evaluation stops at the first failure. The error carries a **stable code**, a
message, and the **position of the node that actually failed** — never the
caller's. Match on the code; the message is human text and may change.

Caught before the rule ever runs:

| Code | When |
|---|---|
| `E_SYNTAX` | it does not parse — including a chained comparison |
| `E_UNTERMINATED`, `E_ESCAPE` | a bad text literal |
| `E_RESERVED` | a reserved word used as a variable |
| `E_BAD_ASSIGN` | assignment to something that is not a name |
| `E_UNKNOWN_FUNC` | no such function |
| `E_ARITY` | wrong argument count |
| `E_REGEX_SYNTAX` | a pattern outside the portable subset |
| `E_DEPTH` | nested too deeply |

At run time:

| Code | When |
|---|---|
| `E_UNDEF_VAR`, `E_NO_KEY`, `E_NO_SCALAR` | something is not there |
| `E_NULL` | an operation that does not accept `NULL` got one |
| `E_NOT_NUM`, `E_NOT_TEXT`, `E_NOT_BIN`, `E_NOT_BOOL`, `E_NOT_INT` | the wrong kind |
| `E_EXPECT_SYMBOL` | a binder that is not a bare name |
| `E_DIV_ZERO`, `E_UTF8`, `E_RANGE`, `E_BAD_ARG`, `E_LEN_MISMATCH` | a bad value |
| `E_ABORT` | the rule called `ABORT` |

`E_ABORT` is the one a rule raises on purpose, and the one worth showing a user.
The full catalogue, with the limits that raise `E_DEPTH` and `E_RANGE`, is
[the limits reference](reference/limits.md); the normative list is
[spec/errors.md](../spec/errors.md).

## The grammar

In one screen, tightest binding first — the [Operators](operators.md) page has
the table with every operator:

```
program    = sequence
sequence   = list { ";" list }
list       = assignment { "," assignment }
term       = ... (assignment, logic, comparison, concatenation, arithmetic)
postfix    = primary { "[" sequence "]" | ".>" call-or-name }
primary    = number | 'raw' | "quoted" | name | name "(" [sequence] ")" | "(" sequence ")"
```

[spec/grammar.md](../spec/grammar.md) is the complete, normative grammar.
