// Aggregates. These are why SEL needs no loop: each evaluates one argument node
// once per element, which is the same move IF makes, repeated.

import * as D from '../decimal.mjs';
import { Value, NONE, structuralHash, scalarKey } from '../value.mjs';
import { define, isHostFunction } from '../registry.mjs';
import { bytesCompare, compareText } from '../utf8.mjs';
import { fail, SelError } from '../errors.mjs';
import { cpLength, checkText, MAX_TEXT_LEN } from '../budget.mjs';
// The direction and field names fold ASCII-only (review 2026-09-25 SEM-05):
// toUpperCase took "deſc" for DESC.
import { asciiUpper } from '../lexer.mjs';

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

// Whether `node` mentions the variable `name`. Iterative, with an explicit
// stack: spec 6.4 says a walk of a tree needs its own bound, since `1+1+1+...`
// builds a tree as deep as it is long, and a recursion here ran out of the
// host's stack on a body of about ten thousand terms before the evaluator --
// which runs next, and counts -- could report E_DEPTH.
function nodeContainsVar(node, name) {
  const want = name.toUpperCase();
  const stack = [node];
  while (stack.length > 0) {
    const n = stack.pop();
    if (!n) continue;
    switch (n.t) {
      case 'var': if (n.name.toUpperCase() === want) return true; break;
      case 'index': stack.push(n.obj, n.idx); break;
      case 'call': for (const item of n.args) stack.push(item); break;
      case 'bin': stack.push(n.l, n.r); break;
      case 'un': stack.push(n.x); break;
      case 'assign': stack.push(n.target, n.value); break;
      case 'seq': case 'list': for (const item of n.items) stack.push(item); break;
      default: break;
    }
  }
  return false;
}

// Whether evaluating `node` might write into a value: it holds an assignment or
// calls a host function. A collector copies an element when it collects it
// (spec §3.4); while nothing below the body can write, deferring that copy to
// the end is unobservable, so only a body that might write pays for copying at
// the moment of collection.
function mayWrite(node) {
  const stack = [node];
  while (stack.length > 0) {
    const n = stack.pop();
    if (!n) continue;
    switch (n.t) {
      case 'assign': return true;
      case 'index': stack.push(n.obj, n.idx); break;
      case 'call':
        if (isHostFunction(n.name)) return true;
        for (const item of n.args) stack.push(item);
        break;
      case 'bin': stack.push(n.l, n.r); break;
      case 'un': stack.push(n.x); break;
      case 'seq': case 'list': for (const item of n.items) stack.push(item); break;
      default: break;
    }
  }
  return false;
}

