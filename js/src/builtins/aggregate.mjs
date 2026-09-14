// Aggregates. These are why SEL needs no loop: each evaluates one argument node
// once per element, which is the same move IF makes, repeated.

import * as D from '../decimal.mjs';
import { Value, NONE, structuralHash } from '../value.mjs';
import { define } from '../registry.mjs';
import { bytesCompare } from '../utf8.mjs';
import { fail } from '../errors.mjs';

// Two-argument form binds `_`; three-argument form takes a bare identifier as
// the binder, checked by inspecting the AST node the caller handed us.
function shape(args) {
  return args.count() === 3
    ? { binder: args.symbol(1), body: args.node(2) }
    : { binder: '_', body: args.node(1) };
}

// A scalar with no children behaves as a one-element list containing itself,
// consistent with scalar context (§3.2). A NONE with no children is genuinely
// empty — that is what FILTER returns when nothing matched, and ALL over it must
// be TRUE rather than a scalar-context failure.
function elements(value) {
  value.force();
  if (value.size() > 0) return value.entries();
  return value.kind === NONE ? [] : [['1', value]];
}

function nodeContainsVar(node, name) {
  if (!node) return false;
  switch (node.t) {
    case 'var': return node.name.toUpperCase() === name.toUpperCase();
    case 'index': return nodeContainsVar(node.obj, name) || nodeContainsVar(node.idx, name);
    case 'call': return node.args.some((item) => nodeContainsVar(item, name));
    case 'bin': return nodeContainsVar(node.l, name) || nodeContainsVar(node.r, name);
    case 'un': return nodeContainsVar(node.x, name);
    case 'assign': return nodeContainsVar(node.target, name)
      || nodeContainsVar(node.value, name);
    case 'seq': case 'list': return node.items.some((item) => nodeContainsVar(item, name));
    default: return false;
  }
}

// Runs `visit` per element with the binder and _K in scope. Returning a value
// from `visit` stops the walk and becomes the result.
function walk(args, ctx, visit) {
  const { binder, body } = shape(args);
  const collection = args.val(0);
  const frame = new Map([[binder, null]]);
  const needsK = nodeContainsVar(body, '_K');
  if (needsK) frame.set('_K', null);
  ctx.pushFrame(frame);
  try {
    const visitItem = (key, item) => {
      frame.set(binder, item);
      if (needsK) frame.set('_K', Value.text(key));
      return visit(args.evalNode(body), key, item, body);
    };
    // Match Lisp's vector fast paths: read the packed storage directly and
    // reuse the binder frame. `entries()` is intentionally reserved for the
    // host-facing snapshot API because its pair arrays create avoidable GC in
    // MAP/FILTER/SUM over large relations.
    if (collection.storage !== null) {
      if (collection.isList) {
        for (let i = 0; i < collection.storage.length; i++) {
          const result = visitItem(String(i + 1), collection.storage[i].force());
          if (result !== undefined) return result;
        }
      } else {
        const keys = collection.shape.keys;
        for (let i = 0; i < collection.storage.length; i++) {
          const result = visitItem(keys[i], collection.storage[i].force());
          if (result !== undefined) return result;
        }
      }
    } else if (collection.children) {
      for (const [key, item] of collection.children) {
        const result = visitItem(key, item.force());
        if (result !== undefined) return result;
      }
    } else if (collection._entries !== null) {
      for (const [key, item] of collection._entries) {
        const result = visitItem(key, item.force());
        if (result !== undefined) return result;
      }
    } else if (collection.kind !== NONE) {
      const result = visitItem('1', collection);
      if (result !== undefined) return result;
    }
  } finally {
    ctx.popFrame();
  }
  return undefined;
}

define({
  name: 'ALL', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    const short = walk(args, ctx, (r, k, i, body) =>
      r.asBool(body.pos) ? undefined : Value.bool(false));
    return short || Value.bool(true);
  },
});

define({
  name: 'ANY', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    const short = walk(args, ctx, (r, k, i, body) =>
      r.asBool(body.pos) ? Value.bool(true) : undefined);
    return short || Value.bool(false);
  },
});

define({
  name: 'MAP', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    const out = [];
    walk(args, ctx, (r) => { out.push(r); return undefined; });
    return Value.listOwned(out);
  },
});

// The one aggregate that preserves keys — a filtered list should still be
// addressable the way the original was.
define({
  name: 'FILTER', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    const out = new Value(NONE, null, true);
    walk(args, ctx, (r, key, item, body) => {
      if (r.asBool(body.pos)) out.set(key, item);
      return undefined;
    });
    return out;
  },
});

define({
  name: 'SUM', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    let total = D.ZERO;
    walk(args, ctx, (r, k, i, body) => {
      total = D.add(total, r.asDecimal(body.pos), body.pos);
      return undefined;
    });
    return Value.num(total);
  },
});

// Strict, not an aggregate: its second argument is a separator, not a body.
define({
  name: 'JOIN', min: 2, max: 2,
  fn: (args) => {
    const sep = args.text(1);
    const parts = [];
    for (const [, item] of elements(args.val(0))) parts.push(item.asText(args.posOf(0)));
    return Value.text(parts.join(sep));
  },
});

