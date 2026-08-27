"""Aggregates. These are why SEL needs no loop: each evaluates one argument node
once per element, which is the same move IF makes, repeated.
"""

from .. import decimal as D
from ..registry import define
from ..value import NONE, Value


def shape(args):
    """Two-argument form binds `_`; three-argument form takes a bare identifier
    as the binder, checked by inspecting the AST node the caller handed us.
    """
    if args.count() == 3:
        return args.symbol(1), args.node(2)
    return '_', args.node(1)


def elements(value):
    """A scalar with no children behaves as a one-element list containing itself,
    consistent with scalar context (§3.2). A NONE with no children is genuinely
    empty — that is what FILTER returns when nothing matched, and ALL over it
    must be TRUE rather than a scalar-context failure.
    """
    if value.size() > 0:
        return value.entries()
    return [] if value.kind == NONE else [('1', value)]


def walk(args, ctx, visit):
    """Runs `visit` per element with the binder and _K in scope. Returning a
    value from `visit` stops the walk and becomes the result.

    The frame is popped in a finally, so a body that raises does not leave the
    binder in scope for whatever runs next.
    """
    binder, body = shape(args)
    for key, item in elements(args.val(0)):
        ctx.push_frame({binder: item, '_K': Value.text(key)})
        try:
            result = visit(args.eval_node(body), key, item, body)
        finally:
            ctx.pop_frame()
        if result is not None:
            return result
    return None


def _all(args, ctx):
    short = walk(args, ctx,
                 lambda r, k, i, body: None if r.as_bool(body.pos) else Value.bool(False))
    return short if short is not None else Value.bool(True)


def _any(args, ctx):
    short = walk(args, ctx,
                 lambda r, k, i, body: Value.bool(True) if r.as_bool(body.pos) else None)
    return short if short is not None else Value.bool(False)


def _map(args, ctx):
    out = []

    def visit(r, k, i, body):
        out.append(r.clone())
        return None

    walk(args, ctx, visit)
    return Value.list(out)


def _filter(args, ctx):
    """The one aggregate that preserves keys — a filtered list should still be
    addressable the way the original was.
    """
    out = Value.none()

    def visit(r, key, item, body):
        if r.as_bool(body.pos):
            out.set(key, item.clone())
        return None

    walk(args, ctx, visit)
    return out


def _sum(args, ctx):
    total = [D.ZERO]

    def visit(r, k, i, body):
        total[0] = D.add(total[0], r.as_decimal(body.pos))
        return None

    walk(args, ctx, visit)
    return Value.num(total[0])


def _join(args, ctx):
    """Strict, not an aggregate: its second argument is a separator, not a body."""
    sep = args.text(1)
    parts = [item.as_text(args.pos_of(0)) for _, item in elements(args.val(0))]
    return Value.text(sep.join(parts))


define('ALL', 2, 3, lazy=True, binds=True, fn=_all)
define('ANY', 2, 3, lazy=True, binds=True, fn=_any)
define('MAP', 2, 3, lazy=True, binds=True, fn=_map)
define('FILTER', 2, 3, lazy=True, binds=True, fn=_filter)
define('SUM', 2, 3, lazy=True, binds=True, fn=_sum)
define('JOIN', 2, 2, fn=_join)