// Runs `visit` per element with the binder and _K in scope. Returning a value
// from `visit` stops the walk and becomes the result. `bodyOverride`, when
// given, is evaluated per element instead of the written body.
function walk(args, ctx, visit, bodyOverride = null) {
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
      return visit(args.evalNode(body), key, item, body);
    };
    // The walk visits a SNAPSHOT of the collection taken here (spec §7.3): what a
    // body appends, adds, overwrites or replaces in the source is not seen by the
    // elements still to come, only a change made INSIDE an element is. The
    // snapshot is a copy of the references -- the packed storage or entry list
    // is sliced, the child map's pairs listed -- so a body that grows the source
    // can neither extend the walk nor invalidate what it is standing on.
    if (collection.storage !== null) {
      const items = collection.storage.slice();
      if (collection.isList) {
        for (let i = 0; i < items.length; i++) {
          const result = visitItem(String(i + 1), items[i]);
          if (result !== undefined) return result;
        }
      } else {
        const keys = collection.shape.keys;
        for (let i = 0; i < items.length; i++) {
          const result = visitItem(keys[i], items[i]);
          if (result !== undefined) return result;
        }
      }
    } else if (collection.children) {
      for (const [key, item] of Array.from(collection.children)) {
        const result = visitItem(key, item);
        if (result !== undefined) return result;
      }
    } else if (collection._entries !== null) {
      for (const [key, item] of collection._entries.slice()) {
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

const BUILDS_NUMBER = new Set(['+', '-', '*', '/', '%']);
// Whether evaluating `node` always yields a value no other reference holds: a
// RECORD/LIST call (their arguments are copied into them, SPEC 3.4) or arithmetic.
function buildsItsResult(node) {
  if (!node) return false;
  if (node.t === 'call') return node.name === 'RECORD' || node.name === 'LIST';
  if (node.t === 'bin') return BUILDS_NUMBER.has(node.op);
  if (node.t === 'un') return node.op === 'NEG';
  return false;
}

define({
  name: 'MAP', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    const out = [];
    // The collectors copy what they collect (spec §3.4): an element the body
    // returned by reference -- `MAP(X, _)` -- must not stay live in X. A body that
    // BUILDS its result (a RECORD or LIST, which copied its own arguments, or
    // arithmetic, which computes a new number) returns a value nothing else refers
    // to, so the copy would only duplicate it (0.9.2 comparison, JS-REG-1).
    walk(args, ctx, (r, _k, _i, body) => {
      out.push(buildsItsResult(body) ? r : r.cloneAt(2, args.pos));
      return undefined;
    });
    return Value.listOwned(out);
  },
});

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
    // Iterative, like nodeContainsVar: a conjunct is as deep as its source is long.
    const readsOnlyFields = (root) => {
      const stack = [root];
      while (stack.length > 0) {
        const n = stack.pop();
        if (!n) continue;
        if (n.t === 'index') {
          if (bareRead(n)) { fields.add(asciiUpper(n.idx.v)); continue; }
          if (n.obj && n.obj.t === 'index') { stack.push(n.obj, n.idx); continue; }
          return false;
        }
        if (n.t === 'num' || n.t === 'text' || n.t === 'bool') continue;
        if (n.t === 'bin') { stack.push(n.l, n.r); continue; }
        if (n.t === 'un') { stack.push(n.x); continue; }
        return false;
      }
      return true;
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
    return { node: c, fields: ok ? fields : null, total, binder };
  });
}

// The one aggregate that preserves keys — a filtered list should still be
// addressable the way the original was.
define({
  name: 'FILTER', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    const out = new Value(NONE, null, true);
    // Over a join, the conjuncts are offered to the LINK, which tests what it
    // can on the rows it joins (SEL-0052, SEL-0054): this FILTER's first --
    // it runs before the FILTER that handed the rest down -- then the handed
    // ones. Deep drops, below the join directly under this FILTER, change its
    // keys, so they are allowed only where nothing observes them
    // (`keysUnobserved`, stamped by the physical optimiser).
    const src = args.node(0);
    const handed = ctx.joinPrefilter;
    ctx.joinPrefilter = null;
    let own = null;
    if (src && src.t === 'call' && (src.name === 'LINK' || src.name === 'LINK_LEFT')) {
      const { binder, body } = shape(args);
      own = leadingFieldConjuncts(body, binder);
      // A first conjunct that is neither a field test nor total ends every
      // walk before it starts: hand nothing, gather nothing.
      const blocked = own.length > 0 && own[0].fields === null && own[0].total === null;
      const stages = blocked ? [] : [{ binder, conjuncts: own, above: 0 }];
      if (handed !== null && !blocked) stages.push(...handed.stages);
      const deep = handed === null ? Boolean(body.keysUnobserved) : true;
      if (stages.length) {
        ctx.joinPrefilter = { stages, deep, above: handed === null ? [] : handed.above,
          obligations: handed === null ? [] : handed.obligations };
      }
    }
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
    // The conjuncts of this FILTER the join below applied held on every row
    // it built, unless it kept a row on an error: then they are TRUE there,
    // raise nowhere, and only the rest is evaluated, in the source's order
    // (with none left, the join's list is the FILTER's result as it is).
    let bodyOverride = null;
    if (own !== null && report !== null && !report.errored && own.some((c) => report.applied.has(c.node))) {
      const rest = own.filter((c) => !report.applied.has(c.node)).map((c) => c.node);
      if (rest.length === 0) return source;
      bodyOverride = rest.reduce((l, r) => ({ t: 'bin', op: 'AND', l, r, pos: l.pos }));
    }
    walk(args, ctx, (r, key, item, body) => {
      if (r.asBool(body.pos)) out.set(key, item.cloneAt(2, args.pos));
      return undefined;
    }, bodyOverride);
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
    // The length is known before the result is built (SPEC 6.4): the parts and
    // the separators between them. An empty result is never too large.
    if (parts.length > 0) {
      let units = sep.length * (parts.length - 1);
      for (const p of parts) units += p.length;
      if (units > MAX_TEXT_LEN) {
        let size = cpLength(sep) * (parts.length - 1);
        for (const p of parts) size += cpLength(p);
        checkText(size, args.pos, 'JOIN result');
      }
    }
    return Value.textOwned(parts.join(sep));
  },
});

