# Validation

The job SEL was made for: a form's rules written once, checked in the browser
as the user types and again on the server, with the same verdict in both. Here a
checkout form has five rules — a name, an e-mail address, a postcode that depends
on the country, a quantity within stock, and a total within the customer's credit
limit.

## The rules

Each rule answers `""` when its field is fine and `ABORT("message")` when it is
not. The rules are data — they could as well come from a file or a database — and
the same text is what the browser compiles.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/validation/python.py#rules -->
```python
RULES = {
    'name':     'IF(IS_BLANK(NAME), ABORT("Please tell us your name"), "")',
    'email':    "IF(RMATCH('^[^@ ]+@[^@ ]+\\.[a-z]{2,}$', TRIM(EMAIL), \"i\"), \"\","
                ' ABORT("{EMAIL} does not look like an e-mail address"))',
    'postcode': 'COND(COUNTRY $== "PL" AND NOT RMATCH(\'^\\d{2}-\\d{3}$\', POSTCODE),'
                '       ABORT("Polish postcodes look like 00-000"),'
                '     COUNTRY $== "DE" AND NOT RMATCH(\'^\\d{5}$\', POSTCODE),'
                '       ABORT("German postcodes have five digits"),'
                '     "")',
    'quantity': 'IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,'
                ' ABORT("Choose between 1 and {STOCK}"), "")',
    'total':    'TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);'
                ' IF(TOTAL > CREDIT_LIMIT, ABORT("{TOTAL} is over your limit of {CREDIT_LIMIT}"), "")',
}
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/validation/js.mjs#rules -->
```js
const RULES = {
  name:     'IF(IS_BLANK(NAME), ABORT("Please tell us your name"), "")',
  email:    "IF(RMATCH('^[^@ ]+@[^@ ]+\\.[a-z]{2,}$', TRIM(EMAIL), \"i\"), \"\","
            + ' ABORT("{EMAIL} does not look like an e-mail address"))',
  postcode: 'COND(COUNTRY $== "PL" AND NOT RMATCH(\'^\\d{2}-\\d{3}$\', POSTCODE),'
            + '       ABORT("Polish postcodes look like 00-000"),'
            + '     COUNTRY $== "DE" AND NOT RMATCH(\'^\\d{5}$\', POSTCODE),'
            + '       ABORT("German postcodes have five digits"),'
            + '     "")',
  quantity: 'IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,'
            + ' ABORT("Choose between 1 and {STOCK}"), "")',
  total:    'TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);'
            + ' IF(TOTAL > CREDIT_LIMIT, ABORT("{TOTAL} is over your limit of {CREDIT_LIMIT}"), "")',
};
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/validation/php.php#rules -->
```php
const RULES = [
    'name'     => 'IF(IS_BLANK(NAME), ABORT("Please tell us your name"), "")',
    'email'    => 'IF(RMATCH(\'^[^@ ]+@[^@ ]+\.[a-z]{2,}$\', TRIM(EMAIL), "i"), "",'
                . ' ABORT("{EMAIL} does not look like an e-mail address"))',
    'postcode' => 'COND(COUNTRY $== "PL" AND NOT RMATCH(\'^\d{2}-\d{3}$\', POSTCODE),'
                . '       ABORT("Polish postcodes look like 00-000"),'
                . '     COUNTRY $== "DE" AND NOT RMATCH(\'^\d{5}$\', POSTCODE),'
                . '       ABORT("German postcodes have five digits"),'
                . '     "")',
    'quantity' => 'IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,'
                . ' ABORT("Choose between 1 and {STOCK}"), "")',
    'total'    => 'TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);'
                . ' IF(TOTAL > CREDIT_LIMIT, ABORT("{TOTAL} is over your limit of {CREDIT_LIMIT}"), "")',
];
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/validation/cpp.cpp#rules -->
```cpp
const std::vector<std::pair<std::string, std::string>> RULES = {
    {"name",     R"(IF(IS_BLANK(NAME), ABORT("Please tell us your name"), ""))"},
    {"email",    R"(IF(RMATCH('^[^@ ]+@[^@ ]+\.[a-z]{2,}$', TRIM(EMAIL), "i"), "",)"
                 R"( ABORT("{EMAIL} does not look like an e-mail address")))"},
    {"postcode", R"(COND(COUNTRY $== "PL" AND NOT RMATCH('^\d{2}-\d{3}$', POSTCODE),)"
                 R"(       ABORT("Polish postcodes look like 00-000"),)"
                 R"(     COUNTRY $== "DE" AND NOT RMATCH('^\d{5}$', POSTCODE),)"
                 R"(       ABORT("German postcodes have five digits"),)"
                 R"(     ""))"},
    {"quantity", R"(IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,)"
                 R"( ABORT("Choose between 1 and {STOCK}"), ""))"},
    {"total",    R"(TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);)"
                 R"( IF(TOTAL > CREDIT_LIMIT, ABORT("{TOTAL} is over your limit of {CREDIT_LIMIT}"), ""))"},
};
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/validation/lisp.lisp#rules -->
```lisp
(defparameter *rules*
  `(("name"     . "IF(IS_BLANK(NAME), ABORT(\"Please tell us your name\"), \"\")")
    ("email"    . ,(concatenate
                    'string
                    "IF(RMATCH('^[^@ ]+@[^@ ]+\\.[a-z]{2,}$', TRIM(EMAIL), \"i\"), \"\","
                    " ABORT(\"{EMAIL} does not look like an e-mail address\"))"))
    ("postcode" . ,(concatenate
                    'string
                    "COND(COUNTRY $== \"PL\" AND NOT RMATCH('^\\d{2}-\\d{3}$', POSTCODE),"
                    "       ABORT(\"Polish postcodes look like 00-000\"),"
                    "     COUNTRY $== \"DE\" AND NOT RMATCH('^\\d{5}$', POSTCODE),"
                    "       ABORT(\"German postcodes have five digits\"),"
                    "     \"\")"))
    ("quantity" . ,(concatenate
                    'string
                    "IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,"
                    " ABORT(\"Choose between 1 and {STOCK}\"), \"\")"))
    ("total"    . ,(concatenate
                    'string
                    "TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);"
                    " IF(TOTAL > CREDIT_LIMIT, ABORT(\"{TOTAL} is over your limit of {CREDIT_LIMIT}\"), \"\")")))
  "One rule per field, as (field . source), in the order they are checked.")
