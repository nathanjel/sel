#!/usr/bin/env php
<?php
// Focused regression checks for the PHP optimizer paths ported from Lisp.

declare(strict_types=1);

require_once __DIR__ . '/../php/src/Sql/bootstrap.php';

use Sel\Optimizer;
use Sel\Program;
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
// The pushed conjuncts run tentatively under the join and the FILTER above
// keeps its whole predicate (spec §7.4; SEL-0051).
check(array_map(static fn (array $step): string => $step['name'], $leftQualified) === ['FILTER', 'LINK', 'FILTER']
    && !empty($leftQualified[0]['args'][1]['tentative']) && !empty($leftQualified[2]['args'][1]['pushedDown']),
    'qualified left join-filter pushdown');

$rightQualified = optimized_steps(
    'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
    . ' .> FILTER(_["customers"]["country"] $== "DE")',
);
check(count($rightQualified) === 2 && $rightQualified[0]['name'] === 'LINK'
    && ($rightQualified[0]['args'][1]['name'] ?? null) === 'FILTER'
    && !empty($rightQualified[0]['args'][1]['args'][1]['tentative'])
    && $rightQualified[1]['name'] === 'FILTER'
    && ($rightQualified[1]['args'][1]['remaining']['t'] ?? null) === 'bool',
    'qualified right join-filter pushdown; a wholly pushed predicate leaves TRUE as the remaining body');

// Only the leading run of conjuncts naming one side is pushed: a customer
// conjunct after an order conjunct stays above the join, as `remaining`.
$mixed = optimized_steps(
    'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
    . ' .> FILTER(_["orders"]["status"] $== "COMPLETED" AND _["customers"]["country"] $== "DE")',
);
check(array_map(static fn (array $step): string => $step['name'], $mixed) === ['FILTER', 'LINK', 'FILTER']
    && !empty($mixed[0]['args'][1]['tentative']) && ($mixed[1]['args'][1]['t'] ?? null) === 'var'
    && !empty($mixed[2]['args'][1]['pushedDown'])
    && ($mixed[2]['args'][1]['remaining']['op'] ?? null) === '$==',
    'a conjunct after the pushed run stays above the join as the remaining body');

// Only a read through the joined row's key names a side. `O["x"]`, `C["x"]`
// or `ORDERS["x"]` after the LINK is E_UNDEF_VAR / E_NO_KEY as written (spec
// §7.4: the binders are scoped to the predicate), so it is left where it is.
$binderAfterLink = optimized_steps(
    'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"])'
    . ' .> FILTER(C["id"] > 1)',
);
check(step_names($binderAfterLink) === ['LINK', 'FILTER']
    && ($binderAfterLink[0]['args'][1]['t'] ?? null) === 'var'
    && ($binderAfterLink[1]['args'][1]['l']['obj']['name'] ?? null) === 'C',
    'a right binder after the LINK is not pushed into the right side');
$leftBinderAfterLink = optimized_steps(
    'ORDERS .> LINK(CUSTOMERS, O, C, O["customer_id"] == C["id"])'
    . ' .> FILTER(O["id"] > 1)',
);
check(step_names($leftBinderAfterLink) === ['LINK', 'FILTER'],
    'a left binder after the LINK is not pushed into the left side');
$relationNameAfterLink = optimized_steps(
    'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
    . ' .> FILTER(CUSTOMERS["id"] > 1)',
);
check(step_names($relationNameAfterLink) === ['LINK', 'FILTER'],
    'a relation name after the LINK is not pushed into its side');

$fixedPoint = optimized_steps(
    'ORDERS .> FILTER(_["status"] $== "ACTIVE")'
    . ' .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
    . ' .> FILTER(_["orders"]["status"] $== "ACTIVE")',
);
check(step_names($fixedPoint) === ['FILTER', 'FILTER', 'LINK', 'FILTER'],
    'a pushed (tentative) FILTER does not fuse with the real FILTER already before the LINK');

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

