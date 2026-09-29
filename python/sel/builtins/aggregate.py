"""Aggregates. These are why SEL needs no loop: each evaluates one argument node
once per element, which is the same move IF makes, repeated.
"""

from functools import cmp_to_key

from .. import decimal as D
from .._budget import check_text
from ..errors import fail
from ..eval import bytes_compare
from ..parser import Node
from ..registry import define
from ..value import NONE, Value, elements, iter_elements, structural_hash
# The direction and field names fold ASCII-only (review 2026-09-25 SEM-05):
# str.upper() took "deſc" for DESC.
from ..lexer import ascii_upper


def shape(args):
    """Two-argument form binds `_`; three-argument form takes a bare identifier
    as the binder, checked by inspecting the AST node the caller handed us.
    """
    if args.count() == 3:
        return args.symbol(1), args.node(2)
    return '_', args.node(1)


def node_contains_var(node, name):
    """Whether NODE reads the variable `name`. Iterative: a body is a tree the
    source can make as deep as it is long (a flat chain of 5,000 `+` inside an
    aggregate), and a recursive walk of it ran into the interpreter's frame limit
    before the evaluator's own depth cap could report E_DEPTH (PY-C1, site e)."""
    upper = name.upper()
    stack = [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        t = n.t
        if t == 'var':
            if n.name.upper() == upper:
                return True
        elif t == 'index':
            stack.append(n.obj)
            stack.append(n.idx)
        elif t == 'call':
            stack.extend(n.args)
        elif t == 'bin':
            stack.append(n.l)
            stack.append(n.r)
        elif t == 'un':
            stack.append(n.x)
        elif t == 'assign':
            stack.append(n.target)
            stack.append(n.value)
        elif t in ('seq', 'list'):
            stack.extend(n.items)
    return False


def walk(args, ctx, visit, body_override=None):
    """Runs `visit` per element with the binder and _K in scope. Returning a
    value from `visit` stops the walk and becomes the result.

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
        # A snapshot (SPEC 7.3): the elements are fixed before the first body runs,
        # so a body that adds a key, appends, or overwrites a later element does
        # not change what is visited. What is inside an element is not copied, and
        # a change made there is seen.
        for key, item in list(iter_elements(args.val(0))):
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


# Calls that always return a value of their own -- a computed scalar, or a
# container built from copies -- so an aggregate collecting their result has
# nothing to copy. Everything else (a variable, an index, IF, COND, `??` ...)
# can hand back a value that is also reachable from the context, and is copied.
_FRESH_CALLS = frozenset({
    'LIST', 'RECORD', 'COUNT', 'LEN', 'UPPER', 'LOWER', 'ABS', 'ROUND', 'FLOOR',
    'CEIL', 'TRUNC', 'TRIM', 'JOIN', 'SUBSTR', 'LEFT', 'RIGHT',
})


def _is_fresh(node) -> bool:
    t = node.t
    if t == 'un':
        return True
    if t == 'bin':
        return node.op not in ('??', '???')
    if t == 'call':
        return node.name in _FRESH_CALLS
    return False


def collected(value, body, args):
    """What an aggregate stores in its result (SPEC 3.4): a copy, one level down,
    unless the body's node cannot have produced anything shared."""
    return value if _is_fresh(body) else value.clone(args.pos, 2)


def _map(args, ctx):
    out = []

    def visit(r, k, i, body):
        out.append(collected(r, body, args))
        return None

    walk(args, ctx, visit)
    return Value._list_owned(out)


_TEXT_COMPARE = ('$==', '$!=', '$<', '$<=', '$>', '$>=')
_NUM_COMPARE = ('==', '!=', '<', '<=', '>', '>=')


def _leading_field_conjuncts(body, binder):
    """Every AND-conjunct of a FILTER body, in order, as (node, fields, total)
    triples for a LINK to pre-apply to its left rows (see ``_link``).

    ``fields`` is the set of the row's fields the conjunct reads -- a bare
    ``_["status"]`` reads STATUS, a nested ``_["orders"]["year"]`` reads
    ORDERS -- or None when it reads anything else (another variable, ``_K``,
    the element as a whole, a call, an assignment): such a conjunct cannot be
    evaluated on a side's rows, and nothing after it can run before it unless
    it is TOTAL.

    ``total`` says when the conjunct cannot raise, so that a later conjunct
    may be applied where this one cannot be (SEL-0052): a comparison between
    literals and bare field reads is total when every such field is present
    on every row of its owning side with the kind the operator takes -- TEXT
    for the text comparisons (a number is text), a number for the numeric
    ones -- listed as (FIELD, kind) requirements. None means "not provable".
    """
    conjuncts = []
    node = body
    while node is not None and node.t == 'bin' and node.op == 'AND':
        conjuncts.append(node.r)
        node = node.l
    conjuncts.append(node)
    conjuncts.reverse()
    # The element is the binder, exactly as named: a variable is not the
    # binder because it differs only in case, and under an explicit binder
    # `_` is not the element.
    names = {binder}

    def literal_kind(n):
        if n is None:
            return None
        if n.t == 'num':
            return 'NUM'
        if n.t == 'text':
            return 'TEXT'
        return None

    out = []
    for c in conjuncts:
        fields = set()
        def reads_only_fields(root):
            # Iterative: a conjunct can be a chain as deep as the source is long.
            stack = [root]
            while stack:
                n = stack.pop()
                if n is None:
                    continue
                if n.t == 'index':
                    if (n.obj is not None and n.obj.t == 'var' and n.obj.name in names
                            and n.idx is not None and n.idx.t == 'text'):
                        fields.add(ascii_upper(n.idx.v))
                    elif n.obj is not None and n.obj.t == 'index':
                        stack.append(n.obj)
                        stack.append(n.idx)
                    else:
                        return False
                elif n.t in ('num', 'text', 'bool'):
                    continue
                elif n.t == 'bin':
                    stack.append(n.l)
                    stack.append(n.r)
                elif n.t == 'un':
                    stack.append(n.x)
                else:
                    return False
            return True
        ok = reads_only_fields(c) and bool(fields)
        total = None
        if c.t == 'bin' and (c.op in _TEXT_COMPARE or c.op in _NUM_COMPARE):
            kind = 'TEXT' if c.op in _TEXT_COMPARE else 'NUM'
            reqs = []
            for operand in (c.l, c.r):
                lit = literal_kind(operand)
                if lit is not None:
                    # A text literal is not a number: such an operand raises
                    # on every row, which is not total.
                    if kind == 'NUM' and lit != 'NUM':
                        reqs = None
                        break
                    continue
                bare = (operand is not None and operand.t == 'index' and operand.obj is not None
                        and operand.obj.t == 'var' and operand.obj.name in names
                        and operand.idx is not None and operand.idx.t == 'text')
                if not bare:
                    reqs = None
                    break
                reqs.append((operand.idx.v, kind))
            total = reqs
        out.append([c, fields if ok else None, total, binder])
    return out


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
    own = None
    if src is not None and src.t == 'call' and src.name in ('LINK', 'LINK_LEFT'):
        binder, body = shape(args)
        own = _leading_field_conjuncts(body, binder)
        # This FILTER's conjuncts first -- it runs before the FILTER that
        # handed the rest down -- then the handed ones; the join decides, in
        # that order, which it may apply and where it must stop (SEL-0052).
        # A stage is (binder, conjuncts, how many joins lie between its
        # FILTER and the join testing it): none, for this FILTER's own.
        stages = [(binder, own, 0)]
        # A first conjunct that is neither a field test nor total ends every
        # walk before it starts: hand nothing, gather nothing.
        if own and own[0][1] is None and own[0][2] is None:
            stages = []
        elif handed is not None:
            stages.extend(handed[0])
        deep = bool(getattr(body, 'keys_unobserved', False)) if handed is None else True
        if stages:
            ctx.join_prefilter = (stages, deep,
                                  handed[2] if handed is not None else [],
                                  handed[3] if handed is not None else [])
    try:
        in_val = args.val(0)
    finally:
        ctx.join_prefilter = None
    # The join's report -- which conjuncts every row that came up has passed,
    # by identity, whether a row was kept on an error, and whether any row was
    # dropped -- goes up as it is: the join above skips the ones it finds
    # there.
    report = ctx.join_prefilter_report
    ctx.join_prefilter_report = None
    if report is not None and handed is not None:
        ctx.join_prefilter_report = report
    # The conjuncts of this FILTER the join below applied held on every row
    # it built, unless it kept a row on an error: then they are TRUE there,
    # raise nowhere, and only the rest is evaluated, in the source's order
    # (with none left, the join's list is the FILTER's result as it is).
    body_override = None
    if own is not None and report is not None and not report[1]:
        applied = report[0]
        if any(id(e[0]) in applied for e in own):
            rest = [e[0] for e in own if id(e[0]) not in applied]
            if not rest:
                return in_val
            body_override = rest[0]
            for node in rest[1:]:
                body_override = Node('bin', body_override.pos, op='AND', l=body_override, r=node)
    is_dense = in_val.is_list and in_val.storage is not None and in_val.list_keys is None
    def keep(r, body):
        return r.as_bool(body.pos)
    storage = []
    keys = None
    needs_custom_keys = False
    orig_idx = 1

    if is_dense:
        def visit(r, key, item, body):
            nonlocal needs_custom_keys, keys, orig_idx
            if keep(r, body):
                storage.append(item.clone(args.pos, 2))
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
                storage.append(item.clone(args.pos, 2))
                if not needs_custom_keys and str(key) != str(expected_index):
                    needs_custom_keys = True
                    keys = [str(j + 1) for j in range(len(storage) - 1)]
                if needs_custom_keys:
                    if keys is None:
                        keys = []
                    keys.append(str(key))
                expected_index += 1
            return None

    walk(args, ctx, visit, body_override)
    return Value._list_owned(storage, keys if needs_custom_keys else None)


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
    # The joined length is refused before it is built (SPEC 6.4); an empty
    # result is never too large.
    if parts:
        check_text(sum(map(len, parts)) + len(sep) * (len(parts) - 1), args.pos)
    return Value.text(sep.join(parts))


define('ALL', 2, 3, lazy=True, binds=True, fn=_all)
define('ANY', 2, 3, lazy=True, binds=True, fn=_any)
define('MAP', 2, 3, lazy=True, binds=True, fn=_map)
define('FILTER', 2, 3, lazy=True, binds=True, fn=_filter)
define('SUM', 2, 3, lazy=True, binds=True, fn=_sum)
define('JOIN', 2, 2, fn=_join)


def _sort_rank(v: Value) -> int:
    """The kind rank of SPEC 7.3's total order: NULL < BOOL < numeric-looking text
    and numbers < every other text < BIN. Anything else (a value with children and
    no scalar) ranks last and ties with its own kind."""
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


def compare_values(a: Value, b: Value) -> int:
    """One total order (SPEC 7.3): by kind rank first, then within the rank --
    BOOL FALSE before TRUE, numbers by exact decimal value (so `"007"` ties with
    `"7"`), other text and BIN by their bytes. It is transitive, which the old
    pairwise rules were not: "10" < "1a" and "1a" < "9" by bytes, but "9" < "10"
    as numbers, and no sort of that list was well defined."""
    ra = _sort_rank(a)
    rb = _sort_rank(b)
    if ra != rb:
        return (ra > rb) - (ra < rb)
    if ra == 2:
        return D.cmp(a.as_decimal(), b.as_decimal())
    if ra == 1:
        av = 1 if a.scalar else 0
        bv = 1 if b.scalar else 0
        return (av > bv) - (av < bv)
    if ra == 3 or ra == 4:
        return bytes_compare(a.as_bytes(), b.as_bytes())
    return 0


def do_sort(args, ctx, forced_dir):
    val = args.val(0)
    ents = [] if val.is_null() else elements(val)

    count = args.count()
    if count == 1:
        if not ents:
            return Value._list_owned([])
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
                direction = ascii_upper(args.text(2))
            elif args.is_symbol(1):
                binder = args.symbol(1)
                body = args.node(2)
                direction = 'ASC'
            else:
                binder = '_'
                body = args.node(1)
                direction = ascii_upper(args.text(2))
        else:  # 4
            binder = args.symbol(1)
            body = args.node(2)
            direction = ascii_upper(args.text(3))

        if direction not in ('ASC', 'DESC'):
            pos_idx = 3 if count == 4 else 2
            fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", args.pos_of(pos_idx))
        # The direction is an argument like any other (SPEC 7.4): it was checked
        # above whether or not there is anything to sort, an empty list and NULL
        # included, so a rule's validity does not depend on its data.
        if not ents:
            return Value._list_owned([])

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
    return Value._list_owned([x['item'].clone(args.pos, 2) for x in indexed])


define('SORT', 1, 3, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, 'ASC'))
define('SORT_DESC', 1, 3, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, 'DESC'))
define('SORT_BY', 2, 4, lazy=True, binds=True, fn=lambda args, ctx: do_sort(args, ctx, None))


