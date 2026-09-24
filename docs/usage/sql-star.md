# Star schema

A fact table of sales in the middle, and dimensions around it — the shape of a
data warehouse. It suits SQL well: most questions are joins from the fact table
to a dimension or two, a filter, and a `GROUP BY`. This page asks two questions of
it in **PostgreSQL**; one goes to SQL whole, the other needs memory for its last
step.

## The schema

```text
                 dim_date                        dim_product
                 date_key   INTEGER PK           product_key  INTEGER PK
                 year, quarter, month INTEGER    sku, name    TEXT
                 month_name TEXT                 category     TEXT   books|games|garden|kitchen
                      │                          brand        TEXT
                      │                          list_price   NUMERIC(10,2)
                      │                               │
fact_sales ───────────┴───────────────────────────────┘
  sale_id      INTEGER PK
  date_key     → dim_date          dim_store
  product_key  → dim_product       store_key  INTEGER PK
  store_key    → dim_store  ─────  city       TEXT   Kraków, Warszawa, Gdańsk, Łódź, Internet
  qty          INTEGER             region     TEXT   South, Central, North, Online
  revenue      NUMERIC(12,2)       format     TEXT   mall|street|outlet|web
```

72 sales over the first half of 2025, eight products, five stores. The seed is
[`examples/sql-star/seed.postgresql.sql`](../../examples/sql-star/seed.postgresql.sql).

## Describing it

Each table is one relation binding: the table, the alias the SQL will use, and a
typed binding per field. A helper keeps it to a line per table.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-star/python.py#bindings -->
```python
def relation(table, alias, **fields):
    return Binding.relation(table, alias, fields={
        name: Binding.column(name, alias, kind) for name, kind in fields.items()})


SCHEMA = {
    'SALES':    relation('fact_sales', 's', sale_id='NUM', date_key='NUM', product_key='NUM',
                         store_key='NUM', qty='NUM', revenue='NUM'),
    'DATES':    relation('dim_date', 'd', date_key='NUM', year='NUM', quarter='NUM',
                         month='NUM', month_name='TEXT'),
    'STORES':   relation('dim_store', 't', store_key='NUM', city='TEXT', region='TEXT',
                         format='TEXT'),
    'PRODUCTS': relation('dim_product', 'p', product_key='NUM', sku='TEXT', name='TEXT',
                         category='TEXT', brand='TEXT', list_price='NUM'),
}
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-star/js.mjs#bindings -->
```js
function relation(table, alias, fields) {
  return Binding.relation(table, alias, Object.fromEntries(Object.entries(fields)
    .map(([name, kind]) => [name, Binding.column(name, alias, kind)])));
}

