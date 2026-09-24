# Overview

**SEL — the Simple Expression Language — is a small language for business
rules, with five implementations that give the same answer.**

A rule is a piece of text:

```sel
TOTAL = SUM(ITEMS, _["qty"] * _["price"]);
COND(IS_BLANK(CUSTOMER),                   ABORT("customer is required"),
     NOT RMATCH('^\d{2}-\d{3}$', POSTCODE), ABORT("postcode {POSTCODE} is not 00-000"),
     TOTAL > CREDIT_LIMIT,                  ABORT("total {TOTAL} exceeds {CREDIT_LIMIT}"),
     "ok")
```

Python, JavaScript, PHP, C++23 and Common Lisp each run it, and each gives the
same answer — the same value, or the same error code at the same line and
column. The same rule can also run *inside a database*: it compiles to a SQL
condition, and a pipeline of steps compiles to a whole `SELECT`, for MariaDB,
MySQL, PostgreSQL and SQLite, with a planner that splits work between the
database and memory when SQL can do only part of it.

This page is the idea. [How parity is guaranteed](parity.md) is the machinery,
[Syntax](syntax.md), [Operators](operators.md) and [Functions](functions.md) are
the language, and [Using SEL](usage/README.md) is the code around it.

- [The problem](#the-problem)
- [What a SEL program is](#what-a-sel-program-is)
- [Design principles](#design-principles)
- [Where SEL fits](#where-sel-fits)
- [What SEL leaves out, and why](#what-sel-leaves-out-and-why)
- [Where it came from](#where-it-came-from)

---

## The problem

Most applications check the same thing twice: once in the browser, so the user
hears straight away that the postcode looks wrong, and once on the server,
because the browser cannot be trusted. Often a third time, in a nightly job or
a report query. Each copy is written in a different language by a different
person, and each interprets "empty", "equal" and "rounded" in its own way. They
drift, and the bug surfaces for the one customer whose order total lands exactly
on a rounding boundary, or whose name contains a character one regex engine
thinks is a letter.

The usual fixes do not quite fix it. A shared schema checks shapes, but not "the
total must not exceed the credit limit". A rules engine is a runtime of its own
to deploy next to every service. Generating code for every target means owning
a generator per target.

SEL's answer is one rule, as text, and several interpreters held to one written
specification by tests that run all of them and compare the results **byte for
byte**. Where the host languages cannot be made to agree, SEL leaves the feature
out rather than pick a winner.

## What a SEL program is

**One expression.** There are no statements; `;` is an operator that yields its
right side, `,` builds a list, and the last value is the result:

```sel
NET = 19.99 * 3; VAT = ROUND(NET * 0.23, 2); NET + VAT  => 73.76
```

**Functions receive syntax, not values.** `IF`, `COND` and the aggregates are
ordinary entries in the function table. They are handed their arguments
unevaluated and decide what to evaluate — which is why no control-flow syntax is
needed, and why this is safe:

```sel
IF(1 > 0, "safe", 1 / 0)  => safe
```

**Iteration is a function with a body.** `MAP`, `FILTER`, `ALL`, `SUM` and the
rest evaluate an expression once per element, and the pipeline operator `.>`
chains them into data processing:

```sel
(19.99, 5.01, 0.50) .> FILTER(_ > 1) .> MAP(_ * 2) .> JOIN(" + ")  => 39.98 + 10.02
```

**A program is data about itself.** Because no name is ever computed at run time,
the set of inputs a rule reads is known without running it — the host API calls
it `dependencies()`, and a form uses it to re-check only the rules a changed
field can affect.

## Design principles

**Exact numbers.** There is no floating point. Numbers are decimal and exact, and
their scale is part of their value, so money keeps its cents:

```sel
0.10 + 0.20 == 0.30  => TRUE
2.50 + 2.50          => 5.00
1 / 3                => 0.3333333333
```

**No truthiness.** Conditions are booleans. An empty string is not "false", and
`1` is not "true":

```sel
IF(1, "a", "b")      => !E_NOT_BOOL
IF("" $== "", "empty", "not")  => empty
```

**Missing things are loud.** An undefined variable, a missing key and a `NULL`
used where a value is needed are errors, never an empty string or a zero:

```sel
PRICE * 2            => !E_UNDEF_VAR
NULL + 1             => !E_NULL
NULL ?? 0            => 0
```

**Every failure has a code and a place.** Errors carry a stable code — the part
applications and tests rely on — and the position of the node that actually
failed. `ABORT` is how a rule reports a business problem, as distinct from a
rule that could not be computed:

```sel
ROUND(2 + "x", 2)    => !E_NOT_NUM
ABORT("out of stock") => !E_ABORT
```

**Refuse rather than guess.** A regex feature two engines interpret differently
is rejected when the rule is compiled; a rule SQL cannot express faithfully is
refused by the translator instead of being approximated; `UPPER` touches ASCII
letters only, because no two hosts agree beyond them. The limitation is visible,
and the same everywhere.

**Values alias, assignment copies.** A value is a reference while an expression
works on it and a copy once it is stored, the same way in every host. That is a
rule of the language, not an accident of five object models, and it is what
makes an assignment land where it says it lands:

```sel
A = (1, 2); B = A; B[1] = 9; A[1]  => 1
A[1] = (A = 2); A                  => t"2"{"1"=t"2"}
```

## Where SEL fits

- **Validation that runs in the browser and on the server.** The same rule
  text, compiled by the JavaScript and the backend host. [Validation](usage/validation.md)
- **Scripting an application's behaviour.** The application registers the
  functions a script may call — stock lookups, messages, prices — and the policy
  lives in text the business can change. [Scripting](usage/scripting.md)
- **Filtering in the database.** A rule becomes a `WHERE` condition — without
  knowing the schema, or with a description of it that makes the SQL tight and
  lets rules reach related tables. [SQL conditions](usage/sql-conditions.md)
- **Whole data pipelines.** `FILTER`, `LINK`, `BUCKET`, `MAP` and the sorts over
  relations become one SQL statement when the database can answer all of it,
  a SQL prefix plus an in-memory remainder when it can answer part, and plain
  in-memory processing when it can answer none — with identical results
  whichever route runs. [SQL pipelines](usage/sql-pipelines.md)
- **A small, predictable language to embed.** Python, PHP and JavaScript need no
  dependencies at all; C++ is three files and a vendored regex engine; Lisp is
  an ASDF system. [Using SEL](usage/README.md)

## What SEL leaves out, and why

| Left out | Why |
|---|---|
| floating point | it cannot hold money, and the hosts' floats disagree at the edges |
| truthiness | `""`, `"0"`, `0` and `NULL` are "false" differently in every language |
| loops, recursion, user-defined functions in the language | a rule should terminate and be readable; aggregates cover iteration, and the host adds functions |
| dynamic variable names | they would make `dependencies()` impossible |
| Unicode case mapping | no two hosts agree on it; ASCII case is exact everywhere |
| `SQRT`, `LOG`, `RANDOM` | no exact decimal result; no testable result |
| regex word boundaries, lookaround, backreferences | the engines disagree, so the pattern would mean different things |
| JSON, XML, dates | a host converts them into SEL values; SEL does not pick one parser's opinion |

Everything above is a decision, recorded in [the specification](../spec/SPEC.md)
and pinned by conformance cases, not a missing feature waiting for a volunteer.

## Where it came from

SEL borrows its central idea from [Aster](https://help.int4.com/int4-aster-documentation/):
arguments passed as syntax and evaluated at the callee's discretion, `;` and `,`
as operators, string interpolation, key–value variables whose first value is
their scalar, and separate operator families for numbers and text.

It is not compatible with Aster, and a rule ported by eye will meet these
differences:

| Aster | SEL |
|---|---|
| `FIND` is 0-based | positions are 1-based; `0` means not found |
| `'X'` and `' '` are booleans | `TRUE` and `FALSE` are a kind of their own; no truthiness |
| `AND`/`OR` evaluate both sides; `\|AND` short-circuits | `AND`/`OR` short-circuit; there are no `\|` forms |
| floating point | exact decimal, scale preserved |
| `WHILE`, `FOR`, `DEFUN`, `LAMBDA` | none — `COND` and the aggregates; functions come from the host |
| `{"A"}` dynamic symbols | none, which is what makes `dependencies()` possible |
| `\` indexing, `~=`, `NAND`, `NOR` | none |
| XML, XPath, XSLT, JSON, GZIP, ZIP, `QSORT`, `RANDOM` | none |
