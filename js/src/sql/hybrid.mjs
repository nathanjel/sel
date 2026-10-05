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
import { MAX_DEPTH } from '../eval.mjs';
import { optimizeAstLogical, unwindPipeline, buildPipeline, LITERAL_TYPES } from '../optimizer.mjs';
import { asciiUpper } from '../lexer.mjs';
import { bindingForm } from '../registry.mjs';
import { BUILTIN_MANIFEST } from '../_builtin_manifest.mjs';
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
  // `bound` is the names in scope that are NOT the catalogue's: an aggregate's
  // binder over its body, and a variable the program assigned, for the rest of
  // the program. A read of one reads no table, whatever a relation of that name is
  // bound to.
  const visit = (node, bound) => {
    if (!node) return;
    if (node.t === 'var') {
      if (bound.has(node.name) || !bindings.has(node.name)) return;
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
    if (node.t === 'seq') {
      // One set for the whole sequence, grown in place: a copy per assignment is
      // quadratic in a chain of helpers.
      let scope = bound;
      let owned = false;
      for (const item of node.items) {
        visit(item, scope);
        if (item.t === 'assign') {
          let root = item.target;
          while (root && root.t === 'index') root = root.obj;
          if (root && root.t === 'var') {
            if (!owned) { scope = new Set(bound); owned = true; }
            scope.add(root.name);
          }
        }
      }
      return;
    }
    if (node.t === 'assign') {
      // The right side and the indexes of the target; the target's own name is a
      // write, not a read.
      visit(node.value, bound);
      let t = node.target;
      while (t && t.t === 'index') { visit(t.idx, bound); t = t.obj; }
      return;
    }
    if (node.t === 'call') {
      const form = bindingForm(node.name, node.args, node.spec);
      if (form !== null) {
        const inner = new Set([...bound, ...form.binds]);
        node.args.forEach((arg, i) => {
          const scope = form.scopes[i];
          if (scope === 'binder') return;
          visit(arg, scope === 'inner' ? inner : bound);
        });
        return;
      }
    }
    if (node.args) node.args.forEach((n) => visit(n, bound));
    if (node.items) node.items.forEach((n) => visit(n, bound));
    if (node.entries) node.entries.forEach(([, v]) => visit(v, bound));
    if (node.l) visit(node.l, bound);
    if (node.r) visit(node.r, bound);
    if (node.x) visit(node.x, bound);
    if (node.obj) visit(node.obj, bound);
    if (node.idx) visit(node.idx, bound);
  };
  visit(ast, new Set());
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
// not a split point and not a full pushdown: its
// continuation would read `_["C"]` where the database sent nothing.
function joinRowsLackBinders(steps) {
  let joined = false;
  for (const step of steps) {
    if (step.name === 'LINK' || step.name === 'LINK_LEFT') joined = true;
    else if (step.name === 'MAP' || step.name === 'SELECT_COLS' || step.name === 'BUCKET') joined = false;
  }
  return joined;
}

// Whether an explicit sort's order would not survive a later step in SQL. SEL's
// result is in the order the sort gave it, and a database promises nothing about the
// order of rows once they pass through a derived table into a join, a group or a
// second sort's tie-break: a BUCKET's groups come out in first-appearance order in
// SEL and in engine order in SQL, a LINK's rows are the left's order then the
// right's, and a later sort keeps the earlier sort's order among its ties, which is
// gone once a projection hid the earlier key. A LIMIT beside the earlier ORDER BY
// decides which rows survive, not any of this. A prefix that ends before that step
// is exact; one that includes it answers in another order
// (docs/internals/sql-translation.md 12.1, "Order").
const ORDER_SORTS = new Set(['SORT', 'SORT_DESC', 'SORT_BY', 'TOP', 'TOP_DESC', 'TOP_BY']);
function orderIsLost(steps) {
  let sorted = false;
  let projected = false;
  for (const step of steps) {
    const name = step.name;
    if (ORDER_SORTS.has(name)) {
      if (sorted && projected) return true;
      sorted = true;
      projected = false;
    } else if (sorted && (name === 'MAP' || name === 'SELECT_COLS')) {
      projected = true;
    } else if (sorted && (name === 'BUCKET' || name === 'LINK' || name === 'LINK_LEFT')) {
      return true;
    }
  }
  return false;
}

// The three together: a prefix whose SQL rows are not the value SEL would have
// produced for it, whatever the translator says about it.
function rowsAreNotTheValue(steps) {
  return bucketRowsAreKeys(steps) || joinRowsLackBinders(steps) || orderIsLost(steps);
}

const SQL_SPECIAL_CALLS = new Set([
  'IF', 'COND', 'COALESCE', 'COUNT', 'SUM', 'MIN', 'MAX', 'RECORD', 'LIST',
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

// FILTER retains ordinal keys that SQL rows plus the local MAP cannot restore.
// The rows the database returns are a rowset numbered 1..n. run() has those keys
// only when the last step that decides them renumbers: every step except FILTER
// does (a sort, TAKE, DROP, DISTINCT, MAP, BUCKET, LINK, ... all build a fresh
// list), and a FILTER keeps the keys its rows had in the list it read. So a split
// directly after a FILTER (with nothing renumbering in between) hands the
// continuation different keys than run() has -- the plan is unsafe whenever the
// continuation can observe them: it reads `_K` before something renumbers, or the
// keys are the answer itself.
const KEY_RETAINING = new Set(['FILTER']);

// The steps the MAP fall-through may push past the MAP. Each keeps the rows
// as they are -- the same records, fewer or reordered -- so the custom half of
// the projection still runs over its own input. A step that changes the row
// shape (MAP, SELECT_COLS, LINK, BUCKET) would put it over something else, and
// the whole-row comparisons (DEDUPE, DISTINCT, the keyless sorts) would compare
// the dependency columns SQL carries where SEL compares the custom values.
const FALLTHROUGH_DOWNSTREAM = new Set(['SORT_BY', 'TOP_BY', 'TAKE', 'DROP']);

function argsReadKey(step) {
  let found = false;
  const visit = (n) => {
    if (found || !n) return;
    if (n.t === 'var') { if (n.name === '_K') found = true; return; }
    if (n.args) n.args.forEach(visit);
    if (n.items) n.items.forEach(visit);
    if (n.entries) n.entries.forEach(([, v]) => visit(v));
    for (const c of [n.l, n.r, n.x, n.obj, n.idx]) visit(c);
  };
  step.args.slice(1).forEach(visit);
  return found;
}

function keysObservable(prefixSteps, continuationSteps) {
  let retained = false;
  for (const step of prefixSteps) retained = KEY_RETAINING.has(step.name);
  if (!retained) return false;
  for (const step of continuationSteps) {
    if (argsReadKey(step)) return true;
    if (!KEY_RETAINING.has(step.name)) return false;   // renumbers: the keys stop here
  }
  return true;                                          // the retained keys are the value
}

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

  // Every step after the MAP stays in memory, behind the MAP's custom half: that
  // half runs over every row the SQL returns, and it can raise on a row a later
  // TAKE, DROP or FILTER would have cut -- run() evaluates the MAP first and
  // reports it. A row-cutting step never crosses a local half that can raise, and
  // nothing here can prove a call with no SQL spelling cannot.
  //
  // Eligibility is unchanged -- only steps that keep the rows as they are, reading
  // only the projected keys, may follow: a step that reshapes them (BUCKET, another
  // MAP, SELECT_COLS, LINK) would hand the custom half rows that are no longer its
  // input -- but none of them is pushed into the statement any more.
  const downstream = steps.slice(mapIndex + 1);
  if (downstream.some((step) => !FALLTHROUGH_DOWNSTREAM.has(step.name))) return null;
  const projected = new Set(pushable.map((pair) => String(pair.key.v)));
  for (const step of downstream) {
    for (const arg of step.args.slice(1)) {
      for (const field of collectFieldReferences(arg, null)) {
        if (!projected.has(field)) return null;
      }
    }
  }
  // The continuation starts at the MAP, and the split is directly after whatever
  // precedes it: a FILTER there hands it a renumbered rowset.
  if (keysObservable(steps.slice(0, mapIndex), steps.slice(mapIndex))) return null;

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
  const rewrittenSteps = [...steps.slice(0, mapIndex), rewrittenMap];
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
  const continuationMap = { ...mapStep,
    args: details.explicit
      ? [input, mapStep.args[1], continuationRecord]
      : [input, continuationRecord] };
  const continuationAst = helpers.wrap(buildPipeline(input, [continuationMap, ...downstream]));
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
// before the pipeline, not once per row. So
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
    // Which argument runs where is the manifest's decision (registry.bindingForm),
    // as in stage 1: a binder position is a name, never a read, and an inner
    // argument sees the binders' names. Only the `3 args, name in position 1`
    // shape used to be recognised, which inlined a literal helper into the binder
    // slot of a 4-argument SORT_BY and turned a pure SQL pipeline into memory.
    const form = bindingForm(node.name, node.args, node.spec);
    const inner = form ? [...bound, ...form.binds] : bound;
    const args = node.args.map((arg, i) => {
      const scope = form ? form.scopes[i] : 'outer';
      if (scope === 'binder') return arg;
      return inline(arg, scope === 'inner' ? inner : bound);
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
  // The source the loop stopped at reads the catalogue's binding when its name is
  // also a helper's (the helper was written `ORDERS = ORDERS .> DROP(2)`): mark
  // it, so wrapping the pipeline in the helpers again does not inline the helper
  // into the very read that was its own definition (DROP twice; the shape
  // sql/cases/48-scope-and-slots.sqlt pins).
  if (source && source.t === 'var' && defs.has(source.name)) {
    source = { ...source, binding: true };
  }
  return { source, steps };
}

// The names a tree reads, binders included: an over-approximation that can
// only keep an assignment the tree does not need, never drop one it does.
function readNames(node, out = new Set()) {
  if (!node) return out;
  if (node.t === 'var') {
    // A read of the BINDING, reached by unwinding through a helper of the same
    // name (`ORDERS = ORDERS .> DROP(2)`), is not a read of that helper.
    if (!node.binding) out.add(node.name);
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
  // A worklist over names, so each assignment is read once: a fixpoint that
  // rescans every statement per round is quadratic in a chain of helpers.
  const byName = new Map();
  for (const s of leading) {
    const name = assignedName(s);
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push(s);
  }
  const needed = new Set();
  const work = [...readNames(node)];
  while (work.length > 0) {
    const name = work.pop();
    if (needed.has(name)) continue;
    needed.add(name);
    for (const s of byName.get(name) ?? []) work.push(...readNames(s.value));
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

// A tree taller than four times the evaluator's depth cannot be run by it (the
// evaluator answers E_DEPTH) and cannot be walked recursively by the planner
// without finding the host's stack -- `dependencies()` was capped for exactly
// this. Measured iteratively; such a program is a pure-memory plan, whose
// continuation raises what run() raises.
function tooDeepToPlan(ast) {
  const limit = 4 * MAX_DEPTH;
  const stack = [[ast, 1]];
  while (stack.length > 0) {
    const [node, depth] = stack.pop();
    if (!node) continue;
    if (depth > limit) return true;
    for (const c of [node.l, node.r, node.x, node.obj, node.idx, node.target, node.value]) {
      if (c) stack.push([c, depth + 1]);
    }
    for (const list of [node.args, node.items]) {
      if (list) for (const c of list) stack.push([c, depth + 1]);
    }
  }
  return false;
}

// The relations a too-deep tree reads, first use first: the same answer as
// sourceTables where no name is shadowed, from an iterative walk.
function flatSourceTables(ast, catalog) {
  const out = [];
  const seen = new Set();
  const stack = [ast];
  const pending = [];
  while (stack.length > 0) {
    const node = stack.pop();
    if (!node) continue;
    if (node.t === 'var') {
      if (catalog.has(node.name)) {
        const b = catalog.get(node.name, node.pos);
        if (b.kind === 'relation') {
          const table = physicalSource(b);
          if (!seen.has(table)) { seen.add(table); out.push(table); }
        }
      }
      continue;
    }
    pending.length = 0;
    for (const list of [node.items, node.args]) if (list) pending.push(...list);
    for (const c of [node.l, node.r, node.x, node.obj, node.idx, node.target, node.value]) {
      if (c) pending.push(c);
    }
    for (let i = pending.length - 1; i >= 0; i--) stack.push(pending[i]);
  }
  return out;
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
  if (tooDeepToPlan(program.ast)) {
    return new HybridPlan({ dialect, pureMemory: true, continuationProgram: program,
      continuationAst: program.ast, sourceTables: flatSourceTables(program.ast, catalog) });
  }

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
    if (keysObservable(prefixSteps, steps.slice(count))) continue;
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
    // A 3-argument LINK names the two sides of the row it builds after the variables it
    // joined: the left side is the pipeline's own source variable, wherever in the
    // continuation the LINK falls (spec 7.4). The rows are therefore fed to the
    // continuation under that name, or the joined row would carry the left side under
    // `_INPUT` where run() has it under ORDERS. A step that also READS that name (a
    // self-join `ORDERS .> TAKE(4) .> LINK(ORDERS, ...)`) would find the truncated rows
    // where run() finds the whole relation, so that split is not made: the join stays in
    // memory, over the relation. The same rule in every host; a LINK in the prefix has
    // already named its sides.
    const isLink = (step) => step.name === 'LINK' || step.name === 'LINK_LEFT';
    const needsRebind = !prefixSteps.some(isLink)
      && remaining.some((step) => isLink(step) && step.args.length === 3);
    if (needsRebind && (source.t !== 'var'
        || remaining.some((step) => (step.args ?? []).slice(1).some((a) => readNames(a).has(source.name))))) {
      continue;
    }
    const feed = needsRebind ? source.name : inputVar;
    const input = { t: 'var', name: feed, pos: remaining[0].pos };
    const continuationAst = helpers.wrap(buildPipeline(input, remaining));
    return new HybridPlan({
      dialect,
      sqlStatement: sql,
      sqlPrefixAst: prefixAst,
      continuationAst,
      continuationProgram: new Program('', continuationAst),
      continuationSourceVar: feed,
      sourceTables: helpers.tables(prefixAst),
    });
  }

  return pureMemoryPlan(program, dialect, catalog);
}

// The names a program may write through: the base variable of every assignment
// target anywhere in the tree (an over-approximation is safe; it only costs a copy).
// Iterative, like every other walk of a tree that can be as deep as its source is long.
const EFFECTS = new WeakMap();

function continuationEffects(ast) {
  if (!ast || typeof ast !== 'object') {
    return { assignedRoots: new Set(), callsApplicationFunction: false };
  }
  let cached = EFFECTS.get(ast);
  if (cached) return cached;
  const names = new Set();
  let callsApp = false;
  const stack = [ast];
  while (stack.length > 0) {
    const n = stack.pop();
    if (!n || typeof n !== 'object') continue;
    if (n.t === 'assign') {
      let target = n.target;
      while (target && target.t === 'index') {
        if (target.idx) stack.push(target.idx);
        target = target.obj;
      }
      if (target && target.t === 'var') names.add(target.name);
      if (n.value) stack.push(n.value);
      continue;
    }
    if (n.t === 'call') {
      const upper = (n.name || '').toUpperCase();
      if (!Object.prototype.hasOwnProperty.call(BUILTIN_MANIFEST, upper)) {
        callsApp = true;
      }
    }
    if (n.args) for (const item of n.args) stack.push(item);
    if (n.items) for (const item of n.items) stack.push(item);
    for (const key of ['l', 'r', 'x', 'obj', 'idx', 'value']) if (n[key]) stack.push(n[key]);
  }
  cached = { assignedRoots: names, callsApplicationFunction: callsApp };
  EFFECTS.set(ast, cached);
  return cached;
}

function continuationRoot(plan, context) {
  if (!(context instanceof Value)) {
    return Value.fromNative(context || {});
  }
  const ast = plan.continuationProgram ? plan.continuationProgram.ast : null;
  const effects = continuationEffects(ast);
  if (effects.callsApplicationFunction) {
    return context.clone();
  }
  return context.shallowRoot(effects.assignedRoots);
}

export function executeHybrid(plan, dbRunner, context = null) {
  if (!(plan instanceof HybridPlan)) throw new TypeError('executeHybrid expects a HybridPlan');
  // The caller's context is never written, whatever the classification: a program
  // that assigns (`A += 1`) would leave A in it, and which plans do depends on how
  // the planner split them -- an accident, not a contract.
  if (plan.pureMemory) {
    return plan.continuationProgram.run(continuationRoot(plan, context));
  }
  const runSql = () => {
    const fragment = plan.sqlStatement;
    return dbRunner(fragment.asStatement('params'), fragment.bindings());
  };
  if (plan.pureSql) return runSql();
  const rows = runSql();
  const root = continuationRoot(plan, context);
  root.set(plan.continuationSourceVar,
    rows instanceof Value ? rows : Value.fromNative(rows));
  return plan.continuationProgram.run(root);
}

