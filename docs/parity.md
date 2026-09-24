# How parity is achieved and guaranteed

Five implementations giving the same answer is the whole product. This page is
how that is arranged, and how it is checked on every change. The contributor's
side of it — the order of work and the traps each host has fallen into — is in
[Contributing](contributing.md).

- [The rule](#the-rule)
- [Achieving it: never use the host's idea of anything the language defines](#achieving-it-never-use-the-hosts-idea-of-anything-the-language-defines)
- [Authored once, rendered five times](#authored-once-rendered-five-times)
- [Guaranteeing it: the gate](#guaranteeing-it-the-gate)
- [What a new implementation has to pass](#what-a-new-implementation-has-to-pass)

---

## The rule

**Specification first, then conformance cases, then every host, then the
gate.** A feature is never implemented in one host and "ported later": a feature
that exists in one host is a feature nobody has compared with anything. No
implementation is the reference. When two disagree, [the specification](../spec/SPEC.md)
and [the conformance suite](../conformance/README.md) decide which one is wrong,
and the minimal case that shows the disagreement is added to the suite *before*
any host is fixed — the case is the part that lasts.

## Achieving it: never use the host's idea of anything the language defines

Each host language has its own notion of a number, a string, a character, a
regex, a copy. Where SEL defines one of those, no host uses its own.

### Numbers

There is no floating point. Arithmetic is exact decimal, hand-written in each
host, because none of them has a usable exact type that carries scale: PHP has
no big integer and BCMath is optional, JavaScript and C++ have doubles, and a
Lisp ratio cannot tell `2.50` from `2.5`. Python's `decimal` could do it and is
deliberately *not* used — it is the oracle the other cores are tested against,
and a host built on it would be marking its own homework.

```sel
2.50 + 2.50          => 5.00
0.10 + 0.20 > 0.30   => FALSE
```

### Text

Every host validates UTF-8 strictly, and every length and position counts code
points — not PHP's bytes, JavaScript's UTF-16 units or C++'s `char`s. Text
compares in UTF-8 byte order, because JavaScript's native order is UTF-16's and
Lisp's is code points', and both differ from it above U+FFFF.

`UPPER` and `LOWER` are ASCII-only. `strtoupper`, `toUpperCase`,
`std::toupper`, `string-upcase` and `str.upper` cannot be reconciled without
shipping a case table, and the last can change a string's length. Even "digit"
is defined here: SBCL's `DIGIT-CHAR-P` accepts U+0661 ARABIC-INDIC DIGIT ONE,
and Python's `int()` accepts that and `"1_2"`, so every host checks for `0`–`9`
itself.

```sel
UPPER("straße")        => STRAßE
"\u{0661}" + 1         => !E_NOT_NUM
```

### Regular expressions

Patterns are checked against a subset that both PCRE and ECMAScript read the
same way, at compile time. `\d`, `\w` and `\s` are rewritten into explicit ASCII
classes, because PHP's `u` modifier turns on Unicode properties and
JavaScript's does not. `\b` is refused outright: a word boundary depends on the
engine's idea of a word character. Each host's engine is bent to one shape —
JavaScript's `RegExp` with `us`, PHP's `preg` with `usD`, the vendored SRELL in
C++ (an ECMAScript engine, so it agrees with JavaScript by construction), and
cl-ppcre and Python's `re` with `^`/`$` lowered to `\A`/`\z`, because Perl-style
engines let `$` match before a trailing newline. Case-insensitive matching
needed correcting in both directions: cl-ppcre folds neither of the two
non-ASCII code points that fold to an ASCII letter, and Python folds those and
two more.

### Identity

Evaluating an expression yields a value, not a snapshot of one, so a change made
by a later part of an expression is visible through a reference taken earlier.
Only assignment and `,` copy. Every host aliases by default and copies exactly
there — a rule, not an accident of each language's object model. (The C++
`Value` used to copy on every assignment and disagreed with the other four in
six ways, one of which returned a wrong number rather than an error.)

### Evaluation order

Strictly left to right, everywhere. In C++, where the order of function
arguments is unspecified, every operand is bound to a named local first — the
fuzzer caught `TRUE $== FALSE` evaluating its right side first.

### NULL

`NULL` never turns into zero, empty text or false; the operations that cannot
take it fail with `E_NULL`, identically in memory and in translated SQL.

### SQL

The SQL layer holds itself to the same standard against the databases: a
translation is emitted only when the SQL means exactly what SEL means — with
exact collations for text, guarded casts for numbers that may not be numbers,
and `NULL`-aware shapes for the aggregates — and is refused otherwise. What a
database may still do differently, it says as a *caveat* on the result.
[SEL and SQL](sql.md) has the details.

## Authored once, rendered five times

What can be data is written once and generated into each host's own source
language. The renderings are committed — a PHP or C++ user never needs Node —
and the gate fails if one is stale.

| Authored | Rendered into every host | What it fixes |
|---|---|---|
| `spec/builtins.json` | each host's function-table manifest, and [the builtin index](reference/builtins.md) | names, arities, laziness and binding forms; each host checks itself against it at start-up |
| `spec/limits.json` | each host's limits, and [the limits reference](reference/limits.md) | depth caps, decimal caps and the error-code catalogue |
| `spec/math-ops.json` | each host's math-plan vocabulary | which arithmetic the optimiser may compile |
| `sql/dialects/*.json` | each host's dialect map | every SQL spelling, per dialect |
| `sql/cases/*.sqlt` | each host's SQL case table | the exact SQL every host must emit |

## Guaranteeing it: the gate

`tools/check.sh` runs every layer and prints `ALL GREEN`, or it isn't done. It
refuses a partial roster — every comparison is a no-op with nothing to compare
against — and it starts its own pinned database servers in Docker for the layers
that need them. The layers run in parallel and report in a fixed order.

| Layer | What it catches |
|---|---|
| **Conformance** | the normative suite (`conformance/*.selt`), run by every host and by the JavaScript bundles and the Python wheel: values, error codes, error positions |
| **Unit tests** | the layers under the suite — decimal, UTF-8, values — where one bug would otherwise surface as a hundred confusing conformance failures |
| **Differential fuzz** | random programs through every host, comparing values, codes and positions; about a third are invalid on purpose, because agreeing on *where* a rule failed is part of the promise |
| **Decimal oracle** | every decimal core against Python's `decimal`: cores written from one spec by one hand would agree with each other and still be wrong |
| **Host API parity** | the same probes through each host's own binding — kinds, predicates, errors, `dependencies()`, host functions — diffed; every other layer goes through `compile().run()` and could not see an API drift |
| **End to end** | one rule set through each host's API: identical results, identical `dependencies()` |
| **Worked examples** | every program under `examples/`, in every language, printing byte-identical output, which must also equal the transcript the documentation quotes |
| **Database examples** | the SQL examples in every host against real PostgreSQL, MariaDB and SQLite (`tools/check-usage.sh`), each also checking the database's rows against an in-memory run |
| **Documentation** | every `expr => result` example in the documentation executed by every host; every code block quoted from an example byte-identical to that example; every link and anchor resolving |
| **Generated artifacts, manifests, versions** | nothing generated is stale, every host accepts exactly the arities the manifest declares, every package manifest carries the same version |
| **SQL translation** | `sql/cases/*.sqlt` through every host, asserting the *exact* SQL string; the dialect map rebuilt through each host's public registration API and diffed against itself |
| **SQL against a database** | the same expressions evaluated by SEL and by real MariaDB, MySQL, PostgreSQL and SQLite — the only layer that asks whether the SQL *means* what SEL means |
| **SQL mutations** | the SQL layer broken about a hundred and sixty ways on purpose; every break must be caught by some check |
| **Relational models** | `LINK` and `FILTER`-after-`LINK` against executable models of the specification's rules, over generated data |

Two more run separately because they take minutes rather than seconds:
`tools/stress.sh` (deep structures, the shapes a fuzzer never emits) and
`cd cpp && make asan` (the suite and the SQL layer under the address, leak and
undefined-behaviour sanitizers).

The fuzzer is the layer that earns its keep. It found the `\d` Unicode
divergence, C++ evaluating operands right to left, and SBCL's Arabic-Indic
digit — each time a disagreement no one had thought to write a test for.

## What a new implementation has to pass

A sixth host — Rust, Go, Java — is finished when it passes the same gate.
`tools/impls.sh` is where it registers, and [the harness](../tools/README.md)
describes the entry points it provides: a conformance runner, a batch runner for
the fuzzer and the documentation, an API probe, and the worked examples in its
own language. The contributor's order of work is in
[Contributing](contributing.md#adding-an-implementation).
