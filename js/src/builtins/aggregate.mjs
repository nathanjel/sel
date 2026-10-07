// Aggregates. These are why SEL needs no loop: each evaluates one argument node
// once per element, which is the same move IF makes, repeated.

import * as D from '../decimal.mjs';
import { Value, NONE, structuralHash, scalarKey, elements } from '../value.mjs';
import { define, callRoles, mayHaveEffects } from '../registry.mjs';
import { mayWrite, mentionsKey } from '../ast.mjs';
import { bytesCompare, compareText, ANY_SURROGATE } from '../utf8.mjs';
import { fail, SelError } from '../errors.mjs';
import { cpLength, checkText, MAX_TEXT_LEN } from '../budget.mjs';
// The direction and field names fold ASCII-only:
// toUpperCase took "deſc" for DESC.
import { asciiUpper } from '../lexer.mjs';
import { ARITH_OPS, NUM_COMPARE_OPS, TEXT_COMPARE_OPS } from '../ops.mjs';

// The binder and the per-element body of a binding aggregate, where the
// manifest's form puts them (registry.argRoles): the two-argument form binds
// `_`, the three-argument form takes a bare identifier as the binder (checked
// on the AST node: E_EXPECT_SYMBOL otherwise).
function shape(args) {
  const roles = callRoles(args.name, args.nodes);
  return { binder: roles.binder < 0 ? '_' : args.symbol(roles.binder), body: args.node(roles.body) };
}

// A sort's decoded arguments: binder, key node (null when the elements are
// their own keys) and direction. The source -- and for a TOP the count -- is
// evaluated by the caller first; then the binder is checked, then the
// direction evaluated: a text literal there is a direction even beside a bare
// name (the manifest's form order), and a computed one is evaluated as one.
function sortArgs(args, forcedDir, directionSlots) {
  const roles = callRoles(args.name, args.nodes);
  const binder = roles.binder < 0 ? '_' : args.symbol(roles.binder);
  const body = roles.body < 0 ? null : args.node(roles.body);
  let dir = forcedDir || 'ASC';
  if (roles.after.length > directionSlots) {
    const at = roles.after[0];
    dir = asciiUpper(args.text(at));
    if (dir !== 'ASC' && dir !== 'DESC') {
      fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", args.posOf(at));
    }
  }
  return { binder, body, dir };
}

// Runs `visit` per element with the binder and _K in scope. Returning a value
// from `visit` stops the walk and becomes the result. `bodyOverride`, when
// given, is evaluated per element instead of the written body.
function walk(args, ctx, visit, bodyOverride = null) {
  const { binder, body: written } = shape(args);
  const body = bodyOverride ?? written;
  const collection = args.val(0);
  const frame = new Map([[binder, null]]);
  const needsK = mentionsKey(body);
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

// The arithmetic family (spec/lexicon.json): each builds a fresh number.
const BUILDS_NUMBER = ARITH_OPS;
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
    // to, so the copy would only duplicate it.
    walk(args, ctx, (r, _k, _i, body) => {
      out.push(buildsItsResult(body) ? r : r.cloneAt(2, args.pos));
      return undefined;
    });
    return Value.listOwned(out);
  },
});

const TEXT_COMPARE = TEXT_COMPARE_OPS;
const NUM_COMPARE = NUM_COMPARE_OPS;

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
    // Iterative, like ast.mjs's walks: a conjunct is as deep as its source is long.
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

