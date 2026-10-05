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
function optimized_steps(string $source, bool $physical = true): array
{
    $ast = Optimizer::optimize(Sel::compile($source)->ast, $physical);
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

// The physical tree never moves a FILTER across a LINK (spec §7.4; SEL-0054):
// a FILTER moved onto a side renumbered the joined rows, skipped the join
// keys of the rows it dropped, and read relation names under explicit
// binders. The join tests conjuncts itself, at run time, where it can prove
// that is the same.
foreach ([
    'a left conjunct' => ' .> FILTER(_["orders"]["status"] $== "COMPLETED")',
    'a right conjunct' => ' .> FILTER(_["customers"]["country"] $== "DE")',
    'a left and a right conjunct' => ' .> FILTER(_["orders"]["status"] $== "COMPLETED" AND _["customers"]["country"] $== "DE")',
] as $why => $filter) {
    $steps = optimized_steps('ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])' . $filter);
    check(step_names($steps) === ['LINK', 'FILTER'] && ($steps[0]['args'][1]['t'] ?? null) === 'var',
        "no FILTER crosses a LINK: {$why}");
}
// Whether a FILTER's keys can be seen, for the join's pre-filter: a step that
// renumbers without reading `_K` hides them; the end of a pipeline does not.
$observed = optimized_steps('ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> FILTER(_["orders"]["status"] $== "A")');
$hidden = optimized_steps('ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"]) .> FILTER(_["orders"]["status"] $== "A") .> MAP(1)');
check(($observed[1]['args'][1]['keysUnobserved'] ?? null) === false && ($hidden[1]['args'][1]['keysUnobserved'] ?? null) === true,
    'a FILTER followed by a MAP has unobserved keys, one ending the pipeline observed ones');

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
check(step_names($fixedPoint) === ['FILTER', 'LINK', 'FILTER'],
    'a FILTER before a LINK stays before it, one after stays after');

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
// pipeline the swap would change the answer's keys. And only past a step that
// cannot raise on the rows it drops (review 2026-09-25 SEM-07/SEM-08): on the
// logical path a relation's field reads cannot, in memory they can (E_NO_KEY),
// so these pushdowns are the logical path's.
$mapFilterPush = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["x"] > 0) .> MAP(_["x"])', false,
);
check(step_names($mapFilterPush) === ['FILTER', 'MAP', 'MAP'], 'MAP filter pushdown');
check(step_names(optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["x"] > 0) .> MAP(_["x"])',
)) === ['MAP', 'FILTER', 'MAP'], 'in memory a MAP whose field read can raise keeps its FILTER behind it');
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
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["x"] > 0) .> FILTER(_["x"] > 1) .> TAKE(1)', false,
);
check(step_names($mapFilterFused) === ['FILTER', 'MAP', 'TAKE'], 'MAP filter pushdown after the FILTERs fuse');
$caseSensitiveMapFilter = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"])) .> FILTER(_["X"] > 0) .> MAP(_["x"])', false,
);
check(step_names($caseSensitiveMapFilter) === ['MAP', 'FILTER', 'MAP'],
    'MAP filter pushdown preserves case-sensitive field names');

$sortFilterPush = optimized_steps('(1, 2) .> SORT() .> FILTER(_ > 0) .> TAKE(1)', false);
check(step_names($sortFilterPush) === ['FILTER', 'TOP'], 'SORT filter pushdown');
check(step_names(optimized_steps('(1, 2) .> SORT() .> FILTER(_ > 0) .> TAKE(1)')) === ['SORT', 'FILTER', 'TAKE'],
    'in memory a FILTER whose predicate can raise is not moved in front of a sort');
check(step_names(optimized_steps('(1, 2) .> SORT_BY(IF(_ == 2, ABORT("s"), _)) .> FILTER(_ == 1) .> TAKE(1)', false))
    === ['SORT_BY', 'FILTER', 'TAKE'], 'a sort key that can raise keeps its FILTER behind it');
$sortFilterEnd = optimized_steps('(1, 2) .> SORT() .> FILTER(_ > 0)');
check(step_names($sortFilterEnd) === ['SORT', 'FILTER'], 'SORT filter pushdown keeps the keys at the end of a pipeline');

