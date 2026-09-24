# SQL conditions

A rule written for the application can also filter rows where they live. The
SQL layer translates a compiled program into a SQL expression — a `WHERE`
clause, a `CHECK`, a column in a `SELECT` — for MariaDB, MySQL, PostgreSQL or
SQLite. It is the same program: nothing about the rule changes, only where it
runs.

Two properties decide how it is used:

- **Refusal is an ordinary answer.** Much of SEL has no faithful SQL — a
  function that yields a list, a regex on a database without regexes, a number
  comparison on a column nobody said holds numbers. Such a rule is refused
  *whole*, before any SQL is written, and the application runs it in memory
  instead. It never gets SQL that means something slightly different.
- **Variables become schema, not values.** `dependencies()` tells the
  application which names a rule reads; the application answers with *bindings*
  that say where each name lives — a column, a list of columns, a related table,
  a constant.

This page is the same idea twice: once knowing nothing about the database, and
once describing it.

- [The data](#the-data)
- [Naive: a column per variable](#naive-a-column-per-variable)
- [Described: types, column lists, related tables, parameters](#described-types-column-lists-related-tables-parameters)
- [What it prints](#what-it-prints)
- [Choosing between them](#choosing-between-them)

---

## The data

The naive part uses one table of customers, loaded into **SQLite** and into
**MariaDB**. In SQLite every column is text, the way a table imported from a
spreadsheet often is; in MariaDB the credit limit is a `DECIMAL` and text uses a
case-insensitive collation, the way a hand-made schema often does.

```text
customers
  id            INTEGER PRIMARY KEY
  name          TEXT          'Anna Nowak', 'Irena Zając-Kowalewska', …
  email         TEXT          '', '  ', 'filip(at)example.pl' among the good ones
  country       CHAR(2)       'PL', 'DE', 'FR', 'SE', 'DK'
  postcode      TEXT          '31-874', '10115', '30-0012', …
  credit_limit  TEXT (SQLite) / DECIMAL(10,2) (MariaDB)
  tier          TEXT          'standard', 'silver', 'gold', 'platinum'
```

The described part uses orders and their lines in **PostgreSQL**. Each order has
three tag columns — a denormalised list — and its lines are a related table:

```text
orders                                   order_items
  id           INTEGER PRIMARY KEY  <──┐   order_id  INTEGER  → orders.id
  customer_id  INTEGER                 └── line_no   INTEGER
  status       TEXT   paid|pending|…       sku       TEXT     'BK-001', 'GM-001', …
  total        NUMERIC(10,2)               qty       INTEGER  (some lines have 0)
  channel      TEXT   web|shop|phone       price     NUMERIC(10,2)
  tag1..tag3   TEXT   gift|b2b|promo|''
```

Some order totals deliberately disagree with the sum of their lines. The seeds
are [`examples/sql-conditions/seed.*.sql`](../../examples/sql-conditions/).

## Naive: a column per variable

The application knows nothing about the schema except that a variable is a column
of the same name. That is one line: every name `dependencies()` reports becomes
an untyped column binding.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-conditions/python.py#naive -->
```python
rule = compile(source)
bindings = {name: Binding.column(name.lower()) for name in rule.dependencies()}
where = Sql.try_translate(rule, dialect, bindings)
if where is not None:
    sql = 'SELECT id FROM customers WHERE ' + where.as_condition('params') + ' ORDER BY id'
    rows = query(conn, sql, where.bindings())
else:
    # refused: the rule stays in the application, over rows it loads
    rows = Value.none()
    for key, row in everyone.entries():
        if rule.run(context_of(row)).as_bool():
            rows.set(key, row)
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-conditions/js.mjs#naive -->
```js
const rule = compile(source);
const bindings = Object.fromEntries(
  rule.dependencies().map((name) => [name, Binding.column(name.toLowerCase())]));
const where = Sql.tryTranslate(rule, dialect, bindings);
let rows;
if (where !== null) {
  const sql = 'SELECT id FROM customers WHERE ' + where.asCondition('params') + ' ORDER BY id';
  rows = await query(conn, sql, where.bindings());
} else {
  // refused: the rule stays in the application, over rows it loads
  rows = Value.none();
  for (const [key, row] of everyone.entries()) {
    if (rule.run(contextOf(row)).asBool()) rows.set(key, row);
  }
}
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-conditions/php.php#naive -->
```php
$rule = Sel::compile($source);
$bindings = [];
foreach ($rule->dependencies() as $name) {
    $bindings[$name] = Binding::column(strtolower($name));
}
$where = Sql::tryTranslate($rule, $dialect, $bindings);
if ($where !== null) {
    $sql = 'SELECT id FROM customers WHERE ' . $where->asCondition('params') . ' ORDER BY id';
    $rows = query($conn, $sql, $where->bindings());
} else {
    // refused: the rule stays in the application, over rows it loads
    $rows = Value::none();
    foreach ($everyone->entries() as [$key, $row]) {
        if ($rule->run(contextOf($row))->asBool()) {
            $rows->set($key, $row);
        }
    }
}
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-conditions/cpp.cpp#naive -->
```cpp
const sel::Program rule = sel::compile(source);
std::vector<std::pair<std::string, Binding>> columns;
for (const std::string& name : rule.dependencies())
  columns.emplace_back(name, Binding::column(ascii_case(name, false)));
const Bindings bindings(columns);
const std::optional<Fragment> where = Sql::try_translate(rule, dialect, bindings);
sel::Value rows = sel::Value::none();
if (where) {
  const std::string sql =
      "SELECT id FROM customers WHERE " + where->as_condition(Mode::Params) + " ORDER BY id";
  rows = db::query(conn, sql, where->bindings());
} else {
  // refused: the rule stays in the application, over rows it loads
  for (const auto& [key, row] : everyone.entries()) {
    sel::Value ctx = context_of(row);
    if (rule.run(ctx).as_bool()) rows.set(key, row);
  }
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-conditions/lisp.lisp#naive -->
```lisp
(defun naive (conn dialect source everyone)
  "The customers SOURCE accepts: in SQL when it translates, else in memory over
EVERYONE. Returns the rows, the WHERE fragment or NIL, and what went into it."
  (let* ((rule (sel:compile-source source))
         (bindings (loop for name in (sel:dependencies rule)
                         collect (cons name (sel.sql:binding-column (string-downcase name)))))
         (where (sel.sql:try-translate rule dialect bindings))
         (rows (sel:make-none)))
    (if where
        (setf rows (sel-db:query conn (concatenate 'string "SELECT id FROM customers WHERE "
                                                   (sel.sql:as-condition where :params)
                                                   " ORDER BY id")
                                 (sel.sql:bindings where)))
        ;; refused: the rule stays in the application, over rows it loads
        (loop for (key . row) in (sel:value-entries everyone)
              when (sel:as-bool (sel:run rule (context-of row)))
                do (sel:value-set rows key row)))
    (values rows where rule bindings)))
```

</details>
<!-- /tabs -->

`try_translate` returns nothing instead of raising when the rule is refused,
because a refusal is expected; `translate` raises the same refusal with the
reason written out, which is what a build-time audit of a rule set wants. The
SQL is used in `params` mode: every text literal becomes a `?` placeholder, and
`bindings()` returns the values in order.

Untyped columns cost something in each position. As an operand of `>=` a column
nobody declared numeric is wrapped in the dialect's *numeric guard* — "if this
text is a number, compare it as one, otherwise it matches nothing" — so the rule
cannot silently compare text as numbers. MariaDB can express that guard with
`REGEXP`; SQLite cannot ask whether a value is a number at all, so there the rule
is refused and runs in memory. The regex rules translate on MariaDB and are
refused on SQLite, which has no `REGEXP` unless the application registers one.
`SPLIT` yields a list, which no SQL expression can, so that rule is refused
everywhere.

Text comparisons are wrapped too: `$==` compares bytes, and MariaDB's
`utf8mb4_general_ci` would say `'PL' = 'pl'`, so the column is compared under a
binary collation. Every answer is checked against the same rule run in memory,
row by row.

## Described: types, column lists, related tables, parameters

Now the application describes the schema. The bindings are built by
constructors, in code — the same calls in every host — never parsed from a
document:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-conditions/python.py#involved -->
```python
bindings = {
    'STATUS':    Binding.column('status', 'o', 'TEXT', exact=True),
    'TOTAL':     Binding.column('total', 'o', 'NUM'),
    'CHANNEL':   Binding.column('channel', 'o', 'TEXT', exact=True),
    'TAGS':      Binding.columns(Binding.column('tag1', 'o', 'TEXT'),
                                 Binding.column('tag2', 'o', 'TEXT'),
                                 Binding.column('tag3', 'o', 'TEXT')),
    'ITEMS':     Binding.relation('order_items', 'i', fields={
                     'sku':   Binding.column('sku', 'i', 'TEXT'),
                     'qty':   Binding.column('qty', 'i', 'NUM'),
                     'price': Binding.column('price', 'i', 'NUM'),
                 }, correlate='"i"."order_id" = "o"."id"'),
    'MIN_TOTAL': Binding.value(Value.text('100.00')),
}
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-conditions/js.mjs#involved -->
```js
const bindings = {
  STATUS:    Binding.column('status', 'o', 'TEXT', /* exact */ true),
  TOTAL:     Binding.column('total', 'o', 'NUM'),
  CHANNEL:   Binding.column('channel', 'o', 'TEXT', /* exact */ true),
  TAGS:      Binding.columns(Binding.column('tag1', 'o', 'TEXT'),
                             Binding.column('tag2', 'o', 'TEXT'),
                             Binding.column('tag3', 'o', 'TEXT')),
  ITEMS:     Binding.relation('order_items', 'i', {
               sku:   Binding.column('sku', 'i', 'TEXT'),
               qty:   Binding.column('qty', 'i', 'NUM'),
               price: Binding.column('price', 'i', 'NUM'),
             }, /* scalar */ null, /* correlate */ '"i"."order_id" = "o"."id"'),
  MIN_TOTAL: Binding.value(Value.text('100.00')),
};
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-conditions/php.php#involved -->
```php
$bindings = [
    'STATUS'    => Binding::column('status', 'o', 'TEXT', exact: true),
    'TOTAL'     => Binding::column('total', 'o', 'NUM'),
    'CHANNEL'   => Binding::column('channel', 'o', 'TEXT', exact: true),
    'TAGS'      => Binding::columns(Binding::column('tag1', 'o', 'TEXT'),
                                    Binding::column('tag2', 'o', 'TEXT'),
                                    Binding::column('tag3', 'o', 'TEXT')),
    'ITEMS'     => Binding::relation('order_items', 'i', fields: [
                       'sku'   => Binding::column('sku', 'i', 'TEXT'),
                       'qty'   => Binding::column('qty', 'i', 'NUM'),
                       'price' => Binding::column('price', 'i', 'NUM'),
                   ], correlate: '"i"."order_id" = "o"."id"'),
    'MIN_TOTAL' => Binding::value(Value::text('100.00')),
];
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-conditions/cpp.cpp#involved -->
```cpp
const Bindings bindings({
    {"STATUS",    Binding::column("status", "o", SqlKind::Text, /*exact=*/true)},
    {"TOTAL",     Binding::column("total", "o", SqlKind::Num)},
    {"CHANNEL",   Binding::column("channel", "o", SqlKind::Text, /*exact=*/true)},
    {"TAGS",      Binding::columns({Binding::column("tag1", "o", SqlKind::Text),
                                    Binding::column("tag2", "o", SqlKind::Text),
                                    Binding::column("tag3", "o", SqlKind::Text)})},
    {"ITEMS",     Binding::relation("order_items", "i", {
                      {"sku",   Binding::column("sku", "i", SqlKind::Text)},
                      {"qty",   Binding::column("qty", "i", SqlKind::Num)},
                      {"price", Binding::column("price", "i", SqlKind::Num)},
                  }, /*scalar=*/std::nullopt, /*correlate=*/R"("i"."order_id" = "o"."id")")},
    {"MIN_TOTAL", Binding::value(sel::Value::text("100.00"))},
});
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-conditions/lisp.lisp#involved -->
```lisp
(defparameter *bindings*
  (list (cons "STATUS"    (sel.sql:binding-column "status" "o" :text :exact t))
        (cons "TOTAL"     (sel.sql:binding-column "total" "o" :num))
        (cons "CHANNEL"   (sel.sql:binding-column "channel" "o" :text :exact t))
        (cons "TAGS"      (sel.sql:binding-columns (sel.sql:binding-column "tag1" "o" :text)
                                                   (sel.sql:binding-column "tag2" "o" :text)
                                                   (sel.sql:binding-column "tag3" "o" :text)))
        (cons "ITEMS"     (sel.sql:binding-relation
                           "order_items" "i"
                           (list (cons "sku"   (sel.sql:binding-column "sku" "i" :text))
                                 (cons "qty"   (sel.sql:binding-column "qty" "i" :num))
                                 (cons "price" (sel.sql:binding-column "price" "i" :num)))
                           nil "\"i\".\"order_id\" = \"o\".\"id\""))
        (cons "MIN_TOTAL" (sel.sql:binding-value (sel:make-text "100.00")))))
```

</details>
<!-- /tabs -->

- `Binding.column(name, table, type, …)` — a column, qualified by its table's
  alias, with a type. `NUM` removes the numeric guard; `exact` says the column
  already compares bytes exactly, so the text comparison needs no collation
  wrapper and can use an index.
- `Binding.columns(…)` — several columns of the same row that a rule treats as
  one list. `ANY(TAGS, _ $== "gift")` unrolls into an `OR` over the three
  columns; no subquery.
- `Binding.relation(table, alias, fields, correlate)` — rows of another table,
  joined back to the outer row by `correlate`. An aggregate over it becomes a
  correlated subquery: `ALL` a `NOT EXISTS`, `ANY` an `EXISTS`, `SUM` and `COUNT`
  scalar subqueries — shaped so that the empty case and the `NULL` case mean what
  they mean in SEL.
- `Binding.value(v)` — a constant the application supplies at translation time,
  here a threshold from configuration. It becomes a literal, and in `params` mode
  a placeholder.

The rules are then translated with `translate`, which raises if a rule is
refused:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-conditions/python.py#involved-run -->
```python
rule = compile(source)
where = Sql.translate(rule, 'postgresql', bindings)
sql = 'SELECT id FROM orders o WHERE ' + where.as_condition('params') + ' ORDER BY id'
rows = query(conn, sql, where.bindings())
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-conditions/js.mjs#involved-run -->
```js
const rule = compile(source);
const where = Sql.translate(rule, 'postgresql', bindings);
const sql = 'SELECT id FROM orders o WHERE ' + where.asCondition('params') + ' ORDER BY id';
const rows = await query(conn, sql, where.bindings());
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-conditions/php.php#involved-run -->
```php
$rule = Sel::compile($source);
$where = Sql::translate($rule, 'postgresql', $bindings);
$sql = 'SELECT id FROM orders o WHERE ' . $where->asCondition('params') . ' ORDER BY id';
$rows = query($conn, $sql, $where->bindings());
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-conditions/cpp.cpp#involved-run -->
```cpp
const sel::Program rule = sel::compile(source);
const Fragment where = Sql::translate(rule, "postgresql", bindings);
const std::string sql =
    "SELECT id FROM orders o WHERE " + where.as_condition(Mode::Params) + " ORDER BY id";
const sel::Value rows = db::query(conn, sql, where.bindings());
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-conditions/lisp.lisp#involved-run -->
```lisp
(defun involved (conn source)
  "The orders SOURCE accepts, asked of PostgreSQL through *BINDINGS*.
Returns the rows, the WHERE fragment and the compiled rule."
  (let* ((rule (sel:compile-source source))
         (where (sel.sql:translate rule "postgresql" *bindings*))
         (sql (concatenate 'string "SELECT id FROM orders o WHERE "
                           (sel.sql:as-condition where :params) " ORDER BY id")))
    (values (sel-db:query conn sql (sel.sql:bindings where)) where rule)))
```

</details>
<!-- /tabs -->

## What it prints

Each rule shows the SQL, the parameters, the rows the database returned, and
whether they are the rows the same rule selects in memory:

<!-- from: examples/sql-conditions/output.txt -->
```text
1. naive bindings
   sqlite   COUNTRY $== "PL" AND TIER $!= "standard"
             sql    ((CAST("country" AS TEXT) COLLATE BINARY = CAST('PL' AS TEXT) COLLATE BINARY) AND (CAST("tier" AS TEXT) COLLATE BINARY <> CAST('standard' AS TEXT) COLLATE BINARY))
             rows   1, 6, 9 | same as in memory: TRUE
   sqlite   COUNTRY $== "PL" AND CREDIT_LIMIT >= 1000
             memory (E_SQL_UNSUPPORTED)
             rows   1, 6, 9 | same as in memory: TRUE
   sqlite   RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE)
             memory (E_SQL_UNSUPPORTED)
             rows   1, 4, 9, 10 | same as in memory: TRUE
   sqlite   IS_BLANK(EMAIL) OR NOT RMATCH('^[^@ ]+@[^@ ]+$', EMAIL)
             memory (E_SQL_UNSUPPORTED)
             rows   3, 6, 10 | same as in memory: TRUE
   sqlite   ANY(SPLIT(NAME, " "), LEN(_) > 9)
             memory (E_SQL_SHAPE)
             rows   4, 9 | same as in memory: TRUE
   mariadb  COUNTRY $== "PL" AND TIER $!= "standard"
             sql    ((CAST(`country` AS CHAR) COLLATE utf8mb4_nopad_bin = CAST('PL' AS CHAR) COLLATE utf8mb4_nopad_bin) AND (CAST(`tier` AS CHAR) COLLATE utf8mb4_nopad_bin <> CAST('standard' AS CHAR) COLLATE utf8mb4_nopad_bin))
             rows   1, 6, 9 | same as in memory: TRUE
   mariadb  COUNTRY $== "PL" AND CREDIT_LIMIT >= 1000
             sql    ((CAST(`country` AS CHAR) COLLATE utf8mb4_nopad_bin = CAST('PL' AS CHAR) COLLATE utf8mb4_nopad_bin) AND (CASE WHEN (`credit_limit` REGEXP '\\A-?[0-9]+(\\.[0-9]+)?\\z') THEN CAST(`credit_limit` AS DECIMAL(65,10)) ELSE NULL END >= 1000))
             rows   1, 6, 9 | same as in memory: TRUE
   mariadb  RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE)
             sql    (`postcode` COLLATE utf8mb4_nopad_bin REGEXP '(?s)^[0-9]{2}-[0-9]{3}$')
             rows   1, 4, 9, 10 | same as in memory: TRUE
   mariadb  IS_BLANK(EMAIL) OR NOT RMATCH('^[^@ ]+@[^@ ]+$', EMAIL)
             sql    (((`email` IS NULL) OR (REGEXP_REPLACE(`email`, '^[ \\t\\r\\n]+|[ \\t\\r\\n]+$', '') = '')) OR (NOT (`email` COLLATE utf8mb4_nopad_bin REGEXP '(?s)^[^@ ]+@[^@ ]+$')))
             rows   3, 6, 10 | same as in memory: TRUE
   mariadb  ANY(SPLIT(NAME, " "), LEN(_) > 9)
             memory (E_SQL_SHAPE)
             rows   4, 9 | same as in memory: TRUE
2. described bindings
   STATUS $== "paid" AND TOTAL >= MIN_TOTAL
             sql    (("o"."status" = 'paid') AND (CAST("o"."total" AS NUMERIC) >= CAST('100.00' AS NUMERIC)))
             params paid, 100.00
             rows   1, 3, 5, 6, 7, 9, 11, 12, 13, 17, 18, 19 | same as in memory: TRUE
   ANY(TAGS, _ $== "gift") AND CHANNEL $== "web"
             sql    ((((CAST("o"."tag1" AS TEXT) COLLATE "C" = CAST('gift' AS TEXT) COLLATE "C") OR (CAST("o"."tag2" AS TEXT) COLLATE "C" = CAST('gift' AS TEXT) COLLATE "C")) OR (CAST("o"."tag3" AS TEXT) COLLATE "C" = CAST('gift' AS TEXT) COLLATE "C")) AND ("o"."channel" = 'web'))
             params gift, gift, gift, web
             rows   4, 12, 20 | same as in memory: TRUE
   COUNT(ITEMS) >= 3 AND ALL(ITEMS, I, I["qty"] > 0)
             sql    (((SELECT COUNT(*) FROM "order_items" "i" WHERE "i"."order_id" = "o"."id") >= 3) AND NOT EXISTS (SELECT 1 FROM "order_items" "i" WHERE "i"."order_id" = "o"."id" AND (("i"."qty" > 0)) IS NOT TRUE))
             rows   1, 2, 6, 9, 13, 14, 17, 18 | same as in memory: TRUE
   SUM(ITEMS, I, I["qty"] * I["price"]) != TOTAL
             sql    ((SELECT COALESCE(SUM((CAST("i"."qty" AS NUMERIC) * CAST("i"."price" AS NUMERIC))), 0) FROM "order_items" "i" WHERE "i"."order_id" = "o"."id") <> "o"."total")
             rows   3, 10, 17 | same as in memory: TRUE
   ANY(ITEMS, I, LEFT(I["sku"], 3) $== "GM-")
             sql    EXISTS (SELECT 1 FROM "order_items" "i" WHERE "i"."order_id" = "o"."id" AND ((CAST(left(CAST("i"."sku" AS TEXT), CAST(3 AS INTEGER)) AS TEXT) COLLATE "C" = CAST('GM-' AS TEXT) COLLATE "C")) IS TRUE)
             params GM-
             rows   1, 2, 6, 7, 9, 12, 13, 14, 17, 19 | same as in memory: TRUE
```

## Choosing between them

| | Naive bindings | Described bindings |
|---|---|---|
| What the application writes | one line, from `dependencies()` | a binding per variable |
| Numbers | guarded casts; refused where the dialect cannot guard | plain comparisons, index-friendly |
| Text | compared under a binary collation | plain equality where declared `exact` |
| Lists of columns, related tables | not reachable | `columns`, `relation` |
| When a rule is refused | the rule runs in memory over loaded rows | the same |

The naive form is a sound default for rules written by users — saved searches,
filters, alerts — over a table the application does not want to describe in
detail. The described form is what an application uses for its own rules over its
own schema. Either way, a rule SQL cannot express faithfully never reaches the
database.

The next step is whole queries: [SQL pipelines](sql-pipelines.md). The binding
constructors, the dialects, the output modes and the refusal codes are in the
[SQL reference](../sql.md).
