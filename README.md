# SEL — Simple Expression Language

**Write a business rule once. Run it everywhere, and get the same answer.**

Most applications check the same thing twice: once in the browser so the user
gets a quick "that postcode looks wrong", and once on the server because the
browser cannot be trusted. Two checks, two languages, two authors, two
interpretations of what "empty" means. They drift, and the bug surfaces for the
one customer whose order sits exactly on a rounding boundary.

SEL is a tiny language for writing that rule **once**:

```sel
TOTAL = SUM(ITEMS, _["QTY"] * _["PRICE"]);

COND(TRIM(CUSTOMER) $== "",                 "customer is required",
     NOT RMATCH('^\d{2}-\d{3}$', POSTCODE), "postcode {POSTCODE} is not 12-345",
     TOTAL > CREDIT_LIMIT,                  "total {TOTAL} exceeds {CREDIT_LIMIT}",
                                            "ok")
```

That file is the rule. It runs unchanged on **Python**, **PHP**, **JavaScript**,
**C++23** and **Common Lisp**, and all five are held to the same written
specification by a test suite that runs every one of them and compares the
results byte for byte — including *where* a rule failed, not just whether it did.

It is deliberately small. There is no floating point (so money stays exact), no
truthiness (so an empty string is never accidentally "false"), no loops, and no
way to define your own functions. Anything the host languages cannot be made to
agree on is left out rather than guessed at.

## Start here

