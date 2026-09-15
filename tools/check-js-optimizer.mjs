#!/usr/bin/env node

// Focused regression checks for the optimizer paths ported from Lisp.

import assert from 'node:assert/strict';
import '../js/src/builtins/index.mjs';
import { parse } from '../js/src/parser.mjs';
import {
  optimizeAstLogical, optimizeAstInMemory, unwindPipeline,
} from '../js/src/optimizer.mjs';
import { compile, Program } from '../js/src/sel.mjs';
import { Binding, Sql } from '../js/src/sql/index.mjs';

let checks = 0;

function check(condition, message) {
  checks += 1;
  assert.ok(condition, message);
}

function optimizedSteps(source, physical = true) {
  const ast = physical ? optimizeAstInMemory(parse(source)) : optimizeAstLogical(parse(source));
  return unwindPipeline(ast).steps;
}

function names(steps) {
  return steps.map((step) => step.name);
}

function same(actual, expected, message) {
  checks += 1;
  assert.deepEqual(actual, expected, message);
}

check(optimizeAstLogical(parse('1 + 2')).v === '3', 'numeric literal folding');
check(optimizeAstLogical(parse('FALSE AND (1 / 0 > 0)')).v === false,
  'short-circuit literal folding');
check(optimizeAstLogical(parse('IF(TRUE, 2 + 3, 1 / 0)')).v === '5',
  'literal IF folding');

same(names(optimizedSteps('(3, 1, 2) .> TAKE(2) .> TAKE(1)', false)), ['TAKE'], 'TAKE fusion');
same(names(optimizedSteps('(3, 1, 2) .> DROP(1) .> DROP(1)', false)), ['DROP'], 'DROP fusion');
same(names(optimizedSteps('(3, 1, 2) .> SORT() .> TAKE(1)', false)), ['TOP'], 'SORT plus TAKE fusion');
same(names(optimizedSteps('(3, 1, 2) .> SORT_DESC() .> TAKE(1)', false)), ['TOP_DESC'],
  'SORT_DESC plus TAKE fusion');
same(names(optimizedSteps(
  '((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
  + ' .> SORT_BY(_["x"], "DESC") .> TAKE(1)', false)), ['TOP_BY'],
  'SORT_BY plus TAKE fusion');

same(names(optimizedSteps(
  '((RECORD("x", 1), RECORD("x", 2)))'
  + ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
  + ' .> FILTER(_["x"] > 0)', false)), ['FILTER', 'MAP'], 'MAP filter pushdown');
same(names(optimizedSteps(
  '((RECORD("x", 1), RECORD("x", 2)))'
  + ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
  + ' .> FILTER(_["heavy"] > 0)', false)), ['MAP', 'FILTER'],
  'MAP filter dependency guard');
same(names(optimizedSteps('(1, 2) .> SORT() .> FILTER(_ > 0)', false)), ['FILTER', 'SORT'],
  'SORT filter pushdown');
same(names(optimizedSteps(
  '((RECORD("x", 1), RECORD("x", 2)))'
  + ' .> SELECT_COLS("x") .> FILTER(_["x"] > 0)', false)), ['FILTER', 'SELECT_COLS'],
  'SELECT_COLS filter pushdown');

const late = optimizedSteps(
  '((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
  + ' .> MAP(RECORD("x", _["x"], "heavy", _["x"] + 1))'
  + ' .> SORT_BY(_["x"], "DESC")', true);
same(names(late), ['SORT_BY', 'MAP'], 'SORT_BY late materialization');
check(late[1].args[1].name === 'LAZY_RECORD', 'MAP projection lazy-record conversion');

const topPrefix = '((RECORD("x", 3), RECORD("x", 1), RECORD("x", 2)))'
  + ' .> MAP(RECORD("x", _["x"], "y", _["x"] + 1)) .> ';
same(names(optimizedSteps(topPrefix + 'TOP(r, r["x"], 1)', false)), ['TOP', 'MAP'],
  'TOP explicit binder pass-through key');
