<?php
// SQL conditions — one rule as a WHERE clause, from PHP.
//
//   tools/check-usage.sh sql-conditions        (starts the databases for you)
//
// A rule written for the application can filter rows where they live. Part 1 is
// the naive integration: the host knows nothing about the schema except that a
// variable is a column of the same name. Part 2 describes the schema — types,
// a list of columns, a related table, a parameter — and gets SQL that is both
// tighter and able to say more. Either way a rule SQL cannot express is refused
// whole, and runs in memory instead; every answer below is checked against the
// in-memory one.
//
// The four files beside this one print byte-identical output.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';
require_once __DIR__ . '/../../php/src/Sql/bootstrap.php';
require_once __DIR__ . '/../lib/db.php';

use Sel\Program;
use Sel\Sel;
use Sel\Sql\Binding;
use Sel\Sql\Sql;
use Sel\Sql\SqlError;
use Sel\Value;
use function Db\{connect, query};

/** @param list<Value> $records */
function ids(array $records): string
{
    $ids = implode(', ', array_map(fn (Value $record) => $record->get('id')->asText(), $records));
    return $ids === '' ? '(none)' : $ids;
}

/** translate() says why tryTranslate() returned null. */
function refusal(Program $rule, string $dialect, array $bindings): string
{
    try {
        Sql::translate($rule, $dialect, $bindings);
        return 'translated';
    } catch (SqlError $e) {
        return $e->code;
    }
}

/** A row as a rule's context: SEL names are upper case, columns are not. */
function contextOf(Value $row): Value
{
    $ctx = Value::none();
    foreach ($row->entries() as [$column, $value]) {
        $ctx->set(strtoupper($column), $value);
    }
    return $ctx;
}

// 1 — naive: a column per variable, nothing else known ---------------------------

const RULES = [
    'COUNTRY $== "PL" AND TIER $!= "standard"',
    'COUNTRY $== "PL" AND CREDIT_LIMIT >= 1000',
    "RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE)",
    'IS_BLANK(EMAIL) OR NOT RMATCH(\'^[^@ ]+@[^@ ]+$\', EMAIL)',
    'ANY(SPLIT(NAME, " "), LEN(_) > 9)',
];

echo "1. naive bindings\n";
foreach (['sqlite', 'mariadb'] as $dialect) {
    $conn = connect($dialect);
    $everyone = query($conn, 'SELECT * FROM customers ORDER BY id');
    foreach (RULES as $source) {
        // EXAMPLE-BEGIN naive
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
        // EXAMPLE-END naive
        $inMemory = array_values(array_filter($everyone->values(),
            fn (Value $row) => $rule->run(contextOf($row))->asBool()));
        printf("   %-8s %s\n", $dialect, $source);
        echo '             ', $where !== null ? 'sql    ' . $where->asCondition()
            : 'memory (' . refusal($rule, $dialect, $bindings) . ')', "\n";
        echo '             rows   ', ids($rows->values()), ' | same as in memory: ',
            ids($rows->values()) === ids($inMemory) ? 'TRUE' : 'FALSE', "\n";
    }
}

// 2 — involved: the host describes its schema ------------------------------------

echo "2. described bindings\n";
$conn = connect('postgresql');
// EXAMPLE-BEGIN involved
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
// EXAMPLE-END involved

$orders = query($conn, 'SELECT * FROM orders ORDER BY id');
$items = query($conn, 'SELECT * FROM order_items ORDER BY order_id, line_no');

/** What the rule sees in memory: the same names, as values. */
function orderContext(Value $order, Value $items): Value
{
    $ctx = Value::none();
    foreach (['status', 'total', 'channel'] as $name) {
        $ctx->set(strtoupper($name), $order->get($name));
    }
    $tags = Value::none();
    foreach (['tag1', 'tag2', 'tag3'] as $i => $column) {
        $tags->set((string) ($i + 1), $order->get($column));
    }
    $ctx->set('TAGS', $tags);
    $lines = Value::none();
    foreach ($items->values() as $item) {
        if ($item->get('order_id')->asText() === $order->get('id')->asText()) {
            $lines->set((string) ($lines->size() + 1), $item);
        }
    }
    $ctx->set('ITEMS', $lines);
    $ctx->set('MIN_TOTAL', Value::text('100.00'));
    return $ctx;
}

foreach ([
    'STATUS $== "paid" AND TOTAL >= MIN_TOTAL',
    'ANY(TAGS, _ $== "gift") AND CHANNEL $== "web"',
    'COUNT(ITEMS) >= 3 AND ALL(ITEMS, I, I["qty"] > 0)',
    'SUM(ITEMS, I, I["qty"] * I["price"]) != TOTAL',
    'ANY(ITEMS, I, LEFT(I["sku"], 3) $== "GM-")',
] as $source) {
    // EXAMPLE-BEGIN involved-run
    $rule = Sel::compile($source);
    $where = Sql::translate($rule, 'postgresql', $bindings);
    $sql = 'SELECT id FROM orders o WHERE ' . $where->asCondition('params') . ' ORDER BY id';
    $rows = query($conn, $sql, $where->bindings());
    // EXAMPLE-END involved-run
    $inMemory = array_values(array_filter($orders->values(),
        fn (Value $o) => $rule->run(orderContext($o, $items))->asBool()));
    echo "   $source\n";
    echo '             sql    ', $where->asCondition(), "\n";
    if ($where->bindings()) {
        echo '             params ',
            implode(', ', array_map(fn (Value $v) => $v->asText(), $where->bindings())), "\n";
    }
    echo '             rows   ', ids($rows->values()), ' | same as in memory: ',
        ids($rows->values()) === ids($inMemory) ? 'TRUE' : 'FALSE', "\n";
}