const SCHEMA = {
  SALES:    relation('fact_sales', 's', { sale_id: 'NUM', date_key: 'NUM', product_key: 'NUM',
                                          store_key: 'NUM', qty: 'NUM', revenue: 'NUM' }),
  DATES:    relation('dim_date', 'd', { date_key: 'NUM', year: 'NUM', quarter: 'NUM',
                                        month: 'NUM', month_name: 'TEXT' }),
  STORES:   relation('dim_store', 't', { store_key: 'NUM', city: 'TEXT', region: 'TEXT',
                                         format: 'TEXT' }),
  PRODUCTS: relation('dim_product', 'p', { product_key: 'NUM', sku: 'TEXT', name: 'TEXT',
                                           category: 'TEXT', brand: 'TEXT', list_price: 'NUM' }),
};
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-star/php.php#bindings -->
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
    'SALES'    => relation('fact_sales', 's', ['sale_id' => 'NUM', 'date_key' => 'NUM',
                                               'product_key' => 'NUM', 'store_key' => 'NUM',
                                               'qty' => 'NUM', 'revenue' => 'NUM']),
    'DATES'    => relation('dim_date', 'd', ['date_key' => 'NUM', 'year' => 'NUM', 'quarter' => 'NUM',
                                             'month' => 'NUM', 'month_name' => 'TEXT']),
    'STORES'   => relation('dim_store', 't', ['store_key' => 'NUM', 'city' => 'TEXT',
                                              'region' => 'TEXT', 'format' => 'TEXT']),
    'PRODUCTS' => relation('dim_product', 'p', ['product_key' => 'NUM', 'sku' => 'TEXT',
                                                'name' => 'TEXT', 'category' => 'TEXT',
                                                'brand' => 'TEXT', 'list_price' => 'NUM']),
];
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-star/cpp.cpp#bindings -->
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
      {"SALES",    relation("fact_sales", "s", {{"sale_id", NUM}, {"date_key", NUM},
                                                {"product_key", NUM}, {"store_key", NUM},
                                                {"qty", NUM}, {"revenue", NUM}})},
      {"DATES",    relation("dim_date", "d", {{"date_key", NUM}, {"year", NUM}, {"quarter", NUM},
                                              {"month", NUM}, {"month_name", TEXT}})},
      {"STORES",   relation("dim_store", "t", {{"store_key", NUM}, {"city", TEXT},
                                               {"region", TEXT}, {"format", TEXT}})},
      {"PRODUCTS", relation("dim_product", "p", {{"product_key", NUM}, {"sku", TEXT},
                                                 {"name", TEXT}, {"category", TEXT},
                                                 {"brand", TEXT}, {"list_price", NUM}})},
  });
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-star/lisp.lisp#bindings -->
```lisp
(defun relation (table alias &rest fields)
  "A table as a relation binding. FIELDS alternate a column name and its kind."
  (sel.sql:binding-relation
   table alias
   (loop for (name kind) on fields by #'cddr
         collect (cons name (sel.sql:binding-column name alias kind)))))

(defparameter *schema*
  (list (cons "SALES"    (relation "fact_sales" "s" "sale_id" :num "date_key" :num
                                   "product_key" :num "store_key" :num "qty" :num
                                   "revenue" :num))
        (cons "DATES"    (relation "dim_date" "d" "date_key" :num "year" :num "quarter" :num
                                   "month" :num "month_name" :text))
        (cons "STORES"   (relation "dim_store" "t" "store_key" :num "city" :text
                                   "region" :text "format" :text))
        (cons "PRODUCTS" (relation "dim_product" "p" "product_key" :num "sku" :text
                                   "name" :text "category" :text "brand" :text
                                   "list_price" :num))))
```

</details>
<!-- /tabs -->

## The pipelines

**Revenue by category in the first quarter.** Two joins, a filter on a dimension,
a group, a sort:

<!-- from: examples/sql-star/revenue-by-category.sel -->
```sel
# Revenue and units per product category in the first quarter of 2025.
SALES
  .> LINK(DATES, S, D, S["date_key"] == D["date_key"])
  .> FILTER(_["year"] == 2025 AND _["quarter"] == 1)
  .> LINK(PRODUCTS, X, P, X["product_key"] == P["product_key"])
  .> BUCKET(_["category"], RECORD(
       "category", _K,
       "units", SUM(_, _["qty"]),
       "revenue", SUM(_, _["revenue"])))
  .> SORT_BY(_["revenue"], "DESC")
```

**The best-selling product of each region, stores only.** The joins and the
filter are SQL's; the `MAP` names the four fields the rest needs, which is what
makes the joined rows a place the planner can split. The projection of the
`BUCKET` then groups each region's sales *again*, by product, and picks the top
one — a "best of" per group that SQL's `GROUP BY` cannot express, so the bucket
runs in memory over the rows the database joined, filtered and sorted:

<!-- from: examples/sql-star/best-product-per-region.sel -->
```sel
# Per region: revenue, and the product that earned the most of it -- physical stores only.
SALES
  .> LINK(STORES, S, T, S["store_key"] == T["store_key"])
  .> LINK(PRODUCTS, X, P, X["product_key"] == P["product_key"])
  .> FILTER(_["format"] $!= "web")
  .> MAP(RECORD("sale", _["sale_id"], "region", _["region"],
                "product", _["name"], "revenue", _["revenue"]))
  .> SORT_BY(_["sale"])
  .> BUCKET(_["region"], RECORD(
       "region", _K,
       "revenue", SUM(_, _["revenue"]),
       "best", BUCKET(_, _["product"], RECORD("product", _K, "revenue", SUM(_, _["revenue"])))
                 .> TOP_BY(_["revenue"], "DESC", 1)
                 .> MAP(_["product"]) .> JOIN("")))
  .> SORT_BY(_["region"])
```

