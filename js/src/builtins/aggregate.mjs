// Aggregates. These are why SEL needs no loop: each evaluates one argument node
// once per element, which is the same move IF makes, repeated.

import * as D from '../decimal.mjs';
import { Value, NONE } from '../value.mjs';
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
  if (value.size() > 0) return value.entries();
  return value.kind === NONE ? [] : [['1', value]];
}

// Runs `visit` per element with the binder and _K in scope. Returning a value
// from `visit` stops the walk and becomes the result.
function walk(args, ctx, visit) {
  const { binder, body } = shape(args);
  for (const [key, item] of elements(args.val(0))) {
    const frame = new Map([[binder, item], ['_K', Value.text(key)]]);
    ctx.pushFrame(frame);
    let result;
    try {
      result = visit(args.evalNode(body), key, item, body);
    } finally {
      ctx.popFrame();
    }
    if (result !== undefined) return result;
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
    walk(args, ctx, (r) => { out.push(r.clone()); return undefined; });
    return Value.list(out);
  },
});

// The one aggregate that preserves keys — a filtered list should still be
// addressable the way the original was.
define({
  name: 'FILTER', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    const out = new Value(NONE, null, true);
    walk(args, ctx, (r, key, item, body) => {
      if (r.asBool(body.pos)) out.set(key, item.clone());
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

  return Value.list(indexed.map((x) => x.item.clone()));
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
