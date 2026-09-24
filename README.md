<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo-light.svg" alt="SEL — Simple Expression Language" width="420">
  </picture>
</p>

<p align="center"><b>Write a business rule once. Run it in five languages — or in your database — and get the same answer.</b></p>

<p align="center">Python · JavaScript · PHP · C++23 · Common Lisp &nbsp;|&nbsp; MariaDB · MySQL · PostgreSQL · SQLite</p>

---

SEL is a small expression language for the rules applications live by —
validation, pricing, eligibility, routing, reporting. A rule is text:

```sel
TOTAL = SUM(ITEMS, _["qty"] * _["price"]);
COND(IS_BLANK(CUSTOMER),                   ABORT("customer is required"),
     NOT RMATCH('^\d{2}-\d{3}$', POSTCODE), ABORT("postcode {POSTCODE} is not 00-000"),
     TOTAL > CREDIT_LIMIT,                  ABORT("total {TOTAL} exceeds {CREDIT_LIMIT}"),
     "ok")
```

Five independent implementations run it, held to one written specification, and
they agree to the byte — on the value, and on the error code and position when a
rule fails. The browser and the server, the batch job and the API, give one
verdict.

## Why SEL

- **Parity is the product.** Exact decimal arithmetic — no floating point — no
  truthiness, strict UTF-8, a portable regex subset, and `NULL` that never turns
  into zero. Every host is held to a shared conformance suite, a differential
  fuzzer, a decimal oracle and documentation whose every example is executed.
  [How parity is guaranteed →](docs/parity.md)
- **Rules that reach the database.** The same rule compiles to a SQL condition
  — knowing nothing about the schema, or with a description of it that makes the
  SQL tight and reaches related tables. A pipeline of `FILTER`, `LINK`, `BUCKET`,
  `MAP` and sorts compiles to a whole `SELECT`, and the **hybrid planner** pushes
  the longest exact prefix into the database and finishes the rest in memory —
  refusing, never guessing, wherever SQL would mean something else.
  [SQL conditions →](docs/usage/sql-conditions.md) · [SQL pipelines →](docs/usage/sql-pipelines.md)
- **Easy to embed and to extend.** Python, PHP and JavaScript have no
  dependencies; C++ is three files and a vendored regex engine; Lisp is an ASDF
  system. An application adds its own functions with one call, and its own SQL
  dialects and spellings the same way. [Extending SEL →](docs/extending.md)
- **Small on purpose.** One expression per program. No loops, no user-defined
  functions, no dynamic names — so every rule terminates, and the inputs it reads
  are known before it runs. [Overview →](docs/overview.md)

## A taste, in each language

Compile once, run per row:

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
<details open>
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
<details open>
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
<details open>
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
<details open>
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

Each of these is part of [`examples/plain`](examples/plain/), and the five print
byte-identical output — which the test suite checks, on the code shown here.

## Documentation

| | |
|---|---|
| [Overview](docs/overview.md) | what SEL is, its principles, where it fits, what it leaves out |
| [Parity](docs/parity.md) | how five hosts are made to agree, and how that is checked |
| [Syntax](docs/syntax.md) · [Operators](docs/operators.md) · [Functions](docs/functions.md) | the language |
| [Using SEL](docs/usage/README.md) | the host API; [a REPL](docs/usage/repl.md), [validation](docs/usage/validation.md), [scripting with host functions](docs/usage/scripting.md) |
| [SEL and SQL](docs/usage/sql-conditions.md) | conditions; [pipelines](docs/usage/sql-pipelines.md) over [star](docs/usage/sql-star.md), [EAV](docs/usage/sql-eav.md), [3NF](docs/usage/sql-3nf.md), [flat](docs/usage/sql-flat.md) and [complex](docs/usage/in-memory.md) data; [reference](docs/sql.md) |
| [Extending](docs/extending.md) · [Contributing](docs/contributing.md) | host functions, dialects, builtins; the order of work and the traps |
| [Specification](spec/SPEC.md) | the normative text, with [grammar](spec/grammar.md) and [error codes](spec/errors.md) |

All of it is also a styled site with language tabs — see [the documentation
home](docs/README.md#two-ways-to-read-this) to build or serve it, or to run it as
a Docker image.

## Install

The package is `sel-lang` everywhere:

```sh
pip install sel-lang
npm install sel-lang
composer require nathanjel/sel-lang
vcpkg install sel-lang            # or: conan install --requires sel-lang/0.8.1
(ql:quickload :sel-lang)          # Quicklisp / Ultralisp
```

Or copy `python/sel/`, `js/src/` or `php/src/` into a project — no package
manager, no build step. [Using SEL](docs/usage/README.md#installing) has the
imports for each host, and [PACKAGING.md](PACKAGING.md) the registries.

## Checking it

```sh
tools/check.sh          # every layer, every host; prints ALL GREEN or it isn't done
```

[Parity](docs/parity.md#guaranteeing-it-the-gate) describes each layer, and
[Contributing](docs/contributing.md) the order of work.

## Licence

[MIT](LICENSE). Two third-party components keep their own BSD 2-Clause
licences: **SRELL**, vendored into the C++ implementation, and **cl-ppcre**, which
the Common Lisp system depends on; both are listed in [LICENSE](LICENSE). The
Python, PHP and JavaScript implementations have no dependencies at all.
