<?php
// A star schema — whole pipelines in SQL, and split with memory, from PHP.
//
//   tools/check-usage.sh sql-star              (starts the databases for you)
//
// fact_sales sits in the middle; dim_date, dim_store and dim_product around it.
// The application describes each table once, as a relation binding, and then
// hands SEL whole pipelines. Sql::planHybrid() decides how much of each one the
// database can answer: all of it (pureSql), a prefix of it (hybrid, the rest
// runs in memory over the rows the prefix returned), or none of it
// (pureMemory). The answer is checked against a run of the same program over
// the tables loaded into memory.
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
// EXAMPLE-END bindings

const PIPELINES = [
    ['revenue by category, first quarter', 'revenue-by-category.sel'],
    ['best-selling product per region, stores only', 'best-product-per-region.sel'],
];

$conn = connect('postgresql');

// The same tables in memory, for the comparison at the end of each pipeline.
$tables = Value::none();
foreach ([['SALES', 'fact_sales', 'sale_id'], ['DATES', 'dim_date', 'date_key'],
          ['STORES', 'dim_store', 'store_key'],
          ['PRODUCTS', 'dim_product', 'product_key']] as [$name, $table, $key]) {
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