// The total order of SPEC §7.3, one rank per kind: NULL < BOOL (FALSE before
// TRUE) < numeric-looking text and numbers, by exact decimal value < every other
// TEXT, bytewise < BIN, bytewise. Equal keys tie, and the sort keeps their input
// order. The old comparator took the first branch that applied to a PAIR (both
// numeric: by value; else both text: bytes), so a number was below one text and
// above another that sorted below it -- not an order at all.
function sortRank(v) {
  if (v === null || v.isNull()) return 0;
  if (v.kind === 'BOOL') return 1;
  if (v.looksNumeric()) return 2;
  if (v.kind === 'TEXT') return 3;
  if (v.kind === 'BIN') return 4;
  return 5;
}

// A value with children and no scalar of its own is ordered by what scalar
// context makes of it (SPEC 3.2: its first child, recursively), so a record
// sorts by its first field and ranks by that field's kind. A chain that ends in
// nothing is NULL.
function sortLeaf(key) {
  if (key.kind !== NONE) return key;
  try {
    return key.scalarSource(null);
  } catch (e) {
    if (e instanceof SelError) return null;
    throw e;
  }
}

// A key with its rank and comparable form worked out once, not per comparison.
function keyInfo(key) {
  const leaf = sortLeaf(key);
  const rk = sortRank(leaf);
  return {
    key,
    rk,
    dec: rk === 2 ? leaf.asDecimal() : null,
    // TEXT keeps its string (compared natively where that is exact), BIN its bytes.
    text: rk === 3 ? leaf.scalar : null,
    plain: rk === 3 && !ANY_SURROGATE.test(leaf.scalar),
    bytes: rk === 4 ? leaf.asBytes() : null,
    flag: rk === 1 ? (leaf.scalar ? 1 : 0) : 0,
  };
}

const ANY_SURROGATE = /[\uD800-\uDFFF]/;

function compareInfo(a, b) {
  if (a.rk !== b.rk) return a.rk - b.rk;
  switch (a.rk) {
    case 1: return a.flag - b.flag;
    case 2: return D.cmp(a.dec, b.dec);
    case 3: return compareText(a.text, b.text);
    case 4: return bytesCompare(a.bytes, b.bytes);
    default: return 0;
  }
}