| If you want to… | Read |
|---|---|
| **write rules** in SEL | [Language reference](docs/LANGUAGE.md) — the friendly tour, with runnable examples |
| **call SEL** from your app | [Install](#install) and [Quick start](#quick-start), just below |
| see it in a **real application** | [`examples/`](examples/) — a complete order-validation rule and the host code around it |
| **add a function** or an operator | [Extending SEL](docs/EXTENDING.md) |
| know what the language **guarantees** | [Normative spec](spec/SPEC.md) · [Grammar](spec/grammar.md) · [Error codes](spec/errors.md) |
| **port SEL** to another language | [Conformance suite](conformance/README.md) · [Test harness](tools/README.md) |

New here? [Quick start](#quick-start) is a CLI you can paste into a terminal, and
[the language in one screen](#the-language-in-one-screen) is the whole thing at a
glance.

---

## Contents

- [Why](#why)
- [Install](#install)
- [Quick start](#quick-start)
- [Calling it from your host](#calling-it-from-your-host)
- [One rule, one answer](#one-rule-one-answer-a-worked-example)
- [Integration patterns](#integration-patterns)
- [The language in one screen](#the-language-in-one-screen)
- [What makes the hosts agree](#what-makes-the-hosts-agree)
- [Layout](#layout)
- [Checking it](#checking-it)
- [Licence](#licence)

---

## Why

Validation written twice drifts. The backend and the frontend disagree about
rounding, about what `\d` matches, about whether an empty string is falsy — and
the bug only shows up for the one customer whose postcode has an unusual
character in it.

The usual fixes do not really fix it. A shared JSON schema handles shapes but not
"the total must not exceed the credit limit". A rules engine drags in a runtime
you now have to deploy in every one of them. Generating code from a common
source means maintaining a generator per target.

SEL is one rule, one artifact, executed by five interpreters held to a shared
conformance suite and a differential fuzzer. Where the host languages cannot be
made to agree, SEL refuses the feature rather than picking a winner.

It has **no statements**. A program is one expression, and what looks like
control flow is a function call — functions receive the caller's syntax tree
rather than values, and decide for themselves what to evaluate:

```sel
IF(1 > 0, "safe", 1 / 0)  => safe
```

The division never happens. That single idea, borrowed from
[Aster](https://help.int4.com/int4-aster-documentation/), is what lets `IF`,
`COND` and the aggregates be ordinary table entries instead of syntax.

## Install

From a package manager — the package is `sel-lang` on all of them:

```
pip install sel-lang
npm install sel-lang
composer require nathanjel/sel-lang
vcpkg install sel-lang            # or: conan install --requires sel-lang/0.4.1
(ql:quickload :sel-lang)          # Quicklisp / Ultralisp
```

Or copy the directory for your host into your project, which needs no package
manager at all and is still the primary story:

```python
from sel import compile, Value            # Python 3.10+, no dependencies
```
```php
require 'path/to/php/src/bootstrap.php';    // PHP 8.1+, no extensions required
```
```js
import { compile, Value } from './path/to/js/src/sel.mjs';   // any ESM runtime
```
```cpp
#include "sel.hpp"                          // compile sel.cpp alongside; C++23
```
```lisp
(ql:quickload :sel-lang)                    ; SBCL; depends on cl-ppcre
```

Python, PHP and JS need nothing at all — no pip, no Composer, no npm, no build
step; copying `python/sel/`, `php/src/` or `js/src/` into a project works. C++ is
a three-file drop-in — `cpp/sel.hpp`, `cpp/sel_ast.hpp` and `cpp/sel.cpp` —
plus the vendored and pinned `cpp/third_party/srell/` (BSD-2), with the SEL→SQL
layer a strict addition of `cpp/sel_sql*.{hpp,cpp}` beside them; it also installs as a CMake package, so
`find_package(sel-lang)` and `sel-lang::sel-lang` work. Common Lisp is an
ordinary ASDF system whose one dependency is cl-ppcre (BSD-2).

Publishing details, and why SRELL is vendored rather than resolved, are in
[PACKAGING.md](PACKAGING.md).

PHP needs no `mbstring`, no `bcmath`, no `gmp`; C++ never touches `std::regex`
or `<locale>`; and Python never touches `decimal` — the UTF-8 codec and the
decimal arithmetic are hand-written in all five precisely so the hosts cannot
drift apart. In Python's case there is a second reason: `tools/decimal-oracle.py`
generates the decimal test cases *from* the `decimal` module, and a host built on
it would be marking its own homework.

## Quick start

There is a CLI for poking at rules:

```
$ node js/bin/sel.mjs -e '2.50 + 2.50'
5.00
$ php php/bin/sel -e 'JOIN(MAP((1,2,3), _ * _), ",")'
1,4,9
$ php php/bin/sel --deps -e 'T = QTY * PRICE; T > LIMIT'
LIMIT
PRICE
QTY
$ node js/bin/sel.mjs          # REPL, keeps its context between lines
sel> A = (1, 2, 3)
-{"1"=t"1", "2"=t"2", "3"=t"3"}
sel> SUM(A, _)
6
```

## Calling it from your host

Five implementations, one answer. Each host below has a complete, runnable
walkthrough in **[examples/plain/](examples/plain/)** — evaluating, compiling
once and running per row, building a context, reading results back, errors, and
`dependencies()`.

All five print **byte-identical** output, and `tools/check-examples.sh` diffs
them against each other. The claim that the hosts agree is therefore checked on
the code you are being invited to copy, not asserted in prose beside it.

| Host | Run it |
|---|---|
| [Python](examples/plain/python.py) | `PYTHONPATH=python python3 examples/plain/python.py` |
| [PHP](examples/plain/php.php) | `php examples/plain/php.php` |
| [JS](examples/plain/js.mjs) | `node examples/plain/js.mjs` |
| [C++](examples/plain/cpp.cpp) | `cd cpp && make && ./build/example-plain` |
| [Common Lisp](examples/plain/lisp.lisp) | `see the header of the file` |

Three things every host does the same way, and the examples show each:

- **Money is text.** `"19.99"`, never a float — a double has already lost the
  exactness SEL exists to preserve, and the dynamic hosts refuse one rather than
  pretend otherwise.
- **Every failure carries a stable code and the position of the node that
  actually failed.** Assert on the code; the message is human text and may change.
- **`dependencies()` is static.** It says which fields a rule reads without
  running it, which is how a frontend knows what to re-validate.

The same fragment — compile once, then run it per row — in each host:

### Python

<!-- from: examples/plain/python.py -->
```python
rule = compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")')
for row in [{'QTY': '3', 'PRICE': '19.99'}, {'QTY': '1', 'PRICE': '5.00'}]:
    ctx = Value.from_native({**row, 'LIMIT': '50.00'})
    print(f"   QTY={row['QTY']} PRICE={row['PRICE']} =>", rule.run(ctx).as_text())
```

### PHP

<!-- from: examples/plain/php.php -->
```php
$rule = Sel::compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")');
foreach ([['QTY' => '3', 'PRICE' => '19.99'], ['QTY' => '1', 'PRICE' => '5.00']] as $row) {
    $ctx = Value::fromNative($row + ['LIMIT' => '50.00']);
    printf("   QTY=%s PRICE=%s => %s\n", $row['QTY'], $row['PRICE'], $rule->run($ctx)->asText());
}
```

### JS

<!-- from: examples/plain/js.mjs -->
```js
const rule = compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")');
for (const row of [{ QTY: '3', PRICE: '19.99' }, { QTY: '1', PRICE: '5.00' }]) {
  const ctx = Value.fromNative({ ...row, LIMIT: '50.00' });
  console.log(`   QTY=${row.QTY} PRICE=${row.PRICE} =>`, rule.run(ctx).asText());
}
```

### C++

<!-- from: examples/plain/cpp.cpp -->
```cpp
  const sel::Program rule = sel::compile("IF(QTY * PRICE > LIMIT, \"over budget\", \"ok\")");
  for (const auto& row : std::vector<std::pair<std::string, std::string>>{
           {"3", "19.99"}, {"1", "5.00"}}) {
    sel::Value ctx = sel::Value::none();
    ctx.set("QTY", sel::Value::text(row.first));
    ctx.set("PRICE", sel::Value::text(row.second));
    ctx.set("LIMIT", sel::Value::text("50.00"));
    std::cout << "   QTY=" << row.first << " PRICE=" << row.second
              << " => " << rule.run(ctx).as_text() << "\n";
  }
```

### Common Lisp

<!-- from: examples/plain/lisp.lisp -->
```lisp
  (let ((rule (sel:compile-source "IF(QTY * PRICE > LIMIT, \"over budget\", \"ok\")")))
    (loop for (qty price) in '(("3" "19.99") ("1" "5.00"))
          do (format t "   QTY=~a PRICE=~a => ~a~%" qty price
                     (sel:as-text
                      (sel:run rule (ctx-of `(("QTY" . ,qty) ("PRICE" . ,price)
                                              ("LIMIT" . "50.00"))))))))
```

## One rule, one answer: a worked example

Here is a small program that looks harmless and is worth understanding, because
it is the kind of thing where languages usually stop agreeing with each other.

```sel
A[1] = (A = 2); A       => t"2"{"1"=t"2"}
```

Read it left to right. `A[1] = …` says *store something under key `1` of `A`*.
But the thing being stored is `(A = 2)`, and that expression **replaces `A`
entirely** with the plain number 2 before the store ever happens. So by the time
SEL comes to store, the `A` the sentence started talking about no longer exists.

SEL's answer is that the assignment lands **where it says it lands**: at the path
`A[1]`, in whatever `A` is by then. You get the number `2` carrying a child `1`
that is also `2`.

The alternative — the one most languages fall into — is to grab hold of the old
`A` when the sentence starts and write into that. The old `A` has since been
thrown away, so the write goes into an object nothing can reach, and the whole
assignment silently evaporates: you would get `2` with no child, and no
indication that half your statement did nothing.

**A write that nothing can ever read is a worse answer than a visible one.** SEL
would rather show you the result than quietly drop it. That is the same
principle as refusing truthiness and refusing floating point: prefer the loud,
inspectable outcome to the convenient one.

Nobody sensible writes `A[1] = (A = 2)` on purpose. It matters because rules grow
in layers — an index computed by a helper, a value produced by another rule — and
the day two of those layers touch the same variable, all five implementations
still answer identically instead of four agreeing and one being subtly special.

Two smaller consequences of the same rule, which are much more likely to come up:

```sel
A[COUNT(A)] = 1; A          => -{"0"=t"1"}
A = 1; A += (A = 5); A      => 6
```

The first works because `A` is created *before* the index expression runs, so
`COUNT(A)` sees an empty `A` and answers `0`. The second reads the target's old
value (`1`) for the arithmetic, but still stores at the path afterwards — so you
get `1 + 5`.

Every line above is executed by all five implementations on every commit; that
is what the `=>` marks mean throughout this document.

## Integration patterns

Runnable versions of everything below are in
[`examples/integration-php.php`](examples/integration-php.php) and
[`examples/integration-js.mjs`](examples/integration-js.mjs) — both print
identical output.

### Compile once, run per request

Parsing is cheap but not free, and a syntax error is a deployment problem rather
than a user problem. Build the table at boot so a broken rule fails there:

```php
final class RuleSet
{
    private array $rules = [];

    public function __construct(array $sources)      // field => SEL source
    {
        foreach ($sources as $field => $source) {
            $this->rules[$field] = Sel::compile($source);
        }
    }
```

### Give each rule its own context

Rules should not see each other's intermediate variables. Rebuilding the context
per rule is cheap and keeps them independent:

```php
    public function validate(array $payload): array
    {
        $messages = [];
        foreach ($this->rules as $field => $program) {
            $context = Value::fromNative($payload);
            try {
                $result = $program->run($context)->asText();
            } catch (SelError $e) {
                $result = self::present($field, $e);
            }
            if ($result !== '') {
                $messages[$field] = $result;
            }
        }
        return $messages;
    }
```

### Separate "tell the user" from "the rule is broken"

`E_ABORT` is the rule author deliberately raising a message. Every other code
means the rule itself is wrong, and the user should never see it:

```php
    private static function present(string $field, SelError $e): string
    {
        if ($e->code === 'E_ABORT') {
            return $e->getMessage();
        }
        error_log("SEL rule for {$field} failed: {$e}");
        return 'could not be validated';
    }
```

### Re-validate only what changed

`dependencies()` reports every input a rule reads, statically, without running
it. Invert that into a watch map and an input listener knows the minimum set of
rules to re-run:

```js
  watchMap() {
    const map = {};
    for (const [field, program] of this.rules) {
      for (const input of program.dependencies()) {
        (map[input] ||= []).push(field);
      }
    }
    return map;
  }
```

```
watch map (field => rules to re-run):
  CREDIT_LIMIT  order
  EMAIL         email
  ITEMS         order
  POSTCODE      postcode
```

Change `EMAIL`, re-run only the `email` rule. This works because SEL has no
dynamic symbol operator — dropping that feature is exactly what buys it.

### Shipping rules to the browser

Send the **source text**, not a compiled form. It is one artifact to version, it
keeps both interpreters complete and symmetric, and it is what the conformance
suite tests. Serve the same strings the backend compiled, and let the frontend
compile them at load.

## The language in one screen

Full detail in the [language reference](docs/LANGUAGE.md).

```sel
2.50 + 2.50                       => 5.00
0.10 + 0.20 == 0.30               => TRUE
1 / 3                             => 0.3333333333
"5.00" == "5"                     => TRUE
"5.00" $== "5"                    => FALSE
A = 3; "value {A} here"           => value 3 here
JOIN(("a", "b", "c"), "-")        => a-b-c
A = (1, 2); A = (A, 3); COUNT(A)  => 3
A[1] = 3; A == 3                  => TRUE
ALL((1, 2, 3), _ > 0)             => TRUE
JOIN(MAP((1, 2), _ * 2), ",")     => 2,4
SUM((1.5, 2.5), _)                => 4.0
S = 85; COND(S >= 90, "A", S >= 80, "B", "F")  => B
LEN("Zażółć") & "/" & BLEN("Zażółć")           => 6/10
RMATCH('^\d{2}-\d{3}$', "31-874")              => TRUE
IF(1, "a", "b")                   => !E_NOT_BOOL
" 2" + 1                          => !E_NOT_NUM
1 < 2 < 3                         => !E_SYNTAX
```

No loops, no user-defined functions, no lexical scoping, no dynamic symbols, no
XML, no JSON, no compression, no floating point, and no truthiness. Iteration is
done by aggregates that evaluate a body per element — the natural payoff of the
calling convention.

## What makes the hosts agree

Cross-host agreement is the whole product, and three things threaten it. Each is
handled structurally rather than hopefully. The rule throughout: **never use the
host's own idea of anything the language defines.**

**Numbers.** There is no floating point. Arithmetic is exact decimal, written by
hand in all five, because no host has a usable exact type that carries scale —
PHP has no bigint and BCMath is optional, JS has doubles, C++ has doubles, and a
Lisp ratio cannot tell `2.50` from `2.5`. Python's `decimal` *would* do the job,
and is still not used: it is the oracle the other cores are checked against, so a
host built on it would be marking its own homework. Scale is part of the value,
so `2.50 + 2.50` is `5.00` and `0.10 + 0.20 > 0.30` is false everywhere.

**Text.** UTF-8 is encoded and decoded by hand, so every length and offset counts
code points rather than PHP's bytes, JS's UTF-16 units or C++'s `char`s. Text
comparison is specified as UTF-8 byte order, because JS's native comparison is
UTF-16 order and Lisp's is code-point order, and both disagree with it above
U+FFFF. `UPPER`/`LOWER` are ASCII-only on purpose — `strtoupper`,
`toUpperCase`, `std::toupper`, `string-upcase` and Python's `str.upper` cannot be
reconciled without shipping a case table, and the last of those can even change a
string's length (`"ß".upper()` is `"SS"`); SEL would rather be visibly limited
than quietly wrong. Even "digit" is defined here: SBCL's `DIGIT-CHAR-P` accepts
U+0661 ARABIC-INDIC DIGIT ONE and Python's `int()` accepts both that and
`"1_2"`, so every implementation tests for `0`–`9` explicitly.

**Identity.** Evaluating an expression yields a value, not a snapshot of one, so
a mutation made by a later sub-expression is visible through a reference taken
earlier — `A[A["k"] = "k"]` finds the key its own index expression just created.
Assignment is the only thing that copies. Every host aliases by default and
deep-copies at exactly five places, which is a rule rather than an accident of
each language's object model: the C++ `Value` was a deep-copying type until
0.3.0 and disagreed with the other four in six different ways, one of which
returned a wrong number rather than an error. It is a handle now, with an
explicit `clone()`, like the other four (§3.4).

**Regex.** Patterns are checked against a PCRE ∩ ECMAScript subset at compile
time, and `\d`, `\w`, `\s` are rewritten into explicit ASCII classes rather than
passed through — PHP's `u` modifier enables PCRE2's UCP and JS's does not, so
otherwise `\d` matches Arabic-Indic digits on the backend only. `\b` is refused
outright, because a word boundary depends on the engine's idea of a word
character and no rewrite fixes that. The engine underneath differs by host and
each one is bent to the same shape: JS uses `RegExp` with `us`, PHP `preg` with
`usD`, C++ the vendored SRELL (an ECMAScript engine, so it agrees with JS by
construction), and Lisp cl-ppcre and Python `re` with `^`/`$` lowered to `\A` and
`\z`/`\Z`, because Perl and PCRE let `$` match before a trailing newline and SEL
does not. Case-insensitive matching needed a correction in both directions:
cl-ppcre folds neither of the two non-ASCII code points that simple-fold to an
ASCII letter, and Python's `re` folds those two *and* two more (U+0130 and U+0131
both fold to `i` there and nowhere else), so both hosts pre-fold the subject to
land on the same set.

## Layout

```
spec/          SPEC.md, grammar.md, errors.md — normative
conformance/   *.selt — normative; every implementation must pass
docs/          LANGUAGE.md (rule authors), EXTENDING.md (contributors)
               SQL-TRANSLATION.md + history/SQL-TESTING.md (the SEL->SQL layer)
sql/           MAP.md, errors.md, dialects/*.json, cases/*.sqlt, mutations.json
python/        sel/ (package sel), bin/, tests/
php/           src/ (namespace Sel\), bin/sel, bin/conformance
js/            src/ (ESM), bin/sel.mjs, bin/conformance.mjs
cpp/           sel.hpp + sel_ast.hpp + sel.cpp (the drop-in), sel_sql*.* (SEL→SQL), third_party/srell/
lisp/          sel.asd, src/ (package SEL), bin/, tests/
examples/      host API, integration patterns, a real rule set
tools/         fuzzer, decimal oracle, doc checker, check scripts
```

When implementations disagree, `spec/` and `conformance/` decide which is wrong —
no implementation is the reference. A future Rust or Go port is finished when it
passes the same suite; `tools/impls.sh` is where it registers itself, and
`tools/README.md` describes the five entry points it has to provide.

## Checking it

```
tools/check.sh
```

Eleven layers, each catching what the others miss:

- **Conformance** — the normative suite, run by every implementation.
- **Unit tests** — for the layers underneath the suite, where a bug otherwise
  shows up as a hundred confusing conformance failures instead of one message.
- **Host API parity** — the same probes through each host's own binding, diffed.
  Every other layer drives the language through `compile().run()`, so without
  this the five APIs could drift apart while staying green — which is exactly
  how the kind constants came to be reachable in PHP and unreachable in JS.
- **Documentation** — every `=>` example in these docs is executed, by every
  implementation. Documentation that cannot be checked is documentation that
  drifts.
- **Decimal oracle** — every decimal core against Python's `decimal`. The SEL
  cores came from one spec and one hand; if the algorithm were wrong they would
  agree with each other and still be wrong.
- **End to end** — one rule set through each host's own API, asserting identical
  results and identical `dependencies()`.
- **Differential fuzz** — random programs run through every implementation,
  comparing values, error codes and error positions. Roughly a third of the
  corpus is invalid on purpose: agreement on *where* a rule failed is as much
  part of the promise as agreement on what it returned.
- **Manifest versions** — every package manifest declares the same version, so a
  release cannot go out half-numbered.
- **SEL→SQL map replay** — the shipped dialect map rebuilt through nothing but
  each host's public registration API, and diffed against itself: 217 calls, 564
  lookups, no differences. It asserts that anything the map contains an
  application could have registered, which is what lets a host ship its map as
  generated *code* rather than as a data file to deploy and parse.
- **SEL→SQL translation** — the dialect map and the case table are generated, and
  both are checked for staleness; then `sql/cases/*.sqlt` runs through every host
  that has a translator, asserting the *exact* emitted string. Three hosts
  agreeing on 377 exact strings is a measurement rather than an intention. The
  design document's own worked examples are checked against the cases that
  produced them, and `tools/mutate-sql.py` damages the layer ninety ways to prove
  the cases can fail.
- **SQL against a database** — the same expressions evaluated by SEL and by a
  real MariaDB, MySQL, PostgreSQL or SQLite. Every other layer asks "is this the
  string we meant to emit?"; only this one asks "does that string *mean* what SEL
  means?". It skips itself when no DSN is set, which is why it is last: it is the
  only layer that cannot run everywhere.

Two more exist and are not in `tools/check.sh`, deliberately, because they take
minutes rather than seconds: `tools/stress.sh` (deep structures, the shapes a
fuzzer never emits) and `cd cpp && make asan` (the suite under the leak and
undefined-behaviour checkers).

The fuzzer is the one that earns its keep. It caught the `\d` UCP divergence; it
caught C++ evaluating `TRUE $== FALSE`'s operands right-to-left, because the
order of function arguments is unspecified there and SEL's is not; and it caught
SBCL's `DIGIT-CHAR-P` accepting U+0661 ARABIC-INDIC DIGIT ONE, which made `"١"`
a number in exactly one host. When it finds a disagreement, add the minimal case
to `conformance/` **before** fixing any host — the case is the durable part.

Contributing, and the full list of invariance traps to watch for, is in
[docs/EXTENDING.md](docs/EXTENDING.md).

## Licence

[MIT](LICENSE) — use it for anything, including commercially, as long as the
copyright notice travels with it.

Two third-party components keep their own (also permissive) licences: **SRELL**,
which is vendored into the C++ implementation, and **cl-ppcre**, which the Common
Lisp system depends on. Both are BSD 2-Clause, and both are listed in
[LICENSE](LICENSE). The Python, PHP and JS implementations have no dependencies
at all, so shipping them is just the MIT notice.
