# Unnormalised data

One wide table where every line repeats its order's customer and product — an
export, a spreadsheet, a nightly dump — with what comes with that: the same
customer under two spellings of their name and e-mail, and a list of tags packed
into one column. Here it is in **MariaDB**, with the case-insensitive collation a
hand-made table often has.

## The schema

```text
order_export
  line_id          INT PK
  order_no         VARCHAR   'SO-1001', … (two lines per order)
  order_date       DATE
  customer_name    VARCHAR   'Anna Nowak' and 'anna nowak '
  customer_email   VARCHAR   'anna@example.pl' and 'Anna@Example.pl'; 'dawid@example.pl' and 'dawid@example.pl '
  customer_city    VARCHAR   Kraków, Berlin, Lyon, Malmö, Warszawa
  sku, product_name, category
  qty              INT
  unit_price       DECIMAL(10,2)
  tags             VARCHAR   'gift;promo', 'b2b', '', …
DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_general_ci
```

Forty lines. The seed is
[`examples/sql-flat/seed.mariadb.sql`](../../examples/sql-flat/seed.mariadb.sql).

## Describing it

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-flat/python.py#bindings -->
```python
def relation(table, alias, **fields):
    return Binding.relation(table, alias, fields={
        name: Binding.column(name, alias, kind) for name, kind in fields.items()})


SCHEMA = {
    'EXPORT': relation('order_export', 'x', line_id='NUM', order_no='TEXT', order_date='TEXT',
                       customer_name='TEXT', customer_email='TEXT', customer_city='TEXT',
                       sku='TEXT', product_name='TEXT', category='TEXT', qty='NUM',
                       unit_price='NUM', tags='TEXT'),
}
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-flat/js.mjs#bindings -->
```js
function relation(table, alias, fields) {
  return Binding.relation(table, alias, Object.fromEntries(Object.entries(fields)
    .map(([name, kind]) => [name, Binding.column(name, alias, kind)])));
}

const SCHEMA = {
  EXPORT: relation('order_export', 'x', { line_id: 'NUM', order_no: 'TEXT', order_date: 'TEXT',
                                          customer_name: 'TEXT', customer_email: 'TEXT',
                                          customer_city: 'TEXT', sku: 'TEXT', product_name: 'TEXT',
                                          category: 'TEXT', qty: 'NUM', unit_price: 'NUM',
                                          tags: 'TEXT' }),
};
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-flat/php.php#bindings -->
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
    'EXPORT' => relation('order_export', 'x', ['line_id' => 'NUM', 'order_no' => 'TEXT',
                                               'order_date' => 'TEXT', 'customer_name' => 'TEXT',
                                               'customer_email' => 'TEXT',
                                               'customer_city' => 'TEXT', 'sku' => 'TEXT',
                                               'product_name' => 'TEXT', 'category' => 'TEXT',
                                               'qty' => 'NUM', 'unit_price' => 'NUM',
                                               'tags' => 'TEXT']),
];
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-flat/cpp.cpp#bindings -->
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
      {"EXPORT", relation("order_export", "x", {{"line_id", NUM}, {"order_no", TEXT},
                                                {"order_date", TEXT}, {"customer_name", TEXT},
                                                {"customer_email", TEXT}, {"customer_city", TEXT},
                                                {"sku", TEXT}, {"product_name", TEXT},
                                                {"category", TEXT}, {"qty", NUM},
                                                {"unit_price", NUM}, {"tags", TEXT}})},
  });
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-flat/lisp.lisp#bindings -->
```lisp
(defun relation (table alias &rest fields)
  "A table as a relation binding. FIELDS alternate a column name and its kind."
  (sel.sql:binding-relation
   table alias
   (loop for (name kind) on fields by #'cddr
         collect (cons name (sel.sql:binding-column name alias kind)))))

(defparameter *schema*
  (list (cons "EXPORT" (relation "order_export" "x" "line_id" :num "order_no" :text
                                 "order_date" :text "customer_name" :text
                                 "customer_email" :text "customer_city" :text
                                 "sku" :text "product_name" :text "category" :text
                                 "qty" :num "unit_price" :num "tags" :text))))
```

</details>
<!-- /tabs -->

## The pipelines

**Revenue per city, February and March.** A filter on the date and a group — SQL
from end to end:

<!-- from: examples/sql-flat/revenue-per-city.sel -->
```sel
# Lines and revenue per customer city, February and March.
EXPORT
  .> FILTER(_["order_date"] $>= "2025-02-01" AND _["order_date"] $< "2025-04-01")
  .> BUCKET(_["customer_city"], RECORD(
       "city", _K,
       "lines", COUNT(_),
       "revenue", SUM(_, _["qty"] * _["unit_price"])))
  .> SORT_BY(_["revenue"], "DESC")
```

**Distinct customers per city.** A customer is their e-mail address, trimmed and
lower-cased. SQL could compute that — but the de-duplication would then compare
the results under MariaDB's collation, which decides for itself which strings
are "the same" (`utf8mb4_general_ci` ignores case and trailing spaces). SEL's
identity is exact bytes, so the planner keeps the normalisation, the
de-duplication and the grouping in memory, and the database only filters:

