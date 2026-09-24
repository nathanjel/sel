<?php
// An entity-attribute-value catalogue — pipelines over EAV rows, from PHP.
//
//   tools/check-usage.sh sql-eav               (starts the databases for you)
//
// entities holds one row per product; attributes holds one (entity, name, value)
// row per property, every value TEXT, whatever it means. That shape is flexible
// to write and awkward to ask: "red or blue, and made of steel" is two EXISTS
// subqueries, and a price is a number only when the text says so. SEL pushes
// down what SQLite can answer exactly (text equality, joins, grouping) and keeps
// the rest — pivoting attributes into records, comparing text as a number — in
// memory, where ISNUM can say what SQLite cannot.
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
    'PRODUCTS' => relation('entities', 'e', ['id' => 'NUM', 'sku' => 'TEXT', 'kind' => 'TEXT']),
    'ATTRS'    => relation('attributes', 'a', ['entity_id' => 'NUM', 'name' => 'TEXT',
                                               'value' => 'TEXT']),
];
// EXAMPLE-END bindings

const PIPELINES = [
    ['red or blue, and steel', 'red-or-blue-steel.sel'],
    ['products per colour', 'products-per-colour.sel'],
    ['priced under 60.00, pivoted', 'priced-under-60.sel'],
];

$conn = connect('sqlite');

// The same tables in memory, for the comparison at the end of each pipeline.
$tables = Value::none();
foreach ([['PRODUCTS', 'entities', 'id'],
          ['ATTRS', 'attributes', 'entity_id, name']] as [$name, $table, $key]) {
    $tables->set($name, query($conn, "SELECT * FROM $table ORDER BY $key"));
}

foreach (PIPELINES as $i => [$title, $file]) {
    // EXAMPLE-BEGIN run
    $program = Sel::compile(file_get_contents(__DIR__ . '/' . $file));
    $plan = Sql::planHybrid($program, 'sqlite', $schema);
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
