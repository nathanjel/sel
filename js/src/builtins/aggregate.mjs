// Aggregates. These are why SEL needs no loop: each evaluates one argument node
// once per element, which is the same move IF makes, repeated.

import * as D from '../decimal.mjs';
import { Value, NONE } from '../value.mjs';
import { define } from '../registry.mjs';

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
    const out = Value.none();
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
      total = D.add(total, r.asDecimal(body.pos));
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
