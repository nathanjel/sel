# Using SEL

SEL is embedded: your application hands a rule and some data to the host
library, and gets a value back. This page is that host API in all five
languages. The pages after it are complete programs, each written five times:

| Page | What it shows |
|---|---|
| [A REPL in thirty lines](repl.md) | the whole API in the smallest useful program |
| [Validation](validation.md) | a form's rule set: compiled once, run per submission, errors told apart |
| [Scripting with host functions](scripting.md) | the application registers functions; a script decides what happens |
| [SQL conditions](sql-conditions.md) | a rule as a `WHERE` clause — naive, and with the schema described |
| [SQL pipelines](sql-pipelines.md) | whole queries in SQL, split with memory, or in memory — star, EAV, 3NF, flat and complex data |
| [Your own functions, in SQL](sql-functions.md) | host functions with a SQL spelling: PostgreSQL SQL and PL/pgSQL functions, list arguments, strict mode |

Every snippet on these pages is quoted from a file under
[`examples/`](../../examples/) that the test suite runs in all five languages
and whose output it compares byte for byte — the code cannot drift from what the
page says, and the five tabs cannot drift from each other.

- [Installing](#installing)
- [Evaluate, or compile once and run many times](#evaluate-or-compile-once-and-run-many-times)
- [Building a context](#building-a-context)
- [Variables flow back](#variables-flow-back)
- [Errors](#errors)
- [What does a rule read?](#what-does-a-rule-read)
- [The API side by side](#the-api-side-by-side)

---

## Installing

The package is `sel-lang` everywhere. Python, PHP and JavaScript have no
dependencies, so copying the directory works just as well as a package manager.

<!-- tabs -->
<details open>
<summary>Python</summary>

```sh
pip install sel-lang                 # or copy python/sel/ into your project
```
```python
from sel import compile, evaluate, Value, SelError      # Python 3.10+
from sel.sql import Sql, Binding                        # the SQL layer, when you need it
```

</details>
<details>
<summary>JavaScript</summary>

```sh
npm install sel-lang                 # or copy js/src/ into your project
```
```js
import { compile, evaluate, Value, SelError } from 'sel-lang';  // any ESM runtime
import { Sql, Binding } from 'sel-lang/sql';                     // the SQL layer
```
```html
<!-- In a browser, nothing to install: the standalone bundle from a CDN (no SQL layer) -->
<script type="module">
  import { compile, evaluate } from 'https://cdn.jsdelivr.net/npm/sel-lang@0.9.2/dist/sel.min.mjs';
</script>
```

</details>
<details>
<summary>PHP</summary>

```sh
composer require nathanjel/sel-lang  # or copy php/src/ into your project
```
```php
require 'vendor/autoload.php';       // or: require 'php/src/bootstrap.php';
use Sel\Sel;                         // PHP 8.1+, no extensions required
use Sel\Value;
use Sel\Sql\Sql;                     // the SQL layer: also require php/src/Sql/bootstrap.php
```

</details>
<details>
<summary>C++</summary>

```sh
vcpkg install sel-lang               # or: conan install --requires sel-lang/0.9.2
                                     # or copy cpp/sel.hpp, sel_ast.hpp, sel_limits.hpp,
                                     # sel_math_ops.hpp, sel_builtin_manifest.hpp, sel.cpp,
                                     # sel_optimizer.cpp and third_party/srell/, and compile sel.cpp
```
```cpp
#include "sel.hpp"                   // C++23; find_package(sel-lang) with CMake
#include "sel_sql.hpp"               // the SQL layer: add cpp/sel_sql*.cpp
```

</details>
<details>
<summary>Common Lisp</summary>

```lisp
(ql:quickload :sel-lang)             ; SBCL; depends on cl-ppcre
(ql:quickload :sel-lang/sql)         ; the SQL layer
```

</details>
<!-- /tabs -->

[PACKAGING.md](../../PACKAGING.md) has the details of each registry.

## Evaluate, or compile once and run many times

`evaluate(source, context)` parses and runs in one call. A form or a batch job
compiles each rule once and keeps the program: a `Program` is immutable and
reusable, and compiling is where syntax errors, unknown functions and wrong
argument counts are caught — before any data is involved.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/plain/python.py#compile -->
```python
rule = compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")')
for row in [{'QTY': '3', 'PRICE': '19.99'}, {'QTY': '1', 'PRICE': '5.00'}]:
    ctx = Value.from_native({**row, 'LIMIT': '50.00'})
    print(f"   QTY={row['QTY']} PRICE={row['PRICE']} =>", rule.run(ctx).as_text())
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/plain/js.mjs#compile -->
```js
const rule = compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")');
for (const row of [{ QTY: '3', PRICE: '19.99' }, { QTY: '1', PRICE: '5.00' }]) {
  const ctx = Value.fromNative({ ...row, LIMIT: '50.00' });
  console.log(`   QTY=${row.QTY} PRICE=${row.PRICE} =>`, rule.run(ctx).asText());
}
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/plain/php.php#compile -->
```php
$rule = Sel::compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")');
foreach ([['QTY' => '3', 'PRICE' => '19.99'], ['QTY' => '1', 'PRICE' => '5.00']] as $row) {
    $ctx = Value::fromNative($row + ['LIMIT' => '50.00']);
    printf("   QTY=%s PRICE=%s => %s\n", $row['QTY'], $row['PRICE'], $rule->run($ctx)->asText());
}
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/plain/cpp.cpp#compile -->
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

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/plain/lisp.lisp#compile -->
```lisp
(let ((rule (sel:compile-source "IF(QTY * PRICE > LIMIT, \"over budget\", \"ok\")")))
  (loop for (qty price) in '(("3" "19.99") ("1" "5.00"))
        do (format t "   QTY=~a PRICE=~a => ~a~%" qty price
                   (sel:as-text
                    (sel:run rule (ctx-of `(("QTY" . ,qty) ("PRICE" . ,price)
                                            ("LIMIT" . "50.00"))))))))
```

</details>
<!-- /tabs -->

**Money is text.** Pass `"19.99"`, never a float: a double has already lost the
exactness SEL exists to keep, and the dynamic hosts refuse one rather than
pretend otherwise.

## Building a context

The context is a value whose children are the rule's variables. Each host builds
it from its own maps and lists; C++, which has no native map to convert from,
builds it with `Value::none()` and `set()`.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/plain/python.py#context -->
```python
order = Value.from_native({
    'CUSTOMER': 'Zażółć',
    'ITEMS': [                                    # a list is a 1-based SEL list
        {'SKU': 'AB-1234', 'QTY': '3', 'PRICE': '19.99'},
        {'SKU': 'CD-5678', 'QTY': '1', 'PRICE': '5.01'},
    ],
})
print('   first SKU =>', compile('ITEMS[1]["SKU"]').run(order).as_text())
print('   total     =>', compile('SUM(ITEMS, _["QTY"] * _["PRICE"])').run(order).as_text())
print('   0.10+0.20 =>', evaluate('0.10 + 0.20').as_text())
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/plain/js.mjs#context -->
```js
const order = Value.fromNative({
  CUSTOMER: 'Zażółć',
  ITEMS: [                                        // an array is a 1-based list
    { SKU: 'AB-1234', QTY: '3', PRICE: '19.99' },
    { SKU: 'CD-5678', QTY: '1', PRICE: '5.01' },
  ],
});
console.log('   first SKU =>', compile('ITEMS[1]["SKU"]').run(order).asText());
console.log('   total     =>', compile('SUM(ITEMS, _["QTY"] * _["PRICE"])').run(order).asText());
console.log('   0.10+0.20 =>', evaluate('0.10 + 0.20').asText());
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/plain/php.php#context -->
```php
$order = Value::fromNative([
    'CUSTOMER' => 'Zażółć',
    'ITEMS' => [                                  // a packed array is a 1-based list
        ['SKU' => 'AB-1234', 'QTY' => '3', 'PRICE' => '19.99'],
        ['SKU' => 'CD-5678', 'QTY' => '1', 'PRICE' => '5.01'],
    ],
]);
echo '   first SKU => ', Sel::compile('ITEMS[1]["SKU"]')->run($order)->asText(), "\n";
echo '   total     => ', Sel::compile('SUM(ITEMS, _["QTY"] * _["PRICE"])')->run($order)->asText(), "\n";
echo '   0.10+0.20 => ', Sel::evaluate('0.10 + 0.20')->asText(), "\n";
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/plain/cpp.cpp#context -->
```cpp
sel::Value order = sel::Value::none();
order.set("CUSTOMER", sel::Value::text("Zażółć"));
std::vector<sel::Value> items;
for (const Item& it : std::vector<Item>{{"AB-1234", "3", "19.99"},
                                        {"CD-5678", "1", "5.01"}}) {
  sel::Value item = sel::Value::none();
  item.set("SKU", sel::Value::text(it.sku));
  item.set("QTY", sel::Value::text(it.qty));
  item.set("PRICE", sel::Value::text(it.price));
  items.push_back(item);
}
order.set("ITEMS", sel::Value::list(items));   // a list is keyed "1".."n"
std::cout << "   first SKU => "
          << sel::compile("ITEMS[1][\"SKU\"]").run(order).as_text() << "\n";
std::cout << "   total     => "
          << sel::compile("SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])").run(order).as_text() << "\n";
std::cout << "   0.10+0.20 => " << sel::evaluate("0.10 + 0.20").as_text() << "\n";
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/plain/lisp.lisp#context -->
```lisp
(let ((order (sel:make-none)))
  (sel:value-set order "CUSTOMER" (sel:make-text "Zażółć"))
  (sel:value-set order "ITEMS"
                 ;; SEL lists are keyed from 1, so ITEMS[1] is the first line
                 ;; on every host.
                 (sel:make-list-value
                  (loop for (sku qty price) in '(("AB-1234" "3" "19.99")
                                                 ("CD-5678" "1" "5.01"))
                        collect (ctx-of `(("SKU" . ,sku) ("QTY" . ,qty)
                                          ("PRICE" . ,price))))))
  (format t "   first SKU => ~a~%"
          (sel:as-text (sel:run (sel:compile-source "ITEMS[1][\"SKU\"]") order)))
  (format t "   total     => ~a~%"
          (sel:as-text (sel:run (sel:compile-source
                                 "SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])") order))))
(format t "   0.10+0.20 => ~a~%" (sel:as-text (sel:evaluate "0.10 + 0.20")))
```

</details>
<!-- /tabs -->

## Constructors

`fromNative` is the short way to a value. Each host also builds one piece by
piece, and every constructor holds the same rules
([spec §8](../../spec/SPEC.md#8-host-interface)):

- **Text and keys are checked.** Invalid UTF-8, or an unpaired surrogate in a
  UTF-16 or code-point host, is `E_UTF8`: in a text, and in every key.
- **Bytes are bytes.** A byte outside 0–255 is `E_RANGE`, and so is a boolean.
- **Numbers obey the digit caps**, whether they come as a string, a native
  integer or the host's decimal form (`E_RANGE`). A decimal is canonicalised
  like a string, so `007` becomes `7` and a negative zero loses its sign.
- **Keys and values pair up.** Counts that differ are `E_BAD_ARG`. A key given
  twice for a record keeps its first position and takes its last value, as
  `RECORD` does. A list's keys are distinct.
- **What you pass is copied.** Changing your list, map, string or buffer
  afterwards does not change the value, and changing what `toNative` returned
  does not change the value it came from.
- **A malformed call is `E_BAD_ARG`.** That covers a float, a non-integer where
  an integer goes, a decimal that is not one, and a native value with no
  conversion. It is a `SelError` in every host, never the host's own exception.
  In PHP and C++, a call whose argument types do not match the declared
  parameters is rejected by the language before SEL sees it.

| | Python | JavaScript | PHP | C++ | Common Lisp |
|---|---|---|---|---|---|
| text, bytes, bool | `Value.text` `Value.bin` `Value.bool` | `Value.text` `Value.bin` `Value.bool` | `Value::text` `Value::bin` `Value::bool` | `Value::text` `Value::bin` `Value::boolean` | `make-text` `make-bin` `make-bool` |
| number from a string | `Value.num("1.50")` | `Value.num('1.50')` | `Value::num('1.50')` | `Value::num("1.50")` | `(make-num "1.50")` |
| number from the decimal form | `Value.num(Dec(neg, digits, scale))`, `Dec` from `sel.decimal` | `Value.num({ neg, digits, scale })`, `digits` a bigint | `Value::num(['neg' => …, 'digits' => '150', 'scale' => 2])` | `Value::num(const Dec&)` | `(make-num (dec-make neg digits scale))` |
| native integer | `Value.int(n)` | `Value.int(n)`, a safe integer or a bigint | `Value::int($n)` | `Value::integer(n)` | `(make-int n)` |
| list | `Value.list(values)` | `Value.list(values)` | `Value::list($values)` | `Value::list(values)` | `(make-list-value values)` |
| list with its own keys | `Value.list(values, keys)` | `Value.fromEntries(entries, true)` | `Value::list($values, $keys)` | — | — |
| record from keys and values | `Value.record(keys, values)`, `Value.shaped(keys, values)` | `Value.shaped(keys, values)` | `Value::record($keys, $values)`, `Value::shaped(…)` | `Value::record(keys, values)` | — |
| record from pairs | `Value.from_entries(pairs)` | `Value.fromEntries(pairs)` | `Value::fromEntries($pairs)` | — | `(from-native alist)` |
| record from a prepared shape | `Value.shaped(shape, values)` | — | `Value::fromShape(RecordShape::intern($keys), $values)` | `Value::shaped(shape, values)` | — |
| many rows of one shape | — | — | `Value::fromNativeRows($rows)` | — | — |
| one key | `v.set(k, x)` | `v.set(k, x)` | `$v->set($k, $x)` | `v.set(k, x)` | `(value-set v k x)` |

The decimal form is `digits × 10^-scale`, negative when `neg`: `digits` is a
non-negative whole number (a string of ASCII digits in PHP and C++) and
`scale` a non-negative integer. Reading one back is `v.as_decimal()` in
Python, `v.asDecimal()` in JS, `$v->asDecimal()` in PHP, `v.dec_val()` in C++
(null when the value holds no parsed decimal) and `(as-dec v)` in Lisp.

## Variables flow back

The context is changed in place, so a rule can hand back more than its result:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/plain/python.py#variables -->
```python
ctx = Value.from_native({'QTY': '3', 'PRICE': '19.99'})
compile('NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT').run(ctx)
for name in ['NET', 'VAT', 'GROSS']:
    print(f'   {name:<5} =>', ctx.get(name).as_text())
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/plain/js.mjs#variables -->
```js
const ctx = Value.fromNative({ QTY: '3', PRICE: '19.99' });
compile('NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT').run(ctx);
for (const name of ['NET', 'VAT', 'GROSS']) {
  console.log(`   ${name.padEnd(5)} =>`, ctx.get(name).asText());
}
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/plain/php.php#variables -->
```php
$ctx = Value::fromNative(['QTY' => '3', 'PRICE' => '19.99']);
Sel::compile('NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT')->run($ctx);
foreach (['NET', 'VAT', 'GROSS'] as $name) {
    printf("   %-5s => %s\n", $name, $ctx->get($name)->asText());
}
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/plain/cpp.cpp#variables -->
```cpp
sel::Value ctx = sel::Value::none();
ctx.set("QTY", sel::Value::text("3"));
ctx.set("PRICE", sel::Value::text("19.99"));
sel::compile("NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT").run(ctx);
for (const char* name : {"NET", "VAT", "GROSS"}) {
  std::cout << "   " << pad(name, 5) << " => " << ctx.get(name)->as_text() << "\n";
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/plain/lisp.lisp#variables -->
```lisp
(let ((ctx (ctx-of '(("QTY" . "3") ("PRICE" . "19.99")))))
  (sel:run (sel:compile-source
            "NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT")
           ctx)
  (dolist (name '("NET" "VAT" "GROSS"))
    (format t "   ~5a => ~a~%" name (sel:as-text (sel:value-get ctx name)))))
```

</details>
<!-- /tabs -->

## Errors

Every failure is one exception type with a stable **code** and the **position**
of the node that failed. Match on the code; the message is for people.
`E_ABORT` is the rule speaking — everything else is the rule failing.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/plain/python.py#errors -->
```python
for src in ['3 + "A"', 'NOSUCH(1)', 'IF(1, "a", "b")', 'ABORT("no stock")']:
    try:
        evaluate(src)
        print(f'   {src:<17} => no error')
    except SelError as e:
        print(f'   {src:<17} => {e.code} at {e.line}:{e.col}')
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/plain/js.mjs#errors -->
```js
for (const src of ['3 + "A"', 'NOSUCH(1)', 'IF(1, "a", "b")', 'ABORT("no stock")']) {
  try {
    evaluate(src);
    console.log(`   ${src.padEnd(17)} => no error`);
  } catch (e) {
    if (!(e instanceof SelError)) throw e;
    console.log(`   ${src.padEnd(17)} => ${e.code} at ${e.line}:${e.col}`);
  }
}
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/plain/php.php#errors -->
```php
foreach (['3 + "A"', 'NOSUCH(1)', 'IF(1, "a", "b")', 'ABORT("no stock")'] as $src) {
    try {
        Sel::evaluate($src);
        printf("   %-17s => no error\n", $src);
    } catch (SelError $e) {
        printf("   %-17s => %s at %d:%d\n", $src, $e->code, $e->line, $e->col);
    }
}
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/plain/cpp.cpp#errors -->
```cpp
for (const char* src : {"3 + \"A\"", "NOSUCH(1)", "IF(1, \"a\", \"b\")",
                        "ABORT(\"no stock\")"}) {
  try {
    sel::evaluate(src);
    std::cout << "   " << pad(src, 17) << " => no error\n";
  } catch (const sel::SelError& e) {
    std::cout << "   " << pad(src, 17) << " => " << e.code()
              << " at " << e.line() << ":" << e.col() << "\n";
  }
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/plain/lisp.lisp#errors -->
```lisp
(dolist (src '("3 + \"A\"" "NOSUCH(1)" "IF(1, \"a\", \"b\")" "ABORT(\"no stock\")"))
  (handler-case
      (progn (sel:evaluate src)
             (format t "   ~17a => no error~%" src))
    (sel:sel-error (e)
      (format t "   ~17a => ~a at ~D:~D~%" src
              (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e)))))
```

</details>
<!-- /tabs -->

## What does a rule read?

`dependencies()` answers statically, without running the rule — which is what
lets a form re-check only the rules a changed field can affect:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/plain/python.py#dependencies -->
```python
print('  ', ' '.join(
    compile('T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""').dependencies()))
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/plain/js.mjs#dependencies -->
```js
console.log('  ', compile('T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""')
  .dependencies().join(' '));
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/plain/php.php#dependencies -->
```php
echo '   ', implode(' ', Sel::compile('T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""')
    ->dependencies()), "\n";
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/plain/cpp.cpp#dependencies -->
```cpp
std::cout << "   " << join(sel::compile(
    "T = SUM(ITEMS, _[\"QTY\"]); T > LIMIT AND CUSTOMER $!= \"\"").dependencies(), " ") << "\n";
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/plain/lisp.lisp#dependencies -->
```lisp
(format t "   ~{~a~^ ~}~%"
        (sel:dependencies
         (sel:compile-source "T = SUM(ITEMS, _[\"QTY\"]); T > LIMIT AND CUSTOMER $!= \"\"")))
```

</details>
<!-- /tabs -->

The answer follows evaluation order (spec §8): a name is reported when some read of
it can happen before the rule has definitely assigned it. So a rule that reads `A`
and assigns it later reports `A`; one that assigns `A` first reports only what else
it reads; and an assignment inside one branch of an `IF`, on the right of `AND`,
`OR` or `??`, or in an aggregate's body does not count as an assignment for a read
after it. `X += 1` and `A[k] += x` read their target. It is safe to over-report
and never to under-report, which is what a form re-checking on change needs.

## Host functions and threads

`register_function` / `registerFunction` may be called while other threads compile
or run programs; the function table is synchronised in every host that has threads,
and a program already compiled keeps the function it was compiled against. Reading
an argument the call does not have (a function declared with one or two arguments
asking for the fifth) is `E_BAD_ARG`, and a registration that is not callable, or a
name that is not allowed, is refused when registering, in each host's own
argument-error class.

## PHP memory

A PHP `Value` costs a few hundred bytes, so a rule that builds a collection near
the language's cap (`MAX_COLLECTION`, one million elements) needs `memory_limit`
of at least 1G on PHP 8.5, and roughly two to three times that on PHP 8.1. Text is
kept as bytes and is not the problem: a 16-million-character text is about that
many bytes. The default 128M is enough for everything below a few hundred thousand
elements. The repository's own budget and PHP 8.1 lanes run with
`memory_limit=-1`.

## The API side by side

| | Python | JavaScript | PHP | C++ | Common Lisp |
|---|---|---|---|---|---|
| compile | `compile(src)` | `compile(src)` | `Sel::compile($src)` | `sel::compile(src)` | `(sel:compile-source src)` |
| run | `p.run(ctx)` | `p.run(ctx)` | `$p->run($ctx)` | `p.run(ctx)` | `(sel:run p ctx)` |
| evaluate | `evaluate(src, ctx)` | `evaluate(src, ctx)` | `Sel::evaluate($src, $ctx)` | `sel::evaluate(src, ctx)` | `(sel:evaluate src ctx)` |
| inputs | `p.dependencies()` | `p.dependencies()` | `$p->dependencies()` | `p.dependencies()` | `(sel:dependencies p)` |
| context | `Value.from_native({...})` | `Value.fromNative({...})` | `Value::fromNative([...])` | `Value::none()` + `set` | `(sel:from-native ...)` |
| result | `.as_text()` `.as_bool()` `.dump()` | `.asText()` `.asBool()` `.dump()` | `->asText()` `->asBool()` `->dump()` | `.as_text()` `.as_bool()` `.dump()` | `(sel:as-text v)` `(sel:as-bool v)` `(sel:value-dump v)` |
| errors | `SelError` `.code .line .col` | `SelError` `.code .line .col` | `SelError` `->code ->line ->col` | `sel::SelError` `.code() .line() .col()` | `sel:sel-error` `sel-error-code` … |
| own functions | `register_function` | `registerFunction` | `Sel::registerFunction` | `sel::register_function` | `sel:register-function` |

The full contract is [spec/SPEC.md §8](../../spec/SPEC.md#8-host-interface), and
`tools/check-api.sh` runs the same probes through all five bindings to keep the
answers identical.