// { member, field } when CONJUNCT is `IS_NULL(binder["member"]["field"])` --
// the shipped IS_NULL of a literal field of a literal member of the FILTER's
// element -- else null. Over a LINK_LEFT, a member that is one of the join's
// right binder keys is the right row (spec §7.4), and the join may skip
// building the joined rows of the right rows the conjunct is FALSE on
// (structure.mjs, rightNullRejects).
function rightNullTest(conjunct, binder) {
  if (!conjunct || conjunct.t !== 'call' || conjunct.name !== 'IS_NULL'
      || conjunct.args.length !== 1 || mayHaveEffects('IS_NULL')) return null;
  const field = conjunct.args[0];
  if (!field || field.t !== 'index' || !field.idx || field.idx.t !== 'text') return null;
  const member = field.obj;
  if (!member || member.t !== 'index' || !member.idx || member.idx.t !== 'text'
      || !member.obj || member.obj.t !== 'var' || member.obj.name !== binder) return null;
  return { member: member.idx.v, field: field.idx.v };
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
    // keys, so they are allowed only where nothing observes them: no step
    // after it (`keysUnobserved`, stamped by the physical optimiser), and not
    // its own body, which reads them as `_K`.
    const src = args.node(0);
    const handed = ctx.joinPrefilter;
    ctx.joinPrefilter = null;
    let own = null;
    // An early test holds only while nothing can change what it read before
    // this FILTER reads it (spec §7.4): a predicate that may write -- an
    // assignment, or a call SEL does not ship (ast.mayWrite, answered once per
    // node) -- is offered to no join, and the conjuncts handed from above stop
    // here too.
    if (src && src.t === 'call' && (src.name === 'LINK' || src.name === 'LINK_LEFT')
        && !mayWrite(shape(args).body)) {
      const { binder, body } = shape(args);
      own = leadingFieldConjuncts(body, binder);
      // A first conjunct that is neither a field test nor total ends every
      // walk before it starts: hand nothing, gather nothing.
      const blocked = own.length > 0 && own[0].fields === null && own[0].total === null;
      const stages = blocked ? [] : [{ binder, conjuncts: own, above: 0 }];
      if (handed !== null && !blocked) stages.push(...handed.stages);
      const deep = (handed === null ? Boolean(body.keysUnobserved) : true) && !mentionsKey(body);
      if (stages.length) {
        ctx.joinPrefilter = { stages, deep, above: handed === null ? [] : handed.above,
          obligations: handed === null ? [] : handed.obligations };
      } else if (handed === null && src.name === 'LINK_LEFT' && own.length > 0) {
        // A predicate that opens with IS_NULL of a right member's field
        // (S6's unsold products): the join may reject the right rows it is
        // FALSE on before building their joined rows. Nothing is reported
        // back -- the null-extended rows were never tested -- so the whole
        // predicate still runs over every row the join builds.
        const test = rightNullTest(own[0].node, binder);
        if (test !== null) ctx.joinRightNull = { member: test.member, field: test.field, deep };
      }
    }
    let source;
    try {
      source = args.val(0);
    } finally {
      ctx.joinPrefilter = null;
      ctx.joinRightNull = null;
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
    return Value.numOwned(total);
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

  const { binder, body, dir } = sortArgs(args, forcedDir, 0);
  let indexed;
  // The direction was evaluated and checked above whether or not there is
  // anything to sort (SPEC 7.4): an empty list does not excuse a bad one.
  if (entries.length === 0) return Value.listOwned([]);

  if (body === null) {
    indexed = entries.map(([, item]) => ({ item, info: keyInfo(item) }));
  } else {
    const needsK = mentionsKey(body);
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

  // The count is the last slot; a direction, when there is one, precedes it.
  const { binder, body, dir } = sortArgs(args, forcedDir, 1);
  // Count and direction are evaluated and checked first, empty source or not.
  if (limit === 0 || (value.kind === NONE && value.size() === 0)) return Value.listOwned([]);

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
  const needsK = body !== null && mentionsKey(body);
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
    if (body === null) {
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
  if (value.kind === NONE && value.size() === 0) return Value.listOwned([]);

  const roles = callRoles(args.name, args.nodes);
  const binder = roles.binder < 0 ? '_' : args.symbol(roles.binder);
  const keyNode = args.node(roles.body);
  const aggregateNode = roles.extra < 0 ? null : args.node(roles.extra);

  const needsK = mentionsKey(keyNode);
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
