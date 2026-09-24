import { Value, RecordShape, NONE, TEXT, structuralHash, internRecordShape } from '../value.mjs';
import * as D from '../decimal.mjs';
import { define } from '../registry.mjs';
import { BUILTIN_MANIFEST } from '../_builtin_manifest.mjs';
import { SelError, fail } from '../errors.mjs';

function elements(value) {
  if (value.size() > 0) return value.entries();
  return value.kind === NONE ? [] : [['1', value]];
}

function firstCollectionItem(value) {
  if (value.isNull() || (value.kind === NONE && value.size() === 0)) return null;
  if (value.storage !== null) return value.storage.length > 0 ? value.storage[0] : null;
  if (value.children) return value.children.values().next().value || null;
  if (value._entries !== null) return value._entries.length > 0 ? value._entries[0][1] : null;
  return value.kind === NONE ? null : value;
}

// Join/aggregate iteration is deliberately separate from `entries()`: the
// latter is a host-facing snapshot and must allocate key/value pairs, whereas
// the relational lane only needs each row. Flat storage therefore stays a
// packed V8 array and no per-row pair arrays or list-key strings are created.
function forEachCollectionItem(value, callback) {
  if (value.storage !== null) {
    for (let i = 0; i < value.storage.length; i++) callback(value.storage[i]);
    return;
  }
  if (value.children) {
    for (const item of value.children.values()) callback(item);
    return;
  }
  if (value._entries !== null) {
    for (const [, item] of value._entries) callback(item);
    return;
  }
  if (value.kind !== NONE) callback(value);
}

define({ name: 'COUNT', min: 1, max: 1, fn: (args) => Value.int(args.val(0).size()) });

define({
  name: 'INDEXES', min: 1, max: 1,
  fn: (args) => Value.listOwned(args.val(0).keys().map(Value.text)),
});

define({
  name: 'HAS', min: 2, max: 2,
  fn: (args) => Value.bool(args.val(0).has(args.text(1))),
});

define({
  name: 'LIST', min: 0, max: Infinity,
  fn: (args) => Value.listOwned(Array.from({ length: args.count() }, (_, i) => args.val(i).clone())),
});

function recordWithShape(args, shape) {
  // Keep key/value evaluation and cloning interleaved. Packed arrays avoid
  // allocating a pair for every field in a prepared projection. Verify the
  // actual keys after evaluation so stale metadata still uses the fallback.
  const keys = [];
  const values = [];
  for (let i = 0; i < args.count(); i += 2) {
    keys.push(args.text(i));
    values.push(args.val(i + 1).clone());
  }
  if (shape.size === keys.length) {
    let i = 0;
    while (i < keys.length && keys[i] === shape.keys[i]) i++;
    if (i === keys.length) return Value.shapedFromShape(shape, values);
  }
  return Value.fromEntries(keys.map((key, i) => [key, values[i]]));
}

function recordFromArgs(args) {
  if (args.recordShape) return recordWithShape(args, args.recordShape);
  const entries = [];
  for (let i = 0; i < args.count(); i += 2) {
    entries.push([args.text(i), args.val(i + 1).clone()]);
  }
  return Value.fromEntries(entries);
}

define({
  name: 'RECORD', min: 0, max: Infinity,   // even count: spec/builtins.json
  fn: recordFromArgs,
});

define({
  name: 'TAKE', min: 2, max: 2,
  fn: (args) => {
    const value = args.val(0);
    const count = args.nonNegInt(1);
    if (count === 0 || value.isNull()) return Value.list([]);
    if (value.isList && value.storage !== null) return Value.listOwned(value.storage.slice(0, count));
    return Value.listOwned(elements(value).slice(0, count).map(([, item]) => item));
  },
});

define({
  name: 'DROP', min: 2, max: 2,
  fn: (args) => {
    const value = args.val(0);
    const count = args.nonNegInt(1);
    if (value.isNull()) return Value.list([]);
    if (value.isList && value.storage !== null) return Value.listOwned(value.storage.slice(count));
    return Value.listOwned(elements(value).slice(count).map(([, item]) => item));
  },
});

define({
  name: 'SELECT_COLS', min: 2, max: Infinity,
  fn: (args) => {
    const value = args.val(0);
    if (value.isNull()) return Value.list([]);
    const columns = [];
    for (let i = 1; i < args.count(); i++) columns.push(args.text(i));
    const rows = elements(value).map(([, row]) => {
      const entries = [];
      for (const column of columns) {
        if (row.has(column)) entries.push([column, row.get(column).clone()]);
      }
      return Value.fromEntries(entries);
    });
    return Value.listOwned(rows);
  },
});

function doDedupe(args) {
  const value = args.val(0);
  if (value.isNull()) return Value.list([]);
  const buckets = new Map();
  const out = [];
  for (const [, item] of elements(value)) {
    const hash = structuralHash(item);
    const bucket = buckets.get(hash) || [];
    if (!bucket.some((existing) => item.eql(existing))) {
      bucket.push(item);
      buckets.set(hash, bucket);
      out.push(item);
    }
  }
  return Value.listOwned(out);
}

define({ name: 'DISTINCT', min: 1, max: 1, fn: doDedupe });
define({ name: 'DEDUPE', min: 1, max: 1, fn: doDedupe });

// --- relational links ------------------------------------------------------

function exprDependsOnlyOn(node, allowed) {
  if (!node) return true;
  switch (node.t) {
    case 'var': return allowed.has(node.name.toUpperCase());
    case 'index': return exprDependsOnlyOn(node.obj, allowed) && exprDependsOnlyOn(node.idx, allowed);
    case 'call': return node.args.every((item) => exprDependsOnlyOn(item, allowed));
    case 'bin': return exprDependsOnlyOn(node.l, allowed) && exprDependsOnlyOn(node.r, allowed);
    case 'un': return exprDependsOnlyOn(node.x, allowed);
    case 'assign': return exprDependsOnlyOn(node.target, allowed)
      && exprDependsOnlyOn(node.value, allowed);
    case 'seq': return node.items.every((item) => exprDependsOnlyOn(item, allowed));
    default: return true;
  }
}

