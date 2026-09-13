// Hybrid SQL-prefix planning. A plan is deliberately small: SQL owns the
// maximal translatable prefix, while the normal SEL Program remains the source
// of truth for the in-memory continuation.

import { Program, Value } from '../sel.mjs';
import { optimizeAstLogical, unwindPipeline, buildPipeline, PIPELINE_OPS } from '../optimizer.mjs';
import { asciiUpper } from '../lexer.mjs';
import * as sqlmap from './map.mjs';
import { Bindings } from './bindings.mjs';
import { SqlError } from './errors.mjs';
import { Translator } from './translator.mjs';

export class HybridPlan {
  constructor({ dialect = null, sqlStatement = null, sqlPrefixAst = null, continuationAst = null,
    continuationProgram = null, continuationSourceVar = '_INPUT', pureSql = false,
    pureMemory = false, sourceTables = [] } = {}) {
    this.dialect = dialect;
    this.sqlStatement = sqlStatement;
    this.sqlPrefixAst = sqlPrefixAst;
    this.continuationAst = continuationAst;
    this.continuationProgram = continuationProgram;
    this.continuationSourceVar = continuationSourceVar;
    this.pureSql = Boolean(pureSql);
    this.pureMemory = Boolean(pureMemory);
    this.sourceTables = sourceTables;
  }

  get sql_query() { return this.sqlStatement; }
  get sqlQuery() { return this.sqlStatement; }
  get sql_prefix_ast() { return this.sqlPrefixAst; }
  get continuation_ast() { return this.continuationAst; }
  get continuation_program() { return this.continuationProgram; }
  get continuation_source_var() { return this.continuationSourceVar; }
  get isHybrid() { return !this.pureSql && !this.pureMemory; }
  get is_hybrid() { return this.isHybrid; }
  get pure_sql() { return this.pureSql; }
  get pure_memory() { return this.pureMemory; }
  get pureSqlExecution() { return this.pureSql; }
  get pureMemoryExecution() { return this.pureMemory; }
  get pureSqlP() { return this.pureSql; }
  get pureMemoryP() { return this.pureMemory; }
  get source_tables() { return this.sourceTables; }
}

function tryStatement(ast, dialect, bindings, options) {
  try {
    const catalog = bindings instanceof Bindings ? bindings : new Bindings(bindings ?? {});
    const translator = new Translator(dialect, catalog, options ?? {});
    return translator.translateStatement(ast);
  } catch (error) {
    if (error instanceof SqlError) return null;
    throw error;
  }
}

function sourceTables(ast, bindings) {
  const out = [];
  const seen = new Set();
  const visit = (node) => {
    if (!node) return;
    if (node.t === 'var' && bindings.has(node.name)) {
      const binding = bindings.get(node.name, node.pos);
      if (binding.kind === 'relation' && !seen.has(node.name)) {
        seen.add(node.name);
        out.push(node.name);
      }
      return;
    }
    if (node.args) node.args.forEach(visit);
    if (node.items) node.items.forEach(visit);
    if (node.l) visit(node.l);
    if (node.r) visit(node.r);
    if (node.x) visit(node.x);
    if (node.obj) visit(node.obj);
    if (node.idx) visit(node.idx);
  };
  visit(ast);
  return out;
}

const SQL_SPECIAL_CALLS = new Set([
  'IF', 'COND', 'COALESCE', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'RECORD', 'LIST',
]);

function containsUnsupportedSql(node, dialect) {
  if (!node) return false;
  if (node.t === 'call') {
    if (!SQL_SPECIAL_CALLS.has(node.name)) {
      const entry = sqlmap.entry(dialect, 'funcs', asciiUpper(node.name));
      if (entry === sqlmap.MISSING || entry === null || typeof entry === 'string') return true;
    }
    return node.args.some((item) => containsUnsupportedSql(item, dialect));
  }
  if (node.args && node.args.some((item) => containsUnsupportedSql(item, dialect))) return true;
  if (node.items && node.items.some((item) => containsUnsupportedSql(item, dialect))) return true;
  if (node.l && containsUnsupportedSql(node.l, dialect)) return true;
  if (node.r && containsUnsupportedSql(node.r, dialect)) return true;
  if (node.x && containsUnsupportedSql(node.x, dialect)) return true;
  if (node.obj && containsUnsupportedSql(node.obj, dialect)) return true;
  if (node.idx && containsUnsupportedSql(node.idx, dialect)) return true;
  if (node.target && containsUnsupportedSql(node.target, dialect)) return true;
  return Boolean(node.value && containsUnsupportedSql(node.value, dialect));
}

