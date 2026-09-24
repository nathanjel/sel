<?php
// An unnormalised export — one wide table, from PHP.
//
//   tools/check-usage.sh sql-flat              (starts the databases for you)
//
// order_export repeats the customer and the product on every line, the way a
// spreadsheet or a nightly dump does, with the inconsistencies that come with
// it: the same person under two spellings of their name and e-mail. The first
// pipeline groups in SQL. The second normalises e-mails and counts distinct
// customers, and the planner keeps the normalised values out of MariaDB's
// hands: its collation would decide which of them are "the same", and SEL's
// identity is exact bytes. The third explodes a `;`-separated column, which no
// SQL step can express, so it runs in memory entirely.
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
    'EXPORT' => relation('order_export', 'x', ['line_id' => 'NUM', 'order_no' => 'TEXT',
                                               'order_date' => 'TEXT', 'customer_name' => 'TEXT',
                                               'customer_email' => 'TEXT',
                                               'customer_city' => 'TEXT', 'sku' => 'TEXT',
                                               'product_name' => 'TEXT', 'category' => 'TEXT',
                                               'qty' => 'NUM', 'unit_price' => 'NUM',
                                               'tags' => 'TEXT']),
];
// EXAMPLE-END bindings

const PIPELINES = [
    ['revenue per city, February and March', 'revenue-per-city.sel'],
    ['distinct customers per city, by normalised e-mail', 'customers-per-city.sel'],
    ['lines per tag', 'lines-per-tag.sel'],
];

$conn = connect('mariadb');

// The same tables in memory, for the comparison at the end of each pipeline.
$tables = Value::none();
foreach ([['EXPORT', 'order_export', 'line_id']] as [$name, $table, $key]) {
    $tables->set($name, query($conn, "SELECT * FROM $table ORDER BY $key"));
}

foreach (PIPELINES as $i => [$title, $file]) {
    // EXAMPLE-BEGIN run
    $program = Sel::compile(file_get_contents(__DIR__ . '/' . $file));
    $plan = Sql::planHybrid($program, 'mariadb', $schema);
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
