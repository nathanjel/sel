#!/usr/bin/env php
<?php
// SQL API parity probe: the planner's contract through every host's own SQL
// binding. tools/check-sqlapi.sh diffs the reports of the hosts that carry the
// SQL layer (the JS bundles do not; they print nothing and are left out).
// Three programs -- one the planner pushes down whole, one it splits into a SQL
// prefix and an in-memory continuation, one it keeps in memory -- and the same
// nine questions about each plan: its classification, dialect, statement, prefix
// and continuation presence, the continuation's dependencies, its source
// variable, the physical source tables and the selected member. The probe NAMES
// are the contract and the VALUES are compared; each host spells its accessors
// its own way (SEL-0044).
declare(strict_types=1);
require_once __DIR__ . '/../php/src/Sql/bootstrap.php';

use Sel\Sel;
use Sel\Sql\Binding;
use Sel\Sql\Sql;

$out = [];
$n = 0;
function say(string $name, string $value): void
{
    global $out, $n;
    $out[] = sprintf('%02d %s = %s', ++$n, $name, $value);
}
$b = fn (bool $x): string => $x ? 'true' : 'false';
$bindings = [
    'ORDERS' => Binding::relation('orders', 'o', [
        'ID' => Binding::column('id', 'o', 'NUM'), 'CUSTOMER_ID' => Binding::column('customer_id', 'o', 'NUM'),
        'AMOUNT' => Binding::column('amount', 'o', 'NUM'), 'NAME' => Binding::column('name', 'o', 'TEXT')]),
    'CUSTOMERS' => Binding::relation('customers', 'c', [
        'ID' => Binding::column('id', 'c', 'NUM'), 'NAME' => Binding::column('name', 'c', 'TEXT')]),
];
$probe = function (string $label, string $source) use ($bindings, $b): void {
    $plan = Sql::planHybrid(Sel::compile($source), 'mariadb', $bindings);
    say("plan.$label.kind", $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid'));
    say("plan.$label.dialect", $plan->dialect ?? '-');
    say("plan.$label.statement", $plan->sqlStatement !== null ? $plan->sqlStatement->asStatement() : '-');
    say("plan.$label.prefix.present", $b($plan->sqlPrefixAst !== null));
    say("plan.$label.continuation.present", $b($plan->continuationAst !== null));
    say("plan.$label.continuation.deps", $plan->continuationProgram !== null ? implode(' ', $plan->continuationProgram->dependencies()) : '-');
    say("plan.$label.source.var", $plan->continuationSourceVar);
    say("plan.$label.tables", implode(',', $plan->sourceTables));
    say("plan.$label.selected.member", $plan->selectedMember !== null ? 'present' : '-');
};
$probe('sql', 'ORDERS .> FILTER(_["AMOUNT"] > 10) .> MAP(RECORD("id", _["ID"], "amount", _["AMOUNT"]))');
$probe('hybrid', 'ORDERS .> SORT_BY(_["AMOUNT"]) .> FILTER(_K > 1)');
$probe('memory', 'A += 1; ORDERS .> TAKE(1)');
// The canonical flag is public: an application (and php/bin/sqlo) reads it to
// know the fragment promised a spelling, not only a value (SEL-0058).
$fragmentProbe = function (string $label, string $dialect, string $source) use ($bindings, $b): void {
    $f = Sql::translate(Sel::compile($source), $dialect, $bindings);
    say("fragment.$label.kind", $f->kind);
    say("fragment.$label.canonical", $b($f->canonical));
    say("fragment.$label.caveats", implode(',', $f->caveats) ?: '-');
};
$fragmentProbe('canon.postgresql', 'postgresql', 'CANON(1.50)');
$fragmentProbe('canon.mariadb', 'mariadb', 'CANON(1.50)');
$fragmentProbe('canon.sqlite', 'sqlite', 'CANON(1.50)');
$fragmentProbe('abs.postgresql', 'postgresql', 'ABS(1.50)');

echo implode("\n", $out), "\n";