same(names(optimizedSteps(topPrefix + 'TOP(r, r["y"], 1)', false)), ['MAP', 'TOP'],
  'TOP explicit binder computed key guard');

same(names(optimizedSteps('(1, 2) .> FILTER(_ > 0) .> FILTER(_ < 3)', false)), ['FILTER'],
  'FILTER fusion');
same(names(optimizedSteps('(1, 2) .> SORT() .> SORT_DESC()', false)), ['SORT_DESC'],
  'redundant sort elimination');
same(names(optimizedSteps('(1, 2) .> DEDUPE() .> DISTINCT()', false)), ['DEDUPE'],
  'redundant dedupe elimination');
same(names(optimizedSteps('(1, 2) .> FILTER(TRUE)', false)), [],
  'trivial TRUE filter elimination');
same(names(optimizedSteps('DATA .> FILTER(TRUE)', false)), ['FILTER'],
  'a first-step TRUE filter over a variable is kept (the source may be a scalar)');

same(names(optimizedSteps(
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
  + ' .> FILTER(_["orders"]["status"] $== "ACTIVE"'
  + ' AND _["customers"]["country"] $== "DE")', true)), ['FILTER', 'LINK'],
  'qualified LINK predicate pushdown');
const leftJoin = optimizedSteps(
  'ORDERS .> LINK_LEFT(CUSTOMERS, _1["customer_id"] == _2["id"])'
  + ' .> FILTER(_["customers"]["country"] $== "DE")', true);
same(names(leftJoin), ['LINK_LEFT', 'FILTER'], 'LINK_LEFT right predicate stays above join');

same(names(optimizedSteps(
  'ORDERS .> FILTER(_["status"] $== "ACTIVE")'
  + ' .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
  + ' .> FILTER(_["orders"]["status"] $== "ACTIVE")', true)), ['FILTER', 'LINK'],
  'join pushdown returns to the logical fixed point');
same(names(optimizedSteps(
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
  + ' .> FILTER(FOO["orders"]["status"] $== "ACTIVE")', true)), ['LINK', 'FILTER'],
  'external qualified root is not pushed through LINK');
same(names(optimizedSteps(
  'ORDERS .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"])'
  + ' .> FILTER(_["orders"]["status"] $== _K)', true)), ['LINK', 'FILTER'],
  '_K is an unknown join dependency');

const foldedNot = optimizeAstLogical(parse('NOT FALSE'));
check(foldedNot.t === 'bool' && foldedNot.pos.col === 1,
  'unary NOT fold keeps operator position');

// A hoisted child takes the folded node's position (spec §6.3: the operand an
// operator rejects is the IF or the AND, not the literal inside it); a branch
// with positions of its own is not hoisted at all. The run()-visible half of
// this is ctl.if.constant-condition-* and op.logic.*-keeps-the-*-position.
const foldedIf = optimizeAstLogical(parse('1 + IF(TRUE, "x", 2)'));
check(foldedIf.r.t === 'text' && foldedIf.r.v === 'x' && foldedIf.r.pos.col === 5,
  'IF fold stamps the literal with the IF position');
const foldedAnd = optimizeAstLogical(parse('1 + (FALSE AND TRUE)'));
check(foldedAnd.r.t === 'bool' && foldedAnd.r.v === false && foldedAnd.r.pos.col === 12,
  'AND short-circuit fold keeps operator position');
const foldedOr = optimizeAstLogical(parse('1 + (TRUE OR FALSE)'));
check(foldedOr.r.t === 'bool' && foldedOr.r.v === true && foldedOr.r.pos.col === 11,
  'OR short-circuit fold keeps operator position');
const unfoldedIf = optimizeAstLogical(parse('IF(TRUE, 1 / 0, 2)'));
check(unfoldedIf.t === 'call' && unfoldedIf.name === 'IF' && unfoldedIf.args[1].pos.col === 12,
  'IF over a compound branch is not folded');
