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

use Sel\Args;
use Sel\Sel;
use Sel\Value;
use Sel\Sql\Binding;
use Sel\Sql\Emit;
use Sel\Sql\Fragment;
use Sel\Sql\Map;
use Sel\Sql\Sql;
use Sel\Sql\SqlError;

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

// --- host functions with a SQL spelling (spec §8.1, sql/MAP.md §4.7) ----------
// The registration order, the caveat, strict mode, a LIST argument, a builder,
// reset() and a re-registration, through this host's own spelling of the API.
$host = ['T' => Binding::column('title', 't', 'TEXT')];
$attempt = function (callable $fn): string {
    try {
        $fn();
        return 'accepted';
    } catch (SqlError $e) {
        return "SqlError {$e->code}";
    } catch (\LogicException $e) {
        return 'refused';
    }
};
$slug = static fn (Args $a): Value => Value::text('local:' . $a->text(0));

say('host.spell.before-register', $attempt(fn () => Map::define(
    'postgresql', 'funcs', 'HSLUG', ['tpl' => 'slug({0})', 'ret' => 'TEXT'])));
Sel::registerFunction('HSLUG', 1, 1, $slug);
Map::define('postgresql', 'funcs', 'HSLUG', ['tpl' => 'slug({0})', 'ret' => 'TEXT', 'args' => ['TEXT']]);
$spelled = Sql::translate(Sel::compile('HSLUG(T) $== "x"'), 'postgresql', $host);
say('host.spell.condition', $spelled->asCondition());
say('host.spell.caveats', implode(',', $spelled->caveats) ?: '-');
say('host.spell.strict', $attempt(fn () => Sql::translate(
    Sel::compile('HSLUG(T)'), 'postgresql', $host, ['strict' => true])));
say('host.spell.other-dialect', $attempt(fn () => Sql::translate(Sel::compile('HSLUG(T)'), 'mariadb', $host)));

Sel::registerFunction('HHAS', 2, 2, static fn (Args $a): Value => Value::bool(false));
Map::define('postgresql', 'funcs', 'HHAS',
    ['tpl' => '({1} = ANY(ARRAY[{0}]))', 'ret' => 'BOOL', 'args' => ['LIST', 'TEXT']]);
$listed = Sql::translate(Sel::compile('HHAS(("a", "b"), "c")'), 'postgresql', $host);
say('host.spell.list.params', $listed->asCondition('params'));
say('host.spell.list.bound', implode(',', array_map(fn (Value $v) => $v->dump(), $listed->bindings())));

Sel::registerFunction('HWRAP', 1, 1, static fn (Args $a): Value => $a->val(0)->copy());
Map::defineBuilder('postgresql', 'funcs', 'HWRAP',
    static fn (Emit $emit, array $args, $at): Fragment
        => new Fragment(['wrap(', ...$args[0]->parts, ')'], 'TEXT', $emit->dialect()));
say('host.spell.builder', Sql::translate(Sel::compile('HWRAP(T)'), 'postgresql', $host)->asValue());

Sel::registerFunction('HSLUG', 1, 2, $slug);
say('host.spell.reregistered-arity', $attempt(fn () => Sql::translate(Sel::compile('HSLUG(T)'), 'postgresql', $host)));
Sel::registerFunction('HSLUG', 1, 1, $slug);
say('host.spell.arity-restored', $attempt(fn () => Sql::translate(Sel::compile('HSLUG(T)'), 'postgresql', $host)));
Map::reset();
say('host.spell.after-reset', $attempt(fn () => Sql::translate(Sel::compile('HSLUG(T)'), 'postgresql', $host)));
say('host.spell.after-reset.local', Sel::evaluate('HSLUG("A")')->asText());

echo implode("\n", $out), "\n";