function doSort(args, ctx, forcedDir) {
  const val = args.val(0);
  const entries = val.isNull() ? [] : elements(val);

  const count = args.count();
  let dir;
  let indexed;
  let binder;
  let body;

  if (count === 1) {
    dir = forcedDir || 'ASC';
  } else {
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
        dir = asciiUpper(args.text(2));
      } else if (args.isSymbol(1)) {
        binder = args.symbol(1);
        body = args.node(2);
        dir = 'ASC';
      } else {
        binder = '_';
        body = args.node(1);
        dir = asciiUpper(args.text(2));
      }
    } else {
      binder = args.symbol(1);
      body = args.node(2);
      dir = asciiUpper(args.text(3));
    }

    if (dir !== 'ASC' && dir !== 'DESC') {
      const posIdx = count === 4 ? 3 : 2;
      fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", args.posOf(posIdx));
    }
  }
  // The direction was evaluated and checked above whether or not there is
  // anything to sort (SPEC 7.4): an empty list does not excuse a bad one.
  if (entries.length === 0) return Value.list([]);

  if (count === 1) {
    indexed = entries.map(([, item]) => ({ item, info: keyInfo(item) }));
  } else {
    const needsK = nodeContainsVar(body, '_K');
    const eager = mayWrite(body);
    indexed = entries.map(([k, item]) => {
      const frame = new Map([[binder, item]]);
      if (needsK) frame.set('_K', Value.text(k));
      ctx.pushFrame(frame);
      let evalKey;
      try {
        evalKey = args.evalNode(body);
      } finally {
        ctx.popFrame();
      }
      // Collected once its key is computed (spec §3.4): a later key that writes
      // into this element must not reach the result.
      return { item: eager ? item.cloneAt(2, args.pos) : item, info: keyInfo(evalKey), owned: eager };
    });
  }

  // Array.prototype.sort is stable, so equal keys keep their input order without an index
  // tie-break. When every key has the same rank the comparison is one specialised step
  // (plain text natively, decimals by D.cmp); otherwise the general ordering of §7.3.
  const sign = dir === 'DESC' ? -1 : 1;
  const rk0 = indexed[0].info.rk;
  const homogeneous = indexed.every((x) => x.info.rk === rk0);
  if (homogeneous && rk0 === 3 && indexed.every((x) => x.info.plain)) {
    indexed.sort((x, y) => (x.info.text < y.info.text ? -sign : x.info.text > y.info.text ? sign : 0));
  } else if (homogeneous && rk0 === 2) {
    indexed.sort((x, y) => sign * D.cmp(x.info.dec, y.info.dec));
  } else {
    indexed.sort((x, y) => sign * compareInfo(x.info, y.info));
  }

  return Value.listOwned(indexed.map((x) => (x.owned ? x.item : x.item.cloneAt(2, args.pos))));
}

