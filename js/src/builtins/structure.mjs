import { Value, NONE, structuralHash } from '../value.mjs';
import * as D from '../decimal.mjs';
import { define } from '../registry.mjs';
import { fail } from '../errors.mjs';

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

function recordFromArgs(args) {
  const entries = [];
  for (let i = 0; i < args.count(); i += 2) {
    entries.push([args.text(i), args.val(i + 1).clone()]);
  }
  return Value.fromEntries(entries);
}

define({
  name: 'RECORD', min: 0, max: Infinity,
  arityError: (count) => count % 2 !== 0
    ? `RECORD takes an even number of arguments (key-value pairs), got ${count}` : null,
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
    try { return D.format(value.asDecimal()); } catch (_) { return null; }
  }
  return value.kind === 'TEXT' ? value.scalar : null;
}

function ensureRowTableAlias(row, tableName) {
  if (!tableName || tableName === '_1' || row.has(tableName)) return row;
  const lower = tableName.toLowerCase();
  if (row.shape) {
    const oldShape = row.shape;
    let cached = oldShape.aliasCache.get(tableName);
    if (!cached) {
      const addLower = lower !== tableName && !oldShape.keyMap.has(lower);
      const keys = [...oldShape.keys, tableName];
      if (addLower) keys.push(lower);
      cached = { keys, oldSize: oldShape.size, addLower };
      oldShape.aliasCache.set(tableName, cached);
    }
    const keys = cached.keys;
    const storage = row.storage.slice(0, cached.oldSize);
    storage.push(row);
    if (cached.addLower) storage.push(row);
    return Value.shapedOwned(keys, storage);
  }
  const entries = row.entries();
  entries.push([tableName, row]);
  if (lower !== tableName && !row.has(lower)) entries.push([lower, row]);
  return Value.fromEntriesPreserveDuplicates(entries);
}

function makeNullRecord(sample, tableName) {
  const entries = [];
  if (sample) for (const key of sample.keys()) entries.push([key, Value.none()]);
  if (tableName) {
    entries.push([tableName, Value.none()]);
    const lower = tableName.toLowerCase();
    if (lower !== tableName) entries.push([lower, Value.none()]);
  }
  return Value.fromEntries(entries);
}

function isNestedRecord(value) {
  return value.size() > 0 && !value.isList;
}

function makeJoinedRow(left, right, b1, b2, promotedLeft, promotedRight, tableLeft, nullRight) {
  // Each key once, where it first occurred (spec §7.4): a carried or promoted
  // key keeps its first value, a binder key holds the row this LINK bound
  // even where an earlier LINK's `_1` or a relation joined twice carried a
  // record of the same name. That is the row the compiled projector below
  // builds from the shape (binders first, then slots); PHP, Python and C++
  // agreed with it on that path, while JS and Lisp built `X` twice.
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

  // Carry nested table records from earlier links so chained joins retain the
  // relation-qualified access path (orders -> customers -> items).
  for (const [key, value] of left.entries()) {
    if (isNestedRecord(value)) put(key, value);
  }

  const low1 = b1.toLowerCase();
  bind(b1, left);
  if (low1 !== b1) bind(low1, left);
  if (b1 !== '_1') bind('_1', left);

  const actualRight = right || nullRight;
  const nullOrRight = actualRight || Value.none();
  const low2 = b2.toLowerCase();
  bind(b2, nullOrRight);
  if (low2 !== b2) bind(low2, nullOrRight);
  if (b2 !== '_2') bind('_2', nullOrRight);

  for (const key of promotedLeft) {
    const value = left.get(key);
    if (value !== undefined) put(key, value);
  }
  if (right) {
    for (const key of promotedRight) {
      const value = right.get(key);
      if (value !== undefined && !value.isNull()) put(key, value);
    }
  }
  return Value.fromEntries(entries);
}

const JOIN_LEFT = 0;
const JOIN_RIGHT = 1;
const JOIN_LEFT_SLOT = 2;
const JOIN_RIGHT_SLOT = 3;
const JOIN_LEFT_GET = 4;
const JOIN_RIGHT_GET = 5;
const JOIN_NONE = 6;

function compileJoinProjector(outputShape, leftShape, rightShape,
  b1, b2, promotedLeft, promotedRight, tableLeft) {
  const n = outputShape.size;
  const kinds = new Uint8Array(n);
  const slots = new Int32Array(n);
  const keys = new Array(n);
  const leftAliases = new Set([b1, b1.toLowerCase(), '_1']);
  const rightAliases = new Set([b2, b2.toLowerCase(), '_2']);
  const leftColumns = new Set([...tableLeft, ...promotedLeft]);
  const rightColumns = new Set(promotedRight);

  for (let i = 0; i < n; i++) {
    const key = outputShape.keys[i];
    if (leftAliases.has(key)) {
      kinds[i] = JOIN_LEFT;
    } else if (rightAliases.has(key)) {
      kinds[i] = JOIN_RIGHT;
    } else if (leftColumns.has(key)) {
      const slot = leftShape.keyMap.get(key);
      if (slot === undefined) {
        kinds[i] = JOIN_LEFT_GET;
        keys[i] = key;
      } else {
        kinds[i] = JOIN_LEFT_SLOT;
        slots[i] = slot;
      }
    } else if (rightColumns.has(key)) {
      const slot = rightShape.keyMap.get(key);
      if (slot === undefined) {
        kinds[i] = JOIN_RIGHT_GET;
        keys[i] = key;
      } else {
        kinds[i] = JOIN_RIGHT_SLOT;
        slots[i] = slot;
      }
    } else {
      kinds[i] = JOIN_NONE;
    }
  }

  return (left, right) => {
    const storage = new Array(n);
    for (let i = 0; i < n; i++) {
      switch (kinds[i]) {
        case JOIN_LEFT: storage[i] = left; break;
        case JOIN_RIGHT: storage[i] = right; break;
        case JOIN_LEFT_SLOT: storage[i] = left.storage[slots[i]]; break;
        case JOIN_RIGHT_SLOT: storage[i] = right.storage[slots[i]]; break;
        case JOIN_LEFT_GET: storage[i] = left.get(keys[i]) || Value.none(); break;
        case JOIN_RIGHT_GET: storage[i] = right.get(keys[i]) || Value.none(); break;
        default: storage[i] = Value.none(); break;
      }
    }
    return Value.shapedFromShape(outputShape, storage);
  };
}

