// Hybrid SQL-prefix planning. A plan is deliberately small: SQL owns the
// maximal translatable prefix, while the normal SEL Program remains the source
// of truth for the in-memory continuation.
//
// The contract every host's planner meets is in docs/SQL-TRANSLATION.md §12.1
// and is pinned by sql/cases/25-hybrid-plans.sqlt. Three parts of it are easy
// to get wrong and were:
//
//   * The planner looks at the tree the TRANSLATOR will see -- stage 1 has run,
//     helper assignments are inlined, value bindings are literals -- and then
//     at the logical optimiser's rewrite of that. Unwinding the raw AST first
//     classified `X = ORDERS; X .> TAKE(1)` as pure memory, because a `seq` is
//     not a pipeline.
//   * `sourceTables` names PHYSICAL sources: the relation's `from`, or the raw
//     query text for a relation query. The SEL binding name is what the caller
//     already has.
//   * `options` is one object and it goes to BOTH the logical optimiser and the
//     translator, the way Python and PHP do it: `strict` for the translator,
//     `fuseFilters` and `foldConstants` for the optimiser. Dropping it on the
//     way to the optimiser gave the same key two meanings across hosts.

import { Program, Value } from '../sel.mjs';
import { optimizeAstLogical, unwindPipeline, buildPipeline, PIPELINE_OPS } from '../optimizer.mjs';
import { asciiUpper } from '../lexer.mjs';
import * as sqlmap from './map.mjs';
import * as constants from './constants.mjs';
import * as normalise from './normalise.mjs';
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

// The physical source a relation binding reads: its table, or for a relation
// query the query text exactly as the application wrote it.
function physicalSource(binding) {
  const from = binding.from;
  return from !== null && typeof from === 'object' && from.raw !== undefined
    ? String(from.raw) : String(from);
}

