<?php
// A report no database can take a share of — SQL loads, SEL computes, from PHP.
//
//   tools/check-usage.sh sql-complex           (starts the databases for you)
//
// The support desk's SLA report (examples/lib/tickets-report.sel) digs incident
// numbers out of subjects with RGROUPS and searches the event log for each
// ticket's first answer. Sql::planHybrid() finds no step of it PostgreSQL can
// answer, and says so: pureMemory. So the database's job shrinks to handing
// over the tables — with the SELECTs written by SEL too, from the same bindings
// — and the report runs in memory over what came back. The last line checks it
// against the report over the data generated in memory (examples/memory-complex),
// which is where the rows in this database came from.
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
use function Db\{connect, query, render};

function read(string $name): string
{
    return file_get_contents(__DIR__ . '/../lib/' . $name);
}

function relation(string $table, string $alias, array $fields): Binding
{
    $columns = [];
    foreach ($fields as $name => $kind) {
        $columns[$name] = Binding::column($name, $alias, $kind);
    }
    return Binding::relation($table, $alias, fields: $columns);
}

// EXAMPLE-BEGIN load
$schema = [
    'TEAMS'     => relation('teams', 'g', ['team_id' => 'NUM', 'team' => 'TEXT']),
    'CUSTOMERS' => relation('customers', 'c', ['customer_id' => 'NUM', 'customer' => 'TEXT',
                                               'plan' => 'TEXT']),
    'SLA'       => relation('sla', 's', ['plan' => 'TEXT', 'priority' => 'TEXT',
                                         'respond_within' => 'NUM', 'resolve_within' => 'NUM']),
    'TICKETS'   => relation('tickets', 't', ['ticket_id' => 'NUM', 'customer_id' => 'NUM',
                                             'team_id' => 'NUM', 'priority' => 'TEXT',
                                             'subject' => 'TEXT', 'opened_at' => 'NUM',
                                             'closed_at' => 'NUM']),
    'EVENTS'    => relation('events', 'e', ['event_id' => 'NUM', 'ticket_id' => 'NUM',
                                            'seq' => 'NUM', 'at' => 'NUM', 'kind' => 'TEXT',
                                            'actor' => 'TEXT']),
];
$keys = ['TEAMS' => 'team_id', 'CUSTOMERS' => 'customer_id', 'SLA' => 'plan',
         'TICKETS' => 'ticket_id', 'EVENTS' => 'event_id'];

$report = Sel::compile(read('tickets-report.sel'));
$plan = Sql::planHybrid($report, 'postgresql', $schema);
echo "1. the report, planned for PostgreSQL\n";
echo '   plan        ', $plan->pureMemory ? 'pure_memory' : 'pushed down', "\n";
echo '   reads       ', implode(', ', $plan->sourceTables), "\n";

echo "2. so SQL only loads the tables it reads\n";
$conn = connect('postgresql');
$tables = Value::none();
foreach ($report->dependencies() as $name) {
    $load = Sel::compile("$name .> SORT_BY(_[\"{$keys[$name]}\"])");
    $sql = Sql::translateStatement($load, 'postgresql', $schema)->asStatement();
    $tables->set($name, query($conn, $sql));
    printf("   %-10s  %3d rows  %s\n", $name, $tables->get($name)->size(), $sql);
}

echo "3. and SEL computes the report over them\n";
$result = $report->run($tables);
echo render($result, '   | '), "\n";
// EXAMPLE-END load

$generated = Sel::evaluate(read('tickets-generate.sel'));
echo '   over the generated rows: ',
    $report->run($generated)->dump() === $result->dump() ? 'same report' : 'DIFFERENT', "\n";