function compareValues(a, b) {
  const aNull = a.isNull();
  const bNull = b.isNull();
  if (aNull && bNull) return 0;
  if (aNull) return -1;
  if (bNull) return 1;

  const aNum = a.looksNumeric();
  const bNum = b.looksNumeric();
  if (aNum && bNum) {
    return D.cmp(a.asDecimal(), b.asDecimal());
  }

  if (a.kind === 'BOOL' && b.kind === 'BOOL') {
    const av = a.scalar ? 1 : 0;
    const bv = b.scalar ? 1 : 0;
    return av - bv;
  }

  if ((a.kind === 'TEXT' || a.kind === 'BIN') && (b.kind === 'TEXT' || b.kind === 'BIN')) {
    return bytesCompare(a.asBytes(), b.asBytes());
  }

  const rank = (v) => {
    if (v.isNull()) return 0;
    if (v.kind === 'BOOL') return 1;
    if (v.looksNumeric()) return 2;
    if (v.kind === 'TEXT') return 3;
    if (v.kind === 'BIN') return 4;
    return 5;
  };
  return rank(a) - rank(b);
}

function doSort(args, ctx, forcedDir) {
  const val = args.val(0);
  if (val.isNull()) return Value.list([]);
  const entries = elements(val);
  if (entries.length === 0) return Value.list([]);

  const count = args.count();
  let dir;
  let indexed;

  if (count === 1) {
    dir = forcedDir || 'ASC';
    indexed = entries.map(([, item], idx) => ({ item, key: item, idx }));
  } else {
    let binder;
    let body;
    if (count === 2) {
      binder = '_';
      body = args.node(1);
      dir = forcedDir || 'ASC';
    } else if (count === 3) {
      if (forcedDir !== null) {
        binder = args.symbol(1);
        body = args.node(2);
        dir = forcedDir;
      } else if (args.node(2).t === 'text') {
        binder = '_';
        body = args.node(1);
        dir = args.text(2).toUpperCase();
      } else if (args.isSymbol(1)) {
        binder = args.symbol(1);
        body = args.node(2);
        dir = 'ASC';
      } else {
        binder = '_';
        body = args.node(1);
        dir = args.text(2).toUpperCase();
      }
    } else {
      binder = args.symbol(1);
      body = args.node(2);
      dir = args.text(3).toUpperCase();
    }

    if (dir !== 'ASC' && dir !== 'DESC') {
      const posIdx = count === 4 ? 3 : 2;
      fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", args.posOf(posIdx));
    }

    indexed = entries.map(([k, item], idx) => {
      const frame = new Map([[binder, item], ['_K', Value.text(k)]]);
      ctx.pushFrame(frame);
      let evalKey;
      try {
        evalKey = args.evalNode(body);
      } finally {
        ctx.popFrame();
      }
      return { item, key: evalKey, idx };
    });
  }

  indexed.sort((x, y) => {
    let c = compareValues(x.key, y.key);
    if (dir === 'DESC') c = -c;
    return c !== 0 ? c : (x.idx - y.idx);
  });

  return Value.listOwned(indexed.map((x) => x.item.clone()));
}

function doTop(args, ctx, forcedDir) {
  const value = args.val(0);
  const limit = args.nonNegInt(args.count() - 1);
  if (limit === 0 || value.isNull()) return Value.list([]);
  value.force();
  if (value.kind === NONE && value.size() === 0) return Value.list([]);

  const sortCount = args.count() - 1;
  let binder = '_';
  let body = null;
  let dir = forcedDir || 'ASC';
  if (sortCount === 1) {
    binder = null;
  } else if (sortCount === 2) {
    body = args.node(1);
  } else if (sortCount === 3) {
    if (forcedDir !== null) {
      binder = args.symbol(1);
      body = args.node(2);
    } else if (args.node(2).t === 'text') {
      body = args.node(1);
      dir = args.text(2).toUpperCase();
    } else if (args.isSymbol(1)) {
      binder = args.symbol(1);
      body = args.node(2);
    } else {
      body = args.node(1);
      dir = args.text(2).toUpperCase();
    }
  } else if (sortCount === 4) {
    binder = args.symbol(1);
    body = args.node(2);
    dir = args.text(3).toUpperCase();
  } else {
    fail('E_ARITY', `${args.name} has an invalid sort form`, args.pos);
  }
  if (dir !== 'ASC' && dir !== 'DESC') {
    const directionIndex = sortCount === 4 ? 3 : 2;
    fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", args.posOf(directionIndex));
  }

  const compare = (a, b) => {
    let c = compareValues(a.key, b.key);
    if (dir === 'DESC') c = -c;
    return c !== 0 ? c : a.idx - b.idx;
  };
  const worse = (a, b) => {
    const c = compare(a, b);
    return c > 0 || (c === 0 && a.idx > b.idx);
  };
  const heap = [];
  const siftUp = (index) => {
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (!worse(heap[index], heap[parent])) break;
      [heap[index], heap[parent]] = [heap[parent], heap[index]];
      index = parent;
    }
  };
  const siftDown = (index) => {
    while (true) {
      const left = index * 2 + 1;
      const right = left + 1;
      let worst = index;
      if (left < heap.length && worse(heap[left], heap[worst])) worst = left;
      if (right < heap.length && worse(heap[right], heap[worst])) worst = right;
      if (worst === index) return;
      [heap[index], heap[worst]] = [heap[worst], heap[index]];
      index = worst;
    }
  };
  const needsK = body !== null && nodeContainsVar(body, '_K');
  let idx = 0;
  const consume = (key, item) => {
    let candidate;
    if (binder === null) {
      candidate = { item, key: item, idx };
    } else {
      const frame = new Map([[binder, item]]);
      if (needsK) frame.set('_K', Value.text(key));
      ctx.pushFrame(frame);
      try {
        candidate = { item, key: args.evalNode(body), idx };
      } finally {
        ctx.popFrame();
      }
    }
    idx += 1;
    if (heap.length < limit) {
      heap.push(candidate);
      siftUp(heap.length - 1);
    } else if (worse(heap[0], candidate)) {
      heap[0] = candidate;
      siftDown(0);
    }
  };
  if (value.isList && value.storage !== null) {
    for (let i = 0; i < value.storage.length; i++) consume(String(i + 1), value.storage[i]);
  } else if (value.shape) {
    for (let i = 0; i < value.storage.length; i++) consume(value.shape.keys[i], value.storage[i]);
  } else {
    for (const [key, item] of elements(value)) consume(key, item);
  }
  heap.sort(compare);
  return Value.listOwned(heap.map((entry) => entry.item));
}

