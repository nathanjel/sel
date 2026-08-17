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

That file is the rule. It runs unchanged on **PHP**, **JavaScript**, **C++23**
and **Common Lisp**, and all four are held to the same written specification by
a test suite that runs every one of them and compares the results byte for byte
— including *where* a rule failed, not just whether it did.

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
- [Calling it from PHP](#calling-it-from-php)
- [Calling it from JS](#calling-it-from-js)
- [Calling it from C++](#calling-it-from-c)
- [Calling it from Common Lisp](#calling-it-from-common-lisp)
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
you now have to deploy in four places. Generating code from a common source means
maintaining four generators.

SEL is one rule, one artifact, executed by four interpreters held to a shared
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
npm install sel-lang
composer require nathanjel/sel-lang
vcpkg install sel-lang            # or: conan install --requires sel-lang/0.1.4
(ql:quickload :sel-lang)          # Quicklisp / Ultralisp
```

Or copy the directory for your host into your project, which needs no package
manager at all and is still the primary story:

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

PHP and JS need nothing at all — no Composer, no npm, no build step. C++ is a
two-file drop-in, `cpp/sel.hpp` and `cpp/sel.cpp`, plus the vendored and pinned
`cpp/third_party/srell/` (BSD-2); it also installs as a CMake package, so
`find_package(sel-lang)` and `sel-lang::sel-lang` work. Common Lisp is an
ordinary ASDF system whose one dependency is cl-ppcre (BSD-2).

Publishing details, and why SRELL is vendored rather than resolved, are in
[PACKAGING.md](PACKAGING.md).

PHP needs no `mbstring`, no `bcmath`, no `gmp`, and C++ never touches
`std::regex` or `<locale>` — the UTF-8 codec and the decimal arithmetic are
hand-written in all four precisely so the hosts cannot drift apart.

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

## Calling it from PHP

```php
require 'php/src/bootstrap.php';
use Sel\Sel; use Sel\Value; use Sel\SelError;

// Compile once, at boot. A Program is immutable and reusable.
$rule = Sel::compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")');

// Run per request.
$ctx = Value::fromNative(['QTY' => '3', 'PRICE' => '19.99', 'LIMIT' => '50.00']);
echo $rule->run($ctx)->asText();          // "over budget"
```

Nested data. A packed array becomes a 1-based list, so `ITEMS[1]` is the first
line — the same as in JS:

```php
$order = Value::fromNative([
    'CUSTOMER' => 'Zażółć',
    'ITEMS' => [
        ['SKU' => 'AB-1234', 'QTY' => '3', 'PRICE' => '19.99'],
        ['SKU' => 'CD-5678', 'QTY' => '1', 'PRICE' => '5.01'],
    ],
]);
Sel::compile('ITEMS[1]["SKU"]')->run($order)->asText();                    // "AB-1234"
Sel::compile('SUM(ITEMS, _["QTY"] * _["PRICE"])')->run($order)->asText();  // "64.98"
```

Reading results, and reading back what a rule assigned — `run()` mutates the
context you hand it:

```php
$v = Sel::evaluate('SPLIT("a,b,c", ",")');
$v->size();             // 3
$v->keys();             // ['1','2','3']
$v->get('2')->asText(); // "b"
$v->asText();           // "a"   — scalar context takes the first
$v->toNative();         // ['1'=>'a','2'=>'b','3'=>'c']

$ctx = Value::fromNative(['QTY' => '3', 'PRICE' => '19.99']);
Sel::compile('NET = QTY*PRICE; VAT = ROUND(NET*0.23,2); GROSS = NET+VAT')->run($ctx);
$ctx->get('GROSS')->asText();   // "73.76"
```

Errors carry a stable code and the position of the node that actually failed:

```php
try { Sel::evaluate('3 + "A"'); }
catch (SelError $e) { echo "{$e->code} at {$e->line}:{$e->col}"; }
// E_NOT_NUM at 1:5   — points at the "A", not at the +
```

Match on `->code`, never on the message. Codes are listed in
[`spec/errors.md`](spec/errors.md).

## Calling it from JS

Same shapes, same results:

```js
import { compile, evaluate, Value, SelError } from './js/src/sel.mjs';

const rule = compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")');
const ctx  = Value.fromNative({ QTY: '3', PRICE: '19.99', LIMIT: '50.00' });
rule.run(ctx).asText();                            // "over budget"

evaluate('0.10 + 0.20').asText();                  // "0.30"  (JS says 0.30000000000000004)

const v = evaluate('SPLIT("a,b,c", ",")');
v.size();                // 3        — a method, as in every other host
v.get('2').asText();     // "b"
v.toNative();            // {"1":"a","2":"b","3":"c"}

try { evaluate('IF(1, "a", "b")'); }
catch (e) { if (e instanceof SelError) console.log(e.code); }   // E_NOT_BOOL
```

The APIs are deliberately parallel, and `tools/check-api.sh` holds them to it —
48 probes run through each host's own binding and diffed. `size()` is a method
in all four, not a getter in one of them, and the only remaining difference is
the one a language forces: how each spells a kind.

**Branch on kind with the predicates**, which read the same everywhere:

```js
if (v.isText()) { ... }              // JS
```
```php
if ($v->isText()) { ... }            // PHP
```
```cpp
if (v.is_text()) { ... }             // C++
```
```lisp
(when (sel:value-text-p v) ...)      ; Lisp
```

The kind *values* cannot be uniform — they are a string in JS, a class constant
in PHP, an enum in C++ and a keyword in Lisp — so the constants are exported in
each host (`Value.BOOL`, `Value::BOOL`, `sel::Kind::Bool`, `:bool`) for code
that would rather switch than branch, but the predicates are the portable form.

**Pass money as strings, not native numbers.** A JS `number` or a PHP `float` has
already lost the exactness SEL exists to preserve; `Value::fromNative` rejects
PHP floats outright rather than pretend otherwise.

## Calling it from C++

Two files to copy — `cpp/sel.hpp` and `cpp/sel.cpp` — plus the vendored
`cpp/third_party/srell/`. Compile `sel.cpp` as part of your target; there is no
library to build and nothing to fetch.

```cpp
#include "sel.hpp"

sel::Program rule = sel::compile(R"(IF(QTY * PRICE > LIMIT, "over budget", "ok"))");

sel::Value ctx = sel::Value::none();
ctx.set("QTY", sel::Value::num("3"));
ctx.set("PRICE", sel::Value::num("19.99"));
ctx.set("LIMIT", sel::Value::num("50.00"));

rule.run(ctx).as_text();                       // "over budget"
sel::evaluate("0.10 + 0.20").as_text();        // "0.30"  (a double says 0.30000000000000004)

for (const std::string& d : rule.dependencies()) { /* LIMIT, PRICE, QTY */ }

try { sel::evaluate(R"(IF(1, "a", "b"))"); }
catch (const sel::SelError& e) { e.code(); }   // E_NOT_BOOL
```

There is no `from_native(double)` on purpose: a `double` has already lost the
exactness SEL exists to preserve, so money is passed as a string and the compiler
says so rather than the arithmetic quietly disagreeing with the backend.

`make` builds the CLI and the harness; `make test` runs the unit tests and the
conformance suite. A `CMakeLists.txt` is there for projects that prefer it.

## Calling it from Common Lisp

An ordinary ASDF system. Its one dependency is cl-ppcre.

```lisp
(ql:quickload :sel-lang)

(defparameter *rule* (sel:compile-source "IF(QTY * PRICE > LIMIT, \"over budget\", \"ok\")"))

(let ((ctx (sel:from-native '(("QTY" . "3") ("PRICE" . "19.99") ("LIMIT" . "50.00")))))
  (sel:as-text (sel:run *rule* ctx)))          ; => "over budget"

(sel:as-text (sel:evaluate "0.10 + 0.20"))     ; => "0.30"
(sel:dependencies *rule*)                      ; => ("LIMIT" "PRICE" "QTY")

(handler-case (sel:evaluate "IF(1, \"a\", \"b\")")
  (sel:sel-error (e) (sel:sel-error-code e)))  ; => "E_NOT_BOOL"
```

`from-native` refuses floats and ratios: a ratio cannot carry SEL's scale — 2.50
and 5/2 are the same ratio and different SEL values — and a float has no exact
decimal form at all. Pass decimal strings.

Run the tests with `(asdf:test-system :sel-lang)`, or `lisp/bin/test`.

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
the day two of those layers touch the same variable, all four implementations
still answer identically instead of three agreeing and one being subtly special.

Two smaller consequences of the same rule, which are much more likely to come up:

```sel
A[COUNT(A)] = 1; A          => -{"0"=t"1"}
A = 1; A += (A = 5); A      => 6
```

The first works because `A` is created *before* the index expression runs, so
`COUNT(A)` sees an empty `A` and answers `0`. The second reads the target's old
value (`1`) for the arithmetic, but still stores at the path afterwards — so you
get `1 + 5`.

Every line above is executed by all four implementations on every commit; that
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

**Numbers.** There is no floating point. Arithmetic is exact decimal on digit
strings, hand-written in all four, because none has a usable exact type that
carries scale — PHP has no bigint and BCMath is optional, JS has doubles, C++ has
doubles, and a Lisp ratio cannot tell `2.50` from `2.5`. Scale is part of the
value, so `2.50 + 2.50` is `5.00` and `0.10 + 0.20 > 0.30` is false everywhere.

**Text.** UTF-8 is encoded and decoded by hand, so every length and offset counts
code points rather than PHP's bytes, JS's UTF-16 units or C++'s `char`s. Text
comparison is specified as UTF-8 byte order, because JS's native comparison is
UTF-16 order and Lisp's is code-point order, and both disagree with it above
U+FFFF. `UPPER`/`LOWER` are ASCII-only on purpose — `strtoupper`,
`toUpperCase`, `std::toupper` and `string-upcase` cannot be reconciled without
shipping a case table, and SEL would rather be visibly limited than quietly
wrong. Even "digit" is defined here: SBCL's `DIGIT-CHAR-P` accepts U+0661
ARABIC-INDIC DIGIT ONE, so every implementation tests for `0`–`9` explicitly.

**Regex.** Patterns are checked against a PCRE ∩ ECMAScript subset at compile
time, and `\d`, `\w`, `\s` are rewritten into explicit ASCII classes rather than
passed through — PHP's `u` modifier enables PCRE2's UCP and JS's does not, so
otherwise `\d` matches Arabic-Indic digits on the backend only. `\b` is refused
outright, because a word boundary depends on the engine's idea of a word
character and no rewrite fixes that. The engine underneath differs by host and
each one is bent to the same shape: JS uses `RegExp` with `us`, PHP `preg` with
`usD`, C++ the vendored SRELL (an ECMAScript engine, so it agrees with JS by
construction), and Lisp cl-ppcre with `^`/`$` lowered to `\A`/`\z`, because Perl
lets `$` match before a trailing newline and SEL does not.

## Layout

```
spec/          SPEC.md, grammar.md, errors.md — normative
conformance/   *.selt — normative; every implementation must pass
docs/          LANGUAGE.md (rule authors), EXTENDING.md (contributors)
php/           src/ (namespace Sel\), bin/sel, bin/conformance
js/            src/ (ESM), bin/sel.mjs, bin/conformance.mjs
cpp/           sel.hpp + sel.cpp (the drop-in), third_party/srell/, bin/, tests/
lisp/          sel.asd, src/ (package SEL), bin/, tests/
examples/      host API, integration patterns, a real rule set
tools/         fuzzer, decimal oracle, doc checker, check scripts
```

When implementations disagree, `spec/` and `conformance/` decide which is wrong —
no implementation is the reference. A future Python or Rust port is finished when
it passes the same suite; `tools/impls.sh` is where it registers itself, and
`tools/README.md` describes the four entry points it has to provide.

## Checking it

```
tools/check.sh
```

Seven layers, each catching what the others miss:

- **Conformance** — the normative suite, run by every implementation.
- **Unit tests** — for the layers underneath the suite, where a bug otherwise
  shows up as a hundred confusing conformance failures instead of one message.
- **Host API parity** — the same probes through each host's own binding, diffed.
  Every other layer drives the language through `compile().run()`, so without
  this the four APIs could drift apart while staying green — which is exactly
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
[LICENSE](LICENSE). The PHP and JS implementations have no dependencies at all,
so shipping them is just the MIT notice.