function tryExtractEquiKeys(node, b1, b2) {
  if (!node || node.t !== 'bin' || (node.op !== '==' && node.op !== '$==')) return null;
  const leftNames = new Set([b1, b1.toLowerCase(), '_1', '_'].map((x) => x.toUpperCase()));
  const rightNames = new Set([b2, b2.toLowerCase(), '_2'].map((x) => x.toUpperCase()));
  if (exprDependsOnlyOn(node.l, leftNames) && exprDependsOnlyOn(node.r, rightNames)) {
    return { left: node.l, right: node.r, numeric: node.op === '==' };
  }
  if (exprDependsOnlyOn(node.r, leftNames) && exprDependsOnlyOn(node.l, rightNames)) {
    return { left: node.r, right: node.l, numeric: node.op === '==' };
  }
  return null;
}

function singleRelationName(node) {
  if (!node) return null;
  if (node.t === 'var') return node.name;
  if (node.t === 'call' && node.args.length > 0
      && node.name !== 'LINK' && node.name !== 'LINK_LEFT') {
    return singleRelationName(node.args[0]);
  }
  return null;
}

function canonicalJoinKey(value, numeric) {
  if (!value || value.isNull()) return null;
  if (numeric) {
    let d;
    try { d = value.asDecimal(); } catch (_) { return null; }
    // One key per number, as `==` compares it (spec §7.4): trailing fraction
    // zeros and a negative zero are representation, not value.
    let digits = d.digits;
    let scale = d.scale;
    if (digits === 0n) return '0';
    while (scale > 0 && digits % 10n === 0n) { digits /= 10n; scale--; }
    return D.format({ neg: d.neg, digits, scale });
  }
  return value.kind === 'TEXT' ? value.scalar : null;
}

// `_1` and `_2` name a position, not a relation: an argument with no name is
// bound bare (spec §7.4).
function isPositionalBinder(name) {
  return name === '_1' || name === '_2';
}

const ALIAS_PLANS = new Map();

function ensureRowTableAlias(row, tableName) {
  if (!tableName || isPositionalBinder(tableName) || row.has(tableName)) return row;
  const lower = tableName.toLowerCase();
  if (row.shape) {
    const oldShape = row.shape;
    let cached = ALIAS_PLANS.get(oldShape);
    if (cached?.tableName !== tableName) cached = null;
    if (!cached) {
      const addLower = lower !== tableName && !oldShape.keyMap.has(lower);
      const keys = [...oldShape.keys, tableName];
      if (addLower) keys.push(lower);
      cached = { shape: new RecordShape(keys), oldSize: oldShape.size, addLower, tableName };
      if (keys.length <= 256 && keys.reduce((n, key) => n + key.length, 0) <= 16384) {
        if (ALIAS_PLANS.size >= 256) ALIAS_PLANS.clear();
        ALIAS_PLANS.set(oldShape, cached);
      }
    }
    const storage = row.storage.slice(0, cached.oldSize);
    storage.push(row);
    if (cached.addLower) storage.push(row);
    return Value.shapedFromShape(cached.shape, storage);
  }
  const entries = row.entries();
  entries.push([tableName, row]);
  if (lower !== tableName && !row.has(lower)) entries.push([lower, row]);
  return Value.fromEntriesPreserveDuplicates(entries);
}

// An unmatched LINK_LEFT row's right side (spec §7.4): shaped like the first
// right element as bound -- SAMPLE, already extended with the name -- every
// field NULL; with no right elements, just the name keys; with no name either,
// NULL.
function makeNullRecord(sample, tableName) {
  const entries = [];
  const seen = new Set();
  if (sample) for (const key of sample.keys()) { entries.push([key, Value.none()]); seen.add(key); }
  if (!sample && tableName && !isPositionalBinder(tableName)) {
    for (const name of [tableName, tableName.toLowerCase()]) {
      if (!seen.has(name)) { entries.push([name, Value.none()]); seen.add(name); }
    }
  }
  return Value.fromEntries(entries);
}

function isNestedRecord(value) {
  return value.size() > 0 && !value.isList;
}

// A field's category for the joined row (spec §7.4): a nested record (a record
// with a field) is carried, anything else is a scalar field -- and a right
// scalar that is NULL is not promoted.
const SCALAR = 0;
const NULL_FIELD = 1;
const NESTED = 2;
function category(value) {
  if (value.kind !== NONE || value.isList) return SCALAR;
  return value.size() > 0 ? NESTED : NULL_FIELD;
}

function binderKeys(name, positional) {
  const keys = [name];
  const lower = name.toLowerCase();
  if (lower !== name) keys.push(lower);
  if (name !== positional) keys.push(positional);
  return keys;
}

// One joined row, from THIS pair's two elements (spec §7.4): the nested
// records the left element carries, the left binders, the right binders, then
// the left element's scalar fields whose names (ASCII-case-insensitively) are
// not the right element's, then the right element's non-NULL scalar fields
// whose names are not the left's -- each key once, where it first occurred, a
// binder key holding the row this LINK bound. RIGHT is null for an unmatched
// LINK_LEFT row, whose right element is NULL_RIGHT and promotes nothing.
function makeJoinedRow(left, right, b1, b2, nullRight) {
  const entries = [];
  const slot = new Map();
  const put = (key, value) => {
    if (slot.has(key)) return;
    slot.set(key, entries.length);
    entries.push([key, value]);
  };
  const bind = (key, value) => {
    if (slot.has(key)) entries[slot.get(key)] = [key, value];
    else put(key, value);
  };
  const leftEntries = left.entries();
  for (const [key, value] of leftEntries) if (category(value) === NESTED) put(key, value);
  for (const name of binderKeys(b1, '_1')) bind(name, left);
  const rside = right || nullRight || Value.none();
  for (const name of binderKeys(b2, '_2')) bind(name, rside);
  const rightEntries = rside.size() > 0 && !rside.isList ? rside.entries() : [];
  const rightNames = new Set(rightEntries.map(([key]) => key.toUpperCase()));
  for (const [key, value] of leftEntries) {
    if (category(value) !== NESTED && !rightNames.has(key.toUpperCase())) put(key, value);
  }
  if (right) {
    const leftNames = new Set(leftEntries.map(([key]) => key.toUpperCase()));
    for (const [key, value] of rightEntries) {
      if (category(value) === SCALAR && !leftNames.has(key.toUpperCase())) put(key, value);
    }
  }
  return Value.fromEntries(entries);
}

