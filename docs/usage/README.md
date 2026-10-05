# Using SEL

SEL is embedded: your application hands a rule and some data to the host
library, and gets a value back. This page is that host API in all seven
languages. The pages after it are complete programs, each written seven times:

| Page | What it shows |
|---|---|
| [A REPL in thirty lines](repl.md) | the whole API in the smallest useful program |
| [Validation](validation.md) | a form's rule set: compiled once, run per submission, errors told apart |
| [Scripting with host functions](scripting.md) | the application registers functions; a script decides what happens |
| [SQL conditions](sql-conditions.md) | a rule as a `WHERE` clause — naive, and with the schema described |
| [SQL pipelines](sql-pipelines.md) | whole queries in SQL, split with memory, or in memory — star, EAV, 3NF, flat and complex data |
| [Your own functions, in SQL](sql-functions.md) | host functions with a SQL spelling: PostgreSQL SQL and PL/pgSQL functions, list arguments, strict mode |

Every snippet on these pages is quoted from a file under
[`examples/`](../../examples/) that the test suite runs in all seven languages
and whose output it compares byte for byte — the code cannot drift from what the
page says, and the seven tabs cannot drift from each other.

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
  import { compile, evaluate } from 'https://cdn.jsdelivr.net/npm/sel-lang@0.10.0/dist/sel.min.mjs';
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
vcpkg install sel-lang               # or: conan install --requires sel-lang/0.10.0
                                     # or copy cpp/sel.hpp, sel_ast.hpp, sel_limits.hpp,
                                     # sel_math_ops.hpp, sel_builtin_manifest.hpp, sel.cpp
                                     # and third_party/srell/, and compile sel.cpp
```
```cpp
#include "sel.hpp"                   // C++23; find_package(sel-lang) with CMake
#include "sel_sql.hpp"               // the SQL layer: add cpp/sel_sql*.cpp
```

</details>
<details>
<summary>Rust</summary>

```sh
cargo add sel-lang                   # Rust 1.85+; the `sel` CLI: cargo install sel-lang
```
```rust
use sel_lang::{compile, evaluate, Pos, SelError, Value};   // depends on regex
use sel_lang::sql::{plan_hybrid, Binding, Bindings};       // the SQL layer: the default `sql` feature
use sel_lang::sql::serde_json::json;                       // for sql::define, re-exported
```

</details>
<details>
<summary>Go</summary>

```sh
go get github.com/nathanjel/sel/go@v0.10.0
go install github.com/nathanjel/sel/go/bin/sel@v0.10.0    # the `sel` CLI
```
```go
import (
	"github.com/nathanjel/sel/go/sel"     // Go 1.22+, no dependencies
	"github.com/nathanjel/sel/go/sel/sql" // the SQL layer
)
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

The snippets on this page come from [`examples/plain`](../../examples/plain/).
The Rust and Go tabs use a few small helpers from it: `at` is "the host"
as a position (`Pos::default()`, `sel.Pos{}`); Rust's `val(s)` is
`Value::text_owned(s.to_string())` and `text(v)` is `v.as_text(at)`; Go's
`check(err)` stops on an error a well-formed program never has, and
`eval(src)` is `sel.Eval(src, nil)` with that check.

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
<summary>Rust</summary>

<!-- from: examples/plain/rust.rs#compile -->
```rust
let mut rule = compile("IF(QTY * PRICE > LIMIT, \"over budget\", \"ok\")")?;
for (qty, price) in [("3", "19.99"), ("1", "5.00")] {
    let ctx = Value::none();
    ctx.set("QTY", val(qty), at)?;
    ctx.set("PRICE", val(price), at)?;
    ctx.set("LIMIT", val("50.00"), at)?;
    println!("   QTY={qty} PRICE={price} => {}", text(&rule.run(Some(ctx))?)?);
}
```

</details>
<details>
<summary>Go</summary>