<!-- from: examples/sql-flat/customers-per-city.sel -->
```sel
# Distinct customers per city, a customer being a normalised e-mail address.
EXPORT
  .> FILTER(_["category"] $!= "books")
  .> MAP(RECORD("city", _["customer_city"], "email", LOWER(TRIM(_["customer_email"]))))
  .> DEDUPE()
  .> BUCKET(_["city"], RECORD("city", _K, "customers", COUNT(_)))
  .> SORT_BY(_["city"])
```

**Lines per tag.** The tags of every line are joined and split again, which
turns a list column into a list of tags — and a list is something no SQL
expression yields, so this pipeline stays in memory whole:

<!-- from: examples/sql-flat/lines-per-tag.sel -->
```sel
# How many lines carry each tag of the ;-separated tags column.
EXPORT
  .> FILTER(_["tags"] $!= "")
  .> MAP(_["tags"])
  .> JOIN(";")
  .> SPLIT(";")
  .> BUCKET(_, RECORD("tag", _K, "lines", COUNT(_)))
  .> SORT_BY(_["tag"])
```

## Running them

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-flat/python.py#run -->
```python
with open(os.path.join(HERE, file), encoding='utf-8') as fh:
    program = compile(fh.read())
plan = plan_hybrid(program, 'mariadb', SCHEMA)
rows = execute_hybrid(plan, runner(conn), tables if plan.pure_memory else None)
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-flat/js.mjs#run -->
```js
const program = compile(readFileSync(new URL(file, import.meta.url), 'utf8'));
const plan = planHybrid(program, 'mariadb', SCHEMA);
const rows = executeHybrid(plan, await runner(conn, plan), plan.pureMemory ? tables : null);
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-flat/php.php#run -->
```php
$program = Sel::compile(file_get_contents(__DIR__ . '/' . $file));
$plan = Sql::planHybrid($program, 'mariadb', $schema);
$rows = Sql::executeHybrid($plan, runner($conn), $plan->pureMemory ? $tables : null);
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-flat/cpp.cpp#run -->
```cpp
const sel::Program program = sel::compile(read("examples/sql-flat/" + file));
const HybridPlan plan = Sql::plan_hybrid(program, "mariadb", SCHEMA);
const sel::Value rows = Sql::execute_hybrid(plan, db::runner(conn),
                                            plan.pure_memory ? tables : sel::Value::none());
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-flat/lisp.lisp#run -->
```lisp
(defun run-pipeline (file conn tables)
  "Run the pipeline in FILE with as much of it in MariaDB as MariaDB can answer.
Returns the rows, the plan and the compiled program."
  (let* ((program (sel:compile-source (read-file file)))
         (plan (sel.sql:plan-hybrid program "mariadb" *schema*))
         (rows (sel.sql:execute-hybrid plan (sel-db:runner conn)
                                       (when (sel.sql:hybrid-plan-pure-memory-p plan) tables))))
    (values rows plan program)))
```

</details>
<!-- /tabs -->

## What it prints

<!-- from: examples/sql-flat/output.txt -->
```text
1. revenue per city, February and March
   plan        pure_sql
   reads       order_export
   sql         SELECT CAST(`x`.`customer_city` AS CHAR) COLLATE utf8mb4_nopad_bin AS `city`, COUNT(*) AS `lines`, COALESCE(SUM((`x`.`qty` * `x`.`unit_price`)), 0) AS `revenue` FROM `order_export` `x` WHERE ((CAST(`x`.`order_date` AS CHAR) COLLATE utf8mb4_nopad_bin >= CAST('2025-02-01' AS CHAR) COLLATE utf8mb4_nopad_bin) AND (CAST(`x`.`order_date` AS CHAR) COLLATE utf8mb4_nopad_bin < CAST('2025-04-01' AS CHAR) COLLATE utf8mb4_nopad_bin)) GROUP BY CAST(`x`.`customer_city` AS CHAR) COLLATE utf8mb4_nopad_bin ORDER BY COALESCE(SUM((`x`.`qty` * `x`.`unit_price`)), 0) DESC
   | city=Kraków  lines=9  revenue=2066.45
   | city=Berlin  lines=5  revenue=1358.37
   | city=Warszawa  lines=2  revenue=559.78
   | city=Malmö  lines=2  revenue=338.90
   | city=Lyon  lines=2  revenue=213.90
   in memory   same rows
2. distinct customers per city, by normalised e-mail
   plan        hybrid
   reads       order_export
   sql         SELECT `x`.* FROM `order_export` `x` WHERE (CAST(`x`.`category` AS CHAR) COLLATE utf8mb4_nopad_bin <> CAST('books' AS CHAR) COLLATE utf8mb4_nopad_bin)
   | city=Berlin  customers=2
   | city=Kraków  customers=2
   | city=Lyon  customers=1
   | city=Malmö  customers=1
   | city=Warszawa  customers=1
   in memory   same rows
3. lines per tag
   plan        pure_memory
   reads       order_export
   | tag=b2b  lines=5
   | tag=clearance  lines=4
   | tag=gift  lines=9
   | tag=promo  lines=14
   in memory   same rows
```

The first plan is one statement: the date compared as text under a binary
collation, grouped by the city under the same collation, so `Kraków` and
`krakow` would stay apart. The second is `hybrid` at the planner's *identity
barrier*: the `WHERE` runs in MariaDB, and everything from the normalising `MAP`
on runs in memory. Anna's two spellings and Dawid's trailing space each become
one customer — because the application said how to normalise, not because the
database's collation happened to agree. The third is `pure_memory`; the database
supplies the table, and SEL does the rest.