function makeJoinProjector(b1, b2, promotedLeft, promotedRight, tableLeft, nullRight) {
  // A separate projector is cached for each pair of physical row shapes. The
  // common homogeneous case compiles once on its first actual match; rows with
  // a different schema retain the general semantic path instead of risking a
  // stale slot offset.
  const byLeftShape = new WeakMap();
  return (left, right) => {
    if (!right || !left.shape || !right.shape) {
      return makeJoinedRow(left, right, b1, b2, promotedLeft, promotedRight, tableLeft, nullRight);
    }
    let byRightShape = byLeftShape.get(left.shape);
    if (!byRightShape) {
      byRightShape = new WeakMap();
      byLeftShape.set(left.shape, byRightShape);
    }
    let projector = byRightShape.get(right.shape);
    if (!projector) {
      // Build one row only to determine the immutable output shape. This is
      // done on the first matched pair, then every subsequent pair copies
      // slots directly into its own packed destination array.
      const first = makeJoinedRow(left, right, b1, b2, promotedLeft, promotedRight, tableLeft, nullRight);
      if (!first.shape) return first;
      projector = compileJoinProjector(first.shape, left.shape, right.shape,
        b1, b2, promotedLeft, promotedRight, tableLeft);
      byRightShape.set(right.shape, projector);
      return first;
    }
    return projector(left, right);
  };
}

function doLink(args, ctx, leftJoin) {
  const count = args.count();
  if (count !== 3 && count !== 5) {
    fail('E_ARITY', `${args.name} takes 3 or 5 arguments, got ${count}`, args.pos);
  }
  const leftValue = args.val(0);
  const rightValue = args.val(1);
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
  const sampleLeft = firstLeft ? ensureRowTableAlias(firstLeft, b1) : null;
  const sampleRight = firstRight ? ensureRowTableAlias(firstRight, b2) : null;
  const nullRight = leftJoin ? makeNullRecord(sampleRight, b2) : null;
  const leftKeys = sampleLeft ? sampleLeft.keys() : [];
  const rightKeys = sampleRight ? sampleRight.keys() : [];
  // Join promotion treats column names case-insensitively, like the Lisp
  // reference's STRING-EQUAL conflict check, while ordinary Value indexing
  // remains case-sensitive.
  const rightKeySet = new Set(rightKeys.map((key) => key.toUpperCase()));
  const leftKeySet = new Set(leftKeys.map((key) => key.toUpperCase()));
  const promotedLeft = sampleLeft ? leftKeys.filter((key) => {
    const value = sampleLeft.get(key);
    return !isNestedRecord(value) && !rightKeySet.has(key.toUpperCase());
  }) : [];
  const promotedRight = sampleRight ? rightKeys.filter((key) => {
    const value = sampleRight.get(key);
    return !isNestedRecord(value) && !leftKeySet.has(key.toUpperCase());
  }) : [];
  const tableLeft = sampleLeft ? leftKeys.filter((key) => isNestedRecord(sampleLeft.get(key))) : [];
  const project = makeJoinProjector(b1, b2, promotedLeft, promotedRight, tableLeft, nullRight);
  const equi = tryExtractEquiKeys(predicate, b1, b2);
  const output = [];
  const each = forEachCollectionItem;

  if (equi && sampleRight) {
    const buckets = new Map();
    const frameRight = new Map([[b2, null], [b2.toLowerCase(), null], ['_2', null]]);
    ctx.pushFrame(frameRight);
    try {
      each(rightValue, (item) => {
        const row = ensureRowTableAlias(item, b2);
        frameRight.set(b2, row);
        frameRight.set(b2.toLowerCase(), row);
        frameRight.set('_2', row);
        const key = canonicalJoinKey(args.evalNode(equi.right), equi.numeric);
        if (key !== null) {
          const bucket = buckets.get(key) || [];
          bucket.push(row);
          buckets.set(key, bucket);
        }
      });
    } finally {
      ctx.popFrame();
    }

    const frameLeft = new Map([[b1, null], [b1.toLowerCase(), null], ['_1', null], ['_', null]]);
    ctx.pushFrame(frameLeft);
    try {
      each(leftValue, (item) => {
        const row = ensureRowTableAlias(item, b1);
        frameLeft.set(b1, row);
        frameLeft.set(b1.toLowerCase(), row);
        frameLeft.set('_1', row);
        frameLeft.set('_', row);
        const key = canonicalJoinKey(args.evalNode(equi.left), equi.numeric);
        const matches = key === null ? null : buckets.get(key);
        if (matches) {
          for (let i = 0; i < matches.length; i++) output.push(project(row, matches[i]));
        } else if (leftJoin) {
          output.push(project(row, null));
        }
      });
    } finally {
      ctx.popFrame();
    }
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

define({ name: 'LINK', min: 3, max: 5, lazy: true, binds: true,
  fn: (args, ctx) => doLink(args, ctx, false) });
define({ name: 'LINK_LEFT', min: 3, max: 5, lazy: true, binds: true,
  fn: (args, ctx) => doLink(args, ctx, true) });