$selectFilterPush = optimized_steps(
    '((RECORD("x", 1), RECORD("x", 2)))'
    . ' .> SELECT_COLS("x") .> FILTER(_["x"] > 0) .> MAP(_["x"])', false,
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
    . ' .> SORT_BY(_["x"], "DESC")', false,
);
check(step_names($lateMaterialization) === ['SORT_BY', 'MAP'], 'SORT_BY late materialization');
check(step_names(optimized_steps(
    '((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
    . ' .> MAP(RECORD("x", _["x"], "y", _["x"] + 1))'
    . ' .> SORT_BY(_["x"], "DESC")',
)) === ['MAP', 'SORT_BY'], 'in memory a MAP that can raise stays in front of a sort');
check(($lateMaterialization[1]['args'][1]['name'] ?? null) === 'RECORD',
    'MAP projection body stays RECORD after physical optimisation');

$filterFusion = optimized_steps('(1, 2) .> FILTER(_ > 0) .> FILTER(_ < 3)', false);
check(step_names(optimized_steps('(1, 2) .> FILTER(_ > 0) .> FILTER(_ < 3)')) === ['FILTER', 'FILTER'],
    'in memory a second FILTER that can raise is not fused into the first');
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
  // REPEAT can raise, so the FILTER stays behind the MAP (review 2026-09-25
  // SEM-07) and nothing pushes down; a MAP that cannot raise lets it through.
  ['ORDERS .> MAP(r, RECORD("id", r["id"], "shout", REPEAT(r["name"], 2))) .> FILTER(s, s["id"] > 1) .> TAKE(5)', 'pure_memory'],
  ['ORDERS .> MAP(r, RECORD("id", r["id"], "plus", r["amount"] + 1)) .> FILTER(s, s["id"] > 1) .> TAKE(5)', 'pure_sql'],
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

// --- T04: an optimisation is invisible (SPEC 6.2) --------------------------
/** @return array{0:string,1:?int,2:?int} outcome of running SOURCE (plain tree vs optimised) */
function outcome(string $source, bool $plain): array
{
    $program = Sel::compile($source);
    try {
        $ctx = new \Sel\Context(\Sel\Value::none());
        $v = $plain
            ? \Sel\Evaluator::evalNode($program->ast, $ctx)
            : $program->run([]);
        return ['value ' . $v->dump(), null, null];
    } catch (\Sel\SelError $e) {
        return [$e->code, $e->line, $e->col];
    }
}
foreach ([
    'MAX(TRUE, U)', 'MIN(1, "x", Y)', 'ROUND("x", Y)', '"abc" + 1/0', 'A = "x"; A + B',
    'A = 1; A + LEN((A = 10; "ab"))',
    'LIST(1,2) .> SORT_BY(_ + 1) .> TAKE(0)', 'LIST(RECORD("a",1)) .> SORT_BY(_["z"]) .> TAKE(-1)',
    'LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER(_)',
    'NOT TAKE(TAKE(LIST(1), 3), 2)', 'NOT DROP(DROP(LIST(1),1),1)', 'NOT FILTER(LIST(1), TRUE)',
    'X = LIST(1); NOT TAKE(SORT(X), 1)', 'C = 0; LIST(3,1,2) .> SORT_BY(_ + (C = C + 1)) .> TAKE(C)',
] as $source) {
    check(outcome($source, true) === outcome($source, false), "optimised run differs from the plain tree: {$source}");
}
// Fusion is limited to a literal count of at least one.
check(step_names(optimized_steps('LIST(3,1,2) .> SORT() .> TAKE(2)')) === ['TOP'], 'SORT + TAKE(2) still fuses');
check(step_names(optimized_steps('LIST(3,1,2) .> SORT() .> TAKE(0)')) === ['SORT', 'TAKE'], 'SORT + TAKE(0) does not fuse');
check(step_names(optimized_steps('LIST(3,1,2) .> SORT() .> TAKE(N)')) === ['SORT', 'TAKE'], 'SORT + TAKE(variable) does not fuse');
check(step_names(optimized_steps('LIST(3,1,2) .> SORT() .> TAKE(1.5)')) === ['SORT', 'TAKE'], 'SORT + TAKE(1.5) does not fuse');
// A bare variable or literal is not a predicate that cannot raise.
check(step_names(optimized_steps('LIST(1,2) .> FILTER(_ > 0) .> FILTER(_)')) === ['FILTER', 'FILTER'], 'FILTER + FILTER(_) is not fused');
check(step_names(optimized_steps('LIST(1,2) .> FILTER(_ > 0) .> FILTER(TRUE)')) === ['FILTER'], 'a TRUE predicate still disappears');
// PHP-C14: the pipeline unwind is linear in the number of stages.
$t0 = microtime(true);
$long = Sel::compile('LIST(1,2)' . str_repeat(' .> SORT', 150) . ' .> COUNT()');
Optimizer::unwindPipeline($long->ast);
check(microtime(true) - $t0 < 2.0, 'unwindPipeline is linear');

