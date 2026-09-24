# Operators

Every operator, its precedence, and the rules it follows. Values and literals
are on [Syntax](syntax.md); the functions are on [Functions](functions.md).
Every `=>` line is executed by all five implementations.

- [Precedence](#precedence)
- [Arithmetic](#arithmetic)
- [Concatenation](#concatenation)
- [Comparison: three families](#comparison-three-families)
- [Logic](#logic)
- [Coalescing](#coalescing)
- [Bitwise](#bitwise)
- [Assignment](#assignment)
- [Building lists and sequencing](#building-lists-and-sequencing)
- [The pipeline operator `.>`](#the-pipeline-operator-)
- [Traps for readers of other languages](#traps-for-readers-of-other-languages)

---

## Precedence

Tightest binding first:

| # | Operators | Associativity | Notes |
|---|---|---|---|
| 1 | `x[k]` `f(…)` `( )` `x .> f(…)` | left | indexing, call, grouping, forward pipeline |
| 2 | `-x` | prefix | numeric negation |
| 3 | `*` `/` `%` | left | |
| 4 | `+` `-` | left | |
| 5 | `&` | left | concatenation |
| 6 | `BAND`, then `BXOR`, then `BOR` | left | bytes of equal length |
| 7 | `??` `???` | right | coalescing, short-circuit |
| 8 | `==` `!=` `<` `<=` `>` `>=` `$==` `$!=` `$<` `$<=` `$>` `$>=` `EQL` `IN` | **none** | a comparison cannot chain |
| 9 | `NOT x` | prefix | looser than comparison |
| 10 | `AND`, then `XOR`, then `OR` | left | `AND` and `OR` short-circuit |
| 11 | `=` `+=` `-=` `*=` `/=` `%=` `&=` | right | |
| 12 | `,` | left | list building, argument separator |
| 13 | `;` | left | sequence |

## Arithmetic

`+ - * / %` and unary `-` take numbers — TEXT that reads as
`-?digits[.digits]` — and compute **exactly**. There is no floating point
anywhere in SEL.

```sel
0.10 + 0.20 == 0.30  => TRUE
2.50 + 2.50          => 5.00
1.5 * 1.5            => 2.25
-(2 - 5)             => 3
```

**Scale.** Addition and subtraction keep the larger scale of the two operands;
multiplication adds them. **Division** gives the minimal scale when the result is
exact, and ten fraction digits, rounded half away from zero, when it is not:

```sel
4 / 2    => 2
10 / 4   => 2.5
1 / 8    => 0.125
1 / 3    => 0.3333333333
2 / 3    => 0.6666666667
1 / 0    => !E_DIV_ZERO
```

`%` is the remainder of truncated division; it takes the sign of the dividend:

```sel
5 % 3    => 2
-5 % 3   => -2
5.5 % 2  => 1.5
```

An operand that is not a number is refused, and so is `NULL`:

```sel
"a" + 1   => !E_NOT_NUM
NULL * 2  => !E_NULL
```

Rounding is a function, not an operator — `ROUND(x, n)`, `FLOOR`, `CEIL`,
`TRUNC` are on [Functions](functions.md#numbers). So is `CANON`, which gives
every number the one spelling all equal numbers share, for the places where the
spelling matters ([Comparison](#comparison-three-families)).

## Concatenation

`&` joins text, and numbers are text:

```sel
"A" & "B"   => AB
2 & "A"     => 2A
"a" & TRUE  => !E_NOT_TEXT
```

If either side is BIN the result is BIN, with text encoded as UTF-8:

```sel
TO_HEX("A" & FROM_HEX("42"))  => 4142
```

## Comparison: three families

The same two values can be equal as numbers and different as text. That is the
point:

```sel
"5.00" == "5"    => TRUE
"5.00" $== "5"   => FALSE
"5.00" EQL "5"   => FALSE
```

- `==` `!=` `<` `<=` `>` `>=` compare **numerically**; both sides must be numbers.
- `$==` `$!=` `$<` `$<=` `$>` `$>=` compare **text**, byte by byte in UTF-8
  order — the same order on every host, whatever the host's own strings do.
- `EQL` compares **structure**: the same kind, the same scalar, the same
  children under the same keys in the same order.
- `x IN list` is true when some element of `list` is `EQL` to `x`.

```sel
"PL" IN ("DE", "PL")     => TRUE
(1, 2) EQL (1, 2)        => TRUE
"b" $> "a"               => TRUE
"Z" $< "a"               => TRUE
```

`EQL` — and everything built on it: `IN`, `DISTINCT`, `DEDUPE`, `BUCKET`'s keys,
index keys — tells `1`, `1.0` and `1.00` apart, because scale is part of a
number. Where a rule means "the same number", it says so with `CANON`:

```sel
1.0 EQL 1                                                    => FALSE
CANON(1.0) EQL CANON(1)                                      => TRUE
LIST(0.5 + 0.5, 4 / 4) .> DEDUPE() .> COUNT                  => 2
LIST(0.5 + 0.5, 4 / 4) .> MAP(CANON(_)) .> DEDUPE() .> COUNT => 1
```

## Logic

`AND`, `OR`, `XOR` and `NOT` take booleans and nothing else — there is no
truthiness:

```sel
TRUE AND FALSE   => FALSE
TRUE XOR TRUE    => FALSE
NOT FALSE        => TRUE
1 AND TRUE       => !E_NOT_BOOL
```

`AND` and `OR` short-circuit, so the right side is not evaluated when the left
decides:

```sel
FALSE AND (1 / 0 == 0)  => FALSE
TRUE OR (1 / 0 == 0)    => TRUE
```

## Coalescing

`a ?? b` yields `b` when `a` is `NULL` — or is a variable or key that does not
exist. `a ??? b` also yields `b` when `a` is *vacant*: empty text, text that is
only whitespace, or an empty list. Both evaluate `b` only when they need it.

```sel
NULL ?? "default"          => default
"value" ?? "fallback"      => value
MISSING ?? "fallback"      => fallback
R["x"] = 1; R["y"] ?? 0    => 0
"" ?? "fallback"           =>
"" ??? "fallback"          => fallback
"   " ??? "fallback"       => fallback
"value" ??? "fallback"     => value
```

They associate to the right, so `A ?? B ?? "last"` tries `A`, then `B`.

## Bitwise

`BAND`, `BOR` and `BXOR` work on **bytes**, not on integers, and both sides must
be the same length:

```sel
TO_HEX(FROM_HEX("0f0f") BAND FROM_HEX("00ff"))  => 000f
TO_HEX(FROM_HEX("0f") BXOR FROM_HEX("ff"))      => f0
FROM_HEX("0f") BOR FROM_HEX("0f0f")             => !E_LEN_MISMATCH
```

## Assignment

`=` stores a value and yields it; `+= -= *= /= %= &=` read the target, apply the
operator and store the result, so they need a target that exists:

```sel
A = "x"; A &= "y"; A   => xy
A += 1                 => !E_UNDEF_VAR
3 = 4                  => !E_BAD_ASSIGN
```

The target is a name, optionally indexed. Intermediate levels are created, index
expressions are evaluated once, left to right, before the right-hand side, and
the value lands at that path in the tree **as it is after the right-hand side
ran**:

```sel
A[COUNT(A)] = 1; A        => -{"0"=t"1"}
A = 1; A += (A = 5); A    => 6
A[1] = (A = 2); A         => t"2"{"1"=t"2"}
```

The last line is worth a second look. `A[1] = …` stores under key `1` of `A`, but
the right-hand side *replaced* `A` with the number `2` first — so the store lands
in the new `A`, and the result is `2` carrying a child `1`. Most languages would
have written into the old, now unreachable `A` and silently lost the assignment;
SEL prefers the visible answer to the vanished one, the same way it prefers an
error to a truthy guess. Nobody writes this on purpose, but rules grow in layers,
and the day two layers touch one variable, all five hosts still agree.

Assignment **copies**. `,` is the only other operator that copies; everything
else hands on the value itself:

```sel
A = (1, 2); B = A; B[1] = 9; A[1]  => 1
```

## Building lists and sequencing

`,` builds a list keyed `"1"`, `"2"`, …; an operand that is itself a list
contributes its values, and the keys are always renumbered:

```sel
(1, 2), 3                          => -{"1"=t"1", "2"=t"2", "3"=t"3"}
A = ("a", "b"); A = (A, "c"); COUNT(A)  => 3
```

`;` evaluates the left, then the right, and yields the right:

```sel
A = 2; B = 3; A * B  => 6
```

Inside a call, `;` binds looser than the argument separator, so a sequence as one
argument needs its own parentheses:

```sel
IF(TRUE, (A = 1; A + 1), 0)  => 2
```

## The pipeline operator `.>`

`x .> F(…)` is `F(x, …)`: the value on the left becomes the first argument of the
call on the right. It rewrites the program at compile time, so it costs nothing,
and it lets a chain of steps read in the order they happen:

```sel
"hello" .> UPPER                              => HELLO
"hello world" .> LEFT(5)                      => hello
(1, 2, 3, 4) .> FILTER(_ % 2 == 0) .> COUNT   => 2
(3, 1, 2) .> SORT() .> JOIN(",")              => 1,2,3
```

When the call already has at least as many arguments as the function needs and
one of them is a bare `_`, the value goes there instead of first:

```sel
"," .> JOIN((1, 2, 3), _)  => 1,2,3
```

`.>` binds as tightly as indexing, so a pipeline is an operand like any other:

```sel
(1, 2, 3) .> COUNT > 2                      => TRUE
(5, 1) .> SORT() .> JOIN("") $== "15"       => TRUE
```

Pipelines are how SEL writes data processing — `FILTER`, `MAP`, `SORT_BY`,
`BUCKET`, `LINK` and the rest are ordinary functions, and a chain of them over a
relation is what the SQL layer can turn into one `SELECT`
([SQL pipelines](usage/sql-pipelines.md)):

```sel
R = LIST(RECORD("c", "a", "v", 2), RECORD("c", "b", "v", 5), RECORD("c", "a", "v", 4)); R .> BUCKET(_["c"], RECORD("c", _K, "total", SUM(_, _["v"]))) .> SORT_BY(_["total"], "DESC") .> MAP(_["c"] & "=" & _["total"]) .> JOIN(" ")  => a=6 b=5
```

## Traps for readers of other languages

`NOT` binds *looser* than a comparison, so this reads as `NOT (1 == 2)`:

```sel
NOT 1 == 2   => TRUE
```

A comparison does not chain — the error arrives when the rule is compiled, not
as a puzzling type error later:

```sel
1 < 2 < 3    => !E_SYNTAX
```

Text from a form is not a number until it is trimmed, and a number compared as
text is compared by its bytes:

```sel
" 2" + 1     => !E_NOT_NUM
"10" $< "9"  => TRUE
10 < 9       => FALSE
```
