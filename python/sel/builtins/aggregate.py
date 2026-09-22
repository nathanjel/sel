"""Aggregates. These are why SEL needs no loop: each evaluates one argument node
once per element, which is the same move IF makes, repeated.
"""

from functools import cmp_to_key

from .. import decimal as D
from ..errors import SelError, fail
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


def _eval_body(args, body, tentative):
    if not tentative:
        return args.eval_node(body)
    try:
        return args.eval_node(body)
    except SelError:
        return None


def walk(args, ctx, visit, tentative=False, body_override=None):
    """Runs `visit` per element with the binder and _K in scope. Returning a
    value from `visit` stops the walk and becomes the result. ``tentative``: a
    body that raises keeps the element -- `visit` sees None -- for the FILTER
    above to decide (a pushed conjunct, spec §7.4).

    The frame is popped in a finally, so a body that raises does not leave the
    binder in scope for whatever runs next.
    """
    binder, body = shape(args)
    if body_override is not None:
        body = body_override
    frame = {binder: None}
    if node_contains_var(body, '_K'):
        frame['_K'] = None
    ctx.push_frame(frame)
    try:
        for key, item in iter_elements(args.val(0)):
            frame[binder] = item
            if '_K' in frame:
                frame['_K'] = Value.text(key)
            result = visit(_eval_body(args, body, tentative), key, item, body)
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


def _leading_field_conjuncts(body, binder):
    """The leading AND-conjuncts of a FILTER body that read nothing but fields
    of the element -- ``_["status"] $== "x"`` -- as (node, fields) pairs, in
    order, stopping at the first conjunct that reads anything else: another
    variable, ``_K``, the element as a whole, a call, an assignment. The list
    is what a LINK may pre-apply to its left rows (see ``_link``). The second
    value says whether EVERY conjunct qualified.
    """
    conjuncts = []
    node = body
    while node is not None and node.t == 'bin' and node.op == 'AND':
        conjuncts.append(node.r)
        node = node.l
    conjuncts.append(node)
    conjuncts.reverse()
    names = {binder.upper(), '_'}
    out = []
    for c in conjuncts:
        fields = set()
        def reads_only_fields(n):
            if n is None:
                return True
            if n.t == 'index':
                if (n.obj is not None and n.obj.t == 'var' and n.obj.name.upper() in names
                        and n.idx is not None and n.idx.t == 'text'):
                    fields.add(n.idx.v.upper())
                    return True
                return False
            if n.t in ('num', 'text', 'bool'):
                return True
            if n.t == 'bin':
                return reads_only_fields(n.l) and reads_only_fields(n.r)
            if n.t == 'un':
                return reads_only_fields(n.x)
            return False
        if not reads_only_fields(c) or not fields:
            return out, False
        out.append((c, fields))
    return out, True


