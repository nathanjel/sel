"""Aggregates. These are why SEL needs no loop: each evaluates one argument node
once per element, which is the same move IF makes, repeated.
"""

from functools import cmp_to_key

from .. import decimal as D
from ..errors import fail
from ..eval import bytes_compare
from ..registry import define
from ..value import NONE, Value, iter_elements, structural_hash


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
    return list(iter_elements(value))


def node_contains_var(node, name):
    if node is None:
        return False
    if node.t == 'var':
        return node.name.upper() == name.upper()
    if node.t == 'index':
        return node_contains_var(node.obj, name) or node_contains_var(node.idx, name)
    if node.t == 'call':
        return any(node_contains_var(item, name) for item in node.args)
    if node.t == 'bin':
        return node_contains_var(node.l, name) or node_contains_var(node.r, name)
    if node.t == 'un':
        return node_contains_var(node.x, name)
    if node.t == 'assign':
        return node_contains_var(node.target, name) or node_contains_var(node.value, name)
    if node.t in ('seq', 'list'):
        return any(node_contains_var(item, name) for item in node.items)
    return False


def walk(args, ctx, visit):
    """Runs `visit` per element with the binder and _K in scope. Returning a
    value from `visit` stops the walk and becomes the result.

    The frame is popped in a finally, so a body that raises does not leave the
    binder in scope for whatever runs next.
    """
    binder, body = shape(args)
    frame = {binder: None}
    if node_contains_var(body, '_K'):
        frame['_K'] = None
    ctx.push_frame(frame)
    try:
        for key, item in iter_elements(args.val(0)):
            frame[binder] = item
            if '_K' in frame:
                frame['_K'] = Value.text(key)
            result = visit(args.eval_node(body), key, item, body)
            if result is not None:
                return result
    finally:
        ctx.pop_frame()
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
        out.append(r)
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
            out.set(key, item)
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
    a.force()
    b.force()
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
    return Value.list([x['item'] for x in indexed])


define('SORT', 1, 3, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, 'ASC'))
define('SORT_DESC', 1, 3, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, 'DESC'))
define('SORT_BY', 2, 4, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, None))


def do_top(args, ctx, forced_dir):
    value = args.val(0)
    limit = args.non_neg_int(args.count() - 1)
    if limit == 0 or value.is_null():
        return Value.list([])
    value.force()
    if value.kind == NONE and value.size() == 0:
        return Value.list([])

    sort_count = args.count() - 1
    binder = '_'
    body = None
    direction = forced_dir or 'ASC'
    if sort_count == 1:
        binder = None
    elif sort_count == 2:
        body = args.node(1)
    elif sort_count == 3:
        if forced_dir is not None:
            binder = args.symbol(1)
            body = args.node(2)
        elif args.node(2).t == 'text':
            body = args.node(1)
            direction = args.text(2).upper()
        elif args.is_symbol(1):
            binder = args.symbol(1)
            body = args.node(2)
        else:
            body = args.node(1)
            direction = args.text(2).upper()
    elif sort_count == 4:
        binder = args.symbol(1)
        body = args.node(2)
        direction = args.text(3).upper()
    else:
        fail('E_ARITY', f'{args.name} has an invalid sort form', args.pos)

    if direction not in ('ASC', 'DESC'):
        direction_index = 3 if sort_count == 4 else 2
        fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'",
             args.pos_of(direction_index))

    def compare(a, b):
        c = compare_values(a['key'], b['key'])
        if direction == 'DESC':
            c = -c
        return c if c != 0 else a['idx'] - b['idx']

    def worse(a, b):
        c = compare(a, b)
        return c > 0 or (c == 0 and a['idx'] > b['idx'])

    heap = []

    def sift_up(index):
        while index > 0:
            parent = (index - 1) // 2
            if not worse(heap[index], heap[parent]):
                break
            heap[index], heap[parent] = heap[parent], heap[index]
            index = parent

    def sift_down(index):
        while True:
            left = index * 2 + 1
            right = left + 1
            worst_index = index
            if left < len(heap) and worse(heap[left], heap[worst_index]):
                worst_index = left
            if right < len(heap) and worse(heap[right], heap[worst_index]):
                worst_index = right
            if worst_index == index:
                return
            heap[index], heap[worst_index] = heap[worst_index], heap[index]
            index = worst_index

    needs_k = body is not None and node_contains_var(body, '_K')
    index = 0

    def consume(key, item):
        nonlocal index
        if binder is None:
            candidate = {'item': item, 'key': item, 'idx': index}
        else:
            frame = {binder: item}
            if needs_k:
                frame['_K'] = Value.text(key)
            ctx.push_frame(frame)
            try:
                candidate = {'item': item, 'key': args.eval_node(body), 'idx': index}
            finally:
                ctx.pop_frame()
        index += 1
        if len(heap) < limit:
            heap.append(candidate)
            sift_up(len(heap) - 1)
        elif worse(heap[0], candidate):
            heap[0] = candidate
            sift_down(0)

    if value.is_list and value.storage is not None:
        for i, item in enumerate(value.storage):
            consume(str(i + 1), item)
    elif value.shape is not None:
        for i, item in enumerate(value.storage):
            consume(value.shape.keys[i], item)
    else:
        for key, item in elements(value):
            consume(key, item)

    heap.sort(key=cmp_to_key(compare))
    return Value.list([entry['item'] for entry in heap])