function collectFieldReferences(node, binder = '_') {
  const wanted = new Set([binder, '_', '_1', '_2'].map((name) => name.toUpperCase()));
  const refs = [];
  const seen = new Set();
  const visit = (item) => {
    if (!item) return;
    if (item.t === 'index' && item.obj?.t === 'var' && item.idx?.t === 'text'
        && wanted.has(item.obj.name.toUpperCase())) {
      const key = String(item.idx.v);
      const upper = key.toUpperCase();
      if (!seen.has(upper)) {
        seen.add(upper);
        refs.push(key);
      }
    }
    if (item.args) item.args.forEach(visit);
    if (item.items) item.items.forEach(visit);
    visit(item.l);
    visit(item.r);
    visit(item.x);
    visit(item.obj);
    visit(item.idx);
    visit(item.target);
    visit(item.value);
  };
  visit(node);
  return refs;
}

function mapRecordDetails(step) {
  const args = step.args;
  const explicit = args.length === 3 && args[1].t === 'var' && !args[1].grouped;
  const binder = explicit ? args[1].name : '_';
  const body = explicit ? args[2] : args[1];
  if (!body || body.t !== 'call' || !['RECORD', 'LAZY_RECORD'].includes(body.name)
      || body.args.length % 2 !== 0) return null;
  const pairs = [];
  for (let i = 0; i < body.args.length; i += 2) {
    const key = body.args[i];
    if (key.t !== 'text') return null;
    pairs.push({ key, value: body.args[i + 1] });
  }
  return { explicit, binder, body, pairs };
}

function tryPlanFallthrough(source, steps, dialect, catalog, options) {
  const mapIndex = steps.findIndex((step) => step.name === 'MAP');
  if (mapIndex < 0) return null;
  const mapStep = steps[mapIndex];
  const details = mapRecordDetails(mapStep);
  if (!details) return null;

  const pushable = [];
  const custom = [];
  for (const pair of details.pairs) {
    if (containsUnsupportedSql(pair.value, dialect)) custom.push(pair);
    else pushable.push(pair);
  }
  if (custom.length === 0 || pushable.length === 0) return null;

  const downstreamRefs = new Set();
  for (const step of steps.slice(mapIndex + 1)) {
    for (const field of collectFieldReferences(step)) downstreamRefs.add(field.toUpperCase());
  }
  if (custom.some((pair) => downstreamRefs.has(String(pair.key.v).toUpperCase()))) return null;

  const projected = new Set(pushable.map((pair) => String(pair.key.v).toUpperCase()));
  const dependencies = [];
  const dependencySet = new Set();
  for (const pair of custom) {
    for (const field of collectFieldReferences(pair.value, details.binder)) {
      const upper = field.toUpperCase();
      if (!projected.has(upper) && !dependencySet.has(upper)) {
        dependencySet.add(upper);
        dependencies.push(field);
      }
    }
  }

  const rewrittenArgs = [];
  for (const pair of pushable) rewrittenArgs.push(pair.key, pair.value);
  for (const field of dependencies) {
    const key = { t: 'text', v: field, pos: mapStep.pos };
    const obj = { t: 'var', name: details.binder, pos: mapStep.pos };
    const idx = { t: 'index', obj, idx: key, pos: mapStep.pos };
    rewrittenArgs.push(key, idx);
  }
  const rewrittenRecord = { ...details.body, args: rewrittenArgs };
  const rewrittenMap = { ...mapStep,
    args: details.explicit
      ? [mapStep.args[0], mapStep.args[1], rewrittenRecord]
      : [mapStep.args[0], rewrittenRecord] };
  const rewrittenSteps = [
    ...steps.slice(0, mapIndex), rewrittenMap, ...steps.slice(mapIndex + 1),
  ];
  const rewrittenAst = buildPipeline(source, rewrittenSteps);
  const sql = tryStatement(rewrittenAst, dialect, catalog, options);
  if (sql === null) return null;

  const input = { t: 'var', name: '_INPUT', pos: mapStep.pos };
  const continuationMap = { ...mapStep,
    args: details.explicit
      ? [input, mapStep.args[1], details.body]
      : [input, details.body] };
  return new HybridPlan({
    dialect,
    sqlStatement: sql,
    sqlPrefixAst: rewrittenAst,
    continuationAst: continuationMap,
    continuationProgram: new Program('', continuationMap),
    sourceTables: sourceTables(rewrittenAst, catalog),
  });
}