// Every physical source the tree reads, first use first, each once. Keyed by
// the physical name, so two bindings over one table are one source.
function sourceTables(ast, bindings) {
  const out = [];
  const seen = new Set();
  const visit = (node) => {
    if (!node) return;
    if (node.t === 'var' && bindings.has(node.name)) {
      const binding = bindings.get(node.name, node.pos);
      if (binding.kind === 'relation') {
        const table = physicalSource(binding);
        if (!seen.has(table)) {
          seen.add(table);
          out.push(table);
        }
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
    if (node.target) visit(node.target);
    if (node.value) visit(node.value);
  };
  visit(ast);
  return out;
}

// Whether the SQL rows for this step list are a bucket's KEYS rather than the
// value SEL would have produced. A BUCKET without a projection is 'open': the
// translator projects its keys, and SEL's value is a map of member rows. The
// next MAP closes it -- it becomes the bucket's projection, one statement, one
// value in both lanes -- and a FILTER between them is a HAVING. Any other step
// seals it: the members are gone, and no continuation can get them back. So a
// prefix that is open or sealed is not a split point, whatever the translator
// says about it, and the MAP fall-through must not fire on a MAP that closes
// one -- its custom half would be evaluated over key rows.
function bucketRowsAreKeys(steps) {
  let open = false;
  for (const step of steps) {
    if (step.name === 'BUCKET') {
      if (open) return true;
      open = step.args.length === 2;
    } else if (open && step.name === 'MAP') {
      open = false;
    } else if (open && step.name !== 'FILTER') {
      return true;
    }
  }
  return open;
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

// The field names read as `binder["field"]` in `node`, first seen first and
// compared exactly: SEL's record keys are case-sensitive, so `name` and
// `Name` are two fields. `binder` null means a read under ANY name counts --
// a downstream step binds the row however it likes (`SORT_BY(s, s["name"])`).
function collectFieldReferences(node, binder = '_') {
  const wanted = binder === null ? null : new Set([binder, '_', '_1', '_2'].map((name) => name.toUpperCase()));
  const refs = [];
  const seen = new Set();
  const visit = (item) => {
    if (!item) return;
    if (item.t === 'index' && item.obj?.t === 'var' && item.idx?.t === 'text'
        && (wanted === null || wanted.has(item.obj.name.toUpperCase()))) {
      const key = String(item.idx.v);
      if (!seen.has(key)) {
        seen.add(key);
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

// The steps the MAP fall-through may push past the MAP. Each keeps the rows
// as they are -- the same records, fewer or reordered -- so the custom half of
// the projection still runs over its own input. A step that changes the row
// shape (MAP, SELECT_COLS, LINK, BUCKET) would put it over something else, and
// the whole-row comparisons (DEDUPE, DISTINCT, the keyless sorts) would compare
// the dependency columns SQL carries where SEL compares the custom values.
const FALLTHROUGH_DOWNSTREAM = new Set(['FILTER', 'SORT_BY', 'TOP_BY', 'TAKE', 'DROP']);

// Whether `node` reads the row itself -- the binder outside an index with a
// text key, as in `GET(_, "name")` or `COUNT(_)` -- which no projected column
// can stand in for.
function readsWholeRow(node, binder) {
  const wanted = new Set([binder, '_', '_1', '_2'].map((name) => name.toUpperCase()));
  let found = false;
  const visit = (item) => {
    if (!item || found) return;
    if (item.t === 'var' && wanted.has(item.name.toUpperCase())) { found = true; return; }
    if (item.t === 'index' && item.obj?.t === 'var' && item.idx?.t === 'text') {
      // A field read; the object is not a whole-row read.
      visit(item.idx);
      return;
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
  return found;
}

// Whether a pushable pair is the plain field read `binder[key]` of its own key,
// so that a dependency of the same name may share its column.
function isOwnFieldRead(pair, binder) {
  const value = pair.value;
  return value.t === 'index' && value.obj?.t === 'var' && value.idx?.t === 'text'
    && value.obj.name.toUpperCase() === binder.toUpperCase()
    && String(value.idx.v) === String(pair.key.v);
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
  if (bucketRowsAreKeys(steps.slice(0, mapIndex))) return null;
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
  // The custom half runs over the rows the SQL returns; a read of the row
  // itself cannot be served by any column.
  if (custom.some((pair) => readsWholeRow(pair.value, details.binder))) return null;

  // Every step after the MAP goes into the SQL, so each must keep the rows as
  // they are, and may read only what SEL's rows have after the MAP: the
  // pushable keys. The custom keys are not in the SQL; a dependency column is
  // in the SQL but not in SEL's row.
  const downstream = steps.slice(mapIndex + 1);
  if (downstream.some((step) => !FALLTHROUGH_DOWNSTREAM.has(step.name))) return null;
  const projected = new Set(pushable.map((pair) => String(pair.key.v)));
  for (const step of downstream) {
    // args[0] is the step's input -- the pipeline so far -- not its own text;
    // the step binds the row under a name of its own, so any read counts.
    for (const arg of step.args.slice(1)) {
      for (const field of collectFieldReferences(arg, null)) {
        if (!projected.has(field)) return null;
      }
    }
  }

  // A dependency may share a projected column only when that column IS the
  // field: `"customer_id", _["amount"]` projects amount under the name the
  // custom half would read customer_id by. Names are compared exactly, as
  // SEL compares them; and a dependency that differs from a projected key
  // only by case is not projected beside it, because SQL aliases are not
  // case-sensitive everywhere.
  const own = new Set(pushable.filter((pair) => isOwnFieldRead(pair, details.binder))
    .map((pair) => String(pair.key.v)));
  // "Case" here is ASCII case, as everywhere in SEL -- never the host's.
  const projectedFolded = new Set([...projected].map(asciiUpper));
  const dependencies = [];
  const dependenciesFolded = new Set();
  for (const pair of custom) {
    for (const field of collectFieldReferences(pair.value, details.binder)) {
      if (projected.has(field)) {
        if (!own.has(field)) return null;
      } else if (projectedFolded.has(asciiUpper(field))) {
        return null;
      } else if (!dependencies.includes(field)) {
        // Two dependencies must not differ only by case either.
        if (dependenciesFolded.has(asciiUpper(field))) return null;
        dependenciesFolded.add(asciiUpper(field));
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

  // The continuation re-applies the projection to the rows that come back:
  // a pushable pair is passed through BY KEY -- the SQL already computed it,
  // under that name -- and a custom pair is evaluated as written, over the
  // dependency columns projected beside it.
  const input = { t: 'var', name: '_INPUT', pos: mapStep.pos };
  const continuationArgs = [];
  for (const pair of details.pairs) {
    if (pushable.includes(pair)) {
      const obj = { t: 'var', name: details.binder, pos: pair.value.pos };
      continuationArgs.push(pair.key, { t: 'index', obj, idx: pair.key, pos: pair.value.pos });
    } else {
      continuationArgs.push(pair.key, pair.value);
    }
  }
  const continuationRecord = { ...details.body, args: continuationArgs };
  const continuationMap = { ...mapStep,
    args: details.explicit
      ? [input, mapStep.args[1], continuationRecord]
      : [input, continuationRecord] };
  return new HybridPlan({
    dialect,
    sqlStatement: sql,
    sqlPrefixAst: rewrittenAst,
    continuationAst: continuationMap,
    continuationProgram: new Program('', continuationMap),
    sourceTables: sourceTables(rewrittenAst, catalog),
  });
}

// The plan for a program nothing of which reaches the database. The
// continuation is the program itself, and the AST it exposes is the program's
// own, so a caller sees the same tree whichever way the plan went.
function pureMemoryPlan(program, dialect, catalog) {
  return new HybridPlan({ dialect, pureMemory: true, continuationProgram: program,
    continuationAst: program.ast, sourceTables: sourceTables(program.ast, catalog) });
}

export function planHybrid(program, dialect, bindings = null, options = null) {
  const catalog = bindings instanceof Bindings ? bindings : new Bindings(bindings ?? {});
  const opts = options ?? {};
  // Match the reference planner's upfront contract checks even when the
  // expression ultimately falls back to memory.  Otherwise an invalid target
  // or aliased catalog is silently accepted simply because no SQL prefix was
  // found.
  sqlmap.requireTarget(dialect);
  catalog.checkAliases();

  // Stage 1 first, exactly as the translator runs it, so the tree unwound
  // below is the one a prefix will be translated from. A program stage 1
  // refuses -- `A += 1; ...`, a bare statement before the result -- is a
  // program no part of which can be pushed down, which is a pure-memory plan
  // and not an exception: "none of it" is one of the planner's answers.
  let normalized;
  try {
    const [constNames, constCtx] = constants.scope(catalog);
    normalized = normalise.run(program.ast, constNames, constCtx);
  } catch (error) {
    if (error instanceof SqlError) return pureMemoryPlan(program, dialect, catalog);
    throw error;
  }
  const optimized = optimizeAstLogical(normalized, opts);
  const { source, steps } = unwindPipeline(optimized);
  if (!steps.length || !source || source.t !== 'var' || !catalog.has(source.name)
      || catalog.get(source.name, source.pos).kind !== 'relation') {
    return pureMemoryPlan(program, dialect, catalog);
  }

  // The whole pipeline, unless its rows would be a bucket's keys: the
  // translator renders a bare bucket as its keys, and a plan that pushes the
  // whole of `... .> BUCKET(k)` would hand them back as the answer.
  const fullAst = buildPipeline(source, steps);
  const fullSql = bucketRowsAreKeys(steps) ? null : tryStatement(fullAst, dialect, catalog, opts);
  if (fullSql !== null) {
    return new HybridPlan({ dialect, sqlStatement: fullSql, sqlPrefixAst: fullAst,
      pureSql: true, sourceTables: sourceTables(fullAst, catalog) });
  }

  const fallthrough = tryPlanFallthrough(source, steps, dialect, catalog, opts);
  if (fallthrough !== null) return fallthrough;

  const inputVar = '_INPUT';
  for (let count = steps.length - 1; count >= 1; count--) {
    const prefixSteps = steps.slice(0, count);
    if (bucketRowsAreKeys(prefixSteps)) continue;
    const prefixAst = buildPipeline(source, prefixSteps);
    const sql = tryStatement(prefixAst, dialect, catalog, opts);
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

  return pureMemoryPlan(program, dialect, catalog);
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
