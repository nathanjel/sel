#!/usr/bin/env node

// Focused regression checks for the optimizer paths ported from Lisp.

import assert from 'node:assert/strict';
import '../js/src/builtins/index.mjs';
import { parse } from '../js/src/parser.mjs';
import {
  optimizeAstLogical, optimizeAstInMemory, unwindPipeline,
} from '../js/src/optimizer.mjs';

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

console.log(`JS optimizer checks: ${checks} passed`);
