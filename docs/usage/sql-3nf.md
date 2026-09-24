# Third normal form

The schema an application usually has: every fact stored once, and a question
answered by joining the tables that hold its parts. This page uses one in
**PostgreSQL** — first for a question SQL can answer whole, then for one that
needs something only the application has.

## The schema

```text
categories                products                          order_lines
  category_id PK  <─────    product_id   PK   <───────────    order_id    → orders
  category        UNIQUE    sku          UNIQUE               line_no
                            title                            product_id  → products
customers                   category_id  → categories        qty         INTEGER
  customer_id  PK  <──┐     list_price   NUMERIC(10,2)        unit_price  NUMERIC(10,2)
  name                │                                           │
  email        UNIQUE │   orders                                  │
  country             └──   customer_id  → customers              │
                            order_id     PK   <───────────────────┘
                            status       paid|pending|cancelled
                            ordered_on   DATE
```

Thirty orders from eight customers, one to three lines each, some at a discounted
unit price. Key columns carry their table's name (`order_id`, not `id`), which is
what lets two joined rows share no field but their key. The seed is
[`examples/sql-3nf/seed.postgresql.sql`](../../examples/sql-3nf/seed.postgresql.sql).

## Describing it

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-3nf/python.py#bindings -->
```python
def relation(table, alias, **fields):
    return Binding.relation(table, alias, fields={
        name: Binding.column(name, alias, kind) for name, kind in fields.items()})


SCHEMA = {
    'CUSTOMERS': relation('customers', 'c', customer_id='NUM', name='TEXT', email='TEXT',
                          country='TEXT'),
    'ORDERS':    relation('orders', 'o', order_id='NUM', customer_id='NUM', status='TEXT',
                          ordered_on='TEXT'),
    'LINES':     relation('order_lines', 'l', order_id='NUM', line_no='NUM', product_id='NUM',
                          qty='NUM', unit_price='NUM'),
    'PRODUCTS':  relation('products', 'p', product_id='NUM', sku='TEXT', title='TEXT',
                          category_id='NUM', list_price='NUM'),
}
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-3nf/js.mjs#bindings -->
```js
function relation(table, alias, fields) {
  return Binding.relation(table, alias, Object.fromEntries(Object.entries(fields)
    .map(([name, kind]) => [name, Binding.column(name, alias, kind)])));
}

const SCHEMA = {
  CUSTOMERS: relation('customers', 'c', { customer_id: 'NUM', name: 'TEXT', email: 'TEXT',
                                          country: 'TEXT' }),
  ORDERS:    relation('orders', 'o', { order_id: 'NUM', customer_id: 'NUM', status: 'TEXT',
                                       ordered_on: 'TEXT' }),
  LINES:     relation('order_lines', 'l', { order_id: 'NUM', line_no: 'NUM', product_id: 'NUM',
                                            qty: 'NUM', unit_price: 'NUM' }),
  PRODUCTS:  relation('products', 'p', { product_id: 'NUM', sku: 'TEXT', title: 'TEXT',
                                         category_id: 'NUM', list_price: 'NUM' }),
};
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-3nf/php.php#bindings -->
```php
function relation(string $table, string $alias, array $fields): Binding
{
    $columns = [];
    foreach ($fields as $name => $kind) {
        $columns[$name] = Binding::column($name, $alias, $kind);
    }
    return Binding::relation($table, $alias, fields: $columns);
}

$schema = [
    'CUSTOMERS' => relation('customers', 'c', ['customer_id' => 'NUM', 'name' => 'TEXT',
                                               'email' => 'TEXT', 'country' => 'TEXT']),
    'ORDERS'    => relation('orders', 'o', ['order_id' => 'NUM', 'customer_id' => 'NUM',
                                            'status' => 'TEXT', 'ordered_on' => 'TEXT']),
    'LINES'     => relation('order_lines', 'l', ['order_id' => 'NUM', 'line_no' => 'NUM',
                                                 'product_id' => 'NUM', 'qty' => 'NUM',
                                                 'unit_price' => 'NUM']),
    'PRODUCTS'  => relation('products', 'p', ['product_id' => 'NUM', 'sku' => 'TEXT',
                                              'title' => 'TEXT', 'category_id' => 'NUM',
                                              'list_price' => 'NUM']),
];
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-3nf/cpp.cpp#bindings -->
```cpp
Binding relation(const std::string& table, const std::string& alias,
                 const std::vector<std::pair<std::string, SqlKind>>& fields) {
  std::vector<std::pair<std::string, Binding>> columns;
  for (const auto& [name, kind] : fields) columns.emplace_back(name, Binding::column(name, alias, kind));
  return Binding::relation(table, alias, columns);
}