// A hoisted child takes the folded node's position (spec §6.3: the operand an
// operator rejects is the IF or the AND, not the literal inside it); a branch
// with positions of its own is not hoisted at all. The run()-visible half of
// this is ctl.if.constant-condition-* and op.logic.*-keeps-the-*-position.
$ifFold = Optimizer::optimize(Sel::compile('1 + IF(TRUE, "x", 2)')->ast, false);
check($ifFold['r']['t'] === 'text' && $ifFold['r']['v'] === 'x' && $ifFold['r']['pos']['col'] === 5,
    'IF fold stamps the literal with the IF position');
$andFold = Optimizer::optimize(Sel::compile('1 + (FALSE AND TRUE)')->ast, false);
check($andFold['r']['t'] === 'bool' && $andFold['r']['v'] === false && $andFold['r']['pos']['col'] === 12,
    'AND short-circuit fold keeps operator position');
$orFold = Optimizer::optimize(Sel::compile('1 + (TRUE OR FALSE)')->ast, false);
check($orFold['r']['t'] === 'bool' && $orFold['r']['v'] === true && $orFold['r']['pos']['col'] === 11,
    'OR short-circuit fold keeps operator position');
$unfoldedIf = Optimizer::optimize(Sel::compile('IF(TRUE, 1 / 0, 2)')->ast, false);
check($unfoldedIf['t'] === 'call' && $unfoldedIf['name'] === 'IF' && $unfoldedIf['args'][1]['pos']['col'] === 12,
    'IF over a compound branch is not folded');
$unfoldedVar = Optimizer::optimize(Sel::compile('IF(TRUE, X, 2)')->ast, false);
check($unfoldedVar['t'] === 'call' && $unfoldedVar['name'] === 'IF',
    'IF over a variable branch is not folded');

// A FILTER moves in front of a MAP, a sort or a SELECT_COLS only when a
// later step renumbers the rows again without reading `_K`: FILTER keeps its
// input's keys and the three renumber (spec §7.3), so at the end of a
// pipeline the swap would change the answer's keys.
$mapFilterPush = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["x"] > 0) .> MAP(_["x"])',
);
check(step_names($mapFilterPush) === ['FILTER', 'MAP', 'MAP'], 'MAP filter pushdown');
$mapFilterEnd = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["x"] > 0)',
);
check(step_names($mapFilterEnd) === ['MAP', 'FILTER'], 'MAP filter pushdown keeps the keys at the end of a pipeline');
$mapFilterKeyRead = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["x"] > 0) .> MAP(_K)',
);
check(step_names($mapFilterKeyRead) === ['MAP', 'FILTER', 'MAP'], 'MAP filter pushdown keeps the keys a later step reads');
$mapFilterFused = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["x"] > 0) .> FILTER(_["x"] > 1) .> TAKE(1)',
);
check(step_names($mapFilterFused) === ['FILTER', 'MAP', 'TAKE'], 'MAP filter pushdown after the FILTERs fuse');
$caseSensitiveMapFilter = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["X"] > 0) .> MAP(_["x"])',
);
check(step_names($caseSensitiveMapFilter) === ['MAP', 'FILTER', 'MAP'],
    'MAP filter pushdown preserves case-sensitive field names');

$sortFilterPush = optimized_steps('(1, 2) .> SORT() .> FILTER(_ > 0) .> TAKE(1)');
check(step_names($sortFilterPush) === ['FILTER', 'TOP'], 'SORT filter pushdown');
$sortFilterEnd = optimized_steps('(1, 2) .> SORT() .> FILTER(_ > 0)');
check(step_names($sortFilterEnd) === ['SORT', 'FILTER'], 'SORT filter pushdown keeps the keys at the end of a pipeline');

$selectFilterPush = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> SELECT_COLS("x") .> FILTER(_["x"] > 0) .> MAP(_["x"])',
);
check(step_names($selectFilterPush) === ['FILTER', 'SELECT_COLS', 'MAP'], 'SELECT_COLS filter pushdown');
$selectFilterEnd = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> SELECT_COLS("x") .> FILTER(_["x"] > 0)',
);
check(step_names($selectFilterEnd) === ['SELECT_COLS', 'FILTER'], 'SELECT_COLS filter pushdown keeps the keys at the end of a pipeline');

