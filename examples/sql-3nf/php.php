<?php
// A third-normal-form shop — joins, grouping and a split, from PHP.
//
//   tools/check-usage.sh sql-3nf               (starts the databases for you)
//
// categories, products, customers, orders and order_lines, each fact stored
// once. The first pipeline is SQL from end to end. The second assigns every
// customer to an A/B cohort by CRC32 of their e-mail — the application's own
// hashing, which PostgreSQL has no spelling for — so the database joins, filters
// and multiplies, and the cohorts are computed in memory over what it returned.
//
// The four files beside this one print byte-identical output.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';
require_once __DIR__ . '/../../php/src/Sql/bootstrap.php';
require_once __DIR__ . '/../lib/db.php';

use Sel\Sel;
use Sel\Sql\Binding;
use Sel\Sql\Sql;
use Sel\Value;
use function Db\{connect, query, render, runner};

// EXAMPLE-BEGIN bindings
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
// EXAMPLE-END bindings

const PIPELINES = [
    ['paid revenue per product since March', 'revenue-per-product.sel'],
    ['paid revenue per experiment cohort', 'revenue-per-cohort.sel'],
];

$conn = connect('postgresql');

// The same tables in memory, for the comparison at the end of each pipeline.
$tables = Value::none();
foreach ([['CUSTOMERS', 'customers', 'customer_id'], ['ORDERS', 'orders', 'order_id'],
          ['LINES', 'order_lines', 'order_id, line_no'],
          ['PRODUCTS', 'products', 'product_id']] as [$name, $table, $key]) {
    $tables->set($name, query($conn, "SELECT * FROM $table ORDER BY $key"));
}

foreach (PIPELINES as $i => [$title, $file]) {
    // EXAMPLE-BEGIN run
    $program = Sel::compile(file_get_contents(__DIR__ . '/' . $file));
    $plan = Sql::planHybrid($program, 'postgresql', $schema);
    $rows = Sql::executeHybrid($plan, runner($conn), $plan->pureMemory ? $tables : null);
    // EXAMPLE-END run
    $kind = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
    echo $i + 1, ". $title\n";
    echo '   plan        ', $kind, "\n";
    echo '   reads       ', implode(', ', $plan->sourceTables), "\n";
    if ($plan->sqlStatement !== null) {
        echo '   sql         ', $plan->sqlStatement->asStatement(), "\n";
    }
    echo render($rows, '   | '), "\n";
    echo '   in memory   ', $program->run($tables->copy())->dump() === $rows->dump()
        ? 'same rows' : 'DIFFERENT', "\n";
}
