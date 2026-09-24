# Scripting with host functions

SEL cannot reach the world on its own — no files, no network, no clock — and
that is deliberate: the application decides what a script may touch, by
registering functions. A registered function is called like a builtin, receives
its arguments evaluated and typed-checked by the same readers the builtins use,
and returns a SEL value.

That turns SEL into a small, safe scripting language for an application's
*policy*. Here a warehouse registers four functions — `STOCK` and `WEIGHT` read
the catalogue, `RESERVE` takes stock, `NOTIFY` queues a message — and a script
the warehouse team owns decides, for each order, whether to ship it, how, and
whom to tell.

## Registering functions

One call per function: a name, the least and most arguments it takes, and the
code. Register before compiling the scripts that call them — an unknown name is a
compile-time error.

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

The rules are the same in every host ([spec §8.1](../../spec/SPEC.md#81-host-functions)):

- A host function **adds** to the language: the name of a builtin, or a reserved
  word, is refused. Registering the same name again replaces the host function
  for programs compiled afterwards.
- It is **strict**: its arguments are evaluated once, left to right, before it
  runs. The typed readers — `text`, `bool`, `int`, `nonNegInt` and their
  spellings — raise the usual error at *that argument's* position, so a script
  that passes the wrong kind gets a message pointing at its own mistake.
- It returns a **new** value and must not modify its arguments.
- To fail on purpose, raise a `SelError` with a code from the catalogue and the
  argument's position (`args.posOf(i)` and its spellings); anything else it
  throws is the host's own exception and passes through unchanged.

## The script

[`fulfil.sel`](../../examples/scripting/fulfil.sel) is plain text that every host
reads and compiles:

<!-- from: examples/scripting/fulfil.sel -->
```sel
# What happens to one order. The warehouse team edits this file; the
# application never changes when it does.
#
# STOCK, RESERVE, WEIGHT and NOTIFY are not SEL builtins: the application
# registers them (examples/scripting/*), and they are how the script reaches
# the inventory and the outbox. ORDER, COUNTRY and ITEMS are the order itself.

MISSING = ITEMS .> FILTER(STOCK(_["sku"]) < _["qty"]);

IF(COUNT(MISSING) > 0,
   (NOTIFY("purchasing", "order {ORDER} waits for " & JOIN(MAP(MISSING, _["sku"]), ", "));
    "backorder"),
   (ALL(ITEMS, RESERVE(_["sku"], _["qty"]));
    KG = SUM(ITEMS, WEIGHT(_["sku"]) * _["qty"]);
    CARRIER = COND(COUNTRY $== "PL" AND KG <= 25, "inpost",
                   KG > 30, "freight",
                   "courier");
    NOTIFY("customer", "order {ORDER} ships by {CARRIER}, {KG} kg");
    "ship by " & CARRIER))
```

It computes (`MISSING`, `KG`), decides (`IF`, `COND`), and acts through the
host's functions — and its inputs are exactly what `dependencies()` says:
`COUNTRY`, `ITEMS` and `ORDER`. Nothing else is reachable.

## Running it

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/scripting/python.py#run -->
```python
with open(os.path.join(HERE, 'fulfil.sel'), encoding='utf-8') as fh:
    fulfil = compile(fh.read())             # after registering: names resolve now

print('1. the script reads', ', '.join(fulfil.dependencies()))
print('2. orders')
orders = [
    {'ORDER': 'A-1', 'COUNTRY': 'PL', 'ITEMS': [{'sku': 'LAMP-01', 'qty': '2'},
                                               {'sku': 'CHAIR-03', 'qty': '1'}]},
    {'ORDER': 'A-2', 'COUNTRY': 'DE', 'ITEMS': [{'sku': 'DESK-02', 'qty': '1'},
                                               {'sku': 'CHAIR-03', 'qty': '2'}]},
    {'ORDER': 'A-3', 'COUNTRY': 'PL', 'ITEMS': [{'sku': 'LAMP-01', 'qty': '3'}]},
    {'ORDER': 'A-4', 'COUNTRY': 'PL', 'ITEMS': [{'sku': 'LAMP-01', 'qty': 'two'}]},
]
for order in orders:
    try:
        decision = fulfil.run(Value.from_native(order)).as_text()
    except SelError as e:
        decision = f'{e.code} at {e.line}:{e.col}'
    print(f'   {order["ORDER"]}  {decision}')
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/scripting/js.mjs#run -->
```js
const source = readFileSync(new URL('fulfil.sel', import.meta.url), 'utf8');
const fulfil = compile(source);             // after registering: names resolve now

console.log('1. the script reads', fulfil.dependencies().join(', '));
console.log('2. orders');
const orders = [
  { ORDER: 'A-1', COUNTRY: 'PL', ITEMS: [{ sku: 'LAMP-01', qty: '2' },
                                         { sku: 'CHAIR-03', qty: '1' }] },
  { ORDER: 'A-2', COUNTRY: 'DE', ITEMS: [{ sku: 'DESK-02', qty: '1' },
                                         { sku: 'CHAIR-03', qty: '2' }] },
  { ORDER: 'A-3', COUNTRY: 'PL', ITEMS: [{ sku: 'LAMP-01', qty: '3' }] },
  { ORDER: 'A-4', COUNTRY: 'PL', ITEMS: [{ sku: 'LAMP-01', qty: 'two' }] },
];
for (const order of orders) {
  let decision;
  try {
    decision = fulfil.run(Value.fromNative(order)).asText();
  } catch (e) {
    if (!(e instanceof SelError)) throw e;
    decision = `${e.code} at ${e.line}:${e.col}`;
  }
  console.log(`   ${order.ORDER}  ${decision}`);
}
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/scripting/php.php#run -->
```php
$fulfil = Sel::compile(file_get_contents(__DIR__ . '/fulfil.sel'));   // after registering: names resolve now

echo '1. the script reads ', implode(', ', $fulfil->dependencies()), "\n";
echo "2. orders\n";
$orders = [
    ['ORDER' => 'A-1', 'COUNTRY' => 'PL', 'ITEMS' => [['sku' => 'LAMP-01', 'qty' => '2'],
                                                      ['sku' => 'CHAIR-03', 'qty' => '1']]],
    ['ORDER' => 'A-2', 'COUNTRY' => 'DE', 'ITEMS' => [['sku' => 'DESK-02', 'qty' => '1'],
                                                      ['sku' => 'CHAIR-03', 'qty' => '2']]],
    ['ORDER' => 'A-3', 'COUNTRY' => 'PL', 'ITEMS' => [['sku' => 'LAMP-01', 'qty' => '3']]],
    ['ORDER' => 'A-4', 'COUNTRY' => 'PL', 'ITEMS' => [['sku' => 'LAMP-01', 'qty' => 'two']]],
];
foreach ($orders as $order) {
    try {
        $decision = $fulfil->run(Value::fromNative($order))->asText();
    } catch (SelError $e) {
        $decision = "{$e->code} at {$e->line}:{$e->col}";
    }
    echo "   {$order['ORDER']}  $decision\n";
}
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/scripting/cpp.cpp#run -->
```cpp
// After registering: names resolve now.
const sel::Program fulfil = sel::compile(read("examples/scripting/fulfil.sel"));

std::cout << "1. the script reads " << join(fulfil.dependencies(), ", ") << "\n";
std::cout << "2. orders\n";
const std::vector<Order> orders = {
    {"A-1", "PL", {{"LAMP-01", "2"}, {"CHAIR-03", "1"}}},
    {"A-2", "DE", {{"DESK-02", "1"}, {"CHAIR-03", "2"}}},
    {"A-3", "PL", {{"LAMP-01", "3"}}},
    {"A-4", "PL", {{"LAMP-01", "two"}}},
};
for (const Order& order : orders) {
  sel::Value ctx = sel::Value::none();
  ctx.set("ORDER", sel::Value::text(order.order));
  ctx.set("COUNTRY", sel::Value::text(order.country));
  std::vector<sel::Value> items;
  for (const Line& line : order.items) {
    sel::Value item = sel::Value::none();
    item.set("sku", sel::Value::text(line.sku));
    item.set("qty", sel::Value::text(line.qty));
    items.push_back(item);
  }
  ctx.set("ITEMS", sel::Value::list(items));
  std::string decision;
  try {
    decision = fulfil.run(ctx).as_text();
  } catch (const sel::SelError& e) {
    decision = e.code() + " at " + std::to_string(e.line()) + ":" + std::to_string(e.col());
  }
  std::cout << "   " << order.order << "  " << decision << "\n";
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/scripting/lisp.lisp#run -->
```lisp
(let ((fulfil (sel:compile-source      ; after registering: names resolve now
               (uiop:read-file-string (merge-pathnames "fulfil.sel" *here*)
                                      :external-format :utf-8))))
  (format t "1. the script reads ~{~a~^, ~}~%" (sel:dependencies fulfil))
  (format t "2. orders~%")
  (dolist (order '((("ORDER" . "A-1") ("COUNTRY" . "PL")
                    ("ITEMS" . ((("sku" . "LAMP-01") ("qty" . "2"))
                                (("sku" . "CHAIR-03") ("qty" . "1")))))
                   (("ORDER" . "A-2") ("COUNTRY" . "DE")
                    ("ITEMS" . ((("sku" . "DESK-02") ("qty" . "1"))
                                (("sku" . "CHAIR-03") ("qty" . "2")))))
                   (("ORDER" . "A-3") ("COUNTRY" . "PL")
                    ("ITEMS" . ((("sku" . "LAMP-01") ("qty" . "3")))))
                   (("ORDER" . "A-4") ("COUNTRY" . "PL")
                    ("ITEMS" . ((("sku" . "LAMP-01") ("qty" . "two")))))))
    (let ((decision
            (handler-case (sel:as-text (sel:run fulfil (sel:from-native order)))
              (sel:sel-error (e)
                (format nil "~a at ~D:~D"
                        (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e))))))
      (format t "   ~a  ~a~%" (cdr (assoc "ORDER" order :test #'string=)) decision))))
```

</details>
<!-- /tabs -->

## What it prints

<!-- from: examples/scripting/output.txt -->
```text
1. the script reads COUNTRY, ITEMS, ORDER
2. orders
   A-1  ship by inpost
   A-2  ship by freight
   A-3  backorder
   A-4  E_NOT_NUM at 8:46
3. outbox
   customer: order A-1 ships by inpost, 10.7 kg
   customer: order A-2 ships by freight, 43.0 kg
   purchasing: order A-3 waits for LAMP-01
4. stock left
   CHAIR-03  3
   DESK-02   0
   LAMP-01   2
```

Order A-3 finds the lamps already reserved by A-1 and is put on back order;
order A-4 has a quantity that is not a number, and the error names the script's
line and column — `STOCK(_["sku"]) < _["qty"]`, where `"two"` met `<`.

## Host functions and SQL

A host function has no SQL spelling — the dialect map spells SEL's own
functions only — so the SQL translator refuses a rule that calls it
(`E_SQL_UNSUPPORTED`), and the hybrid planner keeps the steps that call it in
memory: the database does what it can, and the host function runs over the rows
that come back. The [SQL pipelines](sql-pipelines.md#where-the-planner-splits-and-why)
page shows where such a split lands.