const OP_LEFT_SLOT = 0;
const OP_RIGHT_SLOT = 1;
const OP_LEFT = 2;
const OP_RIGHT = 3;
const OP_LEFT_NESTED = 4;   // a left field that was a nested record (makeJoinBuilder)

// The joined row of a pair as a plan over the two elements' storage, for every
// pair whose elements have these shapes and field categories: the output shape
// and, per output key, where its value comes from. makeJoinedRow is the rule;
// this is it, compiled.
function rowPlan(left, rside, matched, b1, b2) {
  const keys = [];
  const ops = [];
  const slots = [];
  const at = new Map();
  const put = (key, op, slotIndex) => {
    if (at.has(key)) return;
    at.set(key, keys.length);
    keys.push(key); ops.push(op); slots.push(slotIndex);
  };
  const bind = (key, op) => {
    if (at.has(key)) { ops[at.get(key)] = op; slots[at.get(key)] = -1; } else put(key, op, -1);
  };
  const lkeys = left.shape.keys;
  const lcat = left.storage.map(category);
  lkeys.forEach((key, i) => { if (lcat[i] === NESTED) put(key, OP_LEFT_SLOT, i); });
  for (const name of binderKeys(b1, '_1')) bind(name, OP_LEFT);
  for (const name of binderKeys(b2, '_2')) bind(name, OP_RIGHT);
  const rkeys = rside.shape ? rside.shape.keys : [];
  const rightNames = new Set(rkeys.map((key) => key.toUpperCase()));
  lkeys.forEach((key, i) => {
    if (lcat[i] !== NESTED && !rightNames.has(key.toUpperCase())) put(key, OP_LEFT_SLOT, i);
  });
  if (matched) {
    const rcat = rside.storage.map(category);
    const leftNames = new Set(lkeys.map((key) => key.toUpperCase()));
    rkeys.forEach((key, i) => {
      if (rcat[i] === SCALAR && !leftNames.has(key.toUpperCase())) put(key, OP_RIGHT_SLOT, i);
    });
  }
  return { shape: internRecordShape(keys), ops: Uint8Array.from(ops), slots: Int32Array.from(slots) };
}

// Only two facts about a field decide a joined row (spec §7.4): on the left,
// whether it is a nested record (carried first) or not (promoted, NULL or
// not); on the right, whether it is a non-NULL scalar (promoted) or not.
function leftNested(v) {
  return v.kind === NONE && !v.isList && (v.shape ? v.shape.size > 0 : v.size() > 0);
}

// A plan's builder: (left, rside, checkLeft, checkRight) => the row, or null
// when the pair breaks the plan's assumptions -- with checkLeft, that each
// left field is (or is not) a nested record as it was; with checkRight, that
// a right field it copies (promotes) is a non-NULL scalar and that one it
// leaves out for being NULL or a record still is one. Each field is checked as it is copied;
// OP_LEFT_NESTED copies a left field that was a nested record, and the left
// fields the row leaves out are checked apart (lrest, lrestNested).
function makeJoinBuilder(plan, lnested, rkept) {
  const { shape, slots } = plan;
  const ops = Uint8Array.from(plan.ops);
  const copied = new Uint8Array(lnested.length);
  for (let i = 0; i < ops.length; i++) {
    if (ops[i] !== OP_LEFT_SLOT) continue;
    copied[slots[i]] = 1;
    if (lnested[slots[i]]) ops[i] = OP_LEFT_NESTED;
  }
  const rest = [];
  for (let i = 0; i < lnested.length; i++) if (!copied[i]) rest.push(i);
  const lrest = Int32Array.from(rest);
  const lrestNested = rest.map((i) => lnested[i]);
  const n = ops.length;
  return (left, rside, checkLeft, checkRight) => {
    const ls = left.storage;
    const rs = rside.storage;
    if (checkLeft) {
      for (let i = 0; i < lrest.length; i++) if (leftNested(ls[lrest[i]]) !== lrestNested[i]) return null;
    }
    if (checkRight) {
      for (let i = 0; i < rkept.length; i++) {
        const v = rs[rkept[i]];
        if (v.kind !== NONE || v.isList) return null;
      }
    }
    const storage = new Array(n);
    for (let i = 0; i < n; i++) {
      switch (ops[i]) {
        case OP_LEFT_SLOT: {
          const v = ls[slots[i]];
          if (checkLeft && leftNested(v)) return null;
          storage[i] = v;
          break;
        }
        case OP_LEFT_NESTED: {
          const v = ls[slots[i]];
          if (checkLeft && !leftNested(v)) return null;
          storage[i] = v;
          break;
        }
        case OP_RIGHT_SLOT: {
          const v = rs[slots[i]];
          if (checkRight && v.kind === NONE && !v.isList) return null;
          storage[i] = v;
          break;
        }
        case OP_LEFT: storage[i] = left; break;
        default: storage[i] = rside; break;
      }
    }
    return Value.shapedFromShape(shape, storage);
  };
}