$lateMaterialization = optimized_steps(
    '((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"], "y", _["x"] + 1))'
    . ' .> SORT_BY(_["x"], "DESC")',
);
check(step_names($lateMaterialization) === ['SORT_BY', 'MAP'], 'SORT_BY late materialization');
check(($lateMaterialization[1]['args'][1]['name'] ?? null) === 'RECORD',
    'MAP projection body stays RECORD after physical optimisation');

$filterFusion = optimized_steps('(1, 2) .> FILTER(_ > 0) .> FILTER(_ < 3)');
check(step_names($filterFusion) === ['FILTER']
    && ($filterFusion[0]['args'][1]['op'] ?? null) === 'AND', 'FILTER fusion');

$sortKept = optimized_steps('(1, 2) .> SORT() .> SORT_DESC()');
check(step_names($sortKept) === ['SORT', 'SORT_DESC'], 'a sort after a sort is kept: the sorts are stable and the first breaks ties');

$dedupePrune = optimized_steps('(1, 2) .> DEDUPE() .> DISTINCT()');
check(step_names($dedupePrune) === ['DEDUPE'], 'redundant dedupe elimination');

$trueFilter = optimized_steps('(1, 2) .> FILTER(TRUE)');
check($trueFilter === [], 'trivial TRUE filter elimination');
$keptFilter = optimized_steps('DATA .> FILTER(TRUE)');
check(step_names($keptFilter) === ['FILTER'], 'a first-step TRUE filter over a variable is kept (the source may be a scalar)');

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

// --- the hybrid planner's contract, the parts a host-local check can see ---
//
// sql/cases/25-hybrid-plans.sqlt holds the language-neutral version of these;
// what is here is what the shared fixtures cannot express in JSON options or
// cannot observe through the runner: an optimiser option reaching the
// optimiser, the run() cache, and immutability across a real run().

/** @param array<string,mixed> $ast */
function snapshot(array $ast): string
{
    $strip = static function (array $node) use (&$strip): array {
        unset($node['spec']);
        foreach ($node as $k => $v) {
            if (is_array($v)) {
                $node[$k] = array_is_list($v) ? array_map($strip, $v) : $strip($v);
            }
        }
        return $node;
    };
    return serialize($strip($ast));
}

$orders = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];

$helper = Sql::planHybrid(Sel::compile('X = ORDERS; X .> TAKE(1)'), 'postgresql', $orders);
check($helper->pureSql, 'a helper assignment is normalised before planning');
check($helper->sourceTables === ['orders'], 'source tables are physical names');

$twoFilters = 'ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9)';
$fused = Sql::planHybrid(Sel::compile($twoFilters), 'postgresql', $orders);
$unfused = Sql::planHybrid(Sel::compile($twoFilters), 'postgresql', $orders, ['fuseFilters' => false]);
check(count(Optimizer::unwindPipeline($fused->sqlPrefixAst)['steps']) === 1
    && count(Optimizer::unwindPipeline($unfused->sqlPrefixAst)['steps']) === 2,
    'planner options reach the logical optimiser');

$refused = Sel::compile('A += 1; ORDERS .> TAKE(1)');
$refusedPlan = Sql::planHybrid($refused, 'postgresql', $orders);
check($refusedPlan->pureMemory && $refusedPlan->continuationProgram === $refused
    && $refusedPlan->continuationAst === $refused->ast,
    'a program stage 1 refuses is a pure-memory plan over the original program');
check($refusedPlan->sourceTables === ['orders'], 'a pure-memory plan still names its sources');

$reusable = Sel::compile(
    '(3, 1, 2) .> FILTER(NOT (_ < 1 + 1)) .> MAP(RECORD("x", _, "y", _ * 2, "z", _ + 1)) .> TAKE(2 * 1)');