// --- hybrid execution never writes the caller's context ------------------------------
// The shared corpus (sql/oracle/hybrid.json, `programs` and `application`) on an
// in-memory SQLite, so the gate holds PHP to it without a server; php/bin/sqlo
// `hybrid` runs the same corpus on every server a DSN names.
require_once __DIR__ . '/../php/bin/hybrid-parity.php';
if (!extension_loaded('pdo_sqlite')) {
    fwrite(STDERR, "FAIL the hybrid-parity corpus needs pdo_sqlite\n");
    exit(1);
}
$parity = run_hybrid(new PDO('sqlite::memory:', null, null, [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_EMULATE_PREPARES => false,
]), 'sqlite', false, 'sqlite (in memory) ');
check($parity['failed'] === 0, 'the hybrid-parity corpus holds on SQLite');

// What the corpus cannot say, ported from tools/check-js-sql.mjs: an application
// function that writes its argument and then throws, one defined through the
// lower-level Registry::define, one plan executed twice, and a Program whose whole
// tree is replaced after its first execution.
$aOf = static fn (\Sel\Value $ctx): string => $ctx->get('A')->get('k')->asText();
$fresh = static fn (): \Sel\Value => \Sel\Value::fromNative(['A' => ['k' => '1']]);
\Sel\Registry::registerFunction('PHP_POKE_ERR', 1, 1, static function ($args): \Sel\Value {
    $args->val(0)->set('k', \Sel\Value::fromNative('99'));
    throw new \RuntimeException('callback error');
});
$ctx = $fresh();
$threw = false;
try {
    Sql::executeHybrid(Sql::planHybrid(Sel::compile('PHP_POKE_ERR(A)'), 'sqlite'), static fn () => [], $ctx);
} catch (\RuntimeException) {
    $threw = true;
}
check($threw && $aOf($ctx) === '1', 'an application function that writes and throws leaves the caller\'s context alone');

\Sel\Registry::define(['name' => 'PHP_POKE_LOW', 'min' => 1, 'max' => 1,
    'fn' => static function ($args): \Sel\Value {
        $v = $args->val(0);
        $v->set('k', \Sel\Value::fromNative('888'));
        return $v;
    }]);
$ctx = $fresh();
$res = Sql::executeHybrid(Sql::planHybrid(Sel::compile('PHP_POKE_LOW(A)'), 'sqlite'), static fn () => [], $ctx);
check($res->get('k')->asText() === '888' && $aOf($ctx) === '1', 'a function from Registry::define is an application function too');

hybrid_register_application_functions();
$plan = Sql::planHybrid(Sel::compile('POKE(A)'), 'sqlite');
$ctx1 = $fresh();
$ctx2 = $fresh();
Sql::executeHybrid($plan, static fn () => [], $ctx1);
Sql::executeHybrid($plan, static fn () => [], $ctx2);
check($aOf($ctx1) === '1' && $aOf($ctx2) === '1', 'one plan executed twice leaves both contexts alone');