def do_top(args, ctx, forced_dir):
    value = args.val(0)
    limit = args.non_neg_int(args.count() - 1)

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
            direction = ascii_upper(args.text(2))
        elif args.is_symbol(1):
            binder = args.symbol(1)
            body = args.node(2)
        else:
            body = args.node(1)
            direction = ascii_upper(args.text(2))
    elif sort_count == 4:
        binder = args.symbol(1)
        body = args.node(2)
        direction = ascii_upper(args.text(3))
    else:
        fail('E_ARITY', f'{args.name} has an invalid sort form', args.pos)

    if direction not in ('ASC', 'DESC'):
        direction_index = 3 if sort_count == 4 else 2
        fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'",
             args.pos_of(direction_index))
    # Count and direction were evaluated and checked above whatever the list holds
    # (SPEC 7.4); only now may an empty result be returned.
    if limit == 0 or (value.kind == NONE and value.size() == 0):
        return Value._list_owned([])

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
        # A packed list may carry the keys a FILTER kept (list_keys); _K is
        # those, not the positions (review 2026-09-25 SEM-02).
        keys = value.list_keys
        for i, item in enumerate(value.storage):
            consume(keys[i] if keys is not None else str(i + 1), item)
    elif value.shape is not None:
        for i, item in enumerate(value.storage):
            consume(value.shape.keys[i], item)
    else:
        for key, item in elements(value):
            consume(key, item)

    heap.sort(key=cmp_to_key(compare))
    return Value._list_owned([entry['item'].clone(args.pos, 2) for entry in heap])


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
    # A scalar is a one-element list, NULL and an empty list are empty (SPEC 7.3).
    entries = [] if val.is_null() else elements(val)
    if not entries:
        return Value._list_owned([])

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
    index_table = {}
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
        if agg_node is None:
            # The key IS its text: two members whose keys read alike are one group
            # even when the keys differ in structure, and every member is kept.
            key_str = _bucket_key_text(group_key, key_node.pos)
            group = index_table.get(key_str)
            if group is None:
                group = {'key': group_key, 'key_str': key_str, 'rows': [item]}
                index_table[key_str] = group
                groups.append(group)
            else:
                group['rows'].append(item)
            return
        key_str = ''
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
            out.set(g['key_str'], Value._list_owned([row.clone(args.pos, 3) for row in g['rows']]))
        return out

    out = []
    aggregate_frame = {binder: None, '_K': None}
    ctx.push_frame(aggregate_frame)
    try:
        for g in groups:
            aggregate_frame[binder] = Value._list_owned(g['rows'])
            aggregate_frame['_K'] = g['key']
            out.append(collected(args.eval_node(agg_node), agg_node, args))
    finally:
        ctx.pop_frame()
    return Value._list_owned(out)


define('BUCKET', 2, 4, lazy=True, binds=True, fn=do_bucket)