def _filter(args, ctx):
    """The one aggregate that preserves keys — a filtered list should still be
    addressable the way the original was.
    """
    # Over a join, the leading conjuncts that read only fields of the row are
    # offered to the LINK, which pre-applies them to its left rows where that
    # is provably the same as filtering the joined rows; the full predicate
    # still runs over the joined rows below, so results and errors are what
    # they were. This is where this host used to push a filter under the join
    # in its physical tree by reading the context's first row (SEL-0049); the
    # tree is now the same for any data, and the join decides at run time.
    #
    # The conjuncts travel as STAGES, one per FILTER, in the order the FILTERs
    # run: a FILTER between two joins (one the logical optimiser pushed down)
    # takes the stages handed to it by the join above and puts its own first
    # -- but only when its whole predicate is pre-evaluable, so a row dropped
    # below could not have raised in it -- and hands them to its own join.
    # Deep drops, below the join directly under the owning FILTER, change that
    # FILTER's keys, so they are allowed only where nothing observes them
    # (`keys_unobserved`, stamped by the physical optimiser) -- SEL-0050.
    src = args.node(0)
    handed = ctx.join_prefilter
    ctx.join_prefilter = None
    if src is not None and src.t == 'call' and src.name in ('LINK', 'LINK_LEFT'):
        binder, body = shape(args)
        own, complete = _leading_field_conjuncts(body, binder)
        stages = [(binder, own)] if own else []
        if handed is not None and own and complete:
            stages.extend(handed[0])
        deep = bool(getattr(body, 'keys_unobserved', False)) if handed is None else True
        if stages:
            ctx.join_prefilter = (stages, deep)
    written = args.node(args.count() - 1)
    kept_before = ctx.tentative_kept
    try:
        in_val = args.val(0)
    finally:
        ctx.join_prefilter = None
    # A predicate whose leading conjuncts were pushed under the LINK below:
    # when no tentative body kept a row on an error while the source ran,
    # every row here passed them, and only the remaining conjuncts are
    # evaluated (TRUE when there are none); otherwise the whole predicate, as
    # written, decides -- and raises -- in the source's order.
    body_override = None
    if written.pushed_down and ctx.tentative_kept == kept_before:
        body_override = written.remaining
        # Nothing remains: every row of the join below passed, and the join
        # built a fresh list this FILTER would only copy.
        if body_override.t == 'bool' and body_override.v is True:
            return in_val
    # Pass the join's report up past this FILTER's own stage, so the join
    # above counts only the conjuncts that were its own.
    report = ctx.join_prefilter_report
    ctx.join_prefilter_report = None
    if report is not None and handed is not None and own and complete:
        ctx.join_prefilter_report = (max(0, report[0] - len(own)), report[1])
    is_dense = in_val.is_list and in_val.storage is not None and in_val.list_keys is None
    tentative = bool(written.tentative)
    def keep(r, body):
        # A tentative body (a pushed conjunct, spec §7.4) that raised gives
        # None, and one whose value is not a BOOL is kept too: the FILTER
        # above decides, in the source's order.
        if r is None:
            ctx.tentative_kept += 1
            return True
        try:
            return r.as_bool(body.pos)
        except SelError:
            if not tentative:
                raise
            ctx.tentative_kept += 1
            return True
    storage = []
    keys = None
    needs_custom_keys = False
    orig_idx = 1

    if is_dense:
        def visit(r, key, item, body):
            nonlocal needs_custom_keys, keys, orig_idx
            if keep(r, body):
                storage.append(item)
                if needs_custom_keys:
                    keys.append(str(orig_idx))
            else:
                if not needs_custom_keys:
                    needs_custom_keys = True
                    keys = [str(j + 1) for j in range(len(storage))]
            orig_idx += 1
            return None
    else:
        expected_index = 1
        def visit(r, key, item, body):
            nonlocal needs_custom_keys, keys, expected_index
            if keep(r, body):
                storage.append(item)
                if not needs_custom_keys and str(key) != str(expected_index):
                    needs_custom_keys = True
                    keys = [str(j + 1) for j in range(len(storage) - 1)]
                if needs_custom_keys:
                    if keys is None:
                        keys = []
                    keys.append(str(key))
                expected_index += 1
            return None

    walk(args, ctx, visit, tentative, body_override)
    return Value.list(storage, keys if needs_custom_keys else None)


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
    return Value.list([x['item'] for x in indexed])


define('SORT', 1, 3, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, 'ASC'))
define('SORT_DESC', 1, 3, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, 'DESC'))
define('SORT_BY', 2, 4, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, None))


def do_top(args, ctx, forced_dir):
    value = args.val(0)
    limit = args.non_neg_int(args.count() - 1)
    if limit == 0 or value.is_null():
        return Value.list([])
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


def _bucket_key_text(key: Value, pos) -> str:
    if key.kind == Value.NONE:
        if key.is_null():
            fail('E_NULL', 'value is NULL', pos)
        fail('E_NOT_TEXT', 'a bucket key must be text or a number, got a list or record', pos)
    return key.as_text(pos)


def do_bucket(args, ctx):
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
        # A bare bucket's key is an index key (spec §3.3): the scalar,
        # verbatim, and refused the way indexing refuses it -- never collapsed
        # onto a string that stands for every list, record or NULL. The
        # projected spelling has no map to key and groups by identity instead.
        key_str = _bucket_key_text(group_key, key_node.pos) if agg_node is None else ''
        hashed = structural_hash(group_key)
        bucket = table.get(hashed)
        if bucket is None:
            group = {'key': group_key, 'key_str': key_str, 'rows': [item]}
            table[hashed] = [group]
            groups.append(group)
            return
        for group in bucket:
            if group['key'].eql(group_key):
                group['rows'].append(item)
                return
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


define('BUCKET', 2, 4, lazy=True, binds=True, fn=do_bucket)
