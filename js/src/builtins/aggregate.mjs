// Aggregates. These are why SEL needs no loop: each evaluates one argument node
// once per element, which is the same move IF makes, repeated.

import * as D from '../decimal.mjs';
import { Value, NONE, structuralHash } from '../value.mjs';
import { define } from '../registry.mjs';
import { bytesCompare } from '../utf8.mjs';
import { SelError, fail } from '../errors.mjs';

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
// `tentative`: a body that raises keeps the element -- the visitor sees null
// -- for the FILTER above to decide (a pushed conjunct, spec §7.4).
function walk(args, ctx, visit, tentative = false, bodyOverride = null) {
  const { binder, body: written } = shape(args);
  const body = bodyOverride ?? written;
  const collection = args.val(0);
  const frame = new Map([[binder, null]]);
  const needsK = nodeContainsVar(body, '_K');
  if (needsK) frame.set('_K', null);
  ctx.pushFrame(frame);
  try {
    const visitItem = (key, item) => {
      frame.set(binder, item);
      if (needsK) frame.set('_K', Value.text(key));
      let r;
      try {
        r = args.evalNode(body);
      } catch (e) {
        if (!tentative || !(e instanceof SelError)) throw e;
        r = null;
      }
      return visit(r, key, item, body);
    };
    // Match Lisp's vector fast paths: read the packed storage directly and
    // reuse the binder frame. `entries()` is intentionally reserved for the
    // host-facing snapshot API because its pair arrays create avoidable GC in
    // MAP/FILTER/SUM over large relations.
    if (collection.storage !== null) {
      if (collection.isList) {
        for (let i = 0; i < collection.storage.length; i++) {
          const result = visitItem(String(i + 1), collection.storage[i]);
          if (result !== undefined) return result;
        }
      } else {
        const keys = collection.shape.keys;
        for (let i = 0; i < collection.storage.length; i++) {
          const result = visitItem(keys[i], collection.storage[i]);
          if (result !== undefined) return result;
        }
      }
    } else if (collection.children) {
      for (const [key, item] of collection.children) {
        const result = visitItem(key, item);
        if (result !== undefined) return result;
      }
    } else if (collection._entries !== null) {
      for (const [key, item] of collection._entries) {
        const result = visitItem(key, item);
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
const TEXT_COMPARE = new Set(['$==', '$!=', '$<', '$<=', '$>', '$>=']);
const NUM_COMPARE = new Set(['==', '!=', '<', '<=', '>', '>=']);

// Every AND-conjunct of a FILTER body, in order, as { node, fields, total }
// for a LINK to pre-apply to its left rows (structure.mjs, doLink; SEL-0052).
// `fields`: the upper-cased fields of the row the conjunct reads -- a bare
// `_["status"]` reads STATUS, a nested `_["orders"]["year"]` reads ORDERS --
// or null when it reads anything else (another variable, `_K`, the element
// as a whole, a call, an assignment). `total`: for a comparison between
// literals and bare field reads, the [name, kind] requirements under which
// it cannot raise (every such field on every row of its owning side, as
// text or as a number); null when not provable.
export function leadingFieldConjuncts(body, binder) {
  const conjuncts = [];
  let node = body;
  while (node && node.t === 'bin' && node.op === 'AND') {
    conjuncts.push(node.r);
    node = node.l;
  }
  conjuncts.push(node);
  conjuncts.reverse();
  // The element is the binder, exactly as named (names are canonical): under
  // an explicit binder `_` is not the element.
  const isRowVar = (n) => n && n.t === 'var' && n.name === binder;
  const bareRead = (n) => n && n.t === 'index' && isRowVar(n.obj) && n.idx && n.idx.t === 'text';
  const literalKind = (n) => (n && n.t === 'num' ? 'NUM' : n && n.t === 'text' ? 'TEXT' : null);
  return conjuncts.map((c) => {
    const fields = new Set();
    const readsOnlyFields = (n) => {
      if (!n) return true;
      if (n.t === 'index') {
        if (bareRead(n)) { fields.add(n.idx.v.toUpperCase()); return true; }
        if (n.obj && n.obj.t === 'index') return readsOnlyFields(n.obj) && readsOnlyFields(n.idx);
        return false;
      }
      if (n.t === 'num' || n.t === 'text' || n.t === 'bool') return true;
      if (n.t === 'bin') return readsOnlyFields(n.l) && readsOnlyFields(n.r);
      if (n.t === 'un') return readsOnlyFields(n.x);
      return false;
    };
    const ok = readsOnlyFields(c) && fields.size > 0;
    let total = null;
    if (c.t === 'bin' && (TEXT_COMPARE.has(c.op) || NUM_COMPARE.has(c.op))) {
      const kind = TEXT_COMPARE.has(c.op) ? 'TEXT' : 'NUM';
      total = [];
      for (const operand of [c.l, c.r]) {
        const lit = literalKind(operand);
        if (lit !== null) {
          // A text literal is not a number: that operand raises on every row.
          if (kind === 'NUM' && lit !== 'NUM') { total = null; break; }
          continue;
        }
        if (!bareRead(operand)) { total = null; break; }
        total.push([operand.idx.v, kind]);
      }
    }
    return { node: c, fields: ok ? fields : null, total, pushed: false, binder };
  });
}

define({
  name: 'FILTER', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    const out = new Value(NONE, null, true);
    const written = args.node(args.nodes.length - 1);
    const tentative = Boolean(written.tentative);
    // Over a join, the conjuncts are offered to the LINK, which pre-applies
    // what it can to its left rows (SEL-0052): this FILTER's first -- it runs
    // before the FILTER that handed the rest down -- then the handed ones.
    // Deep drops, below the join directly under this FILTER, change its
    // keys, so they are allowed only where nothing observes them
    // (`keysUnobserved`, stamped by the physical optimiser).
    const src = args.node(0);
    const handed = ctx.joinPrefilter;
    ctx.joinPrefilter = null;
    if (src && src.t === 'call' && (src.name === 'LINK' || src.name === 'LINK_LEFT')) {
      const { binder, body } = shape(args);
      const own = leadingFieldConjuncts(body, binder);
      // Conjuncts the physical optimiser already pushed under the join (a
      // tentative FILTER below, SEL-0051) held on every row the join sees
      // unless one kept a row on an error; the join checks that and skips
      // them, rather than testing them again.
      if (written.pushedDown) {
        const rem = written.remaining;
        const kept = new Set();
        if (!(rem.t === 'bool' && rem.v === true)) {
          let n = rem;
          while (n && n.t === 'bin' && n.op === 'AND') { kept.add(n.r); n = n.l; }
          kept.add(n);
        }
        for (const c of own) if (!kept.has(c.node)) c.pushed = true;
      }
      // A first conjunct that is neither a field test nor total nor pushed
      // ends every walk before it starts: hand nothing, gather nothing.
      const blocked = own.length > 0 && own[0].fields === null && own[0].total === null && !own[0].pushed;
      const stages = !blocked && (own.some((c) => !c.pushed) || handed !== null)
        ? [{ binder, conjuncts: own }] : [];
      if (handed !== null && !blocked) stages.push(...handed.stages);
      const deep = handed === null ? Boolean(body.keysUnobserved) : true;
      if (stages.length) {
        ctx.joinPrefilter = { stages, deep, above: handed === null ? [] : handed.above,
          obligations: handed === null ? [] : handed.obligations };
      }
    }
    // A predicate whose leading conjuncts were pushed under the LINK below:
    // when no tentative body kept a row on an error while the source ran,
    // every row here passed them, and only the remaining conjuncts are
    // evaluated (TRUE when there are none); otherwise the whole predicate,
    // as written, decides -- and raises -- in the source's order.
    let bodyOverride = null;
    const before = ctx.tentativeKept;
    let source;
    try {
      source = args.val(0);
    } finally {
      ctx.joinPrefilter = null;
    }
    // The join's report -- which conjuncts every row that came up has
    // passed, and whether a row was kept on an error -- goes up as it is.
    const report = ctx.joinPrefilterReport;
    ctx.joinPrefilterReport = null;
    if (report !== null && handed !== null) ctx.joinPrefilterReport = report;
    if (written.pushedDown) {
      if (ctx.tentativeKept === before) {
        bodyOverride = written.remaining;
        // Nothing remains: every row of the join below passed, and the join
        // built a fresh list this FILTER would only copy.
        if (bodyOverride.t === 'bool' && bodyOverride.v === true) return source;
      }
    }
    walk(args, ctx, (r, key, item, body) => {
      let keep;
      if (r === null) {
        keep = true;
        ctx.tentativeKept++;
      } else {
        try {
          keep = r.asBool(body.pos);
        } catch (e) {
          if (!tentative || !(e instanceof SelError)) throw e;
          keep = true;
          ctx.tentativeKept++;
        }
      }
      if (keep) out.set(key, item);
      return undefined;
    }, tentative, bodyOverride);
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

function bucketKeyText(key, pos) {
  const v = key;
  if (v.kind === 'NONE') {
    if (v.isNull()) fail('E_NULL', 'value is NULL', pos);
    fail('E_NOT_TEXT', 'a bucket key must be text or a number, got a list or record', pos);
  }
  return v.asText(pos);
}

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
    // A bare bucket's key is an index key (spec §3.3): the scalar, verbatim,
    // and refused the way indexing refuses it -- never collapsed onto a
    // string that stands for every list, record or NULL. The projected
    // spelling has no map to key and groups by identity instead.
    const keyString = aggregateNode === null ? bucketKeyText(groupKey, keyNode.pos) : '';
    const hash = structuralHash(groupKey);
    const bucket = table.get(hash) || [];
    const existing = bucket.find((group) => group.key.eql(groupKey));
    if (existing) {
      existing.rows.push(item);
      return;
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