<!-- from: examples/plain/go.go#compile -->
```go
rule, err := sel.Compile(`IF(QTY * PRICE > LIMIT, "over budget", "ok")`)
check(err)
for _, row := range [][2]string{{"3", "19.99"}, {"1", "5.00"}} {
	ctx := sel.NewNone()
	ctx.Set("QTY", sel.NewText(row[0]))
	ctx.Set("PRICE", sel.NewText(row[1]))
	ctx.Set("LIMIT", sel.NewText("50.00"))
	v, err := rule.Run(ctx)
	check(err)
	fmt.Printf("   QTY=%s PRICE=%s => %s\n", row[0], row[1], v.AsText(at))
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
it from its own maps and lists; C++, Rust and Go, which have no native map to
convert from, build it child by child — `Value::none()` and `set()`, or
`sel.NewNone()` and `Set()`.

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
<summary>Rust</summary>

<!-- from: examples/plain/rust.rs#context -->
```rust
let order = Value::none();
order.set("CUSTOMER", val("Zażółć"), at)?;
let mut items = Vec::new();
for (sku, qty, price) in [("AB-1234", "3", "19.99"), ("CD-5678", "1", "5.01")] {
    let item = Value::none();
    item.set("SKU", val(sku), at)?;
    item.set("QTY", val(qty), at)?;
    item.set("PRICE", val(price), at)?;
    items.push(item);
}
order.set("ITEMS", Value::list(items), at)?; // a list is keyed "1".."n"
println!("   first SKU => {}", text(&compile("ITEMS[1][\"SKU\"]")?.run(Some(order.clone()))?)?);
println!("   total     => {}", text(&compile("SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])")?.run(Some(order))?)?);
println!("   0.10+0.20 => {}", text(&evaluate("0.10 + 0.20", None)?)?);
```

</details>
<details>
<summary>Go</summary>

<!-- from: examples/plain/go.go#context -->
```go
order := sel.NewNone()
order.Set("CUSTOMER", sel.NewText("Zażółć"))
var items []*sel.Value
for _, line := range [][3]string{{"AB-1234", "3", "19.99"}, {"CD-5678", "1", "5.01"}} {
	item := sel.NewNone()
	item.Set("SKU", sel.NewText(line[0]))
	item.Set("QTY", sel.NewText(line[1]))
	item.Set("PRICE", sel.NewText(line[2]))
	items = append(items, item)
}
order.Set("ITEMS", sel.NewList(items)) // a list is keyed "1".."n"
first, err := sel.MustCompile(`ITEMS[1]["SKU"]`).Run(order)
check(err)
total, err := sel.MustCompile(`SUM(ITEMS, _["QTY"] * _["PRICE"])`).Run(order)
check(err)
fmt.Println("   first SKU =>", first.AsText(at))
fmt.Println("   total     =>", total.AsText(at))
fmt.Println("   0.10+0.20 =>", eval("0.10 + 0.20").AsText(at))
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
  In PHP, C++, Rust and Go, a call whose argument types do not match the
  declared parameters is rejected by the language before SEL sees it. Go
  reports these by panicking with the `*sel.SelError` rather than returning it
  ([below](#go-and-rust-which-calls-return-the-error)).

| | Python | JavaScript | PHP | C++ | Common Lisp | Rust | Go |
|---|---|---|---|---|---|---|---|
| text, bytes, bool | `Value.text` `Value.bin` `Value.bool` | `Value.text` `Value.bin` `Value.bool` | `Value::text` `Value::bin` `Value::bool` | `Value::text` `Value::bin` `Value::boolean` | `make-text` `make-bin` `make-bool` | `Value::text_owned` `Value::bin` `Value::bool` | `sel.NewText` `sel.NewBin` `sel.NewBool` |
| number from a string | `Value.num("1.50")` | `Value.num('1.50')` | `Value::num('1.50')` | `Value::num("1.50")` | `(make-num "1.50")` | `Value::num(dec_parse("1.50", pos)?)?` | — (a number is its text: `sel.NewText("1.50")`) |
| number from the decimal form | `Value.num(Dec(neg, digits, scale))`, `Dec` from `sel.decimal` | `Value.num({ neg, digits, scale })`, `digits` a bigint | `Value::num(['neg' => …, 'digits' => '150', 'scale' => 2])` | `Value::num(const Dec&)` | `(make-num (dec-make neg digits scale))` | `Value::num(Dec::from_small(neg, mantissa, scale))?` | `sel.NewDecimal(sel.Decimal{Neg: neg, Digits: digits, Scale: scale})`, `Digits` a `*big.Int` |
| native integer | `Value.int(n)` | `Value.int(n)`, a safe integer or a bigint | `Value::int($n)` | `Value::integer(n)` | `(make-int n)` | `Value::int(n)`, an `i64` | `sel.NewInt(n)`, an `int64` |
| list | `Value.list(values)` | `Value.list(values)` | `Value::list($values)` | `Value::list(values)` | `(make-list-value values)` | `Value::list(values)` | `sel.NewList(values)` |
| list with its own keys | `Value.list(values, keys)` | `Value.fromEntries(entries, true)` | `Value::list($values, $keys)` | — | — | — | `sel.NewListWithKeys(values, keys)` |
| record from keys and values | `Value.record(keys, values)`, `Value.shaped(keys, values)` | `Value.shaped(keys, values)` | `Value::record($keys, $values)`, `Value::shaped(…)` | `Value::record(keys, values)` | — | — | — |
| record from pairs | `Value.from_entries(pairs)` | `Value.fromEntries(pairs)` | `Value::fromEntries($pairs)` | — | `(from-native alist)` | — | `sel.NewRecordFromEntries(entries)` |
| record from a prepared shape | `Value.shaped(shape, values)` | — | `Value::fromShape(RecordShape::intern($keys), $values)` | `Value::shaped(shape, values)` | — | — | `sel.NewShapedRecord(sel.InternRecordShape(keys), values)` |
| many rows of one shape | — | — | `Value::fromNativeRows($rows)` | — | — | — | — |
| one key | `v.set(k, x)` | `v.set(k, x)` | `$v->set($k, $x)` | `v.set(k, x)` | `(value-set v k x)` | `v.set(k, x, pos)?` | `v.Set(k, x)` |

Rust (`Value::list_with_keys`, `record_from_entries`, `shaped_record`) has more
constructors than the table shows, but those are the builtins' own: they take
what they are given, without the checks above, so the table leaves them out.
Go's record constructors check their input (a key that is not UTF-8, a repeated
key in a shape, a count that is not the shape's, a nil value), panicking with
the `*sel.SelError` like the others.

The decimal form is `digits × 10^-scale`, negative when `neg`: `digits` is a
non-negative whole number (a string of ASCII digits in PHP and C++) and
`scale` a non-negative integer. Reading one back is `v.as_decimal()` in
Python, `v.asDecimal()` in JS, `$v->asDecimal()` in PHP, `v.as_decimal(pos)` in
C++ (read its digits with `sel::dec_digits(d)`, whatever form the magnitude is
held in; `v.dec_val()` is the cache read, null when the value holds no parsed
decimal yet), `(as-dec v)` in Lisp, `v.as_decimal(pos)?` in Rust, and
`v.Decimal(pos)` in Go, a `sel.Decimal` whose `Digits` is the caller's own copy.
Go's `sel.NewNum` and `Value.AsDecimal` are deprecated: their decimal type
cannot be named outside the module.

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
<summary>Rust</summary>

<!-- from: examples/plain/rust.rs#variables -->
```rust
let ctx = Value::none();
ctx.set("QTY", val("3"), at)?;
ctx.set("PRICE", val("19.99"), at)?;
// A Value is a shared handle: the run assigns into the same record.
compile("NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT")?.run(Some(ctx.clone()))?;
for name in ["NET", "VAT", "GROSS"] {
    println!("   {name:<5} => {}", text(&ctx.get(name).expect("set by the rule"))?);
}
```

</details>
<details>
<summary>Go</summary>

<!-- from: examples/plain/go.go#variables -->
```go
ctx := sel.NewNone()
ctx.Set("QTY", sel.NewText("3"))
ctx.Set("PRICE", sel.NewText("19.99"))
// A *sel.Value is a handle: the run assigns into the same record.
_, err = sel.MustCompile("NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT").Run(ctx)
check(err)
for _, name := range []string{"NET", "VAT", "GROSS"} {
	fmt.Printf("   %-5s => %s\n", name, ctx.Get(name).AsText(at))
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
<summary>Rust</summary>

<!-- from: examples/plain/rust.rs#errors -->
```rust
for src in ["3 + \"A\"", "NOSUCH(1)", "IF(1, \"a\", \"b\")", "ABORT(\"no stock\")"] {
    match evaluate(src, None) {
        Ok(_) => println!("   {src:<17} => no error"),
        Err(e) => println!("   {src:<17} => {} at {}:{}", e.code, e.pos.line, e.pos.col),
    }
}
```

</details>
<details>
<summary>Go</summary>

<!-- from: examples/plain/go.go#errors -->
```go
for _, src := range []string{`3 + "A"`, "NOSUCH(1)", `IF(1, "a", "b")`, `ABORT("no stock")`} {
	_, err := sel.Eval(src, nil)
	var e *sel.SelError
	if errors.As(err, &e) {
		fmt.Printf("   %-17s => %s at %d:%d\n", src, e.Code, e.Line(), e.Col())
	} else {
		fmt.Printf("   %-17s => no error\n", src)
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

### Go and Rust: which calls return the error

In the five dynamic or exception-based hosts every failure is raised the same
way. Go and Rust split it by call.

**Go.** `sel.Compile`, `Program.Run`, `sel.Eval` and the SQL layer's
`Translate`, `TryTranslate`, `TranslateStatement` and `ExecuteHybrid` return an
`error`, which is always a `*sel.SelError` (or the SQL layer's `*sql.SqlError`).
Everything else reports a failure by **panicking** with one — the way an index
out of range does:

- the accessors of a value — `AsText`, `AsBool`, `AsBytes`, `AsDecimal` — when the
  value is not of that kind: a rule that answers `TRUE` where text was expected
  is a panic at `v.AsText(pos)`, after `Run` returned no error;
- constructors and `Set` given what SEL refuses (invalid UTF-8 is `E_UTF8`,
  `NewListWithKeys` with counts that differ is `E_BAD_ARG`);
- `sql.PlanHybrid` with a dialect that does not exist, and the dialect
  extension calls `sql.Define`, `DefineDialect` and `DefineBuilder`;
- `RegisterFunction` with a name or an arity that is not allowed — a programming
  error, so it panics with a plain string, at start-up.

Inside a host function that is the way to fail: `sel.Fail(code, message, pos)`
panics with a `*sel.SelError`, the `Args` readers do the same, and `Run` turns
it into its returned error. Outside one, a server that reads results it does
not control recovers the accessor's panic, as the validation example does:

<!-- from: examples/validation/go.go#astext -->
```go
// asText is v.AsText for a result that might not be text. The accessors of a
// *sel.Value panic with a *sel.SelError rather than return one, so a rule that
// answered TRUE would otherwise take the request down with it.
func asText(v *sel.Value) (text string, err error) {
	defer func() {
		if r := recover(); r != nil {
			e, ok := r.(*sel.SelError)
			if !ok {
				panic(r)
			}
			err = e
		}
	}()
	return v.AsText(sel.Pos{}), nil
}
```

A compiled `*sel.Program` is safe to share between goroutines.

**Rust.** Every call that can fail returns `Result<_, SelError>`, accessors
included (`v.as_text(pos)?`), so `?` carries a SEL error up to the caller. The
exceptions are the SQL layer's configuration calls: `sql::define`,
`define_dialect`, `define_builder`, the `Binding` constructors and
`plan_hybrid` panic with a `SqlError` on a bad argument. A `Program` is neither
`Send` nor `Sync`, and `run` takes `&mut self`: compile one per thread (it is
cheap), or keep it in a `thread_local!`. For the same reason a host function —
which `register_function` requires to be `Send + Sync + 'static` — cannot
capture a compiled program or a `Value`; examples/sql-functions keeps its
programs in a `thread_local!`. A host function fails by returning
`Err(SelError::new(code, message, pos))`.

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
<summary>Rust</summary>

<!-- from: examples/plain/rust.rs#dependencies -->
```rust
let deps = compile("T = SUM(ITEMS, _[\"QTY\"]); T > LIMIT AND CUSTOMER $!= \"\"")?.dependencies()?;
println!("   {}", deps.join(" "));
```

</details>
<details>
<summary>Go</summary>

<!-- from: examples/plain/go.go#dependencies -->
```go
deps := sel.MustCompile(`T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""`).Dependencies()
fmt.Println("  ", strings.Join(deps, " "))
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

### Sharing a compiled program

A compiled program may be shared between threads in C++: a `Program` is
immutable once `compile()` returns and may run on several threads at once, its
first run included, **provided every run gets a context of its own**. A C++
`Value` is not thread-safe even for reading (its reference count is a plain
integer), so a context and every value reachable from it belong to one thread at
a time: build each thread's context on that thread, or `clone()` it there from a
value no other thread is touching. The SQL layer's translation may run on
several threads once the dialects it uses are registered. `cpp/sel.hpp` states
the contract, and `make -C cpp tsan` holds it.

In Rust a `Program` is `Send` but not `Sync`, and `run` takes `&mut self`: move
a compiled program to the thread that runs it, or give each thread its own —
`program.clone()` before spawning, a program compiled per thread, or one in a
`thread_local!`. A `Value` (and so a context or a result) is neither `Send` nor
`Sync`; hand other threads its text (`dump()`, `as_text()`) instead. A host
function must be `Send + Sync + 'static`, so it cannot capture either.

## PHP memory

A PHP `Value` costs a few hundred bytes, so a rule that builds a collection near
the language's cap (`MAX_COLLECTION`, one million elements) needs `memory_limit`
of at least 1G on PHP 8.5, and roughly two to three times that on PHP 8.1. Text is
kept as bytes and is not the problem: a 16-million-character text is about that
many bytes. The default 128M is enough for everything below a few hundred thousand
elements. The repository's own budget and PHP 8.1 lanes run with
`memory_limit=-1`.

## The API side by side

| | Python | JavaScript | PHP | C++ | Common Lisp | Rust | Go |
|---|---|---|---|---|---|---|---|
| compile | `compile(src)` | `compile(src)` | `Sel::compile($src)` | `sel::compile(src)` | `(sel:compile-source src)` | `compile(src)?` | `sel.Compile(src)` |
| run | `p.run(ctx)` | `p.run(ctx)` | `$p->run($ctx)` | `p.run(ctx)` | `(sel:run p ctx)` | `p.run(Some(ctx))?` | `p.Run(ctx)` |
| evaluate | `evaluate(src, ctx)` | `evaluate(src, ctx)` | `Sel::evaluate($src, $ctx)` | `sel::evaluate(src, ctx)` | `(sel:evaluate src ctx)` | `evaluate(src, Some(ctx))?` | `sel.Eval(src, ctx)` |
| inputs | `p.dependencies()` | `p.dependencies()` | `$p->dependencies()` | `p.dependencies()` | `(sel:dependencies p)` | `p.dependencies()?` | `p.Dependencies()` |
| context | `Value.from_native({...})` | `Value.fromNative({...})` | `Value::fromNative([...])` | `Value::none()` + `set` | `(sel:from-native ...)` | `Value::none()` + `set` | `sel.NewNone()` + `Set` |
| result | `.as_text()` `.as_bool()` `.dump()` | `.asText()` `.asBool()` `.dump()` | `->asText()` `->asBool()` `->dump()` | `.as_text()` `.as_bool()` `.dump()` | `(sel:as-text v)` `(sel:as-bool v)` `(sel:value-dump v)` | `.as_text(pos)?` `.as_bool(pos)?` `.dump()?` | `.AsText(pos)` `.AsBool(pos)` `.Dump()` |
| errors | `SelError` `.code .line .col` | `SelError` `.code .line .col` | `SelError` `->code ->line ->col` | `sel::SelError` `.code() .line() .col()` | `sel:sel-error` `sel-error-code` … | `SelError` `.code .pos.line .pos.col` | `*sel.SelError` `.Code .Line() .Col()` |
| own functions | `register_function` | `registerFunction` | `Sel::registerFunction` | `sel::register_function` | `sel:register-function` | `register_function` | `sel.RegisterFunction` |

In Go, the accessors in the *result* row and the constructors panic with a
`*sel.SelError` rather than return it — see
[Go and Rust: which calls return the error](#go-and-rust-which-calls-return-the-error).

The full contract is [spec/SPEC.md §8](../../spec/SPEC.md#8-host-interface), and
`tools/check-api.sh` runs the same probes through all seven bindings to keep the
answers identical.