// project(left, right): makeJoinedRow, through compiled plans. For each pair
// of shapes the plans built so far are tried in turn, and a pair none fits
// gets its own plan from the rule (rowPlan). A left row's matches come one
// after another, so the last pair's plan is tried first, checking the left
// row only when it is a new one. When the join has seen that every right row
// is flat (flatRow), project.rightFlat is set and the right checks are
// skipped: a plan made from a flat row holds for every flat row of its shape.
function makeJoinProjector(b1, b2, nullRight) {
  const plans = new Map();
  let lastLeft = null;
  let lastLeftShape = null;
  let lastRightShape = null;
  let lastMatched = false;
  let lastBuild = null;
  let lastCandidates = null;
  // A new plan's builder, from this pair by the rule, with the facts it
  // assumed: each left field's nestedness, and the right fields the left does
  // not name that were left out for being NULL or records -- they must still
  // be, for the plan to hold. (A scalar left out because its name is already
  // a key of the row stays out whatever it holds; so does one named like a
  // binder key, which the binder holds.)
  const newBuilder = (left, rside, matched) => {
    const plan = rowPlan(left, rside, matched, b1, b2);
    const leftNames = new Set(left.shape.keys.map((k) => k.toUpperCase()));
    const binderNames = new Set([...binderKeys(b1, '_1'), ...binderKeys(b2, '_2')]);
    const kept = [];
    if (matched) {
      rside.shape.keys.forEach((k, i) => {
        const v = rside.storage[i];
        if (!leftNames.has(k.toUpperCase()) && !binderNames.has(k) && v.kind === NONE && !v.isList) kept.push(i);
      });
    }
    return makeJoinBuilder(plan, left.storage.map(leftNested), Int32Array.from(kept));
  };
  const project = (left, right) => {
    const rside = right || nullRight;
    if (!left.shape || !rside || !rside.shape) return makeJoinedRow(left, right, b1, b2, nullRight);
    const matched = Boolean(right);
    let candidates;
    if (lastBuild !== null && lastLeftShape === left.shape && lastRightShape === rside.shape && lastMatched === matched) {
      const row = lastBuild(left, rside, lastLeft !== left, !project.rightFlat);
      if (row !== null) { lastLeft = left; return row; }
      candidates = lastCandidates;
    } else {
      let byRight = plans.get(left.shape);
      if (!byRight) { byRight = new Map(); plans.set(left.shape, byRight); }
      let entry = byRight.get(rside.shape);
      if (!entry) { entry = [[], []]; byRight.set(rside.shape, entry); }
      candidates = entry[matched ? 1 : 0];
    }
    let build = null;
    let row = null;
    for (const candidate of candidates) {
      row = candidate(left, rside, true, !project.rightFlat);
      if (row !== null) { build = candidate; break; }
    }
    if (build === null) {
      build = newBuilder(left, rside, matched);
      candidates.push(build);
      row = build(left, rside, false, false);
    }
    lastLeft = left; lastLeftShape = left.shape; lastRightShape = rside.shape; lastMatched = matched;
    lastBuild = build; lastCandidates = candidates;
    return row;
  };
  project.rightFlat = false;
  return project;
}

// Whether every field of ROW is a non-NULL scalar, but for those named like a
// binder key (BINDERNAMES), which no joined row takes from it.
function flatRow(row, binderNames) {
  if (!row.shape) return false;
  const keys = row.shape.keys;
  const st = row.storage;
  for (let i = 0; i < st.length; i++) {
    const v = st[i];
    if (v.kind === NONE && !v.isList && !binderNames.has(keys[i])) return false;
  }
  return true;
}

// --- the join pre-filter (SEL-0049, SEL-0050, SEL-0052) ---------------------
//
// A FILTER over a LINK hands the join its conjuncts (aggregate.mjs,
// leadingFieldConjuncts); the join pre-applies to its left rows those whose
// fields no right side carries, so the joined rows they would have produced
// -- all dropped by the same conjunct -- are never built. Decided here from
// the rows, at run time, so the physical tree stays a function of the AST.

// Whether evaluating NODE can be observed only through its value: no
// assignment, no sequence, no call outside the shipped builtins (a host's own
// function may do anything), no ABORT. Such a node may be evaluated out of
// order -- the right source of a join before the left -- which is what lets a
// FILTER's conjuncts travel down a chain of joins.
function pureSource(node) {
  if (!node) return true;
  switch (node.t) {
    case 'var': case 'num': case 'text': case 'bool': return true;
    case 'index': return pureSource(node.obj) && pureSource(node.idx);
    case 'bin': return pureSource(node.l) && pureSource(node.r);
    case 'un': return pureSource(node.x);
    case 'list': return node.items.every(pureSource);
    case 'call':
      if (!Object.prototype.hasOwnProperty.call(BUILTIN_MANIFEST, node.name) || node.name === 'ABORT') return false;
      return node.args.every(pureSource);
    default: return false;
  }
}

// The conjuncts a join may test before it joins, in stage order, and where
// the walk stopped. One whose fields are all owned by the left rows is
// applied to them; one that reads only through this join's right binder
// (RIGHTHERE, `_["products"]["is_active"]`) is applied to the right rows;
// one that reads a field of some right side -- this join's or one above --
// ends the walk, since AND short-circuits left to right and a later
// conjunct may not run before it, UNLESS it is total here (it cannot raise
// on any joined row), in which case it is passed over for the join above;
// one that reads anything but fields ends the walk too. A deferral relies on
// no lower relation carrying the field; the join that has those rows repeats
// the walk with them, so a shadowed deferral stops the walk there. Each
// stage is judged against the joins between its FILTER and this join
// (`stage.above` of them, the nearest first): a FILTER in the middle of a
// chain reads rows no join above it has touched.
function stageWalk(stages, ownedHere, totalHere, rightHere = null) {
  const applied = [];
  for (let si = 0; si < stages.length; si++) {
    const conjuncts = stages[si].conjuncts;
    for (let ci = 0; ci < conjuncts.length; ci++) {
      const c = conjuncts[ci];
      const stage = stages[si];
      if (c.fields !== null && ownedHere(c.fields, stage)) { applied.push({ c, stage, right: false }); continue; }
      if (c.fields !== null && rightHere !== null && rightHere(c.fields, stage)) {
        applied.push({ c, stage, right: true });
        continue;
      }
      if (c.total !== null && totalHere(c.total, stage)) continue;
      return { applied, stop: [si, ci] };
    }
  }
  return { applied, stop: null };
}

