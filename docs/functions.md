# Functions

Every builtin, by what it is for. Names are case-insensitive. Every `=>` line is
executed by all five implementations; the generated
[builtin index](reference/builtins.md) lists each function's accepted argument
counts and binding forms, and `spec/SPEC.md` §7 is the normative text.

- [How a function is called](#how-a-function-is-called)
- [Control](#control)
- [Iterating: aggregates](#iterating-aggregates)
- [Sorting and picking](#sorting-and-picking)
- [Grouping: `BUCKET`](#grouping-bucket)
- [Structure and slicing](#structure-and-slicing)
- [Relations: `LINK` and `LINK_LEFT`](#relations-link-and-link_left)
- [Null safety and navigation](#null-safety-and-navigation)
- [Text](#text)
- [Numbers](#numbers)
- [Binary](#binary)
- [Regular expressions](#regular-expressions)
- [Your own functions](#your-own-functions)

---

## How a function is called

`NAME(a, b, c)`. An unknown name, or the wrong number of arguments, is an error
when the rule is **compiled**, before it ever runs:

```sel
NOSUCH(1)   => !E_UNKNOWN_FUNC
LEFT("a")   => !E_ARITY
```

Most functions are **strict**: every argument is evaluated, left to right, before
the function runs. A few are **lazy**: they receive their arguments as syntax
and evaluate what they choose — which is what lets `IF` skip a branch and `MAP`
run its body once per element. The lazy ones are `IF`, `COND`, `COALESCE`,
`GET`, `PATH` and the functions that take a *body*: `ALL`, `ANY`, `MAP`,
`FILTER`, `SUM`, the sorts, `TOP`, `BUCKET` and `LINK`.

A body sees the element as `_` and its key as `_K`; a binder name in front of the
body replaces `_` ([Syntax](syntax.md#binders-_-_k-and-named-ones)). Any
function can also be called through the [pipeline operator](operators.md#the-pipeline-operator-):
`x .> F(a)` is `F(x, a)`.

## Control

| Signature | Yields |
|---|---|
| `IF(cond, then [, else])` | the chosen branch, evaluating only that one; `""` when false and there is no `else` |
| `COND(c1, r1, c2, r2, …, default)` | the first result whose condition is `TRUE`; the default is mandatory |
| `ABORT(message)` | never returns: fails with `E_ABORT` and your message |

```sel
IF(1 > 2, "yes", "no")                                        => no
"[" & IF(1 > 2, "yes") & "]"                                  => []
S = 85; COND(S >= 90, "A", S >= 80, "B", S >= 70, "C", "F")   => B
COND(TRUE, "safe", TRUE, 1 / 0, 1 / 0)                        => safe
```

`COND` needs an odd number of arguments. With an even count, one misplaced comma
would shift every pair and still compile, so it is refused instead:

```sel
COND(TRUE, "a", FALSE, "b")        => !E_ARITY
ABORT("out of stock")              => !E_ABORT
IF(FALSE, ABORT("never"), "fine")  => fine
```

`ABORT` is how a rule says "this is not valid" — as opposed to "this could not be
computed" — and the host tells the two apart by the code
([Validation](usage/validation.md)).

## Iterating: aggregates

There are no loops. These evaluate a body once per element.

| Signature | Yields |
|---|---|
| `ALL(list, [binder,] body)` | `TRUE` when the body is `TRUE` for every element; stops at the first `FALSE`; `TRUE` for no elements |
| `ANY(list, [binder,] body)` | `TRUE` when the body is `TRUE` for some element; stops at the first; `FALSE` for none |
| `MAP(list, [binder,] body)` | the body's results, keyed from `"1"` |
| `FILTER(list, [binder,] body)` | the elements whose body is `TRUE`, **keeping their keys** |
| `SUM(list, [binder,] body)` | the exact sum of the body's results; `0` for none |
| `JOIN(list, separator)` | the elements' text joined — strict: the separator is not a body |

```sel
ALL((1, 2, 3), _ > 0)                        => TRUE
ANY((1, -2), _ < 0)                          => TRUE
JOIN(MAP((1, 2), _ * 2), ",")                => 2,4
SUM((1.5, 2.5), _)                           => 4.0
JOIN(FILTER((1, 2, 3, 4), _ % 2 == 0), ",")  => 2,4
FILTER((1, 2, 3, 4), _ % 2 == 0)             => -{"2"=t"2", "4"=t"4"}
```

`ALL` and `ANY` short-circuit, so a later element is never evaluated:

```sel
ALL((0, 1), _ > 0 AND 1 / _ > 0)  => FALSE
```

A scalar behaves as a list of one, and an empty result as an empty list:

```sel
ALL(5, _ > 0)                      => TRUE
ALL(FILTER((1, 2), _ > 5), _ > 0)  => TRUE
JOIN("solo", "-")                  => solo
```

### Folding

`SUM` and `JOIN` are the two folds a rule usually needs. For any other, keep an
accumulator in a variable and walk the list with `ALL`, whose body must end in
`TRUE` so the walk does not stop early:

```sel
ACC = "1"; ALL((2, 3, 4), (ACC = ACC * _; TRUE)); ACC       => 24
ACC = "0"; ALL((3, 9, 2), (ACC = MAX(ACC, _); TRUE)); ACC   => 9
```

The accumulator is an ordinary variable and outlives the walk; name it for what
it holds. If a fold is common in your rules, it may deserve a function of its
own ([Extending SEL](extending.md)).

## Sorting and picking

| Signature | Yields |
|---|---|
| `SORT(list [, binder,] [body])` / `SORT_DESC(…)` | the elements ascending / descending, by themselves or by the body |
| `SORT_BY(list, [binder,] key [, "ASC" \| "DESC"])` | the elements ordered by `key` |
| `TOP(list, [binder,] [body,] n)` / `TOP_DESC(…)` | the first `n` of `SORT` / `SORT_DESC` |
| `TOP_BY(list, [binder,] key, "DESC", n)` | the first `n` by `key`; the direction, when given as text, comes **before** `n` |

Numbers sort as numbers and text by its UTF-8 bytes. Every sort is **stable**:
equal keys keep their order, so a second sort breaks the ties the first one
left:

```sel
(3, 1, 2) .> SORT() .> JOIN(",")                     => 1,2,3
(3, 1, 2) .> SORT_DESC() .> JOIN(",")                => 3,2,1
("b", "C", "a") .> SORT() .> JOIN(",")               => C,a,b
(10, 9, 100) .> SORT() .> JOIN(",")                  => 9,10,100
("pear", "fig", "banana") .> SORT(LEN(_)) .> JOIN(",")  => fig,pear,banana
(4, 9, 1, 7) .> TOP(2) .> JOIN(",")                  => 1,4
(4, 9, 1, 7) .> TOP_DESC(2) .> JOIN(",")             => 9,7
```

On records, sort by a field. The later sort is the primary key:

```sel
R = LIST(RECORD("n", "a", "v", 2), RECORD("n", "b", "v", 1), RECORD("n", "c", "v", 2)); R .> SORT_BY(_["v"], "DESC") .> MAP(_["n"]) .> JOIN(",")  => a,c,b
R = LIST(RECORD("n", "a", "v", 2), RECORD("n", "b", "v", 1), RECORD("n", "c", "v", 2)); R .> SORT_BY(_["n"], "DESC") .> SORT_BY(_["v"]) .> MAP(_["n"]) .> JOIN(",")  => b,c,a
R = LIST(RECORD("n", "a", "v", 2), RECORD("n", "b", "v", 1), RECORD("n", "c", "v", 3)); R .> TOP_BY(_["v"], "DESC", 1) .> MAP(_["n"]) .> JOIN(",")  => c
```

## Grouping: `BUCKET`

`BUCKET` groups the elements of a list by a key, in the order each key first
appears. It has two spellings.

With **two arguments** it yields a record: one entry per group, keyed by the
group key, holding the group's members. The key must be text or a number,
because it becomes a record key:

```sel
("apple", "avocado", "banana") .> BUCKET(LEFT(_, 1)) .> INDEXES() .> JOIN(",")  => a,b
("apple", "avocado", "banana") .> BUCKET(LEFT(_, 1)) .> MAP(COUNT(_)) .> JOIN(",")  => 2,1
```

With **three arguments** — a key and a *projection* — it yields one projected
value per group. Inside the projection `_` is the group's list of members and
`_K` is its key, so `COUNT(_)` and `SUM(_, …)` aggregate the group:

```sel
R = LIST(RECORD("c", "a", "v", 2), RECORD("c", "b", "v", 5), RECORD("c", "a", "v", 4)); R .> BUCKET(_["c"], RECORD("c", _K, "n", COUNT(_), "total", SUM(_, _["v"]))) .> MAP(_["c"] & ":" & _["n"] & ":" & _["total"]) .> JOIN(" ")  => a:2:6 b:1:5
```

This spelling accepts any key — a list or record of several fields groups by all
of them — and compares keys by identity (`EQL`), so `1` and `1.0` are two groups
unless the key says `CANON`.

## Structure and slicing

| Signature | Yields |
|---|---|
| `COUNT(x)` | the number of children (`0` for a scalar) |
| `INDEXES(x)` | the keys, as a list |
| `HAS(x, key)` | whether `x` has that key |
| `LIST(a, b, …)` | a list of exactly these values, lists inside kept as they are |
| `RECORD(k1, v1, k2, v2, …)` | a record; a repeated key keeps its first position and its last value |
| `TAKE(list, n)` / `DROP(list, n)` | the first `n` elements / all but the first `n` |
| `SELECT_COLS(list, "a", "b", …)` | each record with only these fields |
| `DISTINCT(list)` / `DEDUPE(list)` | the elements without repeats (by `EQL`), in first-seen order |

```sel
HAS(RECORD("a", 1), "a")                       => TRUE
RECORD("a", 1, "b", 2, "a", 3) .> INDEXES() .> JOIN(",")  => a,b
RECORD("a", 1, "b", 2, "a", 3)["a"]            => 3
(1, 2, 3, 4) .> TAKE(2) .> JOIN(",")           => 1,2
(1, 2, 3, 4) .> DROP(3) .> JOIN(",")           => 4
(1, 2, 3) .> TAKE(2) .> DROP(1) .> JOIN(",")   => 2
(1, 2, 1, 3, 2) .> DISTINCT() .> JOIN(",")     => 1,2,3
LIST(RECORD("a", 1, "b", 2)) .> SELECT_COLS("b") .> MAP(INDEXES(_) .> JOIN(",")) .> JOIN("")  => b
```

Every one of these renumbers its result from `"1"` — only `FILTER` keeps keys.

## Relations: `LINK` and `LINK_LEFT`

`LINK(left, right, pred)` is an inner join: one row for every pair of a left and
a right element for which `pred` is `TRUE`, in left order and then right order.
Inside `pred`, `_1` is the left element and `_2` the right; the five-argument form
`LINK(left, right, L, R, pred)` names them.

A **joined row** holds each side under its binder names — and under the name of
the relation, when the argument is a plain variable — followed by the fields of
both sides that are not ambiguous:

```sel
O = LIST(RECORD("oid", 1, "cid", 7), RECORD("oid", 2, "cid", 8)); C = LIST(RECORD("cid", 7, "name", "Ann")); O .> LINK(C, _1["cid"] == _2["cid"]) .> MAP(_["oid"] & ":" & _["name"]) .> JOIN(" ")  => 1:Ann
O = LIST(RECORD("oid", 1, "cid", 7), RECORD("oid", 2, "cid", 8)); C = LIST(RECORD("cid", 7, "name", "Ann")); O .> LINK(C, X, Y, X["cid"] == Y["cid"]) .> MAP(_["Y"]["name"]) .> JOIN("")  => Ann
```

A field both sides carry — the join key above — is not promoted, because it
would be ambiguous; read it through a binder. `LINK_LEFT` also keeps every left
element that matched nothing, with a right side whose fields are all `NULL`:

```sel
O = LIST(RECORD("oid", 1, "cid", 7), RECORD("oid", 2, "cid", 8)); C = LIST(RECORD("cid", 7, "name", "Ann")); O .> LINK_LEFT(C, X, Y, X["cid"] == Y["cid"]) .> MAP(_["oid"] & ":" & (_["Y"]["name"] ?? "-")) .> JOIN(" ")  => 1:Ann 2:-
```

When `pred` is one `==` or `$==` between the two sides, the join is matched by
key rather than by trying every pair; the result is the same either way.
[SQL pipelines](usage/sql-pipelines.md) show `LINK` over database tables, where
it becomes an `INNER JOIN`.

## Null safety and navigation

| Signature | Yields |
|---|---|
| `IS_NULL(x)` / `IS_NOT_NULL(x)` | whether `x` is `NULL` |
| `IS_BLANK(x)` / `IS_PRESENT(x)` | whether `x` is `NULL`, empty or only whitespace / the opposite |
| `COALESCE(a, b, …)` | the first argument that is not `NULL` |
| `GET(x, key [, default])` | `x[key]`, or the default (`NULL` if none) when `x` is `NULL` or lacks the key |
| `PATH(x, "a.b.c" [, default])` | the same along a dotted path |

```sel
IS_NULL(NULL)                   => TRUE
IS_NOT_NULL("a")                => TRUE
IS_BLANK("   ")                 => TRUE
IS_PRESENT("hello")             => TRUE
COALESCE(NULL, "a", "b")        => a
C["x"] = 10; GET(C, "x")        => 10
GET(NULL, "k", "def")           => def
N["a"]["b"] = 5; PATH(N, "a.b") => 5
PATH(NULL, "a.b", 0)            => 0
```

## Text

Positions are 1-based and count code points; `0` means "not found".

| Signature | Yields | |
|---|---|---|
| `LEN(x)` | code points | `LEN("👍a")  => 2` |
| `LEFT(x, n)` / `RIGHT(x, n)` | the first / last `n` | `RIGHT("abc", 2)  => bc` |
| `SUBSTR(x, start [, len])` | from `start` | `SUBSTR("abcdef", 3, 2)  => cd` |
| `FIND(needle, hay [, from])` | a position, `0` if absent | `FIND("c", "abc")  => 3` |
| `REPLACE(needle, repl, hay)` | every occurrence replaced | `REPLACE("a", "X", "banana")  => bXnXnX` |
| `SPLIT(x, sep)` | a list | `JOIN(SPLIT("a,b", ","), "-")  => a-b` |
| `TRIM` / `LTRIM` / `RTRIM` | space, tab, CR and LF removed | `TRIM("  x  ")  => x` |
| `UPPER(x)` / `LOWER(x)` | A–Z and a–z only | `UPPER("aÄz")  => AÄZ` |
| `BACKWARDS(x)` | reversed code points | `BACKWARDS("ab👍")  => 👍ba` |
| `REPEAT(x, n)` | `n` copies | `REPEAT("ab", 3)  => ababab` |
| `PADL(x, n, fill)` / `PADR(…)` | padded to `n`, never cut | `PADL("7", 3, "0")  => 007` |
| `CHAR(n)` | the code point `n` | `CHAR(128077)  => 👍` |
| `CODE(x)` | the first code point | `CODE("👍")  => 128077` |

`UPPER` and `LOWER` touch ASCII letters only, on every host, because the hosts'
own case mappings disagree — PHP's is byte-based, JavaScript's and Python's are
Unicode's, and Python's can change a string's length (`"ß".upper()` is `"SS"`).
SEL would rather be visibly limited than quietly different.

## Numbers

| Signature | Yields | |
|---|---|---|
| `ABS(x)` / `SIGN(x)` | absolute value / `-1`, `0` or `1` | `SIGN(-0.1)  => -1` |
| `CEIL(x)` / `FLOOR(x)` / `TRUNC(x)` | scale 0 | `FLOOR(-2.5)  => -3` |
| `ROUND(x, n)` | exactly `n` decimals, half away from zero | `ROUND(2.5, 0)  => 3` |
| `MIN(a, …)` / `MAX(a, …)` | the smallest / largest | `MIN(3, 1, 2)  => 1` |
| `POWER(x, n)` | `n` a whole number ≥ 0 | `POWER(2.5, 2)  => 6.25` |
| `CANON(x)` | the canonical spelling: no trailing fraction zeros | `CANON(2.50)  => 2.5` |
| `ISNUM(x)` | whether `x` is a number; never fails | `ISNUM(" 2")  => FALSE` |

```sel
ROUND(2.345, 2)     => 2.35
ROUND(-2.5, 0)      => -3
ROUND(2, 2)         => 2.00
CANON("007.50")     => 7.5
CANON(100.00)       => 100
```

`SQRT`, `LOG` and `RANDOM` do not exist: the first two have no exact decimal
result, and the third would make a rule untestable.

## Binary

| Signature | Yields | |
|---|---|---|
| `BLEN(x)` | bytes | `BLEN("ż")  => 2` |
| `TO_UTF8(x)` / `FROM_UTF8(x)` | TEXT ↔ BIN | `TO_UTF8("ż")  => bin:c5bc` |
| `TO_HEX(x)` / `FROM_HEX(x)` | lower-case hex | `TO_HEX("AB")  => 4142` |
| `ENCODE_BASE64` / `DECODE_BASE64` | standard alphabet, padded, strict | `ENCODE_BASE64("hello")  => aGVsbG8=` |
| `CRC32(x)` | CRC-32/ISO-HDLC, 8 hex digits | `CRC32("123456789")  => cbf43926` |
| `BTL(x)` / `LTB(list)` | bytes ↔ a list of 0–255 | `LTB((65, 66))  => bin:4142` |

## Regular expressions

Write patterns as raw `'…'` literals so backslashes need no doubling.

| Signature | Yields | |
|---|---|---|
| `RMATCH(pattern, subject [, flags])` | whether it matches | `RMATCH('^\d{2}-\d{3}$', "31-874")  => TRUE` |
| `RFIND(pattern, subject [, flags])` | the position of the first match, `0` if none | `RFIND('b', "abc")  => 2` |
| `RGROUPS(pattern, subject [, flags])` | the whole match at `"1"`, then the groups | `RGROUPS('(a)(b)', "ab")  => -{"1"=t"ab", "2"=t"a", "3"=t"b"}` |
| `RREPLACE(pattern, repl, subject [, flags])` | every match replaced | `RREPLACE('\s+', " ", "a   b")  => a b` |

A replacement understands `$0`–`$9` and `$$`; nothing else in it is special:

```sel
RREPLACE('(a)(b)', "$2$1", "abab")  => baba
RREPLACE('a', "x$&y", "a")          => x$&y
```

The one flag is `i`, and it needs an ASCII pattern.

Patterns are a **portable subset**, checked when the rule is compiled: literals,
`.` `^` `$`, classes with ranges and negation, `\d \D \w \W \s \S`,
`\n \r \t \f`, escaped metacharacters, `* + ? {n} {n,} {n,m}` and their lazy
forms, groups, non-capturing groups and `|`. What two engines would disagree on
is refused — `\b`, backreferences, lookaround, atomic groups, possessive
quantifiers, inline flags, POSIX classes, `\p{…}`:

```sel
RMATCH('(?=a)', "a")        => !E_REGEX_SYNTAX
RMATCH('[[:alpha:]]', "a")  => !E_REGEX_SYNTAX
RMATCH('\bx', "x")          => !E_REGEX_SYNTAX
```

`.` matches any character including a newline, `^` and `$` anchor to the very
ends of the subject, and `\d` is ASCII on every host:

```sel
RMATCH('^a.b$', "a\nb")      => TRUE
RMATCH('^abc$', "abc\n")     => FALSE
RMATCH('^\d$', "\u{0661}")   => FALSE
```

## Your own functions

An application can add functions of its own — to look up stock, to format money
its way, to send a message — with one registration call in its host language.
They are called like any builtin and follow the same rules. See
[Scripting with host functions](usage/scripting.md) and
[Extending SEL](extending.md).
