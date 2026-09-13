#!/usr/bin/env php
<?php
// Focused regression checks for the PHP optimizer paths ported from Lisp.

declare(strict_types=1);

require_once __DIR__ . '/../php/src/Sql/bootstrap.php';

use Sel\Optimizer;
use Sel\Sel;
use Sel\Dec;
use Sel\Sql\Binding;
use Sel\Sql\Sql;

/** @param array<string,mixed> $condition */
function check(bool $condition, string $message): void
{
    global $checks;
    $checks++;
    if (!$condition) {
        fwrite(STDERR, "FAIL {$message}\n");
        exit(1);
    }
}

/** @return list<array<string,mixed>> */
function optimized_steps(string $source): array
{
    $ast = Optimizer::optimize(Sel::compile($source)->ast, true);
    return Optimizer::unwindPipeline($ast)['steps'];
}

/** @param list<array<string,mixed>> $steps @return list<string> */
function step_names(array $steps): array
{
    return array_map(static fn (array $step): string => $step['name'], $steps);
}

$checks = 0;

$folded = Optimizer::optimize(Sel::compile('1 + 2')->ast, true);
check($folded['t'] === 'num' && $folded['v'] === '3', 'numeric literal folding');

$dead = Optimizer::optimize(Sel::compile('FALSE AND (1 / 0 > 0)')->ast, true);
check($dead['t'] === 'bool' && $dead['v'] === false, 'short-circuit literal folding');

$branch = Optimizer::optimize(Sel::compile('IF(TRUE, 2 + 3, 1 / 0)')->ast, true);
check($branch['t'] === 'num' && $branch['v'] === '5', 'literal IF folding');

$leftQualified = optimized_steps(
    'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
    . ' .> FILTER(_["orders"]["status"] $== "COMPLETED")',
);
check(array_map(static fn (array $step): string => $step['name'], $leftQualified) === ['FILTER', 'LINK'],
    'qualified left join-filter pushdown');

$rightQualified = optimized_steps(
    'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
    . ' .> FILTER(_["customers"]["country"] $== "DE")',
);
check(count($rightQualified) === 1 && $rightQualified[0]['name'] === 'LINK'
    && ($rightQualified[0]['args'][1]['name'] ?? null) === 'FILTER',
    'qualified right join-filter pushdown');

$fixedPoint = optimized_steps(
    'ORDERS .> FILTER(_["status"] $== "ACTIVE")'
    . ' .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
    . ' .> FILTER(_["orders"]["status"] $== "ACTIVE")',
);
check(step_names($fixedPoint) === ['FILTER', 'LINK'],
    'join pushdown returns to the logical fixed point');

$groupKey = optimized_steps(
    'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
    . ' .> FILTER(_["orders"]["status"] $== _K)',
);
check(step_names($groupKey) === ['LINK', 'FILTER'],
    '_K is an unknown join dependency');

$takeFusion = optimized_steps('(3, 1, 2) .> TAKE(2) .> TAKE(1)');
check(step_names($takeFusion) === ['TAKE'] && $takeFusion[0]['args'][1]['v'] === '1',
    'TAKE fusion');

$dropFusion = optimized_steps('(3, 1, 2) .> DROP(1) .> DROP(1)');
check(step_names($dropFusion) === ['DROP'] && $dropFusion[0]['args'][1]['v'] === '2',
    'DROP fusion');

$topFusion = optimized_steps('(3, 1, 2) .> SORT() .> TAKE(1)');
check(step_names($topFusion) === ['TOP'], 'SORT plus TAKE top fusion');

$topComputed = optimized_steps(
    '((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"], "y", _["x"] + 1))'
    . ' .> TOP(r, r["y"], 1)',
);
check(step_names($topComputed) === ['MAP', 'TOP'], 'TOP explicit binder key analysis');

$notFold = Optimizer::optimize(Sel::compile('NOT FALSE')->ast, false);
check($notFold['t'] === 'bool' && $notFold['pos']['col'] === 1,
    'unary NOT fold keeps operator position');

$mapFilterPush = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["x"] > 0)',
);
check(step_names($mapFilterPush) === ['FILTER', 'MAP'], 'MAP filter pushdown');
$caseSensitiveMapFilter = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["X"] > 0)',
);
check(step_names($caseSensitiveMapFilter) === ['MAP', 'FILTER'],
    'MAP filter pushdown preserves case-sensitive field names');

$sortFilterPush = optimized_steps('(1, 2) .> SORT() .> FILTER(_ > 0)');
check(step_names($sortFilterPush) === ['FILTER', 'SORT'], 'SORT filter pushdown');

$selectFilterPush = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> SELECT_COLS("x") .> FILTER(_["x"] > 0)',
);
check(step_names($selectFilterPush) === ['FILTER', 'SELECT_COLS'], 'SELECT_COLS filter pushdown');

$lateMaterialization = optimized_steps(
    '((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"], "y", _["x"] + 1))'
    . ' .> SORT_BY(_["x"], "DESC")',
);
check(step_names($lateMaterialization) === ['SORT_BY', 'MAP'], 'SORT_BY late materialization');
check(($lateMaterialization[1]['args'][1]['name'] ?? null) === 'LAZY_RECORD',
    'MAP projection lazy-record conversion');

$filterFusion = optimized_steps('(1, 2) .> FILTER(_ > 0) .> FILTER(_ < 3)');
check(step_names($filterFusion) === ['FILTER']
    && ($filterFusion[0]['args'][1]['op'] ?? null) === 'AND', 'FILTER fusion');

$sortPrune = optimized_steps('(1, 2) .> SORT() .> SORT_DESC()');
check(step_names($sortPrune) === ['SORT_DESC'], 'redundant sort elimination');

$dedupePrune = optimized_steps('(1, 2) .> DEDUPE() .> DISTINCT()');
check(step_names($dedupePrune) === ['DEDUPE'], 'redundant dedupe elimination');

$trueFilter = optimized_steps('(1, 2) .> FILTER(TRUE)');
check($trueFilter === [], 'trivial TRUE filter elimination');

$bindings = [
    'CUSTOMERS' => Binding::relation('customers', 'customers', [
        'ID' => Binding::column('id', 'customers', 'NUM'),
    ]),
];
$hybrid = Sql::planHybrid(
    Sel::compile("X = 1; CUSTOMERS .> FILTER(_['id'] > X) .> TAKE(1)"),
    'postgresql',
    $bindings,
);
check($hybrid->pureSql && $hybrid->sqlStatement !== null, 'hybrid normalization before planning');
check(Dec::format(Dec::fromInt(PHP_INT_MIN)) === (string) PHP_INT_MIN, 'minimum native integer conversion');

echo "PHP optimizer checks: {$checks} passed\n";