$before = snapshot($reusable->ast);
$first = $reusable->run()->dump();
Optimizer::optimize($reusable->ast, false);
Optimizer::optimize($reusable->ast, true);
Sql::planHybrid($reusable, 'postgresql', $orders);
check(snapshot($reusable->ast) === $before, 'run, both optimisers and planning leave the AST unchanged');
check($reusable->run()->dump() === $first, 'repeated runs answer the same');
check($reusable->physicalAst() === $reusable->physicalAst(), 'the physical AST is built once');
// Review 2026-09-15 finding AA: keyed by the identity of $ast, as the other
// hosts are -- reassigning the whole tree is fine, and noticed.
$physical = $reusable->physicalAst();
$reusable->ast = Sel::compile('1 + 1')->ast;
check($reusable->physicalAst() !== $physical && $reusable->run()->asText() === '2',
    'reassigning ast drops the cache');

// Review 2026-09-15 finding D: foldConstants reaches the optimiser, as it does
// in JS and Python; only an explicit false disables folding.
$foldable = 'ORDERS .> FILTER(_["id"] > 1 + 1)';
$folded = Sql::planHybrid(Sel::compile($foldable), 'postgresql', $orders);
$unfolded = Sql::planHybrid(Sel::compile($foldable), 'postgresql', $orders, ['foldConstants' => false]);
check(str_contains($folded->sqlStatement->asStatement(), '> 2')
    && !str_contains($unfolded->sqlStatement->asStatement(), '> 2'),
    'foldConstants:false reaches the logical optimiser');

// --- a plan's continuation reports errors where run() does ---
//
// The planner folds one tree for both halves of a split, so a hoisted literal
// in the continuation carries the position the in-memory half will report.
// sql/cases/25-hybrid-plans.sqlt pins the SQL side of these; only executing
// the plan can see the position the memory side reports.
$rows = [['id' => '1'], ['id' => '2']];
$runner = static fn (string $sql, array $params): array => $rows;
$failure = static function (callable $fn): string {
    try {
        $fn();
        return 'no error';
    } catch (\Sel\SelError $e) {
        return "{$e->code}@{$e->line}:{$e->col}";
    }
};
//
// Review 2026-09-15 finding AJ: a helper assignment stage 1 inlines carried
// its definition-site position into the continuation, so `Y = "x"; ... + Y`
// reported 1:5 there and 1:45 from run(). The planner now inlines a helper
// only when it is a literal (stamped at the read), unwinds through a helper
// only at the pipeline's source, and keeps every other assignment the
// continuation reads in front of it, as written. LABEL is a context variable
// so that a helper can be something no fold turns into a literal.
foreach ([
    ['ORDERS .> TAKE(2) .> MAP(IF(TRUE, "x", 1) >= _["id"])', 'hybrid', 'E_NOT_NUM@1:26'],
    ['ORDERS .> TAKE(2) .> FILTER((FALSE AND TRUE) + _["id"] > 0)', 'hybrid', 'E_NOT_NUM@1:36'],
    ['ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"])', 'pure_memory', 'E_NOT_NUM@1:18'],
    ['Y = "x"; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)', 'hybrid', 'E_NOT_NUM@1:45'],
    ['X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _["id"])', 'hybrid', 'E_NOT_NUM@1:55'],
    ['Y = "a" & "b"; ORDERS .> TAKE(2) .> MAP(1 + Y)', 'hybrid', 'E_NOT_NUM@1:45'],
    ['Z = "abc"; ORDERS .> TAKE(1) .> FILTER(_["id"] > Z)', 'hybrid', 'E_NOT_NUM@1:50'],
    ['Y = "x"; (ORDERS .> TAKE(2)) .> MAP(_["id"] + Y)', 'hybrid', 'E_NOT_NUM@1:47'],
    ['Y = LABEL; ORDERS .> TAKE(2) .> MAP(_["id"] + Y)', 'hybrid', 'E_NOT_NUM@1:47'],
    ['C = COUNT(ORDERS) + LABEL; ORDERS .> TAKE(2) .> MAP(_["id"] + C)', 'hybrid', 'E_NOT_NUM@1:21'],
    ['X = ORDERS .> TAKE(2); X .> MAP(COUNT(X) + _["id"] + "x")', 'hybrid', 'E_NOT_NUM@1:54'],
    ['Y = ABORT("x"); ORDERS .> TAKE(2) .> MAP(Y)', 'pure_memory', 'E_ABORT@1:11'],
    // SEL-0047: a continuation on line 3 reports its error on line 3.
    ["X = ORDERS .> TAKE(2);\nX .> MAP(COUNT(X) + _[\"id\"]\n   + \"x\")", 'hybrid', 'E_NOT_NUM@3:6'],
] as [$source, $kind, $want]) {
    $program = Sel::compile($source);
    $plan = Sql::planHybrid($program, 'postgresql', $orders);
    $got = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
    check($got === $kind, "{$source}: expected a {$kind} plan, got {$got}");
    $inMemory = $failure(static fn () => $program->run(['ORDERS' => $rows, 'LABEL' => 'x']));
    $executed = $failure(static fn () => Sql::executeHybrid($plan, $runner, ['ORDERS' => $rows, 'LABEL' => 'x']));
    check($inMemory === $want, "{$source}: run() reports {$inMemory}, want {$want}");
    check($executed === $want, "{$source}: the executed plan reports {$executed}, want {$want}");
}

