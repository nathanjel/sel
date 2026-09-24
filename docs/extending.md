# Extending SEL

There are three ways to extend SEL, and they differ in who does it and what it
costs:

| | Who | Where it lives | Cost |
|---|---|---|---|
| [Host functions](#host-functions) | an application | the application's own code, at start-up | one call per function, in one language |
| [Extending the SQL layer](#extending-the-sql-layer) | an application | the application's own code, at start-up | one call per difference from a shipped dialect |
| [A builtin for everyone](#a-builtin-for-everyone) | a contributor | the specification, the suite and all five hosts | a change to the language |

The first two change nothing about SEL and need no one's agreement. The third is
how SEL itself grows — and why it grows slowly.

## Host functions

An application adds functions of its own — a stock lookup, a formatter, a
message queue — with one registration call. A registered function is called like
a builtin and is held to the same rules: strict, arity checked when a rule is
compiled, arguments read through the same typed readers, errors with positions.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/scripting/python.py#register -->
```python
def stock(args):
    return Value.int(inventory.get(args.text(0), 0))


def reserve(args):
    sku, qty = args.text(0), args.non_neg_int(1)
    if inventory.get(sku, 0) < qty:
        return Value.bool(False)
    inventory[sku] -= qty
    return Value.bool(True)


def weight(args):
    return Value.text(weights.get(args.text(0), '0'))


def notify(args):
    outbox.append(f'{args.text(0)}: {args.text(1)}')
    return Value.bool(True)


register_function('STOCK', 1, 1, stock)
register_function('RESERVE', 2, 2, reserve)
register_function('WEIGHT', 1, 1, weight)
register_function('NOTIFY', 2, 2, notify)
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/scripting/js.mjs#register -->
```js
function stock(args) {
  return Value.int(inventory.get(args.text(0)) ?? 0);
}

function reserve(args) {
  const sku = args.text(0), qty = args.nonNegInt(1);
  if ((inventory.get(sku) ?? 0) < qty) return Value.bool(false);
  inventory.set(sku, inventory.get(sku) - qty);
  return Value.bool(true);
}

function weight(args) {
  return Value.text(weights.get(args.text(0)) ?? '0');
}

function notify(args) {
  outbox.push(`${args.text(0)}: ${args.text(1)}`);
  return Value.bool(true);
}

registerFunction('STOCK', 1, 1, stock);
registerFunction('RESERVE', 2, 2, reserve);
registerFunction('WEIGHT', 1, 1, weight);
registerFunction('NOTIFY', 2, 2, notify);
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/scripting/php.php#register -->
```php
Sel::registerFunction('STOCK', 1, 1, function (Args $args) use (&$inventory): Value {
    return Value::int($inventory[$args->text(0)] ?? 0);
});

Sel::registerFunction('RESERVE', 2, 2, function (Args $args) use (&$inventory): Value {
    [$sku, $qty] = [$args->text(0), $args->nonNegInt(1)];
    if (($inventory[$sku] ?? 0) < $qty) {
        return Value::bool(false);
    }
    $inventory[$sku] -= $qty;
    return Value::bool(true);
});

Sel::registerFunction('WEIGHT', 1, 1, function (Args $args) use ($weights): Value {
    return Value::text($weights[$args->text(0)] ?? '0');
});

Sel::registerFunction('NOTIFY', 2, 2, function (Args $args) use (&$outbox): Value {
    $outbox[] = "{$args->text(0)}: {$args->text(1)}";
    return Value::bool(true);
});
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/scripting/cpp.cpp#register -->
```cpp
sel::register_function("STOCK", 1, 1, [](sel::HostArgs& args) {
  const auto found = inventory.find(args.text(0));
  return sel::Value::integer(found == inventory.end() ? 0 : found->second);
});

sel::register_function("RESERVE", 2, 2, [](sel::HostArgs& args) {
  const std::string sku = args.text(0);
  const long long qty = args.non_neg_int(1);
  const auto found = inventory.find(sku);
  if (found == inventory.end() || found->second < qty) return sel::Value::boolean(false);
  found->second -= qty;
  return sel::Value::boolean(true);
});

sel::register_function("WEIGHT", 1, 1, [](sel::HostArgs& args) {
  const auto found = weights.find(args.text(0));
  return sel::Value::text(found == weights.end() ? "0" : found->second);
});

sel::register_function("NOTIFY", 2, 2, [](sel::HostArgs& args) {
  outbox.push_back(args.text(0) + ": " + args.text(1));
  return sel::Value::boolean(true);
});
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/scripting/lisp.lisp#register -->
```lisp
(defun stock (args)
  (sel:make-int (gethash (sel:args-text args 0) *inventory* 0)))

(defun reserve (args)
  (let ((sku (sel:args-text args 0))
        (qty (sel:args-non-neg-int args 1)))
    (cond ((< (gethash sku *inventory* 0) qty)
           (sel:make-bool nil))
          (t
           (decf (gethash sku *inventory*) qty)
           (sel:make-bool t)))))

(defun weight (args)
  (sel:make-text (gethash (sel:args-text args 0) *weights* "0")))

(defun notify (args)
  (setf *outbox* (append *outbox* (list (format nil "~a: ~a"
                                                (sel:args-text args 0)
                                                (sel:args-text args 1)))))
  (sel:make-bool t))

(sel:register-function "STOCK" 1 1 #'stock)
(sel:register-function "RESERVE" 2 2 #'reserve)
(sel:register-function "WEIGHT" 1 1 #'weight)
(sel:register-function "NOTIFY" 2 2 #'notify)
```

</details>
<!-- /tabs -->

The rules, the same in every host ([spec §8.1](../spec/SPEC.md#81-host-functions)):

- **Register before compiling.** An unknown name is a compile-time error, so a
  rule that calls your function must be compiled after the registration.
- **Add, never change.** The name of a builtin, or a reserved word, is refused.
  Registering your own name again replaces the function for rules compiled
  afterwards; rules already compiled keep the one they were compiled with.
- **Strict.** Arguments are evaluated once, left to right, before your function
  runs; read them through the accessor (`text`, `bool`, `int`, `nonNegInt`, `val`
  and their spellings), which raises the usual error at the argument's own
  position.
- **Return a new value; do not modify the arguments.**
- **Fail with a code.** Raise the host's `SelError` with a code from the
  catalogue — `E_BAD_ARG` is usually right — and the argument's position; any
  other exception is the host's and passes through untouched.
- **Not SQL.** A host function has no SQL spelling, so a rule that calls it is
  refused by the translator and kept in memory by the planner.

[Scripting with host functions](usage/scripting.md) is a complete program built
this way.

## Extending the SQL layer

A deployment is rarely exactly one of the shipped dialects: a driver wants
numbered placeholders, a server lacks a function, an extension adds one. A
dialect is **registered, not forked** — the new one names its parent and states
only its differences, and everything else is inherited key by key.

### A dialect of your own

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/dialect/python.py#flavour -->
```python
map.define_dialect('pg-libpq', {
    'extends': 'postgresql',
    'version': '15',
    'target': True,                        # a base is not a target; this is a server
    'lexical': {'placeholder': '${n}'},    # libpq numbers its parameters
})
print('   targets      =>', ' '.join(Sql.dialects()))
print('   chain        =>', ' -> '.join(map.chain('pg-libpq')))
print('   base         =>', sql_in('postgresql'))
print('   pg-libpq     =>', sql_in('pg-libpq'))
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/dialect/js.mjs#flavour -->
```js
map.defineDialect('pg-libpq', {
  extends: 'postgresql',
  version: '15',
  target: true,                       // a base is not a target; this is a server
  lexical: { placeholder: '${n}' },   // libpq numbers its parameters
});
console.log('   targets      =>', Sql.dialects().join(' '));
console.log('   chain        =>', map.chain('pg-libpq').join(' -> '));
console.log('   base         =>', sqlIn('postgresql'));
console.log('   pg-libpq     =>', sqlIn('pg-libpq'));
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/dialect/php.php#flavour -->
```php
Map::defineDialect('pg-libpq', [
    'extends' => 'postgresql',
    'version' => '15',
    'target' => true,                         // a base is not a target; this is a server
    'lexical' => ['placeholder' => '${n}'],   // libpq numbers its parameters
]);
echo '   targets      => ', implode(' ', Sql::dialects()), "\n";
echo '   chain        => ', implode(' -> ', Map::chain('pg-libpq')), "\n";
echo '   base         => ', $sqlIn('postgresql'), "\n";
echo '   pg-libpq     => ', $sqlIn('pg-libpq'), "\n";
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/dialect/cpp.cpp#flavour -->
```cpp
Map::define_dialect("pg-libpq",
                    DialectSpec::extending("postgresql")
                        .version("15")
                        .target(true)                     // a base is not a target; this is a server
                        .lexical("placeholder", "${n}")); // libpq numbers its parameters
const std::string base = sql_in("postgresql");
const std::string libpq = sql_in("pg-libpq");
std::cout << "   targets      => " << join(Sql::dialects(), " ") << "\n";
std::cout << "   chain        => " << join(Map::chain("pg-libpq"), " -> ") << "\n";
std::cout << "   base         => " << base << "\n";
std::cout << "   pg-libpq     => " << libpq << "\n";
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/dialect/lisp.lisp#flavour -->
```lisp
(sel.sql:define-dialect "pg-libpq"
  ;; :TARGET T because a base is not a target; this is a server. The
  ;; lexical map is keyed by strings, which are the map document's own
  ;; key names rather than this host's vocabulary.
  '(:extends "postgresql"
    :version "15"
    :target t
    :lexical (("placeholder" . "${n}"))))   ; libpq numbers its parameters
(format t "   targets      => ~{~a~^ ~}~%" (sel.sql:dialects))
(format t "   chain        => ~{~a~^ -> ~}~%" (sel.sql:dialect-chain "pg-libpq"))
(format t "   base         => ~a~%" (sql-in "postgresql"))
(format t "   pg-libpq     => ~a~%" (sql-in "pg-libpq"))
```

</details>
<!-- /tabs -->

### Spelling a function differently

A template's `{0}`, `{1}`, … are the arguments (zero-based: these are template
holes, not SEL positions) and `{*}` all of them. An entry says what it returns,
because the translator infers kinds and will not guess.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/dialect/python.py#respell -->
```python
map.define('pg-libpq', 'funcs', 'UPPER', {'tpl': 'UPPER({0} COLLATE "C")', 'ret': 'TEXT'})
print('   upper        =>',
      Sql.translate(compile('UPPER(NAME)'), 'pg-libpq', bindings).as_value())
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/dialect/js.mjs#respell -->
```js
map.define('pg-libpq', 'funcs', 'UPPER', { tpl: 'UPPER({0} COLLATE "C")', ret: 'TEXT' });
console.log('   upper        =>',
  Sql.translate(compile('UPPER(NAME)'), 'pg-libpq', bindings).asValue());
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/dialect/php.php#respell -->
```php
Map::define('pg-libpq', 'funcs', 'UPPER', ['tpl' => 'UPPER({0} COLLATE "C")', 'ret' => 'TEXT']);
echo '   upper        => ',
    Sql::translate(Sel::compile('UPPER(NAME)'), 'pg-libpq', $bindings)->asValue(), "\n";
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/dialect/cpp.cpp#respell -->
```cpp
Map::define("pg-libpq", Section::Funcs, "UPPER",
            EntrySpec::tpl("UPPER({0} COLLATE \"C\")", "TEXT"));
const std::string upper =
    Sql::translate(sel::compile("UPPER(NAME)"), "pg-libpq", bindings).as_value();
std::cout << "   upper        => " << upper << "\n";
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/dialect/lisp.lisp#respell -->
```lisp
(sel.sql:define-entry "pg-libpq" :funcs "UPPER"
                      '(:tpl "UPPER({0} COLLATE \"C\")" :ret "TEXT"))
(format t "   upper        => ~a~%"
        (sel.sql:as-value
         (sel.sql:translate (sel:compile-source "UPPER(NAME)") "pg-libpq" bindings)))
```

</details>
<!-- /tabs -->

### Withdrawing what a server does not have

An entry of *nothing* withdraws the function: a rule using it is refused on this
dialect, rather than emitted against a function the server lacks.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/dialect/python.py#withdraw -->
```python
map.define('pg-libpq', 'funcs', 'RMATCH', None)
re = compile('RMATCH(\'^a\', NAME)')
print('   postgresql   =>', 'refused' if Sql.try_translate(re, 'postgresql', bindings)
      is None else 'translated')
print('   pg-libpq     =>', 'refused' if Sql.try_translate(re, 'pg-libpq', bindings)
      is None else 'translated')
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/dialect/js.mjs#withdraw -->
```js
map.define('pg-libpq', 'funcs', 'RMATCH', null);
const re = compile('RMATCH(\'^a\', NAME)');
console.log('   postgresql   =>', Sql.tryTranslate(re, 'postgresql', bindings) === null
  ? 'refused' : 'translated');
console.log('   pg-libpq     =>', Sql.tryTranslate(re, 'pg-libpq', bindings) === null
  ? 'refused' : 'translated');
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/dialect/php.php#withdraw -->
```php
Map::define('pg-libpq', 'funcs', 'RMATCH', null);
$re = Sel::compile("RMATCH('^a', NAME)");
echo '   postgresql   => ', Sql::tryTranslate($re, 'postgresql', $bindings) === null
    ? 'refused' : 'translated', "\n";
echo '   pg-libpq     => ', Sql::tryTranslate($re, 'pg-libpq', $bindings) === null
    ? 'refused' : 'translated', "\n";
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/dialect/cpp.cpp#withdraw -->
```cpp
Map::define("pg-libpq", Section::Funcs, "RMATCH", EntrySpec::withdraw());
const sel::Program re = sel::compile("RMATCH('^a', NAME)");
std::cout << "   postgresql   => "
          << (Sql::try_translate(re, "postgresql", bindings).has_value()
                  ? "translated"
                  : "refused")
          << "\n";
std::cout << "   pg-libpq     => "
          << (Sql::try_translate(re, "pg-libpq", bindings).has_value()
                  ? "translated"
                  : "refused")
          << "\n";
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/dialect/lisp.lisp#withdraw -->
```lisp
(sel.sql:define-entry "pg-libpq" :funcs "RMATCH" nil)
(let ((re (sel:compile-source "RMATCH('^a', NAME)")))
  (format t "   postgresql   => ~a~%"
          (if (null (sel.sql:try-translate re "postgresql" bindings))
              "refused" "translated"))
  (format t "   pg-libpq     => ~a~%"
          (if (null (sel.sql:try-translate re "pg-libpq" bindings))
              "refused" "translated")))
```

</details>
<!-- /tabs -->

### A builder, for what a template cannot say

A builder receives the emitter and the arguments already rendered, and returns a
fragment. Splice the arguments' *parts* rather than their text, so a bound value
stays bound.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/dialect/python.py#builder -->
```python
map.define_builder('pg-libpq', 'funcs', 'LEN', lambda emit, args, _at:
                   Fragment(['length(', *args[0].parts, ')'], 'NUM', emit.dialect()))
print('   len          =>',
      Sql.translate(compile('LEN(NAME)'), 'pg-libpq', bindings).as_value())
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/dialect/js.mjs#builder -->
```js
map.defineBuilder('pg-libpq', 'funcs', 'LEN', (emit, args) =>
  new Fragment(['length(', ...args[0].parts, ')'], 'NUM', emit.dialect()));
console.log('   len          =>',
  Sql.translate(compile('LEN(NAME)'), 'pg-libpq', bindings).asValue());
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/dialect/php.php#builder -->
```php
Map::defineBuilder('pg-libpq', 'funcs', 'LEN', fn (Emit $emit, array $args) =>
    new Fragment(['length(', ...$args[0]->parts, ')'], 'NUM', $emit->dialect()));
echo '   len          => ',
    Sql::translate(Sel::compile('LEN(NAME)'), 'pg-libpq', $bindings)->asValue(), "\n";
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/dialect/cpp.cpp#builder -->
```cpp
Map::define_builder(
    "pg-libpq", Section::Funcs, "LEN",
    std::make_shared<Builder>(
        [](Emit& emit, std::span<const Fragment> args, Pos) {
          std::vector<Fragment::Part> parts;
          parts.push_back({.sql = "length("});
          for (const Fragment::Part& part : args[0].parts()) parts.push_back(part);
          parts.push_back({.sql = ")"});
          return Fragment(std::move(parts), SqlKind::Num, emit.dialect());
        }));
const std::string len =
    Sql::translate(sel::compile("LEN(NAME)"), "pg-libpq", bindings).as_value();
std::cout << "   len          => " << len << "\n";
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/dialect/lisp.lisp#builder -->
```lisp
(sel.sql:define-builder
 "pg-libpq" :funcs "LEN"
 (lambda (dialect args pos)
   (declare (ignore pos))
   (sel.sql::%fragment (append (list "length(")
                               (sel.sql:fragment-parts (first args))
                               (list ")"))
                       :num dialect)))
(format t "   len          => ~a~%"
        (sel.sql:as-value
         (sel.sql:translate (sel:compile-source "LEN(NAME)") "pg-libpq" bindings)))
```

</details>
<!-- /tabs -->

Registrations are checked against the same rules the shipped map is held to
([sql/MAP.md](../sql/MAP.md)), and a malformed one is a start-up error of the
host, never an SQL refusal. `map.reset()` and its spellings drop every
registration — worth calling between tests.

## A builtin for everyone

A function in SEL itself is a change to the language, and it arrives in all five
hosts at once or not at all — a function in one host is a function nobody has
compared with anything. The order of work:

```
spec/SPEC.md §7 and spec/builtins.json    say what it does, and its arity
conformance/*.selt                        cases that fail
js/ php/ python/ cpp/ lisp/               implement, in that order or any other
node tools/gen-builtins.mjs               render the manifest into every host
sql/dialects/*.json + sql/cases/*.sqlt    a SQL spelling, if it has an exact one
tools/check.sh                            ALL GREEN, or it isn't done
```

Most functions are **strict** — they receive values, and the argument accessor
does the arity, type and position work, so a function is a few lines per host.
A function that must *not* evaluate something — a branch, a body per element —
is **lazy** and receives syntax. Both are worked end to end, in all five hosts, in
[Contributing](contributing.md#adding-a-function), with the reference fragments in
[`examples/fn-simple`](../examples/fn-simple/) and
[`examples/fn-complex`](../examples/fn-complex/); giving a builtin a SQL spelling
is [`examples/fn-sql`](../examples/fn-sql/).

Adding an operator, and adding a sixth host, are in
[Contributing](contributing.md) too.