const unfoldedVar = optimizeAstLogical(parse('IF(TRUE, X, 2)'));
check(unfoldedVar.t === 'call' && unfoldedVar.name === 'IF',
  'IF over a variable branch is not folded');

// --- the hybrid planner's contract, the parts a host-local check can see ---
//
// sql/cases/25-hybrid-plans.sqlt holds the language-neutral version of these;
// what is here is what the shared fixtures cannot express in JSON options or
// cannot observe through the runner: an optimiser option reaching the
// optimiser, the run() cache, and immutability across a real run().

const snapshot = (ast) => JSON.stringify(ast, (k, v) => (k === 'spec' ? undefined : v));
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };

const helper = compile('X = ORDERS; X .> TAKE(1)');
const helperPlan = Sql.planHybrid(helper, 'postgresql', orders);
check(helperPlan.pureSql, 'a helper assignment is normalised before planning');
same(helperPlan.sourceTables, ['orders'], 'source tables are physical names');

const twoFilters = 'ORDERS .> FILTER(_["id"] > 1) .> FILTER(_["id"] < 9)';
const fused = Sql.planHybrid(compile(twoFilters), 'postgresql', orders);
const unfused = Sql.planHybrid(compile(twoFilters), 'postgresql', orders, { fuseFilters: false });
check(unwindPipeline(fused.sqlPrefixAst).steps.length === 1
  && unwindPipeline(unfused.sqlPrefixAst).steps.length === 2,
  'planner options reach the logical optimiser');
check(fused.pureSql && unfused.pureSql, 'fusion does not change the classification');
const foldable = 'ORDERS .> FILTER(_["id"] > 1 + 1)';
const folded = Sql.planHybrid(compile(foldable), 'postgresql', orders);
const unfolded = Sql.planHybrid(compile(foldable), 'postgresql', orders, { foldConstants: false });
check(folded.sqlStatement.asStatement().includes('> 2')
  && !unfolded.sqlStatement.asStatement().includes('> 2'),
  'foldConstants:false reaches the logical optimiser');

const refused = compile('A += 1; ORDERS .> TAKE(1)');
const refusedPlan = Sql.planHybrid(refused, 'postgresql', orders);
check(refusedPlan.pureMemory && refusedPlan.continuationProgram === refused
  && refusedPlan.continuationAst === refused.ast,
  'a program stage 1 refuses is a pure-memory plan over the original program');
same(refusedPlan.sourceTables, ['orders'], 'a pure-memory plan still names its sources');

const reusable = compile(
  '(3, 1, 2) .> FILTER(NOT (_ < 1 + 1)) .> MAP(RECORD("x", _, "y", _ * 2, "z", _ + 1)) .> TAKE(2 * 1)');
const before = snapshot(reusable.ast);
const first = reusable.run().dump();
optimizeAstLogical(reusable.ast);
optimizeAstInMemory(reusable.ast);
Sql.planHybrid(reusable, 'postgresql', orders);
check(snapshot(reusable.ast) === before, 'run, both optimisers and planning leave the AST unchanged');
check(reusable.run().dump() === first, 'repeated runs answer the same');
check(reusable.physicalAst() === reusable.physicalAst(), 'the physical AST is built once');
const physical = reusable.physicalAst();
reusable.ast = parse('1 + 1');
check(reusable.physicalAst() !== physical && reusable.run().asText() === '2',
  'reassigning ast drops the cache');