// --- an executed plan answers what run() answers ---
//
// Review 2026-09-15 findings I, AI and P: plans that pushed a bare bucket to
// the end, re-grouped a bucket, or re-applied a MAP's RECORD over rows the SQL
// had already projected, all answered something else than run(). The database
// is stood in for by SEL itself: the SQL prefix's own AST evaluated over the
// same rows is what the SQL would return, which is the planner's premise.
$fullOrders = ['ORDERS' => Binding::relation('orders', 'o', [
    'ID' => Binding::column('id', 'o', 'NUM'), 'CUSTOMER_ID' => Binding::column('customer_id', 'o', 'NUM'),
    'AMOUNT' => Binding::column('amount', 'o', 'NUM'), 'NAME' => Binding::column('name', 'o', 'TEXT')])];
$orderRows = [
    ['id' => '1', 'customer_id' => '7', 'amount' => '10', 'name' => 'a'],
    ['id' => '2', 'customer_id' => '7', 'amount' => '5', 'name' => 'b'],
    ['id' => '3', 'customer_id' => '9', 'amount' => '7', 'name' => 'c'],
];
foreach ([
  ['ORDERS .> MAP(RECORD("cid", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> TAKE(2)', 'hybrid'],
  ['ORDERS .> MAP(RECORD("plus", _["amount"] + 1, "shout", REPEAT(_["name"], 2))) .> SORT_BY(_["plus"])', 'hybrid'],
  ['ORDERS .> MAP(RECORD("customer_id", _["customer_id"], "shout", REPEAT(_["name"], 2))) .> BUCKET(_["customer_id"])', 'pure_memory'],
  ['ORDERS .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))) .> FILTER(_["name"] $== "a")', 'pure_memory'],
  ['ORDERS .> MAP(RECORD("id", _["id"], "row", REPEAT(GET(_, "name"), 2))) .> TAKE(3)', 'pure_memory'],
  ['ORDERS .> MAP(RECORD("customer_id", _["amount"], "tag", REPEAT(_["customer_id"], 2))) .> TAKE(3)', 'pure_memory'],
  ['ORDERS .> FILTER(_["amount"] > 6) .> BUCKET(_["customer_id"])', 'hybrid'],
  ['ORDERS .> BUCKET(_["customer_id"])', 'pure_memory'],
  ['ORDERS .> BUCKET(_["customer_id"]) .> TAKE(1)', 'pure_memory'],
  ['ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(COUNT(_)) .> MAP(RECORD("size", _K, "n", COUNT(_)))', 'pure_memory'],
  ['ORDERS .> MAP(r, RECORD("id", r["id"], "shout", REPEAT(r["name"], 2))) .> SORT_BY(s, s["name"])', 'pure_memory'],
  // The FILTER no longer moves in front of the MAP (it would renumber the
  // answer's keys); over the MAP's derived table sqlite cannot render its NUM
  // guard, so nothing pushes down. A later step that renumbers again lets the
  // swap through.
  ['ORDERS .> MAP(r, RECORD("id", r["id"], "shout", REPEAT(r["name"], 2))) .> FILTER(s, s["id"] > 1)', 'pure_memory'],
  ['ORDERS .> MAP(r, RECORD("id", r["id"], "shout", REPEAT(r["name"], 2))) .> FILTER(s, s["id"] > 1) .> TAKE(5)', 'hybrid'],
  ['ORDERS .> MAP(RECORD("Name", _["name"], "shout", REPEAT(_["name"], 2))) .> TAKE(2)', 'pure_memory'],
  ['ORDERS .> MAP(RECORD("x", _["id"], "X", REPEAT(_["name"], 2))) .> TAKE(2)', 'hybrid'],
  ['ORDERS .> MAP(RECORD("id", _["id"], "shout", (REPEAT(_["name"], 2), 1))) .> TAKE(2)', 'hybrid'],
  ['ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(1) .> FILTER(_["n"] > 1)', 'hybrid'],
  ['ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)', 'hybrid'],
  ['ORDERS .> BUCKET(RECORD("c", _["customer_id"]), RECORD("n", COUNT(_)))', 'pure_sql'],
  ['ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_)))', 'pure_memory'],
  ['N = 1 + 1; X = ORDERS .> TAKE(N) .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2))); X .> FILTER(_["id"] > 1) .> TAKE(5)', 'hybrid'],
  ['LIMIT = 2; ORDERS .> TAKE(LIMIT) .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], LIMIT)))', 'hybrid'],
  ['C = COUNT(ORDERS); ORDERS .> FILTER(_["amount"] > C) .> MAP(RECORD("id", _["id"], "shout", REPEAT(_["name"], 2)))', 'hybrid'],
] as [$source, $kind]) {
    $program = Sel::compile($source);
    $plan = Sql::planHybrid($program, 'sqlite', $fullOrders);
    $got = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
    check($got === $kind, "{$source}: expected a {$kind} plan, got {$got}");
    $prefixInMemory = static fn (string $sql, array $params) => (new Program('', $plan->sqlPrefixAst))->run(['ORDERS' => $orderRows]);
    $outcome = static function (callable $fn): string {
        try {
            $value = $fn();
            return ($value instanceof \Sel\Value ? $value : \Sel\Value::fromNative($value))->dump();
        } catch (\Sel\SelError $e) {
            return "{$e->code}@{$e->line}:{$e->col}";
        }
    };
    $want = $outcome(static fn () => $program->run(['ORDERS' => $orderRows]));
    $executed = $outcome(static fn () => Sql::executeHybrid($plan, $prefixInMemory, ['ORDERS' => $orderRows]));
    check($executed === $want, "{$source}: the executed plan answers {$executed}, run() {$want}");
}

