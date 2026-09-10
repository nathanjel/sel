"""Aggregates. These are why SEL needs no loop: each evaluates one argument node
once per element, which is the same move IF makes, repeated.
"""

from functools import cmp_to_key

from .. import decimal as D
from ..errors import fail
from ..eval import bytes_compare
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
    out = Value(NONE, None, is_list=True)

    def visit(r, key, item, body):
        if r.as_bool(body.pos):
            out.set(key, item.clone())
        return None

    walk(args, ctx, visit)
    return out


def _sum(args, ctx):
    total = [D.ZERO]

    def visit(r, k, i, body):
        total[0] = D.add(total[0], r.as_decimal(body.pos), body.pos)
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


def compare_values(a: Value, b: Value) -> int:
    a_null = a.is_null()
    b_null = b.is_null()
    if a_null and b_null:
        return 0
    if a_null:
        return -1
    if b_null:
        return 1

    a_num = a.looks_numeric()
    b_num = b.looks_numeric()
    if a_num and b_num:
        return D.cmp(a.as_decimal(), b.as_decimal())

    if a.kind == 'BOOL' and b.kind == 'BOOL':
        av = 1 if a.scalar else 0
        bv = 1 if b.scalar else 0
        return (av > bv) - (av < bv)

    if a.kind in ('TEXT', 'BIN') and b.kind in ('TEXT', 'BIN'):
        return bytes_compare(a.as_bytes(), b.as_bytes())

    def rank(v):
        if v.is_null():
            return 0
        if v.kind == 'BOOL':
            return 1
        if v.looks_numeric():
            return 2
        if v.kind == 'TEXT':
            return 3
        if v.kind == 'BIN':
            return 4
        return 5

    ra = rank(a)
    rb = rank(b)
    return (ra > rb) - (ra < rb)


def do_sort(args, ctx, forced_dir):
    val = args.val(0)
    if val.is_null():
        return Value.list([])
    ents = elements(val)
    if not ents:
        return Value.list([])

    count = args.count()
    if count == 1:
        direction = forced_dir or 'ASC'
        indexed = [{'item': item, 'key': item, 'idx': idx} for idx, (_, item) in enumerate(ents)]
    else:
        if count == 2:
            binder = '_'
            body = args.node(1)
            direction = forced_dir or 'ASC'
        elif count == 3:
            if forced_dir is not None:
                binder = args.symbol(1)
                body = args.node(2)
                direction = forced_dir
            elif args.node(2).t == 'text':
                binder = '_'
                body = args.node(1)
                direction = args.text(2).upper()
            elif args.is_symbol(1):
                binder = args.symbol(1)
                body = args.node(2)
                direction = 'ASC'
            else:
                binder = '_'
                body = args.node(1)
                direction = args.text(2).upper()
        else:  # 4
            binder = args.symbol(1)
            body = args.node(2)
            direction = args.text(3).upper()

        if direction not in ('ASC', 'DESC'):
            pos_idx = 3 if count == 4 else 2
            fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", args.pos_of(pos_idx))

        indexed = []
        for idx, (k, item) in enumerate(ents):
            ctx.push_frame({binder: item, '_K': Value.text(k)})
            try:
                eval_key = args.eval_node(body)
            finally:
                ctx.pop_frame()
            indexed.append({'item': item, 'key': eval_key, 'idx': idx})

    def cmp_func(x, y):
        c = compare_values(x['key'], y['key'])
        if direction == 'DESC':
            c = -c
        return c if c != 0 else (x['idx'] - y['idx'])

    indexed.sort(key=cmp_to_key(cmp_func))
    return Value.list([x['item'].clone() for x in indexed])


define('SORT', 1, 3, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, 'ASC'))
define('SORT_DESC', 1, 3, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, 'DESC'))
define('SORT_BY', 2, 4, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, None))