$program = Sel::compile('A');
$plan = Sql::planHybrid($program, 'sqlite');
$ctx = $fresh();
check(Sql::executeHybrid($plan, static fn () => [], $ctx)->get('k')->asText() === '1', 'a read-only continuation answers');
$program->ast = Sel::compile('POKE(A)')->ast;
$res = Sql::executeHybrid($plan, static fn () => [], $ctx);
check($res->get('k')->asText() === '9' && $aOf($ctx) === '1', 'a replaced tree is walked again: its application call copies the context');

$ctx = $fresh();
$res = Sql::executeHybrid(Sql::planHybrid(Sel::compile('A["k"] = "99"; A'), 'sqlite'), static fn () => [], $ctx);
check($res->get('k')->asText() === '99' && $aOf($ctx) === '1', 'an assignment into a nested field leaves the caller\'s context alone');

// --- Binding::column() and raw() refuse what JS and Python refuse ---------------------
// The flags are checked in the body, with E_SQL_BINDING, not coerced by a typed
// signature (`'yes'` was exact = true) or thrown as a TypeError a strict caller's
// tryTranslate does not catch; `splitSargable` is the ninth argument, as in JS and
// Python, where PHP dropped it.
$refusal = static function (callable $make): ?string {
    try {
        $make();
        return null;
    } catch (\Sel\Sql\SqlError $e) {
        return $e->code;
    } catch (\Throwable $e) {
        return get_class($e);
    }
};
foreach ([
    'column exact "yes"' => static fn () => Binding::column('c', null, 'NUM', 'yes'),
    'column sargable 1' => static fn () => Binding::column('c', null, 'NUM', false, 1),
    'column guard null' => static fn () => Binding::column('c', null, 'NUM', false, false, null),
    'column collation 5' => static fn () => Binding::column('c', null, 'NUM', false, false, false, 5),
    'column splitSargable "y"' => static fn () => Binding::column('c', null, 'NUM', false, false, false, null, null, 'y'),
    'raw exact 1' => static fn () => Binding::raw('x', 'NUM', 1),
    'raw collation []' => static fn () => Binding::raw('x', 'NUM', false, false, false, []),
] as $why => $make) {
    check($refusal($make) === 'E_SQL_BINDING', "Binding refuses {$why} with E_SQL_BINDING");
}
check(Binding::column('c', null, 'NUM', false, false, false, null, null, true)->spec['prefilter'] === 'separate',
    'column() takes splitSargable');
check(Binding::raw('x', 'NUM', false, false, false, null, null, true)->spec['prefilter'] === 'separate',
    'raw() takes splitSargable');
check(Binding::column('c', null, 'NUM', false, false, false, 'binary')->spec['exact'] === true,
    'a collation string still folds into the flags');

// --- Composer's autoload.files: the SQL layer is loaded on first use, not up front ------
// What `composer require` gives an application: every file composer.json lists, in a
// fresh process. Evaluating must not load the translator; naming a Sel\Sql class must.
$composer = json_decode((string) file_get_contents(__DIR__ . '/../composer.json'), true);
$requires = implode('', array_map(
    static fn (string $f): string => 'require ' . var_export(realpath(__DIR__ . '/../' . $f), true) . ';',
    $composer['autoload']['files']));
$probe = $requires . ' echo \Sel\Sel::evaluate("1 + 1")->asText(), " ",'
    . ' var_export(class_exists("Sel\\\\Sql\\\\Translator", false), true), " ",'
    . ' \Sel\Sql\Sql::translate(\Sel\Sel::compile("X > 1"), "sqlite",'
    . ' ["X" => \Sel\Sql\Binding::column("x", null, "NUM")])->asCondition();';
$out = shell_exec(escapeshellarg(PHP_BINARY) . ' -r ' . escapeshellarg($probe) . ' 2>&1');
check(trim((string) $out) === '2 false (CAST("x" AS NUMERIC) > CAST(\'1\' AS NUMERIC))', "composer's autoload.files load the SQL layer lazily: got " . trim((string) $out));

echo "PHP optimizer checks: {$checks} passed\n";