```

</details>
<!-- /tabs -->

A rule can compute before it decides (`TOTAL = …; IF(…)`), interpolate values
into its message (`"{TOTAL} is over your limit of {CREDIT_LIMIT}"`), guard a
comparison against text that is not a number (`NOT ISNUM(QTY) OR …`, relying on
`OR` stopping at the first `TRUE`), and choose between countries with `COND`.
The regular expressions are the portable subset: `\d` means ASCII digits in
every host, and the `i` flag is ASCII case folding.

## Compile once

Compile every rule when the application starts. A rule that does not parse, calls
a function that does not exist or passes the wrong number of arguments then fails
the deployment, not a customer.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/validation/python.py#compile -->
```python
compiled = {field: compile(source) for field, source in RULES.items()}
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/validation/js.mjs#compile -->
```js
const compiled = Object.fromEntries(
  Object.entries(RULES).map(([field, source]) => [field, compile(source)]));
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/validation/php.php#compile -->
```php
$compiled = array_map(fn (string $source) => Sel::compile($source), RULES);
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/validation/cpp.cpp#compile -->
```cpp
Rules compiled;
for (const auto& [field, source] : RULES) compiled.emplace_back(field, sel::compile(source));
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/validation/lisp.lisp#compile -->
```lisp
(defparameter *compiled*
  (loop for (field . source) in *rules*
        collect (cons field (sel:compile-source source))))