Bindings schema() {
  const SqlKind NUM = SqlKind::Num, TEXT = SqlKind::Text;
  return Bindings({
      {"CUSTOMERS", relation("customers", "c", {{"customer_id", NUM}, {"name", TEXT},
                                                {"email", TEXT}, {"country", TEXT}})},
      {"ORDERS",    relation("orders", "o", {{"order_id", NUM}, {"customer_id", NUM},
                                             {"status", TEXT}, {"ordered_on", TEXT}})},
      {"LINES",     relation("order_lines", "l", {{"order_id", NUM}, {"line_no", NUM},
                                                  {"product_id", NUM}, {"qty", NUM},
                                                  {"unit_price", NUM}})},
      {"PRODUCTS",  relation("products", "p", {{"product_id", NUM}, {"sku", TEXT},
                                               {"title", TEXT}, {"category_id", NUM},
                                               {"list_price", NUM}})},
  });
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-3nf/lisp.lisp#bindings -->
```lisp
(defun relation (table alias &rest fields)
  "A table as a relation binding. FIELDS alternate a column name and its kind."
  (sel.sql:binding-relation
   table alias
   (loop for (name kind) on fields by #'cddr
         collect (cons name (sel.sql:binding-column name alias kind)))))

(defparameter *schema*
  (list (cons "CUSTOMERS" (relation "customers" "c" "customer_id" :num "name" :text
                                    "email" :text "country" :text))
        (cons "ORDERS"    (relation "orders" "o" "order_id" :num "customer_id" :num
                                    "status" :text "ordered_on" :text))
        (cons "LINES"     (relation "order_lines" "l" "order_id" :num "line_no" :num
                                    "product_id" :num "qty" :num "unit_price" :num))
        (cons "PRODUCTS"  (relation "products" "p" "product_id" :num "sku" :text
                                    "title" :text "category_id" :num "list_price" :num))))
```

</details>
<!-- /tabs -->

`ordered_on` is a `DATE` in PostgreSQL and `TEXT` to SEL: the runner hands every
column over as text, and an ISO date compares correctly as text.

## The pipelines

**Paid revenue per product since March.** Order lines are the hub: both joins key
on a field of `LINES`, the filter reads the order, the group reads the product:

<!-- from: examples/sql-3nf/revenue-per-product.sel -->
```sel
# Units and revenue per product, from paid orders placed since March.
LINES
  .> LINK(ORDERS, L, O, L["order_id"] == O["order_id"])
  .> FILTER(_["status"] $== "paid" AND _["ordered_on"] $>= "2025-03-01")
  .> LINK(PRODUCTS, X, P, X["product_id"] == P["product_id"])
  .> BUCKET(_["title"], RECORD(
       "product", _K,
       "units", SUM(_, _["qty"]),
       "revenue", SUM(_, _["qty"] * _["unit_price"])))
  .> SORT_BY(_["revenue"], "DESC")
```

**Paid revenue per experiment cohort.** The application assigns each customer to
cohort A or B by the CRC32 of their e-mail — its own hashing, which it also uses
wherever the cohort decides what a customer sees. PostgreSQL has no CRC32, so the
database joins, filters and multiplies, and SEL hashes, groups and sums:

<!-- from: examples/sql-3nf/revenue-per-cohort.sel -->
```sel
# Paid revenue per A/B cohort, the cohort being the application's hash of the customer's e-mail.
ORDERS
  .> FILTER(_["status"] $== "paid")
  .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["customer_id"])
  .> LINK(LINES, X, L, X["order_id"] == L["order_id"])
  .> MAP(RECORD("email", _["email"], "amount", _["qty"] * _["unit_price"]))
  .> MAP(RECORD("cohort", IF(RMATCH('[0-7]$', CRC32(_["email"])), "A", "B"),
                "amount", _["amount"]))
  .> BUCKET(_["cohort"], RECORD("cohort", _K, "lines", COUNT(_), "revenue", SUM(_, _["amount"])))
  .> SORT_BY(_["cohort"])
```

## Running them

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-3nf/python.py#run -->
```python
with open(os.path.join(HERE, file), encoding='utf-8') as fh:
    program = compile(fh.read())
plan = plan_hybrid(program, 'postgresql', SCHEMA)
rows = execute_hybrid(plan, runner(conn), tables if plan.pure_memory else None)
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-3nf/js.mjs#run -->
```js
const program = compile(readFileSync(new URL(file, import.meta.url), 'utf8'));
const plan = planHybrid(program, 'postgresql', SCHEMA);
const rows = executeHybrid(plan, await runner(conn, plan), plan.pureMemory ? tables : null);
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-3nf/php.php#run -->
```php
$program = Sel::compile(file_get_contents(__DIR__ . '/' . $file));
$plan = Sql::planHybrid($program, 'postgresql', $schema);
$rows = Sql::executeHybrid($plan, runner($conn), $plan->pureMemory ? $tables : null);
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-3nf/cpp.cpp#run -->
```cpp
const sel::Program program = sel::compile(read("examples/sql-3nf/" + file));
const HybridPlan plan = Sql::plan_hybrid(program, "postgresql", SCHEMA);
const sel::Value rows = Sql::execute_hybrid(plan, db::runner(conn),
                                            plan.pure_memory ? tables : sel::Value::none());
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-3nf/lisp.lisp#run -->
```lisp
(defun run-pipeline (file conn tables)
  "Run the pipeline in FILE with as much of it in PostgreSQL as PostgreSQL can answer.
Returns the rows, the plan and the compiled program."
  (let* ((program (sel:compile-source (read-file file)))
         (plan (sel.sql:plan-hybrid program "postgresql" *schema*))
         (rows (sel.sql:execute-hybrid plan (sel-db:runner conn)
                                       (when (sel.sql:hybrid-plan-pure-memory-p plan) tables))))
    (values rows plan program)))
```

</details>
<!-- /tabs -->

## What it prints

<!-- from: examples/sql-3nf/output.txt -->
```text
1. paid revenue per product since March
   plan        pure_sql
   reads       order_lines, orders, products
   sql         SELECT CAST("p"."title" AS TEXT) COLLATE "C" AS "product", COALESCE(SUM("l"."qty"), 0) AS "units", COALESCE(SUM((CAST("l"."qty" AS NUMERIC) * CAST("l"."unit_price" AS NUMERIC))), 0) AS "revenue" FROM "order_lines" "l" INNER JOIN "orders" "o" ON ("l"."order_id" = "o"."order_id") INNER JOIN "products" "p" ON ("l"."product_id" = "p"."product_id") WHERE ((CAST("o"."status" AS TEXT) COLLATE "C" = CAST('paid' AS TEXT) COLLATE "C") AND (CAST("o"."ordered_on" AS TEXT) COLLATE "C" >= CAST('2025-03-01' AS TEXT) COLLATE "C")) GROUP BY CAST("p"."title" AS TEXT) COLLATE "C" ORDER BY COALESCE(SUM((CAST("l"."qty" AS NUMERIC) * CAST("l"."unit_price" AS NUMERIC))), 0) DESC
   | product=Harbour Lights  units=15  revenue=1949.85
   | product=Cardinal Rules  units=15  revenue=892.50
   | product=Oak Planter  units=10  revenue=740.00
   | product=The Pragmatic Garden  units=25  revenue=622.50
   | product=Decimal Tales  units=15  revenue=585.00
   in memory   same rows
2. paid revenue per experiment cohort
   plan        hybrid
   reads       orders, customers, order_lines
   sql         SELECT "c"."email" AS "email", (CAST("l"."qty" AS NUMERIC) * CAST("l"."unit_price" AS NUMERIC)) AS "amount" FROM "orders" "o" INNER JOIN "customers" "c" ON ("o"."customer_id" = "c"."customer_id") INNER JOIN "order_lines" "l" ON ("o"."order_id" = "l"."order_id") WHERE (CAST("o"."status" AS TEXT) COLLATE "C" = CAST('paid' AS TEXT) COLLATE "C")
   | cohort=A  lines=13  revenue=1614.34
   | cohort=B  lines=27  revenue=3300.01
   in memory   same rows
```

The first plan is `pure_sql`: a three-table join, the `WHERE` on the order's
status and date, the group on the product's title, and the sum of `qty *
unit_price` — exact `NUMERIC` arithmetic, so `SUM` gives the same cents SEL's
decimals give.

The second is `hybrid`, split at the second `MAP`: the database returns each paid
line's e-mail and amount, and the cohort, the grouping and the sums run in memory.
The hash is SEL's `CRC32`, identical in all five hosts — the same cohort the
application computes wherever else it asks.
