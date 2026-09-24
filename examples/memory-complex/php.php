<?php
// The same report with no database at all — generated data, in memory, from PHP.
//
//   php examples/memory-complex/php.php
//
// examples/sql-complex loads the support desk from PostgreSQL. Here the same
// rows come from examples/lib/tickets-generate.sel — a SEL program that builds
// them deterministically, and the source the PostgreSQL seed was rendered from
// — and the same report runs over them. Nothing below opens a connection:
// the plan for MariaDB is computed from the bindings alone, and it says what it
// said for PostgreSQL, that none of this report is SQL's to answer.
//
// The four files beside this one print byte-identical output.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';
require_once __DIR__ . '/../../php/src/Sql/bootstrap.php';
require_once __DIR__ . '/../lib/db.php';

use Sel\Sel;
use Sel\Sql\Binding;
use Sel\Sql\Sql;
use function Db\render;

function read(string $name): string
{
    return file_get_contents(__DIR__ . '/../lib/' . $name);
}

// EXAMPLE-BEGIN generate
$data = Sel::evaluate(read('tickets-generate.sel'));
echo "1. generated in memory\n";
foreach ($data->keys() as $name) {
    printf("   %-10s  %3d rows\n", $name, $data->get($name)->size());
}

$report = Sel::compile(read('tickets-report.sel'));
echo "2. the report\n";
echo render($report->run($data), '   | '), "\n";
// EXAMPLE-END generate

// Planning needs the schema, not a server: the bindings sql-complex describes
// PostgreSQL with, asked about MariaDB this time.
function relation(string $table, string $alias, array $fields): Binding
{
    $columns = [];
    foreach ($fields as $name => $kind) {
        $columns[$name] = Binding::column($name, $alias, $kind);
    }
    return Binding::relation($table, $alias, fields: $columns);
}

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
$plan = Sql::planHybrid($report, 'mariadb', $schema);
echo "3. planned for MariaDB, without connecting\n";
echo '   plan        ', $plan->pureMemory ? 'pure_memory' : 'pushed down', "\n";
echo '   reads       ', implode(', ', $plan->sourceTables), "\n";