define({
  name: 'SORT', min: 1, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => doSort(args, ctx, 'ASC'),
});

define({
  name: 'SORT_DESC', min: 1, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => doSort(args, ctx, 'DESC'),
});

define({
  name: 'SORT_BY', min: 2, max: 4, lazy: true, binds: true,
  fn: (args, ctx) => doSort(args, ctx, null),
});

define({
  name: 'TOP', min: 2, max: 4, lazy: true, binds: true,
  fn: (args, ctx) => doTop(args, ctx, 'ASC'),
});

define({
  name: 'TOP_DESC', min: 2, max: 4, lazy: true, binds: true,
  fn: (args, ctx) => doTop(args, ctx, 'DESC'),
});

define({
  name: 'TOP_BY', min: 3, max: 5, lazy: true, binds: true,
  fn: (args, ctx) => doTop(args, ctx, null),
});

function doBucket(args, ctx) {
  const value = args.val(0);
  if (value.isNull() || value.size() === 0) return Value.list([]);

  const count = args.count();
  let binder = '_';
  let keyNode;
  let aggregateNode = null;
  if (count === 2) {
    keyNode = args.node(1);
  } else if (count === 3) {
    keyNode = args.node(1);
    aggregateNode = args.node(2);
  } else {
    binder = args.symbol(1);
    keyNode = args.node(2);
    aggregateNode = args.node(3);
  }

  const needsK = nodeContainsVar(keyNode, '_K');
  const frame = new Map([[binder, null]]);
  if (needsK) frame.set('_K', null);
  const table = new Map();
  const groups = [];
  const process = (key, item, index) => {
    frame.set(binder, item);
    if (needsK) frame.set('_K', Value.text(key === undefined ? String(index) : key));
    const groupKey = args.evalNode(keyNode);
    const hash = structuralHash(groupKey);
    const bucket = table.get(hash) || [];
    const existing = bucket.find((group) => group.key.eql(groupKey));
    if (existing) {
      existing.rows.push(item);
      return;
    }
    let keyString = '';
    if (aggregateNode === null) {
      if (groupKey.kind === 'TEXT') keyString = String(groupKey.scalar);
      else if (groupKey.kind === 'BOOL') keyString = groupKey.scalar ? 'TRUE' : 'FALSE';
      else if (groupKey.looksNumeric()) keyString = String(groupKey.scalar);
    }
    const group = { key: groupKey, keyString, rows: [item] };
    bucket.push(group);
    table.set(hash, bucket);
    groups.push(group);
  };

  ctx.pushFrame(frame);
  try {
    let index = 0;
    for (const [key, item] of elements(value)) process(key, item, ++index);
  } finally {
    ctx.popFrame();
  }

  if (aggregateNode === null) {
    const out = Value.none();
    for (const group of groups) {
      out.set(group.keyString, Value.listOwned(group.rows.map((row) => row.clone())));
    }
    return out;
  }

  const out = [];
  const aggregateFrame = new Map([[binder, null], ['_K', null]]);
  ctx.pushFrame(aggregateFrame);
  try {
    for (const group of groups) {
      aggregateFrame.set(binder, Value.listOwned(group.rows));
      aggregateFrame.set('_K', group.key);
      out.push(args.evalNode(aggregateNode));
    }
  } finally {
    ctx.popFrame();
  }
  return Value.listOwned(out);
}

define({ name: 'BUCKET', min: 2, max: 4, lazy: true, binds: true, fn: doBucket });