## Running them

Plan, run, and — for the check at the end of each — run the same program over the
tables loaded into memory:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-star/python.py#run -->
```python
with open(os.path.join(HERE, file), encoding='utf-8') as fh:
    program = compile(fh.read())
plan = plan_hybrid(program, 'postgresql', SCHEMA)
rows = execute_hybrid(plan, runner(conn), tables if plan.pure_memory else None)
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-star/js.mjs#run -->
```js
const program = compile(readFileSync(new URL(file, import.meta.url), 'utf8'));
const plan = planHybrid(program, 'postgresql', SCHEMA);
const rows = executeHybrid(plan, await runner(conn, plan), plan.pureMemory ? tables : null);
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-star/php.php#run -->
```php
$program = Sel::compile(file_get_contents(__DIR__ . '/' . $file));
$plan = Sql::planHybrid($program, 'postgresql', $schema);
$rows = Sql::executeHybrid($plan, runner($conn), $plan->pureMemory ? $tables : null);
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-star/cpp.cpp#run -->
```cpp
const sel::Program program = sel::compile(read("examples/sql-star/" + file));
const HybridPlan plan = Sql::plan_hybrid(program, "postgresql", SCHEMA);
const sel::Value rows = Sql::execute_hybrid(plan, db::runner(conn),
                                            plan.pure_memory ? tables : sel::Value::none());
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-star/lisp.lisp#run -->
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

<!-- from: examples/sql-star/output.txt -->
```text
1. revenue by category, first quarter
   plan        pure_sql
   reads       fact_sales, dim_date, dim_product
   sql         SELECT CAST("p"."category" AS TEXT) COLLATE "C" AS "category", COALESCE(SUM("s"."qty"), 0) AS "units", COALESCE(SUM("s"."revenue"), 0) AS "revenue" FROM "fact_sales" "s" INNER JOIN "dim_date" "d" ON ("s"."date_key" = "d"."date_key") INNER JOIN "dim_product" "p" ON ("s"."product_key" = "p"."product_key") WHERE (("d"."year" = 2025) AND ("d"."quarter" = 1)) GROUP BY CAST("p"."category" AS TEXT) COLLATE "C" ORDER BY COALESCE(SUM("s"."revenue"), 0) DESC
   | category=kitchen  units=12  revenue=2075.20
   | category=games  units=16  revenue=1515.92
   | category=garden  units=10  revenue=1430.00
   | category=books  units=16  revenue=483.00
   in memory   same rows
2. best-selling product per region, stores only
   plan        hybrid
   reads       fact_sales, dim_store, dim_product
   sql         SELECT "_sub1".* FROM (SELECT "s"."sale_id" AS "sale", "t"."region" AS "region", "p"."name" AS "product", "s"."revenue" AS "revenue" FROM "fact_sales" "s" INNER JOIN "dim_store" "t" ON ("s"."store_key" = "t"."store_key") INNER JOIN "dim_product" "p" ON ("s"."product_key" = "p"."product_key") WHERE (CAST("t"."format" AS TEXT) COLLATE "C" <> CAST('web' AS TEXT) COLLATE "C")) "_sub1" ORDER BY "_sub1"."sale" ASC
   | region=Central  revenue=4904.75  best=Chef Knife
   | region=North  revenue=2058.46  best=Cast Iron Pan
   | region=South  revenue=2390.27  best=Cast Iron Pan
   in memory   same rows
```

The first plan is `pure_sql`: one statement with two `INNER JOIN`s, a `WHERE` on
the date dimension, a `GROUP BY` on the category — cast to text and collated
`"C"`, so that two categories differing only in case or trailing spaces stay two
groups, as they are in SEL — and an `ORDER BY` on the sum.

The second is `hybrid`. The database's part is everything up to the sort: the
joins, the `WHERE`, the four projected columns and the `ORDER BY sale`. Memory
does the rest: the two nested groupings and the top-1 per region. The sort is
kept in SQL deliberately, and matters: SEL's grouping keeps first-seen order, so
the rows must arrive in a defined order for the answer not to depend on the
database's join strategy.