function truncateStages(stages, stop) {
  if (stop === null) return stages;
  const [si, ci] = stop;
  const out = stages.slice(0, si);
  if (ci) out.push({ ...stages[si], conjuncts: stages[si].conjuncts.slice(0, ci) });
  return out;
}

// NODE with every `r["orders"]` -- a read through the left binder's own name
// (NAMES, upper-cased) on the element BINDER -- replaced by the element: on
// the left rows themselves the joined row's member of that name is the row.
function readSelf(node, names, binder) {
  if (!node) return null;
  if (node.t === 'index' && node.obj && node.obj.t === 'var' && node.obj.name === binder
      && node.idx && node.idx.t === 'text' && names.has(node.idx.v.toUpperCase())) {
    return { t: 'var', name: binder, pos: node.pos };
  }
  const copy = { ...node };
  delete copy.mathPlan;          // a compiled plan of the original reads the original
  delete copy.recordShape;
  for (const slot of ['l', 'r', 'x', 'obj', 'idx', 'target', 'value']) {
    if (node[slot]) copy[slot] = readSelf(node[slot], names, binder);
  }
  if (node.args) copy.args = node.args.map((a) => readSelf(a, names, binder));
  if (node.items) copy.items = node.items.map((a) => readSelf(a, names, binder));
  return copy;
}

function firstKeys(value) {
  const first = firstCollectionItem(value);
  return new Set(first ? first.keys().map((k) => k.toUpperCase()) : []);
}

// The upper-cased keys of every row of a side. Rows of a dense list mostly
// share one record shape, and a shape's keys are read once.
function rowKeys(value, bound = []) {
  const keys = new Set(bound.map((k) => k.toUpperCase()));
  const shapes = new Set();
  forEachCollectionItem(value, (row) => {
    if (row.shape) {
      if (shapes.has(row.shape)) return;
      shapes.add(row.shape);
      for (const k of row.shape.keys) keys.add(k.toUpperCase());
    } else {
      for (const k of row.keys()) keys.add(k.toUpperCase());
    }
  });
  return keys;
}

// Whether every row of VALUE carries the field NAME (as written) as text (a
// number is text), or, for kind NUM, as a number; for ANY, as any non-null
// scalar (what a promoted join key needs); for PRESENT, as any value at all.
function rowFact(value, name, kind) {
  let ok = true;
  let rows = 0;
  forEachCollectionItem(value, (row) => {
    rows++;
    if (!ok) return;
    const v = row.get(name);
    if (kind === 'PRESENT') { if (!v) ok = false; return; }
    if (kind === 'ANY') { if (!v || v.isNull() || isNestedRecord(v)) ok = false; return; }
    if (!v || v.kind !== TEXT) { ok = false; return; }
    if (kind === 'NUM') {
      try { v.asDecimal(); } catch (e) { if (e instanceof SelError) ok = false; else throw e; }
    }
  });
  return ok && (kind !== 'PRESENT' || rows > 0);
}

// What the totality check knows about one side's rows: the union of its
// keys, the keys of its first row (a field every row carries is on the first
// one: a cheap refusal before a scan), per-field presence and kind on demand,
// and whether its rows may be null-extended (the right side of a LINK_LEFT).
class SideFacts {
  constructor(value, keys, nullable, names = []) {
    this.value = value;
    this.keys = keys;
    this.first = firstKeys(value);
    this.nullable = nullable;
    // The member names the side's row is bound under in a joined row -- the
    // binder and its lower-case alias, never a positional `_1`/`_2`, which
    // later joins rebind.
    this.names = new Set();
    for (const b of names) {
      if (!isPositionalBinder(b)) { this.names.add(b); this.names.add(b.toLowerCase()); }
    }
    this.facts = new Map();
  }

  // The field NAME is a key of every row, whatever its value: reading it
  // through the side's member cannot raise.
  present(name) {
    const id = `PRESENT:${name}`;
    let fact = this.facts.get(id);
    if (fact === undefined) {
      fact = rowFact(this.value, name, 'PRESENT');
      this.facts.set(id, fact);
    }
    return fact;
  }

  total(name, kind) {
    if (!this.first.has(name.toUpperCase()) || this.nullable) return false;
    const id = `${kind}:${name}`;
    let fact = this.facts.get(id);
    if (fact === undefined) {
      fact = rowFact(this.value, name, kind);
      this.facts.set(id, fact);
    }
    return fact;
  }
}

// Whether every handed-down join key -- the left key of each join above this
// one that handed its conjuncts down -- cannot raise on a joined row built
// from a left row dropped here. As written those joins compute the key for
// every row they receive; a row dropped below never reaches them, so an
// E_NO_KEY there would be lost. Canonical keys never raise, only the reads
// do, so presence suffices: `r["m"]["f"]` needs f on every row of m's
// relation; `r["f"]` needs f carried, non-null, by every row of the one side
// below the join that has it. Anything else is not proved.
function keysSafe(obligations, left, right, above, leftNames) {
  for (const { key, rowNames, outer } of obligations) {
    const below = above.slice(0, above.length - outer);
    if (!key || key.t !== 'index' || !key.idx || key.idx.t !== 'text' || !key.obj) return false;
    const field = key.idx.v;
    const obj = key.obj;
    if (obj.t === 'index' && obj.obj && obj.obj.t === 'var' && rowNames.has(obj.obj.name)
        && obj.idx && obj.idx.t === 'text') {
      const member = obj.idx.v;
      if (leftNames.has(member)) { if (!left.present(field)) return false; continue; }
      const side = [right, ...below].find((s) => s.names.has(member));
      if (side) { if (!side.present(field)) return false; continue; }
      let ok = true;
      forEachCollectionItem(left.value, (row) => {
        if (!ok) return;
        const inner = row.get(member);
        if (!inner || !inner.get(field)) ok = false;
      });
      if (!ok) return false;
      continue;
    }
    if (obj.t === 'var' && rowNames.has(obj.name)) {
      if (!totality([[field, 'ANY']], left, right, below)) return false;
      continue;
    }
    return false;
  }
  return true;
}