```

</details>
<!-- /tabs -->

## Validate, and tell the two kinds of failure apart

`E_ABORT` is the rule talking to the user. Any other code means the rule itself
could not be computed — a price that is not a number, a field that is missing —
and the user should see a generic line while the details go to a log.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/validation/python.py#validate -->
```python
def validate(form):
    problems = {}
    for field, rule in compiled.items():
        try:
            verdict = rule.run(Value.from_native(form)).as_text()
        except SelError as e:
            # E_ABORT is the rule speaking to the user; anything else is a
            # broken rule or data it cannot read -- log it, show a generic line.
            verdict = e.message if e.code == 'E_ABORT' else f'could not be checked ({e.code})'
        if verdict != '':
            problems[field] = verdict
    return problems
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/validation/js.mjs#validate -->
```js
function validate(form) {
  const problems = {};
  for (const [field, rule] of Object.entries(compiled)) {
    let verdict;
    try {
      verdict = rule.run(Value.fromNative(form)).asText();
    } catch (e) {
      if (!(e instanceof SelError)) throw e;
      // E_ABORT is the rule speaking to the user; anything else is a
      // broken rule or data it cannot read -- log it, show a generic line.
      verdict = e.code === 'E_ABORT' ? e.message : `could not be checked (${e.code})`;
    }
    if (verdict !== '') problems[field] = verdict;
  }
  return problems;
}
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/validation/php.php#validate -->
```php
function validate(array $compiled, array $form): array
{
    $problems = [];
    foreach ($compiled as $field => $rule) {
        try {
            $verdict = $rule->run(Value::fromNative($form))->asText();
        } catch (SelError $e) {
            // E_ABORT is the rule speaking to the user; anything else is a
            // broken rule or data it cannot read — log it, show a generic line.
            $verdict = $e->code === 'E_ABORT' ? $e->getMessage() : "could not be checked ({$e->code})";
        }
        if ($verdict !== '') {
            $problems[$field] = $verdict;
        }
    }
    return $problems;
}
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/validation/cpp.cpp#validate -->
```cpp
using Form = std::vector<std::pair<std::string, std::string>>;       // field, what was typed
using Problems = std::vector<std::pair<std::string, std::string>>;   // field, message

Problems validate(const Rules& compiled, const Form& form) {
  Problems problems;
  for (const auto& [field, rule] : compiled) {
    sel::Value ctx = sel::Value::none();
    for (const auto& [name, typed] : form) ctx.set(name, sel::Value::text(typed));
    std::string verdict;
    try {
      verdict = rule.run(ctx).as_text();
    } catch (const sel::SelError& e) {
      // E_ABORT is the rule speaking to the user; anything else is a
      // broken rule or data it cannot read -- log it, show a generic line.
      verdict = e.code() == "E_ABORT" ? e.message() : "could not be checked (" + e.code() + ")";
    }
    if (!verdict.empty()) problems.emplace_back(field, verdict);
  }
  return problems;
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/validation/lisp.lisp#validate -->
```lisp
(defun validate (form)
  "The problems with FORM, an alist of field values, as (field . message)."
  (loop for (field . rule) in *compiled*
        for verdict = (handler-case (sel:as-text (sel:run rule (sel:from-native form)))
                        (sel:sel-error (e)
                          ;; E_ABORT is the rule speaking to the user; anything
                          ;; else is a broken rule or data it cannot read — log
                          ;; it, show a generic line.
                          (if (string= (sel:sel-error-code e) "E_ABORT")
                              (sel:sel-error-message e)
                              (format nil "could not be checked (~a)" (sel:sel-error-code e)))))
        unless (string= verdict "")
          collect (cons field verdict)))
```

</details>
<!-- /tabs -->

## What it prints

<!-- from: examples/validation/output.txt -->
```text
1. the rule set
   name      reads NAME
   email     reads EMAIL
   postcode  reads COUNTRY POSTCODE
   quantity  reads QTY STOCK
   total     reads CREDIT_LIMIT DISCOUNT PRICE QTY
2. re-check on change
   COUNTRY       postcode
   CREDIT_LIMIT  total
   DISCOUNT      total
   EMAIL         email
   NAME          name
   POSTCODE      postcode
   PRICE         total
   QTY           quantity, total
   STOCK         quantity
3. submissions
   #1 accepted
   #2 name      Please tell us your name
   #2 email     bruno(at)example.de does not look like an e-mail address
   #2 postcode  German postcodes have five digits
   #2 quantity  Choose between 1 and 5
   #2 total     179.91 is over your limit of 100.00
   #3 total     113.96 is over your limit of 100.00
   #4 total     could not be checked (E_NOT_NUM)
```

Submission 3 shows why the rules use `TRIM(EMAIL)` and the `i` flag: the address
has upper-case letters and a trailing space and is still accepted. Submission 4
has a price that is not a number, and the `total` rule reports that it could not
be checked rather than inventing a verdict.

## Re-check only what changed

`dependencies()` lists the fields each rule reads, without running it. Inverted,
it is a map from a field to the rules that must run again when it changes —
section 2 of the output — so a browser re-validates the `total` rule when `QTY`
changes and leaves the `email` rule alone. This works because SEL has no way to
compute a variable's name at run time: what a rule reads is fixed when it is
written.

## Sending rules to the browser

Send the **source text**, the same strings the server compiled, and compile them
with the JavaScript host when the page loads. One artifact to version, and the
[conformance suite](../parity.md) is what guarantees that the browser's verdict
and the server's are the same one.
