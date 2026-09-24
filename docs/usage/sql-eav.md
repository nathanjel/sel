# Entity–attribute–value

One table of entities and one of `(entity, name, value)` rows — the shape of a
product catalogue whose products all have different properties, of a CMS's
custom fields, of a settings store. It is flexible to write to and awkward to
ask: "red or blue, and made of steel" is a subquery per attribute, every value is
text whatever it means, and a record per entity only exists after a pivot. Here
it is in **SQLite**, the database that knows least about types.

## The schema

```text
entities                         attributes
  id    INTEGER PK  <──────────    entity_id  INTEGER  → entities.id
  sku   TEXT   'LMP-01', …         name       TEXT     color|material|price|watts|seats|…
  kind  TEXT   lamp|chair|…        value      TEXT     'red', 'steel', '49.90', 'call us', …
                                   PRIMARY KEY (entity_id, name)
```

Ten products with three or four attributes each. Two details are on purpose: one
product's colour is `Red` where the others say `red`, and one price is
`call us`. The seed is
[`examples/sql-eav/seed.sqlite.sql`](../../examples/sql-eav/seed.sqlite.sql).

## Describing it

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-eav/python.py#bindings -->
```python
def relation(table, alias, **fields):
    return Binding.relation(table, alias, fields={
        name: Binding.column(name, alias, kind) for name, kind in fields.items()})


SCHEMA = {
    'PRODUCTS': relation('entities', 'e', id='NUM', sku='TEXT', kind='TEXT'),
    'ATTRS':    relation('attributes', 'a', entity_id='NUM', name='TEXT', value='TEXT'),
}
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-eav/js.mjs#bindings -->
```js
function relation(table, alias, fields) {
  return Binding.relation(table, alias, Object.fromEntries(Object.entries(fields)
    .map(([name, kind]) => [name, Binding.column(name, alias, kind)])));
}

const SCHEMA = {
  PRODUCTS: relation('entities', 'e', { id: 'NUM', sku: 'TEXT', kind: 'TEXT' }),
  ATTRS:    relation('attributes', 'a', { entity_id: 'NUM', name: 'TEXT', value: 'TEXT' }),
};
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-eav/php.php#bindings -->
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
    'PRODUCTS' => relation('entities', 'e', ['id' => 'NUM', 'sku' => 'TEXT', 'kind' => 'TEXT']),
    'ATTRS'    => relation('attributes', 'a', ['entity_id' => 'NUM', 'name' => 'TEXT',
                                               'value' => 'TEXT']),
];
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-eav/cpp.cpp#bindings -->
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
      {"PRODUCTS", relation("entities", "e", {{"id", NUM}, {"sku", TEXT}, {"kind", TEXT}})},
      {"ATTRS",    relation("attributes", "a", {{"entity_id", NUM}, {"name", TEXT},
                                                {"value", TEXT}})},
  });
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-eav/lisp.lisp#bindings -->
```lisp
(defun relation (table alias &rest fields)
  "A table as a relation binding. FIELDS alternate a column name and its kind."
  (sel.sql:binding-relation
   table alias
   (loop for (name kind) on fields by #'cddr
         collect (cons name (sel.sql:binding-column name alias kind)))))

(defparameter *schema*
  (list (cons "PRODUCTS" (relation "entities" "e" "id" :num "sku" :text "kind" :text))
        (cons "ATTRS"    (relation "attributes" "a" "entity_id" :num "name" :text
                                   "value" :text))))
```

</details>
<!-- /tabs -->

The `value` field is declared `TEXT`, because that is what it is.

## The pipelines

**Red or blue, and steel.** Each condition on an attribute is an `ANY` over the
attribute rows of *this* product — the body compares `A["entity_id"]` with the
outer binder `P`, so the relation needs no `correlate` of its own:

<!-- from: examples/sql-eav/red-or-blue-steel.sel -->
```sel
# Products whose colour is red or blue and whose material is steel.
PRODUCTS
  .> FILTER(P, ANY(ATTRS, A, A["entity_id"] == P["id"]
                   AND A["name"] $== "color" AND A["value"] IN ("red", "blue")))
  .> FILTER(P, ANY(ATTRS, A, A["entity_id"] == P["id"]
                   AND A["name"] $== "material" AND A["value"] $== "steel"))
  .> MAP(RECORD("id", _["id"], "sku", _["sku"]))
  .> SORT_BY(_["id"])
```

**Products per colour.** A group over the attribute rows themselves:

<!-- from: examples/sql-eav/products-per-colour.sel -->
```sel
# How many products have each colour.
ATTRS
  .> FILTER(_["name"] $== "color")
  .> BUCKET(_["value"], RECORD("color", _K, "products", COUNT(_)))
  .> SORT_BY(_["color"])
```

**Priced under 60.00, one record per product.** The database selects and orders
the colour and price rows; memory pivots each product's rows into a record and
compares the price as a number — which SQLite cannot be trusted to do on a text
column holding `call us`, and SEL does only after `ISNUM` says it is one:

<!-- from: examples/sql-eav/priced-under-60.sel -->
```sel
# Products under 60.00, as one record per product: attributes pivoted into fields.
ATTRS
  .> FILTER(_["name"] IN ("color", "price"))
  .> SORT_BY(_["name"]) .> SORT_BY(_["entity_id"])
  .> BUCKET(_["entity_id"], RECORD(
       "id", _K,
       "color", FILTER(_, A, A["name"] $== "color") .> MAP(_["value"]) .> JOIN(""),
       "price", FILTER(_, A, A["name"] $== "price") .> MAP(_["value"]) .> JOIN("")))
  .> FILTER(ISNUM(_["price"]) AND _["price"] < 60)
  .> SORT_BY(_["price"])
```