// Whether every [field, kind] requirement is met over the joined rows of this
// join: exactly one side -- the left rows, this right side, or a right side
// above -- carries the field at all, and that side carries it on every row
// with the kind. A field two sides carry is promoted from neither (spec
// §7.4) and the read would raise; a field of no side would too.
function totality(reqs, left, right, above) {
  for (const [name, kind] of reqs) {
    const key = name.toUpperCase();
    const owners = [left, right, ...above].filter((side) => side !== null && side.keys.has(key));
    if (owners.length !== 1 || !owners[0].total(name, kind)) return false;
  }
  return true;
}

function doLink(args, ctx, leftJoin) {
  // Taken before anything else is evaluated, so a LINK nested in this one's
  // sources cannot pick it up by accident; it is handed down on purpose below.
  const prefilter = ctx.joinPrefilter;
  ctx.joinPrefilter = null;
  const count = args.count();
  if (count !== 3 && count !== 5) {
    fail('E_ARITY', `${args.name} takes 3 or 5 arguments, got ${count}`, args.pos);
  }
  // With conjuncts to pre-apply and a left source that is itself a join, the
  // right source is evaluated first -- unobservable when both sources are
  // pure -- so that the conjuncts still askable of the rows below can travel
  // down to the join below, and from there to the base rows, where dropping
  // a row saves every join above it.
  let rightSide = null;
  const leftNode = args.node(0);
  const rightNode = args.node(1);
  // The keys a side contributes to the joined row include the names its row
  // is bound under: `_["products"]` after LINK(PRODUCTS, ...) is the right
  // row, not a field of the left ones.
  const b2Names = count === 5 ? [args.symbol(3), '_2'] : [singleRelationName(rightNode) || '_2', '_2'];
  const b1Names = count === 5 ? [args.symbol(2), '_1'] : [singleRelationName(leftNode) || '_1', '_1'];
  const stages = prefilter ? prefilter.stages : [];
  const deep = prefilter ? prefilter.deep : false;
  const above = prefilter ? prefilter.above : [];
  const obligations = prefilter ? prefilter.obligations : [];
  const jb1 = count === 5 ? args.symbol(2) : (singleRelationName(args.node(0)) || '_1');
  const jb2 = count === 5 ? args.symbol(3) : (singleRelationName(args.node(1)) || '_2');
  const jequi = tryExtractEquiKeys(args.node(count === 5 ? 4 : 2), jb1, jb2);
  // The upper-cased keys of the joins between a stage's FILTER and this
  // join, per count of them.
  const aboveKeysCache = new Map();
  const aboveKeys = (stage) => {
    let keys = aboveKeysCache.get(stage.above);
    if (!keys) {
      keys = new Set();
      for (const side of above.slice(0, stage.above)) for (const k of side.keys) keys.add(k);
      aboveKeysCache.set(stage.above, keys);
    }
    return keys;
  };
  let leftValue;
  let rightValue;
  if (deep && stages.length && jequi && leftNode && leftNode.t === 'call'
      && (leftNode.name === 'LINK' || leftNode.name === 'LINK_LEFT' || leftNode.name === 'FILTER')
      && pureSource(leftNode) && pureSource(rightNode)) {
    rightValue = args.val(1);
    rightSide = new SideFacts(rightValue, rowKeys(rightValue, b2Names), leftJoin, b2Names);
    // With the left rows unknown: a field no right side (here or above)
    // carries is theirs; a conjunct on a right side's field is passed over
    // only when total on that side alone. The join below repeats the walk
    // with its own sides, and this one again once its left rows are known.
    const ownedBelow = (fields, stage) => {
      const upper = aboveKeys(stage);
      for (const f of fields) if (rightSide.keys.has(f) || upper.has(f)) return false;
      return true;
    };
    const totalBelow = (reqs, stage) => totality(reqs, null, rightSide, above.slice(0, stage.above));
    const walk = stageWalk(stages, ownedBelow, totalBelow);
    // Below this join, every stage has one more join above it: this one.
    const handed = truncateStages(stages, walk.stop).map((stage) => ({ ...stage, above: stage.above + 1 }));
    if (handed.length) {
      // This join computes its left key on every row it receives; a row
      // dropped below never arrives, so the key goes down as an obligation
      // for the join that drops to prove (keysSafe).
      const ownKey = { key: jequi.left, rowNames: new Set([jb1, jb1.toLowerCase(), '_1', '_']), outer: above.length + 1 };
      ctx.joinPrefilter = { stages: handed, deep: true, above: [rightSide, ...above],
        obligations: [ownKey, ...obligations] };
    }
    try {
      leftValue = args.val(0);
    } finally {
      ctx.joinPrefilter = null;
    }
  } else {
    leftValue = args.val(0);
    rightValue = args.val(1);
  }
  // The join below, if it applied some of these conjuncts, says which ones
  // every row that came up has passed; those are skipped here unless a row
  // was kept on an error below, since such a row must reach the FILTER
  // untouched and cannot be told apart from the others.
  let below = ctx.joinPrefilterReport;
  ctx.joinPrefilterReport = null;
  // Drops below that left this join no left rows: as written it may have had
  // some, and then it computes every right key (and raises where one cannot
  // be) before it finds that no row survives. Only the rows as written can
  // say, so the left side -- pure, or nothing was handed down -- is
  // evaluated again without them, and this join runs as written.
  if (below !== null && below.dropped && firstCollectionItem(leftValue) === null) {
    leftValue = args.evalNode(args.node(0));
    ctx.joinPrefilterReport = null;
    below = null;
  }
  const appliedBelow = below !== null && !below.errored ? below.applied : new Set();
  let dropped = below !== null && below.dropped;
  let b1 = '_1';
  let b2 = '_2';
  let predicate;
  if (count === 3) {
    b1 = singleRelationName(args.node(0)) || b1;
    b2 = singleRelationName(args.node(1)) || b2;
    predicate = args.node(2);
  } else {
    b1 = args.symbol(2);
    b2 = args.symbol(3);
    predicate = args.node(4);
  }
  if (leftValue.isNull()) return Value.list([]);

  const firstLeft = firstCollectionItem(leftValue);
  const firstRight = firstCollectionItem(rightValue);
  if (!firstLeft || !firstRight) {
    if (!leftJoin || !firstLeft) return Value.list([]);
  }
  // Every row is built from its own pair (spec §7.4): nothing is decided from
  // a first element except the shape of LINK_LEFT's null record.
  const sampleRight = firstRight ? ensureRowTableAlias(firstRight, b2) : null;
  let nullRight = leftJoin ? makeNullRecord(sampleRight, b2) : null;
  if (nullRight && nullRight.isNull()) nullRight = null;
  const project = makeJoinProjector(b1, b2, nullRight);
  const equi = tryExtractEquiKeys(predicate, b1, b2);
  const output = [];
  const each = forEachCollectionItem;

  // The pre-filter, decided from the rows themselves (see stageWalk). On a
  // left row a conjunct evaluates FALSE the row is dropped -- the joined rows
  // it would have produced (or its null-extended row, for LINK_LEFT) would
  // all have been dropped by the same conjunct; likewise a right row, whose
  // joined rows are then not built. On an error the row is KEPT: the full
  // predicate runs over the joined rows afterwards and raises there, in row
  // order, or does not raise at all for a row that joins nothing.
  const prefix = [];
  const rightPrefix = [];
  // How many left conjuncts come before the first right one: with a right
  // row kept on an error, a later left conjunct may not drop a left row --
  // the joined row would have raised in the right conjunct first.
  let leftBeforeRight = -1;
  let binders = [];
  const appliedIds = new Set();
  let errored = false;
  const selfNames = new Set([b1.toUpperCase(), '_1']);
  // A read through this join's right binder is the right element in every
  // joined row -- the binder is bound last (spec §7.4) -- unless the left
  // binder has the same name, or a join above rebinds it.
  const rightNames = (stage) => new Set(stage.above === 0 ? [b2.toUpperCase(), '_2'] : [b2.toUpperCase()]);
  const rightHere = (!leftJoin && b1.toUpperCase() !== b2.toUpperCase())
    ? (fields, stage) => {
      const names = rightNames(stage);
      const upper = aboveKeys(stage);
      for (const f of fields) if (!names.has(f) || upper.has(f)) return false;
      return true;
    }
    : null;
  const settlePrefix = () => {
    // With both sides at hand: a field no right side carries is the left
    // rows' (a read through the left binder's own name, `_["orders"]["year"]`
    // on ORDERS rows, is the row itself, spec §7.4); a right side's field may
    // be passed over only when the conjunct is total over this join's rows.
    const leftSide = new SideFacts(leftValue, rowKeys(leftValue, b1Names), false, b1Names);
    if (obligations.length && !keysSafe(obligations, leftSide, rightSide, above, leftSide.names)) return;
    // A joined row carries a left element's field exactly as the element
    // does whenever no right element has the name (§7.4, pair by pair) --
    // and no row depends on another, so a drop below changes nothing above
    // but the rows it drops.
    const ownedHere = (fields, stage) => {
      const upper = aboveKeys(stage);
      for (const f of fields) if (rightSide.keys.has(f) || upper.has(f)) return false;
      return true;
    };
    const totalHere = (reqs, stage) => totality(reqs, leftSide, rightSide, above.slice(0, stage.above));
    for (const { c, stage, right } of stageWalk(stages, ownedHere, totalHere, rightHere).applied) {
      appliedIds.add(c.node);
      if (appliedBelow.has(c.node)) continue;
      if (right) {
        if (leftBeforeRight < 0) leftBeforeRight = prefix.length;
        rightPrefix.push(readSelf(c.node, rightNames(stage), c.binder));
        continue;
      }
      let node = c.node;
      let readsSelf = false;
      for (const f of c.fields) if (selfNames.has(f) && !leftSide.first.has(f)) readsSelf = true;
      if (readsSelf) node = readSelf(node, selfNames, c.binder);
      prefix.push(node);
    }
  };
  // 0: keep the row; 1: drop it; 2: keep it, a conjunct raised on it.
  const verdict = (conjuncts, row, frame) => {
    for (const binder of binders) frame.set(binder, row);
    for (const conjunct of conjuncts) {
      let keep;
      try {
        keep = args.evalNode(conjunct).asBool(conjunct.pos);
      } catch (e) {
        if (!(e instanceof SelError)) throw e;
        errored = true;
        return 2;
      }
      if (!keep) return 1;
    }
    return 0;
  };

  if (equi && sampleRight) {
    const buckets = new Map();
    const gather = prefilter && rightSide === null ? { keys: new Set(), shapes: new Set() } : null;
    const frameRight = new Map([[b2, null], [b2.toLowerCase(), null], ['_2', null]]);
    // Read in order here, where they are close together, rather than
    // scattered pair by pair in the projector.
    const binderNames = new Set([...binderKeys(b1, '_1'), ...binderKeys(b2, '_2')]);
    let rightFlat = true;
    ctx.pushFrame(frameRight);
    try {
      each(rightValue, (item) => {
        const row = ensureRowTableAlias(item, b2);
        if (rightFlat && !flatRow(row, binderNames)) rightFlat = false;
        frameRight.set(b2, row);
        frameRight.set(b2.toLowerCase(), row);
        frameRight.set('_2', row);
        const key = canonicalJoinKey(args.evalNode(equi.right), equi.numeric);
        if (key !== null) {
          const bucket = buckets.get(key) || [];
          bucket.push(row);
          buckets.set(key, bucket);
        }
        if (gather !== null) {
          if (row.shape) {
            if (!gather.shapes.has(row.shape)) {
              gather.shapes.add(row.shape);
              for (const k of row.shape.keys) gather.keys.add(k.toUpperCase());
            }
          } else {
            for (const k of row.keys()) gather.keys.add(k.toUpperCase());
          }
        }
      });
    } finally {
      ctx.popFrame();
    }
    project.rightFlat = rightFlat;
    if (prefilter) {
      if (gather !== null) {
        for (const k of b2Names) gather.keys.add(k.toUpperCase());
        rightSide = new SideFacts(rightValue, gather.keys, leftJoin, b2Names);
      }
      binders = stages.map((stage) => stage.binder);
      settlePrefix();
    }
    // The right rows the right conjuncts reject, once each, after every
    // right key was computed. They stay in their buckets: a left row still
    // counts them towards the numbering, and one kept on an error joins them.
    let rejected = null;
    if (rightPrefix.length) {
      rejected = new Set();
      const frame = new Map();
      for (const binder of binders) frame.set(binder, null);
      const before = errored;
      errored = false;
      ctx.pushFrame(frame);
      try {
        for (const bucket of buckets.values()) {
          for (const row of bucket) if (verdict(rightPrefix, row, frame) === 1) rejected.add(row);
        }
      } finally {
        ctx.popFrame();
      }
      if (errored) prefix.length = leftBeforeRight;
      errored = errored || before;
    }

    // A FILTER keeps its input's keys, so the rows dropped here still count
    // towards the numbering of the rows kept: the join key is computed first,
    // as it is for every row (and raises where it would have), the matches
    // say how many joined rows the dropped row stood for, and the kept rows
    // are emitted under the positions they would have had.
    const numbered = (prefix.length || rejected !== null) && !deep;
    const keyed = numbered ? new Value(NONE, null, true) : null;
    let position = 1;
    // When the join key is a literal field of the row -- `_1["customer_id"]`
    // -- the read can only raise for a row that lacks the field, so a row
    // that HAS it may be rejected first and its key never computed. Only
    // where nothing observes the numbering.
    let fastField = null;
    if (prefix.length && deep && equi.left.t === 'index' && equi.left.obj && equi.left.obj.t === 'var'
        && equi.left.idx && equi.left.idx.t === 'text'
        && [b1.toUpperCase(), '_1', '_'].includes(equi.left.obj.name.toUpperCase())) {
      fastField = equi.left.idx.v;
    }
    const frameLeft = new Map([[b1, null], [b1.toLowerCase(), null], ['_1', null], ['_', null]]);
    for (const binder of binders) if (!frameLeft.has(binder)) frameLeft.set(binder, null);
    ctx.pushFrame(frameLeft);
    try {
      each(leftValue, (item) => {
        const row = ensureRowTableAlias(item, b1);
        let asked = -1;
        if (fastField !== null && row.get(fastField)) {
          asked = verdict(prefix, row, frameLeft);
          if (asked === 1) { dropped = true; return; }
        }
        frameLeft.set(b1, row);
        frameLeft.set(b1.toLowerCase(), row);
        frameLeft.set('_1', row);
        frameLeft.set('_', row);
        const key = canonicalJoinKey(args.evalNode(equi.left), equi.numeric);
        const matches = key === null ? null : buckets.get(key);
        if (asked < 0) asked = prefix.length ? verdict(prefix, row, frameLeft) : 0;
        if (asked === 1) {
          dropped = true;
          if (numbered) position += matches ? matches.length : (leftJoin ? 1 : 0);
          return;
        }
        if (matches) {
          // A left row kept on an error meets every right row: its joined
          // rows raise in the FILTER, in order, where they would have.
          const skip = rejected !== null && asked === 0 ? rejected : null;
          for (let i = 0; i < matches.length; i++) {
            if (skip === null || !skip.has(matches[i])) {
              const joined = project(row, matches[i]);
              if (keyed !== null) keyed.set(String(position), joined); else output.push(joined);
            } else {
              dropped = true;
            }
            position++;
          }
        } else if (leftJoin) {
          const joined = project(row, null);
          if (keyed !== null) keyed.set(String(position), joined); else output.push(joined);
          position++;
        }
      });
    } finally {
      ctx.popFrame();
    }
    if (prefilter) ctx.joinPrefilterReport = { applied: appliedIds, errored, dropped };
    if (keyed !== null) return keyed;
  } else {
    const frame = new Map([
      [b1, null], [b1.toLowerCase(), null], ['_1', null], ['_', null],
      [b2, null], [b2.toLowerCase(), null], ['_2', null],
    ]);
    ctx.pushFrame(frame);
    try {
      each(leftValue, (leftItem) => {
        const left = ensureRowTableAlias(leftItem, b1);
        frame.set(b1, left);
        frame.set(b1.toLowerCase(), left);
        frame.set('_1', left);
        frame.set('_', left);
        let matched = false;
        if (sampleRight) each(rightValue, (rightItem) => {
          const right = ensureRowTableAlias(rightItem, b2);
          frame.set(b2, right);
          frame.set(b2.toLowerCase(), right);
          frame.set('_2', right);
          if (args.evalNode(predicate).asBool(predicate.pos)) {
            matched = true;
            output.push(project(left, right));
          }
        });
        if (leftJoin && !matched) output.push(project(left, null));
      });
    } finally {
      ctx.popFrame();
    }
  }
  return Value.listOwned(output);
}

// Three or five arguments, refused at compile time like every E_ARITY (spec
// §7.4); the rule is spec/builtins.json's and the registry installs it.
define({ name: 'LINK', min: 3, max: 5, lazy: true, binds: true,
  fn: (args, ctx) => doLink(args, ctx, false) });
define({ name: 'LINK_LEFT', min: 3, max: 5, lazy: true, binds: true,
  fn: (args, ctx) => doLink(args, ctx, true) });