function doTop(args, ctx, forcedDir) {
  const value = args.val(0);
  const limit = args.nonNegInt(args.count() - 1);

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
      dir = asciiUpper(args.text(2));
    } else if (args.isSymbol(1)) {
      binder = args.symbol(1);
      body = args.node(2);
    } else {
      body = args.node(1);
      dir = asciiUpper(args.text(2));
    }
  } else { // 4 (the manifest's arity bounds the count at compile time)
    binder = args.symbol(1);
    body = args.node(2);
    dir = asciiUpper(args.text(3));
  }
  if (dir !== 'ASC' && dir !== 'DESC') {
    const directionIndex = sortCount === 4 ? 3 : 2;
    fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", args.posOf(directionIndex));
  }
  // Count and direction are evaluated and checked first, empty source or not.
  if (limit === 0 || (value.kind === NONE && value.size() === 0)) return Value.list([]);

  const compare = (a, b) => {
    let c = compareInfo(a.info, b.info);
    if (dir === 'DESC') c = -c;
    return c !== 0 ? c : a.idx - b.idx;
  };
  // compare() breaks ties by input position, so no two entries compare equal.
  const worse = (a, b) => compare(a, b) > 0;
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
  // Collected once its key is computed (spec §3.4); a key that might write copies
  // the element as it is admitted, so a later key's write cannot reach it.
  const eager = body !== null && mayWrite(body);
  const admit = (candidate) => {
    if (eager) { candidate.item = candidate.item.cloneAt(2, args.pos); candidate.owned = true; }
    return candidate;
  };
  let idx = 0;
  const consume = (key, item) => {
    let candidate;
    if (binder === null) {
      candidate = { item, info: keyInfo(item), idx };
    } else {
      const frame = new Map([[binder, item]]);
      if (needsK) frame.set('_K', Value.text(key));
      ctx.pushFrame(frame);
      try {
        candidate = { item, info: keyInfo(args.evalNode(body)), idx };
      } finally {
        ctx.popFrame();
      }
    }
    idx += 1;
    if (heap.length < limit) {
      heap.push(admit(candidate));
      siftUp(heap.length - 1);
    } else if (worse(heap[0], candidate)) {
      heap[0] = admit(candidate);
      siftDown(0);
    }
  };
  // A snapshot, like every aggregate's walk (SPEC 7.3): a key body that appends
  // to the source neither extends the walk nor moves what it stands on.
  if (value.isList && value.storage !== null) {
    const items = value.storage.slice();
    for (let i = 0; i < items.length; i++) consume(String(i + 1), items[i]);
  } else if (value.shape) {
    const items = value.storage.slice();
    for (let i = 0; i < items.length; i++) consume(value.shape.keys[i], items[i]);
  } else {
    for (const [key, item] of elements(value)) consume(key, item);
  }
  heap.sort(compare);
  // Copied like SORT's (spec §3.4): TOP is SORT and TAKE in one pass.
  return Value.listOwned(heap.map((entry) => (entry.owned ? entry.item : entry.item.cloneAt(2, args.pos))));
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
  // NULL and an empty collection group nothing; a scalar is a one-element list
  // (SPEC 3.2), so it makes one group.
  if (value.kind === NONE && value.size() === 0) return Value.list([]);

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
  // A row is collected when its key is computed and it is grouped (spec §3.4):
  // when the key or the projection might write, it is copied then, so neither a
  // later key nor the projection can change a row already grouped.
  const eager = mayWrite(keyNode) || (aggregateNode !== null && mayWrite(aggregateNode));
  const table = new Map();
  const byText = new Map();
  const byScalar = new Map();
  const groups = [];
  const process = (key, source, index) => {
    frame.set(binder, source);
    if (needsK) frame.set('_K', Value.text(key === undefined ? String(index) : key));
    const groupKey = args.evalNode(keyNode);
    // Bare: record → group list → row (3 levels); projected: group list → row (2).
    const item = eager ? source.cloneAt(aggregateNode === null ? 3 : 2, args.pos) : source;
    // A bare bucket's key is an index key (spec §3.3): the scalar, verbatim,
    // and refused the way indexing refuses it -- never collapsed onto a
    // string that stands for every list, record or NULL. The projected
    // spelling has no map to key and groups by identity instead.
    const keyString = aggregateNode === null ? bucketKeyText(groupKey, keyNode.pos) : '';
    if (aggregateNode === null) {
      // The key IS its text: two keys with the same text are one group, whatever
      // structure their values have, and none of their rows is lost.
      const same = byText.get(keyString);
      if (same) { same.rows.push(item); return; }
      const group = { key: groupKey, keyString, rows: [item] };
      byText.set(keyString, group);
      groups.push(group);
      return;
    }
    const exact = scalarKey(groupKey);
    if (exact !== null) {
      const same = byScalar.get(exact);
      if (same) { same.rows.push(item); return; }
      const group = { key: groupKey, keyString, rows: [item] };
      byScalar.set(exact, group);
      groups.push(group);
      return;
    }
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
      out.set(group.keyString, Value.listOwned(eager ? group.rows : group.rows.map((row) => row.cloneAt(3, args.pos))));
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
      out.push(args.evalNode(aggregateNode).cloneAt(2, args.pos));
    }
  } finally {
    ctx.popFrame();
  }
  return Value.listOwned(out);
}

define({ name: 'BUCKET', min: 2, max: 4, lazy: true, binds: true, fn: doBucket });