export function planHybrid(program, dialect, bindings = null, options = null) {
  const catalog = bindings instanceof Bindings ? bindings : new Bindings(bindings ?? {});
  // Match the reference planner's upfront contract checks even when the
  // expression ultimately falls back to memory.  Otherwise an invalid target
  // or aliased catalog is silently accepted simply because no SQL prefix was
  // found.
  sqlmap.requireTarget(dialect);
  catalog.checkAliases();
  const optimized = optimizeAstLogical(program.ast);
  const { source, steps } = unwindPipeline(optimized);
  if (!steps.length || source.t !== 'var' || !catalog.has(source.name)
      || catalog.get(source.name, source.pos).kind !== 'relation') {
    return new HybridPlan({ dialect, pureMemory: true, continuationProgram: program,
      sourceTables: sourceTables(program.ast, catalog) });
  }

  const fullAst = buildPipeline(source, steps);
  const fullSql = tryStatement(fullAst, dialect, catalog, options);
  if (fullSql !== null) {
    return new HybridPlan({ dialect, sqlStatement: fullSql, sqlPrefixAst: fullAst,
      pureSql: true, sourceTables: sourceTables(fullAst, catalog) });
  }

  const fallthrough = tryPlanFallthrough(source, steps, dialect, catalog, options);
  if (fallthrough !== null) return fallthrough;

  const inputVar = '_INPUT';
  for (let count = steps.length - 1; count >= 1; count--) {
    const prefixSteps = steps.slice(0, count);
    const prefixAst = buildPipeline(source, prefixSteps);
    const sql = tryStatement(prefixAst, dialect, catalog, options);
    if (sql === null) continue;
    const remaining = steps.slice(count);
    const input = { t: 'var', name: inputVar, pos: remaining[0].pos };
    const continuationAst = buildPipeline(input, remaining);
    return new HybridPlan({
      dialect,
      sqlStatement: sql,
      sqlPrefixAst: prefixAst,
      continuationAst,
      continuationProgram: new Program('', continuationAst),
      continuationSourceVar: inputVar,
      sourceTables: sourceTables(prefixAst, catalog),
    });
  }

  return new HybridPlan({ dialect, pureMemory: true, continuationProgram: program,
    sourceTables: sourceTables(program.ast, catalog) });
}

export function executeHybrid(plan, dbRunner, context = null) {
  if (!(plan instanceof HybridPlan)) throw new TypeError('executeHybrid expects a HybridPlan');
  if (plan.pureMemory) return plan.continuationProgram.run(context);
  const runSql = () => {
    const fragment = plan.sqlStatement;
    return dbRunner(fragment.asStatement('params'), fragment.bindings());
  };
  if (plan.pureSql) return runSql();
  const rows = runSql();
  const root = context instanceof Value ? context.clone() : Value.fromNative(context || {});
  root.set(plan.continuationSourceVar,
    rows instanceof Value ? rows : Value.fromNative(rows));
  return plan.continuationProgram.run(root);
}

export { PIPELINE_OPS };