// The runner contract (finding AK): the statement in `params` mode with
// `bindings()` in placeholder order -- text literals as `?`, numbers inlined
// -- in every host, so a driver binds what it is handed as it is. Lisp handed
// the runner inline SQL and its creation-order slot list.
{
    $program = Sel::compile('ORDERS .> FILTER(FIND("needle", "hay-" & _["name"]) > 0 AND _["amount"] > 5) .> MAP(RECORD("g", RGROUPS("(a)", _["name"])))');
    $plan = Sql::planHybrid($program, 'mariadb', $fullOrders);
    check(!$plan->pureSql && !$plan->pureMemory, 'runner contract: expected a hybrid plan');
    $seen = null;
    Sql::executeHybrid($plan, static function (string $sql, array $params) use (&$seen): array {
        $seen = ['sql' => $sql, 'params' => implode(',', array_map(static fn (\Sel\Value $v): string => $v->dump(), $params))];
        return [];
    }, ['ORDERS' => $orderRows]);
    check($seen !== null, 'runner contract: the runner was not called');
    check(str_contains($seen['sql'], '?') && !str_contains($seen['sql'], "'needle'") && !str_contains($seen['sql'], "'hay-'"),
        "runner contract: text literals must be placeholders, got {$seen['sql']}");
    check(!str_contains($seen['sql'], '?, 5') && str_contains($seen['sql'], '> 5'), "runner contract: a number is inlined, got {$seen['sql']}");
    check($seen['params'] === 't"hay-",t"needle"', "runner contract: bindings in placeholder order, got {$seen['params']}");
}

echo "PHP optimizer checks: {$checks} passed\n";