define('TOP', 2, 4, lazy=True, binds=True, fn=lambda args, ctx: do_top(args, ctx, 'ASC'))
define('TOP_DESC', 2, 4, lazy=True, binds=True, fn=lambda args, ctx: do_top(args, ctx, 'DESC'))
define('TOP_BY', 3, 5, lazy=True, binds=True, fn=lambda args, ctx: do_top(args, ctx, None))


def do_group_by(args, ctx):
    val = args.val(0)
    if val.is_null() or val.size() == 0:
        return Value.list([])
    entries = elements(val)
    if not entries:
        return Value.list([])

    count = args.count()
    binder = '_'
    agg_node = None

    if count == 2:
        key_node = args.node(1)
    elif count == 3:
        key_node = args.node(1)
        agg_node = args.node(2)
    else:
        binder = args.symbol(1)
        key_node = args.node(2)
        agg_node = args.node(3)

    needs_k = node_contains_var(key_node, '_K')
    frame = {binder: None}
    if needs_k:
        frame['_K'] = None
    table = {}
    groups = []

    def process(key, item, index):
        frame[binder] = item
        if needs_k:
            frame['_K'] = Value.text(key if key is not None else str(index))
        group_key = args.eval_node(key_node)
        hashed = structural_hash(group_key)
        bucket = table.setdefault(hashed, [])
        existing = next((group for group in bucket if group['key'].eql(group_key)), None)
        if existing is not None:
            existing['rows'].append(item)
            return
        key_str = ''
        if agg_node is None:
            if group_key.kind == Value.TEXT:
                key_str = str(group_key.scalar)
            elif group_key.kind == Value.BOOL:
                key_str = 'TRUE' if group_key.scalar else 'FALSE'
            elif group_key.looks_numeric():
                key_str = str(group_key.scalar)
        group = {'key': group_key, 'key_str': key_str, 'rows': [item]}
        bucket.append(group)
        groups.append(group)

    ctx.push_frame(frame)
    try:
        for index, (key, item) in enumerate(entries, 1):
            process(key, item, index)
    finally:
        ctx.pop_frame()

    if agg_node is None:
        out = Value.none()
        for g in groups:
            out.set(g['key_str'], Value.list([row.clone() for row in g['rows']]))
        return out

    out = []
    aggregate_frame = {binder: None, '_K': None}
    ctx.push_frame(aggregate_frame)
    try:
        for g in groups:
            aggregate_frame[binder] = Value.list(g['rows'])
            aggregate_frame['_K'] = g['key']
            out.append(args.eval_node(agg_node))
    finally:
        ctx.pop_frame()
    return Value.list(out)


define('GROUP_BY', 2, 4, lazy=True, binds=True, fn=do_group_by)
define('BUCKET', 2, 4, lazy=True, binds=True, fn=do_group_by)