## Running them

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-eav/python.py#run -->
```python
with open(os.path.join(HERE, file), encoding='utf-8') as fh:
    program = compile(fh.read())
plan = plan_hybrid(program, 'sqlite', SCHEMA)
rows = execute_hybrid(plan, runner(conn), tables if plan.pure_memory else None)
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-eav/js.mjs#run -->
```js
const program = compile(readFileSync(new URL(file, import.meta.url), 'utf8'));
const plan = planHybrid(program, 'sqlite', SCHEMA);
const rows = executeHybrid(plan, await runner(conn, plan), plan.pureMemory ? tables : null);
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-eav/php.php#run -->
```php
$program = Sel::compile(file_get_contents(__DIR__ . '/' . $file));
$plan = Sql::planHybrid($program, 'sqlite', $schema);
$rows = Sql::executeHybrid($plan, runner($conn), $plan->pureMemory ? $tables : null);
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-eav/cpp.cpp#run -->
```cpp
const sel::Program program = sel::compile(read("examples/sql-eav/" + file));
const HybridPlan plan = Sql::plan_hybrid(program, "sqlite", SCHEMA);
const sel::Value rows = Sql::execute_hybrid(plan, db::runner(conn),
                                            plan.pure_memory ? tables : sel::Value::none());
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-eav/lisp.lisp#run -->
```lisp
(defun run-pipeline (file conn tables)
  "Run the pipeline in FILE with as much of it in SQLite as SQLite can answer.
Returns the rows, the plan and the compiled program."
  (let* ((program (sel:compile-source (read-file file)))
         (plan (sel.sql:plan-hybrid program "sqlite" *schema*))
         (rows (sel.sql:execute-hybrid plan (sel-db:runner conn)
                                       (when (sel.sql:hybrid-plan-pure-memory-p plan) tables))))
    (values rows plan program)))
```

</details>
<!-- /tabs -->

## What it prints

<!-- from: examples/sql-eav/output.txt -->
```text
1. red or blue, and steel
   plan        pure_sql
   reads       entities, attributes
   sql         SELECT "_sub1".* FROM (SELECT "e"."id" AS "id", "e"."sku" AS "sku" FROM "entities" "e" WHERE (EXISTS (SELECT 1 FROM "attributes" "a" WHERE 1 AND ((((CAST("a"."entity_id" AS NUMERIC) = CAST("e"."id" AS NUMERIC)) AND (CAST("a"."name" AS TEXT) COLLATE BINARY = CAST('color' AS TEXT) COLLATE BINARY)) AND ((CAST("a"."value" AS TEXT) COLLATE BINARY = CAST('red' AS TEXT) COLLATE BINARY) OR (CAST("a"."value" AS TEXT) COLLATE BINARY = CAST('blue' AS TEXT) COLLATE BINARY)))) IS TRUE) AND EXISTS (SELECT 1 FROM "attributes" "a" WHERE 1 AND ((((CAST("a"."entity_id" AS NUMERIC) = CAST("e"."id" AS NUMERIC)) AND (CAST("a"."name" AS TEXT) COLLATE BINARY = CAST('material' AS TEXT) COLLATE BINARY)) AND (CAST("a"."value" AS TEXT) COLLATE BINARY = CAST('steel' AS TEXT) COLLATE BINARY))) IS TRUE))) "_sub1" ORDER BY "_sub1"."id" ASC
   | id=1  sku=LMP-01
   | id=4  sku=CHR-02
   | id=6  sku=TBL-02
   | id=7  sku=LMP-03
   | id=9  sku=SHF-02
   in memory   same rows
2. products per colour
   plan        pure_sql
   reads       attributes
   sql         SELECT CAST("a"."value" AS TEXT) COLLATE BINARY AS "color", COUNT(*) AS "products" FROM "attributes" "a" WHERE (CAST("a"."name" AS TEXT) COLLATE BINARY = CAST('color' AS TEXT) COLLATE BINARY) GROUP BY CAST("a"."value" AS TEXT) COLLATE BINARY ORDER BY MIN(CAST("a"."value" AS TEXT) COLLATE BINARY) ASC
   | color=Red  products=1
   | color=blue  products=3
   | color=natural  products=1
   | color=red  products=3
   | color=white  products=2
   in memory   same rows
3. priced under 60.00, pivoted
   plan        hybrid
   reads       attributes
   sql         SELECT "a".* FROM "attributes" "a" WHERE ((CAST("a"."name" AS TEXT) COLLATE BINARY = CAST('color' AS TEXT) COLLATE BINARY) OR (CAST("a"."name" AS TEXT) COLLATE BINARY = CAST('price' AS TEXT) COLLATE BINARY)) ORDER BY "a"."entity_id" ASC, CAST("a"."name" AS TEXT) COLLATE BINARY ASC
   | id=10  color=Red  price=19.99
   | id=7  color=blue  price=35.00
   | id=8  color=white  price=45.00
   | id=1  color=red  price=49.90
   | id=9  color=red  price=59.99
   in memory   same rows
```

The first plan is one statement with two `EXISTS` subqueries — `IN` unrolled into
an `OR` — and the second a `GROUP BY` over the value. Both compare text with
`COLLATE BINARY`: SQLite's comparison would otherwise depend on how each column
was declared, and `Red` and `red` are two colours in SEL. They are two groups
here, too.

The third is `hybrid`: SQLite filters and sorts the attribute rows, and the pivot,
the numeric filter and the final sort run in memory. `call us` is simply not a
number, so the product it belongs to is not "under 60" — rather than an error in
one host and a zero in another.
