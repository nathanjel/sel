// Hybrid SQL-prefix planning. A plan is deliberately small: SQL owns the
// maximal translatable prefix, while the normal SEL Program remains the source
// of truth for the in-memory continuation.
//
// The contract every host's planner meets is in docs/internals/sql-translation.md §12.1
// and is pinned by sql/cases/25-hybrid-plans.sqlt. Three parts of it are easy
// to get wrong and were:
//
//   * The planner looks at the PIPELINE, whichever helper assignments it is
//     written through, and then at the logical optimiser's rewrite of that.
//     Unwinding the raw AST first classified `X = ORDERS; X .> TAKE(1)` as
//     pure memory, because a `seq` is not a pipeline; inlining every helper
//     the way stage 1 does for translate() made the continuation report an
//     error at the helper's definition where run() reports its use. See
//     "helper assignments" below for what is done instead.
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
import { Emit } from './emit.mjs';
import { Fragment } from './fragment.mjs';

export class HybridPlan {
  constructor({ dialect = null, sqlStatement = null, sqlPrefixAst = null, continuationAst = null,
    continuationProgram = null, continuationSourceVar = '_INPUT', pureSql = false,
    pureMemory = false, sourceTables = [], selectedMember = null } = {}) {
    this.dialect = dialect;
    this.sqlStatement = sqlStatement;
    this.sqlPrefixAst = sqlPrefixAst;
    this.continuationAst = continuationAst;
    this.continuationProgram = continuationProgram;
    this.continuationSourceVar = continuationSourceVar;
    this.pureSql = Boolean(pureSql);
    this.pureMemory = Boolean(pureMemory);
    this.sourceTables = sourceTables;
    this.selectedMember = selectedMember;
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
  get selected_member() { return this.selectedMember; }
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

// Whether the SQL rows for this step list are a join's rows without the
// binders SEL's rows carry. A LINK's row in SEL holds each side under its
// binders and the promoted fields beside them (spec §7.4); SQL carries the
// promoted fields alone. A MAP, a SELECT_COLS or a projected BUCKET after the
// LINK makes the rows exact again -- what they compute is over the promoted
// fields, or is refused -- so a prefix whose LINK nothing has projected is
// not a split point and not a full pushdown (finding Y, lanes): its
// continuation would read `_["C"]` where the database sent nothing.
function joinRowsLackBinders(steps) {
  let joined = false;
  for (const step of steps) {
    if (step.name === 'LINK' || step.name === 'LINK_LEFT') joined = true;
    else if (step.name === 'MAP' || step.name === 'SELECT_COLS' || step.name === 'BUCKET') joined = false;
  }
  return joined;
}

// The two together: a prefix whose SQL rows are not the value SEL would have
// produced for it, whatever the translator says about it.
function rowsAreNotTheValue(steps) {
  return bucketRowsAreKeys(steps) || joinRowsLackBinders(steps);
}

const SQL_SPECIAL_CALLS = new Set([
  'IF', 'COND', 'COALESCE', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'RECORD', 'LIST',
]);

// `defs` are the helper definitions: a read of one is as unsupported as its
// definition, since the translator will inline it.
function containsUnsupportedSql(node, dialect, defs = null, seen = new Set()) {
  if (!node) return false;
  if (node.t === 'var' && defs !== null && defs.has(node.name) && !seen.has(node.name)) {
    return containsUnsupportedSql(defs.get(node.name), dialect, defs, new Set([...seen, node.name]));
  }
  if (node.t === 'call') {
    if (!SQL_SPECIAL_CALLS.has(node.name)) {
      const entry = sqlmap.entry(dialect, 'funcs', asciiUpper(node.name));
      if (entry === sqlmap.MISSING || entry === null || typeof entry === 'string') return true;
    }
    return node.args.some((item) => containsUnsupportedSql(item, dialect, defs, seen));
  }
  const inner = (item) => containsUnsupportedSql(item, dialect, defs, seen);
  if (node.args && node.args.some(inner)) return true;
  if (node.items && node.items.some(inner)) return true;
  if (node.l && inner(node.l)) return true;
  if (node.r && inner(node.r)) return true;
  if (node.x && inner(node.x)) return true;
  if (node.obj && inner(node.obj)) return true;
  if (node.idx && inner(node.idx)) return true;
  if (node.target && inner(node.target)) return true;
  return Boolean(node.value && inner(node.value));
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
// FILTER retains ordinal keys that SQL rows plus the local MAP cannot restore.
const FALLTHROUGH_DOWNSTREAM = new Set(['SORT_BY', 'TOP_BY', 'TAKE', 'DROP']);

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
  if (!body || body.t !== 'call' || body.name !== 'RECORD'
      || body.args.length % 2 !== 0) return null;
  const pairs = [];
  const seen = new Set();
  for (let i = 0; i < body.args.length; i += 2) {
    const key = body.args[i];
    if (key.t !== 'text') return null;
    if (seen.has(key.v)) return null;
    seen.add(key.v);
    pairs.push({ key, value: body.args[i + 1] });
  }
  return { explicit, binder, body, pairs };
}

function tryPlanFallthrough(source, steps, dialect, catalog, options, helpers) {
  const mapIndex = steps.findIndex((step) => step.name === 'MAP');
  if (mapIndex < 0) return null;
  if (bucketRowsAreKeys(steps.slice(0, mapIndex))) return null;
  const mapStep = steps[mapIndex];
  const details = mapRecordDetails(mapStep);
  if (!details) return null;

  const pushable = [];
  const custom = [];
  for (const pair of details.pairs) {
    if (containsUnsupportedSql(pair.value, dialect, helpers.defs)) custom.push(pair);
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
  const rewrittenAst = helpers.wrap(buildPipeline(source, rewrittenSteps));
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
  const continuationAst = helpers.wrap({ ...mapStep,
    args: details.explicit
      ? [input, mapStep.args[1], continuationRecord]
      : [input, continuationRecord] });
  return new HybridPlan({
    dialect,
    sqlStatement: sql,
    sqlPrefixAst: rewrittenAst,
    continuationAst,
    continuationProgram: new Program('', continuationAst),
    sourceTables: helpers.tables(rewrittenAst),
  });
}

// --- helper assignments -----------------------------------------------------
//
// Stage 1 inlines a helper assignment for translate(): `Y = "x"; ... + Y` is
// rendered as `... + "x"`, the literal keeping its definition-site position,
// which is right for a refusal message. It is wrong for the memory half of a
// plan, because that half is a program run() evaluates and §12.1 promises it
// reports errors where run() would: run() evaluates the READ of Y at the use
// site and reports `+`'s operand there, and it evaluates the definition once,
// before the pipeline, not once per row (review 2026-09-15 finding AJ). So
// the planner does not inline. It plans the program as written, three ways:
//
//   * A helper that IS a literal -- after inlining earlier such helpers and
//     folding, `N = 1 + 1` as much as `N = 2` -- is inlined at its reads,
//     stamped with the read's position. That is invisible: a leaf literal
//     cannot fail, and neither can the read, since the definition exists. It
//     keeps `TAKE(N)` a `LIMIT 2` rather than a helper the SQL has to carry.
//   * A helper read as the pipeline's SOURCE is unwound through: `X = ORDERS
//     .> TAKE(2); X .> MAP(...)` is one pipeline over ORDERS, so the prefix
//     search sees every step. Only the source position looks through a
//     helper; a read anywhere else stays a read.
//   * What is handed to the translator, and what is kept for the
//     continuation, carries in front of it the assignments it still reads and
//     the ones those read, in program order, as the program wrote them. The
//     translator runs its own stage 1 over that seq and inlines; the
//     continuation evaluates them once, before its steps, as run() does. An
//     assignment nothing after the split reads is dropped, as stage 1 drops
//     it for translate() -- the one departure, and the same one.

const LITERAL_TYPES = new Set(['num', 'text', 'bool', 'null']);

// The leading statements and the result expression of a program.
function statements(ast) {
  if (ast.t !== 'seq') return { leading: [], result: ast };
  return { leading: ast.items.slice(0, -1), result: ast.items[ast.items.length - 1] };
}

// The name a leading statement assigns. Stage 1 has accepted every statement
// by the time this runs, so each is an assignment whose target is a name, or
// a name indexed by constants.
function assignedName(statement) {
  let target = statement.target;
  while (target.t === 'index') target = target.obj;
  return target.name;
}

// The whole-name definitions, by name. Stage 1 refuses a name assigned twice,
// or both whole and by index, so each name here has exactly one.
function definitions(leading) {
  const defs = new Map();
  for (const s of leading) {
    if (s.target.t === 'var') defs.set(s.target.name, s.value);
  }
  return defs;
}

// `node` with every read of a literal helper replaced by the literal, stamped
// with the read's position. Binder scoping is stage 1's: a binder shadows a
// same-named helper inside its body. Copies on the way down, never writes.
function inlineLiterals(node, literals, bound = []) {
  if (!node) return node;
  const t = node.t;
  if (t === 'var') {
    if (bound.includes(node.name) || !literals.has(node.name)) return node;
    return { ...literals.get(node.name), pos: node.pos };
  }
  if (LITERAL_TYPES.has(t)) return node;
  const inline = (child, scope = bound) => inlineLiterals(child, literals, scope);
  if (t === 'un') return { ...node, x: inline(node.x) };
  if (t === 'bin') return { ...node, l: inline(node.l), r: inline(node.r) };
  if (t === 'index') return { ...node, obj: inline(node.obj), idx: inline(node.idx) };
  if (t === 'list' || t === 'seq') return { ...node, items: node.items.map((item) => inline(item)) };
  if (t === 'assign') return { ...node, value: inline(node.value) };
  if (t === 'call') {
    const inner = [...bound];
    const binds = node.spec != null && node.spec.binds;
    if (binds) {
      inner.push('_K');
      inner.push(node.args.length === 3 && constants.isBinderName(node.args[1])
        ? node.args[1].name : '_');
    }
    const args = node.args.map((arg, i) => {
      if (binds && i === 1 && node.args.length === 3 && constants.isBinderName(arg)) return arg;
      return inline(arg, i === 0 ? bound : inner);
    });
    return { ...node, args };
  }
  return node;
}

// The literal helpers: each whole-name definition, after the earlier literal
// helpers are inlined into it and it is folded, when what is left is a leaf.
function literalHelpers(leading, options) {
  const literals = new Map();
  for (const s of leading) {
    if (s.target.t !== 'var') continue;
    const folded = optimizeAstLogical(inlineLiterals(s.value, literals), options);
    if (LITERAL_TYPES.has(folded.t)) literals.set(s.target.name, folded);
  }
  return literals;
}

// The pipeline the planner probes: the result unwound, and where its source
// is a helper, that helper's definition unwound in turn.
function unwindThroughHelpers(result, defs, literals) {
  let { source, steps } = unwindPipeline(inlineLiterals(result, literals));
  const seen = new Set();
  while (source && source.t === 'var' && defs.has(source.name) && !seen.has(source.name)) {
    seen.add(source.name);
    const inner = unwindPipeline(inlineLiterals(defs.get(source.name), literals));
    source = inner.source;
    steps = [...inner.steps, ...steps];
  }
  return { source, steps };
}

// The names a tree reads, binders included: an over-approximation that can
// only keep an assignment the tree does not need, never drop one it does.
function readNames(node, out = new Set()) {
  if (!node) return out;
  if (node.t === 'var') {
    out.add(node.name);
    return out;
  }
  if (node.args) node.args.forEach((item) => readNames(item, out));
  if (node.items) node.items.forEach((item) => readNames(item, out));
  for (const child of [node.l, node.r, node.x, node.obj, node.idx, node.target, node.value]) {
    readNames(child, out);
  }
  return out;
}

// The leading assignments `node` depends on, in program order: those whose
// name it reads, and those THEY read, transitively.
function referencedAssignments(leading, node) {
  const needed = readNames(node);
  let grew = true;
  while (grew) {
    grew = false;
    for (const s of leading) {
      if (!needed.has(assignedName(s))) continue;
      for (const name of readNames(s.value)) {
        if (!needed.has(name)) {
          needed.add(name);
          grew = true;
        }
      }
    }
  }
  return leading.filter((s) => needed.has(assignedName(s)));
}

// `node` behind the assignments it depends on, as the program wrote them -- a
// seq the translator's stage 1 inlines and the evaluator runs in order -- or
// `node` itself when it depends on none.
function withHelpers(leading, node) {
  const kept = referencedAssignments(leading, node);
  return kept.length ? { t: 'seq', items: [...kept, node], pos: kept[0].pos } : node;
}

// The plan for a program nothing of which reaches the database. The
// continuation is the program itself, and the AST it exposes is the program's
// own, so a caller sees the same tree whichever way the plan went.
function pureMemoryPlan(program, dialect, catalog) {
  return new HybridPlan({ dialect, pureMemory: true, continuationProgram: program,
    continuationAst: program.ast, sourceTables: sourceTables(program.ast, catalog) });
}

function latestFieldName(n) {
  return n?.t === 'index' && n.obj.t === 'var' && n.obj.name === '_' && n.idx.t === 'text' ? n.idx.v : null;
}

function tryLatestMember(source, steps, dialect, catalog, opts, helpers) {
  if (!['mariadb', 'mysql', 'postgresql', 'sqlite'].includes(dialect)) return null;
  const rel = catalog.get(source.name, source.pos), revision = rel.unique_key;
  const at = steps.findIndex((s) => s.name === 'BUCKET');
  if (!revision || at < 0 || typeof rel.from !== 'string' || rel.correlate) return null;
  const ba = steps[at].args, partition = [2, 3].includes(ba.length) ? latestFieldName(ba[1]) : null;
  let body = ba.length === 3 ? ba[2] : null;
  const m = steps[at + 1];
  if (!body && m?.name === 'MAP' && m.args.length === 2) body = m.args[1];
  const pf = rel.fields[asciiUpper(partition ?? '')] ?? {}, rf = rel.fields[asciiUpper(revision)] ?? {};
  if (partition === null || body?.t !== 'call' || body.name !== 'RECORD' || body.args.length !== 4
      || !['NUM', 'TEXT'].includes(pf.type) || rf.type !== 'NUM' || pf.column !== partition
      || rf.column !== revision || pf.raw || rf.raw || rf.guard) return null;
  const ra = body.args, values = [ra[1], ra[3]];
  if (ra[0].t !== 'text' || ra[2].t !== 'text' || ra[0].v === ra[2].v) return null;
  const top = values.find((n) => n.t === 'call' && n.name === 'TOP_BY');
  if (!top || !values.some((n) => n.t === 'var' && n.name === '_K')) return null;
  const ta = top.args;
  if (ta.length !== 4 || ta[0].t !== 'var' || ta[0].name !== '_' || latestFieldName(ta[1]) !== revision
      || ta[2].t !== 'text' || ta[2].v !== 'DESC' || ta[3].t !== 'num' || ta[3].v !== '1') return null;
  for (const s of steps.slice(0, at)) {
    if (s.name === 'FILTER') continue;
    if (s.name !== 'SORT_BY' || ![2, 3].includes(s.args.length) || latestFieldName(s.args[1]) !== revision
        || (s.args.length === 3 && (s.args[2].t !== 'text' || s.args[2].v !== 'ASC'))) return null;
  }
  const dummy = { t: 'call', name: 'FILTER', pos: source.pos, args: [source, { t: 'bool', v: true, pos: source.pos }] };
  const prefix = helpers.wrap(buildPipeline(source, at ? steps.slice(0, at) : [dummy]));
  const sql = tryStatement(prefix, dialect, catalog, opts);
  if (!sql) return null;
  try {
    const emit = new Emit(dialect);
    let input = '_sel_input', groups = '_sel_latest';
    while (asciiUpper(input) === asciiUpper(rel.from)) input += '_';
    while ([asciiUpper(rel.from), asciiUpper(input)].includes(asciiUpper(groups))) groups += '_';
    const [qi, qg, qr, qmax, qfirst] = [input, groups, revision, '_sel_revision', '_sel_first'].map((s) => emit.ident(s));
    let key = emit.textOperand(new Fragment([emit.ident(partition)], pf.type, dialect)).asValue();
    const parts = [`WITH ${qi} AS (`, ...sql.parts,
      `), ${qg} AS (SELECT MAX(${qr}) AS ${qmax}, MIN(${qr}) AS ${qfirst} FROM ${qi} GROUP BY ${key}) `
      + `SELECT ${qi}.* FROM ${qi} JOIN ${qg} ON ${qi}.${qr} = ${qg}.${qmax} ORDER BY ${qg}.${qfirst} ASC`];
    const continuation = helpers.wrap(buildPipeline({ t: 'var', name: '_INPUT', pos: steps[at].pos }, steps.slice(at)));
    return new HybridPlan({ dialect,
      sqlStatement: new Fragment(parts, 'STATEMENT', dialect, sql.params, sql.paramKinds, sql.caveats),
      sqlPrefixAst: prefix, continuationAst: continuation, continuationProgram: new Program('', continuation),
      sourceTables: [rel.from], selectedMember: { partition_key: partition, revision_key: revision } });
  } catch (e) {
    if (e instanceof SqlError) return null;
    throw e;
  }
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

  // Stage 1 first, exactly as the translator runs it, for its verdict. A
  // program stage 1 refuses -- `A += 1; ...`, a bare statement before the
  // result -- is a program no part of which can be pushed down, which is a
  // pure-memory plan and not an exception: "none of it" is one of the
  // planner's answers. Its TREE is not what is planned, though: see "helper
  // assignments" above.
  const [constNames, constCtx] = constants.scope(catalog);
  let identityBarrier = false;
  try {
    const normalized = normalise.run(program.ast, constNames, constCtx);
    identityBarrier = constants.identityLossBeforeGrouping(normalized);
  } catch (error) {
    if (error instanceof SqlError) return pureMemoryPlan(program, dialect, catalog);
    throw error;
  }
  const { leading, result } = statements(program.ast);
  const literals = literalHelpers(leading, opts);
  const defs = definitions(leading);
  const isRelation = (node) => node && node.t === 'var' && catalog.has(node.name)
    && catalog.get(node.name, node.pos).kind === 'relation';
  const unwound = unwindThroughHelpers(result, defs, literals);
  if (!unwound.steps.length || !isRelation(unwound.source)) {
    return pureMemoryPlan(program, dialect, catalog);
  }
  const optimized = optimizeAstLogical(buildPipeline(unwound.source, unwound.steps), opts);
  const { source, steps } = unwindPipeline(optimized);
  if (!steps.length || !isRelation(source)) return pureMemoryPlan(program, dialect, catalog);

  const helpers = {
    defs,
    wrap: (node) => withHelpers(leading, node),
    // The physical sources of a wrapped tree are read off what the translator
    // renders: stage 1's tree, where an assignment a binder shadows is gone.
    tables: (wrapped) => sourceTables(normalise.run(wrapped, constNames, constCtx), catalog),
  };

  // The whole pipeline, unless its rows would be a bucket's keys: the
  // translator renders a bare bucket as its keys, and a plan that pushes the
  // whole of `... .> BUCKET(k)` would hand them back as the answer.
  const fullAst = helpers.wrap(buildPipeline(source, steps));
  const fullSql = identityBarrier || rowsAreNotTheValue(steps) ? null : tryStatement(fullAst, dialect, catalog, opts);
  if (fullSql !== null) {
    return new HybridPlan({ dialect, sqlStatement: fullSql, sqlPrefixAst: fullAst,
      pureSql: true, sourceTables: helpers.tables(fullAst) });
  }

  const latest = tryLatestMember(source, steps, dialect, catalog, opts, helpers);
  if (latest !== null) return latest;
  const fallthrough = identityBarrier ? null : tryPlanFallthrough(source, steps, dialect, catalog, opts, helpers);
  if (fallthrough !== null) return fallthrough;

  const inputVar = '_INPUT';
  for (let count = steps.length - 1; count >= 1; count--) {
    const prefixSteps = steps.slice(0, count);
    if (rowsAreNotTheValue(prefixSteps)) continue;
    const prefixAst = helpers.wrap(buildPipeline(source, prefixSteps));
    if (identityBarrier) {
      try {
        if (constants.identityLossBeforeGrouping(normalise.run(prefixAst, constNames, constCtx), true)) continue;
      } catch (e) {
        if (e instanceof SqlError) continue;
        throw e;
      }
    }
    const sql = tryStatement(prefixAst, dialect, catalog, opts);
    if (sql === null) continue;
    const remaining = steps.slice(count);
    const input = { t: 'var', name: inputVar, pos: remaining[0].pos };
    const continuationAst = helpers.wrap(buildPipeline(input, remaining));
    return new HybridPlan({
      dialect,
      sqlStatement: sql,
      sqlPrefixAst: prefixAst,
      continuationAst,
      continuationProgram: new Program('', continuationAst),
      continuationSourceVar: inputVar,
      sourceTables: helpers.tables(prefixAst),
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