// --- a plan's continuation reports errors where run() does ---
//
// The planner folds one tree for both halves of a split, so a hoisted literal
// in the continuation carries the position the in-memory half will report.
// sql/cases/25-hybrid-plans.sqlt pins the SQL side of these; only executing
// the plan can see the position the memory side reports.
const rows = [{ id: '1' }, { id: '2' }];
const runner = () => rows;
function failure(fn) {
  try { fn(); return 'no error'; } catch (e) { return `${e.code}@${e.line}:${e.col}`; }
}
for (const [source, kind, want] of [
  ['ORDERS .> TAKE(2) .> MAP(IF(TRUE, "x", 1) >= _["id"])', 'hybrid', 'E_NOT_NUM@1:26'],
  ['ORDERS .> TAKE(2) .> FILTER((FALSE AND TRUE) + _["id"] > 0)', 'hybrid', 'E_NOT_NUM@1:36'],
  ['ORDERS .> FILTER(IF(TRUE, "x", 1) >= _["id"])', 'pure_memory', 'E_NOT_NUM@1:18'],
]) {
  const program = compile(source);
  const plan = Sql.planHybrid(program, 'postgresql', orders);
  const got = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  check(got === kind, `${source}: expected a ${kind} plan, got ${got}`);
  const inMemory = failure(() => program.run({ ORDERS: rows }));
  const executed = failure(() => Sql.executeHybrid(plan, runner, { ORDERS: rows }));
  check(inMemory === want, `${source}: run() reports ${inMemory}, want ${want}`);
  check(executed === want, `${source}: the executed plan reports ${executed}, want ${want}`);
}

// --- an executed plan answers what run() answers ---
//
// Review 2026-09-15 findings I, AI and P: plans that pushed a bare bucket to
// the end, re-grouped a bucket, or re-applied a MAP's RECORD over rows the SQL
// had already projected, all answered something else than run(). The database
// is stood in for by SEL itself: the SQL prefix's own AST evaluated over the
// same rows is what the SQL would return, which is the planner's premise.
const fullOrders = { ORDERS: Binding.relation('orders', 'o', {
  ID: Binding.column('id', 'o', 'NUM'), CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'),
  AMOUNT: Binding.column('amount', 'o', 'NUM'), NAME: Binding.column('name', 'o', 'TEXT') }) };
const orderRows = [
  { id: '1', customer_id: '7', amount: '10', name: 'a' },
  { id: '2', customer_id: '7', amount: '5', name: 'b' },
  { id: '3', customer_id: '9', amount: '7', name: 'c' },
];
for (const [source, kind] of [
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
  ['ORDERS .> MAP(r, RECORD("id", r["id"], "shout", REPEAT(r["name"], 2))) .> FILTER(s, s["id"] > 1)', 'hybrid'],
  ['ORDERS .> MAP(RECORD("Name", _["name"], "shout", REPEAT(_["name"], 2))) .> TAKE(2)', 'pure_memory'],
  ['ORDERS .> MAP(RECORD("x", _["id"], "X", REPEAT(_["name"], 2))) .> TAKE(2)', 'hybrid'],
  ['ORDERS .> MAP(RECORD("id", _["id"], "shout", (REPEAT(_["name"], 2), 1))) .> TAKE(2)', 'hybrid'],
  ['ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_))) .> TAKE(1) .> FILTER(_["n"] > 1)', 'hybrid'],
  ['ORDERS .> BUCKET(_["customer_id"], RECORD("cid", _K, "n", COUNT(_))) .> DROP(1) .> FILTER(_["n"] > 1)', 'hybrid'],
  ['ORDERS .> BUCKET(RECORD("c", _["customer_id"]), RECORD("n", COUNT(_)))', 'pure_sql'],
  ['ORDERS .> BUCKET(RECORD("c", _["customer_id"])) .> MAP(RECORD("n", COUNT(_)))', 'pure_memory'],
]) {
  const program = compile(source);
  const plan = Sql.planHybrid(program, 'sqlite', fullOrders);
  const got = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  check(got === kind, `${source}: expected a ${kind} plan, got ${got}`);
  const prefixInMemory = () => new Program('', plan.sqlPrefixAst).run({ ORDERS: orderRows });
  const outcome = (fn) => { try { return fn().dump(); } catch (e) { return `${e.code}@${e.line}:${e.col}`; } };
  const want = outcome(() => program.run({ ORDERS: orderRows }));
  const executed = outcome(() => Sql.executeHybrid(plan, prefixInMemory, { ORDERS: orderRows }));
  check(executed === want, `${source}: the executed plan answers ${executed}, run() ${want}`);
}

console.log(`JS optimizer checks: ${checks} passed`);
